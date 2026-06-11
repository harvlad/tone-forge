"""
Semantic Decision Provenance System

Tracks reconstruction decisions across all ToneForge analysis components.
Not just WHAT happened, but WHY it happened.

This enables:
- Explainable reconstruction editing
- Debugging analysis pipelines
- User-facing confidence explanations
- ML training data generation
- Audit trails for decisions

Applicable to:
- MIDI extraction decisions
- Plugin matching
- Amp/cab detection
- Archetype routing
- Effect reconstruction
- Synth classification
- Stem separation decisions
"""
from __future__ import annotations

import uuid
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Optional, Union
import json
import logging

logger = logging.getLogger(__name__)


class DecisionAction(Enum):
    """
    Semantic action types for reconstruction decisions.

    These create a proper reconstruction history, not just a flat event list.
    """
    # Creation/detection
    CREATED = "created"
    DETECTED = "detected"       # Initial detection from ML/DSP

    # Refinement
    RETAINED = "retained"       # Kept after validation
    REFINED = "refined"         # Improved precision (timing, velocity, etc.)
    CORRECTED = "corrected"     # Fixed an error
    ADJUSTED = "adjusted"       # Minor adjustment

    # Removal/suppression
    REMOVED = "removed"
    SUPPRESSED = "suppressed"   # Confidence below threshold, kept but flagged
    REJECTED = "rejected"       # Failed validation

    # Reconstruction
    RECONSTRUCTED = "reconstructed"  # Rebuilt from context
    INTERPOLATED = "interpolated"    # Filled gap from neighbors
    EXTRAPOLATED = "extrapolated"    # Extended from pattern

    # Structural
    MERGED = "merged"
    SPLIT = "split"
    CLASSIFIED = "classified"
    MATCHED = "matched"
    SELECTED = "selected"

    # Profile-aware extraction
    PROFILE_SELECTED = "profile_selected"  # Extraction profile was auto-selected
    CLEANUP_APPLIED = "cleanup_applied"    # Cleanup pass was applied to note
    NOTE_SUPPRESSED = "note_suppressed"    # Note suppressed by cleanup pass

    # User interaction (future)
    USER_ACCEPTED = "user_accepted"
    USER_REJECTED = "user_rejected"
    USER_OVERRIDDEN = "user_overridden"
    USER_LOCKED = "user_locked"


class DecisionSeverity(Enum):
    """
    Severity/impact level of a decision.

    Helps filter and prioritize decisions in the UI.
    """
    CRITICAL = "critical"       # Major structural change
    SIGNIFICANT = "significant" # Notable change affecting output
    MINOR = "minor"             # Small adjustment
    COSMETIC = "cosmetic"       # No functional impact
    INFO = "info"               # Informational only


class DecisionDomain(Enum):
    """Domains where decisions can occur."""
    MIDI_EXTRACTION = "midi_extraction"
    MIDI_REFINEMENT = "midi_refinement"
    STEM_SEPARATION = "stem_separation"
    AMP_DETECTION = "amp_detection"
    CAB_DETECTION = "cab_detection"
    EFFECT_CHAIN = "effect_chain"
    PLUGIN_MATCHING = "plugin_matching"
    SYNTH_CLASSIFICATION = "synth_classification"
    ARCHETYPE_ROUTING = "archetype_routing"
    KEY_DETECTION = "key_detection"
    TEMPO_DETECTION = "tempo_detection"
    GENRE_DETECTION = "genre_detection"


