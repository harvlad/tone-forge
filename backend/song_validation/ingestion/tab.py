"""Tab-source ingestion.

Accepts a parsed tab payload (already a dict — fetcher / HTTP handling
lives elsewhere) and stores it in the validation database as a row in
``tab_sources``. Tab sources are the *external reference* that the
alignment module compares the engine's analysis against. They are
never authoritative on their own — the directive is explicit that we
"do not train directly from all tabs"; tabs are evidence to weigh.

Required fields:
- ``song_id``           must match (or create) a row in ``songs``.
- ``source``            short identifier of where the tab came from,
                        e.g. ``"songsterr"``, ``"ultimate_guitar"``,
                        ``"manual"``.
- ``progression``       list of ``{"symbol", "startSec", "endSec"}``
                        entries (the time-indexed chord progression).

Optional fields:
- ``tab_id``            explicit ID; otherwise hashed from the payload.
- ``source_confidence`` float in [0, 1] — caller's prior on how
                        trustworthy this source is. Used later by the
                        disagreement classifier when emitting
                        ``LIKELY_TAB_ERROR``.
- ``raw_tab``           original raw payload (e.g. GP5 text dump,
                        scraped HTML). Stored verbatim for audit.
- ``artist`` / ``title`` / ``duration``   song-row metadata fill-in.

Returns the ``tab_id`` of the inserted row so callers can pass it to
the alignment step.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from ..store import Store


REQUIRED_FIELDS = ("song_id", "source", "progression")


class TabSourceError(ValueError):
    """Raised when a tab-source payload fails validation."""


def _derive_tab_id(payload: Mapping[str, Any]) -> str:
    """Content-addressed tab ID.

    Same shape as ``_derive_analysis_id`` so two ingests of the same
    payload (same song_id, same source, same progression) produce the
    same ID — a regression in the fetcher that re-emits an already-
    known progression is idempotent at the ingestion layer once
    callers layer ``INSERT OR IGNORE`` on top.
    """
    projection = {
        "song_id": payload["song_id"],
        "source": payload["source"],
        "progression": payload["progression"],
    }
    digest = hashlib.sha256(
        json.dumps(projection, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    return f"tab_{digest[:24]}"


def ingest_tab_source(payload: Mapping[str, Any], store: Store) -> str:
    """Validate and persist one tab-source payload.

    Raises :class:`TabSourceError` if any required field is missing or
    has the wrong shape. Otherwise upserts the song row, inserts the
    tab_sources row, and returns the tab_id.
    """
    if not isinstance(payload, Mapping):
        raise TabSourceError(
            f"tab payload must be a mapping, got {type(payload).__name__}"
        )

    missing = [k for k in REQUIRED_FIELDS if k not in payload]
    if missing:
        raise TabSourceError(
            f"tab payload missing required field(s): {', '.join(missing)}"
        )

    song_id = payload["song_id"]
    if not isinstance(song_id, str) or not song_id:
        raise TabSourceError("song_id must be a non-empty string")

    source = payload["source"]
    if not isinstance(source, str) or not source:
        raise TabSourceError("source must be a non-empty string")

    progression = payload["progression"]
    if not isinstance(progression, (list, tuple)):
        raise TabSourceError("progression must be a list")

    source_confidence = payload.get("source_confidence")
    if source_confidence is not None:
        if not isinstance(source_confidence, (int, float)):
            raise TabSourceError("source_confidence must be numeric or null")
        if not (0.0 <= float(source_confidence) <= 1.0):
            raise TabSourceError(
                "source_confidence must be in the closed interval [0, 1]"
            )

    raw_tab = payload.get("raw_tab")
    if raw_tab is not None and not isinstance(raw_tab, str):
        raise TabSourceError("raw_tab must be a string or null")

    store.upsert_song(
        song_id=song_id,
        artist=payload.get("artist"),
        title=payload.get("title"),
        duration=payload.get("duration"),
    )

    tab_id = payload.get("tab_id") or _derive_tab_id(payload)
    store.insert_tab_source(
        tab_id=tab_id,
        song_id=song_id,
        source=source,
        source_confidence=source_confidence,
        progression=progression,
        raw_tab=raw_tab,
    )
    return tab_id
