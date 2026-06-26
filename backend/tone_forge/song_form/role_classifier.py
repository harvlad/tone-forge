"""Structural-role classifier over H2 per-section recurrence vectors.

Implements the `HYBRID_FIXED_RESCUE` design recommended in
`backend/structural_role_classifier_design.md` §C. The classifier
emits one of three structural roles per section:

    ANCHOR       — highly recurring material (the song's identity).
    DEVELOPMENT  — moderately recurring; derived from anchor material.
    UNIQUE       — little/no recurrence (intros, bridges, transitions).

These labels are deliberately **not** song-form labels (verse/chorus/
bridge). The design doc is explicit on this point; mapping these
structural roles onto musical-form vocabulary is a separate, future
milestone whose evidentiary basis does not yet exist.

The classifier consumes only:
    * H2 per-section vector  (`extract_h2(bundle).per_section`)
    * H2 song-level statistic (`extract_h2(bundle).h2_sep`)
    * Section ordering (positional)
    * Section count (implicit)

It does NOT consume vocal RMS, drum density, chord SSM, lyrics,
energy heuristics, or song-form labels.

Determinism: pure function, stdlib-only, no I/O, no RNG.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence

# --- Public types ------------------------------------------------------------

Role = Literal["ANCHOR", "DEVELOPMENT", "UNIQUE"]


@dataclass(frozen=True)
class RoleThresholds:
    """Frozen tuning surface (design doc §C).

    Defaults are calibrated on the canonical-6 corpus and validated on
    the held-out extended-5 without retuning (design doc §D).
    """

    anchor_floor: float = 0.66
    """H2 >= this → ANCHOR (standard path)."""

    unique_ceiling: float = 0.20
    """H2 < this and > 0 → UNIQUE (standard path)."""

    uniform_floor: float = 0.25
    """h2_sep < this → engage Escape 1 (uniform-song mode)."""

    uniform_anchor_threshold: float = 0.50
    """In uniform-song mode, H2 >= this → ANCHOR; else DEVELOPMENT."""


@dataclass(frozen=True)
class RoleDecision:
    """Per-section classifier output."""

    role: Role
    confidence: float


# --- Classifier --------------------------------------------------------------


def classify_roles(
    per_section_h2: Sequence[float],
    h2_sep: float,
    thresholds: RoleThresholds | None = None,
) -> tuple[RoleDecision, ...]:
    """Assign a structural role to every section in an H2 vector.

    Args:
        per_section_h2: H2 values per section, in section order. Empty
            input returns an empty tuple.
        h2_sep: Song-level separability scalar from the same H2
            extraction.
        thresholds: Override tuning constants. None → defaults.

    Returns:
        Tuple of `RoleDecision` aligned 1-to-1 with `per_section_h2`.
    """
    t = thresholds if thresholds is not None else RoleThresholds()
    n = len(per_section_h2)
    if n == 0:
        return ()

    # Escape 1 — uniform-song mode (h2_sep below floor).
    # Damping factor folds the song-level uncertainty into per-section
    # confidence so the UI can render "this whole song is anchor-shaped,
    # don't trust internal structure" cleanly.
    uniform_mode = h2_sep < t.uniform_floor
    if uniform_mode and t.uniform_floor > 0.0:
        damp = h2_sep / t.uniform_floor
    else:
        damp = 1.0

    # Escape 2 — no-natural-anchor rescue (standard mode only).
    # If no section clears the absolute anchor floor, the argmax of H2
    # is promoted to ANCHOR with reduced confidence — at least one
    # loopable region per non-degenerate song. Ties resolve to the
    # earliest section (`list.index` semantics).
    rescue_idx: int | None = None
    if not uniform_mode:
        has_natural_anchor = any(h >= t.anchor_floor for h in per_section_h2)
        if not has_natural_anchor:
            max_h = max(per_section_h2)
            if max_h > 0.0:
                rescue_idx = list(per_section_h2).index(max_h)

    out: list[RoleDecision] = []
    for i, h in enumerate(per_section_h2):
        # Hard floor: exact zero always → UNIQUE regardless of escapes.
        # Justified by H2 semantics: 0.0 means "no chord trigram in
        # this section appears anywhere else in the song". That is the
        # definition of UNIQUE; no escape can override it.
        if h == 0.0:
            out.append(RoleDecision("UNIQUE", 1.0))
            continue

        if uniform_mode:
            if h >= t.uniform_anchor_threshold:
                conf = _clip01(h * damp + (1.0 - damp) * 0.5)
                out.append(RoleDecision("ANCHOR", conf))
            else:
                out.append(RoleDecision("DEVELOPMENT", _clip01(damp)))
            continue

        # Standard path.
        if h >= t.anchor_floor:
            out.append(RoleDecision("ANCHOR", _clip01(h)))
        elif i == rescue_idx:
            out.append(RoleDecision("ANCHOR", _clip01(h * 0.75)))
        elif h < t.unique_ceiling:
            out.append(RoleDecision("UNIQUE", _clip01(1.0 - h)))
        else:
            out.append(
                RoleDecision("DEVELOPMENT", _clip01(1.0 - abs(h - 0.5) * 2.0))
            )

    return tuple(out)


def _clip01(x: float) -> float:
    if x < 0.0:
        return 0.0
    if x > 1.0:
        return 1.0
    return x
