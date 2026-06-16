"""Alignment: time-align analysis chord sequences to tab progressions.

The job: given one ``analysis_results`` row + one ``tab_sources``
row for the same ``song_id``, produce a per-timestamp mapping that
pairs each JAM-predicted chord with the tab's claim for that
moment. The output drives the disagreement classifier downstream.

Two aligners live here behind the same call signature:

- :func:`align_grid` — fixed-step grid sampler (default 0.5s). The
  cheapest-correct baseline; assumes both sides share the same
  absolute time base.
- :func:`align_dtw` — dynamic-time-warping sampler (default 0.25s
  step). Tolerates absolute-time drift between JAM and tab by
  warping the tab timeline onto JAM's. Each yields its own
  ``alignment_id`` so both can coexist for A/B comparisons.

Both write ``alignment_results`` and ``disagreements`` rows in the
same shape — pipeline, classifier, and metrics roll-up are aligner-
agnostic.
"""

from __future__ import annotations

from .dtw import DEFAULT_DTW_STEP_SEC, align_dtw
from .grid import (
    DEFAULT_GRID_STEP_SEC,
    AlignmentError,
    align_grid,
)

__all__ = [
    "align_grid",
    "align_dtw",
    "AlignmentError",
    "DEFAULT_GRID_STEP_SEC",
    "DEFAULT_DTW_STEP_SEC",
]
