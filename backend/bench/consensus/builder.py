"""Build ``ConsensusOutput`` records from multi-source reference evidence.

The builder takes every ``ReferenceSource`` row visible for one
``(song_id, section_id)`` and produces a single ``ConsensusOutput``
describing the agreement state. It is *idempotent* in the sense that
re-running over the same store appends a fresh consensus record but
never deletes the prior one — the schema's "multiple records per
section" invariant lets us track how consensus shifts as more
references arrive.

Field-by-field scoring rule:

    1. Each well-known label key (``guidance_mode``, ``chord_sequence``,
       ``tempo_bpm``) gets its own vote tally across sources.
    2. ``guidance_mode`` is a categorical vote: majority wins; ties
       resolve to None (no consensus) with confidence 0.0.
    3. ``chord_sequence`` is a tuple vote: exact-match counted; on tie
       falls back to None.
    4. ``tempo_bpm`` is bucketed at 1 BPM granularity, then voted.
    5. Top-level ``confidence`` is the *minimum* per-field agreement
       ratio across all keys the consensus actually decided. Reason:
       if four sources agree on guidance_mode but split 2/2 on chord
       sequence, the consensus is still weak — the corpus loader
       should know that.
    6. Per-field ``agreement`` records each key's agreement ratio
       independently so Phase 8 can attribute disagreement cleanly.
    7. ``votes`` records the raw tally
       (e.g. ``{"guidance_mode": {"chord": 2, "riff": 1}}``) so the
       failure miner can recover which sources voted what without
       re-iterating the evidence store.

Edge cases:

    * One source for a section -> consensus is whatever that source
      says, confidence = 1.0 with a single-source caveat in
      ``agreement``. Phase 5's corpus loader can decide whether to
      trust a singleton (the default policy gates at >= 2 sources
      AND confidence >= 0.8).
    * Zero sources -> no consensus written; the section drops out of
      the build cycle silently.
    * Same source contributing twice with different versions -> both
      count as separate votes. This is intentional: the curator
      bumped the version because the underlying tab changed, so the
      two are legitimately different observations.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional

from ..evidence.schema import ConsensusOutput, EvidenceRecord
from ..evidence.store import EvidenceStore


__all__ = [
    "ConsensusBuilderConfig",
    "build_consensus_for_section",
    "build_consensus_for_store",
]


@dataclass(frozen=True)
class ConsensusBuilderConfig:
    """Tunables for the consensus rule.

    Defaults match the directive: agreement < 0.5 -> "no consensus";
    Phase 5 corpus loader further requires >= 0.8 to enter the
    benchmark corpus.
    """

    # Keys we actually score. Other label keys carry through to the
    # ``agreement`` map as ``-1.0`` (= "not scored") if a future Phase
    # 5 cares to inspect them.
    scored_keys: tuple[str, ...] = ("guidance_mode", "chord_sequence", "tempo_bpm")

    # BPM bucket size for tempo voting. 1.0 means tempo-aware
    # providers must agree within 1 BPM for the vote to count as a
    # match. A future phase may switch to fractional buckets if the
    # corpus turns out to be tempo-precise.
    tempo_bucket_bpm: float = 1.0

    # Floor for a "decided" field: if no value gets > floor of the
    # vote weight, the field stays None. 0.5 means "majority needed".
    field_decision_floor: float = 0.5


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _bucket_tempo(value: Any, bucket: float) -> Optional[str]:
    """Bucket a tempo value to a stable string key for voting."""
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    bucketed = round(v / bucket) * bucket
    return f"{bucketed:.3f}"


def _normalize_chord_sequence(value: Any) -> Optional[tuple[str, ...]]:
    """Cast a chord sequence to a hashable tuple for voting."""
    if value is None:
        return None
    if not isinstance(value, (list, tuple)):
        return None
    return tuple(str(c) for c in value)


def _collect_labels(
    records: Iterable[EvidenceRecord],
) -> list[Mapping[str, Any]]:
    """Flatten every ``ReferenceSource.labels`` mapping across records.

    Note: multiple ``ReferenceSource`` entries from the *same* record
    are all counted (a record may carry several sources in a single
    append, though Phase 2 ingest currently emits one per record).
    """
    out: list[Mapping[str, Any]] = []
    for rec in records:
        for ref in rec.reference_sources:
            out.append(ref.labels)
    return out


def _vote_categorical(
    label_dicts: list[Mapping[str, Any]],
    key: str,
    floor: float,
) -> tuple[Optional[Any], dict[str, int], float]:
    """Return (winning_value, vote_tally, agreement_ratio).

    Values are stringified for the tally to keep dict keys hashable;
    the winning value is returned in its original Python type so the
    caller can decide whether to cast (e.g. tuple-ify chord_sequence
    before stashing into ``ConsensusOutput``).
    """
    raw_values = [d.get(key) for d in label_dicts if d.get(key) is not None]
    if not raw_values:
        return None, {}, 0.0
    # Bucket values for hashable counting.
    bucketed = [_stringify_for_vote(v) for v in raw_values]
    tally = Counter(bucketed)
    total = sum(tally.values())
    if total == 0:
        return None, {}, 0.0
    winner_key, winner_count = tally.most_common(1)[0]
    agreement = winner_count / total
    if agreement <= floor:
        # No value crossed the floor -> "no consensus".
        return None, dict(tally), agreement
    # Recover original value by finding first raw value whose
    # stringified form matches the winner.
    winning_raw = next(
        v for v in raw_values if _stringify_for_vote(v) == winner_key
    )
    return winning_raw, dict(tally), agreement


def _stringify_for_vote(value: Any) -> str:
    """Stable string-form for vote keys.

    Lists/tuples -> ``"[a, b, c]"``; otherwise ``str(value)``. Used
    only to make the ``Counter`` hashable; the original typed value
    is reconstituted for the public output.
    """
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(str(c) for c in value) + "]"
    return str(value)


def build_consensus_for_section(
    records: Iterable[EvidenceRecord],
    *,
    config: ConsensusBuilderConfig = ConsensusBuilderConfig(),
) -> Optional[ConsensusOutput]:
    """Compute one consensus from the given reference records.

    ``records`` should be every record visible for the same
    ``(song_id, section_id)`` that carries ``reference_sources``.
    Records without references are skipped silently.

    Returns ``None`` if there are zero scored sources — the caller
    decides whether to skip writing or to write a degenerate "no
    sources" placeholder (the CLI skips).
    """
    record_list = list(records)
    label_dicts = _collect_labels(record_list)
    if not label_dicts:
        return None

    # Normalize tempo + chord_sequence so the vote tally compares
    # like values.
    normalized: list[dict[str, Any]] = []
    for d in label_dicts:
        nd: dict[str, Any] = dict(d)
        if "chord_sequence" in nd:
            nd["chord_sequence"] = _normalize_chord_sequence(nd["chord_sequence"])
        if "tempo_bpm" in nd:
            nd["tempo_bpm"] = _bucket_tempo(nd["tempo_bpm"], config.tempo_bucket_bpm)
        normalized.append(nd)

    agreement_per_key: dict[str, float] = {}
    votes_per_key: dict[str, dict[str, int]] = {}
    winners: dict[str, Any] = {}

    for key in config.scored_keys:
        winner, tally, agreement = _vote_categorical(
            normalized, key, config.field_decision_floor,
        )
        votes_per_key[key] = tally
        # Empty tally = nobody supplied the field. Mark as -1.0 so a
        # consumer can distinguish "no data" from "0% agreement".
        agreement_per_key[key] = agreement if tally else -1.0
        winners[key] = winner

    # Confidence = minimum agreement across keys that *decided* a
    # winner (i.e. crossed the floor). Keys with no data are excluded
    # (so a tempo-less provider doesn't drag confidence down) and so
    # are tied keys (where the agreement ratio is meaningless because
    # no value won). Per the directive: "all disagree -> confidence
    # 0.0, no consensus" — that drop-out happens here when no field
    # has a winner.
    decided = [
        agreement_per_key[k]
        for k in config.scored_keys
        if winners.get(k) is not None
    ]
    confidence = min(decided) if decided else 0.0

    # Restore chord_sequence to a tuple in the output (the normalize
    # step kept it tuple, but be defensive).
    chord_seq = winners.get("chord_sequence")
    if chord_seq is not None and not isinstance(chord_seq, tuple):
        chord_seq = tuple(chord_seq)

    return ConsensusOutput(
        guidance_mode=winners.get("guidance_mode"),
        chord_sequence=chord_seq,
        confidence=float(confidence),
        agreement={k: float(v) for k, v in agreement_per_key.items()},
        votes=votes_per_key,
    )


def build_consensus_for_store(
    store: EvidenceStore,
    *,
    config: ConsensusBuilderConfig = ConsensusBuilderConfig(),
    timestamp_utc: Optional[str] = None,
) -> int:
    """Scan the store, append one consensus record per section.

    For every ``(song_id, section_id)`` key that has at least one
    reference source visible, compute the consensus and append a new
    ``EvidenceRecord`` carrying just the ``consensus_output``. Existing
    records are not modified.

    Returns the count of consensus records written.
    """
    # Group records by (song_id, section_id). Memory-bounded because
    # the store is on the order of MBs even for 10k songs.
    by_key: dict[tuple[str, str], list[EvidenceRecord]] = defaultdict(list)
    for rec in store.iter_records():
        by_key[(rec.song_id, rec.section_id)].append(rec)

    ts = timestamp_utc or _utc_now_iso()
    n_written = 0
    for (song_id, section_id), records in by_key.items():
        consensus = build_consensus_for_section(records, config=config)
        if consensus is None:
            continue
        store.append(EvidenceRecord(
            song_id=song_id,
            section_id=section_id,
            timestamp_utc=ts,
            jam_output={},
            reference_sources=(),
            consensus_output=consensus,
            corrections=(),
            schema_version=1,
        ))
        n_written += 1
    return n_written
