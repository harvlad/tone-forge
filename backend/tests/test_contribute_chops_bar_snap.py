"""Tests for bar-snapping of chord chops.

After beat-snap fixes fractional-beat offset, chord chops still
carry a whole-BEAT duration that isn't necessarily a whole-BAR
duration. The bass preset triggers with ``quantize='bar'`` — a
3-beat chop retriggered every 4 beats drifts one beat per loop
iteration. ``_snap_chops_to_bars`` fixes that by snapping start/end
to the nearest tracked downbeat.

These tests cover the invariants the bar-lock depends on:

  * Chord chop start/end land ON tracked downbeats.
  * Durations are a whole multiple of the bar period.
  * Degenerate chords (both edges snap to same downbeat) are dropped.
  * Missing / single-downbeat grid → no-op.
  * Non-chord slice modes (onset, phrase) are not bar-snapped.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tone_forge.contribute_chops import (
    _chops_from_chords,
    _snap_chops_to_bars,
    build_chops,
)


# Beats at 0.5 s intervals (120 BPM), 4/4 → downbeats every 2 s.
BEATS = [i * 0.5 for i in range(41)]           # 0.0, 0.5, …, 20.0
DOWNBEATS = [i * 2.0 for i in range(11)]        # 0.0, 2.0, …, 20.0
BAR_PERIOD = 2.0


def _result_with_chords(chord_spans):
    """Build a minimal analysis-result dict with the given chord
    spans plus a 120 BPM / 4-4 beat + downbeat grid."""
    return {
        "beats_s": list(BEATS),
        "downbeats_s": list(DOWNBEATS),
        "chords": [
            {"start_s": s, "end_s": e, "symbol": sym}
            for (s, e, sym) in chord_spans
        ],
    }


# ---------------------------------------------------------------------------
# Direct helper tests
# ---------------------------------------------------------------------------

def test_bar_snap_moves_edges_to_nearest_downbeat():
    """A chop at (3.1, 5.4) → downbeats at 2.0 and 6.0 (nearest).
    Both edges collapse to the closer downbeat."""
    result = {"downbeats_s": list(DOWNBEATS)}
    chops = [{"startSec": 3.1, "endSec": 5.4, "kind": None}]
    snapped = _snap_chops_to_bars(chops, result)
    assert len(snapped) == 1
    # 3.1 is between 2.0 and 4.0 → nearer to 4.0 (dist 0.9 vs 1.1).
    assert snapped[0]["startSec"] == 4.0
    # 5.4 is between 4.0 and 6.0 → nearer to 6.0 (dist 0.6 vs 1.4).
    assert snapped[0]["endSec"] == 6.0


def test_bar_snap_produces_whole_bar_durations():
    """Every bar-snapped chop's duration is a whole multiple of the
    bar period (2 s here). This is what keeps loops phase-locked."""
    result = {"downbeats_s": list(DOWNBEATS)}
    chops = [
        {"startSec": 1.07, "endSec": 4.44, "kind": None},
        {"startSec": 4.90, "endSec": 10.11, "kind": None},
        {"startSec": 9.30, "endSec": 15.85, "kind": None},
    ]
    snapped = _snap_chops_to_bars(chops, result)
    assert len(snapped) == 3
    for c in snapped:
        assert c["startSec"] in DOWNBEATS
        assert c["endSec"] in DOWNBEATS
        duration = round(c["endSec"] - c["startSec"], 4)
        n_bars = duration / BAR_PERIOD
        assert abs(n_bars - round(n_bars)) < 1e-6, (
            f"duration {duration}s is not a whole bar multiple"
        )


def test_bar_snap_drops_degenerate_chops():
    """A chord region smaller than one bar (both edges snap to the
    same downbeat) must be dropped — a zero-duration chop plays
    silence."""
    result = {"downbeats_s": list(DOWNBEATS)}
    chops = [
        # 3.4 - 4.6: both snap to 4.0 → dropped.
        {"startSec": 3.4, "endSec": 4.6, "kind": None},
        # 4.2 - 7.9: snap to 4.0 - 8.0 (kept, 2 bars).
        {"startSec": 4.2, "endSec": 7.9, "kind": None},
    ]
    snapped = _snap_chops_to_bars(chops, result)
    assert len(snapped) == 1
    assert snapped[0]["startSec"] == 4.0
    assert snapped[0]["endSec"] == 8.0


def test_bar_snap_noop_when_downbeats_missing():
    """Without a downbeat grid the helper returns the input
    unchanged — chained calls stay safe on beatless analyses."""
    chops = [{"startSec": 1.13, "endSec": 2.47, "kind": None}]
    snapped = _snap_chops_to_bars(chops, {})
    assert snapped == chops


def test_bar_snap_noop_when_only_one_downbeat():
    """A single downbeat can't define bar spacing; helper degrades
    to a passthrough."""
    chops = [{"startSec": 1.13, "endSec": 2.47, "kind": None}]
    snapped = _snap_chops_to_bars(chops, {"downbeats_s": [0.0]})
    assert snapped == chops


def test_bar_snap_preserves_chop_metadata():
    """Non-timing fields (chordSymbol, root, colorHint) survive the
    snap unchanged — only start/end are rewritten."""
    result = {"downbeats_s": list(DOWNBEATS)}
    chops = [{
        "startSec": 3.12, "endSec": 5.38,
        "kind": None, "root": 7, "chordSymbol": "G",
        "sectionLabel": None, "colorHint": "cyan",
    }]
    snapped = _snap_chops_to_bars(chops, result)
    assert snapped[0]["chordSymbol"] == "G"
    assert snapped[0]["root"] == 7
    assert snapped[0]["colorHint"] == "cyan"


# ---------------------------------------------------------------------------
# Integration: _chops_from_chords chains beat-snap then bar-snap
# ---------------------------------------------------------------------------

def test_chord_chops_are_bar_snapped_end_to_end():
    """The public chord slicer's output must land on downbeats and
    span whole bars — the bass preset's ``quantize='bar'`` locks
    depend on this."""
    result = _result_with_chords([
        (0.03, 2.11, "C"),      # → 0.0 -> 2.0
        (2.11, 4.37, "G"),      # → 2.0 -> 4.0
        (4.37, 8.22, "Am"),     # → 4.0 -> 8.0
        (8.22, 10.06, "F"),     # → 8.0 -> 10.0
    ])
    chops = _chops_from_chords(result)
    assert len(chops) == 4
    for c in chops:
        assert c["startSec"] in DOWNBEATS
        assert c["endSec"] in DOWNBEATS
        dur = round(c["endSec"] - c["startSec"], 4)
        n_bars = dur / BAR_PERIOD
        assert abs(n_bars - round(n_bars)) < 1e-6


def test_build_chops_bass_chord_mode_is_bar_snapped():
    """End-to-end via ``build_chops`` — the bass preset's
    (stem='bass', slice_mode='chord') path must return bar-aligned
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
        assert c["startSec"] in DOWNBEATS
        assert c["endSec"] in DOWNBEATS
        dur = round(c["endSec"] - c["startSec"], 4)
        n_bars = dur / BAR_PERIOD
        assert abs(n_bars - round(n_bars)) < 1e-6


# ---------------------------------------------------------------------------
# Non-snap slicers are untouched
# ---------------------------------------------------------------------------

def test_onset_and_phrase_chops_are_not_bar_snapped():
    """Onset and phrase slicers must preserve raw timestamps —
    bar-snapping a kick attack or vocal onset would destroy its
    punch. Guarded by source inspection so the invariant survives
    future refactors."""
    from tone_forge import contribute_chops
    import inspect
    src_onset = inspect.getsource(contribute_chops._chops_from_onsets)
    assert "_snap_chops_to_bars" not in src_onset, (
        "onset chops must not be bar-snapped — attack timing IS "
        "the point of onset mode"
    )
    src_phrase = inspect.getsource(contribute_chops._chops_from_vocal_phrases)
    assert "_snap_chops_to_bars" not in src_phrase, (
        "phrase chops must not be bar-snapped — vocal onset timing "
        "is preserved by the slicer's own silence trim"
    )
