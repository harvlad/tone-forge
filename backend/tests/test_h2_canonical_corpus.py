"""H2 extractor validation against the frozen canonical-6 corpus.

This test is **the gate** between the H2 extractor and any
downstream classifier work (per the 2026-06-21 directive). It loads
the six bundles listed in `backend/corpus_freeze.md` Section A from
`backend/data/history.json` and asserts every per-section and
aggregate value matches the anchors in
`backend/h2_specification.md` Section 5 within ±0.001.

If `data/history.json` is missing or any canonical bundle ID is
absent, every test in this module is skipped — the hermetic
fixture-based unit tests in `test_h2_extractor.py` still run.

Spec §9 item 1.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from tone_forge.song_form.h2 import extract_h2

_HISTORY_PATH = (
    Path(__file__).resolve().parents[1] / "data" / "history.json"
)
_TOLERANCE = 0.001


# Anchors copied verbatim from `backend/h2_specification.md` §5.
# Bundle IDs from `backend/corpus_freeze.md` §A.
CANONICAL_ANCHORS = {
    "73b5931b": {
        "slug": "stairway_to_heaven",
        "n_used": 3,
        "h2_sep": 0.808,
        "per_section": (
            0.625, 0.333, 0.250, 0.400, 0.444, 0.286,
            0.000, 0.000, 0.000, 0.750, 1.000, 1.000,
        ),
    },
    "07320370": {
        "slug": "hotel_california",
        "n_used": 3,
        "h2_sep": 0.845,
        "per_section": (
            0.217, 0.667, 0.667, 0.000, 0.000, 0.500,
            1.000, 0.667, 0.000,
        ),
    },
    "9fb65b01": {
        "slug": "wish_you_were_here",
        "n_used": 3,
        "h2_sep": 0.670,
        "per_section": (
            0.333, 0.250, 0.564, 0.000, 0.444, 0.273,
            0.333, 0.000,
        ),
    },
    "5365ab83": {
        "slug": "romance_de_amor",
        "n_used": 3,
        "h2_sep": 0.837,
        "per_section": (
            0.333, 0.333, 0.286, 0.667, 0.000, 1.000,
            1.000, 0.000, 0.167, 0.667, 0.000, 1.000,
            0.500, 0.500, 0.000,
        ),
    },
    "b640c78a": {
        "slug": "sex_on_fire",
        "n_used": 3,
        "h2_sep": 0.702,
        "per_section": (
            0.286, 0.600, 0.857, 0.000, 0.000, 1.000,
            0.000, 0.867, 0.000, 0.696, 0.333, 1.000,
            1.000, 1.000, 0.385, 1.000, 0.500, 0.750,
            1.000, 0.000,
        ),
    },
    "29b31695": {
        "slug": "whats_my_age_again",
        "n_used": 3,
        "h2_sep": 0.198,
        "per_section": (
            1.000, 0.750, 1.000, 0.565, 1.000, 0.609,
            0.833, 0.889,
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
    """Return the analysis-result dict for `bundle_id`, or None.

    History entries wrap the actual analysis dict under `result`;
    `extract_h2` consumes the inner dict per spec §2.
    """
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
    list(CANONICAL_ANCHORS.keys()),
    ids=[v["slug"] for v in CANONICAL_ANCHORS.values()],
)
def test_h2_matches_taxonomy_anchors(history, bundle_id):
    """Per-section and aggregate H2 must match the frozen anchors.

    This is the only test that touches real bundle data. Failure here
    means either (a) the H2 extractor regressed against the spec or
    (b) the bundle has been re-analysed and the underlying chord
    sequence shifted. Either way the classifier work must not
    proceed until this passes.
    """
    bundle = _find_bundle(history, bundle_id)
    if bundle is None:
        pytest.skip(f"canonical bundle {bundle_id} not in history.json")

    anchor = CANONICAL_ANCHORS[bundle_id]
    result = extract_h2(bundle)

    assert result.n_used == anchor["n_used"], (
        f"{anchor['slug']}: n_used={result.n_used}, expected={anchor['n_used']}"
    )
    assert not result.degenerate, f"{anchor['slug']}: unexpectedly degenerate"

    assert len(result.per_section) == len(anchor["per_section"]), (
        f"{anchor['slug']}: per_section length "
        f"{len(result.per_section)} != {len(anchor['per_section'])}"
    )

    mismatches = []
    for i, (got, exp) in enumerate(
        zip(result.per_section, anchor["per_section"])
    ):
        if not math.isclose(got, exp, abs_tol=_TOLERANCE):
            mismatches.append((i, got, exp))
    assert not mismatches, (
        f"{anchor['slug']} per_section mismatch (tol={_TOLERANCE}): "
        + ", ".join(f"[{i}] got={g:.4f} exp={e:.4f}" for i, g, e in mismatches)
    )

    assert math.isclose(result.h2_sep, anchor["h2_sep"], abs_tol=_TOLERANCE), (
        f"{anchor['slug']}: h2_sep={result.h2_sep:.4f}, "
        f"expected={anchor['h2_sep']} (tol={_TOLERANCE})"
    )


def test_canonical_corpus_completeness(history):
    """Every canonical bundle must exist in history; surface the
    misses as one explicit error rather than six skips."""
    missing = [
        v["slug"]
        for bid, v in CANONICAL_ANCHORS.items()
        if _find_bundle(history, bid) is None
    ]
    if missing:
        pytest.skip(f"missing canonical bundles: {missing}")
