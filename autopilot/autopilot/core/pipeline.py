"""PIPELINE ORCHESTRATOR — Milestone 7.
Coordinates production stages (Research -> Script -> Voice -> Assets -> Render -> QA -> Optional Publish)
reusing existing tested subsystems, verifying artifact integrity, and supporting stage resumption.
"""
from __future__ import annotations
import json
import uuid
import hashlib
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, Dict, Any, Tuple, List

from autopilot.core.config import CONFIG, Config
from autopilot.core.contracts import (
    ContentItem, ScriptDocument, ScriptScene, ContentPackage,
    PublicationMetadata, ProvenanceRecord, RenderPlan, QAStatus, QAReport,
    PublishPlatform, PublishVisibility, PublishStatus,
    RegenerationDefectType,
)
from autopilot.core.state_machine import WorkflowState
from autopilot.core.logging import StructuredLogger
from autopilot.core.artifacts import (
    job_artifact_dir, script_path, provenance_path,
)
from autopilot.db.manager import DBManager
from autopilot.core.channel import ChannelManager
from autopilot.core.research_coordinator import ResearchCoordinator
from autopilot.providers.wikipedia_provider import WikipediaProvider
from autopilot.providers.mock_script import MockScriptProvider
from autopilot.providers.mock_search import MockSearchProvider
from autopilot.providers.mock_tts import MockTTSProvider
from autopilot.providers.tts_factory import get_tts_provider
from autopilot.core.quality import evaluate_script
from autopilot.core.audio_duration import extract_duration
from autopilot.core.asset_pipeline import process_scene_assets
from autopilot.core.asset_cache import compute_file_sha256
from autopilot.core.renderer import FFmpegRenderer
from autopilot.core.media_inspection import inspect_media
from autopilot.core.qa_engine import QAEngine, export_qa_artifacts
from autopilot.core.publisher import PublishingEngine
from autopilot.providers.production.factory import get_production_engine
from autopilot.providers.transcription.faster_whisper_engine import FasterWhisperEngine
from autopilot.core.contracts import ProductionRequest, TranscriptionRequest


class PipelineError(Exception):
    def __init__(self, message: str, category: str = "NON_RETRYABLE", stage: str = "UNKNOWN"):
        super().__init__(message)
        self.message = message
        self.category = category  # RETRYABLE, NON_RETRYABLE, BLOCKED
        self.stage = stage


def classify_qa_defect(findings: list[Any], report: Any = None) -> Tuple[RegenerationDefectType, str, str, str]:
    """Classifies the root defect from QA findings and returns (defect_type, reason, corrective_instruction, target_stage)."""
    for f in findings:
        msg = (getattr(f, "message", "") or "").lower()
        cat = (getattr(f, "category", "") or "").lower()
        check_name = (getattr(f, "check_id", "") or getattr(f, "check_name", "") or "").lower()
        if "list_structure" in msg or "list structure" in msg or "list_structure" in cat or "list_structure" in check_name:
            return (
                RegenerationDefectType.LIST_STRUCTURE_DEFECT,
                getattr(f, "message", "List structure defect: insufficient fact/content scenes for requested list count"),
                "Dedicate exactly one clear scene/fact unit to each requested item. Do not combine multiple list items into one scene.",
                "SCRIPT",
            )
        if "hook" in msg or "hook" in cat:
            return (
                RegenerationDefectType.HOOK_DEFECT,
                "Hook failed QA curiosity or structure criteria",
                "Rewrite the hook to be more compelling, intriguing, and direct without generic filler.",
                "SCRIPT",
            )
        if "duration" in msg or "duration" in cat or "length" in msg:
            return (
                RegenerationDefectType.DURATION_MISMATCH,
                "Video duration drifted outside target threshold",
                "Adjust narration length, pacing, and word count per scene to align with target duration.",
                "SCRIPT",
            )
        if "asset" in msg or "visual" in msg or "image" in msg or "broll" in msg:
            return (
                RegenerationDefectType.VISUAL_DEFECT,
                "Visual asset relevance or visual intent failed QA criteria",
                "Generate more concrete, photography-focused visual intents and precise stock search queries.",
                "SCRIPT",
            )
        if "audio" in msg or "silence" in msg or "level" in msg or "clipping" in msg:
            return (
                RegenerationDefectType.AUDIO_DEFECT,
                "Audio levels, silence, or synthesis quality failed QA criteria",
                "Re-synthesize audio segments with verified speech duration and clean boundaries.",
                "VOICE",
            )
        if "grounding" in msg or "fact" in msg or "source" in msg:
            return (
                RegenerationDefectType.GROUNDING_DEFECT,
                "Factual claims or sources failed verification against research",
                "Re-ground all assertions strictly in the verified research evidence without unverified claims.",
                "SCRIPT",
            )

    return (
        RegenerationDefectType.GENERAL_QA_DEFECT,
        "General QA quality gate rejection",
        "Regenerate script, pacing, and asset queries adhering strictly to channel guidelines.",
        "SCRIPT",
    )


