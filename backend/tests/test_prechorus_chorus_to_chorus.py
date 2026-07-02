"""Fix 4 — Pass 2b: PRECHORUS via CHORUS→CHORUS vocab narrowing.

When Fix C boundary re-detection splits a long CHORUS block into
sub-sections and H2 relabel gives every child ANCHOR (→ CHORUS),
the classic pre-chorus vamp is left wearing the CHORUS jersey.
Pass 2b promotes a CHORUS→CHORUS transition to PRECHORUS→CHORUS
when the first section's chord-root vocabulary is a strict subset
of the anchor's AND the vocabulary is at most 60% the size AND
there is a rising energy ramp.

Invariants pinned here:
  1. Middle CHORUS with narrower vocab + ramp → PRECHORUS.
  2. Equal-size chord vocabularies → no promote (not a proper subset
     by size).
  3. Non-subset vocabularies (prev roots not ⊂ next roots) → no
     promote.
  4. Weak (or negative) energy ramp → no promote.
  5. ``chords_per_section=None`` → Pass 2b silently disabled
     (backward-compat with old callers).
  6. Shape mismatch between ``chords_per_section`` and
     ``stage_a_types`` → Pass 2b silently disabled.
  7. Pass 2 (VERSE→CHORUS) is unaffected by the new pass.
"""
from __future__ import annotations

from tone_forge.analysis.sections import SectionType
from tone_forge.analysis.song_form import refine_section_types
from tone_forge.analysis.song_form_aggregates import SongFormAggregates


def _agg(*, ramp: float = 0.0) -> SongFormAggregates:
    return SongFormAggregates(
        vocal_activity_score=1.0,      # keep Pass 1 (INSTRUMENTAL) quiet
        drum_density_per_s=1.0,
        drum_density_z=0.0,            # keep Pass 3 (BREAKDOWN) quiet
        energy_ramp_into_next=ramp,
        energy_z=0.0,                  # keep Pass 0 (edge demote) quiet
    )


def test_middle_chorus_with_narrower_vocab_and_ramp_becomes_prechorus() -> None:
    """Three CHORUS in a row; middle one has strictly narrower vocab
    (1 root) that is a subset of the next (4 roots), with a positive
    ramp above 0.6 × prechorus_ramp_floor (0.6 × 0.25 = 0.15) → the
    middle section flips to PRECHORUS."""
    types = [SectionType.CHORUS, SectionType.CHORUS, SectionType.CHORUS]
    aggs = [_agg(ramp=0.0), _agg(ramp=0.30), _agg(ramp=0.0)]
    chords_per_section = [
        ["C", "G", "Am", "F"],   # anchor 1
        ["F"],                    # narrow lead-in, subset of anchor 2
        ["C", "G", "Am", "F"],   # anchor 2
    ]
    out = refine_section_types(types, aggs, chords_per_section=chords_per_section)
    assert out[0] is SectionType.CHORUS
    assert out[1] is SectionType.PRECHORUS
    assert out[2] is SectionType.CHORUS


def test_equal_size_vocabs_stays_chorus() -> None:
    """Same chord-root set on both sides — no narrowing, no
    promotion."""
    types = [SectionType.CHORUS, SectionType.CHORUS]
    aggs = [_agg(ramp=0.50), _agg(ramp=0.0)]
    chords_per_section = [
        ["C", "G", "Am", "F"],
        ["C", "G", "Am", "F"],
    ]
    out = refine_section_types(types, aggs, chords_per_section=chords_per_section)
    assert out[0] is SectionType.CHORUS
    assert out[1] is SectionType.CHORUS


def test_non_subset_vocab_stays_chorus() -> None:
    """Prev roots contain a token not in next roots → not a subset,
    no promotion."""
    types = [SectionType.CHORUS, SectionType.CHORUS]
    aggs = [_agg(ramp=0.50), _agg(ramp=0.0)]
    chords_per_section = [
        ["E"],                    # E not in next
        ["C", "G", "Am", "F"],
    ]
    out = refine_section_types(types, aggs, chords_per_section=chords_per_section)
    assert out[0] is SectionType.CHORUS
    assert out[1] is SectionType.CHORUS


