# PROJECT AUTOPILOT — Production Quality & Director Architecture Plan

**Document Version:** 4.0 (Master Autonomous Production Director & End-to-End Quality Engine Architecture)  
**Target:** High-Retention, Studio-Quality Autonomous Short-Form Video Production (1080x1920 MP4)  
**Status:** Canonical Implementation Blueprint  

---

## 🏛️ Master Closed-Loop Architecture Diagram

```
                                  ┌────────────────────────┐
                                  │    RESEARCH ENGINE     │
                                  └───────────┬────────────┘
                                              ↓
                                  ┌────────────────────────┐
                                  │ SCRIPT & FACT CLAIM    │
                                  │   VERIFICATION GATE    │
                                  └───────────┬────────────┘
                                              ↓
                                  ┌────────────────────────┐
                                  │   CONTENT-SPECIFIC     │
                                  │   SCRIPT DIRECTOR      │
                                  └───────────┬────────────┘
                                              ↓
                            ┌─────────────────────────────────────┐
                            │ 🎬 PRODUCTION DIRECTOR               │
                            │        IntentTimeline               │
                            └─────────────────┬───────────────────┘
                                              ↓
                     ┌────────────────────────┴────────────────────────┐
                     ↓                                                 ↓
          ┌──────────────────────┐                          ┌──────────────────────┐
          │ VISUAL INTELLIGENCE  │                          │ NEURAL VOICE STUDIO  │
          │ Media Materialization│                          │ Voice Materialization│
          └──────────┬───────────┘                          └──────────┬───────────┘
                     │                                                 │
                     └────────────────────────┬────────────────────────┘
                                              ↓
                               ┌──────────────────────────────┐
                               │  FASTER-WHISPER TRUTH PASS   │
                               └──────────────┬───────────────┘
                                              ↓
                            ┌─────────────────────────────────────┐
                            │ 📋 MaterializedTimeline             │
                            │ (Exact paths, trims, word timestamps)│
                            └─────────────────┬───────────────────┘
                                              ↓
                            ┌─────────────────────────────────────┐
                            │ ⚙️ TIMELINE COMPILER                │
                            │  10-Point Pre-Render Validation     │
                            │     Immutable RenderPlan (SHA-256)  │
                            └─────────────────┬───────────────────┘
                                              ↓
                            ┌─────────────────────────────────────┐
                            │  10 HARD RENDER INVARIANTS GATE     │
                            └─────────────────┬───────────────────┘
                                              ↓
                            ┌─────────────────────────────────────┐
                            │ MPT / FFMPEG RENDER ENGINE          │
                            │ Autopilot-Owned Assets/Audio Only   │
                            └─────────────────┬───────────────────┘
                                              ↓
                            ┌─────────────────────────────────────┐
                            │ DUAL QA ENGINE                      │
                            │ Technical QA + Creative QA          │
                            │ (Frame / Shot / Whole-Video Vision) │
                            └─────────────────┬───────────────────┘
                                              ↓
                            ┌─────────────────────────────────────┐
                            │          DEFECT CLASSIFIER          │
                            └──────┬──────────────────────┬───────┘
                                   │                      │
                           [DEFECT / LOW SCORE]        [PASS]
                                   │                      │
                                   ↓                      ↓
                    ┌────────────────────────────┐ ┌──────────────────────┐
                    │ TARGETED REGENERATION      │ │ COMPOSITE PUBLISH    │
                    │ Invalidation & Recompile   │ │ READINESS GATE       │
                    └──────────────┬─────────────┘ └──────────┬───────────┘
                                   │                          │
                                   └──────────────────────────┘
                                              ↓
                                    ┌──────────────────┐
                                    │ FINAL 1080x1920  │
                                    │ PUBLISH PACKAGE  │
                                    └──────────────────┘

═══════════════════════════════════════════════════════════════════════════════════════
   CROSS-CUTTING FOUNDATIONS:
   • Versioned Lineage & Manifests   • Cost & Resource Budgets   • Render Cache
   • Circuit Breakers & Backoff      • 60-Topic Benchmark Corpus • Strategy Boundaries
═══════════════════════════════════════════════════════════════════════════════════════
```

---

## 🔴 CROSS-PHASE 0 — The 3-Tier Timeline Architecture & Scene Contract