class PipelineOrchestrator:
    """Executes the full pipeline for a job, resuming cleanly from the last valid stage."""

    def __init__(self, config: Config | None = None, db: DBManager | None = None):
        self.config = config or CONFIG
        self.db = db or DBManager(self.config.db_path)
        self.db.init_schema()

    def run_pipeline(
        self,
        job_id: str,
        topic: str,
        profile: str = "short_vertical",
        priority: int = 2,
        auto_publish: bool = False,
        publish_visibility: str = "private",
        publish_platform: str = "youtube",
        tts_provider: str = "mock",
        asset_provider: str = "local",
        on_stage_progress = None,
        channel_id: str = "default",
        llm_provider: str = "mock",
        research_provider: str = "wikipedia",
        production_engine: str | None = None,
        max_regeneration_attempts: int = 3,
        attempt_number: int = 1,
        corrective_instructions: Optional[str] = None,
        regeneration_reason: Optional[str] = None,
        policy: str = "local_only",
    ) -> Dict[str, Any]:
        """Runs the pipeline for the given job.
        
        If valid artifacts already exist for earlier stages, skips them (resume behavior).
        """
        # Resolve channel profile
        channel_mgr = ChannelManager(self.db)
        channel_profile_obj = channel_mgr.get_or_create_channel(channel_id)

        target_production_engine = (production_engine or getattr(channel_profile_obj, "production_engine", None) or self.config.default_production_engine).lower().strip()
        self.db.create_job(job_id=job_id, channel_id=channel_id, topic=topic)
        logger = StructuredLogger(job_id=job_id, stage="pipeline")
        logger.info(
            "pipeline_started",
            details={
                "topic": topic,
                "profile": profile,
                "channel_id": channel_id,
                "attempt": attempt_number,
                "max_attempts": max_regeneration_attempts,
            },
        )

        base_dir = self.config.get_artifacts_dir()
        art_dir = job_artifact_dir(job_id, base_dir=base_dir)
        art_dir.mkdir(parents=True, exist_ok=True)

        # --------------------------------------------------------------
        # 1. RESEARCH STAGE
        # --------------------------------------------------------------
        if on_stage_progress:
            on_stage_progress("RESEARCH")

        # Check if research already exists in DB
        existing_report = None
        try:
            existing_report = self.db.get_latest_research_report_for_topic(topic, provider=research_provider)
            if not existing_report:
                existing_report = self.db.get_latest_research_report_for_topic(topic, provider=None)
        except Exception:
            pass

        if not existing_report:
            try:
                self.db.update_job_status(job_id, WorkflowState.RESEARCHING.value)
                coord = ResearchCoordinator(
                    wikipedia_provider=WikipediaProvider(),
                    mock_provider=MockSearchProvider(),
                )
                strat = "wikipedia_first"
                if research_provider in ("crawl4ai", "crawl4ai_only"):
                    strat = "crawl4ai_only"
                elif research_provider == "combined":
                    strat = "combined"
                elif research_provider == "mock_search":
                    strat = "mock"

                bundle = coord.coordinate_research(
                    topic=topic,
                    strategy=strat,
                    max_results=5,
                    allow_mock=(research_provider == "mock_search"),
                )
                sources = bundle.sources
                if not sources and research_provider in ("wikipedia", "crawl4ai", "combined"):
                    raise PipelineError(
                        f"Research returned zero results for query '{topic}' via {strat}",
                        category="NON_RETRYABLE",
                        stage="RESEARCH",
                    )

                req_id = f"req-{job_id}"
                rep_id = f"rep-{job_id}"
                self.db.create_research_request(req_id, topic)
                self.db.save_research_report(
                    rep_id,
                    req_id,
                    topic,
                    status="completed",
                    summary=bundle.summary or f"Discovered {len(sources)} sources for '{topic}' via {strat}",
                    provenance_json=json.dumps({"strategy": strat, "provider": research_provider, "count": len(sources)}),
                )
                for src in sources:
                    self.db.record_research_evidence(
                        evidence_id=src.evidence_id,
                        report_id=rep_id,
                        source_id=src.source_id,
                        snippet=src.excerpt,
                        relevance_score=src.confidence_score,
                        status="discovered",
                        provenance_json=json.dumps(src.provenance_metadata),
                    )
                self.db.update_job_status(job_id, WorkflowState.RESEARCHED.value)
                logger.info("stage_completed", details={"stage": "RESEARCH", "sources": len(sources), "strategy": strat})
            except PipelineError:
                raise
            except Exception as exc:
                self.db.update_job_status(job_id, WorkflowState.FAILED_RESEARCH.value)
                self.db.record_error(job_id, "RESEARCH", "research_failed", str(exc))
                raise PipelineError(f"Research failed: {exc}", category="RETRYABLE", stage="RESEARCH")
        else:
            logger.info("stage_resumed", details={"stage": "RESEARCH", "reason": "existing_research_found"})

        # --------------------------------------------------------------
        # 2. SCRIPT STAGE
        # --------------------------------------------------------------
        if on_stage_progress:
            on_stage_progress("SCRIPT")

        sp_path = script_path(job_id, "script.json", base_dir=base_dir)
        pkg_path = art_dir / "script" / "content_package.json"
        script: Optional[ScriptDocument] = None
        package: Optional[ContentPackage] = None

        if sp_path.exists() and pkg_path.exists():
            try:
                script = ScriptDocument.model_validate_json(sp_path.read_text(encoding="utf-8"))
                package = ContentPackage.model_validate_json(pkg_path.read_text(encoding="utf-8"))
                # Invalidate if requested LLM provider is incompatible with existing artifact
                if llm_provider == "openai_compatible" and getattr(package.provenance, "provider", "") != "openai_compatible":
                    script = None
                    package = None
                elif llm_provider == "mock" and getattr(package.provenance, "provider", "") != "mock_script":
                    script = None
                    package = None
                elif channel_id != "default" and script.generation_metadata and script.generation_metadata.get("channel_id") and script.generation_metadata.get("channel_id") != channel_id:
                    # Invalidate if channel profile was switched
                    script = None
                    package = None
                else:
                    self.db.update_job_status(job_id, WorkflowState.SCRIPTED.value)
                    logger.info("stage_resumed", details={"stage": "SCRIPT", "reason": "valid_script_artifact"})
            except Exception:
                script = None
                package = None

        if not script or not package:
            try:
                self.db.update_job_status(job_id, WorkflowState.SCRIPTING.value)
                research_report_obj = None
                try:
                    research_report_obj = self.db.get_latest_research_report_for_topic(topic, provider=research_provider)
                    if not research_report_obj:
                        research_report_obj = self.db.get_latest_research_report_for_topic(topic, provider=None)
                except Exception as ex:
                    logger.warning("failed_fetching_research_report", details={"error": str(ex)})

                from autopilot.providers.openai_llm_provider import get_llm_provider
                script_provider = get_llm_provider(llm_provider)

                script = script_provider.generate_script(
                    topic=topic,
                    content_id=job_id,
                    research_report=research_report_obj,
                    channel_profile=channel_profile_obj,
                    corrective_instructions=corrective_instructions,
                    regeneration_reason=regeneration_reason,
                    attempt_number=attempt_number,
                )

                quality = evaluate_script(script, topic=topic)
                if quality.overall == "fail":
                    list_defect = next((c for c in quality.checks if c.check_name == "list_structure" and c.status == "fail"), None)
                    if list_defect:
                        defect_type = RegenerationDefectType.LIST_STRUCTURE_DEFECT
                        defect_reason = list_defect.message
                        corrective_instruction = "Dedicate exactly one clear scene/fact unit to each requested item. Do not combine multiple list items into one scene."
                    else:
                        first_blocking = next((c for c in quality.checks if c.severity == "blocking" and c.status == "fail"), None)
                        defect_reason = first_blocking.message if first_blocking else f"Script quality failed with {quality.blocking_count} blocking issues"
                        defect_type = RegenerationDefectType.GENERAL_QA_DEFECT
                        corrective_instruction = "Fix script blocking issues and regenerate adhering strictly to channel guidelines."

                    if attempt_number < max_regeneration_attempts:
                        self.db.update_job_status(job_id, WorkflowState.REGENERATING.value)
                        logger.warning(
                            "script_regeneration_triggered",
                            details={
                                "attempt": attempt_number,
                                "max_attempts": max_regeneration_attempts,
                                "defect_type": defect_type.value,
                                "reason": defect_reason,
                                "instruction": corrective_instruction,
                                "target_stage": "SCRIPT",
                            },
                        )
                        regen_meta = {
                            "attempt": attempt_number,
                            "defect_type": defect_type.value,
                            "reason": defect_reason,
                            "instruction": corrective_instruction,
                            "target_stage": "SCRIPT",
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        }
                        regen_file = art_dir / "quality" / f"regeneration_attempt_{attempt_number}.json"
                        regen_file.parent.mkdir(parents=True, exist_ok=True)
                        regen_file.write_text(json.dumps(regen_meta, indent=2), encoding="utf-8")

                        sp_path.unlink(missing_ok=True)
                        pkg_path.unlink(missing_ok=True)

                        return self.run_pipeline(
                            job_id=job_id,
                            topic=topic,
                            profile=profile,
                            priority=priority,
                            auto_publish=auto_publish,
                            publish_visibility=publish_visibility,
                            publish_platform=publish_platform,
                            tts_provider=tts_provider,
                            asset_provider=asset_provider,
                            on_stage_progress=on_stage_progress,
                            channel_id=channel_id,
                            llm_provider=llm_provider,
                            research_provider=research_provider,
                            production_engine=production_engine,
                            max_regeneration_attempts=max_regeneration_attempts,
                            attempt_number=attempt_number + 1,
                            corrective_instructions=corrective_instruction,
                            regeneration_reason=defect_reason,
                            policy=policy,
                        )
                    else:
                        self.db.update_job_status(job_id, WorkflowState.NEEDS_REVIEW.value)
                        self.db.record_error(
                            job_id,
                            "SCRIPT",
                            "regeneration_limit_exceeded",
                            f"Exceeded max attempts ({max_regeneration_attempts}). Reason: {defect_reason}",
                        )
                        raise PipelineError(
                            f"Script Quality Gate Blocked after {max_regeneration_attempts} attempts (job marked NEEDS_REVIEW): {defect_reason}",
                            category="BLOCKED",
                            stage="SCRIPT",
                        )

                sp_path.parent.mkdir(parents=True, exist_ok=True)
                sp_path.write_text(script.model_dump_json(indent=2), encoding="utf-8")

                gen_meta = script.generation_metadata or {}
                real_desc = gen_meta.get("description")
                if not real_desc:
                    if script.hook and script.cta:
                        real_desc = f"{script.hook}\n\n{script.cta}"
                    elif script.hook:
                        real_desc = script.hook
                    elif script_provider.provider_name == "mock_script":
                        real_desc = f"Deterministic demo content for topic: {topic}"
                    else:
                        real_desc = f"Auto-generated video for: {topic}"

                real_tags = gen_meta.get("tags")
                if not real_tags:
                    if script_provider.provider_name == "mock_script":
                        real_tags = ["autopilot", "demo"]
                    else:
                        real_tags = ["autopilot", "shorts"]

                package = ContentPackage(
                    content_item=ContentItem(content_id=job_id, topic=topic, format="9:16_video"),
                    script=script,
                    publication=PublicationMetadata(
                        title=script.working_title or topic,
                        description=real_desc,
                        hashtags=real_tags,
                        privacy_status=publish_visibility,
                    ),
                    provenance=ProvenanceRecord(provider=script_provider.provider_name),
                )
                pkg_path.parent.mkdir(parents=True, exist_ok=True)
                pkg_path.write_text(package.model_dump_json(indent=2), encoding="utf-8")

                self.db.record_artifact(job_id, str(sp_path), "script")
                self.db.record_artifact(job_id, str(pkg_path), "script")
                self.db.update_job_status(job_id, WorkflowState.SCRIPTED.value)
                logger.info("stage_completed", details={"stage": "SCRIPT", "scenes": len(script.scenes)})
            except PipelineError:
                raise
            except Exception as exc:
                self.db.update_job_status(job_id, WorkflowState.FAILED_SCRIPT.value)
                self.db.record_error(job_id, "SCRIPT", "script_failed", str(exc))
                raise PipelineError(f"Script generation failed: {exc}", category="NON_RETRYABLE", stage="SCRIPT")

        # --------------------------------------------------------------
        # 3. VOICE / TTS STAGE
        # --------------------------------------------------------------
        if on_stage_progress:
            on_stage_progress("VOICE")

        voice_dir = art_dir / "voice"
        voice_dir.mkdir(parents=True, exist_ok=True)
        voice_artifacts = []
        voice_valid = True

        if package.voice_artifacts:
            for v_path in package.voice_artifacts:
                p = Path(v_path)
                if not p.exists() or p.stat().st_size == 0:
                    voice_valid = False
                    break
                # Provider resume validation: ensure mock audio is not resumed when Kokoro is requested
                if tts_provider == "kokoro":
                    prov_p = p.with_suffix(p.suffix + ".provenance.json")
                    if prov_p.exists():
                        try:
                            p_info = json.loads(prov_p.read_text(encoding="utf-8"))
                            if p_info.get("provider") != "kokoro":
                                voice_valid = False
                                break
                        except Exception:
                            pass
                voice_artifacts.append(str(p))

        if not voice_valid or not voice_artifacts:
            try:
                self.db.update_job_status(job_id, WorkflowState.VOICING.value)
                tts = get_tts_provider(tts_provider)
                voice_artifacts = []
                total_duration = 0.0

                if tts is not None:
                    for scene in script.scenes:
                        if scene.narration:
                            seg_path = voice_dir / f"scene_{scene.scene_id}.wav"
                            tts.synthesize(scene.narration, str(seg_path))
                            dur_info = extract_duration(str(seg_path))
                            measured = dur_info.get("duration_sec", 2.0) if dur_info.get("valid") else 2.0
                            total_duration += measured
                            voice_artifacts.append(str(seg_path.resolve()))
                            self.db.record_voice_artifact(
                                job_id=job_id,
                                content_id=job_id,
                                segment_id=scene.scene_id,
                                artifact_path=str(seg_path.resolve()),
                                duration_sec=measured,
                            )

                package.voice_artifacts = voice_artifacts
                package.measured_duration_sec = round(total_duration, 2) if total_duration > 0 else None
                pkg_path.write_text(package.model_dump_json(indent=2), encoding="utf-8")

                # Run transcription alignment if voice segments exist and asr_provider is enabled
                if voice_artifacts:
                    try:
                        asr_provider = getattr(self.config, "provider_default_asr", "local_stub")
                        if asr_provider != "local_stub":
                            whisper_engine = FasterWhisperEngine(model_size=self.config.whisper_model_size)
                            combined_voice = voice_artifacts[0]
                            trans_res = whisper_engine.transcribe(
                                TranscriptionRequest(audio_path=combined_voice, language="en", word_timestamps=True)
                            )
                            srt_file = art_dir / "voice" / "subtitles.srt"
                            ass_file = art_dir / "voice" / "subtitles.ass"
                            if trans_res.srt_content:
                                srt_file.write_text(trans_res.srt_content, encoding="utf-8")
                                self.db.record_artifact(job_id, str(srt_file), "subtitles")
                            if trans_res.ass_content:
                                ass_file.write_text(trans_res.ass_content, encoding="utf-8")
                                self.db.record_artifact(job_id, str(ass_file), "subtitles_ass")
                    except Exception as t_err:
                        logger.warning("transcription_stage_skipped", details={"error": str(t_err)})

                self.db.update_job_status(job_id, WorkflowState.VOICE_READY.value)
                logger.info("stage_completed", details={"stage": "VOICE", "provider": tts_provider, "duration_sec": total_duration})
            except Exception as exc:
                self.db.update_job_status(job_id, WorkflowState.FAILED_TTS.value)
                self.db.record_error(job_id, "VOICE", "tts_failed", str(exc))
                raise PipelineError(f"TTS synthesis failed: {exc}", category="RETRYABLE", stage="VOICE")
        else:
            self.db.update_job_status(job_id, WorkflowState.VOICE_READY.value)
            logger.info("stage_resumed", details={"stage": "VOICE", "reason": "existing_audio_artifacts"})

        # --------------------------------------------------------------
        # 4. ASSETS STAGE
        # --------------------------------------------------------------
        if on_stage_progress:
            on_stage_progress("ASSETS")

        asset_artifacts = []
        assets_valid = False
        db_assets = self.db.get_asset_artifacts_for_job(job_id)
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
                        p_data = json.loads(prov_raw)
                        if p_data.get("provider") != "openverse":
                            all_exist = False
                            break
                    except Exception:
                        pass
            assets_valid = all_exist

        if not assets_valid:
            try:
                self.db.update_job_status(job_id, WorkflowState.ASSET_PREPARING.value)
                asset_artifacts, report = process_scene_assets(
                    script=script,
                    job_id=job_id,
                    provider_name=asset_provider,
                    db=self.db,
                    config=self.config,
                )
                if not asset_artifacts or report.get("errors"):
                    err_msg = "; ".join(report.get("errors", ["No assets found"]))
                    self.db.update_job_status(job_id, WorkflowState.FAILED_ASSETS.value)
                    raise PipelineError(f"Asset acquisition failed: {err_msg}", category="BLOCKED", stage="ASSETS")

                self.db.update_job_status(job_id, WorkflowState.ASSETS_READY.value)
                logger.info("stage_completed", details={"stage": "ASSETS", "count": len(asset_artifacts)})
            except PipelineError:
                raise
            except Exception as exc:
                self.db.update_job_status(job_id, WorkflowState.FAILED_ASSETS.value)
                self.db.record_error(job_id, "ASSETS", "assets_failed", str(exc))
                raise PipelineError(f"Asset pipeline failed: {exc}", category="RETRYABLE", stage="ASSETS")
        else:
            # Rehydrate asset artifacts from DB
            from autopilot.core.contracts import AssetArtifact, AssetProvenance, AssetLicense
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
            self.db.update_job_status(job_id, WorkflowState.ASSETS_READY.value)
            logger.info("stage_resumed", details={"stage": "ASSETS", "reason": "existing_asset_artifacts"})

        # --------------------------------------------------------------
        # 5. RENDER STAGE
        # --------------------------------------------------------------
        if on_stage_progress:
            on_stage_progress("RENDER")

        render_dir = art_dir / "render"
        render_dir.mkdir(parents=True, exist_ok=True)
        final_mp4 = render_dir / "final.mp4"
        plan_path = render_dir / "render_plan.json"
        render_valid = False
        render_checksum = None

        render_scenes = []
        for idx, scene in enumerate(script.scenes):
            matched_art = next((a for a in asset_artifacts if a.scene_id == scene.scene_id), None)
            norm_path = matched_art.normalized_path if matched_art else None
            voice_path = voice_artifacts[idx] if idx < len(voice_artifacts) else None
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
                "narration": scene.narration,
                "on_screen_text": scene.on_screen_text,
                "emphasis_words": scene.emphasis_words or [],
                "transition_hint": scene.transition_hint,
            })

        plan_expected_dur = sum(s.get("duration_sec", 0) for s in render_scenes)

        if final_mp4.exists() and final_mp4.stat().st_size > 0:
            probe = inspect_media(str(final_mp4))
            if probe.get("valid"):
                actual_dur = float(probe.get("format", {}).get("duration", 0) or 0)
                # Engine invalidation check: verify previous render used the same production engine
                engine_match = True
                saved_rendered_dur = None
                if plan_path.exists():
                    try:
                        saved_plan_data = json.loads(plan_path.read_text(encoding="utf-8"))
                        saved_engine = saved_plan_data.get("production_engine", "ffmpeg").lower()
                        if saved_engine != target_production_engine:
                            engine_match = False
                        saved_rendered_dur = saved_plan_data.get("rendered_duration_sec")
                    except Exception:
                        pass
                ref_dur = float(saved_rendered_dur) if (saved_rendered_dur is not None and float(saved_rendered_dur) > 0) else plan_expected_dur
                if engine_match and ref_dur > 0 and abs(actual_dur - ref_dur) <= 1.5:
                    render_valid = True
                    render_checksum = compute_file_sha256(str(final_mp4))

        render_plan = RenderPlan(
            plan_id=f"plan-{job_id}",
            content_id=job_id,
            job_id=job_id,
            profile=profile,
            production_engine=target_production_engine,
            scenes=render_scenes,
            raw_speech_duration_sec=plan_expected_dur,
            rendered_duration_sec=actual_dur if render_valid else None,
        )
        plan_path.write_text(render_plan.model_dump_json(indent=2), encoding="utf-8")

        if not render_valid:
            try:
                self.db.update_job_status(job_id, WorkflowState.RENDERING.value)
                prod_engine = get_production_engine(
                    engine_name=target_production_engine,
                    profile=profile,
                    endpoint=self.config.moneyprinter_endpoint,
                    cli_path=self.config.moneyprinter_cli_path,
                )
                voice_file = voice_artifacts[0] if voice_artifacts else None
                prod_req = ProductionRequest(
                    job_id=job_id,
                    content_id=job_id,
                    topic=topic,
                    script=script,
                    render_plan=render_plan,
                    output_path=str(final_mp4),
                    profile=profile,
                    production_engine=target_production_engine,
                    voice_path=voice_file,
                )
                prod_result = prod_engine.generate(prod_req)
                render_checksum = prod_result.checksum_sha256 or compute_file_sha256(str(final_mp4))

                render_plan.rendered_duration_sec = prod_result.duration_sec
                render_plan.raw_speech_duration_sec = plan_expected_dur
                plan_path.write_text(render_plan.model_dump_json(indent=2), encoding="utf-8")

                self.db.record_artifact(job_id, str(final_mp4), "media", checksum_sha256=render_checksum)
                self.db.update_job_status(job_id, WorkflowState.RENDERED.value)
                logger.info("stage_completed", details={"stage": "RENDER", "engine": target_production_engine, "path": str(final_mp4), "sha256": render_checksum[:16]})
            except Exception as exc:
                self.db.update_job_status(job_id, WorkflowState.FAILED_RENDER.value)
                self.db.record_error(job_id, "RENDER", "render_failed", str(exc))
                raise PipelineError(f"Video render failed ({target_production_engine}): {exc}", category="RETRYABLE", stage="RENDER")
        else:
            self.db.update_job_status(job_id, WorkflowState.RENDERED.value)
            logger.info("stage_resumed", details={"stage": "RENDER", "reason": "existing_valid_media", "engine": target_production_engine})

        # --------------------------------------------------------------
        # 6. QA STAGE & QUALITY GATE
        # --------------------------------------------------------------
        if on_stage_progress:
            on_stage_progress("QA")

        qa_receipt_path = art_dir / "quality" / "receipt.json"
        qa_report: Optional[QAReport] = None

        if qa_receipt_path.exists():
            try:
                report_file = art_dir / "quality" / "quality_report.json"
                if report_file.exists():
                    saved_report = QAReport.model_validate_json(report_file.read_text(encoding="utf-8"))
                    if saved_report.receipt and saved_report.receipt.media_checksum_sha256 == render_checksum:
                        qa_report = saved_report
                        if qa_report.publish_allowed and qa_report.status != QAStatus.BLOCK:
                            self.db.update_job_status(job_id, WorkflowState.APPROVED.value)
                        logger.info("stage_resumed", details={"stage": "QA", "status": qa_report.status.value})
            except Exception:
                qa_report = None

        if not qa_report:
            try:
                self.db.update_job_status(job_id, WorkflowState.QA.value)
                qa_engine = QAEngine(self.config)
                qa_report = qa_engine.evaluate(
                    media_path=str(final_mp4),
                    plan=render_plan,
                    package=package,
                    asset_artifacts=asset_artifacts,
                    profile=profile,
                    job_id=job_id,
                    db_manager=self.db,
                )

                self.db.record_qa_report(qa_report)
                export_qa_artifacts(qa_report, art_dir)

                if not qa_report.publish_allowed or qa_report.status == QAStatus.BLOCK:
                    block_findings = qa_report.receipt.blocking_findings if qa_report.receipt else []
                    defect_type, defect_reason, corrective_instruction, target_stage = classify_qa_defect(
                        block_findings, qa_report
                    )

                    if attempt_number < max_regeneration_attempts:
                        self.db.update_job_status(job_id, WorkflowState.REGENERATING.value)
                        logger.warning(
                            "targeted_regeneration_triggered",
                            details={
                                "attempt": attempt_number,
                                "max_attempts": max_regeneration_attempts,
                                "defect_type": defect_type.value,
                                "reason": defect_reason,
                                "instruction": corrective_instruction,
                                "target_stage": target_stage,
                            },
                        )
                        regen_meta = {
                            "attempt": attempt_number,
                            "defect_type": defect_type.value,
                            "reason": defect_reason,
                            "instruction": corrective_instruction,
                            "target_stage": target_stage,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        }
                        regen_file = art_dir / "quality" / f"regeneration_attempt_{attempt_number}.json"
                        regen_file.parent.mkdir(parents=True, exist_ok=True)
                        regen_file.write_text(json.dumps(regen_meta, indent=2), encoding="utf-8")

                        # Invalidate affected artifacts based on target stage
                        qa_receipt_path.unlink(missing_ok=True)
                        report_file = art_dir / "quality" / "quality_report.json"
                        report_file.unlink(missing_ok=True)
                        if target_stage in ("SCRIPT", "GENERAL"):
                            sp_path.unlink(missing_ok=True)
                            pkg_path.unlink(missing_ok=True)
                            final_mp4.unlink(missing_ok=True)
                            plan_path.unlink(missing_ok=True)
                        elif target_stage == "VOICE":
                            for v_art in voice_artifacts:
                                Path(v_art).unlink(missing_ok=True)
                            final_mp4.unlink(missing_ok=True)
                        elif target_stage == "RENDER":
                            final_mp4.unlink(missing_ok=True)

                        return self.run_pipeline(
                            job_id=job_id,
                            topic=topic,
                            profile=profile,
                            priority=priority,
                            auto_publish=auto_publish,
                            publish_visibility=publish_visibility,
                            publish_platform=publish_platform,
                            tts_provider=tts_provider,
                            asset_provider=asset_provider,
                            on_stage_progress=on_stage_progress,
                            channel_id=channel_id,
                            llm_provider=llm_provider,
                            research_provider=research_provider,
                            production_engine=production_engine,
                            max_regeneration_attempts=max_regeneration_attempts,
                            attempt_number=attempt_number + 1,
                            corrective_instructions=corrective_instruction,
                            regeneration_reason=defect_reason,
                            policy=policy,
                        )
                    else:
                        self.db.update_job_status(job_id, WorkflowState.NEEDS_REVIEW.value)
                        self.db.record_error(
                            job_id,
                            "QA",
                            "regeneration_limit_exceeded",
                            f"Exceeded max attempts ({max_regeneration_attempts}). Reason: {defect_reason}",
                        )
                        raise PipelineError(
                            f"QA Gate Blocked after {max_regeneration_attempts} attempts (job marked NEEDS_REVIEW): {defect_reason}",
                            category="BLOCKED",
                            stage="QA",
                        )

                self.db.update_job_status(job_id, WorkflowState.APPROVED.value)
                logger.info("stage_completed", details={"stage": "QA", "status": qa_report.status.value, "allowed": qa_report.publish_allowed})
            except PipelineError:
                raise
            except Exception as exc:
                self.db.update_job_status(job_id, WorkflowState.FAILED_QA.value)
                self.db.record_error(job_id, "QA", "qa_failed", str(exc))
                raise PipelineError(f"QA evaluation failed: {exc}", category="NON_RETRYABLE", stage="QA")

        # --------------------------------------------------------------
        # 7. OPTIONAL PUBLISH STAGE
        # --------------------------------------------------------------
        publish_result = None
        if auto_publish:
            if on_stage_progress:
                on_stage_progress("PUBLISH")
            try:
                # The engine owns the APPROVED -> PUBLISHING transition so that the
                # approval gate runs before any state change. A blocked approval
                # leaves the job READY_TO_PUBLISH (never FAILED_PUBLISH).
                publisher = PublishingEngine(config=self.config, db=self.db)
                publish_result = publisher.publish(
                    job_id=job_id,
                    platform=publish_platform,
                    visibility=publish_visibility,
                    dry_run=False,
                    media_path=str(final_mp4),
                    require_approval=True,
                )
                if not publish_result.success:
                    if publish_result.status == PublishStatus.BLOCKED_APPROVAL:
                        err_msg = publish_result.error.message if publish_result.error else "Operator approval required"
                        self.db.record_error(job_id, "PUBLISH", "approval_gate_blocked", err_msg)
                        raise PipelineError(f"Publication held for approval: {err_msg}", category="NON_RETRYABLE", stage="PUBLISH")

                    self.db.update_job_status(job_id, WorkflowState.FAILED_PUBLISH.value)
                    err_msg = publish_result.error.message if publish_result.error else "Publishing failed"
                    raise PipelineError(f"Publishing failed: {err_msg}", category="RETRYABLE", stage="PUBLISH")

                self.db.update_job_status(job_id, WorkflowState.PUBLISHED.value)
                logger.info("stage_completed", details={"stage": "PUBLISH", "status": publish_result.status.value})
            except PipelineError:
                raise
            except Exception as exc:
                self.db.update_job_status(job_id, WorkflowState.FAILED_PUBLISH.value)
                self.db.record_error(job_id, "PUBLISH", "publish_failed", str(exc))
                raise PipelineError(f"Publishing stage failed: {exc}", category="RETRYABLE", stage="PUBLISH")

        return {
            "job_id": job_id,
            "status": "success",
            "media_path": str(final_mp4),
            "media_checksum": render_checksum,
            "qa_status": qa_report.status.value if qa_report else "UNKNOWN",
            "published": auto_publish and publish_result and publish_result.success,
        }
