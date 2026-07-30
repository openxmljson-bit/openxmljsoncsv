# Build Prompts — Tools Menu (Electron + Rust)

Reusable, implementation-ready prompts for building each **Tools** feature on an
**Electron + Rust** stack. Prepend the **Shared Context** to any single-feature
prompt.

---

## Shared Context (prepend to every prompt)

> You are building **OPENXMLJSON**, a cross-platform desktop viewer for very
> large JSON / XML / CSV / TSV / YAML files, on **Electron + Rust**.
>
> **Architecture**
> - **Rust core** (`crates/oxj-core`): a zero-copy, **memory-mapped** parser +
>   structural index (24-byte packed nodes). It does all heavy lifting: parse,
>   index, reconstruct subtrees, stream records, format, diff, project.
> - **Native binding** (`crates/oxj-node`): expose the core to Node via
>   **napi-rs** (N-API). Long/streaming operations use **`ThreadsafeFunction`**
>   so Rust can push progress + streamed chunks back to JS **without blocking
>   the Node event loop or the renderer**. Ship prebuilt `.node` binaries per
>   platform (napi-rs GitHub Actions matrix).
> - **Electron main** (`src/main`): owns the Rust addon, the filesystem, temp
>   files, native menus (`Menu`/`MenuItem`), and windows. All engine calls run
>   here (or in a `worker_thread`), never in the renderer.
> - **Renderer** (`src/renderer`, TypeScript + HTML/CSS; framework optional —
>   React/Svelte or vanilla): the tabbed UI, dialogs, tree/table/text views.
>   **`contextIsolation: true`, `nodeIntegration: false`, `sandbox: true`.**
>   Talk to main only through a typed **preload bridge** (`contextBridge` +
>   `ipcRenderer.invoke` for request/response, `ipcRenderer.on` for progress
>   events). No `require` in the renderer.
> - **Data shape**: `reconstruct(nodeId)` returns JSON-serializable values
>   (object/array/scalars); XML subtrees serialize as
>   `{ tag, attributes, children }`.
> - **Tabs**: documents open in renderer tabs (max 20). **Tool output opens in
>   a new tab**, backed by a **temp file** (prefix `oxj_`, tracked and cleaned
>   on close / "Free up temp files").
> - **Concurrency rule**: any operation that can exceed ~50 ms runs in Rust
>   (async napi) or a worker thread and reports progress over IPC; the renderer
>   stays at 60 fps. Stream multi-GB documents — never hold the whole file in
>   JS memory.
> - **UI/UX**: theme via **CSS variables** (dark + light); all dialogs are HTML
>   modals, keyboard-accessible, legible in both themes. Because the renderer is
>   Chromium, rich HTML (e.g. the diff report) renders natively.
> - **Menu enablement**: native menu items are enabled/disabled from main based
>   on the active tab's format (main keeps per-tab metadata synced from the
>   renderer).
> - **Size caps**: whole-document reformat capped at ~1 GB; streaming features
>   handle multi-GB. Refuse politely past hard caps.
> - **Packaging**: `electron-builder` (dmg universal, nsis/msi, AppImage);
>   bundle the prebuilt Rust `.node`; code-sign/notarize on macOS.
>
> Deliver: Rust core function(s) + napi binding, the main-process IPC handler,
> the preload bridge types, the renderer UI, menu wiring + enablement, unit
> tests (Rust `#[test]` for core logic; a JS test for the pure formatter/diff
> helpers), and confirmation it builds and tests pass.

---

## 1. Beautify (Pretty-Print) → New Tab  /  Minify (Compact) → New Tab

> Two Tools items that reformat the **whole** active document into a new tab:
> **Beautify** (2-space indent) and **Minify** (compact).
>
> - **Applies to**: JSON and XML only; disable for CSV/TSV/YAML/text and when no
>   doc is open.
> - **Rust core**: `format_document(doc, mode: Beautify|Minify) -> stream of
>   bytes`. Do the serialization in Rust for speed; preserve key order and
>   value fidelity; XML keeps tags/attributes. Stream output to a temp file.
> - **napi/IPC**: `tools:format` invoke → returns the temp-file path (+ emits
>   `progress` events for large docs).
> - **Renderer**: on completion, open the temp file in a new tab titled
>   `data.json · beautified`. Show a modal progress bar for large docs.
> - **Caps/edge cases**: refuse > ~1 GB with a message; handle empty docs, deep
>   nesting (bounded recursion / iterative), non-UTF8, already-minified input.
> - **Tests**: Rust unit tests: value → expected pretty/minified bytes,
>   round-trip parse.

---

## 2. Format JavaScript

> Beautify the active **`.js`** text tab.
>
> - **Applies to**: only a JavaScript text tab; disabled otherwise.
> - **Impl**: format with a JS beautifier in the **main process** (`prettier`
>   or `js-beautify` via IPC) — don't run it in the sandboxed renderer. Return
>   formatted text; the renderer replaces the tab content in place.
> - **Caps**: skip files > ~32 MB with a message; best-effort on invalid JS
>   (never crash); handle minified one-liners and non-UTF8.
> - **Tests**: formatter wrapper returns expected output for a snippet.

---

## 3. Deep Dive (select fields) → New Tab…

