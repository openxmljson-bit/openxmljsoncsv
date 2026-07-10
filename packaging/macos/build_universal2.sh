#!/usr/bin/env bash
#
# Build a UNIVERSAL2 (Intel x86_64 + Apple Silicon arm64) OPENXMLJSON.app and
# .dmg from ONE machine, so a single download runs natively on both.
#
# Requirements on this Mac:
#   * A universal2 Python — the python.org "macOS 64-bit universal2" installer.
#       verify:  file "$(python3 -c 'import sys;print(sys.executable)')"
#                -> "Mach-O universal binary with 2 architectures: x86_64 arm64"
#     A Homebrew / arm64-only Python will NOT work (the app comes out thin).
#   * Rust with both targets:  rustup target add x86_64-apple-darwin aarch64-apple-darwin
#   * Xcode Command Line Tools  (lipo, hdiutil).
#
# Usage:
#   ./packaging/macos/build_universal2.sh
#   PYTHON=/Library/Frameworks/Python.framework/Versions/3.12/bin/python3 \
#       ./packaging/macos/build_universal2.sh      # pick a specific interpreter
#
# Output: dist/OPENXMLJSON.app and dist/OPENXMLJSON.dmg (both universal2).
# Next:   sign + notarize with packaging/macos/sign_and_notarize.sh
#
set -euo pipefail
cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)"

PY="${PYTHON:-python3}"
EXE="$("$PY" -c 'import sys;print(sys.executable)')"
echo "==> Using Python: $EXE"
if ! file "$EXE" | grep -q 'x86_64' || ! file "$EXE" | grep -q 'arm64'; then
    echo "WARNING: this Python is not universal2 (need both x86_64 and arm64)."
    echo "         Install the python.org universal2 build, or the resulting app"
    echo "         will be single-arch and PyInstaller will reject universal2."
fi

echo "==> Creating a clean build venv (.venv-uni)"
rm -rf .venv-uni
"$PY" -m venv .venv-uni
# shellcheck disable=SC1091
source .venv-uni/bin/activate
pip install --upgrade pip >/dev/null
pip install maturin pyinstaller "PySide6-Essentials>=6.5" certifi

echo "==> Ensuring both Rust targets are installed"
rustup target add x86_64-apple-darwin aarch64-apple-darwin

echo "==> Building the universal2 native wheel and installing it"
rm -rf dist_wheel
maturin build --release --target universal2-apple-darwin --out dist_wheel
pip install --force-reinstall --no-deps dist_wheel/*.whl

echo "==> Verifying the bundled binaries are universal2"
NATIVE="$(python -c 'import openxmljson._native as m; print(m.__file__)')"
echo -n "    _native: "; lipo -archs "$NATIVE"
QTCORE="$(python -c 'import PySide6.QtCore as m; print(m.__file__)')"
echo -n "    QtCore : "; lipo -archs "$QTCORE" || true

echo "==> Running PyInstaller (universal2)"
rm -rf build dist
OXJ_TARGET_ARCH=universal2 pyinstaller packaging/openxmljson.spec --noconfirm

echo "==> Verifying the app is universal2"
echo -n "    app    : "; lipo -archs dist/OPENXMLJSON.app/Contents/MacOS/OPENXMLJSON

# release.sh sets OXJ_SKIP_DMG=1 because it builds the *signed* DMG itself; a
# standalone run still produces an (unsigned) DMG for convenience.
if [ "${OXJ_SKIP_DMG:-0}" = "1" ]; then
    echo ""
    echo "DONE: dist/OPENXMLJSON.app is universal2 (DMG left to the signing step)."
    exit 0
fi

echo "==> Building the DMG"
rm -f dist/OPENXMLJSON.dmg
hdiutil create -volname OPENXMLJSON \
    -srcfolder dist/OPENXMLJSON.app -ov -format UDZO dist/OPENXMLJSON.dmg

echo ""
echo "DONE: dist/OPENXMLJSON.app and dist/OPENXMLJSON.dmg are universal2."
echo "Sign & notarize next:"
echo "  IDENTITY=\"Developer ID Application: … (TEAMID)\" NOTARY_PROFILE=oxj-notary \\"
echo "      ./packaging/macos/sign_and_notarize.sh dist/OPENXMLJSON.app"
