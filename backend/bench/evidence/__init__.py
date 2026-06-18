"""Evidence Store — Phase 1 of the JAM Learning System.

Every analysis produced by the pipeline is *capable* of generating
evidence. Evidence is the durable record that lets future detector
changes be evaluated against accumulated real-world output.

The store is intentionally additive-only:

    * Records are appended to daily JSONL files; nothing is mutated
      in place.
    * Re-running analysis on the same ``(song_id, section_id)``
      writes a *new* record — the latest record wins for "current"
      views; older records remain queryable.
    * Schema evolves via ``schema_version`` field; v1 carries
      ``jam_output`` + empty ``reference_sources`` / ``consensus_output``
      slots that later phases populate.

Phase 2 (Reference Import) appends new evidence records carrying
non-empty ``reference_sources``; Phase 3 (Consensus) appends records
carrying ``consensus_output`` + ``confidence``; Phase 4 (Failure
Mining) reads the store to surface JAM-vs-consensus disagreements;
Phase 7 (User Corrections) appends records of type ``correction``.

This subsystem deliberately avoids machine learning. It is the
*data* foundation that lets ML — or simple parameter sweeps — work
later. See ``backend/bench/evidence/schema.py`` docstring for the
record shape and ``store.py`` for the persistence contract.
"""
from __future__ import annotations

from .schema import (
    EvidenceRecord,
    ReferenceSource,
    ConsensusOutput,
    Correction,
    dump_evidence_record,
    load_evidence_record,
)
from .store import EvidenceStore
from .writer import (
    derive_song_id,
    derive_section_id,
    from_analysis_result,
)

__all__ = [
    "EvidenceRecord",
    "ReferenceSource",
    "ConsensusOutput",
    "Correction",
    "EvidenceStore",
    "derive_song_id",
    "derive_section_id",
    "from_analysis_result",
    "dump_evidence_record",
    "load_evidence_record",
]
