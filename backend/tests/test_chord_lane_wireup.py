"""Locks the P4a chord-lane wire-up across the pipeline boundary.

These tests guard three independent contracts that together make the
chord lane visible to the Jam UI:

1. ``tone_forge.analysis.detect_chords`` is the importable entry point
   and emits ``contracts.Chord`` records.
2. ``AnalysisResult`` carries a ``chords`` field that round-trips
   through ``to_dict()`` into the persisted shape that
   ``session.bundle._iter_chords`` recognizes.
3. The pipeline orchestrator's ``_detect_chord_lane`` helper exists,
   is awaitable, and yields list-of-dict records with the four keys
   the bundle assembler reads (``start_s`` / ``end_s`` / ``symbol`` /
   ``confidence``).

The tests deliberately avoid spinning up the full ``UnifiedPipeline``
run — that path requires librosa, stem separation, and IO. We test the
seams, not the whole pipeline. The full-pipeline path is covered by
existing API integration tests.
"""

from __future__ import annotations

import asyncio
import inspect
from pathlib import Path

import numpy as np
import pytest

from tone_forge import analysis as analysis_pkg
from tone_forge.analysis import detect_chords
from tone_forge.contracts import Chord
from tone_forge.session.bundle import build as build_bundle
from tone_forge.unified_pipeline import (
    AnalysisResult,
    AudioData,
    DetectionResult,
    UnifiedPipeline,
)


# ---------------------------------------------------------------------------
# 1. analysis.detect_chords surface
# ---------------------------------------------------------------------------

def test_detect_chords_is_publicly_exported():
    """The chord lane entry point ships from the analysis package root."""
    assert hasattr(analysis_pkg, "detect_chords")
    assert "detect_chords" in analysis_pkg.__all__


def test_detect_chords_returns_contracts_chord_records():
    """A synthetic C major triad must be detected as a C-rooted chord."""
    sr = 22050
    duration = 2.0
    t = np.linspace(0, duration, int(sr * duration), endpoint=False)
    triad = (
        np.sin(2 * np.pi * 261.63 * t)  # C4
        + np.sin(2 * np.pi * 329.63 * t)  # E4
        + np.sin(2 * np.pi * 392.00 * t)  # G4
    ) / 3.0

    chords = detect_chords(triad, sr, min_chord_duration_s=0.3)

    assert len(chords) >= 1, "Should detect at least one chord region"
    for c in chords:
        # Type lock: must be the contract type, not the internal dataclass.
        assert isinstance(c, Chord)
        assert 0.0 <= c.confidence <= 1.0
        assert c.end_s > c.start_s
        assert isinstance(c.symbol, str) and c.symbol

    # Loose sanity: at least one detected segment should be rooted at C.
    assert any(c.symbol.startswith("C") for c in chords)


# ---------------------------------------------------------------------------
# 2. AnalysisResult <-> persisted dict
# ---------------------------------------------------------------------------

def _make_minimal_result(chords_payload=None) -> AnalysisResult:
    """Build a hand-rolled AnalysisResult so we test the shape, not the
    whole pipeline. Only the fields touched by chord-lane wire-up matter.
    """
    detection = DetectionResult(
        is_full_mix=True,
        is_guitar=False,
        is_bass=False,
        is_drums=False,
        is_synth=False,
        is_vocals=False,
        detected_type="full_mix",
        summary="full mix",
        confidence={"full_mix": 0.9},
    )
    return AnalysisResult(
        source_name="fixture",
        source_url=None,
        duration_sec=1.0,
        sample_rate=22050,
        detection=detection,
        detected_type="full_mix",
        chords=chords_payload,
    )


def test_analysis_result_chords_round_trip_through_to_dict():
    """``chords`` survives ``AnalysisResult.to_dict()`` in the persisted shape."""
    payload = [
        {"start_s": 0.0, "end_s": 1.5, "symbol": "C", "confidence": 0.83},
        {"start_s": 1.5, "end_s": 3.0, "symbol": "G", "confidence": 0.71},
    ]
    result = _make_minimal_result(chords_payload=payload)

    out = result.to_dict()

    assert out["chords"] == payload


