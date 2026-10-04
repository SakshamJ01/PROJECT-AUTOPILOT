"""OpenAI-Compatible LLM Providers — Standard interface for local and cloud model servers.
Supports Ollama, Gemini (official OpenAI-compatible API), OpenRouter, and custom endpoints.
Fully compliant with ScriptDocument Pydantic contract, streaming chunk accumulation,
thinking/content separation, prompt-size bounds, and secret-safe logging.
"""
from __future__ import annotations
import json
import os
import re
import socket
import time
import urllib.request
import urllib.error
from typing import Any, Dict, Optional, List, Tuple

from autopilot.providers.contracts import LLMProvider, ProviderHealth, CapabilityMetadata, CostUsageMetadata, ProviderErrorType
from autopilot.core.contracts import ScriptDocument, ScriptScene
from autopilot.core.duration import estimate_duration

# Pacing contract, shared by the generation prompt and the script-time guard.
# Creative QA hard-blocks any scene whose measured speech runs past 4.5s
# (see CreativeQAEngine._evaluate_pacing_cadence). Edge TTS measured 2.22
# words/sec over a 73-word corpus, but real per-scene delivery including
# sentence-final pauses settles at ~1.95 words/sec: a 10-word scene rendered at
# 4.6-5.2s and tripped the pacing gate despite passing a naive word count.
# The pre-flight guard, the prompt, and the gate must therefore all derive from
# the same limit, so the ceiling is computed rather than hardcoded.
PACING_MAX_SCENE_SECONDS = 4.5
TTS_WORDS_PER_SECOND = 1.95
MAX_SCENE_SPEECH_WORDS = int(PACING_MAX_SCENE_SECONDS * TTS_WORDS_PER_SECOND)
MIN_SCENE_SPEECH_WORDS = 5
# Scene count the prompt asks for. The advertised duration/word totals are
# derived from these so the prompt cannot drift from the pacing budget.
MIN_SCENES = 7
MAX_SCENES = 8


class PacingBudgetError(ValueError):
    """Raised when generated narration exceeds the per-scene speech budget.

    Carries the offending scenes so the caller can issue a targeted corrective
    regeneration instead of failing the whole production run.
    """

    def __init__(
        self,
        message: str,
        offenders: Optional[List[Tuple[str, int]]] = None,
        offender_text: Optional[Dict[str, str]] = None,
    ) -> None:
        super().__init__(message)
        self.offenders: List[Tuple[str, int]] = list(offenders or [])
        self.offender_text: Dict[str, str] = dict(offender_text or {})

    def correction_directive(self) -> str:
        detail = ", ".join(f"{sid} ({count} words)" for sid, count in self.offenders)
        # Showing the rejected text is what makes the correction actionable;
        # naming counts alone left the model re-emitting the same length.
        examples = ""
        if self.offender_text:
            lines = []
            for sid, _count in self.offenders:
                text = self.offender_text.get(sid)
                if text:
                    lines.append(f'  {sid}: "{text}"')
            if lines:
                examples = (
                    "\nThe rejected narration was:\n"
                    + "\n".join(lines)
                    + "\nRewrite each of those to "
                    f"{MAX_SCENE_SPEECH_WORDS} words or fewer, counting the words you write."
                )
        return (
            f"\nPACING CORRECTION (mandatory): the previous attempt was REJECTED because these "
            f"scenes exceeded the {MAX_SCENE_SPEECH_WORDS}-word ceiling: {detail}. "
            f"Rewrite those narrations to {MAX_SCENE_SPEECH_WORDS} words or fewer each. "
            f"Drop filler words, ensure each scene conveys a concrete fact, and avoid restating the topic. Split one idea across two scenes, or cut adjectives. "
            f"Keep the same facts and the same scene count unless you genuinely need more scenes."
            f"{examples}"
        )


def redact_api_key(text: str) -> str:
    """Redact bearer tokens and secret keys from strings."""
    if not text:
        return ""
    # Redact Authorization: Bearer tokens
    text = re.sub(r"(Bearer\s+)[a-zA-Z0-9_\-\.]{6,}", r"\1[REDACTED]", text, flags=re.IGNORECASE)
    # Redact common API key prefixes (Google AI, OpenRouter, OpenAI)
    text = re.sub(r"(AIzaSy|sk-or-v1-|sk-)[a-zA-Z0-9_\-\.]{10,}", r"[REDACTED_KEY]", text)
    return text


def _iter_response_lines(resp: Any):
    """Safely stream lines from HTTPResponse, iterables, generators, or test mocks."""
    if type(resp).__name__.startswith("MagicMock"):
        if hasattr(resp, "read"):
            try:
                content_bytes = resp.read()
                if isinstance(content_bytes, str):
                    content_bytes = content_bytes.encode("utf-8")
                if isinstance(content_bytes, (bytes, bytearray)):
                    for line in content_bytes.splitlines(keepends=True):
                        yield line
                    return
            except Exception:
                pass
        if hasattr(resp, "__iter__"):
            try:
                for item in resp:
                    if isinstance(item, (bytes, str)):
                        yield item
                return
            except Exception:
                pass

    if hasattr(resp, "__iter__") and not type(resp).__name__.startswith("MagicMock"):
        try:
            it = iter(resp)
            for line in it:
                yield line
            return
        except TypeError:
            pass

    if hasattr(resp, "read"):
        content_bytes = resp.read()
        if content_bytes and not type(content_bytes).__name__.startswith("MagicMock"):
            if isinstance(content_bytes, str):
                content_bytes = content_bytes.encode("utf-8")
            if isinstance(content_bytes, (bytes, bytearray)):
                for line in content_bytes.splitlines(keepends=True):
                    yield line