@dataclass
class ReasonFactor:
    """A single factor contributing to a decision reason."""
    name: str
    value: Union[float, bool, str, int]
    weight: float = 1.0  # How much this factor influenced the decision
    threshold: Optional[float] = None  # Threshold that was applied (if any)
    passed: Optional[bool] = None  # Whether the value passed the threshold

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class ReasonGraph:
    """
    Explains WHY a decision was made.

    Contains multiple factors that contributed to the decision,
    along with their values, weights, and thresholds.
    """
    factors: list[ReasonFactor] = field(default_factory=list)
    summary: str = ""
    confidence: float = 0.0  # Overall confidence in the decision (0-1)
    model_used: Optional[str] = None  # ML model or heuristic name

    def add_factor(
        self,
        name: str,
        value: Union[float, bool, str, int],
        weight: float = 1.0,
        threshold: Optional[float] = None,
    ) -> "ReasonGraph":
        """Add a factor to the reason graph."""
        passed = None
        if threshold is not None and isinstance(value, (int, float)):
            passed = value >= threshold

        self.factors.append(ReasonFactor(
            name=name,
            value=value,
            weight=weight,
            threshold=threshold,
            passed=passed,
        ))
        return self

    def compute_confidence(self) -> float:
        """Compute overall confidence from weighted factors."""
        if not self.factors:
            return 0.0

        total_weight = sum(f.weight for f in self.factors)
        if total_weight == 0:
            return 0.0

        # For boolean factors, passed=True contributes positively
        # For numeric factors, normalize value if threshold exists
        weighted_sum = 0.0
        for f in self.factors:
            if f.passed is not None:
                score = 1.0 if f.passed else 0.0
            elif isinstance(f.value, bool):
                score = 1.0 if f.value else 0.0
            elif isinstance(f.value, (int, float)) and f.threshold:
                # Sigmoid-like normalization around threshold
                score = min(1.0, f.value / f.threshold) if f.threshold > 0 else 0.5
            else:
                score = 0.5  # Unknown contribution

            weighted_sum += score * f.weight

        self.confidence = weighted_sum / total_weight
        return self.confidence

    def to_dict(self) -> dict:
        return {
            "factors": [f.to_dict() for f in self.factors],
            "summary": self.summary,
            "confidence": self.confidence,
            "model_used": self.model_used,
        }

    def explain(self) -> str:
        """Generate human-readable explanation."""
        if self.summary:
            lines = [self.summary]
        else:
            lines = []

        for f in self.factors:
            if f.passed is not None:
                status = "✓" if f.passed else "✗"
                lines.append(f"  {status} {f.name}: {f.value} (threshold: {f.threshold})")
            else:
                lines.append(f"  - {f.name}: {f.value}")

        if self.confidence > 0:
            lines.append(f"  Confidence: {self.confidence:.1%}")

        return "\n".join(lines)


