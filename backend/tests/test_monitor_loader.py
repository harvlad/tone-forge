"""Tests for the monitor chain YAML loader.

Covers two slices:

  1. Validation: every accept/reject branch of ``_parse_chain``.
  2. Bank coverage: the bundled ``monitor/chains/`` directory ships a
     valid YAML for every ``MonitorChainFamily``, and every chain id
     returned by ``tone.policy.FAMILY_TO_CHAIN_ID`` resolves. This
     pins the integration contract between the tone policy and the
     monitor bank so a future rename in either place fails CI.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tone_forge.contracts import MonitorChain, MonitorChainFamily
from tone_forge.monitor import (
    CHAIN_ID_NAMESPACE,
    ChainNotFoundError,
    ChainSpecError,
    list_chain_ids,
    load_all,
    load_chain,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _valid_yaml(*, chain_id: str = "tfc.test", family: str = "clean") -> str:
    """A minimal YAML string the loader accepts."""
    return f"""
id: {chain_id}
family: {family}
display_name: "Test Chain"
description: "A test fixture."
parameters:
  input:
    gain_db: 0
    high_pass_hz: 80
  gain_stage:
    type: tube_clean
    drive: 0.1
    bias: 0.5
  eq:
    bass_db: 0
    mid_db: 0
    treble_db: 0
    presence_db: 0
  comp:
    enabled: true
    ratio: 2.0
    threshold_db: -18
    attack_ms: 5
    release_ms: 80
  reverb:
    type: room
    size: 0.3
    mix: 0.15
  output:
    trim_db: 0
"""


@pytest.fixture
def tmp_chains(tmp_path: Path) -> Path:
    """Empty chains directory the test can write fixture YAMLs into."""
    root = tmp_path / "chains"
    root.mkdir()
    return root


def _write_chain(root: Path, chain_id: str, body: str) -> Path:
    path = root / f"{chain_id}.yaml"
    path.write_text(body, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_load_chain_returns_monitor_chain_dataclass(tmp_chains: Path) -> None:
    _write_chain(tmp_chains, "tfc.test", _valid_yaml())
    chain = load_chain("tfc.test", chains_root=tmp_chains)
    assert isinstance(chain, MonitorChain)
    assert chain.id == "tfc.test"
    assert chain.family == MonitorChainFamily.CLEAN
    assert chain.display_name == "Test Chain"
    assert "input" in chain.parameters
    assert "gain_stage" in chain.parameters


def test_load_chain_freezes_parameters_as_plain_dict(tmp_chains: Path) -> None:
    """``parameters`` is forwarded as-is to Connect; pin that it's a
    plain Python dict (not a yaml-specific mapping subclass)."""
    _write_chain(tmp_chains, "tfc.test", _valid_yaml())
    chain = load_chain("tfc.test", chains_root=tmp_chains)
    assert type(chain.parameters) is dict


def test_list_chain_ids_returns_sorted_ids(tmp_chains: Path) -> None:
    _write_chain(tmp_chains, "tfc.b", _valid_yaml(chain_id="tfc.b"))
    _write_chain(tmp_chains, "tfc.a", _valid_yaml(chain_id="tfc.a"))
    _write_chain(tmp_chains, "tfc.c", _valid_yaml(chain_id="tfc.c"))
    assert list_chain_ids(chains_root=tmp_chains) == ["tfc.a", "tfc.b", "tfc.c"]


def test_list_chain_ids_ignores_non_yaml(tmp_chains: Path) -> None:
    _write_chain(tmp_chains, "tfc.a", _valid_yaml(chain_id="tfc.a"))
    (tmp_chains / "README.md").write_text("not a chain", encoding="utf-8")
    (tmp_chains / "preview").mkdir()  # subdirectory ignored
    assert list_chain_ids(chains_root=tmp_chains) == ["tfc.a"]


def test_list_chain_ids_empty_root_returns_empty(tmp_path: Path) -> None:
    """Missing chains dir is treated as an empty bank, not an error.
    Tests that import this module shouldn't need a populated bank."""
    assert list_chain_ids(chains_root=tmp_path / "does-not-exist") == []


def test_load_all_keys_by_chain_id(tmp_chains: Path) -> None:
    _write_chain(tmp_chains, "tfc.a", _valid_yaml(chain_id="tfc.a"))
    _write_chain(tmp_chains, "tfc.b", _valid_yaml(chain_id="tfc.b", family="ambient"))
    bank = load_all(chains_root=tmp_chains)
    assert set(bank.keys()) == {"tfc.a", "tfc.b"}
    assert bank["tfc.b"].family == MonitorChainFamily.AMBIENT


# ---------------------------------------------------------------------------
# Reject path — every validation branch
# ---------------------------------------------------------------------------


def test_chain_not_found(tmp_chains: Path) -> None:
    with pytest.raises(ChainNotFoundError) as exc:
        load_chain("tfc.missing", chains_root=tmp_chains)
    assert "tfc.missing" in str(exc.value)


def test_rejects_invalid_yaml(tmp_chains: Path) -> None:
    _write_chain(tmp_chains, "tfc.bad", "id: tfc.bad\n  bad indent here:")
    with pytest.raises(ChainSpecError) as exc:
        load_chain("tfc.bad", chains_root=tmp_chains)
    assert "invalid YAML" in str(exc.value)


def test_rejects_non_mapping_top_level(tmp_chains: Path) -> None:
    _write_chain(tmp_chains, "tfc.bad", "- not a mapping\n- just a list\n")
    with pytest.raises(ChainSpecError) as exc:
        load_chain("tfc.bad", chains_root=tmp_chains)
    assert "mapping" in str(exc.value)


