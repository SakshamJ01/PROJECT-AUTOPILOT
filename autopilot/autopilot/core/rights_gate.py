"""Rights and License Gate — Safe-by-default policy enforcement.
Ensures external media strictly adheres to open licensing requirements before use.
"""
from __future__ import annotations
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any

from autopilot.core.config import CONFIG
from autopilot.core.contracts import AssetLicense


class RightsGateResult(BaseModel):
    allowed: bool
    rights_status: str  # VERIFIED | PARTIALLY_VERIFIED | UNKNOWN | REJECTED
    license_name: str
    reasons: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    attribution_text: Optional[str] = None
    commercial_cleared: bool = False
    derivative_cleared: bool = False


def evaluate_rights_gate(
    license_info: AssetLicense,
    allow_partial: Optional[bool] = None,
) -> RightsGateResult:
    """Evaluate an AssetLicense against the content factory's rights policy.

    Safe by default:
        - VERIFIED: Allowed.
        - PARTIALLY_VERIFIED: Warning, blocked unless allow_partial is True.
        - UNKNOWN: Strictly blocked.
        - REJECTED: Strictly blocked.
    """
    if allow_partial is None:
        allow_partial = CONFIG.rights_policy_allow_partial

    status = (license_info.rights_status or "UNKNOWN").upper().strip()
    reasons: List[str] = []
    warnings: List[str] = []
    allowed = False
    commercial_cleared = bool(license_info.commercial_use)
    derivative_cleared = bool(license_info.derivative_use)

    # Construct Attribution String
    creator = license_info.creator or "Anonymous / Unknown Creator"
    lic_name = license_info.license_name or "Unknown License"
    source = license_info.source_url or ""
    lic_url = license_info.license_url or ""

    if license_info.attribution_required:
        attr_parts = [f"Image by {creator}"]
        if lic_name != "UNKNOWN":
            attr_parts.append(f"under {lic_name}")
        if lic_url:
            attr_parts.append(f"({lic_url})")
        if source:
            attr_parts.append(f"Source: {source}")
        attribution_text = " ".join(attr_parts)
    else:
        attribution_text = f"Public Domain / {lic_name} by {creator}"

    if status == "VERIFIED":
        if license_info.commercial_use is False:
            allowed = False
            reasons.append("License prohibits commercial use")
        elif license_info.derivative_use is False:
            allowed = False
            reasons.append("License prohibits derivative works/adaptations")
        else:
            allowed = True
            reasons.append(f"License '{lic_name}' is verified for production use")
    elif status == "PARTIALLY_VERIFIED":
        if allow_partial:
            allowed = True
            warnings.append(f"License '{lic_name}' is partially verified; allowed under permissive policy override")
        else:
            allowed = False
            reasons.append(f"License '{lic_name}' is only partially verified; strict policy blocked usage")
    elif status == "REJECTED":
        allowed = False
        reasons.append(f"License '{lic_name}' is explicitly rejected for content production")
    elif status == "UNKNOWN":
        allowed = False
        reasons.append("Asset lacks verified license and provenance metadata (UNKNOWN)")
    else:
        allowed = False
        reasons.append(f"Unrecognized rights status: '{status}'")

    return RightsGateResult(
        allowed=allowed,
        rights_status=status,
        license_name=lic_name,
        reasons=reasons,
        warnings=warnings,
        attribution_text=attribution_text,
        commercial_cleared=commercial_cleared,
        derivative_cleared=derivative_cleared,
    )
