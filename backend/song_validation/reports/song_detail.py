"""Per-song detail report (drilldown).

The aggregate reports in :mod:`song_validation.reports.queries` answer
corpus-level questions ("Where is JAM wrong overall?"). When an
engineer needs to debug a specific song regression they need the
opposite: everything we know about *one* song — every analysis row,
every tab row, every alignment, the classified disagreement
breakdown, and the per-engine-version score cards that this song
contributes to.

:func:`inspect_song` returns a single dict the caller can JSON-dump
or render as a markdown report. Pure read; no side effects.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from ..store import Store


def _row_to_dict(row: tuple, columns: list[str]) -> dict[str, Any]:
    return {col: row[i] for i, col in enumerate(columns)}


def _safe_json_array_len(blob: str | None) -> int:
    """Decode a JSON-array column and return its length, or 0 if the
    blob is missing/empty/malformed. Defensive: report queries
    shouldn't blow up on legacy rows."""
    if not blob:
        return 0
    try:
        decoded = json.loads(blob)
    except (TypeError, ValueError):
        return 0
    if not isinstance(decoded, list):
        return 0
    return len(decoded)


def _song_row(store: Store, song_id: str) -> Mapping[str, Any] | None:
    with store.connect() as conn:
        row = conn.execute(
            "SELECT artist, title, duration FROM songs WHERE song_id = ?",
            (song_id,),
        ).fetchone()
    if row is None:
        return None
    return {"artist": row[0], "title": row[1], "duration": row[2]}


def _analyses(store: Store, song_id: str) -> list[dict[str, Any]]:
    with store.connect() as conn:
        rows = conn.execute(
            "SELECT analysis_id, engine_version, key, tempo, "
            "       chords, sections, created_at "
            "FROM analysis_results "
            "WHERE song_id = ? "
            "ORDER BY created_at ASC, analysis_id ASC",
            (song_id,),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "analysis_id": r[0],
                "engine_version": r[1],
                "key": r[2],
                "tempo": r[3],
                "chord_count": _safe_json_array_len(r[4]),
                "section_count": _safe_json_array_len(r[5]),
                "created_at": r[6],
            }
        )
    return out


def _tabs(store: Store, song_id: str) -> list[dict[str, Any]]:
    with store.connect() as conn:
        rows = conn.execute(
            "SELECT tab_id, source, source_confidence, progression "
            "FROM tab_sources "
            "WHERE song_id = ? "
            "ORDER BY tab_id ASC",
            (song_id,),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "tab_id": r[0],
                "source": r[1],
                "source_confidence": r[2],
                "chord_count": _safe_json_array_len(r[3]),
            }
        )
    return out


def _alignments_with_counts(
    store: Store, song_id: str
) -> list[dict[str, Any]]:
    """Alignments for the song, each annotated with its disagreement
    count (sum across all classifications)."""
    with store.connect() as conn:
        rows = conn.execute(
            "SELECT al.alignment_id, al.analysis_id, al.tab_id, "
            "       al.score, al.total_points, al.created_at, "
            "       COUNT(d.disagreement_id) AS dcount "
            "FROM alignment_results al "
            "LEFT JOIN disagreements d "
            "  ON d.alignment_id = al.alignment_id "
            "WHERE al.song_id = ? "
            "GROUP BY al.alignment_id "
            "ORDER BY al.created_at ASC, al.alignment_id ASC",
            (song_id,),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "alignment_id": r[0],
                "analysis_id": r[1],
                "tab_id": r[2],
                "score": r[3],
                "total_points": r[4],
                "created_at": r[5],
                "disagreement_count": r[6],
            }
        )
    return out


def _disagreement_summary(
    store: Store, song_id: str
) -> dict[str, int]:
    with store.connect() as conn:
        rows = conn.execute(
            "SELECT classification, COUNT(*) "
            "FROM disagreements "
            "WHERE song_id = ? "
            "GROUP BY classification",
            (song_id,),
        ).fetchall()
    return {cls: count for cls, count in rows}


def _engine_metrics_for_song(
    store: Store, song_id: str
) -> list[dict[str, Any]]:
    """Metrics rows for every engine_version that has at least one
    analysis on this song. The metrics row itself is corpus-wide, not
    song-specific — it's surfaced here so the reader can see the
    score-card backdrop the song's analyses contributed to."""
    with store.connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT em.engine_version, em.agreement_rate, "
            "                em.boundary_accuracy, em.slash_chord_accuracy, "
            "                em.extension_accuracy, em.updated_at "
            "FROM engine_metrics em "
            "JOIN analysis_results ar "
            "  ON ar.engine_version = em.engine_version "
            "WHERE ar.song_id = ? "
            "ORDER BY em.engine_version ASC",
            (song_id,),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "engine_version": r[0],
                "agreement_rate": r[1],
                "boundary_accuracy": r[2],
                "slash_chord_accuracy": r[3],
                "extension_accuracy": r[4],
                "updated_at": r[5],
            }
        )
    return out


def inspect_song(song_id: str, store: Store) -> dict[str, Any]:
    """Return every artifact the validation store has for ``song_id``.

    The returned dict always carries ``song_id``; the per-table lists
    may be empty if nothing has been ingested yet. ``song`` is ``None``
    when the songs row is missing (which happens when no analysis or
    tab has been ingested for this id).

    Shape::

        {
            "song_id": str,
            "song": {"artist", "title", "duration"} | None,
            "analyses": [{analysis_id, engine_version, key, tempo,
                          chord_count, section_count, created_at}, ...],
            "tabs": [{tab_id, source, source_confidence,
                      chord_count}, ...],
            "alignments": [{alignment_id, analysis_id, tab_id, score,
                            total_points, created_at,
                            disagreement_count}, ...],
            "disagreement_summary": {classification: count, ...},
            "engine_metrics": [{engine_version, agreement_rate,
                                boundary_accuracy, slash_chord_accuracy,
                                extension_accuracy, updated_at}, ...],
        }
    """
    return {
        "song_id": song_id,
        "song": _song_row(store, song_id),
        "analyses": _analyses(store, song_id),
        "tabs": _tabs(store, song_id),
        "alignments": _alignments_with_counts(store, song_id),
        "disagreement_summary": _disagreement_summary(store, song_id),
        "engine_metrics": _engine_metrics_for_song(store, song_id),
    }
