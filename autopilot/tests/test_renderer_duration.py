import unittest
from pathlib import Path
from autopilot.core.renderer import FFmpegRenderer
from autopilot.core.contracts import RenderPlan, RenderScene

class TestRendererDuration(unittest.TestCase):
    def test_two_scenes_sum_duration(self):
        plan = RenderPlan(
            plan_id="p1", content_id="c1", job_id="t", profile="vertical_short",
            scenes=[
                {"scene_id":"s1","duration_sec":6.0,"asset_path":"","audio_path":""},
                {"scene_id":"s2","duration_sec":6.0,"asset_path":"","audio_path":""},
            ]
        )
        total = sum(s.get("duration_sec",5) for s in plan.scenes)
        self.assertEqual(total, 12.0)

    def test_three_scenes_sum_duration(self):
        plan = RenderPlan(
            plan_id="p1", content_id="c1", job_id="t", profile="vertical_short",
            scenes=[
                {"scene_id":"s1","duration_sec":6.0},
                {"scene_id":"s2","duration_sec":6.0},
                {"scene_id":"s3","duration_sec":6.0},
            ],
        )
        total = sum(s.get("duration_sec",5) for s in plan.scenes)
        self.assertEqual(total, 18.0)

    def test_render_returns_checksum(self):
        from autopilot.core.renderer import FFmpegRenderer
        from autopilot.core.contracts import RenderPlan
        r = FFmpegRenderer()
        # Use a minimal dummy render plan with one scene and fixture image
        plan = RenderPlan(plan_id="p-check", content_id="c-check", job_id="check-job", profile="vertical_short",
                          scenes=[{"scene_id":"s1","duration_sec":2.0,"asset_path":"","audio_path":""}])
        out_path = "autopilot/artifacts/jobs/check-job/render/test_checksum.mp4"
        # The fixture image exists; this should produce a file with checksum
        result = r.render(plan, out_path)
        self.assertIsNotNone(result.checksum_sha256)
        self.assertTrue(len(result.checksum_sha256) == 64)
        self.assertTrue(Path(out_path).exists())

    def test_multi_scene_render_execution(self):
        r = FFmpegRenderer()
        plan = RenderPlan(
            plan_id="p-multi",
            content_id="c-multi",
            job_id="multi-job",
            profile="vertical_short",
            scenes=[
                {"scene_id": "s1", "duration_sec": 2.0, "asset_path": "", "audio_path": ""},
                {"scene_id": "s2", "duration_sec": 2.0, "asset_path": "", "audio_path": ""},
                {"scene_id": "s3", "duration_sec": 2.0, "asset_path": "", "audio_path": ""},
            ],
        )
        out_path = "autopilot/artifacts/jobs/multi-job/render/final.mp4"
        result = r.render(plan, out_path)
        self.assertEqual(result.duration_sec, 6.0)
        self.assertIsNotNone(result.checksum_sha256)
        self.assertTrue(Path(out_path).exists())

    def test_multi_scene_failure_raises_runtime_error(self):
        from unittest.mock import patch, MagicMock
        r = FFmpegRenderer()
        plan = RenderPlan(
            plan_id="p-fail",
            content_id="c-fail",
            job_id="fail-job",
            profile="vertical_short",
            scenes=[
                {"scene_id": "s1", "duration_sec": 3.0, "asset_path": "", "audio_path": ""},
                {"scene_id": "s2", "duration_sec": 3.0, "asset_path": "", "audio_path": ""},
            ],
        )
        out_path = "autopilot/artifacts/jobs/fail-job/render/final.mp4"
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stderr = "FFmpeg error"
        with patch("subprocess.run", return_value=mock_proc):
            with self.assertRaises(RuntimeError) as ctx:
                r.render(plan, out_path)
            self.assertIn("render failed", str(ctx.exception))

if __name__ == "__main__":
    unittest.main()

