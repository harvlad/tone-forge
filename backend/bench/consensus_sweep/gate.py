"""Consensus-corpus acceptance gate (Phase 6).

Directive rule: "corpus improves, no regression >1%, runtime within
threshold". We translate that into four checks against a baseline
``ConsensusCorpusScore``:

    1. Headline metric must (optionally) strictly improve.
       Mirrors ``bench.sweep.evaluate_acceptance``'s
       ``corpus_must_strictly_improve`` knob. Default ON so a
       no-op change doesn't accidentally pass.

    2. Headline metric must not regress by more than
       ``max_combined_regression_pp`` percentage points. Default
       1.0pp per directive.

    3. Per-section regression check: if a section that the
       baseline scored as a hit (match == 1.0) regresses to a miss
       (0.0) in the candidate, count it. Reject if the count
       exceeds ``max_section_regressions``. Mirrors
       ``bench.sweep.evaluate_acceptance``'s per-fixture drop rule.

    4. Runtime budget: ``score_wall_seconds`` must not exceed
       ``max_runtime_factor * baseline.score_wall_seconds``.
       Default 2.0x (lenient — scoring is cheap, this is here so
       a future change that accidentally re-decodes audio gets
       caught).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Tuple

from .scorer import ConsensusCorpusScore, ConsensusEntryScore


__all__ = [
    "ConsensusAcceptanceConfig",
    "ConsensusAcceptanceVerdict",
    "evaluate_consensus_acceptance",
]


@dataclass(frozen=True)
class ConsensusAcceptanceConfig:
    corpus_must_strictly_improve: bool = True
    max_combined_regression_pp: float = 1.0
    max_section_regressions: int = 0
    max_runtime_factor: float = 2.0


@dataclass(frozen=True)
class ConsensusAcceptanceVerdict:
    accepted: bool
    combined_delta: float
    rejection_reason: Optional[str] = None
    per_field_deltas: Tuple[Tuple[str, float], ...] = ()
    regressing_sections: Tuple[str, ...] = field(default_factory=tuple)


def _per_section_regressions(
    candidate: ConsensusCorpusScore,
    baseline: ConsensusCorpusScore,
) -> list[str]:
    """Sections where baseline scored 1.0 on some field and candidate scored 0.0."""
    base_by_key = {
        (e.song_id, e.section_id): e for e in baseline.entries
    }
    cand_by_key = {
        (e.song_id, e.section_id): e for e in candidate.entries
    }
    regressed: list[str] = []
    for key, base in base_by_key.items():
        cand = cand_by_key.get(key)
        if cand is None:
            continue
        if _regressed(base, cand, "guidance_mode_match"):
            regressed.append(f"{key[1]}:guidance_mode")
        if _regressed(base, cand, "chord_sequence_match"):
            regressed.append(f"{key[1]}:chord_sequence")
    return regressed


def _regressed(
    base: ConsensusEntryScore,
    cand: ConsensusEntryScore,
    field_name: str,
) -> bool:
    b = getattr(base, field_name)
    c = getattr(cand, field_name)
    if b is None or c is None:
        return False
    return b == 1.0 and c == 0.0


def evaluate_consensus_acceptance(
    candidate: ConsensusCorpusScore,
    baseline: ConsensusCorpusScore,
    rules: ConsensusAcceptanceConfig = ConsensusAcceptanceConfig(),
) -> ConsensusAcceptanceVerdict:
    """Compare a candidate score against a baseline and return a verdict."""
    delta = candidate.combined_match_rate - baseline.combined_match_rate
    per_field = (
        ("guidance_mode_match_rate",
         candidate.guidance_mode_match_rate - baseline.guidance_mode_match_rate),
        ("chord_sequence_match_rate",
         candidate.chord_sequence_match_rate - baseline.chord_sequence_match_rate),
        ("chord_sequence_mean_jaccard",
         candidate.chord_sequence_mean_jaccard - baseline.chord_sequence_mean_jaccard),
    )

    # Rule 1: strict improvement (optional)
    if rules.corpus_must_strictly_improve and delta <= 0.0:
        return ConsensusAcceptanceVerdict(
            accepted=False,
            combined_delta=delta,
            rejection_reason=(
                f"combined_match_rate did not improve "
                f"(delta={delta:+.4f}, candidate={candidate.combined_match_rate:.4f}, "
                f"baseline={baseline.combined_match_rate:.4f})"
            ),
            per_field_deltas=per_field,
        )

    # Rule 2: absolute regression bound
    regression_bound = -rules.max_combined_regression_pp / 100.0
    if delta < regression_bound:
        return ConsensusAcceptanceVerdict(
            accepted=False,
            combined_delta=delta,
            rejection_reason=(
                f"combined_match_rate regressed by {-delta*100:.2f}pp "
                f"(> {rules.max_combined_regression_pp:.2f}pp bound)"
            ),
            per_field_deltas=per_field,
        )

    # Rule 3: per-section regression count
    regressed = _per_section_regressions(candidate, baseline)
    if len(regressed) > rules.max_section_regressions:
        return ConsensusAcceptanceVerdict(
            accepted=False,
            combined_delta=delta,
            rejection_reason=(
                f"{len(regressed)} section(s) regressed from 1.0 to 0.0 "
                f"(max allowed: {rules.max_section_regressions})"
            ),
            per_field_deltas=per_field,
            regressing_sections=tuple(regressed),
        )

    # Rule 4: runtime budget
    if baseline.score_wall_seconds > 0:
        budget = rules.max_runtime_factor * baseline.score_wall_seconds
        if candidate.score_wall_seconds > budget:
            return ConsensusAcceptanceVerdict(
                accepted=False,
                combined_delta=delta,
                rejection_reason=(
                    f"score_wall_seconds {candidate.score_wall_seconds:.3f}s "
                    f"> {rules.max_runtime_factor}x baseline "
                    f"({budget:.3f}s)"
                ),
                per_field_deltas=per_field,
                regressing_sections=tuple(regressed),
            )

    return ConsensusAcceptanceVerdict(
        accepted=True,
        combined_delta=delta,
        rejection_reason=None,
        per_field_deltas=per_field,
        regressing_sections=tuple(regressed),
    )