@dataclass
class DecisionRecord:
    """
    Records a single decision in the analysis pipeline.

    Tracks:
    - WHAT happened (action, entity)
    - WHY it happened (reason graph with factors)
    - HOW it changed things (before/after values, confidence deltas)
    - IMPACT level (severity)

    This enables semantic reconstruction timelines, not just event logs.
    """
    # Unique identifier
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])

    # What happened
    action: DecisionAction = DecisionAction.CREATED
    domain: DecisionDomain = DecisionDomain.MIDI_EXTRACTION
    stage: str = ""  # Specific stage within domain (e.g., "ghost_note_classifier")
    subsystem: str = ""  # More specific component (e.g., "transient_detector")
    severity: DecisionSeverity = DecisionSeverity.INFO

    # The entity being decided upon
    entity_type: str = ""  # e.g., "note", "plugin", "amp", "stem"
    entity_id: Optional[str] = None  # Reference to the entity
    entity_data: Optional[dict] = None  # Snapshot of entity state (after)

    # Before/after tracking for value changes
    value_before: Optional[Any] = None  # Value before decision
    value_after: Optional[Any] = None   # Value after decision
    field_name: Optional[str] = None    # What field was changed (e.g., "velocity", "timing")

    # Confidence delta tracking
    confidence_before: Optional[float] = None  # Confidence before (0-1)
    confidence_after: Optional[float] = None   # Confidence after (0-1)

    # Why it happened
    reason: ReasonGraph = field(default_factory=ReasonGraph)

    # Expandable reasoning (human-readable explanations)
    reasoning_details: list[str] = field(default_factory=list)

    # Context
    timestamp: float = field(default_factory=time.time)
    parent_id: Optional[str] = None  # ID of decision that led to this one

    # Metadata
    tags: list[str] = field(default_factory=list)

    # User interaction (future)
    user_status: Optional[str] = None  # "accepted", "rejected", "locked", None

    @property
    def confidence_delta(self) -> Optional[float]:
        """Calculate confidence change from this decision."""
        if self.confidence_before is not None and self.confidence_after is not None:
            return self.confidence_after - self.confidence_before
        return None

    @property
    def value_delta(self) -> Optional[Any]:
        """Calculate value change from this decision."""
        if self.value_before is not None and self.value_after is not None:
            if isinstance(self.value_before, (int, float)) and isinstance(self.value_after, (int, float)):
                return self.value_after - self.value_before
        return None

    def add_reasoning(self, *reasons: str) -> "DecisionRecord":
        """Add human-readable reasoning explanations."""
        self.reasoning_details.extend(reasons)
        return self

    def to_dict(self) -> dict:
        result = {
            "id": self.id,
            "action": self.action.value,
            "domain": self.domain.value,
            "stage": self.stage,
            "subsystem": self.subsystem,
            "severity": self.severity.value,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "entity_data": self.entity_data,
            "field_name": self.field_name,
            "value_before": self.value_before,
            "value_after": self.value_after,
            "confidence_before": self.confidence_before,
            "confidence_after": self.confidence_after,
            "confidence_delta": self.confidence_delta,
            "reason": self.reason.to_dict(),
            "reasoning_details": self.reasoning_details,
            "timestamp": self.timestamp,
            "parent_id": self.parent_id,
            "tags": self.tags,
            "user_status": self.user_status,
        }
        # Remove None values for cleaner output
        return {k: v for k, v in result.items() if v is not None and v != [] and v != ""}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    def explain(self) -> str:
        """Generate human-readable explanation of this decision."""
        lines = [f"[{self.action.value.upper()}] {self.entity_type}"]

        if self.field_name and self.value_before is not None:
            delta = self.value_delta
            if delta is not None:
                sign = "+" if delta > 0 else ""
                lines.append(f"  {self.field_name}: {self.value_before} → {self.value_after} ({sign}{delta})")
            else:
                lines.append(f"  {self.field_name}: {self.value_before} → {self.value_after}")

        if self.confidence_before is not None and self.confidence_after is not None:
            delta = self.confidence_delta
            sign = "+" if delta > 0 else ""
            lines.append(f"  confidence: {self.confidence_before:.0%} → {self.confidence_after:.0%} ({sign}{delta:.0%})")

        if self.reasoning_details:
            lines.append("  reasoning:")
            for r in self.reasoning_details:
                lines.append(f"    - {r}")

        return "\n".join(lines)


