"""Chain YAML loader.

Reads chain specs from ``backend/tone_forge/monitor/chains/<id>.yaml``
and projects them onto the ``MonitorChain`` contract type. The loader
is deliberately strict: a malformed chain is a deploy-blocking bug
(Connect will fail to construct the AVAudioEngine graph from bad
parameters), so we raise on every validation gap rather than guess.

Design notes:

* The loader returns the *parsed spec* — it does not interpret
  ``parameters`` semantically. Connect (Swift) owns the parameter
  schema and any nudging that happens before AVAudioEngine sees the
  values. This module's job is "is the file structurally valid and
  identifiable by id and family?", nothing more.
* Chain ids must follow the ``tfc.<family>`` namespace pinned in
  ``tone_forge.tone.policy``. We don't import policy (boundary
  discipline forbids monitor → tone), but the validator checks the
  shape independently.
* Path resolution defaults to the bundled ``chains/`` directory next
  to this file. Callers (tests, downloadable banks in Phase 2) can
  override via the optional ``chains_root`` argument.

Boundary discipline:

* This module imports only ``tone_forge.contracts`` and stdlib + PyYAML.
* The boundary test (``tests/test_subsystem_boundaries.py``) enforces it.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

import yaml

from tone_forge.contracts import MonitorChain, MonitorChainFamily

# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

# Directory that ships with the package. Resolved at import time so
# every helper sees the same root.
_DEFAULT_CHAINS_ROOT: Path = Path(__file__).resolve().parent / "chains"

# Chain ids live under this namespace. Pinned independently of
# tone.policy to keep the boundary clean — see module docstring.
CHAIN_ID_NAMESPACE: str = "tfc."

# Required top-level keys on every chain YAML.
_REQUIRED_KEYS: frozenset[str] = frozenset({
    "id", "family", "display_name", "description", "parameters",
})

# Required nested sections inside ``parameters``. Pinned at the names
# Connect expects so a renamed section here is the same change there.
_REQUIRED_PARAM_SECTIONS: tuple[str, ...] = (
    "input", "gain_stage", "eq", "comp", "reverb", "output",
)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class ChainNotFoundError(LookupError):
    """The requested chain id has no file in the bank."""


class ChainSpecError(ValueError):
    """A chain file is structurally invalid (missing keys, wrong types,
    or out-of-namespace id). Always includes the file path in the
    message so a CI failure is actionable."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_chain(
    chain_id: str,
    *,
    chains_root: Optional[Path] = None,
) -> MonitorChain:
    """Load and validate a single chain spec by id.

    ``chain_id`` is the canonical ``tfc.<family>`` string. The loader
    expects a file named ``<chain_id>.yaml`` in the bank.

    Raises
    ------
    ChainNotFoundError
        No file exists for ``chain_id`` in the bank.
    ChainSpecError
        The file exists but failed validation (missing required keys,
        out-of-namespace id, family not in the enum, ...).
    """
    root = chains_root or _DEFAULT_CHAINS_ROOT
    path = root / f"{chain_id}.yaml"
    if not path.is_file():
        raise ChainNotFoundError(
            f"No chain file at {path} (chain_id={chain_id!r}). "
            f"Available ids: {list_chain_ids(chains_root=root)}"
        )
    raw = _read_yaml(path)
    return _parse_chain(raw, source_path=path)


def list_chain_ids(
    *,
    chains_root: Optional[Path] = None,
) -> List[str]:
    """Enumerate chain ids visible in the bank.

    Scans the chains directory for ``*.yaml`` files. Does *not* parse
    them — use ``load_all()`` if you need validated chains. Returned
    list is sorted for deterministic output (tests, telemetry).
    """
    root = chains_root or _DEFAULT_CHAINS_ROOT
    if not root.is_dir():
        return []
    ids: List[str] = []
    for entry in os.scandir(root):
        if not entry.is_file():
            continue
        name = entry.name
        if name.endswith(".yaml"):
            ids.append(name[:-5])  # strip .yaml
        elif name.endswith(".yml"):
            ids.append(name[:-4])
    ids.sort()
    return ids


