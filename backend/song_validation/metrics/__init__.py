"""Metrics: aggregate per-engine-version accuracy scores.

Rolls disagreement rows up into the ``engine_metrics`` table:
agreement_rate, boundary_accuracy, slash_chord_accuracy,
extension_accuracy. Run on demand (after a corpus re-analysis pass)
to produce a per-version score card the engine improvement loop can
diff against the prior version.

Public surface: :func:`aggregate_metrics`.
"""

from __future__ import annotations

from .aggregate import aggregate_metrics

__all__ = ["aggregate_metrics"]
