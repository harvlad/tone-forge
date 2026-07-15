#!/usr/bin/env bash
#
# Train the Beat Capture drum classifier and install the compiled model
# into the ToneForgeEngine resource bundle. macOS-only (CreateML).
#
# Usage: scripts/train_beat_model.sh [--corrections corpus.csv]

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TRAINER="$ROOT/tools/BeatModelTrainer"
RES_DIR="$ROOT/mobile-ios/Sources/ToneForgeEngine/Resources"
MODEL="$RES_DIR/BeatClassifier.mlmodel"
COMPILED="$RES_DIR/BeatClassifier.mlmodelc"

# CreateML needs a full Xcode toolchain, not CommandLineTools.
if [ -d /Applications/Xcode.app ]; then
  export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer
fi

mkdir -p "$RES_DIR"

# When harvested (E-GMD) rows are supplied, real data dominates the
# corpus, so shrink the synthetic backfill unless the caller pinned
# --per-role explicitly.
extra_args=()
has_harvest=0
has_per_role=0
for a in "$@"; do
  case "$a" in
    --harvest) has_harvest=1 ;;
    --per-role) has_per_role=1 ;;
  esac
done
if [[ "$has_harvest" -eq 1 && "$has_per_role" -eq 0 ]]; then
  extra_args+=(--per-role 2500)
fi

echo "==> Training model"
( cd "$TRAINER" && swift run -c release BeatModelTrainer --out "$MODEL" \
    ${extra_args[@]+"${extra_args[@]}"} "$@" )

echo "==> Compiling $MODEL -> $COMPILED"
rm -rf "$COMPILED"
xcrun coremlc compile "$MODEL" "$RES_DIR"

echo "==> Done. Model resources at $RES_DIR"
ls -la "$RES_DIR"
