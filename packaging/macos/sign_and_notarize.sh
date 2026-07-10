#!/usr/bin/env bash
#
# Sign, notarize and staple the OPENXMLJSON macOS app for Developer ID
# (direct / outside-the-App-Store) distribution.
#
# RUN THIS ON A MAC that has:
#   - Xcode command-line tools (codesign, xcrun, hdiutil, stapler)
#   - your "Developer ID Application" certificate in the login keychain
#     (check: security find-identity -v -p codesigning)
#
# ---- one-time credential setup (so notarization runs non-interactively) ----
# Create an app-specific password at https://appleid.apple.com, then:
#
#   xcrun notarytool store-credentials oxj-notary \
#       --apple-id "you@example.com" \
#       --team-id  "YOURTEAMID" \
#       --password "abcd-efgh-ijkl-mnop"      # the app-specific password
#
# ---- usage ----
#   # build the app first:
#   maturin develop --release
#   pyinstaller packaging/openxmljson.spec --noconfirm     # -> dist/OPENXMLJSON.app
#
#   IDENTITY="Developer ID Application: Your Name (YOURTEAMID)" \
#   NOTARY_PROFILE="oxj-notary" \
#   ./packaging/macos/sign_and_notarize.sh dist/OPENXMLJSON.app
#
set -euo pipefail

APP="${1:-dist/OPENXMLJSON.app}"
: "${IDENTITY:?Set IDENTITY to your 'Developer ID Application: Name (TEAMID)' string}"
: "${NOTARY_PROFILE:?Set NOTARY_PROFILE to the notarytool keychain profile name}"

HERE="$(cd "$(dirname "$0")" && pwd)"
ENTITLEMENTS="$HERE/entitlements.plist"
DMG="${DMG:-${APP%.app}.dmg}"

[ -d "$APP" ] || { echo "ERROR: app not found: $APP  (build it with PyInstaller first)"; exit 1; }
[ -f "$ENTITLEMENTS" ] || { echo "ERROR: missing $ENTITLEMENTS"; exit 1; }

# Preflight: confirm the signing identity actually exists in the keychain,
# so we fail with a clear message instead of codesign's cryptic
# "The specified item could not be found in the keychain."
if ! security find-identity -v -p codesigning | grep -qF "$IDENTITY"; then
    echo "ERROR: signing identity not found in your keychain:"
    echo "    $IDENTITY"
    echo
    echo "Available code-signing identities:"
    security find-identity -v -p codesigning || true
    echo
    echo "Fix: copy a 'Developer ID Application: …' name above (or its SHA-1"
    echo "hash) verbatim into IDENTITY. If none are listed, create the cert in"
    echo "Xcode > Settings > Accounts > Manage Certificates > + > Developer ID"
    echo "Application (this also generates the private key in your keychain)."
    exit 1
fi

# Nested code (dylibs, .so, frameworks, helper exes): hardened runtime +
# secure timestamp, no entitlements (entitlements apply to the main app exe).
sign_inner() {
    codesign --force --options runtime --timestamp --sign "$IDENTITY" "$1"
}

echo "==> Signing nested code, deepest first ..."
# -depth makes find emit a directory's contents before the directory itself,
# so nested dylibs are signed before the framework/app that contains them
# (required: signing a parent seals its children's hashes). BSD-find compatible.
while IFS= read -r -d '' f; do
    sign_inner "$f"
done < <(find "$APP/Contents" -depth \
    \( -name '*.dylib' -o -name '*.so' -o -name '*.framework' \) -print0)

# Standalone Mach-O executables (e.g. Python helper binaries) under MacOS/.
if [ -d "$APP/Contents/MacOS" ]; then
    while IFS= read -r -d '' f; do
        [ -f "$f" ] && sign_inner "$f"
    done < <(find "$APP/Contents/MacOS" -type f -print0)
fi

echo "==> Signing the app bundle (with entitlements) ..."
codesign --force --options runtime --timestamp \
    --entitlements "$ENTITLEMENTS" --sign "$IDENTITY" "$APP"

echo "==> Verifying the signature ..."
codesign --verify --deep --strict --verbose=2 "$APP"

echo "==> Building DMG: $DMG"
rm -f "$DMG"
hdiutil create -volname OPENXMLJSON -srcfolder "$APP" -ov -format UDZO "$DMG"
codesign --force --timestamp --sign "$IDENTITY" "$DMG"

echo "==> Submitting to the Apple notary service (a few minutes) ..."
xcrun notarytool submit "$DMG" --keychain-profile "$NOTARY_PROFILE" --wait

echo "==> Stapling the notarization ticket ..."
xcrun stapler staple "$DMG"
xcrun stapler staple "$APP" || true   # also staple the .app inside, best-effort

echo "==> Gatekeeper assessment ..."
spctl -a -t open --context context:primary-signature -vv "$DMG" || true

echo ""
echo "DONE: $DMG is signed, notarized and stapled — ready to distribute."
