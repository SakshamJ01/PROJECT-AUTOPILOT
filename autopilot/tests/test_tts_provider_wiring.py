"""Focused tests for TTS provider selection and wiring."""
import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path

from autopilot.providers.tts_factory import get_tts_provider
from autopilot.providers.mock_tts import MockTTSProvider
from autopilot.providers.kokoro_tts_provider import KokoroTTSProvider
from autopilot.core.pipeline import PipelineOrchestrator
from autopilot.core.contracts import ScriptDocument, ScriptScene


class TestTTSProviderWiring(unittest.TestCase):
    def test_kokoro_selection_instantiates_kokoro_provider(self):
        provider = get_tts_provider("kokoro")
        self.assertIsNotNone(provider)
        self.assertIsInstance(provider, KokoroTTSProvider)
        self.assertEqual(provider.provider_name, "kokoro")
        self.assertNotIsInstance(provider, MockTTSProvider)

    def test_mock_selection_instantiates_mock_provider(self):
        provider = get_tts_provider("mock")
        self.assertIsNotNone(provider)
        self.assertIsInstance(provider, MockTTSProvider)
        self.assertEqual(provider.provider_name, "mock_tts")

    def test_none_selection_preserves_no_tts(self):
        provider = get_tts_provider("none")
        self.assertIsNone(provider)

    def test_explicit_kokoro_never_falls_back_to_mock(self):
        provider = get_tts_provider("kokoro")
        self.assertIsNotNone(provider)
        self.assertFalse(isinstance(provider, MockTTSProvider))
        self.assertNotEqual(provider.provider_name, "mock_tts")

    def test_pipeline_honors_tts_provider_selection(self):
        import uuid
        p = PipelineOrchestrator()
        job_id = f"test-tts-wiring-{uuid.uuid4().hex[:8]}"
        topic = "Test TTS Wiring"

        script = ScriptDocument(
            contract_version="v1.0.0",
            content_id=job_id,
            topic=topic,
            working_title="Test Title",
            hook="Test Hook",
            cta="Test CTA",
            scenes=[
                ScriptScene(scene_id="scene-01", order=1, narration="Test narration for TTS wiring", visual_intent="Test visual")
            ]
        )

        with patch.object(p.db, "get_latest_research_report_for_topic", return_value=None), \
             patch("autopilot.providers.mock_search.MockSearchProvider.search", return_value=[]), \
             patch("autopilot.providers.mock_script.MockScriptProvider.generate_script", return_value=script), \
             patch("autopilot.core.pipeline.process_scene_assets", return_value=([], {})), \
             patch("autopilot.core.renderer.FFmpegRenderer.render") as mock_render, \
             patch("autopilot.core.pipeline.get_tts_provider") as mock_factory:

            mock_tts = MagicMock()
            mock_tts.provider_name = "kokoro"
            mock_factory.return_value = mock_tts

            try:
                p.run_pipeline(job_id=job_id, topic=topic, tts_provider="kokoro", llm_provider="mock")
            except Exception:
                pass

            mock_factory.assert_called_with("kokoro")

    def test_kokoro_onnx_synthesis_path(self):
        """Test KokoroTTSProvider.synthesize using mocked kokoro_onnx.Kokoro."""
        import tempfile
        import numpy as np
        provider = KokoroTTSProvider()
        
        mock_kokoro_instance = MagicMock()
        # Mock 1 second of audio at 24000Hz sample rate
        mock_audio = np.zeros(24000, dtype=np.float32)
        mock_kokoro_instance.create.return_value = (mock_audio, 24000)
        mock_kokoro_instance.get_voices.return_value = ["af_sarah", "am_adam"]

        with tempfile.TemporaryDirectory() as tmp_dir:
            out_file = Path(tmp_dir) / "output.wav"
            m_path = Path(tmp_dir) / "kokoro-v1.0.onnx"
            v_path = Path(tmp_dir) / "voices.bin"
            m_path.touch()
            v_path.touch()

            with patch("kokoro_onnx.Kokoro", return_value=mock_kokoro_instance), \
                 patch("autopilot.providers.kokoro_tts_provider.resolve_kokoro_model_paths", return_value=(m_path, v_path)):

                res = provider.synthesize("Hello from Kokoro ONNX", str(out_file), voice_id="af_sarah")

                assert Path(res).exists()
                assert Path(res).stat().st_size > 0
                mock_kokoro_instance.create.assert_called_once_with("Hello from Kokoro ONNX", voice="af_sarah", speed=1.0)

    def test_kokoro_missing_model_files_raises_explicit_error(self):
        """Test that missing ONNX model or voices files raise an explicit actionable RuntimeError."""
        provider = KokoroTTSProvider()
        with patch("autopilot.providers.kokoro_tts_provider.resolve_kokoro_model_paths") as mock_resolve:
            mock_resolve.side_effect = RuntimeError("Kokoro ONNX model file missing: 'kokoro-v1.0.onnx'.")
            with self.assertRaises(RuntimeError) as cm:
                provider.synthesize("Test text", "out.wav")
    def test_patch_kokoro_onnx_speed_dtype(self):
        """Regression test: verify patch_kokoro_onnx_speed_dtype casts speed to float32 np.ndarray."""
        from autopilot.providers.kokoro_tts_provider import patch_kokoro_onnx_speed_dtype
        import numpy as np

        mock_kokoro = MagicMock()
        mock_kokoro.tokenizer.tokenize.return_value = [1, 2, 3]
        mock_kokoro.sess.get_inputs.return_value = [MagicMock(name="input_ids"), MagicMock(name="style"), MagicMock(name="speed")]
        mock_kokoro.sess.run.return_value = [np.zeros(24000, dtype=np.float32)]

        patch_kokoro_onnx_speed_dtype(mock_kokoro)
        
        # Invoke patched _create_audio
        audio, sr = mock_kokoro._create_audio("test phonemes", np.zeros((10, 64), dtype=np.float32), 1.0)
        assert sr == 24000
        
        # Verify call arguments passed into ONNX session.run
        _, inputs = mock_kokoro.sess.run.call_args[0]
        assert "speed" in inputs
        assert inputs["speed"].dtype == np.float32
        assert inputs["speed"][0] == 1.0


if __name__ == "__main__":
    unittest.main()

