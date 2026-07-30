# Build Prompts — Tools Menu (OPENXMLJSON)

Reusable, implementation-ready prompts for rebuilding each **Tools** menu
feature from scratch. Prepend the **Shared Context** to any single-feature
prompt so the agent has the architecture and constraints without repetition.

---

## Shared Context (prepend to every prompt)

> You are working on **OPENXMLJSON**, a cross-platform desktop viewer for very
> large JSON / XML / CSV / TSV / YAML files.
>
> **Stack & constraints**
> - GUI is **PySide6-Essentials only** — `QtCore`, `QtGui`, `QtWidgets`. Do
>   NOT use QtWebEngine, QtCharts, QtQuick/QML, or QtNetwork (they aren't
>   bundled). On macOS the app runs with the **Fusion** style.
> - The parsing engine is a **Rust zero-copy, memory-mapped** core exposed via
>   PyO3. A `DocumentModel` wraps it; `DocumentModel.reconstruct()` returns
>   plain Python values — `dict` / `list` / scalars — where an XML subtree is
>   represented as `{"tag", "attributes", "children"}`.
> - Documents open in **tabs** (`DocumentView`), max 20. Tool **output opens in
>   a new tab**, backed by a **temp file** written with prefix `oxj_` and
>   tracked so it's deleted on close / by "Free up temp files".
> - **All heavy work runs off the GUI thread**: `QThreadPool` + a `QRunnable`
>   task + a `QObject` signals holder. Call `task.setAutoDelete(False)` and keep
>   a reference so completion signals aren't dropped. Show a styled progress
>   dialog (`_JobProgressDialog`: title, mono subtitle/path, gradient bar,
>   status line, Cancel) and `QApplication.processEvents()` once before starting
>   the worker so it paints.
> - **Theming**: read colors from the active `Style`; every dialog must be
>   legible in both dark and light themes (no hard-coded black/white text).
> - **Enablement**: format-specific items are enabled/disabled per the active
>   document in `_sync_tools_controls()` (called on tab change).
> - **Size caps**: whole-document reformat/convert is capped at ~1 GB
>   (`PRETTY_EXPORT_LIMIT`); streaming features handle multi-GB.
> - **Never block the UI**, never crash on malformed input, and always leave the
>   source document untouched (tools produce new tabs, not in-place edits).
>
> Deliver: the feature code, wiring into the Tools menu, enable/disable rules,
> a headless (Qt-free) unit test for the pure logic, and confirmation it
> compiles and the test suite passes.

---

## 1. Beautify (Pretty-Print) → New Tab  /  Minify (Compact) → New Tab

> Add two Tools items that reformat the **whole** active document into a new
> tab: **Beautify** (indented, human-readable) and **Minify** (compact, no
> whitespace).
>
> - **Applies to**: JSON and XML only. Disable (grey out) for CSV/TSV/YAML,
>   plain-text tabs, and when no document is open. Wire this in
>   `_sync_tools_controls()`.
> - **Input**: `DocumentModel.reconstruct()` of the active document.
> - **Behavior**: pretty-print with 2-space indent (Beautify) or strip all
>   insignificant whitespace to one line (Minify). Preserve key order and
>   value fidelity. For XML, keep tags/attributes; pretty = indented elements,
>   minify = no inter-tag whitespace.
> - **Output**: write the formatted text to an `oxj_` temp file with the right
>   extension and open it in a **new tab**; title like `data.json · beautified`.
> - **Size**: refuse files over ~1 GB reconstructed with a friendly message
>   ("too large to reformat in one pass").
> - **Threading**: reconstruct + serialize on a background worker with the
>   progress dialog; large documents must not freeze the UI.
> - **Edge cases**: empty document; deeply nested (guard recursion); non-UTF8;
>   already-minified input.
> - **Tests**: pure formatter functions (given a Python value → expected
>   pretty/minified string) with round-trip checks.

---

## 2. Format JavaScript

> Add a Tools item that beautifies the **active `.js` plain-text tab**.
>
> - **Applies to**: only when the current tab is a JavaScript (`.js`) file
>   opened in the plain-text viewer. Disable otherwise.
> - **Behavior**: pretty-print the JS source (indentation, spacing, braces)
>   using `jsbeautifier` (already a dependency; import lazily). Replace the tab
>   contents in place (this is a text file, not a structured doc) OR open a new
>   tab — match the existing convention (in-place for the text view).
> - **Cap**: skip very large files (e.g., > 32 MB) with a message.
> - **Edge cases**: minified bundles (one long line), syntax that isn't valid
>   JS (best-effort; never crash), non-UTF8.
> - **Tests**: formatter wrapper returns expected output for a small snippet.

---

## 3. Deep Dive (select fields) → New Tab…

