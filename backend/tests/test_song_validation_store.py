"""Tests for the song-validation SQLite store.

Covers schema creation idempotency, song upsert behaviour (later
metadata fills in gaps left by an earlier upload), and
analysis-result round-trip (JSON columns rehydrate as Python lists).

The store is intentionally thin in this first commit; these tests
pin the contract the ingestion module + future alignment/metrics
modules will build on.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from song_validation import Store
from song_validation.disagreement import DisagreementClass


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(db_path=tmp_path / "sv.db")


def test_schema_creation_is_idempotent(tmp_path: Path) -> None:
    """Constructing a second Store on the same path is a no-op."""
    p = tmp_path / "sv.db"
    Store(db_path=p)
    Store(db_path=p)  # should not raise
    # And the schema rows are queryable.
    with Store(db_path=p).connect() as conn:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
    expected = {
        "songs",
        "analysis_results",
        "tab_sources",
        "alignment_results",
        "disagreements",
        "engine_metrics",
    }
    assert expected <= names, f"missing tables: {expected - names}"


def test_upsert_song_inserts_then_fills_in_gaps(store: Store) -> None:
    """First upsert inserts; second upsert fills in None fields
    without clobbering existing non-None values."""
    store.upsert_song(song_id="s1", artist=None, title=None, duration=None)
    assert store.get_song("s1") == {
        "song_id": "s1",
        "artist": None,
        "title": None,
        "duration": None,
    }
    # Second upsert adds metadata.
    store.upsert_song(song_id="s1", artist="Foo", title="Bar", duration=123.4)
    assert store.get_song("s1") == {
        "song_id": "s1",
        "artist": "Foo",
        "title": "Bar",
        "duration": 123.4,
    }
    # Third upsert with only title=None must NOT erase the existing title.
    store.upsert_song(song_id="s1", artist=None, title=None, duration=None)
    assert store.get_song("s1") == {
        "song_id": "s1",
        "artist": "Foo",
        "title": "Bar",
        "duration": 123.4,
    }


def test_insert_analysis_result_roundtrips_json_columns(store: Store) -> None:
    """chords + sections are stored as JSON; round-trip must rehydrate
    them as Python lists so downstream consumers don't need to know
    about the storage representation."""
    store.upsert_song(song_id="s1", artist="Foo", title="Bar", duration=100.0)
    chords = [
        {"symbol": "C", "startSec": 0.0, "endSec": 2.0},
        {"symbol": "Am", "startSec": 2.0, "endSec": 4.0},
    ]
    sections = [{"label": "Verse", "startSec": 0.0, "endSec": 4.0}]
    store.insert_analysis_result(
        analysis_id="a1",
        song_id="s1",
        engine_version="v1.0",
        chords=chords,
        sections=sections,
        tempo=120.0,
        key="C major",
        created_at="2026-06-15T00:00:00+00:00",
    )
    got = store.get_analysis_result("a1")
    assert got is not None
    assert got["analysis_id"] == "a1"
    assert got["engine_version"] == "v1.0"
    assert got["chords"] == chords
    assert got["sections"] == sections
    assert got["tempo"] == 120.0
    assert got["key"] == "C major"


def test_insert_analysis_result_requires_existing_song(store: Store) -> None:
    """Foreign-key constraint blocks an analysis row whose song_id
    isn't in the songs table."""
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        store.insert_analysis_result(
            analysis_id="a1",
            song_id="missing",
            engine_version="v1.0",
            chords=[],
            sections=[],
            tempo=120.0,
            key="C",
            created_at="2026-06-15T00:00:00+00:00",
        )


def test_disagreement_class_enum_values_match_directive() -> None:
    """The directive's taxonomy is part of the public contract — pin
    the exact label strings so a typo in the enum surfaces here."""
    assert {c.value for c in DisagreementClass} == {
        "BOUNDARY_ERROR",
        "EXTENSION_COLLAPSE",
        "SLASH_CHORD_COLLAPSE",
        "KEY_CONTEXT_ERROR",
        "LIKELY_TAB_ERROR",
        "UNKNOWN",
    }
    # Subclassing str makes instances JSON-friendly.
    assert json.dumps(DisagreementClass.BOUNDARY_ERROR) == '"BOUNDARY_ERROR"'
