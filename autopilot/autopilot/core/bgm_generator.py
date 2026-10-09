"""BGM library generator (Round-2 upgrade #1).

Fills ``CONFIG.bgm_library_dir`` with original, mood-tagged instrumental beds
so ``audio_scene_graph.resolve_bgm_source`` always has a real library track to
pick (instead of falling back to the procedural pad).

Three backends, resolved in order of preference:

* ``musicgen``   — Meta MusicGen via ``transformers`` (lazy import; optional).
* ``stable_audio`` — Stability ``stable-audio-tools`` (lazy import; optional).
* ``procedural`` — the built-in mood-pad synth (always available, no deps).

Heavy generative backends are only imported when actually selected and
installed; any failure degrades gracefully to ``procedural`` so this module is
safe under the hermetic test suite and on machines without a GPU.

File naming embeds the mood name (e.g. ``bgm_dramatic_00.wav``) so the
deterministic mood-alias matcher in ``audio_scene_graph`` selects the right
bed for any topic.
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from autopilot.core.audio_scene_graph import (
    BGM_MOODS,
    generate_ambient_bgm_track,
)

logger = logging.getLogger(__name__)

# Backend identifiers (stable strings — also used in CLI choices).
BGM_BACKEND_PROCEDURAL = "procedural"
BGM_BACKEND_MUSICGEN = "musicgen"
BGM_BACKEND_STABLE_AUDIO = "stable_audio"
BGM_BACKEND_AUTO = "auto"

# Preference order for ``auto``: generative backends first, procedural last.
_AUTO_ORDER: Tuple[str, ...] = (
    BGM_BACKEND_MUSICGEN,
    BGM_BACKEND_STABLE_AUDIO,
    BGM_BACKEND_PROCEDURAL,
)

# Default generative prompt fragment per mood (MusicGen is text-conditioned).
_MOOD_PROMPTS: Dict[str, str] = {
    "dramatic": "tense cinematic underscore, dark driving strings, low brass hits",
    "contemplative": "calm ambient pad, soft warm synths, gentle lo-fi textures",
    "uplifting": "bright hopeful orchestral swell, light percussion, major key",
    "mysterious": "eerie atmospheric drone, sparse piano, subtle suspense pulse",
}


def _backend_available(backend: str) -> bool:
    """Return True when the given backend can be used on this machine."""
    if backend == BGM_BACKEND_PROCEDURAL:
        return True
    if backend == BGM_BACKEND_MUSICGEN:
        try:
            import transformers  # noqa: F401
            import torch  # noqa: F401
            return True
        except Exception:
            return False
    if backend == BGM_BACKEND_STABLE_AUDIO:
        try:
            import stable_audio_tools  # noqa: F401
            return True
        except Exception:
            return False
    return False


def available_bgm_backends() -> List[str]:
    """List every backend usable right now (procedural is always present)."""
    return [b for b in _AUTO_ORDER if _backend_available(b)]


def _resolve_backend(backend: str) -> str:
    """Resolve ``auto`` to the best available concrete backend.

    An explicit backend that is unavailable falls back to procedural with a
    warning (never raises), keeping the generator fail-soft.
    """
    requested = (backend or BGM_BACKEND_AUTO).lower().strip()
    if requested == BGM_BACKEND_AUTO:
        for candidate in _AUTO_ORDER:
            if _backend_available(candidate):
                return candidate
        return BGM_BACKEND_PROCEDURAL
    if requested in _AUTO_ORDER and _backend_available(requested):
        return requested
    logger.warning(
        "BGM backend '%s' unavailable; falling back to '%s'",
        backend, BGM_BACKEND_PROCEDURAL,
    )
    return BGM_BACKEND_PROCEDURAL


def _prompt_for_mood(mood: str, prompt: Optional[str]) -> str:
    if prompt:
        return prompt
    base = _MOOD_PROMPTS.get(mood) or _MOOD_PROMPTS["contemplative"]
    return f"{base}, instrumental, no vocals, background music"


def _generate_musicgen(output_path: Path, duration_sec: float, prompt: str) -> None:
    """Generate a bed with Meta MusicGen (lazy imports, ~50 tokens/sec)."""
    from transformers import AutoProcessor, MusicgenForConditionalGeneration
    import torch
    import scipy.io.wavfile as wavfile

    processor = AutoProcessor.from_pretrained("facebook/musicgen-small")
    model = MusicgenForConditionalGeneration.from_pretrained("facebook/musicgen-small")
    model.eval()

    inputs = processor(text=[prompt], padding=True, return_tensors="pt")
    # MusicGen emits ~50 audio tokens per second; cap by requested duration.
    max_new_tokens = int(max(1.0, duration_sec) * 50)
    with torch.no_grad():
        audio = model.generate(
            **inputs,
            do_sample=True,
            guidance_scale=3.0,
            max_new_tokens=max_new_tokens,
        )
    sampling_rate = model.config.audio_encoder.sampling_rate
    wavfile.write(str(output_path), rate=sampling_rate, data=audio[0, 0].cpu().numpy())


def _generate_stable_audio(output_path: Path, duration_sec: float, prompt: str) -> None:
    """Generate a bed with stable-audio-tools (lazy imports, best-effort)."""
    import torch
    from stable_audio_tools import get_model
    from stable_audio_tools.inference.generation import generate_diffusion_conditioned

    # Default open checkpoint (Stable Audio Open small). If the weights are not
    # cached this raises and the caller falls back to procedural.
    model = get_model("https://huggingface.co/stabilityai/stable-audio-open-1.0/model.safetensors")
    model = model.to("cpu")
    sample_rate = model.sample_rate
    seconds_total = max(5.0, float(duration_sec))
    conditioning = {
        "prompt": [prompt],
        "seconds_start": [0.0],
        "seconds_total": [seconds_total],
    }
    result = generate_diffusion_conditioned(
        model, conditioning, steps=100, cfg_scale=3.0, sample_size=int(sample_rate * seconds_total)
    )
    audio = result[0].cpu().numpy()
    import scipy.io.wavfile as wavfile
    wavfile.write(str(output_path), rate=sample_rate, data=audio)


def generate_bgm_bed(
    output_path: Path,
    duration_sec: float = 30.0,
    mood: str = "contemplative",
    backend: str = BGM_BACKEND_AUTO,
    prompt: Optional[str] = None,
) -> Tuple[Path, str]:
    """Generate one BGM bed at ``output_path``.

    Returns ``(path, backend_used)``. Any generative failure degrades to the
    procedural mood pad so callers always receive a usable file.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    resolved = _resolve_backend(backend)
    text_prompt = _prompt_for_mood(mood, prompt)

    try:
        if resolved == BGM_BACKEND_MUSICGEN:
            _generate_musicgen(output_path, duration_sec, text_prompt)
            return output_path, BGM_BACKEND_MUSICGEN
        if resolved == BGM_BACKEND_STABLE_AUDIO:
            _generate_stable_audio(output_path, duration_sec, text_prompt)
            return output_path, BGM_BACKEND_STABLE_AUDIO
    except Exception as exc:  # pragma: no cover - only hit when heavy deps fail
        logger.warning(
            "BGM backend '%s' failed (%s); using procedural pad", resolved, exc
        )

    # Procedural fallback (also the explicit procedural backend).
    path = generate_ambient_bgm_track(output_path, duration_sec=duration_sec, mood=mood)
    return path, BGM_BACKEND_PROCEDURAL