> Add a Tools item that lets the user **pick a subset of fields** from the
> document's inferred structure and extract a **projected (slimmed) copy** into
> a new tab — designed to work on **multi-GB array documents** via streaming.
>
> - **Applies to**: JSON documents (and NDJSON). Disable for others.
> - **Step 1 — infer schema (background)**: scan the document to build a
>   **field tree** of all paths (array-transparent: `items[].price` collapses
>   the array index), off the GUI thread, with the progress dialog
>   ("Scanning fields…"). Fully cancellable.
> - **UI — field picker dialog**: a **tree of fields with checkboxes**; the
>   whole row is clickable to toggle; parent toggles cascade to children; the
>   tree is **fully expanded** by default. Include OK / Cancel. Selection text
>   must stay readable when a row is highlighted (use theme selection colors).
>   Checkbox indicators must render on Fusion (use rendered PNG indicators if
>   native ones are faint).
> - **Step 2 — project (background)**: stream the document record-by-record,
>   keeping only the selected paths, writing the projected JSON to an `oxj_`
>   temp file; show a large, styled progress bar ("Building projection…").
>   Never load the whole doc into memory for large arrays.
> - **Output**: open the projected file in a new tab; status "kept N field(s)".
> - **Progress dialogs must actually close** on completion/cancel (accept()
>   on success; reject() emits cancel then closes) — verify no lingering blank
>   dialogs.
> - **Tests**: pure functions — `schema_field_tree(value)`, `all_paths(value)`,
>   `project_value(value, selected_paths)` (array-transparent) with fixtures.

---

## 4. Generate JSON Schema → New Tab

> Add a Tools item that **infers a JSON Schema (draft-07)** from the active
> document and opens it in a new tab.
>
> - **Applies to**: JSON / NDJSON. Disable otherwise.
> - **Behavior**: infer types (object/array/string/number/integer/boolean/
>   null), `properties`, `required` (keys present in all sampled objects),
>   `items` for arrays (merged element schema), and merge unions where types
>   vary. Sample large arrays rather than reading every element (configurable
>   sample size) so it scales.
> - **Output**: pretty JSON Schema text in a new `oxj_` `.json` tab titled
>   `data.json · schema`.
> - **Threading**: infer on a background worker with progress for large docs.
> - **Edge cases**: empty arrays/objects, heterogeneous arrays, deeply nested,
>   nulls vs missing keys.
> - **Tests**: `infer_schema(value)` → expected schema dict for fixtures
>   (scalar, object, array-of-objects, mixed types).

---

## 5. Validate Against JSON Schema…

> Add a Tools item that **validates the active document against a JSON Schema
> file** the user selects, and shows a pass/fail report.
>
> - **Applies to**: JSON / NDJSON. Disable otherwise.
> - **UI**: a file-open dialog to choose the `.json` schema; then a **read-only
>   report dialog** (theme-styled, scrollable) showing either "✓ Valid" or a
>   list of failures — each with the failing **path**, the rule violated, and a
>   short message. Include a Save… button (txt).
> - **Behavior**: validate with a draft-07 validator; collect *all* errors (not
>   just the first). For very large docs, validate on a background worker with
>   progress.
> - **Edge cases**: invalid/unparseable schema file (report clearly), schema
>   with `$ref`, document that's an array vs object, huge error counts (cap the
>   displayed list, note the total).
> - **Tests**: pure `validate(value, schema)` → list of `(path, message)`.

---

## 6. Compare With Open Tab…

> Add a Tools item that **structurally diffs** the active document against
> **another open tab** and shows a styled, exportable report.
>
> - **Applies to**: enabled only when **≥ 2 documents** are open. Disable
>   otherwise.
> - **UI — pick target**: a dropdown/list of the other open tabs (disambiguate
>   identical filenames with their path).
> - **Diff logic (pure, Qt-free)**: position-based for arrays (element *i* vs
>   element *i*; extra tail elements = added/removed), key-based for objects;
>   a type change (e.g. number→string, object→array) counts as "changed".
>   Emit `(kind, path, left, right)` where kind ∈ added/removed/changed and
>   paths use the `$.a[0].b` grammar.
> - **UI — report**: a **light, self-contained HTML report** rendered in a
>   `QTextBrowser` (wide dialog ~1240px, 100% width, wrapping cells, no
>   horizontal scrollbar): a header with both filenames + timestamp, summary
>   cards (Total / Added / Removed / Changed), a "by type" breakdown, and a
>   table whose **rows are softly tinted by change kind** (green=added,
>   rose=removed, blue=changed) with dark text — not colored text. White page
>   background.
> - **Export**: "Save As ▾" → **HTML / TXT / JSON / CSV**, plus **Open in
>   Browser** (writes a temp `.html` and opens the system browser for full CSS
>   fidelity). No PDF.
> - **Threading**: reconstruct both docs + diff on a background worker for large
>   inputs.
> - **Tests**: `diff(a, b)`, `summarize`, `to_json_report`, `to_csv_report`,
>   `to_html_report` (self-contained, HTML-escaped) against fixtures.

---

## General acceptance criteria (all items)

- Menu item enabled exactly when applicable; greyed otherwise.
- Output opens in a new tab from an `oxj_` temp file (except in-place text
  formatting), leaving the source untouched.
- Long operations run off the GUI thread with a styled, cancellable progress
  dialog that reliably closes.
- Works (or degrades gracefully) on multi-GB inputs; refuses politely past
  hard caps.
- Legible in dark and light themes.
- Pure logic covered by headless unit tests; app compiles; suite passes.
