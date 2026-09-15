# PROJECT AUTOPILOT — REPOSITORY STACK & ARCHITECTURE
=====================================================

Project Autopilot is built on a repository-first architecture, leveraging established open-source tools for heavy domain tasks while retaining orchestration, governance, quality gating, and state management.

## System Architecture

```
                       TOPIC / USER PROMPT
                                ↓
                     [ ResearchCoordinator ]
                     /                     \
      (Tier-1 Fast Facts)             (Tier-2 Deep Web / URL)
       WikipediaProvider                  Crawl4AIProvider
        (MediaWiki API)                 (crawl4ai v0.9.3)
                     \                     /
              [ Normalized ResearchBundle ]
               (provenance, deduplicated)
                                ↓
                    [ Editorial Scripting ]
              (LLM + ChannelProfile Directives)
              (hook, narration, visual queries)
                                ↓
                      [ Kokoro Voice TTS ]
                     (ONNX model synthesis)
                                ↓
                   [ faster-whisper Engine ]
                    (v1.1.0 CTranslate2)
            (word timestamps, SRT, ASS karaoke)
                                ↓
                 [ Production Engine Selection ]
                 /                             \
      (Primary Engine)                   (Explicit Legacy)
  MoneyPrinterTurbo v1.3.6                Native FFmpeg
  (footage montage, BGM,                 (local fallback)
     subtitles, transitions)
                 \                             /
                   [ Production MP4 Output ]
                                ↓
                      [ Autopilot QA Engine ]
                (loudness, black frames, silence,
                  duration drift, caption safety,
                  listicle structure validation)
                                 ↓
                       /                  \
               [ QA Pass ]            [ QA Fail ]
                    ↓                      ↓
              Ready for Pub     Targeted Regeneration Loop
                                (LIST_STRUCTURE_DEFECT, HOOK_DEFECT,
                                 DURATION_MISMATCH, VISUAL_DEFECT,
                                 GROUNDING_DEFECT)
                                   → Targeted corrective instruction
                                   → Preserves research
                                   → Max 3 attempts
                                   → NEEDS_REVIEW
```

---

## Pinned Upstream Dependencies

| Component | Upstream Repository | Version | License | Role in Autopilot |
| :--- | :--- | :--- | :--- | :--- |
| **Local LLM Engine** | [Ollama](https://github.com/ollama/ollama) | Dynamic (`/v1`) | MIT | Default local OpenAI-compatible inference (`qwen3:4b`, `llama3.2`, etc.) |
| **Google Cloud LLM** | Google Gemini API | OpenAI-compat (`/v1beta/openai`) | Google API | Frontier short-form script generation (`gemini-2.0-flash`), structured JSON |
| **Multi-Model Router** | [OpenRouter](https://openrouter.ai) | REST API (`/api/v1`) | OpenRouter API | Explicit model routing (`meta-llama/llama-3.3-70b-instruct`, etc.) |
| **Production Engine** | [harry0703/MoneyPrinterTurbo](https://github.com/harry0703/MoneyPrinterTurbo) | `v1.3.6` | MIT | Primary short-form video synthesis, montage, background music ducking, transitions |
| **Transcription / Timing** | [SYSTRAN/faster-whisper](https://github.com/SYSTRAN/faster-whisper) | `v1.1.0` | MIT | Audio alignment, sub-second word timestamps, kinetic karaoke ASS/SRT formatting |
| **Secondary Research** | [unclecode/crawl4ai](https://github.com/unclecode/crawl4ai) | `v0.9.3` | Apache 2.0 | Deep web extraction, live webpage crawling, DOM markdown scraping |
| **Primary Research** | Wikipedia API | Native REST | Free / Public | Fast structured knowledge source |
| **Voice TTS** | Kokoro-82M ONNX | `v0.19` | Apache 2.0 | High-quality local voice synthesis |
| **Direct Publishing** | YouTube v3 Data API | Native REST | Proprietary Client | Native resumable YouTube publishing backend |
| **Multi-Platform Sidecar** | [gitroomhq/postiz-app](https://github.com/gitroomhq/postiz-app) | External Sidecar | AGPL-3.0 | Out-of-process HTTP sidecar for multi-platform scheduling |

---

## Phase 3 Feature Summary (Autonomous Publishing, Analytics & Strategy Loop)

1. **Multi-Platform Publishing Architecture**
   - Direct native YouTube publisher remains the default lightweight backend.
   - Postiz integrated strictly as an out-of-process external HTTP sidecar (`PublishProviderProtocol`). Clean AGPL-3.0 boundary: zero imported Postiz code or internals.
   - If Postiz is offline, it reports `POSTIZ_UNAVAILABLE` gracefully without impacting YouTube direct.

2. **Scheduling & Publishing States**
   - Full ISO-8601 UTC timestamp validation, channel timezone awareness, and preferred posting windows.
   - Explicit state transitions: `NOT_READY`, `READY`, `QUEUED`, `UPLOADING`, `PUBLISHED`, `SCHEDULED`, `FAILED`, `RETRYING`, `CANCELLED`.
   - QA-gated: media checksums and valid `publish_allowed=True` QA receipts strictly required before publication.
   - Deterministic idempotency key: `sha256(job_id + render_checksum + platform + visibility + scheduled_time + metadata_hash)` prevents accidental duplicate uploads on retry.

3. **Analytics Collection & Performance Data Model**
   - Snapshots persisted over time in `video_performance_snapshots` table (historical trend snapshots, no overwrites).
   - Metrics tracked: views, likes, comments, shares, watch time, average view duration, retention rate, CTR, impressions, subscriber change. Zero fabricated metrics.
   - Multi-dimensional attribution: links performance back to topic, channel, hook style, duration bracket, production engine, voice ID, and visual motif.

4. **Autonomous Ideation & Strategy Learning Loop**
   - Candidate generator scoring novelty, relevance, evidence availability, estimated cost, and predicted performance.
   - Deduplication with configurable recency cooldown windows.
   - Safety against bad feedback: requires minimum sample size (>= 3 observations) and enforces bounded weight adjustments (max ±0.15 delta per cycle).
   - Strategy versioning: creates traceable immutable strategy versions (`strat-<channel>-v<N>`).

5. **Autonomy Control & Governance**
   - Modes: `manual`, `assisted` (DEFAULT), `autonomous`.
   - Assisted mode pauses execution at a persistent human approval gate (`publish_approvals`) before publishing.
   - End-to-end autonomous cycle: IDEA → SELECT → RESEARCH → SCRIPT → PRODUCTION → TRANSCRIPTION → QA → APPROVAL/PUBLISH GATE → ANALYTICS → STRATEGY → NEXT CANDIDATE.

