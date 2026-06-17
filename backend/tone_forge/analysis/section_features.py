"""Per-section feature signals for guidance-mode classification.

Pure signal extractors. No classification, no I/O, no audio decoding.
Given a stem's MIDI notes plus the song-wide chord lane and a section
window, returns a frozen ``SectionFeatures`` carrying the five signals
the ``guidance_mode`` classifier consumes:

    A. chord_density_per_s   — chord regions / sec inside the section
    B. monophonic_ratio       — fraction of voiced time with one pitch
    C. repetition_score       — pitch-interval n-gram self-similarity
    D. polyphony_score        — mean simultaneous-pitch count
    E. lead_activity_score    — note rate × melodic-interval magnitude

The detector does not need any of these signals; they exist purely to
decide *what guidance to display* in the JAM UI (chord ribbon vs riff
lane vs lead phrase lane). The chord detector is unchanged by this
module — it keeps emitting full per-song chord regions, and the
classifier downstream decides whether to *use* them per section.

Note shape contract
-------------------
Notes are accepted as an iterable of mappings (or objects) exposing
``pitch`` (int, MIDI number), ``start`` (float, seconds), and ``end``
(float, seconds). This matches both the in-pipeline ``ExtractedNote`` /
``EnsembleNote`` dataclasses (`tone_forge.midi.passes.base.ExtractedNote`
fields ``pitch``/``start``/``end``) and the persisted JSON shape
emitted by ``unified_pipeline._build_midi_stems_payload`` (dicts with
``pitch``/``start``/``end``/``velocity``). Both work here because we
only read attributes/keys, never construct.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable, Optional, Sequence

import numpy as np


# 50 ms voxel grid for monophonic-ratio / polyphony computations. Chosen
# to be slightly finer than typical 16th-note resolution at moderate
# tempi (16th @ 180 BPM ≈ 83 ms) so we don't blur adjacent notes into
# one cell.
_VOXEL_HOP_S: float = 0.05


@dataclass(frozen=True)
class SectionFeatures:
    """Feature vector for one stem inside one section."""

    stem_name: str

    # Signal A — Chord density (chords / sec inside the section).
    chord_density_per_s: float
    chord_count_in_section: int

    # Signal B — Monophonic dominance.
    monophonic_ratio: float

    # Signal C — Repetition.
    repetition_score: float
    repetition_period_beats: Optional[float]

    # Signal D — Harmonic occupancy.
    polyphony_score: float

    # Signal E — Lead activity.
    lead_activity_score: float

    # Floor info for the aggregator.
    voiced_frame_ratio: float
    note_count: int
    duration_s: float


def _note_pitch(note: Any) -> int:
    if isinstance(note, dict):
        return int(note["pitch"])
    return int(getattr(note, "pitch"))


def _note_start(note: Any) -> float:
    if isinstance(note, dict):
        return float(note["start"])
    return float(getattr(note, "start"))


def _note_end(note: Any) -> float:
    if isinstance(note, dict):
        return float(note["end"])
    return float(getattr(note, "end"))


def _clip_notes_to_section(
    notes: Iterable[Any],
    section_start_s: float,
    section_end_s: float,
) -> list[tuple[int, float, float]]:
    """Return ``[(pitch, clipped_start, clipped_end), ...]`` for notes
    whose support intersects the section, with start/end clamped to the
    section bounds. Notes that don't overlap at all are dropped.

    Sorted by start time.
    """
    clipped: list[tuple[int, float, float]] = []
    for n in notes:
        s = _note_start(n)
        e = _note_end(n)
        if e <= section_start_s or s >= section_end_s:
            continue
        cs = max(s, section_start_s)
        ce = min(e, section_end_s)
        if ce <= cs:
            continue
        clipped.append((_note_pitch(n), cs, ce))
    clipped.sort(key=lambda t: t[1])
    return clipped


def _chord_density(
    chord_regions: Sequence[Any],
    section_start_s: float,
    section_end_s: float,
    duration_s: float,
) -> tuple[float, int]:
    """Count chord regions whose midpoint lies inside the section.

    Midpoint-membership avoids double-counting regions that span a
    section boundary — consistent with how the chord ribbon already
    trims display.
    """
    count = 0
    for c in chord_regions:
        # Tolerate both ``contracts.Chord`` (start_s/end_s) and dict-
        # like rows (start/end). The pipeline uses Chord; tests can
        # synthesize lighter rows.
        if hasattr(c, "start_s"):
            cs = float(c.start_s)
            ce = float(c.end_s)
        elif isinstance(c, dict):
            cs = float(c.get("start_s", c.get("start", 0.0)))
            ce = float(c.get("end_s", c.get("end", 0.0)))
        else:
            continue
        mid = 0.5 * (cs + ce)
        if section_start_s <= mid < section_end_s:
            count += 1
    denom = max(duration_s, 0.5)  # avoid div-by-zero on tiny sections
    return count / denom, count


def _voxelise(
    clipped: Sequence[tuple[int, float, float]],
    section_start_s: float,
    section_end_s: float,
) -> np.ndarray:
    """Return a 1D array of simultaneous-note counts on the 50 ms grid.

    ``out[i]`` = number of notes overlapping voxel
    ``[section_start_s + i*hop, section_start_s + (i+1)*hop)``.
    """
    duration_s = max(section_end_s - section_start_s, 0.0)
    n_voxels = max(int(np.ceil(duration_s / _VOXEL_HOP_S)), 1)
    counts = np.zeros(n_voxels, dtype=np.int32)
    if not clipped:
        return counts
    for _, cs, ce in clipped:
        i0 = max(int(np.floor((cs - section_start_s) / _VOXEL_HOP_S)), 0)
        i1 = min(int(np.ceil((ce - section_start_s) / _VOXEL_HOP_S)), n_voxels)
        if i1 > i0:
            counts[i0:i1] += 1
    return counts


def _monophonic_and_polyphony(
    voxel_counts: np.ndarray,
) -> tuple[float, float, float]:
    """Return ``(monophonic_ratio, polyphony_score, voiced_frame_ratio)``.

    monophonic_ratio = mono_voxels / voiced_voxels
    polyphony_score  = mean(voiced_counts) / 6, clipped to [0, 1]
    voiced_frame_ratio = voiced_voxels / total_voxels
    """
    total = voxel_counts.size
    if total == 0:
        return 0.0, 0.0, 0.0
    voiced_mask = voxel_counts > 0
    n_voiced = int(voiced_mask.sum())
    if n_voiced == 0:
        return 0.0, 0.0, 0.0
    n_mono = int((voxel_counts == 1).sum())
    mono_ratio = n_mono / n_voiced
    mean_voiced = float(voxel_counts[voiced_mask].mean())
    poly = float(np.clip(mean_voiced / 6.0, 0.0, 1.0))
    voiced_ratio = n_voiced / total
    return mono_ratio, poly, voiced_ratio


def _repetition(
    clipped: Sequence[tuple[int, float, float]],
    beats_s: Optional[np.ndarray],
    section_start_s: float,
    section_end_s: float,
) -> tuple[float, Optional[float]]:
    """Pitch-interval n-gram repetition score (and best period in beats).

    The period in beats is derived from *which* n scored highest:
    that n is the loop length in *notes*, which we then convert to
    beats via the section's note-rate.
    """
    if len(clipped) < 4:
        return 0.0, None
    pitches = [p for p, _, _ in clipped]
    intervals = [pitches[i + 1] - pitches[i] for i in range(len(pitches) - 1)]
    if len(intervals) < 3:
        return 0.0, None
    best_score = 0.0
    best_n: Optional[int] = None
    for n in (3, 4, 5):
        if len(intervals) < n + 1:
            continue
        ngrams = [tuple(intervals[i : i + n]) for i in range(len(intervals) - n + 1)]
        if not ngrams:
            continue
        counts = Counter(ngrams)
        repeated = sum(c for c in counts.values() if c >= 2)
        score = repeated / len(ngrams)
        # Prefer larger n when scores tie — a longer matching n-gram
        # implies a longer real loop. A 4-note riff scores 1.0 at
        # n=3, 4, and 5, but n=4 is the truthful period.
        if score > best_score or (score == best_score and n > (best_n or 0)):
            best_score = score
            best_n = n
    period_beats: Optional[float] = None
    if best_score > 0.4 and best_n is not None and beats_s is not None:
        beats_arr = np.asarray(beats_s, dtype=np.float64)
        in_section = beats_arr[
            (beats_arr >= section_start_s) & (beats_arr < section_end_s)
        ]
        if in_section.size >= 2:
            duration_beats = float(in_section.size)
            note_rate_per_beat = len(clipped) / max(duration_beats, 1.0)
            if note_rate_per_beat > 0:
                raw_period = best_n / note_rate_per_beat
                # Quantise to the nearest {1, 2, 4, 8} bar fraction.
                period_beats = min(
                    (1.0, 2.0, 4.0, 8.0),
                    key=lambda p: abs(p - raw_period),
                )
    return float(best_score), period_beats


def _lead_activity(
    clipped: Sequence[tuple[int, float, float]],
    duration_s: float,
) -> float:
    """Lead-activity score in [0, 1].

    Combines note rate and mean absolute melodic interval. Both inputs
    are normalised piecewise so a value of ``1.0`` is "clearly lead"
    on the synthetic fixtures (see ``backend/tests/fixtures/guidance_mode.py``).
    """
    if not clipped:
        return 0.0
    pitches = [p for p, _, _ in clipped]
    n_notes = len(pitches)
    rate = n_notes / max(duration_s, 0.5)
    # Rate normalisation: 0 notes/s → 0, 4 notes/s → 1.
    rate_score = float(np.clip(rate / 4.0, 0.0, 1.0))
    if n_notes < 2:
        return float(np.clip(0.5 * rate_score, 0.0, 1.0))
    intervals = [abs(pitches[i + 1] - pitches[i]) for i in range(n_notes - 1)]
    interval_mean = float(np.mean(intervals))
    # Interval normalisation: 0 semitones → 0, 7 semitones → 1.
    interval_score = float(np.clip(interval_mean / 7.0, 0.0, 1.0))
    return float(np.clip(0.5 * rate_score + 0.5 * interval_score, 0.0, 1.0))


def compute_section_features(
    *,
    stem_name: str,
    stem_midi: Optional[Iterable[Any]],
    chord_regions: Sequence[Any],
    section_start_s: float,
    section_end_s: float,
    beats_s: Optional[np.ndarray] = None,
) -> SectionFeatures:
    """Compute a per-stem ``SectionFeatures`` for one section window.

    Args:
        stem_name: Identifier (e.g. ``"bass"``, ``"other"``) used by the
            aggregator for human-readable reasoning strings.
        stem_midi: Iterable of notes (dict or dataclass) for this stem
            *across the whole song*. ``None`` is treated as silent.
        chord_regions: Song-wide chord lane (``Chord`` instances or
            dict rows with ``start_s``/``end_s``).
        section_start_s, section_end_s: Section bounds in seconds.
        beats_s: Optional beat times (seconds) used to surface the
            detected repetition period in beats.

    Returns:
        Frozen ``SectionFeatures`` with all five signals plus floor info.
    """
    duration_s = max(section_end_s - section_start_s, 0.0)

    notes_iter = stem_midi if stem_midi is not None else ()
    clipped = _clip_notes_to_section(notes_iter, section_start_s, section_end_s)
    note_count = len(clipped)

    voxel_counts = _voxelise(clipped, section_start_s, section_end_s)
    mono_ratio, poly_score, voiced_ratio = _monophonic_and_polyphony(voxel_counts)

    rep_score, rep_period = _repetition(
        clipped, beats_s, section_start_s, section_end_s
    )
    lead_score = _lead_activity(clipped, duration_s)
    density, count_in = _chord_density(
        chord_regions, section_start_s, section_end_s, duration_s
    )

    return SectionFeatures(
        stem_name=stem_name,
        chord_density_per_s=density,
        chord_count_in_section=count_in,
        monophonic_ratio=mono_ratio,
        repetition_score=rep_score,
        repetition_period_beats=rep_period,
        polyphony_score=poly_score,
        lead_activity_score=lead_score,
        voiced_frame_ratio=voiced_ratio,
        note_count=note_count,
        duration_s=duration_s,
    )


__all__ = [
    "SectionFeatures",
    "compute_section_features",
]
