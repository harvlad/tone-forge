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

from typing import Any, Dict, Optional, Tuple

import numpy as np

from tone_forge.contracts import Chord
from tone_forge.analysis import chord_detector as _internal

__all__ = [
    "detect_chords",
    "detect_chords_with_key",
    "snap_chord_boundaries_to_beats",
]


def detect_chords(
    audio: np.ndarray,
    sr: int,
    *,
    min_chord_duration_s: float = 0.5,
    bass_audio: Optional[np.ndarray] = None,
    beats_s: Optional[np.ndarray] = None,
) -> Tuple[Chord, ...]:
    """Detect chords in ``audio`` and return ``contracts.Chord`` records.

    Args:
        audio: Mono audio samples (any range; librosa-compatible).
        sr: Sample rate in Hz.
        min_chord_duration_s: Drop chord regions shorter than this. The
            spike used 0.3s; default here is 0.5s to favor stable
            regions in the Jam chord lane.
        bass_audio: Optional mono bass-stem samples at the same sample
            rate. When supplied, the detector biases its emission
            scores toward chord templates whose root matches the
            per-window bass pitch class extracted via pyin. This is the
            Phase 5 disambiguation pathway for relative-major/minor
            pairs the chroma matcher alone cannot separate.
        beats_s: Optional beat timestamps in seconds (from
            ``librosa.beat.beat_track``). When supplied, the detector
            replaces its fixed-0.5s analysis grid with beat-aligned
            windows so chord-region boundaries land on musical beats
            rather than on an arbitrary clock subdivision (Phase 6).

    Returns:
        A tuple of ``contracts.Chord`` ordered by ``start_s``.
    """
    raw = _internal.detect_chords_from_audio(
        audio, sr,
        min_chord_duration=min_chord_duration_s,
        bass_y=bass_audio,
        beats_s=beats_s,
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


def detect_chords_with_key(
    audio: np.ndarray,
    sr: int,
    *,
    min_chord_duration_s: float = 0.5,
    bass_audio: Optional[np.ndarray] = None,
    beats_s: Optional[np.ndarray] = None,
) -> Tuple[Tuple[Chord, ...], Dict[str, Any]]:
    """Detect chords AND surface the underlying key decision.

    Behaviourally identical to ``detect_chords`` for chord output. The
    second return value is a small dict the chord_detector populates
    in-place describing the post-tie-break key:

        {"root": int (0-11), "mode": "major"|"minor",
         "strength": float (0-1), "label": "F minor"}

    Empty dict on degenerate input (no chroma, all-zero audio): the
    chord_detector then falls back to its silent-defaults path and
    never writes into ``key_out``.

    Wired by ``unified_pipeline._detect_chord_lane`` so the key
    surfaces in the persisted AnalysisResult dict (same defensibility
    pattern as the Phase-7 tempo/beats hoist). Direct chord_detector
    callers that don't need the key keep using ``detect_chords``.

    Stage 1.4.1 — power-chord third-absence prior (chord-lane only).
    The chord-lane stage opts into the persistence-gated,
    key-conditioned power-chord prior:

      * ``power_chord_third_ratio=0.4``    — third must be at most
        40% of the root+5th mass to flag third-absent
      * ``power_chord_penalty=0.03``       — subtractive cosine
        penalty on maj/min cells at that root
      * ``power_chord_third_min_streak=3`` — 3 consecutive windows
        (~1.5s at 0.5s windowing) before penalty fires
      * ``power_chord_minor_key_only=True`` — gated on the post-
        tie-break key being minor with strength >= 0.7

    Calibrated specifically to the rock idiom (minor-key overdriven
    guitar) without altering bench-corpus behaviour (bench uses the
    default DetectorConfig and these levers are all zero/False
    there). On songs outside the gate (major key or weak key
    confidence) the prior is silently disabled, so this stage
    matches the bench-corpus detector bit-for-bit on those inputs.
    """
    from tone_forge.analysis.detector_config import DetectorConfig

    _stage_config = DetectorConfig(
        power_chord_third_ratio=0.4,
        power_chord_penalty=0.03,
        power_chord_third_min_streak=3,
        power_chord_minor_key_only=True,
    )

    key_out: Dict[str, Any] = {}
    raw = _internal.detect_chords_from_audio(
        audio, sr,
        min_chord_duration=min_chord_duration_s,
        bass_y=bass_audio,
        beats_s=beats_s,
        config=_stage_config,
        key_out=key_out,
    )
    chords = tuple(
        Chord(
            start_s=float(c.start_time),
            end_s=float(c.end_time),
            symbol=c.name,
            confidence=float(c.confidence),
        )
        for c in raw
    )
    return chords, key_out


def snap_chord_boundaries_to_beats(
    chords: Tuple[Chord, ...],
    beats_s: Optional[np.ndarray],
    song_dur_s: float,
) -> Tuple[Chord, ...]:
    """Return ``chords`` with each region's start/end snapped to nearest beat.

    Phase 6 (hybrid grid). The detector emits regions on a fixed 0.5s
    grid because beat-driven chroma aggregation regressed WCSR
    (longer-averaged chroma loses discriminability — see the
    chord_detector phase-progression doc block). This post-processing
    pass moves boundary timestamps to the nearest musical beat so the
    Jam ribbon visually aligns to the rhythm, without disturbing the
    chord labels themselves.

    The toggle exists so the UI can switch between the
    higher-WCSR-precision view (no snap) and the visually-aligned
    view (snap on). Both arrays are computed once at analysis time;
    the toggle is a render-time choice.

    Args:
        chords: Detector output, ordered by ``start_s``, contiguous
            (no gaps), no overlaps.
        beats_s: Beat timestamps in seconds. None or fewer-than-2
            entries returns ``chords`` unchanged.
        song_dur_s: Song duration in seconds, used as the snap target
            for the very last region's end_time.

    Returns:
        Tuple of ``Chord`` with snapped timestamps. Length may be
        equal to or less than input length (regions that collapsed to
        zero duration after snap are dropped). Contiguity is
        preserved: each region's start equals the previous region's
        end.
    """
    if beats_s is None or len(chords) < 2:
        return chords
    beats_arr = np.asarray(beats_s, dtype=np.float64)
    if beats_arr.ndim != 1 or beats_arr.size < 2:
        return chords

    # Snap targets include song start and end so first/last region
    # boundaries have endpoints to land on outside the beat range.
    snap_targets = np.unique(np.concatenate((
        [0.0], beats_arr, [float(song_dur_s)],
    )))

    def _snap(t: float) -> float:
        return float(snap_targets[int(np.argmin(np.abs(snap_targets - t)))])

    snapped_starts = [_snap(c.start_s) for c in chords]
    snapped_ends = [_snap(c.end_s) for c in chords]

    # Force contiguity: a region's start equals the previous region's
    # snapped end. Pin the first start and last end to the original
    # values so the song's overall span is preserved.
    snapped_starts[0] = float(chords[0].start_s)
    snapped_ends[-1] = float(chords[-1].end_s)
    for i in range(1, len(chords)):
        snapped_starts[i] = snapped_ends[i - 1]

    return tuple(
        Chord(
            start_s=snapped_starts[i],
            end_s=snapped_ends[i],
            symbol=chords[i].symbol,
            confidence=chords[i].confidence,
        )
        for i in range(len(chords))
        if snapped_ends[i] > snapped_starts[i]
    )
