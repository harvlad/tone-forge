"""Canonical-corpus regression for Stage B song_form refinement.

Runs the full chain on bundles persisted in `data/history.json`:

    extract_h2(bundle)
        → classify_roles(...)
        → derive_section_types(...)       [Stage A]
        → aggregate_song_form(...)        [Stage B prep]
        → refine_section_types(...)       [Stage B]

Three test groups:

  1. **Canonical-6 ground truth** — parametrised over the canonical
     bundle IDs from `test_role_classifier_canonical.py`. Each
     bundle has a hand-labelled `SONG_FORM_GROUND_TRUTH` entry
     describing which sections must be INSTRUMENTAL, PRECHORUS, or
     BREAKDOWN. Currently a recall floor (empty lists mean "no
     assertion"); future work populates ground truth bundle-by-
     bundle. Skipped if bundles aren't in history.json yet.

  2. **Real-world regressions** — specific named bundles whose
     behaviour we've already locked down because they revealed a
     bug. Currently: Birds of Tokyo "If This Ship Sinks (I Give
     In)" (riff-uniform song; section[0] must NOT be CHORUS after
     B5b's Pass-0 edge demotion).

  3. **Vocabulary discipline across the whole corpus** — every
     multi-section chord-carrying bundle in history.json must
     produce refined section types within the Stage B vocabulary
     (no UNKNOWN, no out-of-vocab types). Catches future
     refactors that accidentally widen the type set or break the
     pipeline.

Skipped if `data/history.json` is missing — mirrors
`test_section_naming_canonical.py`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tone_forge.analysis.section_naming import derive_section_types
from tone_forge.analysis.sections import SectionType
from tone_forge.analysis.song_form import (
    SongFormThresholds,
    refine_section_types,
)
from tone_forge.analysis.song_form_aggregates import aggregate_song_form
from tone_forge.song_form.h2 import extract_h2
from tone_forge.song_form.role_classifier import classify_roles

_HISTORY_PATH = (
    Path(__file__).resolve().parents[1] / "data" / "history.json"
)

# Canonical bundle IDs — same set as test_role_classifier_canonical.py
# and test_section_naming_canonical.py.
CANONICAL_BUNDLES: dict[str, str] = {
    "73b5931b": "stairway_to_heaven",
    "07320370": "hotel_california",
    "9fb65b01": "wish_you_were_here",
    "5365ab83": "romance_de_amor",
    "b640c78a": "sex_on_fire",
    "29b31695": "whats_my_age_again",
}

# Hand-labelled per-bundle ground truth. Each entry is a recall
# floor: indices listed here MUST be refined to the given type;
# unlisted indices are unconstrained. Empty list means "no
# assertion of that type" (still validates the chain ran, just
# without a per-section ground-truth check).
#
# This dict starts as a scaffold — populated bundle-by-bundle as
# each canonical recording is loaded into history.json and
# hand-labelled. Until then, the parametrised test skips per
# missing bundle (handled by the per-test skip below).
SONG_FORM_GROUND_TRUTH: dict[str, dict[str, list[int]]] = {
    "73b5931b": {  # Stairway to Heaven — long instrumental intro
        "instrumental_indices": [],
        "prechorus_indices": [],
        "breakdown_indices": [],
    },
    "07320370": {  # Hotel California — long instrumental outro (solo)
        "instrumental_indices": [],
        "prechorus_indices": [],
        "breakdown_indices": [],
    },
    "9fb65b01": {  # Wish You Were Here — repeated CHORUS, precision lock
        "instrumental_indices": [],
        "prechorus_indices": [],
        "breakdown_indices": [],
    },
    "5365ab83": {  # Romance de Amor — fully instrumental
        "instrumental_indices": [],
        "prechorus_indices": [],
        "breakdown_indices": [],
    },
    "b640c78a": {  # Sex on Fire — clean verse/chorus, no INSTRUMENTAL
        "instrumental_indices": [],
        "prechorus_indices": [],
        "breakdown_indices": [],
    },
    "29b31695": {  # What's My Age Again — pop-punk prechorus ramp
        "instrumental_indices": [],
        "prechorus_indices": [],
        "breakdown_indices": [],
    },
}

# Section types Stage B is allowed to emit. Strictly a superset of
# Stage A's vocabulary plus the refinement types this milestone
# introduces.
_STAGE_B_VOCAB: frozenset[SectionType] = frozenset({
    SectionType.INTRO,
    SectionType.VERSE,
    SectionType.CHORUS,
    SectionType.BRIDGE,
    SectionType.OUTRO,
    SectionType.INSTRUMENTAL,
    SectionType.PRECHORUS,
    SectionType.BREAKDOWN,
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


def _per_stem_features_from_bundle(
    bundle: dict,
) -> tuple[dict[str, list[dict]], list[float]]:
    """Pull per-stem section feature rows and per-section energies
    from a persisted bundle.

    The persisted shape is ``bundle["sections"][i]["debug_features"]``
    where each entry is a dict carrying ``stem_name`` plus the
    fields ``aggregate_song_form`` consumes
    (``lead_activity_score``, ``voiced_frame_ratio``, ``note_count``,
    ``duration_s``). Returns:

    - ``per_stem_features_by_stem``: ``{stem_name: [row_per_section]}``
    - ``energy_means``: ``[float, ...]`` aligned with sections

    Sections with no ``debug_features`` contribute zero rows to that
    stem's per-section list — ``aggregate_song_form`` already handles
    short sequences by padding with zero-aggregates.
    """
    sections = bundle.get("sections") or []
    per_stem: dict[str, list[dict]] = {}
    energies: list[float] = []
    for section in sections:
        energies.append(float(section.get("energy_mean", 0.0)))
        for feat in section.get("debug_features", []) or []:
            stem = feat.get("stem_name")
            if not isinstance(stem, str) or not stem:
                continue
            per_stem.setdefault(stem, []).append(feat)
    return per_stem, energies


def _run_stage_b(bundle: dict) -> tuple[SectionType, ...]:
    """Run extract_h2 → classify_roles → derive_section_types →
    aggregate_song_form → refine_section_types on a bundle.

    Returns the refined section types. Empty tuple on degenerate
    H2 (the production pipeline skips the override in that case,
    so a degenerate-H2 bundle isn't a Stage B concern).
    """
    h2_result = extract_h2(bundle)
    if h2_result.degenerate:
        return ()
    decisions = classify_roles(h2_result.per_section, h2_result.h2_sep)
    stage_a = derive_section_types(decisions)
    per_stem, energies = _per_stem_features_from_bundle(bundle)
    aggregates = aggregate_song_form(per_stem, energies)
    return refine_section_types(stage_a, aggregates)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def history() -> list[dict]:
    h = _load_history()
    if h is None:
        pytest.skip(
            f"canonical corpus unavailable (no {_HISTORY_PATH}); "
            "run corpus_expand first"
        )
    return h


# ---------------------------------------------------------------------------
# Group 1: canonical-6 ground truth (recall floor)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bundle_id",
    list(CANONICAL_BUNDLES.keys()),
    ids=list(CANONICAL_BUNDLES.values()),
)
def test_canonical_stage_b_ground_truth(history, bundle_id):
    """Refined Stage B labels must satisfy the per-bundle recall
    floor in ``SONG_FORM_GROUND_TRUTH``. Empty lists assert no
    per-section ground truth — only that the chain runs end-to-end.
    """
    bundle = _find_bundle(history, bundle_id)
    if bundle is None:
        pytest.skip(f"canonical bundle {bundle_id} not in history.json")

    refined = _run_stage_b(bundle)
    if not refined:
        pytest.skip(
            f"canonical bundle {bundle_id} has degenerate H2; "
            "Stage B abstains by design"
        )

    slug = CANONICAL_BUNDLES[bundle_id]
    truth = SONG_FORM_GROUND_TRUTH.get(bundle_id, {})

    for idx in truth.get("instrumental_indices", []):
        assert refined[idx] is SectionType.INSTRUMENTAL, (
            f"{slug}[{idx}] expected INSTRUMENTAL, got {refined[idx].value}"
        )
    for idx in truth.get("prechorus_indices", []):
        assert refined[idx] is SectionType.PRECHORUS, (
            f"{slug}[{idx}] expected PRECHORUS, got {refined[idx].value}"
        )
    for idx in truth.get("breakdown_indices", []):
        assert refined[idx] is SectionType.BREAKDOWN, (
            f"{slug}[{idx}] expected BREAKDOWN, got {refined[idx].value}"
        )


@pytest.mark.parametrize(
    "bundle_id",
    list(CANONICAL_BUNDLES.keys()),
    ids=list(CANONICAL_BUNDLES.values()),
)
def test_canonical_6_pass_4b_does_not_change_labels(history, bundle_id):
    """Pass 4b (vocal-pitch CHORUS→VERSE demotion) must abstain on
    the canonical-6 corpus.

    Compares two runs on each canonical bundle:
      * defaults: Pass 4b enabled with production thresholds.
      * disabled: Pass 4b's threshold gate configured so it can
        never fire (offset ≫ any plausible cohort spread, ratio ≪
        any plausible dip).

    Identical outputs mean Pass 4b is silent on the canonical
    corpus — verse/chorus disambiguation on shared-progression
    songs is Pass 4b's job, but on the canonical bundles either
    (a) H2 already separates verse from chorus (so Pass 4b sees
    <4 CHORUSes and abstains via ``verse_demotion_min_choruses``),
    or (b) pitch evidence is absent (fully instrumental /
    canonical-6 Romance de Amor), or (c) the choruses genuinely
    have consistent vocal pitch and no demotion candidate crosses
    the AND-gate. If this test flips, the correct fix is to
    tighten Pass 4b's thresholds — not to bypass the abstain-on-
    canonical property.
    """
    bundle = _find_bundle(history, bundle_id)
    if bundle is None:
        pytest.skip(f"canonical bundle {bundle_id} not in history.json")

    h2_result = extract_h2(bundle)
    if h2_result.degenerate:
        pytest.skip(
            f"canonical bundle {bundle_id} has degenerate H2; "
            "Stage B abstains by design"
        )
    decisions = classify_roles(h2_result.per_section, h2_result.h2_sep)
    stage_a = derive_section_types(decisions)
    per_stem, energies = _per_stem_features_from_bundle(bundle)
    aggregates = aggregate_song_form(per_stem, energies)

    with_pass_4b = refine_section_types(stage_a, aggregates)

    # Disable Pass 4b entirely: negative offset means every
    # candidate's median clears the "must dip below" gate; but
    # combined with ratio=0.0 (candidate range must be strictly
    # below 0.0 × cohort_range = 0.0, which is impossible for
    # positive ranges) the AND-gate can never fire.
    disabled = SongFormThresholds(
        verse_pitch_semitone_offset=-1e9,
        verse_pitch_range_ratio=0.0,
    )
    without_pass_4b = refine_section_types(stage_a, aggregates, disabled)

    slug = CANONICAL_BUNDLES[bundle_id]
    assert with_pass_4b == without_pass_4b, (
        f"Pass 4b changed labels on canonical bundle {slug} "
        f"({bundle_id}). With Pass 4b: "
        f"{[t.value for t in with_pass_4b]}. Without: "
        f"{[t.value for t in without_pass_4b]}."
    )


def test_canonical_corpus_completeness(history):
    """All six canonical bundles should eventually exist in history.

    Surfaces the missing-bundles list as a single skip rather than
    one skip per parametrised test. Doesn't fail because the
    corpus is populated incrementally.
    """
    missing = [
        slug for bid, slug in CANONICAL_BUNDLES.items()
        if _find_bundle(history, bid) is None
    ]
    if missing:
        pytest.skip(f"missing canonical bundles: {missing}")


# ---------------------------------------------------------------------------
# Group 2: real-world specific regressions
# ---------------------------------------------------------------------------


# Bundle IDs of recordings that caught visible JAM bugs. Each is a
# hard regression: behaviour on these specific bundles is locked.
# Multiple IDs per song are listed because the same recording can
# be re-analysed and end up under different bundle IDs in history;
# any one being present is enough to exercise the regression.
_BIRDS_OF_TOKYO_BUNDLE_IDS: tuple[str, ...] = (
    "66a50076",
    "626cc398",
    "64bbc506",
    "290949df",
    "d0bc6cf6",
)


def test_birds_of_tokyo_first_section_not_chorus(history):
    """Regression for the JAM "every section labeled chorus" bug.

    Birds of Tokyo — "If This Ship Sinks (I Give In)" has a
    riff-uniform progression (E G#m A#7 G#m C#m C#sus2 C#m
    recurring throughout). H2 sees ANCHOR on every section, Stage
    A maps every one to CHORUS. The actual bundle has section[0]
    at energy_mean=0.145 vs body sections at 0.6-0.76 — clearly
    an intro. Pass 0 (energy_z edge-demotion) must demote it.

    Hard assertion: section[0] is NOT CHORUS. Soft assertion
    (informational only): it should be INTRO under default
    thresholds, but any non-CHORUS label still counts as the bug
    being fixed (a future PRECHORUS/BREAKDOWN classification would
    still be an improvement over "every section is chorus").
    """
    bundle = next(
        (
            b for b in (
                _find_bundle(history, bid) for bid in _BIRDS_OF_TOKYO_BUNDLE_IDS
            )
            if b is not None
        ),
        None,
    )
    if bundle is None:
        pytest.skip(
            "no Birds-of-Tokyo bundle in history.json; "
            f"expected one of {_BIRDS_OF_TOKYO_BUNDLE_IDS}"
        )

    refined = _run_stage_b(bundle)
    assert refined, "Stage B returned empty refinement for BoT bundle"

    # Hard: section[0] must not be CHORUS — that's the visible bug.
    assert refined[0] is not SectionType.CHORUS, (
        f"BoT section[0] still CHORUS after Stage B; "
        f"Pass-0 edge-demotion regressed. Full refinement: "
        f"{[r.value for r in refined]}"
    )


# ---------------------------------------------------------------------------
# Group 3: vocabulary discipline across the whole corpus
# ---------------------------------------------------------------------------


def _multi_section_chord_bundles(history: list[dict]) -> list[tuple[str, dict]]:
    """Return ``(bundle_id, result_dict)`` for every history entry
    that has a non-degenerate Stage A input shape: at least 2
    sections and at least one chord.
    """
    out: list[tuple[str, dict]] = []
    for entry in history:
        bid = entry.get("id")
        result = entry.get("result")
        if not isinstance(bid, str) or not isinstance(result, dict):
            continue
        sections = result.get("sections") or []
        chords = result.get("chords") or []
        if len(sections) >= 2 and len(chords) >= 1:
            out.append((bid, result))
    return out


def test_stage_b_vocabulary_discipline_across_corpus(history):
    """Every chord-carrying multi-section bundle must produce
    refined types within ``_STAGE_B_VOCAB``. No UNKNOWN. No
    out-of-vocab types (e.g. DROP, TRANSITION) — those belong to
    other subsystems.

    This is the broad regression net: when a future refactor
    accidentally widens Stage B's output set or breaks the chain,
    this fires across the entire persisted corpus, not just the
    canonical-6.
    """
    bundles = _multi_section_chord_bundles(history)
    if not bundles:
        pytest.skip("no multi-section chord bundles in history.json")

    failures: list[str] = []
    for bid, result in bundles:
        try:
            refined = _run_stage_b(result)
        except Exception as exc:  # pragma: no cover — diagnostic surface
            failures.append(f"{bid}: chain raised {type(exc).__name__}: {exc}")
            continue
        if not refined:
            # Degenerate H2 — production pipeline preserves the
            # legacy energy-heuristic labels in that case, so
            # nothing to validate here.
            continue
        bad = [
            (i, st.value) for i, st in enumerate(refined)
            if st not in _STAGE_B_VOCAB
        ]
        if bad:
            failures.append(
                f"{bid}: out-of-vocab refined types: "
                + ", ".join(f"[{i}] {v}" for i, v in bad)
            )
    assert not failures, (
        f"vocabulary discipline failures across "
        f"{len(bundles)} bundles:\n"
        + "\n".join(failures)
    )


def test_stage_b_count_alignment_across_corpus(history):
    """For every non-degenerate bundle, refined-types count equals
    sections count. Catches off-by-one drift in the
    aggregate_song_form / refine_section_types contract.
    """
    bundles = _multi_section_chord_bundles(history)
    if not bundles:
        pytest.skip("no multi-section chord bundles in history.json")

    failures: list[str] = []
    for bid, result in bundles:
        refined = _run_stage_b(result)
        if not refined:
            continue
        expected = len(result.get("sections") or [])
        if len(refined) != expected:
            failures.append(
                f"{bid}: {len(refined)} refined types vs "
                f"{expected} sections"
            )
    assert not failures, "count-alignment failures:\n" + "\n".join(failures)
