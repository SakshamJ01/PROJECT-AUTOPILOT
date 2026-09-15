"""Scene-to-Asset Pipeline Orchestration Engine.
Coordinates asset discovery, candidate scoring, rights verification,
safe download, caching, normalization, and quality reporting.
"""
from __future__ import annotations
import json
import uuid
import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional

from autopilot.core.config import CONFIG
from autopilot.core.contracts import (
    ScriptDocument, ScriptScene, AssetRequest, AssetCandidate,
    AssetSelection, AssetArtifact, AssetLicense, AssetProvenance,
    AssetDimensions, AssetMediaInfo, AssetValidationResult
)
from autopilot.providers.asset_contracts import AssetProvider
from autopilot.providers.local_asset_provider import LocalAssetProvider
from autopilot.providers.openverse_provider import OpenverseAssetProvider
from autopilot.core.asset_scoring import score_candidates
from autopilot.core.rights_gate import evaluate_rights_gate
from autopilot.core.asset_cache import safe_download_media, compute_file_sha256, compute_image_phash, AssetCache
from autopilot.core.asset_normalizer import normalize_asset
from autopilot.core.media_inspection import inspect_media
from autopilot.db.manager import DBManager


class AssetQualityReport:
    """Collects and serializes quality metrics for the asset pipeline run."""

    def __init__(self, job_id: str):
        self.job_id = job_id
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.completed_at: Optional[str] = None
        self.total_requests = 0
        self.total_candidates_found = 0
        self.selections: List[Dict[str, Any]] = []
        self.rejections: List[Dict[str, Any]] = []
        self.artifacts_created: List[Dict[str, Any]] = []
        self.warnings: List[str] = []
        self.errors: List[str] = []
        self.status = "pending"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "status": self.status,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "total_requests": self.total_requests,
            "total_candidates_found": self.total_candidates_found,
            "successful_selections": len(self.selections),
            "selections": self.selections,
            "rejections": self.rejections,
            "artifacts": self.artifacts_created,
            "warnings": self.warnings,
            "errors": self.errors,
        }

    def save(self, out_path: Path) -> None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")


def get_asset_provider(name: str = "local") -> AssetProvider:
    """Factory helper to retrieve configured asset provider."""
    name_clean = (name or "local").lower().strip()
    if name_clean == "openverse":
        return OpenverseAssetProvider()
    elif name_clean == "local":
        return LocalAssetProvider()
    else:
        raise ValueError(f"Unknown asset provider: '{name}'. Available: 'local', 'openverse'")


def derive_visual_subject_query(scene: ScriptScene, topic: str) -> str:
    """Derive a visual subject query specific to the scene, avoiding narration sentence fragments."""
    stop_words = {
        "the", "a", "an", "is", "are", "was", "were", "to", "of", "and", "for", "in", "that", "have", "with",
        "as", "this", "it", "from", "on", "at", "by", "be", "or", "not", "but", "will", "we", "you", "they",
        "them", "their", "there", "then", "than", "so", "if", "about", "into", "through", "during", "before",
        "after", "above", "below", "between", "under", "again", "further", "once", "here", "when", "where",
        "why", "how", "all", "each", "few", "more", "most", "other", "some", "such", "no", "nor", "only",
        "own", "same", "can", "could", "should", "would", "may", "might", "must", "shall", "do", "does",
        "did", "done", "doing", "get", "got", "getting", "made", "make", "making", "take", "took", "taking",
        "come", "came", "coming", "go", "went", "going", "see", "saw", "seeing", "know", "knew", "knowing",
        "think", "thought", "thinking", "say", "said", "saying", "use", "used", "using", "first", "today",
        "introduced", "systems", "fact", "facts", "surprising", "about", "let", "lets", "talk",
        "things", "top", "best", "reasons", "ways", "showing", "split", "screen", "animation", "abstract",
        "exponential", "growth", "graph", "chart", "diagram", "illustration", "different", "various", "across",
        "multiple", "unprecedented", "pace", "apps", "app", "futuristic", "evolving", "question", "mark",
        "pattern", "points", "solving", "concept", "background", "city", "skyline", "symbol", "representation",
        "1", "2", "3", "4", "5", "6", "7", "8", "9", "10"
    }

    # 1. Prefer explicit scene asset_query if it describes a visual subject distinct from generic topic
    if scene.asset_query and scene.asset_query.strip().lower() != topic.strip().lower():
        clean_q = scene.asset_query.strip()
        if not clean_q.lower().startswith("visual representation") and len(clean_q) <= 60:
            words = [w for w in re.findall(r"\b\w+\b", clean_q)]
            non_stop = [w for w in words if w.lower() not in stop_words and len(w) > 1]
            if len(non_stop) >= 2:
                bad_abstract = {"animation", "abstract", "showing", "split", "screen", "illustration"}
                cleaned_words = [w for w in words if w.lower() not in bad_abstract]
                return " ".join(cleaned_words)
            elif non_stop:
                return " ".join(non_stop)

    # 2. Prefer explicit visual_intent if available
    if scene.visual_intent and not scene.visual_intent.lower().startswith("visual representation"):
        viz_words = [w for w in re.findall(r"\b\w+\b", scene.visual_intent.lower()) if w not in stop_words and len(w) > 2]
        if viz_words:
            return " ".join(viz_words[:4])

    # 3. Extract key noun concepts from scene narration (filtering verbs/stop-words)
    narration_words = [w for w in re.findall(r"\b\w+\b", (scene.narration or "").lower()) if w not in stop_words and len(w) > 2]
    if narration_words:
        return " ".join(narration_words[:4])

    # Fallback to topic core
    return topic


