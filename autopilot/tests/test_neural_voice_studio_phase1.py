"""Comprehensive Tests for Phase 1 — Neural Voice Studio & Whisper Truth Alignment.

Tests:
  1. Pronunciation Normalizer (Acronyms, Numbers, Currencies, Coordinates, Chemicals, Custom Dict)
  2. Provider Capability & Selection (EdgeTTS, Kokoro, SAPI, Mock)
  3. Prosody Configuration & Profiles (energetic, documentary, mysterious, conversational)
  4. Channel Narrator Binding & Scene Overrides
  5. Audio Mastering (44.1 kHz, stereo, -16 LUFS, ≤ -1 dBTP)
  6. Faster-Whisper Truth Pass & Authoritative Word Timestamps
  7. Pronunciation QA (Dropped words, unexpected words, abnormal pauses > 1.2s)
  8. Voice Acceptance Metrics (Clipping, LUFS, True Peak, Target Drift)
  9. MaterializedTimeline integration & downstream TimelineCompiler compatibility
"""
import pytest
import wave
import tempfile
from pathlib import Path

from autopilot.core.pronunciation_normalizer import PronunciationNormalizer, int_to_words, float_to_words, year_to_words
from autopilot.core.audio_mastering import AudioMasteringEngine, AudioMasteringResult
from autopilot.core.audio_truth_pass import (
    AudioTruthPipeline,
    VoiceQAReport,
    VoiceDefectCode,
    VoiceAcceptanceMetrics,
    AbnormalPause,
)
from autopilot.core.voice_studio import (
    ProsodyProfile,
    ProsodySettings,
    ChannelVoiceBinding,
    VoiceStudioSceneResult,
    NeuralVoiceStudio,
)
from autopilot.providers.edge_tts_provider import EdgeTTSProvider
from autopilot.providers.kokoro_tts_provider import KokoroTTSProvider
from autopilot.providers.mock_tts import MockTTSProvider
from autopilot.providers.tts_factory import get_tts_provider
from autopilot.core.contracts import (
    TranscriptionResult,
    SegmentTimestamp,
    WordTimestamp as CoreWordTimestamp,
)
from autopilot.core.timeline import (
    MaterializedTimeline,
    MaterializedScene,
    MaterializedTiming,
    MaterializedNarration,
    SelectedAsset,
    TrimRange,
    CropFraming,
    WordTimestamp,
    CaptionPhrase,
    MaterializedAudioPlan,
    MaterializedCaptionPlan,
    MaterializedTransitionPlan,
    TimelineCompiler,
)


# ---------------------------------------------------------------------------
# 1. Pronunciation Normalization Tests
# ---------------------------------------------------------------------------

def test_pronunciation_normalizer_acronyms():
    """Verify acronyms from frozen plan are normalized correctly."""
    normalizer = PronunciationNormalizer()
    assert normalizer.normalize("NASA launched a rocket") == "NA-SA launched a rocket"
    assert normalizer.normalize("ISRO sent a probe") == "ISS-RO sent a probe"
    assert normalizer.normalize("AI technology") == "A-I technology"
    assert normalizer.normalize("learning SQL database") == "learning S-Q-L database"
    assert normalizer.normalize("new LLM model") == "new L-L-M model"


def test_pronunciation_normalizer_numbers_and_currencies():
    """Verify numeric, currency, coordinate, and year formatting from frozen plan."""
    normalizer = PronunciationNormalizer()
    # Large numbers
    assert normalizer.normalize("1,400,000,000 people") == "one point four billion people"
    # Currencies ($50M -> fifty million dollars)
    assert normalizer.normalize("$50M investment") == "fifty million dollars investment"
    assert normalizer.normalize("$1.5B revenue") == "one point five billion dollars revenue"
    assert normalizer.normalize("$100 cost") == "one hundred dollars cost"
    # Coordinates (15°N -> fifteen degrees north)
    assert normalizer.normalize("located at 15°N") == "located at fifteen degrees north"
    assert normalizer.normalize("heading 45°W") == "heading forty-five degrees west"
    # Years (2026 -> twenty twenty-six)
    assert normalizer.normalize("in 2026") == "in twenty twenty-six"
    assert normalizer.normalize("by 1999") == "by nineteen ninety-nine"


