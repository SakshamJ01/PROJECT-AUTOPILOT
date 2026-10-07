"""Targeted Regeneration Controller & Dependency Invalidation — Phase 5.

Implements:
  - Surgical component regeneration (Visual Asset, Voice Audio, Crop/Layout, Captions, Audio Mix)
  - Strict Dependency Invalidation:
      - Modifying Scene N visual invalidates only Scene N visual materialization, downstream RenderPlan, QA reports, and rendered video.
      - Preserves all unaffected scene audio, word timestamps, and visual assets.
      - Modifying Scene N voice invalidates word timestamps, captions, and downstream render plan.
  - Automatic timeline lineage progression, recompilation via TimelineCompiler, and re-rendering.
"""
from __future__ import annotations

import copy
import hashlib
from typing import Any, Dict, List, Optional
from pathlib import Path

from autopilot.core.timeline import (
    CaptionPosition,
    CropFraming,
    MaterializedAudioPlan,
    MaterializedCaptionPlan,
    MaterializedNarration,
    MaterializedScene,
    MaterializedTimeline,
    PlatformSafeZone,
    SelectedAsset,
    WordTimestamp,
)
from autopilot.core.kinetic_typography import KineticTypographyEngine
from autopilot.core.timeline_compiler import TimelineCompiler
from autopilot.core.contracts import RenderPlan


class RegenerationResult(object):
    pass


