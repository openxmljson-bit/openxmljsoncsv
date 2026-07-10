#!/usr/bin/env bash
#
# Build the macOS (.icns) and Windows (.ico) app icons from ONE 1024x1024 PNG
# master. The PyInstaller spec picks them up automatically if present.
#
# Usage:
#   ./packaging/make_icons.sh [path/to/master-1024.png]
# Default master:  packaging/icons/icon-1024.png
# Outputs:
#   packaging/icons/OPENXMLJSON.icns   (macOS  — via sips + iconutil)
#   packaging/icons/OPENXMLJSON.ico    (Windows — via ImageMagick or Pillow)
#
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="${1:-$HERE/icons/icon-1024.png}"
OUT="$HERE/icons"
mkdir -p "$OUT"
[ -f "$SRC" ] || { echo "ERROR: master PNG not found: $SRC (make a 1024x1024 PNG)"; exit 1; }

# ---- macOS .icns (needs macOS tools: sips + iconutil) -----------------------
if command -v iconutil >/dev/null 2>&1 && command -v sips >/dev/null 2>&1; then
    TMP="$(mktemp -d)"; ISET="$TMP/OPENXMLJSON.iconset"; mkdir -p "$ISET"
    for s in 16 32 128 256 512; do
        sips -z "$s" "$s"          "$SRC" --out "$ISET/icon_${s}x${s}.png"    >/dev/null
        sips -z "$((s*2))" "$((s*2))" "$SRC" --out "$ISET/icon_${s}x${s}@2x.png" >/dev/null
    done
    iconutil -c icns "$ISET" -o "$OUT/OPENXMLJSON.icns"
    rm -rf "$TMP"
    echo "wrote $OUT/OPENXMLJSON.icns"
else
    echo "skip .icns: run on macOS (needs sips + iconutil)"
fi

# ---- Windows .ico (multi-resolution) ----------------------------------------
if command -v magick >/dev/null 2>&1; then
    magick "$SRC" -define icon:auto-resize=256,128,64,48,32,16 "$OUT/OPENXMLJSON.ico"
    echo "wrote $OUT/OPENXMLJSON.ico (ImageMagick)"
elif python3 -c "import PIL" >/dev/null 2>&1; then
    python3 - "$SRC" "$OUT/OPENXMLJSON.ico" <<'PY'
import sys
from PIL import Image
src, out = sys.argv[1], sys.argv[2]
Image.open(src).convert("RGBA").save(
    out, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
)
print("wrote", out, "(Pillow)")
PY
else
    echo "skip .ico: install ImageMagick (brew install imagemagick) or Pillow (pip install pillow)"
fi
