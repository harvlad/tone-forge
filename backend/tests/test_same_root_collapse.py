"""Round-2 Fix 2 — same-root chord-region collapse.

Pins the harmonic-stability pass that absorbs quality flicker within
a stable root pitch class (the classic distorted-guitar artefact:
``[C#m 0.5s, C#5 0.5s, C#m 1.0s, C#5 0.5s]`` — musically all
"C#-tonality", but chroma ambiguity on the 3rd bin causes the
Viterbi to flip every window or two).
"""
from __future__ import annotations

import numpy as np

from tone_forge.analysis.chords import collapse_same_root_regions
from tone_forge.contracts import Chord


def _chord(start: float, end: float, symbol: str, conf: float = 0.6) -> Chord:
    return Chord(start_s=start, end_s=end, symbol=symbol, confidence=conf)


def _beats(step: float = 0.5, count: int = 40) -> np.ndarray:
    """Uniform beat grid — median beat_dur = ``step`` seconds."""
    return np.arange(count, dtype=np.float64) * step


def test_collapse_absorbs_quality_flicker_within_stable_root():
    """[C#m, C#5, C#m, C#5] over 2 beats → single collapsed region."""
    # Total span: 4 × 0.5s = 2.0s = 4 beats @ 0.5s step. Just at the
    # max_span_beats=4 guard. Test slightly under.
    chords = (
        _chord(0.0, 0.4, "C#m", conf=0.6),
        _chord(0.4, 0.8, "C#5", conf=0.7),
        _chord(0.8, 1.2, "C#m", conf=0.6),
        _chord(1.2, 1.6, "C#5", conf=0.65),
    )
    out = collapse_same_root_regions(chords, _beats(step=0.5))
    assert len(out) == 1, (
        f"expected 1 collapsed region; got {len(out)}: "
        f"{[c.symbol for c in out]}"
    )
    assert out[0].start_s == 0.0
    assert out[0].end_s == 1.6
    # C#5 has higher total (confidence × duration) → wins.
    #   C#5: 0.7*0.4 + 0.65*0.4 = 0.54
    #   C#m: 0.6*0.4 + 0.6*0.4 = 0.48
    assert out[0].symbol == "C#5", (
        f"expected C#5 to win the weighted vote; got {out[0].symbol!r}"
    )


def test_collapse_preserves_long_progression_via_max_span_guard():
    """Cmaj → Cmaj7 → C7 over 16 beats: too long → no collapse."""
    # 16 beats @ 0.5s = 8s. max_span_beats=4 × 0.5s = 2s → 8s > 2s → skip.
    chords = (
        _chord(0.0, 3.0, "C", conf=0.7),
        _chord(3.0, 6.0, "Cmaj7", conf=0.7),
        _chord(6.0, 8.0, "C7", conf=0.7),
    )
    out = collapse_same_root_regions(chords, _beats(step=0.5))
    assert len(out) == 3, (
        f"expected long same-root progression to survive; got "
        f"{[c.symbol for c in out]}"
    )


def test_collapse_stops_at_no_chord_boundary():
    """No-chord regions terminate a same-root run — never merged
    across silence."""
    chords = (
        _chord(0.0, 0.4, "C#m", conf=0.6),
        _chord(0.4, 0.8, "N", conf=0.0),  # no-chord terminator
        _chord(0.8, 1.2, "C#5", conf=0.7),
    )
    out = collapse_same_root_regions(chords, _beats(step=0.5))
    # The no-chord sits between the two C# regions and terminates the
    # run. Result must preserve the no-chord in its original position.
    symbols = [c.symbol for c in out]
    assert "N" in symbols, (
        f"no-chord region was dropped or merged; got {symbols}"
    )
    assert len(out) == 3, (
        f"expected 3 regions (no collapse across no-chord); got "
        f"{len(out)}: {symbols}"
    )


def test_collapse_stops_at_different_root_boundary():
    """Different roots never merge, even when adjacent."""
    chords = (
        _chord(0.0, 0.5, "C#m", conf=0.6),
        _chord(0.5, 1.0, "A", conf=0.7),
        _chord(1.0, 1.5, "F#m", conf=0.6),
    )
    out = collapse_same_root_regions(chords, _beats(step=0.5))
    assert len(out) == 3
    assert [c.symbol for c in out] == ["C#m", "A", "F#m"]


def test_collapse_enharmonic_root_normalisation():
    """C# and Db share pitch class 1 → they collapse together."""
    chords = (
        _chord(0.0, 0.4, "C#m", conf=0.6),
        _chord(0.4, 0.8, "Db5", conf=0.7),
    )
    out = collapse_same_root_regions(chords, _beats(step=0.5))
    assert len(out) == 1, (
        f"enharmonic C#/Db should collapse; got {[c.symbol for c in out]}"
    )


def test_collapse_no_op_on_single_region_input():
    """Single-region input passes through unchanged."""
    chords = (_chord(0.0, 1.0, "C#m", conf=0.6),)
    out = collapse_same_root_regions(chords, _beats())
    assert out is chords or list(out) == list(chords)


def test_collapse_supports_dict_shape():
    """Persistence-side dict rows collapse the same as Chord tuples."""
    dicts = [
        {"start_s": 0.0, "end_s": 0.4, "symbol": "C#m", "confidence": 0.6},
        {"start_s": 0.4, "end_s": 0.8, "symbol": "C#5", "confidence": 0.7},
    ]
    out = collapse_same_root_regions(dicts, _beats())
    assert isinstance(out, list)
    assert len(out) == 1
    assert out[0]["start_s"] == 0.0
    assert out[0]["end_s"] == 0.8
