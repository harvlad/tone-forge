"""Hermetic unit tests for the structural-role classifier.

Covers §F items 1–7 of `backend/structural_role_classifier_design.md`.
The canonical-corpus gate (§F item 8) lives in
`test_role_classifier_canonical.py`, and the held-out validation gate
(§F item 9) lives in `test_role_classifier_extended.py`.
"""

from __future__ import annotations

import math

import pytest

from tone_forge.song_form.role_classifier import (
    RoleDecision,
    RoleThresholds,
    classify_roles,
)


_TOL = 1e-9


def _roles(decisions: tuple[RoleDecision, ...]) -> tuple[str, ...]:
    return tuple(d.role for d in decisions)


def _confs(decisions: tuple[RoleDecision, ...]) -> tuple[float, ...]:
    return tuple(d.confidence for d in decisions)


# Item 1 — all-zero vector
def test_all_zero_vector_is_all_unique():
    decisions = classify_roles((0.0, 0.0, 0.0, 0.0), h2_sep=0.0)
    assert _roles(decisions) == ("UNIQUE",) * 4
    assert _confs(decisions) == (1.0, 1.0, 1.0, 1.0)


# Item 2 — all-one vector
def test_all_one_vector_is_all_anchor():
    # h2_sep is 0 by construction (no variance) but the hard-floor /
    # uniform-mode interaction must still yield all-ANCHOR. In the
    # uniform-escape path, h=1.0 with damp=0 → conf = 0*1 + 1*0.5 = 0.5;
    # design doc accepts damped confidence for uniform songs.
    decisions = classify_roles((1.0, 1.0, 1.0), h2_sep=0.0)
    assert _roles(decisions) == ("ANCHOR", "ANCHOR", "ANCHOR")
    for d in decisions:
        assert d.confidence > 0.0


# Item 3 — uniform-song mode
def test_uniform_song_mode_uses_same_thresholds_as_standard():
    """h2_sep < uniform_floor engages uniform mode.

    Uniform mode uses the same ``anchor_floor`` and
    ``unique_ceiling`` as the standard path — only per-section
    confidences are damped (blended toward 0.5 by
    ``h2_sep / uniform_floor``). Previously the mode lowered
    the ANCHOR threshold to 0.5, which conflated a confidence
    signal with a label bias and hid structure that Stage B
    needs. See ``song_form_classifier_design.md`` §Pass 4.
    """
    decisions = classify_roles((0.6, 0.7, 0.8, 0.55, 0.4), h2_sep=0.10)
    assert _roles(decisions) == (
        "DEVELOPMENT", "ANCHOR", "ANCHOR", "DEVELOPMENT", "DEVELOPMENT",
    )
    damp = 0.10 / 0.25  # = 0.4
    # ANCHOR raw_conf = h → blended = h * damp + (1-damp)*0.5.
    # h=0.7 → 0.7*0.4 + 0.6*0.5 = 0.28 + 0.30 = 0.58
    assert math.isclose(
        decisions[1].confidence,
        0.7 * damp + (1 - damp) * 0.5,
        abs_tol=_TOL,
    )
    # DEVELOPMENT raw_conf = 1 - abs(h-0.5)*2 → blended similarly.
    # h=0.4 → raw = 1 - 0.2 = 0.8; blended = 0.8*0.4 + 0.6*0.5 = 0.62
    raw_dev = 1.0 - abs(0.4 - 0.5) * 2.0
    assert math.isclose(
        decisions[4].confidence,
        raw_dev * damp + (1 - damp) * 0.5,
        abs_tol=_TOL,
    )


def test_uniform_mode_paramore_shaped_h2_yields_two_developments():
    """Regression for Paramore "That's What You Get" (session
    ``5fff8bd2``): a 14-section song whose H2 vector is nearly
    flat (h2_sep ≈ 0.24, just under the uniform floor) but has
    two genuinely-lower sections that must land as DEVELOPMENT
    rather than being papered over as ANCHOR.

    The old uniform-mode branch used ``uniform_anchor_threshold
    = 0.50`` and would have promoted every section >= 0.50 to
    ANCHOR, collapsing the song into all-CHORUS downstream. The
    realigned branch uses ``anchor_floor = 0.66`` in both modes
    so sections at 0.575 and 0.634 remain DEVELOPMENT.
    """
    h2 = (
        0.672, 0.751, 0.751, 0.801, 0.801, 0.801, 0.575,
        0.634, 0.801, 0.801, 0.664, 0.664, 0.664, 0.697,
    )
    decisions = classify_roles(h2, h2_sep=0.24)
    roles = _roles(decisions)
    # 0.575 and 0.634 (indices 6 and 7) sit below anchor_floor.
    assert roles[6] == "DEVELOPMENT"
    assert roles[7] == "DEVELOPMENT"
    # Every other section clears 0.66 and must land ANCHOR.
    for i, r in enumerate(roles):
        if i in (6, 7):
            continue
        assert r == "ANCHOR", (i, r)


