#!/usr/bin/env bash
#
# build_app.sh — assemble JamApp.app, a real macOS .app bundle around
# the SwiftPM JamApp executable. Output goes to dist/JamApp.app.
#
# This is the un-signed, un-notarized version: double-clickable on the
# machine that built it, but on a fresh Mac Gatekeeper will require a
# right-click → Open the first time. That's fine for an internal /
# prototype build. When we want a distribution-grade artifact, copy the
# codesign + notarize + DMG stages from connect/build_release.sh.
#
# Flags:
#   --host-only   build only the host arch (default). Faster.
#   --universal   build arm64 + x86_64. Requires full Xcode.app.
#   --run         after assembly, `open dist/JamApp.app`.
#
# Pre-reqs:
#   - Xcode CLT (provides swift, plutil)
#   - Backend reachable at http://localhost:8000 if you actually want
#     content to load when the app launches.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

APP_NAME="JamApp"
APP_DISPLAY="Jam"
INFO_PLIST="Resources/Info.plist"
DIST_DIR="dist"

UNIVERSAL=0
RUN_AFTER=0
for arg in "$@"; do
    case "$arg" in
        --host-only) UNIVERSAL=0 ;;
        --universal) UNIVERSAL=1 ;;
        --run)       RUN_AFTER=1 ;;
        -h|--help)   sed -n '2,22p' "$0"; exit 0 ;;
        *) echo "ERROR: unknown flag: $arg" >&2; exit 2 ;;
    esac
done

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!! \033[0m%s\n' "$*"; }
die()  { printf '\033[1;31mxx \033[0m%s\n' "$*" >&2; exit 1; }

command -v swift  >/dev/null || die "swift not found (install Xcode CLT)"
command -v plutil >/dev/null || die "plutil not found (Xcode CLT)"

VERSION="$(plutil -extract CFBundleShortVersionString raw "$INFO_PLIST")"
BUILD="$(plutil -extract CFBundleVersion raw "$INFO_PLIST")"
log "Building $APP_DISPLAY $VERSION (build $BUILD)"

# ---- Stage 1: compile release binary ---------------------------------

log "Cleaning dist/"
rm -rf "$DIST_DIR"
mkdir -p "$DIST_DIR"

SWIFT_BUILD_ARGS=(-c release)
if [[ $UNIVERSAL -eq 1 ]]; then
    SWIFT_BUILD_ARGS+=(--arch arm64 --arch x86_64)
    if [[ ! -x "$(xcode-select -p 2>/dev/null)/usr/bin/xcodebuild" ]]; then
        if [[ -x /Applications/Xcode.app/Contents/Developer/usr/bin/xcodebuild ]]; then
            export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer
            log "Using DEVELOPER_DIR=$DEVELOPER_DIR for universal build"
        else
            die "Universal build needs Xcode.app — install it or pass --host-only"
        fi
    fi
fi

log "Compiling release binary"
swift build "${SWIFT_BUILD_ARGS[@]}"
BIN_PATH="$(swift build "${SWIFT_BUILD_ARGS[@]}" --show-bin-path)/$APP_NAME"
[[ -x "$BIN_PATH" ]] || die "Compiled binary not found at $BIN_PATH"

# ---- Stage 2: assemble .app bundle -----------------------------------

APP_BUNDLE="$DIST_DIR/$APP_NAME.app"
log "Assembling $APP_BUNDLE"

mkdir -p "$APP_BUNDLE/Contents/MacOS"
mkdir -p "$APP_BUNDLE/Contents/Resources"

cp "$BIN_PATH"   "$APP_BUNDLE/Contents/MacOS/$APP_NAME"
cp "$INFO_PLIST" "$APP_BUNDLE/Contents/Info.plist"

# Stamp version into the bundled plist so what shipped matches the
# source-of-truth Info.plist even if someone edits mid-build.
plutil -replace CFBundleShortVersionString -string "$VERSION" \
    "$APP_BUNDLE/Contents/Info.plist"
plutil -replace CFBundleVersion -string "$BUILD" \
    "$APP_BUNDLE/Contents/Info.plist"

if [[ -f "Resources/AppIcon.icns" ]]; then
    cp "Resources/AppIcon.icns" "$APP_BUNDLE/Contents/Resources/"
    plutil -replace CFBundleIconFile -string "AppIcon" \
        "$APP_BUNDLE/Contents/Info.plist"
else
    warn "Resources/AppIcon.icns missing — using default Swift icon"
fi

# Ad-hoc sign so Gatekeeper at least recognizes the bundle on the
# build host. Real distribution requires a Developer ID; for that,
# adapt connect/build_release.sh.
log "Ad-hoc code-signing $APP_BUNDLE"
codesign --sign - --force --deep "$APP_BUNDLE" >/dev/null

log "Built $APP_BUNDLE"

if [[ $RUN_AFTER -eq 1 ]]; then
    log "Launching $APP_BUNDLE"
    open "$APP_BUNDLE"
fi
