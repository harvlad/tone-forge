"""Filter ablation testing for regression detection.

Provides A/B comparison of extraction with/without each filter
to identify which filters cause regressions on specific samples.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .dataset_manifest import BenchmarkSample, DatasetManifest
from .parallel_runner import SampleEvaluationResult

logger = logging.getLogger(__name__)


@dataclass
class FilterConfig:
    """Configuration for a single filter."""

    name: str
    enabled: bool = True
    description: str = ""

    # Filter-specific parameters
    parameters: Dict[str, Any] = field(default_factory=dict)


# Default filter configurations for ToneForge extraction pipeline
DEFAULT_FILTERS = {
    "octave_correction": FilterConfig(
        name="octave_correction",
        description="Correct octave errors in detected notes",
    ),
    "harmonic_suppression": FilterConfig(
        name="harmonic_suppression",
        description="Suppress harmonic artifacts from fundamental detection",
    ),
    "delay_echo_cleanup": FilterConfig(
        name="delay_echo_cleanup",
        description="Remove delay/echo duplicate notes",
    ),
    "reverb_tail_cleanup": FilterConfig(
        name="reverb_tail_cleanup",
        description="Trim reverb tail from note endings",
    ),
    "velocity_normalization": FilterConfig(
        name="velocity_normalization",
        description="Normalize velocity based on stem type",
    ),
    "timing_quantization": FilterConfig(
        name="timing_quantization",
        description="Quantize note timing to grid",
    ),
    "note_merging": FilterConfig(
        name="note_merging",
        description="Merge adjacent notes of same pitch",
    ),
    "ghost_note_removal": FilterConfig(
        name="ghost_note_removal",
        description="Remove low-confidence ghost notes",
    ),
    "octave_doubling": FilterConfig(
        name="octave_doubling",
        description="Add missing octave notes for bass",
    ),
}


@dataclass
class AblationResult:
    """Result from ablating a single filter."""

    filter_name: str
    with_filter: SampleEvaluationResult
    without_filter: SampleEvaluationResult

    @property
    def f1_delta(self) -> float:
        """F1 difference (positive = filter helps)."""
        return self.with_filter.f1 - self.without_filter.f1

    @property
    def precision_delta(self) -> float:
        """Precision difference."""
        return self.with_filter.precision - self.without_filter.precision

    @property
    def recall_delta(self) -> float:
        """Recall difference."""
        return self.with_filter.recall - self.without_filter.recall

    @property
    def filter_helps(self) -> bool:
        """Whether the filter improves F1."""
        return self.f1_delta > 0

    @property
    def filter_hurts(self) -> bool:
        """Whether the filter reduces F1 significantly."""
        return self.f1_delta < -0.02  # 2% regression threshold

    def to_dict(self) -> Dict[str, Any]:
        return {
            "filter_name": self.filter_name,
            "f1_delta": self.f1_delta,
            "precision_delta": self.precision_delta,
            "recall_delta": self.recall_delta,
            "filter_helps": self.filter_helps,
            "filter_hurts": self.filter_hurts,
            "with_filter_f1": self.with_filter.f1,
            "without_filter_f1": self.without_filter.f1,
        }


@dataclass
class SampleAblationReport:
    """Ablation report for a single sample."""

    sample_id: str
    stem_type: str
    genre: str

    # Per-filter results
    ablations: Dict[str, AblationResult] = field(default_factory=dict)

    @property
    def helpful_filters(self) -> List[str]:
        """Filters that improve F1 on this sample."""
        return [name for name, result in self.ablations.items() if result.filter_helps]

    @property
    def harmful_filters(self) -> List[str]:
        """Filters that significantly hurt F1 on this sample."""
        return [name for name, result in self.ablations.items() if result.filter_hurts]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sample_id": self.sample_id,
            "stem_type": self.stem_type,
            "genre": self.genre,
            "ablations": {k: v.to_dict() for k, v in self.ablations.items()},
            "helpful_filters": self.helpful_filters,
            "harmful_filters": self.harmful_filters,
        }

    def summary(self) -> str:
        """Generate human-readable summary."""
        lines = [
            f"Sample: {self.sample_id} ({self.stem_type}, {self.genre})",
            "",
        ]

        if self.helpful_filters:
            lines.append(f"Helpful filters: {', '.join(self.helpful_filters)}")
        if self.harmful_filters:
            lines.append(f"Harmful filters: {', '.join(self.harmful_filters)}")

        lines.append("")
        lines.append(f"{'Filter':<25} {'Delta':>8} {'With':>8} {'Without':>8}")
        lines.append("-" * 53)

        for name, result in sorted(
            self.ablations.items(),
            key=lambda x: x[1].f1_delta,
            reverse=True,
        ):
            indicator = " +" if result.filter_helps else (" -" if result.filter_hurts else "  ")
            lines.append(
                f"{name:<25} {result.f1_delta:>+7.1%} "
                f"{result.with_filter.f1:>7.1%} {result.without_filter.f1:>7.1%}{indicator}"
            )

        return "\n".join(lines)


@dataclass
class FilterAblationReport:
    """Aggregate ablation report across all samples."""

    # Per-sample reports
    sample_reports: List[SampleAblationReport] = field(default_factory=list)

    # Aggregate filter statistics
    filter_statistics: Dict[str, Dict[str, float]] = field(default_factory=dict)

    def compute_statistics(self) -> None:
        """Compute aggregate statistics per filter."""
        filter_deltas: Dict[str, List[float]] = {}

        for sample in self.sample_reports:
            for filter_name, result in sample.ablations.items():
                if filter_name not in filter_deltas:
                    filter_deltas[filter_name] = []
                filter_deltas[filter_name].append(result.f1_delta)

        for filter_name, deltas in filter_deltas.items():
            self.filter_statistics[filter_name] = {
                "mean_f1_delta": np.mean(deltas),
                "std_f1_delta": np.std(deltas),
                "max_f1_delta": max(deltas),
                "min_f1_delta": min(deltas),
                "helps_count": sum(1 for d in deltas if d > 0),
                "hurts_count": sum(1 for d in deltas if d < -0.02),
                "sample_count": len(deltas),
            }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sample_reports": [r.to_dict() for r in self.sample_reports],
            "filter_statistics": self.filter_statistics,
        }

    def summary(self) -> str:
        """Generate human-readable summary."""
        if not self.filter_statistics:
            self.compute_statistics()

        lines = [
            "Filter Ablation Report",
            "=" * 60,
            "",
            f"Samples analyzed: {len(self.sample_reports)}",
            "",
            f"{'Filter':<25} {'Mean Δ':>8} {'Helps':>6} {'Hurts':>6} {'Std':>8}",
            "-" * 60,
        ]

        for filter_name, stats in sorted(
            self.filter_statistics.items(),
            key=lambda x: x[1]["mean_f1_delta"],
            reverse=True,
        ):
            indicator = "✓" if stats["mean_f1_delta"] > 0 else ("✗" if stats["mean_f1_delta"] < -0.01 else " ")
            lines.append(
                f"{filter_name:<25} {stats['mean_f1_delta']:>+7.1%} "
                f"{int(stats['helps_count']):>6} {int(stats['hurts_count']):>6} "
                f"{stats['std_f1_delta']:>7.1%} {indicator}"
            )

        # Identify problem filters
        problem_filters = [
            name for name, stats in self.filter_statistics.items()
            if stats["mean_f1_delta"] < -0.01 or stats["hurts_count"] > len(self.sample_reports) * 0.2
        ]

        if problem_filters:
            lines.extend([
                "",
                "PROBLEM FILTERS (consider disabling or tuning):",
            ])
            for f in problem_filters:
                stats = self.filter_statistics[f]
                lines.append(f"  - {f}: mean {stats['mean_f1_delta']:+.1%}, hurts {int(stats['hurts_count'])} samples")

        return "\n".join(lines)


def get_problematic_filters(
    report: FilterAblationReport,
    mean_threshold: float = -0.01,
    hurt_ratio_threshold: float = 0.2,
) -> List[Tuple[str, Dict[str, float]]]:
    """Identify filters that cause regressions.

    Args:
        report: Ablation report
        mean_threshold: Maximum acceptable mean F1 delta
        hurt_ratio_threshold: Maximum acceptable ratio of hurt samples

    Returns:
        List of (filter_name, statistics) for problematic filters
    """
    if not report.filter_statistics:
        report.compute_statistics()

    problems = []
    total_samples = len(report.sample_reports)

    for name, stats in report.filter_statistics.items():
        hurt_ratio = stats["hurts_count"] / total_samples if total_samples > 0 else 0

        if stats["mean_f1_delta"] < mean_threshold or hurt_ratio > hurt_ratio_threshold:
            problems.append((name, stats))

    return sorted(problems, key=lambda x: x[1]["mean_f1_delta"])


def get_filter_recommendations(
    report: FilterAblationReport,
    stem_type: Optional[str] = None,
) -> Dict[str, str]:
    """Get recommendations for filter configuration.

    Args:
        report: Ablation report
        stem_type: Optional stem type to filter by

    Returns:
        Dictionary of filter_name -> recommendation
    """
    if not report.filter_statistics:
        report.compute_statistics()

    recommendations = {}

    for name, stats in report.filter_statistics.items():
        mean_delta = stats["mean_f1_delta"]
        hurts_count = stats["hurts_count"]
        sample_count = stats["sample_count"]

        if mean_delta > 0.02:
            recommendations[name] = "keep_enabled"
        elif mean_delta < -0.02:
            recommendations[name] = "disable"
        elif hurts_count > sample_count * 0.3:
            recommendations[name] = "tune_parameters"
        else:
            recommendations[name] = "review"

    return recommendations
