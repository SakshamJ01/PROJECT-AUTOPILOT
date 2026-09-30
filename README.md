# PROJECT AUTOPILOT — Production-Hardened Video Pipeline

**Autopilot** is a local-first, truthful video generation pipeline that produces research-grounded, vertically-formatted (1080x1920) short-form videos with real providers and hard gates. No mocks, no synthetic claims, no unverified artifacts.

---

## What It Actually Does

| Stage | Provider | Verification |
|-------|----------|--------------|
| **Research** | Wikipedia API (+ Crawl4AI fallback) | Source citations in script |
| **Script** | OpenRouter (Claude/Gemini) or Atria Dawn | Pacing guard (8–12 words/scene), fail-fast |
| **Voice** | Edge TTS (neural) | Faster-Whisper alignment, word timestamps |
| **Assets** | Pexels (video) → Pixabay → Openverse | CLIP ViT-B-32/laion2b_s34b_b79k ≥ 0.21 cosine |
| **Render** | ffmpeg (native) | Provenance gate: perceptual dHash, overlay masking, threshold 12 |
| **Captions** | Kinetic ASS from real word timestamps | Frame-level lower-third burn |
| **QA** | 14 technical + 13 creative dimensions | Composite Publish Readiness gate |

**Key principle:** Every gate is fail-closed. Nothing ships without provenance, technical, creative, and rights verification.

---

## Quick Start

```powershell
cd autopilot
uv sync                          # installs torch, open-clip-torch, edge-tts, faster-whisper, etc.
cp .env.example .env             # add PEXELS_API_KEY, OPENROUTER_API_KEY (or ATRIA_API_KEY)
python -m autopilot produce --topic "Your Topic" --policy quality_first --llm-provider openrouter --asset-provider pexels --tts-provider edge_tts --research-provider wikipedia --production-engine ffmpeg --render
python -m autopilot qa --job <JOB_ID> --strict
python -m autopilot publish --job <JOB_ID> --platform youtube --private --publish-action approve
python -m autopilot publish --job <JOB_ID> --platform youtube --private --publish-action run
```

**Required `.env` keys:**
```env
PEXELS_API_KEY=...
PIXABAY_API_KEY=...
OPENROUTER_API_KEY=...           # or ATRIA_API_KEY for Atria Dawn
# YouTube OAuth: client_secret.json in autopilot/credentials/
```

---

## CLI Commands

```powershell
# Full pipeline (research → script → TTS → assets → render)
autopilot produce --topic "..." --policy quality_first --llm-provider openrouter --render

# Render only (assets + ffmpeg)
autopilot render --job <JOB_ID> --asset-provider pexels

# Strict QA (technical + creative + provenance + publish readiness)
autopilot qa --job <JOB_ID> --strict

# Approve + publish private
autopilot publish --job <JOB_ID> --platform youtube --private --publish-action approve
autopilot publish --job <JOB_ID> --platform youtube --private --publish-action run

# YouTube OAuth (once)
autopilot youtube-auth --no-browser
```

---

## Gates (All Fail-Closed)

| Gate | What It Checks | Failure = Block |
|------|----------------|-----------------|
| **Provenance** | Perceptual dHash of every claimed asset in render (overlay-masked, area-scaled) | Hamming > 12 |
| **Technical QA** | Container, streams, audio, captions, timeline, duplicates, rights | Any BLOCK finding |
| **Creative QA** | Hook, visual match, pacing, captions, narrative, audio, continuity, defects | Any dimension BLOCK |
| **Publish Readiness** | Technical ∧ Creative ∧ Defects ∧ Rights ∧ Invariants | Any layer BLOCK |
| **Stale Artifact** | Pipeline version + content hashes mismatch | Auto-invalidate |

**No gate can be overridden by another layer.** Creative cannot override technical. Analytics cannot override provenance.

---

## Providers (Priority Order)

