"""Boundary-friendly chord detection for the analysis subsystem.

This is the public entry point that other subsystems (specifically
``session`` and ``guidance``) consume via composition. It wraps the
internal librosa-based ``chord_detector`` and emits the platform
``contracts.Chord`` shape so callers never see the internal dataclass.

Spike results (see ``backend/scripts/chord_spike_report.json``): the
underlying detector averages ~94.7% on root + triad metrics across
five synthetic guitar-style progressions. The known weak case is
dom7 fusion (G7 collapses into an adjacent C in I-IV-V7-I). Good
enough for the Jam chord lane; not a research project.
"""
from __future__ import annotations

from typing import Tuple

import numpy as np

from tone_forge.contracts import Chord
from tone_forge.analysis import chord_detector as _internal

__all__ = ["detect_chords"]


def detect_chords(
    audio: np.ndarray,
    sr: int,
    *,
    min_chord_duration_s: float = 0.5,
) -> Tuple[Chord, ...]:
    """Detect chords in ``audio`` and return ``contracts.Chord`` records.

    Args:
        audio: Mono audio samples (any range; librosa-compatible).
        sr: Sample rate in Hz.
        min_chord_duration_s: Drop chord regions shorter than this. The
            spike used 0.3s; default here is 0.5s to favor stable
            regions in the Jam chord lane.

    Returns:
        A tuple of ``contracts.Chord`` ordered by ``start_s``.
    """
    raw = _internal.detect_chords_from_audio(
        audio, sr, min_chord_duration=min_chord_duration_s
    )
    return tuple(
        Chord(
            start_s=float(c.start_time),
            end_s=float(c.end_time),
            symbol=c.name,
            confidence=float(c.confidence),
        )
        for c in raw
    )
