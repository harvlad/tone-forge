#!/usr/bin/env bash
#
# fetch_beat_model.sh — pull the latest published Beat Capture drum
# classifier into the ToneForgeEngine resource bundle so every app build
# embeds it. The server distributes the compiled `.mlmodelc` *directory*
# as a manifest + per-file objects (no zip), so we rebuild the directory
# member-by-member, verifying each file's sha256.
#
# Best-effort by design: no network, no published model, a missing tool,
# or any verification failure leaves the committed baseline model in
# place and exits 0 — a build must never fail because the model server
# is unreachable.
#
# Backend URL: $TONEFORGE_BEAT_MODEL_URL, else $TONEFORGE_BASE_URL, else
# the public host.

set -uo pipefail  # deliberately NOT -e: transient failures keep baseline

BASE_URL="${TONEFORGE_BEAT_MODEL_URL:-${TONEFORGE_BASE_URL:-https://jamn.app}}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RES_DIR="$ROOT/mobile-ios/Sources/ToneForgeEngine/Resources"
COMPILED="$RES_DIR/BeatClassifier.mlmodelc"

note() { printf '[fetch_beat_model] %s\n' "$*"; }
keep() { note "$* — keeping committed baseline"; exit 0; }

command -v curl    >/dev/null 2>&1 || keep "curl not found"
command -v python3 >/dev/null 2>&1 || keep "python3 not found"
command -v shasum  >/dev/null 2>&1 || keep "shasum not found"

latest="$(curl -fsS "$BASE_URL/api/beat-model/latest" 2>/dev/null)" \
    || keep "no model published / server unreachable at $BASE_URL"

version="$(printf '%s' "$latest" | python3 -c \
    'import sys,json; print(json.load(sys.stdin)["version"])' 2>/dev/null)"
[ -n "$version" ] || keep "latest pointer had no version"

manifest="$(curl -fsS "$BASE_URL/api/beat-model/$version/manifest" 2>/dev/null)" \
    || keep "manifest fetch failed for $version"

members="$(printf '%s' "$manifest" | python3 -c \
    'import sys,json
d=json.load(sys.stdin)
for f in d["files"]:
    print(f["path"] + "\t" + f["sha256"])' 2>/dev/null)" \
    || keep "manifest parse failed for $version"
[ -n "$members" ] || keep "manifest listed no files for $version"

staging="$(mktemp -d)"
trap 'rm -rf "$staging"' EXIT
model_dir="$staging/BeatClassifier.mlmodelc"
mkdir -p "$model_dir"

while IFS=$'\t' read -r path sha; do
    [ -n "$path" ] || continue
    dest="$model_dir/$path"
    mkdir -p "$(dirname "$dest")"
    curl -fsS "$BASE_URL/api/beat-model/$version/file/$path" -o "$dest" \
        2>/dev/null || keep "file $path fetch failed"
    got="$(shasum -a 256 "$dest" | awk '{print $1}')"
    [ "$got" = "$sha" ] || keep "sha256 mismatch on $path"
done <<< "$members"

rm -rf "$COMPILED"
mkdir -p "$RES_DIR"
mv "$model_dir" "$COMPILED"
note "installed model $version into $COMPILED"
