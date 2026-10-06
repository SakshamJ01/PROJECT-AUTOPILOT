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
from autopilot.core.contracts import ProductionRequest, TranscriptionRequest, WordTimestamp


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


DEFAULT_TRANSITION_PREFERENCE = "fade"


def _resolve_scene_transition_hint(explicit_hint, channel_profile=None) -> str:
    """Pick a scene's transition hint: explicit script value wins, else brand preference.

    Keeping this in one place makes the brand's ``transition_preference`` field
    reachable instead of dead config, while still honouring an explicit "cut".
    """
    explicit = str(explicit_hint or "").strip().lower()
    if explicit:
        return explicit
    preference = getattr(getattr(channel_profile, "visual", None), "transition_preference", "")
    preference = str(preference or "").strip().lower()
    return preference or DEFAULT_TRANSITION_PREFERENCE


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
                raise PipelineError(f"Script generation failed: {exc}", category="RETRYABLE", stage="SCRIPT")

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
                from autopilot.core.voice_studio import NeuralVoiceStudio
                voice_studio = NeuralVoiceStudio()
                voice_artifacts = []
                total_duration = 0.0

                if tts is not None and tts_provider not in ("none", "off", "disabled"):

                    from autopilot.core.voice_studio import prosody_for_narrative_role
                    from autopilot.core.timeline_builder import scene_roles

                    # Role-based prosody (plan 3.2): hook +8%, payoff -5%,
                    # middle neutral. Applied at synthesis so the word-timestamp
                    # alignment pass measures the rate-shifted audio.
                    roles = scene_roles(len(script.scenes))
                    for idx, scene in enumerate(script.scenes):
                        if scene.narration:
                            scene_res = voice_studio.process_scene_narration(
                                scene_id=f"scene_{scene.scene_id}",
                                raw_text=scene.narration,
                                prosody=prosody_for_narrative_role(roles[idx]),
                                provider_name=tts_provider,
                                out_dir=voice_dir,
                            )
                            final_path = scene_res.mastered_audio_path
                            measured = scene_res.duration_sec
                            total_duration += measured
                            voice_artifacts.append(str(Path(final_path).resolve()))

                            # P0: attach the REAL per-scene word timestamps from
                            # the Faster-Whisper truth pass so kinetic captions are
                            # grounded in measured speech, not estimates.
                            authoritative = getattr(scene_res.qa_report, "authoritative_words", None) or []
                            if authoritative:
                                scene.word_timestamps = [
                                    WordTimestamp(
                                        word=w.word,
                                        start_sec=float(w.start_sec),
                                        end_sec=float(w.end_sec),
                                        probability=float(w.probability),
                                    )
                                    for w in authoritative
                                ]
                            self.db.record_voice_artifact(
                                job_id=job_id,
                                content_id=job_id,
                                segment_id=str(scene.scene_id),
                                artifact_path=str(Path(final_path).resolve()),
                                duration_sec=measured,
                            )

                package.voice_artifacts = voice_artifacts
                package.measured_duration_sec = round(total_duration, 2) if total_duration > 0 else None
                pkg_path.write_text(package.model_dump_json(indent=2), encoding="utf-8")


                # Reference subtitles are generated from the FIRST voice segment
                # (per-scene captions are built later from each scene's own
                # authoritative word timestamps). This stage must NOT be
                # silently skipped: real ASR is mandatory.
                if voice_artifacts:
                    try:
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
                        logger.error("transcription_stage_failed", details={"error": str(t_err)})
                        self.db.update_job_status(job_id, WorkflowState.FAILED_TTS.value)
                        self.db.record_error(job_id, "VOICE", "asr_failed", str(t_err))
                        raise PipelineError(
                            f"Real word-level alignment is required but failed: {t_err}",
                            category="RETRYABLE",
                            stage="VOICE",
                        )

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
        # 3b. UNDERSIZED NARRATION GUARD
        # --------------------------------------------------------------
        # Reject scripts whose narration is far too short for the target
        # duration.  Better to regenerate than to render a tiny video.
        from autopilot.core.profiles import PROFILES, ProfileConfig
        profile_cfg = PROFILES.get(profile, ProfileConfig())
        target_dur = profile_cfg.target_duration_sec  # e.g. 35.0
        min_narration_dur = target_dur * 0.82  # floor: 28.7s for a 35s target

        narration_dur = package.measured_duration_sec or 0.0
        if narration_dur > 0 and narration_dur < min_narration_dur:
            if attempt_number < max_regeneration_attempts:
                logger.warning(
                    "undersized_narration_regeneration",
                    details={
                        "narration_sec": narration_dur,
                        "target_sec": target_dur,
                        "min_narration_sec": min_narration_dur,
                        "attempt": attempt_number,
                    },
                )
                # Invalidate script and voice artifacts so the next attempt
                # generates a longer script.
                sp_path.unlink(missing_ok=True)
                pkg_path.unlink(missing_ok=True)
                for v_art in voice_artifacts:
                    Path(v_art).unlink(missing_ok=True)

                regen_meta = {
                    "attempt": attempt_number,
                    "defect_type": RegenerationDefectType.DURATION_MISMATCH.value,
                    "reason": f"Narration duration {narration_dur:.1f}s is below minimum {min_narration_dur:.1f}s (target {target_dur:.0f}s)",
                    "instruction": (
                        f"The narration is too short ({narration_dur:.1f}s for a {target_dur:.0f}s target). "
                        "Add more detail, examples, and elaboration to each scene. "
                        "Aim for narration that naturally fills the target duration."
                    ),
                    "target_stage": "SCRIPT",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                regen_file = art_dir / "quality" / f"regeneration_attempt_{attempt_number}.json"
                regen_file.parent.mkdir(parents=True, exist_ok=True)
                regen_file.write_text(json.dumps(regen_meta, indent=2), encoding="utf-8")

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
                    corrective_instructions=(
                        f"The narration is too short ({narration_dur:.1f}s for a {target_dur:.0f}s target). "
                        "Add more detail, examples, and elaboration to each scene. "
                        "Aim for narration that naturally fills the target duration."
                    ),
                    regeneration_reason=f"Narration duration {narration_dur:.1f}s below minimum {min_narration_dur:.1f}s",
                    policy=policy,
                )
            else:
                logger.warning(
                    "undersized_narration_accepted",
                    details={
                        "narration_sec": narration_dur,
                        "target_sec": target_dur,
                        "reason": "max_regeneration_attempts_exceeded",
                    },
                )

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
                    allow_fallback=True,
                )
                if not asset_artifacts or len(asset_artifacts) < len(script.scenes):
                    err_msg = "; ".join(report.get("errors") or [f"Only {len(asset_artifacts)}/{len(script.scenes)} scene assets acquired"])
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
            # P0: attach authoritative per-scene word timestamps (from the
            # truth-alignment pass) so the renderer can emit true kinetic
            # captions instead of one static text block per scene.
            scene_words = None
            if getattr(scene, "word_timestamps", None):
                scene_words = [
                    {"word": w.word, "start": w.start_sec, "end": w.end_sec}
                    for w in scene.word_timestamps
                ]
            # Honour an explicit script hint; otherwise fall back to the brand's
            # configured transition_preference so the setting is not dead config.
            _hint = _resolve_scene_transition_hint(
                scene.transition_hint, channel_profile_obj
            )
            render_scenes.append({
                "scene_id": scene.scene_id,
                "duration_sec": dur,
                "asset_path": norm_path,
                "audio_path": voice_path,
                "narration": scene.narration,
                "on_screen_text": scene.on_screen_text,
                "emphasis_words": scene.emphasis_words or [],
                "transition_hint": _hint,
                "word_timestamps": scene_words,
                "caption_plan": {
                    "position": "LOWER",
                    "platform_safe_zone": "YOUTUBE_SHORTS",
                    "style_preset": "hormozi_yellow_pop",
                },
                "asset_type": (matched_art.asset_type if matched_art else "image"),
                "asset_provider": (matched_art.provenance.provider if matched_art else None),
                "selection_reason": (matched_art.selection_reason if matched_art else None),
                "semantic_score": (matched_art.semantic_score if matched_art else None),
            })

        plan_expected_dur = sum(s.get("duration_sec", 0) for s in render_scenes)

        # P0 STALE ARTIFACT PROTECTION: a previously rendered final.mp4 may
        # only be reused if it explicitly proves compatibility with the
        # current pipeline version. Otherwise it is invalidated and re-rendered.
        from autopilot.core.stale_artifact_protection import (
            load_sidecar_compatibility,
            is_artifact_compatible,
        )
        existing_render_compatible = is_artifact_compatible(
            load_sidecar_compatibility(str(final_mp4))
        )

        if existing_render_compatible and final_mp4.exists() and final_mp4.stat().st_size > 0:
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
        elif final_mp4.exists():
            logger.warning(
                "stale_render_invalidated",
                details={
                    "path": str(final_mp4),
                    "reason": "artifact predates current pipeline version; refusing to reuse",
                },
            )

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

                # P0: prove the verified assets are actually in the rendered
                # file before any downstream QA treats them as evidence.
                from autopilot.core.render_provenance import (
                    summarize,
                    verify_asset_presence,
                    write_provenance_report,
                )

                provenance = verify_asset_presence(
                    str(final_mp4),
                    render_scenes,
                    production_engine=target_production_engine,
                )
                write_provenance_report(provenance, str(final_mp4.parent / "render_provenance.json"))
                logger.info("render_provenance_report", details={"summary": summarize(provenance)})
                if provenance.applicable and not provenance.valid:
                    self.db.record_error(
                        job_id,
                        "RENDER",
                        "render_provenance_unverified",
                        f"Engine '{target_production_engine}' did not render the planned "
                        f"assets: missing={provenance.missing_scene_ids}",
                    )
                    # Set the terminal status here because the raise below
                    # bypasses the generic `except Exception` handler that
                    # normally marks the job FAILED_RENDER.
                    self.db.update_job_status(job_id, WorkflowState.FAILED_RENDER.value)
                    raise PipelineError(
                        f"Render provenance unverified: engine '{target_production_engine}' "
                        f"did not render the planned assets (missing "
                        f"{provenance.missing_scene_ids or 'unknown'}); asset and rights QA "
                        "cannot be claimed for this render.",
                        category="NON_RETRYABLE",
                        stage="RENDER",
                    )

                self.db.record_artifact(job_id, str(final_mp4), "media", checksum_sha256=render_checksum)
                # P0: stamp the fresh render with the current pipeline version
                from autopilot.core.stale_artifact_protection import write_sidecar_compatibility

                write_sidecar_compatibility(str(final_mp4), job_id, "render")
                self.db.update_job_status(job_id, WorkflowState.RENDERED.value)
                logger.info("stage_completed", details={"stage": "RENDER", "engine": target_production_engine, "path": str(final_mp4), "sha256": render_checksum[:16]})
            except PipelineError:
                # A deliberate, categorised PipelineError (e.g. the
                # NON_RETRYABLE provenance failure above) must keep its own
                # category. Falling through to `except Exception` used to
                # re-wrap it as RETRYABLE, advertising a job that can never
                # pass as worth retrying.
                raise
            except Exception as exc:
                self.db.update_job_status(job_id, WorkflowState.FAILED_RENDER.value)
                self.db.record_error(job_id, "RENDER", "render_failed", str(exc))
                raise PipelineError(f"Video render failed ({target_production_engine}): {exc}", category="RETRYABLE", stage="RENDER")
        else:
            self.db.update_job_status(job_id, WorkflowState.RENDERED.value)
            logger.info("stage_resumed", details={"stage": "RENDER", "reason": "existing_valid_media", "engine": target_production_engine})

        # 5b. CUSTOM THUMBNAIL (Phase 4.1)
        # Built from the strongest scene's already-verified asset so it cannot
        # introduce rights/provenance questions QA did not already clear.
        # Fail-soft: never blocks the pipeline or publication.
        try:
            from autopilot.core.thumbnail import generate_thumbnail

            _hook_text = ""
            try:
                _first_scene = (script.scenes[0] if script.scenes else None)
                _hook_text = (_first_scene.narration if _first_scene else "") or ""
            except Exception:
                _hook_text = ""
            _channel_label = ""
            try:
                # `profile` here is the profile NAME (a str), so getattr against
                # it always yielded None and the channel label was silently
                # always empty. The brand lives on the ChannelProfile instance.
                _channel_label = str(getattr(channel_profile_obj, "channel_name", "") or "")
            except Exception:
                _channel_label = ""
            _thumb = generate_thumbnail(
                render_scenes=render_scenes,
                job_dir=art_dir,
                hook=_hook_text,
                channel_name=_channel_label,
            )
            if _thumb:
                logger.info("custom_thumbnail_generated", details={"path": str(_thumb)})
        except Exception as exc:
            logger.warning("custom_thumbnail_skipped", details={"reason": str(exc)})

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
