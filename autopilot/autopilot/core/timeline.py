"""3-Tier Timeline Architecture — Cross-Phase 0.
Defines the canonical data models for:
  1. IntentTimeline (Planning / Director Output)
  2. MaterializedTimeline (Post-Acquisition Ground Truth)
  3. RenderPlan (Immutable Execution EDL & MPT Handoff)

Includes versioning, lineage tracking, deterministic hashing, and stale detection.
"""
from __future__ import annotations

import json
import hashlib
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional, List, Dict, Any, Union, Tuple

from pydantic import BaseModel, Field, field_validator, model_validator


TIMELINE_SCHEMA_VERSION = "v4.0.0"


# ---------------------------------------------------------------------------
# Canonical Serialization & Hashing Utilities
# ---------------------------------------------------------------------------

def canonical_json_dumps(obj: Any) -> str:
    """Produce deterministic canonical JSON representation for hashing and storage."""
    if isinstance(obj, BaseModel):
        data = obj.model_dump(mode="json")
    elif isinstance(obj, dict):
        data = obj
    elif hasattr(obj, "__dict__"):
        data = obj.__dict__
    else:
        data = obj
    return json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)


def compute_payload_sha256(payload: Union[BaseModel, Dict[str, Any], str, bytes]) -> str:
    """Compute SHA-256 hex digest of a canonical payload."""
    if isinstance(payload, bytes):
        raw_bytes = payload
    elif isinstance(payload, str):
        raw_bytes = payload.encode("utf-8")
    else:
        raw_bytes = canonical_json_dumps(payload).encode("utf-8")
    return hashlib.sha256(raw_bytes).hexdigest()


# ---------------------------------------------------------------------------
# Common Enums & Sub-Models
# ---------------------------------------------------------------------------

class NarrativeRole(str, Enum):
    HOOK = "HOOK"
    SETUP = "SETUP"
    REVEAL = "REVEAL"
    EXPLANATION = "EXPLANATION"
    ESCALATION = "ESCALATION"
    TWIST = "TWIST"
    PAYOFF = "PAYOFF"
    CTA = "CTA"
    CONTENT = "CONTENT"


class ShotType(str, Enum):
    AERIAL = "AERIAL"
    WIDE = "WIDE"
    MEDIUM = "MEDIUM"
    CLOSE_UP = "CLOSE_UP"
    MACRO = "MACRO"
    DIAGRAM = "DIAGRAM"
    MAP = "MAP"
    TEXT_ONLY = "TEXT_ONLY"
    PHOTO = "PHOTO"
    VIDEO = "VIDEO"


class VisualCoverageClass(str, Enum):
    EXACT_MATCH = "EXACT_MATCH"
    STRONG_MATCH = "STRONG_MATCH"
    CONTEXTUAL_MATCH = "CONTEXTUAL_MATCH"
    WEAK_MATCH = "WEAK_MATCH"
    MISMATCH = "MISMATCH"


class VisualAssertionLevel(str, Enum):
    LITERAL = "literal"
    SCHEMATIC = "schematic"
    ILLUSTRATIVE = "illustrative"
    METAPHORICAL = "metaphorical"


class PlatformSafeZone(str, Enum):
    YOUTUBE_SHORTS = "youtube_shorts"
    INSTAGRAM_REELS = "instagram_reels"
    TIKTOK = "tiktok"
    STANDARD_9_16 = "standard_9_16"


class CaptionPosition(str, Enum):
    TOP = "TOP"
    CENTER = "CENTER"
    LOWER = "LOWER"
    LOWER_LEFT = "LOWER_LEFT"
    LOWER_RIGHT = "LOWER_RIGHT"


class ReproducibilityClass(str, Enum):
    EXACT_REPRODUCIBLE = "EXACT_REPRODUCIBLE"
    CONFIGURATION_REPRODUCIBLE = "CONFIGURATION_REPRODUCIBLE"
    TRACE_REPRODUCIBLE = "TRACE_REPRODUCIBLE"


class TargetTiming(BaseModel):
    target_duration_sec: float = Field(..., gt=0, description="Target duration in seconds")
    target_start_sec: Optional[float] = Field(default=None, ge=0)
    target_end_sec: Optional[float] = Field(default=None, ge=0)


