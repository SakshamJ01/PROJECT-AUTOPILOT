"""Format profiles — Phase 3.
Extensible profile mechanism for content formats.
"""
from __future__ import annotations
from pydantic import BaseModel, Field
from typing import List, Optional


class ProfileConfig(BaseModel):
    profile_id: str = "short_vertical"
    target_duration_sec: float = 45.0
    tone: str = "clear_direct"
    scene_expectations: int = 4
    cta_required: bool = True
    title_style: str = "direct"
    pacing_params: dict = Field(default_factory=lambda: {"max_scene_sec": 15.0})
    allowed_scene_types: List[str] = Field(default_factory=lambda: ["talking_head", "broll", "text", "montage"])


PROFILES = {
    "short_vertical": ProfileConfig(profile_id="short_vertical"),
}
