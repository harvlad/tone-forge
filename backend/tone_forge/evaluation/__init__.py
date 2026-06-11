"""Evaluation infrastructure for ToneForge.

Provides metrics, benchmarks, and evaluation harnesses for measuring
the quality of:
- Descriptor accuracy (amp family, gain, cab, effects)
- Export usability
- MIDI extraction quality
- Ranking/recommendation relevance
- Retrieval similarity
- Reconstruction quality (stem quality, contamination, artifacts)

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
    BenchmarkSample,
    BenchmarkResult,
    run_benchmark,
    load_benchmark_dataset,
    create_benchmark_dataset,
)
from .benchmark_runner import (
    BenchmarkRunner,
    EnhancedBenchmarkResult,
    ReconstructionQualityMetrics,
    run_enhanced_benchmark,
)
from .dataset_builder import (
    BenchmarkDatasetBuilder,
    GroundTruthDescriptor,
    SampleMetadata,
    create_synthwave_benchmark,
    validate_dataset,
)
from .quality_tracker import (
    QualityTracker,
    MetricSnapshot,
    TrendAnalysis,
    get_tracker,
)
from .midi_benchmark import (
    MIDIBenchmarkSample,
    MIDIBenchmarkDataset,
    ProfiledMIDIMetrics,
    SampleResult,
    MIDIBenchmarkRunner,
    save_baseline,
    load_baseline,
)

__all__ = [
    # Metrics
    "DescriptorAccuracy",
    "MIDIQualityMetrics",
    "RetrievalMetrics",
    "RankingMetrics",
    "compute_descriptor_accuracy",
    "compute_midi_quality",
    "compute_retrieval_relevance",
    # Basic benchmarks
    "BenchmarkDataset",
    "BenchmarkSample",
    "BenchmarkResult",
    "run_benchmark",
    "load_benchmark_dataset",
    "create_benchmark_dataset",
    # Enhanced benchmark runner
    "BenchmarkRunner",
    "EnhancedBenchmarkResult",
    "ReconstructionQualityMetrics",
    "run_enhanced_benchmark",
    # Dataset builder
    "BenchmarkDatasetBuilder",
    "GroundTruthDescriptor",
    "SampleMetadata",
    "create_synthwave_benchmark",
    "validate_dataset",
    # Quality tracker
    "QualityTracker",
    "MetricSnapshot",
    "TrendAnalysis",
    "get_tracker",
    # MIDI benchmarks
    "MIDIBenchmarkSample",
    "MIDIBenchmarkDataset",
    "ProfiledMIDIMetrics",
    "SampleResult",
    "MIDIBenchmarkRunner",
    "save_baseline",
    "load_baseline",
]