### 1. The 3-Tier Timeline Model
To eliminate sequencing contradictions where planning depends on un-materialized artifacts, the pipeline separates timeline evolution into three distinct, formal tiers:

```
┌─────────────────────────┐       ┌─────────────────────────┐       ┌─────────────────────────┐
│     IntentTimeline      │ ────> │  MaterializedTimeline   │ ────> │       RenderPlan        │
│ (Planning/Creative Spec)│       │(Resolved Files/Timings) │       │(Immutable Execution EDL)│
└─────────────────────────┘       └─────────────────────────┘       └─────────────────────────┘
```

1. **`IntentTimeline` (Director Creative Plan)**:
   * Emitted by the Production Director.
   * Contains narrative roles, target timing budgets, visual requirements, shot specifications, narration text, pronunciation overrides, caption styles, and BGM intensity cues.
2. **`MaterializedTimeline` (Post-Acquisition Ground Truth)**:
   * Emitted after Voice synthesis, Asset acquisition, and Faster-Whisper truth alignment.
   * Replaces targets with exact local file paths, measured audio durations, word boundary timestamps, actual crop bounding boxes, and clip trim offsets.
3. **`RenderPlan` (Immutable Renderer Instruction Object)**:
   * Compiled by the `TimelineCompiler`.
   * Contains exact compositor commands, layer mappings, filter complex definitions, and handoff configs for MPT and FFmpeg.
   * Carries cryptographic `render_plan_sha256`, `timeline_sha256`, `parent_timeline_version`, and `compiler_version`.

### 2. Formal `SceneContract` Schema
Every scene in the timeline adheres to a versioned contract enabling targeted micro-regeneration:

```json
{
  "scene_id": "scene-01",
  "scene_version": 1,
  "narrative_role": "HOOK",
  "timing": {
    "target_duration_sec": 4.8,
    "actual_duration_sec": 4.82,
    "start_time_sec": 0.0,
    "end_time_sec": 4.82
  },
  "narration": {
    "text": "What if India has 1.4 billion people, but one city has more billionaires than almost anywhere on Earth?",
    "normalized_pronunciation": "What if India has one point four billion people, but one city has more billionaires than almost anywhere on Earth?",
    "voice_id": "en-US-GuyNeural",
    "prosody_style": "energetic",
    "audio_artifact_path": "voice/seg_01.wav",
    "word_timestamps": [
      {"word": "What", "start": 0.05, "end": 0.28},
      {"word": "if", "start": 0.28, "end": 0.42},
      {"word": "India", "start": 0.42, "end": 0.85}
    ]
  },
  "visual_requirements": {
    "visual_concept": "Indian billionaire skyline contrast",
    "required_shot_type": "AERIAL",
    "coverage_expectation": "STRONG_MATCH",
    "visual_assertion_level": "literal"
  },
  "selected_assets": [
    {
      "asset_id": "pexels-vid-882194",
      "asset_path": "assets/norm_01_a.mp4",
      "media_type": "video",
      "provenance": {
        "provider": "pexels",
        "source_url": "https://www.pexels.com/video/882194/",
        "license": "Pexels Commercial",
        "author": "Cinematic Drone",
        "retrieved_at": "2026-09-26T16:00:00Z",
        "download_hash": "a1b2c3d4e5f6..."
      },
      "trim_range": {"in_sec": 1.2, "out_sec": 3.61},
      "shot_type": "AERIAL",
      "shot_spec": "wide drone shot of Mumbai skyline at sunset",
      "camera_motion": "slow_push",
      "crop_framing": {"saliency_x": 0.5, "saliency_y": 0.45, "caption_safe_zone": "LOWER"},
      "color_grade": "cinematic_warm"
    }
  ],
  "transition_plan": {
    "type": "zoom_in",
    "duration_sec": 0.35,
    "sfx_cue": "whoosh_fast"
  },
  "caption_plan": {
    "style_preset": "hormozi_yellow_pop",
    "position": "LOWER",
    "max_words_per_phrase": 3,
    "emphasis_words": ["1.4 BILLION", "BILLIONAIRES"],
    "platform_safe_zone": "youtube_shorts"
  },
  "audio_plan": {
    "bgm_intensity": 0.85,
    "sfx": [
      {"cue": "bass_hit", "time_sec": 0.05, "volume_db": -6.0},
      {"cue": "whoosh_transition", "time_sec": 2.35, "volume_db": -12.0}
    ]
  },
  "qa_expectations": {
    "min_semantic_match": 0.80,
    "allow_contextual_broll": true
  }
}
```

