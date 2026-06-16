"""File-based work queue implementation.

See ``song_validation.queue`` package docstring for the high-level
contract. This module implements the on-disk format and the drain
loop.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from ..ingestion import (
    AnalysisBundleError,
    TabSourceError,
    ingest_analysis_bundle,
    ingest_tab_source,
)
from ..pipeline import PipelineError, validate_song
from ..store import Store


KIND_ANALYSIS_BUNDLE = "analysis_bundle"
KIND_TAB_SOURCE = "tab_source"
_VALID_KINDS = frozenset({KIND_ANALYSIS_BUNDLE, KIND_TAB_SOURCE})

_INBOX = "inbox"
_DONE = "done"
_FAILED = "failed"


class QueueError(ValueError):
    """Raised when a queue payload or directory argument is invalid."""


def _subdirs(queue_dir: Path) -> tuple[Path, Path, Path]:
    """Return (inbox, done, failed) under ``queue_dir``, creating them."""
    if not isinstance(queue_dir, Path):
        queue_dir = Path(queue_dir)
    inbox = queue_dir / _INBOX
    done = queue_dir / _DONE
    failed = queue_dir / _FAILED
    for d in (inbox, done, failed):
        d.mkdir(parents=True, exist_ok=True)
    return inbox, done, failed


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Write JSON to ``path`` atomically via ``<path>.tmp`` + rename."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    data = json.dumps(payload, sort_keys=True, default=str)
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.rename(tmp, path)


def _next_filename(kind: str) -> str:
    """Build a unique envelope filename ``<ts_ns>_<uuid>_<kind>.json``.

    The timestamp prefix gives natural FIFO sort order; the uuid
    suffix prevents collisions when multiple producers enqueue in the
    same nanosecond.
    """
    if kind not in _VALID_KINDS:
        raise QueueError(f"invalid envelope kind: {kind!r}")
    ts = time.time_ns()
    suffix = uuid.uuid4().hex[:12]
    return f"{ts:020d}_{suffix}_{kind}.json"


def _validate_payload_mapping(payload: Any, kind: str) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        raise QueueError(
            f"{kind} payload must be a mapping, got {type(payload).__name__}"
        )
    return payload


def enqueue_bundle(
    payload: Mapping[str, Any], queue_dir: Path | str
) -> Path:
    """Write an analysis-bundle envelope to ``queue_dir/inbox/``.

    Returns the path of the envelope file. The payload is *not*
    validated against the ingestion schema here; that happens later
    inside :func:`drain_queue`. Producers may live on a different
    process/host than the worker, so we only enforce envelope-shape
    invariants at enqueue time.
    """
    payload = _validate_payload_mapping(payload, "analysis_bundle")
    inbox, _, _ = _subdirs(Path(queue_dir))
    name = _next_filename(KIND_ANALYSIS_BUNDLE)
    path = inbox / name
    envelope = {"kind": KIND_ANALYSIS_BUNDLE, "payload": dict(payload)}
    _atomic_write_json(path, envelope)
    return path


def enqueue_tab(payload: Mapping[str, Any], queue_dir: Path | str) -> Path:
    """Write a tab-source envelope to ``queue_dir/inbox/``."""
    payload = _validate_payload_mapping(payload, "tab_source")
    inbox, _, _ = _subdirs(Path(queue_dir))
    name = _next_filename(KIND_TAB_SOURCE)
    path = inbox / name
    envelope = {"kind": KIND_TAB_SOURCE, "payload": dict(payload)}
    _atomic_write_json(path, envelope)
    return path


def _list_inbox(inbox: Path) -> list[Path]:
    """List inbox envelopes (excluding in-flight ``.tmp`` files) in
    FIFO order (filename sort works because the prefix is a fixed-
    width nanosecond timestamp)."""
    return sorted(p for p in inbox.iterdir() if p.suffix == ".json")


def _read_envelope(path: Path) -> Mapping[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, Mapping):
        raise QueueError(f"envelope {path.name} is not a JSON object")
    kind = data.get("kind")
    if kind not in _VALID_KINDS:
        raise QueueError(
            f"envelope {path.name} has unknown kind {kind!r}"
        )
    payload = data.get("payload")
    if not isinstance(payload, Mapping):
        raise QueueError(
            f"envelope {path.name} missing/invalid 'payload' object"
        )
    return data


def _move_to(target_dir: Path, path: Path) -> Path:
    """Move ``path`` into ``target_dir``; on name collision, suffix it."""
    dest = target_dir / path.name
    if dest.exists():
        # Extremely rare (would require timestamp + uuid collision)
        # but cheap to handle for test determinism.
        dest = target_dir / f"{path.stem}_{uuid.uuid4().hex[:6]}{path.suffix}"
    os.rename(path, dest)
    return dest


def _write_error_sidecar(failed_dir: Path, name: str, exc: BaseException) -> Path:
    """Write a ``<name>.error.json`` sidecar describing ``exc``."""
    sidecar = failed_dir / f"{name}.error.json"
    payload = {
        "error_class": type(exc).__name__,
        "error_module": type(exc).__module__,
        "message": str(exc),
    }
    _atomic_write_json(sidecar, payload)
    return sidecar


def _validate_eligible_songs(
    store: Store, song_ids: Iterable[str]
) -> tuple[list[str], list[Mapping[str, Any]]]:
    """Run the validation pipeline for songs that now have both an
    analysis row AND a tab row. Returns (validated, errors)."""
    validated: list[str] = []
    errors: list[Mapping[str, Any]] = []
    seen: set[str] = set()
    for song_id in song_ids:
        if song_id in seen:
            continue
        seen.add(song_id)
        if not _song_has_both_sides(store, song_id):
            continue
        try:
            validate_song(song_id, store)
        except PipelineError as exc:
            errors.append({"song_id": song_id, "error": str(exc)})
            continue
        validated.append(song_id)
    return validated, errors


def _song_has_both_sides(store: Store, song_id: str) -> bool:
    """True iff ``song_id`` has at least one analysis row AND at
    least one tab row."""
    with store.connect() as conn:
        analyses = conn.execute(
            "SELECT 1 FROM analysis_results WHERE song_id = ? LIMIT 1",
            (song_id,),
        ).fetchone()
        if analyses is None:
            return False
        tabs = conn.execute(
            "SELECT 1 FROM tab_sources WHERE song_id = ? LIMIT 1",
            (song_id,),
        ).fetchone()
        return tabs is not None


def drain_queue(
    queue_dir: Path | str,
    store: Store,
    *,
    auto_validate: bool = False,
    max_items: Optional[int] = None,
) -> dict:
    """Drain pending envelopes from ``queue_dir/inbox/``.

    For each envelope, dispatch to the appropriate ingestion entry
    point, move the file into ``done/`` on success, or ``failed/``
    (with a ``.error.json`` sidecar) on failure. The process is
    intentionally single-threaded — multiple worker processes can run
    concurrently because file rename is atomic on POSIX, but we don't
    try to coordinate them ourselves.

    Args:
        queue_dir: root of the queue tree.
        store: validation Store to ingest into.
        auto_validate: if True, after ingestion run
            :func:`validate_song` for every song that now has both an
            analysis and a tab row. Errors from the pipeline are
            collected into ``validation_errors`` rather than raised.
        max_items: optional cap on how many envelopes to process in
            this pass.

    Returns::

        {
            "processed": int,
            "failed": int,
            "ingested_bundles": [analysis_id, ...],
            "ingested_tabs": [tab_id, ...],
            "validated_songs": [song_id, ...],
            "validation_errors": [{"song_id", "error"}, ...],
        }
    """
    inbox, done, failed = _subdirs(Path(queue_dir))

    processed = 0
    failures = 0
    ingested_bundles: list[str] = []
    ingested_tabs: list[str] = []
    touched_song_ids: list[str] = []

    items = _list_inbox(inbox)
    if max_items is not None:
        items = items[: int(max_items)]

    for path in items:
        try:
            envelope = _read_envelope(path)
            payload = envelope["payload"]
            kind = envelope["kind"]
            if kind == KIND_ANALYSIS_BUNDLE:
                analysis_id = ingest_analysis_bundle(payload, store)
                ingested_bundles.append(analysis_id)
            elif kind == KIND_TAB_SOURCE:
                tab_id = ingest_tab_source(payload, store)
                ingested_tabs.append(tab_id)
            else:
                # Should be unreachable thanks to _read_envelope's check,
                # but keep the branch for safety.
                raise QueueError(f"unhandled envelope kind: {kind!r}")
            song_id = payload.get("song_id")
            if isinstance(song_id, str) and song_id:
                touched_song_ids.append(song_id)
            _move_to(done, path)
            processed += 1
        except (
            QueueError,
            AnalysisBundleError,
            TabSourceError,
            json.JSONDecodeError,
            sqlite3.DatabaseError,
            OSError,
            ValueError,
            TypeError,
        ) as exc:
            # Move the bad envelope aside and record the reason.
            try:
                moved = _move_to(failed, path)
                _write_error_sidecar(failed, moved.name, exc)
            except OSError:
                # If even the move failed, leave the inbox alone; the
                # next drain pass will see it again.
                pass
            failures += 1

    out: dict[str, Any] = {
        "processed": processed,
        "failed": failures,
        "ingested_bundles": ingested_bundles,
        "ingested_tabs": ingested_tabs,
        "validated_songs": [],
        "validation_errors": [],
    }

    if auto_validate and touched_song_ids:
        validated, errors = _validate_eligible_songs(store, touched_song_ids)
        out["validated_songs"] = validated
        out["validation_errors"] = list(errors)

    return out
