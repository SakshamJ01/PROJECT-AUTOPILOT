"""TTS Provider Factory module."""
from __future__ import annotations
from typing import Optional

from autopilot.providers.contracts import TTSProvider
from autopilot.providers.mock_tts import MockTTSProvider
from autopilot.providers.kokoro_tts_provider import KokoroTTSProvider


def get_tts_provider(name: str = "mock") -> Optional[TTSProvider]:
    """Factory helper to retrieve configured TTS provider.

    Options:
      - 'none' / 'off' / 'disabled' -> None (no-TTS behavior)
      - 'mock' / 'mock_tts' -> MockTTSProvider
      - 'kokoro' -> KokoroTTSProvider
      - 'windows_sapi' / 'sapi' -> WindowsSAPITTSProvider
    """
    name_clean = (name or "none").lower().strip()
    if name_clean in ("none", "off", "disabled"):
        return None
    elif name_clean in ("mock", "mock_tts"):
        return MockTTSProvider()
    elif name_clean == "kokoro":
        return KokoroTTSProvider()
    elif name_clean in ("windows_sapi", "sapi"):
        from autopilot.providers.sapi_tts_provider import WindowsSAPITTSProvider
        return WindowsSAPITTSProvider()
    else:
        raise ValueError(
            f"Unknown TTS provider: '{name}'. Available: 'none', 'mock', 'kokoro', 'windows_sapi'"
        )