class WordTimestamp(BaseModel):
    word: str = Field(..., min_length=1)
    start: float = Field(..., ge=0)
    end: float = Field(..., ge=0)
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_bounds(self) -> WordTimestamp:
        if self.end < self.start:
            raise ValueError(f"Word end timestamp ({self.end}) cannot be earlier than start ({self.start}) for word '{self.word}'")
        return self


# ---------------------------------------------------------------------------
# Intent Timeline Models (Planning / Director Output)
# ---------------------------------------------------------------------------

class IntentNarration(BaseModel):
    text: str = Field(..., min_length=1, description="Raw spoken narration text")
    normalized_pronunciation: Optional[str] = Field(default=None, description="Pronunciation-normalized spoken text")
    voice_id: str = Field(default="en-US-GuyNeural")
    prosody_style: str = Field(default="energetic")
    pronunciation_overrides: Dict[str, str] = Field(default_factory=dict)


class VisualRequirements(BaseModel):
    visual_concept: str = Field(..., min_length=1)
    required_shot_type: ShotType = Field(default=ShotType.AERIAL)
    shot_spec: Optional[str] = None
    b_roll_search_query: Optional[str] = None
    coverage_expectation: VisualCoverageClass = Field(default=VisualCoverageClass.STRONG_MATCH)
    visual_assertion_level: VisualAssertionLevel = Field(default=VisualAssertionLevel.LITERAL)
    visual_change_budget: Optional[int] = Field(default=None, ge=1, le=5)


class CaptionIntent(BaseModel):
    style_preset: str = Field(default="hormozi_yellow_pop")
    position: CaptionPosition = Field(default=CaptionPosition.LOWER)
    max_words_per_phrase: int = Field(default=3, ge=1, le=8)
    emphasis_words: List[str] = Field(default_factory=list)
    platform_safe_zone: PlatformSafeZone = Field(default=PlatformSafeZone.YOUTUBE_SHORTS)


class AudioIntent(BaseModel):
    bgm_intensity: float = Field(default=0.85, ge=0.0, le=1.0)
    sfx_cues: List[Dict[str, Any]] = Field(default_factory=list)


class TransitionIntent(BaseModel):
    transition_type: str = Field(default="cut")
    duration_sec: float = Field(default=0.0, ge=0.0)
    sfx_cue: Optional[str] = None


class QAExpectations(BaseModel):
    min_semantic_match: float = Field(default=0.70, ge=0.0, le=1.0)
    allow_contextual_broll: bool = Field(default=True)


class IntentScene(BaseModel):
    scene_id: str = Field(..., min_length=1)
    scene_version: int = Field(default=1, ge=1)
    order: int = Field(..., ge=1)
    narrative_role: NarrativeRole = Field(default=NarrativeRole.HOOK)
    timing: TargetTiming
    narration: IntentNarration
    visual_requirements: VisualRequirements
    caption_intent: CaptionIntent = Field(default_factory=CaptionIntent)
    audio_intent: AudioIntent = Field(default_factory=AudioIntent)
    transition_intent: TransitionIntent = Field(default_factory=TransitionIntent)
    qa_expectations: QAExpectations = Field(default_factory=QAExpectations)


class IntentTimeline(BaseModel):
    timeline_id: str = Field(..., min_length=1)
    timeline_version: str = Field(default="v1.0")
    job_id: str = Field(..., min_length=1)
    profile_id: str = Field(default="viral_creator")
    content_type: str = Field(default="LISTICLE")
    total_target_duration_sec: float = Field(..., gt=0)
    scenes: List[IntentScene] = Field(default_factory=list, min_length=1)
    director_metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def get_scene(self, scene_id: str) -> Optional[IntentScene]:
        for s in self.scenes:
            if s.scene_id == scene_id:
                return s
        return None

    def compute_sha256(self) -> str:
        data = self.model_dump(mode="json")
        data.pop("created_at", None)
        return compute_payload_sha256(data)


# ---------------------------------------------------------------------------
# Materialized Timeline Models (Post-Acquisition Ground Truth)
# ---------------------------------------------------------------------------

class MaterializedTiming(BaseModel):
    start_time_sec: float = Field(..., ge=0.0)
    end_time_sec: float = Field(..., gt=0.0)
    duration_sec: float = Field(..., gt=0.0)

    @model_validator(mode="after")
    def validate_durations(self) -> MaterializedTiming:
        if self.end_time_sec <= self.start_time_sec:
            raise ValueError(f"end_time_sec ({self.end_time_sec}) must be greater than start_time_sec ({self.start_time_sec})")
        return self


