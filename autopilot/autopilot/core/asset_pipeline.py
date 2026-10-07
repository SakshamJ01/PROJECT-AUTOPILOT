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
from typing import List, Dict, Any, Optional, Tuple

from autopilot.core.config import CONFIG, Config
from autopilot.core.contracts import (
    ScriptDocument, ScriptScene, AssetRequest, AssetCandidate,
    AssetSelection, AssetArtifact, AssetLicense, AssetProvenance,
    AssetDimensions, AssetMediaInfo, AssetValidationResult
)
from autopilot.providers.asset_contracts import AssetProvider
from autopilot.providers.local_asset_provider import LocalAssetProvider
from autopilot.providers.openverse_provider import OpenverseAssetProvider
from autopilot.providers.pexels_provider import PexelsAssetProvider
from autopilot.providers.pixabay_provider import PixabayAssetProvider
from autopilot.core.infographics_generator import InfographicsAssetProvider
from autopilot.core.asset_scoring import score_candidates
from autopilot.core.rights_gate import evaluate_rights_gate
from autopilot.core.visual_semantic import (
    visual_semantic_verify,
    visual_semantic_enabled,
    visual_semantic_min_similarity,
    VisualSemanticUnavailable,
)
from autopilot.core.asset_cache import safe_download_media, compute_file_sha256, compute_image_phash, AssetCache
from autopilot.core.asset_normalizer import normalize_asset
from autopilot.core.media_inspection import inspect_media
from autopilot.core.artifacts import job_artifact_dir
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
    elif name_clean == "pexels":
        return PexelsAssetProvider()
    elif name_clean == "pixabay":
        return PixabayAssetProvider()
    elif name_clean == "infographics":
        return InfographicsAssetProvider()
    elif name_clean == "local":
        return LocalAssetProvider()
    else:
        raise ValueError(f"Unknown asset provider: '{name}'. Available: 'local', 'openverse', 'pexels', 'pixabay', 'infographics'")


def build_asset_cascade(primary_name: str = "pexels", allow_fallback: bool = True) -> List[AssetProvider]:
    """Build the P0 video-first provider cascade.

    Spec order: Pexels VIDEO -> Pixabay VIDEO -> Openverse VIDEO -> Openverse IMAGE
    -> Autopilot infographic/diagram -> local safety net.

    Providers are only included when their live health check passes (correct
    endpoint, valid key, reachable). A provider present in source code but not
    healthy is NOT considered integrated.
    """
    if not allow_fallback:
        return []
    order = [p.strip().lower() for p in str(getattr(CONFIG, "asset_provider_cascade", "pexels,pixabay,openverse,infographics")).split(",") if p.strip()]
    # Ensure primary is first
    primary = (primary_name or "pexels").strip().lower()
    if primary in order:
        order.remove(primary)
    order.insert(0, primary)

    cascade: List[AssetProvider] = []
    seen: set[str] = set()
    for name in order:
        if name in seen or name == "local":
            continue
        seen.add(name)
        try:
            prov = get_asset_provider(name)
            healthy = prov.health_check().healthy
            if healthy:
                cascade.append(prov)
        except Exception:
            continue
    # Safety nets (always last): generated infographic, then local fixtures.
    try:
        cascade.append(InfographicsAssetProvider())
    except Exception:
        pass
    try:
        cascade.append(LocalAssetProvider())
    except Exception:
        pass
    return cascade


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


# Narrow visual terms mapped to broader, better-stocked stock-footage
# equivalents. Consulted only by derive_broader_query for the second-chance
# search (plan 1.3) so a weak first pass can retry with wider terms.
_VISUAL_SYNONYM_BROADENING: Dict[str, str] = {
    "battlefield": "war soldiers",
    "cavalry": "soldiers horseback",
    "trench": "war soldiers",
    "warship": "ship fleet",
    "aircraft": "plane",
    "airplane": "plane",
    "fortress": "castle",
    "ruins": "ancient ruins",
    "portrait": "person face",
    "manuscript": "old book pages",
    "artifact": "museum object",
    "spaceship": "rocket",
    "spacecraft": "rocket",
    "volcano": "mountain eruption",
    "laboratory": "science lab",
    "cathedral": "church building",
}


