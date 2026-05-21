"""Benchmark infrastructure for ToneForge evaluation.

Provides tools for running evaluations on benchmark datasets:
- Loading labeled datasets
- Running analysis pipelines
- Computing aggregate metrics
- Generating evaluation reports

Benchmark datasets should include ground truth labels for:
- Amp family
- Gain level
- Cab/speaker character
- Effects present
- Optional: MIDI ground truth
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Callable, Any
import json
import logging

import numpy as np

from .metrics import (
    DescriptorAccuracy,
    MIDIQualityMetrics,
    compute_descriptor_accuracy,
    compute_midi_quality,
)

logger = logging.getLogger(__name__)


@dataclass
class BenchmarkSample:
    """A single sample in a benchmark dataset."""
    id: str
    audio_path: Path
    ground_truth: Dict
    metadata: Dict = field(default_factory=dict)


@dataclass
class BenchmarkDataset:
    """A benchmark dataset with labeled samples.

    Attributes:
        name: Dataset name
        version: Dataset version
        samples: List of benchmark samples
        categories: Available ground truth categories
    """
    name: str
    version: str
    samples: List[BenchmarkSample]
    categories: List[str] = field(default_factory=list)
    metadata: Dict = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.samples)

    def filter_by_category(self, category: str, value: Any) -> "BenchmarkDataset":
        """Filter samples by a ground truth category value."""
        filtered = [
            s for s in self.samples
            if s.ground_truth.get(category) == value
        ]
        return BenchmarkDataset(
            name=f"{self.name}_{category}_{value}",
            version=self.version,
            samples=filtered,
            categories=self.categories,
        )

    def to_dict(self) -> dict:
        """Serialize to dictionary."""
        return {
            "name": self.name,
            "version": self.version,
            "samples": [
                {
                    "id": s.id,
                    "audio_path": str(s.audio_path),
                    "ground_truth": s.ground_truth,
                    "metadata": s.metadata,
                }
                for s in self.samples
            ],
            "categories": self.categories,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict, base_path: Optional[Path] = None) -> "BenchmarkDataset":
        """Deserialize from dictionary."""
        samples = []
        for s in d.get("samples", []):
            audio_path = Path(s["audio_path"])
            if base_path and not audio_path.is_absolute():
                audio_path = base_path / audio_path
            samples.append(BenchmarkSample(
                id=s["id"],
                audio_path=audio_path,
                ground_truth=s["ground_truth"],
                metadata=s.get("metadata", {}),
            ))
        return cls(
            name=d["name"],
            version=d["version"],
            samples=samples,
            categories=d.get("categories", []),
            metadata=d.get("metadata", {}),
        )


@dataclass
class BenchmarkResult:
    """Results from running a benchmark.

    Contains aggregate metrics and per-sample details.
    """
    dataset_name: str
    dataset_version: str
    num_samples: int
    descriptor_accuracy: DescriptorAccuracy
    midi_quality: Optional[MIDIQualityMetrics] = None

    # Per-sample results
    sample_results: List[Dict] = field(default_factory=list)

    # Breakdown by category
    per_category_results: Dict[str, DescriptorAccuracy] = field(default_factory=dict)

    # Execution metadata
    execution_time_sec: float = 0.0
    analyzer_version: str = ""
    ml_models_used: bool = False

    def to_dict(self) -> dict:
        """Serialize to dictionary."""
        return {
            "dataset_name": self.dataset_name,
            "dataset_version": self.dataset_version,
            "num_samples": self.num_samples,
            "descriptor_accuracy": self.descriptor_accuracy.to_dict(),
            "midi_quality": self.midi_quality.to_dict() if self.midi_quality else None,
            "per_category_results": {
                k: v.to_dict() for k, v in self.per_category_results.items()
            },
            "execution_time_sec": self.execution_time_sec,
            "analyzer_version": self.analyzer_version,
            "ml_models_used": self.ml_models_used,
        }

    def summary(self) -> str:
        """Generate human-readable summary."""
        lines = [
            f"Benchmark: {self.dataset_name} v{self.dataset_version}",
            f"Samples: {self.num_samples}",
            f"",
            "Descriptor Accuracy:",
            f"  Amp Family:    {self.descriptor_accuracy.amp_family_accuracy:.1%}",
            f"  Amp Top-3:     {self.descriptor_accuracy.amp_family_top3_accuracy:.1%}",
            f"  Cab:           {self.descriptor_accuracy.cab_accuracy:.1%}",
            f"  Gain MAE:      {self.descriptor_accuracy.gain_mae:.3f}",
            f"  Gain ±10%:     {self.descriptor_accuracy.gain_within_10pct:.1%}",
            f"  Effects F1:    {self.descriptor_accuracy.effects_f1:.1%}",
            f"  Overall:       {self.descriptor_accuracy.overall_score:.1%}",
        ]

        if self.midi_quality:
            lines.extend([
                "",
                "MIDI Quality:",
                f"  Note F1:       {self.midi_quality.note_f1:.1%}",
                f"  Pitch Acc:     {self.midi_quality.pitch_accuracy:.1%}",
                f"  Onset ±50ms:   {self.midi_quality.onset_within_50ms:.1%}",
                f"  Overall:       {self.midi_quality.overall_score:.1%}",
            ])

        if self.per_category_results:
            lines.append("")
            lines.append("Per-Category Breakdown:")
            for cat, acc in self.per_category_results.items():
                lines.append(f"  {cat}: {acc.overall_score:.1%}")

        lines.extend([
            "",
            f"Execution time: {self.execution_time_sec:.1f}s",
            f"ML models: {'Yes' if self.ml_models_used else 'No'}",
        ])

        return "\n".join(lines)


def load_benchmark_dataset(path: Path) -> BenchmarkDataset:
    """Load a benchmark dataset from JSON file.

    Args:
        path: Path to benchmark JSON file

    Returns:
        BenchmarkDataset loaded from file
    """
    path = Path(path)
    with open(path, "r") as f:
        data = json.load(f)
    return BenchmarkDataset.from_dict(data, base_path=path.parent)


def run_benchmark(
    dataset: BenchmarkDataset,
    analyzer: Callable[[Path], Dict],
    include_midi: bool = False,
    midi_extractor: Optional[Callable[[Path], List]] = None,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> BenchmarkResult:
    """Run a benchmark evaluation.

    Args:
        dataset: Benchmark dataset to evaluate
        analyzer: Function that takes audio path and returns descriptor dict
        include_midi: Whether to evaluate MIDI extraction
        midi_extractor: Function that extracts MIDI (if include_midi)
        progress_callback: Optional callback(current, total) for progress

    Returns:
        BenchmarkResult with all metrics
    """
    import time
    start_time = time.time()

    predictions = []
    ground_truths = []
    sample_results = []

    for i, sample in enumerate(dataset.samples):
        if progress_callback:
            progress_callback(i, len(dataset.samples))

        try:
            # Run analyzer
            pred = analyzer(sample.audio_path)
            predictions.append(pred)
            ground_truths.append(sample.ground_truth)

            # Store per-sample result
            sample_results.append({
                "id": sample.id,
                "predicted": pred,
                "ground_truth": sample.ground_truth,
                "success": True,
            })

        except Exception as e:
            logger.warning(f"Failed to analyze {sample.id}: {e}")
            sample_results.append({
                "id": sample.id,
                "error": str(e),
                "success": False,
            })

    # Compute aggregate metrics
    descriptor_accuracy = compute_descriptor_accuracy(predictions, ground_truths)

    # MIDI quality if requested
    midi_quality = None
    if include_midi and midi_extractor is not None:
        # Would need ground truth MIDI in dataset
        pass

    # Per-category breakdown
    per_category = {}
    for category in dataset.categories:
        values = set(s.ground_truth.get(category) for s in dataset.samples)
        for value in values:
            if value is None:
                continue
            cat_preds = [
                p for p, s in zip(predictions, dataset.samples)
                if s.ground_truth.get(category) == value
            ]
            cat_truths = [
                s.ground_truth for s in dataset.samples
                if s.ground_truth.get(category) == value
            ]
            if len(cat_preds) >= 3:  # Only if enough samples
                cat_key = f"{category}={value}"
                per_category[cat_key] = compute_descriptor_accuracy(cat_preds, cat_truths)

    execution_time = time.time() - start_time

    return BenchmarkResult(
        dataset_name=dataset.name,
        dataset_version=dataset.version,
        num_samples=len(dataset.samples),
        descriptor_accuracy=descriptor_accuracy,
        midi_quality=midi_quality,
        sample_results=sample_results,
        per_category_results=per_category,
        execution_time_sec=execution_time,
        ml_models_used=False,  # Would check ML registry
    )


def create_benchmark_dataset(
    name: str,
    audio_dir: Path,
    labels: Dict[str, Dict],
    version: str = "1.0",
) -> BenchmarkDataset:
    """Create a benchmark dataset from audio files and labels.

    Args:
        name: Dataset name
        audio_dir: Directory containing audio files
        labels: Dict mapping filename -> ground_truth dict
        version: Dataset version

    Returns:
        BenchmarkDataset ready for evaluation
    """
    audio_dir = Path(audio_dir)
    samples = []

    for filename, ground_truth in labels.items():
        audio_path = audio_dir / filename
        if audio_path.exists():
            samples.append(BenchmarkSample(
                id=filename,
                audio_path=audio_path,
                ground_truth=ground_truth,
            ))
        else:
            logger.warning(f"Audio file not found: {audio_path}")

    # Infer categories from labels
    categories = set()
    for gt in labels.values():
        if isinstance(gt, dict):
            if "amp" in gt and isinstance(gt["amp"], dict):
                categories.add("amp_family")
            if "cab" in gt and isinstance(gt["cab"], dict):
                categories.add("speaker_character")

    return BenchmarkDataset(
        name=name,
        version=version,
        samples=samples,
        categories=list(categories),
    )
