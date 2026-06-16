"""Behavioural pin for ``tone_forge.notation.fretboard.midi_to_fret``.

The JS implementation in ``backend/static/chord_diagrams.js`` is the
authoritative renderer for the Jam UI's lead-tab lane; this Python
port exists so the same algorithm is reachable from server-side code
and so we can pin its expected outputs without spinning up node.

The expected (string, fret) pairs here mirror the JS smoke tests in
``backend/tests/test_chord_diagrams_js.py::test_midi_to_fret_finds_lowest_fret_assignment``.
"""

from __future__ import annotations

import pytest

from tone_forge.notation import (
    STANDARD_TUNING,
    FretAssignment,
    midi_to_fret,
)


def test_standard_tuning_pitches() -> None:
    assert STANDARD_TUNING == (40, 45, 50, 55, 59, 64)


def test_open_low_E_is_string_0_fret_0() -> None:
    # MIDI 40 (E2) is the open low E string.
    assert midi_to_fret(40) == FretAssignment(string=0, fret=0)


def test_open_A_is_string_1_fret_0() -> None:
    # MIDI 45 (A2) is the open A string.
    assert midi_to_fret(45) == FretAssignment(string=1, fret=0)


def test_E3_picks_fret_2_on_D_string() -> None:
    # MIDI 52 (E3): could be fret 12 on low E, fret 7 on A, or fret 2
    # on D (open=50). Smallest fret wins: D string, fret 2.
    assert midi_to_fret(52) == FretAssignment(string=2, fret=2)


def test_C4_picks_fret_1_on_B_string() -> None:
    # MIDI 60 (C4): fret 8 on low E, fret 3 on A, fret 10 on D, fret
    # 5 on G, fret 1 on B (open=59), fret -4 on high E (invalid).
    # Smallest fret wins: B string, fret 1.
    assert midi_to_fret(60) == FretAssignment(string=4, fret=1)


def test_A4_picks_fret_5_on_high_E() -> None:
    # MIDI 69 (A4): smallest fret is 5 on the high E string (open=64).
    assert midi_to_fret(69) == FretAssignment(string=5, fret=5)


def test_E5_picks_fret_12_on_high_E() -> None:
    # MIDI 76 (E5): only reachable string is high E (open=64), fret 12.
    assert midi_to_fret(76) == FretAssignment(string=5, fret=12)


def test_below_low_E_returns_none() -> None:
    # MIDI 30 (F#1) is below the lowest open string (E2 = 40).
    assert midi_to_fret(30) is None


def test_custom_tuning_drop_D() -> None:
    # Drop-D: low string tuned down to D2 (MIDI 38).
    drop_d = (38, 45, 50, 55, 59, 64)
    # MIDI 38 is now reachable as open string 0.
    assert midi_to_fret(38, drop_d) == FretAssignment(string=0, fret=0)


def test_non_int_pitch_raises() -> None:
    with pytest.raises(TypeError):
        midi_to_fret(60.5)  # type: ignore[arg-type]
