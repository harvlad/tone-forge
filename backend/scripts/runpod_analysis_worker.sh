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

# Caches on the PERSISTENT network volume (/workspace) so models + deps are
# downloaded/installed ONCE (first pod seeds it), then every pod after mounts
# the seeded volume and boots in seconds. NOTE: only meaningful when a network
# volume is mounted at /workspace; without one this just uses pod-local disk.
export XDG_CACHE_HOME=/workspace/.cache
export HF_HOME=/workspace/.cache/huggingface
export TORCH_HOME=/workspace/.cache/torch
mkdir -p "$XDG_CACHE_HOME"

# 2. Deps in a venv ON THE VOLUME. --system-site-packages reuses the base
#    image's (multi-GB, CUDA) torch so we never reinstall it; only the analysis
#    stack (demucs, torchcrepe, librosa, ...) installs onto the volume. First
#    pod seeds it (~minutes); every pod after finds everything satisfied (fast).
VENV=/workspace/venv
if [[ ! -x "$VENV/bin/python" ]]; then
  echo "==> creating volume venv (first-run seed)"
  python -m venv --system-site-packages "$VENV"
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"
python -m pip install -q --upgrade pip
python -m pip install -q -r requirements.txt

# 3. Models — Demucs htdemucs_6s + Beat-This + All-In-One (+ Riley HF when
#    experimental_specialist). Cached on the volume via the env above, so the
#    seeded volume skips the ~250 MB download on every subsequent pod.
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
