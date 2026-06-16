"""Tests for ``song_validation.queue``.

Pin the file-queue contract: enqueue writes an envelope under
``inbox/``, drain ingests each envelope and moves it to ``done/`` (or
``failed/`` with a sidecar), and ``auto_validate=True`` fires the
pipeline for songs that now have both sides.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from song_validation import Store
from song_validation.queue import (
    QueueError,
    drain_queue,
    enqueue_bundle,
    enqueue_tab,
)


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(db_path=tmp_path / "sv.db")


@pytest.fixture
def queue_dir(tmp_path: Path) -> Path:
    return tmp_path / "queue"


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


def test_enqueue_bundle_creates_inbox_envelope(
    queue_dir: Path,
) -> None:
    chords = [{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]
    path = enqueue_bundle(_bundle("s1", chords), queue_dir)

    assert path.exists()
    assert path.parent == queue_dir / "inbox"
    assert path.suffix == ".json"
    envelope = json.loads(path.read_text("utf-8"))
    assert envelope["kind"] == "analysis_bundle"
    assert envelope["payload"]["song_id"] == "s1"
    assert envelope["payload"]["chords"] == chords

    # Sibling dirs are pre-created so drain doesn't race on mkdir.
    assert (queue_dir / "done").is_dir()
    assert (queue_dir / "failed").is_dir()


def test_enqueue_tab_creates_inbox_envelope(queue_dir: Path) -> None:
    progression = [{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]
    path = enqueue_tab(_tab("s1", progression), queue_dir)
    envelope = json.loads(path.read_text("utf-8"))
    assert envelope["kind"] == "tab_source"
    assert envelope["payload"]["progression"] == progression


def test_enqueue_rejects_non_mapping_payload(queue_dir: Path) -> None:
    with pytest.raises(QueueError, match="must be a mapping"):
        enqueue_bundle("not-a-dict", queue_dir)  # type: ignore[arg-type]
    with pytest.raises(QueueError, match="must be a mapping"):
        enqueue_tab(["nope"], queue_dir)  # type: ignore[arg-type]


def test_drain_empty_inbox_is_noop(queue_dir: Path, store: Store) -> None:
    result = drain_queue(queue_dir, store)
    assert result["processed"] == 0
    assert result["failed"] == 0
    assert result["ingested_bundles"] == []
    assert result["ingested_tabs"] == []
    assert result["validated_songs"] == []
    assert result["validation_errors"] == []


def test_drain_ingests_bundle_and_moves_to_done(
    queue_dir: Path, store: Store
) -> None:
    chords = [{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]
    enqueue_bundle(_bundle("s1", chords), queue_dir)

    result = drain_queue(queue_dir, store)
    assert result["processed"] == 1
    assert result["failed"] == 0
    assert len(result["ingested_bundles"]) == 1

    # File moved out of inbox into done.
    assert list((queue_dir / "inbox").glob("*.json")) == []
    assert len(list((queue_dir / "done").glob("*.json"))) == 1
    assert list((queue_dir / "failed").glob("*")) == []


def test_drain_handles_mixed_bundle_and_tab(
    queue_dir: Path, store: Store
) -> None:
    chords = [{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]
    enqueue_bundle(_bundle("s1", chords), queue_dir)
    enqueue_tab(_tab("s1", chords), queue_dir)

    result = drain_queue(queue_dir, store)
    assert result["processed"] == 2
    assert len(result["ingested_bundles"]) == 1
    assert len(result["ingested_tabs"]) == 1


def test_drain_routes_malformed_to_failed_with_sidecar(
    queue_dir: Path, store: Store
) -> None:
    """An envelope missing required ingestion fields lands in
    ``failed/`` with a ``.error.json`` sidecar capturing the
    exception class + message."""
    # Bundle missing 'chords' -> AnalysisBundleError downstream.
    bad = {
        "song_id": "s1",
        "sections": [],
        "key": "C major",
        "tempo": 120.0,
        "engine_version": "v1.0",
    }
    enqueue_bundle(bad, queue_dir)

    result = drain_queue(queue_dir, store)
    assert result["processed"] == 0
    assert result["failed"] == 1
    assert result["ingested_bundles"] == []

    failed_jsons = list((queue_dir / "failed").glob("*.json"))
    # Original + sidecar.
    assert len(failed_jsons) == 2
    sidecar = next(p for p in failed_jsons if p.name.endswith(".error.json"))
    payload = json.loads(sidecar.read_text("utf-8"))
    assert "error_class" in payload
    assert "message" in payload
    assert payload["error_class"] == "AnalysisBundleError"


def test_drain_rejects_envelope_with_unknown_kind(
    queue_dir: Path, store: Store
) -> None:
    """Manually drop a hand-rolled envelope with kind=garbage. It
    should land in failed/ rather than raise."""
    inbox = queue_dir / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    bad_envelope = inbox / "00000000000000000000_aaaa_bogus.json"
    bad_envelope.write_text(
        json.dumps({"kind": "garbage", "payload": {"x": 1}}),
        encoding="utf-8",
    )

    result = drain_queue(queue_dir, store)
    assert result["processed"] == 0
    assert result["failed"] == 1


def test_drain_auto_validate_runs_pipeline_when_both_sides_present(
    queue_dir: Path, store: Store
) -> None:
    chords = [{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]
    enqueue_bundle(_bundle("s1", chords), queue_dir)
    enqueue_tab(_tab("s1", chords), queue_dir)

    result = drain_queue(queue_dir, store, auto_validate=True)
    assert result["processed"] == 2
    assert result["validated_songs"] == ["s1"]
    assert result["validation_errors"] == []

    # Pipeline side effects: an alignment_results row + a metrics row.
    metrics = store.get_engine_metrics("v1.0")
    assert metrics is not None


def test_drain_auto_validate_skips_song_with_missing_side(
    queue_dir: Path, store: Store
) -> None:
    chords = [{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]
    # Only the bundle, no tab.
    enqueue_bundle(_bundle("s1", chords), queue_dir)

    result = drain_queue(queue_dir, store, auto_validate=True)
    assert result["processed"] == 1
    assert result["validated_songs"] == []  # Skipped — no tab yet.


def test_drain_is_idempotent_across_repeat_passes(
    queue_dir: Path, store: Store
) -> None:
    """A second drain pass after the inbox is empty does nothing and
    does not disturb files already in done/."""
    chords = [{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]
    enqueue_bundle(_bundle("s1", chords), queue_dir)
    drain_queue(queue_dir, store)
    done_before = sorted(p.name for p in (queue_dir / "done").iterdir())

    result = drain_queue(queue_dir, store)
    assert result["processed"] == 0
    assert sorted(
        p.name for p in (queue_dir / "done").iterdir()
    ) == done_before


def test_drain_max_items_caps_pass(queue_dir: Path, store: Store) -> None:
    chords = [{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]
    enqueue_bundle(_bundle("s1", chords), queue_dir)
    enqueue_bundle(_bundle("s2", chords), queue_dir)
    enqueue_bundle(_bundle("s3", chords), queue_dir)

    result = drain_queue(queue_dir, store, max_items=2)
    assert result["processed"] == 2
    # One envelope should still be in the inbox.
    assert len(list((queue_dir / "inbox").glob("*.json"))) == 1


def test_drain_preserves_fifo_order_via_filename_sort(
    queue_dir: Path, store: Store
) -> None:
    """The fixed-width nanosecond prefix means glob+sort yields FIFO
    order; the worker processes oldest first."""
    chords = [{"symbol": "C", "startSec": 0.0, "endSec": 2.0}]
    p1 = enqueue_bundle(_bundle("s1", chords), queue_dir)
    p2 = enqueue_bundle(_bundle("s2", chords), queue_dir)
    p3 = enqueue_bundle(_bundle("s3", chords), queue_dir)

    # Filenames must sort in the same order they were enqueued.
    assert sorted([p1.name, p2.name, p3.name]) == [p1.name, p2.name, p3.name]

    drain_queue(queue_dir, store, max_items=1)
    # Only the first envelope should be gone from inbox.
    remaining = sorted(p.name for p in (queue_dir / "inbox").iterdir())
    assert remaining == sorted([p2.name, p3.name])
