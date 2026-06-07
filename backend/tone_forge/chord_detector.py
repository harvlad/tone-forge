"""Backward-compat shim.

The canonical home for chord detection moved to
``tone_forge.analysis.chord_detector`` as part of the subsystem
boundary freeze (see ``/EXECUTION_PLAN.md`` Priority 1). This module
re-exports the public surface so existing callers keep working.

New code in the analysis subsystem should use
``tone_forge.analysis.chords.detect_chords`` (returns the
``contracts.Chord`` shape). Direct callers of the internal types here
remain in ``tone_forge.midi`` and ``tone_forge.ableton_session``, both
of which predate the boundary freeze.
"""
from tone_forge.analysis.chord_detector import (  # noqa: F401
    CHORD_TEMPLATES,
    NOTE_NAMES,
    Chord,
    ChordProgression,
    analyze_chord_progression,
    detect_chords_from_audio,
    detect_chords_from_midi,
    group_notes_into_chords,
)
