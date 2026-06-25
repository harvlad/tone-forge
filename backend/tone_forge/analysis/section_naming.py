"""Derive musical-form section types from H2 structural roles.

Replaces the energy-and-onset-density heuristic in
``sections.py:_classify_section_type`` with a derivation grounded in
the H2 chord-trigram recurrence classifier
(``song_form/role_classifier.py``).

Rules (Stage A — H2 + position only):

    UNIQUE      + first   → INTRO
    UNIQUE      + last    → OUTRO
    UNIQUE      + middle  → BRIDGE
    ANCHOR      + any     → CHORUS
    DEVELOPMENT + any     → VERSE

Stage B (separate milestone) refines this with vocal-RMS / drum-
density / chord-progression similarity to disambiguate
INSTRUMENTAL vs CHORUS, PRECHORUS detection, BREAKDOWN detection.
Stage B will extend the ``derive_section_types`` signature with the
additional signals; the H2-only path stays available as a fallback
when the extra signals are unavailable (e.g. for legacy bundles or
the unified pipeline before stem features are wired through).

Determinism: pure function, stdlib-only, no I/O, no RNG.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from tone_forge.analysis.sections import SectionType
from tone_forge.song_form.role_classifier import RoleDecision


@dataclass(frozen=True)
class SectionNamingThresholds:
    """Frozen tuning surface for section_naming.

    Defaults chosen so that low-confidence H2 outputs fall back to
    position-based defaults (INTRO/OUTRO/VERSE) rather than emitting
    a confidently-wrong CHORUS or BRIDGE.
    """

    confidence_floor: float = 0.30
    """RoleDecision.confidence below this → fall through to position-
    based default (INTRO at edges, VERSE in middle)."""


def _position_default(is_first: bool, is_last: bool) -> SectionType:
    """Position-only fallback when H2 evidence is too weak to trust."""
    if is_first:
        return SectionType.INTRO
    if is_last:
        return SectionType.OUTRO
    return SectionType.VERSE


def derive_section_types(
    role_decisions: Sequence[RoleDecision],
    thresholds: SectionNamingThresholds = SectionNamingThresholds(),
) -> tuple[SectionType, ...]:
    """Map H2 role decisions to musical-form section types.

    Args:
        role_decisions: Output of ``classify_roles(...)`` — one
            ``RoleDecision`` per section, in section order.
        thresholds: Frozen tuning knobs (confidence floor).

    Returns:
        Tuple of ``SectionType`` values, aligned 1-to-1 with input.
        Empty input → empty tuple.
    """
    n = len(role_decisions)
    if n == 0:
        return ()

    out: list[SectionType] = []
    for i, d in enumerate(role_decisions):
        is_first = (i == 0)
        is_last = (i == n - 1)

        # Low-confidence fall-through: trust position only. Note we
        # treat n==1 as ``is_first`` (so single-section input lands on
        # INTRO via the position default, not OUTRO).
        if d.confidence < thresholds.confidence_floor:
            out.append(_position_default(is_first, is_last))
            continue

        # H2-driven mapping.
        if d.role == "UNIQUE":
            if is_first:
                out.append(SectionType.INTRO)
            elif is_last:
                out.append(SectionType.OUTRO)
            else:
                out.append(SectionType.BRIDGE)
        elif d.role == "ANCHOR":
            out.append(SectionType.CHORUS)
        elif d.role == "DEVELOPMENT":
            out.append(SectionType.VERSE)
        else:
            # Defensive: classifier extended with a role we don't
            # recognise yet. Position-based default keeps the bundle
            # plausible.
            out.append(_position_default(is_first, is_last))

    return tuple(out)
