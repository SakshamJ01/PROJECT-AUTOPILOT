"""Mock / Template Script Provider — Phase 1.
Not intelligent; proves content contract works end-to-end with zero external services.
Produces deterministic, clearly-identified demo content.
"""
from __future__ import annotations

from autopilot.providers.contracts import LLMProvider, ProviderHealth, CapabilityMetadata, CostUsageMetadata, ProviderErrorType
from autopilot.core.contracts import ScriptDocument, ScriptScene, ContentItem
from autopilot.core.duration import estimate_duration


class MockScriptProvider(LLMProvider):
    provider_name = "mock_script"
    capability = CapabilityMetadata(
        max_resolution="1080p",
        supports_9_16=True,
        local_only=True,
        license_note="Deterministic test/demo provider; not a real LLM",
    )
    error_type = ProviderErrorType.UNCONFIGURED
    cost_meta = CostUsageMetadata(estimated_usd=0.0, provider_type="local")

    def health_check(self) -> ProviderHealth:
        return ProviderHealth(
            healthy=True,
            provider_name=self.provider_name,
            error="",
            details={"mode": "deterministic_mock", "note": "test/demo only"},
        )

    def generate_script(
        self,
        topic: str,
        content_id: str = "demo-001",
        language: str = "en",
        research_report: object = None,
        profile: str = "short_vertical",
        channel_profile: object = None,
        corrective_instructions: Optional[str] = None,
        regeneration_reason: Optional[str] = None,
        attempt_number: int = 1,
        **kwargs,
    ) -> ScriptDocument:
        # Extract channel profile directives if provided
        rules = {}
        if channel_profile:
            if hasattr(channel_profile, "to_editorial_rules"):
                rules = channel_profile.to_editorial_rules()
            elif isinstance(channel_profile, dict):
                rules = channel_profile

        niche = rules.get("niche", "general")
        tone = rules.get("tone", "clear, direct, evidence-oriented")
        motif = rules.get("visual_motif", "clean")

        # Niche-aware hook and narration styling
        if niche == "science":
            hook_text = f"Scientists made a discovery about {topic} that changes our understanding."
            scene1_narration = f"Recent laboratory experiments with {topic} uncovered surprising empirical patterns."
            scene1_intent = f"High-magnification scientific microscopy and laboratory instruments showing {topic}"
            scene1_query = f"laboratory science {topic}"
            working_title = f"The Science of {topic}"
        elif niche == "history":
            hook_text = f"Centuries ago, an extraordinary turning point occurred regarding {topic}."
            scene1_narration = f"Historical records from the archives reveal how {topic} transformed civilizations."
            scene1_intent = f"Archival historical parchment and museum artifacts documenting {topic}"
            scene1_query = f"archival history {topic}"
            working_title = f"The Forgotten History of {topic}"
        elif niche == "technology":
            hook_text = f"A major technological breakthrough in {topic} is accelerating rapidly."
            scene1_narration = f"Engineers and developers working on {topic} just unlocked unprecedented performance."
            scene1_intent = f"Silicon microchip wafer and futuristic server infrastructure powering {topic}"
            scene1_query = f"technology hardware {topic}"
            working_title = f"Next-Gen {topic} Explained"
        else:
            hook_text = f"What if everything you knew about {topic} was only half correct?"
            scene1_narration = f"Today let's talk about {topic}. It matters more than most people realize."
            scene1_intent = f"Talking head with subtle background gradient referencing {topic}"
            scene1_query = f"abstract visual {topic}"
            working_title = f"{topic} — Overview"

        # Apply targeted regeneration corrections if present
        if corrective_instructions:
            if "hook" in corrective_instructions.lower():
                hook_text = f"[CORRECTED HOOK] {hook_text} (Addressed: {corrective_instructions})"
            if "duration" in corrective_instructions.lower():
                working_title += " (Paced)"

        base_scenes = [
            ScriptScene(
                scene_id="scene-01",
                order=1,
                narration=scene1_narration,
                visual_intent=scene1_intent,
                asset_query=scene1_query,
                estimated_duration_seconds=7.0,
                scene_type="talking_head",
                transition_hint="fade_in",
            ),
            ScriptScene(
                scene_id="scene-02",
                order=2,
                narration="The data shows a clear pattern. We will explore the evidence together.",
                visual_intent=f"B-roll montage of {motif} imagery",
                asset_query=f"stock footage {topic}",
                estimated_duration_seconds=6.0,
                scene_type="broll",
                transition_hint="cut",
            ),
            ScriptScene(
                scene_id="scene-03",
                order=3,
                narration="Here is the practical takeaway. Apply this to your own context.",
                visual_intent="Text overlay on clean background",
                asset_query="text background minimal",
                estimated_duration_seconds=5.0,
                scene_type="text",
                transition_hint="slide_right",
            ),
            ScriptScene(
                scene_id="scene-04",
                order=4,
                narration=f"If you want to go deeper, start with {topic}. The next step is yours.",
                visual_intent="Direct address with call-to-action graphic",
                asset_query="cta graphic",
                estimated_duration_seconds=6.0,
                scene_type="talking_head",
                transition_hint="fade_out",
            ),
        ]
        max_scenes = kwargs.get("max_scenes")
        if max_scenes is not None and isinstance(max_scenes, int):
            base_scenes = base_scenes[:max_scenes]

        for s in base_scenes:
            s.estimated_duration_seconds = max(round(estimate_duration(s.narration, wpm=150), 2), 3.0)

        gen_meta = {
            "provider": self.provider_name,
            "mode": "deterministic_mock",
            "note": "test/demo content — not from real LLM",
            "channel_id": rules.get("channel_id", "default"),
            "channel_niche": niche,
            "attempt_number": attempt_number,
        }
        if corrective_instructions:
            gen_meta["corrective_instructions"] = corrective_instructions
            gen_meta["regeneration_reason"] = regeneration_reason

        script = ScriptDocument(
            content_id=content_id,
            topic=topic,
            working_title=working_title,
            title_candidates=[f"Understanding {topic}", f"The truth about {topic}", f"{topic}: a practical guide"],
            hook=hook_text,
            introduction=f"{topic} shapes outcomes more than we admit. This script explores why — without hype.",
            scenes=base_scenes,
            cta=f"Share your take on {topic} in the comments.",
            target_platform="youtube",
            language=language,
            tone_persona=tone,
            source_references=["mock-local-source-ph1"],
            generation_metadata=gen_meta,
        )
        return script
