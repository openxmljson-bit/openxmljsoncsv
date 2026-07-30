# Build Prompt — CSV / TSV Table View (Electron + Rust)

Implementation-ready prompt to build the spreadsheet-style tabular view on an
**Electron (TypeScript renderer) + Rust (napi-rs)** stack. Prepend the Shared
Context from `TOOLS_BUILD_PROMPTS_ELECTRON.md`; the extra context below is
specific to the table view.

---

## Extra context

> The table view renders **tabular documents** — CSV, TSV, and record-array
> JSON (a top-level array of objects). The **Rust core** owns parsing, the row
> index, filtering, sorting, distinct-value tallies, profiling and export;
> the **renderer** owns the grid UI and dialogs. Because datasets can be
> millions of rows, the grid must be **virtualized** (render only visible rows)
> and pull row windows from Rust on demand over IPC — never materialize all
> rows in JS.

---

## Prompt

> Build a **virtualized table view** for tabular documents with the following.
>
> ### Data plumbing (Rust + IPC)
> - Rust exposes, over napi: `open_table(path) -> {columns, row_count}`;
>   `rows(view_id, offset, limit, {sort, filters, visible_cols}) -> rows[]`
>   (returns only the requested window); `distinct(view_id, col, opts)`;
>   `profile(view_id)`; `export(view_id, {format, visible_cols, filters, sort})
>   -> temp_path` (streamed, progress via ThreadsafeFunction).
> - **Filtering & sorting happen in Rust** over the memory-mapped index; the
>   renderer only sends the spec (sort column+dir, per-column filters, visible
>   columns) and requests row windows. Sorting is **numeric-aware**.
> - Cancellation tokens for export/coverage/profile; progress over IPC.
>
> ### Grid (renderer)
> - **Virtualized** rows/columns (e.g. a windowed grid); fixed header row and a
>   left row-number gutter. Smooth 60 fps scroll over millions of rows by
>   fetching windows from Rust.
> - Column **resize** and **reorder** (drag); **pin/freeze** a column to the
>   left; **hide/show** columns.
> - **Cell selection** with keyboard nav; **Ctrl/Cmd-C** copies the selected
>   block (tab/newline separated) to the clipboard.
>
> ### Toolbar
> `☰ Columns · Filter… · Sort… · Profile · Clear Filters · Export CSV ·
> Export JSON`.
> - **Export CSV/JSON** enabled only when the view differs from the original
>   (a column hidden **or** a filter active) **and** rows > 0 and ≥ 1 visible
>   column; greyed otherwise.
>
> ### Columns panel (collapsible, left)
> - Search box to filter column names; a **checkbox list** where clicking the
>   whole row toggles visibility; **Show All / Hide All**; **✕** to collapse.
> - Toggling updates the grid live and re-evaluates export enablement.
>
> ### Header context menu (right-click a column)
> - `Filter "col"…` (+ `Clear this filter` when present) · `Column coverage
>   "col"…` · `Pin to Left` · `Hide Column` · `Show All Columns`.
>
> ### Sorting
> - Explicit via a **Sort dialog** (column + asc/desc). **No click-to-sort** on
>   the header (perf); numeric-aware in Rust.
>
> ### Filtering
> - Per-column filters via a **Filter dialog** or the header menu; ops:
>   contains / equals / starts-with / not-equals / (numeric) >, <, between.
>   Multiple filters combine with AND; **Clear Filters** resets all. Applied in
>   Rust; the grid re-requests windows.
>
> ### Column coverage (→ new tab)
> - Distinct values of one column over the **filtered** rows, with columns
>   **value, count, cumulative_%**, and a colored **share bar** (orange
>   `#D97757`) rendered in the new table tab. **Options**: case-insensitive,
>   trim, top-N. Show numeric stats + uniqueness in a status line. Background +
>   progress; opens as a new tab.
>
> ### Whole-file Profile (→ new tab)
> - One row per column: **column, distinct, non_empty, empty, fill_%,
>   top_value, top_%**. Background + progress; opens as a new tab.
>
> ### Export (→ new tab)
> - Write the **visible columns + filtered rows** to a temp file (`oxj_`
>   prefix) via the streaming Rust exporter; open it in a new tab. Nested values
>   become compact JSON in CSV cells. Progress + cancel.
>
> ### Styling / theming
> - CSS variables for both themes; grey header + row-gutter variants; distinct
>   toolbar-button tints; legible selection; orange coverage bar; everything
>   re-themes live.
>
> ### Acceptance
> - Opens and scrolls a multi-million-row file smoothly (virtualized, windowed
>   fetch); filter/sort/export run in Rust off the UI thread with progress and
>   cancel; export enabled only when the view differs; all dialogs accessible
>   and legible in both themes.
> - Rust `#[test]`s cover filter/sort/distinct/profile/export logic; a renderer
>   test covers the toolbar enablement rules.

---

## PySide6 → Electron mapping (table view)

| Concern | PySide6 build | Electron + Rust build |
|---|---|---|
| Grid | `QTableView` + model/proxy | virtualized DOM grid, windowed fetch |
| Filter/sort | `RecordFilterProxy` (client) | **Rust over the mmap index** (server-side to the UI) |
| Big ops off-thread | `QThreadPool`/`QRunnable` | async napi + `ThreadsafeFunction` progress |
| Checkbox panel | `QListWidget` + custom indicators | HTML checkbox list |
| Header menu | `QMenu` on header | HTML context menu |
| New-tab output | temp file + `DocumentView` | temp file + renderer tab |
| Coverage bar | `QStyledItemDelegate` | CSS bar cell |