| Type | Primary | Fallback | Notes |
|------|---------|----------|-------|
| **LLM** | OpenRouter (`google/gemini-2.5-flash`, `anthropic/claude-3.5-sonnet`, etc.) | Atria Dawn (`Atria-Dawn-Preview`), Ollama, Gemini direct | OpenRouter key in `.env` |
| **TTS** | Edge TTS (neural, 50+ voices) | Kokoro ONNX (local) | Faster-Whisper alignment mandatory |
| **Assets** | Pexels (video) | Pixabay → Openverse → Infographics | CLIP gate ≥ 0.21 |
| **Research** | Wikipedia | Crawl4AI | Citations embedded in script |

---

## Acceptance Standard (5 Fresh Videos)

Every release must produce **5 fresh jobs** that pass:

```text
✅ Provenance: 4/4 assets verified (Hamming 0–5 ≤ 12)
✅ Technical QA: 14 checks PASS (warnings only)
✅ Creative QA: 13 dimensions PASS (pacing, captions, visual match, etc.)
✅ Publish Readiness: READY (composite gate)
✅ YouTube Private Upload: Live + API-verified receipt
```

**No reused artifacts.** Each job = fresh script, fresh TTS, fresh assets, fresh render, fresh UUID.

---

## Test Suite

```powershell
cd autopilot
uv run pytest -q                    # 987 passed (incl. 12 new regression tests)
uv run pytest tests/test_creative_qa_measurement.py -v
uv run pytest tests/test_render_provenance.py -v
```

---

## Key Files

```
autopilot/
├── cli/main.py              # All CLI commands (produce, render, qa, publish)
├── core/
│   ├── pipeline.py          # Orchestration + provenance integration
│   ├── render_provenance.py # Perceptual verification gate
│   ├── creative_qa.py       # 13-dimension creative assessment
│   ├── publish_readiness.py # Composite gate
│   ├── timeline_builder.py  # MaterializedTimeline from real artifacts
│   └── stale_artifact_protection.py
├── providers/
│   ├── openai_llm_provider.py   # OpenRouter, Atria, Ollama, Gemini
│   ├── pexels_provider.py       # Real video search/download
│   ├── edge_tts_provider.py     # Neural TTS
│   └── transcription/faster_whisper_engine.py
└── tests/
    ├── test_render_provenance.py
    └── test_creative_qa_measurement.py   # 12 regression tests for measurement bugs
```

---

## Verified Results (Latest Run)

| Job | Topic | Duration | Provenance | Creative QA | Technical QA | YouTube (Private) |
|-----|-------|----------|------------|-------------|--------------|-------------------|
| `prod-How-Deep-Sea-Vents-Create-Life-49640c0d` | How Deep Sea Vents Create Life | 31.9s | 7/7 ✓ | PASS | PASS | `youtu.be/dQXjtVX5E5Q` |
| `prod-Why-Bioluminescence-Exists-in--85e0b9d3` | Why Bioluminescence Exists in Deep Ocean | 30.6s | 7/7 ✓ | PASS | PASS | `youtu.be/9nNDEaEvESk` |
| `prod-How-Submarine-Canyons-Shape-Oc-1ed8a7af` | How Submarine Canyons Shape Ocean Currents | 39.6s | 7/7 ✓ | PASS | PASS | `youtu.be/YbkJ2uxUW9E` |
| `prod-The-Mystery-of-Deep-Sea-Gigant-2415d25e` | The Mystery of Deep Sea Gigantism | 32.2s | 7/7 ✓ | PASS | PASS | `youtu.be/DW2FUcGNfN0` |
| `prod-How-Hydrothermal-Vents-Power-U-1abcb5e0` | How Hydrothermal Vents Power Unique Ecosystems | 32.6s | 6/6 ✓ | PASS | PASS | `youtu.be/-xQGsqZsv0c` |

All 5: **Provenance ✓, Technical QA ✓, Creative QA ✓, Publish Ready ✓, Live on YouTube (private) ✓**

---

## What This Is NOT

- ❌ No Tauri desktop shell (Python CLI only)
- ❌ No Ollama default (OpenRouter/Atria primary)
- ❌ No SAPI TTS (Edge TTS neural)
- ❌ No MoneyPrinterTurbo default (ffmpeg native)
- ❌ No Openverse primary (Pexels video primary)
- ❌ No synthetic QA passes (all gates fail-closed)
- ❌ No reused artifacts (fresh UUID per job)

---

## License

MIT — see `LICENSE`.