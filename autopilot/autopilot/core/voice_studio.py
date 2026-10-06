"""Neural Voice Studio & Truth Alignment Orchestrator — Phase 1.

Unifies:
  1. Pronunciation Normalization (Acronyms, numbers, scientific terms, custom dictionary)
  2. Multi-Provider Voice Synthesis (EdgeTTS, Kokoro ONNX, Windows SAPI, Mock)
  3. Prosody Profiles (energetic, documentary, mysterious, conversational)
  4. Channel Narrator Binding (default_voice_id and scene overrides)
  5. Audio Mastering (44.1 kHz stereo, -16 LUFS, ≤ -1 dBTP)
  6. Faster-Whisper Truth Pass & Word-Level Alignment
  7. Pronunciation QA & Audio Acceptance Metrics
  8. MaterializedTimeline integration
"""
from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Optional, List, Dict, Any, Union
from pydantic import BaseModel, Field

from autopilot.core.pronunciation_normalizer import PronunciationNormalizer
from autopilot.core.audio_mastering import AudioMasteringEngine, AudioMasteringResult
from autopilot.core.audio_truth_pass import AudioTruthPipeline, VoiceQAReport, VoiceDefectCode
from autopilot.providers.tts_factory import get_tts_provider
from autopilot.core.timeline import (
    MaterializedTimeline,
    MaterializedScene,
    WordTimestamp,
    CaptionPhrase,
    MaterializedNarration,
    MaterializedAudioPlan,
    MaterializedCaptionPlan,
)


class ProsodyProfile(str, Enum):
    ENERGETIC = "energetic"
    DOCUMENTARY = "documentary"
    MYSTERIOUS = "mysterious"
    CONVERSATIONAL = "conversational"
    PAYOFF = "payoff"


class ProsodySettings(BaseModel):
    profile: ProsodyProfile = ProsodyProfile.CONVERSATIONAL
    rate: str = "+0%"
    pitch: str = "+0Hz"
    volume: str = "+0%"
    comma_pause_ms: int = 200

    @classmethod
    def from_profile(cls, profile: Union[str, ProsodyProfile]) -> "ProsodySettings":
        p_str = profile.value if isinstance(profile, ProsodyProfile) else str(profile).lower()
        if p_str == "energetic":
            return cls(
                profile=ProsodyProfile.ENERGETIC,
                rate="+8%",
                pitch="+2Hz",
                comma_pause_ms=150,
            )
        elif p_str == "documentary":
            return cls(
                profile=ProsodyProfile.DOCUMENTARY,
                rate="-4%",
                pitch="-3Hz",
                comma_pause_ms=250,
            )
        elif p_str == "mysterious":
            return cls(
                profile=ProsodyProfile.MYSTERIOUS,
                rate="-8%",
                pitch="-2Hz",
                comma_pause_ms=350,
            )
        elif p_str == "payoff":
            return cls(
                profile=ProsodyProfile.PAYOFF,
                rate="-5%",
                pitch="-1Hz",
                comma_pause_ms=300,
            )
        else:
            return cls(
                profile=ProsodyProfile.CONVERSATIONAL,
                rate="+0%",
                pitch="+0Hz",
                comma_pause_ms=200,
            )


def prosody_for_narrative_role(role: Any) -> ProsodyProfile:
    """Map a narrative role to the synthesis prosody for that scene.

    Hook scenes open faster (energetic, ``rate=+8%``), the payoff/CTA scene
    lands slower (``rate=-5%``), and middle scenes stay neutral. Accepts
    ``NarrativeRole`` members or their names in any casing so the timeline,
    pipeline and tests can all feed it directly.
    """
    name = getattr(role, "name", None) or role
    name = str(name).upper().rsplit(".", 1)[-1].strip()
    if name == "HOOK":
        return ProsodyProfile.ENERGETIC
    if name in ("CTA", "PAYOFF"):
        return ProsodyProfile.PAYOFF
    return ProsodyProfile.CONVERSATIONAL


