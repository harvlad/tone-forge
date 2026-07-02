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

from collections.abc import Mapping as _Mapping
from dataclasses import dataclass
from typing import Any, MutableMapping, Protocol, Sequence, runtime_checkable

from tone_forge.analysis.sections import SectionType


@runtime_checkable
class RoleDecisionLike(Protocol):
    """Structural type for objects this module consumes.

    Matches ``tone_forge.song_form.role_classifier.RoleDecision`` by
    duck-typing — we deliberately don't import the concrete class so
    the ``analysis`` subsystem keeps a clean boundary (cross-subsystem
    types travel through ``tone_forge.contracts``; until song_form is
    promoted to a contract type, structural typing is the
    boundary-friendly bridge).
    """

    role: str
    confidence: float


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
    role_decisions: Sequence[RoleDecisionLike],
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


# ---------------------------------------------------------------------------
# Duration-guard post-pass (Fix B: suspicious-section flagging)
# ---------------------------------------------------------------------------
#
# The Stage 0 RMS-novelty boundary detector (analysis/sections.py)
# under-segments songs with long chorus-riff runs — for the reference
# session c3687f79 ("One Step Closer") it produces a single 70s
# CHORUS block that structurally covers verse2+prechorus2+chorus2+bridge.
# Stages A/B relabel the block but can't redraw a boundary that
# was never detected.
#
# This post-pass doesn't try to fix the boundary (that's Fix C). It
# just flags durations that are structurally implausible for the
# assigned label, so the JAM UI can render a "this section is
# probably wrong" indicator on the pill. Purely advisory, does not
# rename or resegment.


@dataclass(frozen=True)
class DurationGuardThresholds:
    """Duration limits that flag a section as structurally implausible.

    Defaults tuned against radio-pop/rock (3-4 min songs with 20-30s
    choruses). Prog / jam-band forms will trip these — that's a false
    positive, but the indicator is a hint not a hard label change.
    """

    max_chorus_s: float = 30.0
    """CHORUS longer than this is suspicious (unless the final section)."""

    max_prechorus_s: float = 20.0
    """PRECHORUS longer than this is suspicious."""

    max_verse_s: float = 45.0
    """VERSE longer than this is suspicious."""

    max_bridge_s: float = 30.0
    """BRIDGE longer than this is suspicious."""

    min_section_s: float = 6.0
    """Any section shorter than this is flagged as a fragment. The
    boundary detector's ``min_section_duration`` is 4.0s, so anything
    between 4-6s is a candidate for merge-with-neighbour."""

    exempt_final_chorus: bool = True
    """When True, the last section is exempted from the CHORUS length
    check — final choruses often tag out with a long ring or fade."""


def flag_suspicious_durations(
    sections: Sequence[MutableMapping[str, Any]],
    thresholds: DurationGuardThresholds = DurationGuardThresholds(),
) -> None:
    """Annotate each section dict with a ``duration_flag`` string.

    Mutates ``sections`` in place. Each entry is expected to carry
    ``type``, ``start_time`` and ``end_time`` (the
    ``ArrangementSection.to_dict()`` shape used inside the unified
    pipeline). Entries without those keys are silently skipped and
    left as-is.

    Flag values (empty string when no flag applies):
        - ``"chorus_too_long"``     CHORUS > max_chorus_s (not final)
        - ``"prechorus_too_long"``  PRECHORUS > max_prechorus_s
        - ``"verse_too_long"``      VERSE > max_verse_s
        - ``"bridge_too_long"``     BRIDGE > max_bridge_s
        - ``"fragment"``            section < min_section_s

    Purpose: give the JAM UI a signal that a section boundary is
    probably wrong so the frontend can render a suspicious indicator
    on the pill. Purely advisory — does not rename or resegment.

    Determinism: pure over dict values + thresholds, no I/O, no RNG.
    """
    n = len(sections)
    for i, s in enumerate(sections):
        if not isinstance(s, _Mapping):
            continue
        try:
            start = float(s.get("start_time", 0.0))
            end = float(s.get("end_time", 0.0))
        except (TypeError, ValueError):
            continue
        dur = end - start
        stype = str(s.get("type", "")).lower()
        flag = ""
        if dur < thresholds.min_section_s:
            flag = "fragment"
        elif stype == "chorus" and dur > thresholds.max_chorus_s:
            is_last = (i == n - 1)
            if not (thresholds.exempt_final_chorus and is_last):
                flag = "chorus_too_long"
        elif stype == "prechorus" and dur > thresholds.max_prechorus_s:
            flag = "prechorus_too_long"
        elif stype == "verse" and dur > thresholds.max_verse_s:
            flag = "verse_too_long"
        elif stype == "bridge" and dur > thresholds.max_bridge_s:
            flag = "bridge_too_long"
        s["duration_flag"] = flag
