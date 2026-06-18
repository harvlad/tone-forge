"""Evidence record schema for the JAM Learning System (Phase 1).

The directive's record shape:

    {
      song_id,
      section_id,
      timestamp,
      jam_output,
      reference_sources,
      consensus_output,
      confidence,
    }

We expand this into a typed dataclass with one record per
``(song_id, section_id, timestamp)`` triple. Phase 1 only populates
``jam_output``; ``reference_sources`` / ``consensus_output`` /
``confidence`` are stub fields the later phases write.

Serialization format: one record per line, JSON, in ``.jsonl`` files
under ``backend/data/evidence/YYYY-MM-DD.jsonl``. JSONL is chosen
over a single megafile because:

    1. Appends are O(1) — no read-modify-write of a large array.
    2. Roll-over by day keeps individual files bounded.
    3. Streaming ingest in Phase 9 ML pipelines is the JSONL norm.
    4. Crash safety: a partially-written record only loses the
       trailing line; older records remain readable.

Schema is additive-only. Future fields land alongside existing ones
with sensible defaults; ``schema_version`` is bumped only when an
existing field's *meaning* changes (which should never happen).
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, List, Mapping, Optional, Tuple


__all__ = [
    "EvidenceRecord",
    "ReferenceSource",
    "ConsensusOutput",
    "Correction",
    "dump_evidence_record",
    "load_evidence_record",
]


# ---------------------------------------------------------------------------
# Sub-records carried inside EvidenceRecord. Each is its own frozen
# dataclass so a future reader can ``isinstance``-dispatch without
# parsing nested dict shapes.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReferenceSource:
    """One reference source's labels for one section.

    Populated by Phase 2 (Reference Import). The ``source`` field is
    the provider tag (``songsterr`` / ``ultimate_guitar`` / ``chordify``
    / ``manual``); ``version`` is the provider-internal revision so
    we can re-fetch deterministically; ``fetched_at_utc`` records when
    we observed this version.

    ``labels`` is a free-form dict so different providers can carry
    different shapes (Songsterr exports chord+rhythm tabs, Chordify
    exports chord+beat). The consensus builder (Phase 3) reads the
    well-known keys it understands and ignores the rest.
    """

    source: str
    version: str
    fetched_at_utc: str
    labels: Mapping[str, Any]
    # Provider-side URL or local path; preserved verbatim so a human
    # can re-open the source. Never used for join keys.
    source_url: Optional[str] = None


@dataclass(frozen=True)
class ConsensusOutput:
    """Aggregated consensus across all ``ReferenceSource`` entries.

    Phase 3 (Consensus Builder) produces one of these per section.
    ``confidence`` is the agreement score in [0, 1]; low-confidence
    consensus must never enter the benchmark corpus (per directive).

    ``agreement`` carries the per-field agreement breakdown so the
    failure-mining report (Phase 4) can attribute disagreements
    cleanly (e.g. "guidance_mode unanimous, chord_seq split 2/3").
    """

    guidance_mode: Optional[str]
    chord_sequence: Optional[Tuple[str, ...]]
    confidence: float
    agreement: Mapping[str, float]
    # The vote breakdown: ``{"guidance_mode": {"chord": 2, "riff": 1}, ...}``
    # so the failure miner can recover which sources voted what.
    votes: Mapping[str, Mapping[str, int]] = field(default_factory=dict)


@dataclass(frozen=True)
class Correction:
    """One user correction (Phase 7).

    Stored as evidence — *not* applied to detector behaviour. Engine
    changes follow evidence accumulation, not individual corrections.
    """

    correction_type: str          # "guidance_mode" / "chord" / "key" / ...
    previous_value: Any
    corrected_value: Any
    user_id: Optional[str] = None  # opaque; may be null for anonymous
    note: Optional[str] = None     # free-form comment


# ---------------------------------------------------------------------------
# Top-level evidence record.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EvidenceRecord:
    """One ``(song_id, section_id, timestamp)`` evidence row.

    Multiple records for the same ``(song_id, section_id)`` are
    *expected*: each pipeline run, each reference import, each
    consensus rebuild appends a new record. Latest record wins for
    "current state" reads; older records remain queryable for
    longitudinal analysis (did detector accuracy improve over time
    on this song?).

    Field invariants:

        * ``schema_version`` must equal the constant ``SCHEMA_VERSION``
          this module exports; readers reject mismatches.
        * ``song_id`` is content-derived and stable across re-runs.
        * ``section_id`` is ``{song_id}:{section_idx:04d}`` so ordering
          is preserved lexicographically.
        * ``timestamp_utc`` is ISO-8601 in UTC; sorts lexicographically.
        * ``jam_output`` may be empty when this record is a reference-
          only or correction-only append.
        * ``reference_sources`` is empty until Phase 2 populates it.
        * ``consensus_output`` is None until Phase 3 populates it.
        * ``corrections`` is empty until Phase 7 captures any.
        * ``extra`` is a free-form forward-compat bucket — Phase 9 ML
          consumers can land model-specific fields here without
          bumping the top-level schema.
    """

    SCHEMA_VERSION: int = 1

    song_id: str = ""
    section_id: str = ""
    timestamp_utc: str = ""
    jam_output: Mapping[str, Any] = field(default_factory=dict)
    reference_sources: Tuple[ReferenceSource, ...] = ()
    consensus_output: Optional[ConsensusOutput] = None
    corrections: Tuple[Correction, ...] = ()
    schema_version: int = 1
    # Free-form bucket. Phase 9 ML pipelines can attach feature
    # vectors, audio fingerprints, etc. without touching this dataclass.
    extra: Mapping[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Serialisation. JSON-only, no pickle, no Path/datetime objects.
# ---------------------------------------------------------------------------


def _ref_to_jsonable(ref: ReferenceSource) -> dict:
    return {
        "source": ref.source,
        "version": ref.version,
        "fetched_at_utc": ref.fetched_at_utc,
        "labels": dict(ref.labels),
        "source_url": ref.source_url,
    }


def _consensus_to_jsonable(c: ConsensusOutput) -> dict:
    return {
        "guidance_mode": c.guidance_mode,
        "chord_sequence": list(c.chord_sequence) if c.chord_sequence is not None else None,
        "confidence": float(c.confidence),
        "agreement": dict(c.agreement),
        "votes": {k: dict(v) for k, v in c.votes.items()},
    }


def _correction_to_jsonable(c: Correction) -> dict:
    return {
        "correction_type": c.correction_type,
        "previous_value": c.previous_value,
        "corrected_value": c.corrected_value,
        "user_id": c.user_id,
        "note": c.note,
    }


def _record_to_jsonable(record: EvidenceRecord) -> dict:
    """Convert ``EvidenceRecord`` -> nested plain dict.

    Key ordering is fixed so the JSONL files diff cleanly across
    runs (the timestamp varies, but field positions don't).
    """
    return {
        "schema_version": int(record.schema_version),
        "song_id": record.song_id,
        "section_id": record.section_id,
        "timestamp_utc": record.timestamp_utc,
        "jam_output": dict(record.jam_output),
        "reference_sources": [_ref_to_jsonable(r) for r in record.reference_sources],
        "consensus_output": (
            _consensus_to_jsonable(record.consensus_output)
            if record.consensus_output is not None else None
        ),
        "corrections": [_correction_to_jsonable(c) for c in record.corrections],
        "extra": dict(record.extra),
    }


def _jsonable_to_record(data: Mapping[str, Any]) -> EvidenceRecord:
    schema = int(data.get("schema_version", 1))
    if schema != 1:
        raise ValueError(
            f"unsupported evidence schema_version={schema}; "
            "this build supports schema_version=1"
        )
    refs: List[ReferenceSource] = []
    for raw in data.get("reference_sources", []):
        refs.append(ReferenceSource(
            source=str(raw["source"]),
            version=str(raw["version"]),
            fetched_at_utc=str(raw["fetched_at_utc"]),
            labels=dict(raw.get("labels", {})),
            source_url=raw.get("source_url"),
        ))
    consensus_raw = data.get("consensus_output")
    consensus: Optional[ConsensusOutput]
    if consensus_raw is None:
        consensus = None
    else:
        seq_raw = consensus_raw.get("chord_sequence")
        consensus = ConsensusOutput(
            guidance_mode=consensus_raw.get("guidance_mode"),
            chord_sequence=tuple(seq_raw) if seq_raw is not None else None,
            confidence=float(consensus_raw.get("confidence", 0.0)),
            agreement=dict(consensus_raw.get("agreement", {})),
            votes={k: dict(v) for k, v in consensus_raw.get("votes", {}).items()},
        )
    corrections: List[Correction] = []
    for raw in data.get("corrections", []):
        corrections.append(Correction(
            correction_type=str(raw["correction_type"]),
            previous_value=raw.get("previous_value"),
            corrected_value=raw.get("corrected_value"),
            user_id=raw.get("user_id"),
            note=raw.get("note"),
        ))
    return EvidenceRecord(
        song_id=str(data["song_id"]),
        section_id=str(data["section_id"]),
        timestamp_utc=str(data["timestamp_utc"]),
        jam_output=dict(data.get("jam_output", {})),
        reference_sources=tuple(refs),
        consensus_output=consensus,
        corrections=tuple(corrections),
        schema_version=schema,
        extra=dict(data.get("extra", {})),
    )


def dump_evidence_record(record: EvidenceRecord, path: Path | str) -> Path:
    """Append ``record`` as one JSONL line to ``path``.

    Creates parent directories. The file is opened in append mode so
    concurrent processes targeting the same daily file are
    line-atomic on POSIX (single ``write()`` of <PIPE_BUF bytes).
    Records routinely fit well within PIPE_BUF (4096 bytes on Linux,
    512 on macOS); the few that don't would risk interleaving but
    Phase 1 has a single-writer model.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(_record_to_jsonable(record), sort_keys=False, separators=(",", ":"))
    with target.open("a", encoding="utf-8") as fh:
        fh.write(line)
        fh.write("\n")
    return target


def load_evidence_record(line: str) -> EvidenceRecord:
    """Parse one JSONL line back into an ``EvidenceRecord``.

    Use ``EvidenceStore.iter_records`` for whole-file iteration;
    this helper is exposed for tests and ad-hoc tooling.
    """
    return _jsonable_to_record(json.loads(line))
