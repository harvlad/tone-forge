"""Confidence reasoning and adjustment explanation.

Tracks and explains how confidence scores are adjusted
throughout the reconstruction pipeline.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class AdjustmentReason(str, Enum):
    """Reasons for confidence adjustments."""

    # Quality-based
    LOW_STEM_QUALITY = "low_stem_quality"
    HIGH_CONTAMINATION = "high_contamination"
    ARTIFACT_PRESENCE = "artifact_presence"
    SPECTRAL_SMEARING = "spectral_smearing"

    # Context-based
    REVERB_TAIL = "reverb_tail"
    OVERLAPPING_HARMONICS = "overlapping_harmonics"
    TRANSIENT_MASKING = "transient_masking"

    # Recovery-based
    ARCHETYPE_BOOST = "archetype_boost"
    HARMONIC_RECOVERY = "harmonic_recovery"
    CONTEXT_INFERENCE = "context_inference"

    # Validation-based
    CROSS_VALIDATION = "cross_validation"
    MUSICALITY_CHECK = "musicality_check"


@dataclass
class ConfidenceAdjustment:
    """A single confidence adjustment with reasoning."""

    original_confidence: float
    adjusted_confidence: float
    reason: AdjustmentReason
    description: str
    impact: float  # The actual change amount
    stage: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def direction(self) -> str:
        """Whether this was an increase or decrease."""
        if self.impact > 0:
            return "increase"
        elif self.impact < 0:
            return "decrease"
        return "unchanged"

    def to_dict(self) -> dict:
        return {
            "original": self.original_confidence,
            "adjusted": self.adjusted_confidence,
            "reason": self.reason.value,
            "description": self.description,
            "impact": self.impact,
            "direction": self.direction,
            "stage": self.stage,
        }


@dataclass
class ConfidenceReasoning:
    """Complete reasoning for a confidence value's evolution."""

    initial_confidence: float
    final_confidence: float
    target: str  # What this confidence is for (e.g., "amp_family", "gain")
    adjustments: List[ConfidenceAdjustment] = field(default_factory=list)

    def add_adjustment(
        self,
        reason: AdjustmentReason,
        impact: float,
        description: str,
        stage: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Add a confidence adjustment.

        Args:
            reason: Why the adjustment was made
            impact: The change amount (positive or negative)
            description: Human-readable explanation
            stage: Pipeline stage where adjustment occurred
            metadata: Additional context data
        """
        current = self.final_confidence
        new_confidence = max(0.0, min(1.0, current + impact))

        self.adjustments.append(ConfidenceAdjustment(
            original_confidence=current,
            adjusted_confidence=new_confidence,
            reason=reason,
            description=description,
            impact=impact,
            stage=stage,
            metadata=metadata or {},
        ))

        self.final_confidence = new_confidence

    def get_total_impact(self) -> float:
        """Get total confidence change from all adjustments."""
        return self.final_confidence - self.initial_confidence

    def get_negative_factors(self) -> List[ConfidenceAdjustment]:
        """Get all adjustments that decreased confidence."""
        return [a for a in self.adjustments if a.impact < 0]

    def get_positive_factors(self) -> List[ConfidenceAdjustment]:
        """Get all adjustments that increased confidence."""
        return [a for a in self.adjustments if a.impact > 0]

    def to_dict(self) -> dict:
        return {
            "target": self.target,
            "initial": self.initial_confidence,
            "final": self.final_confidence,
            "total_impact": self.get_total_impact(),
            "adjustment_count": len(self.adjustments),
            "adjustments": [a.to_dict() for a in self.adjustments],
        }

    def to_human_readable(self) -> str:
        """Generate human-readable confidence explanation."""
        lines = [
            f"Confidence for {self.target}:",
            f"  Initial: {self.initial_confidence * 100:.0f}%",
            f"  Final: {self.final_confidence * 100:.0f}%",
            f"  Change: {self.get_total_impact() * 100:+.0f}%",
            "",
        ]

        if self.adjustments:
            lines.append("Adjustments:")
            for adj in self.adjustments:
                sign = "+" if adj.impact >= 0 else ""
                lines.append(
                    f"  {sign}{adj.impact * 100:.0f}% - {adj.description}"
                )

        return "\n".join(lines)


def create_confidence_reasoning(
    target: str,
    initial_confidence: float,
) -> ConfidenceReasoning:
    """Create a new confidence reasoning tracker.

    Args:
        target: What the confidence is for
        initial_confidence: Starting confidence value

    Returns:
        ConfidenceReasoning instance
    """
    return ConfidenceReasoning(
        initial_confidence=initial_confidence,
        final_confidence=initial_confidence,
        target=target,
    )
