"""Windows SAPI TTS Provider — Real local speech synthesis for Windows.
Uses built-in System.Speech.Synthesis (.NET / SAPI) to generate genuine spoken WAV files.
$0 cost, 100% offline, zero mandatory external dependencies, and fully compatible with FFmpeg.
"""
from __future__ import annotations
import hashlib
import json
import os
import platform
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional

from autopilot.providers.contracts import TTSProvider, ProviderHealth, CapabilityMetadata, CostUsageMetadata, ProviderErrorType


class WindowsSAPITTSProvider(TTSProvider):
    provider_name = "windows_sapi"
    capability = CapabilityMetadata(
        max_resolution="1080p",
        supports_9_16=True,
        local_only=True,
        license_note="Built-in Windows SAPI speech synthesis via System.Speech; $0 local inference",
    )
    error_type = ProviderErrorType.UNCONFIGURED
    cost_meta = CostUsageMetadata(estimated_usd=0.0, provider_type="local")

    def health_check(self) -> ProviderHealth:
        if platform.system() != "Windows":
            return ProviderHealth(
                healthy=False,
                provider_name=self.provider_name,
                error="Windows SAPI is only available on Windows operating systems",
                details={"platform": platform.system()},
            )
        try:
            # Check PowerShell and System.Speech availability
            ps_test = (
                "Add-Type -AssemblyName System.Speech; "
                "$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                "$voices = $synth.GetInstalledVoices() | ForEach-Object { $_.VoiceInfo.Name }; "
                "$synth.Dispose(); "
                "Write-Output ($voices -join ',')"
            )
            res = subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_test],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if res.returncode == 0:
                installed_voices = [v.strip() for v in res.stdout.strip().split(",") if v.strip()]
                return ProviderHealth(
                    healthy=True,
                    provider_name=self.provider_name,
                    details={
                        "platform": "Windows",
                        "installed_voices": installed_voices,
                        "voice_count": len(installed_voices),
                    },
                )
            else:
                return ProviderHealth(
                    healthy=False,
                    provider_name=self.provider_name,
                    error=f"PowerShell SAPI check failed: {res.stderr.strip()}",
                )
        except Exception as exc:
            return ProviderHealth(
                healthy=False,
                provider_name=self.provider_name,
                error=f"Windows SAPI health check exception: {exc}",
            )

    def synthesize(self, text: str, out_path: str, voice_id: str | None = None, **kwargs) -> str:
        if not text or not text.strip():
            raise ValueError("TTS synthesis received empty text")

        out = Path(out_path).resolve()
        out.parent.mkdir(parents=True, exist_ok=True)

        start_time = time.time()
        # Create a temporary PowerShell script to synthesize speech cleanly
        with tempfile.NamedTemporaryFile(suffix=".ps1", delete=False, mode="w", encoding="utf-8") as ps_file:
            ps_script_path = ps_file.name
            escaped_text = text.replace("'", "''").replace("`", "``")
            select_voice_code = f"$synth.SelectVoice('{voice_id}');" if voice_id else ""
            ps_script_content = f"""$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
{select_voice_code}
$synth.SetOutputToWaveFile('{str(out).replace(chr(92), "/")}')
$synth.Speak('{escaped_text}')
$synth.Dispose()
"""
            ps_file.write(ps_script_content)

        try:
            res = subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps_script_path],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if res.returncode != 0 or not out.exists():
                raise RuntimeError(f"Windows SAPI TTS synthesis failed: {res.stderr.strip()}")

            latency_sec = time.time() - start_time

            # Probe generated audio with FFprobe to extract exact duration and format
            duration_sec = self._probe_audio_duration(str(out))

            # Write machine-readable provenance record
            prov_path = out.with_suffix(out.suffix + ".provenance.json")
            prov_path.write_text(
                json.dumps({
                    "provider": self.provider_name,
                    "input_text_hash_prefix": hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
                    "voice_id": voice_id or "default_windows_sapi",
                    "duration_sec": round(duration_sec, 3),
                    "file_size_bytes": out.stat().st_size,
                    "synthesis_latency_sec": round(latency_sec, 3),
                    "mode": "real_local_speech",
                    "license": "Public / System Local (No External Rights Required)",
                }, indent=2),
                encoding="utf-8",
            )
            return str(out)
        finally:
            if os.path.exists(ps_script_path):
                try:
                    os.unlink(ps_script_path)
                except OSError:
                    pass

    def _probe_audio_duration(self, audio_path: str) -> float:
        try:
            cmd = [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                audio_path,
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if res.returncode == 0 and res.stdout.strip():
                return float(res.stdout.strip())
        except Exception:
            pass
        return 0.0
