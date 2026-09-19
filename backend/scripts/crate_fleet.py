#!/usr/bin/env python3
"""Crate seed run — fan the 40-track manifest across N GPU pods, merge the
results into the PROD crate, and TEAR DOWN every pod.

    RUN THIS ON THE VPS (root@jamn.app). It reads the RunPod key from
    /root/.runpod_key and the R2 creds from /opt/toneforge/.env — both live
    only there — creates the pods, waits for them, installs the merged crate
    into /opt/toneforge/backend/data/crate, restarts the service, and verifies
    no pod leaked. One command, start to finish.

Teardown is defence-in-depth (a leaked A40 bills ~$0.40/hr forever):
  1. trap EXIT  — each pod DELETEs its own id via the RunPod API when its
     bootstrap finishes OR errors, so a pod never outlives its work even if
     this driver dies.
  2. timeout 3600 — the ingest runs under a 1-hour watchdog; a wedged demucs
     can't pin a pod open, the script still falls through to the trap.
  3. final verify — this driver DELETEs every id it created, then re-lists pods
     by name prefix and prints LEAKED:<ids> (or LEAKED:none). Non-zero exit if
     anything is left.
  4. partial-create cleanup — if creating the N pods fails part-way, whatever
     was already created is terminated and the driver exits non-zero WITHOUT
     starting the run.

The pods clone origin/main (the crate prep is consolidated there), write stems
to the SAME prod R2 (track ids are globally unique — no collision), and upload
their small crate/ shard (registry + analysis + licenses JSON) to R2 under
crate-shards/shard-<i>.tgz. This driver pulls those four shards, unions them
with the 3 existing seed rows, and writes the merged registry back to prod.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path

# Make ``tone_forge`` importable (for r2_storage in the merge step) no matter
# the cwd the operator launches from — scripts/ is parents[0], backend/ is [1].
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# --- knobs (top-of-file constants) -----------------------------------------
N = 8                                   # number of GPU pods / shards
GPU_TYPE = "NVIDIA A40"                 # 48GB — the value pick for demucs
BRANCH = "main"                         # pods clone this ref
FLEET_PREFIX = "jamn-crate-seed"        # pod name prefix (teardown scans on it)
WATCHDOG_SEC = 5400                     # per-pod hard cap on the ingest
POLL_DEADLINE_SEC = 5400               # driver gives up waiting after this
CONCURRENCY = 2                         # per-pod --concurrency (2 demucs at once)

REPO_URL_DEFAULT = "https://github.com/harvlad/tone-forge.git"
IMAGE_DEFAULT = "runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04"
MANIFEST = "lab_data/crate_candidates.json"          # path inside backend/
CRATE_DIR = "/opt/toneforge/backend/data/crate"
ENV_FILE = "/opt/toneforge/.env"
RUNPOD_KEY_FILE = "/root/.runpod_key"

REST = "https://rest.runpod.io/v1"
UA = "jamn-crate-fleet/1.0 (+https://jamn.app)"


# --- small helpers ----------------------------------------------------------
def load_envfile(path: str = ENV_FILE) -> dict:
    d = {}
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                d[k.strip()] = v.strip().strip('"').strip("'")
    return d


def runpod_key() -> str:
    with open(RUNPOD_KEY_FILE) as fh:
        return fh.read().strip()


def api(method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(REST + path, data=data, method=method, headers={
        "Authorization": f"Bearer {runpod_key()}",
        "Content-Type": "application/json",
        "User-Agent": UA,
    })
    with urllib.request.urlopen(req, timeout=45) as r:
        raw = r.read()
    return json.loads(raw) if raw else {}


# --- pod bootstrap (dockerStartCmd) ----------------------------------------
def bootstrap(shard_i: int, repo_url: str, engine: str) -> list:
    """Self-terminating bootstrap for pod `shard_i`.

    The trap-EXIT self-delete and the git clone stay HERE (in dockerStartCmd)
    so the pod tears itself down even if the clone fails. Everything heavy —
    ffmpeg + the torch-2.8/cu126 lock + lean deps + model prefetch + GPU
    self-test + the timeout-guarded ingest + the R2 shard upload — lives in the
    committed scripts/crate_pod_bootstrap.sh (mirrors the proven prod worker
    bootstrap; kept out of this f-string so its heredocs' braces don't fight
    Python formatting, and so it's lintable/testable on its own)."""
    inner = f"""
set -uo pipefail
trap 'curl -s -X DELETE -H "Authorization: Bearer $RUNPOD_API_KEY" -H "User-Agent: {UA}" {REST}/pods/$RUNPOD_POD_ID >/dev/null 2>&1 || true' EXIT
echo "==== crate shard {shard_i}/{N} start $(date -u) ===="
cd /workspace && rm -rf tone-forge
git clone -b {BRANCH} --depth 1 "$JAMN_REPO_URL" tone-forge || exit 1
cd tone-forge/backend
bash scripts/crate_pod_bootstrap.sh {shard_i} {N} {CONCURRENCY} {WATCHDOG_SEC}
echo "==== crate shard {shard_i}/{N} done $(date -u) ===="
""".strip()
    return ["bash", "-lc", inner]


def create_pod(env: dict, shard_i: int) -> str | None:
    repo_url = env.get("JAMN_REPO_URL") or REPO_URL_DEFAULT
    engine = env.get("TONEFORGE_ANALYSIS_ENGINE", "current")
    pod_env = {
        "R2_ACCOUNT_ID": env["R2_ACCOUNT_ID"],
        "R2_ACCESS_KEY_ID": env["R2_ACCESS_KEY_ID"],
        "R2_SECRET_ACCESS_KEY": env["R2_SECRET_ACCESS_KEY"],
        "R2_BUCKET": env.get("R2_BUCKET", ""),
        "TONEFORGE_ANALYSIS_ENGINE": engine,
        "JAMN_REPO_URL": repo_url,
        "RUNPOD_API_KEY": runpod_key(),     # for the trap-EXIT self-delete
        "TONEFORGE_EXPECT_GPU": "1",
    }
    body = {
        "name": f"{FLEET_PREFIX}-{shard_i}",
        "imageName": env.get("RUNPOD_IMAGE") or IMAGE_DEFAULT,
        "containerDiskInGb": 40,
        "volumeInGb": 60,
        "volumeMountPath": "/workspace",
        "gpuTypeIds": [GPU_TYPE],
        "gpuCount": 1,
        "env": pod_env,
        "dockerStartCmd": bootstrap(shard_i, repo_url, engine),
    }
    return (api("POST", "/pods", body) or {}).get("id")


def pod_alive(pid: str) -> bool:
    try:
        st = str(api("GET", f"/pods/{pid}").get("desiredStatus", "")).upper()
        return st in ("RUNNING", "PENDING", "CREATED")
    except Exception:
        return False


def terminate(pid: str) -> None:
    try:
        api("DELETE", f"/pods/{pid}")
    except Exception:
        pass


def list_fleet_ids() -> list:
    """Every pod whose name starts with the fleet prefix — the teardown scan
    (catches anything, not just ids we tracked)."""
    try:
        body = api("GET", "/pods")
    except Exception:
        return []
    pods = body if isinstance(body, list) else body.get("pods", [])
    return [p.get("id") for p in pods
            if str(p.get("name", "")).startswith(FLEET_PREFIX) and p.get("id")]


# --- merge shards into the prod crate --------------------------------------
def merge_and_install(env: dict) -> int:
    from tone_forge import r2_storage as r2
    s3 = r2._client()
    bucket = r2.bucket_name()
    reg_path = os.path.join(CRATE_DIR, "registry.json")
    seed = json.load(open(reg_path)) if os.path.exists(reg_path) else []
    by_id = {r["id"]: r for r in seed}          # preserve the existing seed rows
    tmp = tempfile.mkdtemp(prefix="crate-merge-")
    got = 0
    for i in range(N):
        key = f"crate-shards/shard-{i}.tgz"
        dst = os.path.join(tmp, f"shard-{i}.tgz")
        try:
            s3.download_file(bucket, key, dst)
        except Exception as exc:  # a pod that never uploaded — warn, keep going
            print(f"  [warn] shard {i} missing in R2 ({key}): {exc}")
            continue
        with tarfile.open(dst) as t:
            t.extractall(os.path.join(tmp, str(i)))
        sc = os.path.join(tmp, str(i), "crate")
        for r in json.load(open(os.path.join(sc, "registry.json"))):
            by_id[r["id"]] = r                  # upsert by id
        for sub in ("analysis", "licenses"):
            src = os.path.join(sc, sub)
            os.makedirs(os.path.join(CRATE_DIR, sub), exist_ok=True)
            if os.path.isdir(src) and os.listdir(src):
                subprocess.run(f"cp -a {src}/. {os.path.join(CRATE_DIR, sub)}/",
                               shell=True, check=True)
        got += 1
    merged = sorted(by_id.values(), key=lambda r: r["id"])
    tmpf = reg_path + ".part"
    json.dump(merged, open(tmpf, "w"), indent=2)
    os.replace(tmpf, reg_path)
    print(f"merged {got}/{N} shards → registry now {len(merged)} tracks "
          f"(was {len(seed)} seed)")
    return got


# --- orchestration ----------------------------------------------------------
def main() -> int:
    env = load_envfile()
    # Make the R2 creds visible to r2_storage._client() in THIS process.
    for k in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET"):
        if env.get(k):
            os.environ[k] = env[k]

    created: list = []
    # 1) CREATE — with partial-failure cleanup.
    try:
        for i in range(N):
            pid = create_pod(env, i)
            if not pid:
                raise RuntimeError(f"pod create returned no id for shard {i}")
            created.append(pid)
            print(f"created pod {pid}  (shard {i}/{N}, name {FLEET_PREFIX}-{i})")
    except Exception as exc:
        print(f"!!! create failed ({exc}); terminating {len(created)} partial pod(s)")
        for pid in created:
            terminate(pid)
        _verify(created)
        return 2

    # 2) WAIT — pods self-terminate on completion.
    print(f"waiting up to {POLL_DEADLINE_SEC//60} min for {N} pods to finish...")
    deadline = time.time() + POLL_DEADLINE_SEC
    try:
        while time.time() < deadline:
            live = [p for p in created if pod_alive(p)]
            if not live:
                print("all pods reached a terminal state.")
                break
            print(f"  {len(live)} still running: {live}")
            time.sleep(30)
        else:
            print("!!! poll deadline hit — proceeding to merge with whatever landed")
    finally:
        # 3) MERGE regardless (missing shards are warned, not fatal).
        try:
            merge_and_install(env)
            subprocess.run(["systemctl", "restart", "toneforge"], check=False)
        except Exception as exc:
            print(f"!!! merge/restart error: {exc}")
        # 4) TEARDOWN + verify (defence-in-depth on top of each pod's trap).
        for pid in created:
            terminate(pid)
        rc = _verify(created)
    return rc


def _verify(created: list) -> int:
    time.sleep(5)
    leaked = list_fleet_ids()
    if leaked:
        print(f"LEAKED:{','.join(leaked)}")
        print("  kill each by hand:")
        for pid in leaked:
            print(f"    curl -X DELETE -H 'Authorization: Bearer $(cat {RUNPOD_KEY_FILE})' "
                  f"-H 'User-Agent: {UA}' {REST}/pods/{pid}")
        return 1
    print("LEAKED:none")
    return 0


if __name__ == "__main__":
    sys.exit(main())
