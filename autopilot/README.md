# PROJECT AUTOPILOT

Local-first, autonomous AI Shorts production system. End-to-end pipeline: research → script → voice → assets → render → QA → publish → analytics → learn. Python 3.11+, FFmpeg renderer, SQLite persistence, kinetic captions, beat-synced BGM, claim verification, dual-engine QA (technical + creative), and a policy-gated autonomy loop.

**Design principles**

- Local-first: no cloud message queues, no Docker requirement, no mandatory paid APIs
- Rights-aware: every asset carries provenance + license metadata; unknown/rejected licenses are blocked
- Resumable & idempotent: deterministic artifact paths, SQLite idempotency keys, crash-safe stage resume
- Fail-soft optional deps: every optional integration degrades to a safe fallback when its package or credential is missing
- Human review is first-class: publishing requires an explicit operator approval gate by default

---

## Quick start

```powershell
cd autopilot

# One-command setup: installs .[all] extras, generates procedural BGM library, prints activation summary
.venv\Scripts\python.exe -m autopilot.bootstrap
# or: .\setup.ps1

# Configure credentials (copy .env.example if present, or set env vars)
# Required for real runs: at least one LLM key (GEMINI_API_KEY / ATRIA_API_KEY / ...)
# Optional asset keys: PEXELS_API_KEY, PIXABAY_API_KEY (Openverse/Archive.org/Wikimedia need none)

# Health check
.venv\Scripts\python.exe -m autopilot health

# First end-to-end run (private Short)
.venv\Scripts\python.exe -m autopilot run --topic "How Roman Aqueducts Moved Water Uphill" --llm-provider gemini --asset-provider pexels --tts-provider edge_tts --research-provider wikipedia --render
```

YouTube publishing needs a one-time OAuth flow (opens a browser):

```powershell
.venv\Scripts\python.exe -m autopilot youtube-auth
```

---

## Architecture

```text
  [ TOPIC ]
      │
      ▼
  [ Research ]          Wikipedia / Crawl4AI → evidence records
      │
      ▼
  [ Script ]            LLM → ScriptDocument (typed scenes, visual queries, narration)
      │                  + claim verification against research evidence
      ▼
  [ Voice ]             Edge TTS / Kokoro / SAPI → WAV + faster-whisper word timestamps
      │
      ▼
  [ Assets ]            Provider cascade → CLIP verification → rights gate → normalize 9:16
      │                  Pexels → Pixabay → Openverse → Archive.org → Wikimedia → Infographics
      ▼
  [ Render ]            FFmpeg: scenes + kinetic captions + BGM bed + beat-synced SFX + ducking
      │
      ▼
  [ QA ]                Technical (14 checks) + Creative (CLIP, pacing, captions, demucs, Gemini)
      │                  Composite publish-readiness gate (fail-closed)
      ▼
  [ Publish ]           Operator approval → YouTube Data API v3 resumable upload
      │                  SHA-256 integrity, deterministic idempotency, custom thumbnail
      ▼
  [ Analytics ]         YouTube Data API v3 sync → measured/derived/synthetic metrics
      │
      ▼
  [ Learn ]             Associational feedback → versioned strategies → next topics
```

### Pipeline stages & state machine

States: `IDEA → RESEARCHING → RESEARCHED → SCRIPTING → SCRIPTED → ASSET_PREPARING → ASSETS_READY → VOICING → VOICE_READY → EDITING → RENDERING → RENDERED → QA → APPROVED → PUBLISHING → PUBLISHED → LEARNED`

Failure states: `FAILED_RESEARCH`, `FAILED_SCRIPT`, `FAILED_ASSETS`, `FAILED_TTS`, `FAILED_RENDER`, `FAILED_QA`, `FAILED_PUBLISH`. Illegal transitions raise `ValueError`. Idempotent stays are allowed and logged.

### Persistence

SQLite (`artifacts/autopilot.db`, schema v11): jobs, workflow_events, artifacts, errors, research, voice, assets, qa_runs/checks/findings/metrics, publish_records/attempts/approvals, queue_items, batch_manifests, analytics snapshots, autonomy runs/signals/proposals/strategies, channel profiles/versions/quotas.

