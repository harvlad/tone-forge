"""Tests for ``song_validation.alignment.grid``.

The grid aligner is the cheapest-correct baseline: sample at a fixed
cadence and emit a disagreement row for every grid point where jam
and tab disagree. These tests pin the contract so smarter aligners
can drop in behind the same signature later.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import pytest

from song_validation import Store
from song_validation.alignment import AlignmentError, align_grid
from song_validation.disagreement import DisagreementClass
from song_validation.ingestion import (
    ingest_analysis_bundle,
    ingest_tab_source,
)


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(db_path=tmp_path / "sv.db")


def _bundle(
    chords: List[Dict[str, Any]], **overrides: Any
) -> Dict[str, Any]:
    base: Dict[str, Any] = {
        "song_id": "song-align",
        "chords": chords,
        "sections": [],
        "key": "C major",
        "tempo": 120.0,
    }
    base.update(overrides)
    return base


def _tab(
    progression: List[Dict[str, Any]], **overrides: Any
) -> Dict[str, Any]:
    base: Dict[str, Any] = {
        "song_id": "song-align",
        "source": "songsterr",
        "progression": progression,
    }
    base.update(overrides)
    return base


def test_align_grid_perfect_agreement_score_is_one(store: Store) -> None:
    """If jam and tab agree at every grid point, score == 1.0 and no
    disagreement rows are emitted."""
    chords = [
        {"symbol": "C", "startSec": 0.0, "endSec": 2.0},
        {"symbol": "G", "startSec": 2.0, "endSec": 4.0},
    ]
    a_id = ingest_analysis_bundle(_bundle(chords), store)
    t_id = ingest_tab_source(_tab(chords), store)

    al_id = align_grid(a_id, t_id, store, step_sec=0.5)

    al = store.get_alignment_result(al_id)
    assert al is not None
    assert al["score"] == pytest.approx(1.0)
    assert store.list_disagreements_for_alignment(al_id) == []


def test_align_grid_total_disagreement_score_is_zero(store: Store) -> None:
    """If jam and tab disagree at every grid point, score == 0.0 and
    every sampled point becomes a disagreement row."""
    jam = [{"symbol": "C", "startSec": 0.0, "endSec": 4.0}]
    tab = [{"symbol": "G", "startSec": 0.0, "endSec": 4.0}]
    a_id = ingest_analysis_bundle(_bundle(jam), store)
    t_id = ingest_tab_source(_tab(tab), store)

    al_id = align_grid(a_id, t_id, store, step_sec=0.5)

    al = store.get_alignment_result(al_id)
    assert al is not None
    assert al["score"] == pytest.approx(0.0)
    rows = store.list_disagreements_for_alignment(al_id)
    # 4.0s span / 0.5s step = 8 grid points; all disagree.
    assert len(rows) == 8
    for r in rows:
        assert r["jam_chord"] == "C"
        assert r["tab_chord"] == "G"
        assert r["classification"] == DisagreementClass.UNKNOWN.value


def test_align_grid_partial_agreement(store: Store) -> None:
    """Half the grid agrees, half disagrees -> score == 0.5."""
    jam = [
        {"symbol": "C", "startSec": 0.0, "endSec": 2.0},
        {"symbol": "G", "startSec": 2.0, "endSec": 4.0},
    ]
    tab = [
        {"symbol": "C", "startSec": 0.0, "endSec": 2.0},
        {"symbol": "Am", "startSec": 2.0, "endSec": 4.0},  # diverges
    ]
    a_id = ingest_analysis_bundle(_bundle(jam), store)
    t_id = ingest_tab_source(_tab(tab), store)

    al_id = align_grid(a_id, t_id, store, step_sec=0.5)

    al = store.get_alignment_result(al_id)
    assert al is not None
    assert al["score"] == pytest.approx(0.5)

    rows = store.list_disagreements_for_alignment(al_id)
    assert len(rows) == 4  # 2.0..3.5
    timestamps = sorted(r["timestamp"] for r in rows)
    assert timestamps == [2.0, 2.5, 3.0, 3.5]
    for r in rows:
        assert r["jam_chord"] == "G"
        assert r["tab_chord"] == "Am"


def test_align_grid_rejects_mismatched_song_ids(store: Store) -> None:
    a_id = ingest_analysis_bundle(
        _bundle([{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]),
        store,
    )
    t_id = ingest_tab_source(
        _tab(
            [{"symbol": "C", "startSec": 0.0, "endSec": 2.0}],
            song_id="different-song",
        ),
        store,
    )
    with pytest.raises(AlignmentError, match="does not match"):
        align_grid(a_id, t_id, store)


def test_align_grid_rejects_missing_analysis(store: Store) -> None:
    t_id = ingest_tab_source(
        _tab([{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]),
        store,
    )
    with pytest.raises(AlignmentError, match="analysis not found"):
        align_grid("does-not-exist", t_id, store)


def test_align_grid_rejects_missing_tab(store: Store) -> None:
    a_id = ingest_analysis_bundle(
        _bundle([{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]),
        store,
    )
    with pytest.raises(AlignmentError, match="tab not found"):
        align_grid(a_id, "does-not-exist", store)


def test_align_grid_rejects_zero_or_negative_step(store: Store) -> None:
    chords = [{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]
    a_id = ingest_analysis_bundle(_bundle(chords), store)
    t_id = ingest_tab_source(_tab(chords), store)
    with pytest.raises(AlignmentError, match="positive"):
        align_grid(a_id, t_id, store, step_sec=0.0)
    with pytest.raises(AlignmentError, match="positive"):
        align_grid(a_id, t_id, store, step_sec=-0.5)


def test_align_grid_handles_gap_in_one_side(store: Store) -> None:
    """Tab covers [0,2) only, jam covers [0,4). Grid extends to 4.0;
    the [2,4) range has jam=C but tab=None -> disagreement rows."""
    jam = [{"symbol": "C", "startSec": 0.0, "endSec": 4.0}]
    tab = [{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]
    a_id = ingest_analysis_bundle(_bundle(jam), store)
    t_id = ingest_tab_source(_tab(tab), store)

    al_id = align_grid(a_id, t_id, store, step_sec=0.5)
    rows = store.list_disagreements_for_alignment(al_id)
    # 4 agreement points (0..1.5) + 4 disagreements (2.0..3.5).
    assert len(rows) == 4
    for r in rows:
        assert r["jam_chord"] == "C"
        assert r["tab_chord"] is None


def test_align_grid_derived_id_is_stable(store: Store) -> None:
    """Same (analysis, tab, step) re-derives the same alignment_id, so
    a future ``INSERT OR REPLACE`` layer makes alignment idempotent.
    For now strict INSERT means re-running raises on the second
    call — pin that behaviour."""
    import sqlite3

    chords = [{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]
    a_id = ingest_analysis_bundle(_bundle(chords), store)
    t_id = ingest_tab_source(_tab(chords), store)
    al_id_1 = align_grid(a_id, t_id, store)
    assert al_id_1.startswith("al_")
    with pytest.raises(sqlite3.IntegrityError):
        align_grid(a_id, t_id, store)
