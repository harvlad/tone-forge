"""api:audioshake — remote SeparatorProvider (Milestone 2, STUB).

Implements the IDENTICAL SeparatorProvider contract as local:htdemucs_6s. AudioShake
returns a guitar stem under commercial API terms (see acquisition survey in
RILEY_SEPARATOR_REGISTRY.md). Different architecture (proprietary) → decorrelated
from Demucs by construction; whether that decorrelation is PERCEPTUALLY complementary
is decided by the blind-A/B harness (Milestone 3), not assumed here.

STATUS: STUB. The canonical AudioShake flow is upload asset → create stem job →
poll → download. The exact endpoint paths / field names / auth header live behind
AudioShake's account-gated docs (developer.audioshake.ai) and are marked
`# VERIFY:` — fill them in once a Client ID/Secret exists. Until then:
  - health() is False without credentials (never selected in production).
  - separate() raises a clear error without credentials, so nothing above the
    interface can silently depend on an unconfigured provider.
Set `mock=True` to exercise the contract offline (returns a synthetic result path)
without hitting the network — used by the contract test.
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

# VERIFY all of these against developer.audioshake.ai once an account exists.
_BASE_URL = os.environ.get("AUDIOSHAKE_BASE_URL", "https://groovy.audioshake.ai")  # VERIFY
_POLL_INTERVAL_S = 3.0
_POLL_TIMEOUT_S = 600.0
# AudioShake's advertised instrument stems include guitar.
_STEMS = frozenset({"vocals", "drums", "bass", "guitar", "piano", "other"})


class AudioShakeProvider:
    id = "api:audioshake"

    def __init__(self, api_key: str | None = None, out_root: Path = Path("/tmp/riley_sep_api"),
                 mock: bool = False):
        self._key = api_key or os.environ.get("AUDIOSHAKE_API_KEY")
        self._out = Path(out_root)
        self._mock = mock

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            stems=_STEMS,
            architecture="api",
            license_status=LICENSE_API_TERMS,           # usable under AudioShake commercial terms
            decorrelated_from=frozenset({"local:htdemucs_6s"}),  # different arch; PERCEPTUAL test = Milestone 3
            regimes_strong=frozenset(),                 # UNPROVEN until blind-A/B (Milestone 3)
            regimes_weak=frozenset(),
            cost_per_track_usd=None,                     # enterprise/custom — set when known
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
            raise RuntimeError(f"{self.id}: AUDIOSHAKE_API_KEY required (unconfigured provider "
                               "must never be selected in production)")
        if not _HAS_REQUESTS:
            raise RuntimeError(f"{self.id}: `requests` not installed")
        return self._api_flow(req, t0)

    # ---- real API flow (endpoints marked VERIFY; wire when creds land) ----
    def _api_flow(self, req: SeparationRequest, t0: float) -> SeparationResult:
        import requests
        h = {"Authorization": f"Bearer {self._key}"}   # VERIFY auth scheme
        # 1) upload the mixture as an asset
        with open(req.audio_path, "rb") as f:
            up = requests.post(f"{_BASE_URL}/upload",   # VERIFY endpoint
                               headers=h, files={"file": f}, timeout=120)
        up.raise_for_status()
        asset_id = up.json()["id"]                       # VERIFY field
        # 2) create a stem-separation job for the requested stems
        want = list(req.stems)
        job = requests.post(f"{_BASE_URL}/job",          # VERIFY endpoint + payload schema
                            headers=h, json={"assetId": asset_id, "stems": want}, timeout=60)
        job.raise_for_status()
        job_id = job.json()["id"]                        # VERIFY field
        # 3) poll until done
        deadline = time.time() + _POLL_TIMEOUT_S
        while time.time() < deadline:
            st = requests.get(f"{_BASE_URL}/job/{job_id}", headers=h, timeout=30)  # VERIFY
            st.raise_for_status()
            data = st.json()
            if data.get("status") == "completed":        # VERIFY status vocab
                break
            if data.get("status") == "failed":
                raise RuntimeError(f"{self.id}: job failed: {data}")
            time.sleep(_POLL_INTERVAL_S)
        else:
            raise TimeoutError(f"{self.id}: job {job_id} timed out")
        # 4) download the requested stems
        stems: dict[str, Path] = {}
        for out in data.get("outputs", []):              # VERIFY output schema
            name = out.get("stem")
            if name in req.stems:
                dst = self._out / f"{job_id}_{name}.wav"
                r = requests.get(out["url"], timeout=120); r.raise_for_status()
                dst.write_bytes(r.content)
                stems[name] = dst
        return SeparationResult(
            stems=stems, provider_id=self.id, model_id="audioshake:stems",
            latency_ms=int((time.time() - t0) * 1000),
            meta={"asset_id": asset_id, "job_id": job_id, "remote": True})

    def _mock_result(self, req: SeparationRequest, t0: float) -> SeparationResult:
        # Offline contract exercise: emit an empty placeholder per requested stem.
        stems = {}
        for name in req.stems:
            dst = self._out / f"MOCK_{name}.wav"; dst.write_bytes(b"")
            stems[name] = dst
        return SeparationResult(
            stems=stems, provider_id=self.id, model_id="audioshake:MOCK",
            latency_ms=int((time.time() - t0) * 1000),
            meta={"mock": True, "note": "endpoints unverified; wire creds + confirm schema"})


# static contract check — must satisfy the identical interface
_p: SeparatorProvider = AudioShakeProvider(mock=True)  # type: ignore[assignment]
