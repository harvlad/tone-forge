"""Training corpus exporter — snapshot the high-confidence subset
to a portable file format for offline ML jobs.

The directive defers the harmony language model itself (its
architecture, its training loop, its serving). What this module
does is the *producer* side of that interface: given a Store and a
confidence policy, write the qualifying progressions to a file the
training job can mmap or stream without re-running the SQL query.

Why a separate module from ``training.corpus``:

- The corpus iterator returns Python dicts. Training jobs run on a
  separate process / GPU pool (per architectural directive) and
  read from disk. We need a stable on-disk format owned by this
  subsystem so the two sides can evolve independently.

- The snapshot must be atomic: training jobs that pick up a partial
  file mid-write would silently train on truncated data. We write
  through a ``.tmp`` sibling and ``os.rename`` it into place.

- We capture a ``meta`` header on the first line: schema version,
  threshold policy, timestamp, row count. The training side
  validates the header before consuming records — catches the case
  where the corpus policy changed between snapshots.

The format is JSONL (one JSON object per line). First line is the
meta envelope; subsequent lines are progression records. This is
the lowest-friction format for ML pipelines — every framework can
stream JSONL, no custom decoder needed.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Mapping, Optional

from ..store import Store
from .corpus import (
    DEFAULT_MIN_ALIGNMENT_SCORE,
    DEFAULT_MIN_TAB_CONFIDENCE,
    iter_high_confidence_progressions,
)


# Bump when the on-disk record schema changes (NOT when adding new
# threshold parameters — those go in the meta envelope as policy).
CORPUS_SNAPSHOT_SCHEMA_VERSION = 1


class CorpusExportError(ValueError):
    """Raised when the export cannot complete (e.g. unsupported
    format, output path is a directory)."""


def _meta_envelope(
    *,
    schema_version: int,
    policy: Mapping[str, Any],
    record_count: int,
) -> dict[str, Any]:
    """Build the first-line meta envelope.

    The envelope is keyed under ``"meta": true`` so the training-side
    reader can disambiguate it from a progression record with a
    single key check before parsing.
    """
    return {
        "meta": True,
        "schema_version": schema_version,
        "exported_at_unix": int(time.time()),
        "policy": dict(policy),
        "record_count": record_count,
    }


def _atomic_write_lines(path: Path, lines: list[str]) -> None:
    """Write ``lines`` (each already newline-terminated) to ``path``
    atomically. Goes through ``.tmp`` + ``os.rename`` so a reader
    can never see a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        for line in lines:
            fh.write(line)
        fh.flush()
        os.fsync(fh.fileno())
    os.rename(tmp, path)


def export_corpus(
    store: Store,
    output_path: Path,
    *,
    format: str = "jsonl",
    min_alignment_score: float = DEFAULT_MIN_ALIGNMENT_SCORE,
    min_tab_confidence: float = DEFAULT_MIN_TAB_CONFIDENCE,
    min_chord_confidence: Optional[float] = None,
) -> dict[str, Any]:
    """Write the high-confidence corpus to ``output_path``.

    Args:
        store: source validation store.
        output_path: target file path. Parent directories are created
            if missing. Existing file at this path is replaced
            atomically.
        format: ``"jsonl"`` is the only supported value today; passing
            anything else raises :class:`CorpusExportError`. The
            parameter exists so future formats (parquet, msgpack)
            can land without changing the call sites.
        min_alignment_score / min_tab_confidence / min_chord_confidence:
            same semantics as :func:`iter_high_confidence_progressions`.

    Returns::

        {
            "output_path": str,
            "format": "jsonl",
            "record_count": int,
            "policy": {
                "min_alignment_score": float,
                "min_tab_confidence": float,
                "min_chord_confidence": float | None,
            },
            "schema_version": int,
        }

    The returned dict mirrors the meta envelope written to the file's
    first line so callers can assert what was produced without
    re-reading the file.
    """
    if format != "jsonl":
        raise CorpusExportError(
            f"unsupported export format: {format!r} "
            "(only 'jsonl' is supported today)"
        )

    output_path = Path(output_path)
    if output_path.exists() and output_path.is_dir():
        raise CorpusExportError(
            f"output_path is a directory: {output_path}"
        )

    policy = {
        "min_alignment_score": float(min_alignment_score),
        "min_tab_confidence": float(min_tab_confidence),
        "min_chord_confidence": (
            float(min_chord_confidence)
            if min_chord_confidence is not None
            else None
        ),
    }

    # Materialise all records first so the meta envelope can carry
    # the exact count. The corpus is bounded by what fits in sqlite
    # comfortably; a few MB of dicts per snapshot is fine. If the
    # corpus ever outgrows memory, this becomes a two-pass write or
    # a header-rewrite-after-streaming change — but the call
    # contract stays the same.
    records: list[Mapping[str, Any]] = list(
        iter_high_confidence_progressions(
            store,
            min_alignment_score=min_alignment_score,
            min_tab_confidence=min_tab_confidence,
            min_chord_confidence=min_chord_confidence,
        )
    )

    meta = _meta_envelope(
        schema_version=CORPUS_SNAPSHOT_SCHEMA_VERSION,
        policy=policy,
        record_count=len(records),
    )

    lines: list[str] = [json.dumps(meta, sort_keys=True) + "\n"]
    for rec in records:
        lines.append(json.dumps(rec, sort_keys=True, default=str) + "\n")

    _atomic_write_lines(output_path, lines)

    return {
        "output_path": str(output_path),
        "format": format,
        "record_count": len(records),
        "policy": policy,
        "schema_version": CORPUS_SNAPSHOT_SCHEMA_VERSION,
    }


def read_corpus_snapshot(path: Path) -> tuple[Mapping[str, Any], list[Mapping[str, Any]]]:
    """Read a snapshot file and return ``(meta, records)``.

    Provided as the symmetric reader so callers (and tests) don't
    have to know the meta-on-first-line layout. The training side
    will use this same helper; we keep it here so the format owner
    owns both ends of the contract.

    Raises :class:`CorpusExportError` if the file is empty, the meta
    line is malformed, or the schema version is one this module
    doesn't recognise.
    """
    path = Path(path)
    with open(path, "r", encoding="utf-8") as fh:
        first = fh.readline()
        if not first:
            raise CorpusExportError(
                f"snapshot file is empty: {path}"
            )
        try:
            meta = json.loads(first)
        except json.JSONDecodeError as exc:
            raise CorpusExportError(
                f"snapshot {path}: meta line is not JSON: {exc}"
            ) from exc
        if not isinstance(meta, Mapping) or not meta.get("meta"):
            raise CorpusExportError(
                f"snapshot {path}: first line is not a meta envelope"
            )
        if meta.get("schema_version") != CORPUS_SNAPSHOT_SCHEMA_VERSION:
            raise CorpusExportError(
                f"snapshot {path}: schema_version "
                f"{meta.get('schema_version')!r} unsupported "
                f"(this build expects {CORPUS_SNAPSHOT_SCHEMA_VERSION})"
            )
        records: list[Mapping[str, Any]] = []
        for line in fh:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return meta, records
