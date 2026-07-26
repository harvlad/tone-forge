"""Specialist registry loader + license guard.

The registry (specialist_registry.json, checked into git next to this
module) is the ONLY source of truth for which specialist implementations
the runtime may execute. Entries whose license_status is not explicitly
cleared are refused at resolve time — technical availability never
implies legal permission.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

REGISTRY_PATH = Path(__file__).parent / "specialist_registry.json"

CLEARED_STATUSES = {"cleared_internal", "cleared_production"}

ENGINE_CURRENT = "current"
ENGINE_EXPERIMENTAL = "experimental_specialist"
VALID_ENGINES = {ENGINE_CURRENT, ENGINE_EXPERIMENTAL}


class LicenseBlockedError(RuntimeError):
    """Raised when routing would select a license-blocked component."""


@lru_cache(maxsize=1)
def load_registry() -> dict:
    with open(REGISTRY_PATH) as f:
        return json.load(f)


def registry_version() -> str:
    return load_registry()["registry_version"]


def get_transcriber(name: str) -> dict:
    entry = load_registry()["transcribers"].get(name)
    if entry is None:
        raise KeyError(f"unknown transcriber '{name}'")
    status = entry.get("license_status", "")
    if status not in CLEARED_STATUSES:
        raise LicenseBlockedError(
            f"transcriber '{name}' is not license-cleared for runtime "
            f"(status={status!r}). Registry promotion + legal review required.")
    return entry


def get_separator(name: str) -> dict:
    entry = load_registry()["separators"].get(name)
    if entry is None:
        raise KeyError(f"unknown separator '{name}'")
    status = entry.get("license_status", "")
    if status not in CLEARED_STATUSES:
        raise LicenseBlockedError(
            f"separator '{name}' is not license-cleared for runtime "
            f"(status={status!r}).")
    return entry


def get_normalization(name: str) -> dict:
    entry = load_registry()["normalizations"].get(name)
    if entry is None:
        raise KeyError(f"unknown normalization '{name}'")
    return entry
