"""Hermetic unit tests for ``song_form.refine_section_types`` and
``song_form.annotate_transitions``.

Constructs ``(SectionType, SongFormAggregates)`` sequences by hand
and asserts the refined output. No audio, no MIDI, no pipeline.
Mirrors ``test_section_naming.py``.
"""

from __future__ import annotations

from tone_forge.analysis.sections import SectionType
from tone_forge.analysis.song_form import (
    SongFormThresholds,
    annotate_transitions,
    refine_section_types,
)
from tone_forge.analysis.song_form_aggregates import SongFormAggregates


def _agg(
    vocals: float = 1.0,
    drum_z: float = 0.0,
    ramp: float = 0.0,
    drum_rate: float = 0.0,
) -> SongFormAggregates:
    """Shorthand for constructing a SongFormAggregates."""
    return SongFormAggregates(
        vocal_activity_score=vocals,
        drum_density_per_s=drum_rate,
        drum_density_z=drum_z,
        energy_ramp_into_next=ramp,
    )


# ---------------------------------------------------------------------------
# refine_section_types
# ---------------------------------------------------------------------------


def test_empty_input_returns_empty_tuple():
    assert refine_section_types((), ()) == ()


def test_mismatched_aggregate_count_is_no_op():
    """Defensive: shape mismatch yields Stage A labels verbatim."""
    types = (SectionType.INTRO, SectionType.CHORUS, SectionType.OUTRO)
    aggs = (_agg(),)  # length 1, not 3
    assert refine_section_types(types, aggs) == types


def test_no_op_when_aggregates_empty():
    """Aggregates of length 0 with non-empty Stage A → no-op."""
    types = (SectionType.INTRO, SectionType.CHORUS, SectionType.OUTRO)
    assert refine_section_types(types, ()) == types


def test_chorus_with_low_vocals_becomes_instrumental():
    types = (SectionType.CHORUS,)
    aggs = (_agg(vocals=0.05),)
    assert refine_section_types(types, aggs) == (SectionType.INSTRUMENTAL,)


def test_chorus_with_high_vocals_stays_chorus():
    types = (SectionType.CHORUS,)
    aggs = (_agg(vocals=0.8),)
    assert refine_section_types(types, aggs) == (SectionType.CHORUS,)


def test_no_vocals_stem_does_not_trigger_instrumental():
    """When the vocals stem is absent entirely (every section has
    vocal_activity_score == 0.0), CHORUS labels must NOT flip to
    INSTRUMENTAL. ``aggregate_song_form`` returns 0.0 for every
    section when the vocals stem is missing; without this guard,
    every CHORUS on an instrumental song (or a song bundled with
    [guitar, bass, drums] only) would be relabeled.
    """
    types = (
        SectionType.INTRO,
        SectionType.VERSE,
        SectionType.CHORUS,
        SectionType.VERSE,
        SectionType.CHORUS,
        SectionType.OUTRO,
    )
    aggs = (_agg(vocals=0.0),) * 6
    assert refine_section_types(types, aggs) == types


def test_verse_before_chorus_with_strong_ramp_becomes_prechorus():
    types = (
        SectionType.INTRO,
        SectionType.VERSE,
        SectionType.CHORUS,
        SectionType.OUTRO,
    )
    aggs = (
        _agg(),
        _agg(vocals=0.5, ramp=0.5),   # strong ramp into next
        _agg(vocals=0.7, ramp=0.1),
        _agg(),
    )
    out = refine_section_types(types, aggs)
    assert out == (
        SectionType.INTRO,
        SectionType.PRECHORUS,
        SectionType.CHORUS,
        SectionType.OUTRO,
    )


def test_verse_before_bridge_with_strong_ramp_stays_verse():
    """PRECHORUS only fires when next section is CHORUS."""
    types = (SectionType.VERSE, SectionType.BRIDGE)
    aggs = (_agg(vocals=0.5, ramp=0.5), _agg())
    assert refine_section_types(types, aggs)[0] is SectionType.VERSE


def test_verse_before_chorus_with_weak_ramp_stays_verse():
    types = (SectionType.VERSE, SectionType.CHORUS)
    aggs = (_agg(vocals=0.5, ramp=0.1), _agg(vocals=0.8))
    assert refine_section_types(types, aggs)[0] is SectionType.VERSE


def test_verse_before_instrumental_chorus_stays_verse():
    """PRECHORUS does not fire when the next CHORUS has been
    refined to INSTRUMENTAL (rule 1 runs before rule 2)."""
    types = (SectionType.VERSE, SectionType.CHORUS)
    aggs = (
        _agg(vocals=0.5, ramp=0.6),   # verse with strong ramp
        _agg(vocals=0.02),            # chorus with no vocals → INSTRUMENTAL
    )
    out = refine_section_types(types, aggs)
    assert out == (SectionType.VERSE, SectionType.INSTRUMENTAL)


def test_low_drum_z_becomes_breakdown():
    types = (
        SectionType.INTRO,
        SectionType.VERSE,
        SectionType.CHORUS,
        SectionType.OUTRO,
    )
    aggs = (
        _agg(),
        _agg(vocals=0.5, drum_z=-1.5),   # below ceiling
        _agg(vocals=0.7, drum_z=0.2),
        _agg(),
    )
    out = refine_section_types(types, aggs)
    assert out[1] is SectionType.BREAKDOWN