def fill_bgm_library(
    library_dir: Path,
    moods: Optional[List[str]] = None,
    per_mood: int = 1,
    duration_sec: float = 30.0,
    backend: str = BGM_BACKEND_AUTO,
) -> List[Path]:
    """Populate ``library_dir`` with mood-tagged beds for every mood.

    Filenames embed the mood name (``bgm_<mood>_<idx>.wav``) so the
    deterministic mood-alias matcher in ``audio_scene_graph`` selects them.
    Idempotent: existing non-empty files are skipped.
    """
    library = Path(library_dir)
    library.mkdir(parents=True, exist_ok=True)
    moods = moods or sorted(BGM_MOODS.keys())

    written: List[Path] = []
    for mood in moods:
        if mood not in BGM_MOODS:
            logger.warning("Unknown BGM mood '%s'; skipping", mood)
            continue
        for idx in range(max(1, int(per_mood))):
            out = library / f"bgm_{mood}_{idx:02d}.wav"
            if out.exists() and out.stat().st_size > 1000:
                continue
            path, used = generate_bgm_bed(
                out, duration_sec=duration_sec, mood=mood, backend=backend
            )
            logger.info("Generated BGM bed %s via %s", path.name, used)
            written.append(path)
    return written


def library_stats(library_dir: Path) -> Dict[str, int]:
    """Return a per-mood file count for an existing BGM library directory."""
    from autopilot.core.audio_scene_graph import BGM_LIBRARY_EXTENSIONS

    library = Path(library_dir)
    counts: Dict[str, int] = {}
    if not library.is_dir():
        return counts
    for p in sorted(library.iterdir()):
        if p.is_file() and p.suffix.lower() in BGM_LIBRARY_EXTENSIONS:
            digest = hashlib.sha256(p.name.encode("utf-8")).hexdigest()[:8]
            _ = digest  # kept for deterministic per-file identity if needed
            counts[p.name.lower()] = counts.get(p.name.lower(), 0) + 1
    return counts
