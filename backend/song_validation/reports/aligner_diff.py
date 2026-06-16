"""Per-song cross-aligner diff.

Phase 18 introduced :func:`align_dtw` as a drop-in alternative to
:func:`align_grid`. Phase 20 tracks which aligner produced each row
via ``alignment_results.aligner_kind`` and exposes this report so
operators can answer: "For songs aligned by BOTH aligners, did DTW
actually beat grid? On WHICH songs?"

Same shape as :func:`engine_version_song_diff` — for every song that
has at least one alignment under BOTH aligners, we pick the best
alignment per (song, aligner) and emit::

    - best alignment score per aligner (highest-score alignment)
    - disagreement count on that best alignment
    - score_delta = b_score - a_score   (positive = b better)
    - disagreement_delta = b_count - a_count  (negative = b better)

Songs aligned by only one of the two aligners are listed separately
so coverage gaps are visible.
"""

from __future__ import annotations

from typing import Any, Optional

from ..store import Store


def _fetch_per_alignment_rows(
    store: Store, aligner_a: str, aligner_b: str
) -> list[tuple[str, str, str, Optional[float], int]]:
    """Return per-alignment rows for both aligners.

    Each row: ``(song_id, aligner_kind, alignment_id, score,
    disagreement_count)``. The disagreement count is computed via a
    correlated subquery so zero-disagreement alignments still appear.
    """
    sql = (
        "SELECT al.song_id, al.aligner_kind, al.alignment_id, "
        "       al.score, "
        "       (SELECT COUNT(*) FROM disagreements d "
        "        WHERE d.alignment_id = al.alignment_id) AS dcount "
        "FROM alignment_results al "
        "WHERE al.aligner_kind IN (?, ?)"
    )
    with store.connect() as conn:
        rows = conn.execute(sql, (aligner_a, aligner_b)).fetchall()
    return [
        (str(r[0]), str(r[1]), str(r[2]), r[3], int(r[4]))
        for r in rows
    ]


def _pick_best_per_song_aligner(
    rows: list[tuple[str, str, str, Optional[float], int]],
) -> dict[tuple[str, str], tuple[Optional[float], int, str]]:
    """Reduce per-alignment rows to one best row per (song, aligner).

    Best = highest score (NULL treated as worse than any number).
    Ties broken by alignment_id ASC for run-to-run determinism.

    Returns ``{(song_id, aligner_kind): (score, dcount, alignment_id)}``.
    """
    best: dict[tuple[str, str], tuple[Optional[float], int, str]] = {}
    for song_id, kind, alignment_id, score, dcount in sorted(
        rows,
        key=lambda r: (r[0], r[1], r[2]),
    ):
        key = (song_id, kind)
        cur = best.get(key)
        if cur is None:
            best[key] = (score, dcount, alignment_id)
            continue
        cur_score = cur[0]
        if cur_score is None and score is not None:
            best[key] = (score, dcount, alignment_id)
        elif (
            cur_score is not None
            and score is not None
            and score > cur_score
        ):
            best[key] = (score, dcount, alignment_id)
    return best


def aligner_diff_report(
    aligner_a: str,
    aligner_b: str,
    store: Store,
    *,
    limit: Optional[int] = None,
) -> dict[str, Any]:
    """Per-song delta between two aligner kinds.

    Args:
        aligner_a: baseline aligner_kind (e.g. ``"grid"``).
        aligner_b: comparison aligner_kind (e.g. ``"dtw"``).
        store: validation store.
        limit: optional cap on the ``songs`` list. Bucket lists
            (``improvements``, ``regressions``, ``unchanged``) are
            unaffected by this cap.

    Returns::

        {
            "aligner_a": str,
            "aligner_b": str,
            "shared_song_count": int,
            "a_only_songs": list[str],
            "b_only_songs": list[str],
            "songs": [
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
            "improvements": list[str],
            "regressions": list[str],
            "unchanged": list[str],
        }

    Songs in the ``songs`` array are ordered by
    ``disagreement_delta`` ASC then ``song_id`` ASC, so the songs
    where b improved most come first.
    """
    rows = _fetch_per_alignment_rows(store, aligner_a, aligner_b)
    best = _pick_best_per_song_aligner(rows)

    songs_under_a = {sid for (sid, k) in best if k == aligner_a}
    songs_under_b = {sid for (sid, k) in best if k == aligner_b}
    shared = songs_under_a & songs_under_b
    a_only = sorted(songs_under_a - songs_under_b)
    b_only = sorted(songs_under_b - songs_under_a)

    detail_rows: list[dict[str, Any]] = []
    improvements: list[str] = []
    regressions: list[str] = []
    unchanged: list[str] = []

    for sid in sorted(shared):
        a_score, a_dcount, _ = best[(sid, aligner_a)]
        b_score, b_dcount, _ = best[(sid, aligner_b)]
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

    detail_rows.sort(
        key=lambda r: (r["disagreement_delta"], r["song_id"])
    )
    if limit is not None:
        detail_rows = detail_rows[: int(limit)]

    return {
        "aligner_a": aligner_a,
        "aligner_b": aligner_b,
        "shared_song_count": len(shared),
        "a_only_songs": a_only,
        "b_only_songs": b_only,
        "songs": detail_rows,
        "improvements": improvements,
        "regressions": regressions,
        "unchanged": unchanged,
    }