---

## ⚙️ CROSS-PHASE 0.5 — Timeline Compiler & Validation Engine

### 1. Two-Stage Compilation Lifecycle
* **Stage A (Planning Compilation)**: Validates logical continuity of `IntentTimeline` (narrative pacing, topic visualability, budget bounds).
* **Stage B (Executable Compilation)**: Consumes `MaterializedTimeline` and compiles the immutable `RenderPlan`.

### 2. The 10 Hard Render Invariants Suite
Before any rendering subprocess (MPT or FFmpeg) is permitted to execute, the compiler asserts 10 hard invariants:
1. **INVARIANT 01 — Timeline Continuity**: Scene intervals $[t_{start}, t_{end}]$ are non-overlapping and contiguous ($t_{end, i} \equiv t_{start, i+1}$).
2. **INVARIANT 02 — Physical Asset Existence**: Every referenced video, image, and graphic file physically exists and passes corruption tests.
3. **INVARIANT 03 — Audio Coverage**: Visual duration strictly covers narration duration ($\sum d_{visuals} \ge d_{narration}$).
4. **INVARIANT 04 — Caption Enclosure**: All subtitle timestamps are bounded within narration audio boundaries ($t_{cap} \subseteq [t_{voice, start}, t_{voice, end}]$).
5. **INVARIANT 05 — Transition Guard**: Transition durations do not exceed $50\%$ of the shortest adjacent scene duration.
6. **INVARIANT 06 — Trim Validity**: Clip trim ranges $[in, out]$ are valid within source media physical lengths.
7. **INVARIANT 07 — Audio Stem Integrity**: Voice stems, BGM track, and SFX files exist with valid sample rates (44.1kHz stereo).
8. **INVARIANT 08 — Monotonicity**: Timestamps within each ordered timeline stream must be non-decreasing; simultaneous events across independent tracks are permitted.
9. **INVARIANT 09 — License Integrity**: All media assets carry verified commercial-safe licenses in provenance records.
10. **INVARIANT 10 — Zero Stale Artifacts**: No render plan references an invalidated artifact version (`parent_timeline_version` match).

### 3. Autopilot → MPT Ownership & Render Contract
* **Strict Execution Role**: MoneyPrinterTurbo acts strictly as the **renderer**, not the creative decision-maker.
* **Prohibited MPT Behaviors**:
  * MPT **MUST NOT** search for replacement media or randomize clips.
  * MPT **MUST NOT** regenerate voice narration or generate its own subtitle timing.
  * MPT **MUST NOT** alter scene order.
  * If MPT cannot execute an instruction, the job **FAILS EXPLICITLY** with a machine-readable defect code (`MPT_RENDER_CAPABILITY_UNSUPPORTED`) rather than silently substituting creative elements.
* **Handoff Parameters**:
  * `custom_audio_file`: Autopilot-mastered stitched audio stem.
  * `video_materials`: Exact Autopilot-selected and cropped 1080x1920 video assets in order.
  * `match_materials_to_script`: `ON` (1:1 timeline mapping).
  * `random_material_selection`: `OFF`.

### 4. Manifest Integrity & Reproducibility Classification
Every completed video writes an integrity manifest with cryptographic binding:
* **Manifest Hashes**: `manifest_sha256`, `render_plan_sha256`, `media_sha256`, optional local HMAC.
* **Reproducibility Classification**:
  * `EXACT_REPRODUCIBLE`: Local deterministic engine with cached assets and fixed seeds.
  * `CONFIGURATION_REPRODUCIBLE`: Same profile parameters across live provider calls.
  * `TRACE_REPRODUCIBLE`: Complete raw provider input/output snapshot retained for audit.

---

## 🎙️ Phase 1: Voice & Speech Synthesis Upgrade (Neural Voice Studio)

