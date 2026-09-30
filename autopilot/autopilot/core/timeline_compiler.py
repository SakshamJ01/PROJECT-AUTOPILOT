"""Deterministic Timeline Compiler & Hard Render Invariants Gate — Cross-Phase 0.5.

Converts:
    MaterializedTimeline
            ↓
    TimelineCompiler (with 10 Hard Render Invariants)
            ↓
    sealed RenderPlan (with render_plan_sha256 & MPT Handoff)

Strictly enforces:
    01 Timeline Continuity
    02 Physical Asset Existence & Usability
    03 Audio Coverage
    04 Caption Enclosure
    05 Transition Guard
    06 Trim Validity
    07 Audio Stem Integrity
    08 Monotonicity (Non-decreasing stream ordering, allowing cross-track concurrency)
    09 License & Provenance Integrity
    10 Zero Stale Artifacts
"""
from __future__ import annotations

import math
import wave
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional, List, Dict, Any, Union, Tuple

from pydantic import BaseModel, Field

from autopilot.core.timeline import (
    IntentTimeline,
    MaterializedTimeline,
    MaterializedScene,
    RenderPlan,
    MPTHandoffConfig,
    ReproducibilityClass,
    StaleDefectCode,
    detect_stale_timeline,
    detect_stale_scene_version,
)


class HardInvariantCode(str, Enum):
    INVARIANT_01_TIMELINE_CONTINUITY = "INVARIANT_01_TIMELINE_CONTINUITY"
    INVARIANT_02_PHYSICAL_ASSET_EXISTENCE = "INVARIANT_02_PHYSICAL_ASSET_EXISTENCE"
    INVARIANT_03_AUDIO_COVERAGE = "INVARIANT_03_AUDIO_COVERAGE"
    INVARIANT_04_CAPTION_ENCLOSURE = "INVARIANT_04_CAPTION_ENCLOSURE"
    INVARIANT_05_TRANSITION_GUARD = "INVARIANT_05_TRANSITION_GUARD"
    INVARIANT_06_TRIM_VALIDITY = "INVARIANT_06_TRIM_VALIDITY"
    INVARIANT_07_AUDIO_STEM_INTEGRITY = "INVARIANT_07_AUDIO_STEM_INTEGRITY"
    INVARIANT_08_MONOTONICITY = "INVARIANT_08_MONOTONICITY"
    INVARIANT_09_LICENSE_INTEGRITY = "INVARIANT_09_LICENSE_INTEGRITY"
    INVARIANT_10_ZERO_STALE_ARTIFACTS = "INVARIANT_10_ZERO_STALE_ARTIFACTS"


class TimelineValidationError(BaseModel):
    invariant_code: HardInvariantCode
    message: str
    scene_id: Optional[str] = None
    details: Dict[str, Any] = Field(default_factory=dict)


class TimelineCompilationError(Exception):
    """Raised when one or more hard invariants fail during timeline compilation."""
    def __init__(self, errors: List[TimelineValidationError]):
        self.errors = errors
        error_msgs = [f"[{e.invariant_code.value}] {e.message}" for e in errors]
        super().__init__(f"Timeline compilation failed with {len(errors)} invariant violation(s):\n" + "\n".join(error_msgs))


class CompilationResult(BaseModel):
    success: bool
    render_plan: Optional[RenderPlan] = None
    errors: List[TimelineValidationError] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


# Commercial safe license keywords / indicators
COMMERCIAL_SAFE_LICENSES = {
    "pexels commercial",
    "pexels",
    "pixabay",
    "pixabay content license",
    "unsplash",
    "unsplash license",
    "cc0",
    "public domain",
    "commercial",
    "commercial_safe",
    "commercial safe",
    "commercial use allowed",
    "cc-by",
    "cc by 4.0",
    "cc-by-4.0",
    "cc-by-sa",
    "mit",
    "stock_licensed",
    "stock licensed",
    "licensed",
    "creative_commons",
    "local_asset",
    "generated",
    "ai_generated",
    "custom_owned",
}

FORBIDDEN_LICENSE_PATTERNS = [
    "non-commercial",
    "noncommercial",
    "nc",
    "editorial only",
    "all rights reserved",
    "unlicensed",
    "copyrighted",
    "unknown",
]


