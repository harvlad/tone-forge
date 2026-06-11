"""Parallel benchmark runner for multi-genre MIDI evaluation.

Provides process-pool based parallel execution with:
- Configurable worker count
- Per-sample timeout handling
- Progress callbacks
- A/B comparison support
- CSV/JSON output formats
"""
from __future__ import annotations

import csv
import json
import logging
import os
import time
from concurrent.futures import ProcessPoolExecutor, TimeoutError as FuturesTimeoutError
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import numpy as np

from .dataset_manifest import DatasetManifest, BenchmarkSample

logger = logging.getLogger(__name__)


@dataclass
class ParallelConfig:
    """Configuration for parallel benchmark execution."""

    # Worker configuration
    max_workers: int = 4
    per_sample_timeout_sec: float = 120.0

    # Output configuration
    output_dir: Optional[Path] = None
    save_csv: bool = True
    save_json: bool = True

    # Extraction configuration
    use_auto_classify: bool = True
    default_profile: Optional[str] = None

    # Progress
    progress_interval: int = 10  # Report progress every N samples

    # Error handling
    continue_on_error: bool = True
    max_consecutive_failures: int = 10

    def __post_init__(self):
        if self.output_dir is not None:
            self.output_dir = Path(self.output_dir)


@dataclass
class SampleEvaluationResult:
    """Result from evaluating a single benchmark sample."""

    sample_id: str
    success: bool

    # MIDI metrics
    f1: float = 0.0
    precision: float = 0.0
    recall: float = 0.0

    # Note counts
    extracted_note_count: int = 0
    ground_truth_note_count: int = 0
    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0

    # Metadata
    stem_type: str = ""
    genre: str = ""
    profile_used: Optional[str] = None
    profile_auto_classified: bool = False
    difficulty: str = "medium"

    # Timing
    execution_time_ms: float = 0.0
    timed_out: bool = False

    # Error info
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "sample_id": self.sample_id,
            "success": self.success,
            "f1": self.f1,
            "precision": self.precision,
            "recall": self.recall,
            "extracted_note_count": self.extracted_note_count,
            "ground_truth_note_count": self.ground_truth_note_count,
            "true_positives": self.true_positives,
            "false_positives": self.false_positives,
            "false_negatives": self.false_negatives,
            "stem_type": self.stem_type,
            "genre": self.genre,
            "profile_used": self.profile_used,
            "profile_auto_classified": self.profile_auto_classified,
            "difficulty": self.difficulty,
            "execution_time_ms": self.execution_time_ms,
            "timed_out": self.timed_out,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SampleEvaluationResult":
        """Create from dictionary."""
        return cls(**d)


