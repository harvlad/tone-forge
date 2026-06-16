"""Tests for ``song_validation.reports.aligner_diff_report``.

Phase 18 introduced ``align_dtw`` as a drop-in alternative to
``align_grid``. Phase 20 tracks which aligner produced each row
via ``alignment_results.aligner_kind`` and exposes a per-song diff
so operators can spot wins/regressions song-by-song between
aligners.

These tests pin the public shape of ``aligner_diff_report`` and
its CLI smoke surface.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from song_validation import Store
from song_validation.alignment import align_dtw, align_grid
from song_validation.disagreement import classify_alignment
from song_validation.ingestion import (
    ingest_analysis_bundle,
    ingest_tab_source,
)
from song_validation.reports import aligner_diff_report


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


def _set_alignment_score(
    store: Store, alignment_id: str, score: Optional[float]
) -> None:
    with store.connect() as conn:
        conn.execute(
            "UPDATE alignment_results SET score = ? "
            "WHERE alignment_id = ?",
            (score, alignment_id),
        )


_SCORE_UNCHANGED = object()


def _seed_song_aligner(
    store: Store,
    song_id: str,
    *,
    aligner_kind: str,
    jam_chords: List[Dict[str, Any]],
    tab_chords: List[Dict[str, Any]],
    score: Any = _SCORE_UNCHANGED,
    step_sec: Optional[float] = None,
    tab_source: Optional[str] = None,
    engine_version: Optional[str] = None,
) -> str:
    """Seed one (song, aligner_kind) alignment. Returns alignment_id.

    Analyses, tabs, and alignments are all content-addressed. To keep
    multiple seeds in one test from colliding on analysis_id /
    tab_id, we vary both ``engine_version`` and ``tab_source`` per
    aligner_kind by default. Tests can override either to model the
    "same analysis aligned by both kinds" case explicitly.

    ``score=_SCORE_UNCHANGED`` (default) keeps whatever the aligner
    wrote; pass a float (including None) to override.
    """
    ev = engine_version or f"v1.0_{aligner_kind}"
    a = ingest_analysis_bundle(
        _bundle(song_id, jam_chords, engine_version=ev),
        store,
    )
    source = tab_source or f"songsterr_{aligner_kind}"
    t = ingest_tab_source(
        _tab(song_id, tab_chords, source=source), store
    )
    if aligner_kind == "grid":
        step = step_sec if step_sec is not None else 0.5
        al = align_grid(a, t, store, step_sec=step)
    elif aligner_kind == "dtw":
        step = step_sec if step_sec is not None else 0.25
        al = align_dtw(a, t, store, step_sec=step)
    else:
        raise ValueError(
            f"unknown aligner_kind for seed: {aligner_kind!r}"
        )
    classify_alignment(al, store)
    if score is not _SCORE_UNCHANGED:
        _set_alignment_score(store, al, score)
    return al


# ---------------------------------------------- shape / empty corpus
def test_aligner_diff_empty_store_returns_no_shared(
    store: Store,
) -> None:
    out = aligner_diff_report("grid", "dtw", store)
    assert out["aligner_a"] == "grid"
    assert out["aligner_b"] == "dtw"
    assert out["shared_song_count"] == 0
    assert out["a_only_songs"] == []
    assert out["b_only_songs"] == []
    assert out["songs"] == []
    assert out["improvements"] == []
    assert out["regressions"] == []
    assert out["unchanged"] == []


def test_aligner_diff_disjoint_aligners_populate_only_lists(
    store: Store,
) -> None:
    """Songs aligned by one kind only land in a_only/b_only."""
    chords = [{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]
    _seed_song_aligner(
        store, "s1", aligner_kind="grid",
        jam_chords=chords, tab_chords=chords,
    )
    _seed_song_aligner(
        store, "s2", aligner_kind="dtw",
        jam_chords=chords, tab_chords=chords,
    )
    out = aligner_diff_report("grid", "dtw", store)
    assert out["shared_song_count"] == 0
    assert out["a_only_songs"] == ["s1"]
    assert out["b_only_songs"] == ["s2"]
    assert out["songs"] == []


# ---------------------------------------------- improvements / regressions
def test_aligner_diff_classifies_improvement(store: Store) -> None:
    """DTW row with zero disagreements beats grid row with mismatches."""
    jam_bad = [{"symbol": "C", "startSec": 0.0, "endSec": 4.0}]
    tab_diff = [{"symbol": "Cmaj7", "startSec": 0.0, "endSec": 4.0}]
    # grid: jam=C vs tab=Cmaj7 -> EXTENSION_COLLAPSE every grid step
    _seed_song_aligner(
        store, "s1", aligner_kind="grid",
        jam_chords=jam_bad, tab_chords=tab_diff,
        score=0.5,
    )
    # dtw: tab matches itself (no disagreement)
    _seed_song_aligner(
        store, "s1", aligner_kind="dtw",
        jam_chords=tab_diff, tab_chords=tab_diff,
        score=0.95,
    )

    out = aligner_diff_report("grid", "dtw", store)
    assert out["shared_song_count"] == 1
    assert out["a_only_songs"] == []
    assert out["b_only_songs"] == []
    assert out["improvements"] == ["s1"]
    assert out["regressions"] == []
    assert out["unchanged"] == []

    assert len(out["songs"]) == 1
    row = out["songs"][0]
    assert row["song_id"] == "s1"
    assert row["a_score"] == pytest.approx(0.5)
    assert row["b_score"] == pytest.approx(0.95)
    assert row["score_delta"] == pytest.approx(0.45)
    assert row["a_disagreement_count"] >= 1
    assert row["b_disagreement_count"] == 0
    assert row["disagreement_delta"] < 0


def test_aligner_diff_classifies_regression(store: Store) -> None:
    """DTW introduces disagreements where grid had none."""
    jam = [{"symbol": "C", "startSec": 0.0, "endSec": 4.0}]
    tab_diff = [{"symbol": "Cmaj7", "startSec": 0.0, "endSec": 4.0}]
    # grid: jam matches tab -> 0 disagreements
    _seed_song_aligner(
        store, "s1", aligner_kind="grid",
        jam_chords=jam, tab_chords=jam,
        score=0.95,
    )
    # dtw: jam != tab -> disagreements
    _seed_song_aligner(
        store, "s1", aligner_kind="dtw",
        jam_chords=jam, tab_chords=tab_diff,
        score=0.5,
    )
    out = aligner_diff_report("grid", "dtw", store)
    assert out["regressions"] == ["s1"]
    assert out["improvements"] == []
    assert out["unchanged"] == []
    row = out["songs"][0]
    assert row["disagreement_delta"] > 0
    assert row["score_delta"] == pytest.approx(-0.45)


def test_aligner_diff_classifies_unchanged(store: Store) -> None:
    """Same disagreement count under both aligners -> unchanged."""
    jam = [{"symbol": "C", "startSec": 0.0, "endSec": 4.0}]
    tab_diff = [{"symbol": "Cmaj7", "startSec": 0.0, "endSec": 4.0}]
    # Force matching step so both aligners visit the same number of
    # grid points; otherwise dtw's finer default step inflates its
    # disagreement count for the same input.
    _seed_song_aligner(
        store, "s1", aligner_kind="grid",
        jam_chords=jam, tab_chords=tab_diff,
        step_sec=0.5,
        score=0.7,
    )
    _seed_song_aligner(
        store, "s1", aligner_kind="dtw",
        jam_chords=jam, tab_chords=tab_diff,
        step_sec=0.5,
        score=0.7,
    )
    out = aligner_diff_report("grid", "dtw", store)
    assert out["unchanged"] == ["s1"]
    assert out["improvements"] == []
    assert out["regressions"] == []
    row = out["songs"][0]
    assert row["disagreement_delta"] == 0
    assert row["score_delta"] == pytest.approx(0.0)


# ---------------------------------------------- ordering / limits
def test_aligner_diff_orders_by_disagreement_delta_then_song_id(
    store: Store,
) -> None:
    """Most-improved first (largest negative delta), ties by song_id."""
    jam_bad = [{"symbol": "C", "startSec": 0.0, "endSec": 4.0}]
    tab_diff = [{"symbol": "Cmaj7", "startSec": 0.0, "endSec": 4.0}]
    # s_improve: grid bad, dtw perfect
    _seed_song_aligner(
        store, "s_improve", aligner_kind="grid",
        jam_chords=jam_bad, tab_chords=tab_diff,
    )
    _seed_song_aligner(
        store, "s_improve", aligner_kind="dtw",
        jam_chords=tab_diff, tab_chords=tab_diff,
    )
    # s_regress: grid perfect, dtw bad
    _seed_song_aligner(
        store, "s_regress", aligner_kind="grid",
        jam_chords=tab_diff, tab_chords=tab_diff,
    )
    _seed_song_aligner(
        store, "s_regress", aligner_kind="dtw",
        jam_chords=jam_bad, tab_chords=tab_diff,
    )
    # Two unchanged songs.
    for sid in ("s_unchanged_b", "s_unchanged_a"):
        _seed_song_aligner(
            store, sid, aligner_kind="grid",
            jam_chords=jam_bad, tab_chords=tab_diff,
        )
        _seed_song_aligner(
            store, sid, aligner_kind="dtw",
            jam_chords=jam_bad, tab_chords=tab_diff,
        )

    out = aligner_diff_report("grid", "dtw", store)
    ids = [r["song_id"] for r in out["songs"]]
    assert ids[0] == "s_improve"
    assert ids[-1] == "s_regress"
    middle = ids[1:-1]
    assert middle == sorted(middle)


def test_aligner_diff_limit_caps_songs_but_not_buckets(
    store: Store,
) -> None:
    chords = [{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]
    for sid in ("s1", "s2", "s3"):
        _seed_song_aligner(
            store, sid, aligner_kind="grid",
            jam_chords=chords, tab_chords=chords,
        )
        _seed_song_aligner(
            store, sid, aligner_kind="dtw",
            jam_chords=chords, tab_chords=chords,
        )
    out = aligner_diff_report("grid", "dtw", store, limit=2)
    assert len(out["songs"]) == 2
    # Bucket lists still complete.
    assert set(out["unchanged"]) == {"s1", "s2", "s3"}


# ---------------------------------------------- null score handling
def test_aligner_diff_null_score_yields_none_score_delta(
    store: Store,
) -> None:
    jam = [{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]
    _seed_song_aligner(
        store, "s1", aligner_kind="grid",
        jam_chords=jam, tab_chords=jam,
        score=None,
    )
    _seed_song_aligner(
        store, "s1", aligner_kind="dtw",
        jam_chords=jam, tab_chords=jam,
        score=0.8,
    )
    out = aligner_diff_report("grid", "dtw", store)
    row = out["songs"][0]
    assert row["a_score"] is None
    assert row["b_score"] == pytest.approx(0.8)
    assert row["score_delta"] is None


def test_aligner_diff_picks_best_alignment_per_aligner(
    store: Store,
) -> None:
    """When a song has multiple alignments under one aligner, the
    highest-score one is used."""
    jam = [{"symbol": "C", "startSec": 0.0, "endSec": 4.0}]
    tab_diff = [{"symbol": "Cmaj7", "startSec": 0.0, "endSec": 4.0}]
    # First grid alignment (low score).
    _seed_song_aligner(
        store, "s1", aligner_kind="grid",
        jam_chords=jam, tab_chords=tab_diff,
        score=0.2,
    )
    # Second grid alignment using a distinct analysis bundle so the
    # content-addressed analysis_id differs.
    jam_alt = [{"symbol": "C", "startSec": 0.0, "endSec": 3.5}]
    a2 = ingest_analysis_bundle(
        _bundle("s1", jam_alt, engine_version="v1.0_grid"), store
    )
    t2 = ingest_tab_source(
        _tab(
            "s1",
            [{"symbol": "C", "startSec": 0.0, "endSec": 3.5}],
            source="songsterr_grid_alt",
        ),
        store,
    )
    al2 = align_grid(a2, t2, store, step_sec=0.5)
    classify_alignment(al2, store)
    _set_alignment_score(store, al2, 0.9)
    # Single DTW alignment.
    _seed_song_aligner(
        store, "s1", aligner_kind="dtw",
        jam_chords=jam, tab_chords=tab_diff,
        score=0.7,
    )
    out = aligner_diff_report("grid", "dtw", store)
    row = out["songs"][0]
    # Best grid score = 0.9; dtw = 0.7; delta = -0.2.
    assert row["a_score"] == pytest.approx(0.9)
    assert row["b_score"] == pytest.approx(0.7)
    assert row["score_delta"] == pytest.approx(-0.2)


# ---------------------------------------------- aligner_kind column wiring
def test_align_grid_writes_aligner_kind_grid(store: Store) -> None:
    chords = [{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]
    al = _seed_song_aligner(
        store, "s1", aligner_kind="grid",
        jam_chords=chords, tab_chords=chords,
    )
    row = store.get_alignment_result(al)
    assert row is not None
    assert row["aligner_kind"] == "grid"


def test_align_dtw_writes_aligner_kind_dtw(store: Store) -> None:
    chords = [{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]
    al = _seed_song_aligner(
        store, "s1", aligner_kind="dtw",
        jam_chords=chords, tab_chords=chords,
    )
    row = store.get_alignment_result(al)
    assert row is not None
    assert row["aligner_kind"] == "dtw"


# ---------------------------------------------- CLI smoke
def test_cli_report_aligner_diff_subcommand(store: Store) -> None:
    from song_validation import cli

    chords = [{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]
    _seed_song_aligner(
        store, "s1", aligner_kind="grid",
        jam_chords=chords, tab_chords=chords,
    )
    _seed_song_aligner(
        store, "s1", aligner_kind="dtw",
        jam_chords=chords, tab_chords=chords,
    )
    buf = io.StringIO()
    rc = cli.main(
        [
            "--db",
            str(store.db_path),
            "report",
            "aligner-diff",
            "grid",
            "dtw",
        ],
        out=buf,
    )
    assert rc == 0
    payload = json.loads(buf.getvalue())
    assert payload["aligner_a"] == "grid"
    assert payload["aligner_b"] == "dtw"
    assert payload["shared_song_count"] == 1


def test_cli_report_aligner_diff_limit_flag(store: Store) -> None:
    from song_validation import cli

    chords = [{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]
    for sid in ("s1", "s2", "s3"):
        _seed_song_aligner(
            store, sid, aligner_kind="grid",
            jam_chords=chords, tab_chords=chords,
        )
        _seed_song_aligner(
            store, sid, aligner_kind="dtw",
            jam_chords=chords, tab_chords=chords,
        )
    buf = io.StringIO()
    rc = cli.main(
        [
            "--db",
            str(store.db_path),
            "report",
            "aligner-diff",
            "grid",
            "dtw",
            "--limit",
            "2",
        ],
        out=buf,
    )
    assert rc == 0
    payload = json.loads(buf.getvalue())
    assert len(payload["songs"]) == 2
    assert set(payload["unchanged"]) == {"s1", "s2", "s3"}