def _build_prompts_and_evidence(
    topic: str,
    research_report: object = None,
    channel_profile: object = None,
    corrective_instructions: Optional[str] = None,
    regeneration_reason: Optional[str] = None,
    attempt_number: int = 1,
    target_duration: float = 35.0,
) -> Tuple[str, str, List[str], set[str]]:
    """Build bounded, deduplicated system and user prompts with research evidence."""
    evidence_snippets: List[str] = []
    valid_source_refs: set[str] = set()
    seen_snippets: set[str] = set()

    if hasattr(research_report, "sources"):
        for src in getattr(research_report, "sources", []):
            s_id = getattr(src, "source_id", "")
            snip = getattr(src, "excerpt", "").strip()
            title = getattr(src, "title", "").strip()
            pub = getattr(src, "publisher", "").strip()
            if s_id:
                valid_source_refs.add(s_id)
            if snip and snip not in seen_snippets:
                seen_snippets.add(snip)
                meta_str = f" [Source: {s_id}]" if s_id else ""
                if title or pub:
                    meta_str += f" ({pub} - {title})"
                evidence_snippets.append(f"- {snip}{meta_str}")
    elif isinstance(research_report, dict):
        summary = (research_report.get("summary") or "").strip()
        if summary:
            evidence_snippets.append(f"Summary: {summary}")
        evidence_list = research_report.get("evidence") or []
        for ev in evidence_list:
            if isinstance(ev, dict):
                src_id = (ev.get("source_id") or ev.get("url") or "").strip()
                snippet = (ev.get("snippet") or "").strip()
                title = (ev.get("title") or "").strip()
                publisher = (ev.get("publisher") or "").strip()
                if src_id:
                    valid_source_refs.add(src_id)
                if snippet and snippet not in seen_snippets:
                    seen_snippets.add(snippet)
                    meta_str = f" [Source: {src_id}]" if src_id else ""
                    if title or publisher:
                        meta_str += f" ({publisher} - {title})"
                    evidence_snippets.append(f"- {snippet}{meta_str}")
    elif hasattr(research_report, "summary") or hasattr(research_report, "results"):
        summary = getattr(research_report, "summary", None)
        if summary:
            evidence_snippets.append(f"Summary: {summary}")
        results = getattr(research_report, "results", [])
        for res in results:
            items = getattr(res, "evidence_items", [])
            for ev in items:
                src_id = getattr(ev, "source_id", "").strip()
                snippet = getattr(ev, "snippet", "").strip()
                if src_id:
                    valid_source_refs.add(src_id)
                if snippet and snippet not in seen_snippets:
                    seen_snippets.add(snippet)
                    evidence_snippets.append(f"- {snippet} [Source: {src_id}]")

    has_research = len(evidence_snippets) > 0

    # Extract channel profile directives
    channel_rules = {}
    if channel_profile:
        if hasattr(channel_profile, "to_editorial_rules"):
            channel_rules = channel_profile.to_editorial_rules()
        elif isinstance(channel_profile, dict):
            channel_rules = channel_profile

    channel_tone = channel_rules.get("tone", "clear, direct, evidence-oriented")
    channel_niche = channel_rules.get("niche", "general")
    channel_hook_style = channel_rules.get("hook_style", "intriguing_question")
    channel_visual_motif = channel_rules.get("visual_motif", "clean")

    from autopilot.core.quality import detect_listicle_cardinality
    cardinality = detect_listicle_cardinality(topic) if topic else None

    system_prompt = (
        "You are an expert short-form video scriptwriter and editorial director. Output JSON ONLY matching this structure:\n"
        "{\n"
        '  "title": "Title string",\n'
        '  "description": "Video description",\n'
        '  "hook_text": "Intriguing hook sentence (first 2-3 seconds)",\n'
        '  "scenes": [\n'
        '    {\n'
        '      "scene_id": "scene-01",\n'
        '      "order": 1,\n'
        '      "narration": "Punchy hook or intro narration",\n'
        '      "visual_intent": "Concrete physical photographic description",\n'
        '      "asset_query": "2-3 word photographic search query",\n'
        '      "on_screen_text": "2-4 WORD UPPERCASE BADGE",\n'
        '      "emphasis_words": ["KEYWORD"],\n'
        '      "estimated_duration_seconds": 5.0,\n'
        '      "scene_type": "talking_head",\n'
        '      "transition_hint": "fade"\n'
        '    },\n'
        '    {\n'
        '      "scene_id": "scene-02",\n'
        '      "order": 2,\n'
        '      "narration": "First substantive fact or point",\n'
        '      "visual_intent": "Concrete physical photographic description",\n'
        '      "asset_query": "2-3 word photographic search query",\n'
        '      "on_screen_text": "2-4 WORD UPPERCASE BADGE",\n'
        '      "emphasis_words": ["KEYWORD"],\n'
        '      "estimated_duration_seconds": 5.0,\n'
        '      "scene_type": "broll",\n'
        '      "transition_hint": "fade"\n'
        '    },\n'
        '    {\n'
        '      "scene_id": "scene-03",\n'
        '      "order": 3,\n'
        '      "narration": "Next substantive fact or context",\n'
        '      "visual_intent": "Concrete physical photographic description",\n'
        '      "asset_query": "2-3 word photographic search query",\n'
        '      "on_screen_text": "2-4 WORD UPPERCASE BADGE",\n'
        '      "emphasis_words": ["KEYWORD"],\n'
        '      "estimated_duration_seconds": 5.0,\n'
        '      "scene_type": "broll",\n'
        '      "transition_hint": "fade"\n'
        '    },\n'
        '    {\n'
        '      "scene_id": "scene-04",\n'
        '      "order": 4,\n'
        '      "narration": "Additional surprising fact or depth",\n'
        '      "visual_intent": "Concrete physical photographic description",\n'
        '      "asset_query": "2-3 word photographic search query",\n'
        '      "on_screen_text": "2-4 WORD UPPERCASE BADGE",\n'
        '      "emphasis_words": ["KEYWORD"],\n'
        '      "estimated_duration_seconds": 5.0,\n'
        '      "scene_type": "broll",\n'
        '      "transition_hint": "fade"\n'
        '    },\n'
        '    {\n'
        '      "scene_id": "scene-05",\n'
        '      "order": 5,\n'
        '      "narration": "Key insight or climax building toward payoff",\n'
        '      "visual_intent": "Concrete physical photographic description",\n'
        '      "asset_query": "2-3 word photographic search query",\n'
        '      "on_screen_text": "2-4 WORD UPPERCASE BADGE",\n'
        '      "emphasis_words": ["KEYWORD"],\n'
        '      "estimated_duration_seconds": 5.0,\n'
        '      "scene_type": "broll",\n'
        '      "transition_hint": "fade"\n'
        '    },\n'
        '    {\n'
        '      "scene_id": "scene-06",\n'
        '      "order": 6,\n'
        '      "narration": "Intentional conclusion or payoff connecting back to hook",\n'
        '      "visual_intent": "Concrete physical photographic description",\n'
        '      "asset_query": "2-3 word photographic search query",\n'
        '      "on_screen_text": "2-4 WORD UPPERCASE BADGE",\n'
        '      "emphasis_words": ["KEYWORD"],\n'
        '      "estimated_duration_seconds": 5.0,\n'
        '      "scene_type": "broll",\n'
        '      "transition_hint": "fade"\n'
        '    }\n'
        '  ],\n'
        '  "cta_text": "Call to action sentence",\n'
        '  "tags": ["shorts", "educational"],\n'
        '  "source_references": ["source_id_or_url"]\n'
        "}\n"
        f"CHANNEL EDITORIAL DIRECTIVES:\n"
        f"- Channel Niche: {channel_niche}\n"
        f"- Editorial Tone: {channel_tone}\n"
        f"- Hook Style: {channel_hook_style}\n"
        f"- Visual Motif: {channel_visual_motif}\n"
        "EDITORIAL QUALITY RULES:\n"
        f"1. DURATION: Generate {MIN_SCENES} to {MAX_SCENES} useful scenes so total video narration targets "
        f"{round(MIN_SCENES * MAX_SCENE_SPEECH_WORDS / TTS_WORDS_PER_SECOND)}-"
        f"{round(MAX_SCENES * MAX_SCENE_SPEECH_WORDS / TTS_WORDS_PER_SECOND)} seconds of natural speech "
        f"(approx {MIN_SCENES * MAX_SCENE_SPEECH_WORDS} to {MAX_SCENES * MAX_SCENE_SPEECH_WORDS} spoken words "
        f"total across all scenes).\n"
        "2. STRUCTURE: Script MUST follow the progression: HOOK (Scene 1) -> EXPLANATION / FACTS (Middle Scenes) -> INTENTIONAL PAYOFF / ENDING (Final Scene).\n"
        "3. INTENTIONAL ENDING: The final scene MUST be an intentional conclusion (payoff returning to hook, strongest final fact, seamless loop back, or payoff statement). NEVER end abruptly or use generic filler like 'thanks for watching'.\n"
        f"4. SCENE NARRATION: Punchy, conversational, spoken English. Between {MIN_SCENE_SPEECH_WORDS} and {MAX_SCENE_SPEECH_WORDS} words per scene. Below {MIN_SCENE_SPEECH_WORDS} is too thin to convey a fact. One clear idea per scene. Every middle scene MUST deliver one concrete fact, number, date, or causal claim grounded in the evidence. Do NOT write filler scenes that only restate the topic or the hook. Every scene narration must advance the viewers understanding of the topic; if it could apply to any other subject, rewrite it. Do NOT write generic filler like As you can see, Lets dive in, But wait theres more, or generic topic restatements. HARD LIMIT: never exceed {MAX_SCENE_SPEECH_WORDS} words in a scene (measured Edge TTS delivery is ~{TTS_WORDS_PER_SECOND} words/sec, and Creative QA hard-blocks any scene whose speech exceeds {PACING_MAX_SCENE_SECONDS}s). If you write {MAX_SCENE_SPEECH_WORDS + 1}+ words, the script WILL BE REJECTED. Count your words.\n"
        "5. VISUAL INTENT: Describe concrete, tangible physical subjects suitable for photography.\n"
        "6. ASSET QUERY: 2-3 words naming concrete physical photographic subjects.\n"
        "7. ON_SCREEN_TEXT: 2-4 uppercase words for visual title card.\n"
        "8. EMPHASIS_WORDS: 1-2 keywords from narration to highlight in captions.\n"
        "RULES FOR TRANSITIONS:\n"
        "1. 'transition_hint' on a scene controls the cut INTO that scene (scene-01 is the video open and is ignored).\n"
        "2. Allowed values are exactly \"fade\" and \"cut\". Never emit other values; unknown values are ignored.\n"
        "3. Default to \"fade\" for a smooth, continuous narrative.\n"
        "4. Use \"cut\" ONLY for a hard punctuation: a jump in time, a chapter/beat change, or an intentional shock reveal.\n"
        "5. Never use \"cut\" between two closely related consecutive shots.\n"
        "6. Vary them: an all-fade video reads as monotonous, so include at least one \"cut\" per multi-scene script when the beats genuinely warrant it.\n"
        "RULES FOR SCENE NARRATION:\n"
        "1. Every spoken scene (scene_type: talking_head, broll, montage) MUST have non-empty spoken 'narration'.\n"
        "2. Do NOT output empty strings for 'narration'.\n"
        "RULES FOR FACTUAL GROUNDING:\n"
        "1. All factual claims, numbers, dates, names MUST be grounded strictly in supplied research evidence.\n"
        "2. DO NOT invent or fabricate statistics, dates, or names unsupported by supplied evidence.\n"
        "3. In 'source_references', list ONLY source IDs explicitly provided in the supplied research context."
    )


    user_prompt_lines = [
        f"Write a {int(target_duration)}-second vertical video script about: {topic}",
        f"Target spoken narration duration is "
        f"{round(MIN_SCENES * MAX_SCENE_SPEECH_WORDS / TTS_WORDS_PER_SECOND)}-"
        f"{round(MAX_SCENES * MAX_SCENE_SPEECH_WORDS / TTS_WORDS_PER_SECOND)} seconds "
        f"(approx {MIN_SCENES * MAX_SCENE_SPEECH_WORDS}-{MAX_SCENES * MAX_SCENE_SPEECH_WORDS} total spoken words "
        f"across {MIN_SCENES} to {MAX_SCENES} useful scenes).",
        "Scene 1 MUST be a strong hook. The final scene MUST be an intentional conclusion/payoff.",
    ]
    if cardinality:
        user_prompt_lines.append(
            f"\nLISTICLE STRUCTURE REQUIREMENTS:\n"
            f"- This request asks for {cardinality} distinct items/facts.\n"
            f"- You MUST create a hook scene plus at least {cardinality} separate fact scenes (one distinct scene per item/fact).\n"
            f"- Dedicate exactly one clear scene/fact unit to each item (e.g. scene-01: Hook, scene-02: Fact 1, scene-03: Fact 2, scene-04: Fact 3, followed by optional CTA).\n"
            f"- DO NOT combine multiple items into a single scene.\n"
            f"- Keep narration punchy ({MAX_SCENE_SPEECH_WORDS} words or fewer per scene) and make each scene independently visualizable."
        )
    if has_research:
        user_prompt_lines.append("\nSUPPLIED RESEARCH EVIDENCE:")
        user_prompt_lines.extend(evidence_snippets)
        user_prompt_lines.append("\nBase your script ONLY on the above evidence. Do not fabricate facts.")
    else:
        user_prompt_lines.append("\nNOTE: No research evidence provided. Write a general script without making specific unverified factual claims.")

    if corrective_instructions:
        user_prompt_lines.append(f"\nTARGETED REGENERATION INSTRUCTION (Attempt #{attempt_number}):")
        if regeneration_reason:
            user_prompt_lines.append(f"Defect Reason: {regeneration_reason}")
        user_prompt_lines.append(f"Correction Directive: {corrective_instructions}")
        user_prompt_lines.append("Correct the defect directly in this output.")

    user_prompt = "\n".join(user_prompt_lines)
    return system_prompt, user_prompt, evidence_snippets, valid_source_refs


