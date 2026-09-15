# PROJECT AUTOPILOT — Phase 0 / M0 (Build Mode)

This is the working application repository for the local-first autonomous content production skeleton. The starter blueprint (`PROJECT_AUTOPILOT_STARTER_BLUEPRINT`) is preserved separately and is not part of this repo.

## What currently exists (Phase 0 only)

- Package: `autopilot/`
- Provider contracts (`autopilot/providers/contracts.py`) — typed interfaces for LLM, TTS, ASR, Asset, Publisher
- Workflow state machine (`autopilot/core/state_machine.py`) — all states and failure states with legal transition enforcement
- SQLite persistence (`autopilot/db/manager.py`) — jobs, workflow events, artifacts, errors, config
- Configuration loader (`autopilot/core/config.py`) — env vars + local defaults; no hardcoded secrets
- Structured JSON logging (`autopilot/core/logging.py`) — timestamp, job_id, stage, level, event, errors
- Artifact convention (`autopilot/core/artifacts.py`) — deterministic directories per job
- CLI health command (`autopilot/cli/main.py`) — reports Python, platform, FFmpeg, SQLite, artifacts, providers, config
- Synthetic smoke test (`tests/test_smoke.py`) — generates 9:16 synthetic video with FFmpeg, runs IDEA→RENDERED, verifies with ffprobe
- Unit/contract tests (`tests/test_contracts.py`) — state transitions, persistence, idempotency, config security, provider registry, FFmpeg creation

## How to install

Requires Python 3.11+ (tested on 3.14.4). Only `pydantic` and `pytest` are installed for Phase 0.

```powershell
cd C:\Users\Saksham\Documents\PROJECT-AUTOPILOT\autopilot
python -m pip install --quiet -e .
python -m pip install --quiet pytest
```

## How to run health check

```powershell
python -m autopilot health
```

Reports Python version, platform, FFmpeg availability, SQLite availability, artifact directory, DB path, registered providers (currently empty/stub only — expected for Phase 0), configuration validity.

## How to run tests

```powershell
python -m pytest tests/test_contracts.py -v
python tests/test_smoke.py
```

## How the workflow state machine works

States (from `AGENTS.md` / `IMPLEMENTATION_PLAN.md`):

`IDEA → RESEARCHING → RESEARCHED → SCRIPTING → SCRIPTED → ASSET_PREPARING → ASSETS_READY → VOICING → VOICE_READY → EDITING → RENDERING → RENDERED → QA → APPROVED → PUBLISHING → PUBLISHED → LEARNED`

Failure states: `FAILED_RESEARCH`, `FAILED_SCRIPT`, `FAILED_ASSETS`, `FAILED_TTS`, `FAILED_RENDER`, `FAILED_QA`, `FAILED_PUBLISH`. Illegal transitions raise `ValueError`. Idempotent stays are allowed and logged.

## What is intentionally deferred (not Phase 0)

- Actual LLM inference (Ollama/llama.cpp) — interface only
- Actual TTS (Kokoro/OpenVoice/Piper) — interface only
- Actual ASR (faster-whisper/whisper.cpp) — interface only
- Actual asset acquisition (Openverse) — interface only
- Actual rendering beyond synthetic FFmpeg — interface / renderer module deferred
- QA engine beyond contract validation — deferred
- YouTube publisher adapter — deferred
- Batch / scheduler — deferred
- Analytics / learning loop — deferred
- ComfyUI, Wan2.2, LTX — deferred; external process only when needed
- Remotion — excluded by design (license/cost)
- Docker — not installed; not required for Phase 0

## Environment notes

- Windows-compatible; uses `pathlib.Path` everywhere.
- GPU: NVIDIA RTX 4060 4 GB detected; local LLM will require small quantized models when implemented.
- FFmpeg 9.0 available; synthetic smoke test uses `testsrc` + `sine` audio.

## Architecture notes

- Provider interfaces are behind protocols; implementations register to `REGISTRY` but are NOT required for Phase 0.
- Every media asset must carry provenance metadata (`AGENTS.md` line 7); schema supports it.
- Every run is resumable and idempotent (SQLite idempotency keys + deterministic artifact paths).
- Human review is a first-class state (`APPROVED` / `PUBLISHING`); autonomous publishing is blocked until loop is proven (`AGENTS.md`).
- Zero paid APIs required for Phase 0.

* Phase 1 Production Engine Integration (MoneyPrinterTurbo + faster-whisper): ✅ COMPLETE

---

## Phase 1 — Real Production Engine Integration

Phase 1 integrates **MoneyPrinterTurbo** (`harry0703/MoneyPrinterTurbo` v1.3.6) as the primary short-form video production adapter and **faster-whisper** (`SYSTRAN/faster-whisper` v1.1.0) as the sub-second transcription and word-level caption alignment engine.

### Architecture Overview & Separation of Concerns

```text
  [ TOPIC / IDEA ]
         │
         ▼
  [ Wikipedia / Structured Research ] (Autopilot Core)
         │
         ▼
  [ Grounded LLM / Editorial Script ] (Autopilot Core — ScriptDocument & ContentPackage)
         │
         ▼
  [ Voice Generation ] (Kokoro / SAPI / TTS Engine)
         │
         ▼
  [ faster-whisper ] ──► (Word timestamps, segment timings, kinetic ASS/SRT alignment)
         │
         ▼
  [ MoneyPrinterTurbo Adapter ] ──► (Stock footage assembly, montage, audio mixing, BGM ducking)
         │
         ▼
  [ 14-Point QA Quality Gates ] (Autopilot Core — PublishReceipt, LUFS, black/freeze detect)
         │
         ▼
  [ YouTube Publisher ] (Autopilot Core — Resumable Upload, Idempotency, Provenance)
```

### Component Boundaries

| Responsibility | Owner | Technology |
|---|---|---|
| **Orchestration, State & Job Queue** | **Project Autopilot** | SQLite schema v7, WorkflowState machine |
| **Research & Fact Verification** | **Project Autopilot** | WikipediaProvider / Search Engine |
| **Editorial Representation & Contracts** | **Project Autopilot** | Pydantic Canonical Contracts (`contracts.py`) |
| **Voice Synthesis** | **Project Autopilot** | Kokoro TTS / Windows SAPI |
| **Transcription & Subtitle Timing** | **faster-whisper** | CTranslate2 / faster-whisper (`v1.1.0`) |
| **Video Production & Montage** | **MoneyPrinterTurbo** | MoneyPrinterTurbo Adapter (`v1.3.6`) |
| **Quality Assurance & Verification** | **Project Autopilot** | M5 QA Engine (14 checks, loudness, drift) |
| **Publishing Policy & Distribution** | **Project Autopilot** | M6 Publisher (YouTube Data API v3) |

### Strict Non-Negotiable Invariants
1. **Zero Silent Fallback**: If `--production-engine moneyprinterturbo` is requested and the MoneyPrinterTurbo service or CLI is unreachable, execution **fails loudly and immediately** with `ProductionEngineUnavailableError`. Autopilot will **never** silently fall back to native FFmpeg.
2. **Explicit Legacy Selector**: The native FFmpeg renderer remains available strictly as an explicitly selectable legacy/development backend (`--production-engine ffmpeg`).
3. **Engine-Aware Resume Invalidation**: Cached render artifacts generated under one engine are strictly invalidated if a subsequent run requests a different production engine.

