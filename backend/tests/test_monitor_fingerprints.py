"""Bundled chain fingerprint JSON parity + schema gates.

The monitor chain bank ships two artifacts per chain: the YAML spec
(loaded by ``tone_forge.monitor.loader``) and the rendered
fingerprint JSON (consumed by ``tone_forge.tone.guitar_catalog``).
They are produced by different workflows — YAML is hand-authored,
fingerprint JSON is emitted by ``scripts/render_chain_references.py``
after a Connect render. Nothing today catches silent drift between
them: a YAML whose ``family`` is bumped without re-rendering the
fingerprint would route under the new family in the policy layer
but match audio under the old family in the catalog.

This file pins:

  1. **Bundle parity.** Every YAML in the bank has a matching
     fingerprint JSON, and every fingerprint JSON has a matching
     YAML. A new chain is incomplete until both sides ship.
  2. **Fingerprint schema.** Every JSON parses cleanly through the
     same loader the catalog uses at runtime
     (``guitar_catalog._load_entry``); ``chain_id`` / ``display_name``
     / ``family`` are present and well-formed; the eight feature
     keys the catalog reads are all populated as numbers; the
     optional ``feature_validity`` mask, when present, has the same
     eight keys with boolean values.
  3. **YAML <-> JSON cross-check.** ``chain_id`` matches the filename
     stem on both sides; ``family`` agrees between YAML and JSON;
     ``display_name`` agrees between YAML and JSON. These three
     fields are the user-facing contract — a quiet mismatch would
     mean the policy router and the catalog distance gate disagree
     about what they're routing.

Loader-internal validation (missing parameter sections, bad family
strings, filename/id mismatch on the YAML side) lives in
``test_monitor_loader.py`` and is not re-tested here.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tone_forge.contracts import MonitorChainFamily
from tone_forge.monitor.loader import list_chain_ids, load_chain
from tone_forge.tone import guitar_catalog as gc


_CHAINS_ROOT: Path = (
    Path(__file__).resolve().parent.parent
    / "tone_forge"
    / "monitor"
    / "chains"
)


def _fingerprint_path(chain_id: str) -> Path:
    return _CHAINS_ROOT / f"{chain_id}.fingerprint.json"


def _yaml_path(chain_id: str) -> Path:
    return _CHAINS_ROOT / f"{chain_id}.yaml"


# ---------------------------------------------------------------------------
# Bundle parity
# ---------------------------------------------------------------------------


def test_every_yaml_has_a_matching_fingerprint() -> None:
    """A new chain ships incomplete unless both artifacts land."""
    missing = []
    for chain_id in list_chain_ids():
        if not _fingerprint_path(chain_id).is_file():
            missing.append(chain_id)
    assert not missing, (
        f"chains with YAML but no fingerprint JSON: {missing}"
    )


def test_every_fingerprint_has_a_matching_yaml() -> None:
    """Orphan fingerprints would route a query at the catalog
    distance gate that the policy layer doesn't know about — silent
    skew between the two layers."""
    yaml_ids = set(list_chain_ids())
    fp_paths = sorted(_CHAINS_ROOT.glob("*.fingerprint.json"))
    orphans = []
    for fp in fp_paths:
        chain_id = fp.name.removesuffix(".fingerprint.json")
        if chain_id not in yaml_ids:
            orphans.append(chain_id)
    assert not orphans, (
        f"fingerprint JSONs with no matching YAML: {orphans}"
    )


# ---------------------------------------------------------------------------
# Fingerprint schema (runtime-parseable through the same loader)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("chain_id", sorted(list_chain_ids()))
def test_fingerprint_parses_through_catalog_loader(chain_id: str) -> None:
    """If the catalog loader can't parse the JSON, the chain
    silently drops out of the catalog at runtime (the loader logs a
    warning and skips). That degrades retrieval to UNKNOWN-tier
    fallback without surfacing a hard failure — which is the wrong
    behavior for a bundled artifact."""
    entry = gc._load_entry(_fingerprint_path(chain_id))

    assert entry.chain_id == chain_id
    assert isinstance(entry.display_name, str) and entry.display_name
    assert isinstance(entry.family, MonitorChainFamily)
    assert entry.vector.shape == (len(gc._FEATURE_KEYS),)
    assert entry.validity.shape == (len(gc._FEATURE_KEYS),)


@pytest.mark.parametrize("chain_id", sorted(list_chain_ids()))
def test_fingerprint_features_dict_has_all_eight_keys(chain_id: str) -> None:
    """The catalog reads exactly the eight ``_FEATURE_KEYS``; a
    missing one coerces to NaN/0 silently. We want a hard CI failure
    instead — bundled fingerprints must declare all eight."""
    raw = json.loads(_fingerprint_path(chain_id).read_text(encoding="utf-8"))
    features = raw.get("features")
    assert isinstance(features, dict), (
        f"{chain_id}: features must be a dict, got {type(features).__name__}"
    )
    missing = [k for k in gc._FEATURE_KEYS if k not in features]
    assert not missing, (
        f"{chain_id}: missing feature keys {missing}; "
        f"got {sorted(features.keys())}"
    )
    bad_types = [
        k for k in gc._FEATURE_KEYS
        if not isinstance(features[k], (int, float))
    ]
    assert not bad_types, (
        f"{chain_id}: non-numeric feature values for keys {bad_types}"
    )


@pytest.mark.parametrize("chain_id", sorted(list_chain_ids()))
def test_fingerprint_validity_mask_well_formed_when_present(chain_id: str) -> None:
    """The validity mask is optional (catalog treats missing as
    all-True), but when present it must use the same eight keys and
    boolean values. A mask with the wrong key set would silently
    drop axes from the L2 distance."""
    raw = json.loads(_fingerprint_path(chain_id).read_text(encoding="utf-8"))
    validity = raw.get("feature_validity")
    if validity is None:
        # Optional field; nothing to check.
        return

    assert isinstance(validity, dict), (
        f"{chain_id}: feature_validity must be a dict when present, "
        f"got {type(validity).__name__}"
    )
    missing = [k for k in gc._FEATURE_KEYS if k not in validity]
    assert not missing, (
        f"{chain_id}: feature_validity missing keys {missing}"
    )
    bad_types = [
        k for k in gc._FEATURE_KEYS
        if not isinstance(validity[k], bool)
    ]
    assert not bad_types, (
        f"{chain_id}: non-boolean feature_validity values for keys {bad_types}"
    )


# ---------------------------------------------------------------------------
# YAML <-> JSON cross-check
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("chain_id", sorted(list_chain_ids()))
def test_yaml_and_fingerprint_agree_on_chain_id(chain_id: str) -> None:
    """The YAML loader already pins ``id == filename stem``; this
    test pins the same for the JSON, which is parsed by a different
    loader. Both have to agree with the filename or the policy
    layer and the catalog layer get out of sync."""
    raw = json.loads(_fingerprint_path(chain_id).read_text(encoding="utf-8"))
    assert raw.get("chain_id") == chain_id, (
        f"{chain_id}: fingerprint chain_id={raw.get('chain_id')!r} "
        f"does not match filename stem"
    )


@pytest.mark.parametrize("chain_id", sorted(list_chain_ids()))
def test_yaml_and_fingerprint_agree_on_family(chain_id: str) -> None:
    """If a YAML's family is bumped without re-rendering the
    fingerprint, the policy router and the catalog distance gate
    would disagree about what they're routing. This is the most
    consequential cross-check — a silent skew here is the bug class
    that the tone->monitor boundary fix (commit c6ff8d1) was added
    to prevent at the import boundary; this test prevents it at the
    data boundary."""
    yaml_chain = load_chain(chain_id)
    raw = json.loads(_fingerprint_path(chain_id).read_text(encoding="utf-8"))
    assert raw.get("family") == yaml_chain.family.value, (
        f"{chain_id}: YAML family={yaml_chain.family.value!r} "
        f"vs fingerprint family={raw.get('family')!r}"
    )


@pytest.mark.parametrize("chain_id", sorted(list_chain_ids()))
def test_yaml_and_fingerprint_agree_on_display_name(chain_id: str) -> None:
    """Less consequential than family, but a drift here would mean
    the user-facing label in the UI (catalog source) drifts from
    the curator's intent (YAML source). Pin them."""
    yaml_chain = load_chain(chain_id)
    raw = json.loads(_fingerprint_path(chain_id).read_text(encoding="utf-8"))
    assert raw.get("display_name") == yaml_chain.display_name, (
        f"{chain_id}: YAML display_name={yaml_chain.display_name!r} "
        f"vs fingerprint display_name={raw.get('display_name')!r}"
    )
