"""Self-retrieval invariants for the bundled monitor chain bank.

The §0 ambient-redesign entry documents the operator's manual
validation:

    Non-ambient cross-checks: every other catalog chain still
    self-matches at rank 1.

That check was run from a one-shot tmp harness
(``/tmp/ambient_retrieval_validation.py``) and never landed in the
repo. This file pins the invariant in CI so it can't silently
regress on the next chain edit.

What this file does *not* test:

  * The librosa-backed query path (``_extract_query_fingerprint``).
    That requires real audio and lives in heavier integration suites.
    The structural invariant — catalog z-norm + L2 distance puts a
    chain at rank 1 against itself — is what matters here, and it
    holds at the catalog layer without any audio in the loop.
  * The ``recommend()`` public surface. That's tier-policy land,
    covered by ``test_tone_retrieve.py``. This file probes
    underneath, against ``_get_catalog()`` and ``_znorm_l2()``
    directly.

Three invariants pinned:

  1. **Bank loads completely.** Every YAML in the bank has a
     fingerprint that ``_load_entry`` accepts, and the cached
     ``_Catalog`` carries exactly one entry per chain id.
  2. **Self-distance is zero.** A chain's fingerprint vector
     compared against itself, with the same validity mask on both
     sides, produces L2 distance == 0 (modulo machine epsilon).
     The z-norm denominator is the same on both sides so it
     cancels; this should be exact zero up to floating-point noise.
  3. **Self-rank is 1, no ties at the top.** Each chain's
     own fingerprint is strictly closer to itself than to any
     other chain in the bank. A tie at the top would mean two
     chains live at the same point in feature space — the catalog
     would route arbitrarily and the policy layer's ``family``
     gate wouldn't be tight enough to recover.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tone_forge.monitor.loader import list_chain_ids
from tone_forge.tone import guitar_catalog as gc


# A small tolerance for floating-point comparisons. The math is
# (q - c) / std with q == c, so the exact result should be 0.0 —
# but z-norm scaling involves a divide that can leave sub-epsilon
# residue on some platforms.
_ZERO_TOL = 1e-9


@pytest.fixture
def catalog() -> gc._Catalog:
    """Force a fresh read from disk so prior tests can't poison
    the cache with their own mutations."""
    gc._reset_catalog_cache()
    cat = gc._get_catalog()
    yield cat
    # Don't leave a stale catalog in the cache for the next test
    # file (some tests in the broader suite write fingerprints into
    # tmp and reset the cache themselves).
    gc._reset_catalog_cache()


# ---------------------------------------------------------------------------
# Bank loads completely
# ---------------------------------------------------------------------------


def test_catalog_loads_every_bundled_chain(catalog: gc._Catalog) -> None:
    """One entry per YAML in the bank. A silent drop here (loader
    rejected a fingerprint, file got removed without the YAML
    going with it) would degrade retrieval to UNKNOWN-tier
    fallback without a hard failure — which is the wrong behavior
    for a bundled artifact."""
    yaml_ids = set(list_chain_ids())
    catalog_ids = {e.chain_id for e in catalog.entries}
    assert catalog_ids == yaml_ids, (
        f"catalog/yaml mismatch: only-in-yaml={yaml_ids - catalog_ids}, "
        f"only-in-catalog={catalog_ids - yaml_ids}"
    )


def test_catalog_has_at_least_one_entry(catalog: gc._Catalog) -> None:
    """Belt-and-braces: ``_get_catalog`` returns an empty catalog on
    "no fingerprints found" so the recommend path can still serve
    an UNKNOWN fallback. A bundled-artifact test suite ending up
    with that fallback would mean every following assertion no-ops
    silently. Make the empty case explicit."""
    assert len(catalog.entries) >= 1, (
        "catalog is empty — fingerprints may have been removed or "
        "the loader silently rejected all of them"
    )


# ---------------------------------------------------------------------------
# Self-distance is zero
# ---------------------------------------------------------------------------


def test_every_chain_self_distance_is_zero(catalog: gc._Catalog) -> None:
    """``_znorm_l2(v, v, std, validity, validity)`` — same vector,
    same validity on both sides — must be zero up to floating
    point noise. If this ever breaks, the distance function itself
    has acquired a bug (e.g. dropped axes asymmetrically), not the
    bank."""
    for entry in catalog.entries:
        d = gc._znorm_l2(
            entry.vector,
            entry.vector,
            catalog.feature_std,
            query_validity=entry.validity,
            catalog_validity=entry.validity,
        )
        assert d == pytest.approx(0.0, abs=_ZERO_TOL), (
            f"{entry.chain_id}: self-distance = {d!r}, expected ~0"
        )


# ---------------------------------------------------------------------------
# Self-rank is 1, no ties at the top
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("chain_id", sorted(list_chain_ids()))
def test_chain_self_matches_at_rank_1(
    chain_id: str, catalog: gc._Catalog
) -> None:
    """Use each catalog entry as the *query* against the whole
    bank. The closest entry must be itself, and there must be no
    tie at the top — a tie would mean two chains live at the same
    point in feature space, which the policy layer's family gate
    cannot disambiguate.

    This pins the operator's hand-verified invariant from the
    ambient-redesign §0 entry ("Non-ambient cross-checks: every
    other catalog chain still self-matches at rank 1") so it can't
    silently regress on the next chain edit.
    """
    query_entry = next(e for e in catalog.entries if e.chain_id == chain_id)

    distances = []
    for entry in catalog.entries:
        d = gc._znorm_l2(
            query_entry.vector,
            entry.vector,
            catalog.feature_std,
            query_validity=query_entry.validity,
            catalog_validity=entry.validity,
        )
        distances.append((entry.chain_id, d))

    distances.sort(key=lambda pair: pair[1])
    closest_id, closest_d = distances[0]
    second_id, second_d = distances[1]

    assert closest_id == chain_id, (
        f"{chain_id}: closest match is {closest_id!r} at d={closest_d:.6f}, "
        f"not self; full ranking: {distances}"
    )
    # Strict inequality — a tie at the top would mean two chains
    # occupy the same point in feature space.
    assert closest_d < second_d, (
        f"{chain_id}: tie at rank 1 with {second_id!r} (both at d={closest_d:.6f}); "
        f"second-place spread is zero, catalog is degenerate at this row"
    )


def test_no_two_chains_are_at_distance_zero(catalog: gc._Catalog) -> None:
    """Whole-bank version of the no-tie check. Two distinct chains
    landing at distance 0 from each other would corrupt retrieval
    regardless of which one was the query — it's a property of the
    *bank*, not of any single query row, and it's worth pinning
    directly rather than only via the parametrized
    self-rank-1 check."""
    entries = list(catalog.entries)
    degenerate_pairs = []
    for i, a in enumerate(entries):
        for b in entries[i + 1:]:
            d = gc._znorm_l2(
                a.vector,
                b.vector,
                catalog.feature_std,
                query_validity=a.validity,
                catalog_validity=b.validity,
            )
            if d == pytest.approx(0.0, abs=_ZERO_TOL):
                degenerate_pairs.append((a.chain_id, b.chain_id, d))
    assert not degenerate_pairs, (
        f"distinct chains at distance ~0: {degenerate_pairs}"
    )
