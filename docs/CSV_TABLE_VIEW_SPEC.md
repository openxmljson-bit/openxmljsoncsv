# CSV / TSV Table View — Specification

The spreadsheet-style view for tabular documents (CSV, TSV, and record-array
JSON that reconstructs to a list of rows). It presents the data in a scrollable
grid with a toolbar, a collapsible column panel, per-column tools, and
background-threaded export/analysis — all working on very large files.

---

## 1. Architecture

- **Model** — `RecordTableModel` wraps the engine's `DocumentModel` (the
  zero-copy, memory-mapped source). Rows are records; columns are the union of
  fields / CSV headers.
- **Proxy** — `RecordFilterProxy` sits between the model and the view and does
  **client-side filtering and sorting** (numeric-aware).
- **View** — a `QTableView`. The model/proxy live in a **Qt-free module**
  (`csvtable`) so they're headlessly unit-testable; the widget (`csvtableview`)
  adds the toolbar, panels, and dialogs.
- **Threading** — export, column coverage, and whole-file profile run as
  `QRunnable` workers on `QThreadPool` with `setAutoDelete(False)` (so
  completion signals survive delivery), each behind a styled, cancellable
  progress dialog (`_JobProgressDialog`). The grid never freezes.

## 2. Layout

```
┌──────────────────────────────────────────────────────────┐
│ [☰ Columns] [Filter…] [Sort…] [Profile] [Clear Filters]   │  toolbar
│                              [Export CSV] [Export JSON]    │
├───────────────┬──────────────────────────────────────────┤
│ Columns panel │  #  | col A | col B | col C | …            │
│ (collapsible) │  1  | …     | …     | …                    │  QTableView
│  search box   │  2  | …     | …     | …                    │
│  ☑ col A      │                                            │
│  ☑ col B      │                                            │
│  [Show][Hide] │                                            │
└───────────────┴──────────────────────────────────────────┘
```

## 3. Toolbar

| Button | Action |
|---|---|
| **☰ Columns** | Toggle the left Columns panel. |
| **Filter…** | Open the column-filter dialog (also on header right-click). |
| **Sort…** | Open the sort dialog. |
| **Profile** | Whole-file column profile → new tab. |
| **Clear Filters** | Remove all active column filters. |
| **Export CSV** | Export the current view to a new tab as CSV. |
| **Export JSON** | Export the current view to a new tab as JSON. |

**Export enablement:** the two Export buttons are enabled only when the
displayed table **differs from the original** — i.e. at least one column is
hidden **or** a filter is active — **and** there is ≥ 1 visible column and
≥ 1 row. Otherwise they're greyed out (exporting an unchanged table is a no-op).

## 4. Columns panel (collapsible, left)

- **Search box** ("Filter columns…") to find a column by name.
- **Checkbox list** of every column. Clicking **anywhere on a row** toggles the
  column's visibility (not just the small checkbox); a direct click on the
  checkbox keeps normal behavior. Checkbox indicators are custom-rendered PNGs
  so they're clearly visible under the Fusion style.
- **Show All / Hide All** buttons.
- **✕** collapses the panel.
- Toggling a column shows/hides it live in the grid and re-syncs export
  enablement.

## 5. Header right-click context menu (per column)

- **Filter "col"…** — add a filter on that column.
- **Clear this filter** — shown only if the column already has a filter.
- **Column coverage "col"…** — distinct-value analysis (§8).
- **Pin to Left** — move the column to the front and freeze its position.
- **Hide Column** — hide just this column.
- **Show All Columns** — restore visibility of all columns.

## 6. Sorting

- **Explicit only**, via the Sort… dialog (pick column + ascending/descending).
  Click-to-sort on the header is intentionally **disabled**
  (`setDynamicSortFilter(False)`) so a huge table doesn't re-sort on every stray
  header click.
- Sorting is **numeric-aware**: numeric columns sort by value, not lexically.

## 7. Filtering

- Per-column filters added via the Filter dialog or the header menu.
- Applied **client-side** by `RecordFilterProxy` using `FILTER_OPS`
  (contains / equals / starts-with / … ).
- Multiple column filters combine with **AND**; "Clear Filters" resets them all.
- The filtered row set is what Export and Coverage operate on.

## 8. Column coverage (per column → new tab)

Tally the distinct values of one column across the current (filtered) rows:
- Output columns: **value**, **count**, **cumulative_%**, and a colored
  **share** bar (Claude-orange `#D97757`, drawn by a delegate) so it reads as a
  mini bar chart.
- **Options dialog**: case-insensitive grouping, trim whitespace, and top-N
  limiting.
- The status bar shows numeric stats (min/max/mean where the column is numeric)
  plus a uniqueness summary.
- Result opens as a **new CSV tab**; runs on a background worker with progress.

## 9. Whole-file Profile (→ new tab)

One row per column describing the whole file:
- **column, distinct, non_empty, empty, fill_%, top_value, top_%**.
- Opens as a **new CSV tab**; background worker + progress.

## 10. Export (→ new tab)

- Export CSV / Export JSON write the **visible columns and filtered rows**
  (exactly what's on screen) to a temp file (`oxj_` prefix) opened in a new tab.
- Streamed on a background `_CsvExportTask` with progress + cancel; nested
  values become compact JSON in CSV cells.

## 11. Cell copy

- **Ctrl+C** copies the selected cell block to the clipboard (tab/newline
  separated), wired as a widget-level shortcut via an event filter so it only
  fires while the table is the active view.

## 12. Styling

- Column headers and the row-number column use grey variants; toolbar buttons
  have distinct grey/accent tints; selection text stays legible in dark and
  light; the coverage share bar uses the orange delegate.
- All colors come from the active theme and re-apply on theme switch via
  `apply_style()`.

## 13. Performance & testing

- Big operations (export/coverage/profile) are off the UI thread with
  cancellable progress; the grid stays responsive on multi-hundred-MB files.
- Sorting/filtering are explicit and client-side over the proxy.
- The Qt-free `csvtable` model/proxy are covered by headless unit tests.
