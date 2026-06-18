"""Aggregate Phase 4 failures + Phase 7 corrections into a ranked
list of engine areas.

The ranker is deliberately small: it does one pass over the
evidence store, calls into ``mine_failures`` (Phase 4) for the
consensus-side signal, walks ``Correction`` rows for the user
signal, and groups everything by an *engine area* tag. The output
is one ``RoadmapItem`` per area, sorted by combined score.

Engine-area mapping is centralized in ``_FIELD_TO_AREA`` /
``_CORRECTION_TYPE_TO_AREA``. Adding a new compared field or a
new correction type only requires extending those tables; the
ranking math doesn't change.

Score formula::

    score = consensus_weight * sum(consensus_confidence_i)
          + correction_weight * n_user_corrections

Higher-confidence consensus failures pull the score up faster
than low-confidence ones; user corrections pull at a flat rate
since they don't carry their own confidence field. Weights are
exposed on ``RoadmapConfig`` so the operator can tune. The
default 1.0/1.0 means one high-confidence consensus failure is
worth roughly one user correction, which matches the directive's
"consensus is reference, corrections are evidence" framing.
"""
from __future__ import annotations

import json
import socket
import sys
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ..evidence.schema import Correction, EvidenceRecord
from ..evidence.store import EvidenceStore
from ..failures.miner import FailureMiningConfig, FailureRow, mine_failures


__all__ = [
    "RoadmapConfig",
    "RoadmapItem",
    "RoadmapReport",
    "build_roadmap",
    "dump_roadmap",
    "load_roadmap",
]


# Compared-field (from Phase 4) → engine area. The area name is
# the coarse code location the operator will work on; it doesn't
# have to match a module name exactly, but should be specific
# enough to land in a planning doc as "fix this".
_FIELD_TO_AREA: dict[str, str] = {
    "guidance_mode": "guidance_mode_classifier",
    "chord_sequence": "chord_detector",
}

# Correction type (from Phase 7) → engine area. Multiple
# correction types can fan into the same area (chord +
# chord_sequence both indict the chord detector).
_CORRECTION_TYPE_TO_AREA: dict[str, str] = {
    "guidance_mode": "guidance_mode_classifier",
    "chord": "chord_detector",
    "chord_sequence": "chord_detector",
    "key": "key_detector",
    "tempo_bpm": "tempo_detector",
    "section_boundary": "section_segmenter",
}


@dataclass(frozen=True)
class RoadmapConfig:
    """Tunables for ``build_roadmap``.

    ``min_consensus_confidence`` flows into Phase 4's miner so
    low-confidence consensus rows don't inflate engine-area scores
    with noisy reference data.

    ``top_n`` caps the report length; items past the cut are
    dropped, not summarized, to keep the output JSON readable.
    A future variant could surface a "long tail" count.

    ``examples_per_item`` caps how many per-item example breadcrumbs
    we keep so the report stays small for transport.
    """

    min_consensus_confidence: float = 0.8
    top_n: int = 10
    examples_per_item: int = 5
    consensus_weight: float = 1.0
    correction_weight: float = 1.0
    # Set to a song_id to scope the report to one song; useful for
    # per-song retro after a JAM session.
    song_id: Optional[str] = None


@dataclass(frozen=True)
class RoadmapItem:
    """One engine-area action item.

    ``area`` is the human-readable target (e.g.
    ``chord_detector``). ``score`` is the weighted sum used for
    ranking. ``n_consensus_failures`` and ``n_user_corrections``
    are the raw counts so the operator can see "is this area
    weighted by consensus or by users?". ``example_sections`` is a
    capped list of ``(song_id, section_id, kind)`` triples where
    ``kind`` is ``"consensus"`` or ``"correction"`` so the planner
    can click straight into the evidence.

    ``representative_diffs`` carries up to one sample
    ``{jam_value, consensus_value}`` per failure_type so the
    written report has concrete examples without the operator
    re-querying the store.
    """

    area: str
    score: float
    n_consensus_failures: int
    n_user_corrections: int
    mean_consensus_confidence: float
    failure_types: tuple[str, ...]
    correction_types: tuple[str, ...]
    example_sections: tuple[tuple[str, str, str], ...]
    representative_diffs: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class RoadmapReport:
    """Wrapper carrying ranked items + provenance.

    ``timestamp_utc`` / ``hostname`` / ``python_version`` make the
    JSON artifact debuggable across machines and runs. Same
    pattern as ``ConsensusCorpusScore`` (Phase 6) so a reviewer
    can correlate roadmap snapshots against sweep gate outcomes
    by timestamp.
    """

    config: dict[str, Any]
    n_areas_total: int
    n_consensus_failures_total: int
    n_user_corrections_total: int
    items: tuple[RoadmapItem, ...]
    timestamp_utc: str
    hostname: str
    python_version: str
    build_wall_seconds: float


