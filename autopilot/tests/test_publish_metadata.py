"""Regression tests for publishing metadata source resolution and QA gating."""
import hashlib
import json
import pytest
from pathlib import Path

from autopilot.core.config import CONFIG
from autopilot.core.contracts import (
    PublishStatus,
    PublishVisibility,
)
from autopilot.core.state_machine import WorkflowState
from autopilot.core.publisher import PublishingEngine
from autopilot.db.manager import DBManager
from autopilot.providers.mock_publisher import MockPublisher


def setup_job_artifacts(
    tmp_path: Path,
    job_id: str,
    media_content: bytes = b"TEST_VIDEO_BYTES_METADATA_PASS",
    qa_pass: bool = True,
    package_data: dict = None,
    script_data: dict = None,
):
    db = DBManager(tmp_path / "test.db")
    db.init_schema()
    db.create_job(job_id=job_id, topic=f"Topic for {job_id}")

    media_dir = tmp_path / "artifacts" / "jobs" / job_id / "render"
    media_dir.mkdir(parents=True, exist_ok=True)
    media_file = media_dir / "final.mp4"
    media_file.write_bytes(media_content)
    chk = hashlib.sha256(media_content).hexdigest()

    qa_dir = tmp_path / "artifacts" / "jobs" / job_id / "quality"
    qa_dir.mkdir(parents=True, exist_ok=True)
    if qa_pass is not None:
        qa_receipt = {
            "receipt_id": f"rcpt-qa-{job_id}",
            "job_id": job_id,
            "content_id": job_id,
            "status": "PASS" if qa_pass else "FAIL",
            "publish_allowed": qa_pass,
            "media_path": str(media_file),
            "media_checksum_sha256": chk,
        }
        (qa_dir / "receipt.json").write_text(json.dumps(qa_receipt), encoding="utf-8")

    script_dir = tmp_path / "artifacts" / "jobs" / job_id / "script"
    script_dir.mkdir(parents=True, exist_ok=True)

    if script_data:
        (script_dir / "script.json").write_text(json.dumps(script_data), encoding="utf-8")

    if package_data:
        (script_dir / "content_package.json").write_text(json.dumps(package_data), encoding="utf-8")

    return db, media_file, chk


def test_real_production_metadata_reaches_youtube_preview(tmp_path, monkeypatch):
    """Proves real script title, description, and tags reach YouTube metadata without demo placeholder."""
    job_id = "prod-real-meta-001"
    monkeypatch.setattr(CONFIG, "artifacts_dir", tmp_path / "artifacts")

    pkg = {
        "package_version": "v1.0.0",
        "script": {
            "working_title": "The Future of AI Explained",
            "topic": "Future of AI",
            "hook": "AI is advancing faster than ever.",
            "cta": "Subscribe for more insights!",
            "generation_metadata": {
                "description": "An in-depth exploration of artificial intelligence breakthroughs.",
                "tags": ["technology", "artificial-intelligence", "breakthroughs"],
            },
        },
        "publication": {
            "title": "The Future of AI Explained",
            "description": "An in-depth exploration of artificial intelligence breakthroughs.",
            "hashtags": ["technology", "artificial-intelligence", "breakthroughs"],
        },
        "provenance": {
            "provider": "openai_compatible",
        },
    }

    db, media_file, chk = setup_job_artifacts(tmp_path, job_id, package_data=pkg)
    engine = PublishingEngine(CONFIG)
    from autopilot.providers.youtube_publisher import YouTubePublisher
    yt_prov = YouTubePublisher(config=CONFIG)

    result = engine.publish_job(
        job_id=job_id,
        platform="youtube",
        dry_run=True,
        visibility="private",
        provider=yt_prov,
        db_manager=db,
    )

    assert result.success is True
    assert result.status == PublishStatus.DRY_RUN
    assert result.dry_run_preview is not None
    metadata = result.dry_run_preview["metadata"]["snippet"]

    assert metadata["title"] == "The Future of AI Explained"
    assert metadata["description"] == "An in-depth exploration of artificial intelligence breakthroughs."
    assert metadata["tags"] == ["technology", "artificial-intelligence", "breakthroughs"]

    # Invariant: Never emit demo placeholders in real production
    assert "demo" not in metadata["tags"]
    assert "demo" not in metadata["description"].lower()
    assert "deterministic demo content" not in metadata["description"].lower()
    assert result.dry_run_preview["metadata"]["status"]["privacyStatus"] == "private"

    # Also verify exported request.json artifact
    req_file = tmp_path / "artifacts" / "jobs" / job_id / "publish" / "request.json"
    assert req_file.exists()
    req_data = json.loads(req_file.read_text(encoding="utf-8"))
    assert req_data["title"] == "The Future of AI Explained"
    assert req_data["description"] == "An in-depth exploration of artificial intelligence breakthroughs."
    assert req_data["tags"] == ["technology", "artificial-intelligence", "breakthroughs"]


