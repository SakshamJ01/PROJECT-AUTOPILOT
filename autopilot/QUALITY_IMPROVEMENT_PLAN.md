# Production Quality Improvement Plan

**Goal:** Raise output quality of produced videos across visual, script, audio, and distribution layers.

**Current baseline (measured from 2 published jobs):**
- Moon job: 7 scenes, 23.5s, only **4 unique clips** (one reused 3×), CLIP 0.23–0.30, hook_text `None`
- Titanic job: 6 scenes, 23.3s, 6/6 unique clips, CLIP 0.22–0.30
- Hard cuts between every scene, procedural BGM, no thumbnails, one TTS voice, thin metadata

---

## PHASE 1 — Visual (highest impact)

### 1.1 Hard clip deduplication (the real bug)

**Root cause (confirmed by code reading, not guessing):**
Dedup is *wired* but **cannot fire** for Pexels. Two independent defects:

1. `asset_pipeline.py:263` dedup test is `c.provenance.original_hash_sha256 in seen_checksums`. But `pexels_provider.py:235-240` builds `AssetProvenance` **without** `original_hash_sha256` — it's only set *after* download at `asset_pipeline.py:478`. So at scoring time the field is `None` and the membership test is always `False`.
2. Even if it fired, dedup is a **soft penalty**: `asset_scoring.py:131` sets `dup_score = 0.0` weighted at `_W_DEDUP = 0.05` (asset_scoring.py:36). A duplicate with a strong CLIP score still wins.

**Changes:**

- `autopilot/providers/pexels_provider.py` — populate a **search-time stable ref**. `AssetProvenance` is built at `:235-240` without `original_hash_sha256`, so checksum dedup can never fire pre-download. But `source_id` (the Pexels video ID) *is* available at search time (`:237`). Add `provider_asset_ref: Optional[str]` to `AssetProvenance` (`contracts.py`) and set it from `source_id` in every provider (Pexels, Pixabay, Openverse, local).
- `autopilot/core/asset_pipeline.py:260-302` (`get_cleared_candidates`) — **hard-exclude** any candidate whose `provider_asset_ref` (falling back to `original_hash_sha256`) was already chosen in this job. Track a `chosen_asset_refs: set[str]` populated on successful selection (next to `seen_checksums` at :479). Change the loop to `continue` on duplicate instead of relying on score. Only fall back to allowing a duplicate if the scene would otherwise have *zero* cleared candidates (better a repeat than a failed job — recorded as a warning).
- `autopilot/core/asset_scoring.py:130-132` — keep the soft penalty as a secondary signal; also raise `_W_DEDUP` 0.05 → 0.15 as defense-in-depth.

**Files:** `asset_pipeline.py`, `pexels_provider.py`, `contracts.py` (add field), `asset_scoring.py`

**Test:** new `tests/test_asset_dedup.py` — two scenes whose top candidate shares a `provider_asset_ref`; assert the second scene gets a *different* asset, assert the rejections report records the duplicate drop, and assert the zero-candidate fallback still yields an asset. Unit-testable without network by faking candidates.

**Verification on real job:** after change, regenerate a job and assert `len(set(asset ids)) == len(scenes)` from `render_plan.json`.

---

### 1.2 Scene transitions (replace hard cuts)

**Current:** `renderer.py:434-459` concatenates segments with a bare `concat` filter. Hard cut at every boundary.

**Change:** implement transitions via ffmpeg `xfade` at concat time.

> **Verified empirically (2026-10-04):** naive xfade *does* shorten the output by
> `duration × (n−1)` and *does* desync the muxed narration. The fix is the **silent
> handle** design, which I prototyped and measured:
> - Render each segment `T` seconds **longer** than its scene duration (video loops to
>   fill; audio = voice + `anullsrc` silence for the handle).
> - xfade at `offset = scene_duration` (start of the *next* scene's speech, i.e. the
>   silent tail), `duration = T`.
> - Measured result: input 3.85s + 3.00s → output **6.52s ≈ 3.5 + 3.0 exactly**. The audio
>   timeline is **preserved**, so `_apply_audio_scene_graph_mix`'s `cursor`-based row
>   offsets and SFX placement need **no recomputation**.
> - Boundary RMS confirmed audio continuity; dominant-frequency check confirmed the two
>   narration tones occupy their original windows with **no overlap**.
>
> This removes the sync risk I originally flagged — the audio path is untouched.

