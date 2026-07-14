"""C1 — per-stem chord lane detection (pipeline wire-up).

Locks the additive per-stem chord-lane contract introduced by the
JAM chord-lane-selector milestone. The legacy ``chords`` field stays
populated with the "other"-stem lane (backwards compat); the new
``chords_by_stem`` / ``chords_beat_snapped_by_stem`` dicts carry one
entry per stem so the JAM UI can let the user pick which lane the
ribbon follows.

Tests are hermetic — they patch ``detect_chords`` /
``detect_chords_with_key`` / ``snap_chord_boundaries_to_beats`` and
``librosa.load`` so no real audio decoding happens. They exercise:

1. ``UnifiedPipeline._detect_chord_lane`` calls the detector once
   per available stem and returns a ``fixed_by_stem`` dict.
2. The legacy ``fixed`` array equals ``fixed_by_stem["other"]``.
3. Missing stems (e.g. no vocals) don't appear in
   ``fixed_by_stem``.
4. The new dict survives ``AnalysisResult.to_dict()`` round-trip.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from tone_forge.contracts import Chord
from tone_forge.unified_pipeline import (
    AnalysisResult,
    AudioData,
    DetectionResult,
    UnifiedPipeline,
)


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------

def _fake_chord_records(symbol: str) -> list:
    """Two-region chord progression, one symbol throughout — enough
    detail to round-trip through the per-stem dict shape without
    invoking the real librosa-chroma+Viterbi detector.
    """
    return [
        Chord(start_s=0.0, end_s=1.0, symbol=symbol, confidence=0.85),
        Chord(start_s=1.0, end_s=2.0, symbol=symbol, confidence=0.80),
    ]


def _make_audio_data() -> AudioData:
    sr = 22050
    duration = 2.0
    audio = np.zeros(int(sr * duration), dtype=np.float32)
    return AudioData(
        audio=audio,
        sr=sr,
        duration=duration,
        path=Path("/tmp/fake.wav"),
        source_type="file",
        source_name="fake",
    )


def _make_stems_dict(tmp_path: Path, names: list) -> dict:
    """Build a stems dict pointing at non-existent paths — librosa.load
    is patched, so the paths never actually get opened.
    """
    out = {}
    for name in names:
        p = tmp_path / f"{name}.wav"
        p.touch()  # exists so Path.exists() checks pass
        out[name] = p
    return out


# ---------------------------------------------------------------------------
# 1. detect_chords called once per stem
# ---------------------------------------------------------------------------

def test_per_stem_chord_detection_runs_for_each_input_stem(tmp_path: Path):
    """Only harmonic stems get a chord lane: "other" goes through
    ``detect_chords_with_key`` (reference lane, surfaces detected_key)
    and "bass" through ``detect_chords``. Vocals (monophonic melody)
    and drums (unpitched) are deliberately excluded — chord ribbons
    fitted to them traced the tune / hallucinated from noise.
    """
    audio_data = _make_audio_data()
    stems = _make_stems_dict(tmp_path, ["other", "bass", "vocals", "drums"])

    sr = 22050
    fake_audio = np.zeros(sr * 2, dtype=np.float32)

    detect_chords_calls = []
    detect_chords_with_key_calls = []

    def fake_detect_chords(y, sr_in, **kwargs):
        detect_chords_calls.append(("plain", kwargs))
        return _fake_chord_records("C")

    def fake_detect_chords_with_key(y, sr_in, **kwargs):
        detect_chords_with_key_calls.append(("with_key", kwargs))
        return _fake_chord_records("C"), {
            "root": 0, "mode": "major",
            "strength": 0.8, "label": "C major",
        }

    def fake_load(path, sr=None, mono=True):
        return fake_audio, sr or 22050

    with patch(
        "tone_forge.analysis.detect_chords",
        side_effect=fake_detect_chords,
    ), patch(
        "tone_forge.analysis.chords.detect_chords_with_key",
        side_effect=fake_detect_chords_with_key,
    ), patch(
        "tone_forge.analysis.chords.snap_chord_boundaries_to_beats",
        side_effect=lambda recs, beats, dur: list(recs),
    ), patch("librosa.load", side_effect=fake_load):
        pipeline = UnifiedPipeline()
        chord_lane = asyncio.run(pipeline._detect_chord_lane(
            audio_data, stems=stems, beats_s=None,
        ))

    # "other" lane runs through detect_chords_with_key (one call).
    assert len(detect_chords_with_key_calls) == 1

    # Only "bass" runs through detect_chords; vocals and drums are
    # excluded from chord lanes entirely.
    assert len(detect_chords_calls) == 1

    # fixed_by_stem carries the two harmonic stems only.
    assert set(chord_lane["fixed_by_stem"].keys()) == {"other", "bass"}


# ---------------------------------------------------------------------------
# 2. Legacy lane equals "other" entry — backwards compatibility
# ---------------------------------------------------------------------------

def test_per_stem_chord_detection_preserves_legacy_other_lane(tmp_path: Path):
    """The legacy single ``fixed`` array must equal
    ``fixed_by_stem["other"]`` so callers that haven't been updated
    for the per-stem dict see identical behaviour.
    """
    audio_data = _make_audio_data()
    stems = _make_stems_dict(tmp_path, ["other", "bass"])

    fake_audio = np.zeros(22050 * 2, dtype=np.float32)

    def fake_detect_chords(y, sr_in, **kwargs):
        return _fake_chord_records("G")

    def fake_detect_chords_with_key(y, sr_in, **kwargs):
        return _fake_chord_records("G"), {}

    def fake_load(path, sr=None, mono=True):
        return fake_audio, sr or 22050

    with patch(
        "tone_forge.analysis.detect_chords",
        side_effect=fake_detect_chords,
    ), patch(
        "tone_forge.analysis.chords.detect_chords_with_key",
        side_effect=fake_detect_chords_with_key,
    ), patch("librosa.load", side_effect=fake_load):
        pipeline = UnifiedPipeline()
        chord_lane = asyncio.run(pipeline._detect_chord_lane(
            audio_data, stems=stems, beats_s=None,
        ))

    assert chord_lane["fixed"] == chord_lane["fixed_by_stem"]["other"]


# ---------------------------------------------------------------------------
# 3. Missing stems don't appear in chords_by_stem
# ---------------------------------------------------------------------------

def test_per_stem_chord_detection_skips_missing_stems(tmp_path: Path):
    """An absent stem (no entry in the input dict) must not appear in
    the per-stem dict, and non-harmonic stems (drums) never get a
    lane even when present.
    """
    audio_data = _make_audio_data()
    # Only "other" and "drums" — no bass, no vocals.
    stems = _make_stems_dict(tmp_path, ["other", "drums"])

    fake_audio = np.zeros(22050 * 2, dtype=np.float32)

    def fake_detect_chords(y, sr_in, **kwargs):
        return _fake_chord_records("D")

    def fake_detect_chords_with_key(y, sr_in, **kwargs):
        return _fake_chord_records("D"), {}

    def fake_load(path, sr=None, mono=True):
        return fake_audio, sr or 22050

    with patch(
        "tone_forge.analysis.detect_chords",
        side_effect=fake_detect_chords,
    ), patch(
        "tone_forge.analysis.chords.detect_chords_with_key",
        side_effect=fake_detect_chords_with_key,
    ), patch("librosa.load", side_effect=fake_load):
        pipeline = UnifiedPipeline()
        chord_lane = asyncio.run(pipeline._detect_chord_lane(
            audio_data, stems=stems, beats_s=None,
        ))

    keys = set(chord_lane["fixed_by_stem"].keys())
    assert keys == {"other"}
    assert "bass" not in keys
    assert "vocals" not in keys
    assert "drums" not in keys


# ---------------------------------------------------------------------------
# 4. AnalysisResult round-trip
# ---------------------------------------------------------------------------

def _make_minimal_result(
    chords_by_stem=None,
    chords_beat_snapped_by_stem=None,
) -> AnalysisResult:
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
        chords_by_stem=chords_by_stem,
        chords_beat_snapped_by_stem=chords_beat_snapped_by_stem,
    )


def test_analysis_result_chords_by_stem_round_trip():
    payload = {
        "other": [
            {"start_s": 0.0, "end_s": 1.0, "symbol": "C",
             "confidence": 0.9},
        ],
        "bass": [
            {"start_s": 0.0, "end_s": 1.0, "symbol": "C",
             "confidence": 0.8},
        ],
    }
    snapped = {
        "other": [
            {"start_s": 0.0, "end_s": 1.0, "symbol": "C",
             "confidence": 0.9},
        ],
        "bass": None,
    }
    result = _make_minimal_result(
        chords_by_stem=payload,
        chords_beat_snapped_by_stem=snapped,
    )
    out = result.to_dict()
    assert out["chords_by_stem"] == payload
    assert out["chords_beat_snapped_by_stem"] == snapped


def test_analysis_result_omits_chords_by_stem_when_unset():
    """No ``chords_by_stem`` key when the field is None — keeps history
    JSON small for drum-only / instrumental-only sources where chord
    detection was skipped."""
    result = _make_minimal_result(
        chords_by_stem=None,
        chords_beat_snapped_by_stem=None,
    )
    out = result.to_dict()
    assert "chords_by_stem" not in out
    assert "chords_beat_snapped_by_stem" not in out
