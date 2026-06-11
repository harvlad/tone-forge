"""Descriptor reasoning for tone analysis decisions.

Explains why specific amp, cab, and effects were detected.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class AlternativeOption:
    """An alternative that was considered but not selected."""

    name: str
    score: float
    reason_not_selected: str = ""
    features_matched: List[str] = field(default_factory=list)
    features_missing: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "score": self.score,
            "reason_not_selected": self.reason_not_selected,
            "features_matched": self.features_matched,
            "features_missing": self.features_missing,
        }


@dataclass
class DescriptorDecision:
    """A single descriptor decision with full reasoning."""

    category: str  # "amp_family", "cab_config", "effect", etc.
    selected: str
    confidence: float
    reasoning_factors: List[Dict[str, Any]] = field(default_factory=list)
    alternatives: List[AlternativeOption] = field(default_factory=list)

    def add_reasoning(
        self,
        factor_name: str,
        value: Any,
        contribution: float,
        description: str,
    ) -> None:
        """Add a reasoning factor.

        Args:
            factor_name: Name of the feature/factor
            value: The measured value
            contribution: How much this contributed to the decision (0-1)
            description: Human-readable explanation
        """
        self.reasoning_factors.append({
            "factor": factor_name,
            "value": value,
            "contribution": contribution,
            "description": description,
        })

    def add_alternative(
        self,
        name: str,
        score: float,
        reason_not_selected: str = "",
        features_matched: Optional[List[str]] = None,
        features_missing: Optional[List[str]] = None,
    ) -> None:
        """Add an alternative option that was considered."""
        self.alternatives.append(AlternativeOption(
            name=name,
            score=score,
            reason_not_selected=reason_not_selected,
            features_matched=features_matched or [],
            features_missing=features_missing or [],
        ))

    def to_dict(self) -> dict:
        return {
            "category": self.category,
            "selected": self.selected,
            "confidence": self.confidence,
            "reasoning": self.reasoning_factors,
            "alternatives": [a.to_dict() for a in self.alternatives],
        }

    def to_human_readable(self) -> str:
        """Generate human-readable explanation."""
        lines = [
            f"{self.category} classified as {self.selected}",
            f"Confidence: {self.confidence * 100:.0f}%",
            "",
            "Because:",
        ]

        # Sort by contribution
        sorted_factors = sorted(
            self.reasoning_factors,
            key=lambda f: f.get("contribution", 0),
            reverse=True,
        )

        for factor in sorted_factors[:5]:  # Top 5 factors
            lines.append(f"  - {factor['description']}")

        if self.alternatives:
            lines.append("")
            lines.append("Alternatives considered:")
            for alt in sorted(self.alternatives, key=lambda a: a.score, reverse=True)[:3]:
                lines.append(f"  - {alt.name} ({alt.score * 100:.0f}%)")
                if alt.reason_not_selected:
                    lines.append(f"    Not selected: {alt.reason_not_selected}")

        return "\n".join(lines)


@dataclass
class DescriptorReasoning:
    """Complete reasoning for a tone descriptor."""

    decisions: Dict[str, DescriptorDecision] = field(default_factory=dict)
    overall_confidence: float = 1.0
    summary: str = ""

    def add_decision(self, decision: DescriptorDecision) -> None:
        """Add a descriptor decision."""
        self.decisions[decision.category] = decision

    def get_decision(self, category: str) -> Optional[DescriptorDecision]:
        """Get decision for a specific category."""
        return self.decisions.get(category)

    def compute_overall_confidence(self) -> float:
        """Compute weighted average confidence."""
        if not self.decisions:
            return 0.0

        # Weights for different categories
        weights = {
            "amp_family": 1.0,
            "gain": 0.8,
            "cab_config": 0.6,
            "speaker_character": 0.5,
            "effects": 0.4,
        }

        total_weight = 0.0
        weighted_sum = 0.0

        for category, decision in self.decisions.items():
            weight = weights.get(category, 0.5)
            weighted_sum += decision.confidence * weight
            total_weight += weight

        if total_weight > 0:
            self.overall_confidence = weighted_sum / total_weight

        return self.overall_confidence

    def to_dict(self) -> dict:
        return {
            "overall_confidence": self.overall_confidence,
            "decisions": {k: v.to_dict() for k, v in self.decisions.items()},
            "summary": self.summary,
        }

    def to_human_readable(self) -> str:
        """Generate complete human-readable explanation."""
        sections = [
            f"Tone Descriptor Analysis (Overall Confidence: {self.overall_confidence * 100:.0f}%)",
            "=" * 60,
        ]

        for category, decision in self.decisions.items():
            sections.append("")
            sections.append(decision.to_human_readable())

        return "\n".join(sections)


def create_descriptor_reasoning() -> DescriptorReasoning:
    """Create a new descriptor reasoning instance."""
    return DescriptorReasoning()
