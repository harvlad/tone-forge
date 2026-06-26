"""Held-out validation gate for the structural-role classifier.

Mirrors `test_h2_extended_corpus.py`: the 5 extended-validation
bundles (`corpus_freeze.md` §B) have no frozen role anchors — they
were deliberately excluded from design-time tuning. This file
asserts only structural soundness:

  * classifier returns without exceptions
  * one decision per section
  * every confidence ∈ [0, 1]
  * every role ∈ {ANCHOR, DEVELOPMENT, UNIQUE}
  * at least one ANCHOR per song (the rescue rule guarantees this for
    any non-degenerate song; no validation bundle is degenerate)

Skipped if `data/history.json` is missing or any extended bundle ID
is absent.

Spec: `backend/structural_role_classifier_design.md` §F item 9.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tone_forge.song_form.h2 import extract_h2
from tone_forge.song_form.role_classifier import classify_roles

_HISTORY_PATH = (
    Path(__file__).resolve().parents[1] / "data" / "history.json"
)
_VALID_ROLES = {"ANCHOR", "DEVELOPMENT", "UNIQUE"}

# Bundle IDs and slugs from `backend/corpus_freeze.md` §B.
EXTENDED_BUNDLES = {
    "6f5f5634": "enter_sandman",
    "d7804a68": "skinny_love",
    "422f5db5": "get_lucky",
    "5dc3da17": "humble",
    "8ccdf229": "bad_guy",
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
            f"extended corpus unavailable (no {_HISTORY_PATH}); "
            "run corpus_expand first"
        )
    return h


@pytest.mark.parametrize(
    "bundle_id",
    list(EXTENDED_BUNDLES.keys()),
    ids=list(EXTENDED_BUNDLES.values()),
)
def test_extended_bundle_classifies_soundly(history, bundle_id):
    """Held-out validation: classifier must produce sane output without
    retuning. Structural assertions only — no per-section anchors are
    frozen for the extended set (by design).
    """
    bundle = _find_bundle(history, bundle_id)
    if bundle is None:
        pytest.skip(f"extended bundle {bundle_id} not in history.json")

    slug = EXTENDED_BUNDLES[bundle_id]
    h2_result = extract_h2(bundle)
    decisions = classify_roles(h2_result.per_section, h2_result.h2_sep)

    assert len(decisions) == len(h2_result.per_section), (
        f"{slug}: classifier dropped or added sections "
        f"({len(decisions)} vs {len(h2_result.per_section)})"
    )

    for i, d in enumerate(decisions):
        assert d.role in _VALID_ROLES, (
            f"{slug} section[{i}]: invalid role {d.role!r}"
        )
        assert 0.0 <= d.confidence <= 1.0, (
            f"{slug} section[{i}]: confidence {d.confidence} outside [0, 1]"
        )

    # At least one ANCHOR per non-degenerate song; the no-natural-anchor
    # rescue or the uniform-song escape both guarantee this.
    anchor_count = sum(1 for d in decisions if d.role == "ANCHOR")
    assert anchor_count >= 1, (
        f"{slug}: zero ANCHOR sections — rescue rule failed"
    )


def test_extended_corpus_completeness(history):
    """All five extended bundles must be present in history."""
    missing = [
        slug
        for bid, slug in EXTENDED_BUNDLES.items()
        if _find_bundle(history, bid) is None
    ]
    if missing:
        pytest.skip(f"missing extended bundles: {missing}")
