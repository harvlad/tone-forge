#!/bin/bash
# package.sh — build + package jamn Kit as a macOS installer (.pkg).
#
# Usage:  plugin/scripts/package.sh
# Output: plugin/dist/jamnKit-<version>.pkg
#
# Signing: uses a "Developer ID Application/Installer" identity when one
# exists in the keychain; otherwise falls back to AD-HOC signing (fine
# for local installs; Gatekeeper will block ad-hoc pkgs on OTHER Macs).
# For real distribution you still need:
#   1. Apple Developer Program membership
#   2. Developer ID Application + Installer certificates
#   3. Notarization:  xcrun notarytool submit dist/jamnKit-*.pkg \
#        --keychain-profile jamn-notary --wait
#      && xcrun stapler staple dist/jamnKit-*.pkg
#   4. JUCE license tier + Ableton Link agreement (see scope doc).

set -euo pipefail

cd "$(dirname "$0")/.."
VERSION=$(grep -m1 "project(JamnKit VERSION" CMakeLists.txt \
    | sed -E 's/.*VERSION ([0-9.]+).*/\1/')
DIST="dist"
ART="build/JamnKit_artefacts/Release"
export DEVELOPER_DIR="${DEVELOPER_DIR:-/Applications/Xcode.app/Contents/Developer}"

echo "== building jamn Kit ${VERSION}"
cmake -B build -DCMAKE_BUILD_TYPE=Release >/dev/null
cmake --build build --target JamnKit_All -j8 | tail -1

APP_ID=$(security find-identity -v -p codesigning 2>/dev/null \
    | grep "Developer ID Application" | head -1 | sed -E 's/.*"(.+)"/\1/' || true)
INST_ID=$(security find-identity -v 2>/dev/null \
    | grep "Developer ID Installer" | head -1 | sed -E 's/.*"(.+)"/\1/' || true)

sign() {
    local target="$1"
    if [ -n "$APP_ID" ]; then
        codesign --force --deep --options runtime --timestamp \
            -s "$APP_ID" "$target"
    else
        codesign --force --deep -s - "$target"
    fi
}

echo "== signing (${APP_ID:-ad-hoc})"
sign "$ART/VST3/jamn Kit.vst3"
sign "$ART/AU/jamn Kit.component"

rm -rf "$DIST" pkgroot
mkdir -p "$DIST" \
    "pkgroot/Library/Audio/Plug-Ins/VST3" \
    "pkgroot/Library/Audio/Plug-Ins/Components"
cp -R "$ART/VST3/jamn Kit.vst3" "pkgroot/Library/Audio/Plug-Ins/VST3/"
cp -R "$ART/AU/jamn Kit.component" "pkgroot/Library/Audio/Plug-Ins/Components/"

echo "== packaging"
PKG="$DIST/jamnKit-$VERSION.pkg"
COMPONENT="$DIST/jamnKit-component.pkg"
pkgbuild --root pkgroot \
    --identifier com.harvlad.jamnkit \
    --version "$VERSION" \
    --install-location / \
    "$COMPONENT" >/dev/null
if [ -n "$INST_ID" ]; then
    productbuild --package "$COMPONENT" --sign "$INST_ID" "$PKG" >/dev/null
else
    productbuild --package "$COMPONENT" "$PKG" >/dev/null
fi
rm -f "$COMPONENT"
rm -rf pkgroot

echo "== done: $PKG"
[ -z "$APP_ID" ] && echo "NOTE: ad-hoc signed — local installs only." || true