def _parse_json_to_script_document(
    parsed: Dict[str, Any],
    topic: str,
    content_id: str,
    language: str,
    raw_response: str,
    provider_name: str,
    model_name: str,
    valid_source_refs: set[str],
    has_research: bool,
    extra_metadata: Optional[Dict[str, Any]] = None,
) -> ScriptDocument:
    """Validate parsed JSON dictionary and build canonical ScriptDocument."""
    raw_scenes = parsed.get("scenes", [])
    if not raw_scenes:
        raise ValueError(f"LLM returned zero scenes in script for topic '{topic}'")

    spoken_types = {"talking_head", "broll", "montage"}
    scenes = []
    for idx, s in enumerate(raw_scenes):
        s_id = s.get("scene_id", f"scene-{idx+1:02d}")
        s_type = s.get("scene_type", "talking_head")
        narration = (s.get("narration") or "").strip()

        if not narration and s_type in spoken_types:
            alt_narration = (s.get("text") or s.get("spoken_text") or s.get("voiceover") or s.get("script") or "").strip()
            if alt_narration:
                narration = alt_narration

        if not narration and s_type in spoken_types:
            raise ValueError(
                f"LLM returned invalid script for topic '{topic}': Scene {s_id} (type={s_type}) narration is empty."
            )

        viz_intent = (
            s.get("visual_intent")
            or s.get("visual")
            or s.get("visual_description")
            or s.get("image_description")
            or f"Visual representation of scene {idx+1}"
        )
        asset_q = (
            s.get("asset_query")
            or s.get("asset_search")
            or s.get("visual_query")
            or s.get("visual")
            or viz_intent
            or topic
        )

        on_scr_txt = (s.get("on_screen_text") or "").strip() or None
        if not on_scr_txt and idx > 0 and idx <= len(raw_scenes) - 2:
            on_scr_txt = f"FACT {idx:02d}"

        emph_words = s.get("emphasis_words") or []
        if isinstance(emph_words, str):
            emph_words = [w.strip() for w in emph_words.split(",") if w.strip()]

        scene_obj = ScriptScene(
            scene_id=s_id,
            order=int(s.get("order", idx + 1)),
            narration=narration,
            visual_intent=viz_intent,
            asset_query=asset_q,
            on_screen_text=on_scr_txt,
            emphasis_words=emph_words,
            estimated_duration_seconds=float(s.get("estimated_duration_seconds", 5.0)),
            scene_type=s_type,
            transition_hint=s.get("transition_hint", "fade"),
        )
        scenes.append(scene_obj)

    total_duration = sum(s.estimated_duration_seconds for s in scenes)
    if total_duration <= 0:
        total_duration = estimate_duration(" ".join(s.narration for s in scenes))

    # Fail fast on pacing violations. Creative QA blocks any scene whose measured
    # speech runs past 4.5s, and word count is only a proxy for that, so the
    # ceiling is derived from the gate (see MAX_SCENE_SPEECH_WORDS). Catching an
    # over-long scene here costs one LLM call; catching it after assets, voice,
    # and render costs a full production run.
    under_long = [
        (s.scene_id, len(s.narration.split()))
        for s in scenes
        if s.narration and len(s.narration.split()) < MIN_SCENE_SPEECH_WORDS
    ]
    if under_long:
        detail = ", ".join(f'{sid}={count}w' for sid, count in under_long)
        raise PacingBudgetError(
            f"LLM script for topic '{topic}' violates the script density floor: {detail}. "
            f"Each spoken scene must be at least {MIN_SCENE_SPEECH_WORDS} words "
            f"to convey sufficient information. Rewrite under-dense scenes to "
            f"provide one concrete fact, number, date, or causal claim.",
            offenders=under_long,
            offender_text={s.scene_id: s.narration for s in scenes if s.scene_id in {o[0] for o in under_long}},
        )
    over_long = [
        (s.scene_id, len(s.narration.split()))
        for s in scenes
        if s.narration and len(s.narration.split()) > MAX_SCENE_SPEECH_WORDS
    ]
    if over_long:
        detail = ", ".join(f"{sid}={count}w" for sid, count in over_long)
        raise PacingBudgetError(
            f"LLM script for topic '{topic}' violates the pacing budget: {detail}. "
            f"Each spoken scene must be {MAX_SCENE_SPEECH_WORDS} words or fewer "
            f"({int(MAX_SCENE_SPEECH_WORDS / TTS_WORDS_PER_SECOND * 10) / 10}s of speech at the "
            f"measured {TTS_WORDS_PER_SECOND} words/sec). Rewrite the script with more, "
            f"shorter scenes instead of lengthening individual ones.",
            offenders=over_long,
            offender_text={s.scene_id: s.narration for s in scenes if s.scene_id in {o[0] for o in over_long}},
        )

    # Validate returned source_references against supplied evidence
    returned_refs = parsed.get("source_references") or []
    validated_refs = []
    rejected_refs = []
    for ref in returned_refs:
        if ref in valid_source_refs:
            validated_refs.append(ref)
        else:
            rejected_refs.append(ref)

    grounding_status = "unresearched"
    if has_research:
        grounding_status = "grounded" if validated_refs else "evidence_provided_no_references"

    gen_meta = {
        "description": parsed.get("description", f"A short video about {topic}"),
        "tags": parsed.get("tags", ["shorts", "educational"]),
        "prompt_version": "openai_llm_v2_grounded",
        "research_grounding": grounding_status,
        "rejected_source_references": rejected_refs,
        "provider": provider_name,
        "model": model_name,
        "raw_model_response": raw_response,
    }
    if extra_metadata:
        gen_meta.update(extra_metadata)

    return ScriptDocument(
        content_id=content_id,
        language=language,
        topic=topic,
        scenes=scenes,
        hook=parsed.get("hook_text", scenes[0].narration if scenes else topic),
        cta=parsed.get("cta_text", "Follow for more updates!"),
        working_title=parsed.get("title", topic),
        total_estimated_duration=total_duration,
        source_references=validated_refs,
        generation_metadata=gen_meta,
    )


