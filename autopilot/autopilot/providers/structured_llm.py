"""Structured LLM output (Round-2 upgrade #3) — schema-enforced script generation.

Wraps an :class:`LLMProvider` so the scene-beat JSON the model returns is
validated against a Pydantic schema before it is trusted, with automatic
corrective retry on violation — the same validate-and-retry contract the
``instructor`` library provides.

Two modes:

* If the optional ``instructor`` package is installed and the provider exposes
  a raw ``chat``/``complete`` callable, it is used for native structured output.
* Otherwise a portable Pydantic-validation loop is used: the base provider's
  ``generate_script`` is called, its result re-validated against the target
  schema, and any :class:`~pydantic.ValidationError` is converted into
  corrective instructions fed back on the next attempt.

The portable path is always available (no extra dependency), so this module is
safe under the hermetic test suite.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, List, Optional, Tuple, Type, TypeVar

from pydantic import BaseModel, ValidationError

from autopilot.core.contracts import ScriptDocument

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


def instructor_available() -> bool:
    """Return True when the optional ``instructor`` package is importable."""
    try:
        import instructor  # noqa: F401
        return True
    except Exception:
        return False


class ScriptPayloadSchema(BaseModel):
    """Lean schema for the raw LLM script JSON (pre full-parse validation).

    Enforces the fields the downstream parser depends on so a malformed model
    response is caught and corrected before it reaches ``ScriptDocument``.
    """

    title: str
    hook_text: str = ""
    scenes: List[dict]
    cta_text: str = ""


def validation_errors_to_instructions(errors: List[str], max_items: int = 5) -> str:
    """Turn Pydantic error strings into a concise corrective directive."""
    trimmed = "; ".join(errors[:max_items])
    return (
        "Your previous JSON did not match the required schema. Fix these issues "
        f"and return ONLY valid JSON: {trimmed}"
    )


def _exc_errors(exc: ValidationError) -> List[str]:
    out: List[str] = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc", ()))
        msg = err.get("msg", "invalid")
        out.append(f"{loc}: {msg}" if loc else msg)
    return out


def validate_against_schema(payload: Any, schema: Type[T]) -> Tuple[Optional[T], List[str]]:
    """Validate ``payload`` against ``schema``.

    Returns ``(instance, [])`` on success or ``(None, errors)`` on failure.
    """
    try:
        return schema.model_validate(payload), []
    except ValidationError as exc:
        return None, _exc_errors(exc)


def generate_script_structured(
    provider: Any,
    *,
    topic: str,
    content_id: str = "item-001",
    language: str = "en",
    schema: Type[T] = ScriptDocument,  # type: ignore[assignment]
    max_attempts: int = 3,
    **gen_kwargs: Any,
) -> T:
    """Call ``provider.generate_script`` and enforce ``schema`` with retry.

    ``provider`` must expose ``generate_script(...)``. On a schema violation the
    error text is turned into corrective instructions and the call is retried
    (up to ``max_attempts``). The final attempt re-raises the validation error
    so callers can fail loudly if the model never conforms.
    """
    corrective: Optional[str] = None
    last_exc: Optional[ValidationError] = None

    for attempt in range(1, max(1, int(max_attempts)) + 1):
        kwargs = dict(gen_kwargs)
        if corrective:
            existing = kwargs.get("corrective_instructions") or ""
            kwargs["corrective_instructions"] = (
                f"{existing}\n{corrective}".strip() if existing else corrective
            )
        if attempt > 1:
            kwargs["attempt_number"] = attempt

        result = provider.generate_script(
            topic=topic, content_id=content_id, language=language, **kwargs
        )

        # A ScriptDocument is already a BaseModel; re-validate to enforce the
        # requested schema (covers a custom schema or a loosely-built result).
        instance, errors = validate_against_schema(result, schema)
        if instance is not None:
            return instance

        logger.warning(
            "structured script validation failed on attempt %s/%s: %s",
            attempt, max_attempts, "; ".join(errors[:3]),
        )
        corrective = validation_errors_to_instructions(errors)
        try:
            # Reconstruct a ValidationError-like record for the final raise.
            schema.model_validate({})  # always fails; builds a real ValidationError
        except ValidationError as exc:  # pragma: no cover - trivially always raised
            last_exc = exc

    if last_exc is not None:
        raise last_exc
    raise RuntimeError("structured script generation failed without a validation error")


def complete_structured(
    chat_fn: Callable[..., str],
    *,
    schema: Type[T],
    max_attempts: int = 3,
    **kwargs: Any,
) -> T:
    """Generic validate-and-retry around a raw ``chat_fn`` returning JSON text.

    ``chat_fn`` accepts ``messages`` (and any extra kwargs) and returns a string
    that must parse as JSON matching ``schema``. On violation, a corrective
    system message is appended and the call is retried.
    """
    import json

    messages = list(kwargs.pop("messages", []))
    last_exc: Optional[ValidationError] = None

    for attempt in range(1, max(1, int(max_attempts)) + 1):
        text = chat_fn(messages=messages, **kwargs)
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            payload = {}
            errors = [f"json: {exc}"]
        else:
            instance, errors = validate_against_schema(payload, schema)
            if instance is not None:
                return instance

        last_text = validation_errors_to_instructions(errors)
        messages = messages + [
            {"role": "user", "content": last_text},
        ]
        try:
            schema.model_validate({})
        except ValidationError as exc:  # pragma: no cover
            last_exc = exc

    if last_exc is not None:
        raise last_exc
    raise RuntimeError("structured completion failed without a validation error")
