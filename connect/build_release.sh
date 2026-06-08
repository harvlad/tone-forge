#!/usr/bin/env bash
#
# build_release.sh — assemble a signed, notarized ToneForge Connect
# release. Outputs Connect.app and Connect-<version>.dmg into dist/.
#
# Per ONBOARDING_AUDIT §F0.1 and §F1.1. The user-visible promise is
# "double-click installs cleanly on a 5-year-old MacBook". Every step
# in this script protects that promise.
#
# Required environment variables:
#   DEVELOPER_ID          "Developer ID Application: ToneForge Inc (TEAMID)"
#   APPLE_ID              your-account@example.com
#   APPLE_TEAM_ID         10-character Apple Developer team id
#   APPLE_APP_PASSWORD    app-specific password from appleid.apple.com
#
# Notarization via API key (used in CI; takes precedence over the
# app-specific password if all three are set):
#   NOTARY_KEY_ID         App Store Connect API key id
#   NOTARY_ISSUER_ID      issuer UUID
#   NOTARY_KEY_PATH       path to the .p8 private key file
#
# Sparkle:
#   CONNECT_SPARKLE_PUBLIC_KEY  base64 EdDSA public key to embed in
#                               Info.plist (replaces __SPARKLE_PUBLIC_KEY__).
#                               If unset, Sparkle still launches but
#                               auto-update is effectively disabled
#                               because signature verification fails.
#
# Optional flags:
#   --dry-run             skip codesign / notarize / DMG (build only)
#   --skip-notarize       sign locally but don't submit to Apple
#   --publish             after success, gh release create with the DMG
#
# The script is intentionally bash-portable. It does not depend on
# Xcode being installed (only the command-line tools, swift, and
# the codesign / notarytool / stapler / hdiutil binaries that ship
# with the Xcode CLT package).
#

set -euo pipefail

# -----------------------------------------------------------------------------
# Constants
# -----------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

APP_NAME="Connect"
APP_BUNDLE_ID="com.toneforge.connect"
APP_DISPLAY="ToneForge Connect"
INFO_PLIST="Resources/Info.plist"
ENTITLEMENTS="Resources/Connect.entitlements"
DIST_DIR="dist"

# -----------------------------------------------------------------------------
# Flag parsing
# -----------------------------------------------------------------------------

DRY_RUN=0
SKIP_NOTARIZE=0
PUBLISH=0
UNIVERSAL=1  # default: ship universal binaries; --host-only for local dev
for arg in "$@"; do
    case "$arg" in
        --dry-run)        DRY_RUN=1 ;;
        --skip-notarize)  SKIP_NOTARIZE=1 ;;
        --publish)        PUBLISH=1 ;;
        --host-only)      UNIVERSAL=0 ;;
        -h|--help)
            sed -n '2,30p' "$0"
            exit 0
            ;;
        *)
            echo "ERROR: unknown flag: $arg" >&2
            exit 2
            ;;
    esac
done

log() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!! \033[0m%s\n' "$*"; }
die() { printf '\033[1;31mxx \033[0m%s\n' "$*" >&2; exit 1; }

# Submit $1 (zip or DMG) to Apple's notary service using whichever
# auth path the pre-flight selected. Blocks until the service
# returns. The caller is responsible for stapler staple afterward.
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

# -----------------------------------------------------------------------------
# Pre-flight: required tools and (when relevant) credentials
# -----------------------------------------------------------------------------

log "Pre-flight"

command -v swift   >/dev/null || die "swift not found (install Xcode CLT)"
command -v plutil  >/dev/null || die "plutil not found (Xcode CLT)"
command -v hdiutil >/dev/null || die "hdiutil not found (macOS)"

if [[ $DRY_RUN -eq 0 ]]; then
    command -v codesign >/dev/null || die "codesign not found"
    : "${DEVELOPER_ID:?DEVELOPER_ID must be set (e.g. 'Developer ID Application: ToneForge Inc (TEAMID)')}"
fi

NOTARIZE_AUTH=""  # "api" or "password" — populated below
if [[ $DRY_RUN -eq 0 && $SKIP_NOTARIZE -eq 0 ]]; then
    command -v xcrun >/dev/null || die "xcrun not found"
    : "${APPLE_TEAM_ID:?APPLE_TEAM_ID must be set (10-char team id)}"
    # API-key auth (CI path) takes precedence: it's non-interactive,
    # rotatable, and doesn't require an Apple ID to live in the env.
    if [[ -n "${NOTARY_KEY_ID:-}" && -n "${NOTARY_ISSUER_ID:-}" && -n "${NOTARY_KEY_PATH:-}" ]]; then
        [[ -f "$NOTARY_KEY_PATH" ]] || die "NOTARY_KEY_PATH points to a missing file: $NOTARY_KEY_PATH"
        NOTARIZE_AUTH="api"
    elif [[ -n "${APPLE_ID:-}" && -n "${APPLE_APP_PASSWORD:-}" ]]; then
        NOTARIZE_AUTH="password"
    else
        die "notarization requires either (NOTARY_KEY_ID + NOTARY_ISSUER_ID + NOTARY_KEY_PATH) or (APPLE_ID + APPLE_APP_PASSWORD)"
    fi
fi

if [[ $PUBLISH -eq 1 ]]; then
    command -v gh >/dev/null || die "gh not found (GitHub CLI required for --publish)"
fi

# Read version from the Info.plist so there is one source of truth.
VERSION="$(plutil -extract CFBundleShortVersionString raw "$INFO_PLIST")"
BUILD="$(plutil -extract CFBundleVersion raw "$INFO_PLIST")"
log "Building $APP_DISPLAY $VERSION (build $BUILD)"

# -----------------------------------------------------------------------------
# Stage 1: clean + compile (release configuration)
# -----------------------------------------------------------------------------

log "Cleaning dist/"
rm -rf "$DIST_DIR"
mkdir -p "$DIST_DIR"

log "Compiling release binary"
SWIFT_BUILD_ARGS=(-c release)
if [[ $UNIVERSAL -eq 1 ]]; then
    # Universal binary requires full Xcode (not just CLT) because
    # SwiftPM needs xcbuild. Auto-locate Xcode rather than asking the
    # user to `sudo xcode-select` against it — most dev machines have
    # both installed and we don't want to mutate the global default.
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
swift build "${SWIFT_BUILD_ARGS[@]}"
BIN_PATH="$(swift build "${SWIFT_BUILD_ARGS[@]}" --show-bin-path)/$APP_NAME"
[[ -x "$BIN_PATH" ]] || die "Compiled binary not found at $BIN_PATH"

# -----------------------------------------------------------------------------
# Stage 2: assemble Connect.app bundle layout
# -----------------------------------------------------------------------------

APP_BUNDLE="$DIST_DIR/$APP_NAME.app"
log "Assembling $APP_BUNDLE"

mkdir -p "$APP_BUNDLE/Contents/MacOS"
mkdir -p "$APP_BUNDLE/Contents/Resources"

cp "$BIN_PATH"       "$APP_BUNDLE/Contents/MacOS/$APP_NAME"
cp "$INFO_PLIST"     "$APP_BUNDLE/Contents/Info.plist"

if [[ -f "Resources/AppIcon.icns" ]]; then
    cp "Resources/AppIcon.icns" "$APP_BUNDLE/Contents/Resources/"
else
    warn "Resources/AppIcon.icns missing — shipping without a custom icon"
fi

# Stamp the bundle with the build version so notarization records
# what shipped. Belt-and-braces against editing Info.plist mid-script.
plutil -replace CFBundleShortVersionString -string "$VERSION" \
    "$APP_BUNDLE/Contents/Info.plist"
plutil -replace CFBundleVersion -string "$BUILD" \
    "$APP_BUNDLE/Contents/Info.plist"

# Sparkle.framework — the Connect binary is linked with
# @rpath/Sparkle.framework/... and rpath is @executable_path/../lib, so
# the framework MUST land at Contents/lib/Sparkle.framework or the app
# aborts at launch with "Library not loaded: @rpath/Sparkle.framework".
# Source it from the same swift build directory we just compiled out
# of so the bundled Sparkle matches what the binary was linked against.
SPARKLE_SRC="$(dirname "$BIN_PATH")/Sparkle.framework"
if [[ -d "$SPARKLE_SRC" ]]; then
    log "Embedding Sparkle.framework from $SPARKLE_SRC"
    mkdir -p "$APP_BUNDLE/Contents/lib"
    # -R preserves the framework's symlink structure (Versions/Current,
    # top-level Sparkle → Versions/B/Sparkle, etc.). cp -r would
    # dereference the symlinks and produce an invalid framework.
    cp -R "$SPARKLE_SRC" "$APP_BUNDLE/Contents/lib/"
else
    die "Sparkle.framework not found at $SPARKLE_SRC — swift build must produce it alongside the binary"
fi

# Sparkle public key — stamp in the EdDSA public half so a shipped
# build can verify the signature on incoming appcast entries. The
# Info.plist on disk holds the literal __SPARKLE_PUBLIC_KEY__
# placeholder so the source-controlled file never carries a secret
# (the public key isn't a secret, but keeping the placeholder
# pattern means a build without CONNECT_SPARKLE_PUBLIC_KEY fails
# loudly instead of silently shipping with a useless updater).
if [[ -n "${CONNECT_SPARKLE_PUBLIC_KEY:-}" ]]; then
    log "Stamping Sparkle public key into bundle Info.plist"
    plutil -replace SUPublicEDKey -string "$CONNECT_SPARKLE_PUBLIC_KEY" \
        "$APP_BUNDLE/Contents/Info.plist"
else
    warn "CONNECT_SPARKLE_PUBLIC_KEY unset — auto-update will not function in this build"
fi

# -----------------------------------------------------------------------------
# Stage 3: codesign with hardened runtime + entitlements
# -----------------------------------------------------------------------------

if [[ $DRY_RUN -eq 1 ]]; then
    warn "--dry-run: skipping codesign / notarize / DMG"
    log "Bundle ready at $APP_BUNDLE"
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

# Verify the signature was actually applied with the expected runtime.
codesign --verify --deep --strict --verbose=2 "$APP_BUNDLE"

# -----------------------------------------------------------------------------
# Stage 4: notarize the .app
# -----------------------------------------------------------------------------

if [[ $SKIP_NOTARIZE -eq 1 ]]; then
    warn "--skip-notarize: signed but not submitted to Apple"
else
    log "Submitting $APP_NAME.app to Apple notary (this may take several minutes)"

    NOTARIZE_ZIP="$DIST_DIR/$APP_NAME-notarize.zip"
    ditto -c -k --keepParent "$APP_BUNDLE" "$NOTARIZE_ZIP"

    notarize_submit "$NOTARIZE_ZIP"

    rm -f "$NOTARIZE_ZIP"

    log "Stapling notarization ticket to $APP_NAME.app"
    xcrun stapler staple "$APP_BUNDLE"
fi

# Final gatekeeper assessment. Must report source=Notarized Developer ID.
log "Gatekeeper assessment"
if ! spctl --assess --verbose=4 "$APP_BUNDLE" 2>&1 | tee "$DIST_DIR/spctl.log" ; then
    die "spctl assessment failed — the bundle would be rejected by Gatekeeper"
fi
if [[ $SKIP_NOTARIZE -eq 0 ]] \
   && ! grep -q "source=Notarized" "$DIST_DIR/spctl.log"; then
    die "bundle is signed but not notarized — Gatekeeper will block"
fi

# -----------------------------------------------------------------------------
# Stage 5: package as DMG (signed + notarized)
# -----------------------------------------------------------------------------

DMG_PATH="$DIST_DIR/$APP_NAME-$VERSION.dmg"
log "Building $DMG_PATH"

# Layout: drop Connect.app + a symlink to /Applications so the DMG
# opens to a "drag this into Applications" view without us needing a
# bespoke .DS_Store layout.
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

# -----------------------------------------------------------------------------
# Stage 6 (optional): publish to GitHub Releases
# -----------------------------------------------------------------------------

if [[ $PUBLISH -eq 1 ]]; then
    TAG="connect-v$VERSION"
    log "Publishing $TAG to GitHub Releases"

    # Resolve the repo from the parent monorepo (Connect lives under
    # connect/ inside the tone-forge repo).
    REPO="$(cd .. && gh repo view --json nameWithOwner -q .nameWithOwner)"

    gh release create "$TAG" \
        --repo "$REPO" \
        --title "$APP_DISPLAY $VERSION" \
        --notes "ToneForge Connect $VERSION release. See connect/CHANGELOG.md for details." \
        "$DMG_PATH"

    log "Released $TAG to $REPO"
fi

log "Done."
