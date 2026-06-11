"""Benchmark expansion module for multi-genre evaluation."""

from .dataset_manifest import (
    GenreSpec,
    DatasetManifest,
    ManifestBuilder,
    SampleDifficulty,
    BenchmarkSample,
    GENRE_SPECS,
)
from .parallel_runner import (
    ParallelConfig,
    ParallelBenchmarkRunner,
)
from .history_tracker import (
    BenchmarkRun,
    RegressionAlert,
    BenchmarkHistory,
)
from .sample_discovery import (
    discover_samples,
    build_manifest_from_samples,
    load_or_create_manifest,
    get_default_samples_dir,
)
from .filter_ablation import (
    FilterConfig,
    AblationResult,
    SampleAblationReport,
    FilterAblationReport,
    DEFAULT_FILTERS,
    get_problematic_filters,
    get_filter_recommendations,
)

__all__ = [
    "GenreSpec",
    "DatasetManifest",
    "ManifestBuilder",
    "SampleDifficulty",
    "BenchmarkSample",
    "GENRE_SPECS",
    "ParallelConfig",
    "ParallelBenchmarkRunner",
    "BenchmarkRun",
    "RegressionAlert",
    "BenchmarkHistory",
    "discover_samples",
    "build_manifest_from_samples",
    "load_or_create_manifest",
    "get_default_samples_dir",
    "FilterConfig",
    "AblationResult",
    "SampleAblationReport",
    "FilterAblationReport",
    "DEFAULT_FILTERS",
    "get_problematic_filters",
    "get_filter_recommendations",
]
