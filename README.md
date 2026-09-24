# PROJECT AUTOPILOT — Local-First Autonomous Content Engine

**PROJECT AUTOPILOT** is a high-performance, local-first desktop application and autonomous orchestration engine for automated, research-grounded short-form video generation (1080x1920 MP4) on Windows.

---

## Key Capabilities

- **Desktop Shell**: Tauri 2.0 native Windows desktop application with responsive UI, live system health matrix, stage timeline, queue inspector, publishing review, analytics attribution, and strategy tuning.
- **Production Pipeline**: Automated multi-stage video generation:
  - **Research**: Wikipedia API query and source acquisition with Crawl4AI fallback.
  - **Script**: 6–8 scene structured listicle generation with local Ollama (`qwen3:4b`).
  - **Voice**: Native Windows SAPI speech synthesis with per-scene duration probing.
  - **Assets**: Openverse verified vertical CC media retrieval, relevance scoring, and caching.
  - **Render**: MoneyPrinterTurbo (MPT) subprocess management and FFmpeg composition.
  - **Captions**: Monotonic progressive kinetic karaoke ASS subtitles and SRT alignment.
  - **QA Engine**: Multi-pass automated quality assurance (duration drift, silence, caption timing, cryptographic SHA-256 receipts).
- **Publishing Safety**: Fail-closed manual operator approval loop (`auto_publish=False`), cryptographic checksum binding, YouTube OAuth token management, duplicate upload protection.
- **Analytics & Strategy**: YouTube Analytics synchronization, lifetime performance metrics, bounded strategy learning with recency weighting.
- **Scheduler & Autonomy**: Cron and interval cadence dispatcher, Level 3 discovery/proposals, Level 4 production dispatch.

---

## System Requirements & Prerequisites

- **OS**: Windows 10/11 (64-bit)
- **Python**: Python 3.11+ (recommended: Python 3.14 with `uv`)
- **Node.js**: Node 18+ (for building frontend)
- **Rust / Cargo**: Rust 1.80+ (for building Tauri desktop shell)
- **External Binaries**:
  - `ffmpeg` on system `PATH`
  - `ollama` with `qwen3:4b` pulled (`ollama pull qwen3:4b`)
  - `MoneyPrinterTurbo` repository or standalone daemon

---

## Quick Start Guide

### 1. Backend Environment Setup

```powershell
cd autopilot
uv sync
```

### 2. Desktop Frontend & Tauri Build

```powershell
cd desktop
npm install
npm run build
```

### 3. Running Desktop Release Binary

```powershell
cd desktop/src-tauri
cargo build --release
.\target\release\autopilot-desktop.exe
```

---

## Architecture

```
[ Tauri 2.0 Native Shell ]
        │  ▲ (stdio newline JSON-RPC)
        ▼  │
[ Python Bridge Daemon ]
        │
  ┌─────┴──────────────────────────────────────────────────────┐
  ▼                                                            ▼
[ SQLite Database (WAL) ]                           [ Local Video Pipeline ]
  ├── queue_items & jobs                              ├── Research (Wikipedia)
  ├── workflow_events                                 ├── Script (Ollama LLM)
  ├── artifacts & QA reports                          ├── Voice (Windows SAPI)
  ├── publications & receipts                         ├── Assets (Openverse)
  └── analytics_snapshots                             ├── MPT & FFmpeg Render
                                                      └── QA Gate & Approval
```

---

## Verification & Testing

```powershell
# Python Backend Suite (878 tests)
cd autopilot
uv run pytest -q

# Desktop Vitest Suite (69 tests)
cd desktop
npm test
```
