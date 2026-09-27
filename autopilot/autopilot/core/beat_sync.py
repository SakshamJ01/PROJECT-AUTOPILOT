"""Audio-Visual Beat Synchronization Engine — Phase 4.

Implements the multi-sensory beat synchronization relationship:
  Narrative Beat ⟹ Caption Word Emphasis + Visual Cut/Transition + SFX Cue + Music Accent
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from autopilot.core.timeline import (
    CaptionPhrase,
    CaptionPosition,
    MaterializedAudioPlan,
    MaterializedCaptionPlan,
    MaterializedScene,
    NarrativeRole,
    SFXEvent,
    WordTimestamp,
)
from autopilot.core.kinetic_typography import KineticTypographyEngine
from autopilot.core.audio_scene_graph import AudioSceneGraphEngine


# Standard narrative role to default SFX cue mappings
ROLE_SFX_MAPPINGS: Dict[NarrativeRole, str] = {
    NarrativeRole.HOOK: "impact",
    NarrativeRole.SETUP: "whoosh_fast",
    NarrativeRole.REVEAL: "bass_drop",
    NarrativeRole.EXPLANATION: "digital_pop",
    NarrativeRole.ESCALATION: "whoosh_fast",
    NarrativeRole.TWIST: "bass_drop",
    NarrativeRole.PAYOFF: "impact",
    NarrativeRole.CTA: "camera_shutter",
    NarrativeRole.CONTENT: "digital_pop",
}


class BeatSyncEngine:
    """Synchronizes caption emphasis, visual sub-shots, and audio SFX/music accents."""

    def __init__(self):
        self.typography_engine = KineticTypographyEngine()
        self.audio_engine = AudioSceneGraphEngine()

    def synchronize_scene(
        self,
        scene: MaterializedScene,
        saliency_y: Optional[float] = None,
        style_preset: str = "hormozi_yellow_pop",
    ) -> MaterializedScene:
        """Apply kinetic typography segmentation and audio-visual beat cues to a materialized scene."""
        word_timestamps = scene.narration.word_timestamps or []
        emphasis_keywords = scene.caption_plan.phrases[0].emphasis_words if scene.caption_plan and scene.caption_plan.phrases else []

        # 1. 2-5 Word Phrase Segmentation
        phrases = self.typography_engine.segment_words_into_phrases(
            word_timestamps=word_timestamps,
            min_words=2,
            max_words=4,
            emphasis_keywords=emphasis_keywords,
        )

        # 2. Dynamic Position & Platform Safe Zone
        pos = scene.caption_plan.position if scene.caption_plan else CaptionPosition.LOWER
        platform = scene.caption_plan.platform_safe_zone if scene.caption_plan else None

        scene.caption_plan = MaterializedCaptionPlan(
            style_preset=style_preset,
            position=pos,
            phrases=phrases,
            platform_safe_zone=platform or scene.caption_plan.platform_safe_zone,
        )

        # 3. SFX Beat Synchronization
        # If no SFX cues explicitly placed, attach default narrative role cue at scene start / primary emphasis beat
        if not scene.audio_plan.sfx_events:
            default_cue = ROLE_SFX_MAPPINGS.get(scene.narrative_role, "whoosh_fast")
            # Trigger SFX near first emphasis word or at scene start
            trigger_time = 0.05
            if phrases and phrases[0].words:
                trigger_time = max(0.0, phrases[0].words[0].start - scene.timing.start_time_sec)

            scene.audio_plan.sfx_events.append(
                SFXEvent(
                    cue=default_cue,
                    time_sec=round(trigger_time, 3),
                    volume_db=-6.0,
                )
            )

        return scene
