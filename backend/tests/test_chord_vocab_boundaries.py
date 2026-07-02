"""Round-2 Fix 4 — chord-vocabulary Jaccard boundary detector.

Pins the harmonic-content-signal boundary refinement pass. Splits
sections where the chord vocabulary shifts sharply across a candidate
seam even though energy is stable. Genre-neutral; scale-free (Jaccard
distance doesn't depend on vocabulary size).
"""
from __future__ import annotations

import numpy as np

from tone_forge.analysis.chord_vocab_boundaries import (
    detect_chord_vocab_boundaries,
)


def _section(start_s: float, end_s: float) -> dict:
    return {"start_time": start_s, "end_time": end_s, "type": "unknown"}


def _chord(start_s: float, end_s: float, symbol: str) -> dict:
    return {
        "start_s": start_s,
        "end_s": end_s,
        "symbol": symbol,
        "confidence": 0.7,
    }


def _beats(step: float = 0.5, count: int = 100) -> np.ndarray:
    return np.arange(count, dtype=np.float64) * step


def test_detects_boundary_at_vocab_shift():
    """Single section [0-40s] with F# only in [0-20s] and {C, G, Am, F}
    in [20-40s] → boundary detected at ~20s."""
    sections = [_section(0.0, 40.0)]
    chords = [
        # First half: F# vamp only.
        _chord(0.0, 5.0, "F#"),
        _chord(5.0, 10.0, "F#"),
        _chord(10.0, 15.0, "F#"),
        _chord(15.0, 20.0, "F#"),
        # Second half: 4-chord progression.
        _chord(20.0, 22.5, "C"),
        _chord(22.5, 25.0, "G"),
        _chord(25.0, 27.5, "Am"),
        _chord(27.5, 30.0, "F"),
        _chord(30.0, 32.5, "C"),
        _chord(32.5, 35.0, "G"),
        _chord(35.0, 37.5, "Am"),
        _chord(37.5, 40.0, "F"),
    ]
    boundaries = detect_chord_vocab_boundaries(
        sections, chords, _beats(step=0.5, count=100),
    )
    assert boundaries, "expected a boundary at the F#-→-{C,G,Am,F} seam"
    # Should be within a couple beats of 20s.
    times = [row["time_s"] for row in boundaries]
    assert any(18.0 <= t <= 22.0 for t in times), (
        f"expected a boundary near 20s; got {times}"
    )
    # All boundaries should be tagged to source section index 0.
    assert all(row["source_section_index"] == 0 for row in boundaries)


def test_no_boundary_when_vocab_is_stable():
    """Same 4-chord progression throughout → no boundary."""
    sections = [_section(0.0, 40.0)]
    chords = []
    t = 0.0
    for _ in range(10):
        for sym in ("C", "G", "Am", "F"):
            chords.append(_chord(t, t + 1.0, sym))
            t += 1.0
    boundaries = detect_chord_vocab_boundaries(
        sections, chords, _beats(step=0.5, count=100),
    )
    assert boundaries == [], (
        f"expected no boundaries on a stable-vocab section; got {boundaries}"
    )


def test_empty_sections_returns_empty():
    """Defensive: no sections → no boundaries."""
    boundaries = detect_chord_vocab_boundaries(
        [], [_chord(0.0, 1.0, "C")], _beats(),
    )
    assert boundaries == []


def test_missing_beats_returns_empty():
    """Defensive: no beat grid → skip stage cleanly."""
    boundaries = detect_chord_vocab_boundaries(
        [_section(0.0, 40.0)], [_chord(0.0, 1.0, "C")], None,
    )
    assert boundaries == []


def test_empty_chords_returns_empty():
    """Defensive: no chords → no vocabulary → no boundaries."""
    boundaries = detect_chord_vocab_boundaries(
        [_section(0.0, 40.0)], [], _beats(),
    )
    assert boundaries == []


def test_min_sub_duration_guard_rejects_edge_splits():
    """A vocab shift 3s from a section edge violates the
    ``min_sub_duration_s=8`` guard → no boundary emitted."""
    sections = [_section(0.0, 40.0)]
    chords = [
        # F# for the first 3s only, then a stable {C,G,Am,F}
        # progression. Vocab shift is at 3s — inside the 8s edge
        # guard.
        _chord(0.0, 3.0, "F#"),
    ]
    t = 3.0
    for _ in range(10):
        for sym in ("C", "G", "Am", "F"):
            chords.append(_chord(t, t + 1.0, sym))
            t += 1.0
    boundaries = detect_chord_vocab_boundaries(
        sections, chords, _beats(step=0.5, count=100),
    )
    # 3s from edge < 8s guard → boundary suppressed.
    assert boundaries == [], (
        f"boundary too close to section edge should be suppressed; got "
        f"{boundaries}"
    )