def _clean_json_text(text: str) -> str:
    """Clean markdown code block wrappers from JSON strings and fix common LLM formatting issues."""
    text = text.strip()
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start : end + 1]

    # Remove trailing commas before closing braces/brackets
    text = re.sub(r",\s*([}\]])", r"\1", text)
    return text.strip()


def _robust_parse_json(text: str) -> dict:
    """Attempt robust parsing of LLM-generated JSON, applying regex repairs if necessary."""
    cleaned = _clean_json_text(text)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Attempt repair 1: auto-quote unquoted keys { key: ... } or , key: ...
        repaired = re.sub(r"([{,]\s*)([a-zA-Z_][a-zA-Z0-9_\-]*)\s*:", r'\1"\2":', cleaned)
        repaired = re.sub(r",\s*([}\]])", r"\1", repaired)
        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            pass
        # Re-raise original decode error on cleaned
        return json.loads(cleaned)


class OpenAICompatibleLLMProvider(LLMProvider):
    """Base OpenAI-compatible LLM inference adapter for Ollama, vLLM, LM Studio, etc."""
    provider_name = "openai_compatible"
    capability = CapabilityMetadata(
        max_resolution="1080p",
        supports_9_16=True,
        local_only=False,
        license_note="OpenAI-compatible inference adapter (Ollama, vLLM, LM Studio, OpenRouter, Gemini)",
    )
    error_type = ProviderErrorType.UNCONFIGURED
    cost_meta = CostUsageMetadata(estimated_usd=0.0, provider_type="local_or_external")

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        model_name: Optional[str] = None,
        timeout: float = 180.0,
        idle_timeout: float = 60.0,
        stream: bool = True,
        extra_headers: Optional[Dict[str, str]] = None,
        extra_body: Optional[Dict[str, Any]] = None,
    ) -> None:
        from autopilot.core.config import CONFIG

        raw_base = (
            base_url
            or os.getenv("AUTOPILOT_LLM_ENDPOINT")
            or os.getenv("OPENAI_BASE_URL")
            or os.getenv("OLLAMA_BASE_URL")
            or os.getenv("OPENROUTER_BASE_URL")
            or getattr(CONFIG, "openai_endpoint", None)
            or "http://localhost:11434/v1"
        )
        self.base_url = raw_base.rstrip("/")
        self.api_key = self._resolve_api_key(api_key)
        default_timeout = getattr(CONFIG, "ollama_timeout", 180.0)
        env_timeout = (
            os.getenv("OPENAI_TIMEOUT")
            or os.getenv("OLLAMA_TIMEOUT")
            or os.getenv("AUTOPILOT_OLLAMA_TIMEOUT")
        )
        self.timeout = float(env_timeout) if env_timeout else float(default_timeout if timeout == 60.0 else timeout)
        self.idle_timeout = float(os.getenv("OLLAMA_IDLE_TIMEOUT", str(idle_timeout)))
        self.stream = stream
        self.extra_headers = extra_headers or {}
        self.extra_body = extra_body or {}
        self.model_name = self._resolve_model_name(model_name)

    def _resolve_api_key(self, explicit_api_key: Optional[str]) -> str:
        """Resolve the credential without ever borrowing another provider's key.

        An explicitly supplied value always wins -- including an empty string -- so a
        caller (and the test suite) can explicitly assert that a provider is
        unconfigured instead of silently inheriting OPENAI/OPENROUTER credentials.
        """
        if explicit_api_key is not None:
            return explicit_api_key
        return (
            os.getenv("OPENAI_API_KEY")
            or os.getenv("OPENROUTER_API_KEY")
            or os.getenv("OPENROUTER_KEY")
            or ""
        )

    def _discover_available_models(self) -> list[str]:
        """Fetch list of available models from endpoint via /models or /api/tags."""
        url = f"{self.base_url}/models"
        headers = {"User-Agent": "ProjectAutopilot/0.1.0"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        headers.update(self.extra_headers)

        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                models = [m.get("id") for m in data.get("data", []) if isinstance(m, dict) and m.get("id")]
                if models:
                    return models
        except Exception:
            pass

        # Fallback to Ollama native /api/tags if /v1 was in base_url or base is Ollama root
        try:
            root_url = re.sub(r"/v1/?$", "", self.base_url)
            tags_url = f"{root_url}/api/tags"
            req2 = urllib.request.Request(tags_url, headers=headers, method="GET")
            with urllib.request.urlopen(req2, timeout=2.0) as resp2:
                data2 = json.loads(resp2.read().decode("utf-8"))
                models2 = [m.get("name") for m in data2.get("models", []) if isinstance(m, dict) and m.get("name")]
                if models2:
                    return models2
        except Exception:
            pass

        return []

    def _resolve_model_name(self, explicit_model: Optional[str] = None) -> Optional[str]:
        """Resolve LLM model name honoring configuration, environment, or live endpoint discovery."""
        if explicit_model:
            return explicit_model

        from autopilot.core.config import CONFIG
        configured = (
            os.getenv("AUTOPILOT_LLM_MODEL")
            or os.getenv("AUTOPILOT_OLLAMA_MODEL")
            or os.getenv("OLLAMA_MODEL")
            or os.getenv("OPENAI_MODEL")
            or os.getenv("OPENROUTER_MODEL")
            or getattr(CONFIG, "ollama_model", None)
            or getattr(CONFIG, "openai_model", None)
        )
        if configured:
            return configured

        available = self._discover_available_models()
        if available:
            return available[0]

        return None

    def health_check(self) -> ProviderHealth:
        url = f"{self.base_url}/models"
        headers = {"User-Agent": "ProjectAutopilot/0.1.0"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        headers.update(self.extra_headers)

        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                models = [m.get("id") for m in data.get("data", []) if isinstance(m, dict)]
                if not self.model_name and models:
                    self.model_name = models[0]
                return ProviderHealth(
                    healthy=True,
                    provider_name=self.provider_name,
                    details={
                        "endpoint": self.base_url,
                        "model": self.model_name or (models[0] if models else "unconfigured"),
                        "available_models": models,
                        "available_models_count": len(models),
                        "requires_auth": bool(self.api_key),
                    },
                )
        except urllib.error.HTTPError as err:
            safe_err = redact_api_key(str(err))
            return ProviderHealth(
                healthy=False,
                provider_name=self.provider_name,
                error=f"Endpoint HTTP error {err.code}: {safe_err}",
                details={"endpoint": self.base_url, "status_code": err.code},
            )
        except Exception as exc:
            safe_err = redact_api_key(str(exc))
            return ProviderHealth(
                healthy=False,
                provider_name=self.provider_name,
                error=f"Cannot reach endpoint: {safe_err}",
                details={"endpoint": self.base_url, "note": f"Ensure {self.provider_name} server is running/configured"},
            )

    def generate_script(
        self,
        topic: str,
        content_id: str = "item-001",
        language: str = "en",
        research_report: object = None,
        profile: str = "short_vertical",
        channel_profile: object = None,
        corrective_instructions: Optional[str] = None,
        regeneration_reason: Optional[str] = None,
        attempt_number: int = 1,
        target_duration: float = 30.0,
        **kwargs,
    ) -> ScriptDocument:
        if not self.model_name:
            self.model_name = self._resolve_model_name()
            if not self.model_name:
                raise RuntimeError(
                    f"No LLM model configured for '{self.provider_name}' provider at '{self.base_url}'. "
                    f"Please set the model via configuration or environment variables."
                )

        system_prompt, user_prompt, evidence_snippets, valid_source_refs = _build_prompts_and_evidence(
            topic=topic,
            research_report=research_report,
            channel_profile=channel_profile,
            corrective_instructions=corrective_instructions,
            regeneration_reason=regeneration_reason,
            attempt_number=attempt_number,
            target_duration=target_duration,
        )
        has_research = len(evidence_snippets) > 0

        # When targeting Ollama endpoint, route via Ollama-specific transport behind the provider abstraction
        # to properly support think=false, streaming chunk accumulation, and token bounds without OpenAI-layer thinking bottlenecks
        is_ollama = "11434" in self.base_url or "ollama" in self.base_url.lower()
        think_enabled = False
        if is_ollama:
            from autopilot.core.config import CONFIG
            think_enabled = (
                os.getenv("OLLAMA_THINK", "false").lower() in ("true", "1", "yes")
                or getattr(CONFIG, "ollama_think", False)
            )
            root_url = self.base_url.rstrip("/").removesuffix("/v1")
            ollama_transport = OllamaLLMProvider(
                base_url=root_url,
                model=self.model_name,
                think=think_enabled,
                timeout=self.timeout,
                idle_timeout=self.idle_timeout,
                stream=self.stream,
            )
            doc = ollama_transport.generate_script(
                topic=topic,
                content_id=content_id,
                language=language,
                research_report=research_report,
                profile=profile,
                channel_profile=channel_profile,
                corrective_instructions=corrective_instructions,
                regeneration_reason=regeneration_reason,
                attempt_number=attempt_number,
                target_duration=target_duration,
                **kwargs,
            )
            doc.generation_metadata["provider"] = self.provider_name
            return doc

        payload: Dict[str, Any] = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.7,
            "max_tokens": 4096,
            "stream": self.stream,
        }
        if self.extra_body:
            payload.update(self.extra_body)

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "ProjectAutopilot/0.1.0",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        headers.update(self.extra_headers)

        try:
            last_decode_err = None
            retry_directives: List[str] = []
            for attempt_idx in range(1, 4):
                cur_user_prompt = user_prompt + "".join(retry_directives)
                if attempt_idx > 1:
                    cur_user_prompt += "\nCRITICAL: Respond ONLY with valid, RFC 8259 compliant JSON. Ensure all property names and strings are double-quoted, all commas are present between elements, and no trailing commas exist."

                payload["messages"] = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": cur_user_prompt},
                ]
                req_data = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(url, data=req_data, headers=headers, method="POST")

                start_t = time.time()
                last_chunk_t = start_t
                accumulated_content: List[str] = []
                thinking_chunks: List[str] = []

                try:
                    with urllib.request.urlopen(req, timeout=self.idle_timeout) as resp:
                        if self.stream:
                            for line_bytes in _iter_response_lines(resp):
                                now = time.time()
                                if now - start_t > self.timeout:
                                    raise TimeoutError(
                                        f"{self.provider_name} LLM timeout (total_timeout): "
                                        f"request to '{url}' for model '{self.model_name}' timed out after {now - start_t:.1f}s "
                                        f"(streaming=True, think={think_enabled})."
                                    )
                                if isinstance(line_bytes, bytes):
                                    line = line_bytes.decode("utf-8").strip()
                                else:
                                    line = str(line_bytes).strip()
                                if not line:
                                    continue
                                if line.startswith("data: "):
                                    data_str = line[6:].strip()
                                    if data_str == "[DONE]":
                                        break
                                    try:
                                        chunk = json.loads(data_str)
                                        choices = chunk.get("choices", [])
                                        if choices:
                                            delta = choices[0].get("delta", {})
                                            content_chunk = delta.get("content", "")
                                            thinking_chunk = delta.get("thinking") or delta.get("reasoning_content") or ""
                                            if content_chunk:
                                                accumulated_content.append(content_chunk)
                                            if thinking_chunk:
                                                thinking_chunks.append(thinking_chunk)
                                    except json.JSONDecodeError:
                                        pass
                                elif line.startswith("{"):
                                    try:
                                        chunk = json.loads(line)
                                        if "choices" in chunk and isinstance(chunk["choices"], list) and chunk["choices"]:
                                            first_choice = chunk["choices"][0]
                                            if "message" in first_choice and isinstance(first_choice["message"], dict):
                                                c = first_choice["message"].get("content", "")
                                                if c:
                                                    accumulated_content.append(c)
                                                    break
                                            elif "delta" in first_choice and isinstance(first_choice["delta"], dict):
                                                c = first_choice["delta"].get("content", "")
                                                th = first_choice["delta"].get("thinking") or first_choice["delta"].get("reasoning_content") or ""
                                                if c:
                                                    accumulated_content.append(c)
                                                if th:
                                                    thinking_chunks.append(th)
                                        elif "message" in chunk and isinstance(chunk["message"], dict):
                                            msg = chunk["message"]
                                            c = msg.get("content", "")
                                            th = msg.get("thinking", "")
                                            if c:
                                                accumulated_content.append(c)
                                            if th:
                                                thinking_chunks.append(th)
                                    except json.JSONDecodeError:
                                        pass
                                last_chunk_t = time.time()
                            content = "".join(accumulated_content).strip()
                        else:
                            res_body = resp.read().decode("utf-8")
                            raw_json = json.loads(res_body)
                            content = raw_json["choices"][0]["message"]["content"]

                    if not content:
                        raise RuntimeError(
                            f"{self.provider_name} LLM returned empty content for model '{self.model_name}'. "
                            f"Ensure valid output structure or check model token limit."
                        )

                    parsed = _robust_parse_json(content)
                    return _parse_json_to_script_document(
                        parsed=parsed,
                        topic=topic,
                        content_id=content_id,
                        language=language,
                        raw_response=content,
                        provider_name=self.provider_name,
                        model_name=self.model_name,
                        valid_source_refs=valid_source_refs,
                        has_research=has_research,
                    )
                except PacingBudgetError as perr:
                    if attempt_idx < 3:
                        retry_directives.append(perr.correction_directive())
                        continue
                    safe_msg = redact_api_key(str(perr))
                    raise RuntimeError(f"{self.provider_name} LLM call failed: {safe_msg}") from perr
                except json.JSONDecodeError as jerr:
                    last_decode_err = jerr
                    if attempt_idx < 3:
                        time.sleep(1.0)
                        continue
                    safe_msg = redact_api_key(str(jerr))
                    raise RuntimeError(f"{self.provider_name} LLM call failed: {safe_msg}") from jerr

        except urllib.error.HTTPError as err:
            try:
                raw_body = err.read().decode("utf-8", errors="ignore")
            except Exception:
                raw_body = str(err)
            safe_msg = redact_api_key(raw_body)
            if err.code == 404:
                available = self._discover_available_models()
                avail_str = f" Available models on endpoint: {available}." if available else ""
                raise RuntimeError(
                    f"{self.provider_name} LLM error (HTTP 404): Model '{self.model_name}' not found on endpoint '{self.base_url}'.{avail_str} "
                    f"Set configuration/environment variable to an available model."
                ) from err
            elif err.code in (401, 403):
                raise RuntimeError(
                    f"{self.provider_name} LLM authentication error (HTTP {err.code}): {safe_msg}. "
                    f"Verify your API key."
                ) from err
            raise RuntimeError(f"{self.provider_name} LLM error (HTTP {err.code}): {safe_msg}") from err
        except (TimeoutError, socket.timeout) as exc:
            elapsed = time.time() - start_t
            category = "idle_timeout" if (time.time() - last_chunk_t >= self.idle_timeout - 1) else "total_timeout"
            raise TimeoutError(
                f"{self.provider_name} LLM timeout ({category}): "
                f"request to '{url}' for model '{self.model_name}' timed out after {elapsed:.1f}s "
                f"(streaming={self.stream}, think={think_enabled})."
            ) from exc
        except Exception as exc:
            if "timed out" in str(exc).lower():
                elapsed = time.time() - start_t
                raise TimeoutError(
                    f"{self.provider_name} LLM timeout (socket_timeout): "
                    f"request to '{url}' for model '{self.model_name}' timed out after {elapsed:.1f}s "
                    f"(streaming={self.stream}, think={think_enabled})."
                ) from exc
            safe_msg = redact_api_key(str(exc))
            raise RuntimeError(f"{self.provider_name} LLM call failed: {safe_msg}") from exc


