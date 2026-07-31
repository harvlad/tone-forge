"""api:audioshake — remote SeparatorProvider (Milestone 2/3, WIRED).

Implements the IDENTICAL SeparatorProvider contract as local:htdemucs_6s. AudioShake
returns a dedicated guitar stem (models: guitar / guitar_electric / guitar_acoustic)
under commercial API terms. Different architecture (proprietary) → decorrelated from
Demucs by construction; whether that decorrelation is PERCEPTUALLY complementary is
decided by the blind-A/B harness (Milestone 3), not asserted here.

API flow (confirmed live against api.audioshake.ai, 2026-07-31):
  auth:     header  x-api-key: <key>
  upload:   POST /assets   (multipart field "file")          -> {"id": assetId, ...}
  create:   POST /tasks    {assetId, targets:[{model,formats}]} -> {"id": taskId, targets:[...]}
            (a public "url" may be given instead of assetId)
  poll:     GET  /tasks/{taskId}  -> targets[].status=="completed", targets[].output[].link
  download: GET  output[].link    (signed CDN URL) -> wav bytes

Pricing: 1 credit / minute / stem for guitar (guitar_electric/guitar_acoustic same;
other-x-guitar 1.5). Credit→USD depends on the plan; recorded in meta as credits.

Credential-gated: health() is False without AUDIOSHAKE_API_KEY, so an unconfigured
provider is never selected in production. mock=True exercises the contract offline.
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

_BASE_URL = os.environ.get("AUDIOSHAKE_BASE_URL", "https://api.audioshake.ai")
_POLL_INTERVAL_S = 3.0
_POLL_TIMEOUT_S = 600.0
_CREDITS_PER_MIN = {"guitar": 1.0, "guitar_electric": 1.0, "guitar_acoustic": 1.0,
                    "other-x-guitar": 1.5}
# AudioShake exposes dedicated instrument stems, guitar among them.
_STEMS = frozenset({"vocals", "drums", "bass", "guitar", "piano", "other",
                    "guitar_electric", "guitar_acoustic"})
# Riley stem name -> AudioShake model id (identity for the ones that match).
_MODEL_FOR = {"guitar": "guitar", "vocals": "vocals", "drums": "drums",
              "bass": "bass", "piano": "piano", "other": "other",
              "guitar_electric": "guitar_electric", "guitar_acoustic": "guitar_acoustic"}


class AudioShakeProvider:
    id = "api:audioshake"

    def __init__(self, api_key: str | None = None, out_root: Path = Path("/tmp/riley_sep_api"),
                 mock: bool = False, guitar_model: str = "guitar"):
        self._key = api_key or os.environ.get("AUDIOSHAKE_API_KEY")
        self._out = Path(out_root)
        self._mock = mock
        # which AudioShake guitar variant a "guitar" request maps to
        self._guitar_model = guitar_model

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            stems=_STEMS,
            architecture="api",
            license_status=LICENSE_API_TERMS,
            decorrelated_from=frozenset({"local:htdemucs_6s"}),
            # Milestone 3 blind-A/B (exp_20260731_062148_8f65d0): AudioShake 7 / stock 1.
            # Won BOTH electric-vocal-masked verses AND acoustic exposed guitar — even where
            # it was the quieter stem (not a loudness artifact). Only stock win: loud chorus.
            regimes_strong=frozenset({"vocal_masked", "acoustic", "exposed", "clean"}),
            regimes_weak=frozenset({"loud_dense"}),      # stock competitive in loud choruses
            cost_per_track_usd=None,                      # priced per credit/min (see meta)
            max_track_seconds=None,
            confidence="proven",                          # blind-A/B evidence recorded (n=2 songs)
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

    # ---- real API flow (endpoints confirmed live 2026-07-31) ----
    def _api_flow(self, req: SeparationRequest, t0: float) -> SeparationResult:
        import requests
        h = {"x-api-key": self._key}
        # 1) upload the mixture -> assetId
        with open(req.audio_path, "rb") as f:
            up = requests.post(f"{_BASE_URL}/assets", headers=h,
                               files={"file": (Path(req.audio_path).name, f, "audio/wav")},
                               timeout=180)
        up.raise_for_status()
        asset_id = up.json()["id"]
        # 2) create one task with a target per requested stem
        targets = [{"model": _MODEL_FOR.get(s, s), "formats": ["wav"]} for s in req.stems]
        # a "guitar" request honours the configured guitar variant
        for tg in targets:
            if tg["model"] == "guitar":
                tg["model"] = self._guitar_model
        job = requests.post(f"{_BASE_URL}/tasks",
                            headers={**h, "Content-Type": "application/json"},
                            json={"assetId": asset_id, "targets": targets}, timeout=60)
        job.raise_for_status()
        task_id = job.json()["id"]
        # 3) poll until every target completes
        deadline = time.time() + _POLL_TIMEOUT_S
        data: dict = {}
        while time.time() < deadline:
            st = requests.get(f"{_BASE_URL}/tasks/{task_id}", headers=h, timeout=30)
            st.raise_for_status()
            data = st.json()
            tgs = data.get("targets", [])
            states = {t.get("status") for t in tgs}
            if states & {"failed", "error"}:
                raise RuntimeError(f"{self.id}: task {task_id} failed: {data}")
            if tgs and states <= {"completed", "succeeded"}:
                break
            time.sleep(_POLL_INTERVAL_S)
        else:
            raise TimeoutError(f"{self.id}: task {task_id} timed out")
        # 4) download each target's output, mapping AudioShake model back to Riley stem name
        model_to_stem = {_MODEL_FOR.get(s, s): s for s in req.stems}
        model_to_stem[self._guitar_model] = "guitar"
        stems: dict[str, Path] = {}
        for tg in data.get("targets", []):
            stem = model_to_stem.get(tg.get("model"), tg.get("model"))
            for out in tg.get("output", []):
                link = out.get("link") or out.get("url")
                if not link:
                    continue
                dst = self._out / f"{task_id}_{stem}.wav"
                r = requests.get(link, timeout=180)
                r.raise_for_status()
                dst.write_bytes(r.content)
                stems[stem] = dst
                break
        return SeparationResult(
            stems=stems, provider_id=self.id, model_id=f"audioshake:{self._guitar_model}",
            latency_ms=int((time.time() - t0) * 1000),
            meta={"asset_id": asset_id, "task_id": task_id, "remote": True,
                  "credits_per_min": _CREDITS_PER_MIN.get(self._guitar_model, 1.0)})

    def _mock_result(self, req: SeparationRequest, t0: float) -> SeparationResult:
        stems = {}
        for name in req.stems:
            dst = self._out / f"MOCK_{name}.wav"
            dst.write_bytes(b"")
            stems[name] = dst
        return SeparationResult(
            stems=stems, provider_id=self.id, model_id="audioshake:MOCK",
            latency_ms=int((time.time() - t0) * 1000),
            meta={"mock": True, "note": "offline contract exercise"})


# static contract check — must satisfy the identical interface
_p: SeparatorProvider = AudioShakeProvider(mock=True)  # type: ignore[assignment]
