"""MoneyPrinterTurboAdapter — Primary short-form video production adapter for MoneyPrinterTurbo."""
from __future__ import annotations

import json
import os
import shutil
import time
import subprocess
import urllib.request
import urllib.error
from pathlib import Path
from typing import Dict, Any, Optional

from autopilot.core.contracts import (
    ProductionRequest,
    ProductionResult,
    ScriptDocument,
)
from autopilot.core.asset_cache import compute_file_sha256
from autopilot.core.media_inspection import inspect_media
from autopilot.core.logging import StructuredLogger


class ProductionEngineUnavailableError(RuntimeError):
    """Raised when the requested production engine is not installed or unreachable."""
    pass


class MoneyPrinterTurboAdapter:
    """Production adapter integrating MoneyPrinterTurbo for automated short-form montage rendering."""

    def __init__(
        self,
        endpoint: Optional[str] = None,
        cli_path: Optional[str] = None,
        timeout_seconds: int = 300,
        poll_interval_seconds: float = 1.0,
    ):
        from autopilot.core.config import CONFIG

        self.endpoint = (
            endpoint
            or os.environ.get("MONEYPRINTER_ENDPOINT")
            or getattr(CONFIG, "moneyprinter_endpoint", None)
            or "http://127.0.0.1:8080"
        )
        self.cli_path = cli_path or os.environ.get("MONEYPRINTER_CLI_PATH", None)
        self.timeout_seconds = timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self.logger = StructuredLogger(stage="production_moneyprinter")

    @property
    def engine_name(self) -> str:
        return "moneyprinterturbo"

    @property
    def engine_version(self) -> str:
        return "v1.3.6"

    def health_check(self) -> Dict[str, Any]:
        """Check if MoneyPrinterTurbo endpoint is reachable or CLI binary exists."""
        # 1. Check HTTP API endpoint via GET /api/v1/tasks?page=1&page_size=1
        if self.endpoint:
            url = f"{self.endpoint.rstrip('/')}/api/v1/tasks?page=1&page_size=1"
            try:
                req = urllib.request.Request(
                    url,
                    headers={"User-Agent": "ProjectAutopilot/1.0"},
                )
                with urllib.request.urlopen(req, timeout=3) as resp:
                    status = getattr(resp, "status", None) or getattr(resp, "code", 200)
                    if status == 200:
                        raw_body = resp.read().decode("utf-8")
                        body = json.loads(raw_body)
                        if isinstance(body, dict) and (
                            body.get("status") == 200
                            or body.get("message") == "success"
                            or "data" in body
                        ):
                            return {
                                "healthy": True,
                                "mode": "http_api",
                                "endpoint": self.endpoint,
                                "health_url": url,
                                "version": self.engine_version,
                            }
            except Exception:
                pass

        # 2. Check local CLI path
        if self.cli_path and Path(self.cli_path).exists():
            return {
                "healthy": True,
                "mode": "cli",
                "cli_path": self.cli_path,
                "version": self.engine_version,
            }

        return {
            "healthy": False,
            "mode": "unreachable",
            "endpoint": self.endpoint,
            "error": f"MoneyPrinterTurbo HTTP API server is unreachable at {self.endpoint} and CLI path is not found.",
        }

    def _build_task_payload(self, request: ProductionRequest) -> Dict[str, Any]:
        """Translate Autopilot canonical ScriptDocument/Plan to MoneyPrinterTurbo VideoParams schema."""
        script_text = ""
        visual_terms = []

        if request.script:
            narration_parts = [s.narration for s in request.script.scenes if s.narration]
            script_text = " ".join(narration_parts)
            for s in request.script.scenes:
                if s.visual_intent:
                    visual_terms.append(s.visual_intent)
                elif s.asset_query:
                    visual_terms.append(s.asset_query)
        elif request.render_plan:
            for s in request.render_plan.scenes:
                if s.get("caption_text"):
                    script_text += f" {s.get('caption_text')}"
                if s.get("visual_intent"):
                    visual_terms.append(s.get("visual_intent"))

        return {
            "video_subject": request.topic,
            "video_script": script_text.strip() or request.topic,
            "video_terms": visual_terms if visual_terms else None,
            "video_aspect": request.video_ratio or "9:16",
            "video_concat_mode": "random",
            "video_clip_duration": int(request.options.get("clip_duration", 5)),
            "video_count": 1,
            "voice_name": request.options.get("voice_name", "en-US-JennyNeural"),
            "bgm_type": request.options.get("bgm_type", "random"),
            "bgm_volume": float(request.bgm_volume if request.bgm_volume is not None else 0.2),
            "subtitle_enabled": True,
        }

    def generate(self, request: ProductionRequest) -> ProductionResult:
        """Invoke MoneyPrinterTurbo to assemble and render the production video."""
        out_file = Path(request.output_path)
        out_file.parent.mkdir(parents=True, exist_ok=True)

        # Fail loudly if engine is unavailable — NO SILENT FALLBACK!
        health = self.health_check()
        if not health.get("healthy"):
            raise ProductionEngineUnavailableError(
                f"Production engine 'moneyprinterturbo' is unavailable. "
                f"Ensure MoneyPrinterTurbo API service is running at {self.endpoint} "
                f"or set MONEYPRINTER_ENDPOINT. (Legacy fallback available via --production-engine ffmpeg)"
            )

        payload = self._build_task_payload(request)
        self.logger.info("moneyprinter_task_dispatch", details={"job_id": request.job_id, "mode": health.get("mode")})

        try:
            if health.get("mode") == "http_api":
                # 1. Dispatch video generation task: POST /api/v1/videos
                req_data = json.dumps(payload).encode("utf-8")
                submit_url = f"{self.endpoint.rstrip('/')}/api/v1/videos"
                req = urllib.request.Request(
                    submit_url,
                    data=req_data,
                    headers={"Content-Type": "application/json", "User-Agent": "ProjectAutopilot/1.0"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=30) as resp:
                    resp_data = json.loads(resp.read().decode("utf-8"))

                # Extract task ID
                data_obj = resp_data.get("data") or {}
                task_id = (
                    data_obj.get("task_id")
                    or resp_data.get("task_id")
                    or data_obj.get("id")
                    or resp_data.get("id")
                )

                # If task_id returned, poll for completion: GET /api/v1/tasks/{task_id}
                if task_id:
                    task_url = f"{self.endpoint.rstrip('/')}/api/v1/tasks/{task_id}"
                    start_time = time.time()
                    video_found = False
                    timeout_limit = request.timeout_seconds or self.timeout_seconds

                    while time.time() - start_time < timeout_limit:
                        poll_req = urllib.request.Request(
                            task_url,
                            headers={"User-Agent": "ProjectAutopilot/1.0"},
                            method="GET",
                        )
                        with urllib.request.urlopen(poll_req, timeout=10) as poll_resp:
                            poll_data = json.loads(poll_resp.read().decode("utf-8"))

                        task_status = poll_data.get("data") or poll_data
                        state = task_status.get("state")

                        # TASK_STATE_COMPLETE = 1
                        if state == 1:
                            videos = task_status.get("videos") or []
                            vid_target = None
                            if videos and isinstance(videos, list):
                                vid_target = videos[0]
                            elif task_status.get("video_file"):
                                vid_target = task_status.get("video_file")
                            elif task_status.get("video_path"):
                                vid_target = task_status.get("video_path")

                            if vid_target:
                                # Fetch or copy video to output path
                                if isinstance(vid_target, str) and vid_target.startswith(("http://", "https://")):
                                    dl_req = urllib.request.Request(vid_target, headers={"User-Agent": "ProjectAutopilot/1.0"})
                                    with urllib.request.urlopen(dl_req, timeout=60) as dl_resp:
                                        out_file.write_bytes(dl_resp.read())
                                elif isinstance(vid_target, str) and Path(vid_target).exists():
                                    shutil.copyfile(vid_target, str(out_file))
                                else:
                                    # Try resolving relative tasks/ URI
                                    rel_uri = f"{self.endpoint.rstrip('/')}/{vid_target.lstrip('/')}"
                                    dl_req = urllib.request.Request(rel_uri, headers={"User-Agent": "ProjectAutopilot/1.0"})
                                    with urllib.request.urlopen(dl_req, timeout=60) as dl_resp:
                                        out_file.write_bytes(dl_resp.read())
                                video_found = True
                                break
                            else:
                                raise RuntimeError(f"MoneyPrinterTurbo task '{task_id}' completed but returned no video path.")

                        # TASK_STATE_FAILED = -1
                        elif state == -1:
                            err_msg = task_status.get("error") or task_status.get("message") or "Task reported failure"
                            raise RuntimeError(f"MoneyPrinterTurbo task '{task_id}' failed: {err_msg}")

                        # Still processing
                        time.sleep(self.poll_interval_seconds)

                    if not video_found:
                        raise TimeoutError(f"MoneyPrinterTurbo task '{task_id}' timed out after {timeout_limit}s")
                else:
                    # Direct response fallback if server synchronous
                    generated_path = data_obj.get("video_path") or resp_data.get("video_path")
                    if generated_path and Path(generated_path).exists():
                        shutil.copyfile(generated_path, str(out_file))

            elif health.get("mode") == "cli":
                cmd = [
                    self.cli_path,
                    "--subject", payload["video_subject"],
                    "--script", payload["video_script"],
                    "--aspect", payload["video_aspect"],
                    "--output", str(out_file),
                ]
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=request.timeout_seconds)
                if proc.returncode != 0:
                    raise RuntimeError(f"MoneyPrinterTurbo CLI error ({proc.returncode}): {proc.stderr}")

            if not out_file.exists() or out_file.stat().st_size == 0:
                raise RuntimeError(f"MoneyPrinterTurbo completed but output file is missing or empty: {out_file}")

            # Inspect resulting video properties
            probe = inspect_media(str(out_file))
            if not probe.get("valid"):
                raise RuntimeError(f"Rendered video failed container probe: {probe.get('error')}")

            v_stream = probe.get("video", {})
            a_stream = probe.get("audio", {})
            format_info = probe.get("format", {})
            duration = float(format_info.get("duration", 0) or 0)
            checksum = compute_file_sha256(str(out_file))

            return ProductionResult(
                job_id=request.job_id,
                video_path=str(out_file),
                duration_sec=duration,
                width=int(v_stream.get("width", 1080) or 1080),
                height=int(v_stream.get("height", 1920) or 1920),
                audio_present=bool(a_stream),
                captions_path=payload.get("captions_path"),
                engine_name=self.engine_name,
                engine_version=self.engine_version,
                provenance={
                    "engine": self.engine_name,
                    "version": self.engine_version,
                    "mode": health.get("mode"),
                    "endpoint": self.endpoint,
                },
                metadata={
                    "format": format_info.get("format_name"),
                    "file_size": out_file.stat().st_size,
                    "video_codec": v_stream.get("codec_name"),
                    "audio_codec": a_stream.get("codec_name") if a_stream else None,
                },
                checksum_sha256=checksum,
                success=True,
            )

        except Exception as exc:
            self.logger.error("moneyprinter_render_failed", error=str(exc), details={"job_id": request.job_id})
            raise RuntimeError(f"MoneyPrinterTurbo production failed: {exc}") from exc