@dataclass
class ProvenanceChain:
    """
    Collection of decisions forming a complete analysis trace.

    Enables:
    - Full audit trail
    - Decision replay/undo
    - Confidence aggregation
    - Explainable outputs
    """
    records: list[DecisionRecord] = field(default_factory=list)
    domain: Optional[DecisionDomain] = None
    session_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])

    def add(self, record: DecisionRecord) -> DecisionRecord:
        """Add a decision record to the chain."""
        self.records.append(record)
        return record

    def create_record(
        self,
        action: DecisionAction,
        stage: str,
        entity_type: str,
        entity_id: Optional[str] = None,
        entity_data: Optional[dict] = None,
        parent_id: Optional[str] = None,
        domain: Optional[DecisionDomain] = None,
        tags: Optional[list[str]] = None,
        subsystem: str = "",
        severity: DecisionSeverity = DecisionSeverity.INFO,
        field_name: Optional[str] = None,
        value_before: Optional[Any] = None,
        value_after: Optional[Any] = None,
        confidence_before: Optional[float] = None,
        confidence_after: Optional[float] = None,
        reasoning: Optional[list[str]] = None,
    ) -> DecisionRecord:
        """Create and add a new decision record with full provenance."""
        record = DecisionRecord(
            action=action,
            domain=domain or self.domain or DecisionDomain.MIDI_EXTRACTION,
            stage=stage,
            subsystem=subsystem,
            severity=severity,
            entity_type=entity_type,
            entity_id=entity_id,
            entity_data=entity_data,
            parent_id=parent_id,
            tags=tags or [],
            field_name=field_name,
            value_before=value_before,
            value_after=value_after,
            confidence_before=confidence_before,
            confidence_after=confidence_after,
            reasoning_details=reasoning or [],
        )
        return self.add(record)

    def record_value_change(
        self,
        stage: str,
        entity_type: str,
        entity_id: str,
        field_name: str,
        value_before: Any,
        value_after: Any,
        confidence_before: Optional[float] = None,
        confidence_after: Optional[float] = None,
        reasoning: Optional[list[str]] = None,
        severity: DecisionSeverity = DecisionSeverity.MINOR,
        action: DecisionAction = DecisionAction.REFINED,
        subsystem: str = "",
    ) -> DecisionRecord:
        """
        Convenience method for recording a value change with confidence delta.

        This is the primary method for tracking reconstruction decisions.
        """
        return self.create_record(
            action=action,
            stage=stage,
            subsystem=subsystem,
            severity=severity,
            entity_type=entity_type,
            entity_id=entity_id,
            field_name=field_name,
            value_before=value_before,
            value_after=value_after,
            confidence_before=confidence_before,
            confidence_after=confidence_after,
            reasoning=reasoning,
        )

    def get_by_entity(self, entity_id: str) -> list[DecisionRecord]:
        """Get all decisions for a specific entity."""
        return [r for r in self.records if r.entity_id == entity_id]

    def get_by_stage(self, stage: str) -> list[DecisionRecord]:
        """Get all decisions from a specific stage."""
        return [r for r in self.records if r.stage == stage]

    def get_removals(self) -> list[DecisionRecord]:
        """Get all removal decisions."""
        return [r for r in self.records if r.action == DecisionAction.REMOVED]

    def get_modifications(self) -> list[DecisionRecord]:
        """Get all modification decisions."""
        return [r for r in self.records if r.action == DecisionAction.MODIFIED]

    def aggregate_confidence(self) -> float:
        """Compute aggregate confidence across all decisions."""
        if not self.records:
            return 0.0

        confidences = [r.reason.confidence for r in self.records if r.reason.confidence > 0]
        if not confidences:
            return 0.0

        return sum(confidences) / len(confidences)

    def summary_stats(self) -> dict:
        """Generate summary statistics."""
        actions = {}
        stages = {}

        for r in self.records:
            actions[r.action.value] = actions.get(r.action.value, 0) + 1
            stages[r.stage] = stages.get(r.stage, 0) + 1

        return {
            "total_decisions": len(self.records),
            "by_action": actions,
            "by_stage": stages,
            "aggregate_confidence": self.aggregate_confidence(),
        }

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "domain": self.domain.value if self.domain else None,
            "records": [r.to_dict() for r in self.records],
            "summary": self.summary_stats(),
        }

    def to_summary(self, include_decisions: bool = True, max_decisions: int = 500) -> dict:
        """
        Generate a compact summary suitable for API responses.

        Args:
            include_decisions: Whether to include individual decisions
            max_decisions: Maximum number of decisions to include (most recent)

        Returns:
            Compact summary dict
        """
        stats = self.summary_stats()
        summary = {
            "domain": self.domain.value if self.domain else "unknown",
            "decision_count": stats["total_decisions"],
            "aggregate_confidence": stats["aggregate_confidence"],
            "by_action": stats["by_action"],
        }

        # Add severity breakdown
        severity_counts = {}
        for r in self.records:
            sev = r.severity.value
            severity_counts[sev] = severity_counts.get(sev, 0) + 1
        summary["by_severity"] = severity_counts

        if include_decisions and self.records:
            # Include most recent decisions with full provenance info
            decisions = []
            for r in self.records[-max_decisions:]:
                decision = {
                    "action": r.action.value,
                    "stage": r.stage,
                    "subsystem": r.subsystem if r.subsystem else None,
                    "entity_type": r.entity_type,
                    "severity": r.severity.value,
                    "reason": {
                        "summary": r.reason.summary,
                        "confidence": r.reason.confidence,
                    },
                }

                # Include confidence delta if available (flat format for UI)
                if r.confidence_before is not None:
                    decision["confidence_before"] = r.confidence_before
                if r.confidence_after is not None:
                    decision["confidence_after"] = r.confidence_after
                if r.confidence_delta is not None:
                    decision["confidence_delta"] = r.confidence_delta

                # Include value change if available (flat format for UI)
                if r.field_name:
                    decision["field_name"] = r.field_name
                if r.value_before is not None:
                    decision["value_before"] = r.value_before
                if r.value_after is not None:
                    decision["value_after"] = r.value_after
                if r.value_delta is not None:
                    decision["value_delta"] = r.value_delta

                # Include reasoning details if available
                if r.reasoning_details:
                    decision["reasoning_details"] = r.reasoning_details

                decisions.append(decision)
            summary["decisions"] = decisions

        return summary

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    def explain(self) -> str:
        """Generate human-readable explanation of the chain."""
        lines = [f"Provenance Chain [{self.session_id}]"]
        lines.append(f"Domain: {self.domain.value if self.domain else 'mixed'}")
        lines.append(f"Total decisions: {len(self.records)}")
        lines.append("")

        for r in self.records:
            lines.append(f"[{r.id}] {r.action.value.upper()} {r.entity_type}")
            lines.append(f"  Stage: {r.stage}")
            if r.reason.factors:
                lines.append(f"  Reason:")
                lines.append("    " + r.reason.explain().replace("\n", "\n    "))
            lines.append("")

        return "\n".join(lines)


