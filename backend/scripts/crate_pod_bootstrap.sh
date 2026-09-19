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
#     - basic-pitch via its ONNX-GPU backend (NO TensorFlow), matching the prod
#       worker — see step 2b.
#
#   MIDI is back ON: PipelineConfig.crate is full-capture (extract_midi=True,
#   use_ensemble=True) as of the never-compromise-extraction rule. The earlier
#   "MIDI is the ~62% CPU killer, drop it" reasoning was written against a STALE
#   claim — the ensemble was already GPU-accelerated on 2026-09-07 (torchcrepe
#   uses CUDA in midi/ensemble_extractor + gpu_extractor; basic_pitch takes the
#   ONNX CUDA execution provider once TensorFlow is absent). On an A40 the
#   ensemble is fast, so it stays IN. This bootstrap therefore MUST install the
#   ONNX-GPU basic_pitch deps, or the polyphonic detector silently drops out of
#   the ensemble (guitar/other degrade to pYIN) on any pod not using the baked
#   prod RUNPOD_IMAGE.
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

# 2. Lean analysis deps (no web/db stack).
REQ=requirements.txt
[[ -f requirements-worker.txt ]] && REQ=requirements-worker.txt
echo "==> installing deps from $REQ"
python -m pip install -q -r "$REQ" 2>&1 | tail -3 || true

# 2b. basic-pitch via its ONNX backend (NO TensorFlow) — the polyphonic MIDI
#     detector the full-fidelity ensemble needs for guitar/other. Mirrors the
#     proven prod recipe (runpod_analysis_worker.sh): on a CUDA host install
#     onnxruntime-GPU (not the CPU build) so basic_pitch picks the CUDA
#     execution provider automatically, and install basic-pitch --no-deps so its
#     base install_requires can't drag FULL TensorFlow in — with TF present
#     basic_pitch prefers the TF-CPU backend and the polyphonic pass runs on CPU
#     even with onnxruntime-gpu installed. Best-effort: on failure the ensemble
#     falls back to pYIN (lower fidelity) but the shard still completes.
ORT_PKG=onnxruntime
if command -v nvidia-smi >/dev/null 2>&1; then
  ORT_PKG=onnxruntime-gpu
  python -m pip uninstall -y onnxruntime tensorflow >/dev/null 2>&1 || true
fi
{ python -m pip install --no-deps basic-pitch \
    && python -m pip install mir_eval resampy "$ORT_PKG"; } 2>&1 | tail -3 \
  || echo "basic_pitch optional install skipped (pYIN fallback stays in effect)"

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

# 3c. ONNX/basic_pitch MIDI smoke test — the crate config runs basic_pitch on
#     the ONNX CUDA provider, and a mismatched onnxruntime-gpu (installed by
#     step 2b onto this GENERIC base, whose cuDNN may not match the wheel) can
#     HANG for minutes on InferenceSession creation (GPU idle, CPU idle) instead
#     of erroring — the wedge that froze the A40 canary 40 min into a real track.
#     Prove the GPU-MIDI path builds a session + runs on a 1s dummy signal in a
#     few seconds, LOUDLY, up front. Best-effort: a failure prints a big warning
#     but never aborts the pod — the pipeline's onnx-guard forces CPU and the
#     per-stage MIDI timeout still bounds any residual hang at runtime.
echo "==> ONNX/basic_pitch MIDI smoke test (30s budget)"
timeout 30 python - <<'PY' || echo "########## WARN: MIDI/ONNX smoke test FAILED/TIMED OUT — GPU-MIDI env may hang; pipeline onnx-guard + per-stage timeout will catch it at runtime ##########"
import time, tempfile
import numpy as np, soundfile as sf
t0 = time.time()
import onnxruntime as ort
print("onnxruntime", ort.__version__, "| available providers:", ort.get_available_providers())
from basic_pitch import ICASSP_2022_MODEL_PATH
mp = str(ICASSP_2022_MODEL_PATH)
if mp.endswith(".onnx"):
    # Report which EP actually wins for a CUDA-first session (this is the call
    # that hangs on a broken env — the outer `timeout 30` is the real guard).
    sess = ort.InferenceSession(mp, providers=["CUDAExecutionProvider", "CPUExecutionProvider"])
    print("basic_pitch ONNX session providers:", sess.get_providers(), f"({time.time()-t0:.1f}s to init)")
from basic_pitch.inference import predict
y = (0.1 * np.sin(2 * np.pi * 220 * np.linspace(0, 1, 22050))).astype("float32")
with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
    sf.write(f.name, y, 22050); path = f.name
_, _, events = predict(path, ICASSP_2022_MODEL_PATH)
print(f"basic_pitch OK: {len(events)} note-events on 1s signal in {time.time()-t0:.1f}s total")
PY

# 4. Run the shard under the watchdog. TONEFORGE_EXPECT_GPU (set by the fleet)
#    makes ingest_crate hard-exit if CUDA vanished — which matters now that the
#    crate config runs the full GPU-accelerated ensemble MIDI stage.
echo "==> ingest shard ${SHARD_I}/${N} (concurrency=${CONCURRENCY}, watchdog=${WATCHDOG_SEC}s)"
timeout "${WATCHDOG_SEC}" python scripts/ingest_crate.py "${MANIFEST}" \
    --shard "${SHARD_I}/${N}" --concurrency "${CONCURRENCY}"
INGEST_RC=$?
echo "==> ingest exit rc=${INGEST_RC}"

# 5. Ship the crate/ shard to R2 regardless of ingest rc — a partial shard is
#    still worth merging (per-track isolation already dropped the rejects). The
#    LAST fleet run uploaded 0 shards: if every track rejected before writing a
#    license sidecar (e.g. all downloads 403'd) data/crate never existed, `tar`
#    errored, the tgz was never written, and the pod self-deleted empty. mkdir
#    -p guarantees the dir exists so an EMPTY shard still tars + uploads — the
#    merge step treats a missing shard as a warning, so an uploaded-but-empty
#    shard is strictly better than a silent nothing (it proves the pod ran).
mkdir -p data/crate
if tar czf /tmp/shard.tgz -C data crate; then
  python - "$SHARD_I" <<'PY'
import sys
from tone_forge import r2_storage as r2
i = sys.argv[1]
r2._client().upload_file("/tmp/shard.tgz", r2.bucket_name(), f"crate-shards/shard-{i}.tgz")
print(f"shard {i} uploaded to R2")
PY
else
  echo "WARN: tar of data/crate failed — no shard uploaded for ${SHARD_I}"
fi
echo "==== crate shard ${SHARD_I}/${N} done $(date -u) ===="
