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

echo "==> Training model"
( cd "$TRAINER" && swift run -c release BeatModelTrainer --out "$MODEL" "$@" )

echo "==> Compiling $MODEL -> $COMPILED"
rm -rf "$COMPILED"
xcrun coremlc compile "$MODEL" "$RES_DIR"

echo "==> Done. Model resources at $RES_DIR"
ls -la "$RES_DIR"