def derive_deterministic_fallback_query(scene: ScriptScene, topic: str) -> str:
    """Derive a deterministic visual fallback from scene visual concept and topic core.

    Never uses first N narration words.
    """
    stop_words = {
        "the", "a", "an", "is", "are", "was", "were", "to", "of", "and", "for", "in", "that", "have", "with",
        "as", "this", "it", "from", "on", "at", "by", "be", "or", "not", "but", "will", "we", "you", "they",
        "them", "their", "there", "then", "than", "so", "if", "about", "into", "through", "during", "before",
        "after", "above", "below", "between", "under", "again", "further", "once", "here", "when", "where",
        "why", "how", "all", "each", "few", "more", "most", "other", "some", "such", "no", "nor", "only",
        "own", "same", "can", "could", "should", "would", "may", "might", "must", "shall", "do", "does",
        "did", "done", "doing", "get", "got", "getting", "made", "make", "making", "take", "took", "taking",
        "come", "came", "coming", "go", "went", "going", "see", "saw", "seeing", "know", "knew", "knowing",
        "think", "thought", "thinking", "say", "said", "saying", "use", "used", "using", "first", "today",
        "introduced", "systems", "fact", "facts", "surprising", "about", "let", "lets", "talk",
        "things", "top", "best", "reasons", "ways", "showing", "split", "screen", "animation", "abstract",
        "graph", "chart", "diagram", "illustration", "different", "various", "across", "multiple",
        "1", "2", "3", "4", "5", "6", "7", "8", "9", "10"
    }
    # Topic core noun words (filtering numbers and filler words)
    topic_words = [w for w in re.findall(r"\b\w+\b", topic.lower()) if w not in stop_words and len(w) > 2]
    topic_core = " ".join(topic_words[:2]) if topic_words else topic

    # Scene visual concept words
    viz_text = f"{scene.visual_intent or ''} {scene.asset_query or ''} {scene.narration or ''}"
    scene_words = [w for w in re.findall(r"\b\w+\b", viz_text.lower()) if w not in stop_words and len(w) > 2 and w not in topic_words]

    if scene_words:
        return f"{scene_words[0]} {topic_core}".strip()
    return topic_core


