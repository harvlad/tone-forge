"""Tests for ``song_validation.maintenance``.

Pin the operator housekeeping helpers: ``list_songs`` enumerates with
per-song counts, ``purge_song`` cascades manually in reverse-FK order
(the directive schema doesn't set ON DELETE CASCADE), and
``vacuum_store`` reports byte-size delta.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from song_validation import (
    Store,
    list_songs,
    purge_song,
    vacuum_store,
    validate_song,
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
    artist: str | None = None,
    title: str | None = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "song_id": song_id,
        "chords": chords,
        "sections": [],
        "key": "C major",
        "tempo": 120.0,
        "engine_version": engine_version,
    }
    if artist is not None:
        payload["artist"] = artist
    if title is not None:
        payload["title"] = title
    return payload


def _tab(
    song_id: str, progression: List[Dict[str, Any]]
) -> Dict[str, Any]:
    return {
        "song_id": song_id,
        "source": "songsterr",
        "progression": progression,
        "source_confidence": 0.9,
    }


def _seed_song(store: Store, song_id: str) -> None:
    chords = [{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]
    tab_chords = [{"symbol": "Cmaj7", "startSec": 0.0, "endSec": 2.0}]
    ingest_analysis_bundle(_bundle(song_id, chords), store)
    ingest_tab_source(_tab(song_id, tab_chords), store)
    validate_song(song_id, store)


# ----------------------------------------------------------- list_songs
def test_list_songs_empty_store(store: Store) -> None:
    assert list_songs(store) == []


def test_list_songs_returns_per_song_counts(store: Store) -> None:
    _seed_song(store, "s1")
    rows = list_songs(store)
    assert len(rows) == 1
    r = rows[0]
    assert r["song_id"] == "s1"
    assert r["analyses_count"] == 1
    assert r["tabs_count"] == 1
    assert r["alignments_count"] == 1
    # C vs Cmaj7 produces an EXTENSION_COLLAPSE disagreement.
    assert r["disagreements_count"] >= 1


def test_list_songs_orders_by_song_id_asc(store: Store) -> None:
    for sid in ("s3", "s1", "s2"):
        _seed_song(store, sid)
    rows = list_songs(store)
    assert [r["song_id"] for r in rows] == ["s1", "s2", "s3"]


def test_list_songs_respects_limit(store: Store) -> None:
    for sid in ("s1", "s2", "s3"):
        _seed_song(store, sid)
    rows = list_songs(store, limit=2)
    assert [r["song_id"] for r in rows] == ["s1", "s2"]


def test_list_songs_includes_song_metadata(store: Store) -> None:
    chords = [{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]
    ingest_analysis_bundle(
        _bundle("s1", chords, artist="X", title="Y"), store
    )
    rows = list_songs(store)
    assert rows[0]["artist"] == "X"
    assert rows[0]["title"] == "Y"


# ----------------------------------------------------------- purge_song
def test_purge_song_missing_returns_zero_deletes(store: Store) -> None:
    result = purge_song("not-here", store)
    assert result["deleted"]["songs"] == 0
    assert result["deleted"]["analyses"] == 0
    assert result["engine_versions_touched"] == []


def test_purge_song_cascades_all_child_tables(store: Store) -> None:
    _seed_song(store, "s1")
    # Sanity: rows exist.
    pre = list_songs(store)[0]
    assert pre["analyses_count"] >= 1
    assert pre["disagreements_count"] >= 1

    result = purge_song("s1", store)
    assert result["deleted"]["songs"] == 1
    assert result["deleted"]["analyses"] >= 1
    assert result["deleted"]["tabs"] >= 1
    assert result["deleted"]["alignments"] >= 1
    assert result["deleted"]["disagreements"] >= 1
    assert result["engine_versions_touched"] == ["v1.0"]

    # Store now empty.
    assert list_songs(store) == []
    with store.connect() as conn:
        for table in (
            "songs", "analysis_results", "tab_sources",
            "alignment_results", "disagreements",
        ):
            (n,) = conn.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()
            assert n == 0, f"{table} not empty after purge"


def test_purge_song_isolates_other_songs(store: Store) -> None:
    _seed_song(store, "s1")
    _seed_song(store, "s2")
    purge_song("s1", store)
    rows = list_songs(store)
    assert [r["song_id"] for r in rows] == ["s2"]
    # s2's child counts untouched.
    assert rows[0]["analyses_count"] == 1
    assert rows[0]["alignments_count"] == 1


def test_purge_song_reports_distinct_engine_versions(
    store: Store,
) -> None:
    chords = [{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]
    ingest_analysis_bundle(_bundle("s1", chords, engine_version="v1.0"), store)
    ingest_analysis_bundle(_bundle("s1", chords, engine_version="v1.1"), store)
    ingest_tab_source(_tab("s1", chords), store)
    validate_song("s1", store)

    result = purge_song("s1", store)
    assert result["engine_versions_touched"] == ["v1.0", "v1.1"]


# --------------------------------------------------------- vacuum_store
def test_vacuum_store_reports_byte_delta(
    store: Store, tmp_path: Path
) -> None:
    # Seed and purge to actually free pages.
    _seed_song(store, "s1")
    purge_song("s1", store)
    result = vacuum_store(store)
    assert result["bytes_before"] >= 0
    assert result["bytes_after"] >= 0
    # After purge + vacuum the file shouldn't have grown.
    assert result["bytes_after"] <= result["bytes_before"]
    assert (
        result["bytes_reclaimed"]
        == result["bytes_before"] - result["bytes_after"]
    )


def test_vacuum_store_idempotent_on_empty_db(store: Store) -> None:
    # Empty store still has the schema-allocated pages.
    result = vacuum_store(store)
    # No crash; numeric result.
    assert result["bytes_before"] > 0
    assert result["bytes_after"] > 0


# ----------------------------------------------------------------- CLI
def test_cli_store_list_songs_subcommand(store: Store) -> None:
    from song_validation import cli

    _seed_song(store, "s1")
    buf = io.StringIO()
    rc = cli.main(
        ["--db", str(store.db_path), "store", "list-songs"], out=buf
    )
    assert rc == 0
    payload = json.loads(buf.getvalue())
    assert payload["songs"][0]["song_id"] == "s1"


def test_cli_store_list_songs_respects_limit_flag(store: Store) -> None:
    from song_validation import cli

    for sid in ("s1", "s2", "s3"):
        _seed_song(store, sid)
    buf = io.StringIO()
    rc = cli.main(
        [
            "--db",
            str(store.db_path),
            "store",
            "list-songs",
            "--limit",
            "2",
        ],
        out=buf,
    )
    assert rc == 0
    payload = json.loads(buf.getvalue())
    assert len(payload["songs"]) == 2


def test_cli_store_purge_song_subcommand(store: Store) -> None:
    from song_validation import cli

    _seed_song(store, "s1")
    buf = io.StringIO()
    rc = cli.main(
        [
            "--db",
            str(store.db_path),
            "store",
            "purge-song",
            "s1",
        ],
        out=buf,
    )
    assert rc == 0
    payload = json.loads(buf.getvalue())
    assert payload["song_id"] == "s1"
    assert payload["deleted"]["songs"] == 1


def test_cli_store_vacuum_subcommand(store: Store) -> None:
    from song_validation import cli

    buf = io.StringIO()
    rc = cli.main(
        ["--db", str(store.db_path), "store", "vacuum"], out=buf
    )
    assert rc == 0
    payload = json.loads(buf.getvalue())
    assert "bytes_before" in payload
    assert "bytes_after" in payload
    assert "bytes_reclaimed" in payload
