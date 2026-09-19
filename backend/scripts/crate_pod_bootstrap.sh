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
#   ONNX CUDA execution provider once TensorFlow is absent AND onnxruntime's
#   CUDA build matches the image's CUDA runtime — see step 1b + 2b, the fix for
#   the CUDA-13-wheel-on-a-CUDA-12-image CPU fallback). On an A40 the ensemble
#   is fast, so it stays IN. This bootstrap therefore MUST both (a) put the
#   cuDNN/CUDA libs on LD_LIBRARY_PATH and (b) install a CUDA-12 onnxruntime-gpu,
#   or the polyphonic detector silently runs on CPU (~18x realtime) or drops out
#   of the ensemble entirely. NOTE: even the baked prod RUNPOD_IMAGE shipped a
#   CUDA-13 onnxruntime (its Dockerfile.worker pip-installs onnxruntime-gpu
#   unpinned) — so step 2b now RE-verifies + reinstalls on every image, baked or
#   generic, rather than trusting the baked one.
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

# 1b. Put the pip-bundled cuDNN 9 + CUDA 12 runtime libs on LD_LIBRARY_PATH so
#     onnxruntime's CUDA execution provider can dlopen them. onnxruntime does
#     NOT pip-depend on the nvidia-*-cu12 wheels — it expects libcudnn.so.9 /
#     libcublas.so.12 / libcudart.so.12 to already be resolvable — but the
#     torch cu126 stack ships them UNDER site-packages (nvidia/cudnn/lib,
#     nvidia/cublas/lib, nvidia/cuda_runtime/lib, torch/lib), which is NOT on
#     the default loader path. Without this, ORT's CUDA EP can't find its deps
#     and SILENTLY falls back to CPU (basic_pitch ~18x realtime → unusable).
#     This export is inherited by every python below AND by the ingest (step 4),
#     which is where basic_pitch actually runs on real tracks.
# NB: the python is written to a temp file and run, rather than fed via a
# heredoc INSIDE $(...) — macOS's bundled bash 3.2 (what `bash -n` uses in dev)
# mis-parses a here-doc nested in command substitution; a plain heredoc + a
# simple $(python file) is portable to both bash 3.2 and the pod's bash 5.
cat > /tmp/jamn_cuda_libdirs.py <<'PY'
import os, importlib
dirs = []
try:  # torch's own bundled CUDA libs (cu126 wheel: libcudnn, libcublas, ...)
    import torch
    dirs.append(os.path.join(os.path.dirname(torch.__file__), "lib"))
except Exception:
    pass
# pip nvidia-*-cu12 packages that ship with the cu126 torch wheel. Each is a
# namespace pkg whose __file__ sits at nvidia/<x>/__init__.py -> <x>/lib.
for mod in ("nvidia.cudnn", "nvidia.cublas", "nvidia.cuda_runtime",
            "nvidia.cufft", "nvidia.curand", "nvidia.cuda_nvrtc"):
    try:
        m = importlib.import_module(mod)
        d = os.path.join(os.path.dirname(m.__file__), "lib")
        dirs.append(d)
    except Exception:
        pass  # a package the wheel didn't ship — skip it, don't fail the boot
seen, out = set(), []
for d in dirs:  # de-dup, keep order, keep only dirs that actually exist
    if d and d not in seen and os.path.isdir(d):
        seen.add(d); out.append(d)
print(":".join(out))
PY
_CUDA_LIBS="$(python /tmp/jamn_cuda_libdirs.py 2>/dev/null)"
# The pip-package search above misses the OFFICIAL pytorch conda image
# (pytorch/pytorch:2.8.0-cuda12.6-cudnn9-runtime = the baked RUNPOD_IMAGE):
# there cuDNN 9 + the CUDA 12 runtime live in the conda env's own lib
# (/opt/conda/lib, sys.prefix/lib), NOT in pip nvidia-*-cu12 packages. Canary 3
# proved it — the CUDA-12 onnxruntime reinstalled fine but STILL fell to CPU
# ("CUDA 12.x ... make sure they're in the PATH") because libcudnn.so.9 wasn't
# on the path. So ALSO locate the real .so files wherever they sit and add
# their dirs — a find can't be fooled by image layout.
_FOUND_LIBS=""
for _soname in libcudnn.so.9 libcudart.so.12 libcublas.so.12 libcublasLt.so.12 libcufft.so.11; do
  _sopath="$(find /opt/conda /usr/local /usr/lib /usr/lib64 -name "${_soname}" 2>/dev/null | head -1)"
  [ -n "${_sopath}" ] && _FOUND_LIBS="$(dirname "${_sopath}"):${_FOUND_LIBS}"
done
_ALL_LIBS="${_CUDA_LIBS}:${_FOUND_LIBS}"
_ALL_LIBS="${_ALL_LIBS#:}"; _ALL_LIBS="${_ALL_LIBS%:}"
if [ -n "${_ALL_LIBS}" ]; then
  export LD_LIBRARY_PATH="${_ALL_LIBS}:${LD_LIBRARY_PATH:-}"
  echo "==> LD_LIBRARY_PATH prepended with cuDNN/CUDA lib dirs: ${_ALL_LIBS}"
else
  echo "WARN: no cuDNN/CUDA lib dirs found — onnxruntime CUDA EP may not resolve its libs"
fi