### Configuration Options
Configure via environment variables or CLI options:
- `AUTOPILOT_PRODUCTION_ENGINE`: Default production engine (`moneyprinterturbo` or `ffmpeg`, default: `moneyprinterturbo`).
- `MONEYPRINTER_ENDPOINT`: Local REST API endpoint for MoneyPrinterTurbo (default: `http://127.0.0.1:8080` for API backend; WebUI runs on `8501`).
- `MONEYPRINTER_CLI_PATH`: Path to local MoneyPrinterTurbo CLI executable (optional).
- `AUTOPILOT_WHISPER_MODEL_SIZE`: Whisper model size (`tiny`, `base`, `small`, `medium`, `large-v3`, default: `base`).

### CLI Usage
```powershell
# Real production run using MoneyPrinterTurbo
python -m autopilot produce --topic "3 surprising facts about artificial intelligence" \
  --research-provider wikipedia \
  --tts-provider kokoro \
  --production-engine moneyprinterturbo \
  --render

# Legacy/Development run using native FFmpeg
python -m autopilot produce --topic "3 surprising facts about artificial intelligence" \
  --research-provider wikipedia \
  --tts-provider mock \
  --production-engine ffmpeg \
  --render

# Render an existing scripted job with explicit engine selection
python -m autopilot render --job <job_id> --production-engine moneyprinterturbo
python -m autopilot render --job <job_id> --production-engine ffmpeg
```

---

## Phase 1 / M1 — Content Contract + Script Engine

The canonical content contract lives in `autopilot/core/contracts.py`. It defines:

- `ContentProject` / `ContentItem`
- `ScriptDocument` / `ScriptScene`
- `VoiceSegment`, `AssetRequest`, `PublicationMetadata`, `ProvenanceRecord`
- `ContentPackage` (top-level object consumed by future subsystems)

Validation rules enforced by Pydantic: at least one scene; unique scene IDs; deterministic ascending order; positive durations; narration non-empty for spoken scene types; explicit schema version (`v1.0.0`).

`schemas/content-contract-v1.json` is generated from Pydantic models and is the explicit machine-readable contract.

### Architecture note

Topic → Content Contract (`ScriptDocument` + `ContentPackage`) → future research / script / asset / voice / render / publish stages. All future subsystems MUST consume this contract rather than inventing incompatible structures.

---

## Milestone 3 (M3) — Real Asset Engine

The Real Asset Engine provides rights-aware, cached, normalized, and deterministic asset acquisition for video scenes.

### Features
1. **Openverse Provider (`autopilot/providers/openverse_provider.py`)**: Real Openverse API v1 integration searching CC / Public Domain images and metadata with explicit rights classification.
2. **Local Asset Provider (`autopilot/providers/local_asset_provider.py`)**: Offline deterministic fixture provider for test suites and offline operation.
3. **Deterministic Asset Scoring (`autopilot/core/asset_scoring.py`)**: Explainable candidate scoring based on keyword relevance, aspect ratio, resolution, rights status, and duplicate avoidance.
4. **Rights Gate (`autopilot/core/rights_gate.py`)**: Safe-by-default licensing gate (`VERIFIED`, `PARTIALLY_VERIFIED`, `UNKNOWN` [blocked], `REJECTED` [blocked]).
5. **Safe Media Downloader & Asset Cache (`autopilot/core/asset_cache.py`)**: Streaming HTTP downloads with size limits, timeout, atomic writes, SHA-256 integrity checks, and durable disk caching in `artifacts/asset_cache/`.
6. **Asset Normalizer (`autopilot/core/asset_normalizer.py`)**: Standardizes images (Pillow) and videos (FFmpeg) to 9:16 vertical (1080x1920) using smart crop, fit, or pad strategies without modifying raw source artifacts.
7. **Scene-to-Asset Pipeline (`autopilot/core/asset_pipeline.py`)**: Orchestrates scene queries -> search -> scoring -> rights gate -> download -> cache -> normalization -> database persistence -> quality report.

### CLI Commands
```powershell
# Offline deterministic asset acquisition
python -m autopilot assets --job <job_id> --provider local

# Real Openverse asset search & acquisition
python -m autopilot assets --job <job_id> --provider openverse

# Search-only mode (does not download media)
python -m autopilot assets --job <job_id> --provider openverse --search-only

# Dry-run mode (scores & selects without downloading)
python -m autopilot assets --job <job_id> --provider openverse --dry-run
```

---

## Milestone 4 (M4) — FFmpeg Renderer MVP

The FFmpeg Renderer MVP compiles normalized assets, voice tracks, and motion parameters into a 9:16 vertical MP4:
- Target resolution: 1080x1920
- Deterministic rendering pipeline consuming `RenderPlan`
- Subprocess isolation with strict timeout handling
- SHA-256 output checksum computation

---

## Milestone 5 (M5) — QA Engine & Quality Gates

The production QA Engine (`autopilot/core/qa_engine.py`) provides provider-neutral, deterministic verification of rendered media before publishing.

### 13 Quality Categories Tested
1. **Container / File Integrity**: Non-empty, valid MP4 container, readable audio & video streams via FFprobe.
2. **Video Properties**: Exact profile compliance (1080x1920, 25/30 fps, H.264, yuv420p).
3. **Audio Properties**: Valid AAC/PCM codec, sample rate (>= 22050 Hz), channel count, non-zero audio stream.
4. **Loudness Analysis**: Deterministic ITU-R BS.1770 / EBU R128 analysis (`ebur128=peak=true` filter) measuring Integrated LUFS, Loudness Range (LRA), and True Peak (dBFS).
5. **Dead-Air / Silence Detection**: FFmpeg `silencedetect` identifies extended unintended audio dropouts with exact timestamps and durations.
6. **Black-Frame Detection**: FFmpeg `blackdetect` detects full-frame black video segments with pic_th and pix_th thresholds.
7. **Freeze / Static Frame Detection**: FFmpeg `freezedetect` flags suspicious frozen visual frames without requiring ML models.
8. **Duration / Timeline Drift**: Cross-references expected script duration, RenderPlan duration, narration duration, and actual media duration against configurable tolerances.
9. **Scene Coverage**: Verifies that every required scene in the ContentPackage has corresponding asset and audio representation.
10. **Caption QA**: Validates chronological ordering, non-overlapping intervals, positive durations, and maximum line length/count.
11. **Caption Visual QA**: Verifies caption safety margins and visual framing.
12. **Asset / Provenance Integrity & Rights Gate**: Verifies end-to-end provenance, artifact file existence, SHA-256 checksum match, and safe licensing (`VERIFIED` -> PASS; `UNKNOWN` / `REJECTED` -> BLOCK).
13. **Duplicate Output Detection**: Detects accidental duplicate renders using content and media SHA-256 digests in SQLite.

### Canonical QA Models
- `QAReport`: Complete report with run ID, timestamps, engine & FFmpeg versions, checks, findings, metrics, and `PublishReceipt`.
- `QACheck`: Individual check with check ID, category, status (`PASS|WARN|BLOCK`), severity, measured vs expected values, findings, and evidence.
- `QAFinding`: Granular defect findings with category, severity (`LOW|MEDIUM|HIGH|CRITICAL`), timestamps, and artifact references.
- `QAMetric`: Numeric and categorical measurements (LUFS, LRA, True Peak, duration, dimensions, FPS, drift).
- `QAThreshold`: Configurable profile boundaries and tolerances.
- `PublishReceipt`: Machine-readable gate decision (`status`, `publish_allowed`, `blocking_findings`, `warnings`, `metrics`).

