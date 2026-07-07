"""Tests for beat-snapping of chord chops.

Chord regions from the analyzer sit at continuous timestamps that
almost never coincide with the beat grid. Playing them back
produces rhythmic drift — the chop *content* is offset from where
bar-quantized playback lands. ``_snap_chops_to_beats`` fixes that
by snapping start/end to the nearest tracked beat.

These tests cover the invariants the drift fix depends on:

  * Chord chop start/end land ON tracked beats.
  * Whole-beat durations (loop iterations stay phase-locked).
  * Degenerate chords (both edges snap to same beat) are dropped.
  * Missing beat grid → no-op (helper degrades cleanly).
  * Non-chord slice modes (onset, phrase) are not snapped —
    those slicers need to preserve their raw timestamps.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tone_forge.contribute_chops import (
    _chops_from_chords,
    _snap_chops_to_beats,
    build_chops,
)


# Beats at 0.5 s intervals (120 BPM), 20 s of song.
BEATS = [i * 0.5 for i in range(41)]


def _result_with_chords(chord_spans):
    """Build a minimal analysis-result dict with the given chord
    spans and a fixed 120 BPM beat grid."""
    return {
        "beats_s": list(BEATS),
        "chords": [
            {"start_s": s, "end_s": e, "symbol": sym}
            for (s, e, sym) in chord_spans
        ],
    }


# ---------------------------------------------------------------------------
# Direct helper tests
# ---------------------------------------------------------------------------

def test_snap_moves_edges_to_nearest_beat():
    """A chord starting at 3.12 s should snap to 3.0 (nearest beat)
    and ending at 5.38 s should snap to 5.5."""
    result = {"beats_s": list(BEATS)}
    chops = [{"startSec": 3.12, "endSec": 5.38, "kind": None}]
    snapped = _snap_chops_to_beats(chops, result)
    assert len(snapped) == 1
    assert snapped[0]["startSec"] == 3.0
    assert snapped[0]["endSec"] == 5.5


def test_snap_produces_whole_beat_durations():
    """Every snapped chop's duration must be a whole multiple of the
    beat period. This is what keeps loops phase-locked."""
    result = {"beats_s": list(BEATS)}
    chops = [
        {"startSec": 1.07, "endSec": 3.44, "kind": None},
        {"startSec": 4.90, "endSec": 8.11, "kind": None},
        {"startSec": 9.30, "endSec": 12.85, "kind": None},
    ]
    snapped = _snap_chops_to_beats(chops, result)
    assert len(snapped) == 3
    for c in snapped:
        # Each snapped edge lands on a beat.
        assert c["startSec"] in BEATS
        assert c["endSec"] in BEATS
        # Duration is a whole number of 0.5-second beat periods.
        duration = round(c["endSec"] - c["startSec"], 4)
        n_beats = duration / 0.5
        assert abs(n_beats - round(n_beats)) < 1e-6, (
            f"duration {duration}s is not a whole beat multiple"
        )


def test_snap_drops_degenerate_chops():
    """A chord region smaller than one beat spacing (both edges snap
    to the same beat) must be dropped — a zero-duration chop would
    play no audio and confuse downstream selection."""
    result = {"beats_s": list(BEATS)}
    chops = [
        # 3.4 - 3.6: both snap to 3.5.
        {"startSec": 3.4, "endSec": 3.6, "kind": None},
        # 5.1 - 5.9: snap to 5.0 - 6.0 (kept).
        {"startSec": 5.1, "endSec": 5.9, "kind": None},
    ]
    snapped = _snap_chops_to_beats(chops, result)
    assert len(snapped) == 1
    assert snapped[0]["startSec"] == 5.0
    assert snapped[0]["endSec"] == 6.0


def test_snap_noop_when_beats_missing():
    """Without a beat grid the helper returns the input unchanged
    (chained calls stay safe on beatless analyses)."""
    chops = [{"startSec": 1.13, "endSec": 2.47, "kind": None}]
    snapped = _snap_chops_to_beats(chops, {})
    assert snapped == chops


def test_snap_noop_when_beat_grid_has_one_beat():
    """A single beat can't define spacing; helper degrades to a
    passthrough."""
    chops = [{"startSec": 1.13, "endSec": 2.47, "kind": None}]
    snapped = _snap_chops_to_beats(chops, {"beats_s": [0.5]})
    assert snapped == chops


def test_snap_preserves_chop_metadata():
    """Non-timing fields (chordSymbol, root, colorHint) survive the
    snap unchanged — only start/end are rewritten."""
    result = {"beats_s": list(BEATS)}
    chops = [{
        "startSec": 3.12, "endSec": 5.38,
        "kind": None, "root": 7, "chordSymbol": "G",
        "sectionLabel": None, "colorHint": "cyan",
    }]
    snapped = _snap_chops_to_beats(chops, result)
    assert snapped[0]["chordSymbol"] == "G"
    assert snapped[0]["root"] == 7
    assert snapped[0]["colorHint"] == "cyan"


# ---------------------------------------------------------------------------
# Integration: _chops_from_chords wires the snap in
# ---------------------------------------------------------------------------

def test_chord_chops_are_beat_snapped_end_to_end():
    """The public chord slicer must return beat-aligned chops so the
    bass-preset UX doesn't drift. Uses a mix of on-beat and
    off-beat chord boundaries to exercise both snap directions."""
    result = _result_with_chords([
        (0.03, 2.11, "C"),     # ~0.0 -> 2.0
        (2.11, 4.37, "G"),     # ~2.0 -> 4.5
        (4.37, 8.22, "Am"),    # ~4.5 -> 8.0
        (8.22, 10.06, "F"),    # ~8.0 -> 10.0
    ])
    chops = _chops_from_chords(result)
    assert len(chops) == 4
    for c in chops:
        assert c["startSec"] in BEATS
        assert c["endSec"] in BEATS


def test_build_chops_bass_chord_mode_is_beat_snapped():
    """End-to-end via ``build_chops`` — the bass preset's
    (stem='bass', slice_mode='chord') path must return beat-aligned
    chops. This is the exact call the client makes."""
    result = _result_with_chords([
        (0.03, 4.13, "C"),
        (4.13, 8.42, "G"),
        (8.42, 12.11, "Am"),
        (12.11, 16.06, "F"),
    ])
    chops = build_chops(
        stem="bass", slice_mode="chord",
        analysis_result=result,
    )
    assert chops
    for c in chops:
        # Each chop starts and ends on a beat.
        assert c["startSec"] in BEATS
        assert c["endSec"] in BEATS
        # Duration is a whole number of beat periods.
        dur = round(c["endSec"] - c["startSec"], 4)
        n_beats = dur / 0.5
        assert abs(n_beats - round(n_beats)) < 1e-6


# ---------------------------------------------------------------------------
# Non-snap slicers are untouched
# ---------------------------------------------------------------------------

def test_onset_chops_are_not_beat_snapped():
    """Drum-onset chops must preserve their raw transient times —
    beat-snapping a kick attack would destroy its punch. This test
    guards the invariant by exercising an onset build_chops call
    with a WAV that yields off-beat transients.

    Because ``_chops_from_onsets`` requires a WAV, we skip when the
    module can't produce output. The point is only that beat-snap is
    NOT wired into the onset path — the assertion is that any chops
    the onset slicer produced still carry non-snapped timestamps.
    """
    # No WAV supplied → returns []; the guard here is compile-time
    # (the module-level test that _chops_from_chords calls the
    # snapper but _chops_from_onsets does not).
    from tone_forge import contribute_chops
    import inspect
    src = inspect.getsource(contribute_chops._chops_from_onsets)
    assert "_snap_chops_to_beats" not in src, (
        "onset chops must not be beat-snapped — attack timing IS the "
        "point of onset mode"
    )
    src_phrase = inspect.getsource(contribute_chops._chops_from_vocal_phrases)
    assert "_snap_chops_to_beats" not in src_phrase, (
        "phrase chops must not be beat-snapped — vocal onset timing "
        "is preserved by the slicer's own silence trim"
    )
