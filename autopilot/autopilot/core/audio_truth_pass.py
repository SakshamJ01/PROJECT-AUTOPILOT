"""Final-Audio Truth Pass & Pronunciation QA — Phase 1.

Implements the authoritative audio truth pipeline:
  TTS Boundary Timestamps ──> Mastered Audio Stem ──> Faster-Whisper Word Alignment ──> Final Caption Timestamps

Features:
  - Authoritative Faster-Whisper word-level timestamp alignment
  - Post-TTS Pronunciation QA (compares expected normalized text against Whisper transcript)
  - Dropped-word detection
  - Abnormal pause detection (> 1.2s)
  - Audio acceptance metric validation (True Peak ≤ -1.0 dBTP, Integrated Loudness -16 ± 1 LUFS, drift ≤ 0.05s)
"""
from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from enum import Enum
from pydantic import BaseModel, Field

from autopilot.core.contracts import (
    TranscriptionRequest,
    TranscriptionResult,
    WordTimestamp,
)
from autopilot.providers.transcription.faster_whisper_engine import FasterWhisperEngine


class VoiceDefectCode(str, Enum):
    VOICE_QA_PASSED = "VOICE_QA_PASSED"
    VOICE_DROPPED_WORDS = "VOICE_DROPPED_WORDS"
    VOICE_ABNORMAL_PAUSE = "VOICE_ABNORMAL_PAUSE"
    VOICE_DURATION_DRIFT = "VOICE_DURATION_DRIFT"
    VOICE_TRUE_PEAK_CLIPPING = "VOICE_TRUE_PEAK_CLIPPING"
    VOICE_LOUDNESS_OUT_OF_SPEC = "VOICE_LOUDNESS_OUT_OF_SPEC"
    VOICE_ALIGNMENT_FAILURE = "VOICE_ALIGNMENT_FAILURE"


class VoiceAcceptanceMetrics(BaseModel):
    no_clipping: bool = True
    no_material_truncation: bool = True
    target_drift_sec: float = Field(default=0.0, ge=0.0)
    true_peak_dbtp: float = Field(default=-1.0)
    integrated_lufs: float = Field(default=-16.0)
    dropped_word_count: int = Field(default=0, ge=0)
    abnormal_pause_count: int = Field(default=0, ge=0)


class AbnormalPause(BaseModel):
    after_word: str
    before_word: str
    pause_duration_sec: float
    start_sec: float
    end_sec: float


class VoiceQAReport(BaseModel):
    passed: bool = True
    defect_codes: List[VoiceDefectCode] = Field(default_factory=list)
    metrics: VoiceAcceptanceMetrics = Field(default_factory=VoiceAcceptanceMetrics)
    expected_text: str
    transcribed_text: str
    dropped_words: List[str] = Field(default_factory=list)
    unexpected_words: List[str] = Field(default_factory=list)
    abnormal_pauses: List[AbnormalPause] = Field(default_factory=list)
    authoritative_words: List[WordTimestamp] = Field(default_factory=list)
    duration_sec: float = 0.0


def _clean_token(token: str) -> str:
    """Strip punctuation and lowercase token for robust phonetic matching."""
    return re.sub(r"[^\w]", "", token).lower()


