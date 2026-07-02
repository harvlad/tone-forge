"""Chord-vocabulary Jaccard boundary detector (Round-2 Fix 4).

Refines section boundaries produced by the primary RMS-novelty
section detector by splitting on **harmonic-content shifts** — cases
where the chord vocabulary changes sharply across a candidate split
point even though energy is roughly stable.

Motivating case (session 1313168e, Linkin Park "One Step Closer"):
the pre-chorus is a single-chord F#5 vamp; the following chorus
introduces a full 6-chord progression. Energy is broadly comparable
across the transition, so the RMS-novelty detector fuses the two
into one long CHORUS block. The chord-vocabulary Jaccard distance
across the same seam is ~0.83, which this stage promotes to a
boundary. Downstream (H2 refine Pass 2b) then labels the first
sub-section PRECHORUS.

Genre-neutral. The Jaccard signal is scale-free (vocabulary size
does not affect the metric), and the sliding-window comparator
fires anywhere the harmonic language shifts sharply — pre-chorus
into chorus, verse into bridge, coda arrival, etc. No corpus-
specific thresholds; no song-specific branches.

Bench-bit-exact contract: this module runs only inside
``unified_pipeline`` and the ``session/bundle`` re-load path. The
bench chord-eval harness calls ``detect_chords_from_audio`` directly
and never crosses this stage.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

import numpy as np

from tone_forge.analysis.chords import _chord_root_pc


__all__ = ["detect_chord_vocab_boundaries"]


def _section_bounds(section: Mapping[str, Any]) -> tuple[float, float]:
    """Read ``(start_s, end_s)`` from a section dict.

    Supports both the ``ArrangementSection.to_dict()`` shape
    (``start_time`` / ``end_time``) and the older
    ``start_s`` / ``end_s`` shape used by a handful of legacy
    fixtures. Falls back to ``0.0`` when neither pair is present so
    the caller can filter degenerate rows without crashing.
    """
    s = section.get("start_time")
    if s is None:
        s = section.get("start_s")
    e = section.get("end_time")
    if e is None:
        e = section.get("end_s")
    try:
        return float(s), float(e)
    except (TypeError, ValueError):
        return 0.0, 0.0


def _chord_root_or_none(symbol: Any) -> Optional[int]:
    """Wrap ``_chord_root_pc`` with a ``None``-tolerant contract.

    Silence and no-chord regions (``"N"``) return ``None`` and are
    excluded from the vocabulary. Any parse failure returns ``None``
    too — defensive so a garbled symbol can't sink the boundary
    detector.
    """
    if not symbol:
        return None
    text = str(symbol).strip()
    if not text or text.upper() in ("N", "NC", "N.C.", "-"):
        return None
    try:
        return _chord_root_pc(text)
    except Exception:
        return None


def _vocab_in_window(
    chords: Sequence[Mapping[str, Any]],
    beats_s: np.ndarray,
    beat_start: int,
    beat_end: int,
) -> set[int]:
    """Return the set of chord-root pitch classes voiced in
    ``[beats_s[beat_start], beats_s[beat_end])``.

    Any chord region overlapping the window contributes its root.
    Empty window (no overlap) returns an empty set — Jaccard on two
    empty sets is defined as distance 0 (no shift).
    """
    if beat_start >= beat_end or beat_end > beats_s.size:
        return set()
    t0 = float(beats_s[beat_start])
    t1 = float(beats_s[min(beat_end, beats_s.size - 1)])
    if t1 <= t0:
        return set()
    vocab: set[int] = set()
    for row in chords:
        cs = row.get("start_s") if isinstance(row, Mapping) else None
        ce = row.get("end_s") if isinstance(row, Mapping) else None
        if cs is None:
            cs = getattr(row, "start_s", None)
        if ce is None:
            ce = getattr(row, "end_s", None)
        if cs is None or ce is None:
            continue
        try:
            cs_f, ce_f = float(cs), float(ce)
        except (TypeError, ValueError):
            continue
        # Half-open overlap: [cs, ce) intersects [t0, t1).
        if ce_f <= t0 or cs_f >= t1:
            continue
        symbol = (
            row.get("symbol") if isinstance(row, Mapping)
            else getattr(row, "symbol", None)
        )
        pc = _chord_root_or_none(symbol)
        if pc is not None:
            vocab.add(pc)
    return vocab


def _jaccard_distance(a: set[int], b: set[int]) -> float:
    """Jaccard distance ``1 - |A∩B| / |A∪B|``.

    Two empty sets → 0.0 (no shift). One empty + one non-empty →
    1.0 (maximum shift; harmonic content appeared or vanished).
    """
    if not a and not b:
        return 0.0
    union = a | b
    if not union:
        return 0.0
    inter = a & b
    return 1.0 - (len(inter) / len(union))


def detect_chord_vocab_boundaries(
    sections: Sequence[Mapping[str, Any]],
    chords: Sequence[Mapping[str, Any]],
    beats_s: Optional[np.ndarray],
    *,
    window_beats: int = 8,
    min_jaccard_distance: float = 0.6,
    min_sub_duration_s: float = 8.0,
) -> list[dict]:
    """Return new-boundary rows to inject into an existing section list.

    Each row is ``{"time_s": float, "source_section_index": int}``.
    The caller is responsible for using the row to split the source
    section — this function is pure detection.

    Walks each input section with a sliding window of ``window_beats``
    beats (default 8 = 2 bars @ 4/4). At each candidate split point,
    compares the chord-root vocabulary of the preceding window vs
    the following window using Jaccard distance. Peaks above
    ``min_jaccard_distance`` that sit at least ``min_sub_duration_s``
    seconds inside each side of the section (and away from other
    peaks) get promoted to boundaries.

    Degenerate inputs return an empty list defensively:
      - empty section list
      - ``beats_s`` is ``None`` or has fewer than ``2 * window_beats``
        entries
      - empty chord lane
    """
    if not sections:
        return []
    if beats_s is None:
        return []
    beats_arr = np.asarray(beats_s, dtype=np.float64)
    if beats_arr.size < 2 * window_beats:
        return []
    if not chords:
        return []

    new_rows: list[dict] = []

    for sec_idx, section in enumerate(sections):
        s_start, s_end = _section_bounds(section)
        if s_end - s_start < 2 * min_sub_duration_s:
            # Section too short to yield a sub-section on either
            # side of a split. Skip.
            continue

        # Beat indices covered by this section.
        in_sec_mask = (beats_arr >= s_start) & (beats_arr < s_end)
        sec_beat_idx = np.where(in_sec_mask)[0]
        if sec_beat_idx.size < 2 * window_beats:
            continue

        beat_lo = int(sec_beat_idx[0])
        beat_hi = int(sec_beat_idx[-1]) + 1

        # Slide a candidate split point through the section, keeping
        # a full window on each side.
        peaks: list[tuple[int, float]] = []  # (beat_index, jaccard)
        for split_beat in range(
            beat_lo + window_beats, beat_hi - window_beats + 1
        ):
            left_lo = split_beat - window_beats
            right_hi = split_beat + window_beats
            left_vocab = _vocab_in_window(
                chords, beats_arr, left_lo, split_beat,
            )
            right_vocab = _vocab_in_window(
                chords, beats_arr, split_beat, right_hi,
            )
            dist = _jaccard_distance(left_vocab, right_vocab)
            if dist >= min_jaccard_distance:
                # Only accept if the resulting sub-sections meet the
                # min-duration guard on both sides.
                split_time = float(beats_arr[split_beat])
                if (split_time - s_start) >= min_sub_duration_s and \
                   (s_end - split_time) >= min_sub_duration_s:
                    peaks.append((split_beat, dist))

        if not peaks:
            continue

        # Suppress adjacent peaks (keep the strongest in any window-
        # sized neighbourhood, and ensure any two accepted peaks are
        # at least ``min_sub_duration_s`` apart).
        peaks.sort(key=lambda x: x[1], reverse=True)
        accepted: list[tuple[int, float]] = []
        for beat_idx, dist in peaks:
            t = float(beats_arr[beat_idx])
            if any(
                abs(t - float(beats_arr[other_beat])) < min_sub_duration_s
                for other_beat, _ in accepted
            ):
                continue
            accepted.append((beat_idx, dist))

        for beat_idx, _dist in accepted:
            new_rows.append({
                "time_s": float(beats_arr[beat_idx]),
                "source_section_index": sec_idx,
            })

    return new_rows