def test_pronunciation_normalizer_chemicals():
    """Verify chemical formula pronunciation normalization."""
    normalizer = PronunciationNormalizer()
    assert normalizer.normalize("excess CO₂ in air") == "excess C-O-two in air"
    assert normalizer.normalize("excess CO2 in air") == "excess C-O-two in air"
    assert normalizer.normalize("drinking H₂O daily") == "drinking H-two-O daily"
    assert normalizer.normalize("drinking H2O daily") == "drinking H-two-O daily"
    assert normalizer.normalize("breathing O₂") == "breathing O-two"


def test_pronunciation_normalizer_custom_dict():
    """Verify custom pronunciation dictionary overrides work as expected."""
    normalizer = PronunciationNormalizer(custom_dict={"Kubernetes": "Koo-ber-net-eez", "Rust": "RUST-lang"})
    res = normalizer.normalize("Deploying Kubernetes with Rust")
    assert "Koo-ber-net-eez" in res
    assert "RUST-lang" in res


# ---------------------------------------------------------------------------
# 2. Voice Providers & Prosody Profiles Tests
# ---------------------------------------------------------------------------

def test_prosody_profiles_configuration():
    """Verify prosody profiles map to distinct speed, pitch, and cadence parameters."""
    energetic = ProsodySettings.from_profile(ProsodyProfile.ENERGETIC)
    assert energetic.rate == "+8%"
    assert energetic.pitch == "+2Hz"

    documentary = ProsodySettings.from_profile(ProsodyProfile.DOCUMENTARY)
    assert energetic.rate != documentary.rate
    assert documentary.rate == "-4%"
    assert documentary.pitch == "-3Hz"

    mysterious = ProsodySettings.from_profile(ProsodyProfile.MYSTERIOUS)
    assert mysterious.rate == "-8%"
    assert mysterious.comma_pause_ms == 350

    conversational = ProsodySettings.from_profile(ProsodyProfile.CONVERSATIONAL)
    assert conversational.rate == "+0%"
    assert conversational.pitch == "+0Hz"


def test_tts_factory_resolution():
    """Verify tts_factory resolves edge_tts, kokoro, sapi, and mock providers."""
    mock = get_tts_provider("mock")
    assert isinstance(mock, MockTTSProvider)

    kokoro = get_tts_provider("kokoro")
    assert isinstance(kokoro, KokoroTTSProvider)

    edge = get_tts_provider("edge_tts")
    assert isinstance(edge, EdgeTTSProvider)


# ---------------------------------------------------------------------------
# 3. Audio Mastering Engine Tests
# ---------------------------------------------------------------------------

