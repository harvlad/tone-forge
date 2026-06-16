"""Per-song cross-engine diff.

``engine_version_diff`` (in :mod:`song_validation.reports.queries`)
compares the rolled-up score cards of two engine versions. It tells
you "did v1.1's agreement_rate go up?" — but not "WHICH songs got
better, which got worse?". That song-level signal is what catches
regressions: a global improvement can hide a few songs that v1.1
mangled badly.

:func:`engine_version_song_diff` fills that gap. For every song
that has at least one analysis under BOTH versions, it computes::

    - best alignment score per version (highest-score alignment)
    - disagreement count on that best alignment
    - score_delta = b_score - a_score   (positive = b better)
    - disagreement_delta = b_count - a_count  (negative = b better)

and classifies each song as ``improvements`` (fewer disagreements
under b), ``regressions`` (more disagreements under b), or
``unchanged`` (same count). Songs analysed by only one of the two
versions are listed separately so the operator can see what
coverage gaps the comparison can't speak to.

Returned shape is JSON-renderable, mirrors the rest of
``song_validation.reports``.
"""

from __future__ import annotations

from typing import Any, Optional

from ..store import Store


def _fetch_per_alignment_rows(
    store: Store, version_a: str, version_b: str
) -> list[tuple[str, str, str, Optional[float], int]]:
    """Return per-alignment rows for both versions.

    Each row: ``(song_id, engine_version, alignment_id, score,
    disagreement_count)``. The disagreement count is computed via a
    correlated subquery so songs with zero-disagreement alignments
    still appear (with count 0).
    """
    sql = (
        "SELECT ar.song_id, ar.engine_version, al.alignment_id, "
        "       al.score, "
        "       (SELECT COUNT(*) FROM disagreements d "
        "        WHERE d.alignment_id = al.alignment_id) AS dcount "
        "FROM analysis_results ar "
        "JOIN alignment_results al "
        "  ON al.analysis_id = ar.analysis_id "
        "WHERE ar.engine_version IN (?, ?)"
    )
    with store.connect() as conn:
        rows = conn.execute(sql, (version_a, version_b)).fetchall()
    return [
        (str(r[0]), str(r[1]), str(r[2]), r[3], int(r[4]))
        for r in rows
    ]


def _pick_best_per_song_version(
    rows: list[tuple[str, str, str, Optional[float], int]],
) -> dict[tuple[str, str], tuple[Optional[float], int, str]]:
    """Reduce per-alignment rows to one best row per (song, version).

    Best = highest score (NULL treated as worse than any number).
    Ties broken by alignment_id ASC for run-to-run determinism so
    successive reports diff cleanly.

    Returns ``{(song_id, version): (score, dcount, alignment_id)}``.
    """
    best: dict[tuple[str, str], tuple[Optional[float], int, str]] = {}
    # Process in (song, version, alignment_id) order so that on score
    # ties we keep the smallest alignment_id deterministically.
    for song_id, version, alignment_id, score, dcount in sorted(
        rows,
        key=lambda r: (r[0], r[1], r[2]),
    ):
        key = (song_id, version)
        cur = best.get(key)
        if cur is None:
            best[key] = (score, dcount, alignment_id)
            continue
        cur_score = cur[0]
        # NULL score is worse than any concrete score.
        if cur_score is None and score is not None:
            best[key] = (score, dcount, alignment_id)
        elif (
            cur_score is not None
            and score is not None
            and score > cur_score
        ):
            best[key] = (score, dcount, alignment_id)
        # Otherwise keep cur (ties or worse score) — preserves the
        # deterministic alignment_id ordering.
    return best


def engine_version_song_diff(
    version_a: str,
    version_b: str,
    store: Store,
    *,
    limit: Optional[int] = None,
) -> dict[str, Any]:
    """Per-song delta between two engine versions.

    Args:
        version_a: baseline engine_version string.
        version_b: comparison engine_version string.
        store: validation store.
        limit: optional cap on the size of the ``songs`` list. Buckets
            (``improvements``, ``regressions``, ``unchanged``) still
            list every shared song's id regardless of this cap so the
            operator gets a faithful summary even when only a window
            of detailed rows is materialised.

    Returns::

        {
            "version_a": str,
            "version_b": str,
            "shared_song_count": int,
            "a_only_songs": list[str],   # only analysed under a
            "b_only_songs": list[str],   # only analysed under b
            "songs": [                  # one entry per shared song
                {
                    "song_id": str,
                    "a_score": float | None,
                    "b_score": float | None,
                    "score_delta": float | None,
                    "a_disagreement_count": int,
                    "b_disagreement_count": int,
                    "disagreement_delta": int,
                },
                ...
            ],
            "improvements": list[str],  # b has fewer disagreements
            "regressions": list[str],   # b has more disagreements
            "unchanged": list[str],     # same disagreement count
        }

    Songs listed in the ``songs`` array are ordered by
    ``disagreement_delta`` ASC then ``song_id`` ASC, so the most
    improved songs (largest negative delta) come first, then ties
    in alphabetical order. Operators eyeball the head for wins and
    the tail for regressions.
    """
    rows = _fetch_per_alignment_rows(store, version_a, version_b)
    best = _pick_best_per_song_version(rows)

    songs_under_a = {sid for (sid, v) in best if v == version_a}
    songs_under_b = {sid for (sid, v) in best if v == version_b}
    shared = songs_under_a & songs_under_b
    a_only = sorted(songs_under_a - songs_under_b)
    b_only = sorted(songs_under_b - songs_under_a)

    detail_rows: list[dict[str, Any]] = []
    improvements: list[str] = []
    regressions: list[str] = []
    unchanged: list[str] = []

    for sid in sorted(shared):
        a_score, a_dcount, _ = best[(sid, version_a)]
        b_score, b_dcount, _ = best[(sid, version_b)]
        if a_score is None or b_score is None:
            score_delta: Optional[float] = None
        else:
            score_delta = float(b_score) - float(a_score)
        d_delta = int(b_dcount) - int(a_dcount)
        detail_rows.append(
            {
                "song_id": sid,
                "a_score": (
                    float(a_score) if a_score is not None else None
                ),
                "b_score": (
                    float(b_score) if b_score is not None else None
                ),
                "score_delta": score_delta,
                "a_disagreement_count": int(a_dcount),
                "b_disagreement_count": int(b_dcount),
                "disagreement_delta": d_delta,
            }
        )
        if d_delta < 0:
            improvements.append(sid)
        elif d_delta > 0:
            regressions.append(sid)
        else:
            unchanged.append(sid)

    # Order detail rows: best improvement (most-negative delta) first,
    # then ties in alphabetical song_id order.
    detail_rows.sort(
        key=lambda r: (r["disagreement_delta"], r["song_id"])
    )
    if limit is not None:
        detail_rows = detail_rows[: int(limit)]

    return {
        "version_a": version_a,
        "version_b": version_b,
        "shared_song_count": len(shared),
        "a_only_songs": a_only,
        "b_only_songs": b_only,
        "songs": detail_rows,
        "improvements": improvements,
        "regressions": regressions,
        "unchanged": unchanged,
    }