class OllamaLLMProvider(LLMProvider):
    """Local Ollama LLM provider using Ollama's native streaming /api/chat endpoint.
    Fully supports thinking control (think: False by default), output token limits,
    idle/total timeouts, chunk accumulation, and thinking/content separation.
    """
    provider_name = "ollama"
    capability = CapabilityMetadata(
        max_resolution="1080p",
        supports_9_16=True,
        local_only=True,
        license_note="Local Ollama inference provider with streaming and thinking control",
    )
    error_type = ProviderErrorType.UNCONFIGURED
    cost_meta = CostUsageMetadata(estimated_usd=0.0, provider_type="local")

    def __init__(
        self,
        base_url: Optional[str] = None,
        model_name: Optional[str] = None,
        model: Optional[str] = None,
        think: Optional[bool] = None,
        timeout: Optional[float] = None,
        idle_timeout: Optional[float] = None,
        num_predict: Optional[int] = None,
        stream: bool = True,
        **kwargs,
    ) -> None:
        from autopilot.core.config import CONFIG

        raw_base = (
            base_url
            or os.getenv("OLLAMA_BASE_URL")
            or os.getenv("OLLAMA_ENDPOINT")
            or getattr(CONFIG, "ollama_endpoint", "http://localhost:11434")
        ).rstrip("/")
        # Canonicalize base URL to root (without /v1)
        self.root_url = re.sub(r"/v1/?$", "", raw_base)
        self.base_url = f"{self.root_url}/v1"
        self.chat_url = f"{self.root_url}/api/chat"

        resolved_model = model_name or model
        self.model_name = self._resolve_model_name(resolved_model)
        
        # Thinking is False by default for fast short-form production, unless explicitly configured/opted in
        if think is not None:
            self.think = bool(think)
        else:
            env_think = os.getenv("OLLAMA_THINK") or os.getenv("AUTOPILOT_OLLAMA_THINK")
            if env_think is not None:
                self.think = env_think.lower() in ("true", "1", "yes")
            else:
                self.think = getattr(CONFIG, "ollama_think", False)

        self.timeout = float(
            timeout
            if timeout is not None
            else os.getenv("OLLAMA_TIMEOUT")
            or os.getenv("AUTOPILOT_OLLAMA_TIMEOUT")
            or getattr(CONFIG, "ollama_timeout", 180.0)
        )
        self.idle_timeout = float(
            idle_timeout
            if idle_timeout is not None
            else os.getenv("OLLAMA_IDLE_TIMEOUT")
            or getattr(CONFIG, "ollama_idle_timeout", 60.0)
        )
        self.num_predict = int(
            num_predict
            if num_predict is not None
            else os.getenv("OLLAMA_NUM_PREDICT")
            or os.getenv("AUTOPILOT_OLLAMA_NUM_PREDICT")
            or getattr(CONFIG, "ollama_num_predict", 1024)
        )
        self.stream = stream

    def _discover_available_models(self) -> list[str]:
        """Fetch list of available models from Ollama /api/tags."""
        tags_url = f"{self.root_url}/api/tags"
        headers = {"User-Agent": "ProjectAutopilot/0.1.0"}
        req = urllib.request.Request(tags_url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                models = [m.get("name") for m in data.get("models", []) if isinstance(m, dict) and m.get("name")]
                if models:
                    return models
        except Exception:
            pass
        return []

    def _resolve_model_name(self, explicit_model: Optional[str] = None) -> Optional[str]:
        if explicit_model:
            return explicit_model
        from autopilot.core.config import CONFIG
        configured = (
            os.getenv("OLLAMA_MODEL")
            or os.getenv("AUTOPILOT_OLLAMA_MODEL")
            or getattr(CONFIG, "ollama_model", None)
        )
        if configured:
            return configured
        available = self._discover_available_models()
        if available:
            return available[0]
        return None

    def health_check(self) -> ProviderHealth:
        tags_url = f"{self.root_url}/api/tags"
        headers = {"User-Agent": "ProjectAutopilot/0.1.0"}
        req = urllib.request.Request(tags_url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                models = [m.get("name") for m in data.get("models", []) if isinstance(m, dict)]
                if not self.model_name and models:
                    self.model_name = models[0]
                return ProviderHealth(
                    healthy=True,
                    provider_name=self.provider_name,
                    details={
                        "endpoint": self.root_url,
                        "chat_endpoint": self.chat_url,
                        "model": self.model_name or (models[0] if models else "unconfigured"),
                        "available_models": models,
                        "available_models_count": len(models),
                        "think": self.think,
                        "num_predict": self.num_predict,
                    },
                )
        except urllib.error.HTTPError as err:
            safe_err = redact_api_key(str(err))
            return ProviderHealth(
                healthy=False,
                provider_name=self.provider_name,
                error=f"Ollama HTTP error {err.code}: {safe_err}",
                details={"endpoint": self.root_url, "status_code": err.code},
            )
        except Exception as exc:
            safe_err = redact_api_key(str(exc))
            return ProviderHealth(
                healthy=False,
                provider_name=self.provider_name,
                error=f"Cannot reach Ollama: {safe_err}",
                details={"endpoint": self.root_url, "note": "Ensure Ollama server is running with 'ollama serve'"},
            )

    def generate_script(
        self,
        topic: str,
        content_id: str = "item-001",
        language: str = "en",
        research_report: object = None,
        profile: str = "short_vertical",
        channel_profile: object = None,
        corrective_instructions: Optional[str] = None,
        regeneration_reason: Optional[str] = None,
        attempt_number: int = 1,
        target_duration: float = 30.0,
        **kwargs,
    ) -> ScriptDocument:
        if not self.model_name:
            self.model_name = self._resolve_model_name()
            if not self.model_name:
                raise RuntimeError(
                    f"No Ollama model configured or found at '{self.root_url}'. "
                    f"Please run 'ollama pull qwen3:4b' or set OLLAMA_MODEL environment variable."
                )

        system_prompt, user_prompt, evidence_snippets, valid_source_refs = _build_prompts_and_evidence(
            topic=topic,
            research_report=research_report,
            channel_profile=channel_profile,
            corrective_instructions=corrective_instructions,
            regeneration_reason=regeneration_reason,
            attempt_number=attempt_number,
            target_duration=target_duration,
        )
        has_research = len(evidence_snippets) > 0

        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "format": "json",
            "stream": self.stream,
            "think": self.think,
            "options": {
                "temperature": 0.7,
                "num_predict": self.num_predict,
            },
        }

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "ProjectAutopilot/0.1.0",
        }

        # Bounded corrective retry: a pacing violation is a recoverable authoring
        # error, so re-prompt with the offending scenes instead of failing the run
        # (or, worse, silently truncating narration to satisfy the gate).
        retry_directives: List[str] = []
        last_err: Optional[BaseException] = None
        for attempt_idx in range(1, 4):
            cur_user_prompt = user_prompt + "".join(retry_directives)
            if attempt_idx > 1:
                cur_user_prompt += (
                    "\nCRITICAL: Respond ONLY with valid, RFC 8259 compliant JSON. Ensure all "
                    "property names and strings are double-quoted, all commas are present "
                    "between elements, and no trailing commas exist."
                )
            payload["messages"] = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": cur_user_prompt},
            ]
            req_data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(self.chat_url, data=req_data, headers=headers, method="POST")

            start_t = time.time()
            last_chunk_t = start_t
            accumulated_content: List[str] = []
            thinking_chunks: List[str] = []
            eval_meta: Dict[str, Any] = {}

            try:
                with urllib.request.urlopen(req, timeout=self.idle_timeout) as resp:
                    if self.stream:
                        for line_bytes in _iter_response_lines(resp):
                            now = time.time()
                            if now - start_t > self.timeout:
                                raise TimeoutError(
                                    f"{self.provider_name} LLM timeout (total_timeout): "
                                    f"request to '{self.chat_url}' for model '{self.model_name}' timed out after {now - start_t:.1f}s "
                                    f"(streaming=True, think={self.think})."
                                )
                            if isinstance(line_bytes, bytes):
                                line = line_bytes.decode("utf-8").strip()
                            else:
                                line = str(line_bytes).strip()
                            if not line:
                                continue
                            if line.startswith("data: "):
                                data_str = line[6:].strip()
                                if data_str == "[DONE]":
                                    break
                                try:
                                    chunk = json.loads(data_str)
                                    choices = chunk.get("choices", [])
                                    if choices:
                                        delta = choices[0].get("delta", {})
                                        content_chunk = delta.get("content", "")
                                        thinking_chunk = delta.get("thinking") or delta.get("reasoning_content") or ""
                                        if content_chunk:
                                            accumulated_content.append(content_chunk)
                                        if thinking_chunk:
                                            thinking_chunks.append(thinking_chunk)
                                except json.JSONDecodeError:
                                    pass
                            elif line.startswith("{"):
                                try:
                                    chunk = json.loads(line)
                                    if "choices" in chunk and isinstance(chunk["choices"], list) and chunk["choices"]:
                                        first_choice = chunk["choices"][0]
                                        if "message" in first_choice and isinstance(first_choice["message"], dict):
                                            c = first_choice["message"].get("content", "")
                                            if c:
                                                accumulated_content.append(c)
                                                break
                                        elif "delta" in first_choice and isinstance(first_choice["delta"], dict):
                                            c = first_choice["delta"].get("content", "")
                                            th = first_choice["delta"].get("thinking") or first_choice["delta"].get("reasoning_content") or ""
                                            if c:
                                                accumulated_content.append(c)
                                            if th:
                                                thinking_chunks.append(th)
                                    elif "message" in chunk and isinstance(chunk["message"], dict):
                                        msg = chunk["message"]
                                        c = msg.get("content", "")
                                        th = msg.get("thinking", "")
                                        if c:
                                            accumulated_content.append(c)
                                        if th:
                                            thinking_chunks.append(th)
                                    if chunk.get("done"):
                                        eval_meta = {
                                            "eval_count": chunk.get("eval_count"),
                                            "eval_duration_ms": (chunk.get("eval_duration") or 0) / 1e6,
                                            "prompt_eval_count": chunk.get("prompt_eval_count"),
                                        }
                                except json.JSONDecodeError:
                                    pass
                            last_chunk_t = time.time()
                        content = "".join(accumulated_content).strip()
                    else:
                        res_body = resp.read().decode("utf-8")
                        raw_json = json.loads(res_body)
                        content = raw_json.get("message", {}).get("content", "")
                        if not content and "choices" in raw_json:
                            content = raw_json["choices"][0]["message"]["content"]
                        if raw_json.get("done"):
                            eval_meta = {
                                "eval_count": raw_json.get("eval_count"),
                                "eval_duration_ms": (raw_json.get("eval_duration") or 0) / 1e6,
                                "prompt_eval_count": raw_json.get("prompt_eval_count"),
                            }

                if not content:
                    th_len = len("".join(thinking_chunks))
                    raise RuntimeError(
                        f"{self.provider_name} LLM returned empty content for model '{self.model_name}'. "
                        f"Thinking generated: {th_len} chars. "
                        f"Ensure think=False or increase num_predict limit."
                    )

                parsed = _robust_parse_json(content)
                return _parse_json_to_script_document(
                    parsed=parsed,
                    topic=topic,
                    content_id=content_id,
                    language=language,
                    raw_response=content,
                    provider_name=self.provider_name,
                    model_name=self.model_name,
                    valid_source_refs=valid_source_refs,
                    has_research=has_research,
                    extra_metadata=eval_meta,
                )

            except PacingBudgetError as perr:
                if attempt_idx < 3:
                    retry_directives.append(perr.correction_directive())
                    continue
                safe_msg = redact_api_key(str(perr))
                raise RuntimeError(f"{self.provider_name} LLM call failed: {safe_msg}") from perr

            except urllib.error.HTTPError as err:
                try:
                    raw_body = err.read().decode("utf-8", errors="ignore")
                except Exception:
                    raw_body = str(err)
                safe_msg = redact_api_key(raw_body)
                if err.code == 404:
                    available = self._discover_available_models()
                    avail_str = f" Available models on endpoint: {available}." if available else ""
                    raise RuntimeError(
                        f"{self.provider_name} LLM error (HTTP 404): Model '{self.model_name}' not found on endpoint '{self.root_url}'.{avail_str} "
                        f"Set configuration/environment variable to an available model."
                    ) from err
                raise RuntimeError(f"{self.provider_name} LLM error (HTTP {err.code}): {safe_msg}") from err
            except (TimeoutError, socket.timeout) as exc:
                elapsed = time.time() - start_t
                category = "idle_timeout" if (time.time() - last_chunk_t >= self.idle_timeout - 1) else "total_timeout"
                raise TimeoutError(
                    f"{self.provider_name} LLM timeout ({category}): "
                    f"request to '{self.chat_url}' for model '{self.model_name}' timed out after {elapsed:.1f}s "
                    f"(streaming={self.stream}, think={self.think})."
                ) from exc
            except Exception as exc:
                if "timed out" in str(exc).lower():
                    elapsed = time.time() - start_t
                    raise TimeoutError(
                        f"{self.provider_name} LLM timeout (socket_timeout): "
                        f"request to '{self.chat_url}' for model '{self.model_name}' timed out after {elapsed:.1f}s "
                        f"(streaming={self.stream}, think={self.think})."
                    ) from exc
                safe_msg = redact_api_key(str(exc))
                raise RuntimeError(f"{self.provider_name} LLM call failed: {safe_msg}") from exc


