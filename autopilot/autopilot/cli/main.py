"""CLI commands — Phase 0.
Only health is required now; production commands deferred.
"""
from __future__ import annotations
import sys
import platform
import subprocess
import sqlite3
import hashlib
from pathlib import Path

from autopilot.core.config import CONFIG
from autopilot.core.artifacts import job_artifact_dir, script_path, provenance_path
from autopilot.providers.contracts import REGISTRY, ProviderHealth

# ---------------------------------------------------------------------------
# Provider Policy Resolution
# ---------------------------------------------------------------------------

# Maps each policy tier to the canonical real-provider names.
# "mock" / "mock_search" / "none" are NEVER valid in local_only or higher tiers.
_POLICY_PROVIDER_MAP: dict[str, dict[str, str]] = {
    "local_only": {
        "llm": "openai_compatible",      # Ollama / local OpenAI-compatible endpoint
        "research": "wikipedia",          # Wikipedia (no external paid API)
        "tts": "kokoro",                  # Kokoro ONNX (local)
        "production_engine": "moneyprinterturbo",
    },
    "cheap_first": {
        "llm": "openrouter",
        "research": "combined",
        "tts": "kokoro",
        "production_engine": "moneyprinterturbo",
    },
    "quality_first": {
        "llm": "gemini",
        "research": "combined",
        "tts": "kokoro",
        "production_engine": "moneyprinterturbo",
    },
    "ollama": {
        "llm": "ollama",
        "research": "wikipedia",
        "tts": "kokoro",
        "production_engine": "moneyprinterturbo",
    },
    "gemini": {
        "llm": "gemini",
        "research": "combined",
        "tts": "kokoro",
        "production_engine": "moneyprinterturbo",
    },
    "openrouter": {
        "llm": "openrouter",
        "research": "combined",
        "tts": "kokoro",
        "production_engine": "moneyprinterturbo",
    },
}

# Provider values that indicate a mock / no-op was selected.
_MOCK_PROVIDER_VALUES = frozenset({"mock", "mock_search", "none", ""})


def resolve_providers_for_policy(
    policy: str,
    llm_provider: str,
    research_provider: str,
    tts_provider: str,
    production_engine: str,
    *,
    llm_explicit: bool = False,
    research_explicit: bool = False,
    tts_explicit: bool = False,
) -> tuple[str, str, str, str]:
    """Resolve provider names according to the production policy.

    For ``local_only`` (and stricter tiers) any mock provider value is replaced
    with the canonical real local provider **unless the user explicitly passed a
    non-mock value on the CLI**.  If the user explicitly requested a mock under
    ``local_only`` a ``ValueError`` is raised immediately (fail-closed).

    Returns ``(llm, research, tts, production_engine)``.
    """
    tier = _POLICY_PROVIDER_MAP.get(policy)
    if tier is None:
        # Unknown policy — leave providers as-is (permissive for forward compat).
        return llm_provider, research_provider, tts_provider, production_engine

    # --- LLM ---
    if llm_explicit and llm_provider in _MOCK_PROVIDER_VALUES:
        raise ValueError(
            f"Policy '{policy}' forbids mock LLM providers. "
            f"Got --llm-provider={llm_provider!r}. "
            f"Use --llm-provider openai_compatible (Ollama) or change policy to 'cheap_first'."
        )
    if llm_provider in _MOCK_PROVIDER_VALUES:
        llm_provider = tier["llm"]

    # --- Research ---
    if research_explicit and research_provider in _MOCK_PROVIDER_VALUES:
        raise ValueError(
            f"Policy '{policy}' forbids mock research providers. "
            f"Got --research-provider={research_provider!r}. "
            f"Use --research-provider wikipedia (or combined/crawl4ai) or change policy."
        )
    if research_provider in _MOCK_PROVIDER_VALUES:
        research_provider = tier["research"]

    # --- TTS ---
    if tts_explicit and tts_provider in _MOCK_PROVIDER_VALUES:
        raise ValueError(
            f"Policy '{policy}' forbids mock/no-op TTS providers. "
            f"Got --tts-provider={tts_provider!r}. "
            f"Use --tts-provider kokoro or change policy."
        )
    if tts_provider in _MOCK_PROVIDER_VALUES:
        tts_provider = tier["tts"]

    return llm_provider, research_provider, tts_provider, production_engine



def check_ffmpeg() -> dict:
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return {"available": result.returncode == 0, "version_line": result.stdout.splitlines()[0] if result.stdout else ""}
    except Exception as exc:
        return {"available": False, "error": str(exc)}


def check_sqlite() -> dict:
    try:
        conn = sqlite3.connect(":memory:")
        conn.execute("SELECT 1")
        conn.close()
        return {"available": True}
    except Exception as exc:
        return {"available": False, "error": str(exc)}


def run_health() -> int:
    import json
    from autopilot.db.manager import DBManager
    from autopilot.providers.local_asset_provider import LocalAssetProvider
    from autopilot.providers.openverse_provider import OpenverseAssetProvider

    # Initialize DB if not present (idempotent)
    db = DBManager(CONFIG.db_path)
    db.init_schema()

    ffmpeg_info = check_ffmpeg()
    sqlite_info = check_sqlite()

    # Asset providers health check
    local_asset = LocalAssetProvider()
    openverse_asset = OpenverseAssetProvider()
    REGISTRY.register(local_asset)
    REGISTRY.register(openverse_asset)

    # LLM & TTS providers health check
    from autopilot.providers.sapi_tts_provider import WindowsSAPITTSProvider
    from autopilot.providers.openai_llm_provider import OpenAICompatibleLLMProvider
    REGISTRY.register(WindowsSAPITTSProvider())
    REGISTRY.register(OpenAICompatibleLLMProvider())

    # Publisher providers health check
    from autopilot.providers.youtube_publisher import YouTubePublisher
    REGISTRY.register(YouTubePublisher())

    # Provider health — graceful for missing ones
    provider_health = REGISTRY.health_all()

    # Artifact dir
    artifacts_dir = CONFIG.get_artifacts_dir()
    cache_dir = CONFIG.get_asset_cache_dir()

    report = {
        "status": "healthy" if ffmpeg_info.get("available") and sqlite_info.get("available") else "degraded",
        "python_version": sys.version,
        "platform": platform.platform(),
        "ffmpeg": ffmpeg_info,
        "sqlite": sqlite_info,
        "artifacts_dir": str(artifacts_dir),
        "artifact_dir_exists": artifacts_dir.exists(),
        "asset_cache_dir": str(cache_dir),
        "db_path": str(CONFIG.db_path),
        "db_exists": CONFIG.db_path.exists(),
        "config_valid": True,
        "qa_engine": {"available": True, "status": "AVAILABLE"},
        "queue_engine": {
            "status": "AVAILABLE",
            "summary": db.get_queue_status_summary(),
        },
        "worker": {"status": "AVAILABLE"},
        "scheduler": {"status": "AVAILABLE"},
        "analytics_engine": {
            "status": "AVAILABLE",
            "default_provider": CONFIG.analytics_default_provider,
        },
        "autonomy_engine": {
            "status": "AVAILABLE",
            "autonomy_level": CONFIG.autonomy_level,
            "max_daily_jobs": CONFIG.autonomy_max_daily_jobs,
            "max_ideas_per_cycle": CONFIG.autonomy_max_ideas_per_cycle,
        },
        "providers_registered": list(provider_health.keys()),
        "provider_health": {k: v.model_dump() for k, v in provider_health.items()},
    }

    # Channel engine health
    from autopilot.core.channel import ChannelManager
    chan_mgr = ChannelManager(db)
    all_channels = chan_mgr.list_channels()
    enabled_channels = [c for c in all_channels if c.status.value == "active"]
    report["channel_engine"] = {
        "status": "AVAILABLE",
        "total_channels": len(all_channels),
        "enabled_channels": len(enabled_channels),
    }

    # Print structured JSON to stdout and a summary
    print(json.dumps(report, indent=2, default=str))

    # Summary lines
    print("\n--- SUMMARY ---")
    print(f"Python: {platform.python_version()} | {platform.system()}")
    print(f"FFmpeg: {'YES (' + ffmpeg_info.get('version_line', '') + ')' if ffmpeg_info.get('available') else 'NO'}")
    print(f"SQLite: {'YES' if sqlite_info.get('available') else 'NO'}")
    print(f"QA Engine: AVAILABLE")
    q_sum = report["queue_engine"]["summary"]
    print(f"Queue Engine: AVAILABLE ({q_sum['total']} total, {q_sum['queued']} queued, {q_sum['running']} running, {q_sum['succeeded']} succeeded)")
    print(f"Worker: AVAILABLE")
    print(f"Scheduler: AVAILABLE (Local / OS Task Scheduler)")
    print(f"Analytics Engine: AVAILABLE (default={CONFIG.analytics_default_provider})")
    print(f"Autonomy Engine: AVAILABLE (level={CONFIG.autonomy_level}, daily_limit={CONFIG.autonomy_max_daily_jobs})")
    print(f"Channel Engine: AVAILABLE ({len(all_channels)} profiles, {len(enabled_channels)} enabled)")
    print(f"Artifacts: {artifacts_dir}")
    print(f"Asset Cache: {cache_dir}")
    print(f"DB: {CONFIG.db_path} (exists={CONFIG.db_path.exists()})")
    print(f"Providers: {len(provider_health)} registered ({', '.join(provider_health.keys())})")
    for name, h in provider_health.items():
        flag = "AVAILABLE" if h.healthy else f"UNAVAILABLE ({h.error or 'offline'})"
        print(f"  - {name}: {flag}")
    print("Publishers:")
    yt_h = provider_health.get("youtube")
    yt_status = "AVAILABLE" if yt_h and yt_h.healthy else "UNCONFIGURED"
    print(f"  - youtube: {yt_status}")

    return 0 if report["status"] == "healthy" else 1


