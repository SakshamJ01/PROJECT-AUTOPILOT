"""P0 materialized timeline construction from real job artifacts.

Creative QA evaluates a ``MaterializedTimeline`` (post-acquisition ground truth)
rather than a script, so a production job must be able to reconstruct that
timeline from the artifacts it actually produced: the script, the render plan
(verified asset paths, real Edge TTS segments, caption plan) and the audio mix
manifest.

Without this bridge, the creative gate and the publish readiness gate could not
run on real CLI jobs, and any acceptance claim about creative quality was
unverifiable.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from autopilot.core.timeline import (
    CaptionPhrase,
    CaptionPosition,
    MaterializedAudioPlan,
    MaterializedCaptionPlan,
    MaterializedNarration,
    MaterializedScene,
    MaterializedTiming,
    MaterializedTransitionPlan,
    MaterializedTimeline,
    NarrativeRole,
    PlatformSafeZone,
    QAExpectations,
    SelectedAsset,
    VisualRequirements,
)


class TimelineBuildError(RuntimeError):
    """Raised when real artifacts cannot be turned into a truthful timeline."""


def _coerce_enum(enum_cls: Any, raw: Any, default: Any) -> Any:
    """Coerce a stored string into an enum member, falling back to ``default``.

    Artifacts persist enums in several casings (``YOUTUBE_SHORTS``,
    ``youtube_shorts``), so normalize instead of failing the whole build.
    """
    if raw is None:
        return default
    if isinstance(raw, enum_cls):
        return raw
    text = str(raw).strip().lower()
    for member in enum_cls:
        if str(member.value).lower() == text or member.name.lower() == text:
            return member
    return default


def _scene_roles(count: int) -> List[NarrativeRole]:
    """Assign narrative roles: hook first, CTA last, content in between."""
    if count <= 0:
        return []
    if count == 1:
        return [NarrativeRole.CONTENT]
    roles = [NarrativeRole.CONTENT] * count
    roles[0] = NarrativeRole.HOOK
    roles[-1] = NarrativeRole.CTA
    return roles


def _word_timestamps(raw: Any) -> List[Any]:
    """Normalize word timestamps from plan or contract shapes.

    The timeline contract uses ``start``/``end``/``confidence`` while the
    production contract uses ``start_sec``/``end_sec``/``probability``; both are
    accepted and emitted in the timeline shape.
    """
    from autopilot.core.timeline import WordTimestamp

    out: List[WordTimestamp] = []
    for w in raw or []:
        if isinstance(w, WordTimestamp):
            out.append(w)
            continue
        if hasattr(w, "word") and hasattr(w, "start"):
            # contracts.WordTimestamp (or an already-converted object)
            out.append(
                WordTimestamp(
                    word=str(w.word),
                    start=float(getattr(w, "start", None) or w.start_sec),
                    end=float(getattr(w, "end", None) or w.end_sec),
                    confidence=float(getattr(w, "confidence", None) or getattr(w, "probability", 1.0) or 1.0),
                )
            )
            continue
        if not isinstance(w, dict):
            continue
        start = w.get("start", w.get("start_sec"))
        end = w.get("end", w.get("end_sec"))
        word = w.get("word")
        if start is None or end is None or not word:
            continue
        out.append(
            WordTimestamp(
                word=str(word),
                start=float(start),
                end=float(end),
                confidence=float(w.get("confidence", w.get("probability", 1.0)) or 1.0),
            )
        )
    return out


def _caption_phrases(words: List[Any], max_words: int = 4) -> List[CaptionPhrase]:
    """Group real word timings into short caption phrases for the QA record."""
    phrases: List[CaptionPhrase] = []
    for i in range(0, len(words), max_words):
        chunk = words[i : i + max_words]
        if not chunk:
            continue
        text = " ".join(w.word for w in chunk).strip()
        if not text:
            continue
        phrases.append(
            CaptionPhrase(
                phrase_text=text,
                start_sec=float(chunk[0].start),
                end_sec=float(chunk[-1].end),
                words=chunk,
            )
        )
    return phrases


def load_audio_mix_manifest(path: Optional[str | Path]) -> Dict[str, Any]:
    """Load the audio mix manifest if the audio graph stage wrote one."""
    if not path:
        return {}
    p = Path(str(path))
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def build_materialized_timeline(
    job_id: str,
    script: Any,
    plan_scenes: List[Dict[str, Any]],
    *,
    topic: str = "",
    profile_id: str = "viral_creator",
    content_type: str = "EXPLAINER",
    audio_mix_manifest: Optional[Dict[str, Any]] = None,
) -> MaterializedTimeline:
    """Build a truthful MaterializedTimeline from real job artifacts.

    Fails closed rather than inventing data: every scene must have a verified
    asset path and a real voice artifact with a positive measured duration,
    because those are exactly the facts the creative gate is supposed to assess.
    """
    if not plan_scenes:
        raise TimelineBuildError(
            f"job '{job_id}' has no render plan scenes; refusing to build a timeline"
        )

    plan_by_id = {str(s.get("scene_id")): s for s in plan_scenes if s.get("scene_id")}
    script_scenes = list(getattr(script, "scenes", []) or [])
    roles = _scene_roles(len(script_scenes))

    scenes: List[MaterializedScene] = []
    cursor = 0.0
    for idx, sscene in enumerate(script_scenes):
        scene_id = str(sscene.scene_id)
        plan = plan_by_id.get(scene_id)
        if plan is None:
            raise TimelineBuildError(f"scene '{scene_id}' is missing from the render plan")

        asset_path = plan.get("asset_path") or plan.get("normalized_path")
        if not asset_path:
            raise TimelineBuildError(
                f"scene '{scene_id}' has no verified asset; cannot claim creative quality"
            )
        if not Path(str(asset_path)).exists():
            raise TimelineBuildError(
                f"scene '{scene_id}' asset file does not exist: {asset_path}"
            )

        voice_path = plan.get("audio_path")
        if not voice_path or not Path(str(voice_path)).exists():
            raise TimelineBuildError(
                f"scene '{scene_id}' has no real voice artifact; cannot assess audio"
            )

        duration = float(plan.get("duration_sec") or 0.0)
        if duration <= 0:
            raise TimelineBuildError(
                f"scene '{scene_id}' has non-positive measured duration; refusing to guess"
            )

        words = _word_timestamps(plan.get("word_timestamps")) or _word_timestamps(
            getattr(sscene, "word_timestamps", None)
        )
        narration_text = (sscene.narration or "").strip()
        if not narration_text:
            raise TimelineBuildError(f"scene '{scene_id}' has empty narration")

        cap_plan_raw = plan.get("caption_plan") or {}
        caption_plan = MaterializedCaptionPlan(
            style_preset=cap_plan_raw.get("style_preset", "hormozi_yellow_pop"),
            position=_coerce_enum(
                CaptionPosition, cap_plan_raw.get("position"), CaptionPosition.LOWER
            ),
            platform_safe_zone=_coerce_enum(
                PlatformSafeZone,
                cap_plan_raw.get("platform_safe_zone"),
                PlatformSafeZone.YOUTUBE_SHORTS,
            ),
            phrases=_caption_phrases(words),
        )

        semantic_score = plan.get("semantic_score")
        scenes.append(
            MaterializedScene(
                scene_id=scene_id,
                order=idx + 1,
                narrative_role=roles[idx],
                timing=MaterializedTiming(
                    start_time_sec=round(cursor, 3),
                    end_time_sec=round(cursor + duration, 3),
                    duration_sec=round(duration, 3),
                ),
                narration=MaterializedNarration(
                    text=narration_text,
                    audio_artifact_path=str(voice_path),
                    word_timestamps=words,
                ),
                visual_requirements=VisualRequirements(
                    visual_concept=sscene.visual_intent or narration_text[:60],
                    b_roll_search_query=getattr(sscene, "asset_query", None),
                ),
                selected_assets=[
                    SelectedAsset(
                        asset_id=f"{scene_id}-primary",
                        asset_path=str(asset_path),
                        media_type=(plan.get("asset_type") or "video"),
                        provenance={
                            "provider": plan.get("asset_provider"),
                            "selection_reason": plan.get("selection_reason"),
                            "semantic_score": semantic_score,
                        },
                    )
                ],
                transition_plan=MaterializedTransitionPlan(
                    type=(getattr(sscene, "transition_hint", None) or "cut")
                ),
                caption_plan=caption_plan,
                audio_plan=MaterializedAudioPlan(
                    voice_path=str(voice_path),
                    voice_duration_sec=round(duration, 3),
                ),
                qa_expectations=QAExpectations(),
            )
        )
        cursor += duration

    return MaterializedTimeline(
        timeline_id=f"mtl-{job_id}",
        job_id=job_id,
        intent_timeline_id=f"itl-{job_id}",
        profile_id=profile_id,
        content_type=content_type,
        total_measured_duration_sec=round(cursor, 3),
        scenes=scenes,
        audio_mix_manifest=audio_mix_manifest or {},
    )


def load_timeline_from_job_dir(
    job_id: str,
    artifacts_dir: str | Path,
) -> Tuple[MaterializedTimeline, str]:
    """Rebuild the timeline for a job and persist it next to the render.

    Returns ``(timeline, rendered_media_path)``.
    """
    from autopilot.core.contracts import ScriptDocument

    job_dir = Path(str(artifacts_dir)) / "jobs" / str(job_id)
    script_path = job_dir / "script" / "script.json"
    plan_path = job_dir / "render" / "render_plan.json"
    media_path = job_dir / "render" / "final.mp4"

    if not script_path.exists():
        raise TimelineBuildError(f"missing script artifact: {script_path}")
    if not plan_path.exists():
        raise TimelineBuildError(f"missing render plan: {plan_path}")

    script = ScriptDocument.model_validate_json(script_path.read_text(encoding="utf-8"))
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan_scenes = plan.get("scenes") or []

    manifest = load_audio_mix_manifest(job_dir / "audio" / "audio_mix_manifest.json")

    timeline = build_materialized_timeline(
        job_id=job_id,
        script=script,
        plan_scenes=plan_scenes,
        topic=getattr(script, "topic", "") or "",
        content_type=(plan.get("content_type") or "EXPLAINER"),
        audio_mix_manifest=manifest,
    )

    out_path = job_dir / "timeline" / "materialized_timeline.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(timeline.model_dump_json(indent=2), encoding="utf-8")

    return timeline, str(media_path)


__all__ = [
    "TimelineBuildError",
    "build_materialized_timeline",
    "load_timeline_from_job_dir",
    "load_audio_mix_manifest",
]