def test_weak_ramp_stays_chorus() -> None:
    """Vocab narrows correctly but energy is flat (ramp = 0) → no
    promotion. The narrowing alone is not enough; PRECHORUS requires
    a rising energy signal."""
    types = [SectionType.CHORUS, SectionType.CHORUS]
    aggs = [_agg(ramp=0.05), _agg(ramp=0.0)]   # below 0.6 × 0.25 = 0.15
    chords_per_section = [
        ["F"],
        ["C", "G", "Am", "F"],
    ]
    out = refine_section_types(types, aggs, chords_per_section=chords_per_section)
    assert out[0] is SectionType.CHORUS
    assert out[1] is SectionType.CHORUS


def test_chords_per_section_none_disables_pass_2b() -> None:
    """No chord data → Pass 2b silently disabled; Pass 2 alone
    cannot fire on CHORUS→CHORUS so labels survive."""
    types = [SectionType.CHORUS, SectionType.CHORUS]
    aggs = [_agg(ramp=0.50), _agg(ramp=0.0)]
    out = refine_section_types(types, aggs, chords_per_section=None)
    assert out[0] is SectionType.CHORUS
    assert out[1] is SectionType.CHORUS


def test_chords_per_section_shape_mismatch_disables_pass_2b() -> None:
    """Wrong-length chord data → Pass 2b silently disabled (defensive
    no-op, mirrors the top-level aggregates length check)."""
    types = [SectionType.CHORUS, SectionType.CHORUS]
    aggs = [_agg(ramp=0.50), _agg(ramp=0.0)]
    chords_per_section = [["F"]]  # length 1 vs 2 sections
    out = refine_section_types(types, aggs, chords_per_section=chords_per_section)
    assert out[0] is SectionType.CHORUS
    assert out[1] is SectionType.CHORUS


def test_pass_2_verse_to_chorus_still_fires() -> None:
    """The original VERSE→CHORUS PRECHORUS rule (Pass 2) is unchanged
    by the new Pass 2b: strong ramp → VERSE flips to PRECHORUS
    regardless of chord vocab."""
    types = [SectionType.VERSE, SectionType.CHORUS]
    aggs = [_agg(ramp=0.50), _agg(ramp=0.0)]
    # No chord data → Pass 2b disabled; Pass 2 still fires.
    out = refine_section_types(types, aggs, chords_per_section=None)
    assert out[0] is SectionType.PRECHORUS
    assert out[1] is SectionType.CHORUS


def test_pass_2b_ignored_when_first_is_not_chorus() -> None:
    """First section is VERSE, not CHORUS → Pass 2b does not fire;
    Pass 2 handles VERSE→CHORUS with a valid ramp."""
    types = [SectionType.VERSE, SectionType.CHORUS]
    aggs = [_agg(ramp=0.50), _agg(ramp=0.0)]
    chords_per_section = [["F"], ["C", "G", "Am", "F"]]
    out = refine_section_types(types, aggs, chords_per_section=chords_per_section)
    # VERSE→CHORUS with ramp above 0.25 → PRECHORUS via Pass 2.
    assert out[0] is SectionType.PRECHORUS


def test_pass_2b_ignored_when_next_is_not_chorus() -> None:
    """Middle is CHORUS but next is BRIDGE → Pass 2b won't fire."""
    types = [SectionType.CHORUS, SectionType.BRIDGE]
    aggs = [_agg(ramp=0.50), _agg(ramp=0.0)]
    chords_per_section = [["F"], ["C", "G", "Am", "F"]]
    out = refine_section_types(types, aggs, chords_per_section=chords_per_section)
    assert out[0] is SectionType.CHORUS
    assert out[1] is SectionType.BRIDGE


def test_enharmonic_roots_collapse() -> None:
    """``C#`` and ``Db`` are the same pitch class; the subset check
    should treat them identically."""
    types = [SectionType.CHORUS, SectionType.CHORUS]
    aggs = [_agg(ramp=0.30), _agg(ramp=0.0)]
    chords_per_section = [
        ["Db"],                             # pc = 1
        ["C#", "F#", "G#", "B"],            # pc = {1, 6, 8, 11}
    ]
    out = refine_section_types(types, aggs, chords_per_section=chords_per_section)
    assert out[0] is SectionType.PRECHORUS
    assert out[1] is SectionType.CHORUS
