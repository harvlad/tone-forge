"""MusicSource protocol + the shared search/sort/cursor machinery.

The ordering + opaque-cursor logic lives here so every source gets it
for free and behaves identically. Two invariants motivate the design:

  * **Server-side sort BEFORE paging.** A source returns a fully ordered
    list; the page is a slice of it. Clients never re-sort.
  * **Opaque cursor, not offset.** The cursor encodes the last row's
    ``(sort-value, source_ref)`` boundary. The next page is the rows
    strictly greater than that boundary under the same total order. A
    background ingest inserting a row therefore cannot shift the window:
    offset paging would (row inserted before the cursor pushes every
    later row down one), boundary paging does not.
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol, Tuple

from tone_forge.contracts import SearchPage, SourceId, SourceTrack

# Sort modes the Songs table exposes. Kept tiny on purpose — each maps to
# a single total order below.
VALID_SORTS: Tuple[str, ...] = ("recent", "title", "tempo", "key")

# Sentinels that keep "unknown" values sorting LAST in ascending order
# while staying JSON-serialisable inside a cursor (no inf/NaN — those
# round-trip badly and are invalid strict JSON).
_NO_TEMPO = 1.0e12
_NO_KEY = "￿"


def _primary(track: SourceTrack, sort: str) -> Any:
    """The primary sort value for ``track`` under ``sort``.

    Everything is expressed as an ASCENDING comparable so one code path
    handles all modes: ``recent`` negates the epoch so the newest row
    (largest epoch) compares smallest and lands first.
    """
    if sort == "title":
        return (track.title or "").casefold()
    if sort == "tempo":
        return float(track.tempo_bpm) if track.tempo_bpm is not None else _NO_TEMPO
    if sort == "key":
        return (track.key or _NO_KEY)
    # recent (default): newest first.
    return -float(track.created_at_s or 0.0)


def _order_key(track: SourceTrack, sort: str) -> Tuple[Any, str]:
    """Total order: primary value, then ``source_ref`` as a stable
    tiebreak so the ordering is deterministic and the cursor boundary is
    unambiguous even when two rows share a primary value."""
    return (_primary(track, sort), track.source_ref)


def sort_tracks(tracks: List[SourceTrack], sort: str) -> List[SourceTrack]:
    """Return ``tracks`` in the total order for ``sort`` (ascending on the
    derived key). Unknown sort falls back to ``recent``."""
    if sort not in VALID_SORTS:
        sort = "recent"
    return sorted(tracks, key=lambda t: _order_key(t, sort))


def encode_cursor(sort: str, track: SourceTrack) -> str:
    """Opaque token for resuming AFTER ``track`` under ``sort``.

    Encodes the sort mode (so a cursor minted under one sort is ignored
    if the client later changes sort) plus the row's boundary tuple.
    """
    primary, ref = _order_key(track, sort)
    raw = json.dumps({"s": sort, "p": primary, "r": ref}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def decode_cursor(token: str) -> Optional[Dict[str, Any]]:
    """Decode a cursor to ``{"s": sort, "p": primary, "r": ref}`` or
    ``None`` when malformed (treated as "start from the top" — a garbage
    cursor must never 500 the endpoint)."""
    try:
        raw = base64.urlsafe_b64decode(token.encode("ascii"))
        data = json.loads(raw)
    except Exception:
        return None
    if not isinstance(data, dict) or "s" not in data or "r" not in data:
        return None
    return data


def paginate(
    ordered: List[SourceTrack], sort: str, cursor: Optional[str], limit: int
) -> Tuple[List[SourceTrack], Optional[str]]:
    """Slice ``ordered`` (already sorted) into one page + a next cursor.

    ``cursor`` resumes strictly after its boundary tuple under the SAME
    total order. Because we scan for the first row whose ``_order_key``
    exceeds the boundary (rather than trusting an index), a row inserted
    anywhere before the boundary does not move the window.
    """
    start = 0
    if cursor:
        cur = decode_cursor(cursor)
        # A cursor minted under a different sort is meaningless here —
        # ignore it and start fresh rather than page a stale order.
        if cur and cur.get("s") == sort:
            boundary = (cur.get("p"), cur.get("r"))
            start = len(ordered)
            for i, t in enumerate(ordered):
                if _order_key(t, sort) > boundary:
                    start = i
                    break
    page = ordered[start : start + limit]
    next_cursor: Optional[str] = None
    if page and (start + limit) < len(ordered):
        next_cursor = encode_cursor(sort, page[-1])
    return page, next_cursor


@dataclass(frozen=True)
class SourceCaps:
    """What a source can do — surfaced so the shell can hide controls a
    source doesn't support (e.g. a read-only catalog with ``ingest`` off).

    ``facets``/``sorts`` name the filterable fields and sort modes the
    source honours. Not a cross-boundary contract DTO — it stays inside
    this package (the composition point doesn't serialize it in MVP).
    """

    search: bool = True
    ingest: bool = False
    facets: Tuple[str, ...] = ()
    sorts: Tuple[str, ...] = VALID_SORTS


class MusicSource(Protocol):
    """The uniform face of a catalog.

    A source maps its catalog into ``SourceTrack`` rows and answers
    ``search`` (filter + sort + page) and ``ingest`` (bring a row into
    the user's library, returning the job or history id that represents
    it). Implementations live in this package; the composition point
    dispatches by :class:`SourceId`.
    """

    id: SourceId
    caps: SourceCaps

    def search(
        self,
        query: Optional[str] = None,
        facets: Optional[Dict[str, Any]] = None,
        sort: str = "recent",
        cursor: Optional[str] = None,
        limit: int = 50,
    ) -> SearchPage:
        ...

    def ingest(self, source_ref: str) -> Dict[str, Any]:
        ...
