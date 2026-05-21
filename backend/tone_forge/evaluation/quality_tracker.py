"""Quality metrics tracker for monitoring improvement over time.

Tracks quality metrics across benchmark runs to measure improvement
and detect regressions.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .benchmark_runner import EnhancedBenchmarkResult

logger = logging.getLogger(__name__)


@dataclass
class MetricSnapshot:
    """A snapshot of metrics at a point in time."""

    timestamp: str
    run_id: str
    dataset_name: str

    # Core metrics
    descriptor_overall: float
    amp_family_accuracy: float
    gain_mae: float
    effects_f1: float

    # Reconstruction metrics
    stem_quality: Optional[float] = None
    contamination: Optional[float] = None
    global_confidence: Optional[float] = None

    # MIDI metrics
    midi_f1: Optional[float] = None
    midi_timing_accuracy: Optional[float] = None

    # Metadata
    ml_models_used: bool = False
    notes: str = ""

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "timestamp": self.timestamp,
            "run_id": self.run_id,
            "dataset_name": self.dataset_name,
            "descriptor_overall": self.descriptor_overall,
            "amp_family_accuracy": self.amp_family_accuracy,
            "gain_mae": self.gain_mae,
            "effects_f1": self.effects_f1,
            "stem_quality": self.stem_quality,
            "contamination": self.contamination,
            "global_confidence": self.global_confidence,
            "midi_f1": self.midi_f1,
            "midi_timing_accuracy": self.midi_timing_accuracy,
            "ml_models_used": self.ml_models_used,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "MetricSnapshot":
        """Create from dictionary."""
        return cls(**d)

    @classmethod
    def from_result(
        cls,
        result: EnhancedBenchmarkResult,
        run_id: str,
        notes: str = "",
    ) -> "MetricSnapshot":
        """Create from benchmark result."""
        return cls(
            timestamp=datetime.now().isoformat(),
            run_id=run_id,
            dataset_name=result.dataset_name,
            descriptor_overall=result.descriptor_accuracy.overall_score,
            amp_family_accuracy=result.descriptor_accuracy.amp_family_accuracy,
            gain_mae=result.descriptor_accuracy.gain_mae,
            effects_f1=result.descriptor_accuracy.effects_f1,
            stem_quality=result.reconstruction_quality.avg_stem_quality if result.reconstruction_quality else None,
            contamination=result.reconstruction_quality.avg_contamination if result.reconstruction_quality else None,
            global_confidence=result.reconstruction_quality.avg_global_confidence if result.reconstruction_quality else None,
            midi_f1=result.midi_quality.note_f1 if result.midi_quality else None,
            midi_timing_accuracy=result.midi_quality.onset_within_50ms if result.midi_quality else None,
            ml_models_used=result.ml_models_used,
            notes=notes,
        )


@dataclass
class TrendAnalysis:
    """Analysis of metric trends over time."""

    metric_name: str
    num_samples: int
    current_value: float
    best_value: float
    worst_value: float
    mean_value: float
    std_value: float
    trend: str  # "improving", "stable", "degrading"
    improvement_rate: float  # Per run
    last_improvement: Optional[str] = None  # Timestamp

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "metric_name": self.metric_name,
            "num_samples": self.num_samples,
            "current_value": self.current_value,
            "best_value": self.best_value,
            "worst_value": self.worst_value,
            "mean_value": self.mean_value,
            "std_value": self.std_value,
            "trend": self.trend,
            "improvement_rate": self.improvement_rate,
            "last_improvement": self.last_improvement,
        }


class QualityTracker:
    """Track quality metrics over time.

    Maintains history of benchmark results and provides trend
    analysis for monitoring improvement.
    """

    def __init__(
        self,
        storage_path: Optional[Path] = None,
    ):
        """Initialize the tracker.

        Args:
            storage_path: Path to store tracking data
        """
        self.storage_path = Path(storage_path) if storage_path else None
        self.snapshots: List[MetricSnapshot] = []

        if self.storage_path and self.storage_path.exists():
            self._load()

    def record(
        self,
        result: EnhancedBenchmarkResult,
        run_id: Optional[str] = None,
        notes: str = "",
    ) -> MetricSnapshot:
        """Record a benchmark result.

        Args:
            result: Benchmark result to record
            run_id: Optional run identifier
            notes: Optional notes about this run

        Returns:
            MetricSnapshot that was recorded
        """
        if run_id is None:
            run_id = f"run_{len(self.snapshots) + 1}"

        snapshot = MetricSnapshot.from_result(result, run_id, notes)
        self.snapshots.append(snapshot)

        if self.storage_path:
            self._save()

        return snapshot

    def get_history(
        self,
        dataset_name: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[MetricSnapshot]:
        """Get metric history.

        Args:
            dataset_name: Filter by dataset name
            limit: Maximum number of snapshots to return

        Returns:
            List of metric snapshots
        """
        snapshots = self.snapshots

        if dataset_name:
            snapshots = [s for s in snapshots if s.dataset_name == dataset_name]

        if limit:
            snapshots = snapshots[-limit:]

        return snapshots

    def analyze_trend(
        self,
        metric_name: str,
        dataset_name: Optional[str] = None,
        window: int = 10,
    ) -> Optional[TrendAnalysis]:
        """Analyze trend for a specific metric.

        Args:
            metric_name: Name of metric to analyze
            dataset_name: Filter by dataset
            window: Number of recent runs to analyze

        Returns:
            TrendAnalysis or None if not enough data
        """
        snapshots = self.get_history(dataset_name, limit=window)

        if len(snapshots) < 2:
            return None

        # Extract metric values
        values = []
        for s in snapshots:
            value = getattr(s, metric_name, None)
            if value is not None:
                values.append((s.timestamp, value))

        if len(values) < 2:
            return None

        timestamps, metric_values = zip(*values)
        metric_values = np.array(metric_values)

        # Determine if higher is better (invert for error metrics)
        higher_is_better = metric_name not in ("gain_mae", "contamination")

        # Calculate trend
        # Use linear regression slope
        x = np.arange(len(metric_values))
        slope, _ = np.polyfit(x, metric_values, 1)

        if higher_is_better:
            if slope > 0.01:
                trend = "improving"
            elif slope < -0.01:
                trend = "degrading"
            else:
                trend = "stable"
        else:
            # For error metrics, negative slope is improvement
            if slope < -0.01:
                trend = "improving"
            elif slope > 0.01:
                trend = "degrading"
            else:
                trend = "stable"

        # Find last improvement
        last_improvement = None
        for i in range(len(metric_values) - 1, 0, -1):
            improved = (
                metric_values[i] > metric_values[i-1] if higher_is_better
                else metric_values[i] < metric_values[i-1]
            )
            if improved:
                last_improvement = timestamps[i]
                break

        return TrendAnalysis(
            metric_name=metric_name,
            num_samples=len(metric_values),
            current_value=float(metric_values[-1]),
            best_value=float(np.max(metric_values) if higher_is_better else np.min(metric_values)),
            worst_value=float(np.min(metric_values) if higher_is_better else np.max(metric_values)),
            mean_value=float(np.mean(metric_values)),
            std_value=float(np.std(metric_values)),
            trend=trend,
            improvement_rate=float(slope),
            last_improvement=last_improvement,
        )

    def get_summary(
        self,
        dataset_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get summary of tracking history.

        Args:
            dataset_name: Filter by dataset

        Returns:
            Summary dictionary
        """
        snapshots = self.get_history(dataset_name)

        if not snapshots:
            return {"status": "no_data"}

        # Analyze key metrics
        key_metrics = [
            "descriptor_overall",
            "amp_family_accuracy",
            "gain_mae",
            "effects_f1",
            "stem_quality",
            "contamination",
            "global_confidence",
        ]

        trends = {}
        for metric in key_metrics:
            trend = self.analyze_trend(metric, dataset_name)
            if trend:
                trends[metric] = trend.to_dict()

        # Compare first vs last
        first = snapshots[0]
        last = snapshots[-1]

        improvements = {}
        for metric in key_metrics:
            first_val = getattr(first, metric, None)
            last_val = getattr(last, metric, None)
            if first_val is not None and last_val is not None:
                improvements[metric] = last_val - first_val

        return {
            "total_runs": len(snapshots),
            "first_run": first.timestamp,
            "last_run": last.timestamp,
            "current_metrics": last.to_dict(),
            "trends": trends,
            "total_improvement": improvements,
        }

    def get_best_run(
        self,
        metric_name: str = "descriptor_overall",
        dataset_name: Optional[str] = None,
    ) -> Optional[MetricSnapshot]:
        """Get the best run by a specific metric.

        Args:
            metric_name: Metric to optimize
            dataset_name: Filter by dataset

        Returns:
            Best MetricSnapshot or None
        """
        snapshots = self.get_history(dataset_name)

        if not snapshots:
            return None

        # Determine if higher is better
        higher_is_better = metric_name not in ("gain_mae", "contamination")

        best = None
        best_value = None

        for s in snapshots:
            value = getattr(s, metric_name, None)
            if value is None:
                continue

            if best_value is None:
                best = s
                best_value = value
            elif (higher_is_better and value > best_value) or \
                 (not higher_is_better and value < best_value):
                best = s
                best_value = value

        return best

    def check_regression(
        self,
        result: EnhancedBenchmarkResult,
        threshold: float = 0.05,
    ) -> List[str]:
        """Check for regressions against historical best.

        Args:
            result: New result to check
            threshold: Regression threshold

        Returns:
            List of metrics that regressed
        """
        regressions = []

        key_metrics = [
            ("descriptor_overall", True),
            ("amp_family_accuracy", True),
            ("gain_mae", False),  # Lower is better
            ("effects_f1", True),
        ]

        for metric_name, higher_is_better in key_metrics:
            best = self.get_best_run(metric_name, result.dataset_name)
            if best is None:
                continue

            best_value = getattr(best, metric_name, None)
            current_value = getattr(
                MetricSnapshot.from_result(result, "check"),
                metric_name,
                None,
            )

            if best_value is None or current_value is None:
                continue

            if higher_is_better:
                if current_value < best_value - threshold:
                    regressions.append(
                        f"{metric_name}: {current_value:.3f} < {best_value:.3f} (best)"
                    )
            else:
                if current_value > best_value + threshold:
                    regressions.append(
                        f"{metric_name}: {current_value:.3f} > {best_value:.3f} (best)"
                    )

        return regressions

    def generate_report(
        self,
        dataset_name: Optional[str] = None,
    ) -> str:
        """Generate a human-readable progress report.

        Args:
            dataset_name: Filter by dataset

        Returns:
            Report string
        """
        summary = self.get_summary(dataset_name)

        if summary.get("status") == "no_data":
            return "No tracking data available."

        lines = [
            "=" * 50,
            "Quality Tracking Report",
            "=" * 50,
            "",
            f"Total Runs: {summary['total_runs']}",
            f"First Run:  {summary['first_run']}",
            f"Last Run:   {summary['last_run']}",
            "",
            "Current Metrics:",
        ]

        current = summary.get("current_metrics", {})
        for key in ["descriptor_overall", "amp_family_accuracy", "effects_f1", "stem_quality"]:
            value = current.get(key)
            if value is not None:
                lines.append(f"  {key}: {value:.1%}")

        lines.extend(["", "Trends:"])
        for metric, trend in summary.get("trends", {}).items():
            lines.append(f"  {metric}: {trend['trend']} ({trend['improvement_rate']:+.4f}/run)")

        lines.extend(["", "Total Improvement:"])
        for metric, delta in summary.get("total_improvement", {}).items():
            if delta is not None:
                sign = "+" if delta > 0 else ""
                lines.append(f"  {metric}: {sign}{delta:.4f}")

        lines.append("")
        lines.append("=" * 50)

        return "\n".join(lines)

    def _save(self) -> None:
        """Save tracking data to storage."""
        if not self.storage_path:
            return

        self.storage_path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "snapshots": [s.to_dict() for s in self.snapshots],
            "last_updated": datetime.now().isoformat(),
        }

        with open(self.storage_path, "w") as f:
            json.dump(data, f, indent=2)

    def _load(self) -> None:
        """Load tracking data from storage."""
        if not self.storage_path or not self.storage_path.exists():
            return

        try:
            with open(self.storage_path, "r") as f:
                data = json.load(f)

            self.snapshots = [
                MetricSnapshot.from_dict(s) for s in data.get("snapshots", [])
            ]
        except Exception as e:
            logger.warning(f"Failed to load tracking data: {e}")


# Global tracker instance
_tracker: Optional[QualityTracker] = None


def get_tracker(storage_path: Optional[Path] = None) -> QualityTracker:
    """Get or create the global quality tracker.

    Args:
        storage_path: Optional storage path

    Returns:
        QualityTracker instance
    """
    global _tracker

    if _tracker is None:
        _tracker = QualityTracker(storage_path)

    return _tracker
