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
python - <<'PY' || python -m pip install "torch>=2.1" "torchaudio>=2.1"
import importlib.util, sys
sys.exit(0 if importlib.util.find_spec("torch") else 1)
PY
python -m pip install -r requirements.txt

# 3. Models — Demucs htdemucs_6s + Beat-This + All-In-One (+ Riley HF when
#    experimental_specialist). Cached ON THE VOLUME via HF_HOME/TORCH_HOME, so
#    the seeded volume skips the ~250 MB download on every subsequent pod.
python -m local_engine.download_models || echo "WARN: model prefetch failed; worker will lazy-load."

# 3b. GPU self-test — prove whether CUDA actually COMPUTES (is_available can be
#     true while kernels fail on a driver/arch mismatch, silently forcing CPU).
python - <<'PY' || true
import torch
print("== GPU SELF-TEST ==")
print("torch:", torch.__version__, "| cuda build:", torch.version.cuda)
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
else:
    print("CUDA NOT AVAILABLE -> everything runs on CPU")
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
