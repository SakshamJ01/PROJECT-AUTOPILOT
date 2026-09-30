"""EdgeTTS Provider — Neural voice synthesis via Microsoft Edge TTS service.

PRIMARY production voice provider (P0).

  - Real neural voices (e.g. en-US-ChristopherNeural, en-US-AvaNeural).
  - Actually applies rate / pitch / volume prosody.
  - Retries with classified failure on transient errors.
  - On exhaustion, uses an EXPLICITLY CONFIGURED fallback and RECORDS the
    actual fallback provider. Never claims provider=X when provider=Y.
  - NEVER silently falls through to Mock TTS in production (Mock is TEST ONLY).
  - Emits a truthful TTSProvenance sidecar (actual_provider, actual_voice_id,
    requested provider/voice, prosody, latency, audio checksum).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Optional, Dict, Any

from autopilot.providers.contracts import (
    TTSProvider,
    ProviderHealth,
    CapabilityMetadata,
    CostUsageMetadata,
    ProviderErrorType,
    TTSProvenance,
    write_tts_provenance,
)


class EdgeTTSError(RuntimeError):
    """Classified Edge TTS synthesis failure."""

    def __init__(self, message: str, error_type: str = "synthesis_failed"):
        super().__init__(message)
        self.error_type = error_type


class EdgeTTSProvider(TTSProvider):
    provider_name = "edge_tts"
    capability = CapabilityMetadata(
        max_resolution="1080p",
        supports_9_16=True,
        local_only=False,
        license_note="EdgeTTS cloud speech synthesis; requires network access",
    )
    error_type = ProviderErrorType.UNCONFIGURED
    cost_meta = CostUsageMetadata(estimated_usd=0.0, provider_type="cloud_free")

    DEFAULT_VOICE = "en-US-ChristopherNeural"
    # Explicit, recorded fallback chain (never Mock in production).
    FALLBACK_VOICES = ["en-US-AvaNeural", "en-US-AndrewNeural", "en-US-GuyNeural"]

    def __init__(self, default_voice: str = DEFAULT_VOICE):
        self.default_voice = default_voice

    def health_check(self) -> ProviderHealth:
        """Check if the edge-tts python package is installed and a voice resolves."""
        import importlib.util
        has_module = importlib.util.find_spec("edge_tts") is not None
        if not has_module:
            return ProviderHealth(
                healthy=False,
                provider_name=self.provider_name,
                error="edge-tts python package is NOT installed in the production environment.",
                details={"has_module": False},
            )
        # Confirm the configured default voice actually exists.
        try:
            import edge_tts

            async def _voices():
                return {v["ShortName"] for v in await edge_tts.list_voices()}

            voices = asyncio.run(_voices())
            voice_ok = self.default_voice in voices
            return ProviderHealth(
                healthy=voice_ok,
                provider_name=self.provider_name,
                error="" if voice_ok else f"Configured voice {self.default_voice} not found",
                details={
                    "has_module": True,
                    "default_voice": self.default_voice,
                    "voice_exists": voice_ok,
                    "en_voices_available": sorted(v for v in voices if v.startswith("en-"))[:5],
                },
            )
        except Exception as exc:
            return ProviderHealth(
                healthy=False,
                provider_name=self.provider_name,
                error=f"Edge TTS health check failed: {exc}",
                details={"exception": str(exc)},
            )

    def _classify(self, exc: Exception) -> str:
        msg = str(exc).lower()
        if "timeout" in msg or "timed out" in msg:
            return "timeout"
        if "connection" in msg or "network" in msg or "unreachable" in msg or "reset" in msg:
            return "network"
        if "voice" in msg and ("not" in msg or "invalid" in msg or "notfound" in msg):
            return "invalid_voice"
        return "synthesis_failed"

    def _run_async_synthesis(
        self,
        text: str,
        voice: str,
        out_file: Path,
        rate: Optional[str],
        pitch: Optional[str],
        volume: Optional[str],
    ) -> None:
        """Run the async edge_tts synthesis to disk."""
        import edge_tts

        async def _go():
            communicate = edge_tts.Communicate(
                text=text,
                voice=voice,
                rate=rate or "+0%",
                pitch=pitch or "+0Hz",
                volume=volume or "+0%",
            )
            await communicate.save(str(out_file))

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor() as pool:
                    pool.submit(lambda: asyncio.run(_go())).result()
            else:
                loop.run_until_complete(_go())
        except RuntimeError:
            asyncio.run(_go())

    def synthesize(
        self,
        text: str,
        out_path: str,
        voice: Optional[str] = None,
        rate: Optional[str] = None,
        pitch: Optional[str] = None,
        volume: Optional[str] = None,
        requested_provider: Optional[str] = None,
        **kwargs: Any,
    ) -> str:
        """Synthesize text to speech audio file using real EdgeTTS.

        Raises EdgeTTSError if synthesis cannot be performed — it never
        silently degrades to Mock TTS.
        """
        if not text or not text.strip():
            raise ValueError("EdgeTTS synthesis received empty text")

        requested_voice = voice or self.default_voice
        out_file = Path(out_path)
        out_file.parent.mkdir(parents=True, exist_ok=True)

        prosody = {"rate": rate, "pitch": pitch, "volume": volume}
        t_start = time.time()

        # Provider unavailable is a hard, explicit failure (no Mock fallthrough).
        try:
            import edge_tts  # noqa: F401
        except ImportError as exc:
            raise EdgeTTSError(
                "edge-tts package is not installed; cannot synthesize with EdgeTTS. "
                "Mock TTS is TEST ONLY and is never used as a production fallback.",
                error_type="package_missing",
            ) from exc

        # Try the requested voice, then explicit recorded fallback voices.
        attempt_voices = [requested_voice] + [
            v for v in self.FALLBACK_VOICES if v != requested_voice
        ]
        last_error: Optional[EdgeTTSError] = None
        used_voice: Optional[str] = None
        fallback_reason: Optional[str] = None

        for vidx, v in enumerate(attempt_voices):
            # Retry transient failures with backoff.
            for attempt in range(2):
                try:
                    self._run_async_synthesis(text, v, out_file, rate, pitch, volume)
                    if out_file.exists() and out_file.stat().st_size > 0:
                        used_voice = v
                        if vidx > 0:
                            fallback_reason = (
                                f"requested voice '{requested_voice}' failed ({last_error.error_type}: {last_error}); "
                                f"used configured fallback voice '{v}'"
                            )
                        break
                    raise EdgeTTSError(f"EdgeTTS produced empty output for voice {v}")
                except EdgeTTSError:
                    raise
                except Exception as exc:
                    etype = self._classify(exc)
                    if attempt == 0 and etype in ("timeout", "network"):
                        time.sleep(1.0)
                        continue
                    last_error = EdgeTTSError(
                        f"EdgeTTS synthesis failed for voice {v}: {exc}", error_type=etype
                    )
                    break
            if used_voice:
                break

        if not used_voice or not out_file.exists() or out_file.stat().st_size == 0:
            raise EdgeTTSError(
                f"All EdgeTTS synthesis attempts failed for voices {attempt_voices}: "
                f"{last_error}",
                error_type=last_error.error_type if last_error else "synthesis_failed",
            )

        latency = round(time.time() - t_start, 3)
        checksum = hashlib.sha256(out_file.read_bytes()).hexdigest()

        provenance = TTSProvenance(
            actual_provider=self.provider_name,
            actual_voice_id=used_voice,
            requested_provider=requested_provider or self.provider_name,
            requested_voice_id=requested_voice,
            prosody=prosody,
            latency_sec=latency,
            audio_checksum_sha256=checksum,
            audio_path=str(out_file.resolve()),
            fallback_used=(used_voice != requested_voice),
            fallback_reason=fallback_reason,
        )
        try:
            write_tts_provenance(provenance)
        except Exception:
            pass

        # Attach provenance to the returned path for upstream capture.
        out_file.with_suffix(out_file.suffix + ".provenance_meta").write_text(
            provenance.model_dump_json(), encoding="utf-8"
        )

        return str(out_file)
