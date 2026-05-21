"""Base classes for production archetypes.

Production archetypes encode genre-specific assumptions about audio
characteristics, expected note patterns, and extraction parameters.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class TransientClarity(str, Enum):
    """How clear transient attacks typically are."""

    SHARP = "sharp"  # Clear, defined attacks (drums, plucks)
    MEDIUM = "medium"  # Moderate attacks (piano, guitar)
    SOFT = "soft"  # Gentle attacks (pads, swells)
    SMEARED = "smeared"  # Heavily processed/reverbed


class HarmonicComplexity(str, Enum):
    """Harmonic layering complexity."""

    SIMPLE = "simple"  # Single voice, clear harmonics
    MODERATE = "moderate"  # Some layering
    LAYERED = "layered"  # Multiple layers, detuning
    DENSE = "dense"  # Very complex, many simultaneous sources


class RhythmicProfile(str, Enum):
    """Rhythmic characteristics."""

    STRAIGHT = "straight"  # On-grid, quantized
    SWING = "swing"  # Swing timing
    LOOSE = "loose"  # Human feel, slight timing variations
    FREE = "free"  # No strict timing (ambient, experimental)


class DynamicRange(str, Enum):
    """Dynamic range characteristics."""

    COMPRESSED = "compressed"  # Heavy compression, consistent levels
    MODERATE = "moderate"  # Some dynamics preserved
    WIDE = "wide"  # Large dynamic range
    EXTREME = "extreme"  # Very wide dynamics (classical, jazz)


@dataclass
class AudioCharacteristics:
    """Expected audio characteristics for an archetype."""

    # Reverb/space
    reverb_density_range: Tuple[float, float] = (0.2, 0.5)
    typical_reverb_time: float = 1.0  # seconds
    uses_delay: bool = False
    typical_delay_time_ms: Optional[float] = None

    # Transients and attacks
    transient_clarity: TransientClarity = TransientClarity.MEDIUM
    attack_preservation: float = 0.7  # How much attacks are preserved

    # Harmonic content
    harmonic_complexity: HarmonicComplexity = HarmonicComplexity.MODERATE
    typical_detuning_cents: float = 0.0  # Detuning in synths
    octave_doubling_common: bool = False

    # Frequency content
    bass_emphasis: float = 0.5  # 0-1
    brightness: float = 0.5  # 0-1, high freq content
    sub_bass_present: bool = False

    # Stereo
    stereo_width: float = 0.5  # 0=mono, 1=wide
    stereo_effects_common: bool = False  # Chorus, haas, etc.


@dataclass
class ExtractionParameters:
    """Extraction parameter adjustments for an archetype."""

    # Onset/frame detection
    onset_threshold_multiplier: float = 1.0
    frame_threshold_multiplier: float = 1.0

    # Note processing
    note_merge_time_ms: float = 50.0  # Merge notes closer than this
    note_merge_aggression: float = 0.5  # How aggressively to merge
    min_note_duration_ms: float = 50.0
    max_note_duration_ms: float = 10000.0

    # Quantization
    quantization_strength: float = 0.7  # 0-1
    preferred_grid_division: int = 16  # 16th notes
    swing_amount: float = 0.0  # 0-1

    # Effect suppression
    delay_detection_sensitivity: float = 0.5
    reverb_tail_threshold: float = 0.3
    ghost_note_sensitivity: float = 0.5  # Sensitivity to quiet notes

    # Velocity
    velocity_curve: str = "linear"  # linear, soft, hard
    velocity_range: Tuple[int, int] = (30, 127)
    normalize_velocity: bool = False

    def apply_to_context(self, context) -> None:
        """Apply these parameters to an ExtractionContext."""
        context.onset_threshold *= self.onset_threshold_multiplier
        context.frame_threshold *= self.frame_threshold_multiplier
        context.min_note_ms = self.min_note_duration_ms


@dataclass
class ExpectedPatterns:
    """Expected musical patterns for an archetype."""

    # Note density
    note_density_range: Tuple[float, float] = (1.0, 5.0)  # notes/second
    typical_phrase_length_bars: int = 4

    # Velocity patterns
    velocity_range: Tuple[int, int] = (40, 110)
    velocity_variation: float = 0.3  # How much velocity varies

    # Sustain characteristics
    typical_sustain_ratio: float = 0.5  # ratio of sustain to total duration
    uses_long_notes: bool = False
    uses_staccato: bool = False

    # Harmonic patterns
    common_intervals: List[int] = field(default_factory=lambda: [3, 4, 5, 7, 12])
    chord_common: bool = True
    monophonic_expected: bool = False


@dataclass
class ProductionArchetype:
    """Complete production archetype for a genre/style.

    Archetypes encode assumptions about how audio in a particular genre
    typically sounds and how it should be processed for reconstruction.
    """

    name: str
    description: str

    # Component configs
    audio_characteristics: AudioCharacteristics
    extraction_parameters: ExtractionParameters
    expected_patterns: ExpectedPatterns

    # Applicable stem types
    applicable_stems: List[str] = field(
        default_factory=lambda: ["bass", "synth", "keys", "guitar", "vocals"]
    )

    # Related genres (for fallback)
    related_genres: List[str] = field(default_factory=list)

    # Confidence adjustments
    base_confidence_adjustment: float = 0.0  # Added to confidence scores

    def get_extraction_params(
        self,
        stem_type: Optional[str] = None,
    ) -> ExtractionParameters:
        """Get extraction parameters, optionally adjusted for stem type.

        Args:
            stem_type: Type of stem being processed

        Returns:
            ExtractionParameters (possibly adjusted)
        """
        params = self.extraction_parameters

        # Adjust for specific stem types
        if stem_type == "bass":
            # Bass needs cleaner detection
            params = ExtractionParameters(
                onset_threshold_multiplier=params.onset_threshold_multiplier * 1.1,
                frame_threshold_multiplier=params.frame_threshold_multiplier * 1.1,
                note_merge_time_ms=params.note_merge_time_ms * 0.8,
                note_merge_aggression=params.note_merge_aggression * 0.7,
                quantization_strength=params.quantization_strength * 1.1,
                min_note_duration_ms=params.min_note_duration_ms,
                max_note_duration_ms=params.max_note_duration_ms,
                preferred_grid_division=params.preferred_grid_division,
                swing_amount=params.swing_amount,
                delay_detection_sensitivity=params.delay_detection_sensitivity,
                reverb_tail_threshold=params.reverb_tail_threshold,
                ghost_note_sensitivity=params.ghost_note_sensitivity * 0.8,
                velocity_curve=params.velocity_curve,
                velocity_range=params.velocity_range,
                normalize_velocity=params.normalize_velocity,
            )

        elif stem_type in ("pad", "synth"):
            # Pads need gentler detection
            params = ExtractionParameters(
                onset_threshold_multiplier=params.onset_threshold_multiplier * 0.8,
                frame_threshold_multiplier=params.frame_threshold_multiplier * 0.85,
                note_merge_time_ms=params.note_merge_time_ms * 1.5,
                note_merge_aggression=params.note_merge_aggression * 1.2,
                quantization_strength=params.quantization_strength * 0.8,
                min_note_duration_ms=params.min_note_duration_ms,
                max_note_duration_ms=params.max_note_duration_ms * 2,
                preferred_grid_division=params.preferred_grid_division,
                swing_amount=params.swing_amount,
                delay_detection_sensitivity=params.delay_detection_sensitivity * 1.2,
                reverb_tail_threshold=params.reverb_tail_threshold * 0.8,
                ghost_note_sensitivity=params.ghost_note_sensitivity * 1.2,
                velocity_curve=params.velocity_curve,
                velocity_range=params.velocity_range,
                normalize_velocity=params.normalize_velocity,
            )

        return params

    def is_applicable(self, genre: str) -> bool:
        """Check if this archetype applies to a genre.

        Args:
            genre: Genre to check

        Returns:
            True if applicable
        """
        genre_lower = genre.lower()
        if genre_lower == self.name.lower():
            return True
        return any(
            genre_lower == related.lower()
            for related in self.related_genres
        )

    def get_quality_thresholds(self) -> Dict[str, float]:
        """Get quality thresholds appropriate for this archetype."""
        audio = self.audio_characteristics

        thresholds = {
            "min_stem_quality": 0.4,
            "max_contamination": 0.6,
            "max_reverb_density": 0.8,
            "min_transient_integrity": 0.3,
        }

        # Adjust for reverb-heavy genres
        if audio.reverb_density_range[1] > 0.7:
            thresholds["max_reverb_density"] = 0.95
            thresholds["min_transient_integrity"] = 0.2

        # Adjust for clean genres
        if audio.transient_clarity == TransientClarity.SHARP:
            thresholds["min_transient_integrity"] = 0.5
            thresholds["min_stem_quality"] = 0.5

        return thresholds

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "name": self.name,
            "description": self.description,
            "applicable_stems": self.applicable_stems,
            "related_genres": self.related_genres,
            "audio_characteristics": {
                "reverb_density_range": self.audio_characteristics.reverb_density_range,
                "transient_clarity": self.audio_characteristics.transient_clarity.value,
                "harmonic_complexity": self.audio_characteristics.harmonic_complexity.value,
            },
            "extraction_parameters": {
                "onset_threshold_multiplier": self.extraction_parameters.onset_threshold_multiplier,
                "quantization_strength": self.extraction_parameters.quantization_strength,
                "note_merge_time_ms": self.extraction_parameters.note_merge_time_ms,
            },
            "expected_patterns": {
                "note_density_range": self.expected_patterns.note_density_range,
                "velocity_range": self.expected_patterns.velocity_range,
            },
        }
