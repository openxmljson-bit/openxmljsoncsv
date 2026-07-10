#!/usr/bin/env bash
#
# One-command macOS release for OPENXMLJSON:
#   universal2 build → codesign (Developer ID) → notarize → staple → DMG
#
# Produces a signed, notarized, stapled  dist/OPENXMLJSON-<version>.dmg  that
# runs on Intel + Apple Silicon and passes Gatekeeper on a clean Mac.
#
# One-time setup is in packaging/macos/README.md. In short you need: a
# universal2 Python (python.org), Xcode Command Line Tools, Rust with both
# apple-darwin targets, a "Developer ID Application" cert in your keychain, and
# notary credentials stored once via `xcrun notarytool store-credentials`.
#
# Usage:
#   IDENTITY="Developer ID Application: Your Name (TEAMID)" \
#   NOTARY_PROFILE="oxj-notary" \
#   ./packaging/macos/release.sh
#
set -euo pipefail
cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)"

: "${IDENTITY:?Set IDENTITY to your 'Developer ID Application: Name (TEAMID)' string}"
: "${NOTARY_PROFILE:?Set NOTARY_PROFILE to your notarytool keychain profile name}"

HERE="packaging/macos"

echo "==> Preflight"
for t in codesign xcrun hdiutil lipo security; do
    command -v "$t" >/dev/null 2>&1 || {
        echo "ERROR: missing '$t' — install the Xcode Command Line Tools "
        echo "       (xcode-select --install)."
        exit 1
    }
done
if ! security find-identity -v -p codesigning | grep -qF "$IDENTITY"; then
    echo "ERROR: signing identity not in keychain:"
    echo "    $IDENTITY"
    echo "Available code-signing identities:"
    security find-identity -v -p codesigning || true
    exit 1
fi

VERSION="$(
    python3 -c "import tomllib,pathlib;print(tomllib.loads(pathlib.Path('pyproject.toml').read_text())['project']['version'])" \
        2>/dev/null || echo "0.0.0"
)"
DMG="dist/OPENXMLJSON-${VERSION}.dmg"
echo "==> Releasing version ${VERSION}  ->  ${DMG}"

echo "==> [1/2] Building the universal2 app"
OXJ_SKIP_DMG=1 "$HERE/build_universal2.sh"

echo "==> [2/2] Signing, notarizing and stapling"
DMG="$DMG" "$HERE/sign_and_notarize.sh" dist/OPENXMLJSON.app

echo ""
echo "============================================================"
echo " RELEASE READY:  ${DMG}"
echo " Signed (Developer ID), notarized and stapled — universal2."
echo " Sanity check on a clean Mac:"
echo "   spctl -a -t open --context context:primary-signature -vv \"${DMG}\""
echo "============================================================"