def process_scene_assets(
    script: ScriptDocument,
    job_id: str,
    provider_name: str = "local",
    db: Optional[DBManager] = None,
    dry_run: bool = False,
    search_only: bool = False,
    config: Optional[Config] = None,
) -> Tuple[List[AssetArtifact], Dict[str, Any]]:
    """Execute complete scene-to-asset acquisition pipeline.

    Returns:
        (artifacts_list, quality_report_dict)
    """
    cfg = config or CONFIG
    db_mgr = db or DBManager(cfg.db_path)
    db_mgr.init_schema()
    db_mgr.create_job(job_id=job_id, topic=script.topic)

    provider = get_asset_provider(provider_name)
    report = AssetQualityReport(job_id=job_id)
    artifacts: List[AssetArtifact] = []
    seen_checksums: set[str] = set()

    job_asset_dir = cfg.get_artifacts_dir() / "jobs" / job_id / "assets"
    job_asset_dir.mkdir(parents=True, exist_ok=True)

    cache = AssetCache(cache_dir=cfg.get_asset_cache_dir())

    for scene in script.scenes:
        report.total_requests += 1

        # Derive visual subject query specific to this scene
        query = derive_visual_subject_query(scene, script.topic)
        request_id = f"req-{job_id}-{scene.scene_id}"

        asset_request = AssetRequest(
            asset_request_id=request_id,
            scene_id=scene.scene_id,
            query=query,
            asset_type="image",
            aspect_ratio="9:16",
        )

        # 1. Search candidates
        try:
            candidates = provider.search(
                {"query": query, "aspect_ratio": "9:16"},
                max_results=CONFIG.openverse_max_results,
            )
        except Exception as exc:
            err_msg = f"Search failed for scene {scene.scene_id} ('{query}'): {exc}"
            report.errors.append(err_msg)
            report.rejections.append({"scene_id": scene.scene_id, "query": query, "reason": str(exc)})
            continue

        report.total_candidates_found += len(candidates)

        if not candidates:
            # Deterministic visual fallback derived from scene visual intent / scene concept + topic core
            fallback_query = derive_deterministic_fallback_query(scene, script.topic)
            if fallback_query and fallback_query.lower() != query.lower():
                try:
                    candidates = provider.search(
                        {"query": fallback_query, "aspect_ratio": "9:16"},
                        max_results=CONFIG.openverse_max_results,
                    )
                    if candidates:
                        report.rejections.append({
                            "scene_id": scene.scene_id,
                            "query": query,
                            "reason": f"Zero results for primary query; used visual fallback query '{fallback_query}'"
                        })
                        query = fallback_query
                except Exception:
                    pass

            if not candidates:
                err_msg = f"No candidate assets found for scene {scene.scene_id} query '{query}'"
                report.errors.append(err_msg)
                report.rejections.append({"scene_id": scene.scene_id, "query": query, "reason": "Zero search results"})
                continue

        # Helper to score candidates and select best allowed candidate
        def select_best_candidate(cands: List[AssetCandidate], q_str: str) -> Tuple[Optional[AssetCandidate], str]:
            if provider_name != "local":
                for c in cands:
                    if c.provenance and c.provenance.original_hash_sha256 in seen_checksums:
                        c.is_duplicate = True

            scored = score_candidates(
                cands,
                request_criteria={
                    "query": q_str,
                    "scene_id": scene.scene_id,
                    "visual_intent": scene.visual_intent,
                    "narration": scene.narration,
                },
                target_width=CONFIG.asset_target_width,
                target_height=CONFIG.asset_target_height,
            )
            for cand in scored:
                if provider_name != "local" and cand.score < 0.20:
                    report.rejections.append({
                        "candidate_id": cand.candidate_id,
                        "scene_id": scene.scene_id,
                        "reason": f"Semantic relevance score too low ({cand.score:.2f} < 0.20) for query '{q_str}'",
                        "title": cand.title,
                    })
                    continue

                gate_res = evaluate_rights_gate(cand.license)
                if gate_res.allowed:
                    reason = f"Passed rights gate ({cand.license.license_name}) with score {cand.score:.2f}"
                    if gate_res.warnings:
                        report.warnings.extend(gate_res.warnings)
                    return cand, reason
                else:
                    report.rejections.append({
                        "candidate_id": cand.candidate_id,
                        "scene_id": scene.scene_id,
                        "reason": "; ".join(gate_res.reasons),
                        "license": cand.license.license_name,
                        "rights_status": cand.license.rights_status,
                    })
            return None, ""

        selected_candidate, selection_reason = select_best_candidate(candidates, query)

        # If primary query found no eligible candidate, try visual fallback query
        if not selected_candidate:
            fallback_query = derive_deterministic_fallback_query(scene, script.topic)
            if fallback_query and fallback_query.lower() != query.lower():
                try:
                    fb_cands = provider.search(
                        {"query": fallback_query, "aspect_ratio": "9:16"},
                        max_results=CONFIG.openverse_max_results,
                    )
                    report.total_candidates_found += len(fb_cands)
                    if fb_cands:
                        selected_candidate, selection_reason = select_best_candidate(fb_cands, fallback_query)
                        if selected_candidate:
                            query = fallback_query
                except Exception as fb_exc:
                    report.warnings.append(f"Fallback search error for scene {scene.scene_id}: {fb_exc}")

        # If still no candidate, try the core topic as a last resort deterministic query
        if not selected_candidate and provider_name != "local":
            topic_stop = {"the", "a", "an", "is", "are", "surprising", "facts", "fact", "about", "top", "best", "things", "ways", "reasons", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10"}
            topic_words = [w for w in re.findall(r"\b\w+\b", script.topic.lower()) if w not in topic_stop and len(w) > 2]
            topic_core = " ".join(topic_words[:2]) if topic_words else script.topic
            if topic_core and topic_core.lower() not in (query.lower(), fallback_query.lower() if 'fallback_query' in locals() else ""):
                try:
                    tc_cands = provider.search(
                        {"query": topic_core, "aspect_ratio": "9:16"},
                        max_results=CONFIG.openverse_max_results,
                    )
                    report.total_candidates_found += len(tc_cands)
                    if tc_cands:
                        selected_candidate, selection_reason = select_best_candidate(tc_cands, topic_core)
                        if selected_candidate:
                            query = topic_core
                except Exception as tc_exc:
                    report.warnings.append(f"Topic core search error for scene {scene.scene_id}: {tc_exc}")

        if not selected_candidate:
            err_msg = f"No semantically relevant candidate passed rights gate for scene {scene.scene_id} (query: '{query}')"
            report.errors.append(err_msg)
            continue

        report.selections.append({
            "scene_id": scene.scene_id,
            "request_id": request_id,
            "candidate_id": selected_candidate.candidate_id,
            "title": selected_candidate.title,
            "license": selected_candidate.license.license_name,
            "rights_status": selected_candidate.license.rights_status,
            "score": selected_candidate.score,
            "reason": selection_reason,
        })

        if search_only or dry_run:
            continue

        # 5. Safe Download & Cache
        is_video = (selected_candidate.asset_type == "video") or (selected_candidate.source_url and str(selected_candidate.source_url).endswith((".mp4", ".mov", ".mkv", ".webm")))
        file_ext = ".mp4" if is_video else ".png"
        source_dest = job_asset_dir / f"src_{scene.scene_id}_{selected_candidate.candidate_id}{file_ext}"
        try:
            downloaded_path_str = provider.download(selected_candidate, str(source_dest))
            downloaded_path = Path(downloaded_path_str)
        except Exception as exc:
            err_msg = f"Download failed for {selected_candidate.candidate_id}: {exc}"
            report.errors.append(err_msg)
            continue

        # 6. Media Inspection
        inspection = inspect_media(downloaded_path)
        if not inspection.get("valid"):
            err_msg = f"Downloaded media inspection failed for scene {scene.scene_id}: {inspection.get('errors')}"
            report.errors.append(err_msg)
            continue

        source_sha256 = compute_file_sha256(downloaded_path)
        seen_checksums.add(source_sha256)

        # 7. Normalization
        normalized_dest = job_asset_dir / f"norm_{scene.scene_id}_{selected_candidate.candidate_id}{file_ext}"
        try:
            normalized_path_str = provider.normalize(
                str(downloaded_path),
                str(normalized_dest),
                asset_type=selected_candidate.asset_type,
                target_width=CONFIG.asset_target_width,
                target_height=CONFIG.asset_target_height,
            )
            normalized_path = Path(normalized_path_str)
            norm_sha256 = compute_file_sha256(normalized_path)
        except Exception as exc:
            err_msg = f"Normalization failed for scene {scene.scene_id}: {exc}"
            report.errors.append(err_msg)
            continue

        # 8. Create AssetArtifact & Provenance
        provenance = AssetProvenance(
            provider=provider.provider_name,
            source_url=selected_candidate.source_url,
            source_id=selected_candidate.source_id,
            retrieval_timestamp=datetime.now(timezone.utc).isoformat(),
            original_hash_sha256=source_sha256,
            normalized_hash_sha256=norm_sha256,
            job_id=job_id,
            content_id=script.content_id,
            scene_id=scene.scene_id,
        )

        artifact_id = f"art-{job_id}-{scene.scene_id}"
        artifact = AssetArtifact(
            artifact_id=artifact_id,
            job_id=job_id,
            content_id=script.content_id,
            scene_id=scene.scene_id,
            source_path=str(downloaded_path.resolve()),
            normalized_path=str(normalized_path.resolve()),
            asset_type=selected_candidate.asset_type,
            dimensions=AssetDimensions(
                width=CONFIG.asset_target_width,
                height=CONFIG.asset_target_height,
            ),
            media_info=AssetMediaInfo(
                mime_type="image/png",
                file_size_bytes=normalized_path.stat().st_size,
            ),
            checksum_sha256=norm_sha256,
            provenance=provenance,
            license=selected_candidate.license,
            validated=True,
            validation_result="PASS",
        )
        artifacts.append(artifact)

        # 9. Persist in Database
        db_mgr.record_asset_artifact(
            job_id=job_id,
            content_id=script.content_id,
            scene_id=scene.scene_id,
            artifact_path=str(normalized_path.resolve()),
            asset_type=artifact.asset_type,
            checksum=norm_sha256,
            provenance_json=provenance.model_dump_json(),
            license_json=selected_candidate.license.model_dump_json(),
        )

        report.artifacts_created.append({
            "artifact_id": artifact_id,
            "scene_id": scene.scene_id,
            "source_path": str(downloaded_path.resolve()),
            "normalized_path": str(normalized_path.resolve()),
            "checksum_sha256": norm_sha256,
        })

    report.completed_at = datetime.now(timezone.utc).isoformat()
    report.status = "completed" if len(report.errors) == 0 else ("partial" if artifacts else "failed")

    # Write report artifact
    report_path = job_asset_dir / "asset_quality_report.json"
    report.save(report_path)
    db_mgr.record_artifact(job_id, str(report_path), "quality")

    return artifacts, report.to_dict()
