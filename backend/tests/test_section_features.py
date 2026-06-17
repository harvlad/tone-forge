"""Unit tests for ``analysis.section_features``.

Verifies that each of the five signals lands in its expected band on
synthetic MIDI fixtures. These bands are the basis for the classifier
threshold defaults in ``guidance_mode.GuidanceThresholds``.

Fixture helpers are inlined here rather than living in a shared module
because there is no test-side package on this repo (no
``tests/__init__.py``); the fixture surface is small enough that
duplication-by-design is cheaper than adding sys-path plumbing.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

import numpy as np

from tone_forge.analysis.section_features import (
    SectionFeatures,
    compute_section_features,
)


# ---------------------------------------------------------------------------
# Fixture builders (also used by test_guidance_mode_integration.py through
# replication — they are deterministic and short).
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _ChordRow:
    start_s: float
    end_s: float
    symbol: str
    confidence: float = 1.0


def _note(pitch: int, start: float, end: float, velocity: int = 96) -> dict:
    return {"pitch": pitch, "start": start, "end": end, "velocity": velocity}


def _chord_block_4bar(
    tempo_bpm: float = 120.0,
) -> Tuple[List[dict], List[_ChordRow], Tuple[float, float]]:
    beat_s = 60.0 / tempo_bpm
    bar_s = beat_s * 4
    chords = [
        ("C", [60, 64, 67]),
        ("G", [55, 59, 62]),
        ("Am", [57, 60, 64]),
        ("F", [53, 57, 60]),
    ]
    notes: List[dict] = []
    regions: List[_ChordRow] = []
    for i, (sym, pitches) in enumerate(chords):
        t0 = i * bar_s
        t1 = t0 + bar_s
        for p in pitches:
            notes.append(_note(p, t0, t1))
        regions.append(_ChordRow(t0, t1, sym))
    return notes, regions, (0.0, 4 * bar_s)


def _riff_e2g2a2d3_4x(
    tempo_bpm: float = 120.0,
) -> Tuple[List[dict], List[_ChordRow], Tuple[float, float]]:
    beat_s = 60.0 / tempo_bpm
    pitches = [40, 43, 45, 50]  # E2, G2, A2, D3
    notes: List[dict] = []
    t = 0.0
    for _ in range(4):
        for p in pitches:
            notes.append(_note(p, t, t + beat_s * 0.95))
            t += beat_s
    return notes, [], (0.0, t)


def _lead_phrase_sparse(
    tempo_bpm: float = 120.0,
) -> Tuple[List[dict], List[_ChordRow], Tuple[float, float]]:
    beat_s = 60.0 / tempo_bpm
    bar_s = beat_s * 4
    pitches_and_times = [
        (76, 0.5 * beat_s),
        (67, 2.5 * beat_s),
        (79, 5.0 * beat_s),
        (71, 8.0 * beat_s),
        (81, 10.5 * beat_s),
        (72, 14.0 * beat_s),
    ]
    notes = [_note(p, t, t + beat_s * 0.5) for p, t in pitches_and_times]
    return notes, [], (0.0, 4 * bar_s)


def _silent_section(
    duration_s: float = 4.0,
) -> Tuple[List[dict], List[_ChordRow], Tuple[float, float]]:
    return [], [], (0.0, duration_s)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _features(
    *, stem_name, notes, regions, window, beats=None
) -> SectionFeatures:
    s, e = window
    return compute_section_features(
        stem_name=stem_name,
        stem_midi=notes,
        chord_regions=regions,
        section_start_s=s,
        section_end_s=e,
        beats_s=np.asarray(beats) if beats is not None else None,
    )


# ---------------------------------------------------------------------------
# Chord-block fixture
# ---------------------------------------------------------------------------

def test_chord_block_is_polyphonic_low_mono_low_rep() -> None:
    notes, regions, window = _chord_block_4bar()
    sf = _features(stem_name="other", notes=notes, regions=regions, window=window)
    assert sf.polyphony_score >= 0.4
    assert sf.monophonic_ratio < 0.05
    assert sf.chord_count_in_section == 4
    assert sf.chord_density_per_s == 0.5
    assert sf.repetition_score < 0.4
    assert sf.voiced_frame_ratio > 0.95


# ---------------------------------------------------------------------------
# Riff fixture
# ---------------------------------------------------------------------------

def test_riff_is_monophonic_high_rep() -> None:
    notes, regions, window = _riff_e2g2a2d3_4x()
    sf = _features(stem_name="bass", notes=notes, regions=regions, window=window)
    assert sf.monophonic_ratio >= 0.8
    assert sf.repetition_score >= 0.6
    assert sf.chord_density_per_s == 0.0
    assert sf.note_count == 16


def test_riff_period_resolves_against_beat_grid() -> None:
    notes, regions, window = _riff_e2g2a2d3_4x()
    beats = np.arange(window[0], window[1], 0.5)
    sf = _features(
        stem_name="bass", notes=notes, regions=regions, window=window, beats=beats
    )
    assert sf.repetition_period_beats == 4.0


# ---------------------------------------------------------------------------
# Lead fixture
# ---------------------------------------------------------------------------

def test_lead_is_monophonic_low_rep_high_activity() -> None:
    notes, regions, window = _lead_phrase_sparse()
    sf = _features(stem_name="vocals", notes=notes, regions=regions, window=window)
    assert sf.monophonic_ratio >= 0.85
    assert sf.repetition_score < 0.3
    assert sf.lead_activity_score >= 0.55


# ---------------------------------------------------------------------------
# Silent fixture
# ---------------------------------------------------------------------------

def test_silent_section_voiced_ratio_zero() -> None:
    notes, regions, window = _silent_section()
    sf = _features(stem_name="other", notes=notes, regions=regions, window=window)
    assert sf.voiced_frame_ratio == 0.0
    assert sf.monophonic_ratio == 0.0
    assert sf.polyphony_score == 0.0
    assert sf.lead_activity_score == 0.0
    assert sf.repetition_score == 0.0
    assert sf.note_count == 0


# ---------------------------------------------------------------------------
# Boundary safety
# ---------------------------------------------------------------------------

def test_chord_region_straddling_boundary_counted_by_midpoint() -> None:
    regions = [_ChordRow(3.5, 5.5, "C")]
    sf_left = compute_section_features(
        stem_name="other",
        stem_midi=[],
        chord_regions=regions,
        section_start_s=0.0,
        section_end_s=4.0,
    )
    sf_right = compute_section_features(
        stem_name="other",
        stem_midi=[],
        chord_regions=regions,
        section_start_s=4.0,
        section_end_s=8.0,
    )
    assert sf_left.chord_count_in_section == 0
    assert sf_right.chord_count_in_section == 1


def test_short_section_chord_density_does_not_div_zero() -> None:
    sf = compute_section_features(
        stem_name="other",
        stem_midi=[],
        chord_regions=[],
        section_start_s=0.0,
        section_end_s=0.1,
    )
    assert sf.chord_density_per_s == 0.0
    assert sf.voiced_frame_ratio == 0.0


def test_note_dict_and_object_inputs_equivalent() -> None:
    from types import SimpleNamespace

    dict_notes = [
        {"pitch": 60, "start": 0.0, "end": 1.0, "velocity": 96},
        {"pitch": 64, "start": 1.0, "end": 2.0, "velocity": 96},
    ]
    obj_notes = [
        SimpleNamespace(pitch=60, start=0.0, end=1.0, velocity=96),
        SimpleNamespace(pitch=64, start=1.0, end=2.0, velocity=96),
    ]
    sf_dict = compute_section_features(
        stem_name="x", stem_midi=dict_notes, chord_regions=[],
        section_start_s=0.0, section_end_s=2.0,
    )
    sf_obj = compute_section_features(
        stem_name="x", stem_midi=obj_notes, chord_regions=[],
        section_start_s=0.0, section_end_s=2.0,
    )
    assert sf_dict == sf_obj
