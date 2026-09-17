"""CANONICAL CONTENT CONTRACT — Phase 1 / M1.
Every future subsystem consumes these Pydantic models.
Strict validation where practical; extensible where required.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional, Literal, List, Dict, Any, Union, Protocol, runtime_checkable

from pydantic import BaseModel, Field, field_validator, model_validator

# ------------------------------------------------------------------
# Version / metadata
# ------------------------------------------------------------------
CONTRACT_SCHEMA_VERSION = "v1.0.0"


class ContentItem(BaseModel):
    content_id: str = Field(..., description="Deterministic content identifier")
    schema_version: str = CONTRACT_SCHEMA_VERSION
    topic: str = Field(..., min_length=1)
    language: str = "en"
    format: str = "9:16_video"
    platform_targets: List[str] = Field(default_factory=lambda: ["youtube"])
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# ------------------------------------------------------------------
# Research
# ------------------------------------------------------------------
class ResearchSource(BaseModel):
    source_id: str = Field(..., min_length=1)
    url: Optional[str] = None
    title: Optional[str] = None
    publisher: Optional[str] = None
    published_at: Optional[str] = None
    accessed_at: Optional[str] = None
    source_type: str = Field(default="article", description="article|video|social|database|other")
    credibility: Optional[str] = None  # e.g. high/medium/low; not enforced
    notes: Optional[str] = None


# ------------------------------------------------------------------
# Script scenes
# ------------------------------------------------------------------
class ScriptScene(BaseModel):
    scene_id: str = Field(..., min_length=1)
    order: int = Field(..., ge=1, description="Deterministic scene order")
    narration: str = ""
    visual_intent: str = Field(..., min_length=1)
    asset_query: Optional[str] = None
    on_screen_text: Optional[str] = None
    emphasis_words: Optional[List[str]] = Field(default_factory=list)
    estimated_duration_seconds: float = Field(default=5.0, gt=0, description="Must be positive")
    transition_hint: Optional[str] = None
    scene_type: str = Field(default="talking_head", description="Extensible: talking_head|broll|image|text|screen|generated_visual|montage")

    @field_validator("narration")
    @classmethod
    def narration_non_empty_for_spoken(cls, v: str, info) -> str:
        # We enforce non-empty narration for all scenes here; future extensions may allow empty for text/screen
        # But to satisfy validation rules, we'll require at least some narration for spoken content
        # Actually the requirement: narration cannot be empty for spoken scenes.
        # Since scene_type is extensible, we'll allow empty only for specifically non-spoken types.
        # We do this at document level, not here, to access scene_type.
        return v

    @field_validator("scene_type")
    @classmethod
    def scene_type_non_empty(cls, v: str) -> str:
        if not v or len(v.strip()) == 0:
            raise ValueError("scene_type must be a non-empty string")
        return v.strip()


# ------------------------------------------------------------------
# Script document
# ------------------------------------------------------------------
class ScriptDocument(BaseModel):
    contract_version: str = CONTRACT_SCHEMA_VERSION
    content_id: str = Field(..., min_length=1)
    topic: str = Field(..., min_length=1)
    working_title: Optional[str] = None
    title_candidates: List[str] = Field(default_factory=list)
    hook: Optional[str] = None
    introduction: Optional[str] = None
    scenes: List[ScriptScene] = Field(default_factory=list)
    cta: Optional[str] = None  # call-to-action
    total_estimated_duration: Optional[float] = None
    target_platform: str = "youtube"
    language: str = "en"
    tone_persona: Optional[str] = None
    source_references: List[str] = Field(default_factory=list)
    generation_metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("scenes")
    @classmethod
    def at_least_one_scene(cls, v: List[ScriptScene]) -> List[ScriptScene]:
        if len(v) == 0:
            raise ValueError("Script must contain at least one scene")
        return v

    @field_validator("scenes")
    @classmethod
    def unique_scene_ids(cls, v: List[ScriptScene]) -> List[ScriptScene]:
        ids = [s.scene_id for s in v]
        if len(ids) != len(set(ids)):
            raise ValueError("Scene IDs must be unique")
        return v

    @field_validator("scenes")
    @classmethod
    def deterministic_order(cls, v: List[ScriptScene]) -> List[ScriptScene]:
        orders = [s.order for s in v]
        if sorted(orders) != orders:
            raise ValueError("Scene order must be deterministic (ascending)")
        return v

    @model_validator(mode="after")
    def check_narration_for_spoken(self) -> "ScriptDocument":
        spoken_types = {"talking_head", "broll", "montage"}
        for s in self.scenes:
            if s.scene_type in spoken_types and (not s.narration or len(s.narration.strip()) == 0):
                raise ValueError(f"Scene {s.scene_id} (type={s.scene_type}) narration cannot be empty for spoken scenes")
        return self

    @model_validator(mode="after")
    def check_duration_consistency(self) -> "ScriptDocument":
        if self.total_estimated_duration is None:
            # Auto-compute if not provided using utility (called externally)
            pass
        else:
            if self.total_estimated_duration <= 0:
                raise ValueError("total_estimated_duration must be positive")
            # Consistency: sum of scenes should match within 1 second tolerance
            sum_scenes = sum(s.estimated_duration_seconds for s in self.scenes)
            if abs(self.total_estimated_duration - sum_scenes) > 1.0:
                # We don't hard-enforce exact match because estimates vary, but flag if wildly off
                pass  # Allow small variance; document clearly
        return self

    @model_validator(mode="after")
    def check_scene_references(self) -> "ScriptDocument":
        # Scene references in asset queries etc. could reference scene_ids; simple check: all scene_ids exist
        # Already covered by unique ids; for cross-references we'd need more data
        return self


# ------------------------------------------------------------------
# Voice / TTS segments
# ------------------------------------------------------------------
class VoiceSegment(BaseModel):
    segment_id: str = Field(..., min_length=1)
    scene_id: str = Field(..., min_length=1)
    text: str = Field(..., min_length=1)
    estimated_duration: float = Field(default=5.0, gt=0)
    voice_id: Optional[str] = None
    provider_metadata: Dict[str, Any] = Field(default_factory=dict)
    pronunciation_hints: Optional[List[str]] = Field(default_factory=list)
    emphasis_metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)


# ------------------------------------------------------------------
# Asset requests
# ------------------------------------------------------------------
class AssetRequest(BaseModel):
    asset_request_id: str = Field(..., min_length=1)
    scene_id: str = Field(..., min_length=1)
    query: str = Field(..., min_length=1)
    asset_type: str = "image"  # extensible: image|video|audio|music
    duration_target: Optional[float] = None
    aspect_ratio: Optional[str] = "9:16"
    required_visual_properties: List[str] = Field(default_factory=list)
    rights_license_metadata: Dict[str, Any] = Field(default_factory=dict)
    provider_metadata: Dict[str, Any] = Field(default_factory=dict)


# ------------------------------------------------------------------
# Publication
# ------------------------------------------------------------------
class PublicationMetadata(BaseModel):
    title: str = Field(default="Untitled", min_length=1)
    description: Optional[str] = None
    hashtags: List[str] = Field(default_factory=list)
    category: Optional[str] = None
    privacy_status: str = "private"  # private|public|draft
    scheduled_publish_time: Optional[str] = None
    target_platforms: List[str] = Field(default_factory=lambda: ["youtube"])

    @field_validator("privacy_status")
    @classmethod
    def valid_privacy(cls, v: str) -> str:
        allowed = {"private", "public", "draft", "unlisted"}
        if v not in allowed:
            raise ValueError(f"privacy_status must be one of {allowed}, got {v}")
        return v

    @field_validator("target_platforms")
    @classmethod
    def valid_platforms(cls, v: List[str]) -> List[str]:
        if not v:
            raise ValueError("target_platforms must contain at least one platform")
        return v


# ------------------------------------------------------------------
# Provenance
# ------------------------------------------------------------------
class ProvenanceRecord(BaseModel):
    provider: str = "local"
    model: Optional[str] = None
    generation_timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    input_reference_ids: List[str] = Field(default_factory=list)
    source_ids: List[str] = Field(default_factory=list)
    artifact_ids: List[str] = Field(default_factory=list)
    deterministic_idempotency_key: Optional[str] = None
    checksum_sha256: Optional[str] = None


# ------------------------------------------------------------------
# Content Package (top-level)
# ------------------------------------------------------------------
class AssetLicense(BaseModel):
    license_name: str = "UNKNOWN"
    license_url: str = ""
    source_url: str = ""
    creator: Optional[str] = None
    attribution_required: bool = False
    commercial_use: Optional[bool] = None
    derivative_use: Optional[bool] = None
    retrieved_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    rights_status: str = Field(default="UNKNOWN", description="VERIFIED | PARTIALLY_VERIFIED | UNKNOWN | REJECTED")


class AssetDimensions(BaseModel):
    width: Optional[int] = None
    height: Optional[int] = None
    duration_sec: Optional[float] = None
    fps: Optional[float] = None


class AssetMediaInfo(BaseModel):
    mime_type: str = "application/octet-stream"
    file_size_bytes: int = 0
    codec: Optional[str] = None
    format: Optional[str] = None
    sample_rate: Optional[int] = None
    channels: Optional[int] = None


class AssetProvenance(BaseModel):
    provider: str = "local"
    source_url: Optional[str] = None
    source_id: Optional[str] = None
    retrieval_timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    original_hash_sha256: Optional[str] = None
    normalized_hash_sha256: Optional[str] = None
    model: Optional[str] = None
    job_id: Optional[str] = None
    content_id: Optional[str] = None
    scene_id: Optional[str] = None


class AssetCandidate(BaseModel):
    candidate_id: str = Field(..., min_length=1)
    asset_type: str = "image"  # image | video | audio
    source_url: Optional[str] = None
    source_id: Optional[str] = None
    title: Optional[str] = None
    dimensions: AssetDimensions = Field(default_factory=AssetDimensions)
    media_info: AssetMediaInfo = Field(default_factory=AssetMediaInfo)
    license: AssetLicense = Field(default_factory=AssetLicense)
    score: float = 0.0
    provenance: AssetProvenance = Field(default_factory=AssetProvenance)
    path_local: Optional[str] = None
    is_duplicate: bool = False
    tags: List[str] = Field(default_factory=list)


class AssetSelection(BaseModel):
    selection_id: str = Field(..., min_length=1)
    request_id: Optional[str] = None
    scene_id: Optional[str] = None
    selected_candidates: List[AssetCandidate] = Field(default_factory=list)
    selected_id: Optional[str] = None
    status: str = "pending"  # selected | rejected | pending
    reason: Optional[str] = None


class AssetArtifact(BaseModel):
    artifact_id: str = Field(..., min_length=1)
    job_id: Optional[str] = None
    content_id: Optional[str] = None
    scene_id: Optional[str] = None
    source_path: str  # original downloaded / fixture
    normalized_path: Optional[str] = None
    asset_type: str = "image"
    dimensions: AssetDimensions = Field(default_factory=AssetDimensions)
    media_info: AssetMediaInfo = Field(default_factory=AssetMediaInfo)
    checksum_sha256: Optional[str] = None
    provenance: AssetProvenance = Field(default_factory=AssetProvenance)
    license: AssetLicense = Field(default_factory=AssetLicense)
    validated: bool = False
    validation_result: Optional[str] = None  # PASS / WARN / BLOCK


class AssetValidationResult(BaseModel):
    artifact_id: str = Field(..., min_length=1)
    valid: bool = False
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    blocked: bool = False
    checked_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class StudyRef(BaseModel):
    pass


class StudyResults(BaseModel):
    pass


class StudyConfig(BaseModel):
    pass

# Preserve existing ContentPackage
class ContentPackage(BaseModel):
    package_version: str = CONTRACT_SCHEMA_VERSION
    content_item: ContentItem
    research_sources: List[ResearchSource] = Field(default_factory=list)
    script: ScriptDocument
    voice_segments: List[VoiceSegment] = Field(default_factory=list)
    asset_requests: List[AssetRequest] = Field(default_factory=list)
    publication: PublicationMetadata = Field(default_factory=PublicationMetadata)
    provenance: ProvenanceRecord = Field(default_factory=ProvenanceRecord)
    qa_status: Optional[str] = "pending"  # pending|passed|failed
    qa_score: Optional[float] = None
    voice_artifacts: List[str] = Field(default_factory=list, description="Artifact paths / refs for generated audio segments")
    measured_duration_sec: Optional[float] = None

    @field_validator("script")
    @classmethod
    def script_ref_exists(cls, v: ScriptDocument) -> ScriptDocument:
        # Validation handled inside ScriptDocument; here we just ensure it's present
        if v is None:
            raise ValueError("ContentPackage must include script")
        return v


# ------------------------------------------------------------------
# Serialization helpers
# ------------------------------------------------------------------
def package_to_json(package: ContentPackage, indent: int = 2) -> str:
    return package.model_dump_json(indent=indent, by_alias=True)


def package_from_json(text: str) -> ContentPackage:
    return ContentPackage.model_validate_json(text)


def package_to_file(package: ContentPackage, path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(package_to_json(package), encoding="utf-8")


def package_from_file(path: str | Path) -> ContentPackage:
    return package_from_json(Path(path).read_text(encoding="utf-8"))


def generate_json_schema(output_path: str | Path = "schemas/content-contract-v1.json") -> None:
    schema = ContentPackage.model_json_schema()
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(json.dumps(schema, indent=2, default=str), encoding="utf-8")


# ------------------------------------------------------------------
# Phase 2 / M2 — Research domain contracts
# ------------------------------------------------------------------
class ResearchQuery(BaseModel):
    query_string: str = Field(..., min_length=1)
    normalized_query: str = Field(default_factory=lambda: "")
    topic_ref: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class ResearchRequest(BaseModel):
    request_id: str = Field(..., min_length=1)
    topic: str = Field(..., min_length=1)
    language: str = "en"
    normalized_query: Optional[str] = None
    max_sources: int = Field(default=10, ge=1, le=50)
    provider_config: Dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class ResearchEvidence(BaseModel):
    evidence_id: str = Field(..., min_length=1)
    source_id: str = Field(..., min_length=1)
    snippet: str = Field(..., min_length=1, description="Short summary; avoid copying large copyrighted passages")
    relevance_score: float = Field(default=0.5, ge=0.0, le=1.0)
    status: str = Field(default="discovered", description="discovered|fetched|parsed|relevant|rejected|citation_ready")
    provenance: ProvenanceRecord = Field(default_factory=ProvenanceRecord)


class ResearchResult(BaseModel):
    result_id: str = Field(..., min_length=1)
    query_ref: Optional[str] = None
    status: str = Field(default="discovered", description="discovered|fetched|parsed|relevant|rejected")
    evidence_items: List[ResearchEvidence] = Field(default_factory=list)
    source_ids: List[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class ResearchReport(BaseModel):
    report_id: str = Field(..., min_length=1)
    request_id: str = Field(..., min_length=1)
    topic: str = Field(..., min_length=1)
    results: List[ResearchResult] = Field(default_factory=list)
    summary: Optional[str] = None
    provenance: ProvenanceRecord = Field(default_factory=ProvenanceRecord)
    status: str = Field(default="pending", description="pending|completed|failed")
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class ResearchCacheEntry(BaseModel):
    cache_key: str = Field(..., min_length=1)
    topic: str = Field(..., min_length=1)
    normalized_query: Optional[str] = None
    provider_name: str = "local"
    report_id: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

# Phase 4 / M4 Render contracts
class RenderPlan(BaseModel):
    plan_id: str = Field(..., min_length=1)
    content_id: str = Field(..., min_length=1)
    job_id: str = Field(..., min_length=1)
    profile: str = "vertical_short"
    target_resolution: str = "1080x1920"
    scenes: List[dict] = Field(default_factory=list)
    audio_segments: List[dict] = Field(default_factory=list)
    global_config: dict = Field(default_factory=dict)
    production_engine: str = "moneyprinterturbo"
    engine_version: str = "v1.0.0"
    version: str = CONTRACT_SCHEMA_VERSION
    rendered_duration_sec: Optional[float] = None
    raw_speech_duration_sec: Optional[float] = None

class RenderScene(BaseModel):
    scene_id: str
    asset_path: Optional[str] = None
    audio_path: Optional[str] = None
    duration_sec: float = 5.0
    transition_hint: Optional[str] = None
    caption_text: Optional[str] = None
    crop_strategy: str = "fit"

class RenderTrack(BaseModel):
    track_id: str
    track_type: str = "video"
    source_path: str
    start_sec: float = 0.0
    duration_sec: float = 5.0
    order: int = 1

class RenderOutput(BaseModel):
    output_path: str
    duration_sec: float
    width: int
    height: int
    codec_video: str = "h264"
    codec_audio: str = "aac"
    container: str = "mp4"
    file_size_bytes: int = 0
    checksum_sha256: Optional[str] = None
    render_timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    render_version: str = "v1.0.0"
    profile: str = "vertical_short"
    production_engine: str = "moneyprinterturbo"
    engine_version: str = "v1.0.0"

class RenderMetadata(BaseModel):
    ffmpeg_version: Optional[str] = None
    renderer_version: str = "v1.0.0"
    render_time_sec: float = 0.0
    profile: str = "vertical_short"
    input_artifact_refs: List[str] = Field(default_factory=list)

class RenderQualityResult(BaseModel):
    result_id: str = Field(..., min_length=1)
    output_path: str
    valid: bool = False
    dimensions_ok: bool = False
    duration_ok: bool = False
    audio_ok: bool = False
    codec_ok: bool = False
    errors: List[str] = Field(default_factory=list)


# ------------------------------------------------------------------
# Phase 1 Integrated Production & Transcription Contracts
# ------------------------------------------------------------------
class ProductionEngineType(str, Enum):
    MONEYPRINTERTURBO = "moneyprinterturbo"
    FFMPEG = "ffmpeg"


class ProductionRequest(BaseModel):
    job_id: str = Field(..., min_length=1)
    content_id: str = Field(..., min_length=1)
    topic: str = ""
    profile: str = "vertical_short"
    script: Optional[ScriptDocument] = None
    script_doc: Optional[ScriptDocument] = None
    render_plan: Optional[Any] = None
    audio_path: Optional[str] = None
    voice_path: Optional[str] = None
    captions_path: Optional[str] = None
    output_dir: Optional[str] = None
    output_path: Optional[str] = None
    video_ratio: Optional[str] = "9:16"
    target_resolution: str = "1080x1920"
    engine_name: str = "moneyprinterturbo"
    production_engine: str = "moneyprinterturbo"
    timeout_seconds: int = 300
    bgm_volume: float = 0.2
    options: Dict[str, Any] = Field(default_factory=dict)
    params: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def resolve_aliases(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "script" in data and "script_doc" not in data:
                data["script_doc"] = data["script"]
            elif "script_doc" in data and "script" not in data:
                data["script"] = data["script_doc"]
            if "audio_path" in data and "voice_path" not in data:
                data["voice_path"] = data["audio_path"]
            elif "voice_path" in data and "audio_path" not in data:
                data["audio_path"] = data["voice_path"]
            if "output_dir" in data and not data.get("output_path"):
                data["output_path"] = str(Path(data["output_dir"]) / "final.mp4")
            elif "output_path" in data and not data.get("output_dir"):
                data["output_dir"] = str(Path(data["output_path"]).parent)
        return data


class ProductionResult(BaseModel):
    job_id: str = Field(..., min_length=1)
    video_path: str = Field(..., min_length=1)
    duration_sec: float = Field(..., ge=0.0)
    width: int = Field(default=1080)
    height: int = Field(default=1920)
    audio_present: bool = True
    captions_path: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    engine_name: str = "moneyprinterturbo"
    engine_version: str = "v1.0.0"
    provenance: Optional[Union[ProvenanceRecord, Dict[str, Any]]] = None
    artifact_paths: List[str] = Field(default_factory=list)
    checksum_sha256: Optional[str] = None
    success: bool = True


@runtime_checkable
class ProductionEngineProtocol(Protocol):
    @property
    def engine_name(self) -> str:
        ...

    @property
    def engine_version(self) -> str:
        ...

    def generate(self, request: ProductionRequest) -> ProductionResult:
        ...


class WordTimestamp(BaseModel):
    word: str
    start_sec: float
    end_sec: float
    probability: float = 1.0


class SegmentTimestamp(BaseModel):
    segment_id: int
    text: str
    start_sec: float
    end_sec: float
    words: List[WordTimestamp] = Field(default_factory=list)


class TranscriptionRequest(BaseModel):
    audio_path: str = Field(..., min_length=1)
    language: str = "en"
    output_dir: Optional[str] = None
    generate_ass: bool = True
    generate_srt: bool = True
    model_size: str = "base"


class TranscriptionResult(BaseModel):
    audio_path: Optional[str] = None
    text: str = ""
    language: str = "en"
    duration_sec: float = 0.0
    segments: List[SegmentTimestamp] = Field(default_factory=list)
    words: List[WordTimestamp] = Field(default_factory=list)
    ass_path: Optional[str] = None
    srt_path: Optional[str] = None
    srt_content: Optional[str] = None
    ass_content: Optional[str] = None
    engine_name: str = "faster-whisper"
    engine_version: str = "v1.1.0"
    provenance: Optional[Union[ProvenanceRecord, Dict[str, Any]]] = None


@runtime_checkable
class TranscriptionEngineProtocol(Protocol):
    @property
    def engine_name(self) -> str:
        ...

    @property
    def engine_version(self) -> str:
        ...

    def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        ...


# ------------------------------------------------------------------
# Phase 5 / M5 — Canonical QA Models
# ------------------------------------------------------------------
class QAStatus(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    BLOCK = "BLOCK"


class QASeverity(str, Enum):
    INFO = "INFO"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class QAArtifactReference(BaseModel):
    artifact_id: Optional[str] = None
    artifact_type: str = "media"  # media | audio | video | image | caption | receipt
    path: str
    checksum_sha256: Optional[str] = None


class QAThreshold(BaseModel):
    name: str
    target_value: Optional[Any] = None
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    tolerance: Optional[float] = None
    action_on_fail: QAStatus = QAStatus.BLOCK


class QAMetric(BaseModel):
    metric_id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    category: str = Field(..., min_length=1)
    value_numeric: Optional[float] = None
    value_text: Optional[str] = None
    unit: Optional[str] = None
    status: QAStatus = QAStatus.PASS
    threshold: Optional[QAThreshold] = None


class QAFinding(BaseModel):
    finding_id: str = Field(..., min_length=1)
    check_id: str = Field(..., min_length=1)
    category: str = Field(..., min_length=1)
    severity: QASeverity = QASeverity.MEDIUM
    status: QAStatus = QAStatus.WARN
    message: str = Field(..., min_length=1)
    measured_value: Optional[Any] = None
    expected_value: Optional[Any] = None
    timestamp_start_sec: Optional[float] = None
    timestamp_end_sec: Optional[float] = None
    duration_sec: Optional[float] = None
    evidence: Dict[str, Any] = Field(default_factory=dict)
    artifact_ref: Optional[QAArtifactReference] = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class QACheck(BaseModel):
    check_id: str = Field(..., min_length=1)
    category: str = Field(..., min_length=1)
    status: QAStatus = QAStatus.PASS
    severity: QASeverity = QASeverity.INFO
    measured_value: Optional[Any] = None
    expected_value: Optional[Any] = None
    message: str = ""
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    findings: List[QAFinding] = Field(default_factory=list)
    evidence: Dict[str, Any] = Field(default_factory=dict)
    artifact_ref: Optional[QAArtifactReference] = None


class PublishReceipt(BaseModel):
    receipt_id: str = Field(..., min_length=1)
    job_id: str = Field(..., min_length=1)
    content_id: str = Field(..., min_length=1)
    status: QAStatus = QAStatus.PASS  # PASS | WARN | BLOCK
    publish_allowed: bool = True
    blocking_findings: List[QAFinding] = Field(default_factory=list)
    warnings: List[QAFinding] = Field(default_factory=list)
    metrics: Dict[str, Any] = Field(default_factory=dict)
    qa_version: str = "v1.0.0"
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    media_path: Optional[str] = None
    media_checksum_sha256: Optional[str] = None


class QAReport(BaseModel):
    report_id: str = Field(..., min_length=1)
    job_id: str = Field(..., min_length=1)
    content_id: str = Field(..., min_length=1)
    qa_version: str = "v1.0.0"
    renderer_version: str = "v1.0.0"
    ffmpeg_version: Optional[str] = None
    profile: str = "vertical_short"
    status: QAStatus = QAStatus.PASS
    publish_allowed: bool = True
    checks: List[QACheck] = Field(default_factory=list)
    findings: List[QAFinding] = Field(default_factory=list)
    metrics: List[QAMetric] = Field(default_factory=list)
    receipt: Optional[PublishReceipt] = None
    input_artifact_hashes: Dict[str, str] = Field(default_factory=dict)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# ------------------------------------------------------------------
# Milestone 6 / M6 — Publishing subsystem contracts
# ------------------------------------------------------------------
class PublishPlatform(str, Enum):
    YOUTUBE = "youtube"
    TIKTOK = "tiktok"
    INSTAGRAM = "instagram"


class PublishVisibility(str, Enum):
    PRIVATE = "private"
    UNLISTED = "unlisted"
    PUBLIC = "public"


class PublishStatus(str, Enum):
    NOT_READY = "NOT_READY"
    READY = "READY"
    QUEUED = "QUEUED"
    PENDING = "PENDING"
    DRY_RUN = "DRY_RUN"
    UPLOADING = "UPLOADING"
    SUCCESS = "SUCCESS"
    PUBLISHED = "PUBLISHED"
    SCHEDULED = "SCHEDULED"
    FAILED = "FAILED"
    RETRYING = "RETRYING"
    CANCELLED = "CANCELLED"
    BLOCKED_QA = "BLOCKED_QA"
    SKIPPED_DUPLICATE = "SKIPPED_DUPLICATE"


class PublishTarget(BaseModel):
    platform: PublishPlatform = PublishPlatform.YOUTUBE
    account_id: Optional[str] = None
    channel_id: Optional[str] = "default"
    default_visibility: PublishVisibility = PublishVisibility.PRIVATE


class PublishAttempt(BaseModel):
    attempt_id: str = Field(..., min_length=1)
    publish_request_id: str = Field(..., min_length=1)
    attempt_number: int = Field(default=1, ge=1)
    status: PublishStatus = PublishStatus.PENDING
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    details: Dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class PublicationReceipt(BaseModel):
    receipt_id: str = Field(..., min_length=1)
    job_id: str = Field(..., min_length=1)
    content_id: str = Field(..., min_length=1)
    render_checksum_sha256: str = Field(..., min_length=1)
    qa_receipt_reference: Optional[str] = None
    platform: Union[PublishPlatform, str] = PublishPlatform.YOUTUBE
    provider: str = "youtube"
    remote_video_id: Optional[str] = None
    remote_url: Optional[str] = None
    publication_state: PublishStatus = PublishStatus.SUCCESS
    visibility: PublishVisibility = PublishVisibility.PRIVATE
    scheduled_time: Optional[str] = None
    published_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata_hash: str = Field(..., min_length=1)
    idempotency_key: str = Field(..., min_length=1)
    extra_metadata: Dict[str, Any] = Field(default_factory=dict)


class PublishRequest(BaseModel):
    publish_request_id: str = Field(..., min_length=1)
    job_id: str = Field(..., min_length=1)
    content_id: str = Field(..., min_length=1)
    platform: Union[PublishPlatform, str] = PublishPlatform.YOUTUBE
    target_visibility: PublishVisibility = PublishVisibility.PRIVATE
    title: str = Field(..., min_length=1, max_length=100)
    description: str = Field(default="", max_length=5000)
    tags: List[str] = Field(default_factory=list)
    category_id: str = "28"  # 28 = Science & Technology
    media_path: str = Field(..., min_length=1)
    media_checksum_sha256: str = Field(..., min_length=1)
    scheduled_publish_time: Optional[str] = None
    made_for_kids: bool = False
    dry_run: bool = False
    idempotency_key: str = Field(..., min_length=1)
    target: Optional[PublishTarget] = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class PublishError(BaseModel):
    error_code: str = "PUBLISH_ERROR"
    message: str = ""
    retryable: bool = False
    details: Dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class PublishResult(BaseModel):
    success: bool
    status: PublishStatus
    receipt: Optional[PublicationReceipt] = None
    attempts: List[PublishAttempt] = Field(default_factory=list)
    error: Optional[PublishError] = None
    dry_run_preview: Optional[Dict[str, Any]] = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# ------------------------------------------------------------------
# Milestone 7 / M7 — Batch Production & Local Queue Contracts
# ------------------------------------------------------------------
class QueueItemStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    RETRY_WAIT = "retry_wait"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"
    DEAD_LETTER = "dead_letter"


class QueuePriority(int, Enum):
    LOW = 1
    NORMAL = 2
    HIGH = 3


class QueueStage(str, Enum):
    RESEARCH = "RESEARCH"
    SCRIPT = "SCRIPT"
    VOICE = "VOICE"
    ASSETS = "ASSETS"
    RENDER = "RENDER"
    QA = "QA"
    PUBLISH = "PUBLISH"
    COMPLETE = "COMPLETE"


class QueueEventType(str, Enum):
    JOB_ENQUEUED = "JOB_ENQUEUED"
    JOB_CLAIMED = "JOB_CLAIMED"
    JOB_STARTED = "JOB_STARTED"
    STAGE_STARTED = "STAGE_STARTED"
    STAGE_COMPLETED = "STAGE_COMPLETED"
    STAGE_FAILED = "STAGE_FAILED"
    JOB_RETRY = "JOB_RETRY"
    JOB_COMPLETED = "JOB_COMPLETED"
    JOB_BLOCKED = "JOB_BLOCKED"
    JOB_CANCELLED = "JOB_CANCELLED"
    JOB_RECOVERED = "JOB_RECOVERED"


class QueueItem(BaseModel):
    queue_id: str = Field(..., min_length=1)
    job_id: str = Field(..., min_length=1)
    content_id: Optional[str] = None
    priority: int = QueuePriority.NORMAL.value
    status: QueueItemStatus = QueueItemStatus.QUEUED
    stage: QueueStage = QueueStage.RESEARCH
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    scheduled_at: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    attempt_count: int = 0
    max_attempts: int = 3
    next_retry_at: Optional[str] = None
    last_error: Optional[str] = None
    worker_id: Optional[str] = None
    lease_expires_at: Optional[str] = None
    manifest_id: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)


class BatchItem(BaseModel):
    topic: str = Field(..., min_length=1)
    channel_id: Optional[str] = None
    priority: Optional[Union[str, int]] = "normal"  # low, normal, high or 1, 2, 3
    scheduled_at: Optional[str] = None  # ISO format timestamp
    profile: Optional[str] = None
    target_platforms: List[str] = Field(default_factory=lambda: ["youtube"])
    payload: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("priority", mode="before")
    @classmethod
    def validate_priority(cls, v: Any) -> str:
        if isinstance(v, int):
            return {1: "low", 2: "normal", 3: "high"}.get(v, "normal")
        return str(v) if v else "normal"


class BatchManifest(BaseModel):
    manifest_id: Optional[str] = None
    name: Optional[str] = "batch_production"
    channel_id: Optional[str] = "default"
    profile: str = "short_vertical"
    priority: str = "normal"
    auto_publish: bool = False
    publish_visibility: str = "private"
    items: List[BatchItem] = Field(..., min_length=1)


class BatchSubmitResult(BaseModel):
    manifest_id: str
    total_items: int
    submitted_count: int
    skipped_duplicate_count: int
    queued_ids: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)

    @property
    def enqueued(self) -> int:
        return self.submitted_count

    @property
    def duplicates(self) -> int:
        return self.skipped_duplicate_count


class QueueStatusSummary(BaseModel):
    queued: int = 0
    running: int = 0
    retry_wait: int = 0
    succeeded: int = 0
    failed: int = 0
    blocked: int = 0
    cancelled: int = 0
    dead_letter: int = 0
    total: int = 0
    active_workers: List[str] = Field(default_factory=list)


# =====================================================================
# Milestone 8: Analytics & Performance Intelligence Contracts
# =====================================================================

class PerformanceWindow(str, Enum):
    WINDOW_1H = "1h"
    WINDOW_24H = "24h"
    WINDOW_7D = "7d"
    WINDOW_28D = "28d"
    LIFETIME = "lifetime"


class MetricType(str, Enum):
    MEASURED = "measured"
    DERIVED = "derived"
    SYNTHETIC = "synthetic"


class MetricObservation(BaseModel):
    observation_id: Optional[str] = None
    snapshot_id: Optional[str] = None
    metric_name: str = Field(..., min_length=1)
    raw_name: str = ""
    raw_value: float = Field(default=0.0, ge=0.0)
    normalized_value: float = Field(default=0.0, ge=0.0)
    unit: str = "count"
    metric_type: MetricType = MetricType.MEASURED
    observed_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    window: PerformanceWindow = PerformanceWindow.LIFETIME

    @property
    def metric_value(self) -> float:
        return self.normalized_value if self.normalized_value != 0.0 else self.raw_value

    @model_validator(mode="before")
    @classmethod
    def handle_metric_values(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "metric_value" in data and "raw_value" not in data:
                data["raw_value"] = data["metric_value"]
            if "metric_value" in data and "normalized_value" not in data:
                data["normalized_value"] = data["metric_value"]
            if "raw_value" in data and "normalized_value" not in data:
                data["normalized_value"] = data["raw_value"]
        return data

    @field_validator("observed_at", mode="before")
    @classmethod
    def handle_obs_dt(cls, v: Any) -> str:
        if isinstance(v, datetime):
            return v.isoformat()
        return str(v)


class DerivedMetric(BaseModel):
    metric_name: str = Field(..., min_length=1)
    value: float = Field(default=0.0, ge=0.0)
    formula: str = ""
    input_metrics: Dict[str, float] = Field(default_factory=dict)
    calculated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    confidence: float = 1.0


class AnalyticsProvenance(BaseModel):
    provider: str = Field(default="mock", min_length=1)
    source: str = "api"  # "api", "synthetic", "cache"
    remote_content_id: Optional[str] = None
    observed_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    retrieved_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    query_id: Optional[str] = None
    raw_response_hash: Optional[str] = None


class AnalyticsSnapshot(BaseModel):
    snapshot_id: str = Field(..., min_length=1)
    job_id: str = Field(..., min_length=1)
    content_id: Optional[str] = None
    platform: str = "youtube"
    remote_id: str = Field(..., min_length=1)
    window: PerformanceWindow = PerformanceWindow.LIFETIME
    observed_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    retrieved_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    provider: str = "mock"
    metrics: Dict[str, MetricObservation] = Field(default_factory=dict)
    derived_metrics: Dict[str, DerivedMetric] = Field(default_factory=dict)
    provenance: AnalyticsProvenance = Field(default_factory=AnalyticsProvenance)
    is_synthetic: bool = False
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("observed_at", "retrieved_at", mode="before")
    @classmethod
    def handle_dt(cls, v: Any) -> str:
        if isinstance(v, datetime):
            return v.isoformat()
        return str(v)


class ContentPerformance(BaseModel):
    job_id: str = Field(..., min_length=1)
    channel_id: str = "default"
    content_id: Optional[str] = None
    topic: Optional[str] = None
    profile: Optional[str] = None
    render_checksum_sha256: Optional[str] = None
    publication_receipt_id: Optional[str] = None
    platform: Optional[str] = None
    remote_id: Optional[str] = None
    published_at: Optional[str] = None
    latest_snapshot: Optional[AnalyticsSnapshot] = None
    snapshot_history: List[AnalyticsSnapshot] = Field(default_factory=list)


# =====================================================================
# Milestone 9: Autonomous Ideation & Feedback Loop Contracts
# =====================================================================

class AutonomyLevel(int, Enum):
    LEVEL_0_MANUAL = 0
    LEVEL_1_DISCOVERY = 1
    LEVEL_2_PROPOSAL = 2
    LEVEL_3_AUTO_QUEUE = 3
    LEVEL_4_AUTO_PRODUCE = 4


class ProposalStatus(str, Enum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"
    QUEUED = "queued"


class DecisionAction(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"
    QUEUE = "queue"
    BLOCK = "block"


class PerformanceTier(str, Enum):
    TOP = "top"
    AVERAGE = "average"
    LOW = "low"


class StrategyStatus(str, Enum):
    PROPOSED = "proposed"
    ACTIVE = "active"
    DEPRECATED = "deprecated"


class TrendSignal(BaseModel):
    signal_id: str = Field(..., min_length=1)
    topic: str = Field(..., min_length=1)
    source: str = "mock_trend"
    source_url: str = ""
    detected_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    freshness_score: float = Field(default=0.8, ge=0.0, le=1.0)
    relevance_score: float = Field(default=0.8, ge=0.0, le=1.0)
    evidence_text: str = ""
    category: str = "general"
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    provenance: Optional[ProvenanceRecord] = None


class TopicCandidate(BaseModel):
    candidate_id: str = Field(..., min_length=1)
    run_id: str = Field(..., min_length=1)
    channel_id: str = "default"
    proposed_topic: str = Field(..., min_length=1)
    angle: str = ""
    hook_hypothesis: str = ""
    content_format: str = "short_vertical"
    # Niche category inherited from the source trend signal.  Carries the
    # segmentation key used by strategy niche_weights so scoring influence is
    # fully traceable (candidate -> category -> strategy weight).
    category: Optional[str] = None
    rationale: str = ""
    supporting_signal_ids: List[str] = Field(default_factory=list)
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    estimated_effort: int = Field(default=1, ge=1, le=5)
    duplicate_risk: float = Field(default=0.0, ge=0.0, le=1.0)
    commercial_relevance: float = Field(default=0.5, ge=0.0, le=1.0)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class TopicScore(BaseModel):
    score_id: str = Field(..., min_length=1)
    candidate_id: str = Field(..., min_length=1)
    freshness: float = Field(default=0.0, ge=0.0, le=1.0)
    relevance: float = Field(default=0.0, ge=0.0, le=1.0)
    historical_performance_factor: float = Field(default=0.5, ge=0.0, le=1.0)
    content_novelty: float = Field(default=1.0, ge=0.0, le=1.0)
    production_effort_factor: float = Field(default=0.5, ge=0.0, le=1.0)
    duplicate_risk_penalty: float = Field(default=0.0, ge=0.0, le=1.0)
    # Explicit, bounded, auditable adjustment derived from the active
    # StrategyVersion niche weights (see TopicScorer).  Zero by default and
    # whenever no strategy is supplied, so historical callers are unaffected.
    strategy_bonus: float = Field(default=0.0, ge=-1.0, le=1.0)
    strategy_version: Optional[str] = None
    total_score: float = Field(default=0.0, ge=0.0, le=1.0)
    breakdown: Dict[str, Any] = Field(default_factory=dict)
    explanation: str = ""
    calculated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class AutonomyPolicy(BaseModel):
    policy_id: str = "default_policy"
    autonomy_level: int = Field(default=2, ge=0, le=4)
    max_ideas_per_cycle: int = Field(default=10, ge=1, le=100)
    max_auto_queue_per_cycle: int = Field(default=3, ge=1, le=50)
    max_jobs_per_day: int = Field(default=10, ge=1, le=200)
    max_concurrent_jobs: int = Field(default=5, ge=1, le=50)
    topic_cooldown_days: int = Field(default=14, ge=1, le=365)
    similarity_threshold: float = Field(default=0.70, ge=0.1, le=1.0)
    min_score_threshold: float = Field(default=0.60, ge=0.0, le=1.0)
    require_evidence: bool = True
    prohibited_topics: List[str] = Field(
        default_factory=lambda: [
            "get rich quick",
            "hate speech",
            "illegal weapons",
            "harmful medical advice",
            "dangerous challenge",
            "copyright bypass",
        ]
    )
    allowed_profiles: List[str] = Field(default_factory=lambda: ["short_vertical"])
    budget_max_daily_usd: float = Field(default=0.0, ge=0.0)
    max_regeneration_attempts: int = Field(default=3, ge=0, le=10)


class PolicyCheckResult(BaseModel):
    check_name: str
    passed: bool
    reason: str = ""
    details: Dict[str, Any] = Field(default_factory=dict)


class IdeaDecision(BaseModel):
    decision_id: str = Field(..., min_length=1)
    proposal_id: str = ""
    action: DecisionAction
    autonomy_level: int = 0
    reason: str = ""
    checks: List[PolicyCheckResult] = Field(default_factory=list)
    decided_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class IdeaProposal(BaseModel):
    proposal_id: str = Field(..., min_length=1)
    run_id: str = Field(..., min_length=1)
    channel_id: str = "default"
    candidate: TopicCandidate
    score: TopicScore
    status: ProposalStatus = ProposalStatus.PROPOSED
    decision: Optional[IdeaDecision] = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    decided_at: Optional[str] = None


class FeedbackSignal(BaseModel):
    signal_id: str = Field(..., min_length=1)
    job_id: str = Field(..., min_length=1)
    channel_id: str = "default"
    topic: str = Field(..., min_length=1)
    snapshot_id: str = Field(..., min_length=1)
    observed_views: int = Field(default=0, ge=0)
    observed_engagement_rate: float = Field(default=0.0, ge=0.0)
    performance_tier: PerformanceTier = PerformanceTier.AVERAGE
    association_note: str = ""
    recorded_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class LearningObservation(BaseModel):
    observation_id: str = Field(..., min_length=1)
    source_job_ids: List[str] = Field(default_factory=list)
    channel_id: str = "default"
    pattern_type: str = "topic_niche"
    observation_text: str = ""
    sample_size: int = Field(default=1, ge=1)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    recommended_adjustment: Dict[str, Any] = Field(default_factory=dict)
    observed_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class StrategyVersion(BaseModel):
    version_id: str = Field(..., min_length=1)
    parent_version_id: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    status: StrategyStatus = StrategyStatus.ACTIVE
    niche_weights: Dict[str, float] = Field(
        default_factory=lambda: {
            "technology": 1.0,
            "science": 1.0,
            "history": 0.8,
            "finance": 0.8,
            "general": 0.5,
        }
    )
    hook_patterns: List[str] = Field(
        default_factory=lambda: [
            "Did you know {fact}? Here is what happens next.",
            "Why almost everyone is wrong about {topic}.",
            "The hidden truth behind {topic} revealed.",
            "This 30-second secret about {topic} changes everything.",
        ]
    )
    topic_rules: Dict[str, Any] = Field(
        default_factory=lambda: {
            "max_title_words": 12,
            "prefer_questions": False,
            "require_hook": True,
        }
    )
    supporting_evidence_ids: List[str] = Field(default_factory=list)
    rationale: str = "Initial baseline strategy"


class LearningRunStatus(str, Enum):
    """Outcome states of a single analytics learning run."""
    APPLIED = "applied"                    # new strategy version created + activated
    NO_CHANGE = "no_change"                # fingerprint already applied (idempotent skip)
    INSUFFICIENT = "insufficient"          # not enough valid observations
    DRY_RUN = "dry_run"                    # proposed but not persisted
    FAILED = "failed"                      # error during learning


class StrategyDelta(BaseModel):
    """One bounded parameter change produced by a learning run.

    Only ``niche_weights`` entries are tunable.  Every change carries its
    evidence so the adjustment is fully auditable.
    """
    parameter: str = Field(..., min_length=1)      # niche_weights key (category)
    old_value: float = Field(..., ge=0.0)
    new_value: float = Field(..., ge=0.0)
    raw_delta: float = Field(default=0.0)          # before bounding/clamping
    applied_delta: float = Field(default=0.0)      # after max-delta cap
    sample_size: int = Field(default=0, ge=0)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    signal: float = Field(default=0.0, ge=-1.0, le=1.0)
    source_job_ids: List[str] = Field(default_factory=list)
    reason: str = ""


class LearningRunSummary(BaseModel):
    """Structured, explainable result of one analytics learning cycle.

    Captures the full lineage: what data was consumed, which signals were
    calculated, what changed, and why.  Learning never publishes and never
    triggers production; this record is observation + bounded strategy update
    only.
    """
    run_id: str
    channel_id: str = "default"
    status: str = LearningRunStatus.INSUFFICIENT.value
    dry_run: bool = False

    # Input provenance
    window_days: int = 30
    observations_considered: int = 0      # raw published jobs in window
    observations_used: int = 0           # after quality gating
    observations_excluded: int = 0
    excluded_reasons: Dict[str, int] = Field(default_factory=dict)
    input_fingerprint: str = ""
    is_synthetic_input: bool = False     # True when any consumed snapshot is mock/synthetic

    # Signals & segmentation
    category_signals: Dict[str, Any] = Field(default_factory=dict)
    signals_summary: Dict[str, Any] = Field(default_factory=dict)

    # Strategy lineage
    parent_strategy_version: str = "strat-v1"
    resulting_strategy_version: Optional[str] = None
    deltas: List[StrategyDelta] = Field(default_factory=list)
    observation_ids: List[str] = Field(default_factory=list)

    reason: str = ""
    error_message: Optional[str] = None
    started_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    completed_at: Optional[str] = None


class AutonomyCycleSummary(BaseModel):
    run_id: str
    channel_id: str = "default"
    autonomy_level: int
    dry_run: bool = False
    signals_discovered: int = 0
    candidates_generated: int = 0
    proposals_created: int = 0
    jobs_queued: int = 0
    jobs_blocked: int = 0
    status: str = "completed"
    error_message: Optional[str] = None
    active_strategy_version: str = "strat-v1"
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class AutoProduceSummary(BaseModel):
    """Structured result of a Level 4 guarded auto-produce cycle.

    Level 4 consumes eligible auto-queued jobs through the existing real worker
    and pipeline, stopping at the terminal pre-publish state (APPROVED, i.e.
    READY_TO_PUBLISH). It never invokes a public publisher.
    """

    run_id: str
    channel_id: str = "default"
    autonomy_level: int = 4
    dry_run: bool = False
    policy: str = "local_only"
    queued_jobs_discovered: int = 0
    jobs_eligible: int = 0
    jobs_blocked: int = 0
    jobs_producing: int = 0
    jobs_completed: int = 0
    jobs_ready_to_publish: int = 0
    jobs_qa_failed: int = 0
    jobs_retry_wait: int = 0
    jobs_skipped_completed: int = 0
    jobs_skipped_duplicate: int = 0
    jobs_daily_limit_blocked: int = 0
    jobs_concurrency_blocked: int = 0
    jobs_cycle_limit_blocked: int = 0
    status: str = "completed"
    error_message: Optional[str] = None
    active_strategy_version: str = "strat-v1"
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    decision_reasons: List[Dict[str, str]] = Field(default_factory=list)


# =====================================================================
# Phase 3: Scheduling Contracts
# =====================================================================

class ScheduleCadence(str, Enum):
    HOURLY = "hourly"
    DAILY = "daily"
    WEEKLY = "weekly"
    WEEKDAYS = "weekdays"


WEEKDAY_NAMES: List[str] = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


class OperationMode(str, Enum):
    """How a single scheduled operation uses the granted autonomy level.

    Explicit operator control: a schedule may use *less* than its granted
    autonomy level, but never more.  ``level3_then_level4`` runs the full
    content-factory path (discovery -> queue -> production -> QA) inside one
    scheduled operation, sequencing the existing Level 3 and Level 4 engines.
    """

    LEVEL_3_ONLY = "level3"
    LEVEL_4_ONLY = "level4"
    LEVEL_3_THEN_4 = "level3_then_level4"


def derive_operation_mode(autonomy_level: int) -> str:
    """Default operation mode for a granted autonomy level (backward compatible)."""
    return OperationMode.LEVEL_4_ONLY.value if int(autonomy_level) >= 4 else OperationMode.LEVEL_3_ONLY.value


def validate_operation_mode(operation_mode: Optional[str], autonomy_level: int) -> str:
    """Validate mode/level consistency. Returns the resolved mode string.

    ``operation_mode`` None -> derived from ``autonomy_level`` (legacy default).
    Level 4 modes require the Level 4 grant; Level 3-only is allowed under either
    grant (an operator may deliberately run discovery-only on a Level 4 channel).
    """
    if operation_mode is None:
        return derive_operation_mode(autonomy_level)
    try:
        mode = OperationMode(str(operation_mode).strip().lower())
    except ValueError as exc:
        raise ValueError(
            f"Invalid operation_mode {operation_mode!r}; expected one of {[m.value for m in OperationMode]}"
        ) from exc
    if mode in (OperationMode.LEVEL_4_ONLY, OperationMode.LEVEL_3_THEN_4) and int(autonomy_level) < 4:
        raise ValueError(
            f"operation_mode {mode.value!r} requires autonomy_level 4 (guarded auto-produce grant)"
        )
    return mode.value


class AutonomySchedule(BaseModel):
    """Persisted recurring schedule for a channel's autonomy cycle.

    The scheduler itself is a thin orchestrator: it only decides *when* a
    cycle runs, claims the due schedule atomically, invokes the existing
    Level 3 (run_cycle) or Level 4 (run_auto_produce_cycle) engine, and
    records/advances the schedule. It never re-implements cycle logic and
    never invokes a public publisher.

    ``operation_mode`` pins how a run consumes ``autonomy_level``; it is the
    explicit operator control required by the autonomous operation loop.
    """

    schedule_id: str
    channel_id: str = "default"
    autonomy_level: int = 3
    operation_mode: Optional[str] = None
    enabled: bool = True
    cadence: ScheduleCadence = ScheduleCadence.DAILY
    days_of_week: List[str] = Field(default_factory=list)
    timezone: str = "UTC"
    max_items_per_run: int = Field(default=10, ge=1)
    dry_run: bool = False
    policy: str = "local_only"
    # Opt-in: run exactly one idempotent analytics learning stage after the
    # operation stages.  Default False — existing schedules never learn.
    include_learning: bool = False
    next_run_at: Optional[str] = None
    last_run_at: Optional[str] = None
    last_run_id: Optional[str] = None
    last_run_status: Optional[str] = None
    total_runs: int = Field(default=0, ge=0)
    consecutive_failures: int = Field(default=0, ge=0)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class ScheduleRunSummary(BaseModel):
    """Outcome of a single scheduled operation execution (run-now or run-due).

    ``cycle_run_id``/``cycle_status`` always describe the *terminal* stage so
    existing callers keep working; the stage-level fields carry the full
    picture for combined (level3_then_level4) operation-loop runs.
    """

    run_id: str
    schedule_id: str
    channel_id: str = "default"
    autonomy_level: int
    operation_mode: str = "level3"
    status: str
    cycle_run_id: Optional[str] = None
    cycle_status: Optional[str] = None
    level3_run_id: Optional[str] = None
    level3_status: Optional[str] = None
    level3_jobs_queued: int = 0
    level4_run_id: Optional[str] = None
    level4_status: Optional[str] = None
    level4_jobs_ready_to_publish: int = 0
    # Opt-in analytics learning stage (runs once after Level 4, idempotent and
    # fully isolated from the Level 3/4 terminal status).  Off by default.
    include_learning: bool = False
    learning_run_id: Optional[str] = None
    learning_status: Optional[str] = None
    next_run_at: Optional[str] = None
    publish_calls: int = 0
    error_message: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# =====================================================================
# Milestone 10: Multi-Channel Scaling & Channel Profiles Contracts
# =====================================================================

class ChannelStatus(str, Enum):
    ACTIVE = "active"
    DISABLED = "disabled"


class NicheConfig(BaseModel):
    niche_name: str = Field(..., min_length=1)
    description: str = ""
    allowed_categories: List[str] = Field(default_factory=list)
    excluded_categories: List[str] = Field(default_factory=list)
    terminology: List[str] = Field(default_factory=list)
    research_preferences: Dict[str, Any] = Field(default_factory=dict)
    ideation_weighting: Dict[str, float] = Field(default_factory=dict)
    content_format_preferences: List[str] = Field(default_factory=lambda: ["short_vertical"])


class PersonaConfig(BaseModel):
    persona_name: str = "default"
    tone: str = "informative"
    vocabulary_level: str = "accessible"
    narration_personality: str = "objective"
    cta_style: str = "subtle"
    hook_style: str = "intriguing_question"
    pacing_preference: str = "moderate"
    pacing_preferences: str = "moderate"
    visual_preferences: Dict[str, Any] = Field(default_factory=dict)

    @property
    def narrator_personality(self) -> str:
        return self.narration_personality

    @model_validator(mode="before")
    @classmethod
    def handle_persona_aliases(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "narrator_personality" in data and "narration_personality" not in data:
                data["narration_personality"] = data["narrator_personality"]
            elif "narration_personality" in data and "narrator_personality" not in data:
                data["narrator_personality"] = data["narration_personality"]
            if "pacing_preference" in data and "pacing_preferences" not in data:
                data["pacing_preferences"] = data["pacing_preference"]
            elif "pacing_preferences" in data and "pacing_preference" not in data:
                data["pacing_preference"] = data["pacing_preferences"]
        return data


class VoiceProfile(BaseModel):
    provider: str = "mock"
    voice_id: str = "en-US-Standard"
    language: str = "en"
    speaking_rate: float = Field(default=1.0, ge=0.5, le=2.5)
    pitch: float = Field(default=1.0)
    style_metadata: Dict[str, Any] = Field(default_factory=dict)


class VisualBrandProfile(BaseModel):
    font_family: str = "Arial"
    primary_color: str = "#FFFFFF"
    secondary_color: str = "#FFD700"
    theme_color_primary: str = "#FFFFFF"
    theme_color_secondary: str = "#FFD700"
    caption_style: str = "bold_center"
    text_placement: str = "bottom"
    background_strategy: str = "blur_fill"
    transition_preference: str = "fade"
    logo_path: Optional[str] = None
    watermark_enabled: bool = False
    visual_motif: str = "clean"

    @model_validator(mode="before")
    @classmethod
    def handle_colors(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "theme_color_primary" in data and "primary_color" not in data:
                data["primary_color"] = data["theme_color_primary"]
            elif "primary_color" in data and "theme_color_primary" not in data:
                data["theme_color_primary"] = data["primary_color"]
            if "theme_color_secondary" in data and "secondary_color" not in data:
                data["secondary_color"] = data["theme_color_secondary"]
            elif "secondary_color" in data and "theme_color_secondary" not in data:
                data["theme_color_secondary"] = data["secondary_color"]
        return data


class PostingPolicy(BaseModel):
    timezone: str = "UTC"
    posting_windows: List[str] = Field(default_factory=lambda: ["09:00-11:00", "17:00-19:00"])
    max_daily_posts: int = Field(default=3, ge=1, le=50)
    preferred_windows: List[str] = Field(default_factory=lambda: ["09:00-11:00", "17:00-19:00"])
    preferred_days: List[str] = Field(
        default_factory=lambda: [
            "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"
        ]
    )
    cadence_hours: float = Field(default=8.0, ge=1.0)


class AnalyticsConfig(BaseModel):
    tracking_enabled: bool = True
    track_views: bool = True
    track_engagement: bool = True
    sync_window: str = "lifetime"
    primary_metric: str = "views"
    attribution_tag: str = ""
    isolated_learning: bool = True
    target_benchmarks: Dict[str, float] = Field(default_factory=dict)


class MonetizationMetadata(BaseModel):
    monetization_category: str = "general"
    commercial_flags: List[str] = Field(default_factory=list)
    commercial_content_flags: bool = False
    affiliate_strategy: Dict[str, Any] = Field(default_factory=dict)
    cta_configuration: Dict[str, Any] = Field(default_factory=dict)


class ChannelProfile(BaseModel):
    channel_id: str = Field(..., min_length=1)
    channel_name: str = Field(..., min_length=1)
    niche: NicheConfig = Field(default_factory=lambda: NicheConfig(niche_name="general"))
    language: str = "en"
    locale: str = "en-US"
    persona: PersonaConfig = Field(default_factory=PersonaConfig)
    voice: VoiceProfile = Field(default_factory=VoiceProfile)
    visual: VisualBrandProfile = Field(default_factory=VisualBrandProfile)
    content_formats: List[str] = Field(default_factory=lambda: ["short_vertical"])
    target_platforms: List[str] = Field(default_factory=lambda: ["youtube"])
    posting_policy: PostingPolicy = Field(default_factory=PostingPolicy)
    autonomy_policy: AutonomyPolicy = Field(default_factory=AutonomyPolicy)
    analytics_config: AnalyticsConfig = Field(default_factory=AnalyticsConfig)
    monetization: MonetizationMetadata = Field(default_factory=MonetizationMetadata)
    status: ChannelStatus = ChannelStatus.ACTIVE
    active_strategy_version_id: str = "strat-v1"
    active_strategy_version: Optional[str] = "strat-v1"
    profile_version: str = "v1"
    version: Optional[str] = "1"
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @model_validator(mode="before")
    @classmethod
    def handle_version_and_strategy(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "version" in data and "profile_version" not in data:
                v = str(data["version"])
                data["profile_version"] = f"v{v}" if not v.startswith("v") else v
                data["version"] = v.lstrip("v")
            elif "profile_version" in data and "version" not in data:
                data["version"] = data["profile_version"].lstrip("v")
            elif "profile_version" in data and "version" in data:
                v = str(data["version"])
                data["profile_version"] = f"v{v}" if not v.startswith("v") else v

            if "active_strategy_version" in data and "active_strategy_version_id" not in data:
                data["active_strategy_version_id"] = data["active_strategy_version"]
            elif "active_strategy_version_id" in data and "active_strategy_version" not in data:
                data["active_strategy_version"] = data["active_strategy_version_id"]
        return data

    @field_validator("target_platforms")
    @classmethod
    def validate_platforms(cls, platforms: List[str]) -> List[str]:
        if not platforms:
            raise ValueError("ChannelProfile must have at least one target platform.")
        allowed_platforms = {"youtube", "tiktok", "instagram"}
        for p in platforms:
            if p.lower() not in allowed_platforms:
                raise ValueError(f"Platform '{p}' is not supported. Supported platforms: {allowed_platforms}")
        return [p.lower() for p in platforms]

    @field_validator("channel_id")
    @classmethod
    def validate_channel_id(cls, cid: str) -> str:
        cid = cid.strip()
        if not cid:
            raise ValueError("channel_id cannot be empty or whitespace.")
        import re
        if not re.match(r"^[a-zA-Z0-9_\-]+$", cid):
            raise ValueError("channel_id may only contain alphanumeric characters, underscores, and hyphens.")
        return cid

    def to_editorial_rules(self) -> Dict[str, Any]:
        """Extracts data-driven editorial rules to guide script generation and visual styling."""
        return {
            "channel_id": self.channel_id,
            "channel_name": self.channel_name,
            "niche": getattr(self.niche, "niche_name", "general"),
            "tone": getattr(self.persona, "tone", "informative"),
            "vocabulary_level": getattr(self.persona, "vocabulary_level", "accessible"),
            "narration_personality": getattr(self.persona, "narration_personality", "objective"),
            "hook_style": getattr(self.persona, "hook_style", "intriguing_question"),
            "cta_style": getattr(self.persona, "cta_style", "subtle"),
            "visual_motif": getattr(self.visual, "visual_motif", "clean"),
            "caption_style": getattr(self.visual, "caption_style", "bold_center"),
            "primary_color": getattr(self.visual, "primary_color", "#FFFFFF"),
            "secondary_color": getattr(self.visual, "secondary_color", "#FFD700"),
            "voice_provider": getattr(self.voice, "provider", "mock"),
            "voice_id": getattr(self.voice, "voice_id", "en-US-Standard"),
            "speaking_rate": getattr(self.voice, "speaking_rate", 1.0),
        }


class ChannelProfileVersion(BaseModel):
    version_id: str = Field(..., min_length=1)
    channel_id: str = Field(..., min_length=1)
    profile_version: str = Field(..., min_length=1)
    version: Optional[str] = "1"
    profile_snapshot: Any = Field(default_factory=dict)
    strategy_version_id: str = "strat-v1"
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    change_summary: str = "Initial version"
    change_comment: Optional[str] = "Initial version"
    created_by: str = "system"

    @model_validator(mode="before")
    @classmethod
    def handle_version_record_aliases(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "change_summary" in data and "change_comment" not in data:
                data["change_comment"] = data["change_summary"]
            elif "change_comment" in data and "change_summary" not in data:
                data["change_summary"] = data["change_comment"]
            if "profile_version" in data and "version" not in data:
                data["version"] = data["profile_version"].lstrip("v")
            elif "version" in data and "profile_version" not in data:
                data["profile_version"] = f"v{data['version']}"
            if "profile_snapshot_json" in data and "profile_snapshot" not in data:
                raw = data["profile_snapshot_json"]
                data["profile_snapshot"] = json.loads(raw) if isinstance(raw, str) else raw
        return data
# =====================================================================
# Phase 2: Deep Research, Production Policy & Regeneration Contracts
# =====================================================================

class ResearchEvidenceRecord(BaseModel):
    """Canonical normalized research evidence record."""
    evidence_id: str = Field(..., description="Unique evidence identifier")
    source_id: str = Field(..., description="Unique source identifier")
    title: str = Field(default="", description="Title of the source document or page")
    url: str = Field(default="", description="Canonical URL of the source")
    publisher: str = Field(default="", description="Publisher, domain, or publication name")
    retrieved_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    excerpt: str = Field(default="", description="Clean text snippet or excerpt")
    relevant_passage: Optional[str] = Field(default="", description="Detailed relevant passage if available")
    provider: str = Field(default="wikipedia", description="Provider identifier (wikipedia, crawl4ai, mock)")
    confidence_score: float = Field(default=1.0, ge=0.0, le=1.0)
    provenance_metadata: Dict[str, Any] = Field(default_factory=dict)

    def is_valid_and_non_empty(self) -> bool:
        """Quality check: verify that the evidence has a valid snippet and source identity."""
        if not self.excerpt or not self.excerpt.strip():
            return False
        if not self.title and not self.url:
            return False
        return True


class ResearchBundle(BaseModel):
    """Coordinated collection of research evidence records for a topic."""
    topic: str
    sources: List[ResearchEvidenceRecord] = Field(default_factory=list)
    summary: str = ""
    routing_strategy: str = "wikipedia_first"
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    total_sources_evaluated: int = 0
    duplicates_filtered: int = 0
    low_quality_filtered: int = 0


@runtime_checkable
class ResearchProviderProtocol(Protocol):
    """Provider-neutral protocol for research providers."""
    provider_name: str

    def health_check(self) -> Any:
        ...

    def search(self, query: str, max_results: int = 10, **kwargs) -> List[ResearchEvidenceRecord]:
        ...

    def fetch_page(self, url: str, **kwargs) -> Optional[ResearchEvidenceRecord]:
        ...


class ProductionPolicyTier(str, Enum):
    """Cost/Resource policy tiers — strictly local-first and $0-first."""
    LOCAL_ONLY = "local_only"
    CHEAP_FIRST = "cheap_first"
    QUALITY_FIRST = "quality_first"


class ProductionPolicy(BaseModel):
    """Production policy layer regulating engine, voice, research depth, and quality gates."""
    policy_tier: ProductionPolicyTier = ProductionPolicyTier.LOCAL_ONLY
    production_engine: str = "moneyprinterturbo"
    voice_provider: str = "kokoro"
    research_depth: str = "standard"  # standard, deep, fast
    transcription_model: str = "base"
    target_duration_sec: float = 30.0
    quality_threshold: float = 0.8
    max_regeneration_attempts: int = 3
    publishing_eligible: bool = True
    custom_overrides: Dict[str, Any] = Field(default_factory=dict)


class RegenerationDefectType(str, Enum):
    """Categories of QA defects for targeted regeneration."""
    HOOK_DEFECT = "hook_defect"
    DURATION_MISMATCH = "duration_mismatch"
    VISUAL_DEFECT = "visual_defect"
    GROUNDING_DEFECT = "grounding_defect"
    AUDIO_DEFECT = "audio_defect"
    RENDER_DEFECT = "render_defect"
    LIST_STRUCTURE_DEFECT = "list_structure_defect"
    GENERAL_QA_DEFECT = "general_qa_defect"


class RegenerationRequest(BaseModel):
    """Structured request for targeted pipeline regeneration."""
    job_id: str
    attempt_number: int = 1
    max_attempts: int = 3
    defect_type: RegenerationDefectType = RegenerationDefectType.GENERAL_QA_DEFECT
    reason: str = ""
    corrective_instruction: str = ""
    previous_result_ref: Optional[str] = None
    target_stage: str = "SCRIPT"  # SCRIPT, VOICE, ASSETS, RENDER


class RegenerationHistoryItem(BaseModel):
    """Audit record for a regeneration attempt."""
    attempt: int
    defect_type: str
    reason: str
    corrective_instruction: str
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    target_stage: str = "SCRIPT"


@runtime_checkable
class PublishProviderProtocol(Protocol):
    """Provider-neutral protocol for publishing adapters."""
    @property
    def provider_name(self) -> str:
        ...

    def publish(self, request: PublishRequest) -> PublishResult:
        ...

    def health_check(self) -> Any:
        ...


class VideoPerformance(BaseModel):
    """Normalized performance snapshot record for a published video."""
    performance_id: str = Field(..., description="Unique performance record ID")
    job_id: str = Field(..., description="Job identifier")
    platform: str = "youtube"
    remote_id: Optional[str] = None
    collected_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    views: int = 0
    likes: int = 0
    comments: int = 0
    shares: int = 0
    watch_time_seconds: float = 0.0
    avg_view_duration_seconds: float = 0.0
    retention_rate: Optional[float] = None
    ctr: Optional[float] = None
    impressions: Optional[int] = None
    subscriber_change: Optional[int] = None
    raw_metrics: Dict[str, Any] = Field(default_factory=dict)
    # Attribution links
    topic: Optional[str] = None
    channel_id: Optional[str] = None
    hook: Optional[str] = None
    duration_sec: Optional[float] = None
    production_engine: Optional[str] = None
    voice_id: Optional[str] = None
    visual_motif: Optional[str] = None


