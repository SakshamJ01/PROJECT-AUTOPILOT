"""FFmpegProductionAdapter — Legacy/dev production adapter wrapping native FFmpegRenderer."""
from __future__ import annotations

from pathlib import Path
from typing import Dict, Any

from autopilot.core.contracts import (
    ProductionRequest,
    ProductionResult,
    RenderPlan,
)
from autopilot.core.renderer import FFmpegRenderer
from autopilot.core.ffmpeg_runner import FFmpegRunner


class FFmpegProductionAdapter:
    """Production adapter wrapping native FFmpegRenderer for local/development workflows."""

    def __init__(self, profile: str = "vertical_short"):
        self.profile = profile
        self.renderer = FFmpegRenderer(profile=profile)
        self.runner = FFmpegRunner()

    @property
    def engine_name(self) -> str:
        return "ffmpeg"

    @property
    def engine_version(self) -> str:
        return "v1.0.0"

    def health_check(self) -> Dict[str, Any]:
        """Check if ffmpeg executable is available on PATH."""
        version = self.runner.version()
        if version and version != "unknown":
            return {
                "healthy": True,
                "mode": "native_subprocess",
                "ffmpeg_version": version,
                "version": self.engine_version,
            }
        return {
            "healthy": False,
            "mode": "missing_binary",
            "error": "ffmpeg executable is not installed or not found on PATH.",
        }

    def generate(self, request: ProductionRequest) -> ProductionResult:
        """Render using native FFmpegRenderer."""
        health = self.health_check()
        if not health.get("healthy"):
            raise RuntimeError(f"FFmpeg engine unavailable: {health.get('error')}")

        plan = request.render_plan
        if not plan:
            scenes = []
            if request.script:
                for idx, sc in enumerate(request.script.scenes):
                    scenes.append({
                        "scene_id": sc.scene_id,
                        "duration_sec": sc.estimated_duration_seconds or 5.0,
                        "caption_text": sc.narration,
                        "visual_intent": sc.visual_intent,
                    })
            plan = RenderPlan(
                plan_id=f"plan-{request.job_id}",
                content_id=request.content_id,
                job_id=request.job_id,
                profile=request.profile or self.profile,
                scenes=scenes,
            )

        render_out = self.renderer.render(plan, request.output_path)

        return ProductionResult(
            job_id=request.job_id,
            video_path=render_out.output_path,
            duration_sec=render_out.duration_sec,
            width=render_out.width,
            height=render_out.height,
            audio_present=True,
            captions_path=None,
            engine_name=self.engine_name,
            engine_version=self.engine_version,
            provenance={
                "engine": self.engine_name,
                "version": self.engine_version,
                "mode": "native_ffmpeg",
            },
            metadata={
                "codec_video": render_out.codec_video,
                "codec_audio": render_out.codec_audio,
                "container": render_out.container,
                "file_size": render_out.file_size_bytes,
            },
            checksum_sha256=render_out.checksum_sha256,
            success=True,
        )