**Implementation:**
- `renderer.py` concat block: for each adjacent pair, emit `[prev][next]xfade=transition=<t>:duration=T:offset=<cumulative scene durations>[v];[prev_a][next_a]acrossfade=d=T[a]`, chaining `xfade` outputs. Use the scene's `transition_hint` (already emitted by `timeline_compiler.py:628-629` into the plan, currently **ignored** by the renderer): `cut` → no xfade (plain concat, backward compatible); `fade_in`/`fade_out`/`fade` → `fadeblack`; else → `fade`.
- Segments must be rendered with the handle: extend `-t` by `T` for all but the last scene and pad audio with `anullsrc` trimmed to the handle. Add a `TRANSITION_DURATION_SEC = 0.35` constant (configurable via `CONFIG`).
- Keep the guard at `renderer.py:521` (rendered < narration − 1.0s raises) as the safety net.
- xfade requires both inputs to share resolution/fps/pixfmt/timebase (`ffmpeg-filters.html#xfade`) — already guaranteed since every segment is encoded identically (1080×1920, 25fps, yuv420p) at `renderer.py:411-421`.

**Files:** `renderer.py` only.

**Test:** 2-scene plan with `transition_hint: "fade"`; assert output duration ≈ `d1 + d2` (**not** minus T), audio contains both narrations in their original windows, and the narration-truncation guard still passes. Plus a `cut` case asserting byte-identical behavior to today.

---

### 1.3 Raise effective CLIP relevance

**Current floor:** `config.py:73` `visual_semantic_min_similarity = 0.21`. Measured scores 0.22–0.30 sit just above it — barely passing.

**Change:** keep the 0.21 hard floor (it was validated against a real/decoy pair) but add a **second-chance query rewrite**: if the best cleared candidate scores `< 0.28`, retry the scene search once with a broader synonym query derived from `visual_intent` before accepting. Implement as a new helper `derive_broader_query(scene, topic)` in `asset_pipeline.py` alongside the existing `derive_deterministic_fallback_query`.

**Files:** `asset_pipeline.py`, `config.py` (new `visual_semantic_strong_threshold: float = 0.28`)

**Test:** unit test the query-derivation function; integration assertion that a scene scoring 0.25 triggers a re-search.

---

## PHASE 2 — Script & retention

### 2.1 Word-count floor and anti-filler rules

**Current:** `openai_llm_provider.py` enforces only a ceiling (`MAX_SCENE_SPEECH_WORDS = 8`, derived from `4.5s × 1.95 w/s`). No floor. Measured scenes: 5–8 words. Scene-02 of the Moon script ("It's been explored by robots and humans") carries no information about the topic.

**Changes to the prompt block (`openai_llm_provider.py:305-324`):**
- Add `MIN_SCENE_SPEECH_WORDS = 5` and state both bounds in the prompt: "Between 5 and 8 words. Below 5 is too thin to convey a fact."
- Add an **information-density rule**: "Every middle scene MUST deliver one concrete fact, number, date, or causal claim grounded in the evidence. Do NOT write filler scenes that only restate the topic or the hook."
- Add an explicit **anti-filler list**: ban generic lines like "It's been explored by robots and humans", "As you can see", "Let's dive in", "But wait, there's more".
- Add a **topic-relevance rule**: "Every scene narration must advance the viewer's understanding of the topic. If a scene could appear unchanged in a video about any other subject, rewrite it."

**Validation (not just prompting):** add a post-parse guard in the same file where pacing is already validated. Compute per-scene word counts; reject and trigger a corrective retry if any scene < `MIN_SCENE_SPEECH_WORDS` or > ceiling. Reuse the existing `PacingBudgetError` corrective mechanism (it already retries then fails closed) rather than inventing a new rejection path.

