"""Convert ``RawReferenceFile`` -> evidence-store appends.

The ingest step is intentionally additive: each call appends *new*
``EvidenceRecord`` rows keyed by ``(song_id, section_id)`` and carrying
exactly one ``ReferenceSource`` per record. It does *not* mutate or
delete existing records; the Phase 1 invariant "multiple records per
``(song_id, section_id)`` are expected" is what makes longitudinal
analytics (did consensus shift after a re-fetch?) possible.

Idempotency note: re-ingesting the same reference file twice produces
*duplicate* records, distinguishable only by ``timestamp_utc``. The
CLI's ``--dry-run`` flag exists so curators can preview before
committing; we accept the duplicate-on-re-ingest behaviour because
de-dupe would require an index the Phase 1 store doesn't have.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from ..evidence.schema import EvidenceRecord, ReferenceSource
from ..evidence.store import EvidenceStore
from .schema import RawReferenceFile


__all__ = [
    "reference_file_to_records",
    "ingest_reference_file",
]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def reference_file_to_records(
    ref: RawReferenceFile,
    *,
    timestamp_utc: Optional[str] = None,
) -> list[EvidenceRecord]:
    """Materialise one ``EvidenceRecord`` per section in the reference file.

    Each record carries:

        * ``song_id`` / ``section_id`` from the reference file
        * ``timestamp_utc`` = caller-supplied or current UTC, so every
          record in one ingest batch shares the same timestamp
        * ``jam_output`` = empty (this is a reference-only append)
        * ``reference_sources`` = one ``ReferenceSource`` carrying the
          file-level header + this section's ``labels`` dict
        * ``consensus_output`` / ``corrections`` empty (Phase 3 / 7)

    Section ordering in the output matches the order in the input file.
    """
    ts = timestamp_utc or _utc_now_iso()
    out: list[EvidenceRecord] = []
    for section in ref.sections:
        ref_src = ReferenceSource(
            source=ref.source,
            version=ref.version,
            fetched_at_utc=ref.fetched_at_utc,
            labels=dict(section.labels),
            source_url=ref.source_url,
        )
        out.append(EvidenceRecord(
            song_id=ref.song_id,
            section_id=section.section_id,
            timestamp_utc=ts,
            jam_output={},
            reference_sources=(ref_src,),
            consensus_output=None,
            corrections=(),
            schema_version=1,
        ))
    return out


def ingest_reference_file(
    ref: RawReferenceFile,
    store: EvidenceStore,
    *,
    timestamp_utc: Optional[str] = None,
) -> int:
    """Append the reference's per-section records to ``store``.

    Returns the count of records written. Returns 0 if the reference
    file has no sections (legal but unusual — see ``RawReferenceFile``
    docstring).
    """
    records = reference_file_to_records(ref, timestamp_utc=timestamp_utc)
    store.extend(records)
    return len(records)