def test_low_drum_z_at_edges_preserved():
    """INTRO/OUTRO with low drum z stays INTRO/OUTRO, not BREAKDOWN.
    Edge labels are more important than drum-lane dips."""
    types = (
        SectionType.INTRO,
        SectionType.CHORUS,
        SectionType.OUTRO,
    )
    aggs = (
        _agg(vocals=0.5, drum_z=-2.0),  # intro with no drums
        _agg(vocals=0.7, drum_z=0.0),
        _agg(vocals=0.5, drum_z=-2.0),  # outro with no drums
    )
    out = refine_section_types(types, aggs)
    assert out[0] is SectionType.INTRO
    assert out[2] is SectionType.OUTRO


def test_no_drum_song_does_not_trigger_breakdown():
    """When the song has no drums, drum_density_z is 0.0 everywhere
    (aggregator sets it to 0 for no-drum songs). BREAKDOWN must
    not fire."""
    types = (
        SectionType.INTRO,
        SectionType.VERSE,
        SectionType.CHORUS,
        SectionType.OUTRO,
    )
    aggs = (
        _agg(drum_z=0.0),
        _agg(vocals=0.5, drum_z=0.0),
        _agg(vocals=0.8, drum_z=0.0),
        _agg(drum_z=0.0),
    )
    assert refine_section_types(types, aggs) == types


def test_thresholds_knob_wires_through():
    """Tighter vocal_silence_ceiling re-classifies a borderline
    CHORUS that was kept by the default."""
    types = (SectionType.CHORUS,)
    aggs = (_agg(vocals=0.12),)
    # Default ceiling 0.15: 0.12 < 0.15 → INSTRUMENTAL.
    assert refine_section_types(types, aggs)[0] is SectionType.INSTRUMENTAL
    # Tighter ceiling 0.05: 0.12 NOT < 0.05 → CHORUS.
    tighter = SongFormThresholds(vocal_silence_ceiling=0.05)
    assert refine_section_types(types, aggs, tighter)[0] is SectionType.CHORUS


def test_full_canonical_chain_intro_verse_prechorus_chorus_verse_chorus_outro():
    """End-to-end: a pop arrangement where the second VERSE ramps
    into the second CHORUS yields a PRECHORUS at position 4."""
    stage_a = (
        SectionType.INTRO,
        SectionType.VERSE,
        SectionType.CHORUS,
        SectionType.VERSE,
        SectionType.CHORUS,
        SectionType.OUTRO,
    )
    aggs = (
        _agg(),
        _agg(vocals=0.6, ramp=0.1),
        _agg(vocals=0.8, ramp=0.0),
        _agg(vocals=0.6, ramp=0.5),   # ramp into next CHORUS
        _agg(vocals=0.8, ramp=0.0),
        _agg(),
    )
    out = refine_section_types(stage_a, aggs)
    assert out == (
        SectionType.INTRO,
        SectionType.VERSE,
        SectionType.CHORUS,
        SectionType.PRECHORUS,
        SectionType.CHORUS,
        SectionType.OUTRO,
    )


# ---------------------------------------------------------------------------
# annotate_transitions
# ---------------------------------------------------------------------------


def test_annotate_transitions_empty():
    assert annotate_transitions(0, (), (), ()) == ()


def test_annotate_transitions_shape_mismatch_is_no_op():
    """transition_count disagrees with transitions_from_to length →
    return a tuple of None of length transition_count."""
    out = annotate_transitions(
        transition_count=3,
        transitions_from_to=((0, 1),),  # length 1, not 3
        refined_types=(SectionType.VERSE, SectionType.CHORUS),
        aggregates=(_agg(), _agg()),
    )
    assert out == (None, None, None)


def test_annotate_transitions_into_chorus_with_strong_ramp_buildup():
    refined = (SectionType.VERSE, SectionType.CHORUS)
    aggs = (_agg(vocals=0.5, ramp=0.6), _agg(vocals=0.8))
    out = annotate_transitions(
        transition_count=1,
        transitions_from_to=((0, 1),),
        refined_types=refined,
        aggregates=aggs,
    )
    assert out == ("buildup",)


def test_annotate_transitions_into_chorus_with_weak_ramp_unchanged():
    refined = (SectionType.VERSE, SectionType.CHORUS)
    aggs = (_agg(vocals=0.5, ramp=0.1), _agg(vocals=0.8))
    out = annotate_transitions(
        transition_count=1,
        transitions_from_to=((0, 1),),
        refined_types=refined,
        aggregates=aggs,
    )
    assert out == (None,)


def test_annotate_transitions_into_non_chorus_unchanged():
    """Strong ramp into a BRIDGE doesn't get the buildup label."""
    refined = (SectionType.VERSE, SectionType.BRIDGE)
    aggs = (_agg(vocals=0.5, ramp=0.8), _agg())
    out = annotate_transitions(
        transition_count=1,
        transitions_from_to=((0, 1),),
        refined_types=refined,
        aggregates=aggs,
    )
    assert out == (None,)


def test_annotate_transitions_out_of_range_index_safe():
    """Bad section indices (e.g. from a stale transition list)
    don't raise; that transition yields None."""
    refined = (SectionType.VERSE, SectionType.CHORUS)
    aggs = (_agg(ramp=0.6), _agg())
    out = annotate_transitions(
        transition_count=2,
        transitions_from_to=((0, 1), (5, 9)),
        refined_types=refined,
        aggregates=aggs,
    )
    assert out == ("buildup", None)


def test_annotate_transitions_into_instrumental_chorus_unchanged():
    """An INSTRUMENTAL pass (from rule 1) is no longer a CHORUS for
    BUILDUP annotation purposes — keeps the rules consistent."""
    refined = (SectionType.VERSE, SectionType.INSTRUMENTAL)
    aggs = (_agg(ramp=0.6), _agg())
    out = annotate_transitions(
        transition_count=1,
        transitions_from_to=((0, 1),),
        refined_types=refined,
        aggregates=aggs,
    )
    assert out == (None,)
