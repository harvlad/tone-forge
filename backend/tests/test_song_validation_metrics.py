"""Tests for ``song_validation.metrics.aggregate``.

Walks one or more analyses + tabs through the full pipeline
(ingest -> align -> classify -> aggregate) and pins the per-engine-
version score-card the engine improvement loop reads.
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


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(db_path=tmp_path / "sv.db")


def _bundle(
    song_id: str,
    chords: List[Dict[str, Any]],
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


def _tab_payload(
    song_id: str,
    progression: List[Dict[str, Any]],
    source: str = "songsterr",
) -> Dict[str, Any]:
    return {
        "song_id": song_id,
        "source": source,
        "progression": progression,
    }


def test_aggregate_metrics_no_data_returns_null_row(store: Store) -> None:
    result = aggregate_metrics("v0.1", store)
    assert result["agreement_rate"] is None
    assert result["boundary_accuracy"] is None
    assert result["slash_chord_accuracy"] is None
    assert result["extension_accuracy"] is None
    assert result["total_points"] == 0

    row = store.get_engine_metrics("v0.1")
    assert row is not None
    assert row["agreement_rate"] is None


def test_aggregate_metrics_perfect_engine_scores_all_ones(
    store: Store,
) -> None:
    """Engine agrees with the tab everywhere -> all metrics == 1.0."""
    chords = [{"symbol": "C", "startSec": 0.0, "endSec": 4.0}]
    a_id = ingest_analysis_bundle(_bundle("s1", chords, "v1.0"), store)
    t_id = ingest_tab_source(_tab_payload("s1", chords), store)
    al_id = align_grid(a_id, t_id, store, step_sec=0.5)
    classify_alignment(al_id, store)

    result = aggregate_metrics("v1.0", store)
    assert result["agreement_rate"] == pytest.approx(1.0)
    assert result["boundary_accuracy"] == pytest.approx(1.0)
    assert result["slash_chord_accuracy"] == pytest.approx(1.0)
    assert result["extension_accuracy"] == pytest.approx(1.0)
    assert result["total_points"] == 8


def test_aggregate_metrics_extension_collapse_lowers_extension_accuracy(
    store: Store,
) -> None:
    """jam=C vs tab=Cmaj7 over the whole song -> extension_accuracy drops."""
    jam = [{"symbol": "C", "startSec": 0.0, "endSec": 4.0}]
    tab = [{"symbol": "Cmaj7", "startSec": 0.0, "endSec": 4.0}]
    a_id = ingest_analysis_bundle(_bundle("s1", jam, "v1.0"), store)
    t_id = ingest_tab_source(_tab_payload("s1", tab), store)
    al_id = align_grid(a_id, t_id, store, step_sec=0.5)
    classify_alignment(al_id, store)

    result = aggregate_metrics("v1.0", store)
    # 8 grid points, all 8 disagree as EXTENSION_COLLAPSE.
    assert result["total_points"] == 8
    assert result["agreement_rate"] == pytest.approx(0.0)
    assert result["extension_accuracy"] == pytest.approx(0.0)
    # Other failure-class accuracies unaffected.
    assert result["boundary_accuracy"] == pytest.approx(1.0)
    assert result["slash_chord_accuracy"] == pytest.approx(1.0)


def test_aggregate_metrics_isolates_engine_versions(store: Store) -> None:
    """An older buggy version is reflected separately from a newer one."""
    jam_old = [{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]
    jam_new = [{"symbol": "Cmaj7", "startSec": 0.0, "endSec": 2.0}]
    tab = [{"symbol": "Cmaj7", "startSec": 0.0, "endSec": 2.0}]

    a_old = ingest_analysis_bundle(_bundle("s1", jam_old, "v0.9"), store)
    a_new = ingest_analysis_bundle(_bundle("s1", jam_new, "v1.0"), store)
    t = ingest_tab_source(_tab_payload("s1", tab), store)
    al_old = align_grid(a_old, t, store, step_sec=0.5)
    al_new = align_grid(a_new, t, store, step_sec=0.5)
    classify_alignment(al_old, store)
    classify_alignment(al_new, store)

    old = aggregate_metrics("v0.9", store)
    new = aggregate_metrics("v1.0", store)

    # Old version had extension collapses everywhere -> 0 extension acc.
    assert old["extension_accuracy"] == pytest.approx(0.0)
    # New version agrees -> perfect.
    assert new["extension_accuracy"] == pytest.approx(1.0)
    assert new["agreement_rate"] == pytest.approx(1.0)


def test_aggregate_metrics_combines_multiple_alignments(
    store: Store,
) -> None:
    """Two songs under same engine_version are averaged weighted by
    total_points."""
    # Song 1: 8 points, all agree.
    chords_1 = [{"symbol": "C", "startSec": 0.0, "endSec": 4.0}]
    a1 = ingest_analysis_bundle(_bundle("s1", chords_1, "v1.0"), store)
    t1 = ingest_tab_source(_tab_payload("s1", chords_1), store)
    al1 = align_grid(a1, t1, store, step_sec=0.5)
    classify_alignment(al1, store)

    # Song 2: 4 points, all disagree (extension collapse).
    jam_2 = [{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]
    tab_2 = [{"symbol": "Cmaj7", "startSec": 0.0, "endSec": 2.0}]
    a2 = ingest_analysis_bundle(_bundle("s2", jam_2, "v1.0"), store)
    t2 = ingest_tab_source(_tab_payload("s2", tab_2), store)
    al2 = align_grid(a2, t2, store, step_sec=0.5)
    classify_alignment(al2, store)

    result = aggregate_metrics("v1.0", store)
    # Total: 12 points; 8 agreements / 12 total = 0.666...
    assert result["total_points"] == 12
    assert result["agreement_rate"] == pytest.approx(8 / 12)
    # 4 extension collapses out of 12 -> extension_accuracy = 1 - 4/12.
    assert result["extension_accuracy"] == pytest.approx(1 - 4 / 12)


def test_aggregate_metrics_is_idempotent(store: Store) -> None:
    """Running aggregation twice overwrites in place (no duplicate row)."""
    chords = [{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]
    a = ingest_analysis_bundle(_bundle("s1", chords, "v1.0"), store)
    t = ingest_tab_source(_tab_payload("s1", chords), store)
    al = align_grid(a, t, store, step_sec=0.5)
    classify_alignment(al, store)

    first = aggregate_metrics("v1.0", store)
    second = aggregate_metrics("v1.0", store)
    assert first["agreement_rate"] == second["agreement_rate"]

    # Only one row in engine_metrics for v1.0.
    with store.connect() as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM engine_metrics WHERE engine_version = ?",
            ("v1.0",),
        ).fetchone()[0]
    assert n == 1
