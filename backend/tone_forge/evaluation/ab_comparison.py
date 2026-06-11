"""A/B pipeline comparison system for MIDI extraction.

Enables direct comparison between:
- Raw basic-pitch output
- Profile-aware extraction
- Pipeline-based extraction
- With/without cleanup passes
- With/without ML refinement

Provides direct regression visibility for tuning decisions.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np


@dataclass
class ExtractionVariant:
    """Configuration for an extraction variant."""
    name: str
    description: str
    extractor_fn: Callable  # Function that takes (audio, sr, **kwargs) -> notes
    config: Dict[str, Any] = field(default_factory=dict)


@dataclass
class VariantResult:
    """Result from a single extraction variant."""
    variant_name: str
    notes_extracted: int
    precision: float
    recall: float
    f1: float
    avg_onset_error_ms: float
    extraction_time_ms: float
    notes: List[Tuple[int, float, float, int]]  # For detailed analysis


@dataclass
class ComparisonResult:
    """Result of comparing multiple variants on one sample."""
    sample_id: str
    audio_path: str
    ground_truth_path: str
    stem_type: str
    ground_truth_notes: int

    variant_results: Dict[str, VariantResult]

    # Winner analysis
    best_f1_variant: str
    best_precision_variant: str
    best_recall_variant: str

    def summary(self) -> str:
        """Generate comparison summary."""
        lines = [
            f"Sample: {self.sample_id}",
            f"Stem: {self.stem_type}",
            f"Ground Truth: {self.ground_truth_notes} notes",
            "",
            f"{'Variant':<25} {'Notes':>6} {'Prec':>7} {'Recall':>7} {'F1':>7} {'Onset':>8}",
            "-" * 65,
        ]

        for name, result in sorted(self.variant_results.items()):
            winner = ""
            if name == self.best_f1_variant:
                winner = " *"
            lines.append(
                f"{name:<25} {result.notes_extracted:>6} "
                f"{result.precision:>6.1%} {result.recall:>6.1%} "
                f"{result.f1:>6.1%}{winner} {result.avg_onset_error_ms:>7.1f}ms"
            )

        return "\n".join(lines)


@dataclass
class ABComparisonRun:
    """Complete A/B comparison run across multiple samples."""
    run_id: str
    timestamp: str
    variants: List[str]

    # Per-sample results
    sample_results: List[ComparisonResult]

    # Aggregate statistics
    aggregate_f1: Dict[str, float]
    aggregate_precision: Dict[str, float]
    aggregate_recall: Dict[str, float]

    # Win counts
    f1_wins: Dict[str, int]
    precision_wins: Dict[str, int]
    recall_wins: Dict[str, int]

    # Regression analysis (if baseline provided)
    regression_from_baseline: Optional[Dict[str, Dict[str, float]]] = None

    def summary(self) -> str:
        """Generate full comparison summary."""
        lines = [
            f"A/B Comparison Run: {self.run_id}",
            f"Timestamp: {self.timestamp}",
            f"Samples: {len(self.sample_results)}",
            f"Variants: {', '.join(self.variants)}",
            "",
            "=== Aggregate Results ===",
            f"{'Variant':<25} {'F1':>8} {'Prec':>8} {'Recall':>8} {'Wins':>6}",
            "-" * 60,
        ]

        for variant in self.variants:
            f1 = self.aggregate_f1.get(variant, 0)
            prec = self.aggregate_precision.get(variant, 0)
            rec = self.aggregate_recall.get(variant, 0)
            wins = self.f1_wins.get(variant, 0)

            lines.append(
                f"{variant:<25} {f1:>7.1%} {prec:>7.1%} {rec:>7.1%} {wins:>6}"
            )

        if self.regression_from_baseline:
            lines.append("")
            lines.append("=== Regression from Baseline ===")
            for variant, deltas in self.regression_from_baseline.items():
                for metric, delta in deltas.items():
                    sign = "+" if delta >= 0 else ""
                    lines.append(f"  {variant} {metric}: {sign}{delta:.1%}")

        return "\n".join(lines)


class ABComparisonSystem:
    """System for A/B comparison of MIDI extraction approaches.

    Usage:
        system = ABComparisonSystem()

        # Register variants
        system.register_variant("raw_basic_pitch", raw_extractor)
        system.register_variant("pipeline_lead", pipeline_extractor)

        # Run comparison
        results = system.compare(samples, ground_truth_dir)
        print(results.summary())
    """

    def __init__(self, output_dir: Optional[Path] = None):
        """Initialize comparison system."""
        self.variants: Dict[str, ExtractionVariant] = {}
        self.output_dir = Path(output_dir) if output_dir else Path("comparisons")
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Register default variants
        self._register_default_variants()

    def _register_default_variants(self):
        """Register default extraction variants."""
        # Raw basic-pitch
        self.register_variant(
            name="raw_basic_pitch",
            description="Raw basic-pitch output with default thresholds",
            extractor_fn=self._extract_raw_basic_pitch,
            config={"onset_threshold": 0.5, "frame_threshold": 0.3},
        )

        # Basic-pitch with tuned thresholds
        self.register_variant(
            name="basic_pitch_tuned",
            description="Basic-pitch with higher thresholds",
            extractor_fn=self._extract_raw_basic_pitch,
            config={"onset_threshold": 0.6, "frame_threshold": 0.4},
        )

    def register_variant(
        self,
        name: str,
        extractor_fn: Callable,
        description: str = "",
        config: Dict[str, Any] = None,
    ):
        """Register an extraction variant.

        Args:
            name: Unique name for the variant
            extractor_fn: Function (audio, sr, **config) -> List[(pitch, start, end, vel)]
            description: Human-readable description
            config: Configuration dict passed to extractor
        """
        self.variants[name] = ExtractionVariant(
            name=name,
            description=description,
            extractor_fn=extractor_fn,
            config=config or {},
        )

    def register_pipeline_variant(
        self,
        name: str,
        pipeline_name: str,
        description: str = "",
    ):
        """Register a pipeline-based variant.

        Args:
            name: Variant name
            pipeline_name: Name of pipeline (lead, bass, etc.)
            description: Description
        """
        def extractor(audio, sr, **kwargs):
            from tone_forge.midi.pipelines import get_pipeline_by_name
            pipeline = get_pipeline_by_name(pipeline_name)
            result = pipeline.extract(audio, sr, **kwargs)
            return [(n.pitch, n.start, n.end, n.velocity) for n in result.notes]

        self.register_variant(
            name=name,
            extractor_fn=extractor,
            description=description or f"Pipeline: {pipeline_name}",
            config={"pipeline": pipeline_name},
        )

    def compare(
        self,
        samples: List[Dict[str, Any]],
        variant_names: Optional[List[str]] = None,
        baseline_variant: Optional[str] = None,
    ) -> ABComparisonRun:
        """Run A/B comparison on samples.

        Args:
            samples: List of dicts with 'audio_path', 'ground_truth_path', 'stem_type'
            variant_names: Which variants to compare (None = all)
            baseline_variant: Variant to use as baseline for regression

        Returns:
            ABComparisonRun with results
        """
        import time

        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        timestamp = datetime.now().isoformat()

        variants_to_test = variant_names or list(self.variants.keys())
        sample_results = []

        for sample in samples:
            result = self._compare_sample(sample, variants_to_test)
            sample_results.append(result)

        # Compute aggregates
        aggregate_f1 = {}
        aggregate_precision = {}
        aggregate_recall = {}
        f1_wins = {v: 0 for v in variants_to_test}
        precision_wins = {v: 0 for v in variants_to_test}
        recall_wins = {v: 0 for v in variants_to_test}

        for variant in variants_to_test:
            f1_scores = [
                r.variant_results[variant].f1
                for r in sample_results
                if variant in r.variant_results
            ]
            prec_scores = [
                r.variant_results[variant].precision
                for r in sample_results
                if variant in r.variant_results
            ]
            rec_scores = [
                r.variant_results[variant].recall
                for r in sample_results
                if variant in r.variant_results
            ]

            aggregate_f1[variant] = np.mean(f1_scores) if f1_scores else 0
            aggregate_precision[variant] = np.mean(prec_scores) if prec_scores else 0
            aggregate_recall[variant] = np.mean(rec_scores) if rec_scores else 0

        # Count wins
        for result in sample_results:
            f1_wins[result.best_f1_variant] = f1_wins.get(result.best_f1_variant, 0) + 1
            precision_wins[result.best_precision_variant] = precision_wins.get(result.best_precision_variant, 0) + 1
            recall_wins[result.best_recall_variant] = recall_wins.get(result.best_recall_variant, 0) + 1

        # Regression analysis
        regression = None
        if baseline_variant and baseline_variant in aggregate_f1:
            baseline_f1 = aggregate_f1[baseline_variant]
            regression = {}
            for variant in variants_to_test:
                if variant != baseline_variant:
                    regression[variant] = {
                        "f1": aggregate_f1[variant] - baseline_f1,
                        "precision": aggregate_precision[variant] - aggregate_precision[baseline_variant],
                        "recall": aggregate_recall[variant] - aggregate_recall[baseline_variant],
                    }

        return ABComparisonRun(
            run_id=run_id,
            timestamp=timestamp,
            variants=variants_to_test,
            sample_results=sample_results,
            aggregate_f1=aggregate_f1,
            aggregate_precision=aggregate_precision,
            aggregate_recall=aggregate_recall,
            f1_wins=f1_wins,
            precision_wins=precision_wins,
            recall_wins=recall_wins,
            regression_from_baseline=regression,
        )

    def _compare_sample(
        self,
        sample: Dict[str, Any],
        variant_names: List[str],
    ) -> ComparisonResult:
        """Compare variants on a single sample."""
        import time
        import librosa

        audio_path = Path(sample["audio_path"])
        gt_path = Path(sample.get("ground_truth_path", ""))
        stem_type = sample.get("stem_type", "other")

        # Load audio
        audio, sr = librosa.load(str(audio_path), sr=22050, mono=True)

        # Load ground truth
        gt_notes = self._load_ground_truth(gt_path, stem_type)

        variant_results = {}

        for variant_name in variant_names:
            if variant_name not in self.variants:
                continue

            variant = self.variants[variant_name]

            try:
                start_time = time.time()
                extracted = variant.extractor_fn(audio, sr, **variant.config)
                extraction_time = (time.time() - start_time) * 1000

                # Compare to ground truth
                metrics = self._compare_notes(extracted, gt_notes)

                variant_results[variant_name] = VariantResult(
                    variant_name=variant_name,
                    notes_extracted=len(extracted),
                    precision=metrics["precision"],
                    recall=metrics["recall"],
                    f1=metrics["f1"],
                    avg_onset_error_ms=metrics["avg_onset_error_ms"],
                    extraction_time_ms=extraction_time,
                    notes=extracted,
                )

            except Exception as e:
                variant_results[variant_name] = VariantResult(
                    variant_name=variant_name,
                    notes_extracted=0,
                    precision=0,
                    recall=0,
                    f1=0,
                    avg_onset_error_ms=0,
                    extraction_time_ms=0,
                    notes=[],
                )

        # Determine winners
        best_f1 = max(variant_results.items(), key=lambda x: x[1].f1)[0] if variant_results else ""
        best_prec = max(variant_results.items(), key=lambda x: x[1].precision)[0] if variant_results else ""
        best_rec = max(variant_results.items(), key=lambda x: x[1].recall)[0] if variant_results else ""

        return ComparisonResult(
            sample_id=audio_path.stem,
            audio_path=str(audio_path),
            ground_truth_path=str(gt_path),
            stem_type=stem_type,
            ground_truth_notes=len(gt_notes),
            variant_results=variant_results,
            best_f1_variant=best_f1,
            best_precision_variant=best_prec,
            best_recall_variant=best_rec,
        )

    def _extract_raw_basic_pitch(
        self,
        audio: np.ndarray,
        sr: int,
        onset_threshold: float = 0.5,
        frame_threshold: float = 0.3,
        **kwargs,
    ) -> List[Tuple[int, float, float, int]]:
        """Extract using raw basic-pitch."""
        import tempfile
        import os
        import soundfile as sf
        from basic_pitch.inference import predict

        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp:
            tmp_path = tmp.name
            sf.write(tmp_path, audio.astype(np.float32), sr)

        try:
            _, _, note_events = predict(
                tmp_path,
                onset_threshold=onset_threshold,
                frame_threshold=frame_threshold,
            )
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

        return [
            (int(n[2]), float(n[0]), float(n[1]), int(n[3] * 127))
            for n in note_events
        ]

    def _load_ground_truth(
        self,
        midi_path: Path,
        stem_type: str,
    ) -> List[Tuple[int, float, float, int]]:
        """Load ground truth MIDI."""
        import mido

        if not midi_path.exists():
            return []

        mid = mido.MidiFile(str(midi_path))
        notes = []

        tempo = 500000
        for track in mid.tracks:
            for msg in track:
                if msg.type == 'set_tempo':
                    tempo = msg.tempo
                    break

        ticks_per_beat = mid.ticks_per_beat

        patterns = {
            'bass': ['bass'],
            'lead': ['lead'],
            'pad': ['pad'],
            'guitar': ['guitar'],
        }
        track_patterns = patterns.get(stem_type, [])

        for track in mid.tracks:
            track_name = track.name.lower() if track.name else ''
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
    ) -> Dict[str, float]:
        """Compare extracted notes to ground truth."""
        if not ground_truth:
            return {
                "precision": 1.0 if not extracted else 0.0,
                "recall": 1.0,
                "f1": 1.0 if not extracted else 0.0,
                "avg_onset_error_ms": 0,
            }

        if not extracted:
            return {
                "precision": 0.0,
                "recall": 0.0,
                "f1": 0.0,
                "avg_onset_error_ms": 0,
            }

        onset_tolerance = onset_tolerance_ms / 1000.0
        matched_gt = set()
        onset_errors = []

        for ext in extracted:
            ext_pitch, ext_start, _, _ = ext

            for j, gt in enumerate(ground_truth):
                if j in matched_gt:
                    continue

                gt_pitch, gt_start, _, _ = gt

                if ext_pitch != gt_pitch:
                    continue

                onset_error = abs(ext_start - gt_start)
                if onset_error <= onset_tolerance:
                    matched_gt.add(j)
                    onset_errors.append(onset_error * 1000)
                    break

        tp = len(matched_gt)
        fp = len(extracted) - tp
        fn = len(ground_truth) - tp

        precision = tp / (tp + fp) if (tp + fp) > 0 else 0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0

        return {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "avg_onset_error_ms": np.mean(onset_errors) if onset_errors else 0,
        }

    def export_results(
        self,
        run: ABComparisonRun,
        format: str = "json",
    ) -> Path:
        """Export comparison results."""
        if format == "json":
            return self._export_json(run)
        elif format == "csv":
            return self._export_csv(run)
        else:
            raise ValueError(f"Unknown format: {format}")

    def _export_json(self, run: ABComparisonRun) -> Path:
        """Export to JSON."""
        output_path = self.output_dir / f"ab_comparison_{run.run_id}.json"

        data = {
            "run_id": run.run_id,
            "timestamp": run.timestamp,
            "variants": run.variants,
            "aggregate_f1": run.aggregate_f1,
            "aggregate_precision": run.aggregate_precision,
            "aggregate_recall": run.aggregate_recall,
            "f1_wins": run.f1_wins,
            "sample_count": len(run.sample_results),
            "regression_from_baseline": run.regression_from_baseline,
        }

        with open(output_path, "w") as f:
            json.dump(data, f, indent=2)

        return output_path

    def _export_csv(self, run: ABComparisonRun) -> Path:
        """Export to CSV."""
        import csv

        output_path = self.output_dir / f"ab_comparison_{run.run_id}.csv"

        fieldnames = [
            "sample_id", "stem_type", "variant", "notes_extracted",
            "precision", "recall", "f1", "avg_onset_error_ms", "is_winner"
        ]

        with open(output_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()

            for result in run.sample_results:
                for variant_name, vr in result.variant_results.items():
                    writer.writerow({
                        "sample_id": result.sample_id,
                        "stem_type": result.stem_type,
                        "variant": variant_name,
                        "notes_extracted": vr.notes_extracted,
                        "precision": vr.precision,
                        "recall": vr.recall,
                        "f1": vr.f1,
                        "avg_onset_error_ms": vr.avg_onset_error_ms,
                        "is_winner": variant_name == result.best_f1_variant,
                    })

        return output_path


# Convenience function for quick comparisons
def compare_variants(
    audio_path: str,
    ground_truth_path: str,
    stem_type: str = "other",
    variants: Optional[List[str]] = None,
) -> ComparisonResult:
    """Quick comparison of variants on a single sample."""
    system = ABComparisonSystem()

    sample = {
        "audio_path": audio_path,
        "ground_truth_path": ground_truth_path,
        "stem_type": stem_type,
    }

    run = system.compare([sample], variant_names=variants)
    return run.sample_results[0] if run.sample_results else None
