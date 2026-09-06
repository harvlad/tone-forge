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

import logging
import os
import time
from typing import List, Optional

logger = logging.getLogger("toneforge.autoscale")

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


# pod id -> first time THIS process saw it. RunPod's own createdAt is
# preferred when parseable; this is the fallback so a pod with a missing
# or malformed timestamp still ages (an un-ageable pod could never be
# reaped, which is the exact hole a zombie hides in).
_pod_first_seen: dict = {}


def list_worker_pods() -> List[dict]:
    if not enabled() or requests is None:
        return []
    try:
        r = requests.get(f"{_REST}/pods", headers=_headers(), timeout=20)
        if r.status_code != 200:
            return []
        body = r.json()
        pods = body if isinstance(body, list) else body.get("pods", [])
        mine = [p for p in pods if p.get("name") == _POD_NAME]
    except Exception:
        return []
    now = time.time()
    live_ids = set()
    for p in mine:
        pid = p.get("id")
        if pid:
            live_ids.add(pid)
            _pod_first_seen.setdefault(pid, now)
    for pid in [k for k in _pod_first_seen if k not in live_ids]:
        _pod_first_seen.pop(pid, None)
    return mine


def _pod_age_sec(pod: dict) -> float:
    """Seconds since the pod was created. RunPod's createdAt when it
    parses, else how long this process has seen the pod (which restarts
    the clock on a backend restart — deliberately conservative: a fresh
    grace window is better than reaping a pod that is genuinely booting)."""
    raw = pod.get("createdAt") or pod.get("created_at")
    if isinstance(raw, str) and raw:
        try:
            from datetime import datetime, timezone

            stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            return max(0.0, time.time() - stamp.timestamp())
        except ValueError:
            pass
    pid = pod.get("id")
    first = _pod_first_seen.get(pid) if pid else None
    return max(0.0, time.time() - first) if first else 0.0


def _has_live_worker() -> bool:
    for p in list_worker_pods():
        if str(p.get("desiredStatus", "")).upper() in ("RUNNING", "PENDING", "CREATED"):
            return True
    return False


# --- HARD guardrails so a crash-looping bootstrap can never runaway-spawn ---
# (A missing cap once created 244 pods in 4.5h.) Every one of these must pass
# before a pod is created.
def _min_warm() -> int:
    """RUNPOD_MIN_WARM pods stay alive even when the queue is empty
    (default 0 = strict scale-to-zero). 1 kills the first-song-of-the-
    session cold start (image pull can cost minutes on a fresh host)
    at ~1 pod-hour of standing cost. Clamped to 2."""
    try:
        return max(0, min(2, int(os.environ.get("RUNPOD_MIN_WARM", "0"))))
    except ValueError:
        return 0


def _max_live_pods() -> int:
    """Concurrency cap: RUNPOD_MAX_WORKERS live pods (default 2 — two
    songs analyze in parallel; each extra pod is another ~$0.05-0.44/hr
    only while jobs run). Hard-clamped to 4."""
    try:
        return max(1, min(4, int(os.environ.get("RUNPOD_MAX_WORKERS", "2"))))
    except ValueError:
        return 2


_CREATE_COOLDOWN_SEC = 300     # >= 5 min between ANY two create attempts
_MAX_CREATES_PER_PROCESS = 12  # absolute backstop; a runaway trips this, then
                               # STOPS until the backend is restarted
_last_create_ts = 0.0
_creates_this_process = 0


def _reap_exited_pods() -> None:
    """Terminate EXITED/dead jamn pods so a crash-looping bootstrap can't
    accumulate (or keep billing) allocated-but-dead pods."""
    if requests is None:
        return
    for p in list_worker_pods():
        if str(p.get("desiredStatus", "")).upper() in ("EXITED", "TERMINATED", "DEAD"):
            pid = p.get("id")
            if pid:
                try:
                    requests.delete(f"{_REST}/pods/{pid}", headers=_headers(), timeout=20)
                except Exception:
                    pass


