"""Notation Engine: render notation/tablature views.

Houses the Python port of the JS ``midiToFret`` helper from
``backend/static/chord_diagrams.js``. The JS module is the
authoritative renderer; the Python copy exists so the same algorithm is
reachable from server-side code (pre-baked tab generation, batch
export) and as a behavioural pin for the JS implementation.
"""

from tone_forge.notation.fretboard import (
    STANDARD_TUNING,
    FretAssignment,
    midi_to_fret,
)

__all__ = ["STANDARD_TUNING", "FretAssignment", "midi_to_fret"]