@dataclass
class AggregateMetrics:
    """Aggregated metrics across benchmark samples."""

    # Overall metrics
    overall_f1: float = 0.0
    overall_precision: float = 0.0
    overall_recall: float = 0.0

    # Sample counts
    total_samples: int = 0
    successful_samples: int = 0
    failed_samples: int = 0
    timed_out_samples: int = 0

    # Per-stem breakdown
    per_stem_f1: Dict[str, float] = field(default_factory=dict)
    per_stem_precision: Dict[str, float] = field(default_factory=dict)
    per_stem_recall: Dict[str, float] = field(default_factory=dict)
    per_stem_count: Dict[str, int] = field(default_factory=dict)

    # Per-genre breakdown
    per_genre_f1: Dict[str, float] = field(default_factory=dict)
    per_genre_precision: Dict[str, float] = field(default_factory=dict)
    per_genre_recall: Dict[str, float] = field(default_factory=dict)
    per_genre_count: Dict[str, int] = field(default_factory=dict)

    # Per-difficulty breakdown
    per_difficulty_f1: Dict[str, float] = field(default_factory=dict)
    per_difficulty_count: Dict[str, int] = field(default_factory=dict)

    # Timing
    total_execution_time_sec: float = 0.0
    avg_sample_time_ms: float = 0.0

    # Worst performers
    worst_samples: List[Tuple[str, float]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "overall": {
                "f1": self.overall_f1,
                "precision": self.overall_precision,
                "recall": self.overall_recall,
            },
            "sample_counts": {
                "total": self.total_samples,
                "successful": self.successful_samples,
                "failed": self.failed_samples,
                "timed_out": self.timed_out_samples,
            },
            "per_stem": {
                "f1": self.per_stem_f1,
                "precision": self.per_stem_precision,
                "recall": self.per_stem_recall,
                "count": self.per_stem_count,
            },
            "per_genre": {
                "f1": self.per_genre_f1,
                "precision": self.per_genre_precision,
                "recall": self.per_genre_recall,
                "count": self.per_genre_count,
            },
            "per_difficulty": {
                "f1": self.per_difficulty_f1,
                "count": self.per_difficulty_count,
            },
            "timing": {
                "total_sec": self.total_execution_time_sec,
                "avg_sample_ms": self.avg_sample_time_ms,
            },
            "worst_samples": self.worst_samples,
        }

    def summary(self) -> str:
        """Generate human-readable summary."""
        lines = [
            f"Benchmark Results ({self.successful_samples}/{self.total_samples} successful)",
            "=" * 60,
            "",
            "Overall Metrics:",
            f"  F1:        {self.overall_f1:.1%}",
            f"  Precision: {self.overall_precision:.1%}",
            f"  Recall:    {self.overall_recall:.1%}",
        ]

        if self.per_stem_f1:
            lines.extend(["", "Per-Stem F1:"])
            for stem in sorted(self.per_stem_f1.keys()):
                f1 = self.per_stem_f1[stem]
                count = self.per_stem_count.get(stem, 0)
                lines.append(f"  {stem:15s} {f1:.1%} (n={count})")

        if self.per_genre_f1:
            lines.extend(["", "Per-Genre F1:"])
            for genre in sorted(self.per_genre_f1.keys()):
                f1 = self.per_genre_f1[genre]
                count = self.per_genre_count.get(genre, 0)
                lines.append(f"  {genre:15s} {f1:.1%} (n={count})")

        if self.per_difficulty_f1:
            lines.extend(["", "Per-Difficulty F1:"])
            for diff in ["easy", "medium", "hard", "extreme"]:
                if diff in self.per_difficulty_f1:
                    f1 = self.per_difficulty_f1[diff]
                    count = self.per_difficulty_count.get(diff, 0)
                    lines.append(f"  {diff:15s} {f1:.1%} (n={count})")

        if self.worst_samples:
            lines.extend(["", "Worst Performers:"])
            for sample_id, f1 in self.worst_samples[:5]:
                lines.append(f"  {sample_id}: {f1:.1%}")

        if self.failed_samples > 0:
            lines.extend(["", f"Failed: {self.failed_samples}, Timed out: {self.timed_out_samples}"])

        lines.extend([
            "",
            f"Total time: {self.total_execution_time_sec:.1f}s",
            f"Avg per sample: {self.avg_sample_time_ms:.0f}ms",
        ])

        return "\n".join(lines)


