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
    select_landmark_notes,
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


# ---------------------------------------------------------------------------
# select_landmark_notes — pitch-class-diversity-first ranking (engine fix #7)
# ---------------------------------------------------------------------------

def test_landmark_notes_empty_returns_empty_tuple() -> None:
    assert select_landmark_notes(
        stem_midi=None, section_start_s=0.0, section_end_s=4.0
    ) == ()
    assert select_landmark_notes(
        stem_midi=[], section_start_s=0.0, section_end_s=4.0
    ) == ()


def test_landmark_notes_excludes_notes_outside_section() -> None:
    notes = [
        _note(60, -1.0, -0.5),   # entirely before
        _note(62, 5.0, 6.0),     # entirely after
        _note(64, 1.0, 2.0),     # inside
    ]
    out = select_landmark_notes(
        stem_midi=notes, section_start_s=0.0, section_end_s=4.0
    )
    assert len(out) == 1
    assert out[0]["pitch"] == 64


def test_landmark_notes_clips_duration_to_section() -> None:
    # A pad bleeding in from before should be ranked by its clipped
    # (in-window) duration, not its full duration.
    notes = [
        _note(60, -10.0, 0.5),   # full=10.5s, clipped=0.5s
        _note(64, 1.0, 3.0),     # fully in-window, dur=2.0s
    ]
    out = select_landmark_notes(
        stem_midi=notes, section_start_s=0.0, section_end_s=4.0, max_notes=2
    )
    pitches = [n["pitch"] for n in out]
    # Both survive (budget=2), playback order by start.
    assert pitches == [60, 64]
    # Clipped end should be section_start (0.0) + 0.5 for the bleeding pad.
    pad = next(n for n in out if n["pitch"] == 60)
    assert pad["start"] == 0.0
    assert abs(pad["end"] - 0.5) < 1e-9


def test_landmark_notes_diversity_preserves_short_pulse_roots() -> None:
    """The SLTS regression: long held F/Ab roots interleaved with
    short-pulse Bb/Db roots. Pre-fix ranking by raw duration evicts
    the short pulses; diversity-first ranking must keep all four
    distinct pitch classes at any reasonable budget.

    Pitches: F2=41, Bb2=46, Ab2=44, Db2=37.
    Held notes have duration 0.5s; pulses have duration 0.25s.
    """
    # 6 held F + 5 held Ab dominate by duration; 1 Bb + 1 Db are
    # short-pulse and would be evicted under pure-duration ranking
    # with max_notes=8 (8 < 6+5+1+1 = 13; the two short pulses
    # would be ranked 12th and 13th by raw duration).
    notes: List[dict] = []
    t = 0.0
    # Six F2 held
    for _ in range(6):
        notes.append(_note(41, t, t + 0.5))
        t += 0.6
    # Five Ab2 held
    for _ in range(5):
        notes.append(_note(44, t, t + 0.5))
        t += 0.6
    # One Bb2 short-pulse
    notes.append(_note(46, t, t + 0.25))
    t += 0.3
    # One Db2 short-pulse
    notes.append(_note(37, t, t + 0.25))
    t += 0.3

    out = select_landmark_notes(
        stem_midi=notes,
        section_start_s=0.0,
        section_end_s=20.0,
        max_notes=8,
    )
    pcs = {n["pitch"] % 12 for n in out}
    # F=5, Bb=10, Ab=8, Db=1 (mod 12). All four chord roots must survive.
    assert 5 in pcs, f"missing F pitch class; got {sorted(pcs)}"
    assert 10 in pcs, f"missing Bb pitch class; got {sorted(pcs)}"
    assert 8 in pcs, f"missing Ab pitch class; got {sorted(pcs)}"
    assert 1 in pcs, f"missing Db pitch class; got {sorted(pcs)}"
    assert len(out) <= 8