def ensure_worker(queue_depth: int = 1) -> Optional[str]:
    """Create a GPU worker pod if the queue outnumbers live workers —
    guarded by a hard live cap, a create cooldown, and a per-process
    create backstop so a failing bootstrap can never spawn a runaway
    fleet. `queue_depth` scales concurrency: with 2 queued jobs and the
    default RUNPOD_MAX_WORKERS=2 a second pod spins up. Returns the pod
    id, "existing", or None."""
    global _last_create_ts, _creates_this_process
    if not enabled() or requests is None:
        return None
    # Reap dead pods first — otherwise a crash-looping worker leaves EXITED
    # pods behind that may keep billing.
    _reap_exited_pods()
    live = [
        p for p in list_worker_pods()
        if str(p.get("desiredStatus", "")).upper() in ("RUNNING", "PENDING", "CREATED")
    ]
    # Queue depth drives scale-up; the min-warm floor holds even at
    # queue 0 so the next session's first song claims instantly.
    want = min(_max_live_pods(), max(queue_depth, _min_warm()))
    if want <= 0 or len(live) >= want:
        return "existing"
    now = time.time()
    if now - _last_create_ts < _CREATE_COOLDOWN_SEC:
        logger.info("autoscale: create suppressed by cooldown (%ds left)",
                    int(_CREATE_COOLDOWN_SEC - (now - _last_create_ts)))
        return None  # cooldown — a fast-exiting pod cannot be respawned rapidly
    if _creates_this_process >= _MAX_CREATES_PER_PROCESS:
        logger.error("autoscale: create backstop hit — refusing; restart to reset")
        return None
    # Record the attempt BEFORE the POST so a create that succeeds-but-then-
    # exits still counts against the cooldown/backstop.
    _last_create_ts = now
    _creates_this_process += 1
    body = {
        "name": _POD_NAME,
        "imageName": os.environ.get("RUNPOD_IMAGE", _DEFAULT_IMAGE),
        "containerDiskInGb": 40,
        "volumeInGb": 60,
        "volumeMountPath": "/workspace",
        "ports": ["8888/http"],
        "env": _worker_env(),
        "dockerStartCmd": _start_argv(),
    }
    # CPU vs GPU worker. The analysis pipeline is ~90% CPU-bound (only Demucs
    # separation uses the GPU, ~11s of a ~4min run), so a CPU pod is far
    # cheaper (~$0.05-0.10/hr vs $0.44) AND sidesteps every GPU failure mode
    # (CUDA-fork crash, driver mismatch, torchcrepe-on-GPU). Separation just
    # runs slower on CPU. Set RUNPOD_COMPUTE=CPU for the mobile backend.
    if os.environ.get("RUNPOD_COMPUTE", "GPU").upper() == "CPU":
        body["computeType"] = "CPU"
        body["cpuFlavorIds"] = [
            f.strip() for f in os.environ.get("RUNPOD_CPU_FLAVORS", "cpu5c").split(",") if f.strip()
        ]
        try:
            body["vcpuCount"] = int(os.environ.get("RUNPOD_VCPU", "8"))
        except ValueError:
            body["vcpuCount"] = 8
    else:
        body["gpuTypeIds"] = _gpu_ids()
        body["gpuCount"] = 1
    # Persistent network volume: holds the venv + models so a pod boots in
    # seconds instead of re-installing/re-downloading. Region-locked, so pin the
    # pod to the volume's datacenter. Set RUNPOD_NETWORK_VOLUME_ID (+ _DATACENTER).
    _nv = os.environ.get("RUNPOD_NETWORK_VOLUME_ID")
    if _nv:
        body["networkVolumeId"] = _nv
        body.pop("volumeInGb", None)  # the network volume replaces the pod-local one
        _dc = os.environ.get("RUNPOD_DATACENTER")
        if _dc:
            body["dataCenterIds"] = [_dc]
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
            pod_id = (r.json() or {}).get("id")
            logger.warning("autoscale: created worker pod %s (live=%d queued~%d)",
                           pod_id, len(live), queue_depth)
            return pod_id
        # THE overnight-strand failure mode: a create that fails here
        # used to vanish without a trace. Log status + body, always.
        logger.error("autoscale: pod create FAILED HTTP %s: %s",
                     r.status_code, r.text[:400])
    except Exception:
        logger.exception("autoscale: pod create raised")
    return None


