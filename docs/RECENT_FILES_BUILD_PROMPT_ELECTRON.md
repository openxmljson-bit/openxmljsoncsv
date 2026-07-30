# Build Prompt — Recent Files (Electron + Rust)

Implementation-ready prompt for the **Recent Files** feature across all three
surfaces — the **menu bar**, the **welcome center box**, and the **side
panel** — on an Electron (TypeScript) + Rust (napi-rs) stack. Prepend the
Shared Context from `TOOLS_BUILD_PROMPTS_ELECTRON.md`.

---

## Extra context

> "Recents" is **one persisted list** that is the single source of truth for
> three UI surfaces: the native **Open Recent** menu, the **welcome card**
> recent list, and a toggleable **side panel** dock. The list lives in the
> **main process** (persisted to `app.getPath('userData')/recent.json`, e.g.
> via a tiny store). The renderer never reads it directly — it asks main over
> the typed preload bridge and re-renders on a `recents:changed` event. File
> `stat` (size, existence) and "reveal in folder" are main-process Node
> (`fs`, `shell.showItemInFolder`); the Rust core isn't needed for the list
> itself.

---

## Prompt

> Build a **Recent Files** subsystem with a single shared model and three
> synchronized views.
>
> ### Model & persistence (main process)
> - A list of absolute file paths, **most-recent-first**, **deduped by path**,
>   **capped at `MAX_RECENT = 15`**.
> - `rememberRecent(path)`: prepend, drop any existing duplicate, truncate to
>   15. **Never remember** temp/scratch files (paths with the `oxj_` prefix or
>   inside the temp dir) or unsaved buffers (clipboard/converted results).
> - `recentList()`: return the stored list (filtered of temp paths defensively).
> - `pruneRecent()`: at **app startup**, drop entries whose file no longer
>   exists (deleted/moved); persist the cleaned list. Only remove when a path is
>   confirmed missing (don't drop on transient errors).
> - `clearRecent()`: empty the list.
> - On any change (`remember` / `clear` / `prune` / `open`), persist and emit
>   **`recents:changed`** to all windows so every surface re-renders.
> - IPC (invoke): `recents:list`, `recents:clear`, `recents:reveal(path)`,
>   `recents:open(path)`. Each returns typed data; validate inputs in main.
> - Provide `stat(path) -> {exists, size}` for the UIs (human-readable size
>   formatting done in the renderer).
>
> ### Opening behavior (dedup)
> - Opening a recent must **switch to the existing tab** if that file is already
>   open, instead of opening a duplicate. (For formats opened via a converted
>   temp file — e.g. YAML → JSON — match on the recorded original path, not the
>   temp path.)
> - After a successful open, `rememberRecent` moves it to the top and all
>   surfaces refresh.
>
> ### Shared formatting helpers (renderer)
> - `humanSize(bytes)`: `GB` / `MB` / `KB` / `B`, one decimal for GB/MB, shown
>   with a leading `~` (approximate). Color it **orange (`#D97757`), bold**.
> - `middleEllipsis(name, limit)`: shorten long filenames with a middle `…`
>   **keeping the start and the extension** (e.g.
>   `very_long_export_name.json` → `very_long_e…rt_name.json`).
>
> ### Surface 1 — native "Open Recent" menu (File menu)
> - A submenu listing recent files by **basename**, newest first.
> - A separator + **"Clear Menu"** item that calls `recents:clear`.
> - Rebuilt from the model whenever `recents:changed` fires. (Optionally also
>   call `app.addRecentDocument` so the macOS Dock/Jump List shows them, but the
>   in-app submenu is driven by our own list.)
>
> ### Surface 2 — welcome center box (recent list)
> - Heading **"Recent (N)"** where N is the count; hidden entirely when empty.
> - Up to 15 rows; each row = middle-ellipsized filename (keep extension) + the
>   orange `~size`; full path as a tooltip.
> - Clicking a row opens the file (dedup rule applies). Rows are compact so all
>   15 fit without overflowing the card.
> - Re-render on `recents:changed`; does **not** rebuild on unrelated events
>   (e.g. license activation).
>
> ### Surface 3 — side panel (dock)
> - Toggled by a **"Recent"** button that is only reachable while a document is
>   open; the panel auto-hides when the last tab closes and shows a blank state.
> - **Type-grouped, collapsible sections** with counts: `JSON (n)`, `XML`,
>   `CSV`, `YAML`, `Logs`, `Code`, `Text`, `Other` — grouped by extension,
>   recency order preserved within each group; sections expanded by default,
>   header click toggles.
> - Each file row: middle-ellipsized name (keep extension) + `~size`, full path
>   tooltip, and a **reveal-in-folder** button (blue folder icon) →
>   `shell.showItemInFolder(path)`.
> - **No persistent selection** — rows behave like links (hover highlight only,
>   no sticky blue selection).
> - **Empty-state** row ("No recent files") when the list is empty (e.g. right
>   after Clear).
> - Themed (CSS variables, dark + light); the dock title "Recent Files" is
>   always legible; the panel has no float/undock/close chrome — it's opened and
>   closed solely by the toggle.
>
> ### Acceptance
> - One model drives all three surfaces; any change reflects everywhere
>   immediately via `recents:changed`.
> - Cap = 15; dedup by path; temp/unsaved files never recorded.
> - Missing files pruned at startup; "Clear Menu" empties all surfaces at once.
> - Opening an already-open file focuses its tab (no duplicate), including for
>   converted-format tabs.
> - Long names middle-ellipsized with the extension kept; sizes shown as orange
>   `~value`; reveal-in-folder works on macOS/Windows/Linux.
> - Side panel groups by type, collapses, has no sticky selection, and shows an
>   empty state.
> - Unit tests for the pure logic (remember/dedup/cap, prune, group-by-type,
>   middleEllipsis, humanSize).

---

## PySide6 → Electron mapping (recents)

| Concern | PySide6 build | Electron + Rust build |
|---|---|---|
| Storage | `QSettings` "recent" | `recent.json` in `userData` (main) + IPC |
| Source of truth | `_recent_list()` on the window | main-process model + `recents:changed` |
| Menu | `QMenu` "Open Recent" + Clear | Electron `Menu` submenu (rebuilt on change) |
| Center box | welcome card rows | HTML list in the renderer |
| Side panel | `QDockWidget` + `QTreeWidget` groups | HTML collapsible sections |
| Reveal in folder | `open -R` / `explorer /select` / xdg | `shell.showItemInFolder` |
| Prune missing | `_prune_recent()` at startup | `pruneRecent()` at startup |
| Size / ellipsis | `_human_size` / `_middle_ellipsis` | `humanSize` / `middleEllipsis` helpers |
| Dedup on open | `open_path` switches tabs | tab manager focuses existing tab |