### Publishability Invariant
```python
publish_allowed = (status == QAStatus.PASS) or (
    status == QAStatus.WARN and not strict_mode and all_warnings_permitted
)
# Any status == QAStatus.BLOCK strictly enforces publish_allowed = False.
```

### CLI Commands
```powershell
# Run QA evaluation on a rendered job
python -m autopilot qa --job <job_id>

# Run with verbose check output and metric breakdown
python -m autopilot qa --job <job_id> --verbose

# Output machine-readable JSON receipt
python -m autopilot qa --job <job_id> --json

# Strict mode: treat warnings as blocking failures
python -m autopilot qa --job <job_id> --strict

# Health check (confirms QA Engine is available)
python -m autopilot health
```

### Artifact Outputs
Stored under `artifacts/jobs/<job_id>/quality/`:
- `receipt.json`: Minimal machine-readable gate decision for publish subsystem.
- `quality_report.json`: Full canonical QA report.
- `findings.json`: Granular blocking defects and warnings.
- `metrics.json`: Extracted audio/video/loudness metrics.

### Database Persistence
Persisted in SQLite (`autopilot.db`):
- `qa_runs`: Run ID, job ID, status, publish_allowed, engine version, receipt JSON, metrics JSON.
- `qa_checks`: Individual check outcomes and evidence.
- `qa_findings`: Detailed findings with severities and time ranges.
- `qa_metrics`: Extracted quantitative signal metrics.

### Running Tests
```powershell
# Standard offline test suite (100% offline, zero network access)
python -m pytest

# Opt-in live Openverse integration test (requires network access)
python -m pytest -m live
```

---

## Milestone 6 (M6) — YouTube Publishing Engine

The production Publishing Engine (`autopilot/core/publisher.py`) provides a provider-neutral publishing subsystem with the first real platform adapter: **YouTube Data API v3** (`autopilot/providers/youtube_publisher.py`).

### Architecture
- **Provider Protocol (`autopilot/providers/contracts.py`)**: `PublisherProvider` defines standard methods: `validate_request()`, `publish()`, `health_check()`.
- **YouTube Adapter (`autopilot/providers/youtube_publisher.py`)**: Implements Google's official Resumable Upload specification over HTTPS using standard Python libraries (`urllib.request`), supporting chunked streaming, exponential backoff retries, and credential resolution.
- **Mock Adapter (`autopilot/providers/mock_publisher.py`)**: Deterministic offline provider for test suites.
- **Publishing Engine (`autopilot/core/publisher.py`)**: High-level orchestrator that enforces the QA Gate, verifies rendered media SHA-256 integrity, computes deterministic idempotency keys, manages workflow state transitions (`APPROVED` -> `PUBLISHING` -> `PUBLISHED` / `FAILED_PUBLISH`), persists DB records, and exports durable audit receipts.

### 9 Canonical Publishing Contracts
1. `PublishPlatform`: Target platform enum (`youtube`, `tiktok`, `instagram`, `mock`).
2. `PublishVisibility`: Privacy setting enum (`private`, `unlisted`, `public`).
3. `PublishStatus`: Lifecycle state enum (`pending`, `uploading`, `published`, `scheduled`, `failed`, `rejected`, `skipped_idempotent`).
4. `PublishTarget`: Target destination specifier (`platform`, `account_id`, `visibility`, `scheduled_time`).
5. `PublishAttempt`: Granular attempt telemetry (`attempt_number`, `started_at`, `completed_at`, `bytes_uploaded`, `http_status`, `error_code`, `error_message`).
6. `PublicationReceipt`: Durable audit record (`receipt_id`, `job_id`, `content_id`, `render_hash`, `qa_receipt_id`, `platform`, `provider`, `remote_id`, `remote_url`, `visibility`, `idempotency_key`, `published_at`).
7. `PublishRequest`: Complete canonical publishing request model.
8. `PublishResult`: Execution outcome model.
9. `PublishError`: Structured failure model (`code`, `message`, `category`, `retryable`).

### QA Gate Enforcement
Publishing **strictly consumes the canonical QA receipt** (`artifacts/jobs/<job_id>/quality/receipt.json`):
- `qa_receipt.publish_allowed` MUST be `True`.
- `qa_receipt.status` MUST NOT be `QAStatus.BLOCK`.
- Rendered video SHA-256 on disk MUST match `qa_receipt.render_hash`.
- Any mismatch or blocking status immediately aborts publishing with state `FAILED_PUBLISH`.

### Deterministic Idempotency
- An idempotency key is computed using SHA-256 over:
  `content_id + render_hash + platform + target_visibility + title + description + sorted(tags)`
- Before uploading, the database is queried for an existing successful publication matching the key.
- If found, the existing receipt is returned immediately without duplicate network calls, unless `--force-retry` is passed.

### Secret Security
- Client secrets and OAuth tokens are never logged, printed, stored in JSON artifacts, or included in test fixtures.
- Error messages and logs pass through `redact_secrets()` to strip `Bearer` tokens and sensitive JSON keys.

### CLI Commands
```powershell
# Dry-run validation (verifies QA receipt, video checksum, metadata, and auth without uploading)
python -m autopilot publish --job <job_id> --dry-run

# Output machine-readable JSON result
python -m autopilot publish --job <job_id> --dry-run --json

# Private upload (safest real upload mode)
python -m autopilot publish --job <job_id> --platform youtube --private

# Public publishing (requires QA gate PASS)
python -m autopilot publish --job <job_id> --platform youtube --public

# Scheduled publishing
python -m autopilot publish --job <job_id> --platform youtube --schedule 2026-10-01T12:00:00Z

# Force retry bypassing idempotency cache
python -m autopilot publish --job <job_id> --force-retry
```

### Artifact Outputs
Stored under `artifacts/jobs/<job_id>/publish/`:
- `request.json`: Serialized canonical publish request (no secrets).
- `result.json`: Canonical publish outcome and attempt logs.
- `receipt.json`: Durable publication receipt containing remote video ID and URLs.

### Database Persistence
Persisted in SQLite (`autopilot.db`, schema v3):
- `publish_records`: Publication records keyed by `publish_id` and `idempotency_key`.
- `publish_attempts`: Chronological attempt history with timing, byte counts, and error telemetry.

---

## Phase 7 / M7 — Batch Production + Local Scheduler

Milestone 7 delivers a durable, local-first batch production pipeline backed entirely by the existing SQLite database (`autopilot.db`, schema version 4). It operates 100% offline with zero cloud message queues (no Redis, Celery, RabbitMQ, Kafka) and zero n8n workflow dependencies.

### Core Architecture & Capabilities

1. **Durable SQLite Queue (`queue_items`, `batch_manifests`)**:
   - Survives process restarts without losing queued work.
   - Queue Lifecycle States: `queued`, `running`, `succeeded`, `failed`, `retry_wait`, `cancelled`, `blocked`, `dead_letter`.
   - Content Workflow States remain distinct (`IDEA` -> `RESEARCHING` -> `SCRIPTING` -> `VOICING` -> `ASSETS` -> `RENDERING` -> `QA` -> `APPROVED` -> `PUBLISHING` -> `PUBLISHED`).
   - Priority Ordering: Integer-based (`high` = 1, `normal` = 2, `low` = 3) with FIFO ordering within priorities.

