"""Enhanced benchmark runner with reconstruction quality integration.

Provides automated benchmarking that integrates stem quality analysis,
contamination detection, and confidence mapping with existing metrics.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

from .metrics import (
    DescriptorAccuracy,
    MIDIQualityMetrics,
    compute_descriptor_accuracy,
    compute_midi_quality,
)
from .benchmarks import BenchmarkDataset, BenchmarkSample, BenchmarkResult

logger = logging.getLogger(__name__)


@dataclass
class ReconstructionQualityMetrics:
    """Quality metrics from reconstruction analysis."""

    # Stem quality aggregate
    avg_stem_quality: float = 0.0
    min_stem_quality: float = 0.0
    avg_contamination: float = 0.0
    avg_transient_integrity: float = 0.0

    # Confidence map aggregate
    avg_global_confidence: float = 0.0
    low_confidence_ratio: float = 0.0

    # Artifact aggregate
    avg_artifact_score: float = 0.0

    # Quality gate results
    samples_passed_gates: int = 0
    samples_warned: int = 0
    samples_failed_gates: int = 0

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "avg_stem_quality": self.avg_stem_quality,
            "min_stem_quality": self.min_stem_quality,
            "avg_contamination": self.avg_contamination,
            "avg_transient_integrity": self.avg_transient_integrity,
            "avg_global_confidence": self.avg_global_confidence,
            "low_confidence_ratio": self.low_confidence_ratio,
            "avg_artifact_score": self.avg_artifact_score,
            "samples_passed_gates": self.samples_passed_gates,
            "samples_warned": self.samples_warned,
            "samples_failed_gates": self.samples_failed_gates,
        }


@dataclass
class EnhancedBenchmarkResult(BenchmarkResult):
    """Extended benchmark result with reconstruction quality metrics."""

    reconstruction_quality: Optional[ReconstructionQualityMetrics] = None
    per_stem_quality: Dict[str, ReconstructionQualityMetrics] = field(default_factory=dict)

    # Comparison data
    baseline_comparison: Optional[Dict[str, float]] = None

    def to_dict(self) -> dict:
        """Serialize to dictionary."""
        result = super().to_dict()
        if self.reconstruction_quality:
            result["reconstruction_quality"] = self.reconstruction_quality.to_dict()
        if self.per_stem_quality:
            result["per_stem_quality"] = {
                k: v.to_dict() for k, v in self.per_stem_quality.items()
            }
        if self.baseline_comparison:
            result["baseline_comparison"] = self.baseline_comparison
        return result

    def summary(self) -> str:
        """Generate human-readable summary."""
        lines = super().summary().split("\n")

        if self.reconstruction_quality:
            rq = self.reconstruction_quality
            lines.extend([
                "",
                "Reconstruction Quality:",
                f"  Avg Stem Quality:    {rq.avg_stem_quality:.1%}",
                f"  Min Stem Quality:    {rq.min_stem_quality:.1%}",
                f"  Avg Contamination:   {rq.avg_contamination:.1%}",
                f"  Avg Confidence:      {rq.avg_global_confidence:.1%}",
                f"  Low Conf Ratio:      {rq.low_confidence_ratio:.1%}",
                f"  Gates Passed:        {rq.samples_passed_gates}/{self.num_samples}",
            ])

        if self.baseline_comparison:
            lines.extend([
                "",
                "vs Baseline:",
            ])
            for metric, delta in self.baseline_comparison.items():
                sign = "+" if delta > 0 else ""
                lines.append(f"  {metric}: {sign}{delta:.1%}")

        return "\n".join(lines)


class BenchmarkRunner:
    """Enhanced benchmark runner with reconstruction quality analysis.

    Integrates stem quality analysis, contamination detection, and
    confidence mapping with traditional descriptor/MIDI metrics.
    """

    def __init__(
        self,
        include_reconstruction_analysis: bool = True,
        include_midi_analysis: bool = True,
        stem_types: Optional[List[str]] = None,
        quality_threshold: float = 0.4,
    ):
        """Initialize the benchmark runner.

        Args:
            include_reconstruction_analysis: Whether to run reconstruction quality
            include_midi_analysis: Whether to run MIDI analysis
            stem_types: Stem types to analyze (default: all)
            quality_threshold: Minimum quality to consider "passed"
        """
        self.include_reconstruction_analysis = include_reconstruction_analysis
        self.include_midi_analysis = include_midi_analysis
        self.stem_types = stem_types or ["bass", "drums", "other", "vocals"]
        self.quality_threshold = quality_threshold

    def run(
        self,
        dataset: BenchmarkDataset,
        analyzer: Callable[[Path], Dict],
        stem_separator: Optional[Callable[[Path], Dict[str, np.ndarray]]] = None,
        midi_extractor: Optional[Callable[[np.ndarray, int], List]] = None,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> EnhancedBenchmarkResult:
        """Run comprehensive benchmark evaluation.

        Args:
            dataset: Benchmark dataset to evaluate
            analyzer: Function that takes audio path and returns descriptor dict
            stem_separator: Function that separates stems (for reconstruction analysis)
            midi_extractor: Function that extracts MIDI
            progress_callback: Optional callback(current, total, message)

        Returns:
            EnhancedBenchmarkResult with all metrics
        """
        start_time = time.time()

        predictions = []
        ground_truths = []
        sample_results = []

        # Reconstruction quality tracking
        stem_qualities = []
        contamination_scores = []
        artifact_scores = []
        confidence_scores = []
        gate_results = {"passed": 0, "warned": 0, "failed": 0}

        per_stem_metrics: Dict[str, List[Dict]] = {st: [] for st in self.stem_types}

        for i, sample in enumerate(dataset.samples):
            if progress_callback:
                progress_callback(i, len(dataset.samples), f"Processing {sample.id}")

            try:
                # Run descriptor analysis
                pred = analyzer(sample.audio_path)
                predictions.append(pred)
                ground_truths.append(sample.ground_truth)

                sample_result = {
                    "id": sample.id,
                    "predicted": pred,
                    "ground_truth": sample.ground_truth,
                    "success": True,
                }

                # Run reconstruction quality analysis if enabled
                if self.include_reconstruction_analysis and stem_separator is not None:
                    try:
                        recon_result = self._analyze_reconstruction_quality(
                            sample.audio_path,
                            stem_separator,
                        )

                        sample_result["reconstruction"] = recon_result

                        # Aggregate metrics
                        if recon_result.get("overall_quality"):
                            stem_qualities.append(recon_result["overall_quality"])
                        if recon_result.get("contamination"):
                            contamination_scores.append(recon_result["contamination"])
                        if recon_result.get("artifact_score"):
                            artifact_scores.append(recon_result["artifact_score"])
                        if recon_result.get("global_confidence"):
                            confidence_scores.append(recon_result["global_confidence"])

                        # Track gate status
                        if recon_result.get("gate_status") == "passed":
                            gate_results["passed"] += 1
                        elif recon_result.get("gate_status") == "warning":
                            gate_results["warned"] += 1
                        else:
                            gate_results["failed"] += 1

                        # Per-stem metrics
                        for stem_type, stem_data in recon_result.get("per_stem", {}).items():
                            if stem_type in per_stem_metrics:
                                per_stem_metrics[stem_type].append(stem_data)

                    except Exception as e:
                        logger.warning(f"Reconstruction analysis failed for {sample.id}: {e}")
                        sample_result["reconstruction_error"] = str(e)

                sample_results.append(sample_result)

            except Exception as e:
                logger.warning(f"Failed to analyze {sample.id}: {e}")
                sample_results.append({
                    "id": sample.id,
                    "error": str(e),
                    "success": False,
                })

        # Compute aggregate descriptor metrics
        if predictions and ground_truths:
            descriptor_accuracy = compute_descriptor_accuracy(predictions, ground_truths)
        else:
            descriptor_accuracy = DescriptorAccuracy()

        # Compute reconstruction quality metrics
        reconstruction_quality = None
        if self.include_reconstruction_analysis and stem_qualities:
            reconstruction_quality = ReconstructionQualityMetrics(
                avg_stem_quality=float(np.mean(stem_qualities)),
                min_stem_quality=float(np.min(stem_qualities)),
                avg_contamination=float(np.mean(contamination_scores)) if contamination_scores else 0.0,
                avg_transient_integrity=0.0,  # Would need to track
                avg_global_confidence=float(np.mean(confidence_scores)) if confidence_scores else 0.0,
                low_confidence_ratio=0.0,  # Would need to track
                avg_artifact_score=float(np.mean(artifact_scores)) if artifact_scores else 0.0,
                samples_passed_gates=gate_results["passed"],
                samples_warned=gate_results["warned"],
                samples_failed_gates=gate_results["failed"],
            )

        # Per-stem quality aggregation
        per_stem_quality = {}
        for stem_type, metrics_list in per_stem_metrics.items():
            if metrics_list:
                per_stem_quality[stem_type] = ReconstructionQualityMetrics(
                    avg_stem_quality=float(np.mean([m.get("quality", 0) for m in metrics_list])),
                    avg_contamination=float(np.mean([m.get("contamination", 0) for m in metrics_list])),
                )

        execution_time = time.time() - start_time

        return EnhancedBenchmarkResult(
            dataset_name=dataset.name,
            dataset_version=dataset.version,
            num_samples=len(dataset.samples),
            descriptor_accuracy=descriptor_accuracy,
            midi_quality=None,  # Would compute if MIDI ground truth available
            sample_results=sample_results,
            per_category_results={},
            execution_time_sec=execution_time,
            ml_models_used=False,
            reconstruction_quality=reconstruction_quality,
            per_stem_quality=per_stem_quality,
        )

    def _analyze_reconstruction_quality(
        self,
        audio_path: Path,
        stem_separator: Callable[[Path], Dict[str, np.ndarray]],
    ) -> Dict[str, Any]:
        """Analyze reconstruction quality for a single sample."""
        try:
            # Import reconstruction modules
            from ..reconstruction import (
                analyze_stem_quality,
                detect_contamination,
                detect_artifacts,
                build_confidence_map,
                get_quality_gates,
            )
            import librosa
        except ImportError as e:
            logger.warning(f"Reconstruction modules not available: {e}")
            return {}

        # Load audio
        audio, sr = librosa.load(audio_path, sr=None, mono=False)
        if audio.ndim == 1:
            audio = audio.reshape(1, -1)

        # Separate stems
        stems = stem_separator(audio_path)

        # Analyze stem quality
        stem_qualities = analyze_stem_quality(stems, sr)

        # Compute per-stem metrics
        per_stem = {}
        overall_quality = []
        overall_contamination = []
        overall_artifacts = []

        for stem_type, stem_audio in stems.items():
            sq = stem_qualities.get(stem_type)
            if sq:
                # Get contamination
                contam = detect_contamination(
                    stem_audio, sr, stem_type,
                    other_stems={k: v for k, v in stems.items() if k != stem_type}
                )

                # Get artifacts
                artifacts = detect_artifacts(stem_audio, sr, stem_type)

                per_stem[stem_type] = {
                    "quality": sq.overall_quality,
                    "contamination": contam.overall_contamination,
                    "artifact_score": artifacts.overall_artifact_score,
                    "transient_integrity": sq.transient_integrity,
                    "harmonic_purity": sq.harmonic_purity,
                }

                overall_quality.append(sq.overall_quality)
                overall_contamination.append(contam.overall_contamination)
                overall_artifacts.append(artifacts.overall_artifact_score)

        # Build confidence map for main stem (bass for now)
        main_stem = stems.get("bass") or list(stems.values())[0]
        main_stem_type = "bass" if "bass" in stems else list(stems.keys())[0]
        confidence_map = build_confidence_map(
            main_stem, sr, main_stem_type,
            stem_quality=stem_qualities.get(main_stem_type),
        )

        # Evaluate quality gates
        gates = get_quality_gates()
        main_sq = stem_qualities.get(main_stem_type)
        if main_sq:
            report = gates.evaluate(
                main_stem_type,
                stem_quality=main_sq,
            )
            gate_status = report.overall_status.value
        else:
            gate_status = "unknown"

        return {
            "overall_quality": float(np.mean(overall_quality)) if overall_quality else 0.0,
            "contamination": float(np.mean(overall_contamination)) if overall_contamination else 0.0,
            "artifact_score": float(np.mean(overall_artifacts)) if overall_artifacts else 0.0,
            "global_confidence": confidence_map.global_confidence,
            "gate_status": gate_status,
            "per_stem": per_stem,
        }

    def compare(
        self,
        baseline: EnhancedBenchmarkResult,
        improved: EnhancedBenchmarkResult,
    ) -> Dict[str, float]:
        """Compare two benchmark results.

        Args:
            baseline: Baseline benchmark result
            improved: Improved benchmark result

        Returns:
            Dictionary of metric deltas (positive = improvement)
        """
        comparison = {}

        # Descriptor metrics
        comparison["amp_family_accuracy"] = (
            improved.descriptor_accuracy.amp_family_accuracy -
            baseline.descriptor_accuracy.amp_family_accuracy
        )
        comparison["gain_mae"] = (
            baseline.descriptor_accuracy.gain_mae -  # Lower is better
            improved.descriptor_accuracy.gain_mae
        )
        comparison["effects_f1"] = (
            improved.descriptor_accuracy.effects_f1 -
            baseline.descriptor_accuracy.effects_f1
        )
        comparison["descriptor_overall"] = (
            improved.descriptor_accuracy.overall_score -
            baseline.descriptor_accuracy.overall_score
        )

        # Reconstruction quality metrics
        if baseline.reconstruction_quality and improved.reconstruction_quality:
            comparison["stem_quality"] = (
                improved.reconstruction_quality.avg_stem_quality -
                baseline.reconstruction_quality.avg_stem_quality
            )
            comparison["contamination"] = (
                baseline.reconstruction_quality.avg_contamination -  # Lower is better
                improved.reconstruction_quality.avg_contamination
            )
            comparison["confidence"] = (
                improved.reconstruction_quality.avg_global_confidence -
                baseline.reconstruction_quality.avg_global_confidence
            )

        return comparison

    def save_result(
        self,
        result: EnhancedBenchmarkResult,
        path: Path,
    ) -> None:
        """Save benchmark result to JSON file.

        Args:
            result: Benchmark result to save
            path: Output path
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        with open(path, "w") as f:
            json.dump(result.to_dict(), f, indent=2, default=str)

        logger.info(f"Saved benchmark result to {path}")

    def load_result(self, path: Path) -> EnhancedBenchmarkResult:
        """Load benchmark result from JSON file.

        Args:
            path: Path to result file

        Returns:
            EnhancedBenchmarkResult
        """
        with open(path, "r") as f:
            data = json.load(f)

        # Reconstruct dataclass from dict
        descriptor_accuracy = DescriptorAccuracy(**data.get("descriptor_accuracy", {}))

        reconstruction_quality = None
        if data.get("reconstruction_quality"):
            reconstruction_quality = ReconstructionQualityMetrics(
                **data["reconstruction_quality"]
            )

        return EnhancedBenchmarkResult(
            dataset_name=data["dataset_name"],
            dataset_version=data["dataset_version"],
            num_samples=data["num_samples"],
            descriptor_accuracy=descriptor_accuracy,
            sample_results=data.get("sample_results", []),
            execution_time_sec=data.get("execution_time_sec", 0),
            reconstruction_quality=reconstruction_quality,
        )


# Convenience function
def run_enhanced_benchmark(
    dataset: BenchmarkDataset,
    analyzer: Callable[[Path], Dict],
    stem_separator: Optional[Callable[[Path], Dict[str, np.ndarray]]] = None,
    **kwargs,
) -> EnhancedBenchmarkResult:
    """Convenience function to run enhanced benchmark.

    Args:
        dataset: Benchmark dataset
        analyzer: Analyzer function
        stem_separator: Optional stem separator
        **kwargs: Additional arguments for BenchmarkRunner

    Returns:
        EnhancedBenchmarkResult
    """
    runner = BenchmarkRunner(**kwargs)
    return runner.run(dataset, analyzer, stem_separator)