**Files:** `openai_llm_provider.py`

**Test:** extend `tests/test_openai_provider.py` / `test_creative_qa_measurement.py` — feed a parsed script with a 4-word scene, assert rejection with offender named; feed a clean 5-word scene, assert pass.

---

### 2.2 Hook verification (hook_text was `None` on the Moon job)

**Current:** the LLM is *asked* for `hook_text` (`openai_llm_provider.py:221`) and it's parsed at :494 with a fallback to scene-1 narration. But nothing validates it, and on the Moon job `script.json`'s stored hook was `None` — the field silently fell back.

**Changes:**
- In the parse/validation block, require a non-empty `hook_text` that is **distinct from scene-01 narration** (a hook that just repeats scene 1 adds nothing) and is phrased per the channel's `hook_style` (for `history_shorts`: `historical_framing_and_date` — a date/framing hook). On failure, synthesize one from the strongest middle-scene fact rather than silently defaulting, and record that it was synthesized.
- Persist `hook_text` into `script.json` metadata unconditionally so the publisher's description path (`publisher.py:693`) always sees it.

**Files:** `openai_llm_provider.py`, plus whatever writes `script.json` metadata (verify hook round-trips into the `hook` key the publisher reads).

**Test:** assert a script missing `hook_text` gets a synthesized, non-empty, scene-1-distinct hook; assert it appears in `script.json`.

---

### 2.3 Pacing variation (rhythm)

**Current:** every scene 3.0–4.3s — metronomic. No tempo model.

**Change:** introduce a **duration-shape template** applied at timeline materialization. For a 7-scene short: hook slightly faster (2.8–3.2s), middle scenes at the speech-natural duration, payoff slightly longer (up to `PACING_MAX_SCENE_SECONDS`). Implement in `timeline_builder.py` where `duration` is computed (the `cursor += duration` loop at :267) as a small `apply_pacing_shape(scenes)` helper that *nudges* the target duration while never exceeding the pacing ceiling and never going below the measured voice duration (voice is the floor — we cannot compress speech).

**Files:** `timeline_builder.py`

**Test:** unit test `apply_pacing_shape` — 7 scenes, assert scene-1 ≤ middle, last ≥ middle, all within [voice_dur, 4.5].

---

## PHASE 3 — Audio

### 3.1 Per-topic BGM mood (replace single procedural pad)

**Current:** `audio_scene_graph.py:168-198` synthesizes one fixed C–G–Am–F lo-fi pad for every video regardless of topic.

**Change:** add a mood resolver that picks chord timbre/tempo by topic category. Map the channel niche + topic keywords to a small set of named moods (e.g. `contemplative`, `dramatic`, `uplifting`), each with its own chord progression + pulse rate constants. Keep it procedural (no licensing risk) but make it **deterministic per topic** (hash the topic → mood) so the same topic always gets the same bed. Add a `mood` field to the audio mix manifest for observability.

**Files:** `audio_scene_graph.py`

**Test:** unit test that two different topics yield different mood labels and that the same topic yields the same mood.

---

### 3.2 TTS prosody variation per scene role

**Current:** `edge_tts_provider.py:55` default `en-US-ChristopherNeural`, no per-scene variation.

> **Verified:** `edge_tts.communicate.Communicate.__init__` accepts `rate: str`, `pitch: str`,
> `volume: str` (e.g. `"+8%"`, `"+0Hz"`) — these are first-class constructor params in the
> installed edge-tts, so no SSML hackery needed. Kokoro's `synthesize` passes `speed=1.0`
> **hardcoded** (`kokoro_tts_provider.py:192`), so per-scene rate is Edge-TTS-only for now;
> I'll route the prosody hint through `**kwargs` and have Kokoro ignore it (it already
> accepts `**kwargs`), avoiding a provider-specific fork.

