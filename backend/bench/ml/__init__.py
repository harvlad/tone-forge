"""Future-ML compatibility surface for the evidence store
(JAM Learning System V1 — Phase 9).

Phase 9 is **schemas only**. We do not train models here. The
purpose of this package is to:

  1. Expose a stable, typed contract (``MLExample``) that future
     ML pipelines can consume directly without having to relearn
     the evidence schema or the consensus-confidence policy.

  2. Demonstrate that the existing evidence store satisfies the
     directive's ML-readiness criteria: JSONL streaming, additive
     schema, ``extra`` bucket for forward-compat features,
     deterministic JSON round-trip.

  3. Provide a ``stats`` / ``validate`` CLI so an operator can
     answer "how much labelled data do we have?" / "is the
     evidence store ML-loadable?" before spending real compute.

Per the directive, ML changes follow consensus accumulation —
this module is the *consumer* of that accumulation, never a
producer. It never writes to the store; it only reads.
"""
from __future__ import annotations

from .dataset import (
    MLDatasetConfig,
    MLDatasetStats,
    MLExample,
    SchemaValidationError,
    compute_dataset_stats,
    iter_ml_examples,
    validate_store_schema,
)


__all__ = [
    "MLDatasetConfig",
    "MLDatasetStats",
    "MLExample",
    "SchemaValidationError",
    "compute_dataset_stats",
    "iter_ml_examples",
    "validate_store_schema",
]
