"""Specialist router — maps (engine, family) to a routing decision.

The planner/worker asks for a musical capability for a family; this
module answers with implementation bindings. No model names leak past
the decision object's provenance fields.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Optional

from . import registry as reg

# Product families the experimental engine can route today.  Everything
# else falls back to the current pipeline (Wave-3 evidence only covers
# these; do not add speculative routes).
ROUTABLE_FAMILIES = ("bass", "guitar", "keys")

# Map htdemucs_6s stem roles onto product families.
FAMILY_TO_STEM_ROLE = {"bass": "bass", "guitar": "guitar", "keys": "piano"}


@dataclass(frozen=True)
class RoutingRequest:
    engine: str
    family: str
    capability: str = "TARGET_NOTES"
    subfamily: Optional[str] = None


@dataclass(frozen=True)
class RoutingDecision:
    engine: str
    family: str
    stem_role: str
    separator: str
    separator_version: str
    transcriber: str
    transcriber_version: str
    transcriber_impl: str
    transcriber_config: dict
    normalization: str
    normalization_version: str
    normalization_shift: int
    registry_version: str
    caveat: Optional[str] = None
    lab_evidence: dict = field(default_factory=dict)

    def config_hash(self) -> str:
        payload = {
            "separator": self.separator, "separator_version": self.separator_version,
            "transcriber": self.transcriber, "transcriber_version": self.transcriber_version,
            "transcriber_config": self.transcriber_config,
            "normalization": self.normalization,
            "normalization_version": self.normalization_version,
            "normalization_shift": self.normalization_shift,
            "registry_version": self.registry_version,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]

    def provenance(self) -> dict:
        return {
            "engine": self.engine,
            "family": self.family,
            "stem_role": self.stem_role,
            "separator": self.separator,
            "separator_version": self.separator_version,
            "transcriber": self.transcriber,
            "transcriber_version": self.transcriber_version,
            "transcriber_config": self.transcriber_config,
            "normalization": self.normalization,
            "normalization_version": self.normalization_version,
            "normalization_shift_semitones": self.normalization_shift,
            "registry_version": self.registry_version,
            "config_hash": self.config_hash(),
            "caveat": self.caveat,
            "lab_evidence": self.lab_evidence,
        }


def resolve(request: RoutingRequest) -> Optional[RoutingDecision]:
    """Resolve a routing request.  Returns None when the current
    (non-specialist) pipeline should handle it — including every family
    the experimental engine has no validated route for."""
    if request.engine != reg.ENGINE_EXPERIMENTAL:
        return None
    routing = reg.load_registry()["routing"].get(reg.ENGINE_EXPERIMENTAL, {})
    route = routing.get(request.family)
    if route is None or "transcriber" not in route:
        return None  # explicit fallback to current behavior

    sep = reg.get_separator(route["separator"])          # raises if blocked
    trans = reg.get_transcriber(route["transcriber"])    # raises if blocked
    norm = reg.get_normalization(route["normalization"])

    return RoutingDecision(
        engine=request.engine,
        family=request.family,
        stem_role=FAMILY_TO_STEM_ROLE[request.family],
        separator=route["separator"],
        separator_version=sep.get("version", "unknown"),
        transcriber=route["transcriber"],
        transcriber_version=trans["model_version"],
        transcriber_impl=trans["impl"],
        transcriber_config=trans.get("impl_config", {}),
        normalization=route["normalization"],
        normalization_version=norm["version"],
        normalization_shift=int(norm.get("shift_semitones", 0)),
        registry_version=reg.registry_version(),
        caveat=route.get("caveat"),
        lab_evidence=trans.get("lab_evidence", {}),
    )
