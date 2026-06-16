"""Tests for ``song_validation.reports.temporal_trends``.

Pins the time-windowed reports: bucketing by day / week / month /
hour, since/until filtering, breakdown by classification or engine
version, and the empty-corpus / no-data edge cases.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from song_validation import Store
from song_validation.alignment import align_grid
from song_validation.disagreement import classify_alignment
from song_validation.ingestion import (
    ingest_analysis_bundle,
    ingest_tab_source,
)
from song_validation.reports import (
    TemporalReportError,
    disagreement_trends_over_time,
    ingestion_trends_over_time,
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
) -> Dict[str, Any]:
    return {
        "song_id": song_id,
        "source": source,
        "progression": progression,
        "source_confidence": 0.9,
    }


def _stamp_analysis_created_at(
    store: Store, analysis_id: str, iso: str
) -> None:
    """Backdate an analysis row so we can test bucketing across days."""
    with store.connect() as conn:
        conn.execute(
            "UPDATE analysis_results SET created_at = ? "
            "WHERE analysis_id = ?",
            (iso, analysis_id),
        )


def _seed_disagreement_on(
    store: Store,
    song_id: str,
    iso: str,
    *,
    jam_chord: str = "C",
    tab_chord: str = "Cmaj7",
    engine_version: str = "v1.0",
    source: str = "songsterr",
) -> None:
    jam = [{"symbol": jam_chord, "startSec": 0.0, "endSec": 2.0}]
    tab = [{"symbol": tab_chord, "startSec": 0.0, "endSec": 2.0}]
    a = ingest_analysis_bundle(
        _bundle(song_id, jam, engine_version=engine_version), store
    )
    t = ingest_tab_source(_tab(song_id, tab, source=source), store)
    al = align_grid(a, t, store, step_sec=0.5)
    classify_alignment(al, store)
    _stamp_analysis_created_at(store, a, iso)


# ----------------------------------------- bucket arg validation
def test_disagreement_trends_rejects_unknown_bucket(store: Store) -> None:
    with pytest.raises(TemporalReportError):
        disagreement_trends_over_time(store, bucket="fortnight")


def test_ingestion_trends_rejects_unknown_bucket(store: Store) -> None:
    with pytest.raises(TemporalReportError):
        ingestion_trends_over_time(store, bucket="fortnight")


# ----------------------------------------- empty corpus
def test_disagreement_trends_empty_corpus_returns_zero(
    store: Store,
) -> None:
    out = disagreement_trends_over_time(store)
    assert out["bucket"] == "day"
    assert out["since"] is None and out["until"] is None
    assert out["total_disagreements"] == 0
    assert out["buckets"] == []


def test_ingestion_trends_empty_corpus_returns_zero(store: Store) -> None:
    out = ingestion_trends_over_time(store)
    assert out["bucket"] == "day"
    assert out["total_analyses"] == 0
    assert out["distinct_songs"] == 0
    assert out["buckets"] == []


# ----------------------------------------- disagreement bucketing
def test_disagreement_trends_buckets_by_day(store: Store) -> None:
    _seed_disagreement_on(
        store, "s1", "2025-01-01T10:00:00+00:00"
    )
    _seed_disagreement_on(
        store, "s2", "2025-01-02T10:00:00+00:00"
    )
    out = disagreement_trends_over_time(store, bucket="day")
    labels = [b["bucket"] for b in out["buckets"]]
    assert "2025-01-01" in labels
    assert "2025-01-02" in labels
    # Chronological ordering.
    assert labels == sorted(labels)
    # Every bucket has at least one classified disagreement.
    for b in out["buckets"]:
        assert b["total"] >= 1
        assert sum(b["by_class"].values()) == b["total"]


def test_disagreement_trends_breaks_down_by_class(store: Store) -> None:
    # Two days, both with EXTENSION_COLLAPSE (C vs Cmaj7).
    _seed_disagreement_on(store, "s1", "2025-01-01T10:00:00+00:00")
    _seed_disagreement_on(store, "s2", "2025-01-01T11:00:00+00:00")
    out = disagreement_trends_over_time(store, bucket="day")
    by_class = out["buckets"][0]["by_class"]
    # EXTENSION_COLLAPSE should dominate.
    assert "EXTENSION_COLLAPSE" in by_class
    assert by_class["EXTENSION_COLLAPSE"] >= 1
    # Total disagreements equals the sum across all classes / buckets.
    assert out["total_disagreements"] == sum(
        b["total"] for b in out["buckets"]
    )


def test_disagreement_trends_respects_since_filter(store: Store) -> None:
    _seed_disagreement_on(store, "s1", "2025-01-01T10:00:00+00:00")
    _seed_disagreement_on(store, "s2", "2025-01-05T10:00:00+00:00")
    out = disagreement_trends_over_time(
        store, bucket="day", since="2025-01-03"
    )
    labels = [b["bucket"] for b in out["buckets"]]
    assert "2025-01-01" not in labels
    assert "2025-01-05" in labels


def test_disagreement_trends_respects_until_filter(store: Store) -> None:
    _seed_disagreement_on(store, "s1", "2025-01-01T10:00:00+00:00")
    _seed_disagreement_on(store, "s2", "2025-01-05T10:00:00+00:00")
    out = disagreement_trends_over_time(
        store, bucket="day", until="2025-01-03"
    )
    labels = [b["bucket"] for b in out["buckets"]]
    assert "2025-01-01" in labels
    assert "2025-01-05" not in labels


def test_disagreement_trends_month_bucket(store: Store) -> None:
    _seed_disagreement_on(store, "s1", "2025-01-15T10:00:00+00:00")
    _seed_disagreement_on(store, "s2", "2025-02-15T10:00:00+00:00")
    out = disagreement_trends_over_time(store, bucket="month")
    labels = [b["bucket"] for b in out["buckets"]]
    assert labels == ["2025-01", "2025-02"]


# ----------------------------------------- ingestion bucketing
def test_ingestion_trends_buckets_by_day(store: Store) -> None:
    _seed_disagreement_on(store, "s1", "2025-01-01T10:00:00+00:00")
    _seed_disagreement_on(store, "s2", "2025-01-02T10:00:00+00:00")
    out = ingestion_trends_over_time(store, bucket="day")
    assert out["total_analyses"] == 2
    assert out["distinct_songs"] == 2
    labels = [b["bucket"] for b in out["buckets"]]
    assert labels == ["2025-01-01", "2025-01-02"]


def test_ingestion_trends_splits_by_engine_version(store: Store) -> None:
    _seed_disagreement_on(
        store, "s1", "2025-01-01T10:00:00+00:00",
        engine_version="v1.0",
    )
    _seed_disagreement_on(
        store, "s2", "2025-01-01T11:00:00+00:00",
        engine_version="v1.1",
    )
    out = ingestion_trends_over_time(store, bucket="day")
    assert len(out["buckets"]) == 1
    by_v = out["buckets"][0]["by_engine_version"]
    assert by_v["v1.0"] == 1
    assert by_v["v1.1"] == 1


def test_ingestion_trends_distinct_songs_counts_uniquely(
    store: Store,
) -> None:
    # Same song, two engine versions on the same day. distinct_songs=1.
    _seed_disagreement_on(
        store, "s1", "2025-01-01T10:00:00+00:00",
        engine_version="v1.0",
    )
    _seed_disagreement_on(
        store, "s1", "2025-01-01T11:00:00+00:00",
        engine_version="v1.1",
        source="songsterr_v2",
    )
    out = ingestion_trends_over_time(store, bucket="day")
    bucket = out["buckets"][0]
    assert bucket["analyses_count"] == 2
    assert bucket["distinct_songs"] == 1
    assert out["distinct_songs"] == 1


# ----------------------------------------- CLI smoke
def test_cli_report_trends_disagreements_subcommand(
    store: Store,
) -> None:
    from song_validation import cli

    _seed_disagreement_on(store, "s1", "2025-01-01T10:00:00+00:00")
    buf = io.StringIO()
    rc = cli.main(
        [
            "--db",
            str(store.db_path),
            "report",
            "trends-disagreements",
            "--bucket",
            "day",
        ],
        out=buf,
    )
    assert rc == 0
    payload = json.loads(buf.getvalue())
    assert payload["bucket"] == "day"
    assert payload["total_disagreements"] >= 1


def test_cli_report_trends_ingestion_subcommand(store: Store) -> None:
    from song_validation import cli

    _seed_disagreement_on(store, "s1", "2025-01-01T10:00:00+00:00")
    buf = io.StringIO()
    rc = cli.main(
        [
            "--db",
            str(store.db_path),
            "report",
            "trends-ingestion",
            "--bucket",
            "day",
        ],
        out=buf,
    )
    assert rc == 0
    payload = json.loads(buf.getvalue())
    assert payload["bucket"] == "day"
    assert payload["total_analyses"] >= 1


def test_cli_report_trends_disagreements_window_flags(
    store: Store,
) -> None:
    from song_validation import cli

    _seed_disagreement_on(store, "s1", "2025-01-01T10:00:00+00:00")
    _seed_disagreement_on(store, "s2", "2025-01-05T10:00:00+00:00")
    buf = io.StringIO()
    rc = cli.main(
        [
            "--db",
            str(store.db_path),
            "report",
            "trends-disagreements",
            "--since",
            "2025-01-03",
            "--until",
            "2025-01-10",
        ],
        out=buf,
    )
    assert rc == 0
    payload = json.loads(buf.getvalue())
    labels = [b["bucket"] for b in payload["buckets"]]
    assert "2025-01-01" not in labels
    assert "2025-01-05" in labels
