"""Time-windowed reports.

The cross-version diffs (``engine_version_diff``,
``engine_version_song_diff``) compare engine v1 against engine v2 —
but they're silent on *within-version drift*. As more songs flow
into the corpus the failure-mix shifts even when the engine doesn't
change: a new tab supplier with different slash-chord conventions
can swing ``SLASH_CHORD_COLLAPSE`` share without any code change,
and the operator should be able to see that happen.

This module answers two adjacent questions:

- :func:`disagreement_trends_over_time` — bucket disagreements by
  the *ingestion* timestamp of the analysis they trace back to, and
  break each bucket down by classification.
- :func:`ingestion_trends_over_time` — bucket analysis ingestions
  themselves, so the operator can see throughput and correlate any
  failure-mix shift with corpus growth.

Both are pure SQL aggregations; nothing here mutates the store.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from ..store import Store


_BUCKET_FORMATS = {
    # SQLite strftime patterns. Day is the default — matches the
    # granularity at which most operators do their daily review.
    "day": "%Y-%m-%d",
    "week": "%Y-W%W",
    "month": "%Y-%m",
    "hour": "%Y-%m-%dT%H",
}


class TemporalReportError(ValueError):
    """Raised for invalid bucket or window arguments."""


def _resolve_bucket(bucket: str) -> str:
    fmt = _BUCKET_FORMATS.get(bucket)
    if fmt is None:
        raise TemporalReportError(
            f"unknown bucket {bucket!r}; "
            f"expected one of {sorted(_BUCKET_FORMATS)}"
        )
    return fmt


def _window_predicate(
    column: str,
    since: Optional[str],
    until: Optional[str],
) -> tuple[str, list[str]]:
    """Build optional ``WHERE`` clause for an ISO-8601 column."""
    clauses: list[str] = []
    params: list[str] = []
    if since is not None:
        clauses.append(f"{column} >= ?")
        params.append(since)
    if until is not None:
        clauses.append(f"{column} < ?")
        params.append(until)
    if not clauses:
        return "", params
    return " WHERE " + " AND ".join(clauses), params


def disagreement_trends_over_time(
    store: Store,
    *,
    bucket: str = "day",
    since: Optional[str] = None,
    until: Optional[str] = None,
) -> Mapping[str, Any]:
    """Time-bucketed counts of disagreement rows, broken down by
    ``classification``.

    Bucketing is keyed off the *analysis*'s ``created_at`` so the
    timeline reflects when the data flowed in, not when the
    disagreement row happened to be (re)written.

    Args:
        store: validation store.
        bucket: one of ``"hour"``, ``"day"`` (default), ``"week"``,
            ``"month"``.
        since: optional inclusive lower bound as an ISO-8601 string
            (e.g. ``"2025-01-01"`` or ``"2025-01-01T00:00:00+00:00"``).
        until: optional exclusive upper bound, same format.

    Returns::

        {
            "bucket": str,
            "since": str | None,
            "until": str | None,
            "total_disagreements": int,
            "buckets": [
                {
                    "bucket": "2025-01-01",
                    "total": 42,
                    "by_class": {
                        "BOUNDARY_ERROR": 30,
                        "EXTENSION_COLLAPSE": 12,
                        ...
                    },
                },
                ...
            ],
        }

    Buckets are returned in chronological order. Classes within a
    bucket are sorted alphabetically for run-to-run stability.
    """
    fmt = _resolve_bucket(bucket)
    where, params = _window_predicate(
        "ar.created_at", since, until
    )
    sql = (
        "SELECT strftime(?, ar.created_at) AS bucket, "
        "       d.classification, "
        "       COUNT(*) AS cnt "
        "FROM disagreements d "
        "JOIN alignment_results al "
        "  ON al.alignment_id = d.alignment_id "
        "JOIN analysis_results ar "
        "  ON ar.analysis_id = al.analysis_id"
        + where
        + " GROUP BY bucket, d.classification "
        "ORDER BY bucket, d.classification"
    )
    with store.connect() as conn:
        rows = conn.execute(sql, (fmt, *params)).fetchall()

    buckets: dict[str, dict[str, Any]] = {}
    total = 0
    for bucket_label, classification, cnt in rows:
        label = str(bucket_label)
        cls = str(classification)
        n = int(cnt)
        total += n
        entry = buckets.setdefault(
            label, {"bucket": label, "total": 0, "by_class": {}}
        )
        entry["total"] += n
        entry["by_class"][cls] = n

    ordered = [buckets[k] for k in sorted(buckets.keys())]
    return {
        "bucket": bucket,
        "since": since,
        "until": until,
        "total_disagreements": total,
        "buckets": ordered,
    }


def ingestion_trends_over_time(
    store: Store,
    *,
    bucket: str = "day",
    since: Optional[str] = None,
    until: Optional[str] = None,
) -> Mapping[str, Any]:
    """Time-bucketed counts of ``analysis_results`` ingestions.

    Pairs naturally with :func:`disagreement_trends_over_time`: if
    the failure-mix shifted on day X, did the ingestion volume also
    spike on day X (a new ingestion batch) or did it stay flat (an
    actual engine-behaviour shift)?

    Args:
        store: validation store.
        bucket: one of ``"hour"``, ``"day"`` (default), ``"week"``,
            ``"month"``.
        since: optional inclusive ISO-8601 lower bound.
        until: optional exclusive ISO-8601 upper bound.

    Returns::

        {
            "bucket": str,
            "since": str | None,
            "until": str | None,
            "total_analyses": int,
            "distinct_songs": int,
            "buckets": [
                {
                    "bucket": "2025-01-01",
                    "analyses_count": 12,
                    "distinct_songs": 9,
                    "by_engine_version": {"v1.0": 8, "v1.1": 4},
                },
                ...
            ],
        }
    """
    fmt = _resolve_bucket(bucket)
    where, params = _window_predicate(
        "ar.created_at", since, until
    )
    sql = (
        "SELECT strftime(?, ar.created_at) AS bucket, "
        "       ar.engine_version, "
        "       ar.song_id "
        "FROM analysis_results ar"
        + where
        + " ORDER BY bucket, ar.engine_version, ar.song_id"
    )
    with store.connect() as conn:
        rows = conn.execute(sql, (fmt, *params)).fetchall()

    buckets: dict[str, dict[str, Any]] = {}
    total = 0
    seen_songs_overall: set[str] = set()
    for bucket_label, engine_version, song_id in rows:
        label = str(bucket_label)
        engine = str(engine_version)
        sid = str(song_id)
        total += 1
        seen_songs_overall.add(sid)
        entry = buckets.setdefault(
            label,
            {
                "bucket": label,
                "analyses_count": 0,
                "distinct_songs": 0,
                "by_engine_version": {},
                # Track per-bucket distinct songs in a transient set;
                # collapsed before return.
                "_songs": set(),
            },
        )
        entry["analyses_count"] += 1
        entry["_songs"].add(sid)
        entry["by_engine_version"][engine] = (
            entry["by_engine_version"].get(engine, 0) + 1
        )

    ordered: list[dict[str, Any]] = []
    for k in sorted(buckets.keys()):
        e = buckets[k]
        e["distinct_songs"] = len(e.pop("_songs"))
        ordered.append(e)

    return {
        "bucket": bucket,
        "since": since,
        "until": until,
        "total_analyses": total,
        "distinct_songs": len(seen_songs_overall),
        "buckets": ordered,
    }
