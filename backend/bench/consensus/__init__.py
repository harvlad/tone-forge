"""Consensus Builder (JAM Learning System V1 — Phase 3).

Given multiple ``ReferenceSource`` rows for one ``(song_id,
section_id)``, decide what the canonical reference label is and how
confident we are. The output is a ``ConsensusOutput`` record appended
to the evidence store; it does *not* mutate the underlying
``ReferenceSource`` rows.

Core rule (from the directive):

    * Two sources agree on guidance_mode -> consensus_confidence = 1.0
    * Three sources agree on guidance_mode -> consensus_confidence = 1.0
    * Two sources disagree, one absent -> consensus_confidence = 0.5,
      consensus = majority value
    * All disagree -> consensus_confidence = 0.0, no consensus

The Phase 5 corpus loader rejects any consensus < 0.8 (also per the
directive: "low-confidence consensus must never enter benchmark
corpus").

Why a separate package instead of folding into evidence?

    * Phase 1 evidence is a pure data store; Phase 3 is a derivation
      step over that store. Keeping them separate prevents the store
      from accidentally encoding business rules (which fields agree,
      what counts as a vote, etc.).
    * Phase 8's Disagreement-Driven Roadmap reuses the per-field
      ``agreement`` breakdown the builder emits; that signal lives
      naturally here, not in the store.
"""
from __future__ import annotations

from .builder import (
    ConsensusBuilderConfig,
    build_consensus_for_section,
    build_consensus_for_store,
)


__all__ = [
    "ConsensusBuilderConfig",
    "build_consensus_for_section",
    "build_consensus_for_store",
]