**Change:** pass the scene's `narrative_role` (already present in the timeline, `timeline_compiler.py:622`) into the TTS request as a prosody hint: hook scenes `rate +8%`, payoff scenes `rate −5%`, middle scenes neutral. Because rate changes the **rendered audio length**, apply it **before** the word-timestamp alignment pass so caption/SFX timing reflects the actual audio (timestamps are derived from the rendered WAV, so they self-correct as long as alignment runs after synthesis).

**Files:** `edge_tts_provider.py` (accept `rate`/`pitch` kwargs), the per-scene synthesis caller to map role → prosody.

**Test:** assert hook scene request carries `rate="+8%"` and payoff `rate="-5%"`; assert generated audio duration changes accordingly; assert Kokoro path ignores the hint without error.

---

## PHASE 4 — Distribution

### 4.1 Custom thumbnails (biggest CTR lever)

**Current:** `thumbnails/` dir is created (`artifacts.py:49`) but **empty** on both published jobs. `youtube_publisher.py:158-169` sends no thumbnail. YouTube picks a random frame.

> **Verified against YouTube Data API v3 docs (`thumbnails: set`):**
> - Endpoint: `POST https://www.googleapis.com/upload/youtube/v3/thumbnails/set?videoId=...`
> - Requires scope `youtube.upload` — **already granted** by our existing token
>   (`DEFAULT_SCOPES` includes it in `youtube_oauth.py:27`).
> - Body is the raw image; accepted `image/jpeg` / `image/png`, ≤ **50 MB**.
> - Quota cost ≈ 50 units per call.
> - Returns a `thumbnailSetResponse` with the chosen thumbnails.
>
> So the plan's two-step approach is correct and needs **no new OAuth scope**: upload the
> video, then `thumbnails.set` with the composed image.

**Change:** add a thumbnail generator that composes a 1080×1920 frame from the strongest scene asset (highest CLIP score) with the `on_screen_text` badge + hook text burned in using the channel's font/colors (`history_shorts`: Georgia, `#E0C38C`/`#8B5A2B`). Write to `<job>/thumbnails/thumb.jpg` (JPEG, under 2 MB comfortably). After a successful video upload in `youtube_publisher.py`, call `thumbnails.set` with the image.

**Files:** new `autopilot/core/thumbnail.py`, `youtube_publisher.py` (post-upload `thumbnails.set`), `contracts.py` (PublishRequest field for thumbnail path)

**Test:** generate a thumbnail from a fixture asset + text; assert 1080×1920, JPEG, non-trivial size (file > 20KB), text visible. Mock the `thumbnails.set` call in the publisher test; assert it is skipped cleanly when no thumbnail file exists (backward compatible with existing jobs).

---

### 4.2 Richer metadata (SEO)

**Current:** `publisher.py:859` hardcodes `category_id="28"`. Description is one sentence; tags minimal (`['shorts','educational']` on Titanic).

**Changes:**
- Derive `category_id` from the channel niche's `allowed_categories` (`channel.py:73`). **Verified the live assignable category list via the YouTube API** for `regionCode=US`: `27 Education`, `28 Science & Technology`, `22 People & Blogs`, `24 Entertainment`, `25 News & Politics`, `26 Howto & Style`. Note **there is no dedicated "History" category** — so `history_shorts` maps to `27 Education` (not the current hardcoded `28`), and `science_shorts` maps to `28`. I'll put this mapping in `channel.py` next to the profile definitions so each niche declares its own category.
- Expand description: hook line + a "key facts" bulleted list derived from middle-scene narrations + hashtags from `tags`.
- Enrich tags: merge LLM tags + topic-derived keywords + channel niche tags; cap at YouTube's 500-char limit.

**Files:** `publisher.py` (read category from resolved channel profile), `channel.py` (add `youtube_category_id` per niche)

**Test:** unit test description assembly from a fixture script; assert tags deduped and under 500 chars; assert `history_shorts` resolves to category `27`.

---

## PHASE 5 — Coverage & rollout