def test_real_production_metadata_overrides_stale_demo_package(tmp_path, monkeypatch):
    """Proves that even if content_package.json has stale demo publication metadata,
    the real generated description and tags from script.generation_metadata are used."""
    job_id = "prod-stale-override-002"
    monkeypatch.setattr(CONFIG, "artifacts_dir", tmp_path / "artifacts")

    pkg = {
        "package_version": "v1.0.0",
        "script": {
            "working_title": "3 Shocking Facts About Space",
            "topic": "3 facts about space",
            "hook": "Space is completely silent.",
            "cta": "Follow for more science!",
            "generation_metadata": {
                "description": "Three verified scientific facts about outer space.",
                "tags": ["space", "science", "facts"],
            },
        },
        "publication": {
            "title": "3 Shocking Facts About Space",
            "description": "Deterministic demo content for topic: 3 facts about space",
            "hashtags": ["autopilot", "demo"],
        },
        "provenance": {
            "provider": "openai_compatible",
        },
    }

    db, media_file, chk = setup_job_artifacts(tmp_path, job_id, package_data=pkg)
    engine = PublishingEngine(CONFIG)
    from autopilot.providers.youtube_publisher import YouTubePublisher
    yt_prov = YouTubePublisher(config=CONFIG)

    result = engine.publish_job(
        job_id=job_id,
        platform="youtube",
        dry_run=True,
        visibility="unlisted",
        provider=yt_prov,
        db_manager=db,
    )

    assert result.success is True
    assert result.status == PublishStatus.DRY_RUN
    metadata = result.dry_run_preview["metadata"]["snippet"]

    assert metadata["title"] == "3 Shocking Facts About Space"
    assert metadata["description"] == "Three verified scientific facts about outer space."
    assert metadata["tags"] == ["space", "science", "facts"]
    assert "demo" not in metadata["tags"]
    assert "deterministic demo content" not in metadata["description"].lower()
    assert result.dry_run_preview["metadata"]["status"]["privacyStatus"] == "unlisted"


def test_mock_fixtures_can_use_test_metadata(tmp_path, monkeypatch):
    """Proves that mock/test jobs can still use demo fixtures without being rejected or sanitized."""
    job_id = "mock-test-job-003"
    monkeypatch.setattr(CONFIG, "artifacts_dir", tmp_path / "artifacts")

    pkg = {
        "package_version": "v1.0.0",
        "script": {
            "working_title": "Mock Video Title",
            "topic": "Test Mock Topic",
        },
        "publication": {
            "title": "Mock Video Title",
            "description": "Deterministic demo content for topic: Test Mock Topic",
            "hashtags": ["autopilot", "demo"],
        },
        "provenance": {
            "provider": "mock_script",
        },
    }

    db, media_file, chk = setup_job_artifacts(tmp_path, job_id, package_data=pkg)
    engine = PublishingEngine(CONFIG)
    from autopilot.providers.youtube_publisher import YouTubePublisher
    yt_prov = YouTubePublisher(config=CONFIG)

    result = engine.publish_job(
        job_id=job_id,
        platform="youtube",
        dry_run=True,
        provider=yt_prov,
        db_manager=db,
    )

    assert result.success is True
    metadata = result.dry_run_preview["metadata"]["snippet"]
    assert metadata["description"] == "Deterministic demo content for topic: Test Mock Topic"
    assert "demo" in metadata["tags"]


