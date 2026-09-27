"""Kinetic Typography & Dynamic Caption Layout Engine — Phase 4.

Implements:
  - 2-5 Word Phrase Segmentation from authoritative Whisper word timestamps.
  - Selective keyword emphasis (Vibrant Yellow / Neon Green with 1.08x scale pop).
  - Dynamic Saliency Safe-Zone positioning (TOP, CENTER, LOWER).
  - Configurable Platform Safe Zones (YouTube Shorts, Instagram Reels, TikTok).
  - ASS Animated -> Static ASS -> SRT fallback hierarchy.
  - Post-render caption geometry and transcript verification helpers.
"""
from __future__ import annotations

import math
import os
import re
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field

from autopilot.core.timeline import (
    CaptionPhrase,
    CaptionPosition,
    MaterializedCaptionPlan,
    PlatformSafeZone,
    WordTimestamp,
)


class TypographyPreset(BaseModel):
    name: str
    font_name: str = "Arial Black"
    fallback_fonts: List[str] = Field(default_factory=lambda: ["Montserrat-Black", "DejaVu Sans", "Arial"])
    font_size: int = 54
    primary_color_ass: str = "&H00FFFFFF"  # White (ASS BBGGRR)
    primary_color_hex: str = "#FFFFFF"
    emphasis_color_ass: str = "&H0000E6FF"  # Vibrant Yellow (#FFE600 -> B=00, G=E6, R=FF)
    emphasis_color_hex: str = "#FFE600"
    secondary_emphasis_color_ass: str = "&H0066FF00"  # Neon Green (#00FF66 -> B=66, G=FF, R=00)
    secondary_emphasis_color_hex: str = "#00FF66"
    outline_color_ass: str = "&H00000000"  # Black
    outline_width: int = 4
    shadow_depth: int = 2
    emphasis_scale: float = 1.08
    pop_duration_ms: int = 90
    box_enabled: bool = False
    box_color_ass: str = "&H80000000"


TYPOGRAPHY_PRESETS: Dict[str, TypographyPreset] = {
    "hormozi_yellow_pop": TypographyPreset(
        name="hormozi_yellow_pop",
        font_name="Montserrat-Black",
        fallback_fonts=["Arial Black", "Segoe UI Black", "DejaVu Sans Bold"],
        font_size=58,
        primary_color_ass="&H00FFFFFF",
        primary_color_hex="#FFFFFF",
        emphasis_color_ass="&H0000E6FF",  # Vibrant Yellow
        emphasis_color_hex="#FFE600",
        outline_color_ass="&H00000000",
        outline_width=5,
        shadow_depth=3,
        emphasis_scale=1.08,
        pop_duration_ms=90,
    ),
    "neon_green_impact": TypographyPreset(
        name="neon_green_impact",
        font_name="THE BOLD FONT",
        fallback_fonts=["Arial Black", "Montserrat-Black", "DejaVu Sans Bold"],
        font_size=56,
        primary_color_ass="&H00FFFFFF",
        primary_color_hex="#FFFFFF",
        emphasis_color_ass="&H0066FF00",  # Neon Green
        emphasis_color_hex="#00FF66",
        outline_color_ass="&H00000000",
        outline_width=5,
        shadow_depth=2,
        emphasis_scale=1.10,
        pop_duration_ms=100,
    ),
    "clean_minimal": TypographyPreset(
        name="clean_minimal",
        font_name="Montserrat-Medium",
        fallback_fonts=["Segoe UI", "DejaVu Sans", "Arial"],
        font_size=48,
        primary_color_ass="&H00F0F0F0",
        primary_color_hex="#F0F0F0",
        emphasis_color_ass="&H00FFE500",  # Cyan Blue pop
        emphasis_color_hex="#00E5FF",
        outline_color_ass="&H001A1A1A",
        outline_width=3,
        shadow_depth=1,
        emphasis_scale=1.05,
        pop_duration_ms=80,
    ),
    "cinema_subtle": TypographyPreset(
        name="cinema_subtle",
        font_name="DejaVu Sans",
        fallback_fonts=["Segoe UI", "Arial"],
        font_size=46,
        primary_color_ass="&H00FFFFFF",
        primary_color_hex="#FFFFFF",
        emphasis_color_ass="&H0000D7FF",  # Gold
        emphasis_color_hex="#FFD700",
        outline_color_ass="&H00000000",
        outline_width=3,
        shadow_depth=2,
        emphasis_scale=1.04,
        pop_duration_ms=70,
    ),
}