def derive_broader_query(scene: ScriptScene, topic: str) -> str:
    """Broader synonym query for the second-chance scene search (plan 1.3).

    Built from the scene's visual_intent widened through known broad
    synonyms and merged with the topic core, so the retry searches the same
    visual subject with wider terms without drifting away from the video's
    subject. Deterministic: never uses narration words.
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
        "1", "2", "3", "4", "5", "6", "7", "8", "9", "10",
    }
    intent_words = [
        w for w in re.findall(r"\b\w+\b", (scene.visual_intent or "").lower())
        if w not in stop_words and len(w) > 2
    ]
    topic_words = [
        w for w in re.findall(r"\b\w+\b", (topic or "").lower())
        if w not in stop_words and len(w) > 2
    ]

    terms: List[str] = []
    for word in intent_words + topic_words:
        expanded = _VISUAL_SYNONYM_BROADENING.get(word, word)
        for part in expanded.split():
            if part not in terms:
                terms.append(part)
    if not terms:
        return (topic or "").strip()
    return " ".join(terms[:7])


def process_scene_assets(
    script: ScriptDocument,
    job_id: str,
    provider_name: str = "local",
    db: Optional[DBManager] = None,
    dry_run: bool = False,
    search_only: bool = False,
    config: Optional[Config] = None,
    allow_fallback: bool = False,
) -> Tuple[List[AssetArtifact], Dict[str, Any]]:
    """Execute complete scene-to-asset acquisition pipeline.

    Returns:
        (artifacts_list, quality_report_dict)
    """
    cfg = config or CONFIG
    # Plan 1.3: a verified CLIP score between the hard floor and this
    # threshold triggers one second-chance broader query search for the scene.
    strong_threshold = float(getattr(cfg, "visual_semantic_strong_threshold", 0.28))
    db_mgr = db or DBManager(cfg.db_path)
    db_mgr.init_schema()
    db_mgr.create_job(job_id=job_id, topic=script.topic)

    primary_provider = get_asset_provider(provider_name)
    fallback_providers: List[AssetProvider] = build_asset_cascade(provider_name, allow_fallback)

    report = AssetQualityReport(job_id=job_id)
    artifacts: List[AssetArtifact] = []
    seen_checksums: set[str] = set()
    # P0: stable provider-side asset refs chosen for earlier scenes in THIS job.
    # A candidate matching one of these is hard-excluded so a single stock clip
    # can never be selected twice in the same video. The sha256-based dedup in
    # score_candidates cannot do this because the hash is only known after a
    # download, whereas provider_asset_ref is known at search time.
    chosen_asset_refs: set[str] = set()

    job_asset_dir = job_artifact_dir(job_id, base_dir=cfg.get_artifacts_dir()) / "assets"
    job_asset_dir.mkdir(parents=True, exist_ok=True)

    cache = AssetCache(cache_dir=cfg.get_asset_cache_dir())

    for scene in script.scenes:
        report.total_requests += 1

        # Derive visual subject query specific to this scene
        query = derive_visual_subject_query(scene, script.topic)
        request_id = f"req-{job_id}-{scene.scene_id}"

        # Helper to score candidates and select all allowed candidates
        def get_cleared_candidates(cands: List[AssetCandidate], q_str: str, active_pname: str, allow_duplicate: bool = False) -> List[Tuple[AssetCandidate, str]]:
            if active_pname != "local":
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
            cleared = []
            for cand in scored:
                # P0 hard dedup: a clip already chosen for another scene in THIS
                # job is never re-selected. provider_asset_ref is the stable
                # provider-side ID known at search time; source_id is the legacy
                # key. This is what actually stops the "same clip twice" defect
                # -- the soft score penalty alone cannot.
                asset_ref = (cand.provenance.provider_asset_ref if cand.provenance else None) or (
                    cand.source_id if cand.source_id else None
                )
                if asset_ref and asset_ref in chosen_asset_refs and not allow_duplicate:
                    report.rejections.append({
                        "candidate_id": cand.candidate_id,
                        "scene_id": scene.scene_id,
                        "reason": f"Duplicate of an asset already selected for this job (ref={asset_ref})",
                        "title": cand.title,
                    })
                    continue

                if active_pname not in ("local", "infographics") and cand.score < 0.20:
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
                    cleared.append((cand, reason))
                else:
                    report.rejections.append({
                        "candidate_id": cand.candidate_id,
                        "scene_id": scene.scene_id,
                        "reason": "; ".join(gate_res.reasons),
                        "license": cand.license.license_name,
                        "rights_status": cand.license.rights_status,
                    })
            return cleared

        scene_success = False
        scene_errors = []

        # Iterate over provider cascade: primary provider first, then fallbacks
        for provider_idx, active_prov in enumerate([primary_provider] + fallback_providers):
            if scene_success:
                break

            active_pname = active_prov.provider_name
            active_query = query
            candidates: List[AssetCandidate] = []

            try:
                candidates = active_prov.search(
                    {"query": active_query, "aspect_ratio": "9:16", "visual_concept": active_query, "headline": scene.asset_query or active_query[:35], "subtext": scene.narration or active_query},
                    max_results=CONFIG.openverse_max_results,
                )
            except Exception as exc:
                scene_errors.append(f"{active_pname} search error: {exc}")
                continue

            report.total_candidates_found += len(candidates)

            # Fallback query if no candidates
            if not candidates:
                fallback_query = derive_deterministic_fallback_query(scene, script.topic)
                if fallback_query and fallback_query.lower() != active_query.lower():
                    try:
                        candidates = active_prov.search(
                            {"query": fallback_query, "aspect_ratio": "9:16", "visual_concept": fallback_query, "headline": scene.asset_query or fallback_query[:35], "subtext": scene.narration or fallback_query},
                            max_results=CONFIG.openverse_max_results,
                        )
                        if candidates:
                            active_query = fallback_query
                    except Exception:
                        pass

            # Core topic query if still no candidates and remote provider
            if not candidates and active_pname not in ("local", "infographics"):
                topic_stop = {"the", "a", "an", "is", "are", "surprising", "facts", "fact", "about", "top", "best", "things", "ways", "reasons"}
                topic_words = [w for w in re.findall(r"\b\w+\b", script.topic.lower()) if w not in topic_stop and len(w) > 2]
                topic_core = " ".join(topic_words[:2]) if topic_words else script.topic
                if topic_core and topic_core.lower() not in (active_query.lower(), fallback_query.lower() if 'fallback_query' in locals() else ""):
                    try:
                        candidates = active_prov.search(
                            {"query": topic_core, "aspect_ratio": "9:16"},
                            max_results=CONFIG.openverse_max_results,
                        )
                        if candidates:
                            active_query = topic_core
                    except Exception:
                        pass

            cleared_candidates = get_cleared_candidates(candidates, active_query, active_pname)
            if not cleared_candidates and candidates:
                # Every candidate was blocked solely by dedup (they all passed
                # the 0.20 floor and rights gate, or there is only one clip in
                # existence). A repeated asset is better than a failed scene,
                # so relax dedup for this scene and record why.
                relaxed = get_cleared_candidates(candidates, active_query, active_pname, allow_duplicate=True)
                if relaxed:
                    report.warnings.append(
                        f"Scene {scene.scene_id}: all candidates were already used by earlier "
                        f"scenes; re-using the best one rather than failing the scene."
                    )
                    cleared_candidates = relaxed
            if not cleared_candidates:
                continue

            # Try downloading and normalizing cleared candidates from this provider.
            # Entries are (candidate, selection_reason, preverified) where
            # preverified carries an already-downloaded, already gate-passed
            # weak candidate held as the second-chance fallback until the
            # broader-query candidates have had their chance (plan 1.3).
            pending_candidates: List[Tuple[AssetCandidate, str, Optional[Dict[str, Any]]]] = [
                (cand, reason, None) for cand, reason in cleared_candidates
            ]
            weak_fallback: Optional[Dict[str, Any]] = None
            second_chance_attempted = False
            pending_idx = 0
            while True:
                if pending_idx >= len(pending_candidates):
                    if scene_success or weak_fallback is None:
                        break
                    # Nothing from the broader search beat it: accept the weak
                    # asset that already cleared the hard floor, via one more pass.
                    pending_candidates.append(
                        (weak_fallback["candidate"], weak_fallback["selection_reason"], weak_fallback)
                    )
                    weak_fallback = None
                    continue
                selected_candidate, selection_reason, preverified = pending_candidates[pending_idx]
                pending_idx += 1
                if preverified is None:
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

                is_video = (selected_candidate.asset_type == "video") or (selected_candidate.source_url and str(selected_candidate.source_url).endswith((".mp4", ".mov", ".mkv", ".webm")))
                file_ext = ".mp4" if is_video else ".png"
                # Record the media type we actually downloaded, not whatever the
                # provider claimed: a video mislabeled as an image later reads as
                # a "static image" defect to creative QA and misrepresents the
                # asset in the render plan.
                detected_asset_type = "video" if is_video else "image"
                visual_evidence: Optional[Dict[str, Any]] = None

                if preverified is not None:
                    downloaded_path = Path(preverified["downloaded_path"])
                    visual_evidence = preverified.get("visual_evidence")
                else:
                    if search_only or dry_run:
                        # Register the would-be selection so later scenes are still
                        # deduped in search-only/dry-run mode (the full registration
                        # below only runs after a real download+normalize).
                        _search_ref = (selected_candidate.provenance.provider_asset_ref
                                       if selected_candidate.provenance else None) or (
                            selected_candidate.source_id if selected_candidate.source_id else None)
                        if _search_ref:
                            chosen_asset_refs.add(_search_ref)
                        scene_success = True
                        break

                    # 5. Safe Download & Cache
                    source_dest = job_asset_dir / f"src_{scene.scene_id}_{selected_candidate.candidate_id}{file_ext}"
                    try:
                        downloaded_path_str = active_prov.download(selected_candidate, str(source_dest))
                        downloaded_path = Path(downloaded_path_str)
                    except Exception as exc:
                        err_msg = f"Download failed for {selected_candidate.candidate_id} from {active_pname}: {exc}"
                        report.warnings.append(err_msg)
                        scene_errors.append(err_msg)
                        # The candidate is dropped WITHOUT ever reaching the
                        # pixel-level visual-semantic gate, so it stays UNVERIFIED.
                        # Record that explicitly instead of dropping it silently.
                        report.rejections.append({
                            "candidate_id": selected_candidate.candidate_id,
                            "scene_id": scene.scene_id,
                            "reason": (
                                "Unverified candidate dropped: download failed before the visual gate "
                                f"could verify it ({exc})"
                            ),
                            "title": selected_candidate.title,
                            "provider": str(active_pname),
                        })
                        continue

                    # 6. Media Inspection
                    inspection = inspect_media(downloaded_path)
                    if not inspection.get("valid"):
                        err_msg = f"Downloaded media inspection failed for scene {scene.scene_id}: {inspection.get('errors')}"
                        report.warnings.append(err_msg)
                        scene_errors.append(err_msg)
                        # Unusable media never reaches the visual gate either.
                        report.rejections.append({
                            "candidate_id": selected_candidate.candidate_id,
                            "scene_id": scene.scene_id,
                            "reason": (
                                "Unverified candidate dropped: media inspection failed before the "
                                f"visual gate could verify it ({inspection.get('errors')})"
                            ),
                            "title": selected_candidate.title,
                            "provider": str(active_pname),
                        })
                        continue

                    # 6b. P0 VISUAL-SEMANTIC VERIFICATION ON DOWNLOADED PIXELS.
                    # Real CLIP gate against scene narration + visual_intent + asset_query.
                    # Provider-generated tags are never used as semantic evidence.
                    # HARD FAILURE is preferable to an unrelated asset.
                    gated_providers = ("pexels", "pixabay", "openverse")
                    if active_pname in gated_providers and visual_semantic_enabled():
                        try:
                            visual_evidence = visual_semantic_verify(
                                downloaded_path,
                                query=active_query,
                                visual_intent=scene.visual_intent or "",
                                narration=scene.narration or "",
                                min_similarity=visual_semantic_min_similarity(),
                                max_video_frames=int(getattr(CONFIG, "visual_semantic_max_video_frames", 3)),
                            )
                        except VisualSemanticUnavailable as vs_exc:
                            # The authoritative semantic verifier cannot run. We must
                            # NOT accept unverified stock as semantically relevant.
                            err_msg = (
                                f"Visual-semantic verifier unavailable for scene {scene.scene_id} "
                                f"({active_pname}): {vs_exc}. Refusing to accept unverified asset."
                            )
                            report.errors.append(err_msg)
                            scene_errors.append(err_msg)
                            continue
                        except Exception as vs_exc:
                            err_msg = f"Visual-semantic verification error for scene {scene.scene_id}: {vs_exc}"
                            report.warnings.append(err_msg)
                            scene_errors.append(err_msg)
                            continue

                        if not visual_evidence.get("passed_gate"):
                            report.rejections.append({
                                "candidate_id": selected_candidate.candidate_id,
                                "scene_id": scene.scene_id,
                                "reason": (
                                    f"VISUAL GATE REJECT: {visual_evidence.get('reason')} "
                                    f"(query='{active_query}')"
                                ),
                                "title": selected_candidate.title,
                                "provider": active_pname,
                            })
                            # Do not fall through to "better to show something";
                            # try the next candidate/provider instead.
                            continue
                        # Real verification passed: record the authoritative score.
                        selected_candidate.provenance.semantic_score = visual_evidence.get("visual_semantic_score")
                        selected_candidate.provenance.visual_semantic = visual_evidence
                        # 6c. Second-chance query rewrite (plan 1.3): the asset
                        # cleared the hard floor but sits below the strong CLIP
                        # threshold. Hold it as the fallback, retry the scene
                        # search ONCE with a broader synonym query, and give those
                        # candidates their chance before accepting the weak asset.
                        weak_score = float(visual_evidence.get("visual_semantic_score") or 0.0)
                        if weak_score < strong_threshold and not second_chance_attempted:
                            second_chance_attempted = True
                            weak_fallback = {
                                "candidate": selected_candidate,
                                "selection_reason": selection_reason,
                                "downloaded_path": str(downloaded_path),
                                "visual_evidence": visual_evidence,
                            }
                            broader_query = derive_broader_query(scene, script.topic)
                            if broader_query and broader_query.lower() != active_query.lower():
                                try:
                                    retry_candidates = active_prov.search(
                                        {
                                            "query": broader_query,
                                            "aspect_ratio": "9:16",
                                            "visual_concept": broader_query,
                                            "headline": scene.asset_query or broader_query[:35],
                                            "subtext": scene.narration or broader_query,
                                        },
                                        max_results=CONFIG.openverse_max_results,
                                    )
                                    report.total_candidates_found += len(retry_candidates)
                                    cleared_retry = (
                                        get_cleared_candidates(retry_candidates, broader_query, active_pname)
                                        if retry_candidates
                                        else []
                                    )
                                    if cleared_retry:
                                        pending_candidates[pending_idx:pending_idx] = [
                                            (c, r, None) for c, r in cleared_retry
                                        ]
                                        report.warnings.append(
                                            f"Scene {scene.scene_id}: CLIP score {weak_score:.3f} is below the "
                                            f"strong threshold {strong_threshold:.3f}; second-chance search with "
                                            f"broader query {broader_query!r} queued {len(cleared_retry)} candidate(s)."
                                        )
                                except Exception as retry_exc:
                                    report.warnings.append(
                                        f"Scene {scene.scene_id}: second-chance broader search failed: {retry_exc}"
                                    )
                            # Do not accept the weak candidate yet: the broader
                            # candidates get the next chances in this loop.
                            continue

                source_sha256 = compute_file_sha256(downloaded_path)
                seen_checksums.add(source_sha256)

                # 7. Normalization
                normalized_dest = job_asset_dir / f"norm_{scene.scene_id}_{selected_candidate.candidate_id}{file_ext}"
                try:
                    normalized_path_str = active_prov.normalize(
                        str(downloaded_path),
                        str(normalized_dest),
                        asset_type=detected_asset_type,
                        target_width=CONFIG.asset_target_width,
                        target_height=CONFIG.asset_target_height,
                    )
                    normalized_path = Path(normalized_path_str)
                    norm_sha256 = compute_file_sha256(normalized_path)
                except Exception as exc:
                    err_msg = f"Normalization failed for scene {scene.scene_id} from {active_pname}: {exc}"
                    report.warnings.append(err_msg)
                    scene_errors.append(err_msg)
                    continue

                # 8. Create AssetArtifact & Provenance
                provenance = AssetProvenance(
                    provider=active_pname,
                    source_url=selected_candidate.source_url,
                    source_id=selected_candidate.source_id,
                    retrieval_timestamp=datetime.now(timezone.utc).isoformat(),
                    original_hash_sha256=source_sha256,
                    normalized_hash_sha256=norm_sha256,
                    job_id=job_id,
                    content_id=script.content_id,
                    scene_id=scene.scene_id,
                    semantic_score=selected_candidate.provenance.semantic_score,
                    visual_semantic=selected_candidate.provenance.visual_semantic,
                )

                asset_selection_reason = selection_reason
                if visual_evidence and visual_evidence.get("passed_gate"):
                    asset_selection_reason = (
                        f"{selection_reason} | VISUAL-SEMANTIC VERIFIED "
                        f"CLIP={visual_evidence.get('visual_semantic_score'):.3f} "
                        f"model={visual_evidence.get('model')}"
                    )

                artifact_id = f"art-{job_id}-{scene.scene_id}"
                artifact = AssetArtifact(
                    artifact_id=artifact_id,
                    job_id=job_id,
                    content_id=script.content_id,
                    scene_id=scene.scene_id,
                    source_path=str(downloaded_path.resolve()),
                    normalized_path=str(normalized_path.resolve()),
                    asset_type=detected_asset_type,
                    dimensions=AssetDimensions(
                        width=CONFIG.asset_target_width,
                        height=CONFIG.asset_target_height,
                    ),
                    media_info=AssetMediaInfo(
                        mime_type="video/mp4" if is_video else "image/png",
                        file_size_bytes=normalized_path.stat().st_size,
                    ),
                    checksum_sha256=norm_sha256,
                    provenance=provenance,
                    license=selected_candidate.license,
                    validated=True,
                    validation_result="PASS",
                    selection_reason=asset_selection_reason,
                    semantic_score=selected_candidate.provenance.semantic_score,
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
                    "provider": active_pname,
                })
                # Register the chosen asset so later scenes in this job are
                # hard-blocked from re-selecting it.
                _chosen_ref = (selected_candidate.provenance.provider_asset_ref
                               if selected_candidate.provenance else None) or (
                    selected_candidate.source_id if selected_candidate.source_id else None)
                if _chosen_ref:
                    chosen_asset_refs.add(_chosen_ref)
                if provider_idx > 0:
                    report.warnings.append(f"Scene {scene.scene_id} used fallback provider '{active_pname}'.")
                scene_success = True
                break

        # Final absolute safety fallback: if no provider succeeded, generate dedicated infographic
        if not scene_success and allow_fallback and not (search_only or dry_run):
            try:
                info_p = InfographicsAssetProvider()
                info_cands = info_p.search({
                    "visual_concept": query,
                    "headline": scene.asset_query or query[:35],
                    "subtext": scene.narration or query,
                })
                if info_cands:
                    cand = info_cands[0]
                    artifact_id = f"art-{job_id}-{scene.scene_id}"
                    normalized_path = Path(cand.path_local)
                    norm_sha256 = compute_file_sha256(normalized_path)
                    artifact = AssetArtifact(
                        artifact_id=artifact_id,
                        job_id=job_id,
                        content_id=script.content_id,
                        scene_id=scene.scene_id,
                        source_path=str(normalized_path.resolve()),
                        normalized_path=str(normalized_path.resolve()),
                        asset_type="image",
                        dimensions=AssetDimensions(width=CONFIG.asset_target_width, height=CONFIG.asset_target_height),
                        media_info=AssetMediaInfo(mime_type="image/png", file_size_bytes=normalized_path.stat().st_size),
                        checksum_sha256=norm_sha256,
                        provenance=cand.provenance,
                        license=cand.license,
                        validated=True,
                        validation_result="PASS",
                    )
                    artifacts.append(artifact)
                    db_mgr.record_asset_artifact(
                        job_id=job_id,
                        content_id=script.content_id,
                        scene_id=scene.scene_id,
                        artifact_path=str(normalized_path.resolve()),
                        asset_type=artifact.asset_type,
                        checksum=norm_sha256,
                        provenance_json=cand.provenance.model_dump_json(),
                        license_json=cand.license.model_dump_json(),
                    )
                    report.artifacts_created.append({
                        "artifact_id": artifact_id,
                        "scene_id": scene.scene_id,
                        "source_path": str(normalized_path.resolve()),
                        "normalized_path": str(normalized_path.resolve()),
                        "checksum_sha256": norm_sha256,
                        "provider": "infographics_safety_net",
                    })
                    report.warnings.append(f"Scene {scene.scene_id} generated safety-net infographic asset.")
                    scene_success = True
            except Exception as safety_exc:
                err_msg = f"Asset acquisition completely failed for scene {scene.scene_id}: {safety_exc}"
                report.errors.append(err_msg)

    report.completed_at = datetime.now(timezone.utc).isoformat()
    report.status = "completed" if len(report.errors) == 0 else ("partial" if artifacts else "failed")

    # Write report artifact
    report_path = job_asset_dir / "asset_quality_report.json"
    report.save(report_path)
    db_mgr.record_artifact(job_id, str(report_path), "quality")

    return artifacts, report.to_dict()
