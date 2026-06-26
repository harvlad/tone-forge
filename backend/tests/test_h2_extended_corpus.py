"""H2 extractor structural sanity on the held-out extended-validation 5.

`backend/corpus_freeze.md` §B reserves five bundles
(enter_sandman, skinny_love, get_lucky, humble, bad_guy) as a
held-out validation set. They carry **no** per-section anchors in
`backend/h2_specification.md` §5 — those bundles were deliberately
excluded from spec anchor selection so the classifier can be tuned
without overfitting to them.

This file therefore asserts only structural soundness:

  * extractor returns without exceptions
  * `degenerate is False`
  * `n_used == 3` (every extended-corpus song is long enough for trigrams)
  * every per-section value lies in `[0.0, 1.0]`
  * `h2_sep` lies in `[0.0, 1.0]`

Empirical baseline values (2026-06-21, captured for reference; not
asserted because no anchors were frozen for the extended set):

    bundle      slug              h2_sep   sections
    6f5f5634    enter_sandman     0.9308   17
    d7804a68    skinny_love       0.7380   16
    422f5db5    get_lucky         0.7861   18
    5dc3da17    humble            0.4482   18
    8ccdf229    bad_guy           0.9175   16

If `data/history.json` is missing or any extended-corpus bundle ID
is absent, every test in this module is skipped — the hermetic
fixture tests and the canonical-corpus gate still run.

Spec §9 item 1 (held-out validation half).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tone_forge.song_form.h2 import extract_h2

_HISTORY_PATH = (
    Path(__file__).resolve().parents[1] / "data" / "history.json"
)

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
def test_extended_bundle_extracts_without_degeneracy(history, bundle_id):
    """Held-out validation: extractor must produce sane trigram H2.

    Asserts structural properties only — no per-section anchors are
    frozen for the extended set (by design, to keep them reusable
    as a tuning hold-out for the downstream classifier).
    """
    bundle = _find_bundle(history, bundle_id)
    if bundle is None:
        pytest.skip(f"extended bundle {bundle_id} not in history.json")

    slug = EXTENDED_BUNDLES[bundle_id]
    result = extract_h2(bundle)

    assert not result.degenerate, f"{slug}: unexpectedly degenerate"
    assert result.n_used == 3, (
        f"{slug}: n_used={result.n_used}, expected 3 "
        "(all extended bundles have enough chords for trigrams)"
    )
    assert len(result.per_section) > 0, f"{slug}: empty per_section"

    for i, v in enumerate(result.per_section):
        assert 0.0 <= v <= 1.0, (
            f"{slug} per_section[{i}]={v} outside [0, 1]"
        )

    assert 0.0 <= result.h2_sep <= 1.0, (
        f"{slug}: h2_sep={result.h2_sep} outside [0, 1]"
    )


def test_extended_corpus_completeness(history):
    """All five extended bundles must be present in history; surface
    misses as one explicit error rather than five skips."""
    missing = [
        slug
        for bid, slug in EXTENDED_BUNDLES.items()
        if _find_bundle(history, bid) is None
    ]
    if missing:
        pytest.skip(f"missing extended bundles: {missing}")
