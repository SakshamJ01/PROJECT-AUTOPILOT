"""Kokoro TTS Provider — Offline speech synthesis provider.
Uses Kokoro TTS model inference when available, with genuine local voice synthesis.
"""
from __future__ import annotations
import hashlib
import json
import os
import wave
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from autopilot.core.config import CONFIG
from autopilot.providers.contracts import TTSProvider, ProviderHealth, CapabilityMetadata, CostUsageMetadata, ProviderErrorType


def resolve_kokoro_model_paths() -> Tuple[Path, Path]:
    """Resolve explicit paths for Kokoro ONNX model file and voices file.
    
    Checks:
    1. KOKORO_MODEL_PATH and KOKORO_VOICES_PATH environment variables
    2. Config / model directory: CONFIG.get_artifacts_dir() / "models" / "kokoro"
    3. User home directory: ~/.kokoro/ or ~/.cache/kokoro/
    """
    model_env = os.getenv("KOKORO_MODEL_PATH")
    voices_env = os.getenv("KOKORO_VOICES_PATH")

    if model_env and voices_env:
        return Path(model_env), Path(voices_env)

    search_dirs = [
        CONFIG.get_artifacts_dir() / "models" / "kokoro",
        Path.home() / ".kokoro",
        Path.home() / ".cache" / "kokoro",
        Path("models/kokoro"),
    ]

    model_candidates = ["kokoro-v1.0.onnx", "kokoro-v0_19.onnx", "kokoro.onnx", "model.onnx"]
    voices_candidates = ["voices.bin", "voices.json", "voices-v1.0.bin"]

    found_model: Optional[Path] = Path(model_env) if model_env and Path(model_env).exists() else None
    found_voices: Optional[Path] = Path(voices_env) if voices_env and Path(voices_env).exists() else None

    if not found_model or not found_voices:
        for s_dir in search_dirs:
            if not s_dir.exists():
                continue
            if not found_model:
                for mc in model_candidates:
                    p = s_dir / mc
                    if p.exists():
                        found_model = p
                        break
            if not found_voices:
                for vc in voices_candidates:
                    p = s_dir / vc
                    if p.exists():
                        found_voices = p
                        break
            if found_model and found_voices:
                break

    if not found_model or not found_model.exists():
        missing_m = str(found_model) if found_model else "kokoro-v1.0.onnx"
        search_paths_str = ", ".join(str(d) for d in search_dirs)
        raise RuntimeError(
            f"Kokoro ONNX model file missing: '{missing_m}'. "
            f"Please download 'kokoro-v1.0.onnx' and place it in '{search_dirs[0]}' or set KOKORO_MODEL_PATH. "
            f"Searched directories: [{search_paths_str}]"
        )

    if not found_voices or not found_voices.exists():
        missing_v = str(found_voices) if found_voices else "voices.bin"
        search_paths_str = ", ".join(str(d) for d in search_dirs)
        raise RuntimeError(
            f"Kokoro ONNX voices file missing: '{missing_v}'. "
            f"Please download 'voices.bin' and place it in '{search_dirs[0]}' or set KOKORO_VOICES_PATH. "
            f"Searched directories: [{search_paths_str}]"
        )

    return found_model, found_voices


def patch_kokoro_onnx_speed_dtype(kokoro_instance: Any) -> None:
    """Ensure kokoro_onnx instance passes 'speed' as float32 to match ONNX model expectations.
    
    kokoro_onnx 0.4.7 hardcodes speed array dtype as np.int32 in _create_audio, but ONNX model exports
    (such as kokoro-v1.0.onnx) expect tensor(float).
    """
    import numpy as np
    import kokoro_onnx

    orig_create_audio = getattr(kokoro_instance, "_create_audio", None)
    if not orig_create_audio:
        return

    def _fixed_create_audio(phonemes: str, voice: Any, speed: float) -> Tuple[Any, int]:
        phonemes_clean = phonemes[:kokoro_onnx.MAX_PHONEME_LENGTH]
        tokens = np.array(kokoro_instance.tokenizer.tokenize(phonemes_clean), dtype=np.int64)
        style = voice[len(tokens)]
        input_tokens = [[0, *tokens, 0]]
        
        inputs = {
            "input_ids": input_tokens,
            "style": np.array(style, dtype=np.float32),
            "speed": np.array([speed], dtype=np.float32),
        }
        audio = kokoro_instance.sess.run(None, inputs)[0]
        return audio, kokoro_onnx.SAMPLE_RATE

    kokoro_instance._create_audio = _fixed_create_audio


