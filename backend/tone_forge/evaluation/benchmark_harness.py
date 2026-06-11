"""Benchmark harness for MIDI extraction evaluation.

Provides comprehensive benchmarking infrastructure with:
- CSV export for all metrics
- Per-stem, per-profile, per-genre breakdowns
- Visualization support
- A/B comparison
- Regression tracking
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np


@dataclass
class NoteMetrics:
    """Metrics for a single note comparison."""
    pitch_correct: bool
    onset_error_ms: float
    offset_error_ms: float
    velocity_error: int
    matched: bool


@dataclass
class SampleMetrics:
    """Metrics for a single audio sample."""
    sample_id: str
    audio_path: str
    ground_truth_path: str
    stem_type: str
    pipeline_used: str
    profile_used: Optional[str]

    # Core metrics
    precision: float
    recall: float
    f1: float

    # Note counts
    extracted_count: int
    ground_truth_count: int
    true_positives: int
    false_positives: int
    false_negatives: int

    # Timing metrics
    avg_onset_error_ms: float
    max_onset_error_ms: float
    onset_std_ms: float

    # Pitch metrics
    pitch_accuracy: float  # Of matched notes
    octave_error_rate: float

    # Duration
    extraction_time_ms: float

    # Additional
    warnings: List[str] = field(default_factory=list)
    error: Optional[str] = None


@dataclass
class BenchmarkRun:
    """Complete benchmark run results."""
    run_id: str
    timestamp: str
    config: Dict[str, Any]

    # Overall metrics
    overall_precision: float
    overall_recall: float
    overall_f1: float

    # Per-category breakdowns
    per_stem_f1: Dict[str, float]
    per_stem_precision: Dict[str, float]
    per_stem_recall: Dict[str, float]

    per_pipeline_f1: Dict[str, float]
    per_pipeline_precision: Dict[str, float]
    per_pipeline_recall: Dict[str, float]

    per_genre_f1: Dict[str, float]

    # Individual samples
    sample_metrics: List[SampleMetrics]

    # Timing
    total_time_seconds: float

    # Comparison with baseline (if available)
    baseline_comparison: Optional[Dict[str, float]] = None

    def summary(self) -> str:
        """Generate human-readable summary."""
        lines = [
            f"Benchmark Run: {self.run_id}",
            f"Timestamp: {self.timestamp}",
            f"Samples: {len(self.sample_metrics)}",
            "",
            f"Overall: P={self.overall_precision:.1%} R={self.overall_recall:.1%} F1={self.overall_f1:.1%}",
            "",
            "Per Stem Type:",
        ]

        for stem, f1 in sorted(self.per_stem_f1.items()):
            prec = self.per_stem_precision.get(stem, 0)
            rec = self.per_stem_recall.get(stem, 0)
            lines.append(f"  {stem:12s}: P={prec:.1%} R={rec:.1%} F1={f1:.1%}")

        if self.per_pipeline_f1:
            lines.append("")
            lines.append("Per Pipeline:")
            for pipe, f1 in sorted(self.per_pipeline_f1.items()):
                lines.append(f"  {pipe:15s}: F1={f1:.1%}")

        if self.baseline_comparison:
            lines.append("")
            lines.append("Regression from Baseline:")
            for metric, delta in self.baseline_comparison.items():
                sign = "+" if delta >= 0 else ""
                lines.append(f"  {metric}: {sign}{delta:.1%}")

        return "\n".join(lines)


class BenchmarkHarness:
    """Harness for running and tracking MIDI extraction benchmarks.

    Usage:
        harness = BenchmarkHarness(output_dir="benchmarks/")
        run = harness.run_benchmark(samples, config)
        harness.export_csv(run)
        harness.plot_results(run)
    """

    def __init__(
        self,
        output_dir: Path | str = "benchmarks",
        baseline_path: Optional[Path | str] = None,
    ):
        """Initialize harness.

        Args:
            output_dir: Directory for benchmark outputs
            baseline_path: Path to baseline metrics for comparison
        """
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.baseline = None
        if baseline_path:
            baseline_path = Path(baseline_path)
            if baseline_path.exists():
                with open(baseline_path) as f:
                    self.baseline = json.load(f)

    def run_benchmark(
        self,
        samples: List[Dict[str, Any]],
        config: Dict[str, Any] = None,
        use_pipelines: bool = True,
    ) -> BenchmarkRun:
        """Run benchmark on a set of samples.

        Args:
            samples: List of dicts with 'audio_path', 'ground_truth_path', 'stem_type', etc.
            config: Configuration dict
            use_pipelines: Whether to use stem-specific pipelines

        Returns:
            BenchmarkRun with results
        """
        import time
        from tone_forge.midi.pipelines import get_pipeline_for_stem

        start_time = time.time()
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        timestamp = datetime.now().isoformat()

        sample_metrics = []

        for sample in samples:
            audio_path = Path(sample["audio_path"])
            gt_path = Path(sample.get("ground_truth_path", ""))
            stem_type = sample.get("stem_type", "other")
            genre = sample.get("genre")

            try:
                metrics = self._evaluate_sample(
                    audio_path=audio_path,
                    ground_truth_path=gt_path,
                    stem_type=stem_type,
                    use_pipeline=use_pipelines,
                )
                sample_metrics.append(metrics)

            except Exception as e:
                sample_metrics.append(SampleMetrics(
                    sample_id=audio_path.stem,
                    audio_path=str(audio_path),
                    ground_truth_path=str(gt_path),
                    stem_type=stem_type,
                    pipeline_used="error",
                    profile_used=None,
                    precision=0,
                    recall=0,
                    f1=0,
                    extracted_count=0,
                    ground_truth_count=0,
                    true_positives=0,
                    false_positives=0,
                    false_negatives=0,
                    avg_onset_error_ms=0,
                    max_onset_error_ms=0,
                    onset_std_ms=0,
                    pitch_accuracy=0,
                    octave_error_rate=0,
                    extraction_time_ms=0,
                    error=str(e),
                ))

        # Calculate aggregates
        valid_samples = [s for s in sample_metrics if s.error is None]

        overall_precision = np.mean([s.precision for s in valid_samples]) if valid_samples else 0
        overall_recall = np.mean([s.recall for s in valid_samples]) if valid_samples else 0
        overall_f1 = np.mean([s.f1 for s in valid_samples]) if valid_samples else 0

        # Per-category breakdowns
        per_stem_f1 = self._aggregate_by_category(valid_samples, "stem_type", "f1")
        per_stem_precision = self._aggregate_by_category(valid_samples, "stem_type", "precision")
        per_stem_recall = self._aggregate_by_category(valid_samples, "stem_type", "recall")

        per_pipeline_f1 = self._aggregate_by_category(valid_samples, "pipeline_used", "f1")
        per_pipeline_precision = self._aggregate_by_category(valid_samples, "pipeline_used", "precision")
        per_pipeline_recall = self._aggregate_by_category(valid_samples, "pipeline_used", "recall")

        per_genre_f1 = {}  # TODO: Add genre tracking

        # Baseline comparison
        baseline_comparison = None
        if self.baseline:
            baseline_comparison = {
                "overall_f1": overall_f1 - self.baseline.get("overall_f1", 0),
            }
            for stem, f1 in per_stem_f1.items():
                if stem in self.baseline.get("per_stem_f1", {}):
                    baseline_comparison[f"{stem}_f1"] = f1 - self.baseline["per_stem_f1"][stem]

        total_time = time.time() - start_time

        return BenchmarkRun(
            run_id=run_id,
            timestamp=timestamp,
            config=config or {},
            overall_precision=overall_precision,
            overall_recall=overall_recall,
            overall_f1=overall_f1,
            per_stem_f1=per_stem_f1,
            per_stem_precision=per_stem_precision,
            per_stem_recall=per_stem_recall,
            per_pipeline_f1=per_pipeline_f1,
            per_pipeline_precision=per_pipeline_precision,
            per_pipeline_recall=per_pipeline_recall,
            per_genre_f1=per_genre_f1,
            sample_metrics=sample_metrics,
            total_time_seconds=total_time,
            baseline_comparison=baseline_comparison,
        )

    def _evaluate_sample(
        self,
        audio_path: Path,
        ground_truth_path: Path,
        stem_type: str,
        use_pipeline: bool = True,
    ) -> SampleMetrics:
        """Evaluate a single sample."""
        import time
        import librosa
        from tone_forge.midi.pipelines import get_pipeline_for_stem

        start_time = time.time()

        # Load audio
        audio, sr = librosa.load(str(audio_path), sr=22050, mono=True)

        # Extract MIDI
        if use_pipeline:
            pipeline = get_pipeline_for_stem(stem_type)
            result = pipeline.extract(audio, sr)
            extracted_notes = [(n.pitch, n.start, n.end, n.velocity) for n in result.notes]
            pipeline_used = pipeline.name
        else:
            # Use basic extraction
            from tone_forge.midi import create_extractor
            extractor = create_extractor()
            result = extractor.extract(audio, sr, stem_type=stem_type)
            extracted_notes = [(n.pitch, n.start, n.end, n.velocity) for n in result.notes]
            pipeline_used = "basic"

        # Load ground truth
        gt_notes = self._load_ground_truth(ground_truth_path, stem_type)

        # Compare
        metrics = self._compare_notes(extracted_notes, gt_notes)

        extraction_time = (time.time() - start_time) * 1000

        return SampleMetrics(
            sample_id=audio_path.stem,
            audio_path=str(audio_path),
            ground_truth_path=str(ground_truth_path),
            stem_type=stem_type,
            pipeline_used=pipeline_used,
            profile_used=None,
            precision=metrics["precision"],
            recall=metrics["recall"],
            f1=metrics["f1"],
            extracted_count=len(extracted_notes),
            ground_truth_count=len(gt_notes),
            true_positives=metrics["true_positives"],
            false_positives=metrics["false_positives"],
            false_negatives=metrics["false_negatives"],
            avg_onset_error_ms=metrics["avg_onset_error_ms"],
            max_onset_error_ms=metrics["max_onset_error_ms"],
            onset_std_ms=metrics["onset_std_ms"],
            pitch_accuracy=metrics["pitch_accuracy"],
            octave_error_rate=metrics["octave_error_rate"],
            extraction_time_ms=extraction_time,
        )

    def _load_ground_truth(
        self,
        midi_path: Path,
        stem_type: str,
    ) -> List[Tuple[int, float, float, int]]:
        """Load ground truth MIDI filtered by stem type."""
        import mido

        if not midi_path.exists():
            return []

        mid = mido.MidiFile(str(midi_path))
        notes = []

        # Get tempo
        tempo = 500000
        for track in mid.tracks:
            for msg in track:
                if msg.type == 'set_tempo':
                    tempo = msg.tempo
                    break

        ticks_per_beat = mid.ticks_per_beat

        # Stem type to track name patterns
        patterns = {
            'bass': ['bass'],
            'lead': ['lead'],
            'pad': ['pad'],
            'guitar': ['guitar'],
            'drums': ['drum'],
        }
        track_patterns = patterns.get(stem_type, [])

        for track in mid.tracks:
            track_name = track.name.lower() if track.name else ''

            # Filter by stem type
            if track_patterns and not any(p in track_name for p in track_patterns):
                continue

            current_time = 0.0
            active = {}

            for msg in track:
                delta = mido.tick2second(msg.time, ticks_per_beat, tempo)
                current_time += delta

                if msg.type == 'note_on' and msg.velocity > 0:
                    active[msg.note] = (current_time, msg.velocity)
                elif msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0):
                    if msg.note in active:
                        start, vel = active.pop(msg.note)
                        notes.append((msg.note, start, current_time, vel))

        return sorted(notes, key=lambda n: n[1])

    def _compare_notes(
        self,
        extracted: List[Tuple[int, float, float, int]],
        ground_truth: List[Tuple[int, float, float, int]],
        onset_tolerance_ms: float = 50.0,
    ) -> Dict[str, Any]:
        """Compare extracted notes to ground truth."""
        if not ground_truth:
            return {
                "precision": 1.0 if not extracted else 0.0,
                "recall": 1.0 if not extracted else 0.0,
                "f1": 1.0 if not extracted else 0.0,
                "true_positives": 0,
                "false_positives": len(extracted),
                "false_negatives": 0,
                "avg_onset_error_ms": 0,
                "max_onset_error_ms": 0,
                "onset_std_ms": 0,
                "pitch_accuracy": 1.0,
                "octave_error_rate": 0,
            }

        if not extracted:
            return {
                "precision": 0.0,
                "recall": 0.0,
                "f1": 0.0,
                "true_positives": 0,
                "false_positives": 0,
                "false_negatives": len(ground_truth),
                "avg_onset_error_ms": 0,
                "max_onset_error_ms": 0,
                "onset_std_ms": 0,
                "pitch_accuracy": 0.0,
                "octave_error_rate": 0,
            }

        onset_tolerance = onset_tolerance_ms / 1000.0

        matched_gt = set()
        matched_ext = set()
        onset_errors = []
        pitch_matches = 0
        octave_errors = 0

        for i, ext in enumerate(extracted):
            ext_pitch, ext_start, _, _ = ext
            best_match = None
            best_error = float('inf')

            for j, gt in enumerate(ground_truth):
                if j in matched_gt:
                    continue

                gt_pitch, gt_start, _, _ = gt

                # Check pitch (exact or octave)
                pitch_diff = abs(ext_pitch - gt_pitch)
                if pitch_diff > 0 and pitch_diff % 12 != 0:
                    continue

                # Check onset
                onset_error = abs(ext_start - gt_start)
                if onset_error <= onset_tolerance and onset_error < best_error:
                    best_match = j
                    best_error = onset_error

            if best_match is not None:
                matched_gt.add(best_match)
                matched_ext.add(i)
                onset_errors.append(best_error * 1000)

                gt_pitch = ground_truth[best_match][0]
                if ext_pitch == gt_pitch:
                    pitch_matches += 1
                elif abs(ext_pitch - gt_pitch) % 12 == 0:
                    octave_errors += 1

        tp = len(matched_gt)
        fp = len(extracted) - len(matched_ext)
        fn = len(ground_truth) - len(matched_gt)

        precision = tp / (tp + fp) if (tp + fp) > 0 else 0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0

        return {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "true_positives": tp,
            "false_positives": fp,
            "false_negatives": fn,
            "avg_onset_error_ms": np.mean(onset_errors) if onset_errors else 0,
            "max_onset_error_ms": max(onset_errors) if onset_errors else 0,
            "onset_std_ms": np.std(onset_errors) if onset_errors else 0,
            "pitch_accuracy": pitch_matches / tp if tp > 0 else 0,
            "octave_error_rate": octave_errors / tp if tp > 0 else 0,
        }

    def _aggregate_by_category(
        self,
        samples: List[SampleMetrics],
        category: str,
        metric: str,
    ) -> Dict[str, float]:
        """Aggregate metric by category."""
        from collections import defaultdict

        by_category = defaultdict(list)
        for sample in samples:
            cat_value = getattr(sample, category)
            metric_value = getattr(sample, metric)
            by_category[cat_value].append(metric_value)

        return {cat: np.mean(values) for cat, values in by_category.items()}

    def export_csv(
        self,
        run: BenchmarkRun,
        filename: Optional[str] = None,
    ) -> Path:
        """Export benchmark results to CSV.

        Args:
            run: Benchmark run to export
            filename: Optional custom filename

        Returns:
            Path to exported CSV
        """
        if filename is None:
            filename = f"benchmark_{run.run_id}.csv"

        csv_path = self.output_dir / filename

        fieldnames = [
            "sample_id", "audio_path", "stem_type", "pipeline_used",
            "precision", "recall", "f1",
            "extracted_count", "ground_truth_count",
            "true_positives", "false_positives", "false_negatives",
            "avg_onset_error_ms", "pitch_accuracy", "octave_error_rate",
            "extraction_time_ms", "error",
        ]

        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()

            for sample in run.sample_metrics:
                row = {k: getattr(sample, k) for k in fieldnames}
                writer.writerow(row)

        return csv_path

    def export_json(
        self,
        run: BenchmarkRun,
        filename: Optional[str] = None,
    ) -> Path:
        """Export benchmark results to JSON."""
        if filename is None:
            filename = f"benchmark_{run.run_id}.json"

        json_path = self.output_dir / filename

        # Convert to dict
        run_dict = {
            "run_id": run.run_id,
            "timestamp": run.timestamp,
            "config": run.config,
            "overall_precision": run.overall_precision,
            "overall_recall": run.overall_recall,
            "overall_f1": run.overall_f1,
            "per_stem_f1": run.per_stem_f1,
            "per_stem_precision": run.per_stem_precision,
            "per_stem_recall": run.per_stem_recall,
            "per_pipeline_f1": run.per_pipeline_f1,
            "total_time_seconds": run.total_time_seconds,
            "baseline_comparison": run.baseline_comparison,
            "sample_count": len(run.sample_metrics),
        }

        with open(json_path, "w") as f:
            json.dump(run_dict, f, indent=2)

        return json_path

    def save_baseline(
        self,
        run: BenchmarkRun,
        filename: str = "baseline.json",
    ) -> Path:
        """Save run as baseline for future comparisons."""
        baseline_path = self.output_dir / filename

        baseline = {
            "timestamp": run.timestamp,
            "overall_f1": run.overall_f1,
            "overall_precision": run.overall_precision,
            "overall_recall": run.overall_recall,
            "per_stem_f1": run.per_stem_f1,
            "per_pipeline_f1": run.per_pipeline_f1,
        }

        with open(baseline_path, "w") as f:
            json.dump(baseline, f, indent=2)

        return baseline_path
