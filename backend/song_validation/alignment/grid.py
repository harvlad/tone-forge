"""Grid-tempo alignment.

The simplest possible aligner: sample at a fixed cadence over the
union of the analysis chord-track and the tab progression, and at
each grid point ask both sides "what chord is active here?". If the
two answers differ, emit a ``disagreement`` row tagged ``UNKNOWN``
for the Phase-4 classifier to refine.

This is intentionally the cheapest correct baseline so the metrics
loop has *some* signal to roll up. Smarter aligners (DTW,
section-anchored, tempo-warping) can drop in behind the same
function signature.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional, Sequence

from ..disagreement import DisagreementClass
from ..store import Store


DEFAULT_GRID_STEP_SEC = 0.5


class AlignmentError(ValueError):
    """Raised when alignment inputs are inconsistent."""


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _derive_alignment_id(analysis_id: str, tab_id: str, step: float) -> str:
    """Content-addressed alignment ID: (analysis, tab, step) -> stable ID."""
    digest = hashlib.sha256(
        json.dumps(
            {"analysis_id": analysis_id, "tab_id": tab_id, "step": step},
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return f"al_{digest[:24]}"


def _derive_disagreement_id(alignment_id: str, timestamp: float) -> str:
    """Stable per-(alignment, timestamp) ID. Re-running the aligner with
    the same inputs overwrites in-place if callers add ``INSERT OR
    REPLACE`` later; for now a strict INSERT surfaces duplicates."""
    digest = hashlib.sha256(
        f"{alignment_id}:{timestamp:.6f}".encode("utf-8")
    ).hexdigest()
    return f"dg_{digest[:24]}"


def _chord_at_time(
    segments: Sequence[Mapping[str, Any]], t: float
) -> Optional[Mapping[str, Any]]:
    """Return the segment whose ``[startSec, endSec)`` contains ``t``.

    Segments are assumed to be in time order, non-overlapping. Returns
    ``None`` when ``t`` falls in a gap or outside the covered range.
    """
    for seg in segments:
        start = float(seg.get("startSec", 0.0))
        end = float(seg.get("endSec", start))
        if start <= t < end:
            return seg
    return None


def _symbol_of(seg: Optional[Mapping[str, Any]]) -> Optional[str]:
    if seg is None:
        return None
    sym = seg.get("symbol")
    return str(sym) if sym is not None else None


def _grid_points(
    jam_segments: Sequence[Mapping[str, Any]],
    tab_segments: Sequence[Mapping[str, Any]],
    step: float,
) -> Iterable[float]:
    """Yield evenly spaced timestamps from 0 up to the latest endSec
    seen on either side. Stops at the maximum; if both sides are
    empty, yields nothing."""
    if step <= 0:
        raise AlignmentError("grid step must be positive")
    end_jam = max((float(s.get("endSec", 0.0)) for s in jam_segments), default=0.0)
    end_tab = max((float(s.get("endSec", 0.0)) for s in tab_segments), default=0.0)
    end = max(end_jam, end_tab)
    if end <= 0.0:
        return
    t = 0.0
    # Use a tolerance so float drift doesn't drop the final grid point.
    while t < end - 1e-9:
        yield round(t, 6)
        t += step


def align_grid(
    analysis_id: str,
    tab_id: str,
    store: Store,
    *,
    step_sec: float = DEFAULT_GRID_STEP_SEC,
) -> str:
    """Run grid alignment between one analysis bundle and one tab.

    Loads both rows from the store, samples each grid point, writes
    one ``alignment_results`` row, and one ``disagreements`` row per
    grid point where the two chord symbols differ (``classification``
    initialised to ``UNKNOWN`` — Phase 4 refines).

    Returns the new ``alignment_id``.

    Raises :class:`AlignmentError` if either side is missing or if
    they refer to different songs.
    """
    analysis = store.get_analysis_result(analysis_id)
    if analysis is None:
        raise AlignmentError(f"analysis not found: {analysis_id!r}")
    tab = store.get_tab_source(tab_id)
    if tab is None:
        raise AlignmentError(f"tab not found: {tab_id!r}")
    if analysis["song_id"] != tab["song_id"]:
        raise AlignmentError(
            f"analysis song_id={analysis['song_id']!r} does not match "
            f"tab song_id={tab['song_id']!r}"
        )

    song_id = analysis["song_id"]
    jam_segments = list(analysis.get("chords", []))
    tab_segments = list(tab.get("progression", []))

    alignment_id = _derive_alignment_id(analysis_id, tab_id, step_sec)

    grid = list(_grid_points(jam_segments, tab_segments, step_sec))
    total = len(grid)
    agreed = 0
    disagreements: list[tuple[float, Optional[str], Optional[str]]] = []
    for t in grid:
        jam_seg = _chord_at_time(jam_segments, t)
        tab_seg = _chord_at_time(tab_segments, t)
        jam_sym = _symbol_of(jam_seg)
        tab_sym = _symbol_of(tab_seg)
        if jam_sym == tab_sym:
            agreed += 1
        else:
            disagreements.append((t, jam_sym, tab_sym))

    score = (agreed / total) if total > 0 else None
    store.insert_alignment_result(
        alignment_id=alignment_id,
        song_id=song_id,
        analysis_id=analysis_id,
        tab_id=tab_id,
        score=score,
        total_points=total,
        created_at=_utcnow_iso(),
        aligner_kind="grid",
    )

    for t, jam_sym, tab_sym in disagreements:
        store.insert_disagreement(
            disagreement_id=_derive_disagreement_id(alignment_id, t),
            song_id=song_id,
            alignment_id=alignment_id,
            timestamp=t,
            jam_chord=jam_sym,
            tab_chord=tab_sym,
            confidence=None,
            classification=DisagreementClass.UNKNOWN.value,
        )

    return alignment_id