# 2. Lean analysis deps (no web/db stack).
REQ=requirements.txt
[[ -f requirements-worker.txt ]] && REQ=requirements-worker.txt
echo "==> installing deps from $REQ"
python -m pip install -q -r "$REQ" 2>&1 | tail -3 || true

# 2b. basic-pitch via its ONNX backend (NO TensorFlow) — the polyphonic MIDI
#     detector the full-fidelity ensemble needs for guitar/other. basic-pitch
#     goes in --no-deps so its base install_requires can't drag FULL TensorFlow
#     in — with TF present basic_pitch prefers the TF-CPU backend and runs the
#     polyphonic pass on CPU even when onnxruntime-gpu is installed.
#
# ROOT CAUSE this block now fixes (A40 canary on the baked image, cuda12.6):
#   `pip install onnxruntime-gpu` now pulls a CUDA **13** wheel — PyPI's default
#   onnxruntime-gpu build flipped to CUDA 13 around ORT 1.27, so the baked image
#   shipped onnxruntime 1.29.0 built for CUDA 13. On this cuda12.6 image that
#   build can't load its provider libs (no libcudart.so.13 / libcublas.so.13
#   anywhere — the image is CUDA 12.6) and onnxruntime SILENTLY falls back to
#   CPU, printing "CUDA 13.x. Please install all dependencies ... make sure
#   they're in the PATH". basic_pitch on CPU is ~18x realtime → unusable.
#   The OLD skip-guard here tested get_available_providers(), which lists the
#   COMPILED-IN providers (always includes CUDA for any onnxruntime-gpu build) —
#   so it never noticed the CPU fallback and skipped every fix.
#
# FIX: test whether the CUDA EP actually LOADS (build a real InferenceSession
#   and check get_providers()[0]), and if it doesn't, reinstall onnxruntime-gpu
#   from the CUDA-12 feed so its build MATCHES the image's CUDA 12.6 / cuDNN 9
#   runtime (which LD_LIBRARY_PATH from step 1b makes findable). Best-effort: on
#   failure the ensemble falls back to pYIN (lower fidelity) but the shard still
#   completes.
_ort_cuda_loads() {
  # Exit 0 only if onnxruntime's CUDA EP actually loads for the basic_pitch
  # model (get_providers lists it FIRST), not merely if it's compiled in.
  python - <<'PY'
import sys
try:
    import onnxruntime as ort
    from basic_pitch import ICASSP_2022_MODEL_PATH
except Exception:
    sys.exit(1)
mp = str(ICASSP_2022_MODEL_PATH)
if not mp.endswith(".onnx"):
    sys.exit(1)  # not the ONNX model variant — CUDA EP N/A
try:
    s = ort.InferenceSession(mp, providers=["CUDAExecutionProvider", "CPUExecutionProvider"])
except Exception:
    sys.exit(1)
sys.exit(0 if s.get_providers()[:1] == ["CUDAExecutionProvider"] else 1)
PY
}
if _ort_cuda_loads >/dev/null 2>&1; then
  echo "==> basic_pitch ONNX CUDA EP already loads — no onnxruntime reinstall needed"
elif command -v nvidia-smi >/dev/null 2>&1; then
  echo "==> basic_pitch ONNX CUDA EP does NOT load — reinstalling a CUDA-12.6-"
  echo "    MATCHED onnxruntime-gpu (pinned 1.20.1)"
  # Canary 4 proved the issue is a MINOR-version mismatch, not just cuDNN path:
  # the CUDA-12 feed's latest onnxruntime (1.29) is built for CUDA 12.8 and needs
  # `cudaLibraryGetKernel@libcudart.so.12` from 12.8, which the image's CUDA 12.6
  # libcudart lacks → "Failed to create CUDAExecutionProvider ... symbol:
  # cudaLibraryGetKernel". onnxruntime-gpu 1.20.1 (PyPI, PRE the CUDA-13 wheel
  # flip that landed ~ORT 1.27) is a CUDA-12.x + cuDNN-9 build whose libcudart
  # symbols all exist in 12.6. Pin it. If it STILL can't reach the GPU the
  # ensemble runs basic_pitch on CPU — slower but FULL fidelity (the crate's
  # generous midi_timeout_s lets it finish), never a pYIN downgrade.
  python -m pip uninstall -y onnxruntime onnxruntime-gpu tensorflow >/dev/null 2>&1 || true
  { python -m pip install --no-deps basic-pitch \
      && python -m pip install mir_eval resampy \
      && python -m pip install "onnxruntime-gpu==1.20.1"; } 2>&1 | tail -3 \
    || echo "onnxruntime-gpu(1.20.1) install skipped (CPU basic_pitch stays in effect)"
  if _ort_cuda_loads >/dev/null 2>&1; then
    echo "==> CUDA-12 onnxruntime-gpu now loads the CUDA EP"
  else
    echo "########## WARN: CUDA EP STILL not loading after cuda-12 reinstall — basic_pitch will run on CPU (pYIN fallback / slow) ##########"
  fi
else
  # CPU-only host (no nvidia-smi): install the plain CPU build so basic_pitch
  # at least works, no CUDA feed needed.
  { python -m pip install --no-deps basic-pitch \
      && python -m pip install mir_eval resampy onnxruntime; } 2>&1 | tail -3 \
    || echo "basic_pitch optional install skipped (pYIN fallback stays in effect)"
fi

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
