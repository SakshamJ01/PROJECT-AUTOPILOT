"""Stale Artifact Protection (P0).

Guarantees that no production stage silently reuses an artifact produced by
an OLDER pipeline version. Every fresh validation run must use a new job_id,
new script, new TTS, new assets, new render, and new QA receipt.

An artifact is considered reusable ONLY when it explicitly proves
compatibility with the CURRENT pipeline version. Otherwise it is invalidated.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from pydantic import BaseModel

# The single source of truth for the current production pipeline version.
# Bumped whenever the production path changes in a compatibility-breaking
# way (asset semantics, caption engine, audio graph, provenance schema...).
PIPELINE_VERSION = "v9.0.1-p1"


class ArtifactCompatibility(BaseModel):
    pipeline_version: str
    created_at: str
    job_id: str
    artifact_kind: str


def current_pipeline_version() -> str:
    return PIPELINE_VERSION


def record_compatibility(job_id: str, artifact_kind: str) -> Dict[str, Any]:
    """Stamp an artifact record with the CURRENT pipeline version."""
    return {
        "pipeline_version": PIPELINE_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "job_id": job_id,
        "artifact_kind": artifact_kind,
    }


def is_artifact_compatible(meta: Optional[Dict[str, Any]]) -> bool:
    """Return True iff the artifact metadata explicitly matches this version.

    Missing metadata, a different version, or a 'legacy' marker all count as
    incompatible — the artifact is stale and must be regenerated.
    """
    if not meta or not isinstance(meta, dict):
        return False
    if str(meta.get("pipeline_version", "")).lower() in ("legacy", "unknown", ""):
        return False
    return str(meta.get("pipeline_version")) == PIPELINE_VERSION


def must_invalidate(meta: Optional[Dict[str, Any]]) -> bool:
    """Explicit invalidation decision used by the pipeline resume logic."""
    return not is_artifact_compatible(meta)


def load_sidecar_compatibility(artifact_path: str | Path) -> Optional[Dict[str, Any]]:
    """Read a .pipeline.json sidecar next to an artifact, if present."""
    p = Path(str(artifact_path))
    side = p.with_name(p.name + ".pipeline.json")
    if not side.exists():
        return None
    try:
        import json

        return json.loads(side.read_text(encoding="utf-8"))
    except Exception:
        return None


def write_sidecar_compatibility(artifact_path: str | Path, job_id: str, artifact_kind: str) -> str:
    """Write a .pipeline.json sidecar proving current-version compatibility."""
    import json

    p = Path(str(artifact_path))
    side = p.with_name(p.name + ".pipeline.json")
    side.write_text(
        json.dumps(record_compatibility(job_id, artifact_kind), indent=2),
        encoding="utf-8",
    )
    return str(side)


__all__ = [
    "PIPELINE_VERSION",
    "current_pipeline_version",
    "record_compatibility",
    "is_artifact_compatible",
    "must_invalidate",
    "load_sidecar_compatibility",
    "write_sidecar_compatibility",
    "ArtifactCompatibility",
]