@dataclass
class BenchmarkRunResult:
    """Complete result from a benchmark run."""

    manifest_name: str
    manifest_version: str
    run_timestamp: str
    config: ParallelConfig

    # Results
    aggregate_metrics: AggregateMetrics
    sample_results: List[SampleEvaluationResult]

    # Git info (if available)
    git_commit: Optional[str] = None
    git_branch: Optional[str] = None

    # Comparison
    baseline_comparison: Optional[Dict[str, float]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "manifest_name": self.manifest_name,
            "manifest_version": self.manifest_version,
            "run_timestamp": self.run_timestamp,
            "config": {
                "max_workers": self.config.max_workers,
                "per_sample_timeout_sec": self.config.per_sample_timeout_sec,
                "use_auto_classify": self.config.use_auto_classify,
                "default_profile": self.config.default_profile,
            },
            "aggregate_metrics": self.aggregate_metrics.to_dict(),
            "sample_results": [r.to_dict() for r in self.sample_results],
            "git_commit": self.git_commit,
            "git_branch": self.git_branch,
            "baseline_comparison": self.baseline_comparison,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "BenchmarkRunResult":
        """Create from dictionary."""
        config = ParallelConfig(
            max_workers=d["config"]["max_workers"],
            per_sample_timeout_sec=d["config"]["per_sample_timeout_sec"],
            use_auto_classify=d["config"]["use_auto_classify"],
            default_profile=d["config"]["default_profile"],
        )

        aggregate = AggregateMetrics(**d["aggregate_metrics"]["overall"])
        aggregate.total_samples = d["aggregate_metrics"]["sample_counts"]["total"]
        aggregate.successful_samples = d["aggregate_metrics"]["sample_counts"]["successful"]
        aggregate.failed_samples = d["aggregate_metrics"]["sample_counts"]["failed"]
        aggregate.timed_out_samples = d["aggregate_metrics"]["sample_counts"]["timed_out"]

        sample_results = [
            SampleEvaluationResult.from_dict(r)
            for r in d.get("sample_results", [])
        ]

        return cls(
            manifest_name=d["manifest_name"],
            manifest_version=d["manifest_version"],
            run_timestamp=d["run_timestamp"],
            config=config,
            aggregate_metrics=aggregate,
            sample_results=sample_results,
            git_commit=d.get("git_commit"),
            git_branch=d.get("git_branch"),
            baseline_comparison=d.get("baseline_comparison"),
        )


def _evaluate_sample_worker(args: Tuple[Dict, Dict]) -> Dict[str, Any]:
    """Worker function for parallel sample evaluation.

    Args:
        args: Tuple of (sample_dict, config_dict)

    Returns:
        Result dictionary
    """
    sample_dict, config_dict = args
    sample_id = sample_dict["id"]
    start_time = time.time()

    try:
        # Import inside worker to avoid pickling issues
        import librosa

        from tone_forge.midi import MultiPassExtractor, get_profile, classify_profile
        from tone_forge.evaluation.metrics import compute_midi_quality

        # Load audio
        audio_path = sample_dict["audio_path"]
        audio, sr = librosa.load(audio_path, sr=22050, mono=True)

        # Load ground truth
        gt_midi_path = sample_dict.get("ground_truth_midi_path")
        if not gt_midi_path:
            return {
                "sample_id": sample_id,
                "success": False,
                "error": "No ground truth MIDI path",
            }

        ground_truth_notes = _load_midi_notes_static(gt_midi_path)
        if not ground_truth_notes:
            return {
                "sample_id": sample_id,
                "success": False,
                "error": "Failed to load ground truth MIDI",
            }

        # Create extractor
        extractor = MultiPassExtractor()

        # Determine profile
        stem_type = sample_dict.get("stem_type", "other")
        genre = sample_dict.get("genre", "")
        profile = None
        profile_name = None
        auto_classified = False

        if sample_dict.get("profile_hint"):
            profile = get_profile(sample_dict["profile_hint"])
            profile_name = sample_dict["profile_hint"]
        elif config_dict.get("default_profile"):
            profile = get_profile(config_dict["default_profile"])
            profile_name = config_dict["default_profile"]
        elif config_dict.get("use_auto_classify", True):
            classification = classify_profile(audio, sr, stem_type)
            profile = get_profile(classification.profile_name)
            profile_name = classification.profile_name
            auto_classified = True

        # Extract MIDI
        result = extractor.extract(
            audio, sr,
            stem_type=stem_type,
            genre=genre,
            profile=profile,
            auto_classify=not sample_dict.get("profile_hint") and config_dict.get("use_auto_classify", True),
        )

        # Convert notes to comparison format
        extracted_tuples = [
            (n.pitch, n.start, n.end, n.velocity)
            for n in result.notes
        ]

        # Compute metrics
        metrics = compute_midi_quality(extracted_tuples, ground_truth_notes)

        execution_time_ms = (time.time() - start_time) * 1000

        return {
            "sample_id": sample_id,
            "success": True,
            "f1": metrics.note_f1,
            "precision": metrics.note_precision,
            "recall": metrics.note_recall,
            "extracted_note_count": len(result.notes),
            "ground_truth_note_count": len(ground_truth_notes),
            "true_positives": metrics.true_positives,
            "false_positives": metrics.false_positives,
            "false_negatives": metrics.false_negatives,
            "stem_type": stem_type,
            "genre": genre,
            "profile_used": profile_name,
            "profile_auto_classified": auto_classified,
            "difficulty": sample_dict.get("difficulty", "medium"),
            "execution_time_ms": execution_time_ms,
            "timed_out": False,
            "error": None,
        }

    except Exception as e:
        execution_time_ms = (time.time() - start_time) * 1000
        logger.error(f"Worker error for {sample_id}: {e}")
        return {
            "sample_id": sample_id,
            "success": False,
            "error": str(e),
            "execution_time_ms": execution_time_ms,
            "stem_type": sample_dict.get("stem_type", ""),
            "genre": sample_dict.get("genre", ""),
            "difficulty": sample_dict.get("difficulty", "medium"),
        }


def _load_midi_notes_static(midi_path: str) -> Optional[List[Tuple[int, float, float, int]]]:
    """Static function to load MIDI notes (for worker processes)."""
    try:
        import mido

        mid = mido.MidiFile(midi_path)
        notes = []
        active_notes: Dict[int, Tuple[float, int]] = {}

        for track in mid.tracks:
            current_time = 0.0
            for msg in track:
                current_time += mido.tick2second(msg.time, mid.ticks_per_beat, 500000)

                if msg.type == 'note_on' and msg.velocity > 0:
                    active_notes[msg.note] = (current_time, msg.velocity)
                elif msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0):
                    if msg.note in active_notes:
                        start, velocity = active_notes.pop(msg.note)
                        notes.append((msg.note, start, current_time, velocity))

        return notes
    except Exception as e:
        logger.warning(f"Failed to load MIDI {midi_path}: {e}")
        return None


