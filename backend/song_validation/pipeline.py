"""End-to-end validation pipeline orchestrator.

Chains the four offline phases into one call:

    align_grid -> classify_alignment -> aggregate_metrics

This is the entry point an HTTP/worker layer should call after a new
analysis bundle or tab arrives. It is intentionally synchronous and
side-effect-only: every result is written to the validation store,
nothing is returned to a "live" caller. Async batching is the caller's
problem.

The orchestrator skips alignments that already exist (idempotent
fingerprint via ``align_grid``'s content-addressed ID surfaces as a
``sqlite3.IntegrityError`` which we catch and treat as "already
processed"). That makes it safe to re-run after a partial crash.
"""

from __future__ import annotations

import sqlite3
from typing import Optional, Sequence

from .alignment import align_grid
from .alignment.grid import DEFAULT_GRID_STEP_SEC, _derive_alignment_id
from .disagreement import classify_alignment
from .metrics import aggregate_metrics
from .store import Store


class PipelineError(ValueError):
    """Raised when pipeline inputs are inconsistent."""


def _analyses_for_song(store: Store, song_id: str) -> list[str]:
    with store.connect() as conn:
        rows = conn.execute(
            "SELECT analysis_id FROM analysis_results WHERE song_id = ?",
            (song_id,),
        ).fetchall()
    return [r[0] for r in rows]


def _tabs_for_song(store: Store, song_id: str) -> list[str]:
    with store.connect() as conn:
        rows = conn.execute(
            "SELECT tab_id FROM tab_sources WHERE song_id = ?",
            (song_id,),
        ).fetchall()
    return [r[0] for r in rows]


def _engine_version_for_analysis(
    store: Store, analysis_id: str
) -> Optional[str]:
    row = store.get_analysis_result(analysis_id)
    return row["engine_version"] if row is not None else None


def validate_song(
    song_id: str,
    store: Store,
    *,
    step_sec: float = DEFAULT_GRID_STEP_SEC,
) -> dict:
    """Run the full pipeline for one song.

    Cross-joins every ``analysis_results`` row with every
    ``tab_sources`` row for ``song_id`` (typically 1 of each in early
    development, but the cross-join is correct general-case). For each
    (analysis, tab) pair: align, classify, then aggregate metrics for
    the analysis's engine_version.

    Returns a summary dict::

        {
            "song_id": ...,
            "alignments": [alignment_id, ...],   # newly created
            "skipped": [alignment_id, ...],      # already existed
            "engine_versions_updated": [...],    # had metrics rerolled
        }
    """
    analyses = _analyses_for_song(store, song_id)
    tabs = _tabs_for_song(store, song_id)
    if not analyses:
        raise PipelineError(
            f"no analysis_results rows for song_id={song_id!r}"
        )
    if not tabs:
        raise PipelineError(
            f"no tab_sources rows for song_id={song_id!r}"
        )

    created: list[str] = []
    skipped: list[str] = []
    engine_versions: set[str] = set()

    for analysis_id in analyses:
        ev = _engine_version_for_analysis(store, analysis_id)
        if ev is not None:
            engine_versions.add(ev)
        for tab_id in tabs:
            try:
                al_id = align_grid(
                    analysis_id, tab_id, store, step_sec=step_sec
                )
                created.append(al_id)
            except sqlite3.IntegrityError:
                # Same (analysis, tab, step) was previously aligned.
                # Treat as idempotent: re-derive the ID and proceed
                # straight to (re)classification + aggregation.
                al_id = _derive_alignment_id(analysis_id, tab_id, step_sec)
                skipped.append(al_id)
            classify_alignment(al_id, store)

    for ev in engine_versions:
        aggregate_metrics(ev, store)

    return {
        "song_id": song_id,
        "alignments": created,
        "skipped": skipped,
        "engine_versions_updated": sorted(engine_versions),
    }


def validate_songs(
    song_ids: Sequence[str],
    store: Store,
    *,
    step_sec: float = DEFAULT_GRID_STEP_SEC,
) -> list[dict]:
    """Run :func:`validate_song` for each id; collect results.

    Errors from individual songs do not halt the batch — they're
    captured in the per-song result dict under ``"error"``.
    """
    out: list[dict] = []
    for sid in song_ids:
        try:
            out.append(validate_song(sid, store, step_sec=step_sec))
        except PipelineError as exc:
            out.append({"song_id": sid, "error": str(exc)})
    return out
