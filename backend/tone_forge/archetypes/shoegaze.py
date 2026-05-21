"""Shoegaze production archetype.

Shoegaze is characterized by:
- Massive walls of reverb and distortion
- Heavily processed guitars
- Buried vocals
- Layered, swirling textures
- Slow tempo, dreamlike feel
- Extensive use of effects pedals
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


def create_shoegaze_archetype() -> ProductionArchetype:
    """Create the shoegaze production archetype."""

    audio = AudioCharacteristics(
        # Extreme reverb
        reverb_density_range=(0.7, 0.98),
        typical_reverb_time=5.0,

        # Delay often used
        uses_delay=True,
        typical_delay_time_ms=500.0,

        # Transients completely smeared
        transient_clarity=TransientClarity.SMEARED,
        attack_preservation=0.2,

        # Very dense harmonics from distortion
        harmonic_complexity=HarmonicComplexity.DENSE,
        typical_detuning_cents=20.0,
        octave_doubling_common=True,

        # Mid-focused, not too bright
        bass_emphasis=0.6,
        brightness=0.4,
        sub_bass_present=False,

        # Very wide stereo
        stereo_width=0.95,
        stereo_effects_common=True,
    )

    extraction = ExtractionParameters(
        # Very low thresholds for smeared attacks
        onset_threshold_multiplier=0.5,
        frame_threshold_multiplier=0.55,

        # Very aggressive merging
        note_merge_time_ms=150.0,
        note_merge_aggression=0.9,
        min_note_duration_ms=100.0,
        max_note_duration_ms=30000.0,

        # Minimal quantization
        quantization_strength=0.3,
        preferred_grid_division=8,
        swing_amount=0.0,

        # Less effect suppression (effects are intentional)
        delay_detection_sensitivity=0.3,
        reverb_tail_threshold=0.15,
        ghost_note_sensitivity=0.8,

        # Soft velocity
        velocity_curve="soft",
        velocity_range=(30, 90),
        normalize_velocity=True,
    )

    patterns = ExpectedPatterns(
        # Low density
        note_density_range=(0.2, 2.0),
        typical_phrase_length_bars=8,

        # Compressed dynamics
        velocity_range=(40, 80),
        velocity_variation=0.15,

        # Very long sustains
        typical_sustain_ratio=0.9,
        uses_long_notes=True,
        uses_staccato=False,

        # Simple chord shapes
        common_intervals=[5, 7, 12],
        chord_common=True,
        monophonic_expected=False,
    )

    return ProductionArchetype(
        name="shoegaze",
        description="Wall of sound with heavy reverb, distortion, and dreamlike textures",
        audio_characteristics=audio,
        extraction_parameters=extraction,
        expected_patterns=patterns,
        applicable_stems=["guitar", "synth", "vocals", "bass"],
        related_genres=[
            "dream_pop",
            "noise_pop",
            "post_rock",
            "ethereal",
            "ambient",
            "slowcore",
        ],
        base_confidence_adjustment=-0.05,  # Expect lower confidence due to density
    )


SHOEGAZE = create_shoegaze_archetype()


def create_dream_pop_archetype() -> ProductionArchetype:
    """Create dream pop variant (lighter shoegaze)."""
    base = create_shoegaze_archetype()

    base.name = "dream_pop"
    base.description = "Lighter shoegaze with cleaner production and more pop sensibility"

    # Less extreme
    base.audio_characteristics.reverb_density_range = (0.5, 0.85)
    base.audio_characteristics.transient_clarity = TransientClarity.SOFT
    base.audio_characteristics.attack_preservation = 0.4
    base.audio_characteristics.harmonic_complexity = HarmonicComplexity.LAYERED

    # Better detection
    base.extraction_parameters.onset_threshold_multiplier = 0.65
    base.extraction_parameters.frame_threshold_multiplier = 0.7
    base.extraction_parameters.quantization_strength = 0.5

    base.expected_patterns.note_density_range = (0.5, 3.0)
    base.expected_patterns.velocity_range = (45, 95)

    base.related_genres = ["shoegaze", "indie_pop", "ethereal", "chillwave"]
    base.base_confidence_adjustment = 0.0

    return base


DREAM_POP = create_dream_pop_archetype()
