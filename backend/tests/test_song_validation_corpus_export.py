"""Tests for ``song_validation.training.exporter``.

Pin the on-disk contract of the corpus snapshot file: a meta envelope
on the first line, one progression record per subsequent line,
atomic write through ``.tmp`` + ``os.rename``, and a symmetric
``read_corpus_snapshot`` that round-trips the data.
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
from typing import Any, Dict, List

import pytest

from song_validation import Store, validate_song
from song_validation.ingestion import (
    ingest_analysis_bundle,
    ingest_tab_source,
)
from song_validation.training import (
    CORPUS_SNAPSHOT_SCHEMA_VERSION,
    CorpusExportError,
    export_corpus,
    read_corpus_snapshot,
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
    source_confidence: float = 0.9,
) -> Dict[str, Any]:
    return {
        "song_id": song_id,
        "source": "songsterr",
        "progression": progression,
        "source_confidence": source_confidence,
    }


def _seed_qualifying_song(store: Store, song_id: str) -> None:
    """Ingest + validate a song whose alignment score will pass the
    default 0.8 threshold (jam==tab -> score 1.0)."""
    chords = [{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]
    ingest_analysis_bundle(_bundle(song_id, chords), store)
    ingest_tab_source(_tab(song_id, chords), store)
    validate_song(song_id, store)


def test_export_empty_store_writes_meta_only(
    store: Store, tmp_path: Path
) -> None:
    out = tmp_path / "snap.jsonl"
    result = export_corpus(store, out)
    assert result["record_count"] == 0
    assert result["schema_version"] == CORPUS_SNAPSHOT_SCHEMA_VERSION
    text = out.read_text(encoding="utf-8").splitlines()
    assert len(text) == 1  # meta only
    meta = json.loads(text[0])
    assert meta["meta"] is True
    assert meta["record_count"] == 0


def test_export_writes_one_record_per_qualifying_song(
    store: Store, tmp_path: Path
) -> None:
    _seed_qualifying_song(store, "s1")
    _seed_qualifying_song(store, "s2")

    out = tmp_path / "snap.jsonl"
    result = export_corpus(store, out)
    assert result["record_count"] == 2

    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3  # meta + 2 records
    meta = json.loads(lines[0])
    assert meta["meta"] is True
    assert meta["record_count"] == 2

    song_ids = {json.loads(line)["song_id"] for line in lines[1:]}
    assert song_ids == {"s1", "s2"}


def test_export_filters_by_alignment_score(
    store: Store, tmp_path: Path
) -> None:
    """Cranking the alignment-score gate to 0.99 should still let
    the perfect-agreement songs through (score == 1.0)."""
    _seed_qualifying_song(store, "s1")
    out = tmp_path / "snap.jsonl"
    result = export_corpus(store, out, min_alignment_score=0.99)
    assert result["record_count"] == 1
    # And cranking it above achievable should produce an empty body.
    out2 = tmp_path / "snap2.jsonl"
    result2 = export_corpus(store, out2, min_alignment_score=1.01)
    assert result2["record_count"] == 0


def test_export_filters_by_tab_confidence(
    store: Store, tmp_path: Path
) -> None:
    chords = [{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]
    ingest_analysis_bundle(_bundle("s1", chords), store)
    # Low-confidence tab.
    ingest_tab_source(_tab("s1", chords, source_confidence=0.5), store)
    validate_song("s1", store)

    out = tmp_path / "snap.jsonl"
    # Default min_tab_confidence=0.7 excludes this row.
    result = export_corpus(store, out)
    assert result["record_count"] == 0

    # Lowering the gate lets it through.
    out2 = tmp_path / "snap2.jsonl"
    result2 = export_corpus(store, out2, min_tab_confidence=0.4)
    assert result2["record_count"] == 1


def test_export_records_carry_policy_in_meta(
    store: Store, tmp_path: Path
) -> None:
    out = tmp_path / "snap.jsonl"
    export_corpus(
        store,
        out,
        min_alignment_score=0.85,
        min_tab_confidence=0.6,
        min_chord_confidence=0.5,
    )
    meta = json.loads(out.read_text(encoding="utf-8").splitlines()[0])
    assert meta["policy"]["min_alignment_score"] == pytest.approx(0.85)
    assert meta["policy"]["min_tab_confidence"] == pytest.approx(0.6)
    assert meta["policy"]["min_chord_confidence"] == pytest.approx(0.5)


def test_export_overwrites_existing_file_atomically(
    store: Store, tmp_path: Path
) -> None:
    out = tmp_path / "snap.jsonl"
    out.write_text("STALE", encoding="utf-8")
    _seed_qualifying_song(store, "s1")
    export_corpus(store, out)
    # Stale contents replaced.
    text = out.read_text(encoding="utf-8")
    assert "STALE" not in text
    # No leftover .tmp sibling.
    assert not (tmp_path / "snap.jsonl.tmp").exists()


def test_export_creates_parent_directories(
    store: Store, tmp_path: Path
) -> None:
    out = tmp_path / "nested" / "deeper" / "snap.jsonl"
    export_corpus(store, out)
    assert out.exists()


def test_export_rejects_unsupported_format(
    store: Store, tmp_path: Path
) -> None:
    out = tmp_path / "snap.parquet"
    with pytest.raises(CorpusExportError):
        export_corpus(store, out, format="parquet")


def test_export_rejects_directory_target(
    store: Store, tmp_path: Path
) -> None:
    target = tmp_path / "dir"
    target.mkdir()
    with pytest.raises(CorpusExportError):
        export_corpus(store, target)


def test_read_corpus_snapshot_round_trips(
    store: Store, tmp_path: Path
) -> None:
    _seed_qualifying_song(store, "s1")
    _seed_qualifying_song(store, "s2")
    out = tmp_path / "snap.jsonl"
    export_corpus(store, out)

    meta, records = read_corpus_snapshot(out)
    assert meta["schema_version"] == CORPUS_SNAPSHOT_SCHEMA_VERSION
    assert meta["record_count"] == len(records) == 2
    assert {r["song_id"] for r in records} == {"s1", "s2"}


def test_read_corpus_snapshot_rejects_empty_file(tmp_path: Path) -> None:
    out = tmp_path / "snap.jsonl"
    out.write_text("", encoding="utf-8")
    with pytest.raises(CorpusExportError):
        read_corpus_snapshot(out)


def test_read_corpus_snapshot_rejects_non_meta_first_line(
    tmp_path: Path,
) -> None:
    out = tmp_path / "snap.jsonl"
    out.write_text(
        json.dumps({"song_id": "x"}) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(CorpusExportError):
        read_corpus_snapshot(out)


def test_read_corpus_snapshot_rejects_wrong_schema_version(
    tmp_path: Path,
) -> None:
    out = tmp_path / "snap.jsonl"
    bogus_meta = {
        "meta": True,
        "schema_version": 999,
        "record_count": 0,
        "exported_at_unix": 0,
        "policy": {},
    }
    out.write_text(json.dumps(bogus_meta) + "\n", encoding="utf-8")
    with pytest.raises(CorpusExportError):
        read_corpus_snapshot(out)


def test_cli_corpus_export_subcommand(
    store: Store, tmp_path: Path
) -> None:
    """The CLI ``corpus export`` subcommand routes through to the
    Python function and emits the result summary as JSON."""
    from song_validation import cli

    _seed_qualifying_song(store, "s1")
    out = tmp_path / "snap.jsonl"

    buf = io.StringIO()
    rc = cli.main(
        [
            "--db",
            str(store.db_path),
            "corpus",
            "export",
            str(out),
        ],
        out=buf,
    )
    assert rc == 0
    payload = json.loads(buf.getvalue())
    assert payload["record_count"] == 1
    assert payload["format"] == "jsonl"
    assert payload["schema_version"] == CORPUS_SNAPSHOT_SCHEMA_VERSION
    # File actually exists on disk.
    assert out.exists()


def test_cli_corpus_export_threshold_flags_pass_through(
    store: Store, tmp_path: Path
) -> None:
    from song_validation import cli

    _seed_qualifying_song(store, "s1")
    out = tmp_path / "snap.jsonl"
    buf = io.StringIO()
    rc = cli.main(
        [
            "--db",
            str(store.db_path),
            "corpus",
            "export",
            str(out),
            "--min-alignment-score",
            "1.01",
        ],
        out=buf,
    )
    assert rc == 0
    payload = json.loads(buf.getvalue())
    # 1.01 is unreachable -> empty snapshot.
    assert payload["record_count"] == 0