# Item 4 — no-natural-anchor rescue
def test_rescue_promotes_argmax_when_no_section_clears_anchor_floor():
    # max(H2) = 0.55 < anchor_floor 0.66 → rescue triggers.
    # h2_sep large enough to stay out of uniform mode.
    h2 = (0.30, 0.55, 0.40, 0.10)
    decisions = classify_roles(h2, h2_sep=0.70)
    assert _roles(decisions) == ("DEVELOPMENT", "ANCHOR", "DEVELOPMENT", "UNIQUE")
    # Rescue confidence = h * 0.75
    assert math.isclose(decisions[1].confidence, 0.55 * 0.75, abs_tol=_TOL)


def test_rescue_ties_resolve_to_earliest_section():
    """Two sections at identical max H2 — earliest wins (deterministic)."""
    h2 = (0.30, 0.55, 0.55, 0.10)
    decisions = classify_roles(h2, h2_sep=0.70)
    # First occurrence of the max gets the rescue. The second
    # 0.55 falls through to DEVELOPMENT (0.20 <= 0.55 < 0.66).
    assert decisions[1].role == "ANCHOR"
    assert decisions[2].role == "DEVELOPMENT"


# Item 5 — bimodal vector
def test_bimodal_vector_yields_clean_split():
    decisions = classify_roles((0.0, 0.0, 1.0, 1.0), h2_sep=1.0)
    assert _roles(decisions) == ("UNIQUE", "UNIQUE", "ANCHOR", "ANCHOR")
    assert _confs(decisions) == (1.0, 1.0, 1.0, 1.0)


# Item 6 — borderline vector at the anchor_floor boundary
def test_anchor_floor_boundary_is_half_open():
    # h=0.65 just below floor → DEVELOPMENT; h=0.66 at floor → ANCHOR.
    decisions = classify_roles((0.65, 0.66), h2_sep=0.50)
    assert decisions[0].role == "DEVELOPMENT"
    assert decisions[1].role == "ANCHOR"


def test_unique_ceiling_boundary_is_half_open():
    # h=0.19 (< unique_ceiling 0.20) → UNIQUE
    # h=0.20 (== unique_ceiling) → DEVELOPMENT
    # h=0.0 always → UNIQUE regardless of ceiling
    # Append a 1.0 so has_natural_anchor=True and the rescue rule stays off.
    decisions = classify_roles((0.0, 0.19, 0.20, 0.21, 1.0), h2_sep=0.70)
    assert _roles(decisions) == (
        "UNIQUE", "UNIQUE", "DEVELOPMENT", "DEVELOPMENT", "ANCHOR",
    )


# Item 7 — determinism
def test_classifier_is_deterministic_across_runs():
    h2 = (0.0, 0.33, 0.50, 0.67, 1.0, 0.0, 0.85)
    a = classify_roles(h2, h2_sep=0.7)
    b = classify_roles(h2, h2_sep=0.7)
    assert a == b


# --- Additional surface (edge cases beyond the §F checklist) ----------------


def test_empty_vector_returns_empty_tuple():
    assert classify_roles((), h2_sep=0.0) == ()


def test_thresholds_dataclass_is_actually_wired():
    """Bumping `anchor_floor` should flip a borderline ANCHOR back to DEVELOPMENT.

    The vector includes a 1.0 so `has_natural_anchor` remains True under
    the tightened threshold; this isolates the test to the threshold
    plumbing rather than the rescue rule.
    """
    h2 = (0.70, 1.0)
    h2_sep = 0.50
    default = classify_roles(h2, h2_sep)
    assert default[0].role == "ANCHOR"
    assert default[1].role == "ANCHOR"

    tight = RoleThresholds(anchor_floor=0.80)
    bumped = classify_roles(h2, h2_sep, thresholds=tight)
    assert bumped[0].role == "DEVELOPMENT"
    assert bumped[1].role == "ANCHOR"


def test_zero_always_unique_even_in_uniform_mode():
    """Hard floor: H2==0.0 → UNIQUE regardless of escapes."""
    # h2_sep low enough to engage uniform mode, but section[1] is exact zero
    decisions = classify_roles((0.9, 0.0, 0.8), h2_sep=0.10)
    assert decisions[0].role == "ANCHOR"
    assert decisions[1].role == "UNIQUE"
    assert decisions[1].confidence == 1.0
    assert decisions[2].role == "ANCHOR"


def test_confidence_stays_in_unit_interval():
    """No clipping pathology across the full input space."""
    grid = [i / 20.0 for i in range(21)]
    for h2_sep in (0.0, 0.1, 0.25, 0.4, 0.7, 1.0):
        for h in grid:
            d = classify_roles((h,), h2_sep=h2_sep)
            assert 0.0 <= d[0].confidence <= 1.0, (h, h2_sep, d)


