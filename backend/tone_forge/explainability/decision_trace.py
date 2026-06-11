"""Decision tracing infrastructure for reconstruction explainability.

Provides the core engine for recording decisions made during
audio reconstruction and analysis.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional
from contextlib import contextmanager

logger = logging.getLogger(__name__)


class DecisionType(str, Enum):
    """Types of decisions made during reconstruction."""

    # Analysis decisions
    AMP_FAMILY = "amp_family"
    AMP_GAIN = "amp_gain"
    CAB_CONFIG = "cab_config"
    SPEAKER_CHARACTER = "speaker_character"
    EFFECT_DETECTION = "effect_detection"

    # Quality decisions
    CONFIDENCE_ADJUSTMENT = "confidence_adjustment"
    QUALITY_GATE = "quality_gate"
    CONTAMINATION_HANDLING = "contamination_handling"

    # Extraction decisions
    ROLE_CLASSIFICATION = "role_classification"
    NOTE_EXTRACTION = "note_extraction"
    PHRASE_GROUPING = "phrase_grouping"

    # Pipeline decisions
    STAGE_SKIP = "stage_skip"
    ARCHETYPE_APPLICATION = "archetype_application"
    PASS_EXECUTION = "pass_execution"


@dataclass
class ReasoningFactor:
    """A single factor contributing to a decision."""

    name: str
    value: Any
    weight: float  # How much this factor influenced the decision (0-1)
    description: str

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "value": self.value,
            "weight": self.weight,
            "description": self.description,
        }


@dataclass
class DecisionTrace:
    """Record of a single decision with full reasoning."""

    decision_type: DecisionType
    decision_id: str
    timestamp: float

    # What was decided
    selected_option: Any
    alternatives: List[Dict[str, Any]] = field(default_factory=list)

    # Why it was decided
    reasoning_factors: List[ReasoningFactor] = field(default_factory=list)
    confidence: float = 1.0

    # Context
    stage: str = ""
    input_context: Dict[str, Any] = field(default_factory=dict)

    def add_factor(
        self,
        name: str,
        value: Any,
        weight: float,
        description: str,
    ) -> None:
        """Add a reasoning factor to this decision."""
        self.reasoning_factors.append(ReasoningFactor(
            name=name,
            value=value,
            weight=weight,
            description=description,
        ))

    def add_alternative(
        self,
        option: Any,
        score: float,
        reason_not_selected: str = "",
    ) -> None:
        """Add an alternative that was considered but not selected."""
        self.alternatives.append({
            "option": option,
            "score": score,
            "reason_not_selected": reason_not_selected,
        })

    def to_dict(self) -> dict:
        return {
            "decision_type": self.decision_type.value,
            "decision_id": self.decision_id,
            "timestamp": self.timestamp,
            "selected": self.selected_option,
            "alternatives": self.alternatives,
            "reasoning": [f.to_dict() for f in self.reasoning_factors],
            "confidence": self.confidence,
            "stage": self.stage,
        }

    def to_human_readable(self) -> str:
        """Generate human-readable explanation of this decision."""
        lines = [
            f"Decision: {self.decision_type.value}",
            f"Selected: {self.selected_option}",
            f"Confidence: {self.confidence * 100:.0f}%",
            "",
            "Reasoning:",
        ]

        # Sort factors by weight
        sorted_factors = sorted(
            self.reasoning_factors,
            key=lambda f: f.weight,
            reverse=True,
        )

        for factor in sorted_factors:
            weight_pct = factor.weight * 100
            lines.append(f"  - {factor.description} (weight: {weight_pct:.0f}%)")

        if self.alternatives:
            lines.append("")
            lines.append("Alternatives considered:")
            for alt in self.alternatives[:3]:  # Top 3 alternatives
                lines.append(f"  - {alt['option']} (score: {alt['score']:.2f})")
                if alt.get('reason_not_selected'):
                    lines.append(f"    Not selected: {alt['reason_not_selected']}")

        return "\n".join(lines)


class DecisionTraceEngine:
    """Engine for recording and querying decision traces.

    Usage:
        engine = DecisionTraceEngine()

        with engine.trace_decision(DecisionType.AMP_FAMILY, "amp_001") as trace:
            trace.selected_option = "bogner"
            trace.confidence = 0.85
            trace.add_factor(
                "harmonic_saturation",
                0.72,
                weight=0.4,
                description="High harmonic saturation profile matches Bogner characteristics",
            )
            trace.add_alternative("soldano", 0.78, "Lower low-end resonance match")
    """

    def __init__(self):
        self.traces: List[DecisionTrace] = []
        self._current_stage: str = ""
        self._session_id: str = ""
        self._enabled: bool = True

    def enable(self) -> None:
        """Enable decision tracing."""
        self._enabled = True

    def disable(self) -> None:
        """Disable decision tracing (for performance)."""
        self._enabled = False

    def set_stage(self, stage: str) -> None:
        """Set the current pipeline stage for context."""
        self._current_stage = stage

    def start_session(self, session_id: str = "") -> None:
        """Start a new tracing session, clearing previous traces."""
        self._session_id = session_id or str(time.time())
        self.traces = []

    @contextmanager
    def trace_decision(
        self,
        decision_type: DecisionType,
        decision_id: str,
        context: Optional[Dict[str, Any]] = None,
    ):
        """Context manager for tracing a decision.

        Args:
            decision_type: The type of decision being made
            decision_id: Unique identifier for this decision
            context: Optional input context for the decision

        Yields:
            DecisionTrace object to populate
        """
        if not self._enabled:
            # Return a dummy trace that does nothing
            yield DecisionTrace(
                decision_type=decision_type,
                decision_id=decision_id,
                timestamp=time.time(),
                stage=self._current_stage,
            )
            return

        trace = DecisionTrace(
            decision_type=decision_type,
            decision_id=decision_id,
            timestamp=time.time(),
            stage=self._current_stage,
            input_context=context or {},
        )

        try:
            yield trace
        finally:
            self.traces.append(trace)

    def record_decision(
        self,
        decision_type: DecisionType,
        decision_id: str,
        selected: Any,
        confidence: float = 1.0,
        reasoning: Optional[List[Dict]] = None,
        alternatives: Optional[List[Dict]] = None,
    ) -> DecisionTrace:
        """Record a decision directly without context manager.

        Args:
            decision_type: The type of decision
            decision_id: Unique identifier
            selected: The selected option
            confidence: Confidence in the decision
            reasoning: List of reasoning factor dicts
            alternatives: List of alternative option dicts

        Returns:
            The recorded DecisionTrace
        """
        if not self._enabled:
            return DecisionTrace(
                decision_type=decision_type,
                decision_id=decision_id,
                timestamp=time.time(),
                selected_option=selected,
                confidence=confidence,
                stage=self._current_stage,
            )

        trace = DecisionTrace(
            decision_type=decision_type,
            decision_id=decision_id,
            timestamp=time.time(),
            selected_option=selected,
            confidence=confidence,
            stage=self._current_stage,
        )

        if reasoning:
            for r in reasoning:
                trace.add_factor(
                    name=r.get("name", ""),
                    value=r.get("value"),
                    weight=r.get("weight", 0.5),
                    description=r.get("description", ""),
                )

        if alternatives:
            for alt in alternatives:
                trace.add_alternative(
                    option=alt.get("option"),
                    score=alt.get("score", 0),
                    reason_not_selected=alt.get("reason", ""),
                )

        self.traces.append(trace)
        return trace

    def get_traces_by_type(self, decision_type: DecisionType) -> List[DecisionTrace]:
        """Get all traces of a specific type."""
        return [t for t in self.traces if t.decision_type == decision_type]

    def get_traces_by_stage(self, stage: str) -> List[DecisionTrace]:
        """Get all traces from a specific pipeline stage."""
        return [t for t in self.traces if t.stage == stage]

    def get_low_confidence_decisions(self, threshold: float = 0.5) -> List[DecisionTrace]:
        """Get decisions below a confidence threshold."""
        return [t for t in self.traces if t.confidence < threshold]

    def to_dict(self) -> dict:
        """Export all traces as a dictionary."""
        return {
            "session_id": self._session_id,
            "trace_count": len(self.traces),
            "traces": [t.to_dict() for t in self.traces],
            "summary": self._generate_summary(),
        }

    def _generate_summary(self) -> dict:
        """Generate a summary of all traces."""
        if not self.traces:
            return {}

        # Group by type
        by_type = {}
        for trace in self.traces:
            type_name = trace.decision_type.value
            if type_name not in by_type:
                by_type[type_name] = []
            by_type[type_name].append(trace)

        summary = {
            "total_decisions": len(self.traces),
            "by_type": {
                t: len(traces) for t, traces in by_type.items()
            },
            "avg_confidence": sum(t.confidence for t in self.traces) / len(self.traces),
            "low_confidence_count": len(self.get_low_confidence_decisions()),
        }

        return summary

    def generate_explanation(self, decision_types: Optional[List[DecisionType]] = None) -> str:
        """Generate human-readable explanation of decisions.

        Args:
            decision_types: Optional filter for specific decision types

        Returns:
            Human-readable explanation string
        """
        traces = self.traces
        if decision_types:
            traces = [t for t in traces if t.decision_type in decision_types]

        if not traces:
            return "No decisions recorded."

        sections = []
        for trace in traces:
            sections.append(trace.to_human_readable())

        return "\n\n---\n\n".join(sections)


# Global trace engine instance
_trace_engine: Optional[DecisionTraceEngine] = None


def get_trace_engine() -> DecisionTraceEngine:
    """Get the global decision trace engine."""
    global _trace_engine
    if _trace_engine is None:
        _trace_engine = DecisionTraceEngine()
    return _trace_engine
