# Separator Provider Interface

Formalizes the abstraction the whole separator-architecture research pointed to. Riley depends
on a **SeparatorProvider**, never on a specific model/implementation. Everything above this
interface (routing, fusion harness, perceptual repair, Riley transcription, blind-A/B tooling,
Derived Audio) is unchanged when the provider changes. Swapping a local model for a licensed
API for a future in-house model is a config change, not a code change.

Design authority: the agile-sprint findings (`RILEY_SEPARATOR_REGISTRY.md`,
`riley-multimodel-findings` memory). Blind listening remains the promotion gate for any provider.

---

## 1. The contract

```python
# backend/tone_forge/separation/provider.py  (proposed)
from dataclasses import dataclass
from typing import Protocol, Mapping
from pathlib import Path

@dataclass(frozen=True)
class SeparationRequest:
    audio_path: Path                 # input mixture (wav/flac/m4a)
    stems: tuple[str, ...] = ("guitar",)   # requested stems; "guitar" is Riley's primary
    sample_rate: int = 44100
    config_hash: str | None = None   # for cache keying / reproducibility

@dataclass(frozen=True)
class SeparationResult:
    stems: Mapping[str, Path]        # stem_name -> output wav path (44.1k stereo)
    provider_id: str                 # which provider produced this (audit/provenance)
    model_id: str                    # concrete model + version
    latency_ms: int
    meta: Mapping[str, object]       # per-provider extras (holes%, confidence, cost, etc.)

class SeparatorProvider(Protocol):
    id: str                          # stable provider id, e.g. "local:htdemucs_6s"
    def capabilities(self) -> "ProviderCapabilities": ...
    def separate(self, req: SeparationRequest) -> SeparationResult: ...
    def health(self) -> bool: ...    # is this provider currently usable?

@dataclass(frozen=True)
class ProviderCapabilities:
    stems: frozenset[str]            # stems it can output (e.g. {guitar,vocals,drums,bass,other,piano})
    architecture: str                # "demucs" | "roformer" | "mdx" | "api" | ...
    license_status: str              # "clean" | "blocked" | "api_terms" | "unknown"
    decorrelated_from: frozenset[str]  # provider ids it is known to make DIFFERENT mistakes from
    regimes_strong: frozenset[str]   # blind-A/B-proven niches (e.g. {distorted_vocal_masked})
    regimes_weak: frozenset[str]     # known failure regimes (e.g. {acoustic})
    cost_per_track_usd: float | None # ~0 for local, >0 for API
    max_track_seconds: int | None
```

Riley's separation layer selects a provider (or set of providers) per request and never sees
model internals.

## 2. Provider types (all implement the same interface)
| Provider | id example | Arch | License | Notes |
|---|---|---|---|---|
| **Local model** | `local:htdemucs_6s` | demucs | clean | DEFAULT. Bundled Meta MIT weights. |
| **Local model (decorrelated)** | `local:roformer_clean` | roformer | (target) clean | The missing piece — a production-safe RoFormer once acquired. |
| **Remote API** | `api:audioshake` / `api:moises` | api | provider terms | Returns guitar stem over HTTP; interchangeable with local. Cost/track + latency in `meta`. |
| **Future licensed model** | `local:sw_licensed` | roformer | licensed | SW-class weights IF licensing succeeds. |
| **Future in-house** | `local:riley_sep_v1` | roformer/other | clean/in-house | Only if all acquisition paths fail (last resort). |

Research-only providers (e.g. `research:bs_roformer_sw`) may exist behind a flag for evaluation
but MUST be `license_status="blocked"` and never selected in production paths.

## 3. Selection policy (above the interface — unchanged by provider swaps)
- **Default:** the clean local provider (`local:htdemucs_6s`).
- **Routing (validated harness):** where a request's regime matches a provider's proven strong
  niche AND that provider is decorrelated from the default, route/fuse. Regime detected by the
  validated win-predictor features (distortion + dynamics + vocal/guitar ratio). NOTE: routing
  only pays off with a *decorrelated* provider — correlated ones (B1) are excluded by policy
  (`decorrelated_from` must include the default).
- **Promotion gate:** a provider enters the selectable portfolio ONLY after blind-A/B wins in a
  regime (registry-recorded). Metrics are descriptive; listening decides.
- **Fallback:** if a provider `health()` is false or over budget, fall back to the default.

## 4. What this unlocks
- **Acquisition-agnostic:** whichever acquisition path lands (license SW/becruily, an API, a new
  clean RoFormer, or in-house), it plugs in as a provider with zero changes above the interface.
- **API-as-provider:** AudioShake/Moises/etc. are just remote providers; Riley can A/B a local
  vs API guitar stem with the same blind harness and pick per-regime on quality × cost.
- **Cost/latency aware:** `cost_per_track_usd` + `latency_ms` let the selector trade quality vs
  spend (e.g. free local default, paid API only for regimes where it demonstrably wins).

## 5. Migration (incremental, non-breaking)
1. Wrap the CURRENT stock separation behind `local:htdemucs_6s` implementing `SeparatorProvider`
   — pure refactor, byte-identical output, no behaviour change. (Aligns with the existing
   `backend/tone_forge/specialist/` engine-selection seam.)
2. Add `AudioSeparatorProvider` (the python-audio-separator wrapper) as `research:*` for
   evaluation only.
3. When an acquisition path lands, register it as a production provider; enable in the selector
   behind the blind-A/B promotion gate.
- No step changes anything above the interface. Rollback = deselect the provider.

## 6. Non-goals
- Not a new separator. Not more B1/fusion/DSP work (research archived).
- Does not decide WHICH model to acquire — that's the acquisition survey. This is the socket the
  acquired model plugs into.