2. **Atomic Worker Claiming & Lease Heartbeats**:
   - Worker claim operation uses `BEGIN IMMEDIATE` transactions to prevent duplicate processing by concurrent workers.
   - Workers write periodic heartbeats extending `lease_expires_at` (default 300s).
   - Stale running jobs whose leases expire (e.g. following worker crash or SIGKILL) are automatically recovered back to `queued` on the next claim cycle.

3. **Stage Resumption & Artifact Idempotency**:
   - Resuming crashed or interrupted jobs skips completed stages when valid artifacts exist:
     - `RESEARCH`: Validated via existing database research report.
     - `SCRIPT`: Validated via `script.json` and `content_package.json`.
     - `VOICE`: Validated via non-zero audio files on disk.
     - `ASSETS`: Validated via existing asset records in DB with matching disk artifacts.
     - `RENDER`: Validated via ffprobe-valid MP4 with non-zero byte size.
     - `QA`: Validated via QA receipt and matching video SHA-256 checksum.

4. **Classified Error Retries & Dead-Lettering**:
   - `RETRYABLE`: Transient issues (timeouts, network, locks) transition to `retry_wait` with exponential backoff (`backoff_base * 2^(attempt-1)`).
   - `NON_RETRYABLE`: Malformed specs or contract failures transition immediately to `failed`.
   - `BLOCKED`: Quality gate rejection or rights issues transition to `blocked`.
   - When attempts reach `max_attempts` (default 3), retryable failures transition to `dead_letter`.

5. **Failure Isolation**:
   - Failure of Job A does not crash the worker process. The worker logs the failure, records the attempt, and continues to Job B.

6. **Batch Manifests & Deduplication**:
   - Accepts JSON or YAML manifests defining batch production runs:
     ```json
     {
       "manifest_id": "batch-2026-09-11-01",
       "profile": "short_vertical",
       "priority": "normal",
       "auto_publish": false,
       "items": [
         {"topic": "Quantum Computing Basics", "priority": "high"},
         {"topic": "The History of Aviation", "priority": "normal"}
       ]
     }
     ```
   - Deterministic content identification prevents accidental duplicate submissions unless `--force` is specified.
   - Validation occurs before enqueueing to guarantee manifest consistency.

7. **Publishing Safety**:
   - Batch mode defaults to `auto_publish: false` (`generate` -> `render` -> `QA` -> `APPROVED`).
   - If `auto_publish: true` is explicitly configured, all M5/M6 safety gates apply:
     - QA gate MUST pass (`publish_allowed: true`).
     - Render hash MUST match QA receipt.
     - YouTube publication idempotency MUST be respected.
     - Defaults to `private` visibility.

### CLI Commands

```powershell
# Submit a batch manifest
python -m autopilot batch submit --file batch.json
python -m autopilot batch submit --file batch.json --force     # Allow duplicate submission
python -m autopilot batch submit --file batch.json --dry-run   # Validate without enqueueing

# View queue status and metrics
python -m autopilot queue status
python -m autopilot queue status --json

# List queued items
python -m autopilot queue list
python -m autopilot queue list --status queued --limit 20
python -m autopilot queue list --json

# Inspect a specific queued item and its error history
python -m autopilot queue inspect --job <job_id>

# Run worker process
python -m autopilot queue run                                 # Continuous polling
python -m autopilot queue run --once                          # Process current queue and exit
python -m autopilot queue run --worker-id worker-1 --max-jobs 10

# Manage jobs
python -m autopilot queue retry --job <job_id>                # Re-queue failed or dead-letter job
python -m autopilot queue cancel --job <job_id>               # Cancel queued or retry_wait job
```

### Windows Task Scheduler Setup

To execute batch runs on a recurring schedule without background daemons or external tools:

1. **Trigger Recommendation**: Run hourly or at fixed daily intervals.
2. **Action**: Start a Program
   - **Program/script**: `python` (or full path to virtual environment python, e.g. `C:\Python314\python.exe`)
   - **Add arguments**: `-m autopilot queue run --once`
   - **Start in (working directory)**: `C:\Users\Saksham\Documents\PROJECT-AUTOPILOT\autopilot`
3. **Settings**:
   - Enable "Do not start a new instance if already running" (guarantees concurrency safety).
   - Configure "Stop the task if it runs longer than: 2 hours".
4. **Failure Behavior**:
   - Any unhandled job failure is safely caught by the worker, isolated in SQLite, and the worker process exits cleanly with code 0.
   - Errors are persisted in `artifacts/autopilot.db` and structured logs under `artifacts/`.

---

## Phase 8 / M8 — Analytics & Performance Intelligence

The Analytics Engine ingests, normalizes, scores, and stores performance data from published video assets into SQLite. It provides the structured foundation for future automated learning (M9) while strictly enforcing observability without autonomous feedback actions.

### Core Architecture & Principles

1. **Platform-Neutral Contracts**:
   - `AnalyticsProvider` protocol defines typed interfaces for all platform adapters (`autopilot/providers/contracts.py`).
   - Core analytics (`autopilot/core/analytics.py`) processes normalized metrics, completely decoupled from platform-specific APIs.

2. **Metric Provenance & Taxonomy**:
   - **MEASURED**: Direct platform observations retrieved via official APIs (views, likes, comments, watch time, duration).
   - **DERIVED**: Deterministically computed metrics (engagement rate, like ratio, view velocity, completion rate). Guards against division by zero; omitted if inputs unavailable.
   - **SYNTHETIC**: Deterministic offline mock metrics generated for reproducible testing without network calls or API costs.

3. **Time-Series Historical Persistence (Schema v5)**:
   - Historical observations are preserved across observation windows (`1h`, `24h`, `7d`, `28d`, `lifetime`).
   - Records are never overwritten. Time-series snapshots allow tracking velocity and decay over time.

4. **Idempotency & Deduplication**:
   - Snapshots compute a SHA-256 hash of the raw response payload.
   - Duplicate sync attempts detect identical content and return the existing record without duplicating database rows.

5. **Content ↔ Performance Linkage**:
   - `ContentPerformance` bridges `job_id`, `topic`, `render_checksum_sha256`, `publication_receipt_id`, and `remote_id`.
   - Preserves end-to-end lineage: Topic → Script → Assets → Render → QA → Publication → Performance.

6. **Zero Scraping / Opt-In Live YouTube**:
   - Direct integration with YouTube Data API v3 (`videos.list?part=statistics,contentDetails`).
   - Scraping and unofficial endpoints are strictly prohibited.
   - Live API calls are opt-in and require `YOUTUBE_API_KEY` or OAuth access token.
   - Secrets are never persisted in the database and are automatically redacted in logs and error messages.

7. **No Autonomous Actions Yet (M8 Boundary)**:
   - Analytics strictly observes and reports.
   - It does NOT rewrite prompts, reschedule publishing, or alter generation parameters. Those capabilities are deferred to Milestone 9 (Learning & Optimization).

### Analytics CLI Commands

```powershell
# Synchronize analytics for a specific published job (uses default mock provider offline)
python -m autopilot analytics sync --job <job_id>

# Dry-run preview without network calls or DB writes
python -m autopilot analytics sync --job <job_id> --dry-run

# Synchronize with a specific performance window (1h, 24h, 7d, 28d, lifetime)
python -m autopilot analytics sync --job <job_id> --window 24h

# Batch synchronize across all published jobs up to limit
python -m autopilot analytics sync --all --limit 50

# Show full performance history and derived metrics for a job
python -m autopilot analytics show --job <job_id>
python -m autopilot analytics show --job <job_id> --json

# Generate a performance summary report table across jobs
python -m autopilot analytics report
python -m autopilot analytics report --platform youtube --limit 20
python -m autopilot analytics report --json
```

