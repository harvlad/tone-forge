"""Python port of the JS ``midiToFret`` helper from
``backend/static/chord_diagrams.js``.

The JS module is the authoritative renderer (it runs inside the Jam
UI). This module is a behavioural pin for that algorithm and a
forward-looking hook for server-side notation work (pre-baked tab
generation, Songsterr / GuitarPro export).

The algorithm is intentionally simple: for a given MIDI pitch, walk
the open-string pitches in `tuning`, compute `pitch - open_pitch`, and
return the assignment with the smallest non-negative fret. Returns
None if the pitch is below the lowest open string (i.e. unreachable
on any string).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence


# Standard 6-string guitar tuning, low-to-high (E2 A2 D3 G3 B3 E4) in
# MIDI numbers. Mirror of STANDARD_TUNING in chord_diagrams.js.
STANDARD_TUNING: tuple[int, ...] = (40, 45, 50, 55, 59, 64)


@dataclass(frozen=True)
class FretAssignment:
    """Where a single MIDI pitch lives on the fretboard.

    ``string`` is 0-indexed low-to-high (0 = low E, 5 = high E).
    ``fret`` is 0 (open) through whatever fret the player can reach;
    no upper bound is enforced here — callers that need a 24-fret
    pin should clamp at the call site.
    """

    string: int
    fret: int


def midi_to_fret(
    pitch: int,
    tuning: Sequence[int] = STANDARD_TUNING,
) -> Optional[FretAssignment]:
    """Map a MIDI pitch to a (string, fret) assignment.

    Picks the assignment with the smallest non-negative fret across
    all strings whose open pitch is <= the target pitch. Returns
    ``None`` if the pitch is below every string's open pitch (i.e.
    unreachable on the given tuning).

    This is a faithful port of ``midiToFret`` in
    ``backend/static/chord_diagrams.js``; see that file for the
    in-browser caller.
    """
    if not isinstance(pitch, int):
        raise TypeError(f"pitch must be int, got {type(pitch).__name__}")
    best: Optional[FretAssignment] = None
    for string_idx, open_pitch in enumerate(tuning):
        fret = pitch - open_pitch
        if fret < 0:
            continue
        if best is None or fret < best.fret:
            best = FretAssignment(string=string_idx, fret=fret)
    return best