def test_analysis_result_omits_chords_when_unset():
    """No ``chords`` key when the field is None — keeps history JSON small
    for sources where chord detection was skipped (e.g. drum stems)."""
    result = _make_minimal_result(chords_payload=None)
    out = result.to_dict()
    assert "chords" not in out


# ---------------------------------------------------------------------------
# 3. Persisted dict -> SessionBundle.understanding.chords
# ---------------------------------------------------------------------------

def test_session_bundle_consumes_persisted_chords():
    """A persisted history result feeds straight into the bundle."""
    persisted = {
        "detected_type": "guitar",
        "duration_sec": 30.0,
        "sample_rate": 22050,
        "chords": [
            {"start_s": 0.0, "end_s": 4.0, "symbol": "Am", "confidence": 0.9},
            {"start_s": 4.0, "end_s": 8.0, "symbol": "F", "confidence": 0.8},
        ],
    }

    bundle = build_bundle(persisted, session_id="abc123")

    assert len(bundle.understanding.chords) == 2
    first, second = bundle.understanding.chords
    assert first.symbol == "Am"
    assert first.start_s == 0.0
    assert second.symbol == "F"
    assert second.confidence == pytest.approx(0.8)

    # GuidanceTrack reads from the same source for the chord lane.
    assert len(bundle.guidance.chord_lane) == 2
    assert bundle.guidance.chord_lane[0].symbol == "Am"


# ---------------------------------------------------------------------------
# 4. UnifiedPipeline._detect_chord_lane
# ---------------------------------------------------------------------------

def test_detect_chord_lane_is_async_method():
    """Pipeline orchestrator owns the chord stage."""
    method = getattr(UnifiedPipeline, "_detect_chord_lane", None)
    assert method is not None
    assert inspect.iscoroutinefunction(method)


def test_detect_chord_lane_emits_persisted_dict_shape(tmp_path: Path):
    """End-to-end on a synthetic clip: orchestrator -> dicts ready for to_dict()."""
    sr = 22050
    duration = 2.0
    t = np.linspace(0, duration, int(sr * duration), endpoint=False)
    triad = (
        np.sin(2 * np.pi * 261.63 * t)
        + np.sin(2 * np.pi * 329.63 * t)
        + np.sin(2 * np.pi * 392.00 * t)
    ).astype(np.float32) / 3.0

    audio_data = AudioData(
        audio=triad,
        sr=sr,
        duration=duration,
        path=tmp_path / "synthetic.wav",
        source_type="file",
        source_name="synthetic",
    )

    pipeline = UnifiedPipeline()
    chord_lane = asyncio.run(pipeline._detect_chord_lane(audio_data))

    # Phase 6 hybrid grid: orchestrator emits a dict with the
    # fixed-window array under "fixed" and the optional beat-snapped
    # variant under "snapped" (None when no beats were detected).
    # Bug-C hoist (Phase 7+): the post-tie-break key decision the
    # chord_detector reaches internally is surfaced under "key" so
    # AnalysisResult can persist detected_key at the top level.
    assert isinstance(chord_lane, dict)
    assert set(chord_lane.keys()) == {"fixed", "snapped", "key"}

    fixed = chord_lane["fixed"]
    assert isinstance(fixed, list)
    assert len(fixed) >= 1
    for record in fixed:
        assert set(record.keys()) == {"start_s", "end_s", "symbol", "confidence"}
        assert isinstance(record["symbol"], str)
        assert isinstance(record["confidence"], float)
        assert record["end_s"] > record["start_s"]

    snapped = chord_lane["snapped"]
    # Synthetic clip without beat detection upstream -> snapped is None
    # or a list of the same persisted-dict shape.
    assert snapped is None or isinstance(snapped, list)
    if isinstance(snapped, list):
        for record in snapped:
            assert set(record.keys()) == {"start_s", "end_s", "symbol", "confidence"}
            assert record["end_s"] > record["start_s"]

    # Key dict: populated for a tonal triad; degenerate-input case
    # (silent audio) leaves it empty per chord_detector's contract.
    key_dict = chord_lane["key"]
    assert isinstance(key_dict, dict)
    if key_dict:
        assert set(key_dict.keys()) >= {"root", "mode", "strength", "label"}
        assert key_dict["mode"] in {"major", "minor"}
        assert 0 <= int(key_dict["root"]) <= 11