class MaterializedNarration(BaseModel):
    text: str = Field(..., min_length=1)
    normalized_pronunciation: Optional[str] = None
    voice_id: str = Field(default="en-US-GuyNeural")
    prosody_style: str = Field(default="energetic")
    audio_artifact_path: str = Field(..., min_length=1)
    word_timestamps: List[WordTimestamp] = Field(default_factory=list)


class TrimRange(BaseModel):
    in_sec: float = Field(default=0.0, ge=0.0)
    out_sec: Optional[float] = Field(default=None, ge=0.0)


class CropFraming(BaseModel):
    saliency_x: float = Field(default=0.5, ge=0.0, le=1.0)
    saliency_y: float = Field(default=0.5, ge=0.0, le=1.0)
    safe_margin: float = Field(default=0.15, ge=0.0, le=0.5)
    caption_safe_zone: CaptionPosition = Field(default=CaptionPosition.LOWER)


class SelectedAsset(BaseModel):
    asset_id: str = Field(..., min_length=1)
    asset_path: str = Field(..., min_length=1)
    media_type: str = Field(default="video")
    provenance: Dict[str, Any] = Field(default_factory=dict)
    trim_range: TrimRange = Field(default_factory=TrimRange)
    shot_type: ShotType = Field(default=ShotType.AERIAL)
    shot_spec: Optional[str] = None
    camera_motion: str = Field(default="slow_push")
    crop_framing: CropFraming = Field(default_factory=CropFraming)
    color_grade: str = Field(default="cinematic_warm")
    start_offset_sec: float = Field(default=0.0, ge=0.0)
    duration_sec: Optional[float] = Field(default=None, gt=0.0)


class CaptionPhrase(BaseModel):
    phrase_text: str = Field(..., min_length=1)
    start_sec: float = Field(..., ge=0.0)
    end_sec: float = Field(..., gt=0.0)
    words: List[WordTimestamp] = Field(default_factory=list)
    emphasis_words: List[str] = Field(default_factory=list)


class MaterializedCaptionPlan(BaseModel):
    style_preset: str = Field(default="hormozi_yellow_pop")
    position: CaptionPosition = Field(default=CaptionPosition.LOWER)
    phrases: List[CaptionPhrase] = Field(default_factory=list)
    platform_safe_zone: PlatformSafeZone = Field(default=PlatformSafeZone.YOUTUBE_SHORTS)


class SFXEvent(BaseModel):
    cue: str = Field(..., min_length=1)
    time_sec: float = Field(..., ge=0.0)
    volume_db: float = Field(default=-6.0)


class MaterializedAudioPlan(BaseModel):
    voice_path: str = Field(..., min_length=1)
    voice_duration_sec: float = Field(..., gt=0.0)
    bgm_intensity: float = Field(default=0.85, ge=0.0, le=1.0)
    sfx_events: List[SFXEvent] = Field(default_factory=list)
    sample_rate: int = Field(default=44100, description="Audio sample rate (must be 44100 Hz / 44.1 kHz)")
    channels: int = Field(default=2, description="Audio channel count (must be 2 / stereo)")



class MaterializedTransitionPlan(BaseModel):
    type: str = Field(default="cut")
    duration_sec: float = Field(default=0.0, ge=0.0)
    sfx_cue: Optional[str] = None


class MaterializedScene(BaseModel):
    scene_id: str = Field(..., min_length=1)
    scene_version: int = Field(default=1, ge=1)
    order: int = Field(..., ge=1)
    narrative_role: NarrativeRole = Field(default=NarrativeRole.HOOK)
    timing: MaterializedTiming
    narration: MaterializedNarration
    visual_requirements: VisualRequirements
    selected_assets: List[SelectedAsset] = Field(default_factory=list, min_length=1)
    transition_plan: MaterializedTransitionPlan = Field(default_factory=MaterializedTransitionPlan)
    caption_plan: MaterializedCaptionPlan = Field(default_factory=MaterializedCaptionPlan)
    audio_plan: MaterializedAudioPlan
    qa_expectations: QAExpectations = Field(default_factory=QAExpectations)