class ParallelBenchmarkRunner:
    """Parallel benchmark runner for multi-genre MIDI evaluation.

    Features:
    - Process pool execution for parallelism
    - Per-sample timeout handling
    - Progress callbacks
    - A/B comparison support
    - CSV/JSON output formats
    """

    def __init__(self, config: Optional[ParallelConfig] = None):
        """Initialize the parallel benchmark runner.

        Args:
            config: Configuration for parallel execution
        """
        self.config = config or ParallelConfig()

    def run(
        self,
        manifest: DatasetManifest,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> BenchmarkRunResult:
        """Run benchmark on manifest in parallel.

        Args:
            manifest: Dataset manifest to evaluate
            progress_callback: Optional callback(current, total, message)

        Returns:
            BenchmarkRunResult with all metrics
        """
        from datetime import datetime

        start_time = time.time()
        run_timestamp = datetime.now().isoformat()

        samples = manifest.samples
        total = len(samples)

        logger.info(f"Starting parallel benchmark: {total} samples, {self.config.max_workers} workers")

        # Prepare worker arguments
        config_dict = {
            "use_auto_classify": self.config.use_auto_classify,
            "default_profile": self.config.default_profile,
        }

        worker_args = [
            (sample.to_dict(), config_dict)
            for sample in samples
        ]

        # Run in parallel
        sample_results: List[SampleEvaluationResult] = []
        consecutive_failures = 0

        with ProcessPoolExecutor(max_workers=self.config.max_workers) as executor:
            futures = {
                executor.submit(_evaluate_sample_worker, args): args[0]["id"]
                for args in worker_args
            }

            completed = 0
            for future in futures:
                sample_id = futures[future]
                try:
                    result_dict = future.result(timeout=self.config.per_sample_timeout_sec)
                    result = SampleEvaluationResult(**{
                        k: v for k, v in result_dict.items()
                        if k in SampleEvaluationResult.__dataclass_fields__
                    })
                    sample_results.append(result)

                    if result.success:
                        consecutive_failures = 0
                    else:
                        consecutive_failures += 1

                except FuturesTimeoutError:
                    logger.warning(f"Sample {sample_id} timed out after {self.config.per_sample_timeout_sec}s")
                    sample_results.append(SampleEvaluationResult(
                        sample_id=sample_id,
                        success=False,
                        timed_out=True,
                        error=f"Timeout after {self.config.per_sample_timeout_sec}s",
                    ))
                    consecutive_failures += 1

                except Exception as e:
                    logger.error(f"Failed to evaluate {sample_id}: {e}")
                    sample_results.append(SampleEvaluationResult(
                        sample_id=sample_id,
                        success=False,
                        error=str(e),
                    ))
                    consecutive_failures += 1

                completed += 1

                # Progress callback
                if progress_callback and completed % self.config.progress_interval == 0:
                    progress_callback(completed, total, f"Completed {completed}/{total}")

                # Check for too many consecutive failures
                if consecutive_failures >= self.config.max_consecutive_failures:
                    if not self.config.continue_on_error:
                        logger.error(f"Stopping: {consecutive_failures} consecutive failures")
                        break

        # Compute aggregate metrics
        aggregate = self._compute_aggregate_metrics(sample_results)
        aggregate.total_execution_time_sec = time.time() - start_time

        # Get git info
        git_commit, git_branch = self._get_git_info()

        result = BenchmarkRunResult(
            manifest_name=manifest.name,
            manifest_version=manifest.version,
            run_timestamp=run_timestamp,
            config=self.config,
            aggregate_metrics=aggregate,
            sample_results=sample_results,
            git_commit=git_commit,
            git_branch=git_branch,
        )

        # Save outputs
        if self.config.output_dir:
            self._save_outputs(result)

        return result

    def run_sequential(
        self,
        manifest: DatasetManifest,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> BenchmarkRunResult:
        """Run benchmark sequentially (for debugging).

        Args:
            manifest: Dataset manifest to evaluate
            progress_callback: Optional callback(current, total, message)

        Returns:
            BenchmarkRunResult with all metrics
        """
        from datetime import datetime

        start_time = time.time()
        run_timestamp = datetime.now().isoformat()

        samples = manifest.samples
        total = len(samples)

        config_dict = {
            "use_auto_classify": self.config.use_auto_classify,
            "default_profile": self.config.default_profile,
        }

        sample_results: List[SampleEvaluationResult] = []

        for i, sample in enumerate(samples):
            if progress_callback:
                progress_callback(i, total, f"Processing {sample.id}")

            result_dict = _evaluate_sample_worker((sample.to_dict(), config_dict))
            result = SampleEvaluationResult(**{
                k: v for k, v in result_dict.items()
                if k in SampleEvaluationResult.__dataclass_fields__
            })
            sample_results.append(result)

        aggregate = self._compute_aggregate_metrics(sample_results)
        aggregate.total_execution_time_sec = time.time() - start_time

        git_commit, git_branch = self._get_git_info()

        return BenchmarkRunResult(
            manifest_name=manifest.name,
            manifest_version=manifest.version,
            run_timestamp=run_timestamp,
            config=self.config,
            aggregate_metrics=aggregate,
            sample_results=sample_results,
            git_commit=git_commit,
            git_branch=git_branch,
        )

    def compare(
        self,
        baseline: BenchmarkRunResult,
        improved: BenchmarkRunResult,
    ) -> Dict[str, float]:
        """Compare two benchmark runs.

        Args:
            baseline: Baseline run
            improved: Improved run

        Returns:
            Dictionary of metric deltas (positive = improvement)
        """
        comparison = {}

        # Overall metrics
        comparison["overall_f1"] = (
            improved.aggregate_metrics.overall_f1 -
            baseline.aggregate_metrics.overall_f1
        )
        comparison["overall_precision"] = (
            improved.aggregate_metrics.overall_precision -
            baseline.aggregate_metrics.overall_precision
        )
        comparison["overall_recall"] = (
            improved.aggregate_metrics.overall_recall -
            baseline.aggregate_metrics.overall_recall
        )

        # Per-stem comparison
        for stem in set(baseline.aggregate_metrics.per_stem_f1.keys()) | set(improved.aggregate_metrics.per_stem_f1.keys()):
            base_f1 = baseline.aggregate_metrics.per_stem_f1.get(stem, 0)
            impr_f1 = improved.aggregate_metrics.per_stem_f1.get(stem, 0)
            comparison[f"stem_{stem}_f1"] = impr_f1 - base_f1

        # Per-genre comparison
        for genre in set(baseline.aggregate_metrics.per_genre_f1.keys()) | set(improved.aggregate_metrics.per_genre_f1.keys()):
            base_f1 = baseline.aggregate_metrics.per_genre_f1.get(genre, 0)
            impr_f1 = improved.aggregate_metrics.per_genre_f1.get(genre, 0)
            comparison[f"genre_{genre}_f1"] = impr_f1 - base_f1

        return comparison

    def _compute_aggregate_metrics(
        self,
        results: List[SampleEvaluationResult],
    ) -> AggregateMetrics:
        """Compute aggregate metrics from sample results."""
        successful = [r for r in results if r.success]

        if not successful:
            return AggregateMetrics(
                total_samples=len(results),
                failed_samples=len(results),
            )

        # Overall metrics
        overall_f1 = np.mean([r.f1 for r in successful])
        overall_precision = np.mean([r.precision for r in successful])
        overall_recall = np.mean([r.recall for r in successful])

        # Per-stem breakdown
        per_stem_f1 = {}
        per_stem_precision = {}
        per_stem_recall = {}
        per_stem_count = {}

        stems = set(r.stem_type for r in successful if r.stem_type)
        for stem in stems:
            stem_results = [r for r in successful if r.stem_type == stem]
            if stem_results:
                per_stem_f1[stem] = np.mean([r.f1 for r in stem_results])
                per_stem_precision[stem] = np.mean([r.precision for r in stem_results])
                per_stem_recall[stem] = np.mean([r.recall for r in stem_results])
                per_stem_count[stem] = len(stem_results)

        # Per-genre breakdown
        per_genre_f1 = {}
        per_genre_precision = {}
        per_genre_recall = {}
        per_genre_count = {}

        genres = set(r.genre for r in successful if r.genre)
        for genre in genres:
            genre_results = [r for r in successful if r.genre == genre]
            if genre_results:
                per_genre_f1[genre] = np.mean([r.f1 for r in genre_results])
                per_genre_precision[genre] = np.mean([r.precision for r in genre_results])
                per_genre_recall[genre] = np.mean([r.recall for r in genre_results])
                per_genre_count[genre] = len(genre_results)

        # Per-difficulty breakdown
        per_difficulty_f1 = {}
        per_difficulty_count = {}

        difficulties = set(r.difficulty for r in successful if r.difficulty)
        for diff in difficulties:
            diff_results = [r for r in successful if r.difficulty == diff]
            if diff_results:
                per_difficulty_f1[diff] = np.mean([r.f1 for r in diff_results])
                per_difficulty_count[diff] = len(diff_results)

        # Timing
        all_times = [r.execution_time_ms for r in results if r.execution_time_ms > 0]
        avg_sample_time = np.mean(all_times) if all_times else 0

        # Worst performers
        sorted_by_f1 = sorted(
            [(r.sample_id, r.f1) for r in successful],
            key=lambda x: x[1]
        )
        worst_samples = sorted_by_f1[:10]

        return AggregateMetrics(
            overall_f1=overall_f1,
            overall_precision=overall_precision,
            overall_recall=overall_recall,
            total_samples=len(results),
            successful_samples=len(successful),
            failed_samples=len([r for r in results if not r.success and not r.timed_out]),
            timed_out_samples=len([r for r in results if r.timed_out]),
            per_stem_f1=per_stem_f1,
            per_stem_precision=per_stem_precision,
            per_stem_recall=per_stem_recall,
            per_stem_count=per_stem_count,
            per_genre_f1=per_genre_f1,
            per_genre_precision=per_genre_precision,
            per_genre_recall=per_genre_recall,
            per_genre_count=per_genre_count,
            per_difficulty_f1=per_difficulty_f1,
            per_difficulty_count=per_difficulty_count,
            avg_sample_time_ms=avg_sample_time,
            worst_samples=worst_samples,
        )

    def _get_git_info(self) -> Tuple[Optional[str], Optional[str]]:
        """Get current git commit and branch."""
        import subprocess

        try:
            commit = subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                stderr=subprocess.DEVNULL
            ).decode().strip()
        except Exception:
            commit = None

        try:
            branch = subprocess.check_output(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                stderr=subprocess.DEVNULL
            ).decode().strip()
        except Exception:
            branch = None

        return commit, branch

    def _save_outputs(self, result: BenchmarkRunResult) -> None:
        """Save benchmark results to configured output directory."""
        output_dir = self.config.output_dir
        output_dir.mkdir(parents=True, exist_ok=True)

        timestamp = result.run_timestamp.replace(":", "-").replace(".", "-")
        base_name = f"benchmark_{result.manifest_name}_{timestamp}"

        # Save JSON
        if self.config.save_json:
            json_path = output_dir / f"{base_name}.json"
            with open(json_path, "w") as f:
                json.dump(result.to_dict(), f, indent=2, default=str)
            logger.info(f"Saved JSON results to {json_path}")

        # Save CSV (per-sample results)
        if self.config.save_csv:
            csv_path = output_dir / f"{base_name}_samples.csv"
            with open(csv_path, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=[
                    "sample_id", "success", "f1", "precision", "recall",
                    "extracted_note_count", "ground_truth_note_count",
                    "true_positives", "false_positives", "false_negatives",
                    "stem_type", "genre", "profile_used", "profile_auto_classified",
                    "difficulty", "execution_time_ms", "timed_out", "error"
                ])
                writer.writeheader()
                for r in result.sample_results:
                    writer.writerow(r.to_dict())
            logger.info(f"Saved CSV samples to {csv_path}")

            # Save aggregate CSV
            agg_csv_path = output_dir / f"{base_name}_aggregate.csv"
            with open(agg_csv_path, "w", newline="") as f:
                writer = csv.writer(f)

                # Overall metrics
                writer.writerow(["Category", "Metric", "Value"])
                writer.writerow(["overall", "f1", f"{result.aggregate_metrics.overall_f1:.4f}"])
                writer.writerow(["overall", "precision", f"{result.aggregate_metrics.overall_precision:.4f}"])
                writer.writerow(["overall", "recall", f"{result.aggregate_metrics.overall_recall:.4f}"])

                # Per-stem
                for stem, f1 in result.aggregate_metrics.per_stem_f1.items():
                    writer.writerow([f"stem:{stem}", "f1", f"{f1:.4f}"])
                    writer.writerow([f"stem:{stem}", "count", result.aggregate_metrics.per_stem_count.get(stem, 0)])

                # Per-genre
                for genre, f1 in result.aggregate_metrics.per_genre_f1.items():
                    writer.writerow([f"genre:{genre}", "f1", f"{f1:.4f}"])
                    writer.writerow([f"genre:{genre}", "count", result.aggregate_metrics.per_genre_count.get(genre, 0)])

            logger.info(f"Saved aggregate CSV to {agg_csv_path}")
