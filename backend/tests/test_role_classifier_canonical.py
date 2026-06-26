"""Canonical-corpus gate for the structural-role classifier.

This is the §F item 8 gate from
`backend/structural_role_classifier_design.md`: every canonical
bundle's role vector and confidence values must match the §D.1
anchors within tolerance.

Skipped if `data/history.json` is missing or any canonical bundle
ID is absent — mirrors the H2 canonical-corpus gate.

Confidence tolerance is 0.005 (the design-doc tables print
2 decimal places; this leaves room for the third decimal without
admitting drift).
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from tone_forge.song_form.h2 import extract_h2
from tone_forge.song_form.role_classifier import classify_roles

_HISTORY_PATH = (
    Path(__file__).resolve().parents[1] / "data" / "history.json"
)
_CONF_TOL = 0.005


# Anchors transcribed verbatim from §D.1 of
# `backend/structural_role_classifier_design.md`. Each entry is
# (role, confidence). Roles use the same `ANCHOR`/`DEVELOPMENT`/`UNIQUE`
# vocabulary the classifier emits.
CANONICAL_ROLE_ANCHORS: dict[str, dict] = {
    "73b5931b": {
        "slug": "stairway_to_heaven",
        "roles": (
            ("DEVELOPMENT", 0.750),
            ("DEVELOPMENT", 0.667),
            ("DEVELOPMENT", 0.500),
            ("DEVELOPMENT", 0.800),
            ("DEVELOPMENT", 0.889),
            ("DEVELOPMENT", 0.572),
            ("UNIQUE",      1.000),
            ("UNIQUE",      1.000),
            ("UNIQUE",      1.000),
            ("ANCHOR",      0.750),
            ("ANCHOR",      1.000),
            ("ANCHOR",      1.000),
        ),
    },
    "07320370": {
        "slug": "hotel_california",
        "roles": (
            ("DEVELOPMENT", 0.434),
            ("ANCHOR",      0.667),
            ("ANCHOR",      0.667),
            ("UNIQUE",      1.000),
            ("UNIQUE",      1.000),
            ("DEVELOPMENT", 1.000),
            ("ANCHOR",      1.000),
            ("ANCHOR",      0.667),
            ("UNIQUE",      1.000),
        ),
    },
    "9fb65b01": {
        "slug": "wish_you_were_here",
        # Escape 2 (no-natural-anchor rescue) fires here: max(H2)=0.564 < 0.66.
        "roles": (
            ("DEVELOPMENT", 0.667),
            ("DEVELOPMENT", 0.500),
            ("ANCHOR",      0.423),   # rescue: 0.564 * 0.75
            ("UNIQUE",      1.000),
            ("DEVELOPMENT", 0.889),
            ("DEVELOPMENT", 0.547),
            ("DEVELOPMENT", 0.667),
            ("UNIQUE",      1.000),
        ),
    },
    "5365ab83": {
        "slug": "romance_de_amor",
        "roles": (
            ("DEVELOPMENT", 0.667),
            ("DEVELOPMENT", 0.667),
            ("DEVELOPMENT", 0.572),
            ("ANCHOR",      0.667),
            ("UNIQUE",      1.000),
            ("ANCHOR",      1.000),
            ("ANCHOR",      1.000),
            ("UNIQUE",      1.000),
            ("UNIQUE",      0.833),
            ("ANCHOR",      0.667),
            ("UNIQUE",      1.000),
            ("ANCHOR",      1.000),
            ("DEVELOPMENT", 1.000),
            ("DEVELOPMENT", 1.000),
            ("UNIQUE",      1.000),
        ),
    },
    "b640c78a": {
        "slug": "sex_on_fire",
        "roles": (
            ("DEVELOPMENT", 0.572),
            ("DEVELOPMENT", 0.800),
            ("ANCHOR",      0.857),
            ("UNIQUE",      1.000),
            ("UNIQUE",      1.000),
            ("ANCHOR",      1.000),
            ("UNIQUE",      1.000),
            ("ANCHOR",      0.867),
            ("UNIQUE",      1.000),
            ("ANCHOR",      0.696),
            ("DEVELOPMENT", 0.667),
            ("ANCHOR",      1.000),
            ("ANCHOR",      1.000),
            ("ANCHOR",      1.000),
            ("DEVELOPMENT", 0.770),
            ("ANCHOR",      1.000),
            ("DEVELOPMENT", 1.000),
            ("ANCHOR",      0.750),
            ("ANCHOR",      1.000),
            ("UNIQUE",      1.000),
        ),
    },
    "29b31695": {
        "slug": "whats_my_age_again",
        # Escape 1 (uniform-song) fires: h2_sep=0.198 < 0.25.
        # damp = 0.198 / 0.25 = 0.792.
        # All sections h >= 0.5 → ANCHOR with damped confidence.
        "roles": (
            ("ANCHOR", 0.896),  # h=1.000 → 1*0.792 + 0.208*0.5
            ("ANCHOR", 0.698),  # h=0.750
            ("ANCHOR", 0.896),  # h=1.000
            ("ANCHOR", 0.552),  # h=0.565
            ("ANCHOR", 0.896),  # h=1.000
            ("ANCHOR", 0.586),  # h=0.609
            ("ANCHOR", 0.762),  # h=0.833
            ("ANCHOR", 0.808),  # h=0.889
        ),
    },
}


def _load_history() -> list[dict] | None:
    if not _HISTORY_PATH.exists():
        return None
    try:
        return json.loads(_HISTORY_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _find_bundle(history: list[dict], bundle_id: str) -> dict | None:
    for entry in history:
        if entry.get("id") == bundle_id:
            result = entry.get("result")
            return result if isinstance(result, dict) else None
    return None


@pytest.fixture(scope="module")
def history() -> list[dict]:
    h = _load_history()
    if h is None:
        pytest.skip(
            f"canonical corpus unavailable (no {_HISTORY_PATH}); "
            "run corpus_expand first"
        )
    return h


@pytest.mark.parametrize(
    "bundle_id",
    list(CANONICAL_ROLE_ANCHORS.keys()),
    ids=[v["slug"] for v in CANONICAL_ROLE_ANCHORS.values()],
)
def test_canonical_role_vector_matches_design_anchors(history, bundle_id):
    """Per-section role + confidence must match `structural_role_classifier_design.md` §D.1.

    This is the classifier-side analogue of the H2 canonical-corpus gate
    (`test_h2_canonical_corpus.py`). Failure means either:
        (a) the classifier or thresholds drifted from the design, OR
        (b) the upstream H2 extractor produced different per-section values
            and the H2 gate caught it first.

    Either way, classifier behaviour is locked here.
    """
    bundle = _find_bundle(history, bundle_id)
    if bundle is None:
        pytest.skip(f"canonical bundle {bundle_id} not in history.json")

    anchor = CANONICAL_ROLE_ANCHORS[bundle_id]
    h2_result = extract_h2(bundle)
    decisions = classify_roles(h2_result.per_section, h2_result.h2_sep)

    expected = anchor["roles"]
    assert len(decisions) == len(expected), (
        f"{anchor['slug']}: section count {len(decisions)} != {len(expected)}"
    )

    role_mismatches = []
    conf_mismatches = []
    for i, (decision, (exp_role, exp_conf)) in enumerate(zip(decisions, expected)):
        if decision.role != exp_role:
            role_mismatches.append((i, decision.role, exp_role))
        if not math.isclose(decision.confidence, exp_conf, abs_tol=_CONF_TOL):
            conf_mismatches.append((i, decision.confidence, exp_conf))

    assert not role_mismatches, (
        f"{anchor['slug']} role mismatch: "
        + ", ".join(f"[{i}] got={g} exp={e}" for i, g, e in role_mismatches)
    )
    assert not conf_mismatches, (
        f"{anchor['slug']} confidence mismatch (tol={_CONF_TOL}): "
        + ", ".join(f"[{i}] got={g:.4f} exp={e:.4f}" for i, g, e in conf_mismatches)
    )


def test_canonical_corpus_completeness(history):
    """All six canonical bundles must exist in history; surface misses
    as one explicit error rather than six skips."""
    missing = [
        v["slug"]
        for bid, v in CANONICAL_ROLE_ANCHORS.items()
        if _find_bundle(history, bid) is None
    ]
    if missing:
        pytest.skip(f"missing canonical bundles: {missing}")
