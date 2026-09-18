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
    fixture_img = Path(__file__).parent.parent / "autopilot" / "providers" / "fixture_image.png"
    plan = RenderPlan(plan_id="test-2", content_id="c2", job_id="j2", profile="vertical_short", scenes=[{"scene_id":"s1","duration_sec":3,"asset_path":str(fixture_img),"audio_path":None,"transition_hint":"cut"}])
    out_path = CONFIG.get_artifacts_dir() / "jobs" / "test-render" / "render" / "final.mp4"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    result = renderer.render(plan, str(out_path))
    assert result.output_path == str(out_path)
    assert result.profile == "vertical_short"
    assert Path(out_path).exists()
    assert Path(out_path).stat().st_size > 0


def test_missing_asset_raises_error():
    import pytest
    renderer = FFmpegRenderer(profile="vertical_short")
    plan = RenderPlan(plan_id="test-missing", content_id="c-missing", job_id="j-missing", profile="vertical_short", scenes=[{"scene_id":"s1","duration_sec":3,"asset_path":"non_existent_asset.png"}])
    out_path = CONFIG.get_artifacts_dir() / "jobs" / "test-missing" / "render" / "final.mp4"
    with pytest.raises(RuntimeError) as excinfo:
        renderer.render(plan, str(out_path))
    assert "Missing required visual asset" in str(excinfo.value)


def test_ffmpeg_version():
    runner = FFmpegRenderer()
    v = runner.runner.version()
    assert "ffmpeg" in v.lower() or "unknown" in v
