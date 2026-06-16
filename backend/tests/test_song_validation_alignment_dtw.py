"""Tests for ``song_validation.alignment.dtw``.

Pins the smarter-aligner contract: same call signature as
``align_grid``, same alignment_results / disagreements row shape,
but tolerates absolute-time drift between JAM and tab. The grid
aligner is kept as the cheapest baseline; DTW lets the operator
A/B both over the same (analysis, tab) pair to see whether time
warping cleans up spurious disagreements.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import pytest

from song_validation import Store
from song_validation.alignment import (
    AlignmentError,
    align_dtw,
    align_grid,
)
from song_validation.ingestion import (
    ingest_analysis_bundle,
    ingest_tab_source,
)


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(db_path=tmp_path / "sv.db")


def _bundle(
    song_id: str,
    chords: List[Dict[str, Any]],
    *,
    engine_version: str = "v1.0",
) -> Dict[str, Any]:
    return {
        "song_id": song_id,
        "chords": chords,
        "sections": [],
        "key": "C major",
        "tempo": 120.0,
        "engine_version": engine_version,
    }


def _tab(
    song_id: str,
    progression: List[Dict[str, Any]],
    *,
    source: str = "songsterr",
    source_confidence: float = 0.9,
) -> Dict[str, Any]:
    return {
        "song_id": song_id,
        "source": source,
        "progression": progression,
        "source_confidence": source_confidence,
    }


def _ingest_pair(
    store: Store,
    song_id: str,
    jam: List[Dict[str, Any]],
    tab: List[Dict[str, Any]],
) -> tuple[str, str]:
    a = ingest_analysis_bundle(_bundle(song_id, jam), store)
    t = ingest_tab_source(_tab(song_id, tab), store)
    return a, t


# ---------------------------------------------- contract / errors
def test_align_dtw_missing_analysis_raises(store: Store) -> None:
    with pytest.raises(AlignmentError):
        align_dtw("no-such-analysis", "no-such-tab", store)


def test_align_dtw_song_id_mismatch_raises(store: Store) -> None:
    chords = [{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]
    a = ingest_analysis_bundle(_bundle("song-a", chords), store)
    t = ingest_tab_source(_tab("song-b", chords), store)
    with pytest.raises(AlignmentError):
        align_dtw(a, t, store)


def test_align_dtw_zero_step_raises(store: Store) -> None:
    chords = [{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]
    a, t = _ingest_pair(store, "s1", chords, chords)
    with pytest.raises(AlignmentError):
        align_dtw(a, t, store, step_sec=0.0)


# ---------------------------------------------- alignment row shape
def test_align_dtw_writes_alignment_row(store: Store) -> None:
    chords = [{"symbol": "C", "startSec": 0.0, "endSec": 4.0}]
    a, t = _ingest_pair(store, "s1", chords, chords)
    al = align_dtw(a, t, store)
    row = store.get_alignment_result(al)
    assert row is not None
    assert row["song_id"] == "s1"
    assert row["analysis_id"] == a
    assert row["tab_id"] == t
    assert row["total_points"] > 0
    # Identical chords -> score 1.0.
    assert row["score"] == pytest.approx(1.0)


def test_align_dtw_id_distinct_from_grid_id(store: Store) -> None:
    """Same (analysis, tab, step) under both aligners -> distinct
    alignment_ids, so both rows can coexist."""
    chords = [{"symbol": "C", "startSec": 0.0, "endSec": 4.0}]
    a, t = _ingest_pair(store, "s1", chords, chords)
    al_grid = align_grid(a, t, store, step_sec=0.5)
    al_dtw = align_dtw(a, t, store, step_sec=0.5)
    assert al_grid != al_dtw
    # Both rows in the store.
    assert store.get_alignment_result(al_grid) is not None
    assert store.get_alignment_result(al_dtw) is not None


def test_align_dtw_id_deterministic(store: Store) -> None:
    """Re-running with the same inputs yields the same alignment_id
    (idempotent: same row gets overwritten/skipped depending on
    Store semantics, but the ID is stable)."""
    chords = [{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]
    a, t = _ingest_pair(store, "s1", chords, chords)
    al1 = align_dtw(a, t, store, step_sec=0.5)
    # Wipe the alignment row so the second run can re-insert without
    # PK collision; the derived ID must be identical.
    with store.connect() as conn:
        conn.execute(
            "DELETE FROM disagreements WHERE alignment_id = ?", (al1,)
        )
        conn.execute(
            "DELETE FROM alignment_results WHERE alignment_id = ?",
            (al1,),
        )
    al2 = align_dtw(a, t, store, step_sec=0.5)
    assert al1 == al2


# ---------------------------------------------- agreement behaviour
def test_align_dtw_zero_disagreements_when_identical(store: Store) -> None:
    chords = [{"symbol": "C", "startSec": 0.0, "endSec": 4.0}]
    a, t = _ingest_pair(store, "s1", chords, chords)
    al = align_dtw(a, t, store)
    with store.connect() as conn:
        (n,) = conn.execute(
            "SELECT COUNT(*) FROM disagreements WHERE alignment_id = ?",
            (al,),
        ).fetchone()
    assert n == 0


def test_align_dtw_records_disagreements_when_symbols_differ(
    store: Store,
) -> None:
    jam = [{"symbol": "C", "startSec": 0.0, "endSec": 4.0}]
    tab = [{"symbol": "Cmaj7", "startSec": 0.0, "endSec": 4.0}]
    a, t = _ingest_pair(store, "s1", jam, tab)
    al = align_dtw(a, t, store)
    with store.connect() as conn:
        rows = conn.execute(
            "SELECT jam_chord, tab_chord, classification "
            "FROM disagreements WHERE alignment_id = ?",
            (al,),
        ).fetchall()
    assert len(rows) > 0
    for jam_sym, tab_sym, classification in rows:
        assert jam_sym == "C"
        assert tab_sym == "Cmaj7"
        # DTW emits UNKNOWN; Phase-4 classifier refines later.
        assert classification == "UNKNOWN"


# ---------------------------------------------- the actual win
def test_align_dtw_wins_over_grid_when_tab_is_time_shifted(
    store: Store,
) -> None:
    """Tab is identical to JAM but shifted by 1.0s. Grid aligner sees
    a wall of disagreements at the boundary; DTW warps it out."""
    # JAM:  [C @ 0-2][G @ 2-4]
    # Tab:  [C @ 1-3][G @ 3-5]  (same sequence, shifted +1s)
    jam = [
        {"symbol": "C", "startSec": 0.0, "endSec": 2.0},
        {"symbol": "G", "startSec": 2.0, "endSec": 4.0},
    ]
    tab = [
        {"symbol": "C", "startSec": 1.0, "endSec": 3.0},
        {"symbol": "G", "startSec": 3.0, "endSec": 5.0},
    ]
    a, t = _ingest_pair(store, "s1", jam, tab)
    al_grid = align_grid(a, t, store, step_sec=0.5)
    al_dtw = align_dtw(a, t, store, step_sec=0.5)

    grid_row = store.get_alignment_result(al_grid)
    dtw_row = store.get_alignment_result(al_dtw)
    assert grid_row is not None and dtw_row is not None

    # DTW should score at least as high as grid here — the shift is
    # exactly what DTW exists to absorb.
    assert dtw_row["score"] is not None and grid_row["score"] is not None
    assert dtw_row["score"] >= grid_row["score"]
    # And on this synthetic case it should be strictly better.
    assert dtw_row["score"] > grid_row["score"]


# ---------------------------------------------- empty / boundary
def test_align_dtw_empty_sides_produces_no_grid(store: Store) -> None:
    """Both sides empty -> total_points 0, score NULL, no
    disagreements."""
    a = ingest_analysis_bundle(_bundle("s1", []), store)
    t = ingest_tab_source(_tab("s1", []), store)
    al = align_dtw(a, t, store)
    row = store.get_alignment_result(al)
    assert row is not None
    assert row["total_points"] == 0
    assert row["score"] is None


# ---------------------------------------------- DTW helpers
def test_dtw_path_aligns_shifted_sequences() -> None:
    """Unit-test the internal DTW: a shifted-but-same sequence should
    find a path that pairs equal symbols."""
    from song_validation.alignment.dtw import _dtw_path

    jam = ["C", "C", "G", "G"]
    tab = ["X", "C", "C", "G", "G"]  # one extra "X" frame up front
    path = _dtw_path(jam, tab)
    # End points pinned.
    assert path[0] == (0, 0)
    assert path[-1] == (len(jam) - 1, len(tab) - 1)
    # Every JAM index should appear at least once on the path.
    js_seen = {i for i, _ in path}
    assert js_seen == set(range(len(jam)))
