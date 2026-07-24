# Linux packaging (AppImage)

OPENXMLJSON ships on Linux as a single-file **AppImage** — download, `chmod +x`,
and run; no install and no distro-specific packages.

## CI (recommended)

`.github/workflows/linux-appimage.yml` builds and publishes the AppImage:

- Triggers on version tags (`v*`) and on-demand (workflow_dispatch).
- Builds on **ubuntu-22.04** for broad glibc compatibility (an AppImage built on
  newer glibc won't run on older distros).
- Stamps the version from the tag, builds the native module + PyInstaller
  bundle, assembles an AppDir (desktop entry + icon + `AppRun`), and packages it
  with `appimagetool`.
- On a tag, publishes `OPENXMLJSON-<version>-x86_64.AppImage` to the public
  releases repo (same `RELEASES_REPO_TOKEN` secret as the macOS/Windows jobs).

The in-app updater already knows to download the `.AppImage` asset on Linux
(`update.pick_asset`).

## Local build

```bash
packaging/linux/build_appimage.sh 0.3.11
```

Needs Python 3, a Rust toolchain, and the Qt runtime X libraries the bundled
xcb platform plugin links against — see the workflow's *Install Qt runtime
libraries* step for the exact `apt-get` list (`libegl1`, `libxkbcommon-x11-0`,
the `libxcb-*` set, etc.). Output lands in `dist/`.

## Notes

- Only `x86_64` is built. An `aarch64` AppImage would need an ARM runner and the
  arm64 appimagetool.
- If the app fails to start on a target machine with a Qt "xcb" platform error,
  that machine is missing one of the X libraries above; they're normally present
  on any desktop Linux.