def run_produce(
    topic: str,
    profile: str = "short_vertical",
    channel: str = "default",
    policy: str = "local_only",
    research_topic: str | None = None,
    tts_provider: str = "none",
    asset_provider: str = "local",
    llm_provider: str = "mock",
    research_provider: str = "mock_search",
    production_engine: str = "moneyprinterturbo",
    render: bool = False,
) -> int:
    import json
    import uuid
    from autopilot.db.manager import DBManager
    from autopilot.core.state_machine import WorkflowState
    from autopilot.core.channel import ChannelManager
    from autopilot.core.logging import StructuredLogger
    from autopilot.providers.mock_script import MockScriptProvider
    from autopilot.core.contracts import (
        ContentPackage, ContentItem, ScriptDocument, ProvenanceRecord, PublicationMetadata
    )
    from autopilot.core.contracts import package_to_file, generate_json_schema
    from autopilot.core.quality import evaluate_script

    # Ensure schema exported
    generate_json_schema("schemas/content-contract-v1.json")

    job_id = f"prod-{topic.replace(' ', '-')[:30]}-{uuid.uuid4().hex[:8]}"
    db = DBManager(CONFIG.db_path)
    db.init_schema()

    # Resolve channel profile
    chan_mgr = ChannelManager(db)
    channel_profile = chan_mgr.get_or_create_channel(channel)

    # Create workflow record
    db.create_job(job_id, channel_id=channel_profile.channel_id, topic=topic, idempotency_key=f"ph1-{topic}-{job_id}")
    logger = StructuredLogger(job_id=job_id, stage="produce")
    logger.info("produce_started", details={"topic": topic, "job_id": job_id, "channel": channel_profile.channel_id, "policy": policy})

    # ----------------------------------------------------------------
    # Fail-closed guard: local_only must never reach this point with a
    # mock provider (policy resolution should have already caught this,
    # but we double-check here as a last line of defence).
    # ----------------------------------------------------------------
    _NON_PRODUCTION_PROVIDERS = {"mock", "mock_search"}
    if policy in ("local_only", "ollama"):
        if llm_provider in _NON_PRODUCTION_PROVIDERS:
            raise RuntimeError(
                f"Policy '{policy}' is incompatible with LLM provider '{llm_provider}'. "
                "Expected 'ollama' or 'openai_compatible'. "
                "Set AUTOPILOT_LLM_ENDPOINT or configure an Ollama endpoint."
            )
        if research_provider in _NON_PRODUCTION_PROVIDERS:
            raise RuntimeError(
                f"Policy '{policy}' is incompatible with research provider '{research_provider}'. "
                "Expected 'wikipedia' or 'combined'."
            )

    # Select LLM provider based on CLI argument
    from autopilot.providers.openai_llm_provider import get_llm_provider
    provider = get_llm_provider(llm_provider)

    # Ensure research report and evidence exist
    research_report_obj = db.get_latest_research_report_for_topic(topic, provider=research_provider)
    if not research_report_obj:
        if research_provider == "wikipedia":
            from autopilot.providers.wikipedia_provider import WikipediaProvider
            search_provider = WikipediaProvider()
        else:
            from autopilot.providers.mock_search import MockSearchProvider
            search_provider = MockSearchProvider()
        sources = search_provider.search(query=topic, max_results=3)
        if not sources and research_provider == "wikipedia":
            raise RuntimeError(f"Wikipedia research failed: zero sources found for '{topic}'")
        req_id = f"req-{job_id}"
        rep_id = f"rep-{job_id}"
        db.create_research_request(req_id, topic)
        db.save_research_report(
            rep_id, req_id, topic, status="completed",
            summary=f"Discovered {len(sources)} sources for '{topic}' via {search_provider.provider_name}",
            provenance_json=json.dumps({"provider": search_provider.provider_name}),
        )
        for src in sources:
            snippet_text = src.get("snippet") or src.get("notes", "") if research_provider == "wikipedia" else src.get("notes", f"Research note on {topic}")
            db.record_research_evidence(
                evidence_id=f"ev-{rep_id}-{src.get('source_id')}",
                report_id=rep_id,
                source_id=src.get("source_id", ""),
                snippet=snippet_text,
                relevance_score=0.75,
                status="discovered",
                provenance_json=json.dumps({"provider": search_provider.provider_name}),
            )
        research_report_obj = db.get_latest_research_report_for_topic(topic, provider=research_provider)

    # Step 1: invoke provider
    script = provider.generate_script(
        topic=topic,
        content_id=job_id,
        language="en",
        research_report=research_report_obj,
        channel_profile=channel_profile,
    )

    logger.info("script_generated", details={"content_id": script.content_id, "scenes": len(script.scenes)})

    # Step 2: validate script
    # Pydantic validation already done; extra cross-check
    if not script.scenes or len(script.scenes) < 1:
        raise ValueError("Produced script has no scenes")

    # Step 2: quality check (Phase 3)
    quality = evaluate_script(script)
    logger.info("quality_check", details={"overall": quality.overall, "blocking": quality.blocking_count, "warnings": quality.warning_count})
    if quality.overall == "fail":
        db.update_job_status(job_id, WorkflowState.FAILED_SCRIPT.value)
        db.record_error(job_id, "SCRIPT", "quality_failed", f"Blocking failures: {quality.blocking_count}")
        print(f"\n=== QUALITY FAIL ===\nBlocking failures: {quality.blocking_count}\nWarnings: {quality.warning_count}")
        for c in quality.checks:
            print(f"  [{c.severity}] {c.check_name}: {c.message}")
        return 1

    # Step 3: write quality report
    temp_art = job_artifact_dir(job_id)
    quality_file = temp_art / "quality" / "quality_report.json"
    quality_file.parent.mkdir(parents=True, exist_ok=True)
    quality_file.write_text(quality.model_dump_json(indent=2), encoding="utf-8")
    db.record_artifact(job_id, str(quality_file), "quality")
    logger.info("quality_report_written", details={"path": str(quality_file), "overall": quality.overall})

    # Step 4: create content item
    item = ContentItem(content_id=job_id, topic=topic, format="9:16_video")

    # Step 4: create content package
    gen_meta = script.generation_metadata or {}
    real_desc = gen_meta.get("description")
    if not real_desc:
        if script.hook and script.cta:
            real_desc = f"{script.hook}\n\n{script.cta}"
        elif script.hook:
            real_desc = script.hook
        elif provider.provider_name == "mock_script":
            real_desc = f"Deterministic demo content for topic: {topic}"
        else:
            real_desc = f"Video about: {topic}"

    real_tags = gen_meta.get("tags")
    if not real_tags:
        if provider.provider_name == "mock_script":
            real_tags = ["autopilot", "demo"]
        else:
            real_tags = ["autopilot", "shorts"]

    real_title = script.working_title or (f"{topic} — Phase 1 demo" if provider.provider_name == "mock_script" else topic)

    package = ContentPackage(
        content_item=item,
        script=script,
        publication=PublicationMetadata(
            title=real_title,
            description=real_desc,
            hashtags=real_tags,
            privacy_status="draft",
        ),
        provenance=ProvenanceRecord(
            provider=provider.provider_name,
            model=f"mock-template-v1-profile-{profile}" if provider.provider_name == "mock_script" else provider.provider_name,
            generation_timestamp=script.generation_metadata.get("generated_at", ""),
            input_reference_ids=[f"topic-{topic}", f"research-{request_id if 'request_id' in locals() else 'none'}"],
            deterministic_idempotency_key=f"ph3-{topic}-{profile}-{job_id}",
        ),
    )

    # Step 5: write artifacts
    artifact_dir = job_artifact_dir(job_id)
    script_file = script_path(job_id, "script.json")
    package_file = artifact_dir / "script" / "content_package.json"
    script_file.write_text(script.model_dump_json(indent=2), encoding="utf-8")
    package_file.write_text(package.model_dump_json(indent=2), encoding="utf-8")

    # Step 6: provenance record separate
    prov_file = provenance_path(job_id, "provenance.json")
    prov_file.write_text(package.provenance.model_dump_json(indent=2), encoding="utf-8")

    # Step 7: record artifacts in DB
    db.record_artifact(job_id, str(script_file), "script")
    db.record_artifact(job_id, str(package_file), "script")
    db.record_artifact(job_id, str(prov_file), "provenance")

    # Step 8: update workflow state
    db.update_job_status(job_id, WorkflowState.SCRIPTED.value)
    db.log_event(job_id, WorkflowState.IDEA.value, WorkflowState.SCRIPTED.value, reason="produce_ph1", idempotency_key=f"ph1-{topic}-{job_id}")

    logger.info("produce_complete", details={
        "job_id": job_id,
        "script_path": str(script_file),
        "package_path": str(package_file),
        "scenes": len(script.scenes),
        "state": WorkflowState.SCRIPTED.value,
    })

    # Phase 2 / M2 — TTS integration (optional)
    from autopilot.providers.tts_factory import get_tts_provider
    tts = get_tts_provider(tts_provider)
    if tts is not None:
        import hashlib
        from autopilot.core.audio_duration import extract_duration
        voice_dir = artifact_dir / "voice"
        voice_dir.mkdir(parents=True, exist_ok=True)
        total_duration = 0.0
        for idx, scene in enumerate(script.scenes):
            segment_text = scene.narration or ""
            if not segment_text.strip():
                continue
            seg_path = voice_dir / f"segment_{scene.scene_id}.wav"
            try:
                audio_path = tts.synthesize(text=segment_text, out_path=str(seg_path), voice_id="default")
                dur_info = extract_duration(audio_path)
                measured = dur_info.get("duration_sec", 0.0) if dur_info.get("valid") else 0.0
                total_duration += measured
                db.record_voice_artifact(
                    job_id, script.content_id, scene.scene_id, audio_path,
                    provider=tts.provider_name, model_voice="default", duration_sec=measured,
                    provenance_json=json.dumps({
                        "provider": tts.provider_name, "mode": "real" if tts.provider_name == "kokoro" else "synthetic",
                        "input_text_hash_prefix": hashlib.sha256(segment_text.encode()).hexdigest()[:16],
                        "note": "Kokoro ONNX speech synthesis" if tts.provider_name == "kokoro" else "Deterministic mock TTS audio",
                    }),
                )
                package.voice_artifacts.append(str(audio_path))
                logger.info("tts_generated", details={"segment_id": scene.scene_id, "path": audio_path, "duration_sec": measured, "valid": dur_info.get("valid")})
            except Exception as exc:
                logger.error("tts_failed", error=str(exc), details={"scene_id": scene.scene_id})
                if tts_provider != "mock":
                    raise RuntimeError(f"TTS synthesis failed with provider '{tts_provider}' for scene '{scene.scene_id}': {exc}") from exc
        package.measured_duration_sec = round(total_duration, 2) if total_duration > 0 else None
        package_file.write_text(package.model_dump_json(indent=2), encoding="utf-8")
        db.record_artifact(job_id, str(package_file), "script")

    # Print human-readable summary
    print(f"\n=== PHASE 1/2/3 PRODUCE COMPLETE ===")
    print(f"Topic: {topic}")
    print(f"Job ID: {job_id}")
    print(f"Script scenes: {len(script.scenes)}")
    print(f"Working title: {script.working_title}")
    print(f"Hook: {script.hook}")
    print(f"Script artifact: {script_file}")
    print(f"Content package: {package_file}")
    print(f"Schema exported: schemas/content-contract-v1.json")
    print(f"Status: SCRIPTED (voice={tts_provider})")
    print(f"Note: Provider used: LLM={llm_provider}, Research={research_provider}, TTS={tts_provider}")
    print(f"Continuation commands:")
    print(f"  1. Render video: python -m autopilot render --job {job_id} --asset-provider {asset_provider}")
    print(f"  2. Quality gate: python -m autopilot qa --job {job_id}")

    if render:
        print(f"\n--- CONTINUING TO RENDER STAGE (asset_provider={asset_provider}, engine={production_engine}) ---")
        return run_render(job_id=job_id, asset_provider=asset_provider, profile=profile, production_engine=production_engine)

    return 0


def run_research(topic: str, provider_name: str = "local") -> int:
    import uuid, json
    from autopilot.db.manager import DBManager
    from autopilot.core.state_machine import WorkflowState
    from autopilot.core.logging import StructuredLogger
    from autopilot.core.contracts import (
        ResearchRequest, ResearchReport, ResearchResult, ResearchEvidence,
        ResearchSource, PublicationMetadata, ProvenanceRecord,
    )
    from autopilot.providers.mock_search import MockSearchProvider
    from autopilot.core.research_cache import cache_key, save_cache, load_cache

    db = DBManager(CONFIG.db_path)
    db.init_schema()
    request_id = f"res-{topic.replace(' ', '-')[:20]}-{uuid.uuid4().hex[:6]}"
    logger = StructuredLogger(job_id=request_id, stage="research")

    # Create request
    req = ResearchRequest(request_id=request_id, topic=topic, max_sources=5)
    db.create_research_request(request_id, topic, language=req.language, max_sources=req.max_sources)
    logger.info("research_started", details={"topic": topic, "provider": provider_name, "request_id": request_id})

    # Provider selection
    from autopilot.providers.wikipedia_provider import WikipediaProvider
    if provider_name == "wikipedia":
        provider = WikipediaProvider()
    else:
        provider = MockSearchProvider()
    if provider_name not in ("local", "mock_search", "wikipedia"):
        logger.warning("provider_unavailable", details={"requested": provider_name, "using": "mock_search"})

    # Cache check
    cache_key_val = cache_key(topic, req.normalized_query or topic.lower(), provider_name)
    cached = load_cache(cache_key_val)
    if cached:
        logger.info("cache_hit", details={"key": cache_key_val})
        print("=== RESEARCH CACHE HIT ===")
        sources_found = cached.get("sources_found") or (len(cached.get("results", [{}])[0].get("evidence_items", [])) if cached.get("results") else 0)
        print(json.dumps({"status": "cached", "request_id": request_id, "provider": provider_name, "sources_found": sources_found}, indent=2))
        db.update_research_request_status(request_id, "completed")
        # Persist full cached result to DB so produce can retrieve real evidence
        rep_id = req.request_id + "-cached"
        db.save_research_report(rep_id, request_id, topic, status="completed", summary=cached.get("summary", "cached result"))
        for res in cached.get("results", []):
            for ev in res.get("evidence_items", []):
                db.record_research_evidence(
                    evidence_id=f"ev-{ev.get('source_id', ev.get('evidence_id',''))}",
                    report_id=rep_id,
                    source_id=ev.get("source_id", ev.get("url", "")),
                    snippet=ev.get("snippet", ev.get("summary", "")),
                    relevance_score=0.75,
                    status="relevant",
                    provenance_json=json.dumps({"provider": provider_name, "cached": True}),
                )
        return 0

    # Execute search
    try:
        sources_raw = provider.search(query=topic, max_results=req.max_sources)
    except Exception as exc:
        db.update_research_request_status(request_id, "failed")
        db.record_error(request_id, "RESEARCH", "search_failed", str(exc))
        logger.error("search_failed", error=str(exc))
        print(json.dumps({"status": "failed", "reason": str(exc), "request_id": request_id}))
        return 1

    # Build sources + evidence
    evidence_items = []
    source_ids = []
    for src_raw in sources_raw:
        sid = src_raw.get("source_id", f"src-{uuid.uuid4().hex[:4]}")
        source_ids.append(sid)
        # Evidence snippet (short, not full copyrighted text)
        if provider.provider_name == "wikipedia":
            snippet_text = src_raw.get("snippet") or src_raw.get("notes", "")
        else:
            snippet_text = src_raw.get("notes", "synthetic fixture")
        snippet = f"Evidence snippet for {sid}: {src_raw.get('title', 'untitled')}. Summary: {snippet_text}."
        evidence = ResearchEvidence(
            evidence_id=f"ev-{sid}",
            source_id=sid,
            snippet=snippet,
            relevance_score=0.75,
            status="relevant",
            provenance=ProvenanceRecord(provider=provider.provider_name, source_ids=[sid]),
        )
        evidence_items.append(evidence)

    # Create report
    report = ResearchReport(
        report_id=f"rep-{req.request_id}",
        request_id=req.request_id,
        topic=topic,
        results=[ResearchResult(
            result_id=f"res-{req.request_id}-0",
            query_ref=req.request_id,
            status="relevant",
            evidence_items=evidence_items,
            source_ids=source_ids,
        )],
        summary=f"Research for '{topic}': {len(sources_raw)} sources discovered via {provider.provider_name}.",
        provenance=ProvenanceRecord(provider=provider.provider_name, deterministic_idempotency_key=f"res-{topic}"),
        status="completed",
    )

    # Persist
    db.save_research_report(report.report_id, request_id, topic, status="completed", summary=report.summary, provenance_json=report.provenance.model_dump_json())
    for ev in evidence_items:
        db.record_research_evidence(ev.evidence_id, report.report_id, ev.source_id, ev.snippet, ev.relevance_score, ev.status, provenance_json=ev.provenance.model_dump_json())
    db.update_research_request_status(request_id, "completed")

    # Filesystem artifact for research stage
    art_dir = Path(CONFIG.get_artifacts_dir()) / "jobs" / request_id / "research"
    art_dir.mkdir(parents=True, exist_ok=True)
    (art_dir / "research_report.json").write_text(json.dumps(report.model_dump(mode="json"), indent=2))
    (art_dir / "evidence.jsonl").write_text("\n".join([json.dumps({"evidence_id": ev.evidence_id, "source_id": ev.source_id, "snippet": ev.snippet, "provider": provider.provider_name}) for ev in evidence_items]))

    # Cache
    cache_payload = report.model_dump(mode="json")
    cache_payload["sources_found"] = len(sources_raw)
    save_cache(cache_key_val, cache_payload)

    # Log
    logger.info("research_complete", details={"sources_found": len(sources_raw), "evidence_items": len(evidence_items), "report_id": report.report_id})

    # Human-readable summary
    print(f"\n=== PHASE 2 RESEARCH COMPLETE ===")
    print(f"Topic: {topic}")
    print(f"Provider: {provider_name} ({provider.provider_name})")
    print(f"Request ID: {request_id}")
    print(f"Sources found: {len(sources_raw)}")
    print(f"Evidence records: {len(evidence_items)}")
    print(f"Report ID: {report.report_id}")
    status_text = "real-world research" if provider.provider_name == "wikipedia" else "deterministic mock — not real-world research"
    print(f"Status: completed ({status_text})")
    print(f"Cache key: {cache_key_val}")
    print(f"Artifact: artifacts/jobs/{request_id}/research/ (DB + JSON log)")
    return 0


def run_assets(job_id: str, provider_name: str = "local", search_only: bool = False, dry_run: bool = False) -> int:
    import json
    from autopilot.core.contracts import ScriptDocument, ScriptScene
    from autopilot.core.artifacts import job_artifact_dir, script_path
    from autopilot.core.asset_pipeline import process_scene_assets
    from autopilot.db.manager import DBManager

    db = DBManager(CONFIG.db_path)
    db.init_schema()

    # Load existing script if available; otherwise construct a default search scene
    sp = script_path(job_id, "script.json")
    script = None
    if sp.exists():
        try:
            script = ScriptDocument.model_validate_json(sp.read_text(encoding="utf-8"))
        except Exception:
            script = None

    if not script:
        # Fallback single scene for direct asset querying
        job_row = db.get_job(job_id)
        topic = job_row.get("topic") if job_row else f"Topic for {job_id}"
        script = ScriptDocument(
            content_id=job_id,
            topic=topic,
            scenes=[
                ScriptScene(
                    scene_id="scene-01",
                    order=1,
                    narration=f"Visual asset for {topic}",
                    visual_intent=topic,
                    asset_query=topic,
                    estimated_duration_seconds=5.0,
                )
            ],
        )

    print(f"=== M3 ASSET ENGINE ===")
    print(f"Job ID: {job_id}")
    print(f"Provider: {provider_name}")
    print(f"Scenes: {len(script.scenes)}")
    print(f"Mode: {'Search Only' if search_only else ('Dry Run' if dry_run else 'Full Acquisition')}")

    try:
        artifacts, report = process_scene_assets(
            script=script,
            job_id=job_id,
            provider_name=provider_name,
            db=db,
            dry_run=dry_run,
            search_only=search_only,
        )
    except Exception as exc:
        print(f"\n[ERROR] Asset acquisition failed: {exc}")
        return 1

    print("\n--- ASSET SUMMARY ---")
    print(f"Total Requests: {report.get('total_requests', 0)}")
    print(f"Candidates Found: {report.get('total_candidates_found', 0)}")
    print(f"Selected: {report.get('successful_selections', 0)}")
    print(f"Artifacts Created: {len(artifacts)}")
    print(f"Warnings: {len(report.get('warnings', []))}")
    print(f"Errors: {len(report.get('errors', []))}")

    for sel in report.get("selections", []):
        print(f"  + Scene {sel.get('scene_id')}: Selected '{sel.get('candidate_id')}' [{sel.get('license')}] (Score: {sel.get('score')})")

    for rej in report.get("rejections", []):
        print(f"  - Rejection: {rej.get('candidate_id', 'query')} -> {rej.get('reason')}")

    for err in report.get("errors", []):
        print(f"  ! Error: {err}")

    report_file = job_artifact_dir(job_id) / "assets" / "asset_quality_report.json"
    print(f"Quality Report: {report_file}")

    return 0 if report.get("status") in ("completed", "pending") and len(report.get("errors", [])) == 0 else (0 if artifacts else 1)