---

## Asset providers

Default cascade (`ASSET_PROVIDER_CASCADE`): `pexels,pixabay,openverse,archive_org,wikimedia_commons,infographics`

| Provider | Media | API key | Rights |
|----------|-------|---------|--------|
| **Pexels** | video | `PEXELS_API_KEY` | Pexels License (free commercial use) |
| **Pixabay** | video | `PIXABAY_API_KEY` | Pixabay License |
| **Openverse** | image | none | CC / PD, rights classified |
| **Archive.org** | video+image | none | Public domain / CC collections |
| **Wikimedia Commons** | image+video | none | CC / PD, attribution preserved |
| **Infographics** | procedural | none | Generated, fully owned |
| **Local** | fixtures | none | Offline deterministic test assets |

**Pipeline per scene:** cascade search → deterministic scoring (keyword relevance, aspect, resolution, rights, duplicate avoidance) → rights gate → safe download (size limits, timeout, SHA-256, disk cache) → normalize to 1080×1920 9:16 → CLIP semantic verification (ViT-B-32, floor 0.21) → DB persistence.

**Rights gate:** `VERIFIED` passes. `PARTIALLY_VERIFIED` passes only when `RIGHTS_POLICY_ALLOW_PARTIAL=1` (default off, so it blocks). `UNKNOWN` / `REJECTED` always block.

---

## LLM providers

| Provider | CLI flag | Credentials |
|----------|----------|-------------|
| Google Gemini | `--llm-provider gemini` | `GEMINI_API_KEY` |
| Atria | `--llm-provider atria` | `ATRIA_API_KEY` (+ endpoint/model) |
| OpenRouter | `--llm-provider openrouter` | `OPENROUTER_API_KEY`, `OPENROUTER_MODEL` |
| Ollama (local) | `--llm-provider ollama` | `OLLAMA_BASE_URL`, `OLLAMA_MODEL` |
| OpenAI-compatible | `--llm-provider openai_compatible` | `OPENAI_BASE_URL`, `OPENAI_MODEL`, key as needed |
| Mock | `--llm-provider mock` | none (deterministic fixtures) |

All providers parse into the canonical `ScriptDocument` (typed scenes, visual queries, narration, grounding provenance). `providers/structured_llm.py` enforces pydantic schemas with corrective retry; instructor (`.[structured]`) unlocks native structured mode but is not required. Secrets are redacted in logs and error traces; an unknown provider/model name fails loudly instead of silently resolving to a mock.

**Claim verification** cross-checks generated narration against research evidence; flagged claims are recorded and fail-soft (script continues with warnings).

---

## TTS providers

| Provider | CLI flag | Notes |
|----------|----------|-------|
| Edge TTS | `--tts-provider edge_tts` | Free neural voices (default for real runs) |
| Kokoro | `--tts-provider kokoro` | Local ONNX (`kokoro-onnx`) |
| Windows SAPI | `--tts-provider sapi` | Offline system voices |
| Mock | `--tts-provider mock` | Synthetic sine tone |

Faster-Whisper provides word-level timestamps for kinetic caption alignment. Role-based prosody: hook +8%, payoff −5% speaking-rate variation.

---

## Research

- **Wikipedia** (`--research-provider wikipedia`): fast factual grounding
- **Crawl4AI** (`--research-provider crawl4ai` / `combined`): tier-2 deep web extraction (optional `crawl4ai` + Playwright)
- **Mock** (`mock_search`): offline deterministic fixtures
- Routing: `wikipedia_first` (default), `combined`, `crawl4ai_only`, `explicit_url` (URL topics)

Normalized into `ResearchEvidenceRecord` with canonical URLs, publisher metadata, excerpts, confidence.

---

## Rendering

Native FFmpeg renderer (`--production-engine ffmpeg`, default):

