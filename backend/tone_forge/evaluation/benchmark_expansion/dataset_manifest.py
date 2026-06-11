"""Dataset manifest definitions for multi-genre benchmarking.

Provides structured definitions for benchmark datasets covering multiple
genres with metadata, difficulty levels, and characteristic tags.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class SampleDifficulty(str, Enum):
    """Difficulty classification for benchmark samples."""
    EASY = "easy"           # Clean signal, clear notes
    MEDIUM = "medium"       # Some processing, moderate complexity
    HARD = "hard"           # Heavy effects, complex polyphony
    EXTREME = "extreme"     # Edge cases, challenging material


@dataclass
class GenreSpec:
    """Specification for a genre category in the benchmark.

    Defines expected characteristics and weighting for a genre.
    """
    name: str                                    # "synthwave", "edm", "trance"
    description: str = ""

    # Stem type weighting (how important each stem is for this genre)
    stem_weights: Dict[str, float] = field(default_factory=lambda: {
        "bass": 0.25,
        "lead": 0.25,
        "pad": 0.2,
        "arp": 0.15,
        "guitar": 0.15,
    })

    # Expected audio characteristics
    expected_characteristics: List[str] = field(default_factory=list)
    # e.g., ["heavy_reverb", "sidechain", "distorted_bass", "fast_arps"]

    # Sample requirements
    min_samples: int = 5
    target_samples: int = 20

    # Difficulty distribution (percentage)
    difficulty_mix: Dict[str, float] = field(default_factory=lambda: {
        "easy": 0.3,
        "medium": 0.4,
        "hard": 0.25,
        "extreme": 0.05,
    })

    # Profile recommendations for this genre
    recommended_profiles: Dict[str, str] = field(default_factory=dict)
    # e.g., {"bass": "mono_bass", "lead": "lead_staccato"}

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "name": self.name,
            "description": self.description,
            "stem_weights": self.stem_weights,
            "expected_characteristics": self.expected_characteristics,
            "min_samples": self.min_samples,
            "target_samples": self.target_samples,
            "difficulty_mix": self.difficulty_mix,
            "recommended_profiles": self.recommended_profiles,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> GenreSpec:
        """Deserialize from dictionary."""
        return cls(
            name=data["name"],
            description=data.get("description", ""),
            stem_weights=data.get("stem_weights", {}),
            expected_characteristics=data.get("expected_characteristics", []),
            min_samples=data.get("min_samples", 5),
            target_samples=data.get("target_samples", 20),
            difficulty_mix=data.get("difficulty_mix", {}),
            recommended_profiles=data.get("recommended_profiles", {}),
        )


# Predefined genre specifications
GENRE_SPECS = {
    "synthwave": GenreSpec(
        name="synthwave",
        description="80s-inspired electronic with analog synths",
        expected_characteristics=["analog_synths", "arpeggios", "pad_heavy", "reverb"],
        recommended_profiles={"bass": "mono_bass", "lead": "lead_legato", "pad": "pad_sustained"},
    ),
    "edm": GenreSpec(
        name="edm",
        description="Electronic dance music with heavy processing",
        expected_characteristics=["sidechain", "heavy_compression", "layered_synths"],
        recommended_profiles={"bass": "mono_bass", "lead": "lead_staccato"},
    ),
    "trance": GenreSpec(
        name="trance",
        description="Uplifting trance with fast arpeggios",
        expected_characteristics=["fast_arps", "supersaw_leads", "reverb_heavy"],
        recommended_profiles={"arp": "arp_fast", "lead": "lead_legato", "pad": "pad_sustained"},
    ),
    "ambient": GenreSpec(
        name="ambient",
        description="Atmospheric ambient with long sustained textures",
        expected_characteristics=["long_sustain", "reverb_heavy", "slow_evolution"],
        recommended_profiles={"pad": "pad_sustained", "lead": "lead_legato"},
        difficulty_mix={"easy": 0.2, "medium": 0.3, "hard": 0.35, "extreme": 0.15},
    ),
    "house": GenreSpec(
        name="house",
        description="House music with rhythmic bass and chords",
        expected_characteristics=["4_on_floor", "chord_stabs", "sidechain"],
        recommended_profiles={"bass": "mono_bass", "pad": "chord_stack"},
    ),
    "techno": GenreSpec(
        name="techno",
        description="Minimal techno with percussive elements",
        expected_characteristics=["minimal", "percussive_synths", "modular"],
        recommended_profiles={"bass": "mono_bass", "lead": "pluck_transient"},
    ),
    "dnb": GenreSpec(
        name="dnb",
        description="Drum and bass with fast tempo and reese bass",
        expected_characteristics=["fast_tempo", "reese_bass", "chopped_breaks"],
        recommended_profiles={"bass": "poly_bass"},
        difficulty_mix={"easy": 0.15, "medium": 0.35, "hard": 0.35, "extreme": 0.15},
    ),
    "chillwave": GenreSpec(
        name="chillwave",
        description="Lo-fi dreamy electronic with tape saturation",
        expected_characteristics=["lo_fi", "tape_saturation", "dreamy"],
        recommended_profiles={"pad": "pad_sustained", "lead": "lead_legato"},
    ),
    "industrial": GenreSpec(
        name="industrial",
        description="Aggressive industrial with distorted elements",
        expected_characteristics=["distorted", "aggressive", "noise"],
        difficulty_mix={"easy": 0.1, "medium": 0.3, "hard": 0.4, "extreme": 0.2},
    ),
    "acoustic": GenreSpec(
        name="acoustic",
        description="Acoustic instruments and clean recordings",
        expected_characteristics=["clean_signal", "natural_dynamics", "room_reverb"],
        recommended_profiles={"guitar": "guitar"},
        difficulty_mix={"easy": 0.4, "medium": 0.4, "hard": 0.15, "extreme": 0.05},
    ),
}


@dataclass
class BenchmarkSample:
    """A single sample in the benchmark dataset."""
    id: str
    audio_path: str                              # Relative to dataset root
    ground_truth_midi_path: str                  # Relative to dataset root
    stem_type: str                               # "bass", "lead", "pad", etc.
    genre: str

    # Optional metadata
    profile_hint: Optional[str] = None           # Suggested profile
    difficulty: SampleDifficulty = SampleDifficulty.MEDIUM
    tags: List[str] = field(default_factory=list)

    # Audio characteristics
    has_delay_effects: bool = False
    has_reverb: bool = False
    is_polyphonic: bool = False
    tempo_bpm: Optional[float] = None
    key: Optional[str] = None                    # e.g., "C minor"

    # Expected results
    expected_note_count_range: Optional[Tuple[int, int]] = None

    # Additional metadata
    source: str = ""                             # Origin of sample
    notes: str = ""                              # Any notes about the sample

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "id": self.id,
            "audio_path": self.audio_path,
            "ground_truth_midi_path": self.ground_truth_midi_path,
            "stem_type": self.stem_type,
            "genre": self.genre,
            "profile_hint": self.profile_hint,
            "difficulty": self.difficulty.value,
            "tags": self.tags,
            "has_delay_effects": self.has_delay_effects,
            "has_reverb": self.has_reverb,
            "is_polyphonic": self.is_polyphonic,
            "tempo_bpm": self.tempo_bpm,
            "key": self.key,
            "expected_note_count_range": self.expected_note_count_range,
            "source": self.source,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> BenchmarkSample:
        """Deserialize from dictionary."""
        difficulty = data.get("difficulty", "medium")
        if isinstance(difficulty, str):
            difficulty = SampleDifficulty(difficulty)

        return cls(
            id=data["id"],
            audio_path=data["audio_path"],
            ground_truth_midi_path=data["ground_truth_midi_path"],
            stem_type=data["stem_type"],
            genre=data["genre"],
            profile_hint=data.get("profile_hint"),
            difficulty=difficulty,
            tags=data.get("tags", []),
            has_delay_effects=data.get("has_delay_effects", False),
            has_reverb=data.get("has_reverb", False),
            is_polyphonic=data.get("is_polyphonic", False),
            tempo_bpm=data.get("tempo_bpm"),
            key=data.get("key"),
            expected_note_count_range=data.get("expected_note_count_range"),
            source=data.get("source", ""),
            notes=data.get("notes", ""),
        )


@dataclass
class DatasetManifest:
    """Complete benchmark dataset manifest.

    Contains all samples organized by genre with metadata.
    """
    name: str
    version: str
    description: str = ""

    # Directory paths
    audio_dir: str = "audio"
    ground_truth_dir: str = "midi"

    # Genre specifications
    genres: List[GenreSpec] = field(default_factory=list)

    # All samples
    samples: List[BenchmarkSample] = field(default_factory=list)

    # Metadata
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    metadata: Dict[str, Any] = field(default_factory=dict)

    def get_samples_by_genre(self, genre: str) -> List[BenchmarkSample]:
        """Get all samples for a specific genre."""
        return [s for s in self.samples if s.genre == genre]

    def get_samples_by_stem(self, stem_type: str) -> List[BenchmarkSample]:
        """Get all samples for a specific stem type."""
        return [s for s in self.samples if s.stem_type == stem_type]

    def get_samples_by_difficulty(
        self, difficulty: SampleDifficulty
    ) -> List[BenchmarkSample]:
        """Get all samples with a specific difficulty."""
        return [s for s in self.samples if s.difficulty == difficulty]

    def get_samples_by_tag(self, tag: str) -> List[BenchmarkSample]:
        """Get all samples with a specific tag."""
        return [s for s in self.samples if tag in s.tags]

    def filter_samples(
        self,
        genre: Optional[str] = None,
        stem_type: Optional[str] = None,
        difficulty: Optional[SampleDifficulty] = None,
        tags: Optional[List[str]] = None,
    ) -> List[BenchmarkSample]:
        """Filter samples by multiple criteria."""
        result = self.samples

        if genre:
            result = [s for s in result if s.genre == genre]
        if stem_type:
            result = [s for s in result if s.stem_type == stem_type]
        if difficulty:
            result = [s for s in result if s.difficulty == difficulty]
        if tags:
            result = [s for s in result if any(t in s.tags for t in tags)]

        return result

    def get_unique_genres(self) -> List[str]:
        """Get list of unique genres in the dataset."""
        return list(set(s.genre for s in self.samples))

    def get_unique_stems(self) -> List[str]:
        """Get list of unique stem types in the dataset."""
        return list(set(s.stem_type for s in self.samples))

    def get_statistics(self) -> Dict[str, Any]:
        """Get dataset statistics."""
        genre_counts = {}
        stem_counts = {}
        difficulty_counts = {}

        for sample in self.samples:
            genre_counts[sample.genre] = genre_counts.get(sample.genre, 0) + 1
            stem_counts[sample.stem_type] = stem_counts.get(sample.stem_type, 0) + 1
            diff = sample.difficulty.value
            difficulty_counts[diff] = difficulty_counts.get(diff, 0) + 1

        return {
            "total_samples": len(self.samples),
            "genres": len(genre_counts),
            "genre_distribution": genre_counts,
            "stem_distribution": stem_counts,
            "difficulty_distribution": difficulty_counts,
        }

    def validate(self) -> List[str]:
        """Validate the manifest for completeness and consistency.

        Returns list of validation errors (empty if valid).
        """
        errors = []

        # Check required fields
        if not self.name:
            errors.append("Dataset name is required")
        if not self.version:
            errors.append("Dataset version is required")

        # Check for duplicate sample IDs
        ids = [s.id for s in self.samples]
        duplicates = [id for id in ids if ids.count(id) > 1]
        if duplicates:
            errors.append(f"Duplicate sample IDs: {set(duplicates)}")

        # Check genre coverage
        genre_names = {g.name for g in self.genres}
        sample_genres = {s.genre for s in self.samples}

        missing_genres = sample_genres - genre_names
        if missing_genres:
            errors.append(f"Samples reference undefined genres: {missing_genres}")

        # Check minimum samples per genre
        for genre in self.genres:
            count = len(self.get_samples_by_genre(genre.name))
            if count < genre.min_samples:
                errors.append(
                    f"Genre '{genre.name}' has {count} samples, "
                    f"minimum is {genre.min_samples}"
                )

        return errors

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "audio_dir": self.audio_dir,
            "ground_truth_dir": self.ground_truth_dir,
            "genres": [g.to_dict() for g in self.genres],
            "samples": [s.to_dict() for s in self.samples],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> DatasetManifest:
        """Deserialize from dictionary."""
        return cls(
            name=data["name"],
            version=data["version"],
            description=data.get("description", ""),
            audio_dir=data.get("audio_dir", "audio"),
            ground_truth_dir=data.get("ground_truth_dir", "midi"),
            genres=[GenreSpec.from_dict(g) for g in data.get("genres", [])],
            samples=[BenchmarkSample.from_dict(s) for s in data.get("samples", [])],
            created_at=data.get("created_at", datetime.now().isoformat()),
            updated_at=data.get("updated_at", datetime.now().isoformat()),
            metadata=data.get("metadata", {}),
        )

    def save(self, path: Path) -> None:
        """Save manifest to JSON file."""
        self.updated_at = datetime.now().isoformat()
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load(cls, path: Path) -> DatasetManifest:
        """Load manifest from JSON file."""
        with open(path) as f:
            data = json.load(f)
        return cls.from_dict(data)


class ManifestBuilder:
    """Builder for creating dataset manifests incrementally."""

    def __init__(self, name: str, version: str = "1.0.0"):
        self.manifest = DatasetManifest(name=name, version=version)
        self._sample_counter = 0

    def set_description(self, description: str) -> ManifestBuilder:
        """Set dataset description."""
        self.manifest.description = description
        return self

    def set_directories(
        self,
        audio_dir: str = "audio",
        ground_truth_dir: str = "midi",
    ) -> ManifestBuilder:
        """Set directory paths."""
        self.manifest.audio_dir = audio_dir
        self.manifest.ground_truth_dir = ground_truth_dir
        return self

    def add_genre(self, spec: GenreSpec) -> ManifestBuilder:
        """Add a genre specification."""
        self.manifest.genres.append(spec)
        return self

    def add_predefined_genre(self, name: str) -> ManifestBuilder:
        """Add a predefined genre by name."""
        if name in GENRE_SPECS:
            self.manifest.genres.append(GENRE_SPECS[name])
        else:
            raise ValueError(f"Unknown genre: {name}. Available: {list(GENRE_SPECS.keys())}")
        return self

    def add_sample(self, sample: BenchmarkSample) -> ManifestBuilder:
        """Add a benchmark sample."""
        self.manifest.samples.append(sample)
        return self

    def add_sample_from_paths(
        self,
        audio_path: str,
        midi_path: str,
        stem_type: str,
        genre: str,
        **kwargs,
    ) -> ManifestBuilder:
        """Add a sample from file paths with auto-generated ID."""
        self._sample_counter += 1
        sample_id = kwargs.pop("id", f"{genre}_{stem_type}_{self._sample_counter:04d}")

        sample = BenchmarkSample(
            id=sample_id,
            audio_path=audio_path,
            ground_truth_midi_path=midi_path,
            stem_type=stem_type,
            genre=genre,
            **kwargs,
        )
        self.manifest.samples.append(sample)
        return self

    def add_samples_from_directory(
        self,
        base_path: Path,
        genre: str,
        audio_pattern: str = "*.wav",
        midi_pattern: str = "*.mid",
    ) -> ManifestBuilder:
        """Discover and add samples from a directory structure.

        Expected structure:
        base_path/
          bass/
            sample1.wav
            sample1.mid
          lead/
            sample2.wav
            sample2.mid
        """
        base = Path(base_path)

        for stem_dir in base.iterdir():
            if not stem_dir.is_dir():
                continue

            stem_type = stem_dir.name.lower()

            # Find audio files
            audio_files = list(stem_dir.glob(audio_pattern))

            for audio_file in audio_files:
                # Look for matching MIDI file
                midi_name = audio_file.stem + ".mid"
                midi_file = stem_dir / midi_name

                if not midi_file.exists():
                    midi_name = audio_file.stem + ".midi"
                    midi_file = stem_dir / midi_name

                if midi_file.exists():
                    self.add_sample_from_paths(
                        audio_path=str(audio_file.relative_to(base)),
                        midi_path=str(midi_file.relative_to(base)),
                        stem_type=stem_type,
                        genre=genre,
                    )

        return self

    def set_metadata(self, key: str, value: Any) -> ManifestBuilder:
        """Set metadata value."""
        self.manifest.metadata[key] = value
        return self

    def validate(self) -> List[str]:
        """Validate the manifest being built."""
        return self.manifest.validate()

    def build(self) -> DatasetManifest:
        """Build and return the final manifest.

        Raises ValueError if validation fails.
        """
        errors = self.validate()
        if errors:
            raise ValueError(f"Manifest validation failed: {errors}")
        return self.manifest
