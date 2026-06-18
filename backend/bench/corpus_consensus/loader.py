"""Consensus-derived corpus loader (Phase 5).

Walks the evidence store, finds every section with a high-confidence
``ConsensusOutput`` plus a cached ``jam_output``, and emits one
``ConsensusCorpusEntry`` per qualifying section. That's the
"4 -> 50+" expansion: instead of hand-curating audio + JSON fixtures,
we lift the consensus rows directly into a benchmark corpus.

The shape is intentionally narrow:

    * No audio path. Phase 6's sweep gate compares the cached
      ``jam_output`` against the reference labels carried by the
      consensus. There's no re-decode in this corpus.
    * No tempo / arrangement metadata beyond what the consensus
      carries. We're benchmarking guidance + chord-sequence decisions,
      not section detection.
    * Trust gate is the consensus ``confidence`` and matches the
      directive's "trust threshold 0.8" default. Anything below the
      bar is reference uncertainty, not corpus material.

Selecting one entry per section:

    * For each ``(song_id, section_id)`` pick the latest consensus
      record (newest ``timestamp_utc``).
    * Pair it with the latest ``jam_output`` for the same section.
      If no jam_output exists yet, the entry still emits but
      ``latest_jam_output`` is ``None`` — Phase 6's sweep gate
      treats that as "engine hasn't run on this section yet".
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator, Mapping, Optional, Tuple

from ..evidence.schema import ConsensusOutput, EvidenceRecord
from ..evidence.store import EvidenceStore


__all__ = [
    "ConsensusCorpusConfig",
    "ConsensusCorpusEntry",
    "iter_consensus_corpus",
    "summarise_consensus_corpus",
]


@dataclass(frozen=True)
class ConsensusCorpusConfig:
    """Tunables for corpus selection.

    ``min_confidence`` defaults to 0.8 per the directive: anything
    below is reference uncertainty, not benchmark ground truth.
    ``require_jam_output`` defaults to ``False`` so the corpus can
    surface sections where the engine hasn't run yet (Phase 6's
    sweep gate can then decide whether to skip or re-run).
    """

    min_confidence: float = 0.8
    require_jam_output: bool = False
    # If set, restrict to one song. Useful for incremental rebuilds.
    song_id: Optional[str] = None


@dataclass(frozen=True)
class ConsensusCorpusEntry:
    """One corpus row: a section's consensus labels + last jam output.

    Fields:

        * ``song_id`` / ``section_id`` — coordinates back into the
          evidence store.
        * ``ref_guidance_mode`` / ``ref_chord_sequence`` —
          consensus-decided labels (may be ``None`` if consensus
          didn't decide that field but decided enough others to
          clear the confidence bar).
        * ``ref_confidence`` — the consensus's overall confidence.
        * ``ref_agreement`` — per-field agreement breakdown so the
          sweep gate can weight individual fields.
        * ``latest_jam_output`` — last engine output for the section,
          or ``None`` if the engine hasn't analysed it yet.
        * ``consensus_timestamp_utc`` / ``jam_timestamp_utc`` — when
          each side was recorded; useful for staleness tracking.
    """

    song_id: str
    section_id: str
    ref_guidance_mode: Optional[str]
    ref_chord_sequence: Optional[Tuple[str, ...]]
    ref_confidence: float
    ref_agreement: Mapping[str, float] = field(default_factory=dict)
    latest_jam_output: Optional[Mapping[str, Any]] = None
    consensus_timestamp_utc: str = ""
    jam_timestamp_utc: Optional[str] = None


def _pick_latest(
    records: Iterable[EvidenceRecord],
    predicate,
) -> Optional[EvidenceRecord]:
    latest: Optional[EvidenceRecord] = None
    for rec in records:
        if not predicate(rec):
            continue
        if latest is None or rec.timestamp_utc > latest.timestamp_utc:
            latest = rec
    return latest


def _consensus_to_entry(
    *,
    song_id: str,
    section_id: str,
    consensus_rec: EvidenceRecord,
    jam_rec: Optional[EvidenceRecord],
) -> ConsensusCorpusEntry:
    consensus = consensus_rec.consensus_output
    assert consensus is not None  # caller guarantees
    return ConsensusCorpusEntry(
        song_id=song_id,
        section_id=section_id,
        ref_guidance_mode=consensus.guidance_mode,
        ref_chord_sequence=consensus.chord_sequence,
        ref_confidence=float(consensus.confidence),
        ref_agreement=dict(consensus.agreement),
        latest_jam_output=(
            dict(jam_rec.jam_output) if jam_rec is not None else None
        ),
        consensus_timestamp_utc=consensus_rec.timestamp_utc,
        jam_timestamp_utc=(jam_rec.timestamp_utc if jam_rec is not None else None),
    )


def iter_consensus_corpus(
    store: EvidenceStore,
    *,
    config: ConsensusCorpusConfig = ConsensusCorpusConfig(),
) -> Iterator[ConsensusCorpusEntry]:
    """Yield one corpus entry per qualifying ``(song_id, section_id)``.

    Order is unspecified (depends on the store's iteration order);
    callers that need stable ordering should sort by
    ``(song_id, section_id)``.
    """
    by_key: dict[tuple[str, str], list[EvidenceRecord]] = defaultdict(list)
    for rec in store.iter_records():
        if config.song_id is not None and rec.song_id != config.song_id:
            continue
        by_key[(rec.song_id, rec.section_id)].append(rec)

    for (song_id, section_id), records in by_key.items():
        consensus_rec = _pick_latest(
            records, lambda r: r.consensus_output is not None
        )
        if consensus_rec is None:
            continue
        consensus: ConsensusOutput = consensus_rec.consensus_output  # type: ignore[assignment]
        if consensus.confidence < config.min_confidence:
            continue
        jam_rec = _pick_latest(records, lambda r: bool(r.jam_output))
        if config.require_jam_output and jam_rec is None:
            continue
        yield _consensus_to_entry(
            song_id=song_id,
            section_id=section_id,
            consensus_rec=consensus_rec,
            jam_rec=jam_rec,
        )


def summarise_consensus_corpus(
    store: EvidenceStore,
    *,
    config: ConsensusCorpusConfig = ConsensusCorpusConfig(),
) -> dict:
    """Return a counts-only summary of the consensus corpus.

    Output shape:

        {
            "n_entries": int,
            "n_unique_songs": int,
            "n_with_jam_output": int,
            "n_without_jam_output": int,
            "by_guidance_mode": {"chord": N, "riff": M, ...},
            "min_confidence": float,
            "mean_confidence": float,
        }

    Useful for the CLI ``stats`` subcommand and for the Phase 6
    sweep gate's pre-flight "how big is the corpus this run?"
    visibility.
    """
    entries = list(iter_consensus_corpus(store, config=config))
    if not entries:
        return {
            "n_entries": 0,
            "n_unique_songs": 0,
            "n_with_jam_output": 0,
            "n_without_jam_output": 0,
            "by_guidance_mode": {},
            "min_confidence": 0.0,
            "mean_confidence": 0.0,
        }
    confidences = [e.ref_confidence for e in entries]
    by_mode: dict[str, int] = defaultdict(int)
    for e in entries:
        key = e.ref_guidance_mode if e.ref_guidance_mode is not None else "<undecided>"
        by_mode[key] += 1
    n_with_jam = sum(1 for e in entries if e.latest_jam_output is not None)
    return {
        "n_entries": len(entries),
        "n_unique_songs": len({e.song_id for e in entries}),
        "n_with_jam_output": n_with_jam,
        "n_without_jam_output": len(entries) - n_with_jam,
        "by_guidance_mode": dict(by_mode),
        "min_confidence": min(confidences),
        "mean_confidence": sum(confidences) / len(confidences),
    }