- Target: 1080×1920, 25/30 fps, H.264, yuv420p, AAC
- Kinetic captions: word-synced ASS/SRT, safe-zone placement, saliency collision avoidance
- BGM: mood-tagged library beds (`autopilot bgm list` / `bgm fill`), sidechain ducking during speech
- Beat-synced SFX cues aligned to the BGM beat grid
- Subprocess isolation, strict timeouts, SHA-256 output checksum
- Render provenance: perceptual (hamming distance) verification that claimed assets are actually in the render

MoneyPrinterTurbo remains available as an explicit opt-in production engine (`--production-engine moneyprinterturbo`); it fails loudly if unreachable — never a silent fallback.

---

## QA (dual engine)

### Technical QA (`core/qa_engine.py`) — 14 categories

Container integrity, video properties, audio properties, loudness (ITU-R BS.1770 / EBU R128: LUFS, LRA, true peak), dead-air/silence, black frames, freeze frames, duration/timeline drift, scene coverage, caption QA, caption visual safety, asset provenance + rights gate, render-plan consistency, duplicate output detection.

### Creative QA (`core/creative_qa.py`)

Hook effectiveness, visual-to-script alignment (CLIP), scene relevance (CLIP per-scene, hard-blocks below threshold), pacing & cadence, caption readability/placement (frame sampling + OCR), narrative coherence, audio balance (demucs vocal clarity, optional), visual continuity, dead/static sections, black frames, freeze sections, listicle structure. Optional Gemini multimodal overlay (`relevance/dynamism/hook`).

### Composite publish-readiness gate (`core/publish_readiness.py`)

Fail-closed: technical QA + creative QA + rights + render invariants. Rights/invariant failures are non-overridable. Creative hard blocks name the specific dimensions. `publish_allowed` is only true when the full gate passes.

```powershell
.venv\Scripts\python.exe -m autopilot qa --job <job_id> [--verbose] [--json] [--strict]
```

Artifacts under `artifacts/jobs/<job_id>/quality/`: `receipt.json`, `quality_report.json`, `findings.json`, `metrics.json`.

---

## Publishing

YouTube Data API v3 resumable upload (`autopilot/providers/youtube_publisher.py`):

- QA gate strictly enforced: valid receipt, `publish_allowed=True`, matching media SHA-256
- Deterministic idempotency: `sha256(content_id + render_hash + platform + visibility + title + description + sorted(tags))` — retries return the existing receipt unless `--force-retry`
- Operator approval gate: `publish_approvals` table; default flow is `approve` then `run`
- Visibility: `--private` (default), `--unlisted`, `--public`, `--schedule <ISO-8601>`
- Custom thumbnail upload (Gemini generative when enabled + quota allows; PIL composite fallback)
- Secrets never logged; `redact_secrets()` strips tokens from errors

```powershell
.venv\Scripts\python.exe -m autopilot publish approve <job_id> --by operator
.venv\Scripts\python.exe -m autopilot publish run <job_id> --private [--dry-run] [--json] [--force-retry]
.venv\Scripts\python.exe -m autopilot publish inspect <job_id>
```

Postiz sidecar (optional, AGPL-3.0 clean boundary — HTTP only) for multi-platform scheduling; fails soft with `POSTIZ_UNAVAILABLE` if offline.

---

## Batch & queue

Durable SQLite queue (`queue_items`, `batch_manifests`) — no Redis/Celery/RabbitMQ:

- Atomic worker claiming (`BEGIN IMMEDIATE`), lease heartbeats, crash recovery of stale leases
- Stage resume: completed stages skipped when valid artifacts exist
- Classified retries: retryable (exponential backoff), non-retryable, blocked; dead-letter after `max_attempts`
- Failure isolation: one job failing does not crash the worker
- Batch manifests (JSON/YAML) with dedup and dry-run validation

```powershell
.venv\Scripts\python.exe -m autopilot batch submit --file batch.json [--dry-run] [--force]
.venv\Scripts\python.exe -m autopilot queue status|list|inspect|run|retry|cancel
```

