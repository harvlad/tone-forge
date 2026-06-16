"""Tests for ``song_validation.reports.engine_version_song_diff``.

The roll-up ``engine_version_diff`` answers "did v1.1 improve overall?"
but hides per-song regressions. ``engine_version_song_diff`` pins the
shape the operator needs to spot wins and regressions song-by-song.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from song_validation import Store
from song_validation.alignment import align_grid
from song_validation.disagreement import classify_alignment
from song_validation.ingestion import (
    ingest_analysis_bundle,
    ingest_tab_source,
)
from song_validation.reports import engine_version_song_diff


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


def _seed_song_version(
    store: Store,
    song_id: str,
    *,
    engine_version: str,
    jam_chords: List[Dict[str, Any]],
    tab_chords: List[Dict[str, Any]],
    score: Any = _SCORE_UNCHANGED,
    step_sec: float = 0.5,
    tab_source: Optional[str] = None,
) -> str:
    """Seed one (song, version) alignment. Returns alignment_id.

    Tab ingestion is content-addressed; passing ``tab_source`` (or
    relying on the per-version default) keeps tabs distinct so the
    same song under two versions doesn't collide on tab_id.

    ``score`` defaults to the sentinel ``_SCORE_UNCHANGED``: leaves
    the value that ``align_grid`` wrote. Pass an explicit float or
    ``None`` to override (including overriding to NULL).
    """
    a = ingest_analysis_bundle(
        _bundle(song_id, jam_chords, engine_version=engine_version),
        store,
    )
    source = tab_source or f"songsterr_{engine_version}"
    t = ingest_tab_source(
        _tab(song_id, tab_chords, source=source), store
    )
    al = align_grid(a, t, store, step_sec=step_sec)
    classify_alignment(al, store)
    if score is not _SCORE_UNCHANGED:
        _set_alignment_score(store, al, score)
    return al


# ---------------------------------------------- shape / empty corpus
def test_engine_song_diff_empty_store_returns_no_shared(
    store: Store,
) -> None:
    out = engine_version_song_diff("v1.0", "v1.1", store)
    assert out["version_a"] == "v1.0"
    assert out["version_b"] == "v1.1"
    assert out["shared_song_count"] == 0
    assert out["a_only_songs"] == []
    assert out["b_only_songs"] == []
    assert out["songs"] == []
    assert out["improvements"] == []
    assert out["regressions"] == []
    assert out["unchanged"] == []


def test_engine_song_diff_disjoint_versions_populate_only_lists(
    store: Store,
) -> None:
    """Songs analysed under one version only land in a_only/b_only."""
    chords = [{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]
    _seed_song_version(
        store, "s1",
        engine_version="v1.0",
        jam_chords=chords, tab_chords=chords,
    )
    _seed_song_version(
        store, "s2",
        engine_version="v1.1",
        jam_chords=chords, tab_chords=chords,
    )
    out = engine_version_song_diff("v1.0", "v1.1", store)
    assert out["shared_song_count"] == 0
    assert out["a_only_songs"] == ["s1"]
    assert out["b_only_songs"] == ["s2"]
    assert out["songs"] == []


# ---------------------------------------------- improvements / regressions
def test_engine_song_diff_classifies_improvement(store: Store) -> None:
    """v1.1 with zero disagreements beats v1.0 with EXTENSION_COLLAPSE."""
    jam_bad = [{"symbol": "C", "startSec": 0.0, "endSec": 4.0}]
    tab_perfect = [{"symbol": "Cmaj7", "startSec": 0.0, "endSec": 4.0}]
    # v1.0: jam=C vs tab=Cmaj7 -> EXTENSION_COLLAPSE per grid step
    _seed_song_version(
        store, "s1",
        engine_version="v1.0",
        jam_chords=jam_bad, tab_chords=tab_perfect,
        score=0.5,
    )
    # v1.1: jam matches tab -> 0 disagreements
    _seed_song_version(
        store, "s1",
        engine_version="v1.1",
        jam_chords=tab_perfect, tab_chords=tab_perfect,
        score=0.95,
    )

    out = engine_version_song_diff("v1.0", "v1.1", store)
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


def test_engine_song_diff_classifies_regression(store: Store) -> None:
    """v1.1 introduces disagreements where v1.0 had none."""
    jam = [{"symbol": "C", "startSec": 0.0, "endSec": 4.0}]
    tab = [{"symbol": "Cmaj7", "startSec": 0.0, "endSec": 4.0}]
    _seed_song_version(
        store, "s1",
        engine_version="v1.0",
        jam_chords=jam, tab_chords=jam,  # perfect
        score=0.95,
    )
    _seed_song_version(
        store, "s1",
        engine_version="v1.1",
        jam_chords=jam, tab_chords=tab,  # EXTENSION_COLLAPSE
        score=0.5,
    )
    out = engine_version_song_diff("v1.0", "v1.1", store)
    assert out["regressions"] == ["s1"]
    assert out["improvements"] == []
    assert out["unchanged"] == []
    row = out["songs"][0]
    assert row["disagreement_delta"] > 0
    assert row["score_delta"] == pytest.approx(-0.45)


def test_engine_song_diff_classifies_unchanged(store: Store) -> None:
    """Same disagreement count under both versions -> unchanged bucket."""
    jam = [{"symbol": "C", "startSec": 0.0, "endSec": 4.0}]
    tab = [{"symbol": "Cmaj7", "startSec": 0.0, "endSec": 4.0}]
    _seed_song_version(
        store, "s1",
        engine_version="v1.0",
        jam_chords=jam, tab_chords=tab,
        score=0.7,
    )
    _seed_song_version(
        store, "s1",
        engine_version="v1.1",
        jam_chords=jam, tab_chords=tab,
        score=0.7,
    )
    out = engine_version_song_diff("v1.0", "v1.1", store)
    assert out["unchanged"] == ["s1"]
    assert out["improvements"] == []
    assert out["regressions"] == []
    row = out["songs"][0]
    assert row["disagreement_delta"] == 0
    assert row["score_delta"] == pytest.approx(0.0)


# ---------------------------------------------- ordering / limits
def test_engine_song_diff_orders_by_disagreement_delta_then_song_id(
    store: Store,
) -> None:
    """Most-improved first (largest negative delta), ties by song_id."""
    jam_bad = [{"symbol": "C", "startSec": 0.0, "endSec": 4.0}]
    tab_diff = [{"symbol": "Cmaj7", "startSec": 0.0, "endSec": 4.0}]
    # s_improve: v1.0 bad, v1.1 perfect (negative delta)
    _seed_song_version(
        store, "s_improve",
        engine_version="v1.0",
        jam_chords=jam_bad, tab_chords=tab_diff,
    )
    _seed_song_version(
        store, "s_improve",
        engine_version="v1.1",
        jam_chords=tab_diff, tab_chords=tab_diff,
    )
    # s_regress: v1.0 perfect, v1.1 bad (positive delta)
    _seed_song_version(
        store, "s_regress",
        engine_version="v1.0",
        jam_chords=tab_diff, tab_chords=tab_diff,
    )
    _seed_song_version(
        store, "s_regress",
        engine_version="v1.1",
        jam_chords=jam_bad, tab_chords=tab_diff,
    )
    # s_unchanged_a / s_unchanged_b: identical zero-delta pair
    for sid in ("s_unchanged_b", "s_unchanged_a"):
        _seed_song_version(
            store, sid,
            engine_version="v1.0",
            jam_chords=jam_bad, tab_chords=tab_diff,
        )
        _seed_song_version(
            store, sid,
            engine_version="v1.1",
            jam_chords=jam_bad, tab_chords=tab_diff,
        )

    out = engine_version_song_diff("v1.0", "v1.1", store)
    ids = [r["song_id"] for r in out["songs"]]
    # s_improve first (most negative delta), then unchanged tied at 0
    # alphabetically (s_unchanged_a < s_unchanged_b), then s_regress.
    assert ids[0] == "s_improve"
    assert ids[-1] == "s_regress"
    # Ties in the middle ordered alphabetically.
    middle = ids[1:-1]
    assert middle == sorted(middle)


def test_engine_song_diff_limit_caps_songs_but_not_buckets(
    store: Store,
) -> None:
    chords = [{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]
    for sid in ("s1", "s2", "s3"):
        _seed_song_version(
            store, sid,
            engine_version="v1.0",
            jam_chords=chords, tab_chords=chords,
        )
        _seed_song_version(
            store, sid,
            engine_version="v1.1",
            jam_chords=chords, tab_chords=chords,
        )
    out = engine_version_song_diff("v1.0", "v1.1", store, limit=2)
    assert len(out["songs"]) == 2
    # Bucket lists still complete.
    assert set(out["unchanged"]) == {"s1", "s2", "s3"}


# ---------------------------------------------- null score handling
def test_engine_song_diff_null_score_yields_none_score_delta(
    store: Store,
) -> None:
    jam = [{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]
    _seed_song_version(
        store, "s1",
        engine_version="v1.0",
        jam_chords=jam, tab_chords=jam,
        score=None,  # null
    )
    _seed_song_version(
        store, "s1",
        engine_version="v1.1",
        jam_chords=jam, tab_chords=jam,
        score=0.8,
    )
    out = engine_version_song_diff("v1.0", "v1.1", store)
    row = out["songs"][0]
    assert row["a_score"] is None
    assert row["b_score"] == pytest.approx(0.8)
    assert row["score_delta"] is None


def test_engine_song_diff_picks_best_alignment_per_version(
    store: Store,
) -> None:
    """When a song has multiple alignments under one version, the
    highest-score one is used."""
    jam = [{"symbol": "C", "startSec": 0.0, "endSec": 4.0}]
    tab = [{"symbol": "Cmaj7", "startSec": 0.0, "endSec": 4.0}]
    # Two alignments under v1.0 — one low score, one high.
    _seed_song_version(
        store, "s1",
        engine_version="v1.0",
        jam_chords=jam, tab_chords=tab,
        score=0.2,
    )
    # Second v1.0 analysis: different chord window to produce a
    # distinct content-addressed analysis_id (and thus a distinct
    # alignment row when aligned to a tab).
    jam_alt = [{"symbol": "C", "startSec": 0.0, "endSec": 3.5}]
    a2 = ingest_analysis_bundle(
        _bundle("s1", jam_alt, engine_version="v1.0"), store
    )
    t2 = ingest_tab_source(
        _tab(
            "s1",
            [{"symbol": "C", "startSec": 0.0, "endSec": 3.5}],
            source="songsterr_v1.0_alt",
        ),
        store,
    )
    al2 = align_grid(a2, t2, store, step_sec=0.5)
    classify_alignment(al2, store)
    _set_alignment_score(store, al2, 0.9)
    # v1.1 single alignment.
    _seed_song_version(
        store, "s1",
        engine_version="v1.1",
        jam_chords=jam, tab_chords=tab,
        score=0.7,
    )
    out = engine_version_song_diff("v1.0", "v1.1", store)
    row = out["songs"][0]
    # Best (highest) v1.0 score is 0.9; b_score is 0.7; delta -0.2.
    assert row["a_score"] == pytest.approx(0.9)
    assert row["b_score"] == pytest.approx(0.7)
    assert row["score_delta"] == pytest.approx(-0.2)


# ---------------------------------------------- CLI smoke
def test_cli_report_engine_song_diff_subcommand(store: Store) -> None:
    from song_validation import cli

    chords = [{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]
    _seed_song_version(
        store, "s1",
        engine_version="v1.0",
        jam_chords=chords, tab_chords=chords,
    )
    _seed_song_version(
        store, "s1",
        engine_version="v1.1",
        jam_chords=chords, tab_chords=chords,
    )
    buf = io.StringIO()
    rc = cli.main(
        [
            "--db",
            str(store.db_path),
            "report",
            "engine-song-diff",
            "v1.0",
            "v1.1",
        ],
        out=buf,
    )
    assert rc == 0
    payload = json.loads(buf.getvalue())
    assert payload["version_a"] == "v1.0"
    assert payload["version_b"] == "v1.1"
    assert payload["shared_song_count"] == 1


def test_cli_report_engine_song_diff_limit_flag(store: Store) -> None:
    from song_validation import cli

    chords = [{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]
    for sid in ("s1", "s2", "s3"):
        _seed_song_version(
            store, sid,
            engine_version="v1.0",
            jam_chords=chords, tab_chords=chords,
        )
        _seed_song_version(
            store, sid,
            engine_version="v1.1",
            jam_chords=chords, tab_chords=chords,
        )
    buf = io.StringIO()
    rc = cli.main(
        [
            "--db",
            str(store.db_path),
            "report",
            "engine-song-diff",
            "v1.0",
            "v1.1",
            "--limit",
            "2",
        ],
        out=buf,
    )
    assert rc == 0
    payload = json.loads(buf.getvalue())
    assert len(payload["songs"]) == 2
    # Buckets still complete despite limit.
    assert set(payload["unchanged"]) == {"s1", "s2", "s3"}