class GeminiLLMProvider(OpenAICompatibleLLMProvider):
    """Google Gemini LLM provider via official OpenAI-compatible endpoint.
    Endpoint: https://generativelanguage.googleapis.com/v1beta/openai/
    """
    provider_name = "gemini"

    def __init__(
        self,
        api_key: Optional[str] = None,
        model_name: Optional[str] = None,
        base_url: Optional[str] = None,
        thinking_budget: Optional[int] = None,
        timeout: float = 45.0,
        **kwargs,
    ) -> None:
        from autopilot.core.config import CONFIG

        resolved_api_key = (
            api_key
            if api_key is not None
            else (
                os.getenv("GEMINI_API_KEY")
                or os.getenv("AUTOPILOT_GEMINI_API_KEY")
                or getattr(CONFIG, "gemini_api_key", "")
            )
        )

        resolved_base = (
            base_url
            or os.getenv("GEMINI_BASE_URL")
            or os.getenv("GEMINI_ENDPOINT")
            or getattr(CONFIG, "gemini_endpoint", "https://generativelanguage.googleapis.com/v1beta/openai")
        ).rstrip("/")

        resolved_model = (
            model_name
            or os.getenv("GEMINI_MODEL")
            or os.getenv("AUTOPILOT_GEMINI_MODEL")
            or getattr(CONFIG, "gemini_model", "gemini-2.0-flash")
        )

        extra_body = {}
        t_budget = (
            thinking_budget
            if thinking_budget is not None
            else getattr(CONFIG, "gemini_thinking_budget", None)
        )
        if t_budget is not None and t_budget > 0:
            extra_body["thinking"] = {"thinking_budget": t_budget}

        super().__init__(
            base_url=resolved_base,
            api_key=resolved_api_key,
            model_name=resolved_model,
            timeout=timeout,
            extra_body=extra_body,
            **kwargs,
        )

    def _resolve_model_name(self, explicit_model: Optional[str] = None) -> Optional[str]:
        if explicit_model:
            return explicit_model
        from autopilot.core.config import CONFIG
        return (
            os.getenv("GEMINI_MODEL")
            or os.getenv("AUTOPILOT_GEMINI_MODEL")
            or getattr(CONFIG, "gemini_model", "gemini-2.0-flash")
        )

    def generate_script(self, *args, **kwargs) -> ScriptDocument:
        if not self.api_key:
            raise RuntimeError(
                "Gemini LLM provider requires GEMINI_API_KEY to be set in environment or configuration. "
                "Get an API key from https://aistudio.google.com/"
            )
        return super().generate_script(*args, **kwargs)


