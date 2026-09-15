"""M4 Renderer tests — offline, deterministic."""
from autopilot.core.contracts import RenderPlan, RenderScene, ContentPackage
from autopilot.core.renderer import FFmpegRenderer
from autopilot.core.config import CONFIG
from pathlib import Path
import os


def test_render_plan_creation():
    plan = RenderPlan(plan_id="test-1", content_id="c1", job_id="j1", profile="vertical_short")
    assert plan.profile == "vertical_short"
    assert plan.target_resolution == "1080x1920"


def test_renderer_output():
    renderer = FFmpegRenderer(profile="vertical_short")
    plan = RenderPlan(plan_id="test-2", content_id="c2", job_id="j2", profile="vertical_short", scenes=[{"scene_id":"s1","duration_sec":3,"asset_path":None,"audio_path":None,"transition_hint":"cut"}])
    out_path = CONFIG.get_artifacts_dir() / "jobs" / "test-render" / "render" / "final.mp4"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    result = renderer.render(plan, str(out_path))
    assert result.output_path == str(out_path)
    assert result.profile == "vertical_short"
    assert Path(out_path).exists()
    assert Path(out_path).stat().st_size > 0


def test_ffmpeg_version():
    runner = FFmpegRenderer()
    v = runner.runner.version()
    assert "ffmpeg" in v.lower() or "unknown" in v
