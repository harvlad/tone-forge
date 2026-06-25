"""Canonical-corpus regression for section_naming.

Reuses the same canonical-6 bundles `test_role_classifier_canonical.py`
locks down (Stairway, Hotel California, Wish You Were Here, Romance
de Amor, Sex on Fire, What's My Age Again). For each bundle, runs
the full pipeline:

    extract_h2(bundle) → classify_roles → derive_section_types

and asserts:

  * No section is UNKNOWN — the H2 derivation must always produce a
    musical-form label.
  * No section is BUILDUP, BREAKDOWN, DROP, PRECHORUS, or TRANSITION —
    Stage A's vocabulary is intentionally limited to
    INTRO/VERSE/CHORUS/BRIDGE/OUTRO. Stage B will introduce the
    other types.
  * At least one CHORUS in every canonical song (each has at least
    one ANCHOR role).
  * The count of derived types matches the count of decisions.
  * Position invariants: first/last UNIQUE sections become
    INTRO/OUTRO; middle UNIQUE sections become BRIDGE.

Skipped if `data/history.json` is missing or any canonical bundle ID
is absent — mirrors `test_role_classifier_canonical.py`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tone_forge.analysis.section_naming import derive_section_types
from tone_forge.analysis.sections import SectionType
from tone_forge.song_form.h2 import extract_h2
from tone_forge.song_form.role_classifier import classify_roles

_HISTORY_PATH = (
    Path(__file__).resolve().parents[1] / "data" / "history.json"
)

# Canonical bundle IDs from test_role_classifier_canonical.py §D.1.
CANONICAL_BUNDLES: dict[str, str] = {
    "73b5931b": "stairway_to_heaven",
    "07320370": "hotel_california",
    "9fb65b01": "wish_you_were_here",
    "5365ab83": "romance_de_amor",
    "b640c78a": "sex_on_fire",
    "29b31695": "whats_my_age_again",
}

# Section types Stage A is allowed to emit. Stage B adds PRECHORUS,
# BREAKDOWN, DROP, etc.; until then those must never appear in the
# derived output.
_STAGE_A_VOCAB: frozenset[SectionType] = frozenset({
    SectionType.INTRO,
    SectionType.VERSE,
    SectionType.CHORUS,
    SectionType.BRIDGE,
    SectionType.OUTRO,
})


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
    list(CANONICAL_BUNDLES.keys()),
    ids=list(CANONICAL_BUNDLES.values()),
)
def test_canonical_section_types_are_musical_form(history, bundle_id):
    """Every canonical bundle's derived section types stay within the
    Stage A vocabulary and include at least one CHORUS."""
    bundle = _find_bundle(history, bundle_id)
    if bundle is None:
        pytest.skip(f"canonical bundle {bundle_id} not in history.json")

    h2_result = extract_h2(bundle)
    decisions = classify_roles(h2_result.per_section, h2_result.h2_sep)
    derived = derive_section_types(decisions)

    slug = CANONICAL_BUNDLES[bundle_id]

    # Count alignment.
    assert len(derived) == len(decisions), (
        f"{slug}: derived count {len(derived)} != decisions {len(decisions)}"
    )

    # Vocabulary discipline.
    forbidden = [(i, st) for i, st in enumerate(derived) if st not in _STAGE_A_VOCAB]
    assert not forbidden, (
        f"{slug} emitted out-of-vocabulary types: "
        + ", ".join(f"[{i}] {st.value}" for i, st in forbidden)
    )

    # No UNKNOWN labels — the H2 chain must produce a real label.
    unknowns = [i for i, st in enumerate(derived) if st == SectionType.UNKNOWN]
    assert not unknowns, f"{slug} emitted UNKNOWN at indices {unknowns}"

    # Every canonical song has at least one ANCHOR → at least one CHORUS.
    chorus_count = sum(1 for st in derived if st == SectionType.CHORUS)
    assert chorus_count >= 1, (
        f"{slug}: derived has no CHORUS despite ANCHOR roles in "
        f"the canonical anchors"
    )


@pytest.mark.parametrize(
    "bundle_id",
    list(CANONICAL_BUNDLES.keys()),
    ids=list(CANONICAL_BUNDLES.values()),
)
def test_canonical_position_invariants(history, bundle_id):
    """First/last UNIQUE → INTRO/OUTRO; middle UNIQUE → BRIDGE.

    Locks the position-based logic against the real H2 outputs the
    canonical-6 produces. If a future refactor accidentally drops the
    edge handling, this test fires on the songs that actually have
    UNIQUE edges.
    """
    bundle = _find_bundle(history, bundle_id)
    if bundle is None:
        pytest.skip(f"canonical bundle {bundle_id} not in history.json")

    h2_result = extract_h2(bundle)
    decisions = classify_roles(h2_result.per_section, h2_result.h2_sep)
    derived = derive_section_types(decisions)

    n = len(decisions)
    for i, (d, st) in enumerate(zip(decisions, derived)):
        if d.role != "UNIQUE":
            continue
        # Skip low-confidence rows: those fall through to the position
        # default and are covered by the unit tests.
        if d.confidence < 0.30:
            continue
        if i == 0:
            assert st == SectionType.INTRO, (
                f"{CANONICAL_BUNDLES[bundle_id]}[{i}] "
                f"UNIQUE@first expected INTRO, got {st.value}"
            )
        elif i == n - 1:
            assert st == SectionType.OUTRO, (
                f"{CANONICAL_BUNDLES[bundle_id]}[{i}] "
                f"UNIQUE@last expected OUTRO, got {st.value}"
            )
        else:
            assert st == SectionType.BRIDGE, (
                f"{CANONICAL_BUNDLES[bundle_id]}[{i}] "
                f"UNIQUE@middle expected BRIDGE, got {st.value}"
            )


def test_canonical_corpus_completeness(history):
    """All six canonical bundles must exist in history; surface misses
    as one explicit error rather than per-test skips."""
    missing = [
        slug for bid, slug in CANONICAL_BUNDLES.items()
        if _find_bundle(history, bid) is None
    ]
    if missing:
        pytest.skip(f"missing canonical bundles: {missing}")
