"""SeparatorProvider abstraction (see docs/SEPARATOR_PROVIDER_INTERFACE.md).

Riley depends on this contract, never on a concrete separator. Local models, remote
APIs, licensed models, and future in-house models all implement `SeparatorProvider`.
Everything above this interface (routing, Riley transcription, Derived Audio,
blind-A/B tooling) is unchanged when the provider changes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Protocol, runtime_checkable

# License status for PRODUCTION use.
LICENSE_CLEAN = "clean"          # permissive code + weights + defensible data provenance
LICENSE_API_TERMS = "api_terms"  # usable under a provider's commercial API terms
LICENSE_BLOCKED = "blocked"      # research-only; never production-selectable
LICENSE_UNKNOWN = "unknown"


@dataclass(frozen=True)
class SeparationRequest:
    audio_path: Path
    stems: tuple[str, ...] = ("guitar",)      # Riley's primary is "guitar"
    sample_rate: int = 44100
    config_hash: str | None = None            # cache key / reproducibility


@dataclass(frozen=True)
class SeparationResult:
    stems: Mapping[str, Path]                 # stem_name -> output wav (44.1k stereo)
    provider_id: str
    model_id: str
    latency_ms: int
    meta: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderCapabilities:
    stems: frozenset[str]
    architecture: str                         # "demucs" | "roformer" | "mdx" | "api" | ...
    license_status: str                       # one of LICENSE_*
    decorrelated_from: frozenset[str] = frozenset()   # provider ids it makes DIFFERENT mistakes from
    regimes_strong: frozenset[str] = frozenset()      # blind-A/B-proven niches
    regimes_weak: frozenset[str] = frozenset()
    cost_per_track_usd: float | None = None   # ~0 local, >0 API
    max_track_seconds: int | None = None
    confidence: str = "unproven"              # "proven" once blind-A/B evidence exists


@runtime_checkable
class SeparatorProvider(Protocol):
    id: str

    def capabilities(self) -> ProviderCapabilities: ...
    def separate(self, req: SeparationRequest) -> SeparationResult: ...
    def health(self) -> bool: ...