def test_landmark_notes_budget_smaller_than_pc_count_keeps_longest_pcs() -> None:
    # Three distinct pitch classes but budget=2: should keep the two
    # diversity reps with the largest duration.
    notes = [
        _note(60, 0.0, 0.1),   # short C
        _note(62, 1.0, 2.0),   # long D
        _note(64, 3.0, 3.8),   # medium E
    ]
    out = select_landmark_notes(
        stem_midi=notes, section_start_s=0.0, section_end_s=4.0, max_notes=2
    )
    pitches = sorted(n["pitch"] for n in out)
    assert pitches == [62, 64], (
        f"expected D+E (longest 2 distinct pcs); got {pitches}"
    )


def test_landmark_notes_fills_remaining_budget_after_diversity_pass() -> None:
    # Two pitch classes, budget=4: diversity pass picks 2; pass 2
    # fills the remaining 2 with the next-longest duplicate-pc notes.
    notes = [
        _note(60, 0.0, 1.0),   # C long
        _note(60, 2.0, 2.9),   # C medium
        _note(60, 4.0, 4.4),   # C short
        _note(64, 5.0, 5.6),   # E mid-short
    ]
    out = select_landmark_notes(
        stem_midi=notes, section_start_s=0.0, section_end_s=6.0, max_notes=4
    )
    assert len(out) == 4
    pitches = [n["pitch"] for n in out]
    # Playback order; should include all four candidates.
    assert pitches == [60, 60, 60, 64]


def test_landmark_notes_output_sorted_by_start_time() -> None:
    notes = [
        _note(64, 3.0, 3.5),
        _note(60, 0.0, 0.5),
        _note(62, 1.5, 2.0),
    ]
    out = select_landmark_notes(
        stem_midi=notes, section_start_s=0.0, section_end_s=4.0
    )
    starts = [n["start"] for n in out]
    assert starts == sorted(starts)


def test_landmark_notes_max_notes_zero_returns_empty() -> None:
    notes = [_note(60, 0.0, 1.0)]
    assert select_landmark_notes(
        stem_midi=notes,
        section_start_s=0.0,
        section_end_s=4.0,
        max_notes=0,
    ) == ()


def test_landmark_notes_velocity_default_when_missing() -> None:
    # Notes without explicit velocity should fall back to 80.
    notes = [{"pitch": 60, "start": 0.0, "end": 1.0}]
    out = select_landmark_notes(
        stem_midi=notes, section_start_s=0.0, section_end_s=2.0
    )
    assert len(out) == 1
    assert out[0]["velocity"] == 80


# ---------------------------------------------------------------------------
# debug_features serialization round-trip (engine-fix-debug-#1)
# ---------------------------------------------------------------------------
#
# The pipeline persists per-stem SectionFeatures into the section dict
# via ``asdict`` (one dict per stem). The /debug visualizer renders
# those dicts directly. This test pins that the asdict shape round-trips
# losslessly through json, so the persistence chain (history.json →
# bundle → API → frontend) doesn't silently drop a field.


def test_section_features_asdict_json_round_trip() -> None:
    import json
    from dataclasses import asdict

    notes, regions, window = _chord_block_4bar()
    sf = _features(stem_name="guitar_left", notes=notes, regions=regions, window=window)

    as_dict = asdict(sf)
    # Every contract field appears in the dict so the radar/table can
    # render without missing axes.
    expected_keys = {
        "stem_name",
        "chord_density_per_s",
        "chord_count_in_section",
        "monophonic_ratio",
        "repetition_score",
        "repetition_period_beats",
        "polyphony_score",
        "lead_activity_score",
        "voiced_frame_ratio",
        "note_count",
        "duration_s",
        "pitch_class_diversity",
    }
    assert set(as_dict.keys()) == expected_keys

    # JSON round-trip — the path the bundle takes on persistence.
    encoded = json.dumps(as_dict)
    decoded = json.loads(encoded)
    assert decoded["stem_name"] == sf.stem_name
    assert decoded["chord_density_per_s"] == sf.chord_density_per_s
    assert decoded["monophonic_ratio"] == sf.monophonic_ratio
    assert decoded["pitch_class_diversity"] == sf.pitch_class_diversity