class MaterializedTimeline(BaseModel):
    timeline_id: str = Field(..., min_length=1)
    timeline_version: str = Field(default="v1.0")
    job_id: str = Field(..., min_length=1)
    intent_timeline_id: str = Field(..., min_length=1)
    parent_timeline_version: Optional[str] = None
    profile_id: str = Field(default="viral_creator")
    content_type: str = Field(default="LISTICLE")
    total_measured_duration_sec: float = Field(..., gt=0.0)
    scenes: List[MaterializedScene] = Field(default_factory=list, min_length=1)
    audio_mix_manifest: Dict[str, Any] = Field(default_factory=dict)
    materialized_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def get_scene(self, scene_id: str) -> Optional[MaterializedScene]:
        for s in self.scenes:
            if s.scene_id == scene_id:
                return s
        return None

    def compute_sha256(self) -> str:
        data = self.model_dump(mode="json")
        data.pop("materialized_at", None)
        return compute_payload_sha256(data)


# ---------------------------------------------------------------------------
# RenderPlan Models (Immutable Renderer Instruction Object & MPT Handoff)
# ---------------------------------------------------------------------------

class MPTHandoffConfig(BaseModel):
    custom_audio_file: Optional[str] = None
    video_materials: List[str] = Field(default_factory=list)
    match_materials_to_script: bool = True
    random_material_selection: bool = False
    video_transition_mode: str = "cut"
    video_fit_mode: str = "crop"
    subtitle_display_mode: str = "animated"
    subtitle_animation: str = "pop"
    bgm_file: Optional[str] = None
    bgm_volume: float = 0.2


class RenderPlan(BaseModel):
    plan_id: str = Field(..., min_length=1)
    content_id: str = Field(..., min_length=1)
    job_id: str = Field(..., min_length=1)
    profile: str = "vertical_short"
    target_resolution: str = "1080x1920"
    scenes: List[Dict[str, Any]] = Field(default_factory=list)
    audio_segments: List[Dict[str, Any]] = Field(default_factory=list)
    global_config: Dict[str, Any] = Field(default_factory=dict)
    production_engine: str = "ffmpeg"
    engine_version: str = "v1.0.0"
    version: str = TIMELINE_SCHEMA_VERSION
    rendered_duration_sec: Optional[float] = None
    raw_speech_duration_sec: Optional[float] = None

    # Phase 0 Timeline & Manifest fields
    timeline_id: Optional[str] = None
    timeline_version: Optional[str] = None
    parent_timeline_version: Optional[str] = None
    compiler_version: str = "v4.0.0"
    compiled_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    timeline_sha256: Optional[str] = None
    render_plan_sha256: Optional[str] = None
    reproducibility_class: ReproducibilityClass = Field(default=ReproducibilityClass.EXACT_REPRODUCIBLE)
    mpt_handoff: Optional[MPTHandoffConfig] = None

    def compute_sha256(self) -> str:
        # Exclude render_plan_sha256 itself and compilation timestamp from hash calculation
        data = self.model_dump(mode="json")
        data.pop("render_plan_sha256", None)
        data.pop("compiled_at", None)
        return compute_payload_sha256(data)

    def seal(self) -> RenderPlan:
        """Compute and lock render_plan_sha256 into the plan object."""
        calculated = self.compute_sha256()
        return self.model_copy(update={"render_plan_sha256": calculated})


# ---------------------------------------------------------------------------
# Versioning, Lineage & Stale Invalidation Detection
# ---------------------------------------------------------------------------

class StaleDefectCode(str, Enum):
    STALE_TIMELINE = "STALE_TIMELINE"
    STALE_ASSET = "STALE_ASSET"
    STALE_AUDIO = "STALE_AUDIO"
    STALE_CAPTION = "STALE_CAPTION"
    STALE_QA = "STALE_QA"
    STALE_APPROVAL = "STALE_APPROVAL"


