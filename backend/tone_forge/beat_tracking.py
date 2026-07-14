"""Shared beat / downbeat / tempo tracking (task 10).

One entry point — ``track_beats(y, sr)`` — used by both production
analysis paths (``tone_forge.unified_pipeline._track_beats`` and
``local_engine.analysis_worker`` step 4b) so beat quality is identical
everywhere and measured in one place.

Primary tracker: Beat This! (CPJKU, MIT license) — a transformer
beat/downbeat tracker. Measured on BabySlakh 10-track mixes against
pretty_midi ground-truth beats (mir_eval.beat.f_measure, 70ms window,
first 60s):

    beats      librosa 0.815   beat_this 0.895
    downbeats  librosa 0.512   beat_this 0.872

The librosa downbeat number is the old ``beats[::4]`` derivation —
phase-blind, scoring 0.000 on 3/10 tracks. Beat This! predicts real
downbeats, which is the main win.

Fallback: ``librosa.beat.beat_track`` (+ ``[::4]`` downbeat guess)
whenever beat_this is not importable, its checkpoint cannot load, or
it returns fewer than 2 beats. Failure of both degrades silently to
``tempo_bpm=0.0`` and empty lists — same observable contract the
pipeline invariant tests lock down.

The beat_this model (~78 MB checkpoint, auto-downloaded on first use)
is cached per process. Device: MPS > CUDA > CPU.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)

__all__ = ["track_beats"]

# 40-240 BPM sanity window (legacy contract): out-of-range tempo
# estimates are almost always phantom-pulse artefacts.
_MIN_BPM = 40.0
_MAX_BPM = 240.0

_BEAT_THIS_MODEL: Optional[Any] = None
_BEAT_THIS_FAILED = False


def _get_beat_this() -> Optional[Any]:
    """Load Beat This! once per process; None if unavailable."""
    global _BEAT_THIS_MODEL, _BEAT_THIS_FAILED
    if _BEAT_THIS_MODEL is not None or _BEAT_THIS_FAILED:
        return _BEAT_THIS_MODEL
    try:
        import torch
        from beat_this.inference import Audio2Beats

        if torch.backends.mps.is_available():
            device = "mps"
        elif torch.cuda.is_available():
            device = "cuda"
        else:
            device = "cpu"
        _BEAT_THIS_MODEL = Audio2Beats(
            checkpoint_path="final0", device=device, dbn=False
        )
        logger.info(f"beat_this loaded on {device}")
    except Exception as e:
        _BEAT_THIS_FAILED = True
        logger.warning(f"beat_this unavailable ({e}); librosa fallback")
    return _BEAT_THIS_MODEL


def _tempo_from_beats(beats: np.ndarray) -> float:
    """Median inter-beat interval -> BPM, 0.0 if outside sanity window."""
    if len(beats) < 2:
        return 0.0
    median_interval = float(np.median(np.diff(beats)))
    if median_interval <= 0:
        return 0.0
    bpm = 60.0 / median_interval
    return bpm if _MIN_BPM <= bpm <= _MAX_BPM else 0.0


def track_beats(y: np.ndarray, sr: int) -> Dict[str, Any]:
    """Track beats/downbeats/tempo on a mono audio buffer.

    Returns ``{"tempo_bpm": float, "beats_s": [..], "downbeats_s": [..],
    "method": str}``. ``tempo_bpm == 0.0`` with empty lists signals
    "no tempo detected" — degraded, never raised.
    """
    y = np.asarray(y, dtype=np.float32)
    if y.ndim > 1:
        y = y.mean(axis=-1)

    # --- Beat This! (primary) ---------------------------------------
    model = _get_beat_this()
    if model is not None:
        try:
            beats, downbeats = model(y, sr)
            beats = np.asarray(beats, dtype=float)
            downbeats = np.asarray(downbeats, dtype=float)
            tempo = _tempo_from_beats(beats)
            if tempo > 0.0:
                return {
                    "tempo_bpm": tempo,
                    "beats_s": beats.tolist(),
                    "downbeats_s": downbeats.tolist(),
                    "method": "beat_this",
                }
            logger.warning(
                f"beat_this returned {len(beats)} beats / tempo outside "
                f"{_MIN_BPM:.0f}-{_MAX_BPM:.0f}; librosa fallback"
            )
        except Exception as e:
            logger.warning(f"beat_this inference failed ({e}); "
                           f"librosa fallback")

    # --- librosa fallback ---------------------------------------------
    try:
        import librosa

        tempo_raw, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
        tempo_val = (
            float(np.asarray(tempo_raw).item())
            if tempo_raw is not None else 0.0
        )
        if (
            _MIN_BPM <= tempo_val <= _MAX_BPM
            and beat_frames is not None
            and len(beat_frames) >= 2
        ):
            beats_s: List[float] = librosa.frames_to_time(
                beat_frames, sr=sr
            ).tolist()
            return {
                "tempo_bpm": tempo_val,
                "beats_s": beats_s,
                # Phase-blind 4/4 guess — beat_this replaces this with
                # measured downbeats whenever it is available.
                "downbeats_s": beats_s[::4],
                "method": "librosa",
            }
    except Exception as e:
        logger.warning(f"librosa beat tracking failed: {e}")

    return {
        "tempo_bpm": 0.0,
        "beats_s": [],
        "downbeats_s": [],
        "method": "none",
    }
