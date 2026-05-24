"""Stem-specific MIDI extraction pipelines.

Each pipeline owns its:
- Thresholds
- Cleanup logic
- Quantization strategy
- Note merge behavior
- Harmonic filtering
- Polyphony-aware extraction

Global heuristics are no longer acceptable.
"""
from __future__ import annotations

from .base import StemPipeline, PipelineConfig, PipelineResult
from .lead import LeadPipeline
from .bass import BassPipeline
from .pad import PadPipeline
from .guitar import GuitarPipeline
from .arp import ArpPipeline
from .factory import get_pipeline_for_stem, get_pipeline_by_name, list_available_pipelines

# Re-export polyphony types for convenience
from tone_forge.midi.polyphony_estimator import (
    PolyphonyClass,
    PolyphonyEstimate,
    PolyphonyEstimator,
)

__all__ = [
    # Base
    "StemPipeline",
    "PipelineConfig",
    "PipelineResult",
    # Pipelines
    "LeadPipeline",
    "BassPipeline",
    "PadPipeline",
    "GuitarPipeline",
    "ArpPipeline",
    # Factory
    "get_pipeline_for_stem",
    "get_pipeline_by_name",
    "list_available_pipelines",
    # Polyphony
    "PolyphonyClass",
    "PolyphonyEstimate",
    "PolyphonyEstimator",
]
