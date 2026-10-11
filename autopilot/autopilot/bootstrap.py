"""One-command setup for PROJECT AUTOPILOT.

Installs every optional Round-2 dependency (``.[all]``), fills the BGM library
with mood-tagged beds, and prints a summary of what is now active.

Usage:
    python -m autopilot.bootstrap            # from the project root (autopilot/)

The BGM library is filled with the procedural backend by default (fast, always
works, royalty-free). To synthesize original generative beds instead, run:
    autopilot bgm fill --backend musicgen     # needs the bgm-gen extra
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _run(cmd: list[str], *, cwd: Path | None = None) -> int:
    print(f"\n$ {' '.join(str(c) for c in cmd)}")
    return subprocess.run([str(c) for c in cmd], cwd=str(cwd or PROJECT_ROOT)).returncode


def _install_extras() -> int:
    py = sys.executable
    if shutil.which("uv"):
        return _run(["uv", "pip", "install", "--python", py, ".[all]"])
    return _run([py, "-m", "pip", "install", ".[all]"])


def _fill_bgm_library() -> int:
    py = sys.executable
    return _run([py, "-m", "autopilot.cli.main", "bgm", "fill", "--backend", "procedural"])


def _summary() -> None:
    print("\n=== Setup summary ===")
    try:
        from autopilot.core.bgm_generator import available_bgm_backends
        from autopilot.core.audio_intelligence import demucs_available
        from autopilot.core.multimodal_qa import gemini_available
        from autopilot.core.keyword_scorer import pytrends_available
        from autopilot.providers.structured_llm import instructor_available

        rows = [
            ("BGM generative (MusicGen/stable-audio)", available_bgm_backends()),
            ("Demucs vocal-clarity QA", "available" if demucs_available() else "not installed"),
            ("Gemini multimodal QA + thumbnails", "available" if gemini_available() else "not installed"),
            ("pytrends YouTube volume (KGR)", "available" if pytrends_available() else "not installed"),
            ("instructor structured LLM", "available" if instructor_available() else "not installed (pydantic fallback active)"),
        ]
        for name, status in rows:
            print(f"  {name}: {status}")
    except Exception as exc:  # pragma: no cover - summary is best-effort
        print(f"  (summary unavailable: {exc})")

    print("\nNext steps:")
    print("  - Generative BGM beds:   autopilot bgm fill --backend musicgen")
    print("  - Generative thumbnails: set AUTOPILOT_THUMBNAIL_GENERATIVE=true (needs image-gen quota)")
    print("  - Reddit trend signals:  set AUTOPILOT_AUTONOMY_TREND_PROVIDER=reddit")
    print("  - Produce a Short:       autopilot run --topic \"...\" --render")


def main() -> int:
    print("=== PROJECT AUTOPILOT setup ===")
    print(f"Project root: {PROJECT_ROOT}")

    print("\n[1/2] Installing optional dependencies (.[all])...")
    rc = _install_extras()
    if rc != 0:
        print("  WARNING: dependency install returned non-zero; continuing.", file=sys.stderr)

    print("\n[2/2] Filling BGM library (procedural beds)...")
    rc2 = _fill_bgm_library()
    if rc2 != 0:
        print("  WARNING: bgm fill returned non-zero.", file=sys.stderr)

    _summary()
    return 0


if __name__ == "__main__":
    sys.exit(main())
