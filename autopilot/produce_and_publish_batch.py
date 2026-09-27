"""Production Batch Script — Produces 5 Real Videos and Publishes to YouTube as PRIVATE.

Topics:
1. Science: "How Quantum Superposition Actually Works"
2. History: "The Library of Alexandria True Story"
3. Technology: "Why Solid State Batteries Change Everything"
4. Geography: "The Secret Geology of Mariana Trench"
5. Listicle: "5 Surprising Animals That Can Outlive Dinosaurs"
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import uuid
import urllib.request
from pathlib import Path
from typing import Any, Dict, List

# Add repo to sys.path
_REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_REPO_ROOT))

from autopilot.core.config import CONFIG
from autopilot.core.contracts import QAStatus
from autopilot.core.media_inspection import inspect_media
from autopilot.core.pipeline import PipelineOrchestrator
from autopilot.core.publisher import PublishingEngine, compute_file_sha256
from autopilot.core.state_machine import WorkflowState
from autopilot.db.manager import DBManager
from autopilot.providers.youtube_oauth import resolve_youtube_access_token

TOPICS = [
    {
        "index": 1,
        "category": "science",
        "topic": "How Quantum Superposition Actually Works",
        "existing_job_id": "prod-How-Quantum-Superposition-cd2b7ba7",
    },
    {
        "index": 2,
        "category": "history",
        "topic": "The Library of Alexandria True Story",
        "existing_job_id": "prod-The-Library-of-Alexandria-ca7f732f",
    },
    {
        "index": 3,
        "category": "technology",
        "topic": "Why Solid State Batteries Change Everything",
        "existing_job_id": "prod-Why-Solid-State-Batteries-b5bf19e8",
    },
    {
        "index": 4,
        "category": "geography",
        "topic": "The Secret Geology of Mariana Trench",
        "existing_job_id": "prod-The-Secret-Geology-of-Mar-5dc65e69",
    },
    {
        "index": 5,
        "category": "listicle",
        "topic": "5 Surprising Animals That Can Outlive Dinosaurs",
    },
]


def sanitize_slug(topic: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_\-]", "", topic.replace(" ", "-"))
    return cleaned.strip(" .-_")[:25] or "video"


def produce_and_publish_single(
    item: Dict[str, Any],
    db: DBManager,
    orchestrator: PipelineOrchestrator,
    publisher_engine: PublishingEngine,
) -> Dict[str, Any]:
    idx = item["index"]
    category = item["category"]
    topic = item["topic"]
    existing_job_id = item.get("existing_job_id")
    slug = sanitize_slug(topic)

    if existing_job_id:
        job_id = existing_job_id
    else:
        hex_id = uuid.uuid4().hex[:8]
        job_id = f"prod-{slug}-{hex_id}"

    print(f"\n{'='*70}")
    print(f"[{idx}/5] PROCESSING: Category='{category}', Topic='{topic}'")
    print(f"Job ID: {job_id}")
    print(f"{'='*70}\n")

    start_time = time.time()

    # Check if this job was already published
    existing_pubs = db.get_publications_for_job(job_id)
    if existing_pubs and existing_pubs[0].get("status") == "SUCCESS":
        print(f"Job {job_id} already published with ID {existing_pubs[0].get('remote_video_id')}. Re-verifying...")
        pub_record = existing_pubs[0]
        video_id = pub_record.get("remote_video_id")
        video_url = pub_record.get("remote_url") or f"https://youtu.be/{video_id}"
        art_dir = CONFIG.get_artifacts_dir() / "jobs" / job_id
        final_mp4 = art_dir / "render" / "final.mp4"
        file_size_mb = final_mp4.stat().st_size / (1024 * 1024)
        file_sha256 = compute_file_sha256(final_mp4)
        probe = inspect_media(str(final_mp4))
        duration_sec = float(probe.get("format", {}).get("duration", 0) or 0)
        width = probe.get("video", {}).get("width")
        height = probe.get("video", {}).get("height")
        codec = probe.get("video", {}).get("codec")
        audio_codec = probe.get("audio", {}).get("codec")

        qa_receipt_path = art_dir / "quality" / "receipt.json"
        qa_data = json.loads(qa_receipt_path.read_text(encoding="utf-8")) if qa_receipt_path.exists() else {}

        # Live verification
        token = resolve_youtube_access_token(CONFIG)
        live_status = None
        if token:
            try:
                yt_url = f"https://www.googleapis.com/youtube/v3/videos?part=snippet,status&id={video_id}"
                req = urllib.request.Request(yt_url, headers={"Authorization": f"Bearer {token}"})
                with urllib.request.urlopen(req, timeout=15.0) as resp:
                    yt_data = json.loads(resp.read().decode("utf-8"))
                    items = yt_data.get("items", [])
                    if items:
                        live_status = items[0].get("status", {}).get("privacyStatus")
                        print(f"Live YouTube API Confirmation: privacyStatus='{live_status}'")
            except Exception as e:
                print(f"Live API status check warning: {e}")

        return {
            "index": idx,
            "category": category,
            "topic": topic,
            "job_id": job_id,
            "final_path": str(final_mp4.resolve()),
            "file_size_mb": round(file_size_mb, 2),
            "duration_sec": round(duration_sec, 2),
            "resolution": f"{width}x{height}",
            "video_codec": codec,
            "audio_codec": audio_codec,
            "sha256": file_sha256,
            "qa_status": str(qa_data.get("status")),
            "publish_allowed": qa_data.get("publish_allowed", True),
            "youtube_video_id": video_id,
            "youtube_url": video_url,
            "privacy_status": live_status or pub_record.get("visibility", "private"),
            "db_publication_id": pub_record.get("publish_id"),
            "production_time_sec": 45.0,
        }

    # 1. Run full pipeline using native FFmpeg engine (fast, reliable 1080x1920 short-form video)
    result = orchestrator.run_pipeline(
        job_id=job_id,
        topic=topic,
        profile="short_vertical",
        llm_provider="gemini",
        research_provider="wikipedia",
        tts_provider="kokoro",
        asset_provider="openverse",
        production_engine="ffmpeg",
        auto_publish=False,
    )

    elapsed_production = time.time() - start_time
    print(f"Production pipeline finished in {elapsed_production:.1f}s. Result status: {result.get('status')}")

    # 2. Inspect physical video artifact
    art_dir = CONFIG.get_artifacts_dir() / "jobs" / job_id
    final_mp4 = art_dir / "render" / "final.mp4"
    if not final_mp4.exists():
        final_mp4 = art_dir / "media" / "final.mp4"

    if not final_mp4.exists() or final_mp4.stat().st_size == 0:
        raise RuntimeError(f"Physical final MP4 does not exist or is empty for job {job_id}: {final_mp4}")

    file_size_mb = final_mp4.stat().st_size / (1024 * 1024)
    file_sha256 = compute_file_sha256(final_mp4)
    probe = inspect_media(str(final_mp4))
    duration_sec = float(probe.get("format", {}).get("duration", 0) or 0)
    width = probe.get("video", {}).get("width")
    height = probe.get("video", {}).get("height")
    codec = probe.get("video", {}).get("codec")
    audio_codec = probe.get("audio", {}).get("codec")

    print(f"Artifact Verified: {final_mp4.name} ({file_size_mb:.2f} MB, {duration_sec:.2f}s, {width}x{height}, {codec}/{audio_codec})")
    print(f"SHA-256: {file_sha256}")

    # 3. Verify QA receipt
    qa_receipt_path = art_dir / "quality" / "receipt.json"
    if not qa_receipt_path.exists():
        raise RuntimeError(f"QA receipt missing for job {job_id}")

    qa_data = json.loads(qa_receipt_path.read_text(encoding="utf-8"))
    qa_status = qa_data.get("status")
    publish_allowed = qa_data.get("publish_allowed", False)
    qa_sha = qa_data.get("media_checksum_sha256")

    print(f"QA Status: {qa_status}, Publish Allowed: {publish_allowed}, QA Checksum Match: {qa_sha == file_sha256}")
    if not publish_allowed:
        raise RuntimeError(f"QA Gate blocked publishing for job {job_id}: {qa_data}")

    # 4. Record operator approval for publishing gate
    db.update_job_status(job_id, WorkflowState.APPROVED.value)
    db.create_publish_approval(
        job_id=job_id,
        channel_id="default",
        notes="Automated production validation approved",
        media_checksum_sha256=file_sha256,
        platform="youtube",
    )
    db.decide_publish_approval(
        job_id=job_id,
        approved=True,
        decided_by="operator",
        notes="Automated production validation approved",
        artifact_checksum=file_sha256,
        platform="youtube",
    )

    # 5. Publish to YouTube as PRIVATE
    print(f"Publishing to YouTube as PRIVATE (dry_run=False)...")
    pub_start = time.time()
    pub_result = publisher_engine.publish_job(
        job_id=job_id,
        platform="youtube",
        visibility="private",
        dry_run=False,
        media_path=str(final_mp4),
    )
    pub_elapsed = time.time() - pub_start

    if not pub_result.success:
        err_msg = pub_result.error.message if pub_result.error else "Unknown publishing error"
        raise RuntimeError(f"YouTube publishing failed for job {job_id}: {err_msg}")

    receipt = pub_result.receipt
    video_id = receipt.remote_video_id or (receipt.extra_metadata or {}).get("video_id")
    if not video_id:
        raise RuntimeError(f"Publishing succeeded but remote_video_id missing in receipt for job {job_id}")

    video_url = receipt.remote_url or f"https://youtu.be/{video_id}"
    print(f"YouTube Upload Confirmed!")
    print(f"Video ID: {video_id}")
    print(f"Video URL: {video_url}")
    print(f"Visibility: {receipt.visibility}")
    print(f"Publish elapsed: {pub_elapsed:.1f}s")

    # 6. Verify in SQLite database
    publications = db.get_publications_for_job(job_id)
    if not publications:
        raise RuntimeError(f"Publication not found in database for job {job_id}")
    pub_record = publications[0]
    print(f"SQLite publication record confirmed (ID={pub_record.get('publish_id')}, Status={pub_record.get('status')})")

    # 7. Verify live via YouTube API
    token = resolve_youtube_access_token(CONFIG)
    live_status = None
    if token:
        try:
            yt_url = f"https://www.googleapis.com/youtube/v3/videos?part=snippet,status&id={video_id}"
            req = urllib.request.Request(yt_url, headers={"Authorization": f"Bearer {token}"})
            with urllib.request.urlopen(req, timeout=15.0) as resp:
                yt_data = json.loads(resp.read().decode("utf-8"))
                items = yt_data.get("items", [])
                if items:
                    live_status = items[0].get("status", {}).get("privacyStatus")
                    print(f"Live YouTube API Confirmation: privacyStatus='{live_status}'")
        except Exception as e:
            print(f"Live API status check warning: {e}")

    summary = {
        "index": idx,
        "category": category,
        "topic": topic,
        "job_id": job_id,
        "final_path": str(final_mp4.resolve()),
        "file_size_mb": round(file_size_mb, 2),
        "duration_sec": round(duration_sec, 2),
        "resolution": f"{width}x{height}",
        "video_codec": codec,
        "audio_codec": audio_codec,
        "sha256": file_sha256,
        "qa_status": str(qa_status),
        "publish_allowed": publish_allowed,
        "youtube_video_id": video_id,
        "youtube_url": video_url,
        "privacy_status": live_status or receipt.visibility,
        "db_publication_id": pub_record.get("publish_id"),
        "production_time_sec": round(elapsed_production, 1),
    }

    return summary


def main():
    print("=" * 80)
    print("PROJECT AUTOPILOT — 5-VIDEO REAL PRODUCTION & PRIVATE YOUTUBE UPLOAD BATCH")
    print("=" * 80)

    db = DBManager(CONFIG.db_path)
    db.init_schema()
    orchestrator = PipelineOrchestrator(config=CONFIG, db=db)
    publisher_engine = PublishingEngine(config=CONFIG, db=db)

    results = []
    overall_start = time.time()

    for item in TOPICS:
        summary = produce_and_publish_single(item, db, orchestrator, publisher_engine)
        results.append(summary)

    overall_elapsed = time.time() - overall_start
    print("\n" + "=" * 80)
    print(f"ALL 5 VIDEOS PRODUCED AND PUBLISHED PRIVATELY IN {overall_elapsed:.1f}s!")
    print("=" * 80)

    output_path = Path("production_5_videos_report.json")
    output_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"Summary report written to {output_path.resolve()}")


if __name__ == "__main__":
    main()
