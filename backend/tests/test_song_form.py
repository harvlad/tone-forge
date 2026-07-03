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
    energy_z: float = 0.0,
) -> SongFormAggregates:
    """Shorthand for constructing a SongFormAggregates."""
    return SongFormAggregates(
        vocal_activity_score=vocals,
        drum_density_per_s=drum_rate,
        drum_density_z=drum_z,
        energy_ramp_into_next=ramp,
        energy_z=energy_z,
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
# Pass 0: edge-demotion (energy_z) — CHORUS at first/last section
# with a clearly-below-median energy_z gets flipped to INTRO/OUTRO.
# ---------------------------------------------------------------------------


def test_low_energy_chorus_first_section_becomes_intro():
    """Riff-uniform song: every Stage A label is CHORUS, but the
    first section has clearly lower energy → demote to INTRO."""
    types = (SectionType.CHORUS, SectionType.CHORUS, SectionType.CHORUS)
    aggs = (
        _agg(vocals=0.5, energy_z=-1.5),  # clearly lower energy
        _agg(vocals=0.7, energy_z=0.4),
        _agg(vocals=0.7, energy_z=0.4),
    )
    out = refine_section_types(types, aggs)
    assert out[0] is SectionType.INTRO
    assert out[1] is SectionType.CHORUS
    assert out[2] is SectionType.CHORUS


def test_low_energy_chorus_last_section_becomes_outro():
    types = (SectionType.CHORUS, SectionType.CHORUS, SectionType.CHORUS)
    aggs = (
        _agg(vocals=0.7, energy_z=0.4),
        _agg(vocals=0.7, energy_z=0.4),
        _agg(vocals=0.5, energy_z=-1.5),
    )
    out = refine_section_types(types, aggs)
    assert out[0] is SectionType.CHORUS
    assert out[1] is SectionType.CHORUS
    assert out[2] is SectionType.OUTRO


def test_both_edges_demoted_when_riff_uniform_song_drops_at_both_ends():
    """Birds-of-Tokyo shape: 5-section all-CHORUS Stage A with
    low-energy edges at both ends."""
    types = (
        SectionType.CHORUS,
        SectionType.CHORUS,
        SectionType.CHORUS,
        SectionType.CHORUS,
        SectionType.CHORUS,
    )
    aggs = (
        _agg(vocals=0.4, energy_z=-1.4),
        _agg(vocals=0.7, energy_z=0.5),
        _agg(vocals=0.8, energy_z=0.6),
        _agg(vocals=0.7, energy_z=0.5),
        _agg(vocals=0.3, energy_z=-1.3),
    )
    out = refine_section_types(types, aggs)
    assert out == (
        SectionType.INTRO,
        SectionType.CHORUS,
        SectionType.CHORUS,
        SectionType.CHORUS,
        SectionType.OUTRO,
    )


def test_edge_demotion_does_not_fire_above_ceiling():
    """Edges with energy_z above ceiling stay CHORUS."""
    types = (SectionType.CHORUS, SectionType.CHORUS, SectionType.CHORUS)
    aggs = (
        _agg(vocals=0.7, energy_z=-0.5),  # not low enough
        _agg(vocals=0.7, energy_z=0.4),
        _agg(vocals=0.7, energy_z=-0.5),  # not low enough
    )
    out = refine_section_types(types, aggs)
    assert out == (SectionType.CHORUS, SectionType.CHORUS, SectionType.CHORUS)


def test_edge_demotion_does_not_promote_verse_to_intro():
    """Pass 4 only demotes CHORUS; a VERSE at the edge stays VERSE
    even with very low energy_z."""
    types = (SectionType.VERSE, SectionType.CHORUS, SectionType.VERSE)
    aggs = (
        _agg(vocals=0.4, energy_z=-2.0),
        _agg(vocals=0.7, energy_z=0.5),
        _agg(vocals=0.4, energy_z=-2.0),
    )
    out = refine_section_types(types, aggs)
    assert out[0] is SectionType.VERSE
    assert out[2] is SectionType.VERSE


def test_edge_demotion_threshold_knob_wires_through():
    """Tighter edge_energy_z_ceiling refuses to demote a borderline
    edge that the default would have caught."""
    types = (SectionType.CHORUS, SectionType.CHORUS, SectionType.CHORUS)
    aggs = (
        _agg(vocals=0.5, energy_z=-1.1),
        _agg(vocals=0.7, energy_z=0.3),
        _agg(vocals=0.7, energy_z=0.3),
    )
    # Default ceiling -1.0: -1.1 < -1.0 → INTRO.
    assert refine_section_types(types, aggs)[0] is SectionType.INTRO
    # Tighter ceiling -1.5: -1.1 NOT < -1.5 → CHORUS.
    tighter = SongFormThresholds(edge_energy_z_ceiling=-1.5)
    assert refine_section_types(types, aggs, tighter)[0] is SectionType.CHORUS


def test_edge_demotion_single_section_no_op():
    """n < 2 → Pass 4 cannot fire (the same section would be both
    first and last edge)."""
    types = (SectionType.CHORUS,)
    aggs = (_agg(vocals=0.5, energy_z=-2.0),)
    out = refine_section_types(types, aggs)
    assert out == (SectionType.CHORUS,)


# ---------------------------------------------------------------------------
# Pass 4: CHORUS→VERSE demotion (energy_z + vocal_activity_score
# both meaningfully below the intra-CHORUS median).
# ---------------------------------------------------------------------------


def test_chorus_demoted_to_verse_when_energy_z_and_vocals_below_median():
    """Six Stage-A CHORUSes; two have low energy_z AND low vocals
    (but still above ``vocal_silence_ceiling`` so Pass 1 does
    not flip them to INSTRUMENTAL first). Pass 4 demotes them
    to VERSE; the other four stay CHORUS."""
    types = (SectionType.CHORUS,) * 6
    # Median energy_z = 0.4, median vocals = 0.6 (after sort).
    # Threshold z_offset = 0.35 → require energy_z < 0.05.
    # Threshold vocal_ratio = 0.75 → require vocals < 0.45.
    aggs = (
        _agg(vocals=0.6, energy_z=0.4),
        _agg(vocals=0.6, energy_z=0.4),
        _agg(vocals=0.20, energy_z=-1.2),   # → VERSE
        _agg(vocals=0.6, energy_z=0.4),
        _agg(vocals=0.20, energy_z=-1.2),   # → VERSE
        _agg(vocals=0.6, energy_z=0.4),
    )
    out = refine_section_types(types, aggs)
    assert out == (
        SectionType.CHORUS,
        SectionType.CHORUS,
        SectionType.VERSE,
        SectionType.CHORUS,
        SectionType.VERSE,
        SectionType.CHORUS,
    )


def test_chorus_verse_demotion_needs_at_least_min_choruses():
    """Fewer than ``verse_demotion_min_choruses`` (default 4)
    Stage-A CHORUSes → Pass 4 abstains. Median of a 3-element
    sample is too noisy to trust."""
    types = (SectionType.CHORUS, SectionType.CHORUS, SectionType.CHORUS)
    aggs = (
        _agg(vocals=0.6, energy_z=0.4),
        _agg(vocals=0.20, energy_z=-1.2),  # would be demoted at ≥4
        _agg(vocals=0.6, energy_z=0.4),
    )
    out = refine_section_types(types, aggs)
    assert out == (SectionType.CHORUS, SectionType.CHORUS, SectionType.CHORUS)


def test_chorus_verse_demotion_preserves_at_least_one_chorus():
    """Sanity net: with thresholds set so every CHORUS qualifies
    for demotion, Pass 4 stops one short so downstream code
    always has at least one CHORUS to work with. The
    highest-combined-signal survivor is retained
    (deterministic by ascending sort of ``energy_z +
    vocal_activity_score``)."""
    types = (SectionType.CHORUS,) * 5
    aggs = (
        _agg(vocals=0.20, energy_z=-0.5),
        _agg(vocals=0.20, energy_z=-0.4),   # highest combined signal
        _agg(vocals=0.20, energy_z=-0.5),
        _agg(vocals=0.20, energy_z=-0.5),
        _agg(vocals=0.20, energy_z=-0.5),
    )
    # Relaxed thresholds so every section qualifies on both
    # signals. Sanity check is the only reason ``refined[1]``
    # survives.
    thresholds = SongFormThresholds(
        verse_demotion_z_offset=-1000.0,
        verse_demotion_vocal_ratio=1000.0,
    )
    out = refine_section_types(types, aggs, thresholds)
    assert out.count(SectionType.CHORUS) == 1
    assert out.count(SectionType.VERSE) == 4
    assert out[1] is SectionType.CHORUS


def test_chorus_verse_demotion_only_energy_z_low_stays_chorus():
    """Both signals must independently agree. When only
    ``energy_z`` is low but vocals sit at the median, the
    section is a legitimate quiet-chorus variant and stays
    CHORUS."""
    types = (SectionType.CHORUS,) * 6
    # Median energy_z = 0.4, median vocals = 0.7.
    # Two candidates have low energy_z (-1.2) but vocals at 0.7
    # (== median, so not below 0.75 * 0.7 = 0.525) → stay CHORUS.
    aggs = (
        _agg(vocals=0.7, energy_z=0.4),
        _agg(vocals=0.7, energy_z=0.4),
        _agg(vocals=0.7, energy_z=-1.2),
        _agg(vocals=0.7, energy_z=0.4),
        _agg(vocals=0.7, energy_z=-1.2),
        _agg(vocals=0.7, energy_z=0.4),
    )
    out = refine_section_types(types, aggs)
    assert out == types  # no demotions


def test_chorus_verse_demotion_only_vocals_low_stays_chorus():
    """Mirror of the energy-only test. Low vocals alone (with
    full-energy playback) suggests an instrumental break that
    Pass 1 missed — but not a verse. Stays CHORUS. Vocals kept
    above ``vocal_silence_ceiling`` so Pass 1 does not fire."""
    types = (SectionType.CHORUS,) * 6
    # Median vocals = 0.6, median energy_z = 0.4.
    # Two candidates have low vocals (0.20 < 0.45 = 0.75 * 0.6)
    # but energy_z at 0.4 (== median, not below 0.4 - 0.35 =
    # 0.05) → stay CHORUS.
    aggs = (
        _agg(vocals=0.6, energy_z=0.4),
        _agg(vocals=0.6, energy_z=0.4),
        _agg(vocals=0.20, energy_z=0.4),
        _agg(vocals=0.6, energy_z=0.4),
        _agg(vocals=0.20, energy_z=0.4),
        _agg(vocals=0.6, energy_z=0.4),
    )
    out = refine_section_types(types, aggs)
    assert out == types  # no demotions


def test_chorus_verse_demotion_one_sided_never_promotes_verse():
    """Pass 4 is one-directional: only CHORUS → VERSE. A VERSE
    with above-median signals surrounded by CHORUSes stays a
    VERSE (the pass never inspects non-CHORUS sections)."""
    types = (
        SectionType.CHORUS,
        SectionType.CHORUS,
        SectionType.VERSE,      # <- must stay VERSE
        SectionType.CHORUS,
        SectionType.CHORUS,
        SectionType.CHORUS,
    )
    aggs = (
        _agg(vocals=0.6, energy_z=0.4),
        _agg(vocals=0.6, energy_z=0.4),
        _agg(vocals=0.9, energy_z=1.0),  # verse w/ above-median signals
        _agg(vocals=0.6, energy_z=0.4),
        _agg(vocals=0.6, energy_z=0.4),
        _agg(vocals=0.6, energy_z=0.4),
    )
    out = refine_section_types(types, aggs)
    assert out[2] is SectionType.VERSE
    for i in (0, 1, 3, 4, 5):
        assert out[i] is SectionType.CHORUS


def test_chorus_verse_demotion_ignores_non_chorus_types():
    """Pass 4 only touches sections whose refined type is
    CHORUS. A BREAKDOWN / PRECHORUS / INSTRUMENTAL with
    below-median signals stays as-is. Also demonstrates that
    the intra-CHORUS median is computed only across the four
    surviving CHORUSes, not the whole song."""
    types = (
        SectionType.CHORUS,
        SectionType.PRECHORUS,     # non-chorus w/ low signals
        SectionType.CHORUS,
        SectionType.BREAKDOWN,     # non-chorus w/ low signals
        SectionType.CHORUS,
        SectionType.INSTRUMENTAL,  # non-chorus w/ low signals
        SectionType.CHORUS,
    )
    # drum_density_z=0 keeps Pass 3 off the BREAKDOWN slot (Pass 3
    # would otherwise mark other sections as BREAKDOWN too). Low
    # vocals on the non-CHORUS slots don't trigger Pass 1 because
    # Pass 1 only inspects CHORUS labels.
    aggs = (
        _agg(vocals=0.6, energy_z=0.4),
        _agg(vocals=0.20, energy_z=-1.2),
        _agg(vocals=0.6, energy_z=0.4),
        _agg(vocals=0.20, energy_z=-1.2),
        _agg(vocals=0.6, energy_z=0.4),
        _agg(vocals=0.20, energy_z=-1.2),
        _agg(vocals=0.6, energy_z=0.4),
    )
    out = refine_section_types(types, aggs)
    # Non-CHORUS types unchanged.
    assert out[1] is SectionType.PRECHORUS
    assert out[3] is SectionType.BREAKDOWN
    assert out[5] is SectionType.INSTRUMENTAL
    # All four CHORUSes have identical (above-median relative to
    # their own set of {0.4, 0.4, 0.4, 0.4}) signals → none
    # demoted.
    for i in (0, 2, 4, 6):
        assert out[i] is SectionType.CHORUS


def test_chorus_verse_demotion_thresholds_wire_through():
    """Tightening ``verse_demotion_z_offset`` refuses to demote
    a borderline section that the defaults would have caught."""
    types = (SectionType.CHORUS,) * 5
    # Median energy_z = 0.0 (mean of positions 3,4 sorted:
    # -0.5, 0.0, 0.0, 0.4, 0.4 → median = 0.0). Median vocals =
    # 0.6. Candidate at index 2 (vocals=0.25 < 0.75*0.6 = 0.45).
    aggs = (
        _agg(vocals=0.6, energy_z=0.4),
        _agg(vocals=0.6, energy_z=0.0),
        _agg(vocals=0.25, energy_z=-0.5),  # borderline: below 0-0.35
        _agg(vocals=0.6, energy_z=0.0),
        _agg(vocals=0.6, energy_z=0.4),
    )
    # Default z_offset=0.35: candidate energy_z=-0.5 < 0 - 0.35 = -0.35 → demote.
    default_out = refine_section_types(types, aggs)
    assert default_out[2] is SectionType.VERSE
    # Tighter offset=1.0: candidate energy_z=-0.5 NOT < 0 - 1.0 = -1.0 → stay.
    tight = SongFormThresholds(verse_demotion_z_offset=1.0)
    tight_out = refine_section_types(types, aggs, tight)
    assert tight_out[2] is SectionType.CHORUS


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


# ---------------------------------------------------------------------------
# Pass 4b: CHORUS→VERSE demotion by vocal pitch (median AND range
# both meaningfully below the intra-CHORUS cohort).
# ---------------------------------------------------------------------------
#
# Pass 4b is Pass 4's independent-evidence-axis sibling for
# shared-progression songs (pop / rock / folk) where verse and
# chorus use identical chord loops and identical energy/vocal
# intensity — H2 sees ANCHOR everywhere, Pass 4 abstains, but the
# singer sits lower AND moves less in the verse. Two signals in
# AND-gate: median alone would over-fire on brief low-pitch chorus
# moments; range alone would flip monotone choruses.


def _agg_pitch(
    vocals: float = 1.0,
    energy_z: float = 0.0,
    pitch_median: float = 0.0,
    pitch_range: float = 0.0,
) -> SongFormAggregates:
    """Shorthand for constructing a SongFormAggregates with pitch
    fields populated. Extends ``_agg`` with the Pass 4b signals so
    Pass 4b tests stay readable."""
    return SongFormAggregates(
        vocal_activity_score=vocals,
        drum_density_per_s=0.0,
        drum_density_z=0.0,
        energy_ramp_into_next=0.0,
        energy_z=energy_z,
        vocal_pitch_median_semitones=pitch_median,
        vocal_pitch_range_semitones=pitch_range,
    )


def test_pass_4b_demotes_low_pitched_narrow_range_chorus_to_verse():
    """Four CHORUSes with pitch evidence. Cohort median = 70,
    cohort range = 8. Section 2 sits 4 semitones lower (66 < 70-2)
    AND has half the range (4 < 0.75*8 = 6): demote. The other
    three keep CHORUS. Signals kept away from the Pass 4
    thresholds (vocals & energy_z at cohort levels) so Pass 4
    itself abstains — this test isolates Pass 4b."""
    types = (SectionType.CHORUS,) * 4
    aggs = (
        _agg_pitch(vocals=0.6, energy_z=0.4,
                   pitch_median=70.0, pitch_range=8.0),
        _agg_pitch(vocals=0.6, energy_z=0.4,
                   pitch_median=66.0, pitch_range=4.0),   # → VERSE
        _agg_pitch(vocals=0.6, energy_z=0.4,
                   pitch_median=70.0, pitch_range=8.0),
        _agg_pitch(vocals=0.6, energy_z=0.4,
                   pitch_median=71.0, pitch_range=9.0),
    )
    out = refine_section_types(types, aggs)
    assert out[1] is SectionType.VERSE
    for i in (0, 2, 3):
        assert out[i] is SectionType.CHORUS


def test_pass_4b_no_op_when_only_median_dips():
    """Both signals must agree. Median dip alone (range matches
    cohort) → no demotion."""
    types = (SectionType.CHORUS,) * 4
    aggs = (
        _agg_pitch(vocals=0.6, energy_z=0.4,
                   pitch_median=70.0, pitch_range=8.0),
        _agg_pitch(vocals=0.6, energy_z=0.4,
                   pitch_median=66.0, pitch_range=8.0),   # median dip only
        _agg_pitch(vocals=0.6, energy_z=0.4,
                   pitch_median=70.0, pitch_range=8.0),
        _agg_pitch(vocals=0.6, energy_z=0.4,
                   pitch_median=70.0, pitch_range=8.0),
    )
    out = refine_section_types(types, aggs)
    assert out == types  # nothing demoted


def test_pass_4b_no_op_when_only_range_dips():
    """Mirror of the median-only test. Range dip alone (median
    matches cohort) → no demotion. Monotone choruses that happen
    to land on the cohort's median pitch stay CHORUS."""
    types = (SectionType.CHORUS,) * 4
    aggs = (
        _agg_pitch(vocals=0.6, energy_z=0.4,
                   pitch_median=70.0, pitch_range=8.0),
        _agg_pitch(vocals=0.6, energy_z=0.4,
                   pitch_median=70.0, pitch_range=3.0),   # range dip only
        _agg_pitch(vocals=0.6, energy_z=0.4,
                   pitch_median=70.0, pitch_range=8.0),
        _agg_pitch(vocals=0.6, energy_z=0.4,
                   pitch_median=70.0, pitch_range=8.0),
    )
    out = refine_section_types(types, aggs)
    assert out == types  # nothing demoted


def test_pass_4b_preserves_at_least_one_chorus():
    """Pathological input: every CHORUS qualifies for pitch
    demotion. Pass 4b must stop one short so downstream code
    keeps at least one CHORUS. Deterministic which one via the
    ascending sort on ``median + range`` (highest joint signal
    survives)."""
    types = (SectionType.CHORUS,) * 4
    # Loose thresholds so every section qualifies on both axes;
    # cohort computed on the actual values.
    aggs = (
        _agg_pitch(vocals=0.6, energy_z=0.4,
                   pitch_median=60.0, pitch_range=2.0),
        _agg_pitch(vocals=0.6, energy_z=0.4,
                   pitch_median=60.5, pitch_range=2.1),   # highest joint
        _agg_pitch(vocals=0.6, energy_z=0.4,
                   pitch_median=60.0, pitch_range=2.0),
        _agg_pitch(vocals=0.6, energy_z=0.4,
                   pitch_median=60.0, pitch_range=2.0),
    )
    # Force every candidate to qualify: absurdly small offset +
    # ratio > 1 so ``range < cohort_range * ratio`` fires even at
    # cohort value.
    thresholds = SongFormThresholds(
        verse_pitch_semitone_offset=-1000.0,
        verse_pitch_range_ratio=1000.0,
    )
    out = refine_section_types(types, aggs, thresholds)
    assert out.count(SectionType.CHORUS) == 1
    assert out.count(SectionType.VERSE) == 3
    # The highest-joint-signal CHORUS survives (index 1).
    assert out[1] is SectionType.CHORUS


def test_pass_4b_respects_verse_demotion_min_choruses():
    """Fewer than ``verse_demotion_min_choruses`` (default 4)
    CHORUSes → Pass 4b abstains, matching Pass 4's guardrail.
    The intra-CHORUS median on tiny cohorts is too noisy to
    trust."""
    types = (SectionType.CHORUS, SectionType.CHORUS, SectionType.CHORUS)
    aggs = (
        _agg_pitch(vocals=0.6, energy_z=0.4,
                   pitch_median=70.0, pitch_range=8.0),
        _agg_pitch(vocals=0.6, energy_z=0.4,
                   pitch_median=66.0, pitch_range=4.0),   # would qualify at n>=4
        _agg_pitch(vocals=0.6, energy_z=0.4,
                   pitch_median=70.0, pitch_range=8.0),
    )
    out = refine_section_types(types, aggs)
    assert out == types


def test_pass_4b_abstains_when_pitch_data_missing():
    """0.0-sentinel-as-abstain: when every CHORUS has zero pitch
    evidence (no vocals stem / all None upstream), Pass 4b must
    abstain rather than firing on a phantom pitch dip. Signals
    that would trigger Pass 4 are also kept above its
    thresholds so this test isolates Pass 4b."""
    types = (SectionType.CHORUS,) * 4
    aggs = (
        _agg_pitch(vocals=0.6, energy_z=0.4,
                   pitch_median=0.0, pitch_range=0.0),
        _agg_pitch(vocals=0.6, energy_z=0.4,
                   pitch_median=0.0, pitch_range=0.0),
        _agg_pitch(vocals=0.6, energy_z=0.4,
                   pitch_median=0.0, pitch_range=0.0),
        _agg_pitch(vocals=0.6, energy_z=0.4,
                   pitch_median=0.0, pitch_range=0.0),
    )
    out = refine_section_types(types, aggs)
    assert out == types


def test_pass_4b_runs_after_pass_4_in_refine_section_types():
    """End-to-end: on a five-CHORUS fixture where one section
    triggers Pass 4 (energy + vocal dip) and a different section
    triggers Pass 4b (pitch dip), both fire independently. The
    Pass-4b candidate must have Pass 4-safe energy and vocal
    signals so that only Pass 4b flips it; the Pass-4 candidate
    must have Pass 4b-safe pitch (or zero pitch evidence) so
    that only Pass 4 flips it."""
    types = (SectionType.CHORUS,) * 5
    # Pass 4 cohort: median energy_z = 0.4, median vocals = 0.6.
    #   → energy < 0.4-0.35 = 0.05 AND vocals < 0.75*0.6 = 0.45.
    # Pass 4b cohort (after Pass 4): valid pitch entries only.
    #   median = 70, range = 8 → median < 68 AND range < 6.
    aggs = (
        # 0: full-signal chorus
        _agg_pitch(vocals=0.6, energy_z=0.4,
                   pitch_median=70.0, pitch_range=8.0),
        # 1: Pass 4 target — low energy AND low vocals, but ZERO
        #    pitch evidence (upstream row was None) so Pass 4b
        #    ignores it.
        _agg_pitch(vocals=0.20, energy_z=-1.2,
                   pitch_median=0.0, pitch_range=0.0),
        # 2: full-signal chorus
        _agg_pitch(vocals=0.6, energy_z=0.4,
                   pitch_median=70.0, pitch_range=8.0),
        # 3: Pass 4b target — Pass 4-safe (vocals & energy at
        #    cohort levels) but pitch median and range both dip.
        _agg_pitch(vocals=0.6, energy_z=0.4,
                   pitch_median=66.0, pitch_range=4.0),
        # 4: full-signal chorus
        _agg_pitch(vocals=0.6, energy_z=0.4,
                   pitch_median=70.0, pitch_range=8.0),
    )
    out = refine_section_types(types, aggs)
    assert out[0] is SectionType.CHORUS
    assert out[1] is SectionType.VERSE   # Pass 4 fired
    assert out[2] is SectionType.CHORUS
    assert out[3] is SectionType.VERSE   # Pass 4b fired
    assert out[4] is SectionType.CHORUS
