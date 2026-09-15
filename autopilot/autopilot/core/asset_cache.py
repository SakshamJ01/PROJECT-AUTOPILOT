"""Safe media downloader and durable asset cache manager.
Provides streaming downloads with size limits, atomic file writes, checksums,
path safety, and deduplication.
"""
from __future__ import annotations
import os
import re
import json
import shutil
import hashlib
import tempfile
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any, Tuple

from autopilot.core.config import CONFIG


def compute_file_sha256(file_path: str | Path) -> str:
    """Compute exact SHA-256 hash of a file on disk."""
    p = Path(file_path)
    if not p.exists() or not p.is_file():
        raise FileNotFoundError(f"File not found: {file_path}")
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def compute_image_phash(image_path: str | Path) -> Optional[str]:
    """Compute a lightweight 64-bit average perceptual hash (aHash) for an image."""
    try:
        from PIL import Image
        with Image.open(image_path) as img:
            # Resize to 8x8 grayscale
            resized = img.convert("L").resize((8, 8), Image.Resampling.BILINEAR)
            # Use list of pixel values
            pixels = [resized.getpixel((x, y)) for y in range(8) for x in range(8)]
            avg = sum(pixels) / 64.0
            bits = "".join("1" if p >= avg else "0" for p in pixels)
            # Hex string
            hex_val = f"{int(bits, 2):016x}"
            return hex_val
    except Exception:
        return None