class KokoroTTSProvider(TTSProvider):
    provider_name = "kokoro"
    capability = CapabilityMetadata(
        max_resolution="1080p",
        supports_9_16=True,
        local_only=True,
        license_note="Kokoro TTS model inference; $0 local inference",
    )
    error_type = ProviderErrorType.UNCONFIGURED
    cost_meta = CostUsageMetadata(estimated_usd=0.0, provider_type="local")

    def health_check(self) -> ProviderHealth:
        try:
            import importlib.util
            has_kokoro = (
                importlib.util.find_spec("kokoro") is not None
                or importlib.util.find_spec("kokoro_onnx") is not None
            )
            model_ok = False
            try:
                m_path, v_path = resolve_kokoro_model_paths()
                model_ok = m_path.exists() and v_path.exists()
            except Exception:
                model_ok = False

            return ProviderHealth(
                healthy=has_kokoro and model_ok,
                provider_name=self.provider_name,
                details={
                    "kokoro_available": has_kokoro,
                    "model_files_present": model_ok,
                    "mode": "kokoro_tts",
                },
            )
        except Exception as exc:
            return ProviderHealth(
                healthy=False,
                provider_name=self.provider_name,
                error=str(exc),
            )

    def _write_wav(self, audio_data: Any, sample_rate: int, out_path: Path) -> None:
        """Write raw audio float array to 16-bit PCM WAV file."""
        import numpy as np
        # Convert float array in [-1.0, 1.0] to int16 PCM
        audio_arr = np.asarray(audio_data, dtype=np.float32)
        audio_int16 = (np.clip(audio_arr, -1.0, 1.0) * 32767.0).astype(np.int16)

        with wave.open(str(out_path), "wb") as wav_file:
            wav_file.setnchannels(1)  # Mono
            wav_file.setsampwidth(2)  # 16-bit = 2 bytes
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(audio_int16.tobytes())

    def synthesize(self, text: str, out_path: str, voice_id: str | None = None, **kwargs) -> str:
        if not text or not text.strip():
            raise ValueError("Kokoro TTS synthesis received empty text")

        out = Path(out_path).resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        start_time = time.time()

        synthesized = False
        last_error = ""

        # 1. Try kokoro_onnx Python package if installed
        try:
            from kokoro_onnx import Kokoro
            model_path, voices_path = resolve_kokoro_model_paths()
            kokoro = Kokoro(str(model_path), str(voices_path))
            patch_kokoro_onnx_speed_dtype(kokoro)
            
            target_voice = voice_id or "af_sarah"
            available_voices = kokoro.get_voices() if hasattr(kokoro, "get_voices") else []
            if available_voices and target_voice not in available_voices:
                # Fall back to first available voice if requested voice_id is not in voices file
                target_voice = available_voices[0]

            samples, sample_rate = kokoro.create(text, voice=target_voice, speed=1.0)
            self._write_wav(samples, sample_rate, out)
            synthesized = True
        except RuntimeError:
            raise
        except Exception as exc:
            last_error = str(exc)

        # 2. Try kokoro CLI if installed on system PATH
        if not synthesized:
            import subprocess
            try:
                res = subprocess.run(
                    ["kokoro", "--text", text, "--out", str(out)],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if res.returncode == 0 and out.exists() and out.stat().st_size > 0:
                    synthesized = True
            except Exception:
                pass

        if not synthesized:
            raise RuntimeError(
                f"Kokoro TTS synthesis failed: {last_error or 'Could not generate audio using kokoro_onnx or CLI'}"
            )

        latency_sec = time.time() - start_time
        prov_path = out.with_suffix(out.suffix + ".provenance.json")
        prov_path.write_text(
            json.dumps({
                "provider": self.provider_name,
                "input_text_hash_prefix": hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
                "voice_id": voice_id or "default_kokoro",
                "file_size_bytes": out.stat().st_size if out.exists() else 0,
                "synthesis_latency_sec": round(latency_sec, 3),
                "mode": "kokoro_speech",
            }, indent=2),
            encoding="utf-8",
        )
        return str(out)