# Convenience functions for creating common decision records

def record_note_created(
    chain: ProvenanceChain,
    note_data: dict,
    stage: str,
    model: str = "basic-pitch",
    confidence: float = 0.0,
) -> DecisionRecord:
    """Record creation of a MIDI note."""
    record = chain.create_record(
        action=DecisionAction.CREATED,
        stage=stage,
        entity_type="note",
        entity_id=f"n{len(chain.records)}",
        entity_data=note_data,
        domain=DecisionDomain.MIDI_EXTRACTION,
    )
    record.reason.model_used = model
    record.reason.confidence = confidence
    record.reason.summary = f"Note created by {model}"
    return record


def record_note_removed(
    chain: ProvenanceChain,
    note_id: str,
    note_data: dict,
    stage: str,
    reason: ReasonGraph,
) -> DecisionRecord:
    """Record removal of a MIDI note with full reason graph."""
    record = chain.create_record(
        action=DecisionAction.REMOVED,
        stage=stage,
        entity_type="note",
        entity_id=note_id,
        entity_data=note_data,
        domain=DecisionDomain.MIDI_REFINEMENT,
    )
    record.reason = reason
    return record


def record_plugin_matched(
    chain: ProvenanceChain,
    detected_params: dict,
    matched_plugin: str,
    similarity: float,
    stage: str = "plugin_matcher",
) -> DecisionRecord:
    """Record a plugin matching decision."""
    record = chain.create_record(
        action=DecisionAction.MATCHED,
        stage=stage,
        entity_type="plugin",
        entity_id=matched_plugin,
        entity_data=detected_params,
        domain=DecisionDomain.PLUGIN_MATCHING,
    )
    record.reason.add_factor("similarity_score", similarity, threshold=0.7)
    record.reason.compute_confidence()
    record.reason.summary = f"Matched to {matched_plugin} with {similarity:.1%} similarity"
    return record


def record_stem_separated(
    chain: ProvenanceChain,
    stem_name: str,
    model: str,
    confidence: float,
) -> DecisionRecord:
    """Record stem separation decision."""
    record = chain.create_record(
        action=DecisionAction.CREATED,
        stage="stem_separator",
        entity_type="stem",
        entity_id=stem_name,
        domain=DecisionDomain.STEM_SEPARATION,
    )
    record.reason.model_used = model
    record.reason.confidence = confidence
    record.reason.summary = f"Stem '{stem_name}' separated using {model}"
    return record


# Global provenance registry (optional, for cross-component tracking)
_active_chains: dict[str, ProvenanceChain] = {}


def get_or_create_chain(
    session_id: str,
    domain: Optional[DecisionDomain] = None,
) -> ProvenanceChain:
    """Get or create a provenance chain for a session."""
    if session_id not in _active_chains:
        _active_chains[session_id] = ProvenanceChain(
            session_id=session_id,
            domain=domain,
        )
    return _active_chains[session_id]


def clear_chain(session_id: str) -> None:
    """Clear a provenance chain."""
    _active_chains.pop(session_id, None)