class PlatformGeometry(BaseModel):
    platform: PlatformSafeZone
    screen_width: int = 1080
    screen_height: int = 1920
    top_margin_px: int = 140
    bottom_margin_px: int = 300
    left_margin_px: int = 80
    right_margin_px: int = 80

    @property
    def safe_top_y(self) -> int:
        return self.top_margin_px + 80

    @property
    def safe_center_y(self) -> int:
        return self.screen_height // 2

    @property
    def safe_lower_y(self) -> int:
        return self.screen_height - self.bottom_margin_px - 80


PLATFORM_GEOMETRIES: Dict[PlatformSafeZone, PlatformGeometry] = {
    PlatformSafeZone.YOUTUBE_SHORTS: PlatformGeometry(
        platform=PlatformSafeZone.YOUTUBE_SHORTS,
        top_margin_px=120,
        bottom_margin_px=280,
        left_margin_px=70,
        right_margin_px=70,
    ),
    PlatformSafeZone.INSTAGRAM_REELS: PlatformGeometry(
        platform=PlatformSafeZone.INSTAGRAM_REELS,
        top_margin_px=140,
        bottom_margin_px=320,
        left_margin_px=80,
        right_margin_px=80,
    ),
    PlatformSafeZone.TIKTOK: PlatformGeometry(
        platform=PlatformSafeZone.TIKTOK,
        top_margin_px=100,
        bottom_margin_px=300,
        left_margin_px=60,
        right_margin_px=120,  # right side interaction rail
    ),
}


