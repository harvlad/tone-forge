"""M1.0 invariant: DetectorConfig() default reproduces pre-M1 behaviour.

The first invariant of the M1 self-improving-platform plan is:

    detect_chords_from_audio(y, sr)
    must produce byte-identical output to
    detect_chords_from_audio(y, sr, config=DetectorConfig())

In other words, the optional ``config`` parameter introduced in
M1.0 is an additive lever; omitting it (or passing the
default-constructed ``DetectorConfig()``) must keep the detector
on its pre-M1 code path bit-for-bit.

This test exercises both code paths across:

 * Every synthetic chord progression in
   ``test_chord_eval_regression.SYNTHETIC_SUITE`` (7 fixtures).
 * Every real-audio fixture under
   ``backend/tests/fixtures/chord_groundtruth/`` whose source audio
   is present on disk (skipped cleanly otherwise; 4 on a CI machine
   with the corpus populated).

For each fixture we run the internal
``chord_detector.detect_chords_from_audio`` twice — once with no
``config`` keyword, once with ``config=DetectorConfig()`` — and
assert the two returned ``Chord`` lists are element-wise equal in
``(start_time, end_time, name, confidence)``. ``confidence`` is
compared with ``math.isclose`` (rel_tol=1e-12) because cosine
multiplication is associative to within floating-point noise even
under identical inputs; everything else is compared with ``==``.

If this test fails the M1.0 plumbing has accidentally introduced
a behaviour change in the no-config path. Fix by restoring the
default-equals-prior-constants invariant in
``DetectorConfig``/``chord_detector.py``.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
import pytest

from tone_forge.analysis import chord_detector
from tone_forge.analysis.detector_config import DetectorConfig

# Import the synthetic-fixture helpers and suite directly from the
# existing regression suite so we don't duplicate the test corpus.
# ``tests/`` is not a package (no ``__init__.py``); pytest discovers
# each test file as a top-level module via rootdir mode.
from test_chord_eval_regression import (  # type: ignore
    SYNTHETIC_SUITE,
    _evenly_spaced_beats,
    _synth_bass_track,
    _synth_progression,
)


# ---------------------------------------------------------------------------
# Equivalence assertion
# ---------------------------------------------------------------------------


def _assert_chord_lists_equivalent(
    actual_none: list,
    actual_cfg: list,
    *,
    context: str,
) -> None:
    assert len(actual_none) == len(actual_cfg), (
        f"{context}: chord-list lengths differ — "
        f"no-config={len(actual_none)} vs DetectorConfig()={len(actual_cfg)}"
    )
    for i, (a, b) in enumerate(zip(actual_none, actual_cfg)):
        assert a.name == b.name, (
            f"{context}: region {i} name mismatch "
            f"{a.name!r} vs {b.name!r}"
        )
        assert a.start_time == b.start_time, (
            f"{context}: region {i} start_time {a.start_time} vs {b.start_time}"
        )
        assert a.end_time == b.end_time, (
            f"{context}: region {i} end_time {a.end_time} vs {b.end_time}"
        )
        assert math.isclose(
            float(a.confidence), float(b.confidence),
            rel_tol=1e-12, abs_tol=1e-12,
        ), (
            f"{context}: region {i} confidence "
            f"{a.confidence} vs {b.confidence}"
        )


# ---------------------------------------------------------------------------
# Synthetic fixtures
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,regions,bpm,_triad_floor,_root_floor",
    SYNTHETIC_SUITE,
    ids=[s[0] for s in SYNTHETIC_SUITE],
)
def test_default_config_matches_no_config_on_synthetic(
    name: str,
    regions: List[Tuple[float, float, str]],
    bpm: float,
    _triad_floor: float,
    _root_floor: float,
) -> None:
    sr = 22050
    duration_s = regions[-1][1]
    y = _synth_progression(regions, sr=sr)
    bass = _synth_bass_track(regions, sr=sr)
    beats = _evenly_spaced_beats(duration_s, bpm=bpm)

    pred_none = chord_detector.detect_chords_from_audio(
        y, sr, bass_y=bass, beats_s=beats,
    )
    pred_cfg = chord_detector.detect_chords_from_audio(
        y, sr, bass_y=bass, beats_s=beats, config=DetectorConfig(),
    )
    _assert_chord_lists_equivalent(
        pred_none, pred_cfg, context=f"synthetic[{name}]",
    )


# ---------------------------------------------------------------------------
# Real-audio fixtures
# ---------------------------------------------------------------------------


_FIXTURE_DIR = (
    Path(__file__).resolve().parent / "fixtures" / "chord_groundtruth"
)
_BACKEND_DIR = Path(__file__).resolve().parent.parent


def _discover_fixtures() -> List[Path]:
    if not _FIXTURE_DIR.exists():
        return []
    return sorted(_FIXTURE_DIR.glob("*.json"))


def _resolve_audio(fixture: dict, key: str) -> Optional[Path]:
    raw = fixture.get(key)
    if not raw:
        return None
    p = Path(raw)
    if not p.is_absolute():
        p = (_BACKEND_DIR / p).resolve()
    return p if p.exists() else None


@pytest.mark.parametrize(
    "fixture_path",
    _discover_fixtures(),
    ids=lambda p: p.stem,
)
def test_default_config_matches_no_config_on_real_audio(
    fixture_path: Path,
) -> None:
    with open(fixture_path, "r") as f:
        fixture = json.load(f)

    audio_path = (
        _resolve_audio(fixture, "source_audio_other_stem")
        or _resolve_audio(fixture, "source_audio")
    )
    if audio_path is None:
        pytest.skip(
            f"source audio for {fixture_path.stem} not on disk"
        )
    bass_path = _resolve_audio(fixture, "source_audio_bass_stem")

    import librosa  # local import (synthetic suite doesn't need it)
    y, sr = librosa.load(str(audio_path), sr=22050, mono=True)
    bass_y = None
    if bass_path is not None:
        bass_y, _ = librosa.load(str(bass_path), sr=sr, mono=True)

    beats_s = None
    try:
        tempo_raw, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
        tempo_val = float(np.asarray(tempo_raw).item())
        if 40 <= tempo_val <= 240 and len(beat_frames) >= 2:
            beats_s = librosa.frames_to_time(beat_frames, sr=sr)
    except Exception:
        beats_s = None

    pred_none = chord_detector.detect_chords_from_audio(
        y, sr, bass_y=bass_y, beats_s=beats_s,
    )
    pred_cfg = chord_detector.detect_chords_from_audio(
        y, sr, bass_y=bass_y, beats_s=beats_s, config=DetectorConfig(),
    )
    _assert_chord_lists_equivalent(
        pred_none, pred_cfg, context=f"real-audio[{fixture_path.stem}]",
    )


# ---------------------------------------------------------------------------
# Sanity: config fields match the prior hardcoded constants exactly
# ---------------------------------------------------------------------------


def test_default_config_field_values_match_prior_constants() -> None:
    """Pin the literal default field values.

    These numbers are the exact constants that previously lived
    inline in ``chord_detector.detect_chords_from_audio`` and
    ``chord_detector._build_transition_matrix`` pre-M1. Changing
    them in ``DetectorConfig`` would silently alter the no-config
    path's behaviour, so we pin them here at unit-test scope to
    catch the mutation source-of-truth even if every fixture in the
    corpus happened to remain green.
    """
    cfg = DetectorConfig()
    assert cfg.cos_cutoff == 0.70
    assert cfg.diatonic_bias == 0.10
    assert cfg.bass_root_bias == 0.05
    assert cfg.self_loop_bonus == 0.01
    assert cfg.same_root_quality_bonus == 0.01
    assert cfg.no_chord_penalty == -0.10
    assert cfg.quality_switch_penalty == 0.0
    assert cfg.hcdf_snap_radius_frames == 0
