"""Integration test for the full H2 → role → Stage A → Stage B pipeline.

Exercises the chain that lives in ``unified_pipeline.py`` and
``local_engine/analysis_worker.py``:

    extract_h2(bundle)
        →  classify_roles(...)
        →  derive_section_types(...)             [Stage A]
        →  aggregate_song_form(per_stem, energy) [Stage B prep]
        →  refine_section_types(...)             [Stage B]

Uses synthetic chord+section bundles and hand-crafted per-stem
``SectionFeatures``-shaped dicts. No audio, no MIDI.
"""

from __future__ import annotations

from typing import Any

from tone_forge.analysis.section_naming import derive_section_types
from tone_forge.analysis.sections import SectionType
from tone_forge.analysis.song_form import refine_section_types
from tone_forge.analysis.song_form_aggregates import aggregate_song_form
from tone_forge.song_form.h2 import extract_h2
from tone_forge.song_form.role_classifier import classify_roles


def _chord(start_s: float, end_s: float, symbol: str) -> dict[str, Any]:
    return {"start_s": start_s, "end_s": end_s, "symbol": symbol}


def _section(start_s: float, end_s: float, name: str = "") -> dict[str, Any]:
    s: dict[str, Any] = {"start_s": start_s, "end_s": end_s}
    if name:
        s["name"] = name
    return s


def _vocals_row(
    *, lead_activity_score: float, voiced_frame_ratio: float
) -> dict[str, float]:
    """Per-section vocal stem feature row (dict-shaped duck type)."""
    return {
        "lead_activity_score": lead_activity_score,
        "voiced_frame_ratio": voiced_frame_ratio,
        "note_count": 0.0,
        "duration_s": 4.0,
    }


def _drums_row(*, note_count: float, duration_s: float = 4.0) -> dict[str, float]:
    """Per-section drum stem feature row (dict-shaped duck type)."""
    return {
        "lead_activity_score": 0.0,
        "voiced_frame_ratio": 0.0,
        "note_count": note_count,
        "duration_s": duration_s,
    }


def _pop_bundle() -> dict[str, Any]:
    """5-section pop arrangement: INTRO / CHORUS / BRIDGE / CHORUS / OUTRO.

    s1 and s3 share the [C, F, G, Am] trigram → ANCHOR.
    s0, s2, s4 are UNIQUE.
    """
    chords = [
        # s0: G D A — unique
        _chord(0.0, 1.0, "G"),
        _chord(1.0, 2.0, "D"),
        _chord(2.0, 3.0, "A"),
        # s1: C F G Am — chord seq X
        _chord(3.0, 4.0, "C"),
        _chord(4.0, 5.0, "F"),
        _chord(5.0, 6.0, "G"),
        _chord(6.0, 7.0, "Am"),
        # s2: Am F C G — unique
        _chord(7.0, 8.0, "Am"),
        _chord(8.0, 9.0, "F"),
        _chord(9.0, 10.0, "C"),
        _chord(10.0, 11.0, "G"),
        # s3: C F G Am — chord seq X (recurs)
        _chord(11.0, 12.0, "C"),
        _chord(12.0, 13.0, "F"),
        _chord(13.0, 14.0, "G"),
        _chord(14.0, 15.0, "Am"),
        # s4: Em Bm A — unique
        _chord(15.0, 16.0, "Em"),
        _chord(16.0, 17.0, "Bm"),
        _chord(17.0, 18.0, "A"),
    ]
    sections = [
        _section(0.0, 3.0, "s0"),
        _section(3.0, 7.0, "s1"),
        _section(7.0, 11.0, "s2"),
        _section(11.0, 15.0, "s3"),
        _section(15.0, 18.0, "s4"),
    ]
    return {"chords": chords, "sections": sections}


def _stage_a_for_pop_bundle() -> tuple[SectionType, ...]:
    """Run extract_h2 → classify_roles → derive_section_types
    on the pop bundle, returning Stage A labels."""
    bundle = _pop_bundle()
    h2 = extract_h2(bundle)
    assert not h2.degenerate
    decisions = classify_roles(h2.per_section, h2.h2_sep)
    return derive_section_types(decisions)


def test_stage_b_pop_bundle_no_refinement_with_uniform_signals():
    """Stage A produces the canonical pop labels; Stage B with
    uniform per-stem signals should leave them untouched."""
    stage_a = _stage_a_for_pop_bundle()
    assert stage_a == (
        SectionType.INTRO,
        SectionType.CHORUS,
        SectionType.BRIDGE,
        SectionType.CHORUS,
        SectionType.OUTRO,
    )
    per_stem = {
        "vocals": [
            _vocals_row(lead_activity_score=0.6, voiced_frame_ratio=0.8),
        ] * 5,
        "drums": [_drums_row(note_count=16.0)] * 5,
    }
    energy_means = [0.5, 0.6, 0.5, 0.6, 0.5]
    aggregates = aggregate_song_form(per_stem, energy_means)
    refined = refine_section_types(stage_a, aggregates)
    assert refined == stage_a


