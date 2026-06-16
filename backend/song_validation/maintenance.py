"""Operator housekeeping for the validation store.

Three day-2 utilities the operator CLI needs but the data-pipeline
phases don't:

- :func:`list_songs` — enumerate every song in the store with its
  child-row counts. Answers "what's in here?" in a single round-trip
  for `python -m song_validation store list-songs`.

- :func:`purge_song` — delete a song and every dependent row in
  reverse-FK order. The directive's schema declares foreign keys but
  doesn't set ``ON DELETE CASCADE``, so cascade is manual. This is
  the only safe way to recover from "ingested with bad data" without
  trashing the entire DB.

- :func:`vacuum_store` — run sqlite ``VACUUM`` and report the bytes
  reclaimed. Useful after a big purge.

All three are pure read/write helpers on top of :class:`Store`. None
of them re-aggregate metrics; if a purge invalidates an engine
version's score card, the caller should follow up with
``aggregate_metrics(version, store)`` (or run
``reclassify_all_alignments(store)``, which re-aggregates as a
side-effect). This keeps each operation single-purpose.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from .store import Store


def list_songs(
    store: Store, *, limit: Optional[int] = None
) -> list[dict[str, Any]]:
    """Enumerate songs with per-song child-row counts.

    Args:
        store: the validation store.
        limit: optional cap on the number of rows returned. Default
            is no cap. Useful for operator triage when the store
            holds tens of thousands of songs.

    Returns a list of dicts ordered by ``song_id`` ASC::

        [
            {
                "song_id": str,
                "artist": str | None,
                "title": str | None,
                "duration": float | None,
                "analyses_count": int,
                "tabs_count": int,
                "alignments_count": int,
                "disagreements_count": int,
            },
            ...
        ]

    Order is stable (ASCII sort on song_id) so successive runs
    produce the same listing for diff-style inspection.
    """
    sql = (
        "SELECT s.song_id, s.artist, s.title, s.duration, "
        "       (SELECT COUNT(*) FROM analysis_results ar "
        "        WHERE ar.song_id = s.song_id) AS analyses_count, "
        "       (SELECT COUNT(*) FROM tab_sources ts "
        "        WHERE ts.song_id = s.song_id) AS tabs_count, "
        "       (SELECT COUNT(*) FROM alignment_results al "
        "        WHERE al.song_id = s.song_id) AS alignments_count, "
        "       (SELECT COUNT(*) FROM disagreements d "
        "        WHERE d.song_id = s.song_id) AS disagreements_count "
        "FROM songs s "
        "ORDER BY s.song_id ASC"
    )
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    with store.connect() as conn:
        rows = conn.execute(sql).fetchall()
    return [
        {
            "song_id": r[0],
            "artist": r[1],
            "title": r[2],
            "duration": r[3],
            "analyses_count": int(r[4]),
            "tabs_count": int(r[5]),
            "alignments_count": int(r[6]),
            "disagreements_count": int(r[7]),
        }
        for r in rows
    ]


def purge_song(song_id: str, store: Store) -> dict[str, Any]:
    """Delete one song and every row that references it.

    Order matters: disagreements → alignment_results → tab_sources →
    analysis_results → songs. This is the inverse of the FK
    declaration order; doing it any other way would either need
    ``ON DELETE CASCADE`` (not set in the directive schema) or
    silently leave orphan rows behind.

    Returns::

        {
            "song_id": str,
            "deleted": {
                "songs": int,            # 0 or 1
                "analyses": int,
                "tabs": int,
                "alignments": int,
                "disagreements": int,
            },
            "engine_versions_touched": list[str],
        }

    ``engine_versions_touched`` is the distinct list of engine
    versions whose analyses were removed. The caller should
    re-aggregate those versions' metrics if score cards are
    expected to stay live; we don't do it here to keep the
    operation single-purpose (and skip the work when the operator
    is purging a batch of songs in a script).
    """
    with store.connect() as conn:
        engine_versions = sorted(
            {
                row[0]
                for row in conn.execute(
                    "SELECT DISTINCT engine_version FROM analysis_results "
                    "WHERE song_id = ?",
                    (song_id,),
                )
            }
        )

        d_dis = conn.execute(
            "DELETE FROM disagreements WHERE song_id = ?", (song_id,)
        ).rowcount
        d_al = conn.execute(
            "DELETE FROM alignment_results WHERE song_id = ?", (song_id,)
        ).rowcount
        d_tab = conn.execute(
            "DELETE FROM tab_sources WHERE song_id = ?", (song_id,)
        ).rowcount
        d_an = conn.execute(
            "DELETE FROM analysis_results WHERE song_id = ?", (song_id,)
        ).rowcount
        d_song = conn.execute(
            "DELETE FROM songs WHERE song_id = ?", (song_id,)
        ).rowcount

    return {
        "song_id": song_id,
        "deleted": {
            "songs": int(d_song),
            "analyses": int(d_an),
            "tabs": int(d_tab),
            "alignments": int(d_al),
            "disagreements": int(d_dis),
        },
        "engine_versions_touched": engine_versions,
    }


def vacuum_store(store: Store) -> dict[str, Any]:
    """Run sqlite ``VACUUM`` and return byte-size delta.

    VACUUM rebuilds the database file from scratch in a single
    transaction; after a big purge this is the only way to recover
    the freed pages. Returns the before/after sizes plus the
    reclaimed delta in bytes.

    ``VACUUM`` cannot run inside an explicit transaction, so we
    open a fresh connection with ``isolation_level=None`` for this
    operation. ``Store.connect`` opens its own transaction
    contextmanager, so VACUUM has to bypass it.
    """
    import sqlite3

    bytes_before = (
        store.db_path.stat().st_size if store.db_path.exists() else 0
    )
    conn = sqlite3.connect(str(store.db_path), isolation_level=None)
    try:
        conn.execute("VACUUM")
    finally:
        conn.close()
    bytes_after = (
        store.db_path.stat().st_size if store.db_path.exists() else 0
    )
    return {
        "bytes_before": int(bytes_before),
        "bytes_after": int(bytes_after),
        "bytes_reclaimed": int(bytes_before - bytes_after),
    }
