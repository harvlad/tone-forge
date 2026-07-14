#!/usr/bin/env bash
#
# build_app.sh — assemble Jamn.app from the SwiftPM JamDesktop
# executable. Output goes to dist/.
#
# Default is a dev build: ad-hoc signed, double-clickable on the
# machine that built it; a fresh Mac needs right-click → Open once.
# Pass --release for a distribution-grade artifact: Developer ID
# codesign with hardened runtime + entitlements, Apple notarization,
# stapling, and a signed+notarized DMG (stages cloned from
# connect/build_release.sh).
#
# Flags:
#   --host-only       build only the host arch (default). Faster.
#   --universal       build arm64 + x86_64. Requires full Xcode.app.
#   --run             after assembly, `open dist/Jamn.app`.
#   --release         codesign + notarize + DMG (see env vars below).
#   --skip-notarize   with --release: sign locally, skip Apple submit.
#
# Env vars required by --release:
#   DEVELOPER_ID          "Developer ID Application: ToneForge Inc (TEAMID)"
#   APPLE_TEAM_ID         10-character Apple Developer team id
#   and ONE notarization auth path (API key takes precedence):
#   NOTARY_KEY_ID + NOTARY_ISSUER_ID + NOTARY_KEY_PATH   (CI)
#   APPLE_ID + APPLE_APP_PASSWORD                        (interactive)
#
# Pre-reqs: Xcode CLT (swift, plutil, codesign, notarytool, stapler,
# hdiutil). Backend reachable at http://localhost:8000 (or jamn.app
# via in-app Settings) for content to load.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Binary/executable name (SwiftPM target) vs user-visible app name.
APP_NAME="JamDesktop"
APP_DISPLAY="Jamn"
INFO_PLIST="Resources/Info.plist"
ENTITLEMENTS="Resources/JamDesktop.entitlements"
DIST_DIR="dist"

UNIVERSAL=0
RUN_AFTER=0
RELEASE=0
SKIP_NOTARIZE=0
DEBUG_BUILD=0
for arg in "$@"; do
    case "$arg" in
        --host-only)     UNIVERSAL=0 ;;
        --universal)     UNIVERSAL=1 ;;
        --run)           RUN_AFTER=1 ;;
        --release)       RELEASE=1 ;;
        --skip-notarize) SKIP_NOTARIZE=1 ;;
        --debug)         DEBUG_BUILD=1 ;;
        -h|--help)       sed -n '2,29p' "$0"; exit 0 ;;
        *) echo "ERROR: unknown flag: $arg" >&2; exit 2 ;;
    esac
done

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!! \033[0m%s\n' "$*"; }
die()  { printf '\033[1;31mxx \033[0m%s\n' "$*" >&2; exit 1; }

# Submit $1 (zip or DMG) to Apple's notary service. Blocks until the
# service returns; caller staples afterward.
notarize_submit() {
    local target="$1"
    case "$NOTARIZE_AUTH" in
        api)
            xcrun notarytool submit "$target" \
                --key "$NOTARY_KEY_PATH" \
                --key-id "$NOTARY_KEY_ID" \
                --issuer "$NOTARY_ISSUER_ID" \
                --team-id "$APPLE_TEAM_ID" \
                --wait
            ;;
        password)
            xcrun notarytool submit "$target" \
                --apple-id "$APPLE_ID" \
                --team-id "$APPLE_TEAM_ID" \
                --password "$APPLE_APP_PASSWORD" \
                --wait
            ;;
        *)
            die "notarize_submit called without an auth path configured"
            ;;
    esac
}

# ---- Pre-flight -------------------------------------------------------

command -v swift  >/dev/null || die "swift not found (install Xcode CLT)"
command -v plutil >/dev/null || die "plutil not found (Xcode CLT)"

NOTARIZE_AUTH=""
if [[ $RELEASE -eq 1 ]]; then
    command -v codesign >/dev/null || die "codesign not found"
    : "${DEVELOPER_ID:?DEVELOPER_ID must be set (e.g. 'Developer ID Application: ToneForge Inc (TEAMID)')}"
    [[ -f "$ENTITLEMENTS" ]] || die "entitlements missing: $ENTITLEMENTS"
    if [[ $SKIP_NOTARIZE -eq 0 ]]; then
        command -v xcrun >/dev/null || die "xcrun not found"
        : "${APPLE_TEAM_ID:?APPLE_TEAM_ID must be set (10-char team id)}"
        if [[ -n "${NOTARY_KEY_ID:-}" && -n "${NOTARY_ISSUER_ID:-}" && -n "${NOTARY_KEY_PATH:-}" ]]; then
            [[ -f "$NOTARY_KEY_PATH" ]] || die "NOTARY_KEY_PATH points to a missing file: $NOTARY_KEY_PATH"
            NOTARIZE_AUTH="api"
        elif [[ -n "${APPLE_ID:-}" && -n "${APPLE_APP_PASSWORD:-}" ]]; then
            NOTARIZE_AUTH="password"
        else
            die "notarization requires (NOTARY_KEY_ID + NOTARY_ISSUER_ID + NOTARY_KEY_PATH) or (APPLE_ID + APPLE_APP_PASSWORD)"
        fi
    fi
fi

VERSION="$(plutil -extract CFBundleShortVersionString raw "$INFO_PLIST")"
BUILD="$(plutil -extract CFBundleVersion raw "$INFO_PLIST")"
log "Building $APP_DISPLAY $VERSION (build $BUILD)"

# ---- Stage 1: compile release binary ---------------------------------

log "Cleaning dist/"
rm -rf "$DIST_DIR"
mkdir -p "$DIST_DIR"

if [[ $DEBUG_BUILD -eq 1 ]]; then
    SWIFT_BUILD_ARGS=(-c debug)
