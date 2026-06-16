"""Tests for ``song_validation.reports.song_detail.inspect_song``.

Pin the per-song drilldown contract: empty stores produce empty
lists (not crashes), and a fully-processed song surfaces every
artifact — analyses, tabs, alignments, disagreement counts, and the
engine_metrics rows the song contributed to.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import pytest

from song_validation import Store, validate_song
from song_validation.ingestion import (
    ingest_analysis_bundle,
    ingest_tab_source,
)
from song_validation.reports import inspect_song


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(db_path=tmp_path / "sv.db")


def _bundle(
    song_id: str,
    chords: List[Dict[str, Any]],
    *,
    engine_version: str = "v1.0",
    sections: List[Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    return {
        "song_id": song_id,
        "chords": chords,
        "sections": sections if sections is not None else [],
        "key": "C major",
        "tempo": 120.0,
        "engine_version": engine_version,
    }


def _tab(
    song_id: str, progression: List[Dict[str, Any]], *, source="songsterr"
) -> Dict[str, Any]:
    return {
        "song_id": song_id,
        "source": source,
        "progression": progression,
    }


def test_inspect_unknown_song_returns_empty_shell(store: Store) -> None:
    result = inspect_song("nope", store)
    assert result["song_id"] == "nope"
    assert result["song"] is None
    assert result["analyses"] == []
    assert result["tabs"] == []
    assert result["alignments"] == []
    assert result["disagreement_summary"] == {}
    assert result["engine_metrics"] == []


def test_inspect_after_ingest_only_lists_analyses_and_tabs(
    store: Store,
) -> None:
    chords = [{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]
    ingest_analysis_bundle(_bundle("s1", chords), store)
    ingest_tab_source(_tab("s1", chords), store)

    result = inspect_song("s1", store)
    assert result["song"] is not None
    assert len(result["analyses"]) == 1
    a = result["analyses"][0]
    assert a["engine_version"] == "v1.0"
    assert a["chord_count"] == 1
    assert a["section_count"] == 0
    assert a["key"] == "C major"
    assert a["tempo"] == 120.0

    assert len(result["tabs"]) == 1
    t = result["tabs"][0]
    assert t["source"] == "songsterr"
    assert t["chord_count"] == 1

    # Pipeline hasn't run yet.
    assert result["alignments"] == []
    assert result["disagreement_summary"] == {}


def test_inspect_after_pipeline_includes_alignment_and_disagreements(
    store: Store,
) -> None:
    jam = [{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]
    # tab disagrees by an extension -> classifier labels EXTENSION_COLLAPSE.
    tab = [{"symbol": "Cmaj7", "startSec": 0.0, "endSec": 2.0}]
    ingest_analysis_bundle(_bundle("s1", jam), store)
    ingest_tab_source(_tab("s1", tab), store)
    validate_song("s1", store)

    result = inspect_song("s1", store)
    assert len(result["alignments"]) == 1
    al = result["alignments"][0]
    assert al["total_points"] > 0
    assert al["disagreement_count"] > 0
    assert al["score"] is not None

    summary = result["disagreement_summary"]
    assert "EXTENSION_COLLAPSE" in summary
    assert summary["EXTENSION_COLLAPSE"] > 0


def test_inspect_includes_engine_metrics_only_for_song_versions(
    store: Store,
) -> None:
    chords = [{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]
    ingest_analysis_bundle(
        _bundle("s1", chords, engine_version="v1.0"), store
    )
    ingest_tab_source(_tab("s1", chords), store)
    validate_song("s1", store)

    # A DIFFERENT song running a different engine version. inspect("s1")
    # must NOT include this version's metrics.
    ingest_analysis_bundle(
        _bundle("s2", chords, engine_version="v9.9"), store
    )
    ingest_tab_source(_tab("s2", chords), store)
    validate_song("s2", store)

    result = inspect_song("s1", store)
    versions = {m["engine_version"] for m in result["engine_metrics"]}
    assert versions == {"v1.0"}


def test_inspect_handles_multiple_analyses_and_tabs(store: Store) -> None:
    """N analyses * M tabs -> N*M alignments in the drilldown."""
    chords = [{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]
    ingest_analysis_bundle(_bundle("s1", chords, engine_version="v1.0"), store)
    ingest_analysis_bundle(_bundle("s1", chords, engine_version="v1.1"), store)
    ingest_tab_source(_tab("s1", chords, source="songsterr"), store)
    ingest_tab_source(_tab("s1", chords, source="ultimate_guitar"), store)
    validate_song("s1", store)

    result = inspect_song("s1", store)
    assert len(result["analyses"]) == 2
    assert len(result["tabs"]) == 2
    assert len(result["alignments"]) == 4  # 2 * 2
    # engine_metrics should cover both versions.
    versions = {m["engine_version"] for m in result["engine_metrics"]}
    assert versions == {"v1.0", "v1.1"}


def test_inspect_chord_count_uses_safe_json_decoding(
    store: Store,
) -> None:
    """A row with a multi-chord progression reports the actual length;
    chord_count is derived from the JSON column, not a separate
    integer field."""
    chords = [
        {"symbol": "C", "startSec": 0.0, "endSec": 2.0},
        {"symbol": "G", "startSec": 2.0, "endSec": 4.0},
        {"symbol": "Am", "startSec": 4.0, "endSec": 6.0},
    ]
    sections = [{"label": "Verse", "startSec": 0.0, "endSec": 6.0}]
    ingest_analysis_bundle(
        _bundle("s1", chords, sections=sections), store
    )
    result = inspect_song("s1", store)
    a = result["analyses"][0]
    assert a["chord_count"] == 3
    assert a["section_count"] == 1


def test_inspect_cli_subcommand_returns_drilldown_json(
    tmp_path: Path, store: Store
) -> None:
    """The CLI ``report inspect <song_id>`` surfaces the same dict."""
    import io
    import json
    from song_validation import cli

    chords = [{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]
    ingest_analysis_bundle(_bundle("s1", chords), store)
    ingest_tab_source(_tab("s1", chords), store)
    validate_song("s1", store)

    db_path = store.db_path
    buf = io.StringIO()
    rc = cli.main(
        ["--db", str(db_path), "report", "inspect", "s1"],
        out=buf,
    )
    assert rc == 0
    result = json.loads(buf.getvalue())
    assert result["song_id"] == "s1"
    assert len(result["analyses"]) == 1
    assert len(result["tabs"]) == 1
    assert len(result["alignments"]) == 1
