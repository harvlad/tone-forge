"""Ambient production archetype.

Ambient is characterized by:
- Extremely long sustains and evolving textures
- Minimal rhythmic content
- Heavy use of reverb and delay
- Granular and spectral processing
- Very slow evolution
- Focus on atmosphere over melody
"""
from __future__ import annotations

from .base import (
    AudioCharacteristics,
    ExtractionParameters,
    ExpectedPatterns,
    HarmonicComplexity,
    ProductionArchetype,
    TransientClarity,
)


def create_ambient_archetype() -> ProductionArchetype:
    """Create the ambient production archetype."""

    audio = AudioCharacteristics(
        # Maximum reverb
        reverb_density_range=(0.6, 0.99),
        typical_reverb_time=8.0,

        # Long delays
        uses_delay=True,
        typical_delay_time_ms=1000.0,

        # No clear transients
        transient_clarity=TransientClarity.SMEARED,
        attack_preservation=0.1,

        # Can be simple or complex
        harmonic_complexity=HarmonicComplexity.LAYERED,
        typical_detuning_cents=5.0,
        octave_doubling_common=False,

        # Varies widely
        bass_emphasis=0.3,
        brightness=0.4,
        sub_bass_present=False,

        # Often mono or subtle stereo
        stereo_width=0.6,
        stereo_effects_common=True,
    )

    extraction = ExtractionParameters(
        # Very low thresholds
        onset_threshold_multiplier=0.4,
        frame_threshold_multiplier=0.45,

        # Maximum merging
        note_merge_time_ms=300.0,
        note_merge_aggression=0.95,
        min_note_duration_ms=200.0,
        max_note_duration_ms=60000.0,  # Very long notes

        # Minimal quantization
        quantization_strength=0.1,
        preferred_grid_division=4,
        swing_amount=0.0,

        # Don't suppress effects
        delay_detection_sensitivity=0.2,
        reverb_tail_threshold=0.1,
        ghost_note_sensitivity=0.9,

        # Very soft velocity
        velocity_curve="soft",
        velocity_range=(20, 70),
        normalize_velocity=True,
    )

    patterns = ExpectedPatterns(
        # Very low density
        note_density_range=(0.05, 0.5),
        typical_phrase_length_bars=16,

        # Minimal dynamics
        velocity_range=(30, 60),
        velocity_variation=0.1,

        # Extremely long sustains
        typical_sustain_ratio=0.95,
        uses_long_notes=True,
        uses_staccato=False,

        # Simple intervals, drones
        common_intervals=[5, 7, 12],
        chord_common=False,
        monophonic_expected=True,
    )

    return ProductionArchetype(
        name="ambient",
        description="Atmospheric, textural music with long sustains and minimal rhythm",
        audio_characteristics=audio,
        extraction_parameters=extraction,
        expected_patterns=patterns,
        applicable_stems=["synth", "pad", "keys", "guitar", "texture"],
        related_genres=[
            "drone",
            "dark_ambient",
            "space_music",
            "new_age",
            "soundscape",
            "meditative",
            "atmospheric",
        ],
        base_confidence_adjustment=-0.1,  # Expect lower confidence
    )


AMBIENT = create_ambient_archetype()


def create_drone_archetype() -> ProductionArchetype:
    """Create drone variant (extreme ambient)."""
    base = create_ambient_archetype()

    base.name = "drone"
    base.description = "Sustained tones with minimal change, focus on harmonic content"

    # Even more extreme
    base.audio_characteristics.reverb_density_range = (0.8, 1.0)
    base.audio_characteristics.typical_reverb_time = 15.0

    base.extraction_parameters.onset_threshold_multiplier = 0.3
    base.extraction_parameters.note_merge_time_ms = 500.0
    base.extraction_parameters.note_merge_aggression = 0.98
    base.extraction_parameters.quantization_strength = 0.0

    base.expected_patterns.note_density_range = (0.01, 0.2)
    base.expected_patterns.typical_sustain_ratio = 0.99

    base.related_genres = ["ambient", "dark_ambient", "noise", "experimental"]
    base.base_confidence_adjustment = -0.15

    return base


DRONE = create_drone_archetype()


def create_dark_ambient_archetype() -> ProductionArchetype:
    """Create dark ambient variant."""
    base = create_ambient_archetype()

    base.name = "dark_ambient"
    base.description = "Dark, atmospheric ambient with dissonant and unsettling elements"

    base.audio_characteristics.brightness = 0.25
    base.audio_characteristics.bass_emphasis = 0.5
    base.audio_characteristics.sub_bass_present = True

    base.expected_patterns.common_intervals = [1, 2, 6, 11]  # Dissonant intervals
    base.expected_patterns.velocity_range = (25, 65)

    base.related_genres = ["ambient", "drone", "industrial", "noise"]
    base.base_confidence_adjustment = -0.1

    return base


DARK_AMBIENT = create_dark_ambient_archetype()