class KineticTypographyEngine:
    """Core kinetic typography engine for generating impactful, readable captions."""

    def __init__(self, default_preset: str = "hormozi_yellow_pop"):
        self.default_preset = default_preset

    def get_preset(self, preset_name: Optional[str] = None) -> TypographyPreset:
        name = preset_name or self.default_preset
        return TYPOGRAPHY_PRESETS.get(name, TYPOGRAPHY_PRESETS["hormozi_yellow_pop"])

    def segment_words_into_phrases(
        self,
        word_timestamps: List[Any],
        min_words: int = 2,
        max_words: int = 4,
        emphasis_keywords: Optional[List[str]] = None,
    ) -> List[CaptionPhrase]:
        """Segment raw word timestamps into 2-5 word high-impact phrase units.

        Consumes authoritative word timestamps rather than regenerating independent timing.
        """
        if not word_timestamps:
            return []

        # Normalize incoming WordTimestamp models (supporting both contracts.py and timeline.py schemas)
        normalized_words: List[WordTimestamp] = []
        for wt in word_timestamps:
            w_text = getattr(wt, "word", str(wt)).strip()
            w_start = float(getattr(wt, "start", getattr(wt, "start_sec", 0.0)))
            w_end = float(getattr(wt, "end", getattr(wt, "end_sec", w_start + 0.35)))
            w_conf = getattr(wt, "confidence", getattr(wt, "probability", None))
            if w_end < w_start:
                w_end = w_start + 0.35
            normalized_words.append(
                WordTimestamp(
                    word=w_text,
                    start=round(w_start, 3),
                    end=round(w_end, 3),
                    confidence=w_conf,
                )
            )

        phrases: List[CaptionPhrase] = []
        curr_words: List[WordTimestamp] = []
        emphasis_set = {w.lower().strip(",.!?\"'") for w in (emphasis_keywords or []) if w}

        def _clean_token(tok: str) -> str:
            return re.sub(r"[^\w\s\-_.,!?'\"]", "", tok).strip()

        for idx, wt in enumerate(normalized_words):
            cleaned = _clean_token(wt.word)
            if not cleaned:
                continue

            curr_words.append(wt)
            raw_text = wt.word.strip()
            ends_with_punct = bool(re.search(r"[.!?,;:]$", raw_text))

            # Phrase boundary conditions:
            # 1. Punctuation break reached (with at least min_words or if last word)
            # 2. Reached max_words limit
            # 3. Last word in scene
            is_last = (idx == len(normalized_words) - 1)
            should_break = (
                is_last
                or (len(curr_words) >= min_words and ends_with_punct)
                or (len(curr_words) >= max_words)
            )

            if should_break and curr_words:
                p_text = " ".join(w.word.strip() for w in curr_words)
                p_start = curr_words[0].start
                p_end = max(curr_words[-1].end, p_start + 0.35)

                # Identify emphasis words within this phrase
                p_emphasis = []
                for w in curr_words:
                    clean_w = re.sub(r"[^\w]", "", w.word).lower()
                    if clean_w in emphasis_set or (clean_w.isupper() and len(clean_w) > 1):
                        p_emphasis.append(w.word.strip())

                # If no explicit emphasis keyword matched, select highest impact word
                if not p_emphasis and len(curr_words) > 1:
                    # Prefer numbers, acronyms, or longest substantive word
                    substantive = [
                        w.word.strip()
                        for w in curr_words
                        if len(re.sub(r"[^\w]", "", w.word)) >= 4
                        and re.sub(r"[^\w]", "", w.word).lower() not in {"this", "that", "with", "from", "they", "have", "were", "what"}
                    ]
                    if substantive:
                        p_emphasis.append(substantive[0])

                phrase = CaptionPhrase(
                    phrase_text=p_text,
                    start_sec=round(p_start, 3),
                    end_sec=round(p_end, 3),
                    words=list(curr_words),
                    emphasis_words=p_emphasis,
                )
                phrases.append(phrase)
                curr_words = []

        # Catch remaining trailing words
        if curr_words:
            p_text = " ".join(w.word.strip() for w in curr_words)
            p_start = curr_words[0].start
            p_end = max(curr_words[-1].end, p_start + 0.35)
            phrases.append(
                CaptionPhrase(
                    phrase_text=p_text,
                    start_sec=round(p_start, 3),
                    end_sec=round(p_end, 3),
                    words=list(curr_words),
                    emphasis_words=[w.word.strip() for w in curr_words if w.word.strip().lower() in emphasis_set],
                )
            )

        return phrases

    def compute_caption_coordinates(
        self,
        position: CaptionPosition,
        platform: PlatformSafeZone = PlatformSafeZone.YOUTUBE_SHORTS,
        saliency_y: Optional[float] = None,
    ) -> Tuple[int, int]:
        """Compute pixel (x, y) coordinates for caption rendering considering safe zones & saliency."""
        geo = PLATFORM_GEOMETRIES.get(platform, PLATFORM_GEOMETRIES[PlatformSafeZone.YOUTUBE_SHORTS])
        x = geo.screen_width // 2

        if position == CaptionPosition.TOP:
            y = geo.safe_top_y
        elif position == CaptionPosition.CENTER:
            # If subject is near center, offset slightly up or down
            if saliency_y is not None and 0.40 <= saliency_y <= 0.60:
                y = int(geo.screen_height * 0.38)
            else:
                y = geo.safe_center_y
        else:  # LOWER / Default
            # If subject is low, avoid covering by pushing to lower safe bound
            if saliency_y is not None and saliency_y > 0.70:
                y = geo.safe_top_y  # Flip to top if subject is at the bottom
            else:
                y = geo.safe_lower_y

        return (x, y)

    def generate_ass_script(
        self,
        phrases: List[CaptionPhrase],
        style_preset: str = "hormozi_yellow_pop",
        position: CaptionPosition = CaptionPosition.LOWER,
        platform: PlatformSafeZone = PlatformSafeZone.YOUTUBE_SHORTS,
        saliency_y: Optional[float] = None,
        animated: bool = True,
    ) -> str:
        """Generate full Advanced SubStation Alpha (.ass) script with kinetic animations."""
        preset = self.get_preset(style_preset)
        geo = PLATFORM_GEOMETRIES.get(platform, PLATFORM_GEOMETRIES[PlatformSafeZone.YOUTUBE_SHORTS])
        x_pos, y_pos = self.compute_caption_coordinates(position, platform, saliency_y)

        # Build ASS header
        header = f"""[Script Info]
; Script generated by Autopilot Kinetic Typography Engine
ScriptType: v4.00+
PlayResX: {geo.screen_width}
PlayResY: {geo.screen_height}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{preset.font_name},{preset.font_size},{preset.primary_color_ass},&H000000FF,{preset.outline_color_ass},{preset.outline_color_ass},-1,0,0,0,100,100,1,0,1,{preset.outline_width},{preset.shadow_depth},2,{geo.left_margin_px},{geo.right_margin_px},80,1
Style: Emphasis,{preset.font_name},{preset.font_size},{preset.emphasis_color_ass},&H000000FF,{preset.outline_color_ass},{preset.outline_color_ass},-1,0,0,0,100,100,1,0,1,{preset.outline_width + 1},{preset.shadow_depth + 1},2,{geo.left_margin_px},{geo.right_margin_px},80,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

        events: List[str] = []

        def _to_ass_time(seconds: float) -> str:
            total_cs = max(0, int(round(seconds * 100)))
            cs = total_cs % 100
            total_sec = total_cs // 100
            s = total_sec % 60
            total_min = total_sec // 60
            m = total_min % 60
            h = total_min // 60
            return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"

        for p in phrases:
            start_str = _to_ass_time(p.start_sec)
            end_str = _to_ass_time(p.end_sec)

            formatted_tokens = []
            for w in p.words:
                clean_w = re.sub(r"[^\w]", "", w.word).lower()
                clean_raw = w.word.strip().upper()
                is_emph = any(clean_w == re.sub(r"[^\w]", "", ew).lower() for ew in p.emphasis_words)

                if is_emph and animated:
                    # Animated pop scale (108%) with vibrant yellow/green color
                    scale_pct = int(preset.emphasis_scale * 100)
                    pop_ms = preset.pop_duration_ms
                    tag = f"{{\\c{preset.emphasis_color_ass}\\t(0,{pop_ms},\\fscx{scale_pct}\\fscy{scale_pct})\\t({pop_ms},{pop_ms*2},\\fscx100\\fscy100)}}{clean_raw}{{\\r}}"
                elif is_emph and not animated:
                    tag = f"{{\\c{preset.emphasis_color_ass}}}{clean_raw}{{\\r}}"
                else:
                    tag = clean_raw
                formatted_tokens.append(tag)

            text_line = " ".join(formatted_tokens)
            # Alignment=2 is bottom-center, with explicit position override
            event_line = f"Dialogue: 0,{start_str},{end_str},Default,,0,0,0,,{{\\an5\\pos({x_pos},{y_pos})}}{text_line}"
            events.append(event_line)

        return header + "\n".join(events) + "\n"

    def generate_srt(self, phrases: List[CaptionPhrase]) -> str:
        """Generate standard SubRip (.srt) format as universal fallback."""
        lines: List[str] = []

        def _to_srt_time(seconds: float) -> str:
            total_ms = max(0, int(round(seconds * 1000)))
            ms = total_ms % 1000
            total_sec = total_ms // 1000
            s = total_sec % 60
            total_min = total_sec // 60
            m = total_min % 60
            h = total_min // 60
            return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

        for idx, p in enumerate(phrases, start=1):
            start_str = _to_srt_time(p.start_sec)
            end_str = _to_srt_time(p.end_sec)
            clean_text = p.phrase_text.strip().upper()
            lines.append(f"{idx}\n{start_str} --> {end_str}\n{clean_text}\n")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Post-Render Caption Verification Helper
# ---------------------------------------------------------------------------

class CaptionVerificationResult(BaseModel):
    is_valid: bool
    transcript_match_ratio: float = 1.0
    safe_zone_compliant: bool = True
    overflow_detected: bool = False
    details: Dict[str, Any] = Field(default_factory=dict)


def verify_caption_layout(
    phrases: List[CaptionPhrase],
    expected_transcript: str,
    platform: PlatformSafeZone = PlatformSafeZone.YOUTUBE_SHORTS,
    saliency_y: Optional[float] = None,
) -> CaptionVerificationResult:
    """Validate caption phrases against transcript and safe-zone constraints."""
    if not phrases:
        return CaptionVerificationResult(
            is_valid=False,
            transcript_match_ratio=0.0,
            safe_zone_compliant=False,
            overflow_detected=False,
            details={"error": "Empty caption phrases list."},
        )

    # 1. Transcript reconciliation
    reconstructed = " ".join(p.phrase_text for p in phrases)
    clean_rec = re.sub(r"[^\w\s]", "", reconstructed).lower()
    clean_exp = re.sub(r"[^\w\s]", "", expected_transcript).lower()
    words_rec = clean_rec.split()
    words_exp = clean_exp.split()

    matched_words = sum(1 for w in words_rec if w in words_exp)
    total_words = max(len(words_exp), 1)
    match_ratio = round(matched_words / total_words, 3)

    # 2. Check phrase length / overflow
    geo = PLATFORM_GEOMETRIES.get(platform, PLATFORM_GEOMETRIES[PlatformSafeZone.YOUTUBE_SHORTS])
    overflow = False
    for p in phrases:
        if len(p.phrase_text) > 35 or len(p.words) > 6:
            overflow = True

    # 3. Safe zone check
    engine = KineticTypographyEngine()
    x, y = engine.compute_caption_coordinates(CaptionPosition.LOWER, platform, saliency_y)
    safe_compliant = (geo.top_margin_px <= y <= (geo.screen_height - geo.bottom_margin_px))

    is_valid = (match_ratio >= 0.70) and not overflow and safe_compliant

    return CaptionVerificationResult(
        is_valid=is_valid,
        transcript_match_ratio=match_ratio,
        safe_zone_compliant=safe_compliant,
        overflow_detected=overflow,
        details={
            "phrase_count": len(phrases),
            "reconstructed_length": len(words_rec),
            "expected_length": len(words_exp),
            "computed_y": y,
        },
    )
