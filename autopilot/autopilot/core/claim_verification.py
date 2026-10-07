"""Factual Claim Verification Layer — Phase 2.

Implements:
  1. Factual Claim Verification (VERIFIED, UNVERIFIED, CONFLICTING, REJECTED)
  2. Contradiction Detection across multi-source research evidence
  3. Claim-Strength Calibration (ESTABLISHED_FACT, OBSERVED_DATA, THEORETICAL_PROPOSAL, POPULAR_MYTH)
  4. Qualification Requirement flags (e.g. "Scientists believe...", "According to early reports...")
"""
from __future__ import annotations

import re
from enum import Enum
from typing import Optional, List, Dict, Any, Union, Tuple
from pydantic import BaseModel, Field


class ClaimVerificationStatus(str, Enum):
    VERIFIED = "VERIFIED"
    UNVERIFIED = "UNVERIFIED"
    CONFLICTING = "CONFLICTING"
    REJECTED = "REJECTED"


class ClaimStrength(str, Enum):
    ESTABLISHED_FACT = "ESTABLISHED_FACT"
    OBSERVED_DATA = "OBSERVED_DATA"
    THEORETICAL_PROPOSAL = "THEORETICAL_PROPOSAL"
    POPULAR_MYTH = "POPULAR_MYTH"


class FactualClaim(BaseModel):
    claim_id: Optional[str] = None
    claim: str = Field(..., min_length=1)
    source: str = Field(..., min_length=1)
    evidence: str = Field(..., min_length=1)
    verification: ClaimVerificationStatus = Field(default=ClaimVerificationStatus.UNVERIFIED)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    claim_strength: ClaimStrength = Field(default=ClaimStrength.ESTABLISHED_FACT)
    qualification_required: bool = Field(default=False)
    qualification_phrase: Optional[str] = None
    conflicts: List[str] = Field(default_factory=list)


class ClaimVerifier:
    """Production claim verifier validating narrative statements against research provenance."""

    THEORY_KEYWORDS = {"theory", "hypothesize", "proposed", "suggests", "might", "model predicts", "theoretical", "postulated"}
    MYTH_KEYWORDS = {"myth", "common belief", "misconception", "falsely", "debunked", "urban legend", "wrongly"}
    OBSERVED_KEYWORDS = {"measured", "recorded", "observed", "surveyed", "tested", "experiment", "sensor", "data shows"}

    def calibrate_strength(self, claim_text: str, evidence_text: str = "") -> ClaimStrength:
        """Determine appropriate claim strength category from wording and evidence."""
        combined = f"{claim_text.lower()} {evidence_text.lower()}"
        if any(k in combined for k in self.MYTH_KEYWORDS):
            return ClaimStrength.POPULAR_MYTH
        if any(k in combined for k in self.THEORY_KEYWORDS):
            return ClaimStrength.THEORETICAL_PROPOSAL
        if any(k in combined for k in self.OBSERVED_KEYWORDS):
            return ClaimStrength.OBSERVED_DATA
        return ClaimStrength.ESTABLISHED_FACT

    def verify_claim(
        self,
        claim_text: str,
        sources: List[Dict[str, Any]],
        requested_strength: Optional[ClaimStrength] = None,
    ) -> FactualClaim:
        """Verify a factual claim against provided research sources."""
        if not sources:
            strength = requested_strength or self.calibrate_strength(claim_text)
            return FactualClaim(
                claim=claim_text,
                source="NONE",
                evidence="No source evidence available.",
                verification=ClaimVerificationStatus.UNVERIFIED,
                confidence=0.0,
                claim_strength=strength,
                qualification_required=True,
                qualification_phrase="Unverified assertion requires source confirmation",
            )

        # Match evidence keywords against sources
        matched_sources: List[Tuple[str, str, float]] = []
        conflicts: List[str] = []

        claim_tokens = set(re.findall(r"\w+", claim_text.lower()))
        # Remove common stop words for token overlap
        stop_words = {"the", "a", "an", "in", "on", "at", "is", "was", "are", "were", "of", "and", "to", "for"}
        meaningful_tokens = claim_tokens - stop_words

        for s in sources:
            s_name = s.get("source_id") or s.get("title") or s.get("source", "source")
            s_content = s.get("content") or s.get("text") or s.get("snippet") or s.get("evidence", "")
            s_tokens = set(re.findall(r"\w+", s_content.lower()))
            overlap = len(meaningful_tokens.intersection(s_tokens))
            ratio = overlap / max(1, len(meaningful_tokens))

            # Contradiction detection: check for numerical or categorical conflict
            # e.g., if claim mentions a number but source has an explicitly different number
            claim_nums = re.findall(r"\b\d+(?:,\d{3})*(?:\.\d+)?\b", claim_text)
            source_nums = re.findall(r"\b\d+(?:,\d{3})*(?:\.\d+)?\b", s_content)
            if claim_nums and source_nums and ratio >= 0.3:
                # If there are numbers in both and none match
                if not any(cn in source_nums for cn in claim_nums):
                    conflicts.append(f"{s_name}: mentions different values ({', '.join(source_nums[:2])})")

            if ratio >= 0.25:
                matched_sources.append((str(s_name), str(s_content[:300]), ratio))

        strength = requested_strength or self.calibrate_strength(claim_text, matched_sources[0][1] if matched_sources else "")

        if conflicts:
            return FactualClaim(
                claim=claim_text,
                source=matched_sources[0][0] if matched_sources else "multi-source",
                evidence=matched_sources[0][1] if matched_sources else "Conflicting research sources found.",
                verification=ClaimVerificationStatus.CONFLICTING,
                confidence=0.45,
                claim_strength=strength,
                qualification_required=True,
                qualification_phrase="Conflicting source data points require qualified wording",
                conflicts=conflicts,
            )

        if not matched_sources:
            return FactualClaim(
                claim=claim_text,
                source="UNVERIFIED",
                evidence="No matching evidence found in research sources.",
                verification=ClaimVerificationStatus.UNVERIFIED,
                confidence=0.1,
                claim_strength=strength,
                qualification_required=True,
                qualification_phrase="Unverified claim requires qualification",
            )

        best_source, best_evidence, score = max(matched_sources, key=lambda x: x[2])
        confidence = min(1.0, round(0.5 + score * 0.5, 2))
        is_verified = confidence >= 0.65

        qual_req = not is_verified or strength in (ClaimStrength.THEORETICAL_PROPOSAL, ClaimStrength.POPULAR_MYTH)
        qual_phrase = None
        if strength == ClaimStrength.THEORETICAL_PROPOSAL:
            qual_phrase = "Scientists propose that..."
        elif strength == ClaimStrength.POPULAR_MYTH:
            qual_phrase = "Contrary to popular belief..."
        elif not is_verified:
            qual_phrase = "Evidence suggests..."

        return FactualClaim(
            claim=claim_text,
            source=best_source,
            evidence=best_evidence,
            verification=ClaimVerificationStatus.VERIFIED if is_verified else ClaimVerificationStatus.UNVERIFIED,
            confidence=confidence,
            claim_strength=strength,
            qualification_required=qual_req,
            qualification_phrase=qual_phrase,
        )
