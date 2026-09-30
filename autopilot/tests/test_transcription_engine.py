"""Tests for FasterWhisperEngine and subtitle formatting."""
import pytest
from pathlib import Path

from autopilot.core.contracts import (
    TranscriptionRequest,
    TranscriptionResult,
    WordTimestamp,
    SegmentTimestamp,
)
from autopilot.providers.transcription.faster_whisper_engine import FasterWhisperEngine


def test_srt_formatting():
    engine = FasterWhisperEngine()
    words = [
        WordTimestamp(word="AI", start_sec=0.0, end_sec=0.5),
        WordTimestamp(word="is", start_sec=0.5, end_sec=0.8),
        WordTimestamp(word="powerful", start_sec=0.8, end_sec=1.5),
    ]
    segments = [
        SegmentTimestamp(segment_id=1, start_sec=0.0, end_sec=1.5, text="AI is powerful", words=words),
        SegmentTimestamp(segment_id=2, start_sec=2.0, end_sec=3.5, text="Second scene line", words=[]),
    ]
    srt = engine.generate_srt(segments)
    assert "00:00:00,000 --> 00:00:01,500" in srt
    assert "AI is powerful" in srt
    assert "00:00:02,000 --> 00:00:03,500" in srt
    assert "Second scene line" in srt


def test_ass_karaoke_highlight_formatting():
    engine = FasterWhisperEngine()
    words = [
        WordTimestamp(word="Artificial", start_sec=0.0, end_sec=0.6),
        WordTimestamp(word="Intelligence", start_sec=0.6, end_sec=1.4),
    ]
    segments = [
        SegmentTimestamp(segment_id=1, start_sec=0.0, end_sec=1.4, text="Artificial Intelligence", words=words),
    ]
    ass = engine.generate_ass(segments)
    assert "[Script Info]" in ass
    assert "[V4+ Styles]" in ass
    assert "PlayResX: 1080" in ass
    assert "PlayResY: 1920" in ass
    assert "Dialogue: 0,0:00:00.00,0:00:01.40" in ass
    assert "\\k" in ass  # Karaoke / word highlight timing tag present


def test_silence_is_not_fabricated_into_word_timestamps(tmp_path):
    """P0: real ASR must never invent words for silent/empty audio.

    Previously this path silently produced fake word timestamps, which
    corrupted kinetic captions and QA verdicts. Production now fails closed
    and reports exactly what the model actually heard (nothing).
    """
    audio_path = tmp_path / "test_audio.wav"
    audio_path.write_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00D\xac\x00\x00\x88X\x01\x00\x02\x00\x10\x00data\x00\x00\x00\x00")

    engine = FasterWhisperEngine()
    req = TranscriptionRequest(audio_path=str(audio_path), language="en", word_timestamps=True)
    res = engine.transcribe(req)

    assert res.engine_name == "faster-whisper"
    # No fabricated words for silence.
    assert all(len(seg.words) == 0 for seg in res.segments)
    assert res.srt_content is not None
    assert res.ass_content is not None


def test_missing_audio_raises_instead_of_falling_back(tmp_path):
    """P0 fail-closed: a missing/corrupt file must raise, never fake-align."""
    engine = FasterWhisperEngine(allow_fallback=False)
    req = TranscriptionRequest(audio_path=str(tmp_path / "nope.wav"), word_timestamps=True)
    try:
        engine.transcribe(req)
    except Exception:
        return
    raise AssertionError("expected fail-closed error for missing audio")


def test_explicit_allow_fallback_still_supported(tmp_path):
    """Non-production callers may still opt into the deterministic fallback.

    The fallback only engages when real decoding actually fails, so this uses
    a file that is not decodable audio.
    """
    audio_path = tmp_path / "not_audio.wav"
    audio_path.write_bytes(b"RIFF" + b"this is definitely not decodable audio" * 8)

    engine = FasterWhisperEngine(allow_fallback=True)
    req = TranscriptionRequest(audio_path=str(audio_path), language="en", word_timestamps=True)
    res = engine.transcribe(req)

    assert res.engine_name == "fallback-aligner"
    assert res.duration_sec >= 0.0
    assert len(res.segments) >= 1
    assert res.srt_content is not None
    assert res.ass_content is not None