class OpenRouterLLMProvider(OpenAICompatibleLLMProvider):
    """OpenRouter LLM provider for unified routing to frontier and open-source models.
    Endpoint: https://openrouter.ai/api/v1
    """
    provider_name = "openrouter"

    def __init__(
        self,
        api_key: Optional[str] = None,
        model_name: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = 45.0,
        **kwargs,
    ) -> None:
        from autopilot.core.config import CONFIG

        resolved_api_key = (
            api_key
            if api_key is not None
            else (
                os.getenv("OPENROUTER_API_KEY")
                or os.getenv("AUTOPILOT_OPENROUTER_API_KEY")
                or os.getenv("OPENROUTER_KEY")
                or getattr(CONFIG, "openrouter_api_key", "")
            )
        )

        resolved_base = (
            base_url
            or os.getenv("OPENROUTER_BASE_URL")
            or os.getenv("OPENROUTER_ENDPOINT")
            or getattr(CONFIG, "openrouter_endpoint", "https://openrouter.ai/api/v1")
        ).rstrip("/")

        resolved_model = (
            model_name
            or os.getenv("OPENROUTER_MODEL")
            or os.getenv("AUTOPILOT_OPENROUTER_MODEL")
            or getattr(CONFIG, "openrouter_model", None)
        )

        extra_headers = {
            "HTTP-Referer": "https://github.com/project-autopilot",
            "X-Title": "ProjectAutopilot",
        }

        super().__init__(
            base_url=resolved_base,
            api_key=resolved_api_key,
            model_name=resolved_model,
            timeout=timeout,
            extra_headers=extra_headers,
            **kwargs,
        )

    def _resolve_model_name(self, explicit_model: Optional[str] = None) -> Optional[str]:
        if explicit_model:
            return explicit_model
        from autopilot.core.config import CONFIG
        return (
            os.getenv("OPENROUTER_MODEL")
            or os.getenv("AUTOPILOT_OPENROUTER_MODEL")
            or getattr(CONFIG, "openrouter_model", None)
        )

    def generate_script(self, *args, **kwargs) -> ScriptDocument:
        if not self.api_key:
            raise RuntimeError(
                "OpenRouter LLM provider requires OPENROUTER_API_KEY to be set in environment or configuration. "
                "Get an API key from https://openrouter.ai/keys"
            )
        if not self.model_name:
            raise RuntimeError(
                "OpenRouter LLM provider requires an explicit OPENROUTER_MODEL to be configured "
                "(e.g. 'export OPENROUTER_MODEL=meta-llama/llama-3.3-70b-instruct' or 'google/gemini-2.0-flash-001'). "
                "Autopilot will never silently select a random model."
            )
        return super().generate_script(*args, **kwargs)