def sanitize_filename(name: str) -> str:
    """Strip path traversal characters and unsafe symbols from filename."""
    clean = name.replace("/", "_").replace("\\", "_")
    clean = re.sub(r"[^\w\-_.]", "_", clean)
    clean = re.sub(r"_+", "_", clean).strip("_.")
    return clean[:128] or "media_asset"


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Custom redirect handler that enforces max redirect limit."""
    def __init__(self, max_redirects: int = 5):
        super().__init__()
        self.max_redirects = max_redirects
        self.redirect_count = 0

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.redirect_count += 1
        if self.redirect_count > self.max_redirects:
            raise urllib.error.HTTPError(
                req.full_url, code, f"Exceeded max redirects ({self.max_redirects})", headers, fp
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def url_cache_key(url: str) -> str:
    """Derive deterministic cache key from media URL."""
    return hashlib.sha256(url.strip().encode("utf-8")).hexdigest()


class AssetCache:
    """Manages cached media files in artifacts/asset_cache/."""

    def __init__(self, cache_dir: Optional[Path] = None):
        self.cache_dir = cache_dir or CONFIG.get_asset_cache_dir()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.metadata_dir = self.cache_dir / "meta"
        self.metadata_dir.mkdir(parents=True, exist_ok=True)

    def _meta_path(self, key: str) -> Path:
        return self.metadata_dir / f"{key}.json"

    def get(self, url: str) -> Optional[Tuple[Path, Dict[str, Any]]]:
        """Check cache for URL. Returns (cached_file_path, metadata) or None."""
        key = url_cache_key(url)
        meta_file = self._meta_path(key)
        if not meta_file.exists():
            return None
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            cached_file = Path(meta.get("cached_path", ""))
            if cached_file.exists() and cached_file.stat().st_size > 0:
                # Verify checksum if recorded
                expected_sha = meta.get("checksum_sha256")
                if expected_sha:
                    actual_sha = compute_file_sha256(cached_file)
                    if actual_sha != expected_sha:
                        # Corrupted or modified on disk; invalidate
                        self.invalidate(url)
                        return None
                meta["cache_status"] = "CACHE_HIT"
                return cached_file, meta
        except Exception:
            self.invalidate(url)
        return None

    def put(
        self,
        url: str,
        file_path: Path,
        content_type: str = "application/octet-stream",
        provider: str = "openverse",
        source_id: str = "",
    ) -> Path:
        """Store a downloaded file into the cache."""
        key = url_cache_key(url)
        sha256 = compute_file_sha256(file_path)
        ext = file_path.suffix or ".bin"
        cached_file = self.cache_dir / f"{sha256}{ext}"

        if not cached_file.exists():
            shutil.copy2(str(file_path), str(cached_file))

        phash = compute_image_phash(cached_file)

        meta = {
            "cache_key": key,
            "url": url,
            "provider": provider,
            "source_id": source_id,
            "cached_path": str(cached_file.resolve()),
            "checksum_sha256": sha256,
            "phash": phash,
            "content_type": content_type,
            "file_size_bytes": cached_file.stat().st_size,
            "cached_at": datetime.now(timezone.utc).isoformat(),
            "cache_status": "CACHE_MISS",
        }
        self._meta_path(key).write_text(json.dumps(meta, indent=2), encoding="utf-8")
        return cached_file

    def invalidate(self, url: str) -> None:
        """Invalidate cache entry for given URL."""
        key = url_cache_key(url)
        meta_file = self._meta_path(key)
        if meta_file.exists():
            meta_file.unlink()


def safe_download_media(
    url: str,
    out_path: str | Path,
    max_bytes: Optional[int] = None,
    timeout: Optional[float] = None,
    max_redirects: Optional[int] = None,
    expected_hash: Optional[str] = None,
    use_cache: bool = True,
    cache: Optional[AssetCache] = None,
) -> str:
    """Download a media URL safely with streaming, limits, and caching.

    Returns:
        Absolute string path to destination file.
    """
    out_dest = Path(out_path)
    out_dest.parent.mkdir(parents=True, exist_ok=True)

    # Local file URL or local path handling
    if url.startswith("file://") or (not url.startswith("http://") and not url.startswith("https://")):
        local_src = Path(url.replace("file://", ""))
        if not local_src.exists():
            raise FileNotFoundError(f"Local source file not found: {url}")
        shutil.copy2(str(local_src), str(out_dest))
        return str(out_dest.resolve())

    max_bytes = max_bytes or CONFIG.asset_max_download_bytes
    timeout = timeout or CONFIG.openverse_timeout
    max_redirects = max_redirects or CONFIG.asset_max_redirects
    asset_cache = cache or AssetCache()

    # Check cache
    if use_cache:
        cached_result = asset_cache.get(url)
        if cached_result:
            cached_path, _ = cached_result
            shutil.copy2(str(cached_path), str(out_dest))
            return str(out_dest.resolve())

    # Create safe opener
    redirect_handler = SafeRedirectHandler(max_redirects=max_redirects)
    opener = urllib.request.build_opener(redirect_handler)
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "PROJECT-AUTOPILOT/1.0 (local-content-factory; safe-media-downloader)",
            "Accept": "image/*,video/*,audio/*,*/*",
        },
    )

    # Atomic write to temporary file in destination directory
    tmp_fd, tmp_path_str = tempfile.mkstemp(
        dir=str(out_dest.parent),
        prefix=f".tmp_{out_dest.stem}_",
        suffix=out_dest.suffix or ".tmp",
    )
    os.close(tmp_fd)
    tmp_path = Path(tmp_path_str)

    try:
        with opener.open(req, timeout=timeout) as response:
            content_type = response.headers.get("Content-Type", "application/octet-stream")
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > max_bytes:
                raise ValueError(
                    f"Remote file size ({content_length} bytes) exceeds limit ({max_bytes} bytes)"
                )

            bytes_received = 0
            hasher = hashlib.sha256()

            with open(tmp_path, "wb") as f_out:
                while True:
                    chunk = response.read(65536)
                    if not chunk:
                        break
                    bytes_received += len(chunk)
                    if bytes_received > max_bytes:
                        raise ValueError(f"Download exceeded maximum permitted size ({max_bytes} bytes)")
                    hasher.update(chunk)
                    f_out.write(chunk)

            if bytes_received == 0:
                raise ValueError("Downloaded zero bytes from media URL")

            sha256 = hasher.hexdigest()
            if expected_hash and sha256 != expected_hash:
                raise ValueError(
                    f"Checksum mismatch: expected {expected_hash}, got {sha256}"
                )

        # Store in cache
        cached_stored = asset_cache.put(url, tmp_path, content_type=content_type)
        # Move temporary file to destination
        if out_dest.exists():
            out_dest.unlink()
        shutil.move(str(tmp_path), str(out_dest))
        return str(out_dest.resolve())

    except Exception as exc:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass
        raise exc
