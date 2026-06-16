"""Unit tests for the chord evaluation harness.

Pins the WCSR / confusion-matrix calculations against hand-computed
toy examples. These pin the *ruler*, not the detector; a regression
here means the ruler itself is broken and every subsequent metric in
the rebuild plan is suspect.

Also tests the symbol parser (`root_of`, `quality_of`,
`normalise_symbol`) since every WCSR call routes through it.
"""
from __future__ import annotations

import pytest

from tone_forge.analysis.chord_eval import (
    root_of,
    quality_of,
    normalise_symbol,
    to_regions,
    wcsr,
    triad_relaxed_wcsr,
    root_only_wcsr,
    confusion_matrix,
    format_confusion_top_n,
)


# ---- symbol parser ---------------------------------------------------


@pytest.mark.parametrize("sym,pc", [
    ("C", 0), ("C#", 1), ("Db", 1), ("D", 2), ("D#", 3), ("E", 4),
    ("F", 5), ("F#", 6), ("Gb", 6), ("G", 7), ("Ab", 8), ("A", 9),
    ("A#", 10), ("Bb", 10), ("B", 11),
    ("Am", 9), ("F#m", 6), ("Bbm7", 10), ("Cmaj7", 0), ("G7", 7),
    ("Ddim", 2), ("Aaug", 9), ("Dsus2", 2), ("Asus4", 9), ("E5", 4),
])
def test_root_of(sym: str, pc: int) -> None:
    assert root_of(sym) == pc


@pytest.mark.parametrize("sym,q", [
    ("A", "maj"), ("Am", "min"), ("Amaj7", "maj"), ("Amin7", "min"),
    ("A7", "7"), ("Adom7", "7"), ("Adim", "dim"), ("Adim7", "dim"),
    ("Aaug", "aug"), ("Asus2", "sus2"), ("Asus4", "sus4"),
    ("A5", "5"), ("Amaj9", "maj"), ("Amin9", "min"), ("Aadd9", "maj"),
    ("Am9", "min"), ("Am7", "min"),
])
def test_quality_of(sym: str, q: str) -> None:
    assert quality_of(sym) == q


@pytest.mark.parametrize("raw,canon", [
    ("Amaj7", "A"), ("Amaj9", "A"), ("Aadd9", "A"),
    ("Amin9", "Am"), ("Amin", "Am"), ("Am7", "Am"),
    ("F#min", "F#m"), ("Dbm9", "C#m"),  # accidental normalisation
    ("G7", "G7"), ("Gdom7", "G7"),
    ("E5", "E5"), ("Asus4", "Asus4"),
])
def test_normalise_symbol(raw: str, canon: str) -> None:
    assert normalise_symbol(raw) == canon


def test_unparsable_symbol_raises() -> None:
    with pytest.raises(ValueError):
        root_of("not a chord")


# ---- to_regions adapter ----------------------------------------------


def test_to_regions_accepts_dict_with_start_end_label() -> None:
    regs = to_regions([{"start": 0.0, "end": 1.0, "label": "A"}])
    assert regs == [(0.0, 1.0, "A")]


def test_to_regions_accepts_dict_with_contracts_keys() -> None:
    regs = to_regions([{"start_s": 0.0, "end_s": 1.0, "symbol": "A"}])
    assert regs == [(0.0, 1.0, "A")]


def test_to_regions_accepts_tuples_and_sorts() -> None:
    regs = to_regions([(2.0, 3.0, "B"), (0.0, 1.0, "A")])
    assert regs == [(0.0, 1.0, "A"), (2.0, 3.0, "B")]


def test_to_regions_accepts_contracts_chord_objects() -> None:
    class Stub:
        def __init__(self, s, e, sym):
            self.start_s, self.end_s, self.symbol = s, e, sym
    regs = to_regions([Stub(0.0, 1.0, "A")])
    assert regs == [(0.0, 1.0, "A")]


def test_to_regions_accepts_internal_chord_objects() -> None:
    class Stub:
        def __init__(self, s, e, sym):
            self.start_time, self.end_time, self.name = s, e, sym
    regs = to_regions([Stub(0.0, 1.0, "A")])
    assert regs == [(0.0, 1.0, "A")]


# ---- WCSR hand-computed toy example ---------------------------------
#
# A 10-second toy with 5 reference regions:
#   [0, 2)  A
#   [2, 4)  D
#   [4, 5)  E
#   [5, 8)  A
#   [8, 10) F#m
#
# Predicted (intentionally noisy):
#   [0, 2)  A          -> matches A, 2s
#   [2, 3)  Dm         -> root match, quality mismatch (1s)
#   [3, 4)  D          -> matches D, 1s
#   [4, 5)  E          -> matches E, 1s
#   [5, 7)  F#m        -> root mismatch on A region (2s)
#   [7, 8)  (none)     -> gap on A region (1s)
#   [8, 10) F#m        -> matches F#m, 2s
#
# Strict WCSR  = (2 + 0 + 1 + 1 + 0 + 2) / 10 = 0.60
# Triad-relax  = (2 + 1 + 1 + 1 + 0 + 2) / 10 = 0.70 (Dm root-matches D)
# Root-only    = same as triad-relax = 0.70


