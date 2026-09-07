#!/usr/bin/env bash
# runpod_analysis_worker.sh — run the JAMN GPU analysis worker on a RunPod pod.
#
# The production backend (https://jamn.app) has no GPU. This worker connects
# OUTBOUND (works behind NAT), long-polls /api/engine/claim, runs the heavy
# pipeline (Demucs separation + MIDI extraction + Riley transcription) on the
# pod's GPU, and posts results back. Stems land LOCAL on the pod — which is also
# where the Performance-Intelligence derivation runs fastest (no R2 round-trip).
#
# ── One-time pod setup (RunPod) ────────────────────────────────────────────
#   GPU:   A40 (48GB) is the value pick for Demucs+MIDI; RTX 4090/A5000 also fine.
#   Image: any CUDA 12.x PyTorch image (e.g. runpod/pytorch:2.x-cuda12).
#   Secrets (RunPod env):
#     TONEFORGE_ENGINE_TOKEN   the shared engine secret the backend validates
#     TONEFORGE_BACKEND_URL    https://jamn.app        (default below)
#     TONEFORGE_ANALYSIS_ENGINE experimental_specialist  (latest Riley) | current
#     JAMN_REPO_URL            git URL if the repo isn't already on the pod
#
# ── Run ────────────────────────────────────────────────────────────────────
#   bash backend/scripts/runpod_analysis_worker.sh
#
set -uo pipefail  # NOT -e: a single failing step must not kill the worker silently

# Mirror ALL output to a log on the persistent volume + serve it over the pod's
# 8888 http port, so failures are retrievable without SSH:
#   https://<podId>-8888.proxy.runpod.net/worker.log
LOG="/workspace/worker.log"
exec > >(tee -a "$LOG") 2>&1
echo "==== bootstrap start $(date -u) ===="
( cd /workspace && python -m http.server 8888 >/dev/null 2>&1 & ) || true

BACKEND_URL="${TONEFORGE_BACKEND_URL:-https://jamn.app}"
ANALYSIS_ENGINE="${TONEFORGE_ANALYSIS_ENGINE:-current}"
REPO_DIR="${JAMN_REPO_DIR:-/workspace/tone-forge}"

if [[ -z "${TONEFORGE_ENGINE_TOKEN:-}" ]]; then
  echo "FATAL: set TONEFORGE_ENGINE_TOKEN (the backend's engine secret)." >&2
  exit 1
fi

# 0. System codecs the analysis stack needs but the base pytorch image lacks:
#    ffmpeg decodes m4a/mp3 (_ensure_decodable); libsndfile backs soundfile.
#    Without ffmpeg, any non-wav upload crashes the analysis subprocess BEFORE
#    the GPU engages ("engine subprocess exited without a result").
if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "==> installing ffmpeg + libsndfile1"
  apt-get update -qq && apt-get install -y -qq ffmpeg libsndfile1 || \
    echo "WARN: apt install failed; non-wav decode may fail"
fi

# 1. Repo — clone if absent, else pull the deploy branch.
if [[ ! -d "$REPO_DIR/backend" ]]; then
  if [[ -z "${JAMN_REPO_URL:-}" ]]; then
    echo "FATAL: $REPO_DIR has no repo and JAMN_REPO_URL unset." >&2; exit 1
  fi
  git clone "$JAMN_REPO_URL" "$REPO_DIR"
fi
cd "$REPO_DIR/backend"
git fetch --quiet origin || true
git checkout "${JAMN_DEPLOY_REF:-main}" || true
git pull --quiet || true

# Persistent network volume (/workspace) caches the SLOW-to-fetch bits — the
# ~250 MB model weights and the pip WHEEL cache — but NOT the installed packages
# themselves: a venv on a network volume is thousands of small files and makes
# both install and every runtime import painfully slow. So deps install to the
# base image's LOCAL python (fast imports), and pip pulls wheels from the volume
# cache so it never re-downloads. Net: model download + wheel download happen
# ONCE (seeded on the volume); subsequent pods just unpack cached wheels locally.
export XDG_CACHE_HOME=/workspace/.cache
export HF_HOME=/workspace/.cache/huggingface
export TORCH_HOME=/workspace/.cache/torch
export PIP_CACHE_DIR=/workspace/.cache/pip
mkdir -p "$XDG_CACHE_HOME" "$PIP_CACHE_DIR"

# 2. Deps -> base image's local python. torch/torchaudio already ship in the
#    base; only install if missing. pip reuses the volume wheel cache, so on a
#    seeded volume this is unpack-only (no downloads).
python -m pip install --upgrade pip
# HARD version lock on the torch stack for every pip step below. Without
# it, dependency resolution (hf_midi_transcription's chain, observed
# 2026-09-07) UPGRADED torch to a CUDA-13 build mid-bootstrap: cu13
# wheels need newer host drivers than many RunPod hosts run (-> the
# intermittent "GPU pod, torch.cuda unavailable" lottery) and mismatch
# the CUDA-12 onnxruntime-gpu build, whose CUDA provider then silently
# falls back to CPU.
#
# But the lock must be >= 2.8: all-in-one-mps (sections) requires
# torch>=2.8, so locking whatever the base image ships (2.4.1 on the
# baked image) makes requirements resolution IMPOSSIBLE — the exact
# failure that shipped a librosa-less image on 2026-09-07. Bring the
# stack to 2.8.0+cu126 first (cu126 wheels bundle their own CUDA
# runtime and run on any 12.x host driver; it's cu13x that starts the
# driver lottery), THEN freeze it for everything below.
python - <<'PY' || python -m pip install --index-url https://download.pytorch.org/whl/cu126 \
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
python - <<'PY' || python -m pip install "torch>=2.1" "torchaudio>=2.1"
import importlib.util, sys
sys.exit(0 if importlib.util.find_spec("torch") else 1)
PY
# Prefer the lean worker requirements (analysis deps only) — it drops the
# compile-heavy web/db stack the worker never uses, cutting install time.
# Falls back to the full requirements.txt if the lean file isn't present.
REQ=requirements.txt
[[ -f requirements-worker.txt ]] && REQ=requirements-worker.txt
echo "==> installing deps from $REQ"
python -m pip install -r "$REQ"