def test_rejects_missing_top_level_keys(tmp_chains: Path) -> None:
    _write_chain(tmp_chains, "tfc.bad", "id: tfc.bad\nfamily: clean\n")
    with pytest.raises(ChainSpecError) as exc:
        load_chain("tfc.bad", chains_root=tmp_chains)
    msg = str(exc.value)
    assert "display_name" in msg or "description" in msg


def test_rejects_id_outside_namespace(tmp_chains: Path) -> None:
    body = _valid_yaml(chain_id="other.test").replace(
        "id: other.test", "id: other.test"
    )
    (tmp_chains / "other.test.yaml").write_text(body, encoding="utf-8")
    with pytest.raises(ChainSpecError) as exc:
        load_chain("other.test", chains_root=tmp_chains)
    assert CHAIN_ID_NAMESPACE in str(exc.value)


def test_rejects_id_filename_mismatch(tmp_chains: Path) -> None:
    """File ``tfc.a.yaml`` containing ``id: tfc.b`` is a deploy-blocking
    bug: ``select_fallback_chain`` would resolve to "tfc.a" but the
    loader returns a chain identifying itself as "tfc.b"."""
    _write_chain(tmp_chains, "tfc.a", _valid_yaml(chain_id="tfc.b"))
    with pytest.raises(ChainSpecError) as exc:
        load_chain("tfc.a", chains_root=tmp_chains)
    assert "filename" in str(exc.value)


def test_rejects_unknown_family(tmp_chains: Path) -> None:
    body = _valid_yaml().replace("family: clean", "family: shoegaze")
    _write_chain(tmp_chains, "tfc.test", body)
    with pytest.raises(ChainSpecError) as exc:
        load_chain("tfc.test", chains_root=tmp_chains)
    assert "shoegaze" in str(exc.value)


def test_rejects_empty_display_name(tmp_chains: Path) -> None:
    body = _valid_yaml().replace('display_name: "Test Chain"', 'display_name: ""')
    _write_chain(tmp_chains, "tfc.test", body)
    with pytest.raises(ChainSpecError) as exc:
        load_chain("tfc.test", chains_root=tmp_chains)
    assert "display_name" in str(exc.value)


def test_rejects_non_mapping_parameters(tmp_chains: Path) -> None:
    body = (
        "id: tfc.test\n"
        "family: clean\n"
        'display_name: "Test"\n'
        'description: ""\n'
        "parameters: not_a_mapping\n"
    )
    _write_chain(tmp_chains, "tfc.test", body)
    with pytest.raises(ChainSpecError) as exc:
        load_chain("tfc.test", chains_root=tmp_chains)
    assert "parameters" in str(exc.value)


def test_rejects_missing_parameter_section(tmp_chains: Path) -> None:
    """Drop the ``eq`` section — loader must catch it so Connect never
    sees a chain it can't construct."""
    body = _valid_yaml()
    # Strip the eq block by replacing it with nothing.
    body = body.replace(
        "  eq:\n    bass_db: 0\n    mid_db: 0\n    treble_db: 0\n    presence_db: 0\n",
        "",
    )
    _write_chain(tmp_chains, "tfc.test", body)
    with pytest.raises(ChainSpecError) as exc:
        load_chain("tfc.test", chains_root=tmp_chains)
    assert "eq" in str(exc.value)


# ---------------------------------------------------------------------------
# Bundled bank — covers every MonitorChainFamily
# ---------------------------------------------------------------------------


def test_bundled_bank_loads_clean() -> None:
    """The default ``monitor/chains/`` directory must load without any
    errors. CI gate: a malformed placeholder breaks the build."""
    bank = load_all()
    assert bank, "Bundled chain bank is empty."


def test_bundled_bank_covers_every_family() -> None:
    """Every ``MonitorChainFamily`` must have at least one chain in the
    bundled bank. Pinned so a new family added to the enum breaks
    here until someone ships the corresponding YAML."""
    bank = load_all()
    families_present = {chain.family for chain in bank.values()}
    missing = set(MonitorChainFamily) - families_present
    assert not missing, f"Bundled bank missing families: {sorted(f.value for f in missing)}"


def test_bundled_bank_chain_ids_match_policy_namespace() -> None:
    """Every chain id in the bundled bank lives in the ``tfc.`` namespace.
    The tone.policy module pins its own ``CHAIN_ID_*`` constants under
    the same namespace; this test catches drift between them."""
    for chain_id in list_chain_ids():
        assert chain_id.startswith(CHAIN_ID_NAMESPACE)


def test_bundled_bank_resolves_every_policy_chain_id() -> None:
    """Cross-subsystem integration pin: every chain id ``tone.policy``
    can return must be loadable from the monitor bank. If they ever
    drift, retrieval LOW/UNKNOWN paths would return chain ids the
    Connect-side loader can't resolve."""
    from tone_forge.tone import policy

    for chain_id in policy.FAMILY_TO_CHAIN_ID.values():
        chain = load_chain(chain_id)
        assert chain.id == chain_id


def test_bundled_chains_have_unique_families() -> None:
    """MVP ships one chain per family. Pinned so a future Phase-2
    expansion (multiple chains per family) is a deliberate test
    update, not an accidental dup."""
    bank = load_all()
    families = [chain.family for chain in bank.values()]
    assert len(families) == len(set(families)), (
        f"Duplicate family in bundled bank: {families}"
    )
