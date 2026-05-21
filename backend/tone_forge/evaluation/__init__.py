"""Evaluation infrastructure for ToneForge.

Provides metrics, benchmarks, and evaluation harnesses for measuring
the quality of:
- Descriptor accuracy (amp family, gain, cab, effects)
- Export usability
- MIDI extraction quality
- Ranking/recommendation relevance
- Retrieval similarity

This infrastructure is critical for principled model iteration.
Without it, model development becomes subjective chaos.
"""
from __future__ import annotations

from .metrics import (
    DescriptorAccuracy,
    MIDIQualityMetrics,
    RetrievalMetrics,
    RankingMetrics,
    compute_descriptor_accuracy,
    compute_midi_quality,
    compute_retrieval_relevance,
)
from .benchmarks import (
    BenchmarkDataset,
    BenchmarkResult,
    run_benchmark,
    load_benchmark_dataset,
)

__all__ = [
    "DescriptorAccuracy",
    "MIDIQualityMetrics",
    "RetrievalMetrics",
    "RankingMetrics",
    "compute_descriptor_accuracy",
    "compute_midi_quality",
    "compute_retrieval_relevance",
    "BenchmarkDataset",
    "BenchmarkResult",
    "run_benchmark",
    "load_benchmark_dataset",
]