# Optional: basic-pitch via its ONNX backend (NO TensorFlow) re-enables the
# polyphonic-bass MIDI route + octave-subdivision aid, which are silently dead
# without it (the pipeline logs "basic_pitch not installed" and locks poly bass
# to monophonic pYIN). Best-effort: if it fails to install, the worker keeps
# working exactly as today (pYIN fallback), so it never breaks the boot. The
# next run's receipt shows whether it helped + the boot cost.
#
# On CUDA hosts install onnxruntime-GPU, not the CPU build: basic_pitch's
# polyphonic pass is the single heaviest MIDI stage (~10 min/song measured on
# guitar+other with the CPU build) and ORT-GPU picks the CUDA execution
# provider automatically. Plain `onnxruntime` here was why "GPU" pods spent
# the MIDI wall on CPU. basic-pitch is installed WITHOUT the [onnx] extra so
# it can't drag the CPU build back in as a pinned dep.
# --no-deps: basic-pitch's base install_requires pulls FULL TensorFlow on
# Linux, and basic_pitch prefers the TF backend whenever it imports — which
# ran the polyphonic pass on TF-CPU even with onnxruntime-gpu present
# (observed on the 2026-09-07 Doomsday verification run: guitar 253 s +
# other 336 s). With TF absent the backend resolver falls through to ONNX.
# mir_eval + resampy are the only runtime deps not already installed.
ORT_PKG=onnxruntime
if command -v nvidia-smi >/dev/null 2>&1; then
  ORT_PKG=onnxruntime-gpu
  python -m pip uninstall -y onnxruntime tensorflow >/dev/null 2>&1 || true
fi
{ python -m pip install --no-deps basic-pitch \
    && python -m pip install mir_eval resampy "$ORT_PKG"; } 2>&1 | tail -3 \
  || echo "basic_pitch optional install skipped (pYIN fallback stays in effect)"

# 3. Models — Demucs htdemucs_6s + Beat-This + All-In-One (+ Riley HF when
#    experimental_specialist). Cached ON THE VOLUME via HF_HOME/TORCH_HOME, so
#    the seeded volume skips the ~250 MB download on every subsequent pod.
python -m local_engine.download_models || echo "WARN: model prefetch failed; worker will lazy-load."

# 3b. GPU self-test — prove whether CUDA actually COMPUTES (is_available can be
#     true while kernels fail on a driver/arch mismatch, silently forcing CPU).
#     When the autoscaler rented a GPU pod (TONEFORGE_EXPECT_GPU=1), a failed
#     self-test EXITS the pod instead of grinding every job 3-10x slower on
#     CPU at GPU prices — the autoscaler reaps the dead pod and the next
#     create usually lands on a healthy host. Measured: separation 18 s on a
#     working A40 vs 168-187 s on the silent-CPU pods this used to allow.
python - <<'PY' || { [ "${TONEFORGE_EXPECT_GPU:-0}" = "1" ] && { echo "FATAL: GPU pod without working CUDA — exiting for replacement"; exit 3; } || true; }
import torch
print("== GPU SELF-TEST ==")
print("torch:", torch.__version__, "| cuda build:", torch.version.cuda)
try:
    import onnxruntime as _ort
    print("onnxruntime providers:", _ort.get_available_providers())
except Exception as _e:
    print("onnxruntime probe failed:", _e)
print("cuda.is_available:", torch.cuda.is_available())
if torch.cuda.is_available():
    try:
        print("device:", torch.cuda.get_device_name(0),
              "| capability:", torch.cuda.get_device_capability(0))
        a = torch.randn(2048, 2048, device="cuda")
        b = (a @ a).sum().item()   # real kernel launch
        torch.cuda.synchronize()
        print("GPU MATMUL OK, checksum:", b)
    except Exception as e:
        print("GPU COMPUTE FAILED (falls back to CPU):", repr(e))
        print("== END SELF-TEST ==")
        raise SystemExit(1)
else:
    print("CUDA NOT AVAILABLE -> everything runs on CPU")
    print("== END SELF-TEST ==")
    raise SystemExit(1)
print("== END SELF-TEST ==")
PY

# 4. Run the claim loop. Restarts on crash so a transient error doesn't idle GPU.
echo "==> JAMN analysis worker → $BACKEND_URL  (engine=$ANALYSIS_ENGINE)"
export TONEFORGE_ANALYSIS_ENGINE="$ANALYSIS_ENGINE"
while true; do
  python -m local_engine.remote_worker \
    --backend "$BACKEND_URL" \
    --token "$TONEFORGE_ENGINE_TOKEN" \
    || echo "worker exited ($?); restarting in 5s"
  sleep 5
done
