"""Benchmark history tracker for regression detection.

Provides:
- JSON storage of benchmark runs
- Git commit association
- Regression detection with configurable threshold
- Timeline export for trend analysis
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

if TYPE_CHECKING:
    from .parallel_runner import BenchmarkRunResult

logger = logging.getLogger(__name__)


@dataclass
class BenchmarkRun:
    """A single benchmark run for historical tracking."""

    # Identification
    run_id: str
    timestamp: str
    manifest_name: str
    manifest_version: str

    # Git context
    git_commit: Optional[str] = None
    git_branch: Optional[str] = None
    git_message: Optional[str] = None

    # Overall metrics
    overall_f1: float = 0.0
    overall_precision: float = 0.0
    overall_recall: float = 0.0

    # Sample counts
    total_samples: int = 0
    successful_samples: int = 0
    failed_samples: int = 0

    # Per-stem metrics
    per_stem_f1: Dict[str, float] = field(default_factory=dict)

    # Per-genre metrics
    per_genre_f1: Dict[str, float] = field(default_factory=dict)

    # Execution info
    execution_time_sec: float = 0.0
    worker_count: int = 1

    # Notes/tags
    notes: str = ""
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "run_id": self.run_id,
            "timestamp": self.timestamp,
            "manifest_name": self.manifest_name,
            "manifest_version": self.manifest_version,
            "git_commit": self.git_commit,
            "git_branch": self.git_branch,
            "git_message": self.git_message,
            "overall_f1": self.overall_f1,
            "overall_precision": self.overall_precision,
            "overall_recall": self.overall_recall,
            "total_samples": self.total_samples,
            "successful_samples": self.successful_samples,
            "failed_samples": self.failed_samples,
            "per_stem_f1": self.per_stem_f1,
            "per_genre_f1": self.per_genre_f1,
            "execution_time_sec": self.execution_time_sec,
            "worker_count": self.worker_count,
            "notes": self.notes,
            "tags": self.tags,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "BenchmarkRun":
        """Create from dictionary."""
        return cls(
            run_id=d["run_id"],
            timestamp=d["timestamp"],
            manifest_name=d["manifest_name"],
            manifest_version=d["manifest_version"],
            git_commit=d.get("git_commit"),
            git_branch=d.get("git_branch"),
            git_message=d.get("git_message"),
            overall_f1=d.get("overall_f1", 0.0),
            overall_precision=d.get("overall_precision", 0.0),
            overall_recall=d.get("overall_recall", 0.0),
            total_samples=d.get("total_samples", 0),
            successful_samples=d.get("successful_samples", 0),
            failed_samples=d.get("failed_samples", 0),
            per_stem_f1=d.get("per_stem_f1", {}),
            per_genre_f1=d.get("per_genre_f1", {}),
            execution_time_sec=d.get("execution_time_sec", 0.0),
            worker_count=d.get("worker_count", 1),
            notes=d.get("notes", ""),
            tags=d.get("tags", []),
        )

    @classmethod
    def from_benchmark_result(
        cls,
        result: "BenchmarkRunResult",
        notes: str = "",
        tags: Optional[List[str]] = None,
    ) -> "BenchmarkRun":
        """Create from a BenchmarkRunResult.

        Args:
            result: The benchmark run result
            notes: Optional notes about this run
            tags: Optional tags for categorization
        """
        from .parallel_runner import BenchmarkRunResult

        # Generate run ID
        run_id = f"{result.manifest_name}_{result.run_timestamp}".replace(":", "-").replace(".", "-")

        # Get git message if commit available
        git_message = None
        if result.git_commit:
            git_message = _get_git_commit_message(result.git_commit)

        return cls(
            run_id=run_id,
            timestamp=result.run_timestamp,
            manifest_name=result.manifest_name,
            manifest_version=result.manifest_version,
            git_commit=result.git_commit,
            git_branch=result.git_branch,
            git_message=git_message,
            overall_f1=result.aggregate_metrics.overall_f1,
            overall_precision=result.aggregate_metrics.overall_precision,
            overall_recall=result.aggregate_metrics.overall_recall,
            total_samples=result.aggregate_metrics.total_samples,
            successful_samples=result.aggregate_metrics.successful_samples,
            failed_samples=result.aggregate_metrics.failed_samples,
            per_stem_f1=dict(result.aggregate_metrics.per_stem_f1),
            per_genre_f1=dict(result.aggregate_metrics.per_genre_f1),
            execution_time_sec=result.aggregate_metrics.total_execution_time_sec,
            worker_count=result.config.max_workers,
            notes=notes,
            tags=tags or [],
        )


@dataclass
class RegressionAlert:
    """Alert for a detected regression."""

    metric_name: str
    current_value: float
    baseline_value: float
    delta: float
    delta_percent: float
    severity: str  # "warning" or "critical"
    run_id: str
    baseline_run_id: str

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "metric_name": self.metric_name,
            "current_value": self.current_value,
            "baseline_value": self.baseline_value,
            "delta": self.delta,
            "delta_percent": self.delta_percent,
            "severity": self.severity,
            "run_id": self.run_id,
            "baseline_run_id": self.baseline_run_id,
        }

    def __str__(self) -> str:
        """Human-readable string."""
        return (
            f"[{self.severity.upper()}] {self.metric_name}: "
            f"{self.baseline_value:.1%} -> {self.current_value:.1%} "
            f"({self.delta_percent:+.1%})"
        )


@dataclass
class RegressionReport:
    """Summary of regression detection."""

    has_regressions: bool
    alerts: List[RegressionAlert]
    current_run: BenchmarkRun
    baseline_run: BenchmarkRun
    threshold_used: float

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "has_regressions": self.has_regressions,
            "alerts": [a.to_dict() for a in self.alerts],
            "current_run_id": self.current_run.run_id,
            "baseline_run_id": self.baseline_run.run_id,
            "threshold_used": self.threshold_used,
        }

    def summary(self) -> str:
        """Generate human-readable summary."""
        if not self.has_regressions:
            return (
                f"No regressions detected.\n"
                f"Current: {self.current_run.overall_f1:.1%} F1\n"
                f"Baseline: {self.baseline_run.overall_f1:.1%} F1"
            )

        lines = [
            f"REGRESSION DETECTED ({len(self.alerts)} alerts)",
            "=" * 50,
            "",
        ]

        critical_alerts = [a for a in self.alerts if a.severity == "critical"]
        warning_alerts = [a for a in self.alerts if a.severity == "warning"]

        if critical_alerts:
            lines.append("Critical Regressions:")
            for alert in critical_alerts:
                lines.append(f"  {alert}")
            lines.append("")

        if warning_alerts:
            lines.append("Warnings:")
            for alert in warning_alerts:
                lines.append(f"  {alert}")
            lines.append("")

        lines.extend([
            f"Current run:  {self.current_run.run_id}",
            f"Baseline run: {self.baseline_run.run_id}",
            f"Threshold:    {self.threshold_used:.1%}",
        ])

        return "\n".join(lines)


class BenchmarkHistory:
    """Manages benchmark history with regression detection.

    Features:
    - JSON-based persistent storage
    - Git commit association
    - Configurable regression thresholds
    - Timeline export for trend visualization
    """

    def __init__(
        self,
        storage_path: Optional[Path] = None,
        regression_threshold: float = 0.02,
        critical_threshold: float = 0.05,
    ):
        """Initialize the history tracker.

        Args:
            storage_path: Path to JSON history file (default: ~/.toneforge/benchmark_history.json)
            regression_threshold: F1 drop threshold for warnings (default: 2%)
            critical_threshold: F1 drop threshold for critical alerts (default: 5%)
        """
        if storage_path is None:
            storage_path = Path.home() / ".toneforge" / "benchmark_history.json"

        self.storage_path = Path(storage_path)
        self.regression_threshold = regression_threshold
        self.critical_threshold = critical_threshold

        self._runs: List[BenchmarkRun] = []
        self._load()

    def _load(self) -> None:
        """Load history from storage."""
        if self.storage_path.exists():
            try:
                with open(self.storage_path, "r") as f:
                    data = json.load(f)
                    self._runs = [
                        BenchmarkRun.from_dict(r)
                        for r in data.get("runs", [])
                    ]
                logger.info(f"Loaded {len(self._runs)} historical runs")
            except Exception as e:
                logger.warning(f"Failed to load history: {e}")
                self._runs = []
        else:
            self._runs = []

    def _save(self) -> None:
        """Save history to storage."""
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "version": "1.0",
            "runs": [r.to_dict() for r in self._runs],
            "last_updated": datetime.now().isoformat(),
        }

        with open(self.storage_path, "w") as f:
            json.dump(data, f, indent=2)

        logger.info(f"Saved {len(self._runs)} runs to {self.storage_path}")

    def add_run(
        self,
        run: BenchmarkRun,
        save: bool = True,
    ) -> None:
        """Add a benchmark run to history.

        Args:
            run: The benchmark run to add
            save: Whether to save immediately
        """
        # Check for duplicate
        if any(r.run_id == run.run_id for r in self._runs):
            logger.warning(f"Run {run.run_id} already exists, updating")
            self._runs = [r for r in self._runs if r.run_id != run.run_id]

        self._runs.append(run)

        # Sort by timestamp
        self._runs.sort(key=lambda r: r.timestamp)

        if save:
            self._save()

    def get_run(self, run_id: str) -> Optional[BenchmarkRun]:
        """Get a specific run by ID."""
        for run in self._runs:
            if run.run_id == run_id:
                return run
        return None

    def get_runs(
        self,
        manifest_name: Optional[str] = None,
        branch: Optional[str] = None,
        limit: Optional[int] = None,
        since: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> List[BenchmarkRun]:
        """Get runs matching filters.

        Args:
            manifest_name: Filter by manifest name
            branch: Filter by git branch
            limit: Maximum number of runs to return
            since: ISO timestamp to filter runs after
            tags: Filter by tags (any match)

        Returns:
            List of matching runs, newest first
        """
        runs = self._runs.copy()

        if manifest_name:
            runs = [r for r in runs if r.manifest_name == manifest_name]

        if branch:
            runs = [r for r in runs if r.git_branch == branch]

        if since:
            runs = [r for r in runs if r.timestamp >= since]

        if tags:
            runs = [r for r in runs if any(t in r.tags for t in tags)]

        # Sort newest first
        runs.sort(key=lambda r: r.timestamp, reverse=True)

        if limit:
            runs = runs[:limit]

        return runs

    def get_latest(
        self,
        manifest_name: Optional[str] = None,
        branch: Optional[str] = None,
    ) -> Optional[BenchmarkRun]:
        """Get the most recent run matching filters."""
        runs = self.get_runs(manifest_name=manifest_name, branch=branch, limit=1)
        return runs[0] if runs else None

    def get_baseline(
        self,
        manifest_name: str,
        branch: Optional[str] = None,
    ) -> Optional[BenchmarkRun]:
        """Get the baseline run for comparison.

        The baseline is the most recent run on the main/master branch,
        or the most recent run with the "baseline" tag.

        Args:
            manifest_name: Manifest to find baseline for
            branch: Specific branch (default: main/master)

        Returns:
            Baseline run or None
        """
        # First try tagged baseline
        tagged = self.get_runs(
            manifest_name=manifest_name,
            tags=["baseline"],
            limit=1,
        )
        if tagged:
            return tagged[0]

        # Try main/master branch
        for branch_name in [branch, "main", "master"]:
            if branch_name:
                runs = self.get_runs(
                    manifest_name=manifest_name,
                    branch=branch_name,
                    limit=1,
                )
                if runs:
                    return runs[0]

        # Fall back to most recent
        return self.get_latest(manifest_name=manifest_name)

    def detect_regression(
        self,
        current: BenchmarkRun,
        baseline: Optional[BenchmarkRun] = None,
    ) -> RegressionReport:
        """Detect regressions compared to baseline.

        Args:
            current: Current benchmark run
            baseline: Baseline run (auto-detected if None)

        Returns:
            RegressionReport with any detected regressions
        """
        if baseline is None:
            baseline = self.get_baseline(current.manifest_name)
            if baseline is None:
                return RegressionReport(
                    has_regressions=False,
                    alerts=[],
                    current_run=current,
                    baseline_run=current,  # Compare to self
                    threshold_used=self.regression_threshold,
                )

        alerts: List[RegressionAlert] = []

        # Check overall F1
        delta = current.overall_f1 - baseline.overall_f1
        if delta < -self.regression_threshold:
            severity = "critical" if delta < -self.critical_threshold else "warning"
            alerts.append(RegressionAlert(
                metric_name="overall_f1",
                current_value=current.overall_f1,
                baseline_value=baseline.overall_f1,
                delta=delta,
                delta_percent=delta,
                severity=severity,
                run_id=current.run_id,
                baseline_run_id=baseline.run_id,
            ))

        # Check per-stem F1
        for stem in set(current.per_stem_f1.keys()) | set(baseline.per_stem_f1.keys()):
            current_f1 = current.per_stem_f1.get(stem, 0)
            baseline_f1 = baseline.per_stem_f1.get(stem, 0)

            if baseline_f1 > 0:  # Only check if baseline had this stem
                delta = current_f1 - baseline_f1
                if delta < -self.regression_threshold:
                    severity = "critical" if delta < -self.critical_threshold else "warning"
                    alerts.append(RegressionAlert(
                        metric_name=f"stem_{stem}_f1",
                        current_value=current_f1,
                        baseline_value=baseline_f1,
                        delta=delta,
                        delta_percent=delta,
                        severity=severity,
                        run_id=current.run_id,
                        baseline_run_id=baseline.run_id,
                    ))

        # Check per-genre F1
        for genre in set(current.per_genre_f1.keys()) | set(baseline.per_genre_f1.keys()):
            current_f1 = current.per_genre_f1.get(genre, 0)
            baseline_f1 = baseline.per_genre_f1.get(genre, 0)

            if baseline_f1 > 0:
                delta = current_f1 - baseline_f1
                if delta < -self.regression_threshold:
                    severity = "critical" if delta < -self.critical_threshold else "warning"
                    alerts.append(RegressionAlert(
                        metric_name=f"genre_{genre}_f1",
                        current_value=current_f1,
                        baseline_value=baseline_f1,
                        delta=delta,
                        delta_percent=delta,
                        severity=severity,
                        run_id=current.run_id,
                        baseline_run_id=baseline.run_id,
                    ))

        return RegressionReport(
            has_regressions=len(alerts) > 0,
            alerts=alerts,
            current_run=current,
            baseline_run=baseline,
            threshold_used=self.regression_threshold,
        )

    def export_timeline(
        self,
        manifest_name: Optional[str] = None,
        metric: str = "overall_f1",
        output_path: Optional[Path] = None,
    ) -> List[Dict[str, Any]]:
        """Export timeline data for trend visualization.

        Args:
            manifest_name: Filter by manifest name
            metric: Metric to track (overall_f1, per_stem_f1.bass, etc.)
            output_path: Optional path to save CSV

        Returns:
            List of timeline data points
        """
        runs = self.get_runs(manifest_name=manifest_name)
        runs.sort(key=lambda r: r.timestamp)

        timeline = []
        for run in runs:
            # Extract the metric value
            if metric == "overall_f1":
                value = run.overall_f1
            elif metric == "overall_precision":
                value = run.overall_precision
            elif metric == "overall_recall":
                value = run.overall_recall
            elif metric.startswith("per_stem_f1."):
                stem = metric.split(".", 1)[1]
                value = run.per_stem_f1.get(stem)
            elif metric.startswith("per_genre_f1."):
                genre = metric.split(".", 1)[1]
                value = run.per_genre_f1.get(genre)
            else:
                value = None

            if value is not None:
                timeline.append({
                    "timestamp": run.timestamp,
                    "run_id": run.run_id,
                    "git_commit": run.git_commit,
                    "git_branch": run.git_branch,
                    "value": value,
                    "metric": metric,
                })

        # Save to CSV if requested
        if output_path:
            import csv
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)

            with open(output_path, "w", newline="") as f:
                if timeline:
                    writer = csv.DictWriter(f, fieldnames=timeline[0].keys())
                    writer.writeheader()
                    writer.writerows(timeline)

            logger.info(f"Exported timeline to {output_path}")

        return timeline

    def get_commit_comparison(
        self,
        commit_a: str,
        commit_b: str,
        manifest_name: Optional[str] = None,
    ) -> Optional[Dict[str, float]]:
        """Compare metrics between two git commits.

        Args:
            commit_a: First commit (baseline)
            commit_b: Second commit (comparison)
            manifest_name: Filter by manifest

        Returns:
            Dictionary of metric deltas or None if commits not found
        """
        run_a = None
        run_b = None

        for run in self._runs:
            if manifest_name and run.manifest_name != manifest_name:
                continue
            if run.git_commit:
                if run.git_commit.startswith(commit_a):
                    run_a = run
                elif run.git_commit.startswith(commit_b):
                    run_b = run

        if not run_a or not run_b:
            return None

        return {
            "overall_f1": run_b.overall_f1 - run_a.overall_f1,
            "overall_precision": run_b.overall_precision - run_a.overall_precision,
            "overall_recall": run_b.overall_recall - run_a.overall_recall,
            **{
                f"stem_{stem}": run_b.per_stem_f1.get(stem, 0) - run_a.per_stem_f1.get(stem, 0)
                for stem in set(run_a.per_stem_f1.keys()) | set(run_b.per_stem_f1.keys())
            },
        }

    def set_baseline(
        self,
        run_id: str,
        save: bool = True,
    ) -> bool:
        """Mark a run as the baseline.

        Args:
            run_id: Run ID to mark as baseline
            save: Whether to save immediately

        Returns:
            True if successful
        """
        # Remove baseline tag from all runs
        for run in self._runs:
            if "baseline" in run.tags:
                run.tags.remove("baseline")

        # Add baseline tag to specified run
        for run in self._runs:
            if run.run_id == run_id:
                run.tags.append("baseline")
                if save:
                    self._save()
                return True

        return False

    def delete_run(
        self,
        run_id: str,
        save: bool = True,
    ) -> bool:
        """Delete a run from history.

        Args:
            run_id: Run ID to delete
            save: Whether to save immediately

        Returns:
            True if deleted
        """
        original_count = len(self._runs)
        self._runs = [r for r in self._runs if r.run_id != run_id]

        if len(self._runs) < original_count:
            if save:
                self._save()
            return True
        return False

    def prune_old_runs(
        self,
        keep_count: int = 100,
        keep_days: Optional[int] = None,
        save: bool = True,
    ) -> int:
        """Prune old runs to manage storage.

        Args:
            keep_count: Minimum number of runs to keep
            keep_days: Keep runs from last N days (optional)
            save: Whether to save immediately

        Returns:
            Number of runs pruned
        """
        if keep_days:
            from datetime import timedelta
            cutoff = (datetime.now() - timedelta(days=keep_days)).isoformat()
            self._runs = [r for r in self._runs if r.timestamp >= cutoff]

        # Always keep baseline runs
        baseline_runs = [r for r in self._runs if "baseline" in r.tags]
        other_runs = [r for r in self._runs if "baseline" not in r.tags]

        # Sort by timestamp, keep newest
        other_runs.sort(key=lambda r: r.timestamp, reverse=True)

        original_count = len(self._runs)

        # Keep baseline + most recent other runs
        to_keep = keep_count - len(baseline_runs)
        if to_keep > 0:
            self._runs = baseline_runs + other_runs[:to_keep]
        else:
            self._runs = baseline_runs

        pruned = original_count - len(self._runs)

        if pruned > 0 and save:
            self._save()

        return pruned

    def summary(self) -> str:
        """Generate summary of history."""
        lines = [
            f"Benchmark History ({len(self._runs)} runs)",
            "=" * 50,
        ]

        if not self._runs:
            lines.append("No runs recorded.")
            return "\n".join(lines)

        # Group by manifest
        manifests: Dict[str, List[BenchmarkRun]] = {}
        for run in self._runs:
            if run.manifest_name not in manifests:
                manifests[run.manifest_name] = []
            manifests[run.manifest_name].append(run)

        for manifest, runs in manifests.items():
            runs.sort(key=lambda r: r.timestamp, reverse=True)
            latest = runs[0]
            baseline = next((r for r in runs if "baseline" in r.tags), None)

            lines.extend([
                "",
                f"Manifest: {manifest}",
                f"  Runs: {len(runs)}",
                f"  Latest: {latest.overall_f1:.1%} F1 ({latest.timestamp[:10]})",
            ])

            if baseline:
                lines.append(f"  Baseline: {baseline.overall_f1:.1%} F1 ({baseline.timestamp[:10]})")

        return "\n".join(lines)


def _get_git_commit_message(commit: str) -> Optional[str]:
    """Get commit message for a git commit."""
    try:
        message = subprocess.check_output(
            ["git", "log", "-1", "--pretty=%s", commit],
            stderr=subprocess.DEVNULL
        ).decode().strip()
        return message
    except Exception:
        return None
