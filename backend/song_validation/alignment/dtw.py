"""Dynamic-time-warping (DTW) alignment.

``align_grid`` is the cheapest-correct baseline: at every grid point
it compares jam[t] vs tab[t] directly, assuming both sides share the
same absolute time base. That assumption fails when the tab and the
JAM analysis come from different recordings of the same song (live
vs studio cut, cover with different tempo, a tab the transcriber
wrote relative to an idealised beat grid). A few seconds of
absolute-time drift between sides produces a wall of spurious
disagreements that drown out the real engine errors.

``align_dtw`` is the smarter drop-in. Both sides are still sampled
at a fixed step (``step_sec`` defaults to a finer 0.25s for
DTW-quality alignment), but instead of pairing index-to-index we
build a chord-symbol distance matrix and solve a standard DTW DP to
find the cheapest warping path. Each JAM frame is then matched
against the tab frame the path picked for it; mismatches become
``disagreements`` rows at the JAM-frame timestamp.

Contract mirrors :func:`align_grid`:

- Loads one analysis + one tab from the store, errors if they
  reference different songs.
- Writes exactly one ``alignment_results`` row plus one
  ``disagreements`` row per mismatched JAM frame.
- Returns the new ``alignment_id``.

The alignment_id derivation includes the aligner kind, so a DTW run
and a grid run over the same (analysis, tab, step) get distinct IDs
and can coexist in the store. This matters for back-to-back A/B
runs while we're tuning aligner choice.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Iterable, List, Mapping, Optional, Sequence, Tuple

from ..disagreement import DisagreementClass
from ..store import Store
from .grid import AlignmentError, _chord_at_time, _symbol_of


DEFAULT_DTW_STEP_SEC = 0.25


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _derive_dtw_alignment_id(
    analysis_id: str, tab_id: str, step: float
) -> str:
    """DTW alignment ID: hash includes aligner kind so DTW and grid
    runs over the same (analysis, tab, step) produce distinct IDs."""
    digest = hashlib.sha256(
        json.dumps(
            {
                "analysis_id": analysis_id,
                "tab_id": tab_id,
                "step": step,
                "kind": "dtw",
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return f"al_{digest[:24]}"


def _derive_disagreement_id(alignment_id: str, timestamp: float) -> str:
    digest = hashlib.sha256(
        f"{alignment_id}:{timestamp:.6f}".encode("utf-8")
    ).hexdigest()
    return f"dg_{digest[:24]}"


def _sample_symbols(
    segments: Sequence[Mapping[str, Any]],
    grid: Sequence[float],
) -> List[Optional[str]]:
    """Look up the chord symbol active at every grid timestamp."""
    out: List[Optional[str]] = []
    for t in grid:
        seg = _chord_at_time(segments, t)
        out.append(_symbol_of(seg))
    return out


def _grid_points(
    jam_segments: Sequence[Mapping[str, Any]],
    tab_segments: Sequence[Mapping[str, Any]],
    step: float,
) -> List[float]:
    """Even grid from 0 to max(endSec). Returns a list, not a
    generator — DTW needs random access to the timestamp series.
    """
    if step <= 0:
        raise AlignmentError("grid step must be positive")
    end_jam = max(
        (float(s.get("endSec", 0.0)) for s in jam_segments), default=0.0
    )
    end_tab = max(
        (float(s.get("endSec", 0.0)) for s in tab_segments), default=0.0
    )
    end = max(end_jam, end_tab)
    if end <= 0.0:
        return []
    out: List[float] = []
    t = 0.0
    while t < end - 1e-9:
        out.append(round(t, 6))
        t += step
    return out


def _symbol_distance(a: Optional[str], b: Optional[str]) -> int:
    """Symmetric chord-symbol distance. 0 if equal, 1 otherwise.

    Two ``None``s (both sides silent / outside the covered range) are
    treated as equal — they shouldn't count as a disagreement.
    """
    if a == b:
        return 0
    return 1


def _dtw_path(
    jam_syms: Sequence[Optional[str]],
    tab_syms: Sequence[Optional[str]],
) -> List[Tuple[int, int]]:
    """Standard DTW: cost[i][j] = d(i,j) + min(neighbours).

    Returns the optimal warping path as a list of ``(i, j)`` index
    pairs from ``(0, 0)`` to ``(N-1, M-1)``. The neighbour set is
    the classic three: ``(i-1,j-1)``, ``(i-1,j)``, ``(i,j-1)``.
    Ties broken in the order diagonal > up > left so the path
    prefers the diagonal step (no warp) whenever it's free.

    O(N*M) time and memory. For 5-minute songs at 0.25s step we get
    N=M=1200, so a 1.44M-cell matrix — manageable in pure Python.
    """
    n = len(jam_syms)
    m = len(tab_syms)
    if n == 0 or m == 0:
        return []
    INF = float("inf")
    cost = [[INF] * m for _ in range(n)]
    cost[0][0] = _symbol_distance(jam_syms[0], tab_syms[0])
    # First row / column: only one predecessor each.
    for j in range(1, m):
        cost[0][j] = cost[0][j - 1] + _symbol_distance(
            jam_syms[0], tab_syms[j]
        )
    for i in range(1, n):
        cost[i][0] = cost[i - 1][0] + _symbol_distance(
            jam_syms[i], tab_syms[0]
        )
    # Body.
    for i in range(1, n):
        row = cost[i]
        prev = cost[i - 1]
        jsyms = jam_syms[i]
        for j in range(1, m):
            best = prev[j - 1]
            up = prev[j]
            if up < best:
                best = up
            left = row[j - 1]
            if left < best:
                best = left
            row[j] = best + _symbol_distance(jsyms, tab_syms[j])
    # Backtrack from (n-1, m-1).
    path: List[Tuple[int, int]] = []
    i, j = n - 1, m - 1
    path.append((i, j))
    while i > 0 or j > 0:
        if i == 0:
            j -= 1
        elif j == 0:
            i -= 1
        else:
            diag = cost[i - 1][j - 1]
            up = cost[i - 1][j]
            left = cost[i][j - 1]
            # Diagonal preference on ties.
            best = diag
            choice = "diag"
            if up < best:
                best = up
                choice = "up"
            if left < best:
                choice = "left"
            if choice == "diag":
                i -= 1
                j -= 1
            elif choice == "up":
                i -= 1
            else:
                j -= 1
        path.append((i, j))
    path.reverse()
    return path


def _match_jam_to_tab(
    path: Sequence[Tuple[int, int]], n_jam: int
) -> List[int]:
    """For each JAM index i, return the tab index j the path matched
    to it. When the path passes through several j's for the same i
    (a horizontal segment), pick the LAST one — that's the tab frame
    the path "settled on" before moving forward in JAM time.
    Deterministic and parallels how grid alignment would compare.
    """
    out: List[int] = [0] * n_jam
    for i, j in path:
        if 0 <= i < n_jam:
            out[i] = j
    return out


def align_dtw(
    analysis_id: str,
    tab_id: str,
    store: Store,
    *,
    step_sec: float = DEFAULT_DTW_STEP_SEC,
) -> str:
    """DTW-based alignment between one analysis bundle and one tab.

    Tolerates absolute-time drift between JAM and tab by warping the
    tab timeline onto JAM's frame-by-frame. Writes one
    ``alignment_results`` row and one ``disagreements`` row per JAM
    frame whose matched tab symbol differs.

    Returns the new ``alignment_id``.
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

    alignment_id = _derive_dtw_alignment_id(
        analysis_id, tab_id, step_sec
    )

    grid = _grid_points(jam_segments, tab_segments, step_sec)
    total = len(grid)
    jam_syms = _sample_symbols(jam_segments, grid)
    tab_syms = _sample_symbols(tab_segments, grid)

    path = _dtw_path(jam_syms, tab_syms)
    match = _match_jam_to_tab(path, len(jam_syms))

    agreed = 0
    disagreements: List[Tuple[float, Optional[str], Optional[str]]] = []
    for i, t in enumerate(grid):
        jam_sym = jam_syms[i]
        tab_sym = tab_syms[match[i]] if tab_syms else None
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
        aligner_kind="dtw",
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
