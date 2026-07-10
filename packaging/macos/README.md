# macOS release (notarized Developer ID DMG)

This produces a signed, notarized, stapled **universal2** `.dmg` that runs on
Intel and Apple Silicon and opens without Gatekeeper warnings — distributed
directly (download link), **not** via the Mac App Store.

## One-time setup

1. **Xcode Command Line Tools** (gives `codesign`, `xcrun`, `notarytool`,
   `hdiutil`, `lipo`):
   ```
   xcode-select --install
   ```

2. **Universal2 Python** — install the python.org "macOS 64-bit universal2"
   build (a Homebrew/arm64-only Python can't produce a universal app). Verify:
   ```
   file "$(python3 -c 'import sys;print(sys.executable)')"   # → x86_64 + arm64
   ```

3. **Rust with both targets**:
   ```
   rustup target add x86_64-apple-darwin aarch64-apple-darwin
   ```

4. **Developer ID Application certificate** in your login keychain. Create it
   in Keychain Access (Certificate Assistant → *Request a Certificate…*, save
   to disk) → developer.apple.com → Certificates → **+** → *Developer ID
   Application* → upload the CSR → download → double-click to install. Confirm:
   ```
   security find-identity -v -p codesigning     # note the "Developer ID Application: … (TEAMID)"
   ```

5. **Notary credentials**, stored once under a profile name. Create an
   app-specific password at https://appleid.apple.com, then:
   ```
   xcrun notarytool store-credentials oxj-notary \
       --apple-id "you@example.com" --team-id "TEAMID" \
       --password "abcd-efgh-ijkl-mnop"
   ```

## Cut a release

```
IDENTITY="Developer ID Application: Your Name (TEAMID)" \
NOTARY_PROFILE="oxj-notary" \
./packaging/macos/release.sh
```

That runs the whole pipeline — universal2 build → sign → notarize → staple —
and leaves the shippable file at `dist/OPENXMLJSON-<version>.dmg`
(version read from `pyproject.toml`).

Verify it on a clean Mac (ideally one that never ran the app):
```
spctl -a -t open --context context:primary-signature -vv dist/OPENXMLJSON-<version>.dmg
```

## Individual steps (if you don't want the all-in-one)

- `build_universal2.sh` — universal2 `.app` (+ unsigned `.dmg` unless
  `OXJ_SKIP_DMG=1`).
- `sign_and_notarize.sh <app>` — sign + notarize + staple + build the DMG
  (honors a `DMG=…` override for the output name).
- `make_icons.sh` — regenerate `OPENXMLJSON.icns` / `.ico` from a 1024px master.

## Troubleshooting

- **"The specified item could not be found in the keychain"** → your `IDENTITY`
  string doesn't match; copy the exact name (or SHA-1 hash) from
  `security find-identity -v -p codesigning`.
- **Notarization rejected** → read the log; it names the offending binary:
  ```
  xcrun notarytool log <submission-id> --keychain-profile oxj-notary
  ```
  Usually an unsigned nested dylib/`.so` — re-run; the deepest-first signing
  pass in `sign_and_notarize.sh` covers standard PyInstaller layouts.
- **App won't launch after signing** → almost always a missing hardened-runtime
  entitlement; adjust `packaging/macos/entitlements.plist`.
- **Not universal2** (`lipo -archs` shows one arch) → the Python you built with
  isn't universal2 (see setup step 2).
