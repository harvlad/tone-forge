"""Analysis-bundle ingestion.

Accepts a parsed ``analysis_bundle.json`` payload (already a dict —
HTTP handling lives elsewhere) and stores it in the validation
database. The bundle is the only artifact the Connect client uploads
per the architecture directive; it is not re-derived server-side.

Required fields (per the directive's example):
- ``song_id``
- ``chords``     list/tuple
- ``sections``   list/tuple
- ``key``
- ``tempo``      numeric

Optional fields that are stored if present:
- ``analysis_id``    explicit ID; otherwise hashed from the payload
- ``engine_version`` defaults to ``"unknown"``
- ``artist`` / ``title`` / ``duration``   song-row metadata fill-in
- ``created_at``     ISO-8601 timestamp; defaults to ``utcnow()``

Returns the ``analysis_id`` of the inserted row so callers can
correlate the upload with subsequent alignment work.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Mapping

from ..store import Store


REQUIRED_FIELDS = ("song_id", "chords", "sections", "key", "tempo")


class AnalysisBundleError(ValueError):
    """Raised when an analysis bundle fails validation."""


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _derive_analysis_id(bundle: Mapping[str, Any]) -> str:
    """Hash the bundle payload to produce a stable, content-derived ID.

    Using a hash of the payload (song_id + engine_version + a JSON-
    sorted projection of the analysis fields) means re-uploading the
    same bundle is idempotent at the ingestion layer once the caller
    layers ``INSERT OR IGNORE`` on top. The first commit keeps a
    strict INSERT so duplicate uploads surface loudly during early
    development.
    """
    payload = {
        "song_id": bundle["song_id"],
        "engine_version": bundle.get("engine_version", "unknown"),
        "chords": bundle["chords"],
        "sections": bundle["sections"],
        "key": bundle["key"],
        "tempo": bundle["tempo"],
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    return f"an_{digest[:24]}"


def ingest_analysis_bundle(bundle: Mapping[str, Any], store: Store) -> str:
    """Validate and persist one analysis bundle.

    Raises :class:`AnalysisBundleError` if any required field is
    missing or has the wrong shape. Otherwise upserts the song row,
    inserts the analysis_result row, and returns the analysis_id.
    """
    if not isinstance(bundle, Mapping):
        raise AnalysisBundleError(
            f"bundle must be a mapping, got {type(bundle).__name__}"
        )

    missing = [k for k in REQUIRED_FIELDS if k not in bundle]
    if missing:
        raise AnalysisBundleError(
            f"bundle missing required field(s): {', '.join(missing)}"
        )

    song_id = bundle["song_id"]
    if not isinstance(song_id, str) or not song_id:
        raise AnalysisBundleError("song_id must be a non-empty string")

    chords = bundle["chords"]
    sections = bundle["sections"]
    if not isinstance(chords, (list, tuple)):
        raise AnalysisBundleError("chords must be a list")
    if not isinstance(sections, (list, tuple)):
        raise AnalysisBundleError("sections must be a list")

    tempo = bundle["tempo"]
    if tempo is not None and not isinstance(tempo, (int, float)):
        raise AnalysisBundleError("tempo must be numeric or null")

    store.upsert_song(
        song_id=song_id,
        artist=bundle.get("artist"),
        title=bundle.get("title"),
        duration=bundle.get("duration"),
    )

    analysis_id = bundle.get("analysis_id") or _derive_analysis_id(bundle)
    store.insert_analysis_result(
        analysis_id=analysis_id,
        song_id=song_id,
        engine_version=bundle.get("engine_version", "unknown"),
        chords=chords,
        sections=sections,
        tempo=tempo,
        key=bundle["key"],
        created_at=bundle.get("created_at") or _utcnow_iso(),
    )
    return analysis_id
