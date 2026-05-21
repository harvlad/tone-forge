"""Dataset builder for creating benchmark datasets.

Provides tools for programmatically creating and managing benchmark
datasets with ground truth labels.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime

from .benchmarks import BenchmarkDataset, BenchmarkSample

logger = logging.getLogger(__name__)


@dataclass
class SampleMetadata:
    """Metadata for a benchmark sample."""

    genre: str = ""
    complexity: str = "medium"  # simple, medium, complex
    known_challenges: List[str] = field(default_factory=list)
    source: str = ""
    duration_sec: float = 0.0
    bpm: Optional[float] = None
    key: Optional[str] = None
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "genre": self.genre,
            "complexity": self.complexity,
            "known_challenges": self.known_challenges,
            "source": self.source,
            "duration_sec": self.duration_sec,
            "bpm": self.bpm,
            "key": self.key,
            "tags": self.tags,
        }


@dataclass
class GroundTruthDescriptor:
    """Ground truth descriptor labels."""

    # Amp settings
    amp_family: str = "unknown"
    amp_gain: float = 0.5
    amp_voicing: Dict[str, float] = field(default_factory=dict)

    # Cab settings
    cab_speaker_character: str = "unknown"
    cab_mic_position: str = "unknown"

    # Effects
    effects_present: List[str] = field(default_factory=list)
    delay_time_ms: Optional[float] = None
    reverb_size: Optional[str] = None

    # MIDI ground truth (optional)
    midi_notes: Optional[List[Tuple[int, float, float, int]]] = None
    expected_note_count: Optional[int] = None

    def to_dict(self) -> dict:
        """Convert to standard descriptor format."""
        descriptor = {
            "amp": {
                "family": self.amp_family,
                "gain": self.amp_gain,
                "voicing": self.amp_voicing or {
                    "bass": 0.5,
                    "mid": 0.5,
                    "treble": 0.5,
                    "presence": 0.5,
                },
            },
            "cab": {
                "speaker_character": self.cab_speaker_character,
                "mic_position": self.cab_mic_position,
            },
            "effects": {},
        }

        # Add effects
        for effect in self.effects_present:
            if effect == "delay":
                descriptor["effects"]["delay"] = {
                    "time_ms": self.delay_time_ms or 300,
                    "active": True,
                }
            elif effect == "reverb":
                descriptor["effects"]["reverb"] = {
                    "size": self.reverb_size or "medium",
                    "active": True,
                }
            elif effect == "modulation":
                descriptor["effects"]["modulation"] = {"active": True}
            elif effect == "compressor":
                descriptor["effects"]["compressor"] = {"active": True}

        # Add MIDI if present
        if self.midi_notes:
            descriptor["midi_ground_truth"] = {
                "notes": self.midi_notes,
                "note_count": len(self.midi_notes),
            }
        elif self.expected_note_count:
            descriptor["midi_ground_truth"] = {
                "expected_note_count": self.expected_note_count,
            }

        return descriptor


class BenchmarkDatasetBuilder:
    """Builder for creating benchmark datasets.

    Provides a fluent interface for constructing benchmark datasets
    with validation and metadata.
    """

    def __init__(
        self,
        name: str,
        version: str = "1.0",
        description: str = "",
    ):
        """Initialize the builder.

        Args:
            name: Dataset name
            version: Dataset version
            description: Dataset description
        """
        self.name = name
        self.version = version
        self.description = description
        self.samples: List[Tuple[Path, GroundTruthDescriptor, SampleMetadata]] = []
        self.categories: List[str] = []
        self.metadata: Dict[str, Any] = {
            "created": datetime.now().isoformat(),
            "description": description,
        }

    def add_sample(
        self,
        audio_path: Path,
        ground_truth: GroundTruthDescriptor,
        metadata: Optional[SampleMetadata] = None,
    ) -> "BenchmarkDatasetBuilder":
        """Add a sample to the dataset.

        Args:
            audio_path: Path to audio file
            ground_truth: Ground truth descriptor
            metadata: Optional sample metadata

        Returns:
            Self for chaining
        """
        audio_path = Path(audio_path)
        if not audio_path.exists():
            logger.warning(f"Audio file not found: {audio_path}")

        self.samples.append((
            audio_path,
            ground_truth,
            metadata or SampleMetadata(),
        ))
        return self

    def add_samples_from_directory(
        self,
        directory: Path,
        labels: Dict[str, GroundTruthDescriptor],
        metadata_map: Optional[Dict[str, SampleMetadata]] = None,
        extensions: Tuple[str, ...] = (".wav", ".mp3", ".flac"),
    ) -> "BenchmarkDatasetBuilder":
        """Add samples from a directory.

        Args:
            directory: Directory containing audio files
            labels: Mapping from filename to ground truth
            metadata_map: Optional mapping from filename to metadata
            extensions: Audio file extensions to include

        Returns:
            Self for chaining
        """
        directory = Path(directory)
        metadata_map = metadata_map or {}

        for audio_path in directory.iterdir():
            if audio_path.suffix.lower() in extensions:
                filename = audio_path.name
                if filename in labels:
                    self.add_sample(
                        audio_path,
                        labels[filename],
                        metadata_map.get(filename),
                    )
                else:
                    logger.warning(f"No labels for {filename}")

        return self

    def add_category(self, category: str) -> "BenchmarkDatasetBuilder":
        """Add a category for filtering.

        Args:
            category: Category name

        Returns:
            Self for chaining
        """
        if category not in self.categories:
            self.categories.append(category)
        return self

    def set_metadata(self, key: str, value: Any) -> "BenchmarkDatasetBuilder":
        """Set metadata value.

        Args:
            key: Metadata key
            value: Metadata value

        Returns:
            Self for chaining
        """
        self.metadata[key] = value
        return self

    def build(self) -> BenchmarkDataset:
        """Build the benchmark dataset.

        Returns:
            BenchmarkDataset ready for evaluation
        """
        samples = []

        for audio_path, ground_truth, metadata in self.samples:
            # Generate sample ID from file hash
            sample_id = self._generate_sample_id(audio_path)

            samples.append(BenchmarkSample(
                id=sample_id,
                audio_path=audio_path,
                ground_truth=ground_truth.to_dict(),
                metadata=metadata.to_dict(),
            ))

        # Auto-detect categories
        categories = list(self.categories)
        if samples:
            if "amp" in samples[0].ground_truth:
                if "amp_family" not in categories:
                    categories.append("amp_family")
            if "cab" in samples[0].ground_truth:
                if "speaker_character" not in categories:
                    categories.append("speaker_character")

        return BenchmarkDataset(
            name=self.name,
            version=self.version,
            samples=samples,
            categories=categories,
            metadata=self.metadata,
        )

    def save(self, path: Path) -> BenchmarkDataset:
        """Build and save the dataset.

        Args:
            path: Output path for JSON file

        Returns:
            Built BenchmarkDataset
        """
        dataset = self.build()
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        with open(path, "w") as f:
            json.dump(dataset.to_dict(), f, indent=2, default=str)

        logger.info(f"Saved benchmark dataset to {path}")
        return dataset

    def _generate_sample_id(self, audio_path: Path) -> str:
        """Generate unique sample ID from file path."""
        # Use filename + first 8 chars of path hash
        path_hash = hashlib.md5(str(audio_path).encode()).hexdigest()[:8]
        return f"{audio_path.stem}_{path_hash}"


def create_synthwave_benchmark(
    audio_dir: Path,
    output_path: Optional[Path] = None,
) -> BenchmarkDataset:
    """Create a synthwave-focused benchmark dataset.

    This is a template for creating genre-specific benchmarks.

    Args:
        audio_dir: Directory containing synthwave audio samples
        output_path: Optional path to save the dataset

    Returns:
        BenchmarkDataset for synthwave evaluation
    """
    builder = BenchmarkDatasetBuilder(
        name="synthwave_benchmark",
        version="1.0",
        description="Synthwave-focused benchmark for pad and synth extraction",
    )

    builder.set_metadata("genre_focus", "synthwave")
    builder.set_metadata("expected_challenges", [
        "heavy_reverb",
        "layered_synths",
        "soft_attacks",
        "wide_stereo",
    ])

    builder.add_category("reverb_density")
    builder.add_category("synth_type")

    # This would be populated with actual samples
    # For now, just return an empty dataset structure

    if output_path:
        return builder.save(output_path)
    return builder.build()


def validate_dataset(dataset: BenchmarkDataset) -> List[str]:
    """Validate a benchmark dataset.

    Args:
        dataset: Dataset to validate

    Returns:
        List of validation errors (empty if valid)
    """
    errors = []

    if len(dataset.samples) == 0:
        errors.append("Dataset has no samples")

    for sample in dataset.samples:
        # Check audio file exists
        if not sample.audio_path.exists():
            errors.append(f"Audio file not found: {sample.audio_path}")

        # Check ground truth has required fields
        gt = sample.ground_truth
        if "amp" not in gt:
            errors.append(f"Sample {sample.id} missing 'amp' in ground truth")
        elif "family" not in gt.get("amp", {}):
            errors.append(f"Sample {sample.id} missing amp family")

    return errors
