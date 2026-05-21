"""Reconstruction priors for extraction guidance.

Provides prior information based on genre, archetype, and historical
patterns to guide and constrain extraction.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .base import ProductionArchetype, ExtractionParameters
from .registry import get_archetype, get_archetype_or_default

logger = logging.getLogger(__name__)


@dataclass
class ExtractionPriors:
    """Prior information to guide MIDI extraction."""

    # Note density expectations
    expected_note_density: Tuple[float, float] = (0.5, 5.0)  # notes/sec
    density_confidence: float = 0.5

    # Velocity expectations
    expected_velocity_range: Tuple[int, int] = (30, 127)
    expected_velocity_mean: float = 80.0
    velocity_confidence: float = 0.5

    # Timing expectations
    expected_tempo_range: Tuple[float, float] = (60, 180)
    quantization_strength: float = 0.7
    swing_amount: float = 0.0

    # Note duration expectations
    expected_sustain_ratio: float = 0.5  # sustain / total
    min_note_ms: float = 50.0
    max_note_ms: float = 10000.0

    # Pitch expectations
    expected_pitch_range: Tuple[int, int] = (24, 96)  # MIDI note numbers
    pitch_confidence: float = 0.5

    # Effect expectations
    likely_effects: List[str] = field(default_factory=list)
    effect_suppression_strength: float = 0.5

    # Threshold suggestions
    suggested_onset_threshold: float = 0.5
    suggested_frame_threshold: float = 0.4
    threshold_confidence: float = 0.5

    # Source information
    source_archetype: Optional[str] = None
    source_genre: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "expected_note_density": self.expected_note_density,
            "expected_velocity_range": self.expected_velocity_range,
            "expected_tempo_range": self.expected_tempo_range,
            "quantization_strength": self.quantization_strength,
            "expected_sustain_ratio": self.expected_sustain_ratio,
            "likely_effects": self.likely_effects,
            "suggested_onset_threshold": self.suggested_onset_threshold,
            "suggested_frame_threshold": self.suggested_frame_threshold,
            "source_archetype": self.source_archetype,
            "source_genre": self.source_genre,
        }


@dataclass
class ValidationBounds:
    """Bounds for validating extraction results."""

    # Note count bounds
    min_notes: int = 1
    max_notes: int = 10000
    warn_few_notes: int = 5
    warn_many_notes: int = 1000

    # Density bounds (notes/sec)
    min_density: float = 0.01
    max_density: float = 50.0

    # Velocity bounds
    min_velocity: int = 1
    max_velocity: int = 127
    warn_low_velocity_ratio: float = 0.5  # Warn if >50% notes below expected

    # Duration bounds
    min_duration_ms: float = 10.0
    max_duration_ms: float = 60000.0

    # Pitch bounds
    min_pitch: int = 0
    max_pitch: int = 127

    def validate(self, notes: List, duration: float) -> List[str]:
        """Validate extraction results against bounds.

        Args:
            notes: Extracted notes
            duration: Audio duration in seconds

        Returns:
            List of warning messages
        """
        warnings = []

        if len(notes) < self.min_notes:
            warnings.append(f"Too few notes: {len(notes)}")
        elif len(notes) > self.max_notes:
            warnings.append(f"Too many notes: {len(notes)}")
        elif len(notes) < self.warn_few_notes:
            warnings.append(f"Very few notes ({len(notes)}) - may be incomplete")

        if duration > 0 and len(notes) > 0:
            density = len(notes) / duration
            if density < self.min_density:
                warnings.append(f"Very low note density: {density:.2f}/sec")
            elif density > self.max_density:
                warnings.append(f"Very high note density: {density:.2f}/sec")

        return warnings


class ReconstructionPriors:
    """Generates reconstruction priors for extraction.

    Uses archetype information and optional historical data to
    generate priors that guide and constrain extraction.
    """

    def __init__(
        self,
        use_archetypes: bool = True,
        use_historical: bool = False,
    ):
        """Initialize reconstruction priors.

        Args:
            use_archetypes: Whether to use archetype information
            use_historical: Whether to use historical data (future)
        """
        self.use_archetypes = use_archetypes
        self.use_historical = use_historical

    def get_priors(
        self,
        genre: Optional[str] = None,
        stem_type: Optional[str] = None,
        embedding: Optional[np.ndarray] = None,
    ) -> ExtractionPriors:
        """Get extraction priors.

        Args:
            genre: Detected or specified genre
            stem_type: Type of stem
            embedding: Audio embedding (for similarity lookup, future)

        Returns:
            ExtractionPriors with guidance information
        """
        priors = ExtractionPriors()

        # Get archetype-based priors
        if self.use_archetypes and genre:
            archetype = get_archetype(genre)
            if archetype:
                priors = self._priors_from_archetype(archetype, stem_type)
                priors.source_archetype = archetype.name
                priors.source_genre = genre

        # Adjust for stem type
        if stem_type:
            priors = self._adjust_for_stem_type(priors, stem_type)

        # Future: blend with historical data
        # if self.use_historical and embedding is not None:
        #     historical_priors = self._get_historical_priors(embedding)
        #     priors = self._blend_priors(priors, historical_priors)

        return priors

    def get_validation_bounds(
        self,
        genre: Optional[str] = None,
        stem_type: Optional[str] = None,
    ) -> ValidationBounds:
        """Get validation bounds for results.

        Args:
            genre: Genre
            stem_type: Stem type

        Returns:
            ValidationBounds for checking results
        """
        bounds = ValidationBounds()

        if genre:
            archetype = get_archetype(genre)
            if archetype:
                patterns = archetype.expected_patterns

                # Adjust bounds based on expected patterns
                min_density, max_density = patterns.note_density_range
                bounds.min_density = min_density * 0.5
                bounds.max_density = max_density * 2.0

                min_vel, max_vel = patterns.velocity_range
                bounds.min_velocity = max(1, min_vel - 20)
                bounds.max_velocity = min(127, max_vel + 20)

        if stem_type == "bass":
            bounds.min_pitch = 24
            bounds.max_pitch = 72
        elif stem_type == "vocals":
            bounds.min_pitch = 36
            bounds.max_pitch = 84

        return bounds

    def _priors_from_archetype(
        self,
        archetype: ProductionArchetype,
        stem_type: Optional[str],
    ) -> ExtractionPriors:
        """Build priors from an archetype.

        Args:
            archetype: Production archetype
            stem_type: Stem type for adjustments

        Returns:
            ExtractionPriors
        """
        audio = archetype.audio_characteristics
        extraction = archetype.get_extraction_params(stem_type)
        patterns = archetype.expected_patterns

        # Determine likely effects
        effects = []
        if audio.uses_delay:
            effects.append("delay")
        if audio.reverb_density_range[1] > 0.5:
            effects.append("reverb")
        if audio.stereo_effects_common:
            effects.append("chorus")

        return ExtractionPriors(
            expected_note_density=patterns.note_density_range,
            density_confidence=0.7,

            expected_velocity_range=patterns.velocity_range,
            expected_velocity_mean=sum(patterns.velocity_range) / 2,
            velocity_confidence=0.6,

            expected_tempo_range=(80, 140),  # Could be more genre-specific
            quantization_strength=extraction.quantization_strength,
            swing_amount=extraction.swing_amount,

            expected_sustain_ratio=patterns.typical_sustain_ratio,
            min_note_ms=extraction.min_note_duration_ms,
            max_note_ms=extraction.max_note_duration_ms,

            expected_pitch_range=(24, 96),
            pitch_confidence=0.5,

            likely_effects=effects,
            effect_suppression_strength=extraction.delay_detection_sensitivity,

            suggested_onset_threshold=0.5 * extraction.onset_threshold_multiplier,
            suggested_frame_threshold=0.4 * extraction.frame_threshold_multiplier,
            threshold_confidence=0.7,
        )

    def _adjust_for_stem_type(
        self,
        priors: ExtractionPriors,
        stem_type: str,
    ) -> ExtractionPriors:
        """Adjust priors for specific stem type.

        Args:
            priors: Base priors
            stem_type: Stem type

        Returns:
            Adjusted priors
        """
        stem_lower = stem_type.lower()

        if stem_lower == "bass":
            priors.expected_pitch_range = (24, 60)
            priors.pitch_confidence = 0.8
            priors.expected_velocity_range = (50, 120)
            priors.quantization_strength = min(1.0, priors.quantization_strength * 1.2)

        elif stem_lower == "vocals":
            priors.expected_pitch_range = (48, 84)
            priors.pitch_confidence = 0.6
            priors.expected_sustain_ratio = 0.6
            priors.quantization_strength = max(0.3, priors.quantization_strength * 0.7)

        elif stem_lower in ("pad", "synth"):
            priors.expected_sustain_ratio = max(0.6, priors.expected_sustain_ratio)
            priors.max_note_ms = max(10000, priors.max_note_ms)
            priors.quantization_strength = max(0.3, priors.quantization_strength * 0.8)

        elif stem_lower == "drums":
            priors.expected_sustain_ratio = 0.1
            priors.min_note_ms = 20.0
            priors.max_note_ms = 1000.0
            priors.quantization_strength = min(1.0, priors.quantization_strength * 1.3)

        return priors

    def apply_to_extraction_context(
        self,
        context,
        priors: ExtractionPriors,
    ) -> None:
        """Apply priors to an extraction context.

        Args:
            context: ExtractionContext to modify
            priors: Priors to apply
        """
        # Adjust thresholds if confidence is high enough
        if priors.threshold_confidence > 0.5:
            blend = priors.threshold_confidence
            context.onset_threshold = (
                context.onset_threshold * (1 - blend) +
                priors.suggested_onset_threshold * blend
            )
            context.frame_threshold = (
                context.frame_threshold * (1 - blend) +
                priors.suggested_frame_threshold * blend
            )

        # Set note duration bounds
        context.min_note_ms = priors.min_note_ms


# Module-level singleton
_priors: Optional[ReconstructionPriors] = None


def get_priors_generator() -> ReconstructionPriors:
    """Get the global priors generator.

    Returns:
        ReconstructionPriors instance
    """
    global _priors
    if _priors is None:
        _priors = ReconstructionPriors()
    return _priors


def get_extraction_priors(
    genre: Optional[str] = None,
    stem_type: Optional[str] = None,
) -> ExtractionPriors:
    """Convenience function to get extraction priors.

    Args:
        genre: Genre
        stem_type: Stem type

    Returns:
        ExtractionPriors
    """
    return get_priors_generator().get_priors(genre=genre, stem_type=stem_type)