### 1. Pronunciation Control & Text Normalization Layer
* **Acronym Normalizer**: (`NASA` $\to$ `"NA-SA"`, `ISRO` $\to$ `"ISS-RO"`, `AI` $\to$ `"A-I"`, `SQL` $\to$ `"S-Q-L"`).
* **Number & Unit Formatter**: (`1,400,000,000` $\to$ `"one point four billion"`, `$50M` $\to$ `"fifty million dollars"`, `15°N` $\to$ `"fifteen degrees north"`, `2026` $\to$ `"twenty twenty-six"`).
* **Scientific & Chemical Pronunciations**: (`CO₂` $\to$ `"C-O-two"`, `H₂O` $\to$ `"H-two-O"`).
* **Custom Pronunciation Dictionary**: JSON table in `autopilot/core/pronunciation_dict.json` for manual overrides.

### 2. Provider Capability Matrix & Prosody Profiles
```
Provider Capability Matrix:
├─ EdgeTTS: [Voice Selection: YES, Rate: YES, Pitch: YES, Word Timestamps: via alignment/truth-pass, Offline: NO]
├─ Kokoro ONNX: [Voice Selection: YES, Rate: YES, Pitch: NO, Word Timestamps: via alignment/truth-pass, Offline: YES]
└─ Windows SAPI (Deprecated Fallback): [Voice Selection: BASIC, Rate: YES, Offline: YES]
```
* **Prosody Delivery Profiles**:
  * `energetic`: Rate $+8\%$, Pitch $+2\text{Hz}$, punchy cadence.
  * `documentary`: Rate $-4\%$, Pitch $-3\text{Hz}$, calm authoritative pause cadence.
  * `mysterious`: Rate $-8\%$, expanded comma pauses ($350\text{ms}$), subtle drop inflection.
  * `conversational`: Natural pacing ($1.0\times$) with standard pauses.
* **Channel Narrator Binding**: Each channel profile binds to a default narrator identity (`default_voice_id`).

### 3. Final-Audio Truth Pass & Pronunciation QA
```
TTS Boundary Timestamps ──> Mastered Audio Stem ──> Faster-Whisper Word Alignment ──> Final Caption Timestamps
```
* **Post-TTS Pronunciation QA**: Runs local Faster-Whisper on synthesized speech to verify that synthesized words match expected text (catches defects like `ISRO` spoken as `"Israel"` or dropped words).
* **Measurable Voice Acceptance Metrics**:
  * Hard Safety: No audio clipping, no material truncation ($\le 0.05\text{s}$ target drift).
  * True Peak $\le -1.0\text{ dBTP}$, Integrated Loudness: $-16\text{ LUFS} \pm 1.0\text{ LUFS}$ (configurable for YouTube Shorts / Reels / TikTok).
  * Zero dropped words, zero abnormal pauses ($>1.2\text{s}$).

---

## 🧠 Phase 2: Script Intelligence, Claim Verification & Topic Difficulty

### 1. Factual Claim Verification Layer
Every script scene grounds its core claim in verified research evidence:
```json
{
  "claim": "India operates the world's highest rail bridge, the Chenab Bridge, standing 359 meters above the river bed.",
  "source": "wiki-Chenab_Bridge",
  "evidence": "The Chenab Bridge is a steel and concrete arch bridge ... height of 359 m (1,178 ft) above the river bed.",
  "verification": "VERIFIED",
  "confidence": 0.98,
  "claim_strength": "ESTABLISHED_FACT",
  "qualification_required": false
}
```
* **Contradiction Detection**: Flags conflicting multi-source data points for immediate prompt resolution rather than silent guessing.
* **Claim-Strength Calibration**: Classifies `ESTABLISHED_FACT`, `OBSERVED_DATA`, `THEORETICAL_PROPOSAL`, and `POPULAR_MYTH`.

### 2. Topic Difficulty Estimation & Content "Visualability"
* **Difficulty Classifier**: Evaluates source density, visual availability, and technical complexity (`EASY`, `MODERATE`, `HARD`, `RARE`).
* **Content Visualability Score**: Prefers facts that are both conceptually strong and visually depictable (e.g. *"Black holes bend light"* $\to$ HIGH visualability vs. abstract mathematical formulations $\to$ LOW visualability).