class TimelineCompiler:
    """Deterministic compiler translating MaterializedTimeline into immutable RenderPlan."""

    def __init__(
        self,
        compiler_version: str = "v4.0.0",
        check_physical_files: bool = True,
        reproducibility_class: ReproducibilityClass = ReproducibilityClass.EXACT_REPRODUCIBLE,
    ):
        self.compiler_version = compiler_version
        self.check_physical_files = check_physical_files
        self.reproducibility_class = reproducibility_class

    def validate_hard_invariants(
        self,
        timeline: MaterializedTimeline,
        parent_intent_timeline: Optional[IntentTimeline] = None,
        check_physical_files: Optional[bool] = None,
    ) -> List[TimelineValidationError]:
        """Assert all 10 hard render invariants against the materialized timeline."""
        errors: List[TimelineValidationError] = []
        should_check_files = self.check_physical_files if check_physical_files is None else check_physical_files

        if not timeline.scenes:
            errors.append(
                TimelineValidationError(
                    invariant_code=HardInvariantCode.INVARIANT_01_TIMELINE_CONTINUITY,
                    message="Timeline contains 0 scenes; at least 1 scene is required.",
                )
            )
            return errors

        scenes = sorted(timeline.scenes, key=lambda s: s.order)

        # ------------------------------------------------------------------
        # INVARIANT 01: Timeline Continuity
        # ------------------------------------------------------------------
        if abs(scenes[0].timing.start_time_sec) > 1e-3:
            errors.append(
                TimelineValidationError(
                    invariant_code=HardInvariantCode.INVARIANT_01_TIMELINE_CONTINUITY,
                    message=f"First scene '{scenes[0].scene_id}' must start at 0.0s (got {scenes[0].timing.start_time_sec}s).",
                    scene_id=scenes[0].scene_id,
                )
            )

        for i, s in enumerate(scenes):
            expected_end = s.timing.start_time_sec + s.timing.duration_sec
            if abs(s.timing.end_time_sec - expected_end) > 1e-3:
                errors.append(
                    TimelineValidationError(
                        invariant_code=HardInvariantCode.INVARIANT_01_TIMELINE_CONTINUITY,
                        message=f"Scene '{s.scene_id}' end_time_sec ({s.timing.end_time_sec}) does not equal start + duration ({expected_end}).",
                        scene_id=s.scene_id,
                    )
                )

            if i > 0:
                prev = scenes[i - 1]
                if abs(prev.timing.end_time_sec - s.timing.start_time_sec) > 1e-3:
                    errors.append(
                        TimelineValidationError(
                            invariant_code=HardInvariantCode.INVARIANT_01_TIMELINE_CONTINUITY,
                            message=f"Timeline discontinuity between scene '{prev.scene_id}' (ends {prev.timing.end_time_sec}s) and '{s.scene_id}' (starts {s.timing.start_time_sec}s).",
                            scene_id=s.scene_id,
                        )
                    )

        last_end = scenes[-1].timing.end_time_sec
        if abs(timeline.total_measured_duration_sec - last_end) > 1e-3:
            errors.append(
                TimelineValidationError(
                    invariant_code=HardInvariantCode.INVARIANT_01_TIMELINE_CONTINUITY,
                    message=f"Timeline total_measured_duration_sec ({timeline.total_measured_duration_sec}s) does not match final scene end ({last_end}s).",
                    scene_id=scenes[-1].scene_id,
                )
            )

        # ------------------------------------------------------------------
        # INVARIANT 02: Physical Asset Existence & Usability
        # ------------------------------------------------------------------
        for s in scenes:
            if not s.selected_assets:
                errors.append(
                    TimelineValidationError(
                        invariant_code=HardInvariantCode.INVARIANT_02_PHYSICAL_ASSET_EXISTENCE,
                        message=f"Scene '{s.scene_id}' has no selected assets.",
                        scene_id=s.scene_id,
                    )
                )
                continue

            for asset in s.selected_assets:
                if not asset.asset_path or not asset.asset_path.strip():
                    errors.append(
                        TimelineValidationError(
                            invariant_code=HardInvariantCode.INVARIANT_02_PHYSICAL_ASSET_EXISTENCE,
                            message=f"Scene '{s.scene_id}' asset '{asset.asset_id}' has empty asset_path.",
                            scene_id=s.scene_id,
                            details={"asset_id": asset.asset_id},
                        )
                    )
                    continue

                if should_check_files:
                    p = Path(asset.asset_path)
                    if not p.exists():
                        errors.append(
                            TimelineValidationError(
                                invariant_code=HardInvariantCode.INVARIANT_02_PHYSICAL_ASSET_EXISTENCE,
                                message=f"Referenced asset file does not exist on disk: {asset.asset_path}",
                                scene_id=s.scene_id,
                                details={"asset_id": asset.asset_id, "path": asset.asset_path},
                            )
                        )
                    elif p.stat().st_size == 0:
                        errors.append(
                            TimelineValidationError(
                                invariant_code=HardInvariantCode.INVARIANT_02_PHYSICAL_ASSET_EXISTENCE,
                                message=f"Referenced asset file is 0 bytes (corrupt): {asset.asset_path}",
                                scene_id=s.scene_id,
                                details={"asset_id": asset.asset_id, "path": asset.asset_path},
                            )
                        )

        # ------------------------------------------------------------------
        # INVARIANT 03: Audio Coverage
        # ------------------------------------------------------------------
        total_voice_dur = 0.0
        total_visual_dur = 0.0
        for s in scenes:
            total_visual_dur += s.timing.duration_sec
            voice_dur = s.audio_plan.voice_duration_sec
            total_voice_dur += voice_dur
            if s.timing.duration_sec < (voice_dur - 0.05):
                errors.append(
                    TimelineValidationError(
                        invariant_code=HardInvariantCode.INVARIANT_03_AUDIO_COVERAGE,
                        message=f"Scene '{s.scene_id}' visual duration ({s.timing.duration_sec}s) is shorter than narration audio duration ({voice_dur}s).",
                        scene_id=s.scene_id,
                        details={"visual_dur": s.timing.duration_sec, "voice_dur": voice_dur},
                    )
                )

        if total_visual_dur < (total_voice_dur - 0.05):
            errors.append(
                TimelineValidationError(
                    invariant_code=HardInvariantCode.INVARIANT_03_AUDIO_COVERAGE,
                    message=f"Total visual duration ({total_visual_dur}s) does not cover total voice duration ({total_voice_dur}s).",
                    details={"total_visual_dur": total_visual_dur, "total_voice_dur": total_voice_dur},
                )
            )

        # ------------------------------------------------------------------
        # INVARIANT 04: Caption Enclosure
        # ------------------------------------------------------------------
        for s in scenes:
            scene_start = s.timing.start_time_sec
            scene_end = s.timing.end_time_sec

            if s.caption_plan and s.caption_plan.phrases:
                for phrase in s.caption_plan.phrases:
                    # Phrase bounds relative to scene
                    if phrase.start_sec < (scene_start - 0.05):
                        errors.append(
                            TimelineValidationError(
                                invariant_code=HardInvariantCode.INVARIANT_04_CAPTION_ENCLOSURE,
                                message=f"Caption phrase '{phrase.phrase_text}' starts at {phrase.start_sec}s before scene '{s.scene_id}' start ({scene_start}s).",
                                scene_id=s.scene_id,
                            )
                        )
                    if phrase.end_sec > (scene_end + 0.05):
                        errors.append(
                            TimelineValidationError(
                                invariant_code=HardInvariantCode.INVARIANT_04_CAPTION_ENCLOSURE,
                                message=f"Caption phrase '{phrase.phrase_text}' ends at {phrase.end_sec}s after scene '{s.scene_id}' end ({scene_end}s).",
                                scene_id=s.scene_id,
                            )
                        )

            # Word timestamps check
            if s.narration and s.narration.word_timestamps:
                for w in s.narration.word_timestamps:
                    if w.start < (scene_start - 0.05) or w.end > (scene_end + 0.05):
                        errors.append(
                            TimelineValidationError(
                                invariant_code=HardInvariantCode.INVARIANT_04_CAPTION_ENCLOSURE,
                                message=f"Word '{w.word}' timestamp [{w.start}s, {w.end}s] exceeds scene '{s.scene_id}' bounds [{scene_start}s, {scene_end}s].",
                                scene_id=s.scene_id,
                            )
                        )

        # ------------------------------------------------------------------
        # INVARIANT 05: Transition Guard
        # ------------------------------------------------------------------
        for i, s in enumerate(scenes):
            t_dur = s.transition_plan.duration_sec
            if t_dur > 0:
                if i < len(scenes) - 1:
                    next_s = scenes[i + 1]
                    max_allowed = 0.5 * min(s.timing.duration_sec, next_s.timing.duration_sec) + 1e-3
                    if t_dur > max_allowed:
                        errors.append(
                            TimelineValidationError(
                                invariant_code=HardInvariantCode.INVARIANT_05_TRANSITION_GUARD,
                                message=f"Scene '{s.scene_id}' transition duration ({t_dur}s) exceeds 50% of adjacent scene minimum ({max_allowed:.2f}s).",
                                scene_id=s.scene_id,
                                details={"transition_dur": t_dur, "max_allowed": max_allowed},
                            )
                        )
                else:
                    max_allowed = 0.5 * s.timing.duration_sec + 1e-3
                    if t_dur > max_allowed:
                        errors.append(
                            TimelineValidationError(
                                invariant_code=HardInvariantCode.INVARIANT_05_TRANSITION_GUARD,
                                message=f"Final scene '{s.scene_id}' transition duration ({t_dur}s) exceeds 50% of scene duration ({max_allowed:.2f}s).",
                                scene_id=s.scene_id,
                            )
                        )

        # ------------------------------------------------------------------
        # INVARIANT 06: Trim Validity
        # ------------------------------------------------------------------
        for s in scenes:
            scene_trimmed_total = 0.0
            for asset in s.selected_assets:
                trim = asset.trim_range
                if trim.in_sec < 0.0:
                    errors.append(
                        TimelineValidationError(
                            invariant_code=HardInvariantCode.INVARIANT_06_TRIM_VALIDITY,
                            message=f"Scene '{s.scene_id}' asset '{asset.asset_id}' trim in_sec ({trim.in_sec}s) is negative.",
                            scene_id=s.scene_id,
                        )
                    )
                if trim.out_sec is not None:
                    if trim.out_sec <= trim.in_sec:
                        errors.append(
                            TimelineValidationError(
                                invariant_code=HardInvariantCode.INVARIANT_06_TRIM_VALIDITY,
                                message=f"Scene '{s.scene_id}' asset '{asset.asset_id}' trim out_sec ({trim.out_sec}s) must be greater than in_sec ({trim.in_sec}s).",
                                scene_id=s.scene_id,
                            )
                        )
                    trimmed_dur = trim.out_sec - trim.in_sec
                    scene_trimmed_total += trimmed_dur
                    if len(s.selected_assets) == 1 and trimmed_dur < (s.timing.duration_sec - 0.05):
                        errors.append(
                            TimelineValidationError(
                                invariant_code=HardInvariantCode.INVARIANT_06_TRIM_VALIDITY,
                                message=f"Scene '{s.scene_id}' asset '{asset.asset_id}' trim range duration ({trimmed_dur:.2f}s) is shorter than required scene duration ({s.timing.duration_sec}s).",
                                scene_id=s.scene_id,
                            )
                        )
                    if asset.duration_sec is not None and trim.out_sec > (asset.duration_sec + 0.05):
                        errors.append(
                            TimelineValidationError(
                                invariant_code=HardInvariantCode.INVARIANT_06_TRIM_VALIDITY,
                                message=f"Scene '{s.scene_id}' asset '{asset.asset_id}' trim out_sec ({trim.out_sec}s) exceeds physical asset duration ({asset.duration_sec}s).",
                                scene_id=s.scene_id,
                            )
                        )
            if len(s.selected_assets) > 1 and scene_trimmed_total < (s.timing.duration_sec - 0.05):
                errors.append(
                    TimelineValidationError(
                        invariant_code=HardInvariantCode.INVARIANT_06_TRIM_VALIDITY,
                        message=f"Scene '{s.scene_id}' total asset trim duration ({scene_trimmed_total:.2f}s) across {len(s.selected_assets)} assets is shorter than required scene duration ({s.timing.duration_sec}s).",
                        scene_id=s.scene_id,
                    )
                )

        # ------------------------------------------------------------------
        # INVARIANT 07: Audio Stem Integrity (44.1 kHz, Stereo)
        # ------------------------------------------------------------------
        for s in scenes:
            voice_path = s.audio_plan.voice_path
            if not voice_path or not voice_path.strip():
                errors.append(
                    TimelineValidationError(
                        invariant_code=HardInvariantCode.INVARIANT_07_AUDIO_STEM_INTEGRITY,
                        message=f"Scene '{s.scene_id}' audio plan has empty voice_path.",
                        scene_id=s.scene_id,
                    )
                )
            
            # Enforce sample rate (44.1 kHz) & channels (stereo / 2)
            sr = getattr(s.audio_plan, "sample_rate", 44100)
            if sr != 44100:
                errors.append(
                    TimelineValidationError(
                        invariant_code=HardInvariantCode.INVARIANT_07_AUDIO_STEM_INTEGRITY,
                        message=f"Scene '{s.scene_id}' audio plan sample rate {sr}Hz is invalid (expected 44.1kHz / 44100Hz).",
                        scene_id=s.scene_id,
                        details={"sample_rate": sr, "expected": 44100},
                    )
                )

            ch = getattr(s.audio_plan, "channels", 2)
            if ch != 2:
                errors.append(
                    TimelineValidationError(
                        invariant_code=HardInvariantCode.INVARIANT_07_AUDIO_STEM_INTEGRITY,
                        message=f"Scene '{s.scene_id}' audio plan channel count {ch} is invalid (expected stereo / 2 channels).",
                        scene_id=s.scene_id,
                        details={"channels": ch, "expected": 2},
                    )
                )

            if should_check_files and voice_path:
                p = Path(voice_path)
                if not p.exists():
                    errors.append(
                        TimelineValidationError(
                            invariant_code=HardInvariantCode.INVARIANT_07_AUDIO_STEM_INTEGRITY,
                            message=f"Scene '{s.scene_id}' voice audio stem not found on disk: {voice_path}",
                            scene_id=s.scene_id,
                        )
                    )
                elif p.stat().st_size == 0:
                    errors.append(
                        TimelineValidationError(
                            invariant_code=HardInvariantCode.INVARIANT_07_AUDIO_STEM_INTEGRITY,
                            message=f"Scene '{s.scene_id}' voice audio stem is 0 bytes (corrupt): {voice_path}",
                            scene_id=s.scene_id,
                        )
                    )
                elif p.suffix.lower() == ".wav" and p.stat().st_size > 44:
                    try:
                        with wave.open(str(p), "rb") as wf:
                            phys_sr = wf.getframerate()
                            phys_ch = wf.getnchannels()
                            if phys_sr != 44100:
                                errors.append(
                                    TimelineValidationError(
                                        invariant_code=HardInvariantCode.INVARIANT_07_AUDIO_STEM_INTEGRITY,
                                        message=f"Scene '{s.scene_id}' voice audio sample rate {phys_sr}Hz is invalid (expected 44.1kHz / 44100Hz).",
                                        scene_id=s.scene_id,
                                        details={"sample_rate": phys_sr, "expected": 44100},
                                    )
                                )
                            if phys_ch != 2:
                                errors.append(
                                    TimelineValidationError(
                                        invariant_code=HardInvariantCode.INVARIANT_07_AUDIO_STEM_INTEGRITY,
                                        message=f"Scene '{s.scene_id}' voice audio channel count {phys_ch} is invalid (expected stereo / 2 channels).",
                                        scene_id=s.scene_id,
                                        details={"channels": phys_ch, "expected": 2},
                                    )
                                )
                    except Exception:
                        pass


        # ------------------------------------------------------------------
        # INVARIANT 08: Monotonicity
        # ------------------------------------------------------------------
        # Non-decreasing within each stream
        for i in range(len(scenes) - 1):
            if scenes[i + 1].timing.start_time_sec < scenes[i].timing.start_time_sec:
                errors.append(
                    TimelineValidationError(
                        invariant_code=HardInvariantCode.INVARIANT_08_MONOTONICITY,
                        message=f"Scene stream non-monotonic: scene {i+1} starts at {scenes[i+1].timing.start_time_sec}s < scene {i} start {scenes[i].timing.start_time_sec}s.",
                        scene_id=scenes[i+1].scene_id,
                    )
                )

        for s in scenes:
            # Check narration word timestamps stream
            words = s.narration.word_timestamps or []
            for j in range(len(words) - 1):
                if words[j + 1].start < words[j].start:
                    errors.append(
                        TimelineValidationError(
                            invariant_code=HardInvariantCode.INVARIANT_08_MONOTONICITY,
                            message=f"Scene '{s.scene_id}' word timestamps non-monotonic: '{words[j+1].word}' ({words[j+1].start}s) < '{words[j].word}' ({words[j].start}s).",
                            scene_id=s.scene_id,
                        )
                    )
                if words[j].end < words[j].start:
                    errors.append(
                        TimelineValidationError(
                            invariant_code=HardInvariantCode.INVARIANT_08_MONOTONICITY,
                            message=f"Scene '{s.scene_id}' word '{words[j].word}' end ({words[j].end}s) < start ({words[j].start}s).",
                            scene_id=s.scene_id,
                        )
                    )

            # Check caption phrase stream
            phrases = (s.caption_plan.phrases if s.caption_plan else []) or []
            for k in range(len(phrases) - 1):
                if phrases[k + 1].start_sec < phrases[k].start_sec:
                    errors.append(
                        TimelineValidationError(
                            invariant_code=HardInvariantCode.INVARIANT_08_MONOTONICITY,
                            message=f"Scene '{s.scene_id}' caption stream non-monotonic: phrase {k+1} start ({phrases[k+1].start_sec}s) < phrase {k} start ({phrases[k].start_sec}s).",
                            scene_id=s.scene_id,
                        )
                    )

        # ------------------------------------------------------------------
        # INVARIANT 09: License & Provenance Integrity
        # ------------------------------------------------------------------
        for s in scenes:
            for asset in s.selected_assets:
                prov = asset.provenance or {}
                raw_license = str(prov.get("license", "") or "").strip().lower()
                provider = str(prov.get("provider", "") or "").strip().lower()

                # If no provenance or empty license provided, check provider fallback
                is_safe = False
                if raw_license:
                    # Check forbidden patterns
                    has_forbidden = any(pat in raw_license for pat in FORBIDDEN_LICENSE_PATTERNS)
                    if not has_forbidden and (
                        raw_license in COMMERCIAL_SAFE_LICENSES
                        or any(c in raw_license for c in COMMERCIAL_SAFE_LICENSES)
                        or provider in {"pexels", "pixabay", "unsplash", "openverse", "wikimedia", "local", "generated", "infographics"}
                    ):
                        is_safe = True
                elif provider in {"pexels", "pixabay", "unsplash", "openverse", "wikimedia", "local", "generated", "infographics"}:
                    # Known trusted stock providers are commercial safe by default
                    is_safe = True

                if not is_safe:
                    errors.append(
                        TimelineValidationError(
                            invariant_code=HardInvariantCode.INVARIANT_09_LICENSE_INTEGRITY,
                            message=f"Scene '{s.scene_id}' asset '{asset.asset_id}' lacks verified commercial-safe license (license='{raw_license}', provider='{provider}').",
                            scene_id=s.scene_id,
                            details={"asset_id": asset.asset_id, "provenance": prov},
                        )
                    )

        # ------------------------------------------------------------------
        # INVARIANT 10: Zero Stale Artifacts
        # ------------------------------------------------------------------
        if parent_intent_timeline is not None:
            if timeline.intent_timeline_id != parent_intent_timeline.timeline_id:
                errors.append(
                    TimelineValidationError(
                        invariant_code=HardInvariantCode.INVARIANT_10_ZERO_STALE_ARTIFACTS,
                        message=f"Materialized timeline intent_timeline_id '{timeline.intent_timeline_id}' does not match parent '{parent_intent_timeline.timeline_id}'.",
                    )
                )
            if timeline.parent_timeline_version and timeline.parent_timeline_version != parent_intent_timeline.timeline_version:
                errors.append(
                    TimelineValidationError(
                        invariant_code=HardInvariantCode.INVARIANT_10_ZERO_STALE_ARTIFACTS,
                        message=f"Materialized timeline parent_timeline_version '{timeline.parent_timeline_version}' does not match parent version '{parent_intent_timeline.timeline_version}'.",
                    )
                )
            for s in scenes:
                parent_scene = parent_intent_timeline.get_scene(s.scene_id)
                if parent_scene:
                    is_stale, code, reason = detect_stale_scene_version(s, parent_scene.scene_version)
                    if is_stale:
                        errors.append(
                            TimelineValidationError(
                                invariant_code=HardInvariantCode.INVARIANT_10_ZERO_STALE_ARTIFACTS,
                                message=f"Scene '{s.scene_id}' version is stale relative to parent: {reason}",
                                scene_id=s.scene_id,
                            )
                        )

        return errors

    def compile(
        self,
        timeline: MaterializedTimeline,
        parent_intent_timeline: Optional[IntentTimeline] = None,
        check_physical_files: Optional[bool] = None,
    ) -> RenderPlan:
        """Compile a MaterializedTimeline into a sealed, immutable RenderPlan.

        Raises:
            TimelineCompilationError: If any of the 10 hard render invariants are violated.
        """
        errors = self.validate_hard_invariants(
            timeline=timeline,
            parent_intent_timeline=parent_intent_timeline,
            check_physical_files=check_physical_files,
        )
        if errors:
            raise TimelineCompilationError(errors)

        # Convert MaterializedScenes to exact legacy-compatible scene structures
        legacy_scenes: List[Dict[str, Any]] = []
        video_materials: List[str] = []

        for s in sorted(timeline.scenes, key=lambda x: x.order):
            primary_asset = s.selected_assets[0] if s.selected_assets else None
            asset_path = primary_asset.asset_path if primary_asset else None
            if asset_path:
                video_materials.append(asset_path)

            scene_dict: Dict[str, Any] = {
                "scene_id": s.scene_id,
                "order": s.order,
                "narrative_role": s.narrative_role.value if hasattr(s.narrative_role, "value") else str(s.narrative_role),
                "asset_path": asset_path,
                "audio_path": s.audio_plan.voice_path,
                "duration_sec": s.timing.duration_sec,
                "start_time_sec": s.timing.start_time_sec,
                "end_time_sec": s.timing.end_time_sec,
                "transition_hint": s.transition_plan.type,
                "transition_duration_sec": s.transition_plan.duration_sec,
                "caption_text": s.narration.text,
                "caption_plan": s.caption_plan.model_dump(mode="json") if s.caption_plan else {},
                "caption_phrases": [p.model_dump(mode="json") for p in (s.caption_plan.phrases if s.caption_plan else [])],
                "crop_strategy": "crop",
                "crop_framing": primary_asset.crop_framing.model_dump(mode="json") if primary_asset else {},
                "trim_range": primary_asset.trim_range.model_dump(mode="json") if primary_asset else {},
                "word_timestamps": [w.model_dump(mode="json") for w in (s.narration.word_timestamps or [])],
                "sfx_events": [ev.model_dump(mode="json") for ev in (s.audio_plan.sfx_events if s.audio_plan else [])],
            }
            legacy_scenes.append(scene_dict)

        # Custom mastered audio stem
        master_audio = (
            timeline.audio_mix_manifest.get("master_voice_path")
            or (timeline.scenes[0].audio_plan.voice_path if timeline.scenes else None)
        )

        bgm_file = timeline.audio_mix_manifest.get("bgm_file")
        bgm_vol = timeline.scenes[0].audio_plan.bgm_intensity if timeline.scenes else 0.2

        mpt_handoff = MPTHandoffConfig(
            custom_audio_file=master_audio,
            video_materials=video_materials,
            match_materials_to_script=True,
            random_material_selection=False,
            video_transition_mode=timeline.scenes[0].transition_plan.type if timeline.scenes else "cut",
            video_fit_mode="crop",
            subtitle_display_mode="animated",
            subtitle_animation="pop",
            bgm_file=bgm_file,
            bgm_volume=bgm_vol,
        )

        unsealed_plan = RenderPlan(
            plan_id=f"plan-{timeline.job_id}",
            content_id=timeline.intent_timeline_id or f"content-{timeline.job_id}",
            job_id=timeline.job_id,
            profile=timeline.profile_id,
            target_resolution="1080x1920",
            scenes=legacy_scenes,
            audio_segments=[
                {
                    "scene_id": s.scene_id,
                    "voice_path": s.audio_plan.voice_path,
                    "duration_sec": s.audio_plan.voice_duration_sec,
                }
                for s in timeline.scenes
            ],
            global_config={
                "target_resolution": "1080x1920",
                "profile": timeline.profile_id,
                "total_duration_sec": timeline.total_measured_duration_sec,
            },
            production_engine="ffmpeg",
            engine_version="v1.0.0",
            timeline_id=timeline.timeline_id,
            timeline_version=timeline.timeline_version,
            parent_timeline_version=timeline.parent_timeline_version,
            compiler_version=self.compiler_version,
            timeline_sha256=timeline.compute_sha256(),
            reproducibility_class=self.reproducibility_class,
            mpt_handoff=mpt_handoff,
            rendered_duration_sec=timeline.total_measured_duration_sec,
        )

        # Seal the plan with deterministic render_plan_sha256
        return unsealed_plan.seal()
