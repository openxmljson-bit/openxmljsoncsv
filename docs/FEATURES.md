# OPENXMLJSON — Features

A fast viewer for very large JSON, XML and CSV files, built on a zero-copy memory-mapped structural index — files up to ~10 GB open and search without loading them into memory. Files too large to index eagerly are opened with on-demand (lazy) indexing, so even documents near or beyond available RAM open without exhausting memory.

## Opening documents

Every file is memory-mapped and parsed once into a compact index; only the rows on screen are ever built, so opening stays fast regardless of size.

- **Open File** _(Ctrl+O)_ — JSON, JSON-Lines/NDJSON, XML, CSV and TSV. The parser is chosen from the extension.
- **Open URL** _(Alt+Shift+O)_ — Fetch a document over HTTP(S) with optional auth (None / Basic / Bearer Token / API Key). Credentials can be remembered.
- **Open Clipboard** _(Ctrl+Shift+V)_ — Paste JSON/XML/CSV text straight into a new tab; the format is auto-detected.
- **Open Recent** — Quick access to recently opened files.
- **Drag & drop** — Drop files from the file manager onto the window to open them as tabs.
- **Reload** _(F5)_ — Re-read the current file from disk; changed files are flagged with a dot on the tab.

## Very large files (on-demand indexing)

When a file is big enough that a full index would strain memory, it is opened with **on-demand (lazy) indexing**: only the synthetic root exists at first, and each node's children are materialized the moment you expand them — so resident memory tracks the part of the document you actually look at, not the whole file. Search, filter and query still work: a whole-file parallel scan finds matches and only the paths to them are materialized. Available for JSON/NDJSON, CSV/TSV and XML.

- **Lazy Indexing** _(View ▸ Lazy Indexing)_ — Choose **Auto** (default: lazy only when a full, file-sized index would exceed ~half of system RAM), **Always** (lazy for every supported file), or **Never** (always build the full index).
- **What to expect** — The status bar shows “lazy index (loads on demand)”. Expand All is guarded (fully expanding a lazy document rebuilds the whole index). Lazy XML is lenient — it does not flag malformed markup the way the eager parser does, and only engages on files large enough to matter.

## Navigating the tree

Documents render as a uniform, virtualized tree — objects, arrays, elements and records all in one view.

- **Expand / Collapse All** _(Ctrl+Shift+E / Ctrl+Shift+C)_ — Open or close the whole tree (guarded on very large documents).
- **Index labels** — Array elements are labelled [0], [1], … like a code editor.
- **Collapsed counts** — A collapsed container shows its size, e.g. “products : [...], 31 items”.
- **Jump to Path** _(Ctrl+L)_ — Go straight to a node by path: $.a[0].b for JSON/CSV, /root/item[2]/@attr for XML.
- **Bookmarks** _(Ctrl+D)_ — Pin a node and jump back to it later, across files.
- **Type badge** — The status bar shows the selected node's type and size (“Object · 63 keys”).

## Searching, filtering and querying

Three complementary ways to find things — all backed by the parallel native search.

- **Find** _(Ctrl+F, F3 / Shift+F3)_ — Regex or literal search (“Aa” match case, “.*” regex), scoped to All / Keys / Values / Attributes; matched text is chip-highlighted and ▲ ▼ step through hits.
- **Filter box** — Hide every row except those that match (and their parents) — live as you type.
- **Query bar** _(Ctrl+Q)_ — JSONPath for JSON/CSV ($.store.items[*].name, $..price, [*], [-1]) and XPath for XML (//item/@id, /catalog/item[2]/title, text(), *). Combine several paths with '|' to select multiple fields at once ($.a.name | $.a.price). Results filter the tree.

## CSV tools

CSV/TSV files get a spreadsheet experience in addition to the tree.

- **Table view** — View CSV/TSV as a spreadsheet grid via View ▸ CSV Table View. CSV/TSV files open in the tree by default (like every other format); switch to the grid whenever you want it.
- **Header detection** — A text first row becomes column names; leading-zero cells (ZIP codes) stay strings.

## Live data

Watch data as it arrives.

- **Follow Tail (tail -f)** _(Ctrl+T)_ — Follow a growing JSON/NDJSON/log file: only newly appended lines are parsed and appended as rows, so it stays cheap even on huge files.

## Statistics and export

Understand and re-shape your data.

- **Node statistics** — Right-click any container for child counts, a type histogram, distinct values and numeric min/max/avg.
- **Export Data As** — Save the whole document as a raw copy, pretty JSON, XML or CSV (cross-format conversion).
- **Export search matches** — Write the current search's matches (path + value) to JSON or CSV.
- **Copy / Export node** — Right-click a node to copy its name, value, path, or pretty JSON (member name included), or export it — with format-specific options for JSON, XML and CSV.
- **Copy as cURL** — Reproduce an Open URL request, including auth headers, as a cURL command.

## Transform, validate and compare

Reshape and check documents from the **Tools** menu.

- **Beautify / Minify** _(Tools ▸ Beautify / Minify → New Tab)_ — Reformat the current JSON or XML document into a pretty-printed or compact form, opened in a new tab. (CSV/TSV has no meaningful beautify.)
- **Validate Against JSON Schema…** _(Tools)_ — Check the document against a JSON Schema file you pick. Supports the common draft-07 / 2020-12 keywords (type, enum/const, properties/required/additionalProperties/patternProperties, items/prefixItems, min/max on numbers/strings/arrays/objects, pattern, uniqueItems, allOf/anyOf/oneOf/not, and local `#/…` `$ref`). Reports each error with its path, or confirms the document is valid.
- **Compare With Open Tab…** _(Tools)_ — Structural diff of the current document against another open tab: added, removed and changed values, each with its `$.a[0].b` path, in a saveable report.

These tools reconstruct the whole document in memory, so they are limited to files up to 64 MB (open a large file and export/convert a selected node instead).

## Appearance and layout

Make it comfortable.

- **Tabs** _(Ctrl+W)_ — Open up to 12 documents at once; reorder by dragging; right-click for close others / left / right.
- **Appearance** — Dark, Light or System theme (View ▸ Appearance).
- **Font & zoom** _(Ctrl++ / Ctrl+- / Ctrl+0)_ — Change the font, zoom in/out (also Ctrl+wheel), or reset — remembered between sessions.
- **Long values** — Long text is never truncated; rows extend as wide as their content and the tree scrolls horizontally to reach it (fast, fixed-height rows at any file size).
- **Session restore** — Your open tabs reopen on the next launch.
