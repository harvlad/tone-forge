"""Reference Import Pipeline (JAM Learning System V1 — Phase 2).

A *reference* is an external label set for a song, sourced from a tab
provider (Songsterr / Ultimate Guitar / Chordify) or a manual human
annotator. References are evidence — they may agree, disagree, or
contradict the JAM pipeline's own output. The Consensus Builder
(Phase 3) aggregates references into a confidence-weighted label that
later phases use as the benchmark corpus ground truth.

The package's responsibilities:

    * Normalize provider-specific tab JSON into ``ReferenceSource``
      records keyed to the existing evidence store's
      ``(song_id, section_id)`` rows.
    * Append the resulting evidence records — never overwrite existing
      JAM-output records, just add a sibling record with the same
      keys and the ``reference_sources`` field populated.
    * Provide a CLI (``python -m bench reference {ingest,list}``) so
      operators can curate the reference corpus by hand without
      writing Python.

Out of scope for Phase 2:

    * Live HTTP scraping of providers (rate limits, ToS, login walls).
      References are dropped into ``backend/data/references/`` as
      already-normalized JSON files; how they got there is a manual
      curation step today. A future Phase 2.1 may add adapters.
    * Cross-source disagreement scoring — that's Phase 3.
"""
from __future__ import annotations

from .schema import (
    RawReferenceFile,
    RawReferenceSection,
    load_reference_file,
    dump_reference_file,
)
from .ingest import (
    ingest_reference_file,
    reference_file_to_records,
)


__all__ = [
    "RawReferenceFile",
    "RawReferenceSection",
    "load_reference_file",
    "dump_reference_file",
    "ingest_reference_file",
    "reference_file_to_records",
]