Windows Task Scheduler: run `-m autopilot queue run --once` hourly with "do not start a new instance" enabled.

---

## Autonomy & learning

Five levels (new installs default to Level 0 Manual):

| Level | Name | Behavior |
|-------|------|----------|
| 0 | Manual | Human supplies all topics |
| 1 | Discovery | System discovers/ranks topics, does not queue |
| 2 | Queue proposal | Candidates → reviewable proposal queue; operator approves |
| 3 | Guarded auto-queue | Approved ideas auto-enter M7 queue under quotas |
| 4 | Guarded auto-production | Autonomous production under strict quotas/concurrency |

**Trend discovery:** `MockTrendProvider` (offline) and `RedditTrendProvider` (`AUTOPILOT_AUTONOMY_TREND_PROVIDER=reddit`; fail-softs to `[]` when Reddit blocks the IP).

**Scoring:** explainable multi-factor heuristic (freshness, relevance, historical association, novelty, duplicate penalty) with machine-readable breakdown.

**Feedback:** associational (non-causal) observations, outlier damping, sample-size gates (≥3), quantile tiers (TOP/AVERAGE/LOW), immutable versioned strategies with rollback.

**Safety:** never rewrites code/prompts, never bypasses QA or rights gates, never auto-publishes publicly by default.

```powershell
.venv\Scripts\python.exe -m autopilot autonomy policy|strategy|run|proposals|show|approve|reject
```

---

## Multi-channel

One engine, many profiles. Channel profiles (`science_shorts`, `history_shorts`, `tech_shorts` presets, or custom YAML/JSON) control niche, persona, voice, visual brand, posting policy, autonomy policy, and quotas.

Strict isolation: strategy, daily quotas, voice/visual branding, and queue identity are all channel-scoped. Immutable profile versioning preserves historical lineage. Cross-channel duplication is protected via channel-scoped dedup keys.

```powershell
.venv\Scripts\python.exe -m autopilot channel list|show|create|enable|disable|validate|history|compare
```

---

## Analytics

YouTube Data API v3 sync (opt-in, OAuth):

- Metric provenance: `MEASURED` (API), `DERIVED` (engagement rate, velocity, etc.), `SYNTHETIC` (offline mock)
- Historical time-series snapshots (1h/24h/7d/28d/lifetime) — never overwritten
- Content ↔ performance linkage: topic → script → assets → render → QA → publication → performance
- Observes and reports only; does not autonomously act (learning loop is separate)

```powershell
.venv\Scripts\python.exe -m autopilot analytics sync|show|report
```

---

## Optional integrations (Round-2)

Everything below fails soft when its extra or credential is missing. See `requirements-optional.txt` and pyproject extras.

| Extra | Package | Unlocks |
|-------|---------|---------|
| `.[gemini]` | `google-genai` | Multimodal creative QA + generative thumbnails |
| `.[audio]` | `demucs` | Stem-based vocal-clarity QA |
| `.[seo]` | `pytrends` | YouTube search volume for KGR keyword scorer |
| `.[structured]` | `instructor` | Native structured LLM output |
| `.[bgm-gen]` | `transformers<5`, `torch`, `scipy` | MusicGen generative BGM beds |
| `.[all]` | all of the above | Everything |

- **BGM library:** procedural mood-pad synth (always available, royalty-free) + optional MusicGen. CLI: `autopilot bgm fill`, `autopilot bgm list`. Beat-synced SFX cues; sidechain ducking during speech.
- **Demucs vocal clarity:** measures voice-above-background dB; wired into creative QA audio-balance.
- **Gemini multimodal QA:** scores rendered video relevance/dynamism/hook.
- **Generative thumbnails:** `gemini-2.5-flash-image` with PIL composite fallback (free-tier image gen can 429).
- **Reddit trends:** stdlib urllib; enable via `AUTOPILOT_AUTONOMY_TREND_PROVIDER=reddit`.
- **KGR keyword scorer:** YouTube search interest via pytrends; offline heuristic without it.

---

## CLI reference

