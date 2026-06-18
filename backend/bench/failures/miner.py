"""Mine engine-vs-consensus disagreements from the evidence store.

Algorithm:

    1. Group records by ``(song_id, section_id)``.
    2. For each key, pick the latest JAM output (latest record with
       a non-empty ``jam_output``) and the latest consensus
       (latest record with a non-None ``consensus_output``).
    3. If either is missing, skip the section.
    4. If consensus confidence < ``min_consensus_confidence``, skip
       (low-confidence consensus is not engine ground truth).
    5. For each compared field (``guidance_mode``, ``chord_sequence``),
       diff. Emit one ``FailureRow`` per disagreement.

A section may produce 0, 1, or N ``FailureRow`` rows depending on
how many fields disagreed. The aggregator at the end groups by
``failure_type`` so the operator sees counts per category.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable, Optional, Tuple

from ..evidence.schema import EvidenceRecord
from ..evidence.store import EvidenceStore


__all__ = [
    "FailureMiningConfig",
    "FailureRow",
    "mine_failures",
]


@dataclass(frozen=True)
class FailureMiningConfig:
    """Tunables for failure mining.

    The defaults reflect the directive: corpus trust threshold 0.8
    means a consensus under that bar doesn't count as ground truth,
    so disagreements there are *not* engine failures.
    """

    # Per directive: low-confidence consensus is reference
    # uncertainty, not engine failure.
    min_consensus_confidence: float = 0.8

    # Fields to compare. Each maps to a failure_type string in the
    # output. Adding new fields here automatically expands coverage
    # without touching the diff logic.
    compared_fields: Tuple[str, ...] = ("guidance_mode", "chord_sequence")


@dataclass(frozen=True)
class FailureRow:
    """One engine-vs-consensus disagreement.

    Fields:

        * ``song_id`` / ``section_id`` — coordinates into the
          evidence store.
        * ``failure_type`` — which field disagreed
          (``guidance_mode_mismatch`` / ``chord_sequence_mismatch``).
        * ``jam_value`` — what the pipeline said.
        * ``consensus_value`` — what the reference consensus said.
        * ``consensus_confidence`` — for downstream weighting in
          Phase 8 (higher-confidence consensus disagreements are
          more important to fix).
        * ``reason`` — short human-readable explanation; useful when
          dumping to a CSV / report.
    """

    song_id: str
    section_id: str
    failure_type: str
    jam_value: Any
    consensus_value: Any
    consensus_confidence: float
    reason: str


def _latest(records: Iterable[EvidenceRecord],
            predicate) -> Optional[EvidenceRecord]:
    """Return the record with the newest ``timestamp_utc`` matching ``predicate``.

    Ties broken by iteration order (caller's responsibility to
    pre-sort if it matters). ISO-8601 timestamps sort lexically =
    chronologically, so newer wins.
    """
    latest: Optional[EvidenceRecord] = None
    for rec in records:
        if not predicate(rec):
            continue
        if latest is None or rec.timestamp_utc > latest.timestamp_utc:
            latest = rec
    return latest


def _normalise_chord_seq(value: Any) -> Optional[tuple[str, ...]]:
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        return tuple(str(c) for c in value)
    return None


def _jam_value(jam_output: dict, field: str) -> Any:
    """Pull the engine's value for a compared field from jam_output."""
    if field == "guidance_mode":
        return jam_output.get("guidance_mode")
    if field == "chord_sequence":
        # JAM stores chord regions per section (not a flat sequence)
        # under "chords_in_section". Reduce to a symbol-only tuple so
        # it diffs against the consensus's chord_sequence list.
        chords = jam_output.get("chords_in_section") or []
        return tuple(
            str(c.get("symbol")) for c in chords if c.get("symbol")
        ) or None
    return jam_output.get(field)


def _consensus_value(consensus, field: str) -> Any:
    """Pull the consensus's value for a compared field."""
    if field == "guidance_mode":
        return consensus.guidance_mode
    if field == "chord_sequence":
        return consensus.chord_sequence
    return None


def _values_match(field: str, jam_v: Any, cons_v: Any) -> bool:
    if cons_v is None:
        # No consensus on this field => no failure.
        return True
    if jam_v is None:
        # Engine missing a value the consensus has = failure.
        return False
    if field == "chord_sequence":
        return _normalise_chord_seq(jam_v) == _normalise_chord_seq(cons_v)
    return jam_v == cons_v


def _failure_reason(field: str, jam_v: Any, cons_v: Any) -> str:
    if field == "guidance_mode":
        return f"engine said {jam_v!r}, consensus said {cons_v!r}"
    if field == "chord_sequence":
        return (
            f"engine chord_seq={list(_normalise_chord_seq(jam_v) or [])!r}, "
            f"consensus chord_seq={list(_normalise_chord_seq(cons_v) or [])!r}"
        )
    return f"engine {jam_v!r} != consensus {cons_v!r}"


def mine_failures(
    store: EvidenceStore,
    *,
    config: FailureMiningConfig = FailureMiningConfig(),
) -> list[FailureRow]:
    """Walk the store and return one ``FailureRow`` per disagreement.

    The output is unsorted; callers that want stable ordering should
    sort by ``(song_id, section_id, failure_type)``. Phase 6's sweep
    acceptance gate sorts; the human-facing CLI prints in
    ``failure_type`` order so similar mistakes cluster.
    """
    by_key: dict[tuple[str, str], list[EvidenceRecord]] = defaultdict(list)
    for rec in store.iter_records():
        by_key[(rec.song_id, rec.section_id)].append(rec)

    out: list[FailureRow] = []
    for (song_id, section_id), records in by_key.items():
        jam_rec = _latest(records, lambda r: bool(r.jam_output))
        cons_rec = _latest(records, lambda r: r.consensus_output is not None)
        if jam_rec is None or cons_rec is None:
            continue
        consensus = cons_rec.consensus_output
        if consensus is None:
            continue
        if consensus.confidence < config.min_consensus_confidence:
            continue
        for field in config.compared_fields:
            jam_v = _jam_value(dict(jam_rec.jam_output), field)
            cons_v = _consensus_value(consensus, field)
            if _values_match(field, jam_v, cons_v):
                continue
            out.append(FailureRow(
                song_id=song_id,
                section_id=section_id,
                failure_type=f"{field}_mismatch",
                jam_value=jam_v,
                consensus_value=cons_v,
                consensus_confidence=consensus.confidence,
                reason=_failure_reason(field, jam_v, cons_v),
            ))
    return out
