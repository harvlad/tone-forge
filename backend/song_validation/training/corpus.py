"""Training corpus construction.

The directive draws a hard line: "do not train directly from all
tabs". Training candidates must come from the corpus-derived
high-confidence subset — alignment confidence high AND tab confidence
high. (Per-chord engine confidence is an optional third gate when the
analysis bundle carries it on each chord dict.)

What this module produces is *chord-progression samples* suitable
for the future harmony language model. The model itself, its
architecture, and its training loop are entirely deferred; only the
query that materialises the candidate set lands here.

Resource-isolation requirement: training jobs run on a separate GPU
pool from runtime analysis. The Python code here doesn't enforce
that — it's an infra concern — but consumers MUST NOT invoke this
module from the runtime path.

Public surface:

- :func:`iter_high_confidence_progressions` -- generator of progression
  dicts, one per qualifying analysis.
- :func:`corpus_stats` -- aggregate counts + score ranges, useful for
  answering "do we have enough data yet?".
"""

from __future__ import annotations

import json
from typing import Iterator, Mapping, Optional

from ..store import Store


DEFAULT_MIN_ALIGNMENT_SCORE = 0.8
DEFAULT_MIN_TAB_CONFIDENCE = 0.7


def _qualifying_analyses_sql() -> str:
    """SQL that returns the (analysis_id, alignment_score, source_conf)
    tuples that pass the confidence gates.

    The same analysis may be aligned against multiple tabs; we keep
    the BEST (highest-score) alignment for each analysis to avoid
    counting a song twice when several tab sources happen to agree.
    """
    return (
        "SELECT ar.analysis_id, "
        "       MAX(al.score) AS best_score, "
        "       ar.song_id, "
        "       ar.engine_version, "
        "       ar.chords, "
        "       ar.sections, "
        "       ar.key, "
        "       ar.tempo "
        "FROM analysis_results ar "
        "JOIN alignment_results al "
        "  ON al.analysis_id = ar.analysis_id "
        "JOIN tab_sources ts "
        "  ON ts.tab_id = al.tab_id "
        "WHERE al.score IS NOT NULL "
        "  AND al.score >= ? "
        "  AND (ts.source_confidence IS NULL OR ts.source_confidence >= ?) "
        "GROUP BY ar.analysis_id"
    )


def _filter_chords_by_confidence(
    chords: list, min_chord_confidence: Optional[float]
) -> list:
    """If a per-chord confidence gate is set, drop chord dicts whose
    own ``confidence`` field is below it. Chord dicts without a
    ``confidence`` key pass through (treated as "unknown -> keep")."""
    if min_chord_confidence is None:
        return chords
    out = []
    for c in chords:
        if not isinstance(c, Mapping):
            continue
        conf = c.get("confidence")
        if conf is None or float(conf) >= min_chord_confidence:
            out.append(c)
    return out


def iter_high_confidence_progressions(
    store: Store,
    *,
    min_alignment_score: float = DEFAULT_MIN_ALIGNMENT_SCORE,
    min_tab_confidence: float = DEFAULT_MIN_TAB_CONFIDENCE,
    min_chord_confidence: Optional[float] = None,
) -> Iterator[Mapping]:
    """Yield one progression dict per qualifying analysis.

    Each yielded dict::

        {
            "song_id": ...,
            "engine_version": ...,
            "key": ...,
            "tempo": ...,
            "sections": [...],
            "chords": [...],          # filtered if min_chord_confidence
            "best_alignment_score": float,
        }

    The chord list is the *engine's* output (analysis_results.chords),
    not the tab's. The tab is used only as evidence the engine got
    this song right enough to learn from.
    """
    sql = _qualifying_analyses_sql()
    with store.connect() as conn:
        rows = conn.execute(
            sql, (min_alignment_score, min_tab_confidence)
        ).fetchall()
    for row in rows:
        analysis_id, best_score, song_id, engine_version, chords_json, \
            sections_json, key_str, tempo = row
        chords = json.loads(chords_json)
        chords = _filter_chords_by_confidence(chords, min_chord_confidence)
        if not chords:
            # All chords filtered out by per-chord conf -> not useful.
            continue
        yield {
            "song_id": song_id,
            "engine_version": engine_version,
            "key": key_str,
            "tempo": tempo,
            "sections": json.loads(sections_json),
            "chords": chords,
            "best_alignment_score": float(best_score),
        }


def corpus_stats(
    store: Store,
    *,
    min_alignment_score: float = DEFAULT_MIN_ALIGNMENT_SCORE,
    min_tab_confidence: float = DEFAULT_MIN_TAB_CONFIDENCE,
    min_chord_confidence: Optional[float] = None,
) -> dict:
    """Aggregate sizing info for the high-confidence subset.

    Returns::

        {
            "matching_songs": int,
            "matching_analyses": int,
            "total_chords": int,
            "alignment_score_min": float | None,
            "alignment_score_max": float | None,
            "alignment_score_mean": float | None,
        }

    Useful for answering the directive's "do we have enough data yet?"
    question before kicking off LM training.
    """
    songs: set[str] = set()
    analyses = 0
    total_chords = 0
    scores: list[float] = []
    for prog in iter_high_confidence_progressions(
        store,
        min_alignment_score=min_alignment_score,
        min_tab_confidence=min_tab_confidence,
        min_chord_confidence=min_chord_confidence,
    ):
        songs.add(prog["song_id"])
        analyses += 1
        total_chords += len(prog["chords"])
        scores.append(prog["best_alignment_score"])
    return {
        "matching_songs": len(songs),
        "matching_analyses": analyses,
        "total_chords": total_chords,
        "alignment_score_min": min(scores) if scores else None,
        "alignment_score_max": max(scores) if scores else None,
        "alignment_score_mean": (
            sum(scores) / len(scores) if scores else None
        ),
    }
