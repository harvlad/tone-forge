"""MIDI extraction benchmark infrastructure.

Provides profile-aware MIDI benchmarking with:
- Per-profile/stem/genre metric breakdowns
- Regression tracking against baselines
- Failure heatmap generation
- Support for ground truth MIDI comparison

This enables principled iteration on MIDI extraction without
regression on previously-working cases.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

from .metrics import MIDIQualityMetrics, compute_midi_quality

logger = logging.getLogger(__name__)


@dataclass
class MIDIBenchmarkSample:
    """A single sample in a MIDI benchmark dataset.

    Includes metadata for profile-aware evaluation.
    """
    id: str
    audio_path: Path
    ground_truth_midi_path: Optional[Path] = None
    ground_truth_notes: Optional[List[Tuple[int, float, float, int]]] = None

    # Metadata for stratified evaluation
    stem_type: str = "other"
    profile_hint: Optional[str] = None  # Suggested profile
    genre: Optional[str] = None
    tags: List[str] = field(default_factory=list)  # e.g., ["staccato", "reverb_heavy"]

    # Expected characteristics
    expected_note_count_range: Optional[Tuple[int, int]] = None
    has_delay_effects: bool = False
    has_reverb: bool = False
    is_polyphonic: bool = False

    def to_dict(self) -> Dict:
        """Serialize to dictionary."""
        return {
            "id": self.id,
            "audio_path": str(self.audio_path),
            "ground_truth_midi_path": str(self.ground_truth_midi_path) if self.ground_truth_midi_path else None,
            "stem_type": self.stem_type,
            "profile_hint": self.profile_hint,
            "genre": self.genre,
            "tags": self.tags,
            "has_delay_effects": self.has_delay_effects,
            "has_reverb": self.has_reverb,
            "is_polyphonic": self.is_polyphonic,
        }

    @classmethod
    def from_dict(cls, d: Dict, base_path: Optional[Path] = None) -> "MIDIBenchmarkSample":
        """Deserialize from dictionary."""
        audio_path = Path(d["audio_path"])
        if base_path and not audio_path.is_absolute():
            audio_path = base_path / audio_path

        gt_midi_path = None
        if d.get("ground_truth_midi_path"):
            gt_midi_path = Path(d["ground_truth_midi_path"])
            if base_path and not gt_midi_path.is_absolute():
                gt_midi_path = base_path / gt_midi_path

        return cls(
            id=d["id"],
            audio_path=audio_path,
            ground_truth_midi_path=gt_midi_path,
            stem_type=d.get("stem_type", "other"),
            profile_hint=d.get("profile_hint"),
            genre=d.get("genre"),
            tags=d.get("tags", []),
            has_delay_effects=d.get("has_delay_effects", False),
            has_reverb=d.get("has_reverb", False),
            is_polyphonic=d.get("is_polyphonic", False),
        )


@dataclass
class MIDIBenchmarkDataset:
    """A MIDI benchmark dataset with profile-aware samples."""
    name: str
    version: str
    samples: List[MIDIBenchmarkSample]
    metadata: Dict = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.samples)

    def filter_by_stem(self, stem_type: str) -> "MIDIBenchmarkDataset":
        """Filter samples by stem type."""
        filtered = [s for s in self.samples if s.stem_type == stem_type]
        return MIDIBenchmarkDataset(
            name=f"{self.name}_stem_{stem_type}",
            version=self.version,
            samples=filtered,
        )

    def filter_by_profile(self, profile_name: str) -> "MIDIBenchmarkDataset":
        """Filter samples by profile hint."""
        filtered = [s for s in self.samples if s.profile_hint == profile_name]
        return MIDIBenchmarkDataset(
            name=f"{self.name}_profile_{profile_name}",
            version=self.version,
            samples=filtered,
        )

    def filter_by_genre(self, genre: str) -> "MIDIBenchmarkDataset":
        """Filter samples by genre."""
        filtered = [s for s in self.samples if s.genre == genre]
        return MIDIBenchmarkDataset(
            name=f"{self.name}_genre_{genre}",
            version=self.version,
            samples=filtered,
        )

    def filter_by_tag(self, tag: str) -> "MIDIBenchmarkDataset":
        """Filter samples by tag."""
        filtered = [s for s in self.samples if tag in s.tags]
        return MIDIBenchmarkDataset(
            name=f"{self.name}_tag_{tag}",
            version=self.version,
            samples=filtered,
        )

    def get_stems(self) -> List[str]:
        """Get unique stem types."""
        return list(set(s.stem_type for s in self.samples))

    def get_profiles(self) -> List[str]:
        """Get unique profile hints."""
        return list(set(s.profile_hint for s in self.samples if s.profile_hint))

    def get_genres(self) -> List[str]:
        """Get unique genres."""
        return list(set(s.genre for s in self.samples if s.genre))

    def get_tags(self) -> List[str]:
        """Get unique tags."""
        tags = set()
        for s in self.samples:
            tags.update(s.tags)
        return list(tags)

    def to_dict(self) -> Dict:
        """Serialize to dictionary."""
        return {
            "name": self.name,
            "version": self.version,
            "samples": [s.to_dict() for s in self.samples],
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: Dict, base_path: Optional[Path] = None) -> "MIDIBenchmarkDataset":
        """Deserialize from dictionary."""
        samples = [
            MIDIBenchmarkSample.from_dict(s, base_path)
            for s in d.get("samples", [])
        ]
        return cls(
            name=d["name"],
            version=d["version"],
            samples=samples,
            metadata=d.get("metadata", {}),
        )

    @classmethod
    def load(cls, path: Path) -> "MIDIBenchmarkDataset":
        """Load from JSON file."""
        path = Path(path)
        with open(path, "r") as f:
            data = json.load(f)
        return cls.from_dict(data, base_path=path.parent)

    def save(self, path: Path):
        """Save to JSON file."""
        path = Path(path)
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)


@dataclass
class ProfiledMIDIMetrics:
    """MIDI metrics with per-profile/stem/genre breakdowns."""

    # Overall metrics
    overall_f1: float = 0.0
    overall_precision: float = 0.0
    overall_recall: float = 0.0

    # Per-profile breakdown
    per_profile_f1: Dict[str, float] = field(default_factory=dict)
    per_profile_precision: Dict[str, float] = field(default_factory=dict)
    per_profile_recall: Dict[str, float] = field(default_factory=dict)
    per_profile_count: Dict[str, int] = field(default_factory=dict)

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

    # Per-tag breakdown
    per_tag_f1: Dict[str, float] = field(default_factory=dict)

    # Regression tracking
    regression_from_baseline: Dict[str, float] = field(default_factory=dict)

    # Failure analysis
    worst_samples: List[Tuple[str, float]] = field(default_factory=list)
    failure_reasons: Dict[str, int] = field(default_factory=dict)

    # Execution metadata
    num_samples: int = 0
    execution_time_sec: float = 0.0

    def to_dict(self) -> Dict:
        """Serialize to dictionary."""
        return {
            "overall": {
                "f1": self.overall_f1,
                "precision": self.overall_precision,
                "recall": self.overall_recall,
            },
            "per_profile": {
                "f1": self.per_profile_f1,
                "precision": self.per_profile_precision,
                "recall": self.per_profile_recall,
                "count": self.per_profile_count,
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
            "per_tag": {
                "f1": self.per_tag_f1,
            },
            "regression": self.regression_from_baseline,
            "worst_samples": self.worst_samples,
            "failure_reasons": self.failure_reasons,
            "num_samples": self.num_samples,
            "execution_time_sec": self.execution_time_sec,
        }

    def summary(self) -> str:
        """Generate human-readable summary."""
        lines = [
            f"MIDI Benchmark Results ({self.num_samples} samples)",
            "=" * 50,
            "",
            "Overall Metrics:",
            f"  F1:        {self.overall_f1:.1%}",
            f"  Precision: {self.overall_precision:.1%}",
            f"  Recall:    {self.overall_recall:.1%}",
        ]

        if self.per_profile_f1:
            lines.extend(["", "Per-Profile F1:"])
            for profile, f1 in sorted(self.per_profile_f1.items(), key=lambda x: -x[1]):
                count = self.per_profile_count.get(profile, 0)
                lines.append(f"  {profile}: {f1:.1%} (n={count})")

        if self.per_stem_f1:
            lines.extend(["", "Per-Stem F1:"])
            for stem, f1 in sorted(self.per_stem_f1.items(), key=lambda x: -x[1]):
                count = self.per_stem_count.get(stem, 0)
                lines.append(f"  {stem}: {f1:.1%} (n={count})")

        if self.per_genre_f1:
            lines.extend(["", "Per-Genre F1:"])
            for genre, f1 in sorted(self.per_genre_f1.items(), key=lambda x: -x[1]):
                count = self.per_genre_count.get(genre, 0)
                lines.append(f"  {genre}: {f1:.1%} (n={count})")

        if self.regression_from_baseline:
            lines.extend(["", "Regression from Baseline:"])
            for key, delta in sorted(self.regression_from_baseline.items(), key=lambda x: x[1]):
                status = "↓" if delta < 0 else "↑" if delta > 0 else "="
                lines.append(f"  {key}: {delta:+.1%} {status}")

        if self.worst_samples:
            lines.extend(["", "Worst Performing Samples:"])
            for sample_id, f1 in self.worst_samples[:5]:
                lines.append(f"  {sample_id}: {f1:.1%}")

        lines.extend(["", f"Execution time: {self.execution_time_sec:.1f}s"])

        return "\n".join(lines)


@dataclass
class SampleResult:
    """Result from evaluating a single sample."""
    sample_id: str
    success: bool
    metrics: Optional[MIDIQualityMetrics] = None
    profile_used: Optional[str] = None
    profile_auto_classified: bool = False
    extracted_note_count: int = 0
    ground_truth_note_count: int = 0
    error: Optional[str] = None
    execution_time_ms: float = 0.0

    def to_dict(self) -> Dict:
        """Serialize to dictionary."""
        return {
            "sample_id": self.sample_id,
            "success": self.success,
            "metrics": self.metrics.to_dict() if self.metrics else None,
            "profile_used": self.profile_used,
            "profile_auto_classified": self.profile_auto_classified,
            "extracted_note_count": self.extracted_note_count,
            "ground_truth_note_count": self.ground_truth_note_count,
            "error": self.error,
            "execution_time_ms": self.execution_time_ms,
        }


class MIDIBenchmarkRunner:
    """Run MIDI extraction benchmarks with profile awareness.

    Supports:
    - Profile-specific extraction
    - Auto-classification evaluation
    - Per-profile/stem/genre metrics
    - Regression tracking against baselines
    """

    def __init__(
        self,
        extractor_factory: Optional[Callable] = None,
        use_auto_classify: bool = True,
        baseline_path: Optional[Path] = None,
    ):
        """Initialize the benchmark runner.

        Args:
            extractor_factory: Factory function to create extractors
            use_auto_classify: Whether to use auto-classification
            baseline_path: Path to baseline metrics JSON for regression tracking
        """
        self.extractor_factory = extractor_factory
        self.use_auto_classify = use_auto_classify
        self.baseline = None

        if baseline_path and baseline_path.exists():
            with open(baseline_path, "r") as f:
                self.baseline = json.load(f)

    def run(
        self,
        dataset: MIDIBenchmarkDataset,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> Tuple[ProfiledMIDIMetrics, List[SampleResult]]:
        """Run benchmark on dataset.

        Args:
            dataset: Dataset to evaluate
            progress_callback: Optional callback(current, total)

        Returns:
            (ProfiledMIDIMetrics, list of SampleResult)
        """
        start_time = time.time()
        sample_results: List[SampleResult] = []

        # Accumulators for metrics
        all_metrics: List[Tuple[MIDIBenchmarkSample, MIDIQualityMetrics]] = []

        for i, sample in enumerate(dataset.samples):
            if progress_callback:
                progress_callback(i, len(dataset.samples))

            result = self._evaluate_sample(sample)
            sample_results.append(result)

            if result.success and result.metrics:
                all_metrics.append((sample, result.metrics))

        # Compute aggregate metrics
        metrics = self._compute_aggregate_metrics(all_metrics, dataset)
        metrics.num_samples = len(dataset.samples)
        metrics.execution_time_sec = time.time() - start_time

        # Compute regression if baseline available
        if self.baseline:
            metrics.regression_from_baseline = self._compute_regression(metrics)

        # Find worst samples
        if all_metrics:
            sorted_by_f1 = sorted(
                [(s.id, m.note_f1) for s, m in all_metrics],
                key=lambda x: x[1]
            )
            metrics.worst_samples = sorted_by_f1[:10]

        return metrics, sample_results

    def _evaluate_sample(self, sample: MIDIBenchmarkSample) -> SampleResult:
        """Evaluate a single sample."""
        sample_start = time.time()

        try:
            # Load ground truth MIDI
            ground_truth_notes = sample.ground_truth_notes
            if ground_truth_notes is None and sample.ground_truth_midi_path:
                ground_truth_notes = self._load_midi_notes(sample.ground_truth_midi_path)

            if ground_truth_notes is None:
                return SampleResult(
                    sample_id=sample.id,
                    success=False,
                    error="No ground truth MIDI available",
                )

            # Extract MIDI
            extracted_notes, profile_used, auto_classified = self._extract_midi(sample)

            # Compute metrics
            extracted_tuples = [
                (n.pitch, n.start, n.end, n.velocity)
                for n in extracted_notes
            ]
            metrics = compute_midi_quality(extracted_tuples, ground_truth_notes)

            return SampleResult(
                sample_id=sample.id,
                success=True,
                metrics=metrics,
                profile_used=profile_used,
                profile_auto_classified=auto_classified,
                extracted_note_count=len(extracted_notes),
                ground_truth_note_count=len(ground_truth_notes),
                execution_time_ms=(time.time() - sample_start) * 1000,
            )

        except Exception as e:
            logger.warning(f"Failed to evaluate {sample.id}: {e}")
            return SampleResult(
                sample_id=sample.id,
                success=False,
                error=str(e),
                execution_time_ms=(time.time() - sample_start) * 1000,
            )

    def _extract_midi(
        self,
        sample: MIDIBenchmarkSample,
    ) -> Tuple[List, Optional[str], bool]:
        """Extract MIDI from sample audio.

        Returns (notes, profile_used, auto_classified).
        """
        # Import here to avoid circular dependency
        try:
            import librosa
            from ..midi import MultiPassExtractor, get_profile, classify_profile
        except ImportError as e:
            raise RuntimeError(f"Required modules not available: {e}")

        # Load audio
        audio, sr = librosa.load(str(sample.audio_path), sr=22050, mono=True)

        # Create extractor
        if self.extractor_factory:
            extractor = self.extractor_factory()
        else:
            extractor = MultiPassExtractor()

        # Determine profile
        profile = None
        profile_name = None
        auto_classified = False

        if sample.profile_hint:
            profile = get_profile(sample.profile_hint)
            profile_name = sample.profile_hint
        elif self.use_auto_classify:
            classification = classify_profile(audio, sr, sample.stem_type)
            profile = get_profile(classification.profile_name)
            profile_name = classification.profile_name
            auto_classified = True

        # Run extraction
        result = extractor.extract(
            audio, sr,
            stem_type=sample.stem_type,
            genre=sample.genre,
            profile=profile,
            auto_classify=not sample.profile_hint and self.use_auto_classify,
        )

        return result.notes, profile_name, auto_classified

    def _load_midi_notes(
        self,
        midi_path: Path,
    ) -> Optional[List[Tuple[int, float, float, int]]]:
        """Load notes from MIDI file."""
        try:
            import mido
        except ImportError:
            logger.warning("mido not available for MIDI loading")
            return None

        try:
            mid = mido.MidiFile(str(midi_path))
            notes = []
            current_time = 0.0
            active_notes: Dict[int, Tuple[float, int]] = {}  # pitch -> (start, velocity)

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

    def _compute_aggregate_metrics(
        self,
        all_metrics: List[Tuple[MIDIBenchmarkSample, MIDIQualityMetrics]],
        dataset: MIDIBenchmarkDataset,
    ) -> ProfiledMIDIMetrics:
        """Compute aggregate metrics from sample results."""
        if not all_metrics:
            return ProfiledMIDIMetrics()

        # Overall
        overall_f1 = np.mean([m.note_f1 for _, m in all_metrics])
        overall_precision = np.mean([m.note_precision for _, m in all_metrics])
        overall_recall = np.mean([m.note_recall for _, m in all_metrics])

        # Per-profile
        per_profile_f1 = {}
        per_profile_precision = {}
        per_profile_recall = {}
        per_profile_count = {}

        for profile in dataset.get_profiles():
            profile_metrics = [m for s, m in all_metrics if s.profile_hint == profile]
            if profile_metrics:
                per_profile_f1[profile] = np.mean([m.note_f1 for m in profile_metrics])
                per_profile_precision[profile] = np.mean([m.note_precision for m in profile_metrics])
                per_profile_recall[profile] = np.mean([m.note_recall for m in profile_metrics])
                per_profile_count[profile] = len(profile_metrics)

        # Per-stem
        per_stem_f1 = {}
        per_stem_precision = {}
        per_stem_recall = {}
        per_stem_count = {}

        for stem in dataset.get_stems():
            stem_metrics = [m for s, m in all_metrics if s.stem_type == stem]
            if stem_metrics:
                per_stem_f1[stem] = np.mean([m.note_f1 for m in stem_metrics])
                per_stem_precision[stem] = np.mean([m.note_precision for m in stem_metrics])
                per_stem_recall[stem] = np.mean([m.note_recall for m in stem_metrics])
                per_stem_count[stem] = len(stem_metrics)

        # Per-genre
        per_genre_f1 = {}
        per_genre_precision = {}
        per_genre_recall = {}
        per_genre_count = {}

        for genre in dataset.get_genres():
            genre_metrics = [m for s, m in all_metrics if s.genre == genre]
            if genre_metrics:
                per_genre_f1[genre] = np.mean([m.note_f1 for m in genre_metrics])
                per_genre_precision[genre] = np.mean([m.note_precision for m in genre_metrics])
                per_genre_recall[genre] = np.mean([m.note_recall for m in genre_metrics])
                per_genre_count[genre] = len(genre_metrics)

        # Per-tag
        per_tag_f1 = {}
        for tag in dataset.get_tags():
            tag_metrics = [m for s, m in all_metrics if tag in s.tags]
            if tag_metrics:
                per_tag_f1[tag] = np.mean([m.note_f1 for m in tag_metrics])

        return ProfiledMIDIMetrics(
            overall_f1=overall_f1,
            overall_precision=overall_precision,
            overall_recall=overall_recall,
            per_profile_f1=per_profile_f1,
            per_profile_precision=per_profile_precision,
            per_profile_recall=per_profile_recall,
            per_profile_count=per_profile_count,
            per_stem_f1=per_stem_f1,
            per_stem_precision=per_stem_precision,
            per_stem_recall=per_stem_recall,
            per_stem_count=per_stem_count,
            per_genre_f1=per_genre_f1,
            per_genre_precision=per_genre_precision,
            per_genre_recall=per_genre_recall,
            per_genre_count=per_genre_count,
            per_tag_f1=per_tag_f1,
        )

    def _compute_regression(self, metrics: ProfiledMIDIMetrics) -> Dict[str, float]:
        """Compute regression from baseline."""
        if not self.baseline:
            return {}

        regression = {}

        # Overall
        if "overall" in self.baseline:
            baseline_f1 = self.baseline["overall"].get("f1", 0)
            regression["overall"] = metrics.overall_f1 - baseline_f1

        # Per-profile
        if "per_profile" in self.baseline and "f1" in self.baseline["per_profile"]:
            for profile, baseline_f1 in self.baseline["per_profile"]["f1"].items():
                if profile in metrics.per_profile_f1:
                    regression[f"profile:{profile}"] = metrics.per_profile_f1[profile] - baseline_f1

        # Per-stem
        if "per_stem" in self.baseline and "f1" in self.baseline["per_stem"]:
            for stem, baseline_f1 in self.baseline["per_stem"]["f1"].items():
                if stem in metrics.per_stem_f1:
                    regression[f"stem:{stem}"] = metrics.per_stem_f1[stem] - baseline_f1

        return regression


def save_baseline(
    metrics: ProfiledMIDIMetrics,
    path: Path,
):
    """Save metrics as baseline for future regression tracking.

    Args:
        metrics: Metrics to save as baseline
        path: Output path for baseline JSON
    """
    path = Path(path)
    with open(path, "w") as f:
        json.dump(metrics.to_dict(), f, indent=2)
    logger.info(f"Saved baseline to {path}")


def load_baseline(path: Path) -> Optional[Dict]:
    """Load baseline metrics.

    Args:
        path: Path to baseline JSON

    Returns:
        Baseline dict or None if not found
    """
    path = Path(path)
    if not path.exists():
        return None
    with open(path, "r") as f:
        return json.load(f)