---

## Phase 9 / M9 — Autonomous Ideation & Feedback Loop

Milestone 9 transforms Project Autopilot from a system where humans supply topics into an autonomous intelligence and feedback loop:

```
SYSTEM DISCOVERS TREND OPPORTUNITIES
  ↓
SYSTEM DIVERSIFIES & FORMULATES TOPIC CANDIDATES
  ↓
SYSTEM SCORES CANDIDATES (Explainable Heuristics)
  ↓
POLICY & QUALITY GATES (Safety, Quotas, Evidence, Cooldown)
  ↓
PROPOSAL QUEUE (Level 2) OR AUTO-QUEUE (Level 3+) → M7 Queue
  ↓
BATCH PRODUCTION & RENDERING (M7/M4)
  ↓
QUALITY ASSURANCE (M5 QA Engine)
  ↓
SAFE PUBLICATION PATH (M6 YouTube Engine)
  ↓
ANALYTICS ENGINE MEASUREMENT (M8)
  ↓
ASSOCIATIONAL FEEDBACK EXTRACTION (M9)
  ↓
VERSIONED STRATEGY LEARNING (M9)
  ↓
FUTURE TOPIC CANDIDATES RANKED WITH HISTORICAL CONTEXT
```

### 1. Autonomy Safety Model & Levels

Unrestricted autonomous production or public publishing is strictly prohibited. The system operates under 5 explicit autonomy levels:

- **Level 0 — MANUAL (Default)**: Human supplies all topics. System ideation is dormant.
- **Level 1 — DISCOVERY**: System discovers, normalizes, and ranks topics from trend signals, but does not queue them.
- **Level 2 — QUEUE PROPOSAL**: System generates candidates and policy decisions, saving them into a reviewable proposal queue (`idea_proposals`). Requires explicit operator approval (`autopilot autonomy approve`) to enter the M7 queue.
- **Level 3 — GUARDED AUTO-QUEUE**: System automatically inserts approved ideas into the standard M7 queue, provided they pass all safety, budget, duplicate, and daily quota limits.
- **Level 4 — GUARDED AUTO-PRODUCTION**: System allows autonomous production under strict quotas and concurrency limits.

> **CRITICAL SAFETY BOUNDARIES**:
> - M9 does **NOT** automatically rewrite code or system prompts.
> - M9 does **NOT** bypass the M5 QA Engine.
> - M9 does **NOT** bypass asset rights/licensing gates.
> - M9 does **NOT** automatically publish publicly by default (`auto_publish: false`).
> - New installations default to conservative Level 0 (Manual).

### 2. Trend Discovery & Signal Model

- **Provider-Neutral Trend Ingestion**: Leverages existing research provider architecture without requiring paid trend APIs. Includes `MockTrendProvider` for deterministic offline testing across `technology`, `science`, `history`, and `finance` categories.
- **Structural Trend Signals (`TrendSignal`)**: Separates observed factual signals from model-inferred scores. Preserves signal provenance, category, freshness score, relevance score, evidence text, and raw JSON payload.
- **Multi-Source Consolidation**: When identical or near-identical topics are discovered from multiple sources, the ideation engine consolidates them into a single topic candidate, appending all source IDs into `supporting_signal_ids`.

### 3. Topic Candidate Ideation & Diversity Controls

- **Deterministic Generation (`TopicCandidate`)**: Synthesizes title, angle, hook hypothesis, rationale, confidence, effort, duplicate risk, and commercial relevance.
- **Diversity & Cooldown Filter (`DiversityFilter`)**: Employs tokenization, stop-word removal, and token Jaccard similarity. Rejects candidates whose similarity to recently produced topics exceeds `similarity_threshold` (default `0.70`).
- **Deterministic Cycle Hashing**: Uses SHA-256 hashes (`run_id` + normalized title) for stable candidate and proposal identification.

### 4. Explainable Multi-Factor Scoring (`TopicScorer`)

Topic scores are explainable decision-support heuristics—not claims of predicting virality:

$$\text{Score} = w_{\text{fresh}} \cdot F + w_{\text{rel}} \cdot R + w_{\text{hist}} \cdot H + w_{\text{nov}} \cdot N - \text{Penalty}_{\text{dup}}$$

- **Freshness ($F$)**: Trend signal freshness score.
- **Relevance ($R$)**: Target profile and category alignment.
- **Historical Association ($H$)**: Damped performance factor derived from previous M8 video analytics. Neutral ($0.50$) when unobserved.
- **Novelty ($N$)**: Inverse duplicate risk ($1.0 - \text{duplicate\_risk}$).
- **Duplicate Penalty**: Subtracted penalty scaled by duplicate risk.
- **Full Breakdown & Explanation**: Every score produces machine-readable `breakdown` dictionary and natural-language `explanation`.

### 5. Associational Feedback & Strategy Versioning

- **Non-Causal Associational Reasoning**: Observations explicitly frame findings as *"associated with stronger observed performance"* rather than claiming causal impact.
- **Outlier Damping & Sample Size Gates**: Single viral or failed videos are prevented from dominating or eliminating categories. Strategy adjustments require configurable minimum sample sizes (default $\ge 3$).
- **Quantile-Based Performance Tiers**: Classifies videos into `TOP`, `AVERAGE`, and `LOW` tiers relative to empirical baseline distributions.
- **Persistent Versioned Strategies (`StrategyVersion`)**: Strategies are immutable snapshots (`strat-v1`, `strat-v2`). Proposed strategies remain in `PROPOSED` status until explicitly activated or rolled back.

### 6. SQLite Schema v6 (Database Extensions)

Adds 8 dedicated relational tables with foreign keys and indexes:
- `autonomy_runs`: Tracks cycle execution, autonomy level, status, and metrics.
- `trend_signals`: Preserves raw and normalized trend signals with provenance.
- `topic_candidates`: Stores formulated topics, angles, hooks, and duplicate risk.
- `topic_scores`: Explainable score breakdowns and heuristic explanations.
- `idea_proposals`: Lifecycle-managed proposals (`proposed`, `approved`, `rejected`, `queued`).
- `autonomy_decisions`: Immutable audit log of every policy check result.
- `strategy_versions`: Versioned generation strategy parameters with rollback capability.
- `feedback_observations`: Extracted performance associations linked to source jobs.

### 7. Crash Recovery & Idempotency

- **Crash Recovery (`recover_stale_runs`)**: Recovers interrupted cycles left in `running` state, marking them `interrupted` without corrupting processed candidates.
- **Durable Queue Integration**: Approved proposals are inserted as standard M7 queue jobs with stage `RESEARCH`, carrying full lineage (`origin = autonomous_auto_queued`, `run_id`, `proposal_id`, `strategy_version`).

### Autonomy CLI Commands

