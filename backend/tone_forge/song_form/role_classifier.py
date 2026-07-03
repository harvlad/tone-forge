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
    """H2 >= this → ANCHOR (standard path). Also used by uniform
    mode — see ``uniform_anchor_threshold`` note below."""

    unique_ceiling: float = 0.20
    """H2 < this and > 0 → UNIQUE. Applies in both standard and
    uniform modes."""

    uniform_floor: float = 0.25
    """h2_sep < this → engage uniform-song mode.

    Uniform mode damps per-section confidences but uses the same
    label thresholds as standard mode. Previously it also lowered
    the ANCHOR threshold via ``uniform_anchor_threshold``; that
    conflated a confidence signal (correct) with a label bias
    (wrong — it hid real internal structure). See
    ``song_form_classifier_design.md`` §"Pass 4 discussion" for
    the motivating case."""

    uniform_anchor_threshold: float = 0.50
    """Legacy no-op. Retained on the frozen dataclass for
    backwards-compatibility with callers that construct
    ``RoleThresholds`` with a custom value. As of the pop-punk
    fix uniform mode uses ``anchor_floor`` for labelling and only
    damps confidence via ``uniform_floor``."""


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
    per_section_insufficient: Sequence[bool] | None = None,
) -> tuple[RoleDecision, ...]:
    """Assign a structural role to every section in an H2 vector.

    Args:
        per_section_h2: H2 values per section, in section order. Empty
            input returns an empty tuple.
        h2_sep: Song-level separability scalar from the same H2
            extraction.
        thresholds: Override tuning constants. None → defaults.
        per_section_insufficient: Parallel tuple (aligned with
            ``per_section_h2``) marking sections whose chord
            sub-sequence had fewer than ``n_used`` symbols in the H2
            pass. Those sections carry ``h == 0.0`` as an abstain
            sentinel, not as evidence of uniqueness. When provided,
            insufficient sections short-circuit to ``DEVELOPMENT``
            with low confidence so Stage B refinements (vocal / energy
            aggregates) decide the final section type instead of
            defaulting to BRIDGE via UNIQUE. None → legacy behaviour
            (``h == 0.0`` collapses to UNIQUE regardless of provenance).

    Returns:
        Tuple of `RoleDecision` aligned 1-to-1 with `per_section_h2`.
    """
    t = thresholds if thresholds is not None else RoleThresholds()
    n = len(per_section_h2)
    if n == 0:
        return ()

    # Uniform-song mode (h2_sep below floor).
    #
    # Uniform mode carries a single signal: the song-level H2
    # separability is low, so we should trust every per-section
    # label less. We fold that into a scalar ``damp`` and use it to
    # blend per-section confidences toward 0.5 (maximum
    # uncertainty). Labels themselves use the same thresholds as
    # the standard path — collapsing labels on top of damped
    # confidences double-counts the same signal and hides
    # structure that Stage B needs to distinguish verse from
    # chorus on songs where the H2 vector is nearly flat but
    # still has internal ordering (pop-punk, folk, many pop
    # songs).
    uniform_mode = h2_sep < t.uniform_floor
    if uniform_mode and t.uniform_floor > 0.0:
        damp = h2_sep / t.uniform_floor
    else:
        damp = 1.0

    # No-natural-anchor rescue.
    #
    # If no section clears the absolute anchor floor, the argmax of
    # H2 is promoted to ANCHOR with reduced confidence — at least
    # one loopable region per non-degenerate song. Ties resolve to
    # the earliest section (``list.index`` semantics). Now runs
    # symmetrically across both standard and uniform modes: with
    # uniform mode using the same ``anchor_floor``, a song
    # genuinely without a section at floor would otherwise
    # end up with zero ANCHORs, which downstream section-naming
    # cannot handle.
    has_natural_anchor = any(h >= t.anchor_floor for h in per_section_h2)
    rescue_idx: int | None = None
    if not has_natural_anchor:
        max_h = max(per_section_h2)
        if max_h > 0.0:
            rescue_idx = list(per_section_h2).index(max_h)

    def _blend(raw_conf: float) -> float:
        """Blend a standard-mode confidence toward 0.5 by ``damp``.

        At ``damp == 1`` (h2_sep at the floor) returns ``raw_conf``
        unchanged; at ``damp == 0`` (h2_sep == 0) returns 0.5.
        Used only in uniform mode; standard mode uses raw
        confidences.
        """
        return _clip01(raw_conf * damp + (1.0 - damp) * 0.5)

    out: list[RoleDecision] = []
    for i, h in enumerate(per_section_h2):
        # Abstain path: caller-supplied ``insufficient`` flag says
        # this section did not have enough chord symbols to produce
        # any n-grams. Its ``h == 0.0`` is a sentinel for "no data",
        # not a claim about recurrence. Route to DEVELOPMENT with
        # low confidence so Stage B refinements (vocals / energy)
        # decide the final section type — Pass 1 catches drum-only
        # spans as INSTRUMENTAL, Pass 2 catches prechorus vocab,
        # otherwise DEVELOPMENT maps to VERSE at Stage A.
        # Guarded bounds check: a shorter ``per_section_insufficient``
        # falls back to the legacy path for out-of-range indices.
        if (
            per_section_insufficient is not None
            and i < len(per_section_insufficient)
            and per_section_insufficient[i]
        ):
            out.append(RoleDecision("DEVELOPMENT", 0.25))
            continue

        # Hard floor: exact zero always → UNIQUE regardless of mode.
        # Justified by H2 semantics: 0.0 means "no chord trigram in
        # this section appears anywhere else in the song". That is
        # the definition of UNIQUE.
        if h == 0.0:
            out.append(RoleDecision("UNIQUE", 1.0))
            continue

        # Label assignment — identical in both modes.
        if h >= t.anchor_floor:
            role: Role = "ANCHOR"
            raw_conf = h
        elif i == rescue_idx:
            role = "ANCHOR"
            raw_conf = h * 0.75
        elif h < t.unique_ceiling:
            role = "UNIQUE"
            raw_conf = 1.0 - h
        else:
            role = "DEVELOPMENT"
            raw_conf = 1.0 - abs(h - 0.5) * 2.0

        conf = _blend(raw_conf) if uniform_mode else _clip01(raw_conf)
        out.append(RoleDecision(role, conf))

    return tuple(out)


def _clip01(x: float) -> float:
    if x < 0.0:
        return 0.0
    if x > 1.0:
        return 1.0
    return x