def detect_stale_timeline(
    render_plan: RenderPlan,
    current_timeline: MaterializedTimeline,
) -> Tuple[bool, Optional[StaleDefectCode], Optional[str]]:
    """Verify if a RenderPlan is stale relative to current MaterializedTimeline."""
    if render_plan.timeline_id != current_timeline.timeline_id:
        return True, StaleDefectCode.STALE_TIMELINE, f"RenderPlan timeline_id '{render_plan.timeline_id}' does not match current '{current_timeline.timeline_id}'"
    
    if render_plan.timeline_version != current_timeline.timeline_version:
        return True, StaleDefectCode.STALE_TIMELINE, f"RenderPlan timeline_version '{render_plan.timeline_version}' differs from current '{current_timeline.timeline_version}'"

    current_hash = current_timeline.compute_sha256()
    if render_plan.timeline_sha256 and render_plan.timeline_sha256 != current_hash:
        return True, StaleDefectCode.STALE_TIMELINE, f"RenderPlan timeline_sha256 mismatch (expected '{current_hash}', got '{render_plan.timeline_sha256}')"

    return False, None, None


def detect_stale_scene_version(
    scene: MaterializedScene,
    expected_version: int,
) -> Tuple[bool, Optional[StaleDefectCode], Optional[str]]:
    """Detect if a materialized scene is stale relative to expected version."""
    if scene.scene_version != expected_version:
        return True, StaleDefectCode.STALE_ASSET, f"Scene '{scene.scene_id}' version {scene.scene_version} is stale; expected {expected_version}"
    return False, None, None


# ---------------------------------------------------------------------------
# Adapters & Conversion Helpers
# ---------------------------------------------------------------------------

def script_document_to_intent_timeline(
    script: Any,
    job_id: str,
    profile_id: str = "viral_creator",
    content_type: str = "LISTICLE",
) -> IntentTimeline:
    """Convert an existing ScriptDocument into a canonical IntentTimeline."""
    scenes: List[IntentScene] = []
    total_dur = 0.0

    raw_scenes = getattr(script, "scenes", [])
    for idx, s in enumerate(raw_scenes, start=1):
        dur = float(getattr(s, "estimated_duration_seconds", 5.0) or 5.0)
        total_dur += dur
        
        # Determine narrative role
        role = NarrativeRole.HOOK if idx == 1 else (NarrativeRole.CTA if idx == len(raw_scenes) else NarrativeRole.CONTENT)
        
        intent_scene = IntentScene(
            scene_id=str(getattr(s, "scene_id", f"scene-{idx:02d}")),
            scene_version=1,
            order=idx,
            narrative_role=role,
            timing=TargetTiming(target_duration_sec=dur),
            narration=IntentNarration(
                text=str(getattr(s, "narration", "") or ""),
                normalized_pronunciation=str(getattr(s, "narration", "") or ""),
            ),
            visual_requirements=VisualRequirements(
                visual_concept=str(getattr(s, "visual_intent", "") or f"Visual for scene {idx}"),
                required_shot_type=ShotType.AERIAL,
                b_roll_search_query=getattr(s, "asset_query", None),
            ),
            caption_intent=CaptionIntent(
                emphasis_words=list(getattr(s, "emphasis_words", []) or []),
            ),
            transition_intent=TransitionIntent(
                transition_type=str(getattr(s, "transition_hint", "cut") or "cut"),
            ),
        )
        scenes.append(intent_scene)

    if not scenes:
        # Fallback single scene
        scenes.append(
            IntentScene(
                scene_id="scene-01",
                scene_version=1,
                order=1,
                narrative_role=NarrativeRole.HOOK,
                timing=TargetTiming(target_duration_sec=5.0),
                narration=IntentNarration(text="Introduction scene"),
                visual_requirements=VisualRequirements(visual_concept="Intro visual"),
            )
        )
        total_dur = 5.0

    return IntentTimeline(
        timeline_id=f"tl-{job_id}",
        timeline_version="v1.0",
        job_id=job_id,
        profile_id=profile_id,
        content_type=content_type,
        total_target_duration_sec=round(total_dur, 2),
        scenes=scenes,
        director_metadata={
            "topic": getattr(script, "topic", ""),
            "working_title": getattr(script, "working_title", ""),
            "hook": getattr(script, "hook", ""),
            "target_platform": getattr(script, "target_platform", "youtube"),
        },
    )


# ---------------------------------------------------------------------------
# Lazy Re-Exports from Timeline Compiler (Cross-Phase 0.5)
# ---------------------------------------------------------------------------

from autopilot.core.timeline_compiler import (
    HardInvariantCode,
    TimelineValidationError,
    TimelineCompilationError,
    CompilationResult,
    TimelineCompiler,
)