### 3. Content-Type Specific Director Templates
* `SCIENCE_EXPLAINER`: `HOOK` $\to$ `PARADOX` $\to$ `MECHANISM` $\to$ `EVIDENCE` $\to$ `IMPLICATION` $\to$ `PAYOFF`
* `HISTORY_STORY`: `HOOK` $\to$ `SETTING` $\to$ `CATALYST` $\to$ `CRISIS` $\to$ `RESOLUTION` $\to$ `LESSON`
* `LISTICLE`: `HOOK` $\to$ `FACT_1` $\to$ `FACT_2` $\to$ `FACT_3` $\to$ `FACT_4` $\to$ `FACT_5` $\to$ `FINAL_PAYOFF`
* `MYTH_BUST`: `HOOK` $\to$ `COMMON_BELIEF` $\to$ `WHY_ITS_WRONG` $\to$ `REAL_FACT` $\to$ `PROOF` $\to$ `PAYOFF`
* **Optional CTA Rule**: Call-To-Action is omitted if it weakens the final narrative payoff.

### 4. Cloud Provider Registry & Cost Tracking
* **Cloud Master Registry**: Standardized `ScriptGenerationContract` supporting `Gemini 2.5 Flash`, `OpenRouter`, `Claude 3.5`, `OpenAI`.
* **Cost & Budget Telemetry**: Tracks estimated generation cost, token usage, and latency metrics against per-video budgets.
* **Local Ollama (`qwen3:4b`) Dual-Role**: Dedicated local validator for schema validation and zero-dependency offline backup.

### 5. Search Query vs. Shot Spec Architecture
* **`b_roll_search_query`**: Concrete physical nouns for stock API retrieval (e.g. `"Chenab bridge river aerial"`).
* **`shot_spec`**: Directional framing instructions for the compositor (e.g. `"wide drone reveal, moving slow push forward, 4k"`).

---

## 🎬 Phase 3: Visual Intelligence & Semantic Asset Engine

### 1. Multi-Tier Stock Video & Informational Graphics
* **Tier 1 — Pexels Video API**: Vertical $1080\times 1920$ HD MP4 stock footage.
* **Tier 2 — Pixabay Video API**: Secondary vertical HD motion footage.
* **Tier 3 — Informational Graphics Generator**: Automated generation of clean maps, timelines, labeled diagrams, and statistical charts for science/history facts.
* **Tier 4 — Unsplash / Openverse**: High-resolution photography with dynamic motion.
* **Tier 5 — Local AI Generation (Flux.1 / SDXL)**: Custom 9:16 generation for hyper-specific historical scenes.

### 2. Dual-Gate Semantic Visual Scoring & Quality Floor
Every candidate asset undergoes hard gating followed by soft ranking:
* **Hard Gates**: Commercial rights verification, media corruption check, **effective resolution gate** (source media must contain sufficient pixels for the intended 9:16 crop at 1080x1920 without upscaling beyond configured threshold, allowing 4K/1080p landscape clips with valid vertical crop regions), watermark/stock overlay rejection, and blur rejection (Laplacian variance $< 150$).
* **Soft Ranking**: Semantic visual similarity, composition quality, subject prominence, and aesthetic lighting.
* **Visual Coverage Classes**: `EXACT_MATCH`, `STRONG_MATCH`, `CONTEXTUAL_MATCH`, `WEAK_MATCH`, `MISMATCH`. (Hard rule: `WEAK_MATCH` and `MISMATCH` blocked from publication).
* **Visual Assertion Levels**: Categorized as `literal`, `schematic`, `illustrative`, or `metaphorical`.

### 3. Video Candidate Temporal Quality Analysis
* Samples candidate stock video frames to evaluate motion stability, camera shake, freeze frames, and trim-safe action windows.

### 4. Saliency-Aware Dynamic Caption Safe Zones
* Uses **MediaPipe/OpenCV Saliency & Face Detection** to calculate subject bounding boxes and dynamically position captions (`TOP`, `CENTER`, `LOWER`) to avoid obscuring the primary subject.

