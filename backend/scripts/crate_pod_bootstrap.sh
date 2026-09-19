#!/usr/bin/env bash
# crate_pod_bootstrap.sh — set up a RunPod pod for a Vinyl-Crate ingest SHARD
# and run it. Called by crate_fleet.py's dockerStartCmd AFTER the repo is
# cloned and the trap-EXIT self-delete is armed (so this script never owns the
# teardown — a failure here still falls through to the pod's trap).
#
# WHY this exists (RCA of the first 40-track fleet run, which uploaded 0 shards):
#   The old inline bootstrap ran `pip install -r requirements.txt` (the FULL
#   web/db stack) on the generic runpod/pytorch:2.4.0 image — ~20 min before a
#   single track was touched — and installed NO ffmpeg, so the mp3 downloads
#   couldn't even decode. It also never brought torch to the 2.8 the sections
#   model needs, and left demucs to silently fall back to CPU. This mirrors the
#   PROVEN prod worker bootstrap (scripts/runpod_analysis_worker.sh) instead:
#     - ffmpeg + libsndfile (mp3/m4a decode)
#     - torch 2.8.0+cu126 FIRST, then PIP_CONSTRAINT-frozen (cu126 wheels bundle
#       their CUDA runtime and run on any 12.x host driver — cu13x is the
#       "GPU pod, torch.cuda unavailable" driver-lottery that silently ran demucs
#       on CPU)
#     - the LEAN requirements-worker.txt (drops fastapi/uvicorn/boto3/asyncpg…)
#     - prefetch demucs + beat-this + all-in-one so nothing downloads mid-run
#   It deliberately does NOT install basic-pitch/onnxruntime: the crate config
#   (PipelineConfig.crate) sets extract_midi=False, so the per-stem torchcrepe/
#   basic-pitch MIDI stage — ~62% of a full run and the reason the old run blew
#   the watchdog — never executes. That is the single biggest speedup here.
#
# Usage: bash crate_pod_bootstrap.sh <shard_i> <N> <concurrency> <watchdog_sec>
#        (run from the repo's backend/ dir)
set -uo pipefail   # NOT -e: one failing step must not skip the shard upload

SHARD_I="${1:?shard index}"
N="${2:?shard count}"
CONCURRENCY="${3:-1}"
WATCHDOG_SEC="${4:-2400}"
MANIFEST="${CRATE_MANIFEST:-lab_data/crate_candidates.json}"

echo "==== crate shard ${SHARD_I}/${N} bootstrap start $(date -u) ===="

# 0. Codecs. Without ffmpeg the ccMixter/FMA mp3 downloads crash on decode
#    BEFORE the GPU engages — the crate's donor audio is almost all mp3.
if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "==> installing ffmpeg + libsndfile1"
  apt-get update -qq && apt-get install -y -qq ffmpeg libsndfile1 \
    || echo "WARN: apt install failed; mp3 decode may fail"
fi

# 1. Torch stack — bring to 2.8.0+cu126 (all-in-one-mps sections need >=2.8),
#    then freeze it so nothing below re-resolves torch onto a cu13x build.
python -m pip install --upgrade pip -q
python - <<'PY' || python -m pip install -q --index-url https://download.pytorch.org/whl/cu126 \
    "torch==2.8.0" "torchaudio==2.8.0"
import sys
try:
    import torch
except Exception:
    sys.exit(1)
v = tuple(int(x) for x in torch.__version__.split("+")[0].split(".")[:2])
sys.exit(0 if v >= (2, 8) else 1)
PY
python - <<'PYCON'
import torch, torchaudio, pathlib
pathlib.Path("/tmp/jamn-pip-constraints.txt").write_text(
    f"torch=={torch.__version__.split('+')[0]}\n"
    f"torchaudio=={torchaudio.__version__.split('+')[0]}\n")
print("pip constraint:", open("/tmp/jamn-pip-constraints.txt").read().strip())
PYCON
export PIP_CONSTRAINT=/tmp/jamn-pip-constraints.txt

# 2. Lean analysis deps (no web/db stack, no basic-pitch — MIDI is off).
REQ=requirements.txt
[[ -f requirements-worker.txt ]] && REQ=requirements-worker.txt
echo "==> installing deps from $REQ"
python -m pip install -q -r "$REQ" 2>&1 | tail -3 || true

# 3. Prefetch models (demucs + beat-this + all-in-one) so none download mid-run
#    and blow the watchdog. Returns in seconds when the caches are warm.
python -m local_engine.download_models || echo "WARN: model prefetch failed; will lazy-load"

# 3b. GPU self-test — is_available() can be True while kernels fail on a
#     driver/arch mismatch, silently forcing CPU (the old run's real cost).
#     A real matmul proves compute; the crate's _gpu_preflight in ingest_crate
#     also guards, but failing here EXITS before we waste the pip/model time.
python - <<'PY' || { [ "${TONEFORGE_EXPECT_GPU:-0}" = "1" ] && { echo "FATAL: GPU pod without working CUDA — exiting"; exit 3; } || true; }
import torch
print("== GPU SELF-TEST ==")
print("torch:", torch.__version__, "| cuda build:", torch.version.cuda,
      "| is_available:", torch.cuda.is_available())
if not torch.cuda.is_available():
    raise SystemExit(1)
a = torch.randn(2048, 2048, device="cuda"); b = (a @ a).sum().item()
torch.cuda.synchronize(); print("GPU MATMUL OK:", b, "|", torch.cuda.get_device_name(0))
PY

# 4. Run the shard under the watchdog. TONEFORGE_EXPECT_GPU (set by the fleet)
#    makes ingest_crate hard-exit if CUDA vanished; the crate config skips MIDI.
echo "==> ingest shard ${SHARD_I}/${N} (concurrency=${CONCURRENCY}, watchdog=${WATCHDOG_SEC}s)"
timeout "${WATCHDOG_SEC}" python scripts/ingest_crate.py "${MANIFEST}" \
    --shard "${SHARD_I}/${N}" --concurrency "${CONCURRENCY}"
INGEST_RC=$?
echo "==> ingest exit rc=${INGEST_RC}"

# 5. Ship the crate/ shard to R2 regardless of ingest rc — a partial shard is
#    still worth merging (per-track isolation already dropped the rejects).
tar czf /tmp/shard.tgz -C data crate
python - "$SHARD_I" <<'PY'
import sys
from tone_forge import r2_storage as r2
i = sys.argv[1]
r2._client().upload_file("/tmp/shard.tgz", r2.bucket_name(), f"crate-shards/shard-{i}.tgz")
print(f"shard {i} uploaded to R2")
PY
echo "==== crate shard ${SHARD_I}/${N} done $(date -u) ===="
