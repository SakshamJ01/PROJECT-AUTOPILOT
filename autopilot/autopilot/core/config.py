"""Configuration loader — Phase 0.
Environment variables + sensible local defaults.
No secrets in source.
"""
from __future__ import annotations
import os
from pathlib import Path
from typing import Optional
from pydantic import BaseModel, Field


class Config(BaseModel):
    project_root: Path = Field(default_factory=lambda: Path(__file__).resolve().parent.parent.parent)
    artifacts_dir: Path = Field(default_factory=lambda: Path(__file__).resolve().parent.parent.parent / "artifacts")
    db_path: Path = Field(default_factory=lambda: Path(__file__).resolve().parent.parent.parent / "artifacts" / "autopilot.db")
    log_level: str = "INFO"
    provider_default_llm: str = "local_stub"
    provider_default_tts: str = "local_stub"
    provider_default_asr: str = "local_stub"
    provider_default_asset: str = "local"
    provider_default_publisher: str = "local_stub"
    max_render_resolution: str = "1080p"
    target_aspect_ratio: str = "9:16"
    synthetic_smoke_enabled: bool = True
    default_production_engine: str = "moneyprinterturbo"
    moneyprinter_endpoint: str = "http://127.0.0.1:8080"
    moneyprinter_cli_path: Optional[str] = None
    whisper_model_size: str = "base"

    # LLM & Local Ollama configurations
    ollama_endpoint: str = "http://localhost:11434"
    ollama_model: Optional[str] = None
    ollama_think: bool = False
    ollama_timeout: float = 180.0
    ollama_idle_timeout: float = 60.0
    ollama_num_predict: int = 1024
    openai_endpoint: str = "http://localhost:11434/v1"
    openai_model: Optional[str] = None

    # Cloud LLM: Gemini & OpenRouter configurations
    gemini_api_key: Optional[str] = None
    gemini_model: Optional[str] = "gemini-2.0-flash"
    gemini_endpoint: str = "https://generativelanguage.googleapis.com/v1beta/openai"
    gemini_thinking_budget: Optional[int] = 0

    openrouter_api_key: Optional[str] = None
    openrouter_model: Optional[str] = None
    openrouter_endpoint: str = "https://openrouter.ai/api/v1"

    # Asset Engine & Openverse configurations
    openverse_enabled: bool = True
    openverse_base_url: str = "https://api.openverse.org/v1/"
    openverse_timeout: float = 10.0
    openverse_max_results: int = 5
    asset_cache_dir: Path = Field(default_factory=lambda: Path(__file__).resolve().parent.parent.parent / "artifacts" / "asset_cache")
    asset_target_width: int = 1080
    asset_target_height: int = 1920
    asset_max_download_bytes: int = 50 * 1024 * 1024  # 50 MB
    asset_max_redirects: int = 5
    rights_policy_allow_partial: bool = False

    # QA Engine & Quality Gates configurations
    qa_loudness_target_lufs: float = -14.0
    qa_loudness_min_lufs: float = -24.0
    qa_loudness_max_lufs: float = -10.0
    qa_max_silence_duration_sec: float = 2.0
    qa_silence_threshold_db: float = -50.0
    qa_max_black_duration_sec: float = 1.0
    qa_max_freeze_duration_sec: float = 3.0
    qa_duration_tolerance_sec: float = 1.5
    qa_duration_tolerance_pct: float = 0.20
    qa_strict_mode: bool = False
    qa_fps_target: float = 25.0
    qa_caption_max_line_length: int = 40
    qa_caption_max_lines: int = 2

    # Publishing & YouTube configurations
    publish_default_visibility: str = "private"
    publish_default_platform: str = "youtube"
    publish_dry_run_default: bool = False
    publish_max_retries: int = 3
    publish_timeout_seconds: float = 60.0
    publish_chunk_size_bytes: int = 1024 * 1024 * 5  # 5 MB chunks for resumable upload
    youtube_client_secrets_path: Optional[Path] = None
    youtube_token_path: Optional[Path] = None
    youtube_access_token: Optional[str] = None
    postiz_endpoint: str = "http://127.0.0.1:3000"
    postiz_api_key: Optional[str] = None

    # Milestone 7 / M7 Queue, Worker & Batch configurations
    queue_default_priority: int = 2
    queue_max_attempts: int = 3
    queue_lease_duration_seconds: float = 300.0
    queue_poll_interval_seconds: float = 2.0
    queue_retry_backoff_base_seconds: float = 2.0
    queue_max_concurrency: int = 1
    queue_max_queued_jobs: int = 1000

    # Milestone 8 / M8 Analytics & Performance Intelligence configurations
    analytics_default_provider: str = "mock"
    analytics_sync_interval_hours: int = 24
    analytics_cache_ttl_seconds: int = 3600
    analytics_batch_size: int = 25

    # Milestone 9 / M9 Autonomous Ideation & Feedback Loop configurations
    autonomy_level: int = 0  # 0=manual, 1=discovery, 2=proposal, 3=auto_queue, 4=auto_produce
    autonomy_max_ideas_per_cycle: int = 10
    autonomy_max_auto_queue_per_cycle: int = 3
    autonomy_max_daily_jobs: int = 10
    autonomy_topic_cooldown_days: int = 14
    autonomy_similarity_threshold: float = 0.70
    autonomy_min_score_threshold: float = 0.60
    autonomy_trend_provider: str = "mock"
    autonomy_auto_publish: bool = False

    def __init__(self, **data):
        # Apply env overrides before validation
        env_map = {
            "AUTOPILOT_ARTIFACTS_DIR": "artifacts_dir",
            "AUTOPILOT_DB_PATH": "db_path",
            "AUTOPILOT_LOG_LEVEL": "log_level",
            "AUTOPILOT_PROVIDER_DEFAULT_LLM": "provider_default_llm",
            "AUTOPILOT_PROVIDER_DEFAULT_ASSET": "provider_default_asset",
            "OPENVERSE_ENABLED": "openverse_enabled",
            "OPENVERSE_BASE_URL": "openverse_base_url",
            "OPENVERSE_TIMEOUT": "openverse_timeout",
            "OPENVERSE_MAX_RESULTS": "openverse_max_results",
            "ASSET_CACHE_DIR": "asset_cache_dir",
            "ASSET_TARGET_WIDTH": "asset_target_width",
            "ASSET_TARGET_HEIGHT": "asset_target_height",
            "ASSET_MAX_DOWNLOAD_BYTES": "asset_max_download_bytes",
            "ASSET_MAX_REDIRECTS": "asset_max_redirects",
            "RIGHTS_POLICY_ALLOW_PARTIAL": "rights_policy_allow_partial",
            "AUTOPILOT_QA_STRICT_MODE": "qa_strict_mode",
            "AUTOPILOT_QA_LOUDNESS_TARGET": "qa_loudness_target_lufs",
            "AUTOPILOT_QA_MAX_SILENCE": "qa_max_silence_duration_sec",
            "AUTOPILOT_PUBLISH_DEFAULT_VISIBILITY": "publish_default_visibility",
            "AUTOPILOT_PUBLISH_DEFAULT_PLATFORM": "publish_default_platform",
            "AUTOPILOT_PUBLISH_DRY_RUN": "publish_dry_run_default",
            "AUTOPILOT_PUBLISH_MAX_RETRIES": "publish_max_retries",
            "AUTOPILOT_PUBLISH_TIMEOUT_SECONDS": "publish_timeout_seconds",
            "AUTOPILOT_PUBLISH_CHUNK_SIZE_BYTES": "publish_chunk_size_bytes",
            "YOUTUBE_CLIENT_SECRETS_PATH": "youtube_client_secrets_path",
            "YOUTUBE_TOKEN_PATH": "youtube_token_path",
            "YOUTUBE_ACCESS_TOKEN": "youtube_access_token",
            "AUTOPILOT_QUEUE_DEFAULT_PRIORITY": "queue_default_priority",
            "AUTOPILOT_QUEUE_MAX_ATTEMPTS": "queue_max_attempts",
            "AUTOPILOT_QUEUE_LEASE_DURATION": "queue_lease_duration_seconds",
            "AUTOPILOT_QUEUE_POLL_INTERVAL": "queue_poll_interval_seconds",
            "AUTOPILOT_QUEUE_RETRY_BACKOFF_BASE": "queue_retry_backoff_base_seconds",
            "AUTOPILOT_QUEUE_MAX_CONCURRENCY": "queue_max_concurrency",
            "AUTOPILOT_QUEUE_MAX_QUEUED_JOBS": "queue_max_queued_jobs",
            "AUTOPILOT_ANALYTICS_DEFAULT_PROVIDER": "analytics_default_provider",
            "AUTOPILOT_ANALYTICS_SYNC_INTERVAL_HOURS": "analytics_sync_interval_hours",
            "AUTOPILOT_ANALYTICS_CACHE_TTL_SECONDS": "analytics_cache_ttl_seconds",
            "AUTOPILOT_ANALYTICS_BATCH_SIZE": "analytics_batch_size",
            "AUTOPILOT_AUTONOMY_LEVEL": "autonomy_level",
            "AUTOPILOT_AUTONOMY_MAX_IDEAS": "autonomy_max_ideas_per_cycle",
            "AUTOPILOT_AUTONOMY_MAX_AUTO_QUEUE": "autonomy_max_auto_queue_per_cycle",
            "AUTOPILOT_AUTONOMY_MAX_DAILY_JOBS": "autonomy_max_daily_jobs",
            "AUTOPILOT_AUTONOMY_TOPIC_COOLDOWN_DAYS": "autonomy_topic_cooldown_days",
            "AUTOPILOT_AUTONOMY_SIMILARITY_THRESHOLD": "autonomy_similarity_threshold",
            "AUTOPILOT_AUTONOMY_MIN_SCORE_THRESHOLD": "autonomy_min_score_threshold",
            "AUTOPILOT_AUTONOMY_TREND_PROVIDER": "autonomy_trend_provider",
            "AUTOPILOT_AUTONOMY_AUTO_PUBLISH": "autonomy_auto_publish",
            "AUTOPILOT_PRODUCTION_ENGINE": "default_production_engine",
            "MONEYPRINTER_ENDPOINT": "moneyprinter_endpoint",
            "MONEYPRINTER_CLI_PATH": "moneyprinter_cli_path",
            "AUTOPILOT_WHISPER_MODEL_SIZE": "whisper_model_size",
            "OLLAMA_ENDPOINT": "ollama_endpoint",
            "OLLAMA_BASE_URL": "ollama_endpoint",
            "AUTOPILOT_OLLAMA_ENDPOINT": "ollama_endpoint",
            "OLLAMA_MODEL": "ollama_model",
            "OLLAMA_THINK": "ollama_think",
            "AUTOPILOT_OLLAMA_THINK": "ollama_think",
            "OLLAMA_TIMEOUT": "ollama_timeout",
            "AUTOPILOT_OLLAMA_TIMEOUT": "ollama_timeout",
            "OLLAMA_IDLE_TIMEOUT": "ollama_idle_timeout",
            "OLLAMA_NUM_PREDICT": "ollama_num_predict",
            "AUTOPILOT_OLLAMA_NUM_PREDICT": "ollama_num_predict",
            "OPENAI_BASE_URL": "openai_endpoint",
            "AUTOPILOT_LLM_ENDPOINT": "openai_endpoint",
            "OPENAI_MODEL": "openai_model",
            "GEMINI_API_KEY": "gemini_api_key",
            "AUTOPILOT_GEMINI_API_KEY": "gemini_api_key",
            "GEMINI_MODEL": "gemini_model",
            "AUTOPILOT_GEMINI_MODEL": "gemini_model",
            "GEMINI_ENDPOINT": "gemini_endpoint",
            "GEMINI_BASE_URL": "gemini_endpoint",
            "GEMINI_THINKING_BUDGET": "gemini_thinking_budget",
            "OPENROUTER_API_KEY": "openrouter_api_key",
            "AUTOPILOT_OPENROUTER_API_KEY": "openrouter_api_key",
            "OPENROUTER_KEY": "openrouter_api_key",
            "OPENROUTER_MODEL": "openrouter_model",
            "AUTOPILOT_OPENROUTER_MODEL": "openrouter_model",
            "OPENROUTER_BASE_URL": "openrouter_endpoint",
            "OPENROUTER_ENDPOINT": "openrouter_endpoint",
        }
        for env_key, field_name in env_map.items():
            val = os.environ.get(env_key)
            if val is not None:
                if field_name in ("openverse_enabled", "rights_policy_allow_partial", "synthetic_smoke_enabled", "qa_strict_mode", "publish_dry_run_default", "autonomy_auto_publish", "ollama_think"):
                    data[field_name] = val.lower() in ("1", "true", "yes")
                elif field_name in ("openverse_max_results", "asset_target_width", "asset_target_height", "asset_max_download_bytes", "asset_max_redirects", "qa_caption_max_line_length", "qa_caption_max_lines", "publish_max_retries", "publish_chunk_size_bytes", "queue_default_priority", "queue_max_attempts", "queue_max_concurrency", "queue_max_queued_jobs", "analytics_sync_interval_hours", "analytics_cache_ttl_seconds", "analytics_batch_size", "autonomy_level", "autonomy_max_ideas_per_cycle", "autonomy_max_auto_queue_per_cycle", "autonomy_max_daily_jobs", "autonomy_topic_cooldown_days", "ollama_num_predict"):
                    data[field_name] = int(val)
                elif field_name in ("openverse_timeout", "qa_loudness_target_lufs", "qa_max_silence_duration_sec", "qa_silence_threshold_db", "qa_max_black_duration_sec", "qa_max_freeze_duration_sec", "qa_duration_tolerance_sec", "qa_duration_tolerance_pct", "qa_fps_target", "publish_timeout_seconds", "queue_lease_duration_seconds", "queue_poll_interval_seconds", "queue_retry_backoff_base_seconds", "autonomy_similarity_threshold", "autonomy_min_score_threshold", "ollama_timeout", "ollama_idle_timeout"):
                    data[field_name] = float(val)
                else:
                    data[field_name] = val
        # Convert Path strings if needed
        for k in ("artifacts_dir", "db_path", "project_root", "asset_cache_dir", "youtube_client_secrets_path", "youtube_token_path"):
            if isinstance(data.get(k), str):
                data[k] = Path(data[k])
        super().__init__(**data)

    def get_artifacts_dir(self) -> Path:
        d = self.artifacts_dir
        d.mkdir(parents=True, exist_ok=True)
        return d

    def get_asset_cache_dir(self) -> Path:
        d = self.asset_cache_dir
        d.mkdir(parents=True, exist_ok=True)
        return d


# Default instance (re-read from env at import time; safe because env not hardcoded)
CONFIG = Config()
