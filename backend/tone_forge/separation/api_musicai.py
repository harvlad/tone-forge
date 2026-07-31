"""api:musicai — remote SeparatorProvider (Music.AI / Moises, STUB).

Identical SeparatorProvider contract. Music.AI exposes guitar stems (acoustic/electric)
and parts (rhythm/solo) at a PUBLISHED rate (~$0.10/min guitar, $0.095 Pro). Different
architecture (proprietary) → decorrelated from Demucs by construction; PERCEPTUAL
complementarity is decided by blind A/B (Milestone 3), not assumed.

STATUS: STUB. Music.AI's API is workflow-based: create a job referencing a user-defined
"stem separation" workflow + an input, poll, collect output URLs. Exact endpoint paths /
auth header / workflow reference live in their account-gated docs and are marked
`# VERIFY`. Until credentials exist:
  - health() is False without an API key (never selected in production).
  - separate() raises without a key.
Set `mock=True` to exercise the contract offline.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from .provider import (
    LICENSE_API_TERMS,
    ProviderCapabilities,
    SeparationRequest,
    SeparationResult,
    SeparatorProvider,
)

try:
    import requests  # noqa: F401
    _HAS_REQUESTS = True
except Exception:
    _HAS_REQUESTS = False

_BASE_URL = os.environ.get("MUSICAI_BASE_URL", "https://api.music.ai")  # VERIFY
# The workflow id/slug of a "stem separation (guitar)" workflow the user builds in the
# Music.AI dashboard. VERIFY / configure per account.
_WORKFLOW = os.environ.get("MUSICAI_WORKFLOW", "riley-guitar-stems")
_POLL_INTERVAL_S = 3.0
_POLL_TIMEOUT_S = 600.0
_USD_PER_MINUTE_GUITAR = 0.10   # published pay-as-you-go rate (0.095 Professional)
_STEMS = frozenset({"vocals", "drums", "bass", "guitar", "piano", "other"})


class MusicAIProvider:
    id = "api:musicai"

    def __init__(self, api_key: str | None = None, out_root: Path = Path("/tmp/riley_sep_api"),
                 mock: bool = False):
        self._key = api_key or os.environ.get("MUSICAI_API_KEY")
        self._out = Path(out_root)
        self._mock = mock

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            stems=_STEMS,
            architecture="api",
            license_status=LICENSE_API_TERMS,
            decorrelated_from=frozenset({"local:htdemucs_6s"}),
            regimes_strong=frozenset(),                 # UNPROVEN until blind-A/B
            regimes_weak=frozenset(),
            cost_per_track_usd=None,                     # priced PER MINUTE (see meta below)
            max_track_seconds=None,
            confidence="unproven",
        )

    def health(self) -> bool:
        if self._mock:
            return True
        return bool(self._key) and _HAS_REQUESTS

    def separate(self, req: SeparationRequest) -> SeparationResult:
        t0 = time.time()
        self._out.mkdir(parents=True, exist_ok=True)
        if self._mock:
            return self._mock_result(req, t0)
        if not self._key:
            raise RuntimeError(f"{self.id}: MUSICAI_API_KEY required (unconfigured provider "
                               "must never be selected in production)")
        if not _HAS_REQUESTS:
            raise RuntimeError(f"{self.id}: `requests` not installed")
        return self._api_flow(req, t0)

    # ---- real workflow-based flow (endpoints marked VERIFY) ----
    def _api_flow(self, req: SeparationRequest, t0: float) -> SeparationResult:
        import requests
        h = {"Authorization": self._key}                 # VERIFY: Music.AI uses raw key header
        # 1) make the input reachable. Music.AI fetches from a URL; upload or presign first.
        input_url = self._ensure_input_url(req.audio_path)   # VERIFY upload/presign flow
        # 2) create a job against the stem-separation workflow
        job = requests.post(f"{_BASE_URL}/api/job",       # VERIFY endpoint
                            headers=h, json={"name": f"riley-{req.config_hash or 'job'}",
                                             "workflow": _WORKFLOW,
                                             "params": {"inputUrl": input_url}}, timeout=60)
        job.raise_for_status()
        job_id = job.json()["id"]                          # VERIFY field
        # 3) poll
        deadline = time.time() + _POLL_TIMEOUT_S
        data = {}
        while time.time() < deadline:
            st = requests.get(f"{_BASE_URL}/api/job/{job_id}", headers=h, timeout=30)  # VERIFY
            st.raise_for_status(); data = st.json()
            status = data.get("status")
            if status == "SUCCEEDED":                      # VERIFY status vocab
                break
            if status in ("FAILED", "CANCELLED"):
                raise RuntimeError(f"{self.id}: job {status}: {data}")
            time.sleep(_POLL_INTERVAL_S)
        else:
            raise TimeoutError(f"{self.id}: job {job_id} timed out")
        # 4) collect requested stems from the workflow result
        result = data.get("result", {})                    # VERIFY output schema (name->url map)
        stems: dict[str, Path] = {}
        for name in req.stems:
            url = result.get(name) or result.get(f"{name}.wav")
            if url:
                dst = self._out / f"{job_id}_{name}.wav"
                r = requests.get(url, timeout=120); r.raise_for_status()
                dst.write_bytes(r.content); stems[name] = dst
        return SeparationResult(
            stems=stems, provider_id=self.id, model_id=f"musicai:{_WORKFLOW}",
            latency_ms=int((time.time() - t0) * 1000),
            meta={"job_id": job_id, "remote": True, "usd_per_minute": _USD_PER_MINUTE_GUITAR})

    def _ensure_input_url(self, audio_path: Path) -> str:
        # VERIFY: Music.AI provides an upload/presign endpoint; returns a fetchable URL.
        raise NotImplementedError(f"{self.id}: upload/presign flow unverified — wire with creds")

    def _mock_result(self, req: SeparationRequest, t0: float) -> SeparationResult:
        stems = {}
        for name in req.stems:
            dst = self._out / f"MOCK_musicai_{name}.wav"; dst.write_bytes(b""); stems[name] = dst
        return SeparationResult(
            stems=stems, provider_id=self.id, model_id="musicai:MOCK",
            latency_ms=int((time.time() - t0) * 1000),
            meta={"mock": True, "usd_per_minute": _USD_PER_MINUTE_GUITAR,
                  "note": "endpoints unverified; wire creds + confirm workflow schema"})


_p: SeparatorProvider = MusicAIProvider(mock=True)  # type: ignore[assignment]