def load_all(
    *,
    chains_root: Optional[Path] = None,
) -> Dict[str, MonitorChain]:
    """Load every chain in the bank, keyed by chain id.

    Raises ``ChainSpecError`` on the first malformed file — partial
    banks are not allowed. Callers that want to surface a partial
    bank in CI (e.g. a chain authoring branch) should iterate
    ``list_chain_ids()`` and catch per-chain.
    """
    root = chains_root or _DEFAULT_CHAINS_ROOT
    return {
        chain_id: load_chain(chain_id, chains_root=root)
        for chain_id in list_chain_ids(chains_root=root)
    }


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _read_yaml(path: Path) -> Any:
    """Parse YAML, mapping every failure mode to ``ChainSpecError``."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ChainSpecError(f"{path}: failed to read file: {exc}") from exc
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ChainSpecError(f"{path}: invalid YAML: {exc}") from exc


def _parse_chain(raw: Any, *, source_path: Path) -> MonitorChain:
    """Validate the parsed YAML and project onto ``MonitorChain``."""
    if not isinstance(raw, Mapping):
        raise ChainSpecError(
            f"{source_path}: top-level YAML must be a mapping, got "
            f"{type(raw).__name__}."
        )

    missing = _REQUIRED_KEYS.difference(raw.keys())
    if missing:
        raise ChainSpecError(
            f"{source_path}: missing required top-level keys: "
            f"{sorted(missing)}"
        )

    chain_id = raw["id"]
    if not isinstance(chain_id, str) or not chain_id:
        raise ChainSpecError(
            f"{source_path}: 'id' must be a non-empty string."
        )
    if not chain_id.startswith(CHAIN_ID_NAMESPACE):
        raise ChainSpecError(
            f"{source_path}: id {chain_id!r} must start with "
            f"{CHAIN_ID_NAMESPACE!r}."
        )

    expected_id = source_path.stem
    if chain_id != expected_id:
        raise ChainSpecError(
            f"{source_path}: id {chain_id!r} does not match filename "
            f"{expected_id!r}. Rename one to make them match."
        )

    family = _coerce_family(raw["family"], source_path=source_path)

    display_name = raw["display_name"]
    if not isinstance(display_name, str) or not display_name:
        raise ChainSpecError(
            f"{source_path}: 'display_name' must be a non-empty string."
        )

    description = raw["description"]
    if not isinstance(description, str):
        raise ChainSpecError(
            f"{source_path}: 'description' must be a string."
        )

    parameters = raw["parameters"]
    if not isinstance(parameters, Mapping):
        raise ChainSpecError(
            f"{source_path}: 'parameters' must be a mapping, got "
            f"{type(parameters).__name__}."
        )

    missing_sections = [
        section for section in _REQUIRED_PARAM_SECTIONS
        if section not in parameters
    ]
    if missing_sections:
        raise ChainSpecError(
            f"{source_path}: parameters missing required sections: "
            f"{missing_sections}"
        )

    for section in _REQUIRED_PARAM_SECTIONS:
        if not isinstance(parameters[section], Mapping):
            raise ChainSpecError(
                f"{source_path}: parameters.{section} must be a "
                f"mapping, got {type(parameters[section]).__name__}."
            )

    return MonitorChain(
        id=chain_id,
        family=family,
        display_name=display_name,
        description=description,
        parameters=dict(parameters),
    )


def _coerce_family(value: Any, *, source_path: Path) -> MonitorChainFamily:
    if not isinstance(value, str) or not value:
        raise ChainSpecError(
            f"{source_path}: 'family' must be a non-empty string."
        )
    try:
        return MonitorChainFamily(value)
    except ValueError as exc:
        valid = [f.value for f in MonitorChainFamily]
        raise ChainSpecError(
            f"{source_path}: family {value!r} is not one of {valid}."
        ) from exc


__all__ = [
    "CHAIN_ID_NAMESPACE",
    "ChainNotFoundError",
    "ChainSpecError",
    "list_chain_ids",
    "load_all",
    "load_chain",
]
