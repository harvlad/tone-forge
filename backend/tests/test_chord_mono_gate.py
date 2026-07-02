"""Fix 3 — ``filter_chords_in_monophonic_sections`` drops chords
inside monophonic-riff sections.

Chord recognition on a single-note riff is a category error — there
is no polyphonic signal to latch onto. The chord recognizer will
confabulate triads out of the overtone series of the fundamental.
This filter enforces the physical constraint that chord detection
requires ≥2 concurrent pitch classes.

Invariants pinned here:
  1. A section with high monophonic_ratio AND low pitch_class_diversity
     drops all chords whose midpoint falls inside it.
  2. Chords outside gated sections survive.
  3. Sections missing both top-level and debug_features signals are
     treated as NOT-gated (conservative default).
  4. Sections with a strong mono signal on `other` or `bass` via
     ``debug_features`` are gated (aggregation across harmonic stems).
  5. Vocals/drums stems being monophonic do NOT gate a section
     (they don't feed the chord recognizer).
  6. Empty inputs are handled cleanly (no crash).
  7. List-of-dicts (persistence shape) and Tuple[Chord] both round-trip.
"""
from __future__ import annotations

from tone_forge.analysis.chords import filter_chords_in_monophonic_sections
from tone_forge.contracts import Chord


def _sec(
    start_s: float,
    end_s: float,
    *,
    mono: float | None = None,
    diversity: float | None = None,
    debug_features: list | None = None,
) -> dict:
    sec: dict = {"start_s": start_s, "end_s": end_s}
    if mono is not None:
        sec["monophonic_ratio"] = mono
    if diversity is not None:
        sec["pitch_class_diversity"] = diversity
    if debug_features is not None:
        sec["debug_features"] = debug_features
    return sec


def _chord(start_s: float, end_s: float, symbol: str = "C") -> Chord:
    return Chord(
        start_s=start_s, end_s=end_s, symbol=symbol, confidence=0.5
    )


def test_gate_fires_on_top_level_signals() -> None:
    sections = [
        _sec(0.0, 5.0, mono=0.9, diversity=0.15),  # gated intro
        _sec(5.0, 10.0, mono=0.2, diversity=0.7),  # normal verse
    ]
    chords = (
        _chord(1.0, 2.0, "Dm"),   # inside gated intro → drop
        _chord(2.0, 3.0, "F"),    # inside gated intro → drop
        _chord(5.5, 6.5, "C"),    # inside verse → survive
        _chord(7.0, 8.0, "G"),    # inside verse → survive
    )
    out = filter_chords_in_monophonic_sections(chords, sections)
    assert [c.symbol for c in out] == ["C", "G"]


def test_gate_does_not_fire_below_mono_floor() -> None:
    sections = [_sec(0.0, 5.0, mono=0.5, diversity=0.15)]  # mono too low
    chords = (_chord(1.0, 2.0, "Dm"),)
    out = filter_chords_in_monophonic_sections(chords, sections)
    assert len(out) == 1


def test_gate_does_not_fire_above_diversity_ceiling() -> None:
    sections = [_sec(0.0, 5.0, mono=0.9, diversity=0.6)]  # diversity too high
    chords = (_chord(1.0, 2.0, "Dm"),)
    out = filter_chords_in_monophonic_sections(chords, sections)
    assert len(out) == 1


def test_gate_fires_via_debug_features_on_other_stem() -> None:
    """When top-level signals are absent, filter aggregates from
    ``debug_features``. 'other' stem strongly monophonic + narrow
    pc → gate fires."""
    sections = [_sec(
        0.0, 5.0,
        debug_features=[
            {"stem_name": "other", "monophonic_ratio": 0.95,
             "pitch_class_diversity": 0.10},
            {"stem_name": "bass", "monophonic_ratio": 0.6,
             "pitch_class_diversity": 0.30},
            {"stem_name": "vocals", "monophonic_ratio": 0.0,
             "pitch_class_diversity": 1.0},
            {"stem_name": "drums", "monophonic_ratio": 0.0,
             "pitch_class_diversity": 1.0},
        ],
    )]
    chords = (_chord(1.0, 2.0, "Dm"),)
    out = filter_chords_in_monophonic_sections(chords, sections)
    assert len(out) == 0


def test_gate_does_not_fire_when_only_vocals_or_drums_are_mono() -> None:
    """Vocals + drums monophonic while other/bass are polyphonic must
    NOT gate the section — those stems don't feed the chord
    recognizer."""
    sections = [_sec(
        0.0, 5.0,
        debug_features=[
            {"stem_name": "other", "monophonic_ratio": 0.20,
             "pitch_class_diversity": 0.85},
            {"stem_name": "bass", "monophonic_ratio": 0.30,
             "pitch_class_diversity": 0.75},
            {"stem_name": "vocals", "monophonic_ratio": 0.95,
             "pitch_class_diversity": 0.10},
            {"stem_name": "drums", "monophonic_ratio": 0.99,
             "pitch_class_diversity": 0.05},
        ],
    )]
    chords = (_chord(1.0, 2.0, "Dm"),)
    out = filter_chords_in_monophonic_sections(chords, sections)
    assert len(out) == 1


def test_missing_signals_conservative_default_survives() -> None:
    sections = [_sec(0.0, 5.0)]  # no signals at all
    chords = (_chord(1.0, 2.0, "Dm"),)
    out = filter_chords_in_monophonic_sections(chords, sections)
    assert len(out) == 1


def test_dict_shape_round_trips() -> None:
    """Pipeline persistence shape (list of dicts with start_s/end_s)
    is filtered and returned as a list."""
    sections = [_sec(0.0, 5.0, mono=0.9, diversity=0.15)]
    chords_dicts = [
        {"start_s": 1.0, "end_s": 2.0, "symbol": "Dm", "confidence": 0.5},
        {"start_s": 6.0, "end_s": 7.0, "symbol": "C", "confidence": 0.8},
    ]
    out = filter_chords_in_monophonic_sections(chords_dicts, sections)
    assert isinstance(out, list)
    assert len(out) == 1
    assert out[0]["symbol"] == "C"


def test_empty_inputs_are_noop() -> None:
    assert filter_chords_in_monophonic_sections((), []) == ()
    assert filter_chords_in_monophonic_sections([], []) == []


def test_supports_legacy_start_time_end_time_keys() -> None:
    """Some persistence layers use ``start_time``/``end_time`` instead
    of ``start_s``/``end_s``."""
    sections = [{
        "start_time": 0.0, "end_time": 5.0,
        "monophonic_ratio": 0.9, "pitch_class_diversity": 0.15,
    }]
    chords = (_chord(1.0, 2.0, "Dm"),)
    out = filter_chords_in_monophonic_sections(chords, sections)
    assert len(out) == 0
