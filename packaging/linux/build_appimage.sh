#!/usr/bin/env bash
# Build a Linux AppImage locally (mirrors .github/workflows/linux-appimage.yml).
#
#   Usage:  packaging/linux/build_appimage.sh [VERSION]
#
# Run from the repository root. Requires: Python 3, Rust toolchain, and the Qt
# runtime X libraries (see the workflow's "Install Qt runtime libraries" step).
# Produces dist/OPENXMLJSON-<version>-x86_64.AppImage.
set -euo pipefail

VER="${1:-0.0.0}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

python3 -m pip install --upgrade pip
python3 -m pip install pyinstaller
python3 -m pip install .

( cd packaging && pyinstaller openxmljson.spec \
    --distpath ../dist --workpath ../build --noconfirm )

APPDIR=dist/AppDir
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin"
cp -r dist/OPENXMLJSON/. "$APPDIR/usr/bin/"

cat > "$APPDIR/openxmljson.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=OPENXMLJSON
Comment=Viewer for very large JSON / XML / CSV files
Exec=OPENXMLJSON
Icon=openxmljson
Categories=Utility;Development;
Terminal=false
EOF

cp packaging/icons/icon-1024.png "$APPDIR/openxmljson.png"

cat > "$APPDIR/AppRun" <<'EOF'
#!/bin/bash
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/bin/OPENXMLJSON" "$@"
EOF
chmod +x "$APPDIR/AppRun"

if [ ! -x /tmp/appimagetool ]; then
  wget -q \
    https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage \
    -O /tmp/appimagetool
  chmod +x /tmp/appimagetool
fi

OUT="dist/OPENXMLJSON-${VER}-x86_64.AppImage"
ARCH=x86_64 /tmp/appimagetool --appimage-extract-and-run "$APPDIR" "$OUT"
echo "Built $OUT"