### 5.1 Test additions (per change, listed above)
- `tests/test_asset_dedup.py` (1.1)
- renderer transition test (1.2)
- query-derivation test (1.3)
- script floor / hook tests (2.1, 2.2)
- pacing-shape test (2.3)
- BGM mood test (3.1)
- TTS prosody test (3.2)
- thumbnail test (4.1)
- metadata test (4.2)

### 5.2 Regression guard
- Bump `PIPELINE_VERSION` in `stale_artifact_protection.py` so old jobs are flagged stale and regenerated with the new pipeline (expected, since rendering changes).
- Run full suite (`pytest -q`) before committing.

### 5.3 Production validation
- Produce the 3 approved topics (Mars red, ocean blue, Antikythera) end-to-end.
- Before/after comparison on: unique-clip count, scene word counts, hook presence, transition presence, thumbnail presence, measured ducking.
- Strict QA + publish private, same gate as before.

---

## Priority order for implementation

| # | Change | Impact | Risk | Depends on |
|---|--------|--------|------|------------|
| 1.1 | Hard clip dedup | High | Low | — |
| 1.2 | Scene transitions (silent-handle xfade) | High | **Low** (de-risked by prototype) | — |
| 2.1 | Script density floor | High | Low | — |
| 4.1 | Thumbnails | High (CTR) | **Low** (scope already granted) | — |
| 2.2 | Hook verification | Medium | Low | 2.1 |
| 1.3 | CLIP second chance | Medium | Low | 1.1 |
| 2.3 | Pacing variation | Medium | Low | — |
| 3.1 | BGM mood | Medium | Low | — |
| 3.2 | TTS prosody | Medium | Low (Edge only; Kokoro ignores) | — |
| 4.2 | Metadata SEO | Low-Med | Low | — |

I'll implement in this order, committing after each verified change, and will stop immediately if any change breaks the suite or the narration-sync guard.

---

## Research validation (2026-10-04)

Verified against the installed stack that the three approved topics actually ground through the real research provider before committing to producing them:

| Topic | Wikipedia results | First hit |
|---|---|---|
| Why is Mars red | 3 | "Mars surface color" |
| Antikythera mechanism | 3 | "Antikythera mechanism" |
| Why is the ocean blue | 3 | "Blue" |

All three return usable evidence, so factual grounding will succeed. (This matters: the Moon job produced *no* research artifacts beyond `gate_evidence.json`, and its script leaned on general knowledge — confirming the "thin script" symptom in 2.1.)

---

## What changed in this review (2026-10-04)

1. **Transitions (1.2): rewritten and de-risked.** My original plan correctly identified that xfade shortens output and desyncs audio, but proposed compensating audio offsets afterward — fragile. I prototyped the **silent-handle** design and **measured** it: output duration is *exactly* preserved (6.52s ≈ 3.5 + 3.0) and narration windows don't overlap, so the audio mix path needs **zero changes**. Risk downgraded Medium → Low; files touched reduced to `renderer.py` only.
2. **Thumbnails (4.1): no new OAuth scope needed.** Verified `thumbnails: set` accepts the `youtube.upload` scope we already hold (≤50MB, jpeg/png). Confirmed the two-step upload-then-set flow against the live API docs.
3. **Category mapping (4.2): resolved the open question.** I had written "27 Education or 28 Science&Tech — I'll map explicitly." I pulled the **live** assignable category list for regionCode=US: there is **no History category**, so `history_shorts` → `27 Education` (current code hardcodes `28`).
4. **TTS prosody (3.2): confirmed the installed edge-tts exposes `rate`/`pitch`/`volume` as constructor params** — no SSML workaround. Also found Kokoro hardcodes `speed=1.0`, so prosody is Edge-only and must degrade gracefully on Kokoro.
5. **Dedup (1.1): narrowed the root cause.** Confirmed Pexels *does* set `source_id` at search time (`:237`) even though it omits the hash, so the fix keys on a new `provider_asset_ref` rather than trying to backfill hashes. Added a zero-candidate fallback so dedup can never make a job unproducible.
6. **Topics validated:** all three approved topics return real Wikipedia evidence, so production won't stall at the grounding stage.
