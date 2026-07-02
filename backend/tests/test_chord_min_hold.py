"""Fix 2B — ``enforce_min_hold`` absorbs sub-beat chord flickers.

The Viterbi decoder emits chord regions on a fixed 0.5s grid; even
with the raised ``self_loop_bonus=0.03`` (Fix 2A) noisy distorted-
guitar chroma can produce 1-2 window flickers between two long stable
regions. ``enforce_min_hold`` absorbs any region shorter than
``min_beats * median_beat_dur`` into the higher-confidence neighbour.

Invariants pinned here:
  1. Single flicker between two long neighbours is absorbed into
     the higher-confidence neighbour, extending its span.
  2. All-short chord streams are collapsed to one region (walk is
     transitive).
  3. Empty beat grid is a no-op.
  4. Regions already at or above the hold floor are unchanged.
  5. Span contiguity: the overall (min-start, max-end) span is
     preserved.
"""
from __future__ import annotations

import numpy as np

from tone_forge.analysis.chords import enforce_min_hold
from tone_forge.contracts import Chord


def _beats(bpm: float = 120.0, count: int = 20) -> np.ndarray:
    period = 60.0 / bpm
    return np.arange(count) * period


def test_short_flicker_absorbed_into_higher_conf_prev() -> None:
    """A 0.2s flicker between two 2s regions is absorbed into the
    higher-confidence previous region."""
    beats = _beats(bpm=120.0, count=10)  # 0.5s per beat
    chords = (
        Chord(start_s=0.0, end_s=2.0, symbol="C", confidence=0.90),
        Chord(start_s=2.0, end_s=2.2, symbol="G", confidence=0.30),  # flicker
        Chord(start_s=2.2, end_s=4.0, symbol="F", confidence=0.80),
    )
    out = enforce_min_hold(chords, beats, min_beats=1.0)
    assert len(out) == 2, [c.symbol for c in out]
    assert out[0].symbol == "C"
    assert out[0].end_s == 2.2  # C swallowed the flicker
    assert out[1].symbol == "F"
    assert out[1].start_s == 2.2
    # Span preserved.
    assert out[0].start_s == 0.0 and out[-1].end_s == 4.0


def test_short_flicker_absorbed_into_higher_conf_next() -> None:
    """If the next region is more confident, the flicker is absorbed
    into it — flicker's span extends the next region's start_s."""
    beats = _beats(bpm=120.0, count=10)
    chords = (
        Chord(start_s=0.0, end_s=2.0, symbol="C", confidence=0.30),
        Chord(start_s=2.0, end_s=2.2, symbol="G", confidence=0.20),
        Chord(start_s=2.2, end_s=4.0, symbol="F", confidence=0.95),
    )
    out = enforce_min_hold(chords, beats, min_beats=1.0)
    assert len(out) == 2
    assert out[1].symbol == "F"
    assert out[1].start_s == 2.0  # F swallowed the flicker


def test_all_regions_meet_floor_is_noop() -> None:
    """Regions comfortably above ``min_beats * beat_dur`` are unchanged."""
    beats = _beats(bpm=120.0, count=10)  # beat_dur = 0.5s
    chords = (
        Chord(start_s=0.0, end_s=2.0, symbol="C", confidence=0.8),
        Chord(start_s=2.0, end_s=4.0, symbol="G", confidence=0.7),
    )
    out = enforce_min_hold(chords, beats, min_beats=1.0)
    assert out == chords


def test_no_beats_is_noop() -> None:
    """Without a beat grid we can't derive a hold floor; pass through."""
    chords = (
        Chord(start_s=0.0, end_s=2.0, symbol="C", confidence=0.8),
        Chord(start_s=2.0, end_s=2.1, symbol="G", confidence=0.2),
        Chord(start_s=2.1, end_s=4.0, symbol="F", confidence=0.9),
    )
    out = enforce_min_hold(chords, None, min_beats=1.0)
    assert out == chords


def test_empty_chords_is_noop() -> None:
    beats = _beats()
    assert enforce_min_hold((), beats, min_beats=1.0) == ()


def test_transitive_walk_collapses_run_of_flickers() -> None:
    """A run of sub-beat flickers collapses correctly — the higher-
    confidence anchor absorbs the neighbours one at a time."""
    beats = _beats(bpm=120.0, count=10)  # beat_dur 0.5s, floor 0.5s
    chords = (
        Chord(start_s=0.0, end_s=2.0, symbol="C", confidence=0.95),
        Chord(start_s=2.0, end_s=2.2, symbol="G", confidence=0.30),
        Chord(start_s=2.2, end_s=2.4, symbol="F", confidence=0.25),
        Chord(start_s=2.4, end_s=4.0, symbol="Am", confidence=0.60),
    )
    out = enforce_min_hold(chords, beats, min_beats=1.0)
    # After absorb, only C and Am should survive (the flickers are
    # sandwiched between them; C has higher conf so its end extends).
    labels = [c.symbol for c in out]
    assert "G" not in labels and "F" not in labels
    assert "C" in labels and "Am" in labels
    # Span preserved.
    assert out[0].start_s == 0.0 and out[-1].end_s == 4.0


def test_min_beats_configurable() -> None:
    """A caller with faster harmonic rhythm can lower ``min_beats``
    below 1.0 to preserve shorter regions."""
    beats = _beats(bpm=120.0, count=10)  # beat_dur 0.5s
    chords = (
        Chord(start_s=0.0, end_s=2.0, symbol="C", confidence=0.8),
        Chord(start_s=2.0, end_s=2.3, symbol="G", confidence=0.7),  # 0.3s
        Chord(start_s=2.3, end_s=4.0, symbol="F", confidence=0.8),
    )
    # With min_beats=0.5 (floor = 0.25s), the 0.3s G survives.
    out = enforce_min_hold(chords, beats, min_beats=0.5)
    assert len(out) == 3
    # With min_beats=1.0 (floor = 0.5s), the 0.3s G is absorbed.
    out2 = enforce_min_hold(chords, beats, min_beats=1.0)
    assert len(out2) == 2
