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
    """Types of actions that can be recorded."""
    CREATED = "created"
    RETAINED = "retained"
    REMOVED = "removed"
    MODIFIED = "modified"
    MERGED = "merged"
    SPLIT = "split"
    CLASSIFIED = "classified"
    MATCHED = "matched"
    REJECTED = "rejected"
    SELECTED = "selected"


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

    Tracks both WHAT happened and WHY.
    """
    # Unique identifier
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])

    # What happened
    action: DecisionAction = DecisionAction.CREATED
    domain: DecisionDomain = DecisionDomain.MIDI_EXTRACTION
    stage: str = ""  # Specific stage within domain (e.g., "ghost_note_classifier")

    # The entity being decided upon
    entity_type: str = ""  # e.g., "note", "plugin", "amp", "stem"
    entity_id: Optional[str] = None  # Reference to the entity
    entity_data: Optional[dict] = None  # Snapshot of entity state

    # Why it happened
    reason: ReasonGraph = field(default_factory=ReasonGraph)

    # Context
    timestamp: float = field(default_factory=time.time)
    parent_id: Optional[str] = None  # ID of decision that led to this one

    # Metadata
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "action": self.action.value,
            "domain": self.domain.value,
            "stage": self.stage,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "entity_data": self.entity_data,
            "reason": self.reason.to_dict(),
            "timestamp": self.timestamp,
            "parent_id": self.parent_id,
            "tags": self.tags,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)


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
    ) -> DecisionRecord:
        """Create and add a new decision record."""
        record = DecisionRecord(
            action=action,
            domain=domain or self.domain or DecisionDomain.MIDI_EXTRACTION,
            stage=stage,
            entity_type=entity_type,
            entity_id=entity_id,
            entity_data=entity_data,
            parent_id=parent_id,
            tags=tags or [],
        )
        return self.add(record)

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

    def to_summary(self, include_decisions: bool = True, max_decisions: int = 10) -> dict:
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

        if include_decisions and self.records:
            # Include most recent decisions with condensed info
            decisions = []
            for r in self.records[-max_decisions:]:
                decisions.append({
                    "action": r.action.value,
                    "stage": r.stage,
                    "entity_type": r.entity_type,
                    "reason": {
                        "summary": r.reason.summary,
                        "confidence": r.reason.confidence,
                    },
                })
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
