"""Phase 4.1: custom thumbnail generation + YouTube thumbnails.set upload."""
import json
import urllib.request
from pathlib import Path

from PIL import Image

from autopilot.core.thumbnail import (
    THUMB_HEIGHT,
    THUMB_WIDTH,
    generate_thumbnail,
    select_thumbnail_source,
)
from autopilot.core.contracts import PublishRequest
from autopilot.providers.youtube_publisher import YouTubePublisher


def _scene(scene_id, path, score=None, idx=0):
    return {
        "scene_id": scene_id,
        "asset_path": str(path),
        "semantic_score": score,
        "asset_type": "image",
        "index": idx,
    }


def _make_jpeg(path: Path, color=(20, 40, 90), size=(1920, 1080)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color=color).save(path, format="JPEG")
    return path


def test_selects_highest_clip_scene(tmp_path):
    a = _make_jpeg(tmp_path / "a.jpg", (200, 30, 30))
    b = _make_jpeg(tmp_path / "b.jpg", (30, 30, 200))
    scenes = [_scene("s1", a, 0.11), _scene("s2", b, 0.44), _scene("s3", a, 0.05)]
    chosen = select_thumbnail_source(scenes)
    assert chosen["scene_id"] == "s2"


def test_scene_without_evidence_never_outranks_one_with(tmp_path):
    """An unmeasured scene must not win just by being first."""
    a = _make_jpeg(tmp_path / "a.jpg")
    b = _make_jpeg(tmp_path / "b.jpg", (5, 200, 5))
    scenes = [_scene("s1", a, None), _scene("s2", b, 0.02)]
    assert select_thumbnail_source(scenes)["scene_id"] == "s2"


def test_missing_or_empty_assets_are_not_selected(tmp_path):
    good = _make_jpeg(tmp_path / "good.jpg")
    empty = tmp_path / "empty.jpg"
    empty.write_bytes(b"")
    scenes = [
        _scene("s1", tmp_path / "does_not_exist.jpg", 0.9),
        _scene("s2", empty, 0.9),
        _scene("s3", good, 0.1),
    ]
    assert select_thumbnail_source(scenes)["scene_id"] == "s3"


def test_generate_thumbnail_writes_vertical_jpeg(tmp_path):
    asset = _make_jpeg(tmp_path / "a.jpg")
    out = generate_thumbnail(
        [_scene("s1", asset, 0.5)],
        job_dir=tmp_path / "job",
        hook="Why the Moon has no visible atmosphere",
        channel_name="Deep Facts",
    )
    assert out is not None
    assert out.name == "thumb.jpg"
    assert out.parent.name == "thumbnails"
    with Image.open(out) as im:
        assert im.size == (THUMB_WIDTH, THUMB_HEIGHT)
        assert im.format == "JPEG"
        # Must not be a flat/black frame: the source pixels have to survive.
        colors = im.convert("RGB").getcolors(maxcolors=100000)
        assert colors is None or len(colors) > 1


def test_generate_thumbnail_is_fail_soft(tmp_path):
    """No usable asset -> None, never an exception."""
    assert generate_thumbnail([], job_dir=tmp_path, hook="x") is None
    assert generate_thumbnail(
        [_scene("s1", tmp_path / "nope.jpg", 0.5)], job_dir=tmp_path, hook="x"
    ) is None


def test_generate_thumbnail_handles_blank_hook(tmp_path):
    asset = _make_jpeg(tmp_path / "a.jpg")
    out = generate_thumbnail([_scene("s1", asset, 0.5)], job_dir=tmp_path, hook="")
    assert out is not None and out.exists()


def test_thumbnail_not_in_publish_dir_when_missing(tmp_path):
    from autopilot.core.publisher import PublishingEngine
    from autopilot.core.config import Config

    cfg = Config(artifacts_dir=tmp_path, db_path=tmp_path / "t.db")
    eng = PublishingEngine(config=cfg)
    assert eng._resolve_thumbnail_file("no-such-job") is None


def test_publish_request_carries_thumbnail_path(tmp_path):
    req = PublishRequest(
        publish_request_id="r1",
        job_id="j1",
        content_id="c1",
        title="t",
        media_path=str(tmp_path / "v.mp4"),
        thumbnail_path=str(tmp_path / "thumb.jpg"),
        media_checksum_sha256="a" * 64,
        idempotency_key="k",
    )
    assert req.thumbnail_path.endswith("thumb.jpg")
    assert req.model_dump()["thumbnail_path"].endswith("thumb.jpg")


def _capturing_transport(capture: list):
    def _transport(req):
        capture.append(req)
        if "thumbnails/set" in (req.full_url or ""):
            body = json.dumps({"kind": "youtube#thumbnail", "url": "https://i.ytimg.com/x.jpg"}).encode()
            return 200, {}, body
        raise AssertionError("unexpected call in thumbnail-only test")
    return _transport


def test_apply_custom_thumbnail_posts_correct_protocol(tmp_path):
    thumb = _make_jpeg(tmp_path / "thumb.jpg")
    capture: list = []
    pub = YouTubePublisher(transport=_capturing_transport(capture))
    pub._resolve_access_token = lambda auth_transport=None: "fake-token"

    ev = pub._apply_custom_thumbnail(video_id="abc123", thumbnail_path=str(thumb))

    assert ev["applied"] is True
    assert len(capture) == 1
    req = capture[0]
    assert req.get_method() == "POST"
    assert "uploadType=media" in req.full_url
    assert "videoId=abc123" in req.full_url
    assert req.headers["Content-type"] == "image/jpeg"
    assert req.data == thumb.read_bytes()


def test_apply_custom_thumbnail_never_raises(tmp_path):
    pub = YouTubePublisher(transport=lambda req: (_ for _ in ()).throw(RuntimeError("boom")))
    pub._resolve_access_token = lambda auth_transport=None: "fake-token"

    ev = pub._apply_custom_thumbnail(video_id="x", thumbnail_path=str(_make_jpeg(tmp_path / "t.jpg")))
    assert ev["applied"] is False
    assert "boom" in ev["error"]


def test_apply_custom_thumbnail_rejects_missing_file(tmp_path):
    pub = YouTubePublisher(transport=lambda req: (200, {}, b"{}"))
    ev = pub._apply_custom_thumbnail(video_id="x", thumbnail_path=str(tmp_path / "gone.jpg"))
    assert ev["applied"] is False
    assert "not found" in ev["error"]


def test_apply_custom_thumbnail_requires_token(tmp_path):
    pub = YouTubePublisher(transport=lambda req: (200, {}, b"{}"))
    pub._resolve_access_token = lambda auth_transport=None: None
    ev = pub._apply_custom_thumbnail(video_id="x", thumbnail_path=str(_make_jpeg(tmp_path / "t.jpg")))
    assert ev["applied"] is False
    assert "token" in ev["error"]


def test_apply_custom_thumbnail_rejects_oversized(tmp_path):
    big = tmp_path / "big.jpg"
    big.write_bytes(b"\xff\xd8" + b"0" * (2 * 1024 * 1024 + 10))
    pub = YouTubePublisher(transport=lambda req: (200, {}, b"{}"))
    pub._resolve_access_token = lambda auth_transport=None: "fake-token"
    ev = pub._apply_custom_thumbnail(video_id="x", thumbnail_path=str(big))
    assert ev["applied"] is False
    assert "2MB" in ev["error"]