# ---------------------------------------------------------------------------
# Aggregation helpers
# ---------------------------------------------------------------------------


def _area_for_correction(c: Correction) -> Optional[str]:
    return _CORRECTION_TYPE_TO_AREA.get(c.correction_type)


def _area_for_failure(f: FailureRow) -> Optional[str]:
    # FailureRow.failure_type is `{field}_mismatch`; strip the
    # suffix to recover the field name, then look up the area.
    if f.failure_type.endswith("_mismatch"):
        field_name = f.failure_type[: -len("_mismatch")]
        return _FIELD_TO_AREA.get(field_name)
    return None


def _iter_correction_rows(
    store: EvidenceStore,
    *,
    song_id: Optional[str],
) -> list[tuple[EvidenceRecord, Correction]]:
    """All ``(record, correction)`` pairs in the store.

    A record may carry multiple corrections (currently Phase 7
    appends one per record, but the schema allows N). Each
    correction is yielded as its own pair so the aggregator can
    treat them independently.
    """
    out: list[tuple[EvidenceRecord, Correction]] = []
    for rec in store.iter_records():
        if song_id is not None and rec.song_id != song_id:
            continue
        for c in rec.corrections:
            out.append((rec, c))
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_roadmap(
    store: EvidenceStore,
    *,
    config: RoadmapConfig = RoadmapConfig(),
) -> RoadmapReport:
    """Build a roadmap snapshot from one evidence store.

    Side-effect free; safe to call repeatedly. The returned
    ``RoadmapReport`` is self-contained (JSON-dumpable via
    ``dump_roadmap``).
    """
    started = time.perf_counter()

    # ----- Phase 4 signal: engine-vs-consensus failures.
    failure_cfg = FailureMiningConfig(
        min_consensus_confidence=config.min_consensus_confidence,
    )
    all_failures = mine_failures(store, config=failure_cfg)
    if config.song_id is not None:
        all_failures = [f for f in all_failures if f.song_id == config.song_id]

    # ----- Phase 7 signal: user corrections.
    correction_pairs = _iter_correction_rows(store, song_id=config.song_id)

    # ----- Per-area buckets.
    consensus_buckets: dict[str, list[FailureRow]] = defaultdict(list)
    for f in all_failures:
        area = _area_for_failure(f)
        if area is None:
            continue
        consensus_buckets[area].append(f)

    correction_buckets: dict[str, list[tuple[EvidenceRecord, Correction]]] = (
        defaultdict(list)
    )
    for rec, c in correction_pairs:
        area = _area_for_correction(c)
        if area is None:
            continue
        correction_buckets[area].append((rec, c))

    all_areas = set(consensus_buckets) | set(correction_buckets)

    items: list[RoadmapItem] = []
    for area in sorted(all_areas):
        failures = consensus_buckets.get(area, [])
        corrections = correction_buckets.get(area, [])
        n_failures = len(failures)
        n_corrections = len(corrections)
        if n_failures == 0 and n_corrections == 0:
            continue

        sum_conf = sum(f.consensus_confidence for f in failures)
        mean_conf = (sum_conf / n_failures) if n_failures else 0.0
        score = (
            config.consensus_weight * sum_conf
            + config.correction_weight * float(n_corrections)
        )

        failure_types = tuple(sorted({f.failure_type for f in failures}))
        correction_types = tuple(sorted({c.correction_type for _, c in corrections}))

        # Example breadcrumbs: interleave consensus + correction
        # rows up to ``examples_per_item``. Order failures first
        # (they tend to dominate quantitatively) then corrections.
        examples: list[tuple[str, str, str]] = []
        for f in failures:
            if len(examples) >= config.examples_per_item:
                break
            examples.append((f.song_id, f.section_id, "consensus"))
        for rec, _ in corrections:
            if len(examples) >= config.examples_per_item:
                break
            examples.append((rec.song_id, rec.section_id, "correction"))

        # Representative diffs: one sample per failure_type.
        diffs: list[dict[str, Any]] = []
        seen_types: set[str] = set()
        for f in failures:
            if f.failure_type in seen_types:
                continue
            seen_types.add(f.failure_type)
            diffs.append({
                "failure_type": f.failure_type,
                "song_id": f.song_id,
                "section_id": f.section_id,
                "jam_value": list(f.jam_value) if isinstance(
                    f.jam_value, tuple) else f.jam_value,
                "consensus_value": list(f.consensus_value) if isinstance(
                    f.consensus_value, tuple) else f.consensus_value,
                "consensus_confidence": f.consensus_confidence,
            })

        items.append(RoadmapItem(
            area=area,
            score=score,
            n_consensus_failures=n_failures,
            n_user_corrections=n_corrections,
            mean_consensus_confidence=mean_conf,
            failure_types=failure_types,
            correction_types=correction_types,
            example_sections=tuple(examples),
            representative_diffs=tuple(diffs),
        ))

    # Sort by score desc, then by area asc for deterministic tie-break.
    items.sort(key=lambda it: (-it.score, it.area))
    capped = tuple(items[: config.top_n])

    return RoadmapReport(
        config={
            "min_consensus_confidence": config.min_consensus_confidence,
            "top_n": config.top_n,
            "examples_per_item": config.examples_per_item,
            "consensus_weight": config.consensus_weight,
            "correction_weight": config.correction_weight,
            "song_id": config.song_id,
        },
        n_areas_total=len(all_areas),
        n_consensus_failures_total=len(all_failures),
        n_user_corrections_total=len(correction_pairs),
        items=capped,
        timestamp_utc=datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.%fZ"
        ),
        hostname=socket.gethostname(),
        python_version=".".join(str(v) for v in sys.version_info[:3]),
        build_wall_seconds=time.perf_counter() - started,
    )