| Command | Purpose |
|---------|---------|
| `health` | System health: Python, FFmpeg, SQLite, providers, config |
| `run` | End-to-end production via PipelineOrchestrator |
| `produce` | Stage-oriented content package generation |
| `render` | Render from existing content package + assets |
| `research` | Research engine |
| `assets` | Asset engine (search / download / dry-run) |
| `qa` | QA engine + quality gates |
| `publish` | Publish (approve / run / inspect / reject) |
| `youtube-auth` | Interactive Google OAuth for YouTube |
| `bgm` | BGM library management (fill / list) |
| `batch` | Batch production management |
| `queue` | Durable worker queue |
| `inspect` | Job status inspection |
| `analytics` | Analytics sync / show / report |
| `autonomy` | Autonomy cycles, proposals, strategies |
| `schedule` | Recurring schedule orchestration |
| `channel` | Multi-channel profile management |

Run `<command> --help` for full flags.

---

## Configuration

Credentials via environment variables or `.env` (not committed). Key variables:

| Variable | Purpose |
|----------|---------|
| `GEMINI_API_KEY` | Gemini LLM + multimodal QA + image gen |
| `ATRIA_API_KEY` (+ endpoint/model) | Atria LLM |
| `OPENROUTER_API_KEY`, `OPENROUTER_MODEL` | OpenRouter |
| `OLLAMA_BASE_URL`, `OLLAMA_MODEL` | Ollama local |
| `PEXELS_API_KEY`, `PIXABAY_API_KEY` | Stock video |
| `YOUTUBE_*` | OAuth token path / client secrets |
| `ASSET_PROVIDER_CASCADE` | Provider order (default includes archive_org, wikimedia_commons) |
| `AUTOPILOT_AUTONOMY_TREND_PROVIDER` | `mock` (default) or `reddit` |
| `AUTOPILOT_DB_PATH`, `AUTOPILOT_ARTIFACTS_DIR` | Persistence locations |
| `AUTOPILOT_PRODUCTION_ENGINE` | `ffmpeg` (default) or `moneyprinterturbo` |

No secrets are hardcoded, logged, or stored in JSON artifacts.

---

## Testing

```powershell
# Full hermetic suite (~1190 tests, 100% offline; live tests deselected by default)
.venv\Scripts\python.exe -m pytest

# Opt-in live network tests
.venv\Scripts\python.exe -m pytest -m live

# Lint (error-class rules: syntax, statement bugs, undefined names)
uvx ruff check .
```

Tests cover: contracts, state machine, persistence, config security, provider registry, asset pipeline, QA engine, publisher idempotency, batch/queue, autonomy, channels, analytics, Round-2 integrations, Archive.org/Wikimedia providers.

---

## Verified live

Private Shorts published through the full pipeline (QA gates, BGM beds, demucs clarity, Gemini multimodal QA, custom thumbnails):

| Video | ID |
|-------|----|
| https://youtu.be/GkfUWjTunNs | Roman Ninth Legion |
| https://youtu.be/7o8qMgPngdw | Roman Aqueducts |
| https://youtu.be/R7TYxujDzW8 | Sky Color at Sunset |

---

## Project layout

```text
autopilot/
├── autopilot/
│   ├── cli/main.py            # CLI entry (all subcommands)
│   ├── core/                  # pipeline, contracts, state machine, QA, publisher, ...
│   ├── providers/             # LLM, TTS, asset, research, trend, publisher adapters
│   ├── db/manager.py          # SQLite persistence
│   └── bootstrap.py           # one-command setup
├── tests/                     # hermetic pytest suite
├── artifacts/                 # per-job workspaces + autopilot.db
├── requirements-optional.txt  # documented optional deps
├── pyproject.toml             # base deps + extras
└── setup.ps1                  # Windows setup script
```

---

## Environment notes

- Windows-first; `pathlib.Path` throughout
- FFmpeg 9.x required for render + QA
- GPU optional (CPU torch works for demucs/MusicGen; NVIDIA detected when present)
- Python 3.11+ (tested on 3.14)
