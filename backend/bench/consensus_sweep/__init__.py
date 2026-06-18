"""Consensus-corpus regression gate (JAM Learning System V1 — Phase 6).

Phase 5 (``bench.corpus_consensus``) lifted the evidence store's
consensus rows into a label-only benchmark corpus. Phase 6 turns
that corpus into a regression gate: a one-shot scorer that measures
the current engine's agreement against the consensus, plus an
acceptance gate that compares candidate-vs-baseline scores and
emits accept/reject per the directive's "corpus improves, no
regression >1%, runtime within threshold" rule.

Why separate from ``bench.sweep``?

    * ``bench.sweep`` operates on the audio-bound chord_groundtruth
      corpus: re-decodes audio per fixture, computes WCSR-style
      duration-weighted metrics, drives a parameter-overlay
      enumeration loop.
    * The consensus corpus is label-only: every section already
      has a cached ``jam_output`` from the last pipeline run, so
      "scoring" is just comparing cached outputs against the
      consensus labels. No re-decode, no parameter overlay.
    * The acceptance criteria are different metrics
      (guidance_mode_match_rate, chord_sequence_match_rate) on
      different units (per-section binary match, not duration-
      weighted continuous WCSR).

Two infrastructures, one directive: both gates must pass for an
engine change to be accepted in CI. Wiring the consensus gate
into the same parameter-sweep loop is a follow-up milestone —
Phase 6's narrower scope is "we have a gate the engine commits
have to clear".
"""
from __future__ import annotations

from .gate import (
    ConsensusAcceptanceConfig,
    ConsensusAcceptanceVerdict,
    evaluate_consensus_acceptance,
)
from .scorer import (
    ConsensusCorpusScore,
    ConsensusEntryScore,
    ConsensusScoreConfig,
    dump_consensus_score,
    load_consensus_score,
    score_consensus_corpus,
    score_entry,
)


__all__ = [
    "ConsensusAcceptanceConfig",
    "ConsensusAcceptanceVerdict",
    "ConsensusCorpusScore",
    "ConsensusEntryScore",
    "ConsensusScoreConfig",
    "dump_consensus_score",
    "evaluate_consensus_acceptance",
    "load_consensus_score",
    "score_consensus_corpus",
    "score_entry",
]
