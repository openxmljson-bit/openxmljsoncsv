# PyInstaller spec for OPENXMLJSON (SPEC §14).
#
# macOS:   pyinstaller packaging/openxmljson.spec  → dist/OPENXMLJSON.app
#          then: hdiutil create -volname OPENXMLJSON -srcfolder dist/OPENXMLJSON.app \
#                -ov -format UDZO dist/OPENXMLJSON.dmg
#          sign/notarize: codesign + notarytool + stapler (docs/BUILD.md)
# Windows: pyinstaller packaging/openxmljson.spec  → dist/OPENXMLJSON/OPENXMLJSON.exe
#          then wrap into an MSI with WiX or Inno Setup; sign with signtool.
#
# Prereq: the native module must be importable (maturin develop --release
# or pip install of the built wheel) before running PyInstaller.
#
# Size: the app uses only PySide6 QtCore/QtGui/QtWidgets. Everything below
# excludes the rest of the Qt/PySide6 surface and prunes Qt translations and
# unused plugin categories, then strips symbols — roughly halving the bundle
# vs. a default PyInstaller build with no feature loss. If the app fails to
# launch after a prune, widen KEEP_PLUGIN_DIRS (below) or drop the plugin
# filter; the translation filter and strip are always safe.

import os
import sys
from PyInstaller.utils.hooks import collect_submodules

# Target architecture for the macOS build. Leave unset for a native (host-arch)
# build; set OXJ_TARGET_ARCH=universal2 to build a Universal binary that runs on
# both Intel and Apple Silicon. A universal2 build REQUIRES every bundled binary
# (Python, PySide6, the Rust _native module) to itself be universal2 — otherwise
# PyInstaller aborts and names the thin (single-arch) file.
_TARGET_ARCH = os.environ.get("OXJ_TARGET_ARCH") or None

# App version for the macOS Info.plist — read from the installed package
# metadata (CI stamps it from the git tag before building), so Finder/About
# match the release. Falls back to 0.0.0 if metadata isn't available.
try:
    from importlib.metadata import version as _pkg_version
    _APP_VERSION = _pkg_version("openxmljson")
except Exception:
    _APP_VERSION = "0.0.0"

# App icons (built from a 1024px master by packaging/make_icons.sh). Used only
# if present, so the build still works before you've made them.
_ICON_DIR = os.path.join(SPECPATH, "icons")
_MAC_ICON = os.path.join(_ICON_DIR, "OPENXMLJSON.icns")
_WIN_ICON = os.path.join(_ICON_DIR, "OPENXMLJSON.ico")
_mac_icon = _MAC_ICON if os.path.exists(_MAC_ICON) else None
_exe_icon = _WIN_ICON if os.path.exists(_WIN_ICON) else None

block_cipher = None

# --- what we DON'T ship ------------------------------------------------------

# PySide6 submodules the app never imports (it uses only QtCore/QtGui/
# QtWidgets). Excluding them keeps PyInstaller's PySide6 hook from bundling
# their bindings and libraries.
_EXCLUDE_PYSIDE = [
    "PySide6.QtNetwork", "PySide6.QtNetworkAuth",
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuickWidgets",
    "PySide6.QtQuickControls2", "PySide6.QtQuick3D",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick", "PySide6.QtWebChannel",
    "PySide6.QtWebSockets", "PySide6.QtWebView",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets",
    "PySide6.QtPdf", "PySide6.QtPdfWidgets",
    "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtGraphs",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic", "PySide6.Qt3DAnimation", "PySide6.Qt3DExtras",
    "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtDesigner",
    "PySide6.QtHelp", "PySide6.QtUiTools",
    "PySide6.QtLocation", "PySide6.QtPositioning", "PySide6.QtSensors",
    "PySide6.QtSerialPort", "PySide6.QtSerialBus",
    "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtTextToSpeech",
    "PySide6.QtRemoteObjects", "PySide6.QtScxml", "PySide6.QtStateMachine",
    "PySide6.QtSpatialAudio", "PySide6.QtDBus", "PySide6.QtXml",
    "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets",
    "PySide6.QtSvg", "PySide6.QtSvgWidgets",
]
_EXCLUDE_OTHER = ["tkinter", "unittest", "pydoc", "pdb", "test", "lib2to3"]

a = Analysis(
    ["launcher.py"],
    pathex=["."],
    binaries=[],
    datas=[],
    hiddenimports=[
        "openxmljson._native",
        *collect_submodules("openxmljson"),
        *collect_submodules("jsbeautifier"),  # .js formatter (lazy-imported)
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=_EXCLUDE_PYSIDE + _EXCLUDE_OTHER,
    cipher=block_cipher,
)

# --- prune Qt translations and unused plugin categories ----------------------
#
# Qt ships a .qm translation file per module per locale (tens of MB); the app
# is English-only, so drop them all. Keep only the plugin directories the app
# actually needs at runtime.
KEEP_PLUGIN_DIRS = (
    "platforms",            # cocoa / windows / xcb — REQUIRED to start
    "styles",               # native look
    "imageformats",         # icons / images
    "iconengines",
    "platforminputcontexts",
    "platformthemes",
)


def _keep(dest: str) -> bool:
    d = dest.replace("\\", "/").lower()
    # Drop all Qt translation catalogs.
    if "/translations/" in d or d.endswith(".qm"):
        return False
    # Within Qt plugins, keep only the whitelisted categories.
    marker = "/plugins/"
    idx = d.find(marker)
    if idx != -1:
        rest = d[idx + len(marker):]
        category = rest.split("/", 1)[0]
        if category and category not in KEEP_PLUGIN_DIRS:
            return False
    return True


a.datas = [t for t in a.datas if _keep(t[0])]
a.binaries = [t for t in a.binaries if _keep(t[0])]

# Stripping symbols shrinks the bundle on macOS/Linux, but on Windows the
# strip utility corrupts the bundled DLLs (e.g. python3xx.dll), causing
# "LoadLibrary: Invalid access to memory location" at launch. So strip only
# off Windows.
_STRIP = sys.platform != "win32"

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name="OPENXMLJSON",
    debug=False,
    strip=_STRIP,   # drop symbol tables (macOS/Linux only — unsafe on Windows)
    upx=False,    # UPX breaks macOS code-signing/notarization — never on
    console=False,
    target_arch=_TARGET_ARCH,   # None = native; "universal2" = Intel+ARM
    icon=_exe_icon,             # Windows .ico (ignored on macOS)
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=_STRIP,
    upx=False,
    name="OPENXMLJSON",
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="OPENXMLJSON.app",
        icon=_mac_icon,
        bundle_identifier="com.openxmljson.viewer",
        info_plist={
            "NSHighResolutionCapable": True,
            "CFBundleShortVersionString": _APP_VERSION,
            # Declare the file types the app can open so macOS lists it in
            # "Open With" / lets it be set as the default handler. "Alternate"
            # rank offers it without hijacking existing defaults.
            "CFBundleDocumentTypes": [
                {
                    "CFBundleTypeName": "Structured data document",
                    "CFBundleTypeRole": "Viewer",
                    "LSHandlerRank": "Alternate",
                    "LSItemContentTypes": [
                        "public.json",
                        "public.xml",
                        "public.comma-separated-values-text",
                        "public.plain-text",
                        "public.text",
                    ],
                    "CFBundleTypeExtensions": [
                        "json", "jsonl", "ndjson", "xml", "csv", "tsv",
                        "tab", "txt", "js", "yaml", "yml",
                    ],
                },
            ],
        },
    )