```powershell
# Inspect active autonomy policy, guardrails, and quota limits
python -m autopilot autonomy policy
python -m autopilot autonomy policy --json

# Inspect active strategy version and historical version lineage
python -m autopilot autonomy strategy
python -m autopilot autonomy strategy --activate strat-v1
python -m autopilot autonomy strategy --json

# Execute a dry-run autonomy cycle (no queue or DB mutations)
python -m autopilot autonomy run --dry-run
python -m autopilot autonomy run --level 2 --dry-run --limit 3 --json

# Execute an autonomy cycle at Level 2 (Queue Proposal)
python -m autopilot autonomy run --level 2 --category technology

# List reviewable idea proposals
python -m autopilot autonomy proposals
python -m autopilot autonomy proposals --status proposed --json

# Inspect a specific run or proposal
python -m autopilot autonomy show --run <run_id>
python -m autopilot autonomy show --proposal <proposal_id>

# Approve a proposal into the standard M7 production queue
python -m autopilot autonomy approve --proposal <proposal_id>

# Reject a proposal with an explanatory reason
python -m autopilot autonomy reject --proposal <proposal_id> --reason "Off-brand angle"
```

---

## Phase 10 / M10 — Multi-Channel Scaling & Channel Profiles

Milestone 10 transforms Project Autopilot from a single-channel configuration into a **shared production engine powering multiple distinct channels, niche identities, voice/visual personas, and isolated strategy/quotas/analytics**.

### 1. Architectural Model: One Engine, Many Profiles

The core production engine (scriptwriting, TTS voicing, asset procurement, FFmpeg rendering, QA gates, and YouTube publishing) remains completely shared and unified. Variations between channels are managed entirely through typed, declarative **Channel Profiles**.

```text
                               ┌────────────────────────────────┐
                               │     Global System Engine       │
                               │  (FFmpeg, SQLite, Queue, QA)   │
                               └───────────────┬────────────────┘
                                               │
                    ┌──────────────────────────┼──────────────────────────┐
                    ▼                          ▼                          ▼
          ┌───────────────────┐      ┌───────────────────┐      ┌───────────────────┐
          │ Channel: Tech     │      │ Channel: Science  │      │ Channel: History  │
          │ Niche: Technology │      │ Niche: Space/Bio  │      │ Niche: Antiquity  │
          │ Voice: en_us_fast │      │ Voice: en_uk_calm │      │ Voice: en_us_deep │
          │ Visual: Tech Dark │      │ Visual: Minimal   │      │ Visual: Vintage   │
          │ Strategy: strat-t1│      │ Strategy: strat-s1│      │ Strategy: strat-h1│
          │ Daily Quota: 5    │      │ Daily Quota: 3    │      │ Daily Quota: 2    │
          └───────────────────┘      └───────────────────┘      └───────────────────┘
```

### 2. Channel Profile Schema & Sub-Configurations

Channel configurations are represented via canonical Pydantic models in `autopilot/core/contracts.py`:

- **`ChannelProfile`**: Canonical model specifying `channel_id`, `channel_name`, `niche`, `persona`, `voice`, `visual`, `posting_policy`, `autonomy_policy`, `analytics_config`, `monetization`, and status flags.
- **`NicheConfig`**: Defines niche name, description, allowed/excluded categories, terminology, and research preferences.
- **`PersonaConfig`**: Controls narration personality, tone, vocabulary, hook style, CTA style, and pacing.
- **`VoiceProfile`**: Selects provider, `voice_id`, speaking rate, and style metadata without tight coupling to underlying TTS engines.
- **`VisualBrandProfile`**: Consumed by the renderer as a `RenderProfile` for font family, theme colors, captions, logo/watermark, and motion transitions.
- **`PostingPolicy` & `AutonomyPolicy`**: Defines daily queue/publish quotas, allowed platforms, topic cooldowns, and autonomy levels independently per channel.
- **`MonetizationMetadata`**: Metadata flags for affiliate categories, commercial content markers, and disclosures (without hardcoded affiliate claims).

### 3. Immutable Profile Versioning (`ChannelProfileVersion`)

Every modification to a channel profile creates an immutable historical version snapshot (`v1` $\rightarrow$ `v2` $\rightarrow$ `v3`).
- Produced videos and jobs retain explicit foreign-key references to the exact `profile_version` under which they were generated.
- Modifying a channel profile for future content never retroactively rewrites or invalidates historical lineage.

### 4. Strict Isolation Guarantees

1. **Strategy Isolation**: Each channel binds to its own `active_strategy_version_id`. Performance outcomes and associational weights from Channel A never influence Channel B.
2. **Quota Isolation**: Daily quotas are tracked per channel in `channel_daily_quotas`. If Channel A reaches its daily quota, Channel B continues producing unhindered.
3. **Voice & Visual Isolation**: TTS voice IDs and visual branding themes are channel-scoped; worker rendering resolves branding derived strictly from the target channel.
4. **Queue Isolation**: Batch items persist `channel_id`. Multi-channel batches (`channel_a: 5`, `channel_b: 3`) execute in a single shared queue while maintaining strict channel identity and quota checks.

### 5. Cross-Channel Duplication Protection & Content Transformation

- **Scoped Deduplication**: Batches and ideation cycles use channel-scoped deduplication keys (`{channel_id}:{topic}:{profile_version}`) while tracking global topic hashes.
- **Profile-Driven Transformation**: The same trend signal (e.g., "James Webb Telescope discovery") is transformed into distinct content angles:
  - *Tech Channel*: Deep-dive on infrared sensor engineering and telemetry.
  - *Science Channel*: Astrophysical explanation of galactic redshift.
  - *History Channel*: Contextual evolution from early astronomical lenses.

### 6. Platform Targeting & Graceful Fallback

- `platform_targets` specifies distribution channels (default `["youtube"]`).
- **YouTube Publishing Adapter**: Fully functional live and mock publishing through existing M6 publisher.
- **Future Platforms (TikTok, Instagram)**: Recognized as contract targets, but intentionally isolated and stubbed. Attempting to publish to unimplemented targets yields graceful structured errors (`CHANNEL_TARGET_UNSUPPORTED`) without crashing worker pipelines.

### 7. SQLite Schema v7

Adds dedicated tables for channel management while preserving historical M0–M9 tables:
- `channel_profiles`: Current operational configuration, status, and metadata.
- `channel_profile_versions`: Immutable historical snapshots of channel profiles.
- `channel_daily_quotas`: Date-partitioned quota usage tracking per channel.
- Added `channel_id` indexes to `jobs`, `queue_items`, `feedback_observations`, and `autonomy_runs`.

### 8. Channel Management CLI

```powershell
# List all registered channel profiles
python -m autopilot channel list
python -m autopilot channel list --json

# Show detailed configuration for a specific channel
python -m autopilot channel show --id ai-insights
python -m autopilot channel show --id ai-insights --json

# Create or update a channel profile from a YAML/JSON definition
python -m autopilot channel create --profile configs/tech_channel.json

# Enable or disable a channel profile
python -m autopilot channel enable --id ai-insights
python -m autopilot channel disable --id ai-insights

# Validate channel profile configuration and brand consistency
python -m autopilot channel validate --id ai-insights

# Inspect historical version snapshots for a channel
python -m autopilot channel history --id ai-insights

# Compare measured performance and operational metrics across channels
python -m autopilot channel compare
python -m autopilot channel compare --channels ai-insights,science-frontier --json

# Run an autonomy cycle targeted to a specific channel
python -m autopilot autonomy run --channel ai-insights --level 2
```

---

## Production Readiness & Live Validation Status

PROJECT AUTOPILOT has undergone comprehensive live validation, failure injection testing, crash-recovery auditing, and security inspection.

### 1. Provider Operational Status Classification