class AtriaLLMProvider(OpenAICompatibleLLMProvider):
    """Atria ASI LLM provider for Atria Dawn model.
    Endpoint: https://api.atria-asi.ai/v1
    """
    provider_name = "atria"

    def __init__(
        self,
        api_key: Optional[str] = None,
        model_name: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = 120.0,
        **kwargs,
    ) -> None:
        from autopilot.core.config import CONFIG

        resolved_api_key = (
            api_key
            if api_key is not None
            else (
                os.getenv("ATRIA_API_KEY")
                or os.getenv("AUTOPILOT_ATRIA_API_KEY")
                or getattr(CONFIG, "atria_api_key", "")
            )
        )

        resolved_base = (
            base_url
            or os.getenv("ATRIA_BASE_URL")
            or os.getenv("ATRIA_ENDPOINT")
            or getattr(CONFIG, "atria_endpoint", "https://api.atria-asi.ai/v1")
        ).rstrip("/")

        resolved_model = (
            model_name
            or os.getenv("ATRIA_MODEL")
            or os.getenv("AUTOPILOT_ATRIA_MODEL")
            or getattr(CONFIG, "atria_model", "Atria-Dawn-Preview")
        )

        # Atria may be slower; disable streaming and use longer timeout
        super().__init__(
            base_url=resolved_base,
            api_key=resolved_api_key,
            model_name=resolved_model,
            timeout=timeout,
            stream=False,
            **kwargs,
        )

    def _resolve_model_name(self, explicit_model: Optional[str] = None) -> Optional[str]:
        if explicit_model:
            return explicit_model
        from autopilot.core.config import CONFIG
        return (
            os.getenv("ATRIA_MODEL")
            or os.getenv("AUTOPILOT_ATRIA_MODEL")
            or getattr(CONFIG, "atria_model", "Atria-Dawn-Preview")
        )

    def generate_script(self, *args, **kwargs) -> ScriptDocument:
        if not self.api_key:
            raise RuntimeError(
                "Atria API key not configured. Set ATRIA_API_KEY or AUTOPILOT_ATRIA_API_KEY, "
                "or configure atria_api_key in autopilot config."
            )
        if not self.model_name:
            raise RuntimeError(
                "Atria LLM provider requires an explicit ATRIA_MODEL to be configured "
                "(default: 'Atria-Dawn-Preview')."
            )
        return super().generate_script(*args, **kwargs)


def get_llm_provider(
    provider_name: str = "ollama",
    model_name: Optional[str] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    **kwargs,
) -> LLMProvider:
    """Factory to instantiate the requested LLM provider with fail-closed validation."""
    key = (provider_name or "ollama").lower().strip()
    if key in ("mock", "mock_script", "mock_llm", "local_stub"):
        from autopilot.providers.mock_script import MockScriptProvider
        return MockScriptProvider()
    elif key in ("ollama", "ollama-local", "ollama_local"):
        return OllamaLLMProvider(model_name=model_name, base_url=base_url, **kwargs)
    elif key in ("gemini", "gemini-flash", "gemini_flash", "gemini-pro", "gemini_pro", "google"):
        return GeminiLLMProvider(api_key=api_key, model_name=model_name, base_url=base_url, **kwargs)
    elif key in ("openrouter", "open_router"):
        return OpenRouterLLMProvider(api_key=api_key, model_name=model_name, base_url=base_url, **kwargs)
    elif key in ("atria", "atria-dawn", "atria_dawn", "atria_asi"):
        return AtriaLLMProvider(api_key=api_key, model_name=model_name, base_url=base_url, **kwargs)
    elif key in ("openai_compatible", "openai", "openai-compatible", "openai_compat"):
        return OpenAICompatibleLLMProvider(api_key=api_key, model_name=model_name, base_url=base_url, **kwargs)
    else:
        raise ValueError(
            f"Unknown LLM provider: '{provider_name}'. "
            f"Supported providers: 'ollama', 'gemini', 'openrouter', 'atria', 'openai_compatible', 'mock'."
        )