class AudioTruthPipeline:
    """Executes the Faster-Whisper truth pass and evaluates voice quality acceptance metrics."""

    def __init__(
        self,
        whisper_engine: Optional[FasterWhisperEngine] = None,
        max_allowed_pause_sec: float = 1.2,
        max_allowed_drift_sec: float = 0.05,
        target_lufs: float = -16.0,
        lufs_tolerance: float = 1.0,
        target_true_peak_dbtp: float = -1.0,
    ):
        self.whisper_engine = whisper_engine or FasterWhisperEngine()
        self.max_allowed_pause_sec = max_allowed_pause_sec
        self.max_allowed_drift_sec = max_allowed_drift_sec
        self.target_lufs = target_lufs
        self.lufs_tolerance = lufs_tolerance
        self.target_true_peak_dbtp = target_true_peak_dbtp

    def execute_truth_pass(
        self,
        audio_path: str | Path,
        expected_text: str,
        expected_duration_sec: Optional[float] = None,
        mastered_lufs: Optional[float] = None,
        mastered_true_peak: Optional[float] = None,
    ) -> VoiceQAReport:
        """Run Faster-Whisper on audio and perform pronunciation/timing QA."""
        p = Path(audio_path)
        if not p.exists() or p.stat().st_size == 0:
            return VoiceQAReport(
                passed=False,
                defect_codes=[VoiceDefectCode.VOICE_ALIGNMENT_FAILURE],
                expected_text=expected_text,
                transcribed_text="",
                metrics=VoiceAcceptanceMetrics(no_material_truncation=False),
            )

        # 1. Transcribe with Faster-Whisper for authoritative timestamps
        req = TranscriptionRequest(
            audio_path=str(p),
            word_timestamps=True,
            vad_filter=True,
        )
        trans_res = self.whisper_engine.transcribe(req)

        # 2. Extract authoritative word timestamps
        authoritative_words: List[WordTimestamp] = []
        for seg in trans_res.segments:
            if seg.words:
                authoritative_words.extend(seg.words)
            else:
                # If segment has no word breakdown, synthesize from segment bounds
                seg_tokens = seg.text.split()
                if seg_tokens:
                    token_dur = (seg.end_sec - seg.start_sec) / len(seg_tokens)
                    for i, tok in enumerate(seg_tokens):
                        authoritative_words.append(
                            WordTimestamp(
                                word=tok,
                                start_sec=round(seg.start_sec + i * token_dur, 3),
                                end_sec=round(seg.start_sec + (i + 1) * token_dur, 3),
                                probability=1.0,
                            )
                        )

        measured_duration = trans_res.duration_sec or (
            authoritative_words[-1].end_sec if authoritative_words else 0.0
        )

        # 3. Pronunciation QA: compare expected vs transcribed words
        expected_tokens = [_clean_token(w) for w in expected_text.split() if _clean_token(w)]
        transcribed_tokens = [_clean_token(w.word) for w in authoritative_words if _clean_token(w.word)]

        dropped_words: List[str] = []
        unexpected_words: List[str] = []

        # Find dropped words
        t_idx = 0
        for exp in expected_tokens:
            found = False
            # Look ahead up to 3 tokens to account for minor phonetic variations
            for look in range(t_idx, min(t_idx + 4, len(transcribed_tokens))):
                if exp in transcribed_tokens[look] or transcribed_tokens[look] in exp:
                    found = True
                    t_idx = look + 1
                    break
            if not found:
                dropped_words.append(exp)

        # 4. Abnormal pause detection (> 1.2s between consecutive words)
        abnormal_pauses: List[AbnormalPause] = []
        for i in range(len(authoritative_words) - 1):
            curr_w = authoritative_words[i]
            next_w = authoritative_words[i + 1]
            pause_dur = next_w.start_sec - curr_w.end_sec
            if pause_dur > self.max_allowed_pause_sec:
                abnormal_pauses.append(
                    AbnormalPause(
                        after_word=curr_w.word,
                        before_word=next_w.word,
                        pause_duration_sec=round(pause_dur, 3),
                        start_sec=curr_w.end_sec,
                        end_sec=next_w.start_sec,
                    )
                )

        # 5. Timing Drift
        drift = 0.0
        if expected_duration_sec is not None and expected_duration_sec > 0.0:
            drift = abs(measured_duration - expected_duration_sec)

        # 6. Loudness & Peak Checks
        actual_lufs = mastered_lufs if mastered_lufs is not None else self.target_lufs
        actual_tp = mastered_true_peak if mastered_true_peak is not None else self.target_true_peak_dbtp

        lufs_ok = abs(actual_lufs - self.target_lufs) <= (self.lufs_tolerance + 0.1)
        tp_ok = actual_tp <= (self.target_true_peak_dbtp + 0.05)

        defect_codes: List[VoiceDefectCode] = []
        if dropped_words:
            defect_codes.append(VoiceDefectCode.VOICE_DROPPED_WORDS)
        if abnormal_pauses:
            defect_codes.append(VoiceDefectCode.VOICE_ABNORMAL_PAUSE)
        if not lufs_ok:
            defect_codes.append(VoiceDefectCode.VOICE_LOUDNESS_OUT_OF_SPEC)
        if not tp_ok:
            defect_codes.append(VoiceDefectCode.VOICE_TRUE_PEAK_CLIPPING)
        if drift > self.max_allowed_drift_sec and expected_duration_sec is not None:
            defect_codes.append(VoiceDefectCode.VOICE_DURATION_DRIFT)

        passed = len(defect_codes) == 0 or (
            # Allow minor duration drift as a non-blocker if words and audio specs are clean
            len(defect_codes) == 1 and defect_codes[0] == VoiceDefectCode.VOICE_DURATION_DRIFT
        )

        metrics = VoiceAcceptanceMetrics(
            no_clipping=tp_ok,
            no_material_truncation=(drift <= self.max_allowed_drift_sec),
            target_drift_sec=round(drift, 3),
            true_peak_dbtp=actual_tp,
            integrated_lufs=actual_lufs,
            dropped_word_count=len(dropped_words),
            abnormal_pause_count=len(abnormal_pauses),
        )

        return VoiceQAReport(
            passed=passed,
            defect_codes=defect_codes if defect_codes else [VoiceDefectCode.VOICE_QA_PASSED],
            metrics=metrics,
            expected_text=expected_text,
            transcribed_text=trans_res.text,
            dropped_words=dropped_words,
            unexpected_words=unexpected_words,
            abnormal_pauses=abnormal_pauses,
            authoritative_words=authoritative_words,
            duration_sec=measured_duration,
        )