```text
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                              PROVIDER OPERATIONAL MATRIX                               │
├──────────────────────┬──────────────────────────────────────────┬──────────────────────┤
│ Classification       │ Capabilities & Providers                 │ Operational Status   │
├──────────────────────┼──────────────────────────────────────────┼──────────────────────┤
│ WORKING IN           │ • Windows SAPI TTS (Real Speech WAV)     │ 100% Operational     │
│ PRODUCTION           │ • Openverse Asset Engine (Rights Gate)   │ 100% Operational     │
│                      │ • FFmpeg 9:16 Vertical Video Renderer    │ 100% Operational     │
│                      │ • 10-Point QA Engine & Quality Gates     │ 100% Operational     │
│                      │ • Multi-Channel Profile Engine           │ 100% Operational     │
│                      │ • Batch Processor & Worker Queue         │ 100% Operational     │
│                      │ • SQLite Persistence (Schema v7)         │ 100% Operational     │
├──────────────────────┼──────────────────────────────────────────┼──────────────────────┤
│ OPTIONAL /           │ • OpenAI-Compatible LLM (Ollama / vLLM)  │ Available on-demand  │
│ EXTERNAL             │ • OpenRouter LLM API                     │ Opt-in external API  │
│                      │ • YouTube Data API v3 (Live Upload)      │ Configurable OAuth   │
│                      │ • YouTube Analytics Sync                 │ Configurable OAuth   │
├──────────────────────┼──────────────────────────────────────────┼──────────────────────┤
│ MOCK /               │ • MockScriptProvider                     │ Active for offline   │
│ DEVELOPMENT          │ • MockTTSProvider (Synthetic Sine Audio) │ Active for CI/CD     │
│                      │ • LocalAssetProvider (Offline Fixtures)  │ Active for offline   │
│                      │ • MockPublisher (Dry-Run Receipts)       │ Active for offline   │
│                      │ • MockAnalyticsProvider                  │ Active for offline   │
├──────────────────────┼──────────────────────────────────────────┼──────────────────────┤
│ INTENTIONALLY        │ • TikTok Publishing Adapter              │ Deferred (M11+)      │
│ DEFERRED             │ • Instagram Publishing Adapter           │ Deferred (M11+)      │
│                      │ • ComfyUI / Wan2.2 / LTX AI Video        │ Deferred (M11+)      │
└──────────────────────┴──────────────────────────────────────────┴──────────────────────┤
```

### 2. $0 Operating Cost Architecture

- **Mandatory Operating Cost**: **$0.00 / month**.
- **Inference & Synthesis**: Zero mandatory paid API subscriptions.
- **Rendering & Persistence**: 100% local compute via FFmpeg and SQLite.
- **Asset Acquisition**: Openverse CC0 / CC-BY public domain licensing with mandatory rights validation.

---

# PHASE 2: DEEP RESEARCH, AUTOMATION & CHANNEL SYSTEM

Phase 2 transforms Project Autopilot from a working single-pipeline prototype into a scalable, repeatable automated content production system.

## 1. Secondary Research Engine (Crawl4AI v0.9.3)
- **Role**: Tier-2 deep web extraction, live webpage crawling, and DOM markdown scraping.
- **License**: Apache 2.0.
- **Installation**:
  ```powershell
  pip install crawl4ai==0.9.3
  playwright install chromium
  ```
- **Windows Setup**: Crawl4AI runs on Windows via standard Python async event loop and Playwright Chromium headless processes.
- **Routing Strategy**:
  - `wikipedia_first` (default): Fast factual queries start with Wikipedia; augments with Crawl4AI if Wikipedia results are sparse.
  - `combined`: Simultaneously queries Wikipedia and Crawl4AI.
  - `crawl4ai_only`: Directs queries solely to Crawl4AI web extraction.
  - `explicit_url`: Detected automatically when a topic is a URL (`https://...`), crawling the target webpage directly.
- **Normalization & Provenance**: Normalizes all evidence into `ResearchEvidenceRecord` with canonical URLs, publisher metadata, excerpt text, and confidence scores. Rejects empty pages, inaccessible sources, and synthetic fixtures.

## 2. Channel Profiles System
Data-driven channel configurations that inject distinct styling and editorial directives into script generation and production:
- **Built-in Presets**:
  - `science_shorts`: Educational tone, accessible expert vocabulary, surprising discovery hook style, scientific photography motif, `af_bella` voice.
  - `history_shorts`: Historical tone, date/era framing hook style, archival photography motif, `am_adam` voice.
  - `tech_shorts`: Tech analyst tone, futuristic breakthrough hook style, hardware & electronics motif, `am_adam` voice.
- **External Configuration**: Load custom profiles via YAML or JSON:
  ```powershell
  python -m autopilot channel create --file my_channel.yaml
  ```
- **Profile Impact**: Tangibly alters the LLM prompt directives, scene count, hook style, call-to-action, font choices, and primary/secondary brand colors.

## 3. Batch Production & Job Isolation
Run multiple topics in a single command with isolated job failures:
```powershell
python -m autopilot batch --topics topics.txt --channel science_shorts
```
- **Input Formats**: Plain-text `.txt` (one topic per line, `#` comments ignored) or manifest files (`.json`, `.yaml`).
- **Job Isolation**: Each topic executes in an independent workspace (`artifacts/jobs/<job_id>`). If job #2 fails, jobs #1 and #3 continue unimpeded.
- **Failure Classification**: Distinguishes between retryable transient errors (network timeout, HTTP 503) and non-retryable errors (contract violations, invalid syntax).

## 4. Targeted Quality-Aware Regeneration
When a generated video fails QA quality gates, Autopilot avoids blind retries:
1. **Defect Classification**: Root cause mapped to `HOOK_DEFECT`, `DURATION_MISMATCH`, `VISUAL_DEFECT`, `AUDIO_DEFECT`, or `GROUNDING_DEFECT`.
2. **Corrective Feedback**: Generates targeted instructions (e.g. "Rewrite hook to be more compelling without filler", "Adjust scene narration duration to meet 30s target").
3. **Selective Artifact Invalidation**: Only invalidates artifacts affected by the target stage (e.g. script & render plan for hook defects; audio for TTS defects).
4. **Bounded Retries**: Configurable maximum attempts (default: 3). If QA still fails after the limit, the job status transitions to `NEEDS_REVIEW`.

## 5. CLI Automation Workflow
```powershell
# Run a single topic with a channel profile and production policy
python -m autopilot run --topic "James Webb Telescope discoveries" --channel science_shorts --policy quality_first

# Run batch from topic list
python -m autopilot batch --topics topics.txt --channel history_shorts
```

---

# PHASE 3 — AUTONOMOUS PUBLISHING, ANALYTICS & STRATEGY FEEDBACK

Phase 3 completes Project Autopilot's end-to-end loop:
`IDEA → RESEARCH → SCRIPT → PRODUCTION → TRANSCRIPTION → QA → APPROVAL/PUBLISH → ANALYTICS → FEEDBACK → NEXT IDEA`.

## 1. Multi-Platform Publishing Architecture
- **YouTube Direct (Default)**: Lightweight native integration supporting resumable chunk uploads, metadata generation, and scheduled timestamps.
- **Postiz Sidecar**: External HTTP sidecar integration (`PublishProviderProtocol`) for multi-platform scheduling (TikTok, Instagram, LinkedIn).
  - *AGPL-3.0 Clean Boundary*: Zero imported Postiz code or internals; communicated strictly via external HTTP REST (`POST /api/v1/posts`).
  - *Fault Isolation*: If the Postiz sidecar is offline, it reports `POSTIZ_UNAVAILABLE` gracefully without impacting YouTube direct publishing.