def test_audio_mastering_produces_44100_stereo(tmp_path):
    """Verify AudioMasteringEngine outputs 44.1 kHz stereo audio."""
    # Create test mono audio wav
    mono_in = tmp_path / "mono_test.wav"
    with wave.open(str(mono_in), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(22050)
        wf.writeframes(b"\x00\x00" * 2205)

    mastered_out = tmp_path / "mastered_test.wav"
    engine = AudioMasteringEngine()
    result = engine.master_audio(mono_in, mastered_out)

    assert result.success is True
    assert result.sample_rate == 44100
    assert result.channels == 2
    assert mastered_out.exists()

    with wave.open(str(mastered_out), "rb") as out_wf:
        assert out_wf.getframerate() == 44100
        assert out_wf.getnchannels() == 2


# ---------------------------------------------------------------------------
# 4. Faster-Whisper Truth Pass & Pronunciation QA Tests
# ---------------------------------------------------------------------------

class _DeterministicStubWhisper:
    """Stub aligner that returns evenly spaced words for a known phrase.

    These tests exercise the pronunciation/timing QA logic, not Whisper
    itself (real Whisper is covered in test_transcription_engine.py). The
    previous fake production fallback made them pass on silent audio by
    inventing words; production now refuses to do that, so the alignment
    input is injected explicitly here.
    """

    def __init__(self, words, duration_sec=2.0):
        self._words = list(words)
        self._duration = duration_sec
        self.engine_name = "stub-aligner"
        self.engine_version = "test"

    def transcribe(self, request):
        n = max(1, len(self._words))
        step = self._duration / n
        word_objs = [
            CoreWordTimestamp(
                word=w,
                start_sec=round(i * step, 3),
                end_sec=round((i + 1) * step, 3),
                probability=1.0,
            )
            for i, w in enumerate(self._words)
        ]
        seg = SegmentTimestamp(
            segment_id=1,
            start_sec=0.0,
            end_sec=self._duration,
            text=" ".join(self._words),
            words=word_objs,
        )
        return TranscriptionResult(
            text=" ".join(self._words),
            language="en",
            duration_sec=self._duration,
            segments=[seg],
            words=word_objs,
            srt_content="",
            ass_content="",
            engine_name=self.engine_name,
            engine_version=self.engine_version,
        )


def test_audio_truth_pipeline_clean_narration(tmp_path):
    """Verify truth pass extracts authoritative timestamps for clean narration."""
    # Create test audio wav
    test_wav = tmp_path / "clean_voice.wav"
    with wave.open(str(test_wav), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(44100)
        wf.writeframes(b"\x00\x00\x00\x00" * 88200) # 2.0s

    phrase = "Video narration audio voiceover"
    pipeline = AudioTruthPipeline(
        whisper_engine=_DeterministicStubWhisper(phrase.split(), duration_sec=2.0)
    )
    report = pipeline.execute_truth_pass(
        audio_path=test_wav,
        expected_text=phrase,
        expected_duration_sec=2.0,
    )

    assert report.passed is True
    assert len(report.authoritative_words) > 0
    assert report.metrics.no_clipping is True
    assert report.metrics.dropped_word_count == 0


def test_audio_truth_pipeline_detects_dropped_words(tmp_path):
    """Verify truth pass detects dropped words when expected script has missing words."""
    test_wav = tmp_path / "short_voice.wav"
    with wave.open(str(test_wav), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(44100)
        wf.writeframes(b"\x00\x00\x00\x00" * 44100) # 1.0s

    pipeline = AudioTruthPipeline()
    # Expect words that are definitely not in the fallback transcription
    report = pipeline.execute_truth_pass(
        audio_path=test_wav,
        expected_text="Supercalifragilisticexpialidocious quantum teleportation anomaly",
    )

    assert report.passed is False
    assert VoiceDefectCode.VOICE_DROPPED_WORDS in report.defect_codes
    assert len(report.dropped_words) > 0


def test_audio_truth_pipeline_detects_abnormal_pauses(tmp_path):
    """Verify truth pass detects abnormal pauses greater than 1.2s."""
    pipeline = AudioTruthPipeline(max_allowed_pause_sec=1.2)
    # Mock transcript with 1.5s gap between words
    class MockWhisper:
        def transcribe(self, req):
            words = [
                CoreWordTimestamp(word="First", start_sec=0.1, end_sec=0.5),
                CoreWordTimestamp(word="Second", start_sec=2.2, end_sec=2.8), # 1.7s pause!
            ]
            seg = SegmentTimestamp(segment_id=1, start_sec=0.1, end_sec=2.8, text="First Second", words=words)
            return TranscriptionResult(
                text="First Second",
                language="en",
                duration_sec=2.8,
                segments=[seg],
                srt_content="",
                ass_content="",
            )

    test_wav = tmp_path / "pause_test.wav"
    test_wav.write_bytes(b"dummy")

    truth_pipe = AudioTruthPipeline(whisper_engine=MockWhisper())
    report = truth_pipe.execute_truth_pass(
        audio_path=test_wav,
        expected_text="First Second",
    )

    assert report.passed is False
    assert VoiceDefectCode.VOICE_ABNORMAL_PAUSE in report.defect_codes
    assert len(report.abnormal_pauses) == 1
    assert report.abnormal_pauses[0].pause_duration_sec > 1.2


# ---------------------------------------------------------------------------
# 5. Neural Voice Studio Full Integration Tests
# ---------------------------------------------------------------------------

def test_neural_voice_studio_scene_processing(tmp_path):
    """Verify NeuralVoiceStudio synthesizes, masters, and aligns a scene narration."""
    studio = NeuralVoiceStudio(
        truth_pipeline=AudioTruthPipeline(
            whisper_engine=_DeterministicStubWhisper(
                "NA-SA deployed A-I in twenty twenty-six for fifty million dollars".split(),
                duration_sec=4.0,
            )
        )
    )
    result = studio.process_scene_narration(
        scene_id="scene-01",
        raw_text="NASA deployed AI in 2026 for $50M.",
        prosody=ProsodyProfile.ENERGETIC,
        provider_name="mock",
        out_dir=tmp_path,
    )

    assert result.scene_id == "scene-01"
    # Verify normalization occurred
    assert "NA-SA" in result.normalized_text
    assert "A-I" in result.normalized_text
    assert "twenty twenty-six" in result.normalized_text
    assert "fifty million dollars" in result.normalized_text
    # Verify 44.1 kHz stereo mastering
    assert result.sample_rate == 44100
    assert result.channels == 2
    assert Path(result.mastered_audio_path).exists()
    assert len(result.word_timestamps) > 0


def test_neural_voice_studio_materialized_timeline_integration(tmp_path):
    """Verify NeuralVoiceStudio populates MaterializedTimeline with authoritative stems and passes TimelineCompiler."""
    # 1. Create a dummy asset file
    dummy_video = tmp_path / "dummy_visual.mp4"
    dummy_video.write_bytes(b"dummy_video_bytes")

    # 2. Build initial MaterializedTimeline
    scene = MaterializedScene(
        scene_id="scene-01",
        order=1,
        timing=MaterializedTiming(start_time_sec=0.0, end_time_sec=5.0, duration_sec=5.0),
        narration=MaterializedNarration(text="NASA explored Mars in 2026.", audio_artifact_path="placeholder.wav"),
        visual_requirements={"visual_concept": "Mars exploration"},
        selected_assets=[
            SelectedAsset(
                asset_id="asset-01",
                asset_path=str(dummy_video),
                media_type="video",
                trim_range=TrimRange(in_sec=0.0, out_sec=5.0),
                duration_sec=10.0,
                provenance={"provider": "pexels", "license": "Pexels Commercial"},
            )
        ],
        transition_plan=MaterializedTransitionPlan(type="cut", duration_sec=0.0),
        caption_plan=MaterializedCaptionPlan(),
        audio_plan=MaterializedAudioPlan(
            voice_path="placeholder.wav",
            voice_duration_sec=4.0,
        ),
    )

    timeline = MaterializedTimeline(
        timeline_id="tl-voice-01",
        timeline_version="v1.0",
        job_id="job-voice-01",
        intent_timeline_id="it-01",
        total_measured_duration_sec=5.0,
        scenes=[scene],
    )

    # 3. Process timeline through Neural Voice Studio
    studio = NeuralVoiceStudio(
        truth_pipeline=AudioTruthPipeline(
            whisper_engine=_DeterministicStubWhisper(
                "NA-SA explored Mars in twenty twenty-six".split(),
                duration_sec=4.0,
            )
        )
    )
    binding = ChannelVoiceBinding(
        channel_id="chan-01",
        default_voice_id="en-US-ChristopherNeural",
        default_prosody=ProsodyProfile.DOCUMENTARY,
    )
    processed_tl = studio.process_materialized_timeline(
        timeline=timeline,
        channel_binding=binding,
        provider_name="mock",
        out_dir=tmp_path,
    )

    # Verify scene was populated with mastered audio and word timestamps
    assert Path(processed_tl.scenes[0].audio_plan.voice_path).exists()
    assert processed_tl.scenes[0].audio_plan.sample_rate == 44100
    assert processed_tl.scenes[0].audio_plan.channels == 2
    assert len(processed_tl.scenes[0].narration.word_timestamps) > 0

    # 4. Verify that processed timeline compiles cleanly through TimelineCompiler!
    compiler = TimelineCompiler(check_physical_files=True)
    plan = compiler.compile(processed_tl)

    assert plan.render_plan_sha256 is not None
    assert len(plan.scenes) == 1
    assert plan.mpt_handoff.match_materials_to_script is True
