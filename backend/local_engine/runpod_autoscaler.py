"""RunPod auto-spin-up for the GPU analysis worker.

The prod backend has no GPU. Instead of a hand-started persistent worker, this
creates a GPU pod ON DEMAND when analysis jobs are queued and TERMINATES it when
the queue goes idle — scale-to-zero, pay only while analyzing.

The pod runs `backend/scripts/runpod_analysis_worker.sh` (git-pull → deps →
models → claim loop), so there is NO Docker image to build/push. The worker then
claims the queued jobs via the normal /api/engine/claim flow and posts stems
back → R2 as usual.

Env (all off by default — this module is inert unless RUNPOD_AUTOSCALE=1):
  RUNPOD_AUTOSCALE=1              enable
  RUNPOD_API_KEY=rpa_...         RunPod API key
  RUNPOD_GPU_TYPE_IDS            comma list, default "NVIDIA A40"
  RUNPOD_IMAGE                   base image, default a CUDA-12 PyTorch image.
                                 Set to ghcr.io/<owner>/tone-forge-worker:latest
                                 (built by .github/workflows/runpod-worker-image.yml)
                                 for a prebuilt image with ffmpeg+deps+models baked
                                 in — cold boot ~2-3min -> ~30-60s.
  RUNPOD_IDLE_MINUTES            terminate after this idle, default 10
  JAMN_REPO_URL / JAMN_DEPLOY_REF / TONEFORGE_ENGINE_TOKEN / TONEFORGE_BACKEND_URL
  TONEFORGE_ANALYSIS_ENGINE      "experimental_specialist" (latest Riley) | current

NOTE: the RunPod REST v1 request bodies below follow the documented v1 schema but
have NOT been exercised against a live account (the key was unset when authored).
Verify the create body once RUNPOD_API_KEY is set — kept isolated + env-gated so
it can't affect the existing claim-worker path until deliberately enabled.
"""
from __future__ import annotations

import os
import time
from typing import List, Optional

try:
    import requests
except Exception:  # pragma: no cover
    requests = None  # type: ignore

_REST = "https://rest.runpod.io/v1"
_POD_NAME = "jamn-analysis-worker"
# CUDA 12.4 runtime runs on any host driver >= 12.4, so an ephemeral pod placed
# on any modern host has a working GPU. The 12.8.1 image needed a >=12.8.1
# driver and silently lost the GPU (torch.cuda unavailable) on 12.8.0 hosts,
# crashing analysis. Keep the runtime CUDA at/below the common host-driver floor.
_DEFAULT_IMAGE = "runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04"


def enabled() -> bool:
    return os.environ.get("RUNPOD_AUTOSCALE") == "1" and bool(os.environ.get("RUNPOD_API_KEY"))


def _key() -> str:
    return os.environ.get("RUNPOD_API_KEY", "")


def _headers() -> dict:
    return {"Authorization": f"Bearer {_key()}", "Content-Type": "application/json"}


def _gpu_ids() -> List[str]:
    raw = os.environ.get("RUNPOD_GPU_TYPE_IDS", "NVIDIA A40")
    return [g.strip() for g in raw.split(",") if g.strip()]


def _start_argv() -> List[str]:
    """dockerStartCmd (argv form, per REST v1 PodCreateInput): overrides the image
    CMD. Bootstraps then runs the worker; env is injected via the pod env below."""
    repo = os.environ.get("JAMN_REPO_DIR", "/workspace/tone-forge")
    inner = (
        f"cd {repo} 2>/dev/null || git clone \"$JAMN_REPO_URL\" {repo}; "
        f"bash {repo}/backend/scripts/runpod_analysis_worker.sh"
    )
    return ["bash", "-lc", inner]


def _worker_env() -> dict:
    # REST v1 wants env as a flat {KEY: value} object (NOT the legacy GraphQL
    # [{key,value}] list).
    passthrough = [
        "TONEFORGE_ENGINE_TOKEN", "TONEFORGE_BACKEND_URL", "TONEFORGE_ANALYSIS_ENGINE",
        "JAMN_REPO_URL", "JAMN_DEPLOY_REF", "JAMN_REPO_DIR",
    ]
    return {k: os.environ[k] for k in passthrough if os.environ.get(k)}


def list_worker_pods() -> List[dict]:
    if not enabled() or requests is None:
        return []
    try:
        r = requests.get(f"{_REST}/pods", headers=_headers(), timeout=20)
        if r.status_code != 200:
            return []
        pods = r.json() if isinstance(r.json(), list) else r.json().get("pods", [])
        return [p for p in pods if p.get("name") == _POD_NAME]
    except Exception:
        return []


def _has_live_worker() -> bool:
    for p in list_worker_pods():
        if str(p.get("desiredStatus", "")).upper() in ("RUNNING", "PENDING", "CREATED"):
            return True
    return False


def ensure_worker() -> Optional[str]:
    """Create a GPU worker pod if none is live. Returns the pod id (or existing).
    Safe to call on every job submit — no-ops when a worker is already up."""
    if not enabled() or requests is None:
        return None
    if _has_live_worker():
        return "existing"
    body = {
        "name": _POD_NAME,
        "imageName": os.environ.get("RUNPOD_IMAGE", _DEFAULT_IMAGE),
        "gpuTypeIds": _gpu_ids(),
        "gpuCount": 1,
        "containerDiskInGb": 40,
        "volumeInGb": 60,
        "volumeMountPath": "/workspace",
        "ports": ["8888/http"],
        "env": _worker_env(),
        "dockerStartCmd": _start_argv(),
    }
    # Private-registry pull (e.g. a private GHCR prebuilt image): RunPod uses a
    # stored Container Registry Auth credential, referenced by id. Set
    # RUNPOD_REGISTRY_AUTH_ID to attach it. Omitted -> anonymous pull (public
    # image / the default base image).
    _auth_id = os.environ.get("RUNPOD_REGISTRY_AUTH_ID")
    if _auth_id:
        body["containerRegistryAuthId"] = _auth_id
    try:
        r = requests.post(f"{_REST}/pods", headers=_headers(), json=body, timeout=40)
        if r.status_code in (200, 201):
            return (r.json() or {}).get("id")
    except Exception:
        pass
    return None


def terminate_worker() -> None:
    if not enabled() or requests is None:
        return
    for p in list_worker_pods():
        pid = p.get("id")
        if pid:
            try:
                requests.delete(f"{_REST}/pods/{pid}", headers=_headers(), timeout=20)
            except Exception:
                pass


# --- idle tracking: terminate after RUNPOD_IDLE_MINUTES with no queued/running jobs ---
_last_active_ts = time.time()


def note_activity() -> None:
    global _last_active_ts
    _last_active_ts = time.time()


def scale_down_if_idle(has_pending_or_running: bool) -> None:
    """Call periodically. Keeps the worker while jobs exist; terminates it after
    the idle window once the queue is empty."""
    if not enabled():
        return
    if has_pending_or_running:
        note_activity()
        return
    idle_min = float(os.environ.get("RUNPOD_IDLE_MINUTES", "10"))
    if (time.time() - _last_active_ts) >= idle_min * 60:
        terminate_worker()
        note_activity()  # reset so we don't re-terminate every tick