## 2. QA-Gating & Deterministic Idempotency
- **Strict Quality Gating**: Videos cannot be published unless a valid QA receipt exists with `publish_allowed=True` and matching media SHA-256 checksums.
- **Publishing States**: `NOT_READY`, `READY`, `QUEUED`, `UPLOADING`, `PUBLISHED`, `SCHEDULED`, `FAILED`, `RETRYING`, `CANCELLED`, `BLOCKED_QA`, `SKIPPED_DUPLICATE`.
- **Deterministic Idempotency**:
  `sha256(job_id + render_checksum + platform + visibility + scheduled_time + metadata_hash)`
  Retries will return the existing publication receipt instead of triggering duplicate uploads.

## 3. Analytics & Performance Data Model
- **Historical Snapshots**: Snapshots are persisted to SQLite table `video_performance_snapshots` with timestamps, enabling trend analysis.
- **Integrity**: Zero fabricated metrics. Missing remote metrics remain `None`.
- **Performance Attribution**: Multi-dimensional analysis connecting views, retention, and CTR to:
  - Duration brackets (`<30s`, `30-50s`, `>50s`)
  - Hook patterns
  - Production engines (`moneyprinter`, `ffmpeg`)
  - Voice IDs & visual motifs

## 4. Autonomous Ideation & Strategy Learning
- **Candidate Generator**: Scores ideas based on novelty, relevance, evidence availability, estimated cost, and predicted performance.
- **Topic Deduplication**: Rejects duplicate topics within a configurable recency window (default: 14 days).
- **Safety Against Bad Feedback**:
  - Sample size threshold: Minimum 3 observations required before adjusting weights.
  - Bounded adjustments: Maximum ±0.15 delta per observation cycle.
  - Strategy versioning: Every change creates an immutable version record (`strat-<channel>-v<N>`).

## 5. Autonomy Modes & Human Approval Gate
- `manual`: Operator manually triggers each job and publishing step.
- `assisted` (DEFAULT): Autonomous generation through QA; pauses at a persistent human approval gate (`publish_approvals`) before publishing.
- `autonomous`: Full loop execution including scheduled publishing within policy constraints.

## 6. CLI Commands & Orchestration

* **`autopilot run`**: The **true end-to-end command** driving the central `PipelineOrchestrator`. It sequentially executes: `RESEARCH → SCRIPT → VOICE → TRANSCRIPTION (faster-whisper) → ASSETS → RENDER (MoneyPrinterTurbo) → QA (QAEngine) → PUBLISH GATE`.
* **`autopilot produce`**: Stage-oriented generation command focused on creating research, script documents, and content packages for intermediate inspection.

```powershell
# Run true end-to-end production through QA (with listicle structure intelligence)
python -m autopilot run --topic "3 surprising facts about artificial intelligence" --channel tech_shorts

# Produce structured content package only
python -m autopilot produce --topic "Quantum Computing Breakthroughs" --channel science_shorts

# Publish a QA-verified job
python -m autopilot publish <job_id> --visibility unlisted

# Schedule for a future time
python -m autopilot publish <job_id> --schedule 2026-10-01T18:00:00Z

# Inspect job state, QA, and publication status
python -m autopilot inspect <job_id>

# View channel performance attribution
python -m autopilot analytics --channel tech_shorts

# Run an assisted autonomy cycle
python -m autopilot autonomy --channel tech_shorts --mode assisted
```

## 7. Editorial Listicle Structure & Regeneration Intelligence
* **Cardinality Detection**: Automatically detects requested item counts in topics (e.g., `3 surprising facts...` -> 3 distinct facts).
* **Multi-Scene Prompting**: System and user prompts enforce discrete scenes (Hook scene + $N$ discrete fact scenes + optional CTA).
* **Quality & Gating**: `evaluate_script` checks substantive scene counts (excluding pure CTAs) against the requested cardinality.
* **Targeted Regeneration**: Structure defects emit `LIST_STRUCTURE_DEFECT`, preserving verified research and triggering targeted script-only regeneration with explicit corrective guidance.

# LLM PROVIDER EXPANSION & MULTI-MODEL ARCHITECTURE

Project Autopilot features a unified, pluggable LLM layer designed to prevent local inference bottlenecks while strictly adhering to typed `ScriptDocument` contracts.

## 1. Supported Providers & Configuration

### A. Ollama (Local)
* **Description**: Local, privacy-first inference via Ollama's OpenAI-compatible `/v1` endpoint.
* **Environment Variables**:
  * `OLLAMA_BASE_URL` (default: `http://localhost:11434`)
  * `OLLAMA_MODEL` (e.g. `qwen3:4b`, `llama3.2:3b`)
* **Features**: Dynamic installed-model discovery from `/models` and `/api/tags`. Fails closed if the configured model is not pulled.

### B. Google Gemini (Cloud / Official OpenAI Endpoint)
* **Description**: Google's official OpenAI-compatible endpoint for high-speed frontier script generation.
* **Endpoint**: `https://generativelanguage.googleapis.com/v1beta/openai/`
* **Environment Variables**:
  * `GEMINI_API_KEY` (required)
  * `GEMINI_MODEL` (default: `gemini-2.0-flash`)
  * `GEMINI_THINKING_BUDGET` (optional integer, e.g. `0` for instant output or `1024` for reasoning)
* **Features**: Fast short-form script generation, structured JSON response format, customizable thinking levels.

### C. OpenRouter (Multi-Model Cloud Router)
* **Description**: Cloud routing to frontier and open-source models (Llama 3.3, Claude, Mistral, DeepSeek, etc.).
* **Endpoint**: `https://openrouter.ai/api/v1`
* **Environment Variables**:
  * `OPENROUTER_API_KEY` (required)
  * `OPENROUTER_MODEL` (e.g. `meta-llama/llama-3.3-70b-instruct`, `anthropic/claude-3.5-sonnet`)
* **Features**: Explicit model routing with custom client attribution headers (`HTTP-Referer`, `X-Title`). **Never** silently chooses a random cloud model when the user selected a specific provider/model.

### D. Mock / Development
* **Description**: Instant, deterministic fixture script generation for testing and CI.
* **CLI Option**: `--llm-provider mock`

## 2. CLI Usage Examples

```powershell
# 1. Local Ollama generation
python -m autopilot run --topic "How Quantum Computing Works" --llm-provider ollama

# 2. Google Gemini script generation (Fast cloud inference)
$env:GEMINI_API_KEY="AIzaSy..."
python -m autopilot run --topic "3 Surprising Space Discoveries" --llm-provider gemini

# 3. OpenRouter script generation
$env:OPENROUTER_API_KEY="sk-or-v1-..."
$env:OPENROUTER_MODEL="meta-llama/llama-3.3-70b-instruct"
python -m autopilot run --topic "The Future of Nuclear Fusion" --llm-provider openrouter

# 4. Explicit Policy selection
python -m autopilot run --topic "History of the Internet" --policy quality_first
```

## 3. Strict Safety & Contract Guarantees

1. **Common Contract**: All providers parse responses into the canonical `ScriptDocument` with full scene timings, visual queries, narration, and grounding provenance.
2. **Secret Redaction**: API keys and authorization tokens are automatically masked (`[REDACTED]`) in all logs and error traces.
3. **No Silent Fallbacks**: Autopilot will **never** silently substitute cloud providers or fallback models. If a chosen provider or model is unavailable, execution halts with a clear diagnostic message.




