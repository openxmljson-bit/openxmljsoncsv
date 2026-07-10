# Building, packaging and distributing OPENXMLJSON

This document expands SPEC.md §14. The repository is a Cargo workspace
(`crates/oxj-core`, `crates/oxj-py`, `crates/oxj-gui`) plus a Python package
(`python/openxmljson`) glued together by maturin.

## Development loop (Python/Qt app)

The native module `openxmljson._native` is built by maturin from
`crates/oxj-py` and dropped into your environment next to the pure-Python
package (`python-source = "python"` in `pyproject.toml`):

    pip install maturin pyside6
    maturin develop --release
    python -m openxmljson            # optionally: python -m openxmljson file.json

`maturin develop` rebuilds only the Rust side; edit the Python files freely
without rebuilding.

## Engine tests and validators

    cargo test -p oxj-core           # Rust unit tests for all engine modules
    cd validators && python run_all.py          # full: 5000 JSON / 4000 XML / 5000 CSV
    cd validators && python run_all.py --quick  # 10x fewer fuzz cases

The validators are exact Python ports of the three parsers cross-checked
against stdlib `json`, `xml.etree` and `csv` (SPEC §15). They need no Rust
toolchain, so they run anywhere Python runs.

## Pure-Rust GUI (no Python)

    cargo run -p oxj-gui --release

## Wheels

    maturin build --release
    # macOS fat binary (Intel + Apple silicon):
    maturin build --release --target universal2-apple-darwin

The wheel is abi3 (py39+), so one wheel per platform covers all supported
Python versions. Release profile uses fat LTO and one codegen unit
(workspace `Cargo.toml`); per-target AVX2/FMA flags live in
`.cargo/config.toml` (SPEC §13).

## Installers

### macOS (.app → .dmg)

    pip install pyinstaller
    maturin develop --release
    pyinstaller packaging/openxmljson.spec        # → dist/OPENXMLJSON.app
    hdiutil create -volname OPENXMLJSON -srcfolder dist/OPENXMLJSON.app \
        -ov -format UDZO dist/OPENXMLJSON.dmg

Signing and notarization (required for distribution outside the App Store):

    codesign --deep --force --options runtime \
        --sign "Developer ID Application: YOUR NAME (TEAMID)" dist/OPENXMLJSON.app
    xcrun notarytool submit dist/OPENXMLJSON.dmg \
        --apple-id you@example.com --team-id TEAMID --password APP_SPECIFIC --wait
    xcrun stapler staple dist/OPENXMLJSON.dmg

### Windows (.exe → .msi)

    pip install pyinstaller
    maturin develop --release
    pyinstaller packaging/openxmljson.spec        # → dist/OPENXMLJSON/OPENXMLJSON.exe

Wrap into an MSI with WiX (or Inno Setup), then Authenticode-sign:

    signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 \
        /f yourcert.pfx /p PASSWORD OPENXMLJSON.msi

Briefcase (`briefcase package`) is a maintained alternative to the
PyInstaller + hdiutil/WiX pipeline if you prefer one tool for both OSes.

## CI

`.github/workflows/build.yml` runs `cargo test -p oxj-core`, builds
`oxj-gui`, runs the three validators at full fuzz counts on
Ubuntu/macOS/Windows, then produces wheels (Linux/Windows/macOS-universal2)
and PyInstaller app bundles as artifacts. Signing is deliberately NOT done
in CI — it needs org secrets; run the commands above on a release machine.
