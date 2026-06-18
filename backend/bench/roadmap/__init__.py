"""Disagreement-Driven Roadmap report (JAM Learning System V1 — Phase 8).

Phase 4 mines per-section engine-vs-consensus disagreements
(``FailureRow``); Phase 7 captures per-section user corrections
(``Correction`` rows in the evidence store). On their own each is
a flat list of incidents. The roadmap fuses both signals into a
ranked list of *engine areas* — coarse-grained code locations the
operator should fix next.

Why a separate module rather than tacking it onto the failure
miner? Two reasons:

  1. **Different denominators.** Failures answer "where does the
     engine disagree with the corpus?" Corrections answer "where
     do users tell us we got it wrong?" Same target population
     (engine areas), but the aggregation rule and confidence
     weighting differ.

  2. **Roadmap output is a deliverable, not a debugging tool.**
     The Phase 4 CLI is for spelunking incidents; this one
     produces a JSON artifact you can paste into a planning doc
     ("here are the top 5 engine areas to fix"). Keeping them
     separate keeps both readable.

Per the directive: ranking is by aggregated evidence weight, not
recency. A correction reported once is one data point; ten
corrections of the same area are ten. The score formula mirrors
the directive's "consensus is reference, corrections are
evidence, both feed the roadmap" framing.
"""
from __future__ import annotations

from .ranker import (
    RoadmapConfig,
    RoadmapItem,
    RoadmapReport,
    build_roadmap,
    dump_roadmap,
    load_roadmap,
)


__all__ = [
    "RoadmapConfig",
    "RoadmapItem",
    "RoadmapReport",
    "build_roadmap",
    "dump_roadmap",
    "load_roadmap",
]