# ---------------------------------------------------------------------------
# JSON round-trip
# ---------------------------------------------------------------------------


def dump_roadmap(report: RoadmapReport) -> dict[str, Any]:
    """Convert a ``RoadmapReport`` to a JSON-ready dict.

    Tuples become lists; nested ``RoadmapItem`` dataclasses are
    ``asdict``-ed. Float values are passed through; the writer
    can ``json.dumps`` the result directly.
    """
    out = asdict(report)
    # asdict handles nested dataclasses, but the example_sections
    # tuples-of-tuples come out as nested lists already. Be
    # explicit so a future schema change here is loud.
    out["items"] = [asdict(it) for it in report.items]
    return out


def load_roadmap(data: dict[str, Any]) -> RoadmapReport:
    """Inverse of ``dump_roadmap``.

    Tolerant of lists-where-tuples-were so a hand-edited or
    diff-trimmed JSON still loads. Unknown keys are ignored to
    keep forward compatibility — Phase 9 may add ML-facing
    fields and old readers should still parse.
    """
    items_data = data.get("items", [])
    items = tuple(
        RoadmapItem(
            area=str(it["area"]),
            score=float(it["score"]),
            n_consensus_failures=int(it["n_consensus_failures"]),
            n_user_corrections=int(it["n_user_corrections"]),
            mean_consensus_confidence=float(it["mean_consensus_confidence"]),
            failure_types=tuple(it.get("failure_types", [])),
            correction_types=tuple(it.get("correction_types", [])),
            example_sections=tuple(
                tuple(triple) for triple in it.get("example_sections", [])
            ),
            representative_diffs=tuple(it.get("representative_diffs", [])),
        )
        for it in items_data
    )
    return RoadmapReport(
        config=dict(data.get("config", {})),
        n_areas_total=int(data.get("n_areas_total", 0)),
        n_consensus_failures_total=int(data.get("n_consensus_failures_total", 0)),
        n_user_corrections_total=int(data.get("n_user_corrections_total", 0)),
        items=items,
        timestamp_utc=str(data.get("timestamp_utc", "")),
        hostname=str(data.get("hostname", "")),
        python_version=str(data.get("python_version", "")),
        build_wall_seconds=float(data.get("build_wall_seconds", 0.0)),
    )