### 5. Visual Diversity, Shot Continuity & Change Budget
* **No Near-Duplicates**: Uses perceptual hashes (pHash) and visual embeddings to prevent repeating identical subjects across scenes.
* **Shot Continuity**: Evaluates screen direction, subject orientation, and scale progression (`WIDE` $\to$ `MEDIUM` $\to$ `CLOSE_UP` $\to$ `MACRO`).
* **Visual Change Budget**: For scenes $>4.5\text{s}$, the Director plans $2$ to $3$ rapid visual cuts matching narrative clauses (not arbitrary time slicing).
* **Automated Color Grading**: Applies unified LUT / contrast and saturation normalization across mixed video sources.
* **Scene-Level Asset Provenance**: Full machine-readable attribution manifest (`provider`, `asset_id`, `source_url`, `license`, `author`, `download_hash`).

---

## ⚡ Phase 4: Modern Kinetic Captions, Audio Scene Graph & Editing

### 1. 2–5 Word Phrase Segmentation & Semantic Kinetic Typography
* **Font System**: Bundled Google Fonts (**`Montserrat-Black`**, **`THE BOLD FONT`**, **`Komika Axis`**).
* **Selective Keyword Emphasis**:
  * Phrases broken into 2–4 impactful words per screen (e.g., `["WORLD'S HIGHEST", "RAIL BRIDGE"]`).
  * Only semantic emphasis words pop in **Vibrant Yellow (`#FFE600`)** or **Neon Green (`#00FF66`)** with a $1.08\times$ scale spring animation.
  * Prevents "casino seizure mode" by keeping non-emphasis words cleanly formatted with black stroke borders.
* **Platform Safe Zones**: Automated geometry margins for YouTube Shorts, Instagram Reels, and TikTok.
* **Caption Fallback Hierarchy**: `ASS animated` $\to$ `static ASS` $\to$ `SRT` (preserves line-wrapping and safe zones).
* **Post-Render Caption OCR Verification**: Samples rendered frames to verify that burned-in subtitles match expected transcript and do not collide with UI boundaries.

### 2. Audio-Visual Beat Synchronization
* Key narrative beats trigger a synchronized multi-sensory cue:
  $$\text{Narrative Beat} \implies \text{Word Emphasis Pop} + \text{Visual Cut / Transition} + \text{SFX (Impact/Whoosh)} + \text{Music Accent}$$

### 3. Audio Scene Graph & Sidechain Ducking
* Multi-track audio composition with explicit fade-in, fade-out, attack, and release parameters:
  * **Track 1: Voice Narration** ($0\text{dB}$ reference, EBU R128 mastered).
  * **Track 2: Background Music (BGM)** (Ambient/Lo-Fi/Upbeat at $-22\text{dB}$ baseline, dynamically ducked to $-28\text{dB}$ during speech, rising to $-16\text{dB}$ during dramatic pauses).
  * **Track 3: SFX & Foley** (Subtle `whoosh_fast`, `bass_drop`, `digital_pop`, `camera_shutter` at scene transitions and badge pop-ups).

---

## 🔍 Phase 5: Dual QA Engine, Video Inspector UI & Targeted Regeneration

### 1. Dual QA Architecture: Technical QA + Creative QA

```
┌────────────────────────────────────────────────────────┐
│                   DUAL QA ARCHITECTURE                 │
├───────────────────────────┬────────────────────────────┤
│       TECHNICAL QA        │        CREATIVE QA         │
├───────────────────────────┼────────────────────────────┤
│ • Duration Drift (≤0.05s) │ • Hook Retention Score     │
│ • Silence Breach Check    │ • Frame-by-Frame OCR/Vision│
│ • Subtitle Overlap Check  │ • Visual Match Scorecard   │
│ • Audio Peak & Clipping   │ • Scene Cadence & Pacing   │
│ • Manifest Checksum       │ • Narrative Coherence      │
└───────────────────────────┴────────────────────────────┘
```

* **Video Understanding Frame Analysis**: Samples video frames every $1.5\text{s}$, runs local OCR and vision classification, and compares against the Director's scene intent.
* **Creative QA Scorecard**: Normalized $0-100$ scores with explicit target, warning, and blocking thresholds.
* **Human-Review Escalation**: Low-confidence evaluations route automatically to `HUMAN_REVIEW` rather than making false assumptions.

