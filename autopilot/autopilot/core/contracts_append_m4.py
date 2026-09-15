
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
    version: str = CONTRACT_SCHEMA_VERSION

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
