"""Informational Graphics Generator & Tier 3 Asset Provider — Phase 3.
Generates high-resolution 1080x1920 (9:16) clean, modern graphic cards,
data visualizations, schematic cards, comparison cards, and stat callouts.
"""
from __future__ import annotations
import math
import os
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

from autopilot.core.config import CONFIG
from autopilot.providers.asset_contracts import AssetProvider
from autopilot.providers.contracts import ProviderHealth, CapabilityMetadata, CostUsageMetadata, ProviderErrorType
from autopilot.core.contracts import (
    AssetCandidate, AssetSelection, AssetArtifact, AssetLicense,
    AssetProvenance, AssetDimensions, AssetMediaInfo
)


class InfographicsGenerator:
    """Procedural generator for high-definition 9:16 vertical graphic assets."""

    def __init__(self, width: int = 1080, height: int = 1920):
        self.width = width
        self.height = height

    def _get_font(self, size: int) -> ImageFont.ImageFont:
        """Load standard clean font or fallback to default."""
        try:
            # Try standard Windows fonts first
            candidates = [
                "C:/Windows/Fonts/segoeui.ttf",
                "C:/Windows/Fonts/arial.ttf",
                "C:/Windows/Fonts/calibri.ttf",
            ]
            for c in candidates:
                if Path(c).exists():
                    return ImageFont.truetype(c, size)
            return ImageFont.load_default()
        except Exception:
            return ImageFont.load_default()

    def generate_stat_card(
        self,
        headline: str,
        stat_value: str,
        subtext: str,
        out_path: Path,
        accent_color: Tuple[int, int, int] = (255, 215, 0),  # Gold/Yellow
    ) -> Path:
        """Generate a sleek modern dark-mode stat card in 9:16."""
        out_path.parent.mkdir(parents=True, exist_ok=True)
        img = Image.new("RGB", (self.width, self.height), color=(15, 17, 26))
        draw = ImageDraw.Draw(img)

        # Subtle dark gradient / radial glow behind card
        center_x, center_y = self.width // 2, self.height // 2
        for r in range(400, 0, -10):
            alpha_val = int(25 * (1 - r / 400.0))
            draw.ellipse(
                [center_x - r, center_y - r, center_x + r, center_y + r],
                fill=(15 + alpha_val, 20 + alpha_val, 35 + alpha_val)
            )

        # Card container (Glassmorphism rounded rectangle)
        card_margin_x = 80
        card_w = self.width - (2 * card_margin_x)
        card_h = 750
        card_y = (self.height - card_h) // 2
        card_x = card_margin_x

        # Outer card border with gradient glow
        draw.rounded_rectangle(
            [card_x - 3, card_y - 3, card_x + card_w + 3, card_y + card_h + 3],
            radius=32,
            outline=accent_color,
            width=3,
        )
        draw.rounded_rectangle(
            [card_x, card_y, card_x + card_w, card_y + card_h],
            radius=30,
            fill=(25, 28, 44),
        )

        # Draw Headline
        font_title = self._get_font(46)
        font_stat = self._get_font(100)
        font_sub = self._get_font(38)

        # Headline
        draw.text(
            (self.width // 2, card_y + 90),
            headline.upper()[:40],
            fill=(220, 225, 240),
            font=font_title,
            anchor="mm",
        )

        # Divider line
        div_w = 200
        draw.line(
            [(self.width // 2 - div_w // 2, card_y + 150), (self.width // 2 + div_w // 2, card_y + 150)],
            fill=accent_color,
            width=4,
        )

        # Stat Value (Large Punchy Text)
        draw.text(
            (self.width // 2, card_y + 300),
            stat_value[:20],
            fill=accent_color,
            font=font_stat,
            anchor="mm",
        )

        # Subtext explanation (Wrapped / Centered)
        draw.text(
            (self.width // 2, card_y + 450),
            subtext[:80],
            fill=(170, 178, 200),
            font=font_sub,
            anchor="mm",
        )

        # Footer badge
        draw.rounded_rectangle(
            [self.width // 2 - 150, card_y + 580, self.width // 2 + 150, card_y + 640],
            radius=15,
            fill=(40, 45, 70),
            outline=(60, 68, 100),
            width=1,
        )
        font_badge = self._get_font(26)
        draw.text(
            (self.width // 2, card_y + 610),
            "KEY INSIGHT",
            fill=(200, 210, 235),
            font=font_badge,
            anchor="mm",
        )

        img.save(str(out_path), "PNG")
        return out_path

    def generate_comparison_card(
        self,
        topic: str,
        left_label: str,
        left_val: str,
        right_label: str,
        right_val: str,
        out_path: Path,
    ) -> Path:
        """Generate a vertical 2-way comparison card in 9:16."""
        out_path.parent.mkdir(parents=True, exist_ok=True)
        img = Image.new("RGB", (self.width, self.height), color=(12, 14, 22))
        draw = ImageDraw.Draw(img)

        font_title = self._get_font(52)
        font_lbl = self._get_font(38)
        font_val = self._get_font(48)

        # Top Topic Title
        draw.text((self.width // 2, 450), topic.upper()[:35], fill=(255, 255, 255), font=font_title, anchor="mm")

        # Left Card
        card_w = 400
        card_h = 550
        card_y = 650
        left_x = 100
        right_x = 580

        draw.rounded_rectangle([left_x, card_y, left_x + card_w, card_y + card_h], radius=24, fill=(24, 30, 48), outline=(50, 150, 255), width=3)
        draw.text((left_x + card_w // 2, card_y + 80), left_label[:20], fill=(100, 180, 255), font=font_lbl, anchor="mm")
        draw.text((left_x + card_w // 2, card_y + 260), left_val[:20], fill=(255, 255, 255), font=font_val, anchor="mm")

        # Right Card
        draw.rounded_rectangle([right_x, card_y, right_x + card_w, card_y + card_h], radius=24, fill=(35, 25, 35), outline=(255, 80, 120), width=3)
        draw.text((right_x + card_w // 2, card_y + 80), right_label[:20], fill=(255, 120, 160), font=font_lbl, anchor="mm")
        draw.text((right_x + card_w // 2, card_y + 260), right_val[:20], fill=(255, 255, 255), font=font_val, anchor="mm")

        # VS badge in middle
        draw.ellipse([self.width // 2 - 45, card_y + 230, self.width // 2 + 45, card_y + 320], fill=(255, 215, 0))
        font_vs = self._get_font(32)
        draw.text((self.width // 2, card_y + 275), "VS", fill=(0, 0, 0), font=font_vs, anchor="mm")

        img.save(str(out_path), "PNG")
        return out_path


class InfographicsAssetProvider(AssetProvider):
    """Tier 3 Informational Graphics Generator Asset Provider."""
    provider_name = "infographics"
    capability = CapabilityMetadata(
        max_resolution="1080p",
        supports_9_16=True,
        local_only=True,
        license_note="Autopilot Infographic Engine — 100% Commercial Cleared / Procedural Generation.",
    )
    error_type = ProviderErrorType.NOT_AVAILABLE
    cost_meta = CostUsageMetadata(estimated_usd=0.0, provider_type="procedural_generator")

    def __init__(self, output_dir: Optional[Path] = None):
        self.generator = InfographicsGenerator()
        self.output_dir = output_dir or (CONFIG.get_artifacts_dir() / "infographics")
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def health_check(self) -> ProviderHealth:
        """Infographics engine is local and always available."""
        return ProviderHealth(
            healthy=True,
            provider_name=self.provider_name,
            error="",
            details={"generator": "PIL", "resolution": "1080x1920"},
        )

    def search(self, request: dict, max_results: int = 5, **kwargs) -> List[AssetCandidate]:
        """Synthesize candidate informational graphic models based on scene concept."""
        concept = request.get("visual_concept") or request.get("query") or "Data Insight"
        headline = request.get("headline") or concept[:35]
        stat_value = request.get("stat_value") or "100%"
        subtext = request.get("subtext") or concept

        card_hash = hashlib.sha256(f"{headline}_{stat_value}_{subtext}".encode("utf-8")).hexdigest()[:12]
        asset_id = f"info_{card_hash}"
        file_path = self.output_dir / f"{asset_id}.png"

        # Generate on the fly
        self.generator.generate_stat_card(
            headline=headline,
            stat_value=stat_value,
            subtext=subtext,
            out_path=file_path,
        )

        cand = AssetCandidate(
            candidate_id=asset_id,
            asset_type="image",
            source_id=asset_id,
            source_url=str(file_path),
            path_local=str(file_path),
            title=f"Infographic: {headline}",
            tags=["infographic", "data", "chart", "statistic", "schematic"],
            dimensions=AssetDimensions(width=1080, height=1920),
            media_info=AssetMediaInfo(mime_type="image/png", format="png"),
            license=AssetLicense(
                license_name="Autopilot Procedural Generation",
                license_url="",
                source_url="",
                creator="Autopilot Infographics Generator",
                attribution_required=False,
                commercial_use=True,
                derivative_use=True,
                rights_status="VERIFIED",
            ),
            provenance=AssetProvenance(
                provider=self.provider_name,
                source_id=asset_id,
                source_url=str(file_path),
                retrieval_timestamp=datetime.now(timezone.utc).isoformat(),
            ),
        )
        return [cand]

    def select(self, candidates: List[AssetCandidate], criteria: Optional[dict] = None) -> AssetSelection:
        """Select generated infographic candidate."""
        if not candidates:
            return AssetSelection(candidate_id="none", selected=False, reason="No infographic candidate available")
        c = candidates[0]
        return AssetSelection(
            candidate_id=c.candidate_id,
            selected=True,
            reason=f"Selected generated infographic card: {c.candidate_id}",
            score=1.0,
            license=c.license,
            provenance=c.provenance,
        )

    def download(self, candidate: AssetCandidate, out_path: str, **kwargs) -> str:
        """Already generated locally; ensure destination exists."""
        dest = Path(out_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        src_str = getattr(candidate, "path_local", None) or getattr(candidate, "source_url", None) or getattr(candidate, "url", "")
        src = Path(src_str)
        if src.exists() and src != dest:
            import shutil
            shutil.copy2(src, dest)
            return str(dest)
        elif src.exists():
            return str(src)
        return str(dest)

    def normalize(self, artifact_path: str, out_path: str, **kwargs) -> str:
        """Infographics are generated directly at 1080x1920."""
        return artifact_path