def test_stage_b_pop_bundle_instrumental_chorus_via_low_vocals():
    """A CHORUS with no vocal activity becomes INSTRUMENTAL."""
    stage_a = _stage_a_for_pop_bundle()
    per_stem = {
        "vocals": [
            _vocals_row(lead_activity_score=0.6, voiced_frame_ratio=0.8),  # s0
            _vocals_row(lead_activity_score=0.02, voiced_frame_ratio=0.05),  # s1
            _vocals_row(lead_activity_score=0.6, voiced_frame_ratio=0.8),  # s2
            _vocals_row(lead_activity_score=0.6, voiced_frame_ratio=0.8),  # s3
            _vocals_row(lead_activity_score=0.6, voiced_frame_ratio=0.8),  # s4
        ],
        "drums": [_drums_row(note_count=16.0)] * 5,
    }
    energy_means = [0.5, 0.6, 0.5, 0.6, 0.5]
    aggregates = aggregate_song_form(per_stem, energy_means)
    refined = refine_section_types(stage_a, aggregates)
    assert refined[1] is SectionType.INSTRUMENTAL
    assert refined[3] is SectionType.CHORUS


def test_stage_b_pop_bundle_breakdown_via_low_drum_density():
    """A non-edge section with a drum-density dip becomes BREAKDOWN.

    The pop bundle's middle section (s2) is BRIDGE in Stage A; with a
    sharp drum dropout, Stage B reclassifies it as BREAKDOWN.
    """
    stage_a = _stage_a_for_pop_bundle()
    per_stem = {
        "vocals": [
            _vocals_row(lead_activity_score=0.6, voiced_frame_ratio=0.8),
        ] * 5,
        "drums": [
            _drums_row(note_count=16.0),  # s0
            _drums_row(note_count=16.0),  # s1
            _drums_row(note_count=1.0),   # s2 — heavy dropout
            _drums_row(note_count=16.0),  # s3
            _drums_row(note_count=16.0),  # s4
        ],
    }
    energy_means = [0.5, 0.6, 0.5, 0.6, 0.5]
    aggregates = aggregate_song_form(per_stem, energy_means)
    refined = refine_section_types(stage_a, aggregates)
    assert refined[2] is SectionType.BREAKDOWN


def test_stage_b_no_vocals_stem_keeps_chorus_labels():
    """Regression for the JAM "every section labeled instrumental" bug.

    When ``midi_stems`` contains [guitar, bass, drums] only (no vocals
    stem at all), ``aggregate_song_form`` produces
    ``vocal_activity_score == 0.0`` for every section. The Stage B
    INSTRUMENTAL rule must NOT fire on this case; Stage A's CHORUS
    labels survive.
    """
    stage_a = _stage_a_for_pop_bundle()
    per_stem = {
        "guitar": [_drums_row(note_count=8.0)] * 5,
        "bass": [_drums_row(note_count=8.0)] * 5,
        "drums": [_drums_row(note_count=16.0)] * 5,
    }
    energy_means = [0.5, 0.6, 0.5, 0.6, 0.5]
    aggregates = aggregate_song_form(per_stem, energy_means)
    refined = refine_section_types(stage_a, aggregates)
    # Stage A's chorus labels (s1, s3) must survive.
    assert refined[1] is SectionType.CHORUS
    assert refined[3] is SectionType.CHORUS
    # No INSTRUMENTAL at all.
    assert SectionType.INSTRUMENTAL not in refined


def test_stage_b_defensive_no_op_with_empty_per_stem():
    """Empty per-stem dict + empty energy_means → empty aggregates →
    refine_section_types returns Stage A verbatim (defensive)."""
    stage_a = _stage_a_for_pop_bundle()
    aggregates = aggregate_song_form({}, [])
    refined = refine_section_types(stage_a, aggregates)
    assert refined == stage_a


def test_stage_b_prechorus_via_explicit_chain():
    """End-to-end: a verse-before-chorus chain ramps into PRECHORUS.

    Constructs a Stage A label sequence directly (rather than going
    through extract_h2) because PRECHORUS detection requires a VERSE
    label and the pop bundle's H2 output is CHORUS/BRIDGE only.
    """
    stage_a = (
        SectionType.INTRO,
        SectionType.VERSE,
        SectionType.CHORUS,
        SectionType.VERSE,
        SectionType.CHORUS,
        SectionType.OUTRO,
    )
    per_stem = {
        "vocals": [
            _vocals_row(lead_activity_score=0.6, voiced_frame_ratio=0.8),
        ] * 6,
        "drums": [_drums_row(note_count=16.0)] * 6,
    }
    # Second verse (s3) ramps sharply into the second chorus (s4).
    energy_means = [0.1, 0.4, 0.5, 0.3, 0.8, 0.2]
    aggregates = aggregate_song_form(per_stem, energy_means)
    refined = refine_section_types(stage_a, aggregates)
    assert refined == (
        SectionType.INTRO,
        SectionType.VERSE,
        SectionType.CHORUS,
        SectionType.PRECHORUS,
        SectionType.CHORUS,
        SectionType.OUTRO,
    )
