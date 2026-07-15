#!/usr/bin/env bash
#
# Harvest E-GMD (Expanded Groove MIDI Dataset, CC BY 4.0) into a
# corrections-format CSV the Beat Capture trainer can merge via
# --harvest. Two stages:
#
#   Stage A (Python) backend/scripts/harvest_egmd.py
#     E-GMD wav+mid pairs -> onset manifest CSV (wav_path,onset_sec,role)
#   Stage B (Swift)  BeatModelTrainer harvest
#     manifest -> 7 OnsetFeatures + original,corrected,timestamp
#     (feature extraction in Swift = inference-parity, see plan)
#
# Best-effort: a missing/failed dataset must never block a synthetic-only
# train. On success prints the harvest CSV path on stdout as the LAST
# line so callers can capture it; prints nothing capturable on failure.
#
# Usage:
#   scripts/harvest_beat_dataset.sh <out_csv> [--limit N] [--max-per-role N] \
#       [--cache-dir DIR] [--sessions S]
# Env:
#   EGMD_CACHE_DIR   overrides default cache (backend/bench/data/e-gmd)
#   EGMD_LIMIT       shorthand for --limit (fast CI runs)

set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND="$ROOT/backend"
TRAINER="$ROOT/tools/BeatModelTrainer"

OUT="${1:-}"
if [[ -z "$OUT" ]]; then
  echo "usage: harvest_beat_dataset.sh <out_csv> [harvest_egmd args...]" >&2
  exit 2
fi
shift || true

CACHE_DIR="${EGMD_CACHE_DIR:-$BACKEND/bench/data/e-gmd}"
MANIFEST="$(mktemp -t egmd_manifest.XXXXXX).csv"

# CreateML/AVFoundation resample need a full Xcode toolchain.
if [[ -d /Applications/Xcode.app ]]; then
  export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer
fi

harvest_args=(--out "$MANIFEST" --cache-dir "$CACHE_DIR")
if [[ -n "${EGMD_LIMIT:-}" ]]; then
  harvest_args+=(--limit "$EGMD_LIMIT")
fi
harvest_args+=("$@")

echo "==> Stage A: MIDI -> onset manifest" >&2
if ! ( cd "$BACKEND" && python3 -m scripts.harvest_egmd "${harvest_args[@]}" >&2 ); then
  echo "harvest: Stage A failed; skipping E-GMD (synthetic-only train)" >&2
  exit 0
fi

manifest_rows=$(($(wc -l < "$MANIFEST") - 1))  # minus header
if [[ "$manifest_rows" -le 0 ]]; then
  echo "harvest: empty manifest; skipping E-GMD" >&2
  exit 0
fi
echo "==> Stage A produced $manifest_rows onset(s)" >&2

echo "==> Stage B: audio -> OnsetFeatures (Swift, inference parity)" >&2
if ! ( cd "$TRAINER" && swift run -c release BeatModelTrainer harvest \
         --harvest-manifest "$MANIFEST" --audio-root "$CACHE_DIR" \
         --out "$OUT" >&2 ); then
  echo "harvest: Stage B failed; skipping E-GMD" >&2
  exit 0
fi

harvest_rows=$(($(wc -l < "$OUT") - 1))
if [[ "$harvest_rows" -le 0 ]]; then
  echo "harvest: no feature rows emitted; skipping E-GMD" >&2
  exit 0
fi

echo "==> Harvested $harvest_rows feature row(s) -> $OUT" >&2
# Machine-readable path on stdout (last line) for the caller.
echo "$OUT"
