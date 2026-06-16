"""Tests for ``song_validation.reports``.

These pin the four corpus-level reports the directive lists as the
"success state" surface. Each test sets up a small synthetic corpus
end-to-end (ingest -> align -> classify -> aggregate) and asserts the
report dict shape and content.
"""

from __future__ import annotations

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
from song_validation.metrics import aggregate_metrics
from song_validation.reports import (
    dominant_failure_class,
    engine_version_diff,
    where_are_tabs_wrong,
    where_is_jam_wrong,
)


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(db_path=tmp_path / "sv.db")


def _seed_extension_collapse_corpus(
    store: Store, engine_version: str = "v1.0"
) -> str:
    """Seed: jam=C vs tab=Cmaj7 across one song -> 8 EXTENSION_COLLAPSE
    rows."""
    jam = [{"symbol": "C", "startSec": 0.0, "endSec": 4.0}]
    tab = [{"symbol": "Cmaj7", "startSec": 0.0, "endSec": 4.0}]
    a = ingest_analysis_bundle(
        {
            "song_id": "s1",
            "chords": jam,
            "sections": [],
            "key": "C major",
            "tempo": 120.0,
            "engine_version": engine_version,
        },
        store,
    )
    t = ingest_tab_source(
        {"song_id": "s1", "source": "songsterr", "progression": tab},
        store,
    )
    al = align_grid(a, t, store, step_sec=0.5)
    classify_alignment(al, store)
    return al


def test_where_is_jam_wrong_empty_corpus(store: Store) -> None:
    report = where_is_jam_wrong(store)
    assert report == {"total_disagreements": 0, "ranked": []}


def test_where_is_jam_wrong_ranks_classes(store: Store) -> None:
    _seed_extension_collapse_corpus(store)
    report = where_is_jam_wrong(store)
    assert report["total_disagreements"] == 8
    assert len(report["ranked"]) >= 1
    top = report["ranked"][0]
    assert top["classification"] == "EXTENSION_COLLAPSE"
    assert top["count"] == 8
    assert top["share"] == pytest.approx(1.0)


def test_dominant_failure_class_returns_top(store: Store) -> None:
    _seed_extension_collapse_corpus(store)
    assert dominant_failure_class(store) == "EXTENSION_COLLAPSE"


def test_dominant_failure_class_none_for_empty_corpus(store: Store) -> None:
    assert dominant_failure_class(store) is None


def test_where_are_tabs_wrong_returns_likely_tab_error_rows(
    store: Store,
) -> None:
    """Low-confidence tab + non-matching chords -> rows flow into the
    LIKELY_TAB_ERROR report."""
    jam = [{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]
    tab = [{"symbol": "Bb", "startSec": 0.0, "endSec": 2.0}]
    a = ingest_analysis_bundle(
        {
            "song_id": "s1",
            "chords": jam,
            "sections": [],
            "key": "C major",
            "tempo": 120.0,
        },
        store,
    )
    t = ingest_tab_source(
        {
            "song_id": "s1",
            "source": "manual",
            "source_confidence": 0.1,
            "progression": tab,
        },
        store,
    )
    al = align_grid(a, t, store, step_sec=0.5)
    classify_alignment(al, store)

    report = where_are_tabs_wrong(store)
    assert report["count"] == 4
    for row in report["rows"]:
        assert row["jam_chord"] == "C"
        assert row["tab_chord"] == "Bb"
        assert row["song_id"] == "s1"


def test_engine_version_diff_reports_delta(store: Store) -> None:
    """Old version regresses extension_accuracy by 1.0 vs new version."""
    # Old: bad extension behaviour
    _seed_extension_collapse_corpus(store, engine_version="v0.9")
    aggregate_metrics("v0.9", store)
    # New: perfect agreement on a separate song
    chords = [{"symbol": "C", "startSec": 0.0, "endSec": 4.0}]
    a = ingest_analysis_bundle(
        {
            "song_id": "s2",
            "chords": chords,
            "sections": [],
            "key": "C major",
            "tempo": 120.0,
            "engine_version": "v1.0",
        },
        store,
    )
    t = ingest_tab_source(
        {"song_id": "s2", "source": "songsterr", "progression": chords},
        store,
    )
    al = align_grid(a, t, store, step_sec=0.5)
    classify_alignment(al, store)
    aggregate_metrics("v1.0", store)

    diff = engine_version_diff("v0.9", "v1.0", store)
    assert diff["a"]["engine_version"] == "v0.9"
    assert diff["b"]["engine_version"] == "v1.0"
    assert diff["delta"]["agreement_rate"] == pytest.approx(1.0)
    assert diff["delta"]["extension_accuracy"] == pytest.approx(1.0)


def test_engine_version_diff_handles_missing_row(store: Store) -> None:
    """If one version has no metrics row yet, deltas are None."""
    _seed_extension_collapse_corpus(store, engine_version="v0.9")
    aggregate_metrics("v0.9", store)
    diff = engine_version_diff("v0.9", "v-future", store)
    assert diff["a"] is not None
    assert diff["b"] is None
    for col in (
        "agreement_rate",
        "boundary_accuracy",
        "slash_chord_accuracy",
        "extension_accuracy",
    ):
        assert diff["delta"][col] is None
