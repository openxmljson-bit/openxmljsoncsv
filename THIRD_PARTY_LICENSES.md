# Third-party licenses

OPENXMLJSON bundles the following third-party software. Each component remains
under its own license; the notices below satisfy their attribution terms.

## Python packages

| Component | License | Notes |
|---|---|---|
| PySide6-Essentials / Qt 6 | **LGPLv3** | Qt libraries are bundled as separate dynamic libraries (dynamically linked, replaceable by the user), per LGPLv3 §4(d). Source: <https://code.qt.io>. |
| Pygments | **BSD 2-Clause** | Syntax highlighting. © Pygments contributors. |
| jsbeautifier | **MIT** | JavaScript formatter (js-beautify port). |
| certifi | **MPL-2.0** | Mozilla CA bundle. File-level copyleft only; distributed unmodified. |
| pyobjc-framework-Cocoa (macOS only) | **MIT** | macOS menu-bar integration. |

## Rust crates (compiled into the native engine)

| Component | License |
|---|---|
| memmap2 | MIT / Apache-2.0 |
| rayon | MIT / Apache-2.0 |
| regex | MIT / Apache-2.0 |
| memchr | MIT / Unlicense |
| serde / serde_json | MIT / Apache-2.0 |
| jaq (jaq-interpret / jaq-parse / jaq-core / jaq-std) | MIT |
| pyo3 | MIT / Apache-2.0 |

## Notices

- **Qt / PySide6 (LGPLv3):** this application uses Qt under the GNU Lesser
  General Public License v3. The Qt libraries are dynamically linked; you may
  replace them with your own builds. The full LGPLv3 text is available at
  <https://www.gnu.org/licenses/lgpl-3.0.html>.
- **Pygments (BSD-2):** Redistribution and use in source and binary forms, with
  or without modification, are permitted provided that the copyright notice and
  disclaimer are retained. Full text: <https://github.com/pygments/pygments/blob/master/LICENSE>.
- Full license texts for all MIT / Apache-2.0 / MPL-2.0 components are
  available from their respective upstream repositories.
