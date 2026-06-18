"""Automatic Failure Mining (JAM Learning System V1 — Phase 4).

Compares JAM pipeline outputs (``jam_output``) against the consensus
reference labels (``consensus_output``) across the evidence store and
emits per-section *failure rows* describing where the engine
disagreed.

A *failure* is any disagreement between the engine and the consensus,
where the consensus crossed the corpus-trust threshold (default 0.8).
Disagreements against low-confidence consensus are excluded — those
are not engine failures, they're reference uncertainties (Phase 8's
disagreement-driven roadmap handles those separately).

Failure mining is read-only: it never mutates the evidence store. The
output is a tidy list of ``FailureRow`` rows that Phase 8 will
aggregate, and that the operator can ``--json`` for ad-hoc analysis
today.

Why a separate package?

    * The "what counts as a failure" rule is policy that may evolve
      (chord-set equivalence, transposed riff equivalence, etc.).
      Keeping it out of the evidence store and out of the consensus
      builder lets each evolve independently.
    * Phase 6's sweep acceptance gate consumes the same FailureRow
      shape to enforce "no regression > 1%". One scoring code path
      keeps the gate and the report aligned.
"""
from __future__ import annotations

from .miner import (
    FailureMiningConfig,
    FailureRow,
    mine_failures,
)


__all__ = [
    "FailureMiningConfig",
    "FailureRow",
    "mine_failures",
]
