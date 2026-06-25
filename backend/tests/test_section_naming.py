"""Hermetic unit tests for ``section_naming.derive_section_types``.

Construct ``RoleDecision`` sequences by hand and assert the derived
``SectionType`` output. No audio, no MIDI, no pipeline. Mirrors the
testing pattern of ``test_role_classifier.py``.
"""

from __future__ import annotations

from tone_forge.analysis.section_naming import (
    SectionNamingThresholds,
    derive_section_types,
)
from tone_forge.analysis.sections import SectionType
from tone_forge.song_form.role_classifier import RoleDecision


def _rd(role: str, conf: float = 0.9) -> RoleDecision:
    """Shorthand for constructing a RoleDecision."""
    return RoleDecision(role=role, confidence=conf)  # type: ignore[arg-type]


def test_empty_input_returns_empty_tuple():
    assert derive_section_types(()) == ()
    assert derive_section_types([]) == ()


def test_single_anchor_section():
    out = derive_section_types((_rd("ANCHOR"),))
    assert out == (SectionType.CHORUS,)


def test_single_unique_section_lands_on_intro():
    # Single section: is_first wins over is_last (position default).
    out = derive_section_types((_rd("UNIQUE"),))
    assert out == (SectionType.INTRO,)


def test_single_development_section_lands_on_verse():
    out = derive_section_types((_rd("DEVELOPMENT"),))
    assert out == (SectionType.VERSE,)


def test_all_anchor_three_sections_all_chorus():
    out = derive_section_types(
        (_rd("ANCHOR"), _rd("ANCHOR"), _rd("ANCHOR"))
    )
    assert out == (SectionType.CHORUS, SectionType.CHORUS, SectionType.CHORUS)


def test_all_development_all_verse():
    out = derive_section_types(
        (_rd("DEVELOPMENT"), _rd("DEVELOPMENT"), _rd("DEVELOPMENT"))
    )
    assert out == (SectionType.VERSE, SectionType.VERSE, SectionType.VERSE)


def test_all_unique_three_sections_intro_bridge_outro():
    out = derive_section_types(
        (_rd("UNIQUE"), _rd("UNIQUE"), _rd("UNIQUE"))
    )
    assert out == (SectionType.INTRO, SectionType.BRIDGE, SectionType.OUTRO)


def test_canonical_pop_arrangement():
    # U A D A D A U  →  INTRO CHORUS VERSE CHORUS VERSE CHORUS OUTRO
    sequence = (
        _rd("UNIQUE"),
        _rd("ANCHOR"),
        _rd("DEVELOPMENT"),
        _rd("ANCHOR"),
        _rd("DEVELOPMENT"),
        _rd("ANCHOR"),
        _rd("UNIQUE"),
    )
    out = derive_section_types(sequence)
    assert out == (
        SectionType.INTRO,
        SectionType.CHORUS,
        SectionType.VERSE,
        SectionType.CHORUS,
        SectionType.VERSE,
        SectionType.CHORUS,
        SectionType.OUTRO,
    )


def test_verse_chorus_bridge_arrangement():
    # U D A D A U A U  →  INTRO VERSE CHORUS VERSE CHORUS BRIDGE CHORUS OUTRO
    sequence = (
        _rd("UNIQUE"),
        _rd("DEVELOPMENT"),
        _rd("ANCHOR"),
        _rd("DEVELOPMENT"),
        _rd("ANCHOR"),
        _rd("UNIQUE"),
        _rd("ANCHOR"),
        _rd("UNIQUE"),
    )
    out = derive_section_types(sequence)
    assert out == (
        SectionType.INTRO,
        SectionType.VERSE,
        SectionType.CHORUS,
        SectionType.VERSE,
        SectionType.CHORUS,
        SectionType.BRIDGE,
        SectionType.CHORUS,
        SectionType.OUTRO,
    )


def test_low_confidence_falls_through_to_position_default():
    # Middle section has ANCHOR role but confidence below floor (0.30):
    # should be VERSE (position default), not CHORUS.
    sequence = (
        _rd("ANCHOR", conf=0.9),
        _rd("ANCHOR", conf=0.10),   # below floor — fall through
        _rd("ANCHOR", conf=0.9),
    )
    out = derive_section_types(sequence)
    assert out == (SectionType.CHORUS, SectionType.VERSE, SectionType.CHORUS)


def test_low_confidence_at_first_edge_falls_to_intro():
    sequence = (
        _rd("ANCHOR", conf=0.05),   # below floor — first → INTRO
        _rd("ANCHOR", conf=0.9),
    )
    out = derive_section_types(sequence)
    assert out == (SectionType.INTRO, SectionType.CHORUS)


def test_low_confidence_at_last_edge_falls_to_outro():
    sequence = (
        _rd("ANCHOR", conf=0.9),
        _rd("ANCHOR", conf=0.05),   # below floor — last → OUTRO
    )
    out = derive_section_types(sequence)
    assert out == (SectionType.CHORUS, SectionType.OUTRO)


def test_threshold_knob_wires_through():
    # Same sequence, two different threshold values: confirms the
    # threshold dataclass is actually consulted.
    sequence = (
        _rd("ANCHOR", conf=0.9),
        _rd("ANCHOR", conf=0.5),    # mid-confidence
        _rd("ANCHOR", conf=0.9),
    )
    # Default floor (0.30) → middle stays CHORUS.
    out_default = derive_section_types(sequence)
    assert out_default == (
        SectionType.CHORUS,
        SectionType.CHORUS,
        SectionType.CHORUS,
    )

    # Aggressive floor (0.80) → middle flips to VERSE (position default).
    aggressive = SectionNamingThresholds(confidence_floor=0.80)
    out_aggressive = derive_section_types(sequence, aggressive)
    assert out_aggressive == (
        SectionType.CHORUS,
        SectionType.VERSE,
        SectionType.CHORUS,
    )


def test_defensive_unknown_role_falls_to_position_default():
    # If role_classifier is extended with a new role value we don't
    # recognise yet, section_naming must not crash — it falls through
    # to the position default. We bypass the Literal type by forging
    # the role string directly.
    forged_unknown = RoleDecision.__new__(RoleDecision)
    object.__setattr__(forged_unknown, "role", "MYSTERY")
    object.__setattr__(forged_unknown, "confidence", 0.9)

    sequence = (
        _rd("UNIQUE"),
        forged_unknown,
        _rd("UNIQUE"),
    )
    out = derive_section_types(sequence)
    # First → INTRO, middle (unknown) → VERSE (position default), last → OUTRO.
    assert out == (SectionType.INTRO, SectionType.VERSE, SectionType.OUTRO)


def test_confidence_at_floor_is_included():
    # confidence == floor should NOT fall through (we use strict <).
    sequence = (_rd("ANCHOR", conf=0.30),)
    out = derive_section_types(sequence)
    assert out == (SectionType.CHORUS,)
