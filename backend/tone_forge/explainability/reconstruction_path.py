"""Reconstruction path tracking for pipeline decisions.

Tracks the full decision path through the reconstruction pipeline,
enabling post-hoc analysis of why specific reconstruction choices were made.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional
import time


class PipelineStage(str, Enum):
    """Stages in the reconstruction pipeline."""

    # Source separation
    STEM_SEPARATION = "stem_separation"
    STEM_QUALITY = "stem_quality"

    # Quality analysis
    CONTAMINATION = "contamination"
    ARTIFACT_DETECTION = "artifact_detection"
    CONFIDENCE_MAPPING = "confidence_mapping"

    # Role and temporal
    ROLE_CLASSIFICATION = "role_classification"
    TEMPORAL_CONTINUITY = "temporal_continuity"

    # MIDI extraction passes
    MIDI_HIGH_CONFIDENCE = "midi_high_confidence"
    MIDI_HARMONIC_RECOVERY = "midi_harmonic_recovery"
    MIDI_PHRASE_GROUPING = "midi_phrase_grouping"
    MIDI_EFFECT_SUPPRESSION = "midi_effect_suppression"
    MIDI_GENRE_REFINEMENT = "midi_genre_refinement"
    MIDI_CONFIDENCE_QUANTIZATION = "midi_confidence_quantization"
    MIDI_MUSICALITY_CHECK = "midi_musicality_check"

    # Tone analysis
    TONE_ANALYSIS = "tone_analysis"
    AMP_CLASSIFICATION = "amp_classification"
    CAB_CLASSIFICATION = "cab_classification"
    EFFECT_DETECTION = "effect_detection"

    # Quality gates
    QUALITY_GATE = "quality_gate"


@dataclass
class StageDecision:
    """A decision made during a pipeline stage."""

    stage: PipelineStage
    decision_name: str
    selected_value: Any
    confidence: float
    timestamp: float = field(default_factory=time.time)

    # Reasoning
    factors: List[Dict[str, Any]] = field(default_factory=list)
    alternatives: List[Dict[str, Any]] = field(default_factory=list)

    # Impact tracking
    downstream_impact: List[str] = field(default_factory=list)
    quality_warnings: List[str] = field(default_factory=list)

    def add_factor(
        self,
        name: str,
        value: Any,
        contribution: float,
        description: str,
    ) -> None:
        """Add a reasoning factor."""
        self.factors.append({
            "name": name,
            "value": value,
            "contribution": contribution,
            "description": description,
        })

    def add_alternative(
        self,
        value: Any,
        score: float,
        reason_rejected: str = "",
    ) -> None:
        """Add an alternative that was considered."""
        self.alternatives.append({
            "value": value,
            "score": score,
            "reason_rejected": reason_rejected,
        })

    def add_downstream_impact(self, impact: str) -> None:
        """Record downstream impact of this decision."""
        self.downstream_impact.append(impact)

    def add_warning(self, warning: str) -> None:
        """Add a quality warning."""
        self.quality_warnings.append(warning)

    def to_dict(self) -> dict:
        return {
            "stage": self.stage.value,
            "decision": self.decision_name,
            "selected": self.selected_value,
            "confidence": self.confidence,
            "timestamp": self.timestamp,
            "factors": self.factors,
            "alternatives": self.alternatives,
            "downstream_impact": self.downstream_impact,
            "warnings": self.quality_warnings,
        }

    def to_human_readable(self) -> str:
        """Generate human-readable explanation."""
        lines = [
            f"[{self.stage.value}] {self.decision_name}",
            f"  Selected: {self.selected_value}",
            f"  Confidence: {self.confidence * 100:.0f}%",
        ]

        if self.factors:
            lines.append("  Factors:")
            sorted_factors = sorted(
                self.factors,
                key=lambda f: f.get("contribution", 0),
                reverse=True,
            )
            for factor in sorted_factors[:3]:
                lines.append(f"    - {factor['description']}")

        if self.quality_warnings:
            lines.append("  Warnings:")
            for warning in self.quality_warnings:
                lines.append(f"    ⚠ {warning}")

        return "\n".join(lines)


@dataclass
class ReconstructionPath:
    """Complete reconstruction decision path."""

    session_id: str = ""
    audio_file: str = ""
    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None

    # All decisions in order
    decisions: List[StageDecision] = field(default_factory=list)

    # Stage-indexed lookup
    _by_stage: Dict[PipelineStage, List[StageDecision]] = field(
        default_factory=dict,
        repr=False,
    )

    # Overall metrics
    overall_confidence: float = 1.0
    critical_decisions: List[str] = field(default_factory=list)
    quality_summary: Dict[str, Any] = field(default_factory=dict)

    def add_decision(self, decision: StageDecision) -> None:
        """Add a decision to the path."""
        self.decisions.append(decision)

        # Index by stage
        if decision.stage not in self._by_stage:
            self._by_stage[decision.stage] = []
        self._by_stage[decision.stage].append(decision)

        # Track critical low-confidence decisions
        if decision.confidence < 0.5:
            self.critical_decisions.append(
                f"{decision.stage.value}:{decision.decision_name}"
            )

    def record_decision(
        self,
        stage: PipelineStage,
        decision_name: str,
        selected_value: Any,
        confidence: float = 1.0,
        factors: Optional[List[Dict]] = None,
        alternatives: Optional[List[Dict]] = None,
    ) -> StageDecision:
        """Convenience method to create and add a decision."""
        decision = StageDecision(
            stage=stage,
            decision_name=decision_name,
            selected_value=selected_value,
            confidence=confidence,
        )

        if factors:
            for f in factors:
                decision.add_factor(
                    name=f.get("name", ""),
                    value=f.get("value"),
                    contribution=f.get("contribution", 0.5),
                    description=f.get("description", ""),
                )

        if alternatives:
            for alt in alternatives:
                decision.add_alternative(
                    value=alt.get("value"),
                    score=alt.get("score", 0),
                    reason_rejected=alt.get("reason", ""),
                )

        self.add_decision(decision)
        return decision

    def get_decisions_by_stage(
        self,
        stage: PipelineStage,
    ) -> List[StageDecision]:
        """Get all decisions from a specific stage."""
        return self._by_stage.get(stage, [])

    def get_low_confidence_decisions(
        self,
        threshold: float = 0.6,
    ) -> List[StageDecision]:
        """Get decisions below confidence threshold."""
        return [d for d in self.decisions if d.confidence < threshold]

    def get_decisions_with_warnings(self) -> List[StageDecision]:
        """Get decisions that have quality warnings."""
        return [d for d in self.decisions if d.quality_warnings]

    def complete(self) -> None:
        """Mark the reconstruction path as complete."""
        self.end_time = time.time()
        self._compute_overall_confidence()
        self._generate_quality_summary()

    def _compute_overall_confidence(self) -> None:
        """Compute weighted overall confidence."""
        if not self.decisions:
            self.overall_confidence = 0.0
            return

        # Weight by stage importance
        stage_weights = {
            PipelineStage.STEM_SEPARATION: 1.0,
            PipelineStage.STEM_QUALITY: 0.9,
            PipelineStage.CONTAMINATION: 0.8,
            PipelineStage.TONE_ANALYSIS: 0.9,
            PipelineStage.AMP_CLASSIFICATION: 0.85,
            PipelineStage.CAB_CLASSIFICATION: 0.7,
            PipelineStage.EFFECT_DETECTION: 0.6,
            PipelineStage.ROLE_CLASSIFICATION: 0.7,
        }

        total_weight = 0.0
        weighted_sum = 0.0

        for decision in self.decisions:
            weight = stage_weights.get(decision.stage, 0.5)
            weighted_sum += decision.confidence * weight
            total_weight += weight

        if total_weight > 0:
            self.overall_confidence = weighted_sum / total_weight

    def _generate_quality_summary(self) -> None:
        """Generate quality summary."""
        all_warnings = []
        for decision in self.decisions:
            all_warnings.extend(decision.quality_warnings)

        low_conf = self.get_low_confidence_decisions()

        self.quality_summary = {
            "total_decisions": len(self.decisions),
            "low_confidence_count": len(low_conf),
            "warning_count": len(all_warnings),
            "critical_count": len(self.critical_decisions),
            "overall_confidence": self.overall_confidence,
            "processing_time_ms": (
                (self.end_time - self.start_time) * 1000
                if self.end_time
                else None
            ),
        }

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "audio_file": self.audio_file,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "overall_confidence": self.overall_confidence,
            "decision_count": len(self.decisions),
            "critical_decisions": self.critical_decisions,
            "quality_summary": self.quality_summary,
            "decisions": [d.to_dict() for d in self.decisions],
        }

    def to_human_readable(self) -> str:
        """Generate human-readable reconstruction path explanation."""
        duration = ""
        if self.end_time:
            ms = (self.end_time - self.start_time) * 1000
            duration = f" ({ms:.0f}ms)"

        lines = [
            f"Reconstruction Path{duration}",
            f"Overall Confidence: {self.overall_confidence * 100:.0f}%",
            f"Total Decisions: {len(self.decisions)}",
            "=" * 50,
        ]

        # Group by stage
        current_stage = None
        for decision in self.decisions:
            if decision.stage != current_stage:
                current_stage = decision.stage
                lines.append("")
                lines.append(f"## {current_stage.value.upper()}")

            lines.append("")
            lines.append(decision.to_human_readable())

        # Summary
        if self.critical_decisions:
            lines.append("")
            lines.append("=" * 50)
            lines.append("CRITICAL LOW-CONFIDENCE DECISIONS:")
            for crit in self.critical_decisions:
                lines.append(f"  ! {crit}")

        return "\n".join(lines)


def create_reconstruction_path(
    session_id: str = "",
    audio_file: str = "",
) -> ReconstructionPath:
    """Create a new reconstruction path tracker.

    Args:
        session_id: Optional session identifier
        audio_file: Path to the audio file being processed

    Returns:
        ReconstructionPath instance
    """
    return ReconstructionPath(
        session_id=session_id,
        audio_file=audio_file,
    )
