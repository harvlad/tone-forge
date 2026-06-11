"""Explainability and decision tracing for ToneForge reconstruction.

This module provides infrastructure for tracking and explaining
reconstruction decisions throughout the pipeline.

Core Components:
- DecisionTrace: Records individual decisions with reasoning
- ConfidenceReasoning: Explains confidence adjustments
- DescriptorReasoning: Explains tone descriptor decisions
- ReconstructionPath: Tracks the full reconstruction decision path
"""
from __future__ import annotations

from .decision_trace import (
    DecisionTrace,
    DecisionType,
    ReasoningFactor,
    DecisionTraceEngine,
    get_trace_engine,
)
from .confidence_reasoning import (
    AdjustmentReason,
    ConfidenceAdjustment,
    ConfidenceReasoning,
    create_confidence_reasoning,
)
from .descriptor_reasoning import (
    DescriptorDecision,
    AlternativeOption,
    DescriptorReasoning,
    create_descriptor_reasoning,
)
from .reconstruction_path import (
    PipelineStage,
    StageDecision,
    ReconstructionPath,
    create_reconstruction_path,
)

__all__ = [
    # Decision trace
    "DecisionTrace",
    "DecisionType",
    "ReasoningFactor",
    "DecisionTraceEngine",
    "get_trace_engine",
    # Confidence reasoning
    "AdjustmentReason",
    "ConfidenceAdjustment",
    "ConfidenceReasoning",
    "create_confidence_reasoning",
    # Descriptor reasoning
    "DescriptorDecision",
    "AlternativeOption",
    "DescriptorReasoning",
    "create_descriptor_reasoning",
    # Reconstruction path
    "PipelineStage",
    "StageDecision",
    "ReconstructionPath",
    "create_reconstruction_path",
]