> Let the user **pick fields** from the document's inferred structure and
> extract a **projected copy** into a new tab; must handle **multi-GB arrays**.
>
> - **Applies to**: JSON / NDJSON.
> - **Step 1 — infer field tree (Rust, streaming)**: `schema_field_tree(doc)`
>   walks the document and returns an **array-transparent** path tree
>   (`items[].price`), streaming so it never loads everything. Report progress
>   over IPC ("Scanning fields…"); cancellable (pass an `AbortSignal`/cancel
>   token honored in Rust).
> - **UI — field picker modal (HTML)**: a **checkbox tree**, whole-row
>   clickable, parent↔child cascade, **expanded by default**, search/filter box
>   for large trees, "Select all / none", OK / Cancel. Keyboard navigable;
>   readable selection highlight in both themes.
> - **Step 2 — project (Rust, streaming)**: `project(doc, selected_paths) ->
>   temp file`, streaming record-by-record, keeping only selected paths.
>   Progress bar ("Building projection…"); cancellable.
> - **Renderer**: open the projected file in a new tab; toast "kept N field(s)".
> - **Cancellation/cleanup**: cancelling stops the Rust stream promptly and
>   removes the partial temp file; progress modal always closes.
> - **Tests**: Rust — `schema_field_tree`, `all_paths`, `project_value`
>   (array-transparent) against fixtures.

---

## 4. Generate JSON Schema → New Tab

> Infer a **draft-07 JSON Schema** from the active document into a new tab.
>
> - **Applies to**: JSON / NDJSON.
> - **Rust core**: `infer_schema(doc, sample_limit)` — infer types, `properties`,
>   `required` (keys in all sampled objects), `items` (merged element schema),
>   unions for mixed types; **sample** large arrays rather than reading every
>   element. Stream/emit progress for large docs.
> - **Output**: pretty schema JSON in a new `oxj_` tab titled `data.json ·
>   schema`.
> - **Edge cases**: empty arrays/objects, heterogeneous arrays, deep nesting,
>   null vs missing keys.
> - **Tests**: Rust — `infer_schema(value)` → expected schema for fixtures.

---

## 5. Validate Against JSON Schema…

> Validate the active document against a **user-selected JSON Schema file** and
> report pass/fail.
>
> - **Applies to**: JSON / NDJSON.
> - **UI**: native open dialog (`dialog.showOpenDialog` in main) for the schema;
>   then an HTML **report modal** — "✓ Valid" or a scrollable list of failures,
>   each with **path**, rule, and message; a **Save…** (txt) button; cap the
>   displayed list with a "+N more" note.
> - **Impl**: validate in Rust (`jsonschema` crate) or in main (`ajv`) —
>   prefer Rust for large docs; collect **all** errors; run off the UI thread
>   with progress.
> - **Edge cases**: unparseable schema (report clearly), `$ref`, array-root vs
>   object-root docs, huge error counts.
> - **Tests**: `validate(value, schema) -> Vec<(path, message)>`.

---

## 6. Compare With Open Tab…

> **Structurally diff** the active document against **another open tab**; show a
> styled, exportable report.
>
> - **Applies to**: enabled only when **≥ 2 documents** are open.
> - **UI — pick target**: HTML modal listing other open tabs (disambiguate
>   duplicate names with paths).
> - **Diff (Rust core, pure)**: `diff(a, b) -> Vec<Change>` where
>   `Change = { kind: Added|Removed|Changed, path, left, right }`. Arrays are
>   compared **position-based** (element *i* vs *i*; tail = added/removed);
>   objects **key-based**; a JSON-type change counts as `Changed`. Paths use the
>   `$.a[0].b` grammar. Runs off the UI thread for large inputs.
> - **UI — report (native HTML, no WebEngine limits)**: a light report with a
>   header (both filenames + timestamp), summary cards (Total/Added/Removed/
>   Changed), a "by type" breakdown, and a table whose **rows are tinted by
>   change kind** (green=added, rose=removed, blue=changed) with dark text on a
>   white page. Full-width, wrapping cells, sticky header, virtualized rows for
>   large diffs.
> - **Export**: **HTML / TXT / JSON / CSV** via a "Save As" menu, plus **Open in
>   Browser** (`shell.openPath` on a temp `.html`). No PDF.
> - **Tests**: Rust — `diff`, `summarize`; JS/Rust — `to_json/csv/html_report`
>   (self-contained, HTML-escaped) against fixtures.

---

## Cross-cutting requirements (all items)

- **Security**: `contextIsolation` on, `nodeIntegration` off, `sandbox` on;
  the renderer never touches Node/FS directly — only the typed preload bridge.
  Validate all IPC inputs in main.
- **Threading**: nothing > ~50 ms on the renderer; use async napi /
  `worker_threads`; stream multi-GB; report progress + support cancel over IPC.
- **New-tab output** from `oxj_` temp files; source untouched; temp files
  tracked and cleaned.
- **Menus**: native `Menu` items enabled/disabled per active tab format; keep
  main's per-tab state in sync with the renderer.
- **Theming**: CSS variables, dark + light; all modals accessible.
- **Packaging**: `electron-builder` targets (dmg universal / nsis / AppImage),
  prebuilt Rust `.node` bundled, macOS signed + notarized.
- **Tests**: Rust `#[test]` for core logic; a JS test for pure helpers; app
  builds; suites pass.

---

## Stack notes (PySide6 → Electron mapping)

| Concern | PySide6 build | Electron + Rust build |
|---|---|---|
| Rust ↔ app | PyO3 (abi3) | **napi-rs** N-API addon (prebuilt `.node`) |
| Off-thread work | `QThreadPool` + `QRunnable` | async napi / `worker_threads` |
| Progress/streaming | Qt signals | napi **`ThreadsafeFunction`** → IPC events |
| UI toolkit | QtWidgets | HTML/CSS/TS renderer (Chromium) |
| Rich report | limited `QTextBrowser` | native HTML (no WebEngine restriction) |
| New tab output | temp file + `DocumentView` | temp file + renderer tab |
| Menus | `QMenu`/`QAction` | Electron `Menu`/`MenuItem` |
| Packaging | PyInstaller + hdiutil | `electron-builder` |
| Trade-off | ~50–70 MB app | larger (~150 MB, bundled Chromium) |
