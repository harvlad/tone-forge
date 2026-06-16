"""Tests for ``song_validation.pipeline``.

The pipeline is the end-to-end entry point that an HTTP/worker layer
will invoke. These tests pin the orchestrator's contract: align +
classify + aggregate run in order, are idempotent on re-run, and
batch behaviour collects per-song errors without halting.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import pytest

from song_validation import (
    PipelineError,
    Store,
    validate_song,
    validate_songs,
)
from song_validation.disagreement import DisagreementClass
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
    song_id: str, progression: List[Dict[str, Any]]
) -> Dict[str, Any]:
    return {
        "song_id": song_id,
        "source": "songsterr",
        "progression": progression,
    }


def test_validate_song_runs_full_pipeline(store: Store) -> None:
    """Single (analysis, tab) pair -> alignment + classification +
    metrics row all produced in one call."""
    jam = [{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]
    tab = [{"symbol": "Cmaj7", "startSec": 0.0, "endSec": 2.0}]
    ingest_analysis_bundle(_bundle("s1", jam), store)
    ingest_tab_source(_tab("s1", tab), store)

    result = validate_song("s1", store)

    assert result["song_id"] == "s1"
    assert len(result["alignments"]) == 1
    assert result["skipped"] == []
    assert result["engine_versions_updated"] == ["v1.0"]

    # Disagreement rows should be classified (not UNKNOWN).
    al_id = result["alignments"][0]
    rows = store.list_disagreements_for_alignment(al_id)
    assert len(rows) == 4
    for r in rows:
        assert r["classification"] == (
            DisagreementClass.EXTENSION_COLLAPSE.value
        )

    # Metrics row written.
    metrics = store.get_engine_metrics("v1.0")
    assert metrics is not None
    assert metrics["agreement_rate"] == pytest.approx(0.0)
    assert metrics["extension_accuracy"] == pytest.approx(0.0)


def test_validate_song_is_idempotent(store: Store) -> None:
    """Second call sees the same alignment_id, treats it as already
    processed, and still re-runs classification + aggregation."""
    chords = [{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]
    ingest_analysis_bundle(_bundle("s1", chords), store)
    ingest_tab_source(_tab("s1", chords), store)

    r1 = validate_song("s1", store)
    r2 = validate_song("s1", store)

    assert len(r1["alignments"]) == 1
    assert r1["skipped"] == []
    assert r2["alignments"] == []
    assert len(r2["skipped"]) == 1
    assert r2["skipped"][0] == r1["alignments"][0]


def test_validate_song_cross_joins_multiple_analyses_and_tabs(
    store: Store,
) -> None:
    """N analyses + M tabs -> N*M alignment rows."""
    chords_a = [{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]
    chords_b = [{"symbol": "G", "startSec": 0.0, "endSec": 2.0}]
    ingest_analysis_bundle(_bundle("s1", chords_a, "v1.0"), store)
    ingest_analysis_bundle(_bundle("s1", chords_b, "v1.1"), store)
    ingest_tab_source(_tab("s1", chords_a), store)
    ingest_tab_source(
        {
            "song_id": "s1",
            "source": "ultimate_guitar",
            "progression": chords_b,
        },
        store,
    )

    result = validate_song("s1", store)
    assert len(result["alignments"]) == 4  # 2 analyses * 2 tabs
    assert set(result["engine_versions_updated"]) == {"v1.0", "v1.1"}


def test_validate_song_rejects_missing_analyses(store: Store) -> None:
    ingest_tab_source(
        _tab("s1", [{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]),
        store,
    )
    with pytest.raises(PipelineError, match="no analysis_results"):
        validate_song("s1", store)


def test_validate_song_rejects_missing_tabs(store: Store) -> None:
    ingest_analysis_bundle(
        _bundle("s1", [{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]),
        store,
    )
    with pytest.raises(PipelineError, match="no tab_sources"):
        validate_song("s1", store)


def test_validate_songs_batch_continues_past_individual_errors(
    store: Store,
) -> None:
    """One bad song doesn't halt the batch; the error is captured in
    that song's result entry."""
    chords = [{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]
    ingest_analysis_bundle(_bundle("good", chords), store)
    ingest_tab_source(_tab("good", chords), store)

    out = validate_songs(["good", "missing"], store)
    assert len(out) == 2

    good_result = out[0]
    assert good_result["song_id"] == "good"
    assert "error" not in good_result
    assert len(good_result["alignments"]) == 1

    bad_result = out[1]
    assert bad_result["song_id"] == "missing"
    assert "error" in bad_result
    assert "no analysis_results" in bad_result["error"]
