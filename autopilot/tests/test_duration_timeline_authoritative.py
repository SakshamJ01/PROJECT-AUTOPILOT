import unittest
from autopilot.core.contracts import RenderPlan, QAStatus, QASeverity
from autopilot.core.qa_engine import QAEngine
from autopilot.core.config import Config


class TestDurationTimelineAuthoritative(unittest.TestCase):
    def setUp(self):
        self.config = Config()
        self.qa = QAEngine(self.config)

    def test_mpt_authoritative_duration_avoids_false_warning(self):
        """When authoritative rendered_duration_sec is provided (e.g. 15.33s vs speech 13.14s), QA passes."""
        plan = RenderPlan(
            plan_id="p-mpt",
            content_id="c-mpt",
            job_id="job-mpt",
            profile="vertical_short",
            scenes=[
                {"scene_id": "s1", "duration_sec": 3.072},
                {"scene_id": "s2", "duration_sec": 4.949},
                {"scene_id": "s3", "duration_sec": 5.120},
            ],
            raw_speech_duration_sec=13.141,
            rendered_duration_sec=15.33,
        )
        actual_dur = 15.33
        check, metrics = self.qa.check_duration_timeline(actual_dur=actual_dur, plan=plan)

        self.assertEqual(check.status, QAStatus.PASS)
        self.assertEqual(len(check.findings), 0)
        self.assertEqual(check.measured_value, "15.33s")
        self.assertEqual(check.expected_value, "15.33s")

        # Verify raw speech metric is captured for diagnostics
        raw_metric = next((m for m in metrics if m.name == "raw_speech_duration_seconds"), None)
        self.assertIsNotNone(raw_metric)
        self.assertEqual(raw_metric.value_numeric, 13.14)

    def test_missing_authoritative_duration_uses_fallback(self):
        """When rendered_duration_sec is None, existing fallback to sum of scenes (13.14s) is used, causing warning for 15.33s."""
        plan = RenderPlan(
            plan_id="p-fallback",
            content_id="c-fallback",
            job_id="job-fallback",
            profile="vertical_short",
            scenes=[
                {"scene_id": "s1", "duration_sec": 3.072},
                {"scene_id": "s2", "duration_sec": 4.949},
                {"scene_id": "s3", "duration_sec": 5.120},
            ],
            rendered_duration_sec=None,
        )
        actual_dur = 15.33
        check, metrics = self.qa.check_duration_timeline(actual_dur=actual_dur, plan=plan)

        self.assertEqual(check.status, QAStatus.WARN)
        self.assertTrue(any("minor-drift" in f.finding_id for f in check.findings))
        self.assertEqual(check.expected_value, "13.14s")

    def test_genuine_mismatch_with_authoritative_duration_triggers_block_or_warn(self):
        """When media duration diverges significantly from authoritative rendered_duration_sec, warning/block fires."""
        plan = RenderPlan(
            plan_id="p-mismatch",
            content_id="c-mismatch",
            job_id="job-mismatch",
            profile="vertical_short",
            scenes=[
                {"scene_id": "s1", "duration_sec": 5.0},
                {"scene_id": "s2", "duration_sec": 5.0},
            ],
            raw_speech_duration_sec=10.0,
            rendered_duration_sec=12.0,
        )
        # Actual media is 18.0s (drift 6.0s > 3.0s and 50% > 25%) -> Major drift BLOCK
        actual_dur = 18.0
        check, metrics = self.qa.check_duration_timeline(actual_dur=actual_dur, plan=plan)

        self.assertEqual(check.status, QAStatus.BLOCK)
        self.assertTrue(any("major-drift" in f.finding_id for f in check.findings))

    def test_raw_speech_duration_retained_in_metrics(self):
        """Raw speech duration is preserved in diagnostic metrics regardless of plan structure."""
        plan = RenderPlan(
            plan_id="p-speech",
            content_id="c-speech",
            job_id="job-speech",
            profile="vertical_short",
            scenes=[
                {"scene_id": "s1", "duration_sec": 4.5},
                {"scene_id": "s2", "duration_sec": 5.5},
            ],
            raw_speech_duration_sec=10.0,
            rendered_duration_sec=10.0,
        )
        check, metrics = self.qa.check_duration_timeline(actual_dur=10.0, plan=plan)
        self.assertEqual(check.status, QAStatus.PASS)

        raw_metric = next((m for m in metrics if m.name == "raw_speech_duration_seconds"), None)
        self.assertIsNotNone(raw_metric)
        self.assertEqual(raw_metric.value_numeric, 10.0)
