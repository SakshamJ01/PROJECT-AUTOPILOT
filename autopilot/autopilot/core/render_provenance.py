"""P0 render provenance verification.

Proves that the assets a render *plan* claims to have used are actually present
in the rendered output file. Without this, an engine that silently re-fetches
its own footage (e.g. MoneyPrinterTurbo) produces a video that passes technical
QA while the CLIP/rights verification performed earlier described footage that
is not in the shipped file.

The check is perceptual (difference hash) rather than checksum based because the
renderer applies scale/crop/zoom motion, so a per-scene source asset is never
byte-identical to the corresponding region of the final video.

Fails closed: if a planned asset cannot be located in the render, the report is
not valid and the caller must not claim verified provenance.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

# A difference hash of an 8x8 grid is 64 bits. Measured on real output: scenes
# whose source asset is genuinely in the render score 0-3, while unrelated
# footage scores 23-34. A threshold of 12 sits in the middle of that gap with
# room for Ken Burns motion/crop drift, and avoids false "found" verdicts.
DEFAULT_MATCH_THRESHOLD = 12
HASH_WIDTH = 9
HASH_HEIGHT = 8
# Hash only the central visual band. The renderer burns a badge near the top
# and kinetic captions in the lower third, and those overlays are identical for
# every scene; including them would make every scene look alike (or alike in the
# wrong way) and would measure overlay text instead of source footage.
HASH_CROP = "crop=iw:ih*0.47:0:ih*0.21"


class RenderProvenanceError(RuntimeError):
    """Raised when provenance cannot be established at all."""


@dataclass
class SceneProvenance:
    scene_id: str
    asset_path: Optional[str]
    present: bool
    best_distance: Optional[int]
    threshold: int
    matched_at_sec: Optional[float] = None
    samples_compared: int = 0
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RenderProvenanceReport:
    rendered_path: str
    production_engine: Optional[str] = None
    threshold: int = DEFAULT_MATCH_THRESHOLD
    scenes: List[SceneProvenance] = field(default_factory=list)
    render_frames_hashed: int = 0
    applicable: bool = False
    valid: bool = False
    claimed_scene_count: int = 0
    verified_scene_count: int = 0
    missing_scene_ids: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["scenes"] = [s.to_dict() for s in self.scenes]
        return payload

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=2)


def _probe_duration(video: Path) -> float:
    """Return media duration in seconds, or 0.0 when unknown."""
    cmd = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(video),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    try:
        return float((proc.stdout or "").strip())
    except (TypeError, ValueError):
        return 0.0


def _frame_signatures(video: Path, sample_count: int) -> List[Any]:
    """Return evenly spaced difference-hash signatures for a video/image.

    Frames are read as raw grayscale bytes (9x8) so no temporary image files or
    Pillow dependency is required.

    Sampling is spread across the whole clip. Note that ffmpeg's ``fps`` filter
    takes a frame *rate*, not a frame count, so the rate is derived from the
    probed duration; passing the desired count directly would only ever read the
    first second of footage.
    """
    if sample_count < 1:
        raise RenderProvenanceError("sample_count must be >= 1")
    duration = _probe_duration(video)
    # For a still image ffprobe reports no duration; a small rate is fine
    # because the decoder simply emits the single frame.
    rate = (sample_count / duration) if duration > 0 else 1.0
    cmd = [
        "ffmpeg", "-v", "error", "-i", str(video),
        # flags=area does proper area averaging; the default bicubic scaler
        # aliases heavily when reducing 1080p to 9x8 and makes the hash unstable.
        "-vf", f"fps={rate:.8f},{HASH_CROP},scale={HASH_WIDTH}:{HASH_HEIGHT}:flags=area,format=gray",
        "-frames:v", str(sample_count),
        "-f", "rawvideo", "-pix_fmt", "gray", "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, timeout=300)
    frame_bytes = HASH_WIDTH * HASH_HEIGHT
    raw = proc.stdout or b""
    usable = len(raw) - (len(raw) % frame_bytes)
    signatures: List[Any] = []
    try:
        import numpy as np
    except Exception as exc:  # pragma: no cover - numpy is a hard dependency
        raise RenderProvenanceError(f"numpy is required for render provenance: {exc}") from exc
    for offset in range(0, usable, frame_bytes):
        block = np.frombuffer(raw, dtype=np.uint8, count=frame_bytes, offset=offset)
        grid = block.reshape(HASH_HEIGHT, HASH_WIDTH)
        signatures.append((grid[:, 1:] > grid[:, :-1]).flatten())
    if not signatures:
        detail = (proc.stderr or b"").decode("utf-8", "ignore").strip()[:400]
        raise RenderProvenanceError(f"no frames decoded from {video}: {detail}")
    return signatures


def _hamming(a: Any, b: Any) -> int:
    return int((a != b).sum())


def verify_asset_presence(
    rendered_path: str | Path,
    scenes: Sequence[Dict[str, Any]],
    render_sample_count: int = 48,
    asset_sample_count: int = 12,
    threshold: int = DEFAULT_MATCH_THRESHOLD,
    production_engine: Optional[str] = None,
) -> RenderProvenanceReport:
    """Verify every planned scene asset actually appears in ``rendered_path``.

    ``scenes`` accepts the render plan scene dicts and needs ``scene_id`` plus
    one of ``asset_path`` / ``normalized_path``.

    Only scenes that *claim* an asset are treated as provenance obligations. A
    scene with no asset path claims nothing, so it is recorded as unverifiable
    rather than failed; the renderer already refuses to render such a scene, so
    this state cannot silently ship in production. When at least one scene
    claims an asset, every claim must be confirmed or the report is invalid.
    """
    rendered = Path(rendered_path)
    report = RenderProvenanceReport(
        rendered_path=str(rendered),
        production_engine=production_engine,
        threshold=threshold,
    )
    if not rendered.exists() or rendered.stat().st_size == 0:
        report.errors.append("rendered file is missing or zero bytes")
        return report

    try:
        render_sigs = _frame_signatures(rendered, render_sample_count)
    except RenderProvenanceError as exc:
        report.errors.append(str(exc))
        return report
    report.render_frames_hashed = len(render_sigs)

    for scene in scenes:
        scene_id = str(scene.get("scene_id") or f"scene-{len(report.scenes) + 1}")
        asset = scene.get("asset_path") or scene.get("normalized_path")
        entry = SceneProvenance(
            scene_id=scene_id,
            asset_path=str(asset) if asset else None,
            present=False,
            best_distance=None,
            threshold=threshold,
        )
        report.scenes.append(entry)

        if not asset:
            # Nothing was claimed for this scene, so it is not a provenance
            # failure; the renderer itself rejects asset-less scenes.
            entry.error = "no asset claimed for this scene (nothing to verify)"
            continue
        asset_path = Path(asset)
        report.claimed_scene_count += 1
        if not asset_path.exists() or asset_path.stat().st_size == 0:
            entry.error = "claimed asset file is missing or zero bytes"
            report.missing_scene_ids.append(scene_id)
            continue

        try:
            asset_sigs = _frame_signatures(asset_path, asset_sample_count)
        except RenderProvenanceError as exc:
            entry.error = str(exc)
            report.missing_scene_ids.append(scene_id)
            continue

        entry.samples_compared = len(asset_sigs) * len(render_sigs)
        best = min(
            ((_hamming(a, r), i) for a in asset_sigs for i, r in enumerate(render_sigs)),
            key=lambda pair: pair[0],
        )
        entry.best_distance = int(best[0])
        entry.matched_at_sec = round(best[1], 3)
        entry.present = entry.best_distance <= threshold
        if not entry.present:
            report.missing_scene_ids.append(scene_id)
        else:
            report.verified_scene_count += 1

    # The gate only applies when at least one scene actually claims an asset.
    report.applicable = report.claimed_scene_count > 0
    report.valid = bool(
        report.applicable
        and not report.missing_scene_ids
        and not report.errors
    )
    return report


def write_provenance_report(report: RenderProvenanceReport, path: str | Path) -> Path:
    """Persist the provenance report next to the rendered artifact."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report.to_json(), encoding="utf-8")
    return out


def summarize(report: RenderProvenanceReport) -> str:
    """One-line human summary for logs."""
    if not report.scenes:
        return "render provenance: no scenes to verify"
    if not report.applicable:
        return (
            "render provenance: NOT APPLICABLE (no scene claimed an asset, so there is "
            "no verified-asset claim to confirm)"
        )
    status = "VERIFIED" if report.valid else "UNVERIFIED"
    parts = [
        f"render provenance: {status} "
        f"({report.verified_scene_count}/{report.claimed_scene_count} claimed assets found in render)"
    ]
    for s in report.scenes:
        if s.present:
            parts.append(f"  {s.scene_id}: found (hamming={s.best_distance}<={s.threshold})")
        elif s.error and s.error.startswith("no asset claimed"):
            parts.append(f"  {s.scene_id}: no asset claimed (skipped)")
        else:
            reason = s.error or f"best hamming={s.best_distance} > {s.threshold}"
            parts.append(f"  {s.scene_id}: MISSING ({reason})")
    return "\n".join(parts)