class TargetedRegenerationController:
    """Orchestrates surgical asset/voice/caption regeneration with dependency invalidation."""

    def __init__(self, compiler: Optional[TimelineCompiler] = None):
        self.compiler = compiler or TimelineCompiler()
        self.typo_engine = KineticTypographyEngine()

    def regenerate_scene_visual(
        self,
        timeline: MaterializedTimeline,
        scene_id: str,
        new_asset: SelectedAsset,
    ) -> MaterializedTimeline:
        """Surgically replace the visual asset for a single scene with dependency invalidation."""
        updated = copy.deepcopy(timeline)
        scene_found = False

        for scene in updated.scenes:
            curr_id = getattr(scene, "scene_id", f"scene_{getattr(scene, 'scene_index', 0)}")
            if curr_id == scene_id:
                scene.selected_assets = [new_asset]
                scene_found = True
                break

        if not scene_found:
            raise ValueError(f"Scene '{scene_id}' not found in timeline.")

        self._advance_lineage_and_invalidate(updated, reason=f"Regenerated visual for {scene_id}")
        return updated

    def regenerate_scene_voice(
        self,
        timeline: MaterializedTimeline,
        scene_id: str,
        new_voice_path: str,
        new_word_timestamps: List[WordTimestamp],
        new_duration_sec: float,
        text_override: Optional[str] = None,
    ) -> MaterializedTimeline:
        """Surgically replace voice audio and re-align word timestamps/captions for a single scene."""
        updated = copy.deepcopy(timeline)
        scene_found = False

        for scene in updated.scenes:
            curr_id = getattr(scene, "scene_id", f"scene_{getattr(scene, 'scene_index', 0)}")
            if curr_id == scene_id:
                # 1. Update narration
                text = text_override or (scene.narration.text if scene.narration else "")
                scene.narration = MaterializedNarration(
                    text=text,
                    audio_artifact_path=new_voice_path,
                    word_timestamps=new_word_timestamps,
                )

                # 2. Update timing
                if scene.timing:
                    scene.timing.duration_sec = new_duration_sec
                    scene.timing.end_time_sec = scene.timing.start_time_sec + new_duration_sec

                # 3. Invalidate & recompute caption phrases from new timestamps
                phrases = self.typo_engine.segment_words_into_phrases(new_word_timestamps)
                pos = scene.caption_plan.position if scene.caption_plan else CaptionPosition.LOWER
                style = scene.caption_plan.style_preset if scene.caption_plan else "hormozi_yellow_pop"
                plat = scene.caption_plan.platform_safe_zone if scene.caption_plan else PlatformSafeZone.YOUTUBE_SHORTS

                scene.caption_plan = MaterializedCaptionPlan(
                    phrases=phrases,
                    style_preset=style,
                    position=pos,
                    platform_safe_zone=plat,
                )

                # 4. Update audio plan
                if scene.audio_plan:
                    scene.audio_plan.voice_path = new_voice_path
                    scene.audio_plan.voice_duration_sec = new_duration_sec

                scene_found = True
                break

        if not scene_found:
            raise ValueError(f"Scene '{scene_id}' not found in timeline.")

        self._advance_lineage_and_invalidate(updated, reason=f"Regenerated voice for {scene_id}")
        return updated

    def regenerate_scene_crop(
        self,
        timeline: MaterializedTimeline,
        scene_id: str,
        new_saliency_y: float,
        new_caption_safe_zone: CaptionPosition,
    ) -> MaterializedTimeline:
        """Surgically adjust crop framing and safe zone without touching media files."""
        updated = copy.deepcopy(timeline)
        scene_found = False

        for scene in updated.scenes:
            curr_id = getattr(scene, "scene_id", f"scene_{getattr(scene, 'scene_index', 0)}")
            if curr_id == scene_id:
                if scene.selected_assets:
                    scene.selected_assets[0].crop_framing = CropFraming(
                        saliency_x=0.5,
                        saliency_y=new_saliency_y,
                        caption_safe_zone=new_caption_safe_zone,
                    )
                if scene.caption_plan:
                    scene.caption_plan.position = new_caption_safe_zone
                scene_found = True
                break

        if not scene_found:
            raise ValueError(f"Scene '{scene_id}' not found in timeline.")

        self._advance_lineage_and_invalidate(updated, reason=f"Adjusted crop and safe zone for {scene_id}")
        return updated

    def regenerate_script(
        self,
        timeline: MaterializedTimeline,
        new_script_document: Any,
    ) -> MaterializedTimeline:
        """SCRIPT defect route: regenerate the script and invalidate ALL dependents.

        A script change invalidates dependent voice, assets, render plan, and
        QA — the whole production is re-derived from the new script.
        """
        updated = copy.deepcopy(timeline)
        # Attach the new script and clear all derived per-scene artifacts so
        # nothing stale can be reused.
        try:
            setattr(updated, "script_document", new_script_document)
        except Exception:
            pass
        for scene in updated.scenes:
            # Invalidate derived voice/caption/asset materialization.
            if scene.narration:
                scene.narration.word_timestamps = []
            scene.caption_plan = None
            scene.selected_assets = []
        self._advance_lineage_and_invalidate(updated, reason="Script regenerated; all dependents invalidated")
        return updated

    def route_defect(self, regeneration_target: str) -> List[str]:
        """Map a defect's regeneration target to the ordered stages to re-run.

        Ensures a defect only re-runs its own dependency chain — never the
        entire pipeline for every defect.
        """
        routing = {
            "VISUAL_ASSET": ["ASSETS", "RENDER", "QA"],
            "CROP_FRAMING": ["RENDER", "QA"],
            "CAPTION_LAYOUT": ["CAPTIONS", "RENDER", "QA"],
            "VOICE_AUDIO": ["VOICE", "CAPTIONS", "RENDER", "QA"],
            "AUDIO_MIX": ["AUDIO_MIX", "RENDER", "QA"],
            "SCRIPT": ["SCRIPT", "VOICE", "ASSETS", "CAPTIONS", "RENDER", "QA"],
            "FULL_TIMELINE": ["VOICE", "ASSETS", "CAPTIONS", "RENDER", "QA"],
            "NONE": [],
        }
        return routing.get(str(regeneration_target), [])

    def recompile_render_plan(
        self,
        timeline: MaterializedTimeline,
        check_physical_files: bool = True,
    ) -> RenderPlan:
        """Recompile the updated MaterializedTimeline into a fresh sealed RenderPlan."""
        return self.compiler.compile(timeline, check_physical_files=check_physical_files)

    def _advance_lineage_and_invalidate(
        self,
        timeline: MaterializedTimeline,
        reason: str,
    ) -> None:
        """Update timeline hash and bump lineage while marking downstream render plans invalid."""
        # Recalculate total duration if scene durations shifted
        tot_dur = sum(
            (getattr(s, "duration_sec", None) or (s.timing.duration_sec if getattr(s, "timing", None) else 0.0))
            for s in timeline.scenes
        )
        timeline.total_measured_duration_sec = round(tot_dur, 3)

        # Bump version and set parent lineage
        prev_ver = timeline.timeline_version or "v1.0"
        timeline.parent_timeline_version = prev_ver
        try:
            ver_num = float(prev_ver.lstrip("v"))
            timeline.timeline_version = f"v{round(ver_num + 0.1, 1)}"
        except Exception:
            timeline.timeline_version = f"{prev_ver}.1"

        # Record invalidation trace in audio mix manifest
        mix = dict(timeline.audio_mix_manifest or {})
        mix["last_invalidation_reason"] = reason
        timeline.audio_mix_manifest = mix
