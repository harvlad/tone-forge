"""Corpus-level reports.

Pure aggregations over the validation database that answer the
directive's "success state" questions. Each function returns a
plain-Python dict the caller can ``json.dumps`` or render to
markdown — no UI/HTML coupling.

Reports:

- :func:`where_is_jam_wrong`      top failure classes across the corpus.
- :func:`where_are_tabs_wrong`    timestamps flagged ``LIKELY_TAB_ERROR``.
- :func:`engine_version_diff`     score-card comparison between two
                                  ``engine_metrics`` rows.
- :func:`dominant_failure_class`  argmax over ``disagreements.classification``.
"""

from __future__ import annotations

from typing import Mapping, Optional

from ..disagreement import DisagreementClass
from ..store import Store


def _all_classification_counts(store: Store) -> dict[str, int]:
    with store.connect() as conn:
        rows = conn.execute(
            "SELECT classification, COUNT(*) FROM disagreements "
            "GROUP BY classification"
        ).fetchall()
    counts = {c.value: 0 for c in DisagreementClass}
    for cls, n in rows:
        counts[str(cls)] = int(n)
    return counts


def where_is_jam_wrong(store: Store, *, top_n: int = 6) -> dict:
    """Return the top failure classes ranked by count across the corpus.

    Shape::

        {
            "total_disagreements": int,
            "ranked": [
                {"classification": "EXTENSION_COLLAPSE", "count": 1234,
                 "share": 0.42},
                ...
            ],
        }

    A zero-disagreement corpus yields ``total_disagreements=0`` and an
    empty ``ranked`` list — callers should not assume non-empty.
    """
    counts = _all_classification_counts(store)
    total = sum(counts.values())
    items = [
        {
            "classification": cls,
            "count": n,
            "share": (n / total) if total > 0 else 0.0,
        }
        for cls, n in counts.items()
        if n > 0
    ]
    items.sort(key=lambda x: x["count"], reverse=True)
    return {
        "total_disagreements": total,
        "ranked": items[:top_n],
    }


def where_are_tabs_wrong(store: Store, *, limit: int = 500) -> dict:
    """Return the disagreement rows flagged ``LIKELY_TAB_ERROR``.

    Useful when manually auditing low-quality tab sources. The result
    is ordered by song_id then timestamp so a reviewer can scan a
    song's flagged regions linearly.
    """
    with store.connect() as conn:
        import sqlite3
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT song_id, alignment_id, timestamp, jam_chord, "
            "       tab_chord, confidence "
            "FROM disagreements "
            "WHERE classification = ? "
            "ORDER BY song_id ASC, timestamp ASC "
            "LIMIT ?",
            (DisagreementClass.LIKELY_TAB_ERROR.value, limit),
        ).fetchall()
    return {
        "count": len(rows),
        "rows": [dict(r) for r in rows],
    }


def dominant_failure_class(store: Store) -> Optional[str]:
    """Return the single most common classification, or ``None`` if
    the corpus is empty.

    Tie-breaks alphabetically on classification name to keep the
    answer stable run-to-run."""
    counts = _all_classification_counts(store)
    nonzero = [(cls, n) for cls, n in counts.items() if n > 0]
    if not nonzero:
        return None
    nonzero.sort(key=lambda x: (-x[1], x[0]))
    return nonzero[0][0]


def engine_version_diff(
    a: str, b: str, store: Store
) -> dict:
    """Score-card diff between two engine_versions.

    Shape::

        {
            "a": {"engine_version": ..., "agreement_rate": ..., ...},
            "b": {...},
            "delta": {
                "agreement_rate": b - a,
                "boundary_accuracy": b - a,
                "slash_chord_accuracy": b - a,
                "extension_accuracy": b - a,
            },
        }

    If a metrics row is missing (e.g. no alignments yet for a
    version), the corresponding ``delta`` field is ``None``. The
    caller can render that as "n/a".
    """
    a_row = store.get_engine_metrics(a)
    b_row = store.get_engine_metrics(b)
    delta: dict[str, Optional[float]] = {}
    for col in (
        "agreement_rate",
        "boundary_accuracy",
        "slash_chord_accuracy",
        "extension_accuracy",
    ):
        a_val = (a_row or {}).get(col)
        b_val = (b_row or {}).get(col)
        if a_val is None or b_val is None:
            delta[col] = None
        else:
            delta[col] = float(b_val) - float(a_val)
    return {
        "a": dict(a_row) if a_row else None,
        "b": dict(b_row) if b_row else None,
        "delta": delta,
    }
