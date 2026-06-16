"""Tests for ``song_validation.cli``.

Pin the operator CLI's contract: each subcommand resolves to the
right underlying Python call and emits JSON on stdout. We invoke
``cli.main(argv, out=stream)`` directly rather than spawning a
subprocess — that keeps the tests fast and the captured output
deterministic across Python distributions.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from song_validation import Store, cli
from song_validation.ingestion import (
    ingest_analysis_bundle,
    ingest_tab_source,
)
from song_validation.queue import enqueue_bundle, enqueue_tab


@pytest.fixture
def store_path(tmp_path: Path) -> Path:
    return tmp_path / "sv.db"


@pytest.fixture
def store(store_path: Path) -> Store:
    return Store(db_path=store_path)


def _bundle(song_id: str, chords: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "song_id": song_id,
        "chords": chords,
        "sections": [],
        "key": "C major",
        "tempo": 120.0,
        "engine_version": "v1.0",
    }


def _tab(song_id: str, progression: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "song_id": song_id,
        "source": "songsterr",
        "progression": progression,
    }


def _run(argv: List[str], store_path: Path) -> Dict[str, Any]:
    """Invoke the CLI with --db pointing at the test DB and return
    the parsed JSON from stdout."""
    buf = io.StringIO()
    rc = cli.main(["--db", str(store_path), *argv], out=buf)
    assert rc == 0, f"argv {argv!r} returned {rc}, output={buf.getvalue()!r}"
    return json.loads(buf.getvalue())


def test_drain_subcommand_processes_queue(
    tmp_path: Path, store_path: Path
) -> None:
    queue_dir = tmp_path / "queue"
    chords = [{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]
    # Seed via the same store the CLI will hit.
    store = Store(db_path=store_path)
    enqueue_bundle(_bundle("s1", chords), queue_dir)
    enqueue_tab(_tab("s1", chords), queue_dir)
    del store

    result = _run(["drain", str(queue_dir), "--auto-validate"], store_path)
    assert result["processed"] == 2
    assert result["failed"] == 0
    assert result["validated_songs"] == ["s1"]


def test_enqueue_bundle_subcommand_writes_envelope(
    tmp_path: Path, store_path: Path
) -> None:
    queue_dir = tmp_path / "queue"
    payload = _bundle(
        "s1", [{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]
    )
    payload_path = tmp_path / "bundle.json"
    payload_path.write_text(json.dumps(payload), encoding="utf-8")

    result = _run(
        ["enqueue-bundle", str(queue_dir), str(payload_path)], store_path
    )
    assert "enqueued" in result
    enqueued = Path(result["enqueued"])
    assert enqueued.exists()
    assert enqueued.parent == queue_dir / "inbox"


def test_enqueue_tab_subcommand_writes_envelope(
    tmp_path: Path, store_path: Path
) -> None:
    queue_dir = tmp_path / "queue"
    payload = _tab(
        "s1", [{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]
    )
    payload_path = tmp_path / "tab.json"
    payload_path.write_text(json.dumps(payload), encoding="utf-8")

    result = _run(
        ["enqueue-tab", str(queue_dir), str(payload_path)], store_path
    )
    assert "enqueued" in result
    enqueued = Path(result["enqueued"])
    assert enqueued.parent == queue_dir / "inbox"


def test_enqueue_bundle_subcommand_rejects_missing_file(
    tmp_path: Path, store_path: Path
) -> None:
    queue_dir = tmp_path / "queue"
    buf = io.StringIO()
    rc = cli.main(
        [
            "--db",
            str(store_path),
            "enqueue-bundle",
            str(queue_dir),
            str(tmp_path / "does-not-exist.json"),
        ],
        out=buf,
    )
    assert rc == 2  # graceful exit, not traceback


def test_validate_subcommand_runs_pipeline(
    tmp_path: Path, store_path: Path, store: Store
) -> None:
    chords = [{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]
    ingest_analysis_bundle(_bundle("s1", chords), store)
    ingest_tab_source(_tab("s1", chords), store)

    result = _run(["validate", "s1"], store_path)
    assert "results" in result
    assert len(result["results"]) == 1
    s1_result = result["results"][0]
    assert s1_result["song_id"] == "s1"
    assert "error" not in s1_result


def test_validate_subcommand_collects_per_song_errors(
    tmp_path: Path, store_path: Path
) -> None:
    # No data ingested -> "no analysis_results" error captured into
    # the batch result rather than exit-2 from the CLI.
    result = _run(["validate", "missing-song"], store_path)
    assert result["results"][0]["song_id"] == "missing-song"
    assert "error" in result["results"][0]


def test_report_jam_wrong_returns_ranked_payload(
    tmp_path: Path, store_path: Path, store: Store
) -> None:
    jam = [{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]
    tab = [{"symbol": "Cmaj7", "startSec": 0.0, "endSec": 2.0}]
    ingest_analysis_bundle(_bundle("s1", jam), store)
    ingest_tab_source(_tab("s1", tab), store)
    from song_validation import validate_song
    validate_song("s1", store)

    result = _run(["report", "jam-wrong"], store_path)
    assert "total_disagreements" in result
    assert "ranked" in result
    assert any(
        row["classification"] == "EXTENSION_COLLAPSE" for row in result["ranked"]
    )


def test_report_tabs_wrong_returns_rows_shape(
    tmp_path: Path, store_path: Path
) -> None:
    result = _run(["report", "tabs-wrong"], store_path)
    assert result["count"] == 0
    assert result["rows"] == []


def test_report_dominant_returns_string_or_none(
    tmp_path: Path, store_path: Path
) -> None:
    result = _run(["report", "dominant"], store_path)
    assert "dominant_failure_class" in result
    # Empty DB -> None.
    assert result["dominant_failure_class"] is None


def test_report_engine_diff_handles_missing_versions(
    tmp_path: Path, store_path: Path
) -> None:
    result = _run(["report", "engine-diff", "v1.0", "v1.1"], store_path)
    assert "a" in result
    assert "b" in result
    assert "delta" in result


def test_corpus_stats_subcommand_returns_zeroed_when_empty(
    tmp_path: Path, store_path: Path
) -> None:
    result = _run(["corpus", "stats"], store_path)
    assert result["matching_songs"] == 0
    assert result["matching_analyses"] == 0
    assert result["total_chords"] == 0
    assert result["alignment_score_min"] is None


def test_pretty_flag_indents_output(
    tmp_path: Path, store_path: Path
) -> None:
    buf = io.StringIO()
    rc = cli.main(
        ["--db", str(store_path), "--pretty", "corpus", "stats"],
        out=buf,
    )
    assert rc == 0
    text = buf.getvalue()
    assert "\n  " in text  # indented JSON has 2-space leading whitespace


def test_unknown_command_exits_with_argparse_error(
    tmp_path: Path, store_path: Path
) -> None:
    with pytest.raises(SystemExit):
        cli.main(["--db", str(store_path), "bogus-command"])


def test_dispatch_covers_all_top_level_commands() -> None:
    """Belt-and-braces: each top-level subcommand has a dispatch entry."""
    expected = {
        "drain", "enqueue-bundle", "enqueue-tab",
        "validate", "report", "reclassify", "corpus", "store",
    }
    assert set(cli._DISPATCH.keys()) == expected
