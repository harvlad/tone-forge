"""Consensus-derived benchmark corpus (JAM Learning System V1 — Phase 5).

Turns the evidence store into a benchmark corpus: every section with a
consensus_output meeting the trust bar becomes a corpus entry. This is
the "4 → 50+" expansion path from the directive — instead of
hand-curating audio + chord-region JSON fixtures, the corpus grows
automatically as references arrive and consensus builds.

Why not extend the existing ``bench.corpus`` loader?

    * The chord_groundtruth fixtures at
      ``backend/tests/fixtures/chord_groundtruth/`` are *audio-bound*:
      each carries an audio stem path and per-region chord labels for
      chord-recognition benchmarking. The benchmark runner re-runs
      the chord detector against the audio at every commit.
    * Consensus corpus entries are *label-only*: derived from
      analyses already cached in ``backend/data/history.json`` /
      ``backend/data/evidence/``, with reference labels merged in.
      There is no audio to re-decode; the regression check compares
      the cached jam_output against the consensus.
    * Mixing both into one loader would conflate "what to run the
      detector on" with "what to compare the detector's cached
      output against". Keeping them separate keeps each invariant
      clean.

Phase 6's sweep gate consumes both corpora in parallel: chord
fixtures for re-decoded chord-detector regressions, consensus entries
for guidance-mode / chord-sequence consistency over the historical
analysis fleet.
"""
from __future__ import annotations

from .loader import (
    ConsensusCorpusConfig,
    ConsensusCorpusEntry,
    iter_consensus_corpus,
    summarise_consensus_corpus,
)


__all__ = [
    "ConsensusCorpusConfig",
    "ConsensusCorpusEntry",
    "iter_consensus_corpus",
    "summarise_consensus_corpus",
]