def test_uniform_escape_engages_strictly_below_floor():
    """Boundary: h2_sep == uniform_floor → standard path (not uniform)."""
    # Construct h2 vector with a section that would be DEVELOPMENT under
    # standard path but ANCHOR under uniform path.
    h2 = (0.55,)
    # h2_sep exactly at floor → standard path → 0.55 < anchor_floor → no anchor
    # max=0.55 → rescue → ANCHOR with conf 0.55*0.75
    on_floor = classify_roles(h2, h2_sep=0.25)
    assert on_floor[0].role == "ANCHOR"  # via rescue
    assert math.isclose(on_floor[0].confidence, 0.55 * 0.75, abs_tol=_TOL)
    # h2_sep below floor → uniform escape → ANCHOR with damped conf
    below = classify_roles(h2, h2_sep=0.24)
    assert below[0].role == "ANCHOR"


def test_decision_namedtuple_equality():
    """RoleDecision must compare by value (frozen dataclass)."""
    a = RoleDecision("ANCHOR", 0.5)
    b = RoleDecision("ANCHOR", 0.5)
    c = RoleDecision("DEVELOPMENT", 0.5)
    assert a == b
    assert a != c


@pytest.mark.parametrize(
    "h, expected_role",
    [
        (0.0, "UNIQUE"),
        (0.05, "UNIQUE"),
        (0.19, "UNIQUE"),
        (0.20, "DEVELOPMENT"),
        (0.50, "DEVELOPMENT"),
        (0.65, "DEVELOPMENT"),
        (0.66, "ANCHOR"),
        (0.85, "ANCHOR"),
        (1.0, "ANCHOR"),
    ],
)
def test_standard_path_threshold_table(h, expected_role):
    """When no escape fires (h2_sep large, ample natural anchors),
    classification is a pure function of H2 against the two thresholds."""
    # Add enough high values to ensure has_natural_anchor=True so the
    # rescue rule never fires on borderline inputs.
    decisions = classify_roles((h, 1.0, 1.0), h2_sep=0.80)
    assert decisions[0].role == expected_role


# --- Insufficient-data abstain path (H2 spec §4 field) -----------------------


def test_classify_roles_insufficient_flag_routes_to_development_not_unique():
    """A section whose ``insufficient=True`` (H2=0.0 sentinel for
    'no chord data') must abstain to DEVELOPMENT with low confidence,
    not confidently label as UNIQUE. This is the fix for the Paramore
    over-labelling case where 30s no-chord spans landed as BRIDGE."""
    decisions = classify_roles(
        (0.8, 0.0, 0.8),
        h2_sep=0.60,
        per_section_insufficient=(False, True, False),
    )
    assert _roles(decisions) == ("ANCHOR", "DEVELOPMENT", "ANCHOR")
    assert math.isclose(decisions[1].confidence, 0.25, abs_tol=_TOL)


def test_classify_roles_genuine_zero_h2_still_unique_when_flag_false():
    """Same shape but ``insufficient=False``: the H2 hard floor
    survives and h==0.0 stays UNIQUE (genuine no-recurrence)."""
    decisions = classify_roles(
        (0.8, 0.0, 0.8),
        h2_sep=0.60,
        per_section_insufficient=(False, False, False),
    )
    assert _roles(decisions) == ("ANCHOR", "UNIQUE", "ANCHOR")
    assert decisions[1].confidence == 1.0


def test_classify_roles_insufficient_flag_optional_backwards_compatible():
    """Legacy three-arg call (no ``per_section_insufficient``)
    preserves the h==0.0 → UNIQUE hard floor. Guards existing callers
    (canonical/extended regression suites) from behaviour drift."""
    decisions = classify_roles((0.8, 0.0, 0.8), h2_sep=0.60)
    assert _roles(decisions) == ("ANCHOR", "UNIQUE", "ANCHOR")


def test_classify_roles_insufficient_flag_shorter_than_h2_vector():
    """Defensive: a shorter ``per_section_insufficient`` tuple only
    affects the indices it covers; out-of-range indices fall back to
    the legacy path. Prevents silent misalignment from crashing."""
    decisions = classify_roles(
        (0.0, 0.0, 0.0),
        h2_sep=0.60,
        per_section_insufficient=(True,),
    )
    # Index 0 → abstain path
    assert decisions[0].role == "DEVELOPMENT"
    assert math.isclose(decisions[0].confidence, 0.25, abs_tol=_TOL)
    # Indices 1 and 2 → legacy hard floor
    assert decisions[1].role == "UNIQUE"
    assert decisions[2].role == "UNIQUE"