_REF = [
    (0.0, 2.0, "A"),
    (2.0, 4.0, "D"),
    (4.0, 5.0, "E"),
    (5.0, 8.0, "A"),
    (8.0, 10.0, "F#m"),
]
_PRED = [
    (0.0, 2.0, "A"),
    (2.0, 3.0, "Dm"),
    (3.0, 4.0, "D"),
    (4.0, 5.0, "E"),
    (5.0, 7.0, "F#m"),
    # 7-8s intentionally uncovered
    (8.0, 10.0, "F#m"),
]


def test_wcsr_strict() -> None:
    assert wcsr(_PRED, _REF, 10.0) == pytest.approx(0.60, abs=1e-6)


def test_wcsr_triad_relaxed() -> None:
    assert triad_relaxed_wcsr(_PRED, _REF, 10.0) == pytest.approx(0.70, abs=1e-6)


def test_wcsr_root_only_alias() -> None:
    # By construction root_only is currently identical to triad_relaxed.
    assert root_only_wcsr(_PRED, _REF, 10.0) == \
           triad_relaxed_wcsr(_PRED, _REF, 10.0)


def test_wcsr_zero_duration_is_zero() -> None:
    assert wcsr(_PRED, _REF, 0.0) == 0.0


def test_wcsr_perfect_match_returns_one() -> None:
    assert wcsr(_REF, _REF, 10.0) == pytest.approx(1.0, abs=1e-6)


def test_wcsr_empty_prediction_is_zero() -> None:
    assert wcsr([], _REF, 10.0) == 0.0


def test_wcsr_normalisation_collapses_extensions() -> None:
    # "Amaj7" predicted against "A" reference should count as match.
    ref = [(0.0, 1.0, "A")]
    pred = [(0.0, 1.0, "Amaj7")]
    assert wcsr(pred, ref, 1.0) == pytest.approx(1.0, abs=1e-6)


def test_wcsr_relative_minor_does_not_match_strict() -> None:
    # Aminor vs A major: strict WCSR distinguishes them.
    ref = [(0.0, 1.0, "A")]
    pred = [(0.0, 1.0, "Am")]
    assert wcsr(pred, ref, 1.0) == 0.0
    # Triad-relaxed: relative minor matches root, so 1.0.
    assert triad_relaxed_wcsr(pred, ref, 1.0) == pytest.approx(1.0, abs=1e-6)


# ---- confusion matrix -----------------------------------------------


def test_confusion_matrix_diagonal_for_perfect_match() -> None:
    cm = confusion_matrix(_REF, _REF)
    # Diagonal should sum to 10s total.
    diag_total = sum(s for (r, p), s in cm.items() if r == p)
    assert diag_total == pytest.approx(10.0, abs=1e-6)
    # No off-diagonal entries.
    off = [(k, v) for k, v in cm.items() if k[0] != k[1]]
    assert off == []


def test_confusion_matrix_total_equals_reference_duration() -> None:
    cm = confusion_matrix(_PRED, _REF)
    total = sum(cm.values())
    # Reference covers [0, 10) = 10s; confusion sums must equal 10s.
    assert total == pytest.approx(10.0, abs=1e-6)


def test_confusion_matrix_gap_attribution() -> None:
    cm = confusion_matrix(_PRED, _REF)
    # The 7-8s gap should attribute to ("A", "") = 1s.
    assert cm.get(("A", ""), 0.0) == pytest.approx(1.0, abs=1e-6)


def test_confusion_matrix_captures_relative_minor_confusion() -> None:
    # Predicting F#m on A region [5, 7) = 2s.
    cm = confusion_matrix(_PRED, _REF)
    assert cm.get(("A", "F#m"), 0.0) == pytest.approx(2.0, abs=1e-6)


def test_confusion_matrix_captures_quality_confusion() -> None:
    # Predicting Dm on D region [2, 3) = 1s.
    cm = confusion_matrix(_PRED, _REF)
    assert cm.get(("D", "Dm"), 0.0) == pytest.approx(1.0, abs=1e-6)


def test_format_confusion_top_n_renders() -> None:
    cm = confusion_matrix(_PRED, _REF)
    out = format_confusion_top_n(cm, n=5)
    assert "correct (diagonal):" in out
    assert "confusions (off-diagonal):" in out
    assert "[GAP]" in out  # 7-8s gap on A region
