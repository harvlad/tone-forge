"""Append-only JSONL evidence store.

Storage layout::

    backend/data/evidence/
        2026-06-18.jsonl
        2026-06-19.jsonl
        ...

Each ``.jsonl`` file holds one ``EvidenceRecord`` per line. The
directory is the entire store; no index, no DB. Reads stream the
files; writes append.

Why daily roll-over?

    * Each file stays bounded (a typical analysis emits 10-40 records;
      hundreds of songs per day still leaves files under a few MB).
    * Old days are immutable, which lets backups / sync tools rely on
      mtime checks.
    * A future Phase 9 ML loader can ingest by date range without
      indexing.

The directory lives under ``backend/data/`` which is gitignored by
convention; commit ``.gitkeep`` if checked-in placement is wanted.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator, Optional

from .schema import (
    EvidenceRecord,
    _jsonable_to_record,
    _record_to_jsonable,
)


__all__ = ["EvidenceStore"]


# Default store root. Resolved lazily so tests can override via
# ``EvidenceStore(root=tmp_path)``.
_DEFAULT_ROOT = Path(__file__).resolve().parents[2] / "data" / "evidence"


def _utc_date_string(timestamp_utc: str) -> str:
    """Extract ``YYYY-MM-DD`` from an ISO-8601 UTC timestamp.

    Accepts both ``2026-06-18T14:23:01Z`` and the longer
    ``2026-06-18T14:23:01.123456+00:00`` forms. Defensive against
    a missing ``T`` (returns the first 10 chars).
    """
    if "T" in timestamp_utc:
        return timestamp_utc.split("T", 1)[0]
    return timestamp_utc[:10]


class EvidenceStore:
    """Append-only JSONL store for ``EvidenceRecord``.

    Thread/process safety: a single writer per file is assumed.
    ``append()`` uses POSIX append-mode ``write()`` which is atomic
    for single-line writes under PIPE_BUF (records are typically
    well under that bound, but JSONL is intentionally
    line-delimited so a partial write can be detected on read).

    Read-side helpers tolerate corrupt trailing lines (truncated
    record) by skipping them with a warning — important for crash
    recovery, since the writer process may die mid-write and we
    don't want one bad line to block analytics queries.
    """

    def __init__(self, root: Optional[Path] = None) -> None:
        self.root: Path = Path(root) if root is not None else _DEFAULT_ROOT
        self.root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def append(self, record: EvidenceRecord) -> Path:
        """Append one record to the daily file matching its timestamp.

        Returns the path written to. If the record's
        ``timestamp_utc`` is empty the current UTC time is used and
        the record is *not* mutated — callers that care about
        deterministic timestamps must set the field themselves before
        appending.
        """
        ts = record.timestamp_utc or datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.%fZ"
        )
        date = _utc_date_string(ts)
        target = self.root / f"{date}.jsonl"
        line = json.dumps(_record_to_jsonable(record), sort_keys=False, separators=(",", ":"))
        with target.open("a", encoding="utf-8") as fh:
            fh.write(line)
            fh.write("\n")
        return target

    def extend(self, records: Iterable[EvidenceRecord]) -> int:
        """Bulk-append; returns the number of records written.

        Each record may land in a different daily file (rare, but
        possible at midnight boundaries). The method opens each file
        on demand and closes it before the next append, accepting
        the syscall cost in exchange for simpler semantics.
        """
        n = 0
        for record in records:
            self.append(record)
            n += 1
        return n

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def iter_records(
        self,
        *,
        date_prefix: Optional[str] = None,
    ) -> Iterator[EvidenceRecord]:
        """Stream every record in the store.

        ``date_prefix`` filters by the daily filename prefix
        (``"2026-06"`` returns every record in June 2026). Files are
        read in lexicographic name order, which is chronological for
        ISO date filenames.

        Malformed lines (truncated, non-JSON) are skipped silently;
        a future verbose-mode could log them.
        """
        for path in sorted(self.root.glob("*.jsonl")):
            if date_prefix is not None and not path.stem.startswith(date_prefix):
                continue
            try:
                with path.open("r", encoding="utf-8") as fh:
                    for raw in fh:
                        raw = raw.strip()
                        if not raw:
                            continue
                        try:
                            yield _jsonable_to_record(json.loads(raw))
                        except (json.JSONDecodeError, KeyError, ValueError):
                            # Corrupt trailing line or unsupported
                            # schema. Skip and keep going so analytics
                            # queries don't break on a single bad line.
                            continue
            except FileNotFoundError:
                # File deleted between glob and open; rare race.
                continue

    def count(self, *, date_prefix: Optional[str] = None) -> int:
        """Cheap counter; iterates records and discards them."""
        n = 0
        for _ in self.iter_records(date_prefix=date_prefix):
            n += 1
        return n

    def latest_for_section(
        self, song_id: str, section_id: str
    ) -> Optional[EvidenceRecord]:
        """Return the most recent record matching the given keys.

        "Most recent" is by ``timestamp_utc`` lexicographic order
        (ISO-8601 sorts chronologically). ``None`` if no record
        matches. Iterates the entire store on every call — fine for
        Phase 1's evidence volumes; Phase 4 may add an index if
        failure-mining queries become hot.
        """
        latest: Optional[EvidenceRecord] = None
        for record in self.iter_records():
            if record.song_id != song_id or record.section_id != section_id:
                continue
            if latest is None or record.timestamp_utc > latest.timestamp_utc:
                latest = record
        return latest

    def latest_per_section(
        self,
        *,
        song_id: Optional[str] = None,
    ) -> dict[tuple[str, str], EvidenceRecord]:
        """Group records by ``(song_id, section_id)``, keep newest each.

        Useful for "current view" queries: which sections have we
        analysed, what does the consensus say *right now*. Filter to
        one song with ``song_id=...``.
        """
        out: dict[tuple[str, str], EvidenceRecord] = {}
        for record in self.iter_records():
            if song_id is not None and record.song_id != song_id:
                continue
            key = (record.song_id, record.section_id)
            existing = out.get(key)
            if existing is None or record.timestamp_utc > existing.timestamp_utc:
                out[key] = record
        return out

    # ------------------------------------------------------------------
    # Maintenance helpers
    # ------------------------------------------------------------------

    def file_paths(self) -> list[Path]:
        """List all daily JSONL files, lexicographically sorted."""
        return sorted(self.root.glob("*.jsonl"))

    def total_bytes(self) -> int:
        """Sum of file sizes — Phase 1 ops visibility for disk usage."""
        return sum(os.path.getsize(p) for p in self.file_paths())
