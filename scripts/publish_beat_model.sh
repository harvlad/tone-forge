#!/usr/bin/env bash
#
# publish_beat_model.sh — zip the compiled Beat Capture .mlmodelc and
# POST it to the backend's publish endpoint so every subsequent app
# build (and running clients on their next background check) pick it up.
#
# Unlike the best-effort build-time fetch, publishing MUST succeed or
# fail loudly: a silent no-publish would leave clients on a stale model.
#
# Usage:
#   scripts/publish_beat_model.sh <version>
#
# Environment:
#   TONEFORGE_BASE_URL       backend base (default https://jamn.app)
#   TONEFORGE_ENGINE_TOKEN   engine token (X-Engine-Token). Required for
#                            any non-loopback host.

set -euo pipefail

VERSION="${1:-}"
if [[ -z "$VERSION" ]]; then
    echo "usage: $0 <version>" >&2
    exit 2
fi

BASE_URL="${TONEFORGE_BASE_URL:-https://jamn.app}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RES_DIR="$ROOT/mobile-ios/Sources/ToneForgeEngine/Resources"
COMPILED="$RES_DIR/BeatClassifier.mlmodelc"

if [[ ! -d "$COMPILED" ]]; then
    echo "error: compiled model missing at $COMPILED — run scripts/train_beat_model.sh first" >&2
    exit 1
fi

command -v curl >/dev/null 2>&1 || { echo "error: curl not found" >&2; exit 1; }
command -v zip  >/dev/null 2>&1 || { echo "error: zip not found" >&2; exit 1; }

staging="$(mktemp -d)"
trap 'rm -rf "$staging"' EXIT
archive="$staging/model.zip"

# Zip with the BeatClassifier.mlmodelc/ wrapper — the store flattens the
# single top-level *.mlmodelc/ prefix on ingest.
( cd "$RES_DIR" && zip -r -q "$archive" BeatClassifier.mlmodelc )

echo "==> Publishing $VERSION to $BASE_URL"

auth_args=()
if [[ -n "${TONEFORGE_ENGINE_TOKEN:-}" ]]; then
    auth_args=(-H "X-Engine-Token: $TONEFORGE_ENGINE_TOKEN")
fi

# --fail-with-body: non-2xx exits non-zero but still writes the body.
curl -sS --fail-with-body \
    "${auth_args[@]+"${auth_args[@]}"}" \
    -X POST "$BASE_URL/api/beat-model" \
    -F "version=$VERSION" \
    -F "file=@$archive;type=application/zip" \
    -o "$staging/resp.txt" || {
        echo "error: publish request failed" >&2
        cat "$staging/resp.txt" >&2 || true
        exit 1
    }

cat "$staging/resp.txt"
echo
echo "==> Published $VERSION"
