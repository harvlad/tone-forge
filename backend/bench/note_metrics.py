"""Note-level transcription metrics (task 12 substrate).

Strict, standard MIR scoring for MIDI extraction accuracy via
``mir_eval.transcription``. This intentionally does NOT reuse
``tone_forge.evaluation.metrics.compute_midi_quality`` — that metric
auto-aligns time offsets, allows octave equivalence and a 300ms onset
window, which is useful for smoke tests but too forgiving to drive
hardening decisions. Here:

* onset tolerance 50ms (mir_eval default, MIREX standard)
* pitch tolerance 50 cents, NO octave equivalence
* two scores per comparison:
  - ``onset``  — onset+pitch only (offset_ratio=None)
  - ``full``   — onset+pitch+offset (offset_ratio=0.2)

Input note shape matches the engine's normalized dicts::

    {"pitch": 57, "start": 1.23, "end": 1.75, ...}

Extra keys (velocity, role, confidence) are ignored. Notes with
non-positive duration are clamped to a 1ms minimum so mir_eval's
interval validation never rejects an otherwise-usable note.

Drums: pitched matching is meaningless for the onset-based drum
extractor (GM percussion numbers vs detected classes), so
``onset_only_prf`` scores pure onset lists with no pitch constraint —
use that for the drums lane.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

import numpy as np

__all__ = ["NotePRF", "note_prf", "onset_only_prf", "notes_to_arrays"]

_MIN_DUR_S = 0.001


@dataclass(frozen=True)
class NotePRF:
    """Precision / recall / F1 for one comparison."""

    precision: float
    recall: float
    f1: float
    n_ref: int
    n_est: int

    def to_dict(self) -> dict:
        return {
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
            "n_ref": self.n_ref,
            "n_est": self.n_est,
        }


def notes_to_arrays(
    notes: Iterable[Mapping[str, object]],
) -> tuple[np.ndarray, np.ndarray]:
    """Convert engine note dicts to (intervals[n,2], pitches_hz[n]).

    Skips notes missing pitch/start. ``end`` missing or <= start is
    clamped to start + 1ms. Pitch converted MIDI -> Hz because
    mir_eval.transcription matches in Hz/cents.
    """
    intervals: list[list[float]] = []
    pitches: list[float] = []
    for n in notes:
        try:
            pitch = float(n["pitch"])  # type: ignore[index]
            start = float(n["start"])  # type: ignore[index]
        except (KeyError, TypeError, ValueError):
            continue
        raw_end = n.get("end")
        try:
            end = float(raw_end)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            end = start + _MIN_DUR_S
        if end <= start:
            end = start + _MIN_DUR_S
        intervals.append([start, end])
        pitches.append(440.0 * (2.0 ** ((pitch - 69.0) / 12.0)))
    if not intervals:
        return np.zeros((0, 2)), np.zeros((0,))
    return np.asarray(intervals, dtype=float), np.asarray(pitches, dtype=float)


def note_prf(
    ref_notes: Sequence[Mapping[str, object]],
    est_notes: Sequence[Mapping[str, object]],
    *,
    onset_tolerance_s: float = 0.05,
    pitch_tolerance_cents: float = 50.0,
    with_offset: bool = False,
) -> NotePRF:
    """Strict note-level P/R/F1 (onset+pitch, optionally +offset)."""
    from mir_eval import transcription

    ref_iv, ref_hz = notes_to_arrays(ref_notes)
    est_iv, est_hz = notes_to_arrays(est_notes)

    if len(ref_hz) == 0 and len(est_hz) == 0:
        return NotePRF(1.0, 1.0, 1.0, 0, 0)
    if len(ref_hz) == 0 or len(est_hz) == 0:
        return NotePRF(0.0, 0.0, 0.0, len(ref_hz), len(est_hz))

    p, r, f, _ = transcription.precision_recall_f1_overlap(
        ref_iv,
        ref_hz,
        est_iv,
        est_hz,
        onset_tolerance=onset_tolerance_s,
        pitch_tolerance=pitch_tolerance_cents,
        offset_ratio=0.2 if with_offset else None,
    )
    return NotePRF(float(p), float(r), float(f), len(ref_hz), len(est_hz))


def onset_only_prf(
    ref_onsets_s: Sequence[float],
    est_onsets_s: Sequence[float],
    *,
    tolerance_s: float = 0.05,
) -> NotePRF:
    """Pitch-free onset matching (drums lane).

    Greedy one-to-one matching on sorted onsets within tolerance —
    same contract as mir_eval.onset.f_measure but returns NotePRF.
    """
    from mir_eval import onset as mir_onset

    ref = np.asarray(sorted(float(t) for t in ref_onsets_s), dtype=float)
    est = np.asarray(sorted(float(t) for t in est_onsets_s), dtype=float)

    if len(ref) == 0 and len(est) == 0:
        return NotePRF(1.0, 1.0, 1.0, 0, 0)
    if len(ref) == 0 or len(est) == 0:
        return NotePRF(0.0, 0.0, 0.0, len(ref), len(est))

    f, p, r = mir_onset.f_measure(ref, est, window=tolerance_s)
    return NotePRF(float(p), float(r), float(f), len(ref), len(est))