def test_real_production_without_gen_meta_uses_hook_cta_and_no_demo_tags(tmp_path, monkeypatch):
    """Proves that when generation_metadata is absent, a real production job falls back to hook+cta
    and strips demo tags."""
    job_id = "prod-hook-cta-004"
    monkeypatch.setattr(CONFIG, "artifacts_dir", tmp_path / "artifacts")

    pkg = {
        "package_version": "v1.0.0",
        "script": {
            "working_title": "Clean Title",
            "topic": "Clean Topic",
            "hook": "Did you know quantum computers can solve complex equations?",
            "cta": "Subscribe for daily tech updates!",
            "generation_metadata": {},
        },
        "publication": {
            "title": "Clean Title",
            "description": "Deterministic demo content for topic: Clean Topic",
            "hashtags": ["autopilot", "demo"],
        },
        "provenance": {
            "provider": "openai_compatible",
        },
    }

    db, media_file, chk = setup_job_artifacts(tmp_path, job_id, package_data=pkg)
    engine = PublishingEngine(CONFIG)
    from autopilot.providers.youtube_publisher import YouTubePublisher
    yt_prov = YouTubePublisher(config=CONFIG)

    result = engine.publish_job(
        job_id=job_id,
        platform="youtube",
        dry_run=True,
        provider=yt_prov,
        db_manager=db,
    )

    assert result.success is True
    metadata = result.dry_run_preview["metadata"]["snippet"]
    assert "Did you know quantum computers" in metadata["description"]
    assert "Subscribe for daily tech updates!" in metadata["description"]
    assert "demo" not in metadata["description"].lower()
    assert metadata["tags"] == ["autopilot", "shorts"]


def test_qa_and_checksum_gates_remain_enforced(tmp_path, monkeypatch):
    """Proves that QA gating and media checksum verification remain strictly enforced."""
    monkeypatch.setattr(CONFIG, "artifacts_dir", tmp_path / "artifacts")
    engine = PublishingEngine(CONFIG)
    mock_prov = MockPublisher()

    # 1. QA Fail blocks publish
    job_id_qa_fail = "prod-qa-fail-005"
    db1, _, _ = setup_job_artifacts(tmp_path, job_id_qa_fail, qa_pass=False)
    res_qa_fail = engine.publish_job(
        job_id=job_id_qa_fail,
        platform="youtube",
        dry_run=True,
        provider=mock_prov,
        db_manager=db1,
    )
    assert res_qa_fail.success is False
    assert res_qa_fail.status == PublishStatus.BLOCKED_QA

    # 2. Missing QA receipt blocks publish
    job_id_missing_qa = "prod-qa-missing-006"
    db2, _, _ = setup_job_artifacts(tmp_path, job_id_missing_qa, qa_pass=None)
    res_qa_missing = engine.publish_job(
        job_id=job_id_missing_qa,
        platform="youtube",
        dry_run=True,
        provider=mock_prov,
        db_manager=db2,
    )
    assert res_qa_missing.success is False
    assert res_qa_missing.status == PublishStatus.BLOCKED_QA

    # 3. Checksum mismatch blocks publish
    job_id_chk_mismatch = "prod-chk-mismatch-007"
    db3, media_file3, _ = setup_job_artifacts(tmp_path, job_id_chk_mismatch, qa_pass=True)
    # Corrupt media file after QA receipt was written
    media_file3.write_bytes(b"CORRUPTED_VIDEO_BYTES_AFTER_QA")
    res_chk = engine.publish_job(
        job_id=job_id_chk_mismatch,
        platform="youtube",
        dry_run=True,
        provider=mock_prov,
        db_manager=db3,
    )
    assert res_chk.success is False
    assert res_chk.status == PublishStatus.BLOCKED_QA
    assert "checksum" in res_chk.error.message.lower()
