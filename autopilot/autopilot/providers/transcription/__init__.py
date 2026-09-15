"""Transcription Engine Package — faster-whisper integration & protocols."""
from autopilot.core.contracts import (
    TranscriptionRequest,
    TranscriptionResult,
    WordTimestamp,
    SegmentTimestamp,
    TranscriptionEngineProtocol,
)
from autopilot.providers.transcription.faster_whisper_engine import FasterWhisperEngine

__all__ = [
    "TranscriptionRequest",
    "TranscriptionResult",
    "WordTimestamp",
    "SegmentTimestamp",
    "TranscriptionEngineProtocol",
    "FasterWhisperEngine",
]
