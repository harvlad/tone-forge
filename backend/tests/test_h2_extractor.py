"""Unit tests for the H2 extractor.

Hermetic — no real-bundle dependencies. The canonical-6
reproducibility check (spec §9 item 1) lives in
`test_h2_canonical_corpus.py`, which is the classifier-development
gate. This file covers the 7 hermetic checklist items from spec §9
(items 2–8) plus a golden-output sweep over every fixture.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

# Make the fixtures package importable as `fixtures.h2_corpus` from
# this test file. (`backend/tests` has no __init__.py — same pattern
# the other test files use.)
sys.path.insert(0, str(Path(__file__).parent))

from fixtures.h2_corpus import (  # noqa: E402
    ALL_FIXTURES,
    fixture_asymmetric_three_section,
    fixture_bigram_fallback,
    fixture_empty_chords,
    fixture_empty_sections,
    fixture_parse_failure_dropped,
    fixture_section_no_chords_inside,
    fixture_single_chord_section,
    fixture_singletons_no_ngrams,
    fixture_truly_degenerate_one_chord_song,
    fixture_uniform_repeat,
)
from tone_forge.song_form.h2 import (
    PC_NONE,
    H2Result,
    _root_pc,
    extract_h2,
)


# Tight tolerance — fixtures' golden values are computed by hand or
# in closed form, so float precision is the only thing that should
# move them.
_TOL = 1e-9


def _assert_h2_close(actual: H2Result, expected: H2Result) -> None:
    """Element-wise tolerance check for H2Result."""
    assert actual.n_used == expected.n_used
    assert actual.degenerate == expected.degenerate
    assert actual.section_names == expected.section_names
    assert len(actual.per_section) == len(expected.per_section)
    for i, (a, e) in enumerate(zip(actual.per_section, expected.per_section)):
        assert math.isclose(a, e, abs_tol=_TOL), f"per_section[{i}]: {a} vs {e}"
    assert math.isclose(actual.h2_sep, expected.h2_sep, abs_tol=_TOL)


# --- Spec §9 checklist tests -------------------------------------------------


# Item 2 — empty-chord
def test_empty_chord_bundle_is_degenerate_all_zeros():
    bundle, expected = fixture_empty_chords()
    result = extract_h2(bundle)
    _assert_h2_close(result, expected)
    assert result.degenerate is True
    assert all(v == 0.0 for v in result.per_section)


# Item 3 — empty-section
def test_empty_section_bundle_has_no_per_section():
    bundle, expected = fixture_empty_sections()
    result = extract_h2(bundle)
    _assert_h2_close(result, expected)
    assert result.per_section == ()
    assert result.degenerate is True


# Item 4 — single-chord-section
def test_single_chord_section_emits_zero_for_that_section():
    bundle, expected = fixture_single_chord_section()
    result = extract_h2(bundle)
    _assert_h2_close(result, expected)
    # The single-chord section must be exactly 0.0 — no rounding noise.
    assert result.per_section[1] == 0.0


# Item 5 — bigram fallback
def test_bigram_fallback_when_longest_section_is_two_chords():
    bundle, expected = fixture_bigram_fallback()
    result = extract_h2(bundle)
    _assert_h2_close(result, expected)
    assert result.n_used == 2
    assert result.degenerate is False


# Item 5b — section-grams empty but song-level is not degenerate
def test_singletons_no_section_grams_is_not_degenerate():
    """Three single-chord sections: full_seq len=3 so n_used=2 (not
    degenerate at the song level), but no section has enough chords
    to emit any bigram, so every per_section is 0.0.
    """
    bundle, expected = fixture_singletons_no_ngrams()
    result = extract_h2(bundle)
    _assert_h2_close(result, expected)
    assert result.n_used == 2
    assert result.degenerate is False
    assert all(v == 0.0 for v in result.per_section)


# Item 5c — truly degenerate: full_seq len < 2
def test_one_chord_song_is_truly_degenerate():
    bundle, expected = fixture_truly_degenerate_one_chord_song()
    result = extract_h2(bundle)
    _assert_h2_close(result, expected)
    assert result.n_used == 0
    assert result.degenerate is True


# Item 6 — parse failure (N.C., empty, None)
def test_unparseable_chord_symbol_dropped_no_exception():
    bundle, expected = fixture_parse_failure_dropped()
    # The N.C. chord between the two sections must not contaminate
    # the trigram sequence. Both sections end up with identical
    # PC content so h2_sep is exactly zero.
    result = extract_h2(bundle)
    _assert_h2_close(result, expected)


# Item 7 — determinism
def test_extractor_is_deterministic_across_runs():
    bundle, _ = fixture_asymmetric_three_section()
    first = extract_h2(bundle)
    second = extract_h2(bundle)
    assert first == second


# Item 8 — symbol parser unit tests
@pytest.mark.parametrize(
    "symbol, expected",
    [
        ("C", 0),
        ("C#", 1),
        ("Db", 1),
        ("D", 2),
        ("D#", 3),
        ("Eb", 3),
        ("E", 4),
        ("F", 5),
        ("F#", 6),
        ("Gb", 6),
        ("G", 7),
        ("G#", 8),
        ("Ab", 8),
        ("A", 9),
        ("A#", 10),
        ("Bb", 10),
        ("B", 11),
        # Quality after the root is ignored
        ("Cm", 0),
        ("Cmaj7", 0),
        ("C#m7b5", 1),
        ("Fsus4", 5),
        # Unicode accidentals (♯ / ♭)
        ("C\u266F", 1),
        ("D\u266D", 1),
        # Unparseable
        ("N.C.", PC_NONE),
        ("", PC_NONE),
        (None, PC_NONE),
        (42, PC_NONE),
        ("H", PC_NONE),
        # Boundary wrap-arounds
        ("Cb", 11),  # B
        ("B#", 0),   # C
    ],
)
def test_root_pc_symbol_parser(symbol, expected):
    assert _root_pc(symbol) == expected


# --- Extra main-path coverage -----------------------------------------------


def test_asymmetric_three_section_golden_values():
    bundle, expected = fixture_asymmetric_three_section()
    result = extract_h2(bundle)
    _assert_h2_close(result, expected)
    # Sanity: h2_sep should be substantial because per-section values
    # span 0.0 → 0.5
    assert result.h2_sep > 0.5


def test_section_without_any_chord_midpoint_inside():
    bundle, expected = fixture_section_no_chords_inside()
    result = extract_h2(bundle)
    _assert_h2_close(result, expected)
    # The silent middle section is zero, the two outer sections are 1.0
    assert result.per_section[1] == 0.0
    assert result.per_section[0] == 1.0
    assert result.per_section[2] == 1.0


def test_uniform_repeat_has_zero_separability():
    bundle, expected = fixture_uniform_repeat()
    result = extract_h2(bundle)
    _assert_h2_close(result, expected)
    assert result.h2_sep == 0.0


# --- Golden-output sweep -----------------------------------------------------


@pytest.mark.parametrize("fixture_name", list(ALL_FIXTURES.keys()))
def test_fixture_golden_output_bit_equivalent(fixture_name):
    """Every fixture in the catalogue must reproduce its golden H2Result.

    This is the regression net — any future edit to the extractor
    that changes a fixture's output breaks this test and demands an
    explicit golden-update commit.
    """
    bundle, expected = ALL_FIXTURES[fixture_name]()
    result = extract_h2(bundle)
    _assert_h2_close(result, expected)
