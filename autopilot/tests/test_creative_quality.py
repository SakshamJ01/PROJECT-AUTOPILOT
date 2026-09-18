import unittest
from pathlib import Path
from autopilot.core.contracts import RenderPlan, ScriptScene, ScriptDocument, AssetCandidate, AssetLicense, AssetDimensions
from autopilot.core.renderer import FFmpegRenderer, format_caption, get_system_font
from autopilot.core.asset_pipeline import derive_visual_subject_query
from autopilot.core.asset_scoring import score_candidate


class TestCreativeQuality(unittest.TestCase):
    def test_caption_formatting_max_lines_and_chars(self):
        text = "In 1956, researchers at the Dartmouth workshop coined the term artificial intelligence for the first time."
        caption = format_caption(text, max_chars=28)
        lines = caption.split("\n")
        self.assertLessEqual(len(lines), 2)
        for line in lines:
            self.assertLessEqual(len(line), 28)

    def test_caption_formatting_handles_empty_and_short(self):
        self.assertEqual(format_caption(""), "")
        self.assertEqual(format_caption("Hello world"), "Hello world")

    def test_system_font_detection(self):
        font = get_system_font()
        # On Windows, Arial or Segoe UI should be found
        self.assertIsNotNone(font)
        self.assertTrue("Windows" in font or "Fonts" in font)

    def test_derive_visual_subject_query_strips_abstract_words(self):
        scene = ScriptScene(
            scene_id="s1",
            order=1,
            narration="First, systems were built to solve problems with computers.",
            visual_intent="Animation of abstract data points forming pattern",
            asset_query="problem solving animation",
        )
        query = derive_visual_subject_query(scene, topic="artificial intelligence")
        # 'animation', 'abstract', 'points', 'pattern', 'problem', 'solving' are filtered out
        self.assertNotIn("animation", query.lower())
        self.assertNotIn("abstract", query.lower())

    def test_asset_scoring_rewards_scene_specific_keywords(self):
        # Candidate A: matches specific scene keyword 'microchip'
        cand_a = AssetCandidate(
            candidate_id="cand-chip",
            source_provider="openverse",
            title="Silicon Microchip Wafer in cleanroom",
            tags=["technology", "semiconductor", "microchip"],
            license=AssetLicense(license_name="CC BY-2.0", rights_status="VERIFIED"),
            dimensions=AssetDimensions(width=1080, height=1920),
        )
        # Candidate B: only generic topic 'artificial intelligence'
        cand_b = AssetCandidate(
            candidate_id="cand-generic",
            source_provider="openverse",
            title="Abstract illustration of artificial intelligence",
            tags=["ai", "futuristic"],
            license=AssetLicense(license_name="CC BY-2.0", rights_status="VERIFIED"),
            dimensions=AssetDimensions(width=1080, height=1920),
        )
        criteria = {
            "query": "microchip wafer",
            "visual_intent": "Silicon microchip wafer close-up under cleanroom lighting",
            "narration": "Modern AI algorithms require billions of transistors on a single microchip.",
        }
        score_a, breakdown_a = score_candidate(cand_a, request_criteria=criteria)
        score_b, breakdown_b = score_candidate(cand_b, request_criteria=criteria)
        self.assertGreater(score_a, score_b)
        self.assertGreater(breakdown_a["relevance_score"], breakdown_b["relevance_score"])

    def test_multi_scene_render_with_badges_and_captions(self):
        r = FFmpegRenderer()
        fixture_img = Path(__file__).parent.parent / "autopilot" / "providers" / "fixture_image.png"
        plan = RenderPlan(
            plan_id="p-creative-test",
            content_id="c-creative-test",
            job_id="job-creative-test",
            profile="vertical_short",
            scenes=[
                {
                    "scene_id": "scene-01",
                    "duration_sec": 2.0,
                    "asset_path": str(fixture_img),
                    "audio_path": "",
                    "on_screen_text": "1956: BIRTH OF AI",
                    "narration": "In 1956, researchers coined the term artificial intelligence.",
                },
                {
                    "scene_id": "scene-02",
                    "duration_sec": 2.0,
                    "asset_path": str(fixture_img),
                    "audio_path": "",
                    "on_screen_text": "FACT 02",
                    "narration": "Modern neural networks learn from billions of data points.",
                },
            ],
        )
        out_path = "artifacts/jobs/test-creative/render/final.mp4"
        output = r.render(plan, out_path)
        self.assertIsNotNone(output.checksum_sha256)
        self.assertEqual(output.duration_sec, 4.0)
        self.assertTrue(Path(out_path).exists())
        self.assertGreater(output.file_size_bytes, 0)


if __name__ == "__main__":
    unittest.main()