else
    SWIFT_BUILD_ARGS=(-c release)
fi
if [[ $UNIVERSAL -eq 1 ]]; then
    SWIFT_BUILD_ARGS+=(--arch arm64 --arch x86_64)
fi
# SwiftPM against this package needs full Xcode (XCTest-adjacent
# toolchain paths); auto-locate rather than mutating xcode-select.
if [[ ! -x "$(xcode-select -p 2>/dev/null)/usr/bin/xcodebuild" ]]; then
    if [[ -x /Applications/Xcode.app/Contents/Developer/usr/bin/xcodebuild ]]; then
        export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer
        log "Using DEVELOPER_DIR=$DEVELOPER_DIR"
    elif [[ $UNIVERSAL -eq 1 ]]; then
        die "Universal build needs Xcode.app — install it or pass --host-only"
    fi
fi

log "Compiling release binary"
swift build "${SWIFT_BUILD_ARGS[@]}"
BIN_PATH="$(swift build "${SWIFT_BUILD_ARGS[@]}" --show-bin-path)/$APP_NAME"
[[ -x "$BIN_PATH" ]] || die "Compiled binary not found at $BIN_PATH"

# ---- Stage 2: assemble .app bundle -----------------------------------

APP_BUNDLE="$DIST_DIR/$APP_DISPLAY.app"
log "Assembling $APP_BUNDLE"

mkdir -p "$APP_BUNDLE/Contents/MacOS"
mkdir -p "$APP_BUNDLE/Contents/Resources"

cp "$BIN_PATH"   "$APP_BUNDLE/Contents/MacOS/$APP_NAME"
cp "$INFO_PLIST" "$APP_BUNDLE/Contents/Info.plist"

# SwiftPM resource bundles (e.g. JamDesktop_JamDesktop.bundle) must
# ship next to the binary's Resources dir for Bundle.module lookup.
for bundle in "$(dirname "$BIN_PATH")"/*.bundle; do
    [[ -d "$bundle" ]] || continue
    log "Embedding $(basename "$bundle")"
    cp -R "$bundle" "$APP_BUNDLE/Contents/Resources/"
done

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

# ---- Stage 3: sign ----------------------------------------------------

if [[ $RELEASE -eq 0 ]]; then
    # Ad-hoc sign so Gatekeeper at least recognizes the bundle on the
    # build host. TCC (mic prompt) works because the bundle has a
    # stable identifier + Info.plist usage string.
    log "Ad-hoc code-signing $APP_BUNDLE"
    codesign --sign - --force --deep "$APP_BUNDLE" >/dev/null
    log "Built $APP_BUNDLE (dev build)"
    if [[ $RUN_AFTER -eq 1 ]]; then
        log "Launching $APP_BUNDLE"
        open "$APP_BUNDLE"
    fi
    exit 0
fi

log "Code-signing $APP_BUNDLE with hardened runtime"
codesign \
    --sign "$DEVELOPER_ID" \
    --options runtime \
    --entitlements "$ENTITLEMENTS" \
    --timestamp \
    --force \
    "$APP_BUNDLE"
codesign --verify --deep --strict --verbose=2 "$APP_BUNDLE"

# ---- Stage 4: notarize the .app ---------------------------------------

if [[ $SKIP_NOTARIZE -eq 1 ]]; then
    warn "--skip-notarize: signed but not submitted to Apple"
else
    log "Submitting $APP_DISPLAY.app to Apple notary (this may take several minutes)"
    NOTARIZE_ZIP="$DIST_DIR/$APP_DISPLAY-notarize.zip"
    ditto -c -k --keepParent "$APP_BUNDLE" "$NOTARIZE_ZIP"
    notarize_submit "$NOTARIZE_ZIP"
    rm -f "$NOTARIZE_ZIP"

    log "Stapling notarization ticket to $APP_DISPLAY.app"
    xcrun stapler staple "$APP_BUNDLE"
fi

# Gatekeeper assessment. Must report source=Notarized Developer ID.
log "Gatekeeper assessment"
if ! spctl --assess --verbose=4 "$APP_BUNDLE" 2>&1 | tee "$DIST_DIR/spctl.log"; then
    die "spctl assessment failed — the bundle would be rejected by Gatekeeper"
fi
if [[ $SKIP_NOTARIZE -eq 0 ]] \
   && ! grep -q "source=Notarized" "$DIST_DIR/spctl.log"; then
    die "bundle is signed but not notarized — Gatekeeper will block"
fi

# ---- Stage 5: DMG (signed + notarized) --------------------------------

DMG_PATH="$DIST_DIR/$APP_DISPLAY-$VERSION.dmg"
log "Building $DMG_PATH"

DMG_STAGING="$DIST_DIR/.dmg-staging"
rm -rf "$DMG_STAGING"
mkdir -p "$DMG_STAGING"
cp -R "$APP_BUNDLE" "$DMG_STAGING/"
ln -s /Applications "$DMG_STAGING/Applications"

hdiutil create \
    -volname "$APP_DISPLAY" \
    -srcfolder "$DMG_STAGING" \
    -ov \
    -format UDZO \
    "$DMG_PATH"

rm -rf "$DMG_STAGING"

log "Code-signing DMG"
codesign --sign "$DEVELOPER_ID" --timestamp --force "$DMG_PATH"

if [[ $SKIP_NOTARIZE -eq 0 ]]; then
    log "Notarizing DMG"
    notarize_submit "$DMG_PATH"
    xcrun stapler staple "$DMG_PATH"
fi

log "Built $DMG_PATH"
shasum -a 256 "$DMG_PATH"

if [[ $RUN_AFTER -eq 1 ]]; then
    log "Launching $APP_BUNDLE"
    open "$APP_BUNDLE"
fi
log "Done."
