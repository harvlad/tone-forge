"""Synthwave/Retrowave production archetype.

Synthwave is characterized by:
- Heavy reverb and delay on synths
- Soft attack pads with layered detuning
- Punchy bass with sub content
- 80s-inspired drum machines
- Gated reverb on snares
- Wide stereo imaging
- Typically 100-120 BPM
"""
from __future__ import annotations

from .base import (
    AudioCharacteristics,
    DynamicRange,
    ExtractionParameters,
    ExpectedPatterns,
    HarmonicComplexity,
    ProductionArchetype,
    RhythmicProfile,
    TransientClarity,
)


def create_synthwave_archetype() -> ProductionArchetype:
    """Create the synthwave production archetype."""

    audio = AudioCharacteristics(
        # Heavy reverb is signature
        reverb_density_range=(0.5, 0.85),
        typical_reverb_time=2.5,

        # Delay is very common
        uses_delay=True,
        typical_delay_time_ms=375.0,  # Dotted eighth at 120 BPM

        # Soft attacks on pads, but punchy drums
        transient_clarity=TransientClarity.SOFT,
        attack_preservation=0.5,

        # Layered, detuned synths
        harmonic_complexity=HarmonicComplexity.LAYERED,
        typical_detuning_cents=15.0,
        octave_doubling_common=True,

        # Strong bass and bright synths
        bass_emphasis=0.7,
        brightness=0.6,
        sub_bass_present=True,

        # Wide stereo, lots of chorus
        stereo_width=0.8,
        stereo_effects_common=True,
    )

    extraction = ExtractionParameters(
        # Lower thresholds for soft attacks
        onset_threshold_multiplier=0.7,
        frame_threshold_multiplier=0.75,

        # Merge more aggressively (layered sounds)
        note_merge_time_ms=80.0,
        note_merge_aggression=0.75,
        min_note_duration_ms=60.0,
        max_note_duration_ms=15000.0,  # Long pad notes

        # Less strict quantization
        quantization_strength=0.6,
        preferred_grid_division=16,
        swing_amount=0.05,  # Slight swing

        # Aggressive effect detection
        delay_detection_sensitivity=0.7,
        reverb_tail_threshold=0.25,
        ghost_note_sensitivity=0.6,

        # Moderate velocity
        velocity_curve="soft",
        velocity_range=(40, 110),
        normalize_velocity=False,
    )

    patterns = ExpectedPatterns(
        # Moderate density
        note_density_range=(0.5, 4.0),
        typical_phrase_length_bars=8,

        # Controlled dynamics
        velocity_range=(50, 100),
        velocity_variation=0.25,

        # Long sustains common
        typical_sustain_ratio=0.7,
        uses_long_notes=True,
        uses_staccato=False,

        # Common synthwave intervals
        common_intervals=[3, 4, 5, 7, 12, 15, 16],  # Including octave+
        chord_common=True,
        monophonic_expected=False,
    )

    return ProductionArchetype(
        name="synthwave",
        description="80s-inspired electronic with heavy reverb, layered synths, and punchy drums",
        audio_characteristics=audio,
        extraction_parameters=extraction,
        expected_patterns=patterns,
        applicable_stems=["synth", "bass", "keys", "pad", "lead"],
        related_genres=[
            "retrowave",
            "outrun",
            "darkwave",
            "dreamwave",
            "vaporwave",
            "chillwave",
            "cyberpunk",
        ],
        base_confidence_adjustment=0.0,
    )


# Pre-built instance
SYNTHWAVE = create_synthwave_archetype()


# Variant: Darkwave (darker, more aggressive)
def create_darkwave_archetype() -> ProductionArchetype:
    """Create darkwave variant (darker synthwave)."""
    base = create_synthwave_archetype()

    # Darker characteristics
    base.name = "darkwave"
    base.description = "Darker synthwave with more aggressive tones and heavier bass"

    base.audio_characteristics.brightness = 0.4
    base.audio_characteristics.bass_emphasis = 0.8
    base.audio_characteristics.reverb_density_range = (0.4, 0.75)

    base.extraction_parameters.onset_threshold_multiplier = 0.8
    base.extraction_parameters.ghost_note_sensitivity = 0.5

    base.related_genres = ["synthwave", "industrial", "ebm", "goth"]

    return base


DARKWAVE = create_darkwave_archetype()


# Variant: Dreamwave (more atmospheric)
def create_dreamwave_archetype() -> ProductionArchetype:
    """Create dreamwave variant (more atmospheric)."""
    base = create_synthwave_archetype()

    base.name = "dreamwave"
    base.description = "Atmospheric synthwave with more reverb and ambient elements"

    # Even more reverb
    base.audio_characteristics.reverb_density_range = (0.6, 0.95)
    base.audio_characteristics.typical_reverb_time = 4.0
    base.audio_characteristics.transient_clarity = TransientClarity.SMEARED

    # Even softer detection
    base.extraction_parameters.onset_threshold_multiplier = 0.6
    base.extraction_parameters.frame_threshold_multiplier = 0.65
    base.extraction_parameters.note_merge_aggression = 0.85
    base.extraction_parameters.quantization_strength = 0.4

    # Longer notes
    base.expected_patterns.typical_sustain_ratio = 0.85
    base.expected_patterns.note_density_range = (0.3, 2.5)

    base.related_genres = ["synthwave", "ambient", "chillwave", "vaporwave"]

    return base


DREAMWAVE = create_dreamwave_archetype()
