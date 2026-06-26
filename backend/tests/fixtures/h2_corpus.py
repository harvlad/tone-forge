"""Hermetic synthetic fixtures for the H2 extractor test suite.

Per the user directive (2026-06-21): fixture-based tests, golden
outputs. No real-song data here — those live in the validation
harness (`test_h2_canonical_corpus.py`), which is the
classifier-development gate.

Every fixture builder returns a `(bundle_dict, expected_h2_result)`
pair. The expected result is hand-computed (or trivially derived) and
is the *golden* truth — the spec at `backend/h2_specification.md`
is the source of truth for the algorithm; these fixtures are the
source of truth for the implementation's behaviour on edge cases.
"""

from __future__ import annotations

from typing import Any

from tone_forge.song_form.h2 import H2Result


# --- Low-level builders ------------------------------------------------------


def _chord(start_s: float, end_s: float, symbol: str) -> dict[str, Any]:
    return {"start_s": start_s, "end_s": end_s, "symbol": symbol}


def _section(start_s: float, end_s: float, name: str = "") -> dict[str, Any]:
    s: dict[str, Any] = {"start_s": start_s, "end_s": end_s}
    if name:
        s["name"] = name
    return s


def _chords_from_pcs(
    pcs_by_section: list[list[int]],
    chord_dur: float = 1.0,
    section_gap: float = 0.0,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build chord + section dicts from a per-section PC plan.

    Each PC becomes a `chord_dur`-second chord whose symbol is the
    canonical pitch-class symbol (`PC_SYMBOLS[pc]`). Section
    boundaries are placed at chord boundaries with an optional
    `section_gap` for safety.
    """
    chords: list[dict[str, Any]] = []
    sections: list[dict[str, Any]] = []
    t = 0.0
    for sec_idx, pcs in enumerate(pcs_by_section):
        sec_start = t
        for pc in pcs:
            chords.append(_chord(t, t + chord_dur, PC_SYMBOLS[pc]))
            t += chord_dur
        sec_end = t
        sections.append(_section(sec_start, sec_end, f"sec_{sec_idx}"))
        t += section_gap
    return chords, sections


# Canonical symbol per pitch class (sharp-side). The H2 extractor's
# parser maps these back to the same PC value, so round-trip is exact.
PC_SYMBOLS = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


# --- Fixture catalogue -------------------------------------------------------


def fixture_empty_chords() -> tuple[dict, H2Result]:
    """Spec §8 row 1: chords=[], sections present → all-zero, degenerate."""
    bundle = {
        "chords": [],
        "sections": [
            _section(0.0, 5.0, "alpha"),
            _section(5.0, 10.0, "beta"),
        ],
    }
    expected = H2Result(
        per_section=(0.0, 0.0),
        h2_sep=0.0,
        n_used=0,
        degenerate=True,
        section_names=("alpha", "beta"),
    )
    return bundle, expected


def fixture_empty_sections() -> tuple[dict, H2Result]:
    """Spec §8 row 2: chords present, sections=[] → empty, degenerate."""
    bundle = {
        "chords": [_chord(0.0, 1.0, "C")],
        "sections": [],
    }
    expected = H2Result(
        per_section=(),
        h2_sep=0.0,
        n_used=0,
        degenerate=True,
        section_names=(),
    )
    return bundle, expected


def fixture_single_chord_section() -> tuple[dict, H2Result]:
    """Spec §8 row 7 / checklist #4: one section has only 1 chord.

    full_seq = [0,2,4,5,0,2,4]  →  len=7  →  n=3
    full_seq trigrams: (0,2,4), (2,4,5), (4,5,0), (5,0,2), (0,2,4)
    Counter: (0,2,4)=2, rest=1
        sec 0 trigrams: (0,2,4) → 1.0
        sec 1 trigrams: [] (single chord)  → 0.0
        sec 2 trigrams: (0,2,4) → 1.0
    """
    chords, sections = _chords_from_pcs(
        [
            [0, 2, 4],
            [5],
            [0, 2, 4],
        ]
    )
    bundle = {"chords": chords, "sections": sections}
    expected = H2Result(
        per_section=(1.0, 0.0, 1.0),
        h2_sep=0.7071067801258875,
        n_used=3,
        degenerate=False,
        section_names=("sec_0", "sec_1", "sec_2"),
    )
    return bundle, expected


def fixture_bigram_fallback() -> tuple[dict, H2Result]:
    """Spec §3.4: full chord seq <6 → n_used=2.

    full_seq = [0,7,0,7,2]  →  len=5 < 6  →  n=2
    full_seq bigrams: (0,7), (7,0), (0,7), (7,2)
    Counter: (0,7)=2, (7,0)=1, (7,2)=1
        sec 0 bigrams: (0,7) → 1.0
        sec 1 bigrams: (0,7) → 1.0
        sec 2 bigrams: [] (single chord) → 0.0
    """
    chords, sections = _chords_from_pcs(
        [
            [0, 7],
            [0, 7],
            [2],
        ]
    )
    bundle = {"chords": chords, "sections": sections}
    expected = H2Result(
        per_section=(1.0, 1.0, 0.0),
        h2_sep=0.7071067801258875,
        n_used=2,
        degenerate=False,
        section_names=("sec_0", "sec_1", "sec_2"),
    )
    return bundle, expected


def fixture_singletons_no_ngrams() -> tuple[dict, H2Result]:
    """Every section has exactly 1 chord. full_seq has 3 chords so the
    extractor stays in bigram mode (not degenerate) but no section
    produces any bigrams.

    full_seq = [0, 2, 4]  →  len=3 < 6  →  n=2
    Each section has 0 bigrams → all per_section = 0.0.
    """
    chords, sections = _chords_from_pcs([[0], [2], [4]])
    bundle = {"chords": chords, "sections": sections}
    expected = H2Result(
        per_section=(0.0, 0.0, 0.0),
        h2_sep=0.0,
        n_used=2,
        degenerate=False,
        section_names=("sec_0", "sec_1", "sec_2"),
    )
    return bundle, expected


def fixture_truly_degenerate_one_chord_song() -> tuple[dict, H2Result]:
    """Spec §3.4 final clause: full_seq length < 2 → degenerate."""
    chords, sections = _chords_from_pcs([[0]])
    bundle = {"chords": chords, "sections": sections}
    expected = H2Result(
        per_section=(0.0,),
        h2_sep=0.0,
        n_used=0,
        degenerate=True,
        section_names=("sec_0",),
    )
    return bundle, expected


def fixture_parse_failure_dropped() -> tuple[dict, H2Result]:
    """Spec §8 row 4: unparseable symbol dropped, no exception.

    Section 0: C N.C. D E F → after drop: [0, 2, 4, 5] → trigrams
        (0,2,4), (2,4,5)
    Section 1: C D E F → trigrams (0,2,4), (2,4,5)

    Both sections share both trigrams, both repeat globally → both 1.0.
    """
    chords = [
        _chord(0.0, 1.0, "C"),
        _chord(1.0, 2.0, "N.C."),
        _chord(2.0, 3.0, "D"),
        _chord(3.0, 4.0, "E"),
        _chord(4.0, 5.0, "F"),
        _chord(5.0, 6.0, "C"),
        _chord(6.0, 7.0, "D"),
        _chord(7.0, 8.0, "E"),
        _chord(8.0, 9.0, "F"),
    ]
    sections = [
        _section(0.0, 5.0, "with_nc"),
        _section(5.0, 9.0, "without_nc"),
    ]
    bundle = {"chords": chords, "sections": sections}
    expected = H2Result(
        per_section=(1.0, 1.0),
        h2_sep=0.0,
        n_used=3,
        degenerate=False,
        section_names=("with_nc", "without_nc"),
    )
    return bundle, expected


def fixture_asymmetric_three_section() -> tuple[dict, H2Result]:
    """Main-path fixture with non-uniform per-section H2.

    Section A: C D E C D E (PCs 0,2,4,0,2,4)
    Section B: C D E F G (PCs 0,2,4,5,7)
    Section C: A B C# D# F (PCs 9,11,1,3,5)

    full_seq = [0,2,4,0,2,4, 0,2,4,5,7, 9,11,1,3,5]  (len=16, n=3)

    full_seq trigrams (boundary trigrams INCLUDED — spec §3.5):
        (0,2,4),(2,4,0),(4,0,2),(0,2,4),(2,4,0),(4,0,2),
        (0,2,4),(2,4,5),(4,5,7),(5,7,9),(7,9,11),
        (9,11,1),(11,1,3),(1,3,5)
    Counter:
        (0,2,4)=3, (2,4,0)=2, (4,0,2)=2,
        (2,4,5)=1, (4,5,7)=1, (5,7,9)=1, (7,9,11)=1,
        (9,11,1)=1, (11,1,3)=1, (1,3,5)=1

    Section A's own trigrams: (0,2,4),(2,4,0),(4,0,2),(0,2,4) — all 4 ≥2 → 1.0
    Section B's own trigrams: (0,2,4),(2,4,5),(4,5,7) — 1/3 ≥2 → 1/3
    Section C's own trigrams: (9,11,1),(11,1,3),(1,3,5) — 0/3 → 0.0

    mean = (1.0 + 1/3 + 0) / 3 = 4/9 ≈ 0.4444
    pstdev = sqrt(14/81) ≈ 0.4157
    h2_sep = 0.4157 / (0.4444 + 1e-9) ≈ 0.9354
    """
    chords, sections = _chords_from_pcs(
        [
            [0, 2, 4, 0, 2, 4],
            [0, 2, 4, 5, 7],
            [9, 11, 1, 3, 5],
        ]
    )
    bundle = {"chords": chords, "sections": sections}
    expected = H2Result(
        per_section=(1.0, 1.0 / 3.0, 0.0),
        h2_sep=0.9354143445888031,
        n_used=3,
        degenerate=False,
        section_names=("sec_0", "sec_1", "sec_2"),
    )
    return bundle, expected


def fixture_section_no_chords_inside() -> tuple[dict, H2Result]:
    """Spec §8 row 3: a section spans zero chords (no midpoint inside)."""
    # Chords concentrated in sec 0 and sec 2; sec 1 is a silent gap.
    chords = [
        _chord(0.0, 1.0, "C"),
        _chord(1.0, 2.0, "D"),
        _chord(2.0, 3.0, "E"),
        # Silent gap from 3.0 to 5.0 (sec_1 spans this with no chords inside)
        _chord(5.0, 6.0, "C"),
        _chord(6.0, 7.0, "D"),
        _chord(7.0, 8.0, "E"),
    ]
    sections = [
        _section(0.0, 3.0, "left"),
        _section(3.0, 5.0, "silent"),
        _section(5.0, 8.0, "right"),
    ]
    bundle = {"chords": chords, "sections": sections}
    # sec_0: trigram (0,2,4), global count 2 → 1.0
    # sec_1: no trigrams → 0.0
    # sec_2: trigram (0,2,4), global count 2 → 1.0
    expected = H2Result(
        per_section=(1.0, 0.0, 1.0),
        h2_sep=0.7071067801258875,
        n_used=3,
        degenerate=False,
        section_names=("left", "silent", "right"),
    )
    return bundle, expected


def fixture_uniform_repeat() -> tuple[dict, H2Result]:
    """Two-section identical-PC: h2_sep = 0.0 by construction.

    Sanity check: when sections are structurally identical, H2 is
    uniform across sections and separability is exactly zero.
    """
    chords, sections = _chords_from_pcs(
        [
            [0, 2, 4, 5, 7],
            [0, 2, 4, 5, 7],
        ]
    )
    bundle = {"chords": chords, "sections": sections}
    # Both sections: trigrams (0,2,4), (2,4,5), (4,5,7), each appears 2× → 1.0
    expected = H2Result(
        per_section=(1.0, 1.0),
        h2_sep=0.0,
        n_used=3,
        degenerate=False,
        section_names=("sec_0", "sec_1"),
    )
    return bundle, expected


# Registry — every fixture must be reachable from this dict so the
# golden-output test can iterate them all.
ALL_FIXTURES = {
    "empty_chords": fixture_empty_chords,
    "empty_sections": fixture_empty_sections,
    "single_chord_section": fixture_single_chord_section,
    "bigram_fallback": fixture_bigram_fallback,
    "singletons_no_ngrams": fixture_singletons_no_ngrams,
    "truly_degenerate_one_chord_song": fixture_truly_degenerate_one_chord_song,
    "parse_failure_dropped": fixture_parse_failure_dropped,
    "asymmetric_three_section": fixture_asymmetric_three_section,
    "section_no_chords_inside": fixture_section_no_chords_inside,
    "uniform_repeat": fixture_uniform_repeat,
}