def reap_stalled_workers(min_age_sec: float) -> int:
    """Terminate live-looking pods older than ``min_age_sec``. Returns the
    number terminated.

    THE hole the previous self-heal left open. ``ensure_worker`` judges
    liveness by RunPod's ``desiredStatus``, which says RUNNING for a pod
    whose bootstrap died — bad git ref, failed pip install, image pull
    wedged, wrong TONEFORGE_ENGINE_TOKEN so every claim 404s. The pod
    therefore counts against the live cap forever, ``ensure_worker``
    answers "existing" on every tick, and the queued job sits at 0%
    until someone notices. The only honest liveness signal is a claim or
    heartbeat actually reaching the backend, which lives in the API
    process — so the caller supplies the verdict (it only calls this
    after minutes of total engine silence with work queued) and this
    function supplies the pod age, so a pod that is merely still booting
    is never killed.

    Terminating also clears the create cooldown: a reap is proof the
    previous create produced nothing, and the pod-age gate already paces
    this to at most one reap per grace window. The per-process create
    backstop is untouched and still ends a true crash-loop.
    """
    global _last_create_ts
    if not enabled() or requests is None:
        return 0
    killed = 0
    for p in list_worker_pods():
        if str(p.get("desiredStatus", "")).upper() not in ("RUNNING", "PENDING", "CREATED"):
            continue
        age = _pod_age_sec(p)
        if age < min_age_sec:
            continue
        pid = p.get("id")
        if not pid:
            continue
        try:
            requests.delete(f"{_REST}/pods/{pid}", headers=_headers(), timeout=20)
            killed += 1
            logger.error(
                "autoscale: reaped stalled worker %s (age %.0fs, never claimed "
                "a job) — replacing", pid, age)
        except Exception:
            logger.exception("autoscale: reap of %s failed", pid)
    if killed:
        _last_create_ts = 0.0
    return killed


def diagnostics() -> dict:
    """Everything needed to tell the stall cases apart from a single
    admin call: a pod that never booted a worker, a create that keeps
    failing, a cooldown or backstop holding the line, or autoscale
    simply being off. Guessing from a screenshot cost two rounds of
    "you said you fixed it" — this is the answer in one request."""
    info = {
        "enabled": enabled(),
        "min_warm": _min_warm(),
        "max_live_pods": _max_live_pods(),
        "creates_this_process": _creates_this_process,
        "create_cooldown_remaining_s": max(
            0.0, _CREATE_COOLDOWN_SEC - (time.time() - _last_create_ts)
        ) if _last_create_ts else 0.0,
        "create_backstop": _MAX_CREATES_PER_PROCESS,
        "compute": os.environ.get("RUNPOD_COMPUTE", "GPU").upper(),
        "pods": [],
    }
    for p in list_worker_pods():
        info["pods"].append({
            "id": p.get("id"),
            "desired_status": p.get("desiredStatus"),
            "age_s": round(_pod_age_sec(p), 1),
        })
    return info


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


# --- presence-based pre-warm -------------------------------------------
# App-open signals (Library/history fetch, engine-status poll) pre-warm
# a worker BEFORE the user uploads: by the time they picked a song the
# pod is hot. Debounced so chatty polls cost one RunPod API call per
# 2 minutes; every create guard (cap, cooldown, backstop) still applies.
# Presence also counts as activity, so the idle teardown clock only
# starts once every app has gone quiet.
_last_prewarm_ts = 0.0
_PREWARM_DEBOUNCE_SEC = 120.0


def prewarm_async() -> None:
    global _last_prewarm_ts
    if not enabled():
        return
    now = time.time()
    if now - _last_prewarm_ts < _PREWARM_DEBOUNCE_SEC:
        return
    _last_prewarm_ts = now
    note_activity()  # user is here — hold the idle teardown
    import threading

    threading.Thread(
        target=lambda: ensure_worker(1), daemon=True
    ).start()


# --- idle tracking: terminate after RUNPOD_IDLE_MINUTES with no queued/running jobs ---
_last_active_ts = time.time()


def note_activity() -> None:
    global _last_active_ts
    _last_active_ts = time.time()


def scale_down_if_idle(has_pending_or_running: bool) -> None:
    """Call periodically. Keeps workers while jobs exist; after the
    idle window, terminates down to the RUNPOD_MIN_WARM floor (0 =
    full scale-to-zero)."""
    if not enabled():
        return
    if has_pending_or_running:
        note_activity()
        return
    idle_min = float(os.environ.get("RUNPOD_IDLE_MINUTES", "10"))
    if (time.time() - _last_active_ts) >= idle_min * 60:
        keep = _min_warm()
        if keep <= 0:
            terminate_worker()
        else:
            live = [
                p for p in list_worker_pods()
                if str(p.get("desiredStatus", "")).upper()
                in ("RUNNING", "PENDING", "CREATED")
            ]
            for p in live[keep:]:
                pid = p.get("id")
                if pid and requests is not None:
                    try:
                        requests.delete(f"{_REST}/pods/{pid}",
                                        headers=_headers(), timeout=20)
                        logger.info("autoscale: idle scale-down terminated %s "
                                    "(keeping %d warm)", pid, keep)
                    except Exception:
                        pass
        note_activity()  # reset so we don't re-terminate every tick