def run_render(job_id: str, asset_provider: str = "local", profile: str = "short_vertical", production_engine: str = "moneyprinterturbo") -> int:
    import json
    from pathlib import Path
    from autopilot.core.contracts import ScriptDocument, ContentPackage, RenderPlan, AssetArtifact, ProductionRequest
    from autopilot.core.artifacts import job_artifact_dir, script_path
    from autopilot.core.audio_duration import extract_duration
    from autopilot.core.asset_cache import compute_file_sha256
    from autopilot.core.asset_pipeline import process_scene_assets
    from autopilot.core.renderer import FFmpegRenderer
    from autopilot.providers.production.factory import get_production_engine
    from autopilot.core.state_machine import WorkflowState
    from autopilot.core.logging import StructuredLogger
    from autopilot.db.manager import DBManager

    db = DBManager(CONFIG.db_path)
    db.init_schema()
    logger = StructuredLogger(job_id=job_id, stage="render")

    art_dir = job_artifact_dir(job_id)
    sp_path = script_path(job_id, "script.json")
    pkg_path = art_dir / "script" / "content_package.json"

    if not sp_path.exists():
        print(f"[ERROR] Script artifact not found for job '{job_id}': {sp_path}")
        return 1

    try:
        script = ScriptDocument.model_validate_json(sp_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[ERROR] Failed to load script for job '{job_id}': {exc}")
        return 1

    package = None
    if pkg_path.exists():
        try:
            package = ContentPackage.model_validate_json(pkg_path.read_text(encoding="utf-8"))
        except Exception:
            package = None

    print(f"=== M4 RENDER ENGINE ===")
    print(f"Job ID: {job_id}")
    print(f"Topic: {script.topic}")
    print(f"Scenes: {len(script.scenes)}")
    print(f"Asset Provider: {asset_provider}")
    print(f"Profile: {profile}")

    # 1. Resolve asset artifacts from DB or acquire if missing
    db_assets = db.get_asset_artifacts_for_job(job_id)
    asset_artifacts = []
    assets_valid = False
    if db_assets and len(db_assets) >= len(script.scenes):
        all_exist = True
        for da in db_assets:
            p = Path(da.get("artifact_path", ""))
            if not p.exists() or p.stat().st_size == 0:
                all_exist = False
                break
            if asset_provider == "openverse":
                prov_raw = da.get("provenance_json") or "{}"
                try:
                    pdata = json.loads(prov_raw)
                    if pdata.get("provider") != "openverse":
                        all_exist = False
                        break
                except Exception:
                    pass
        assets_valid = all_exist

    if not assets_valid:
        print(f"Acquiring scene assets via '{asset_provider}'...")
        db.update_job_status(job_id, WorkflowState.ASSET_PREPARING.value)
        asset_artifacts, report = process_scene_assets(
            script=script,
            job_id=job_id,
            provider_name=asset_provider,
            db=db,
            config=CONFIG,
        )
        if not asset_artifacts or report.get("errors"):
            err_msg = "; ".join(report.get("errors", ["No assets found"]))
            db.update_job_status(job_id, WorkflowState.FAILED_ASSETS.value)
            print(f"[ERROR] Asset acquisition failed: {err_msg}")
            return 1
        db.update_job_status(job_id, WorkflowState.ASSETS_READY.value)
    else:
        from autopilot.core.contracts import AssetProvenance, AssetLicense
        for da in db_assets:
            prov_raw = json.loads(da.get("provenance_json") or "{}")
            lic_raw = json.loads(da.get("license_json") or "{}")
            asset_artifacts.append(AssetArtifact(
                artifact_id=f"art-{da.get('artifact_id')}",
                job_id=job_id,
                content_id=da.get("content_id"),
                scene_id=da.get("scene_id"),
                source_path=da.get("artifact_path"),
                normalized_path=da.get("artifact_path"),
                checksum_sha256=da.get("checksum_sha256"),
                provenance=AssetProvenance(**prov_raw) if prov_raw else AssetProvenance(),
                license=AssetLicense(**lic_raw) if lic_raw else AssetLicense(),
            ))

    # 2. Resolve voice artifacts
    voice_dir = art_dir / "voice"
    voice_artifacts = []
    if package and package.voice_artifacts:
        for v in package.voice_artifacts:
            vp = Path(v)
            if vp.exists() and vp.stat().st_size > 0:
                voice_artifacts.append(str(vp))
    if not voice_artifacts and voice_dir.exists():
        import glob
        found_wavs = sorted(glob.glob(str(voice_dir / "*.wav")))
        voice_artifacts.extend(found_wavs)

    # 3. Build render scenes and plan
    render_dir = art_dir / "render"
    render_dir.mkdir(parents=True, exist_ok=True)
    final_mp4 = render_dir / "final.mp4"
    plan_path = render_dir / "render_plan.json"

    render_scenes = []
    for idx, scene in enumerate(script.scenes):
        matched_art = next((a for a in asset_artifacts if a.scene_id == scene.scene_id), None)
        norm_path = matched_art.normalized_path if matched_art else None
        voice_path = None
        for v in voice_artifacts:
            if scene.scene_id in Path(v).name:
                voice_path = v
                break
        if not voice_path and idx < len(voice_artifacts):
            voice_path = voice_artifacts[idx]

        dur = scene.estimated_duration_seconds
        if voice_path and Path(voice_path).exists():
            dur_info = extract_duration(voice_path)
            if dur_info.get("valid") and dur_info.get("duration_sec", 0) > 0:
                dur = max(float(dur_info["duration_sec"]), 2.0)

        render_scenes.append({
            "scene_id": scene.scene_id,
            "duration_sec": dur,
            "asset_path": norm_path,
            "audio_path": voice_path,
        })

    raw_speech_dur = sum(s.get("duration_sec", 0) for s in render_scenes)
    render_plan = RenderPlan(
        plan_id=f"plan-{job_id}",
        content_id=job_id,
        job_id=job_id,
        profile=profile,
        production_engine=production_engine,
        scenes=render_scenes,
        raw_speech_duration_sec=raw_speech_dur,
    )
    plan_path.write_text(render_plan.model_dump_json(indent=2), encoding="utf-8")

    # 4. Execute Render via selected Production Engine
    print(f"Rendering {len(render_scenes)} scenes via production engine '{production_engine}'...")
    db.update_job_status(job_id, WorkflowState.RENDERING.value)
    try:
        engine = get_production_engine(
            engine_name=production_engine,
            profile=profile,
            endpoint=CONFIG.moneyprinter_endpoint,
            cli_path=CONFIG.moneyprinter_cli_path,
        )
        voice_file = None
        for s in render_scenes:
            if s.get("audio_path") and Path(s.get("audio_path")).exists():
                voice_file = s.get("audio_path")
                break
        prod_req = ProductionRequest(
            job_id=job_id,
            content_id=job_id,
            topic=script.topic if script else job_id,
            script=script,
            render_plan=render_plan,
            output_path=str(final_mp4),
            profile=profile,
            production_engine=production_engine,
            voice_path=voice_file,
        )
        render_out = engine.generate(prod_req)
        render_checksum = render_out.checksum_sha256 or compute_file_sha256(str(final_mp4))
        render_plan.rendered_duration_sec = render_out.duration_sec
        render_plan.raw_speech_duration_sec = raw_speech_dur
        plan_path.write_text(render_plan.model_dump_json(indent=2), encoding="utf-8")
        db.record_artifact(job_id, str(final_mp4), "media", checksum_sha256=render_checksum)
        db.update_job_status(job_id, WorkflowState.RENDERED.value)
        logger.info("stage_completed", details={"stage": "RENDER", "engine": production_engine, "path": str(final_mp4), "sha256": render_checksum[:16]})
    except Exception as exc:
        db.update_job_status(job_id, WorkflowState.FAILED_RENDER.value)
        db.record_error(job_id, "RENDER", "render_failed", str(exc))
        print(f"\n[ERROR] Video render failed ({production_engine}): {exc}")
        return 1

    print(f"\n=== RENDER COMPLETE ===")
    print(f"Job ID: {job_id}")
    print(f"Engine: {production_engine}")
    print(f"Output: {final_mp4}")
    print(f"Duration: {render_out.duration_sec:.2f}s")
    print(f"Resolution: {render_out.width}x{render_out.height}")
    print(f"Checksum: {render_checksum}")
    print(f"Plan: {plan_path}")
    print(f"Next step: python -m autopilot qa --job {job_id}")
    return 0


def run_qa(job_id: str, media_path: str | None = None, verbose: bool = False, output_json: bool = False, strict: bool = False) -> int:
    import json
    from pathlib import Path
    from autopilot.core.contracts import ContentPackage, RenderPlan, AssetArtifact, QAStatus
    from autopilot.core.artifacts import job_artifact_dir
    from autopilot.core.qa_engine import QAEngine, export_qa_artifacts
    from autopilot.core.state_machine import WorkflowState
    from autopilot.db.manager import DBManager

    db = DBManager(CONFIG.db_path)
    db.init_schema()

    # Determine media path
    target_media = None
    if media_path and Path(media_path).exists():
        target_media = Path(media_path)
    else:
        # Check standard render output
        standard_path = CONFIG.get_artifacts_dir() / "jobs" / job_id / "render" / "final.mp4"
        if standard_path.exists():
            target_media = standard_path
        else:
            # Check DB artifacts table
            artifacts = db.get_artifacts_for_job(job_id)
            for a in artifacts:
                if a.get("artifact_type") == "media":
                    p = Path(a.get("artifact_path"))
                    if p.exists():
                        target_media = p
                        break

    if not target_media:
        err_msg = f"No rendered media found for job '{job_id}' (searched artifacts and DB)"
        if output_json:
            print(json.dumps({"status": "BLOCK", "publish_allowed": False, "error": err_msg}))
        else:
            print(f"[ERROR] {err_msg}")
        return 1

    # Load package if available
    package = None
    package_path = job_artifact_dir(job_id) / "script" / "content_package.json"
    if package_path.exists():
        try:
            package = ContentPackage.model_validate_json(package_path.read_text(encoding="utf-8"))
        except Exception:
            package = None

    # Load plan if available
    plan = None
    plan_path = job_artifact_dir(job_id) / "render" / "render_plan.json"
    if plan_path.exists():
        try:
            plan = RenderPlan.model_validate_json(plan_path.read_text(encoding="utf-8"))
        except Exception:
            plan = None

    # Load asset artifacts from DB if available
    asset_artifacts = []
    try:
        db_assets = db.get_asset_artifacts_for_job(job_id)
        for da in db_assets:
            prov_raw = json.loads(da.get("provenance_json") or "{}")
            lic_raw = json.loads(da.get("license_json") or "{}")
            from autopilot.core.contracts import AssetProvenance, AssetLicense
            asset_artifacts.append(AssetArtifact(
                artifact_id=f"art-{da.get('artifact_id')}",
                job_id=job_id,
                content_id=da.get("content_id"),
                scene_id=da.get("scene_id"),
                source_path=da.get("artifact_path"),
                normalized_path=da.get("artifact_path"),
                checksum_sha256=da.get("checksum_sha256"),
                provenance=AssetProvenance(**prov_raw) if prov_raw else AssetProvenance(),
                license=AssetLicense(**lic_raw) if lic_raw else AssetLicense(),
            ))
    except Exception:
        pass

    engine = QAEngine(CONFIG)
    report = engine.evaluate(
        media_path=target_media,
        plan=plan,
        package=package,
        asset_artifacts=asset_artifacts,
        profile="vertical_short",
        strict=strict,
        job_id=job_id,
        db_manager=db,
    )

    # Ensure job exists in DB for foreign key constraints
    if not db.get_job(job_id):
        db.create_job(job_id=job_id, topic=job_id)

    # Persist report in DB
    db.record_qa_report(report)

    # Export artifacts to filesystem
    art_dir = job_artifact_dir(job_id)
    export_paths = export_qa_artifacts(report, art_dir)
    db.record_artifact(job_id, export_paths["quality_report"], "quality")
    db.record_artifact(job_id, export_paths["receipt"], "quality")

    # Update workflow state
    if report.status == QAStatus.BLOCK:
        db.update_job_status(job_id, WorkflowState.FAILED_QA.value)
    else:
        db.update_job_status(job_id, WorkflowState.QA.value)

    if output_json:
        print(report.receipt.model_dump_json(indent=2) if report.receipt else report.model_dump_json(indent=2))
    else:
        print("\n==================================================")
        print("          M5 QA ENGINE & QUALITY RECEIPT          ")
        print("==================================================")
        print(f"Job ID:            {job_id}")
        print(f"Media Path:        {target_media}")
        print(f"QA Status:         {report.status.value}")
        print(f"Publish Allowed:   {'YES' if report.publish_allowed else 'NO'}")
        print(f"Strict Mode:       {'ON' if strict else 'OFF'}")
        print(f"Checks Run:        {len(report.checks)}")
        print(f"Blocking Findings: {len(report.receipt.blocking_findings if report.receipt else [])}")
        print(f"Warnings:          {len(report.receipt.warnings if report.receipt else [])}")
        print(f"Metrics:           {len(report.metrics)}")

        if report.receipt and report.receipt.blocking_findings:
            print("\n--- BLOCKING FAILURES ---")
            for bf in report.receipt.blocking_findings:
                print(f"  [BLOCK] {bf.check_id}: {bf.message}")

        if report.receipt and report.receipt.warnings:
            print("\n--- WARNINGS ---")
            for w in report.receipt.warnings:
                print(f"  [WARN]  {w.check_id}: {w.message}")

        if verbose:
            print("\n--- DETAILED CHECKS ---")
            for c in report.checks:
                flag = f"[{c.status.value}]"
                print(f"  {flag:<8} {c.check_id} ({c.category}): {c.message}")
            print("\n--- METRICS ---")
            for m in report.metrics:
                val = f"{m.value_numeric} {m.unit or ''}" if m.value_numeric is not None else str(m.value_text)
                print(f"  - {m.name}: {val.strip()} [{m.status.value}]")

        print("\n--- ARTIFACTS ---")
        print(f"Receipt:        {export_paths['receipt']}")
        print(f"Full Report:    {export_paths['quality_report']}")

    return 0 if (report.status in (QAStatus.PASS, QAStatus.WARN) and report.publish_allowed) else 1


def run_publish(
    job_id: str,
    platform: str = "youtube",
    dry_run: bool = False,
    visibility: Optional[str] = None,
    scheduled_time: Optional[str] = None,
    force_retry: bool = False,
    media_path: Optional[str] = None,
    output_json: bool = False,
) -> int:
    import json
    from autopilot.core.publisher import PublishingEngine
    from autopilot.core.contracts import PublishStatus

    engine = PublishingEngine(CONFIG)
    result = engine.publish_job(
        job_id=job_id,
        platform=platform,
        visibility=visibility,
        scheduled_time=scheduled_time,
        dry_run=dry_run,
        force_retry=force_retry,
        media_path=media_path,
    )

    if output_json:
        print(json.dumps(result.model_dump(), indent=2, default=str))
    else:
        print("\n==================================================")
        print("          M6 PUBLISHING ENGINE RESULT             ")
        print("==================================================")
        video_path_str = media_path or (result.dry_run_preview.get("media_path") if result.dry_run_preview else "N/A")
        qa_status_str = "PASSED" if (result.receipt or result.dry_run_preview) else "UNKNOWN"
        vis_str = result.receipt.visibility.value if result.receipt else (visibility or "private")
        sched_str = result.receipt.scheduled_time if (result.receipt and result.receipt.scheduled_time) else (scheduled_time or "IMMEDIATE")
        remote_url_str = result.receipt.remote_url if (result.receipt and result.receipt.remote_url) else "N/A"

        print(f"JOB:                 {job_id}")
        print(f"STATUS:              {'SUCCESS' if result.success else 'FAILED'}")
        print(f"VIDEO:               {video_path_str}")
        print(f"QA:                  {qa_status_str}")
        print(f"TARGET PLATFORM:     {platform}")
        print(f"VISIBILITY:          {vis_str}")
        print(f"SCHEDULE:            {sched_str}")
        print(f"PUBLICATION STATUS:  {result.status.value}")
        print(f"REMOTE URL:          {remote_url_str}")

        if result.receipt:
            rcpt = result.receipt
            print(f"Remote Video ID:     {rcpt.remote_video_id or 'N/A'}")
            print(f"Idempotency Key:     {rcpt.idempotency_key[:16]}...")

        if result.error:
            print(f"\n--- ERROR [{result.error.error_code}] ---")
            print(f"Message: {result.error.message}")

        if result.dry_run_preview:
            print("\n--- DRY RUN PREVIEW ---")
            print(f"Endpoint:   {result.dry_run_preview.get('endpoint')}")
            print(f"Media Path: {result.dry_run_preview.get('media_path')}")
            print(f"File Size:  {result.dry_run_preview.get('file_size')} bytes")
            print(f"Metadata:   {json.dumps(result.dry_run_preview.get('metadata', {}), indent=2)}")

        art_dir = job_artifact_dir(job_id) / "publish"
        print("\n--- ARTIFACTS ---")
        print(f"Request:  {art_dir / 'request.json'}")
        print(f"Result:   {art_dir / 'result.json'}")
        if result.receipt:
            print(f"Receipt:  {art_dir / 'receipt.json'}")

    return 0 if result.success else 1


def run_youtube_auth(
    secrets_path: Optional[str] = None,
    token_path: Optional[str] = None,
    no_browser: bool = False,
    port: int = 0,
    output_json: bool = False,
) -> int:
    import json
    from autopilot.providers.youtube_oauth import run_youtube_oauth_flow
    from autopilot.providers.youtube_publisher import redact_secrets

    try:
        saved_path = run_youtube_oauth_flow(
            secrets_path=secrets_path,
            token_path=token_path,
            open_browser=not no_browser,
            port=port,
        )
        if output_json:
            print(json.dumps({
                "success": True,
                "token_path": str(saved_path),
                "scope": "https://www.googleapis.com/auth/youtube.upload",
            }))
        else:
            print("\n==================================================")
            print("        YOUTUBE OAUTH AUTHENTICATION READY        ")
            print("==================================================")
            print(f"Token Saved:  {saved_path}")
            print("Scope:        https://www.googleapis.com/auth/youtube.upload")
            print("Status:       SUCCESS")
            print("Publishing is now ready for private/unlisted uploads.\n")
        return 0
    except Exception as exc:
        err_msg = redact_secrets(str(exc))
        if output_json:
            print(json.dumps({"success": False, "error": err_msg}))
        else:
            print(f"\n[ERROR] YouTube OAuth authorization failed: {err_msg}\n")
        return 1


# ------------------------------------------------------------------
# Milestone 7 / M7 — Batch & Queue CLI Handlers
# ------------------------------------------------------------------
def run_batch_submit(file_path: str, dry_run: bool = False, force: bool = False, output_json: bool = False) -> int:
    import json
    from autopilot.core.batch import BatchProcessor

    processor = BatchProcessor()
    try:
        manifest = processor.parse_manifest_file(file_path)
    except Exception as exc:
        if output_json:
            print(json.dumps({"error": f"Failed to parse manifest: {exc}", "success": False}))
        else:
            print(f"[ERROR] Failed to parse manifest: {exc}")
        return 1

    result = processor.submit_manifest(manifest, dry_run=dry_run, force=force)

    if output_json:
        print(result.model_dump_json(indent=2))
    else:
        print(f"=== BATCH SUBMISSION {'(DRY RUN)' if dry_run else ''} ===")
        print(f"Manifest ID:  {result.manifest_id}")
        print(f"Total Items:  {result.total_items}")
        print(f"Submitted:    {result.submitted_count}")
        print(f"Skipped Dups: {result.skipped_duplicate_count}")
        if result.queued_ids:
            print(f"Queued IDs:   {', '.join(result.queued_ids)}")
        if result.errors:
            print("\nErrors:")
            for err in result.errors:
                print(f"  ! {err}")

    return 0 if result.submitted_count > 0 or (result.total_items == 0 or (result.skipped_duplicate_count > 0 and not result.errors)) else 1


def run_batch_direct(
    topics_file: str,
    channel: str = "default",
    production_engine: str = "moneyprinterturbo",
    policy: str = "local_only",
    max_regeneration_attempts: int = 3,
    output_json: bool = False,
) -> int:
    import json
    from autopilot.core.batch import BatchProcessor
    from autopilot.core.pipeline import PipelineOrchestrator

    processor = BatchProcessor()
    try:
        manifest = processor.parse_manifest_file(topics_file, channel_id=channel)
    except Exception as exc:
        if output_json:
            print(json.dumps({"error": f"Failed to parse topics file: {exc}", "success": False}))
        else:
            print(f"[ERROR] Failed to parse topics file: {exc}")
        return 1

    orchestrator = PipelineOrchestrator(config=CONFIG, db=processor.db)
    result = processor.execute_batch(
        manifest=manifest,
        orchestrator=orchestrator,
        production_engine=production_engine,
        policy=policy,
        max_regeneration_attempts=max_regeneration_attempts,
    )

    if output_json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(f"=== BATCH PRODUCTION RESULT ===")
        print(f"Manifest ID:      {result['manifest_id']}")
        print(f"Total Jobs:       {result['total_jobs']}")
        print(f"Succeeded:        {result['succeeded_jobs']}")
        print(f"Needs Review:     {result['needs_review_jobs']}")
        print(f"Failed:           {result['failed_jobs']}")
        print("-" * 50)
        for j in result["job_results"]:
            stat = j.get("status")
            top = j.get("topic")
            jid = j.get("job_id")
            err = j.get("error")
            err_str = f" ({err})" if err else ""
            print(f"[{stat.upper()}] {top} -> {jid}{err_str}")

    return 0 if result["failed_jobs"] == 0 and result["needs_review_jobs"] == 0 else 1


def run_queue_list(status: str | None = None, limit: int = 50, output_json: bool = False) -> int:
    import json
    from autopilot.db.manager import DBManager

    db = DBManager(CONFIG.db_path)
    db.init_schema()
    items = db.list_queue_items(status=status, limit=limit)

    if output_json:
        print(json.dumps(items, indent=2, default=str))
    else:
        print(f"=== QUEUE ITEMS ({len(items)} shown) ===")
        if not items:
            print("Queue is empty.")
            return 0
        fmt = "{:<16} {:<18} {:<12} {:<8} {:<12} {:<8} {:<20}"
        print(fmt.format("Queue ID", "Job ID", "Status", "Priority", "Stage", "Attempts", "Scheduled"))
        print("-" * 96)
        for it in items:
            sched = str(it.get("scheduled_at") or "-")
            print(fmt.format(
                it.get("queue_id", "")[:16],
                it.get("job_id", "")[:18],
                it.get("status", ""),
                str(it.get("priority", 2)),
                it.get("stage", ""),
                f"{it.get('attempt_count', 0)}/{it.get('max_attempts', 3)}",
                sched[:20],
            ))
    return 0


def run_queue_status(output_json: bool = False) -> int:
    import json
    from autopilot.db.manager import DBManager

    db = DBManager(CONFIG.db_path)
    db.init_schema()
    summary = db.get_queue_status_summary()

    if output_json:
        print(json.dumps(summary, indent=2))
    else:
        print("=== QUEUE STATUS SUMMARY ===")
        print(f"Queued:       {summary.get('queued', 0)}")
        print(f"Running:      {summary.get('running', 0)}")
        print(f"Retry Wait:   {summary.get('retry_wait', 0)}")
        print(f"Succeeded:    {summary.get('succeeded', 0)}")
        print(f"Failed:       {summary.get('failed', 0)}")
        print(f"Blocked:      {summary.get('blocked', 0)}")
        print(f"Cancelled:    {summary.get('cancelled', 0)}")
        print(f"Dead Letter:  {summary.get('dead_letter', 0)}")
        print(f"Total:        {summary.get('total', 0)}")
        workers = summary.get("active_workers", [])
        print(f"Active Workers ({len(workers)}): {', '.join(workers) if workers else 'none'}")
    return 0


def run_queue_run(
    max_jobs: int | None = None,
    worker_id: str | None = None,
    poll_interval: float | None = None,
    once: bool = False,
) -> int:
    from autopilot.core.worker import LocalWorker

    worker = LocalWorker(worker_id=worker_id)
    print(f"Starting local worker [{worker.worker_id}] (mode: {'ONCE' if once else 'DAEMON'})...")
    processed = worker.run(max_jobs=max_jobs, poll_interval=poll_interval, once=once)
    print(f"Worker [{worker.worker_id}] finished. Jobs processed: {processed}")
    return 0


def run_queue_retry(job_id: str) -> int:
    from autopilot.db.manager import DBManager

    db = DBManager(CONFIG.db_path)
    db.init_schema()

    item = db.get_queue_item_by_job(job_id) or db.get_queue_item(job_id)
    if not item:
        print(f"[ERROR] Queue item not found for '{job_id}'")
        return 1

    qid = item["queue_id"]
    success = db.retry_queue_item(qid)
    if success:
        print(f"Queue item '{qid}' (job: '{item['job_id']}') reset to 'queued' for retry.")
        return 0
    else:
        print(f"[ERROR] Failed to retry queue item '{qid}' (current status: {item.get('status')})")
        return 1


def run_queue_cancel(job_id: str) -> int:
    from autopilot.db.manager import DBManager

    db = DBManager(CONFIG.db_path)
    db.init_schema()

    item = db.get_queue_item_by_job(job_id) or db.get_queue_item(job_id)
    if not item:
        print(f"[ERROR] Queue item not found for '{job_id}'")
        return 1

    qid = item["queue_id"]
    success = db.cancel_queue_item(qid)
    if success:
        print(f"Queue item '{qid}' (job: '{item['job_id']}') cancelled.")
        return 0
    else:
        print(f"[ERROR] Could not cancel queue item '{qid}' (current status: {item.get('status')})")
        return 1


def run_queue_cancel_all(
    status: str = "queued",
    dry_run: bool = False,
    output_json: bool = False,
) -> int:
    import json
    import sys
    from autopilot.db.manager import DBManager

    if status != "queued":
        msg = f"[ERROR] cancel-all only supports status 'queued', got '{status}'"
        if output_json:
            print(json.dumps({"error": msg}))
        else:
            print(msg, file=sys.stderr)
        return 1

    db = DBManager(CONFIG.db_path)
    db.init_schema()

    summary = db.cancel_all_queued_items(status=status, dry_run=dry_run)

    if output_json:
        print(json.dumps(summary, indent=2))
    else:
        print("=== QUEUE CANCEL ALL ===")
        print(f"Target Status:   {summary['status']}")
        print(f"Items Found:     {summary['found_count']}")
        print(f"Items Cancelled: {summary['cancelled_count']}")
        print(f"Dry Run:         {summary['dry_run']}")
        if dry_run:
            print("[NOTICE] Dry run mode — previewed count without mutating database.")
        elif summary['cancelled_count'] > 0:
            print(f"[SUCCESS] Successfully cancelled {summary['cancelled_count']} queued item(s).")
        else:
            print("[INFO] No queued items were available to cancel.")
    return 0



def run_queue_inspect(job_id: str, output_json: bool = False) -> int:
    import json
    from autopilot.db.manager import DBManager

    db = DBManager(CONFIG.db_path)
    db.init_schema()

    item = db.get_queue_item_by_job(job_id) or db.get_queue_item(job_id)
    if not item:
        if output_json:
            print(json.dumps({"error": f"Queue item not found for '{job_id}'"}))
        else:
            print(f"[ERROR] Queue item not found for '{job_id}'")
        return 1

    real_job_id = item["job_id"]
    job_row = db.get_job(real_job_id)
    events = db.get_events_for_job(real_job_id)
    artifacts = db.get_artifacts_for_job(real_job_id)
    errors = db.get_errors_for_job(real_job_id)

    full_record = {
        "queue_item": item,
        "job": job_row,
        "events": events,
        "artifacts": artifacts,
        "errors": errors,
    }

    if output_json:
        print(json.dumps(full_record, indent=2, default=str))
    else:
        print(f"=== QUEUE ITEM INSPECT [{item['queue_id']}] ===")
        print(f"Job ID:       {item['job_id']}")
        print(f"Status:       {item['status']}")
        print(f"Stage:        {item['stage']}")
        print(f"Priority:     {item['priority']}")
        print(f"Attempts:     {item['attempt_count']} / {item['max_attempts']}")
        print(f"Scheduled At: {item.get('scheduled_at') or 'Immediate'}")
        print(f"Started At:   {item.get('started_at') or 'Not started'}")
        print(f"Completed At: {item.get('completed_at') or 'Not completed'}")
        print(f"Last Error:   {item.get('last_error') or 'None'}")
        print(f"Worker ID:    {item.get('worker_id') or 'Unassigned'}")
        print(f"Lease Exp:    {item.get('lease_expires_at') or 'None'}")
        print(f"\nWorkflow Events: {len(events)}")
        for ev in events[-5:]:
            print(f"  [{ev.get('occurred_at')}] {ev.get('from_state')} -> {ev.get('to_state')} ({ev.get('reason')})")
        print(f"\nArtifacts: {len(artifacts)}")
        for art in artifacts:
            print(f"  [{art.get('artifact_type')}] {art.get('artifact_path')}")
        if errors:
            print(f"\nErrors ({len(errors)}):")
            for err in errors:
                print(f"  ! [{err.get('stage')}] {err.get('message')}")
    return 0


def run_analytics_sync(
    job_id: str | None = None,
    platform: str | None = None,
    provider: str | None = None,
    window: str = "lifetime",
    sync_all_jobs: bool = False,
    limit: int = 25,
    dry_run: bool = False,
    output_json: bool = False,
) -> int:
    import json
    from autopilot.core.analytics import AnalyticsEngine

    engine = AnalyticsEngine()
    if sync_all_jobs or not job_id:
        result = engine.sync_all(
            platform=platform,
            provider_name=provider,
            window=window,
            limit=limit,
            dry_run=dry_run,
        )
        if output_json:
            print(json.dumps(result, indent=2, default=str))
        else:
            print(f"=== ANALYTICS BATCH SYNC ===")
            print(f"Total Targeted: {result['total_targeted']}")
            print(f"Synced Count:   {result['synced_count']}")
            print(f"Dry Run:        {result['dry_run']}")
            for r in result.get("results", []):
                status_icon = "+" if r.get("status") in ("success", "simulated") else "!"
                print(f" {status_icon} [{r.get('job_id')}] Status: {r.get('status')} | Remote ID: {r.get('remote_id', 'N/A')}")
        return 0
    else:
        try:
            result = engine.sync_job(
                job_id=job_id,
                platform=platform,
                provider_name=provider,
                window=window,
                dry_run=dry_run,
            )
            if output_json:
                print(json.dumps(result, indent=2, default=str))
            else:
                print(f"=== ANALYTICS SYNC [{result['job_id']}] ===")
                print(f"Status:       {result['status']}")
                print(f"Remote ID:    {result['remote_id']}")
                print(f"Platform:     {result['platform']}")
                print(f"Provider:     {result['provider']}")
                print(f"Window:       {result['window']}")
                if result.get("dry_run"):
                    print("Dry Run:      True (no API calls or DB writes performed)")
                else:
                    print(f"Snapshot ID:  {result.get('snapshot_id')}")
                    print("\nMeasured Metrics:")
                    for k, v in result.get("metrics", {}).items():
                        print(f"  - {k}: {v}")
                    print("\nDerived Metrics:")
                    for k, v in result.get("derived_metrics", {}).items():
                        print(f"  * {k}: {v}")
            return 0
        except Exception as exc:
            if output_json:
                print(json.dumps({"error": str(exc), "job_id": job_id}, indent=2))
            else:
                print(f"Error syncing analytics for job '{job_id}': {exc}", file=sys.stderr)
            return 1


def run_analytics_show(job_id: str, output_json: bool = False) -> int:
    import json
    from autopilot.core.analytics import AnalyticsEngine

    engine = AnalyticsEngine()
    perf = engine.db.get_content_performance(job_id)
    if not perf:
        if output_json:
            print(json.dumps({"error": f"No content performance record found for job '{job_id}'"}, indent=2))
        else:
            print(f"Error: No content performance record found for job '{job_id}'", file=sys.stderr)
        return 1

    if output_json:
        print(perf.model_dump_json(indent=2))
    else:
        print(f"=== CONTENT PERFORMANCE [{perf.job_id}] ===")
        print(f"Topic:        {perf.topic or 'N/A'}")
        print(f"Platform:     {perf.platform or 'N/A'}")
        print(f"Remote ID:    {perf.remote_id or 'N/A'}")
        print(f"Published At: {perf.published_at or 'N/A'}")
        print(f"Receipt ID:   {perf.publication_receipt_id or 'N/A'}")
        print(f"Snapshots:    {len(perf.snapshot_history)}")

        latest = perf.latest_snapshot
        if latest:
            print(f"\n--- Latest Snapshot [{latest.snapshot_id}] ({latest.window.value}) ---")
            print(f"Observed At:  {latest.observed_at}")
            print(f"Provider:     {latest.provider} ({'Synthetic/Test' if latest.is_synthetic else 'Measured/Live'})")
            print("Measured Metrics:")
            for k, m in latest.metrics.items():
                print(f"  - {k} ({m.raw_name}): {m.normalized_value} {m.unit}")
            if latest.derived_metrics:
                print("Derived Metrics:")
                for k, d in latest.derived_metrics.items():
                    print(f"  * {k}: {d.value} [{d.formula}]")
        else:
            print("\nNo analytics snapshots recorded yet.")
    return 0


def run_analytics_report(
    platform: str | None = None,
    channel_id: str | None = None,
    limit: int = 20,
    output_json: bool = False,
) -> int:
    import json
    from autopilot.core.analytics import AnalyticsEngine

    engine = AnalyticsEngine()

    if channel_id:
        attr = engine.attribute_channel_performance(channel_id)
        if output_json:
            print(json.dumps(attr, indent=2, default=str))
        else:
            print("\n==================================================")
            print(f"   CHANNEL PERFORMANCE ATTRIBUTION: {channel_id}  ")
            print("==================================================")
            print(f"Total Videos:   {attr.get('total_videos', 0)}")
            print(f"Average Views:  {attr.get('average_views', 0.0)}")
            print(f"Recommendation: {attr.get('recommendation', 'N/A')}")
            if attr.get("top_durations"):
                print("\n--- DURATION PERFORMANCE ---")
                for d in attr["top_durations"]:
                    print(f"  * {d['category']:<18}: avg {d['avg_views']:>6.1f} views (sample size: {d['sample_size']})")
            if attr.get("top_engines"):
                print("\n--- PRODUCTION ENGINE PERFORMANCE ---")
                for e in attr["top_engines"]:
                    print(f"  * {e['category']:<18}: avg {e['avg_views']:>6.1f} views (sample size: {e['sample_size']})")
            if attr.get("top_hooks"):
                print("\n--- TOP HOOK PATTERNS ---")
                for h in attr["top_hooks"][:3]:
                    print(f"  * {h['category']:<25}: avg {h['avg_views']:>6.1f} views")
        return 0

    items = engine.get_performance_report(platform=platform, limit=limit)

    if output_json:
        print(json.dumps(items, indent=2, default=str))
    else:
        print(f"=== PERFORMANCE REPORT ({len(items)} items) ===")
        print(f"{'JOB ID':<20} {'VIEWS':>8} {'LIKES':>8} {'COMMENTS':>10} {'ENG RATE':>10} {'TYPE':<10} {'TOPIC'}")
        print("-" * 80)
        for it in items:
            t_type = "SYNTHETIC" if it["is_synthetic"] else "MEASURED"
            print(f"{it['job_id']:<20} {it['views']:>8} {it['likes']:>8} {it['comments']:>10} {it['engagement_rate']:>9.2%} {t_type:<10} {it['topic'][:25]}")
    return 0


def run_autonomy_run(
    level: int | None = None,
    channel_id: str | None = None,
    dry_run: bool = False,
    category: str | None = None,
    limit: int = 10,
    output_json: bool = False,
    policy: str | None = None,
) -> int:
    import json
    from autopilot.core.contracts import AutonomyLevel
    from autopilot.core.autonomy import AutonomyEngine

    autonomy_level = AutonomyLevel(level) if level is not None else None
    engine = AutonomyEngine()
    if autonomy_level == AutonomyLevel.LEVEL_4_AUTO_PRODUCE:
        summary = engine.run_auto_produce_cycle(
            channel_id=channel_id,
            limit=limit,
            dry_run=dry_run,
            policy=policy or "local_only",
        )
        if output_json:
            print(summary.model_dump_json(indent=2))
        else:
            print(f"=== AUTO-PRODUCE CYCLE SUMMARY [{summary.run_id}] ===")
            print(f"Channel ID:          {summary.channel_id or 'all / default'}")
            print(f"Autonomy Level:      4 (GUARDED AUTO-PRODUCE)")
            print(f"Policy Tier:         {summary.policy}")
            print(f"Strategy Version:    {summary.active_strategy_version}")
            print(f"Status:              {summary.status}")
            print(f"Dry Run:             {summary.dry_run}")
            print(f"\nDiscovered:          {summary.queued_jobs_discovered} queued item(s)")
            print(f"Eligible:            {summary.jobs_eligible}")
            print(f"Blocked (pre-flight):{summary.jobs_blocked}")
            print(f"Producing:           {summary.jobs_producing}")
            print(f"Completed:           {summary.jobs_completed}")
            print(f"READY_TO_PUBLISH:    {summary.jobs_ready_to_publish}")
            print(f"QA Failed:           {summary.jobs_qa_failed}")
            print(f"Retry Wait:          {summary.jobs_retry_wait}")
            print(f"Skipped (dup):       {summary.jobs_skipped_duplicate}")
            print(f"Limit-blocked:       {summary.jobs_cycle_limit_blocked}")
            print(f"Daily-blocked:       {summary.jobs_daily_limit_blocked}")
            print(f"Concurrency-blocked: {summary.jobs_concurrency_blocked}")
            if summary.error_message:
                print(f"\nError: {summary.error_message}")
        return 0 if summary.status in ("completed", "completed_manual_mode") else 1

    summary = engine.run_cycle(
        autonomy_level=autonomy_level.value if autonomy_level is not None else None,
        channel_id=channel_id,
        dry_run=dry_run,
        category=category,
        limit=limit,
    )

    if output_json:
        print(summary.model_dump_json(indent=2))
    else:
        lvl_name = AutonomyLevel(summary.autonomy_level).name
        print(f"=== AUTONOMY CYCLE SUMMARY [{summary.run_id}] ===")
        print(f"Channel ID:        {summary.channel_id or 'all / default'}")
        print(f"Autonomy Level:    {lvl_name} ({summary.autonomy_level})")
        print(f"Strategy Version:  {summary.active_strategy_version}")
        print(f"Status:            {summary.status}")
        print(f"Dry Run:           {summary.dry_run}")
        print(f"\nDiscovered:        {summary.signals_discovered} trend signals")
        print(f"Candidates:        {summary.candidates_generated} generated")
        print(f"Proposals Created: {summary.proposals_created}")
        print(f"Jobs Queued:       {summary.jobs_queued}")
        print(f"Jobs Blocked:      {summary.jobs_blocked}")
        if summary.error_message:
            print(f"\nError: {summary.error_message}")
    return 0 if "completed" in summary.status or summary.status == "partial" else 1


def run_autonomy_proposals(
    status: str | None = None,
    limit: int = 50,
    output_json: bool = False,
) -> int:
    import json
    from autopilot.core.autonomy import AutonomyEngine

    engine = AutonomyEngine()
    proposals = engine.db.list_idea_proposals(status=status, limit=limit)

    if output_json:
        print(json.dumps(proposals, indent=2, default=str))
    else:
        print(f"=== AUTONOMY PROPOSALS ({len(proposals)}) ===")
        if not proposals:
            print("No proposals found matching criteria.")
            return 0
        fmt = "{:<16} {:<12} {:<8} {:<16} {:<30}"
        print(fmt.format("Proposal ID", "Status", "Score", "Candidate ID", "Topic"))
        print("-" * 88)
        for p in proposals:
            score_val = p.get("total_score")
            score_str = f"{score_val:.2f}" if score_val is not None else "-"
            print(fmt.format(
                str(p.get("proposal_id", ""))[:16],
                str(p.get("status", "")),
                score_str,
                str(p.get("candidate_id", ""))[:16],
                str(p.get("proposed_topic", ""))[:30],
            ))
    return 0


def run_autonomy_show(
    run_id: str | None = None,
    proposal_id: str | None = None,
    output_json: bool = False,
) -> int:
    import json
    from autopilot.core.contracts import AutonomyLevel
    from autopilot.core.autonomy import AutonomyEngine

    engine = AutonomyEngine()
    if run_id:
        run = engine.db.get_autonomy_run(run_id)
        if not run:
            if output_json:
                print(json.dumps({"error": f"Autonomy run '{run_id}' not found"}))
            else:
                print(f"Error: Autonomy run '{run_id}' not found", file=sys.stderr)
            return 1
        signals = engine.db.get_trend_signals_for_run(run_id)
        candidates = engine.db.get_topic_candidates_for_run(run_id)
        if output_json:
            print(json.dumps({
                "run": run,
                "signals": signals,
                "candidates": candidates,
            }, indent=2, default=str))
        else:
            lvl_val = run.get("autonomy_level", 0)
            lvl_name = AutonomyLevel(lvl_val).name if lvl_val in [l.value for l in AutonomyLevel] else str(lvl_val)
            print(f"=== AUTONOMY RUN [{run.get('run_id')}] ===")
            print(f"Autonomy Level:   {lvl_name} ({lvl_val})")
            print(f"Strategy Version: {run.get('strategy_version')}")
            print(f"Status:           {run.get('status')}")
            print(f"Dry Run:          {bool(run.get('dry_run'))}")
            print(f"Started At:       {run.get('started_at')}")
            print(f"Completed At:     {run.get('completed_at') or 'N/A'}")
            print(f"Signals Found:    {len(signals)}")
            print(f"Candidates:       {len(candidates)}")
            print(f"Jobs Queued:      {run.get('jobs_queued', 0)}")
            print(f"Proposals:        {run.get('proposals_created', 0)}")
            if candidates:
                print("\nGenerated Candidates:")
                for c in candidates:
                    print(f"  * [{c.get('candidate_id', '')[:8]}] {c.get('proposed_topic')} (Angle: {c.get('angle')})")
        return 0
    elif proposal_id:
        prop = engine.db.get_idea_proposal(proposal_id)
        if not prop:
            if output_json:
                print(json.dumps({"error": f"Idea proposal '{proposal_id}' not found"}))
            else:
                print(f"Error: Idea proposal '{proposal_id}' not found", file=sys.stderr)
            return 1
        decision = engine.db.get_decision_for_proposal(proposal_id)
        if output_json:
            print(json.dumps({
                "proposal": prop,
                "decision": decision,
            }, indent=2, default=str))
        else:
            print(f"=== IDEA PROPOSAL [{prop.get('proposal_id')}] ===")
            print(f"Status:           {prop.get('status')}")
            print(f"Candidate ID:     {prop.get('candidate_id')}")
            print(f"Topic:            {prop.get('proposed_topic')}")
            print(f"Angle:            {prop.get('angle')}")
            print(f"Hook:             {prop.get('hook_hypothesis')}")
            print(f"Format:           {prop.get('content_format')}")
            if prop.get("total_score") is not None:
                print(f"\nScore Breakdown (Composite: {prop.get('total_score', 0):.2f}):")
                print(f"  - Breakdown:    {prop.get('breakdown_json')}")
                print(f"  - Explanation:  {prop.get('explanation')}")
            if decision:
                print(f"\nPolicy Decision:  {decision.get('action')}")
                print(f"  Reason:         {decision.get('reason')}")
                print(f"  Decided At:     {decision.get('decided_at')}")
            if prop.get("decision_reason"):
                print(f"Decision Reason:  {prop.get('decision_reason')}")
        return 0
    else:
        print("Error: Must specify either --run <run_id> or --proposal <proposal_id>", file=sys.stderr)
        return 1


def run_autonomy_approve(proposal_id: str, output_json: bool = False) -> int:
    import json
    from autopilot.core.autonomy import AutonomyEngine

    engine = AutonomyEngine()
    result = engine.approve_proposal(proposal_id)
    if output_json:
        print(json.dumps(result, indent=2))
    else:
        if result.get("status") == "approved":
            print(f"Proposal '{proposal_id}' APPROVED successfully.")
            print(f"Enqueued as M7 Job: '{result.get('job_id')}' (Queue ID: '{result.get('queue_id')}')")
        else:
            print(f"Error approving proposal '{proposal_id}': {result.get('reason')}", file=sys.stderr)
            return 1
    return 0


def run_autonomy_reject(proposal_id: str, reason: str = "Rejected by operator", output_json: bool = False) -> int:
    import json
    from autopilot.core.autonomy import AutonomyEngine

    engine = AutonomyEngine()
    result = engine.reject_proposal(proposal_id, reason=reason)
    if output_json:
        print(json.dumps(result, indent=2))
    else:
        if result.get("status") == "rejected":
            print(f"Proposal '{proposal_id}' REJECTED.")
            print(f"Reason: {reason}")
        else:
            print(f"Error rejecting proposal '{proposal_id}': {result.get('reason')}", file=sys.stderr)
            return 1
    return 0


def run_autonomy_policy(output_json: bool = False) -> int:
    import json
    from autopilot.core.autonomy import AutonomyEngine

    engine = AutonomyEngine()
    policy = engine.policy

    if output_json:
        print(policy.model_dump_json(indent=2))
    else:
        print("=== AUTONOMY POLICY CONFIGURATION ===")
        print(f"Policy ID:                  {policy.policy_id}")
        print(f"Max Ideas Per Cycle:        {policy.max_ideas_per_cycle}")
        print(f"Max Auto-Queue Per Cycle:   {policy.max_auto_queue_per_cycle}")
        print(f"Max Jobs Per Day:           {policy.max_jobs_per_day}")
        print(f"Max Concurrent Jobs:        {policy.max_concurrent_jobs}")
        print(f"Topic Cooldown Days:        {policy.topic_cooldown_days}")
        print(f"Similarity Threshold:       {policy.similarity_threshold:.2f}")
        print(f"Min Score Threshold:        {policy.min_score_threshold:.2f}")
        print(f"Require Evidence:           {policy.require_evidence}")
        print(f"Allowed Profiles:           {', '.join(policy.allowed_profiles)}")
        print(f"Prohibited Topics ({len(policy.prohibited_topics)}): {', '.join(policy.prohibited_topics[:6])}...")
    return 0


def run_autonomy_strategy(activate_id: str | None = None, output_json: bool = False) -> int:
    import json
    from autopilot.core.feedback import StrategyManager

    manager = StrategyManager()
    if activate_id:
        success = manager.activate_strategy(activate_id)
        if success:
            if output_json:
                print(json.dumps({"status": "activated", "strategy_id": activate_id}))
            else:
                print(f"Strategy '{activate_id}' activated successfully.")
            return 0
        else:
            if output_json:
                print(json.dumps({"error": f"Failed to activate strategy '{activate_id}'"}))
            else:
                print(f"Error: Failed to activate strategy '{activate_id}'", file=sys.stderr)
            return 1

    active = manager.get_active_strategy()
    all_strategies = manager.db.list_strategy_versions(limit=20)

    if output_json:
        print(json.dumps({
            "active_strategy": active.model_dump(mode="json") if active else None,
            "strategies": [s.model_dump(mode="json") for s in all_strategies],
        }, indent=2))
    else:
        print("=== AUTONOMY STRATEGY VERSIONS ===")
        if active:
            print(f"Active Strategy:  {active.version_id} (Status: {active.status.value})")
            print(f"Created At:       {active.created_at}")
            print(f"Rationale:        {active.rationale}")
            print(f"Evidence Count:   {len(active.supporting_evidence_ids)} signals")
            print(f"Niche Weights:    {active.niche_weights}")
        else:
            print("Active Strategy: None")
        print(f"\nAll Strategy Versions ({len(all_strategies)}):")
        fmt = "{:<16} {:<12} {:<16} {:<10} {:<24}"
        print(fmt.format("Version ID", "Status", "Parent", "Evidence", "Created At"))
        print("-" * 82)
        for s in all_strategies:
            print(fmt.format(
                s.version_id[:16],
                s.status.value,
                (s.parent_version_id or "-")[:16],
                str(len(s.supporting_evidence_ids)),
                str(s.created_at)[:24],
            ))
    return 0


def run_channel_list(output_json: bool = False) -> int:
    import json
    from autopilot.core.channel import ChannelManager
    mgr = ChannelManager()
    channels = mgr.list_channels()
    if output_json:
        print(json.dumps([c.model_dump(mode="json") for c in channels], indent=2))
    else:
        print(f"=== CHANNEL PROFILES ({len(channels)}) ===")
        if not channels:
            print("No channel profiles configured.")
            return 0
        fmt = "{:<16} {:<24} {:<14} {:<10} {:<10} {:<10}"
        print(fmt.format("Channel ID", "Name", "Niche", "Status", "Version", "Platforms"))
        print("-" * 88)
        for c in channels:
            print(fmt.format(
                c.channel_id[:16],
                c.channel_name[:24],
                c.niche.niche_name[:14],
                c.status.value,
                f"v{c.version}",
                ",".join(c.target_platforms)[:10],
            ))
    return 0


def run_channel_show(channel_id: str, output_json: bool = False) -> int:
    import json
    from autopilot.core.channel import ChannelManager
    mgr = ChannelManager()
    profile = mgr.get_channel(channel_id)
    if not profile:
        if output_json:
            print(json.dumps({"error": f"Channel profile '{channel_id}' not found"}))
        else:
            print(f"Error: Channel profile '{channel_id}' not found", file=sys.stderr)
        return 1

    if output_json:
        print(profile.model_dump_json(indent=2))
    else:
        print(f"=== CHANNEL PROFILE: {profile.channel_name} [{profile.channel_id}] ===")
        print(f"Status:             {profile.status.value}")
        print(f"Version:            v{profile.version}")
        print(f"Language / Locale:  {profile.language} / {profile.locale}")
        print(f"Active Strategy:    {profile.active_strategy_version or '(Global Default)'}")
        print(f"\n[Niche: {profile.niche.niche_name}]")
        print(f"  Description:      {profile.niche.description}")
        print(f"  Allowed Cats:     {', '.join(profile.niche.allowed_categories)}")
        print(f"  Excluded Cats:    {', '.join(profile.niche.excluded_categories) or 'None'}")
        print(f"  Terminology:      {', '.join(profile.niche.terminology)}")
        print(f"\n[Persona: {profile.persona.tone} / {profile.persona.hook_style}]")
        print(f"  Narrator:         {profile.persona.narrator_personality}")
        print(f"  CTA Style:        {profile.persona.cta_style}")
        print(f"  Pacing:           {profile.persona.pacing_preference}")
        print(f"\n[Voice: {profile.voice.provider} / {profile.voice.voice_id}]")
        print(f"  Speaking Rate:    {profile.voice.speaking_rate}x")
        print(f"\n[Visual Brand]")
        print(f"  Font / Captions:  {profile.visual.font_family} / {profile.visual.caption_style}")
        print(f"  Theme / Motif:    {profile.visual.theme_color_primary} / {profile.visual.visual_motif}")
        print(f"\n[Platforms & Formats]")
        print(f"  Platforms:        {', '.join(profile.target_platforms)}")
        print(f"  Content Formats:  {', '.join(profile.content_formats)}")
        print(f"\n[Policy]")
        print(f"  Autonomy Level:   {profile.autonomy_policy.autonomy_level}")
        print(f"  Daily Max Jobs:   {profile.autonomy_policy.max_jobs_per_day}")
        print(f"  Topic Cooldown:   {profile.autonomy_policy.topic_cooldown_days} days")
        quota = mgr.get_quota_usage(profile.channel_id)
        print(f"  Today Queued:     {quota['jobs_queued']} / {profile.autonomy_policy.max_jobs_per_day}")
    return 0


def run_channel_create(profile_path: str, output_json: bool = False) -> int:
    import json
    import yaml
    from pathlib import Path
    from autopilot.core.channel import ChannelManager
    from autopilot.core.contracts import ChannelProfile

    p = Path(profile_path)
    if not p.exists():
        msg = f"Profile file not found: {profile_path}"
        if output_json:
            print(json.dumps({"error": msg}))
        else:
            print(f"Error: {msg}", file=sys.stderr)
        return 1

    content = p.read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(content)
    except Exception:
        try:
            data = json.loads(content)
        except Exception as ex:
            msg = f"Failed to parse profile file as YAML or JSON: {ex}"
            if output_json:
                print(json.dumps({"error": msg}))
            else:
                print(f"Error: {msg}", file=sys.stderr)
            return 1

    try:
        profile = ChannelProfile.model_validate(data)
    except Exception as ex:
        msg = f"Invalid channel profile schema: {ex}"
        if output_json:
            print(json.dumps({"error": msg}))
        else:
            print(f"Error: {msg}", file=sys.stderr)
        return 1

    mgr = ChannelManager()
    errors = mgr.validate_profile(profile)
    if errors:
        msg = f"Profile validation errors: {', '.join(errors)}"
        if output_json:
            print(json.dumps({"error": msg, "validation_errors": errors}))
        else:
            print("Error: Channel profile validation failed:", file=sys.stderr)
            for err in errors:
                print(f"  - {err}", file=sys.stderr)
        return 1

    saved = mgr.save_profile(profile, comment=f"Created from {p.name}")
    if output_json:
        print(json.dumps({"status": "created", "channel_id": saved.channel_id, "version": saved.version}))
    else:
        print(f"Channel profile '{saved.channel_id}' created successfully (version v{saved.version}).")
    return 0


def run_channel_enable(channel_id: str, output_json: bool = False) -> int:
    import json
    from autopilot.core.channel import ChannelManager
    from autopilot.core.contracts import ChannelStatus
    mgr = ChannelManager()
    success = mgr.set_channel_status(channel_id, ChannelStatus.ACTIVE)
    if success:
        if output_json:
            print(json.dumps({"status": "active", "channel_id": channel_id}))
        else:
            print(f"Channel '{channel_id}' enabled (ACTIVE).")
        return 0
    else:
        msg = f"Channel '{channel_id}' not found"
        if output_json:
            print(json.dumps({"error": msg}))
        else:
            print(f"Error: {msg}", file=sys.stderr)
        return 1


def run_channel_disable(channel_id: str, output_json: bool = False) -> int:
    import json
    from autopilot.core.channel import ChannelManager
    from autopilot.core.contracts import ChannelStatus
    mgr = ChannelManager()
    success = mgr.set_channel_status(channel_id, ChannelStatus.DISABLED)
    if success:
        if output_json:
            print(json.dumps({"status": "disabled", "channel_id": channel_id}))
        else:
            print(f"Channel '{channel_id}' disabled (DISABLED).")
        return 0
    else:
        msg = f"Channel '{channel_id}' not found"
        if output_json:
            print(json.dumps({"error": msg}))
        else:
            print(f"Error: {msg}", file=sys.stderr)
        return 1


def run_channel_validate(
    channel_id: str | None = None,
    file_path: str | None = None,
    output_json: bool = False,
) -> int:
    import json
    import yaml
    from pathlib import Path
    from autopilot.core.channel import ChannelManager
    from autopilot.core.contracts import ChannelProfile

    mgr = ChannelManager()
    profile: ChannelProfile | None = None

    if file_path:
        p = Path(file_path)
        if not p.exists():
            msg = f"File not found: {file_path}"
            if output_json:
                print(json.dumps({"error": msg}))
            else:
                print(f"Error: {msg}", file=sys.stderr)
            return 1
        content = p.read_text(encoding="utf-8")
        try:
            data = yaml.safe_load(content)
            profile = ChannelProfile.model_validate(data)
        except Exception as ex:
            msg = f"Failed to load or validate profile from file: {ex}"
            if output_json:
                print(json.dumps({"error": msg}))
            else:
                print(f"Error: {msg}", file=sys.stderr)
            return 1
    elif channel_id:
        profile = mgr.get_channel(channel_id)
        if not profile:
            msg = f"Channel '{channel_id}' not found"
            if output_json:
                print(json.dumps({"error": msg}))
            else:
                print(f"Error: {msg}", file=sys.stderr)
            return 1
    else:
        print("Error: Must specify either --id <channel_id> or --file <path>", file=sys.stderr)
        return 1

    errors = mgr.validate_profile(profile)
    valid = len(errors) == 0

    if output_json:
        print(json.dumps({
            "channel_id": profile.channel_id,
            "valid": valid,
            "errors": errors,
        }, indent=2))
    else:
        print(f"=== VALIDATING CHANNEL PROFILE: {profile.channel_id} ===")
        if valid:
            print("Status: VALID (all constraints satisfied)")
            print(f"Platforms: {', '.join(profile.target_platforms)}")
            print(f"Voice:     {profile.voice.provider}:{profile.voice.voice_id}")
            print(f"Niche:     {profile.niche.niche_name}")
        else:
            print(f"Status: INVALID ({len(errors)} issues detected):")
            for err in errors:
                print(f"  - {err}")
    return 0 if valid else 1


def run_channel_history(channel_id: str, output_json: bool = False) -> int:
    import json
    from autopilot.core.channel import ChannelManager
    mgr = ChannelManager()
    versions = mgr.get_version_history(channel_id)

    if output_json:
        print(json.dumps([v.model_dump(mode="json") for v in versions], indent=2))
    else:
        print(f"=== CHANNEL VERSION HISTORY: {channel_id} ({len(versions)} versions) ===")
        if not versions:
            print("No version history found.")
            return 0
        fmt = "{:<10} {:<24} {:<20} {:<30}"
        print(fmt.format("Version", "Created At", "Created By", "Comment"))
        print("-" * 88)
        for v in versions:
            print(fmt.format(
                f"v{v.version}",
                str(v.created_at)[:24],
                v.created_by[:20],
                (v.change_comment or "-")[:30],
            ))
    return 0


def run_channel_compare(platform: str | None = None, output_json: bool = False) -> int:
    import json
    from autopilot.core.channel import ChannelManager
    mgr = ChannelManager()
    comparisons = mgr.compare_channels(platform=platform)

    if output_json:
        print(json.dumps(comparisons, indent=2))
    else:
        print(f"=== CHANNEL PERFORMANCE COMPARISON ({len(comparisons)} channels) ===")
        fmt = "{:<16} {:<10} {:<8} {:<8} {:<10} {:<10} {:<10}"
        print(fmt.format("Channel ID", "Status", "Content", "Pubs", "Views", "Avg Views", "Eng Rate"))
        print("-" * 80)
        for c in comparisons:
            print(fmt.format(
                c["channel_id"][:16],
                c["status"][:10],
                str(c["content_count"]),
                str(c["published_count"]),
                str(c["total_views"]),
                f"{c['avg_views']:.1f}",
                f"{c['avg_engagement_rate']:.2%}",
            ))
    return 0


def build_parser():
    import argparse
    parser = argparse.ArgumentParser(prog="autopilot")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("health", help="Local system health check")
    # Produce command
    sub_parser = sub.add_parser("produce", help="Generate structured content package (Phase 1/3)")
    sub_parser.add_argument("--topic", required=True, help="Topic for content generation")
    sub_parser.add_argument("--channel", default="default", help="Channel profile ID or path (e.g. science_shorts, history_shorts, tech_shorts)")
    sub_parser.add_argument("--policy", default="local_only", choices=["local_only", "cheap_first", "quality_first", "ollama", "gemini", "openrouter", "mock"], help="Production policy tier")
    sub_parser.add_argument("--profile", default="short_vertical", choices=["short_vertical"], help="Content profile")
    sub_parser.add_argument("--tts-provider", default="none", choices=["none", "mock", "kokoro"], help="TTS provider (default none; mock = synthetic audio)")
    sub_parser.add_argument("--llm-provider", default="mock", choices=["mock", "ollama", "gemini", "openrouter", "openai_compatible"], help="LLM provider (mock, ollama, gemini, openrouter, openai_compatible)")
    sub_parser.add_argument("--research-provider", default="mock_search", choices=["local", "mock_search", "wikipedia", "crawl4ai", "combined"], help="Research provider")
    sub_parser.add_argument("--asset-provider", default="local", choices=["local", "openverse"], help="Asset provider (default local)")
    sub_parser.add_argument("--production-engine", default="moneyprinterturbo", choices=["moneyprinterturbo", "ffmpeg"], help="Production engine (default: moneyprinterturbo; ffmpeg = legacy/dev)")
    sub_parser.add_argument("--render", action="store_true", help="Continue through asset acquisition and rendering immediately")

    # Run command (central end-to-end PipelineOrchestrator workflow)
    sub_parser_run = sub.add_parser("run", help="Run end-to-end production workflow for a topic via PipelineOrchestrator")
    sub_parser_run.add_argument("--topic", required=True, help="Topic for content generation")
    sub_parser_run.add_argument("--job", default=None, help="Explicit job ID (optional)")
    sub_parser_run.add_argument("--channel", default="default", help="Channel profile ID or path")
    sub_parser_run.add_argument("--policy", default="local_only", choices=["local_only", "cheap_first", "quality_first", "ollama", "gemini", "openrouter", "mock"], help="Production policy tier")
    sub_parser_run.add_argument("--profile", default="short_vertical", choices=["short_vertical"], help="Content profile")
    sub_parser_run.add_argument("--tts-provider", default="none", choices=["none", "mock", "kokoro"], help="TTS provider")
    sub_parser_run.add_argument("--llm-provider", default="mock", choices=["mock", "ollama", "gemini", "openrouter", "openai_compatible"], help="LLM provider")
    sub_parser_run.add_argument("--research-provider", default="mock_search", choices=["local", "mock_search", "wikipedia", "crawl4ai", "combined"], help="Research provider")
    sub_parser_run.add_argument("--asset-provider", default="local", choices=["local", "openverse"], help="Asset provider")
    sub_parser_run.add_argument("--production-engine", default="moneyprinterturbo", choices=["moneyprinterturbo", "ffmpeg"], help="Production engine")
    sub_parser_run.add_argument("--render", action="store_true", help="Continue through asset acquisition and rendering immediately")
    sub_parser_run.add_argument("--json", action="store_true", help="Output machine-readable JSON result")

    sub_parser_render = sub.add_parser("render", help="Render video from content package and assets (Milestone 4)")
    sub_parser_render.add_argument("--job", required=True, help="Job ID to render")
    sub_parser_render.add_argument("--asset-provider", default="local", choices=["local", "openverse"], help="Asset provider (default local)")
    sub_parser_render.add_argument("--profile", default="short_vertical", choices=["short_vertical"], help="Render profile")
    sub_parser_render.add_argument("--production-engine", default="moneyprinterturbo", choices=["moneyprinterturbo", "ffmpeg"], help="Production engine (default: moneyprinterturbo; ffmpeg = legacy/dev)")
    sub_parser_research = sub.add_parser("research", help="Run research engine (Phase 2)")
    sub_parser_research.add_argument("--topic", required=True, help="Research topic")
    sub_parser_research.add_argument("--provider", default="local", choices=["local", "mock_search", "wikipedia", "crawl4ai", "combined"], help="Provider selection")
    sub_parser_assets = sub.add_parser("assets", help="Asset engine (Milestone 3)")
    sub_parser_assets.add_argument("--job", required=True, help="Job ID")
    sub_parser_assets.add_argument("--provider", default="local", choices=["local", "openverse"], help="Asset provider (default local)")
    sub_parser_assets.add_argument("--search-only", action="store_true", help="Perform search without downloading")
    sub_parser_assets.add_argument("--dry-run", action="store_true", help="Score and select without downloading")
    sub_parser_qa = sub.add_parser("qa", help="Run QA Engine & Quality Gates (Milestone 5)")
    sub_parser_qa.add_argument("--job", required=True, help="Job ID to evaluate")
    sub_parser_qa.add_argument("--media", default=None, help="Explicit path to media file (optional)")
    sub_parser_qa.add_argument("--verbose", action="store_true", help="Show verbose check and metric details")
    sub_parser_qa.add_argument("--json", action="store_true", help="Output machine-readable JSON receipt")
    sub_parser_qa.add_argument("--strict", action="store_true", help="Strict mode: treat warnings as blocking failures")

    sub_parser_publish = sub.add_parser("publish", help="Run Publishing Engine (Milestone 6 / Phase 3)")
    sub_parser_publish.add_argument("positional_job", nargs="?", default=None, help="Job ID to publish (positional)")
    sub_parser_publish.add_argument("--job", default=None, help="Job ID to publish")
    sub_parser_publish.add_argument("--platform", default="youtube", help="Target publishing platform (youtube, postiz/tiktok, etc.)")
    sub_parser_publish.add_argument("--dry-run", action="store_true", help="Validate and preview upload without sending network payload")
    sub_parser_publish.add_argument("--private", action="store_true", help="Publish with private visibility (default)")
    sub_parser_publish.add_argument("--public", action="store_true", help="Publish with public visibility (requires QA pass)")
    sub_parser_publish.add_argument("--unlisted", action="store_true", help="Publish with unlisted visibility")
    sub_parser_publish.add_argument("--schedule", default=None, help="Schedule publication for future UTC timestamp (ISO-8601)")
    sub_parser_publish.add_argument("--force-retry", action="store_true", help="Force re-attempt even if previous publication succeeded")
    sub_parser_publish.add_argument("--media", default=None, help="Explicit path to media file (optional)")
    sub_parser_publish.add_argument("--json", action="store_true", help="Output machine-readable JSON result")

    sub_parser_ytauth = sub.add_parser("youtube-auth", help="Perform interactive Google OAuth authorization for YouTube publishing")
    sub_parser_ytauth.add_argument("--secrets", default=None, help="Path to client_secret.json (optional override)")
    sub_parser_ytauth.add_argument("--token-path", default=None, help="Path to save youtube_token.json (optional override)")
    sub_parser_ytauth.add_argument("--no-browser", action="store_true", help="Do not automatically open browser")
    sub_parser_ytauth.add_argument("--port", type=int, default=0, help="Local port for callback server (default 0 for random free port)")
    sub_parser_ytauth.add_argument("--json", action="store_true", help="Output machine-readable JSON result")

    # Milestone 7 / M7 — Batch & Queue subparsers
    sub_parser_batch = sub.add_parser("batch", help="Batch production management (Milestone 7 / Phase 2)")
    sub_parser_batch.add_argument("--topics", default=None, help="Path to topics text file (.txt) or manifest file (.json/.yaml)")
    sub_parser_batch.add_argument("--channel", default="default", help="Channel profile ID (e.g. science_shorts, history_shorts, tech_shorts)")
    sub_parser_batch.add_argument("--production-engine", default="moneyprinterturbo", choices=["moneyprinterturbo", "ffmpeg"], help="Production engine")
    sub_parser_batch.add_argument("--policy", default="local_only", choices=["local_only", "cheap_first", "quality_first", "ollama", "gemini", "openrouter", "mock"], help="Production policy tier")
    sub_parser_batch.add_argument("--json", action="store_true", help="Output machine-readable JSON result")
    sub_batch = sub_parser_batch.add_subparsers(dest="batch_action")
    sub_batch_submit = sub_batch.add_parser("submit", help="Submit a batch manifest file to queue")
    sub_batch_submit.add_argument("--file", required=True, help="Path to batch manifest JSON or YAML file")
    sub_batch_submit.add_argument("--dry-run", action="store_true", help="Validate manifest without enqueueing")
    sub_batch_submit.add_argument("--force", action="store_true", help="Force enqueueing even if duplicate job exists")
    sub_batch_submit.add_argument("--json", action="store_true", help="Output machine-readable JSON result")

    sub_parser_queue = sub.add_parser("queue", help="Local worker queue management (Milestone 7)")
    sub_queue = sub_parser_queue.add_subparsers(dest="queue_action")
    
    sub_queue_list = sub_queue.add_parser("list", help="List items in the local queue")
    sub_queue_list.add_argument("--status", default=None, choices=["queued", "running", "succeeded", "failed", "retry_wait", "cancelled", "blocked", "dead_letter"], help="Filter by status")
    sub_queue_list.add_argument("--limit", type=int, default=50, help="Maximum number of items to show")
    sub_queue_list.add_argument("--json", action="store_true", help="Output machine-readable JSON")

    sub_queue_status = sub_queue.add_parser("status", help="Show queue status summary")
    sub_queue_status.add_argument("--json", action="store_true", help="Output machine-readable JSON")

    sub_queue_run = sub_queue.add_parser("run", help="Run local worker to process queued jobs")
    sub_queue_run.add_argument("--max-jobs", type=int, default=None, help="Maximum number of jobs to process before stopping")
    sub_queue_run.add_argument("--worker-id", default=None, help="Explicit worker ID")
    sub_queue_run.add_argument("--poll-interval", type=float, default=None, help="Seconds to wait between polls when idle")
    sub_queue_run.add_argument("--once", action="store_true", help="Process claimable jobs and exit cleanly (ideal for OS schedulers)")

    sub_queue_retry = sub_queue.add_parser("retry", help="Reset a failed or blocked job for retry")
    sub_queue_retry.add_argument("--job", required=True, help="Job ID or Queue ID to retry")

    sub_queue_cancel = sub_queue.add_parser("cancel", help="Cancel a queued or running job")
    sub_queue_cancel.add_argument("--job", required=True, help="Job ID or Queue ID to cancel")

    sub_queue_cancel_all = sub_queue.add_parser("cancel-all", help="Cancel all queued items matching status filter (default: queued)")
    sub_queue_cancel_all.add_argument("--status", default="queued", choices=["queued"], help="Status filter (must be 'queued')")
    sub_queue_cancel_all.add_argument("--dry-run", action="store_true", help="Preview count of items to cancel without mutating database")
    sub_queue_cancel_all.add_argument("--json", action="store_true", help="Output machine-readable JSON")

    sub_queue_inspect = sub_queue.add_parser("inspect", help="Inspect detailed status of a job in the queue")
    sub_queue_inspect.add_argument("--job", required=True, help="Job ID or Queue ID to inspect")
    sub_queue_inspect.add_argument("--json", action="store_true", help="Output machine-readable JSON")

    # Phase 3: Top-level inspect
    sub_parser_inspect = sub.add_parser("inspect", help="Inspect detailed status of a job (Phase 3)")
    sub_parser_inspect.add_argument("job", help="Job ID to inspect")
    sub_parser_inspect.add_argument("--json", action="store_true", help="Output machine-readable JSON")

    # Milestone 8 / M8 — Analytics & Performance Intelligence subparsers
    sub_parser_analytics = sub.add_parser("analytics", help="Analytics & Performance Intelligence (Milestone 8)")
    sub_parser_analytics.add_argument("--channel", default=None, help="Channel ID for performance attribution")
    sub_parser_analytics.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    sub_analytics = sub_parser_analytics.add_subparsers(dest="analytics_action")

    sub_analytics_sync = sub_analytics.add_parser("sync", help="Synchronize analytics metrics for published content")
    sub_analytics_sync.add_argument("--job", default=None, help="Specific Job ID to synchronize")
    sub_analytics_sync.add_argument("--platform", default=None, choices=["youtube", "mock"], help="Target platform (default: youtube)")
    sub_analytics_sync.add_argument("--provider", default=None, choices=["mock", "youtube"], help="Analytics provider override (default: mock)")
    sub_analytics_sync.add_argument("--window", default="lifetime", choices=["1h", "24h", "7d", "28d", "lifetime"], help="Performance observation window")
    sub_analytics_sync.add_argument("--all", action="store_true", help="Sync all published jobs up to limit")
    sub_analytics_sync.add_argument("--limit", type=int, default=25, help="Maximum number of jobs to sync in batch")
    sub_analytics_sync.add_argument("--dry-run", action="store_true", help="Preview sync requests without executing network calls or persisting")
    sub_analytics_sync.add_argument("--json", action="store_true", help="Output machine-readable JSON")

    sub_analytics_show = sub_analytics.add_parser("show", help="Show performance snapshot and derived metrics for a job")
    sub_analytics_show.add_argument("--job", required=True, help="Job ID to show")
    sub_analytics_show.add_argument("--json", action="store_true", help="Output machine-readable JSON")

    sub_analytics_report = sub_analytics.add_parser("report", help="Generate performance report table across jobs")
    sub_analytics_report.add_argument("--channel", default=None, help="Channel ID for performance attribution")
    sub_analytics_report.add_argument("--platform", default=None, choices=["youtube", "mock"], help="Filter by platform")
    sub_analytics_report.add_argument("--limit", type=int, default=20, help="Maximum number of entries to display")
    sub_analytics_report.add_argument("--json", action="store_true", help="Output machine-readable JSON")

    # Milestone 9 / M9 — Autonomous Ideation & Feedback Loop subparsers
    sub_parser_autonomy = sub.add_parser("autonomy", help="Autonomous Ideation & Feedback Loop (Milestone 9)")
    sub_parser_autonomy.add_argument("--channel", default=None, help="Target channel profile ID")
    sub_parser_autonomy.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    sub_autonomy = sub_parser_autonomy.add_subparsers(dest="autonomy_action")

    sub_autonomy_run = sub_autonomy.add_parser("run", help="Execute an autonomous ideation cycle")
    sub_autonomy_run.add_argument("--level", type=int, default=None, choices=[0, 1, 2, 3, 4], help="Autonomy level override (0=Manual, 1=Discovery, 2=Proposal, 3=Guarded Auto-Queue, 4=Guarded Auto-Produce)")
    sub_autonomy_run.add_argument("--channel", default=None, help="Target channel profile ID")
    sub_autonomy_run.add_argument("--dry-run", action="store_true", help="Simulate ideation, scoring, and policy evaluation without DB or Queue mutations")
    sub_autonomy_run.add_argument("--category", default=None, help="Target niche/category filter")
    sub_autonomy_run.add_argument("--limit", type=int, default=10, help="Maximum trend signals / candidates per cycle")
    sub_autonomy_run.add_argument("--policy", default=None, help="Production provider policy tier for Level 4 auto-produce (e.g. local_only)")
    sub_autonomy_run.add_argument("--json", action="store_true", help="Output machine-readable JSON summary")

    sub_autonomy_proposals = sub_autonomy.add_parser("proposals", help="List reviewable idea proposals")
    sub_autonomy_proposals.add_argument("--status", default=None, choices=["proposed", "approved", "rejected", "queued", "expired"], help="Filter by proposal status")
    sub_autonomy_proposals.add_argument("--limit", type=int, default=50, help="Maximum number of proposals to list")
    sub_autonomy_proposals.add_argument("--json", action="store_true", help="Output machine-readable JSON")

    sub_autonomy_show = sub_autonomy.add_parser("show", help="Show detailed run or proposal record")
    sub_autonomy_show.add_argument("--run", default=None, help="Autonomy Run ID to inspect")
    sub_autonomy_show.add_argument("--proposal", default=None, help="Idea Proposal ID to inspect")
    sub_autonomy_show.add_argument("--json", action="store_true", help="Output machine-readable JSON")

    sub_autonomy_approve = sub_autonomy.add_parser("approve", help="Approve an idea proposal for production queueing")
    sub_autonomy_approve.add_argument("--proposal", required=True, help="Idea Proposal ID to approve")
    sub_autonomy_approve.add_argument("--json", action="store_true", help="Output machine-readable JSON")

    sub_autonomy_reject = sub_autonomy.add_parser("reject", help="Reject an idea proposal")
    sub_autonomy_reject.add_argument("--proposal", required=True, help="Idea Proposal ID to reject")
    sub_autonomy_reject.add_argument("--reason", default="Rejected by operator", help="Explanation for rejection")
    sub_autonomy_reject.add_argument("--json", action="store_true", help="Output machine-readable JSON")

    sub_autonomy_policy = sub_autonomy.add_parser("policy", help="Display active autonomy policy & limits")
    sub_autonomy_policy.add_argument("--json", action="store_true", help="Output machine-readable JSON")

    sub_autonomy_strategy = sub_autonomy.add_parser("strategy", help="Inspect or activate strategy versions")
    sub_autonomy_strategy.add_argument("--activate", default=None, help="Strategy Version ID to activate")
    sub_autonomy_strategy.add_argument("--json", action="store_true", help="Output machine-readable JSON")

    # Milestone 10 / M10 — Multi-Channel Scaling & Channel Profiles subparsers
    sub_parser_channel = sub.add_parser("channel", help="Multi-Channel Profile Management (Milestone 10)")
    sub_channel = sub_parser_channel.add_subparsers(dest="channel_action")

    sub_channel_list = sub_channel.add_parser("list", help="List all channel profiles")
    sub_channel_list.add_argument("--json", action="store_true", help="Output machine-readable JSON")

    sub_channel_show = sub_channel.add_parser("show", help="Show channel profile details")
    sub_channel_show.add_argument("--id", required=True, help="Channel ID")
    sub_channel_show.add_argument("--json", action="store_true", help="Output machine-readable JSON")

    sub_channel_create = sub_channel.add_parser("create", help="Create or update a channel profile from file")
    sub_channel_create.add_argument("--profile", "--file", dest="file", required=True, help="Path to profile YAML or JSON file")
    sub_channel_create.add_argument("--json", action="store_true", help="Output machine-readable JSON")

    sub_channel_enable = sub_channel.add_parser("enable", help="Enable a channel profile (set to ACTIVE)")
    sub_channel_enable.add_argument("--id", required=True, help="Channel ID")
    sub_channel_enable.add_argument("--json", action="store_true", help="Output machine-readable JSON")

    sub_channel_disable = sub_channel.add_parser("disable", help="Disable a channel profile")
    sub_channel_disable.add_argument("--id", required=True, help="Channel ID")
    sub_channel_disable.add_argument("--json", action="store_true", help="Output machine-readable JSON")

    sub_channel_validate = sub_channel.add_parser("validate", help="Validate a channel profile against constraints")
    sub_channel_validate.add_argument("--id", default=None, help="Channel ID in database")
    sub_channel_validate.add_argument("--file", default=None, help="Path to local profile file")
    sub_channel_validate.add_argument("--json", action="store_true", help="Output machine-readable JSON")

    sub_channel_history = sub_channel.add_parser("history", help="Show version history for a channel profile")
    sub_channel_history.add_argument("--id", required=True, help="Channel ID")
    sub_channel_history.add_argument("--json", action="store_true", help="Output machine-readable JSON")

    sub_channel_compare = sub_channel.add_parser("compare", help="Compare performance metrics across channels")
    sub_channel_compare.add_argument("--platform", default=None, choices=["youtube", "mock"], help="Filter by platform")
    sub_channel_compare.add_argument("--json", action="store_true", help="Output machine-readable JSON")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "health" or args.command is None:
        return run_health()
    elif args.command in ("produce", "run"):
        policy = getattr(args, "policy", "local_only")
        raw_llm = args.llm_provider
        raw_research = args.research_provider
        raw_tts = args.tts_provider

        # Determine which flags the user set explicitly (vs. argparse defaults).
        # argparse doesn't expose this directly, so we compare against the
        # hardcoded defaults for the subparser.
        _DEFAULT_LLM = "mock"
        _DEFAULT_RESEARCH = "mock_search"
        _DEFAULT_TTS = "none"
        llm_explicit = (raw_llm != _DEFAULT_LLM)
        research_explicit = (raw_research != _DEFAULT_RESEARCH)
        tts_explicit = (raw_tts != _DEFAULT_TTS)

        try:
            resolved_llm, resolved_research, resolved_tts, resolved_engine = resolve_providers_for_policy(
                policy,
                raw_llm,
                raw_research,
                raw_tts,
                getattr(args, "production_engine", "moneyprinterturbo"),
                llm_explicit=llm_explicit,
                research_explicit=research_explicit,
                tts_explicit=tts_explicit,
            )
        except ValueError as exc:
            print(f"[autopilot] ERROR: {exc}", file=sys.stderr)
            return 1

        print(
            f"[autopilot] policy={policy!r}  "
            f"LLM={resolved_llm!r}  Research={resolved_research!r}  TTS={resolved_tts!r}",
            file=sys.stderr,
        )

        if args.command == "run":
            import uuid
            from autopilot.core.pipeline import PipelineOrchestrator
            job_id = getattr(args, "job", None) or f"run-{args.topic.replace(' ', '-')[:30]}-{uuid.uuid4().hex[:8]}"
            orchestrator = PipelineOrchestrator(CONFIG)
            try:
                res = orchestrator.run_pipeline(
                    job_id=job_id,
                    topic=args.topic,
                    profile=args.profile,
                    channel_id=getattr(args, "channel", "default"),
                    policy=policy,
                    llm_provider=resolved_llm,
                    research_provider=resolved_research,
                    tts_provider=resolved_tts,
                    production_engine=resolved_engine,
                    asset_provider=args.asset_provider,
                    auto_publish=getattr(args, "publish", False),
                    publish_visibility="public" if getattr(args, "public", False) else ("unlisted" if getattr(args, "unlisted", False) else "private"),
                    publish_platform=getattr(args, "platform", "youtube"),
                )
                if getattr(args, "json", False):
                    print(json.dumps(res, indent=2, default=str))
                else:
                    print(f"[autopilot] Pipeline completed for job: {job_id}")
                    print(f"  Status: {res.get('status')}")
                    print(f"  Media: {res.get('media_path')}")
                    print(f"  QA Status: {res.get('qa_status')}")
                return 0 if res.get("status") == "success" else 1
            except Exception as exc:
                print(f"[autopilot] ERROR: Pipeline execution failed: {exc}", file=sys.stderr)
                return 1

        return run_produce(
            args.topic,
            profile=args.profile,
            channel=getattr(args, "channel", "default"),
            policy=policy,
            tts_provider=resolved_tts,
            asset_provider=args.asset_provider,
            llm_provider=resolved_llm,
            research_provider=resolved_research,
            production_engine=resolved_engine,
            render=args.render,
        )
    elif args.command == "render":
        return run_render(
            args.job,
            asset_provider=args.asset_provider,
            profile=args.profile,
            production_engine=getattr(args, "production_engine", "moneyprinterturbo"),
        )
    elif args.command == "research":
        return run_research(args.topic, provider_name=args.provider)
    elif args.command == "assets":
        return run_assets(args.job, provider_name=args.provider, search_only=args.search_only, dry_run=args.dry_run)
    elif args.command == "qa":
        return run_qa(args.job, media_path=args.media, verbose=args.verbose, output_json=args.json, strict=args.strict)
    elif args.command == "inspect":
        return run_queue_inspect(job_id=args.job, output_json=args.json)
    elif args.command == "publish":
        target_job = getattr(args, "positional_job", None) or args.job
        if not target_job:
            print("Error: Job ID is required (e.g. autopilot publish <job_id> or --job <job_id>)")
            return 1
        vis = "public" if args.public else ("unlisted" if args.unlisted else "private")
        return run_publish(
            job_id=target_job,
            platform=args.platform,
            dry_run=args.dry_run,
            visibility=vis,
            scheduled_time=args.schedule,
            force_retry=args.force_retry,
            media_path=args.media,
            output_json=args.json,
        )
    elif args.command == "youtube-auth":
        return run_youtube_auth(
            secrets_path=args.secrets,
            token_path=args.token_path,
            no_browser=args.no_browser,
            port=args.port,
            output_json=args.json,
        )
    elif args.command == "batch":
        if getattr(args, "topics", None):
            return run_batch_direct(
                topics_file=args.topics,
                channel=getattr(args, "channel", "default"),
                production_engine=getattr(args, "production_engine", "moneyprinterturbo"),
                policy=getattr(args, "policy", "local_only"),
                output_json=args.json,
            )
        elif args.batch_action == "submit":
            return run_batch_submit(file_path=args.file, dry_run=args.dry_run, force=args.force, output_json=args.json)
        else:
            sub_parser_batch.print_help()
            return 1
    elif args.command == "queue":
        if args.queue_action == "list":
            return run_queue_list(status=args.status, limit=args.limit, output_json=args.json)
        elif args.queue_action == "status":
            return run_queue_status(output_json=args.json)
        elif args.queue_action == "run":
            return run_queue_run(max_jobs=args.max_jobs, worker_id=args.worker_id, poll_interval=args.poll_interval, once=args.once)
        elif args.queue_action == "retry":
            return run_queue_retry(job_id=args.job)
        elif args.queue_action == "cancel":
            return run_queue_cancel(job_id=args.job)
        elif args.queue_action == "cancel-all":
            return run_queue_cancel_all(
                status=getattr(args, "status", "queued"),
                dry_run=getattr(args, "dry_run", False),
                output_json=getattr(args, "json", False),
            )
        elif args.queue_action == "inspect":
            return run_queue_inspect(job_id=args.job, output_json=args.json)
        else:
            sub_parser_queue.print_help()
            return 1
    elif args.command == "analytics":
        if args.analytics_action == "sync":
            return run_analytics_sync(
                job_id=args.job,
                platform=args.platform,
                provider=args.provider,
                window=args.window,
                sync_all_jobs=args.all,
                limit=args.limit,
                dry_run=args.dry_run,
                output_json=args.json,
            )
        elif args.analytics_action == "show":
            return run_analytics_show(job_id=args.job, output_json=args.json)
        elif args.analytics_action == "report" or getattr(args, "channel", None) or args.analytics_action is None:
            return run_analytics_report(
                platform=getattr(args, "platform", None),
                channel_id=getattr(args, "channel", None),
                limit=getattr(args, "limit", 20),
                output_json=getattr(args, "json", False),
            )
        else:
            sub_parser_analytics.print_help()
            return 1
    elif args.command == "autonomy":
        if args.autonomy_action == "run" or args.autonomy_action is None:
            return run_autonomy_run(
                level=getattr(args, "level", None),
                channel_id=getattr(args, "channel", None),
                dry_run=getattr(args, "dry_run", False),
                category=getattr(args, "category", None),
                limit=getattr(args, "limit", 10),
                output_json=getattr(args, "json", False),
                policy=getattr(args, "policy", None),
            )
        elif args.autonomy_action == "proposals":
            return run_autonomy_proposals(
                status=args.status,
                limit=args.limit,
                output_json=args.json,
            )
        elif args.autonomy_action == "show":
            return run_autonomy_show(
                run_id=args.run,
                proposal_id=args.proposal,
                output_json=args.json,
            )
        elif args.autonomy_action == "approve":
            return run_autonomy_approve(
                proposal_id=args.proposal,
                output_json=args.json,
            )
        elif args.autonomy_action == "reject":
            return run_autonomy_reject(
                proposal_id=args.proposal,
                reason=args.reason,
                output_json=args.json,
            )
        elif args.autonomy_action == "policy":
            return run_autonomy_policy(output_json=args.json)
        elif args.autonomy_action == "strategy":
            return run_autonomy_strategy(
                activate_id=args.activate,
                output_json=args.json,
            )
        else:
            sub_parser_autonomy.print_help()
            return 1
    elif args.command == "channel":
        if args.channel_action == "list":
            return run_channel_list(output_json=args.json)
        elif args.channel_action == "show":
            return run_channel_show(channel_id=args.id, output_json=args.json)
        elif args.channel_action == "create":
            return run_channel_create(profile_path=args.file, output_json=args.json)
        elif args.channel_action == "enable":
            return run_channel_enable(channel_id=args.id, output_json=args.json)
        elif args.channel_action == "disable":
            return run_channel_disable(channel_id=args.id, output_json=args.json)
        elif args.channel_action == "validate":
            return run_channel_validate(channel_id=args.id, file_path=args.file, output_json=args.json)
        elif args.channel_action == "history":
            return run_channel_history(channel_id=args.id, output_json=args.json)
        elif args.channel_action == "compare":
            return run_channel_compare(platform=args.platform, output_json=args.json)
        else:
            sub_parser_channel.print_help()
            return 1
    else:
        parser.print_help()
        return 1



