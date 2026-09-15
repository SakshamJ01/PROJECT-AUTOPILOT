# PROJECT AUTOPILOT — PHASE 1 INTEGRATION MANIFEST

This manifest documents the exact upstream open-source repositories integrated into Project Autopilot during Phase 1.

---

## Integrated Upstream Repositories

### 1. MoneyPrinterTurbo
* **Repository**: [https://github.com/harry0703/MoneyPrinterTurbo](https://github.com/harry0703/MoneyPrinterTurbo)
* **Runtime Version / Release**: `v1.3.6` (Tested local runtime)
* **Role**: Primary Short-Form Video Production Engine
* **Integration Method**: Dedicated Subprocess CLI & Local REST API Adapter (`autopilot/providers/production/moneyprinter_adapter.py`)
* **License**: MIT License
* **Local Service Endpoint**: `http://127.0.0.1:8080` for API service (FastAPI backend endpoints: `GET /api/v1/tasks?page=1&page_size=1` for health probe, `POST /api/v1/videos` for task creation, `GET /api/v1/tasks/{task_id}` for status polling; WebUI runs on `http://127.0.0.1:8501`). Configurable via `MONEYPRINTER_ENDPOINT` or CLI path via `MONEYPRINTER_CLI_PATH`.
* **Delegated Responsibilities**:
  - Footage & stock media montage
  - Scene pacing & Ken Burns transitions
  - Audio mixing & automatic BGM ducking
  - Full-resolution MP4 rendering
* **Autopilot Retained Responsibilities**:
  - Topic intake & Channel profiles
  - Wikipedia / Web research & provenance tracking
  - Editorial Script generation (Pydantic contracts)
  - Multi-pass QA receipts & rights gating
  - Database state machine & SQLite job queue
  - YouTube publishing & analytics feedback

---

### 2. faster-whisper
* **Repository**: [https://github.com/SYSTRAN/faster-whisper](https://github.com/SYSTRAN/faster-whisper)
* **Pinned Revision / Release**: `v1.1.0` (PyPI / CTranslate2)
* **Role**: Voice Transcription, Sub-Second Word Alignment & Subtitle Generation
* **Integration Method**: In-process Python Dependency & Engine (`autopilot/providers/transcription/faster_whisper_engine.py`)
* **License**: MIT License
* **Delegated Responsibilities**:
  - Audio voice activity detection (VAD)
  - Sub-second word-level timestamp extraction
  - Segment alignment
  - Kinetic karaoke ASS & SRT subtitle generation
* **Autopilot Retained Responsibilities**:
  - Subtitle styling presets for 9:16 vertical shorts
  - Caption overflow QA validation & alignment checks

---

## Phase 2 Upstream Repositories & Modules

### 3. Crawl4AI
* **Repository**: [https://github.com/unclecode/crawl4ai](https://github.com/unclecode/crawl4ai)
* **Pinned Revision / Release**: `v0.9.3` (PyPI package)
* **Role**: Secondary Deep Web Research Engine
* **Integration Method**: In-process Python dependency & Async Crawler Adapter (`autopilot/providers/crawl4ai_provider.py`)
* **License**: Apache License 2.0
* **System / Runtime Requirements**:
  - Python 3.10+ (tested on Python 3.14.4)
  - Playwright browser binaries (`playwright install chromium`)
  - Supported on Windows, Linux, and macOS
* **Delegated Responsibilities**:
  - Dynamic page retrieval and DOM scraping
  - Clean Markdown & extracted text extraction
  - URL and page title provenance preservation
  - Retrieval timestamping and structured metadata
* **Autopilot Retained Responsibilities**:
  - Provenance canonicalization and deduplication
  - Synthetic / low-quality source rejection
  - Deterministic routing via `ResearchCoordinator` (Wikipedia first, Crawl4AI secondary / explicit URL)
  - Grounded editorial constraints for downstream LLM generation

---

## Phase 2 Core Systems & Architecture

### Channel Profiles System
* **Presets**: `science_shorts`, `history_shorts`, `tech_shorts` (`autopilot/core/channel.py`)
* **External Files**: JSON and YAML profile support via `load_profile_from_file`
* **Editorial Impact**: Profile injects distinct tones, vocabulary, hook styles, visual motifs, and caption styles into editorial prompt generation.

### Orchestration & CLI Workflows
* **`autopilot run`**: The central, true end-to-end orchestration command. Routes directly through `PipelineOrchestrator` to execute: RESEARCH → SCRIPT → VOICE → TRANSCRIPTION (faster-whisper) → ASSETS → RENDER (MoneyPrinterTurbo) → QA (QAEngine) → PUBLISH GATE.
* **`autopilot produce`**: Stage-oriented content package generation for inspection, debugging, and intermediate artifact generation.

### Editorial Structure Intelligence & Listicle Validation
* **Cardinality Detection**: Automatically detects listicle item counts (e.g. `"3 surprising facts..."` -> 3 items) from topic names.
* **Multi-Scene Prompting**: Enforces hook/intro + discrete fact scenes in LLM generation prompts with multi-scene JSON contract examples.
* **Substantive Fact Gating**: Quality evaluator excludes pure CTAs/outros and verifies distinct fact scenes; rejects single-scene collapsed listicles.
* **Targeted Quality-Aware Regeneration**: Defect classification maps structure mismatches to `LIST_STRUCTURE_DEFECT`, preserving research while triggering targeted script regeneration with explicit corrective guidance (up to max regeneration attempts).

### Batch Production Engine
* **Topic Inputs**: Supports plain-text topic lists (`.txt`) and manifest files (`.json`, `.yaml`)
* **Job Isolation**: Each job runs with an independent SQLite record and distinct artifact workspace; job failure does not halt remaining batch items.
* **Failure Classification**: Categorized retries (retryable HTTP/timeout vs non-retryable syntax/contract errors).

### Targeted Quality-Aware Regeneration
* **Defect Classification**: Root cause mapped to `LIST_STRUCTURE_DEFECT`, `HOOK_DEFECT`, `DURATION_MISMATCH`, `VISUAL_DEFECT`, `AUDIO_DEFECT`, or `GROUNDING_DEFECT`.
* **Corrective Feedback**: Generates targeted instructions and passes them to the LLM editorial planner instead of raw re-runs.
* **Bounded Retries**: Configurable maximum attempts (default: 3); transitions to `NEEDS_REVIEW` on persistent failure.

---

## Explicit Legacy / Development Backends

### Native FFmpeg Engine
* **Adapter**: `autopilot/providers/production/ffmpeg_adapter.py`
* **Status**: Retained strictly as an explicitly selectable legacy/development backend (`--production-engine ffmpeg`).
* **Invariant**: Autopilot will **NEVER** silently fall back from MoneyPrinterTurbo to native FFmpeg. If MoneyPrinterTurbo is requested and unavailable, execution fails loudly with an actionable error.

---

## Phase 3 Upstream Integrations & Autonomy

### 4. Postiz Sidecar Integration
* **Repository**: [https://github.com/gitroomhq/postiz-app](https://github.com/gitroomhq/postiz-app)
* **License**: AGPL-3.0
* **Architectural Boundary**: Out-of-process HTTP REST API sidecar ONLY (`autopilot/providers/postiz_publisher.py`).
* **Clean License Separation**:
  - Zero imported Postiz code or internals in the Autopilot codebase.
  - Zero Python package dependencies from Postiz.
  - Integrated strictly via external standard HTTP requests (`POST /api/v1/posts`).
* **Failure Decoupling**: If the Postiz sidecar is offline or unconfigured, it reports `POSTIZ_UNAVAILABLE` gracefully. YouTube Direct publishing remains completely unaffected and fully operational.

### 5. YouTube Direct Publishing
* **Adapter**: `autopilot/providers/youtube_publisher.py`
* **Status**: Default lightweight native YouTube publishing provider.
* **Capabilities**: Resumable multi-chunk uploads, OAuth2 authentication, private/unlisted/public visibility gating, scheduled time UTC enforcement, and deterministic idempotency calculation.

### 6. Video Performance & Attribution Data Model
* **Model**: `VideoPerformance` (`autopilot/core/contracts.py`)
* **Persistence**: Historical time-series snapshots stored in `video_performance_snapshots` table.
* **Integrity**: Zero fabricated metrics. If a metric is not reported by the remote platform, it remains `None`.
* **Attribution**: Evaluates content dimensions (topic, duration bracket, hook style, production engine, voice ID, visual motif) against performance metrics to drive strategy learning.

### 7. Strategy Learning & Autonomous Control
* **Safety Rules**:
  - Sample size threshold: Minimum 3 observations required before adjusting channel strategy.
  - Bounded weight delta: Maximum adjustment of ±0.15 per observation cycle to prevent overreaction to single outliers.
  - Strategy versioning: Every adjustment creates an immutable version record (`strat-<channel>-v<N>`).
* **Autonomy Modes**:
  - `manual`: Operator initiates every pipeline stage manually.
  - `assisted` (DEFAULT): Pipeline executes candidate generation, research, script, production, transcription, and QA autonomously; halts at a persistent human approval gate (`publish_approvals`) before any publishing.
  - `autonomous`: End-to-end autonomous cycle running within channel policy; public publishing requires explicit configuration.

---

## LLM Provider Expansion & Model Selection

Autopilot provides a unified, contract-compliant LLM inference suite where every provider strictly outputs the canonical `ScriptDocument`. Core orchestration code interacts exclusively with standard interfaces.

### 8. LLM Provider Suite

| Provider | Type | Default Endpoint | Configuration Keys | Features & Constraints |
| :--- | :--- | :--- | :--- | :--- |
| **Ollama** | Local / Self-hosted | `http://localhost:11434/v1` | `OLLAMA_BASE_URL`<br>`OLLAMA_MODEL` | Local offline inference, dynamic `/models` and `/api/tags` discovery, fail-closed if model missing. |
| **Gemini** | Cloud / Official OpenAI Endpoint | `https://generativelanguage.googleapis.com/v1beta/openai/` | `GEMINI_API_KEY`<br>`GEMINI_MODEL`<br>`GEMINI_THINKING_BUDGET` | OpenAI-compatible Gemini API, default model `gemini-2.0-flash`, structured JSON output, configurable thinking budget. |
| **OpenRouter** | Cloud Router | `https://openrouter.ai/api/v1` | `OPENROUTER_API_KEY`<br>`OPENROUTER_MODEL` | Unified multi-model routing, requires explicit model ID (e.g. `meta-llama/llama-3.3-70b-instruct`). **Never** silently selects a fallback model. |
| **OpenAI-Compatible** | Generic Endpoint | `http://localhost:11434/v1` (or custom) | `AUTOPILOT_LLM_ENDPOINT`<br>`OPENAI_API_KEY`<br>`OPENAI_MODEL` | Backward-compatible generic adapter for custom vLLM, LM Studio, or OpenAI proxies. |
| **Mock** | In-Process / Synthetic | N/A | None | Deterministic, offline fixture generator for automated testing and CI. |

#### Invariants & Safety Rules
1. **Canonical Contract**: All providers must produce a valid Pydantic `ScriptDocument` with grounded evidence provenance and strict scene structures.
2. **Secret Redaction**: API keys (Google `AIzaSy...`, OpenRouter `sk-or-v1-...`, OpenAI `sk-...`, and `Bearer ...` tokens) are automatically redacted from error traces and structured logs.
3. **No Silent Fallbacks**: Autopilot will **NEVER** silently substitute a cloud provider or random model when a specific provider was configured. If the provider or model is unavailable or unconfigured, execution fails fast with an actionable diagnostic.


