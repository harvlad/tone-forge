"""Round-2 Fix 3 — duration filter + note-event mono ratio.

Pins the two-part fix that makes the ``monophonic_ratio`` signal
robust to (a) drum-transient bleed on Demucs-separated "other" stems
and (b) voxel-quantisation artefacts. The final ``monophonic_ratio``
is the max of the voxel-based and note-event-based metrics — a
section is monophonic if EITHER metric says so.
"""
from __future__ import annotations

import pytest

from tone_forge.analysis.section_features import (
    _filter_drum_transient_notes,
    _note_level_mono_ratio,
    compute_section_features,
)


def _clipped(*notes):
    """Build a ``_clip_notes_to_section``-shaped list from tuples."""
    return [(pitch, start, end) for pitch, start, end in notes]


def test_duration_filter_drops_drum_transients():
    """Sub-40 ms notes drop; ≥40 ms notes survive."""
    clipped = _clipped(
        (60, 0.00, 0.20),   # 200 ms — riff note, keep
        (61, 0.25, 0.27),   # 20 ms — drum transient, drop
        (62, 0.30, 0.50),   # 200 ms — riff note, keep
        (63, 0.55, 0.58),   # 30 ms — extractor fragment, drop
    )
    survivors = _filter_drum_transient_notes(clipped)
    assert len(survivors) == 2
    pitches = sorted(p for p, _, _ in survivors)
    assert pitches == [60, 62]


def test_duration_filter_boundary_at_40ms_inclusive():
    """The 40 ms threshold is inclusive: exactly 40 ms notes survive."""
    clipped = _clipped(
        (60, 0.0, 0.040),   # exactly 40 ms — keep
        (61, 0.1, 0.139),   # 39 ms — drop
    )
    survivors = _filter_drum_transient_notes(clipped)
    pitches = [p for p, _, _ in survivors]
    assert pitches == [60]


def test_note_level_mono_ratio_perfect_on_mono_riff():
    """A pure single-line riff (no note overlaps another) → 1.0."""
    clipped = _clipped(
        (60, 0.00, 0.20),
        (62, 0.20, 0.40),
        (64, 0.40, 0.60),
        (65, 0.60, 0.80),
    )
    assert _note_level_mono_ratio(clipped) == pytest.approx(1.0)


def test_note_level_mono_ratio_zero_on_chord():
    """A chord (3 notes at same start+end) → all notes overlap at
    midpoint → mono ratio 0.0."""
    clipped = _clipped(
        (60, 0.0, 1.0),
        (64, 0.0, 1.0),
        (67, 0.0, 1.0),
    )
    assert _note_level_mono_ratio(clipped) == pytest.approx(0.0)


def test_note_level_mono_ratio_robust_to_transient_bleed():
    """Mono riff + 5 spurious sub-40ms drum-transient bleed notes →
    still 1.0 (transients filtered out before mono computation)."""
    clipped = _clipped(
        # Real riff — 4 non-overlapping 200 ms notes.
        (60, 0.00, 0.20),
        (62, 0.20, 0.40),
        (64, 0.40, 0.60),
        (65, 0.60, 0.80),
        # Drum-transient bleed — 5 sub-40 ms notes that WOULD overlap
        # each riff midpoint if we didn't filter them first.
        (48, 0.09, 0.11),
        (49, 0.29, 0.31),
        (50, 0.49, 0.51),
        (51, 0.69, 0.71),
        (52, 0.10, 0.12),
    )
    assert _note_level_mono_ratio(clipped) == pytest.approx(1.0)


def test_note_level_mono_ratio_empty_input():
    """Empty input → 0.0 (matches voxel-metric degenerate convention)."""
    assert _note_level_mono_ratio([]) == 0.0
    # Also all-transient input → nothing survives filter → 0.0.
    all_transients = _clipped(
        (60, 0.0, 0.02),
        (61, 0.1, 0.12),
    )
    assert _note_level_mono_ratio(all_transients) == 0.0


def test_compute_section_features_uses_max_of_both_mono_metrics():
    """``compute_section_features`` returns the max of the voxel-based
    and note-event-based mono ratios in the ``monophonic_ratio`` field.

    Constructing an input where the voxel metric scores LOW (drum
    bleed overlaps riff notes in the voxel grid) but the note-event
    metric scores HIGH (transients filtered before mono computation).
    """
    # Mono riff — 4 non-overlapping notes across 0-1s.
    riff_notes = [
        {"pitch": 60, "start": 0.00, "end": 0.20, "velocity": 100},
        {"pitch": 62, "start": 0.20, "end": 0.40, "velocity": 100},
        {"pitch": 64, "start": 0.40, "end": 0.60, "velocity": 100},
        {"pitch": 65, "start": 0.60, "end": 0.80, "velocity": 100},
    ]
    # Drum-transient bleed — 20 ms notes overlapping every riff
    # midpoint. Below the 40 ms threshold → dropped from both
    # voxel-input AND note-event mono computation.
    bleed = [
        {"pitch": 48, "start": 0.09, "end": 0.11, "velocity": 30},
        {"pitch": 49, "start": 0.29, "end": 0.31, "velocity": 30},
        {"pitch": 50, "start": 0.49, "end": 0.51, "velocity": 30},
        {"pitch": 51, "start": 0.69, "end": 0.71, "velocity": 30},
    ]
    features = compute_section_features(
        stem_name="other",
        stem_midi=riff_notes + bleed,
        chord_regions=(),
        section_start_s=0.0,
        section_end_s=1.0,
    )
    # After Fix 3, the mono ratio should be >= 0.7 (mono-gate floor)
    # thanks to the duration filter clearing the bleed before both
    # metrics are computed.
    assert features.monophonic_ratio >= 0.7, (
        f"expected mono ratio >= 0.7 after transient filter; got "
        f"{features.monophonic_ratio}"
    )
