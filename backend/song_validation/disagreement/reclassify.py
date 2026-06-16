"""Batch reclassification.

When the classifier rules in :mod:`song_validation.disagreement.classifier`
change — a new rule lands, a threshold is tuned, a bug is fixed — every
existing ``disagreements`` row in the store carries a stale
classification label. Re-running the whole pipeline would work but is
wasteful: alignment hasn't changed, only the labels need to be
recomputed.

This module re-runs :func:`classify_alignment` over the existing
alignment_results rows in bulk. It captures the per-class counts
before and after the reclassification pass so operators can see what
the rule change actually did to the failure-class distribution.

By default it also re-aggregates metrics for every engine_version
that had at least one reclassified alignment, since
``engine_metrics.*_accuracy`` columns depend on classification counts.

Public surface:

- :func:`reclassify_all_alignments` -- whole-store sweep.
- :func:`reclassify_song`           -- single-song sweep, useful for
  spot-checking a rule change before committing to the full pass.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from ..metrics import aggregate_metrics
from ..store import Store
from .classifier import (
    LIKELY_TAB_ERROR_CONF_THRESHOLD,
    classify_alignment,
)


def _counts_snapshot(store: Store, scope_song_id: Optional[str]) -> dict:
    """Per-class counts across every ``disagreements`` row in scope.

    ``scope_song_id`` narrows the snapshot to one song; ``None`` means
    "whole store".
    """
    if scope_song_id is None:
        sql = (
            "SELECT classification, COUNT(*) "
            "FROM disagreements "
            "GROUP BY classification"
        )
        params: tuple = ()
    else:
        sql = (
            "SELECT classification, COUNT(*) "
            "FROM disagreements WHERE song_id = ? "
            "GROUP BY classification"
        )
        params = (scope_song_id,)
    with store.connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return {cls: count for cls, count in rows}


def _list_alignment_ids(
    store: Store, scope_song_id: Optional[str]
) -> list[str]:
    if scope_song_id is None:
        sql = "SELECT alignment_id FROM alignment_results"
        params: tuple = ()
    else:
        sql = "SELECT alignment_id FROM alignment_results WHERE song_id = ?"
        params = (scope_song_id,)
    with store.connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [r[0] for r in rows]


def _engine_versions_for_alignments(
    store: Store, alignment_ids: list[str]
) -> list[str]:
    """Resolve the engine_version for each alignment via its analysis
    parent. Returns a sorted unique list."""
    if not alignment_ids:
        return []
    placeholders = ",".join("?" for _ in alignment_ids)
    sql = (
        "SELECT DISTINCT ar.engine_version "
        "FROM alignment_results al "
        "JOIN analysis_results ar ON ar.analysis_id = al.analysis_id "
        f"WHERE al.alignment_id IN ({placeholders})"
    )
    with store.connect() as conn:
        rows = conn.execute(sql, alignment_ids).fetchall()
    return sorted({r[0] for r in rows if r[0] is not None})


def _delta(before: Mapping[str, int], after: Mapping[str, int]) -> dict:
    classes = set(before) | set(after)
    return {cls: int(after.get(cls, 0)) - int(before.get(cls, 0))
            for cls in sorted(classes)}


def _reclassify(
    store: Store,
    scope_song_id: Optional[str],
    *,
    likely_tab_error_threshold: float = LIKELY_TAB_ERROR_CONF_THRESHOLD,
    reaggregate_metrics: bool = True,
) -> dict[str, Any]:
    before = _counts_snapshot(store, scope_song_id)
    alignment_ids = _list_alignment_ids(store, scope_song_id)
    for al_id in alignment_ids:
        classify_alignment(
            al_id,
            store,
            likely_tab_error_threshold=likely_tab_error_threshold,
        )
    after = _counts_snapshot(store, scope_song_id)
    engine_versions = _engine_versions_for_alignments(
        store, alignment_ids
    )
    if reaggregate_metrics:
        for ev in engine_versions:
            aggregate_metrics(ev, store)
    return {
        "alignments_reclassified": len(alignment_ids),
        "before": before,
        "after": after,
        "delta": _delta(before, after),
        "engine_versions_updated": (
            engine_versions if reaggregate_metrics else []
        ),
    }


def reclassify_all_alignments(
    store: Store,
    *,
    likely_tab_error_threshold: float = LIKELY_TAB_ERROR_CONF_THRESHOLD,
    reaggregate_metrics: bool = True,
) -> dict[str, Any]:
    """Re-run the classifier over every alignment in the store.

    Returns::

        {
            "alignments_reclassified": int,
            "before": {classification: count, ...},
            "after":  {classification: count, ...},
            "delta":  {classification: int, ...},   # after - before
            "engine_versions_updated": [str, ...],  # empty if
                                                     reaggregate_metrics=False
        }

    ``likely_tab_error_threshold`` is forwarded to the underlying
    classifier; tuning it here without changing
    :data:`LIKELY_TAB_ERROR_CONF_THRESHOLD` lets operators preview the
    impact of a threshold change before committing it.
    """
    return _reclassify(
        store,
        None,
        likely_tab_error_threshold=likely_tab_error_threshold,
        reaggregate_metrics=reaggregate_metrics,
    )


def reclassify_song(
    song_id: str,
    store: Store,
    *,
    likely_tab_error_threshold: float = LIKELY_TAB_ERROR_CONF_THRESHOLD,
    reaggregate_metrics: bool = True,
) -> dict[str, Any]:
    """Like :func:`reclassify_all_alignments` but scoped to one song.

    Useful as a spot-check: tune a threshold, reclassify one song, eye
    the delta. If it looks right, run the all-alignments version.
    """
    return _reclassify(
        store,
        song_id,
        likely_tab_error_threshold=likely_tab_error_threshold,
        reaggregate_metrics=reaggregate_metrics,
    )