### 2. Defect Classifier & Targeted Regeneration Controller
* **Surgical Regeneration**:
  * `VISUAL_MISMATCH_SCENE_03` $\to$ Regenerate asset for Scene 3 only.
  * `VOICE_PRONUNCIATION_01` $\to$ Re-synthesize voice for Scene 1 only.
  * `CAPTION_OVERFLOW_04` $\to$ Re-segment caption for Scene 4 only.
* **Dependency Invalidation & Stale Artifact Detection**: Invalidation of Scene 3 visual automatically invalidates downstream render plan, QA report, and final video while strictly preserving unaffected scenes. Stale artifact references (`STALE_TIMELINE`, `STALE_ASSET`, `STALE_AUDIO`) trigger immediate validation blocks.

### 3. Permanent 60-Topic Creative Benchmark Dataset
* **Corpus Scope**: 10 science, 10 history, 10 technology, 10 geography, 10 listicles, 10 explainers/myths.
* **Negative Example Defect Tests**: Curated test cases with known defects (bad crop, robotic audio, caption overflow) to verify QA detection accuracy.
* **Golden Master Baseline & A/B Tooling**: Side-by-side comparison player tracking duration, asset relevance, caption timing, and loudness deltas against golden master productions.

### 4. Desktop UI Studio & Creator Mode
* **Creator Mode vs. Advanced Settings**: Simple creator controls (Topic, Voice, Visual Style, BGM) separated from engineering knobs (timeouts, rate limits, thresholds).
* **Multi-Track Video Inspector**: Visual timeline showing stacked tracks (`VOICE`, `CAPTIONS`, `VISUALS`, `BGM`, `SFX`) with per-scene inspection.
* **Scene-Level Preview Renderer**: Lightweight renderer for previewing single edited scenes in $<3$ seconds without full-video re-rendering.
* **"Before Publish" Review Screen**: Mandatory review gate presenting in-app video playback, creative QA scorecards, verified claims, and one-click *"Approve & Publish"* / *"Regenerate Scene"* actions.
* **Metadata Director & Thumbnail Generator**: Generates titles, descriptions, hashtags, and composite 9:16 thumbnails independently from spoken narration.

### 5. Strategy & Analytics Learning Boundaries
* Strategy layer may tune profile preferences, hook templates, and BGM styles based on audience retention.
* **Hard Boundary**: Analytics **MUST NOT** override factual verification gates, rights checks, or safety invariants.

### 6. Resource Budgeting & Content-Addressed Render Cache
* **Concurrency Limits**: Max 1 simultaneous Whisper job, max 1 MPT render job, bounded vision analysis workers.
* **Render Cache**: Content-addressed cache based on `sha256(input_manifest)` to instantly return cached renders for identical inputs.

---

## 🔒 Security & Credential Management Protocol
* All API keys (Pexels, Pixabay, Gemini, OpenRouter, YouTube) are resolved strictly via `.env`, OS environment variables, or local encrypted storage.
* **Zero Secret Leakage**: API tokens are never logged, never committed to Git, and automatically redacted from error traces and job artifacts.

---

## 📊 Summary of Phases & Engineering Deliverables

| Phase | Core Deliverable | Desktop UI Feature | Prerequisite Dependencies |
| :--- | :--- | :--- | :--- |
| **Phase 0** | Production Director (`IntentTimeline` & `MaterializedTimeline`) | Timeline data model | None (Foundational Architecture) |
| **Phase 0.5**| Timeline Compiler & 10 Hard Render Invariants | Pre-render validation badges | Phase 0 |
| **Phase 1** | Neural Voice Studio (`edge-tts` / Kokoro / Whisper Truth Pass) | Voice Selector Dropdown | Phase 0, 0.5 |
| **Phase 2** | Script Grounding, Claim Verification & Content Templates | Factual Verification View | Phase 0, 0.5 |
| **Phase 3** | HD Stock Video, Graphics & Saliency Crop Engine | Asset Thumbnail Previews | Phase 0, 0.5, Phase 2 |
| **Phase 4** | Kinetic Captions, Multi-Sensory Beat Sync & Audio Scene Graph | Style & BGM Tone Presets | Phase 0, 0.5, Phase 1, Phase 3 |
| **Phase 5** | Dual QA, Video Inspector UI, Scene Preview & Publish Package | In-App Player & Scene Tuning | Phase 0 to 4 |

---