class ChannelVoiceBinding(BaseModel):
    channel_id: str
    default_voice_id: str = "en-US-ChristopherNeural"
    default_provider: str = "kokoro"
    default_prosody: ProsodyProfile = ProsodyProfile.CONVERSATIONAL


class VoiceStudioSceneResult(BaseModel):
    scene_id: str
    raw_text: str
    normalized_text: str
    voice_id: str
    provider_name: str
    prosody_profile: ProsodyProfile
    raw_audio_path: str
    mastered_audio_path: str
    duration_sec: float
    sample_rate: int = 44100
    channels: int = 2
    word_timestamps: List[WordTimestamp] = Field(default_factory=list)
    caption_phrases: List[CaptionPhrase] = Field(default_factory=list)
    qa_report: VoiceQAReport


class NeuralVoiceStudio:
    """Production Neural Voice Studio orchestrating speech synthesis, mastering, and truth alignment."""

    def __init__(
        self,
        normalizer: Optional[PronunciationNormalizer] = None,
        mastering_engine: Optional[AudioMasteringEngine] = None,
        truth_pipeline: Optional[AudioTruthPipeline] = None,
    ):
        self.normalizer = normalizer or PronunciationNormalizer()
        self.mastering_engine = mastering_engine or AudioMasteringEngine()
        self.truth_pipeline = truth_pipeline or AudioTruthPipeline()

    def process_scene_narration(
        self,
        scene_id: str,
        raw_text: str,
        voice_id: Optional[str] = None,
        prosody: Union[str, ProsodyProfile] = ProsodyProfile.CONVERSATIONAL,
        provider_name: str = "kokoro",
        out_dir: Optional[Path] = None,
        target_duration_sec: Optional[float] = None,
    ) -> VoiceStudioSceneResult:
        """Process a single scene narration through the full Phase 1 voice pipeline."""
        out_directory = Path(out_dir or tempfile_get_dir()) / "voice_studio" / scene_id
        out_directory.mkdir(parents=True, exist_ok=True)

        # 1. Normalize pronunciation text while preserving raw text
        normalized_text = self.normalizer.normalize(raw_text)

        # 2. Resolve prosody configuration
        prosody_config = ProsodySettings.from_profile(prosody)

        # 3. Resolve TTS provider
        tts_provider = get_tts_provider(provider_name)
        if tts_provider is None:
            from autopilot.providers.mock_tts import MockTTSProvider
            tts_provider = MockTTSProvider()

        # 4. Synthesize raw audio
        raw_out_path = out_directory / f"{scene_id}_raw.wav"
        resolved_voice = voice_id or "default"
        synth_kwargs = {
            "voice": resolved_voice,
            "rate": prosody_config.rate,
            "pitch": prosody_config.pitch,
            "volume": prosody_config.volume,
        }

        try:
            actual_raw_path = tts_provider.synthesize(
                text=normalized_text,
                out_path=str(raw_out_path),
                **synth_kwargs,
            )
        except Exception as exc:
            # Fallback to mock synthesis if provider fails
            from autopilot.providers.mock_tts import MockTTSProvider
            mock = MockTTSProvider()
            actual_raw_path = mock.synthesize(
                text=normalized_text,
                out_path=str(raw_out_path),
                **synth_kwargs,
            )

        # 5. Audio Mastering (44.1 kHz stereo, -16 LUFS, ≤ -1 dBTP)
        mastered_out_path = out_directory / f"{scene_id}_mastered.wav"
        master_res = self.mastering_engine.master_audio(
            input_audio_path=actual_raw_path,
            output_audio_path=mastered_out_path,
        )

        final_audio_path = master_res.output_path if master_res.success else actual_raw_path
        measured_duration = master_res.duration_sec

        # 6. Faster-Whisper Truth Pass & Pronunciation QA
        qa_report = self.truth_pipeline.execute_truth_pass(
            audio_path=final_audio_path,
            expected_text=normalized_text,
            expected_duration_sec=target_duration_sec,
            mastered_lufs=master_res.integrated_lufs,
            mastered_true_peak=master_res.true_peak_dbtp,
        )

        # 7. Generate WordTimestamp list and CaptionPhrases
        word_ts_list = [
            WordTimestamp(
                word=w.word,
                start=w.start_sec,
                end=w.end_sec,
                confidence=w.probability,
            )
            for w in qa_report.authoritative_words
        ]

        # Group words into clean caption phrases
        caption_phrases: List[CaptionPhrase] = []
        if word_ts_list:
            caption_phrases.append(
                CaptionPhrase(
                    phrase_text=raw_text,
                    start_sec=word_ts_list[0].start,
                    end_sec=word_ts_list[-1].end,
                    words=word_ts_list,
                )
            )

        return VoiceStudioSceneResult(
            scene_id=scene_id,
            raw_text=raw_text,
            normalized_text=normalized_text,
            voice_id=resolved_voice,
            provider_name=provider_name,
            prosody_profile=prosody_config.profile,
            raw_audio_path=actual_raw_path,
            mastered_audio_path=final_audio_path,
            duration_sec=measured_duration,
            sample_rate=master_res.sample_rate,
            channels=master_res.channels,
            word_timestamps=word_ts_list,
            caption_phrases=caption_phrases,
            qa_report=qa_report,
        )

    def process_materialized_timeline(
        self,
        timeline: MaterializedTimeline,
        channel_binding: Optional[ChannelVoiceBinding] = None,
        provider_name: Optional[str] = None,
        out_dir: Optional[Path] = None,
    ) -> MaterializedTimeline:
        """Process all scenes in a MaterializedTimeline and attach mastered audio & Whisper truth timestamps."""
        binding = channel_binding or ChannelVoiceBinding(channel_id=timeline.job_id)
        chosen_provider = provider_name or binding.default_provider

        current_time_offset = 0.0
        for scene in timeline.scenes:
            voice_id = binding.default_voice_id
            # Role-based prosody (plan 3.2): hook +8% and payoff -5% override
            # the channel default; neutral scenes keep the binding's profile.
            role_prosody = prosody_for_narrative_role(scene.narrative_role)
            prosody = (
                binding.default_prosody
                if role_prosody is ProsodyProfile.CONVERSATIONAL
                else role_prosody
            )

            res = self.process_scene_narration(
                scene_id=scene.scene_id,
                raw_text=scene.narration.text,
                voice_id=voice_id,
                prosody=prosody,
                provider_name=chosen_provider,
                out_dir=out_dir,
                target_duration_sec=scene.timing.duration_sec,
            )

            # Shift word timestamps by scene start offset
            shifted_words = [
                WordTimestamp(
                    word=w.word,
                    start=round(current_time_offset + w.start, 3),
                    end=round(current_time_offset + w.end, 3),
                    confidence=w.confidence,
                )
                for w in res.word_timestamps
            ]

            scene.narration.audio_artifact_path = res.mastered_audio_path
            scene.narration.word_timestamps = shifted_words

            scene.audio_plan.voice_path = res.mastered_audio_path
            scene.audio_plan.voice_duration_sec = res.duration_sec
            scene.audio_plan.sample_rate = res.sample_rate
            scene.audio_plan.channels = res.channels

            if res.caption_phrases:
                scene.caption_plan.phrases = [
                    CaptionPhrase(
                        phrase_text=p.phrase_text,
                        start_sec=round(current_time_offset + p.start_sec, 3),
                        end_sec=round(current_time_offset + p.end_sec, 3),
                        words=shifted_words,
                    )
                    for p in res.caption_phrases
                ]

            current_time_offset += scene.timing.duration_sec

        return timeline


def tempfile_get_dir() -> Path:
    import tempfile
    return Path(tempfile.gettempdir())
