# OPENXMLJSON — Complete Feature List

A cross-platform desktop viewer for very large JSON, NDJSON, XML, CSV and TSV files, built on a zero-copy memory-mapped Rust engine with a PySide6 interface. Each feature below is followed by two sentences describing what it does and why it matters.

---

## File & Input/Output

**Open File**
Opens JSON, NDJSON/JSON-Lines, XML, CSV and TSV files, with the parser chosen automatically from the file extension. Files are memory-mapped and indexed off the GUI thread, so even multi-gigabyte documents open without freezing the interface.

**Open URL**
Fetches a document directly over HTTP or HTTPS, with optional None / Basic / Bearer Token / API Key authentication. Credentials can be remembered for repeat requests, making it easy to inspect API responses without leaving the app.

**Open Clipboard**
Pastes JSON, XML or CSV text straight from the clipboard into a new tab with the format auto-detected. This is the fastest way to inspect a snippet copied from a log, a browser, or a colleague's message.

**Open Recent**
Keeps a running list of recently opened files, each shown with its human-readable size, and a "Clear Menu" option to reset it. It lets you jump back into the files you work with most without navigating the file system.

**Drag & Drop**
Files dropped onto the window open immediately as new tabs. It removes friction for quick, ad-hoc inspection of files sitting in a folder or on the desktop.

**Reload**
Re-reads the current file from disk on demand, and flags externally changed files with a dot on their tab. This keeps your view in sync with files that are still being written or edited elsewhere.

**Plain-Text Tabs**
Opens `.txt` and `.js` files in a read-only code view that bypasses the structured engine entirely. This means you can read supporting scripts and notes alongside your data in the same tool.

**Session Restore**
Remembers your open tabs and reopens them on the next launch, using lightweight placeholders for very large files. You can close the app and pick up exactly where you left off without re-hunting for files.

**Asynchronous Loading**
All parsing happens on a background thread while animated activity indicators show progress. The window stays fully responsive so you can keep working in other tabs while a large file indexes.

---

## View Modes

**Tree View**
The default view renders JSON objects/arrays, XML elements/attributes, and CSV records in one uniform, virtualized tree with collapsers, guide lines, zebra striping and color-coded value types. Because it only lays out the rows currently on screen, scrolling stays instant regardless of file size.

**CSV Table View**
Displays CSV and TSV files as a spreadsheet-style grid with header detection, available from the View menu or a toolbar toggle. Leading-zero fields such as ZIP codes are preserved as text rather than being coerced into numbers.

**XML Source View**
Shows the raw XML markup in a read-only pane, pretty-printing parsed markup and falling back to raw bytes for very large files. It lets you read the original document structure exactly as authored, not just the parsed tree.

**XML Syntax Highlighting**
Optionally colorizes the XML source view for tags, attributes and text, enabled for reasonably sized files. The highlighting makes dense markup far easier to scan and understand at a glance.

**Flow Diagram**
Renders the document as a JSONCrack-style graph of node cards connected by edges, for documents within a node-count limit. This turns nested data into a visual map that is often the quickest way to grasp an unfamiliar structure.

**Plain-Text / Code View**
Presents `.txt` and `.js` files in an editor with a line-number gutter, JavaScript syntax highlighting, and automatic beautification on open. It gives supporting code files a readable, IDE-like presentation without a separate editor.

**Full-Value Display**
Long values are never silently wrapped or truncated in the tree; the column extends and scrolls horizontally instead. This guarantees you always see complete field contents rather than a misleading preview.

---

## Search, Query & Filter

**Find**
A regex-or-literal search with case and regex toggles and a scope selector for All, Keys, Values or Attributes, powered by a parallel native search engine. Matches are chip-highlighted and steppable with next/previous, and the status bar reports "Match N of M."

**Plain-Text Find**
In text, `.js`, and XML source views, Find becomes an in-editor search with a live match count and wrap-around. This provides consistent search behavior no matter which view you are looking at.

**Table Find**
Search also works within the CSV grid, scanning across many thousands of rows. It lets you locate specific cell values in tabular data as easily as in the tree.

**Unified Filter Box**
A single filter input intelligently routes your query: plain text filters rows, `key:value` matches a field, and `$.path` or `//xpath` runs a structural query, hiding non-matching rows while keeping ancestors visible. This one control covers everything from casual filtering to precise path selection without switching modes.

**JSONPath & XPath Query Engine**
Supports JSONPath for JSON/CSV (wildcards, recursive descent, and predicate filters with comparison and existence tests) and XPath for XML (element paths, attributes, `text()`, wildcards), with multiple paths unionable via `|`. Traversal is capped for safety, so even broad queries on huge files stay responsive.

**jq Filter Bar**
A dedicated bar runs full jq programs through a native embedded jq engine, opening the transformed result in a new tab. It brings industry-standard JSON transformation directly into the viewer, with no external tooling required.

**Match Reveal**
After a search, the tree automatically expands to reveal the matching nodes up to a sensible limit. This means results are shown in context rather than as a disconnected list.

**Safety Guards**
Filters and expansions that would match an enormous number of rows are refused or confirmed first, while smaller result sets auto-expand. These guards keep the interface from stalling on pathological queries against very large documents.

---

## Navigation

**Jump to Path**
Navigates directly to a node by typing its path, using `$.a[0].b` syntax for JSON/CSV or `/root/item[2]/@attr` for XML. It is the fastest way to reach a known location deep inside a large document.

**Bookmarks**
Lets you pin nodes and jump back to them later, with bookmarks persisted across files and managed from a dedicated menu. This is invaluable when repeatedly comparing or referencing specific points in large datasets.

**Expand Children / Expand All / Collapse All**
Provides one-level expansion (safe on huge documents), full recursive expansion (tiered by node count with confirmation for very large trees), and a one-click collapse. These give you precise control over how much structure is revealed at once.

**Collapsed Counts & Index Labels**
Collapsed containers show their size (for example, "31 items") and array elements are labelled with their index. These cues help you understand shape and size without expanding everything.

**Type Badge & Path Readout**
The status bar shows the selected node's type and size (such as "Object · 63 keys") along with its full path. This constant context makes it easy to understand exactly where you are in the structure.

---

## Editing, Export & Clipboard

**Copy Row & Copy as cURL**
Copies the selected row, or reproduces the original Open URL request — including auth headers — as a ready-to-run cURL command. The cURL export is especially handy for sharing or scripting the exact API call you just inspected.

**Format-Aware Context Menu**
Right-clicking a node offers copy actions tailored to the format: names, values, paths, pretty JSON, raw tokens, XML markup, attributes, XPath, and CSV rows/cells. This means the right copy option is always one click away, whatever kind of data you are looking at.

**Copy to New Tab**
Opens the selected node's entire subtree in a new tab, as XML for XML elements and pretty JSON otherwise. It lets you isolate and work with a fragment of a large document as if it were its own file.

**Export Value As**
Exports any individual node to JSON or text (plus XML for elements and CSV for records) with timestamped default filenames. This is ideal for extracting just the piece of data you need from a much larger file.

**Whole-Document Export**
The Export menu offers Raw Copy, Pretty JSON, XML, and CSV of the entire document, plus selection exports. Because these run through cross-format converters, the same file can be saved out in whichever format you need next.

**Export Search Matches**
Saves all current search results as JSON or CSV, capturing each match's path and value, enabled only while a search has results. This turns a search into a reusable dataset of exactly the records you were looking for.

**Node Statistics**
Reports child counts, a type histogram, distinct values, and numeric min/max/average for the selected node. It provides a quick quantitative summary without exporting the data to another tool.

**Timestamped Exports**
Every export defaults to a filename stamped with the date and time. This prevents accidental overwrites and keeps successive exports naturally ordered.

---

## Conversion & Formatting

**Beautify & Minify**
Pretty-prints or compacts JSON and XML into a new tab. This makes minified payloads readable, or shrinks verbose documents for transport, without altering the original file.

**Format JavaScript**
Beautifies an open `.js` tab in place using a JavaScript formatter. It cleans up minified or messy scripts so they can be read comfortably.

**Cross-Format Converters**
Convert reconstructed data between JSON, XML and CSV, with sensible rules for nesting, attributes, and header unions. This lets the tool act as a lightweight converter as well as a viewer.

---

## Schema, Validation & Comparison

**Generate JSON Schema**
Infers a draft-07 JSON Schema from the document and opens it in a new tab, streaming over the native index so it works even on large files. It gives you a documented contract for your data in seconds.

**Validate Against JSON Schema**
Validates the document against a chosen schema file using a dependency-free validator that supports a broad range of draft-07 keywords, reporting each error with its path. This makes it easy to confirm that a file conforms to an expected structure and to pinpoint exactly where it does not.

**Compare With Open Tab**
Performs a structural diff against another open tab, listing added, removed and changed values by path with a summary count and a saveable report. It is a fast way to see precisely what changed between two versions of a document.

---

## Visualization (Flow Diagram)

**Card & Edge Graph**
Renders objects as cards, keys as rows, scalars inline and color-coded by type, with nested structures becoming their own connected cards. This visual layout makes complex relationships in the data immediately apparent.

**Tidy-Tree Layout & Rotation**
Lays the graph out as a tidy tree with parents centered over their children, and rotates the flow direction between top-down, left-right and their reverses. You can orient the diagram to whatever best fits your screen and mental model.

**Zoom, Pan & Gestures**
Supports fit-to-view, zoom in/out, 100/150/200% presets, a live zoom-percentage readout, mouse-wheel and trackpad zoom, and hand-drag and two-finger panning (including native macOS pinch and smart-zoom). Navigating large diagrams feels as fluid as a dedicated diagramming app.

**Diagram Export**
Exports the diagram as PNG, high-resolution JPG, or PDF with timestamped filenames. This lets you drop clear data-structure visuals straight into documentation, tickets, or presentations.

**Theming**
The diagram adapts to dark and light themes, with a dotted-grid background, color swatches for hex values, and expand markers on nested rows. These touches keep large diagrams legible and visually consistent with the rest of the app.

---

## Live Data

**Follow Tail (tail -f)**
Follows a growing JSON, NDJSON or log file, parsing only newly appended lines on each tick and auto-scrolling to the newest rows. It turns the viewer into a live monitor for files that are actively being written.

---

## Performance & Engine

**Zero-Copy Memory-Mapped Index**
The file is memory-mapped once and parsed into a flat array of compact fixed-size node records holding byte offsets, so memory use tracks the working set rather than the file size. This is what allows documents up to roughly ten gigabytes to be opened and browsed smoothly.

**On-Demand (Lazy) Indexing**
For files too large to fully index up front, only the root is built initially and children materialize as you expand them, while search still scans the whole file in parallel. This makes even the largest files openable almost instantly.

**Lazy Indexing Modes**
The indexing strategy can be set to Auto (switching to lazy above a memory threshold), Always, or Never. This gives power users direct control over the trade-off between up-front speed and full-index responsiveness.

**Parallel Native Search**
Search is executed in native Rust across multiple cores using chunked scanning with overlap. This delivers fast full-file searches that a single-threaded approach could not match on large data.

**Bounded, Recursion-Free Parsing**
Parsing is iterative with a strict nesting depth cap, avoiding stack overflows and denial-of-service from maliciously deep input. The result is predictable, safe handling of untrusted files.

**Per-Tab Independent Indexing**
Each tab owns its own memory-mapped index, and background tabs consume effectively no CPU. You can keep many large files open at once without them competing for resources.

---

## Interface & Appearance

**Multi-Tab Interface**
Supports up to twelve documents at once with drag-to-reorder tabs, right-click close options, and a changed-file indicator. It makes working across several related files a natural, browser-like experience.

**Appearance Themes**
Offers Dark, Light and System-following themes across the whole application. You can match the tool to your environment and reduce eye strain during long sessions.

**Font & Zoom Controls**
Lets you change the font and zoom in or out (via menu, shortcuts or Ctrl+wheel), with your preferences remembered between sessions. This keeps content readable on any display and to any personal taste.

**Welcome Screen**
The empty-state screen presents a central card with quick actions, format chips, a recent-files list with sizes, an edition badge, a "files served" statistics panel, and animated feature boxes. It provides an inviting, informative starting point every time you open the app with no file loaded.

**Welcome Screen Modes**
The welcome screen can be shown as the center card only, with static feature links, with animated links, or hidden entirely. This lets you choose between a lively landing screen and a minimal one.

**Activity Indicators**
Status-bar activity lights animate during operations and pulse when a followed file receives new data. They give clear, unobtrusive feedback that work is happening in the background.

---

## Updates & Editions

**Edition System**
The build ships in Free, Premium, or Netcore Unbxd editions, each with its own feature flags, on-screen label, and colored badge, all configured from a single place. This makes it straightforward to offer differentiated builds from one codebase.

**JSON Size Gate**
The Free edition caps JSON files at 100 MB (XML, CSV and TSV are unaffected) with an upgrade prompt, while Premium and Unbxd editions remove the cap. It cleanly separates the free tier from the paid tiers on the dimension that matters most for large-file work.

**Check for Updates**
Premium and Unbxd editions can check GitHub Releases for a newer version off the GUI thread and download the correct installer for the platform, including a quiet once-a-day startup check. This keeps users on the latest build without any manual version hunting.

**In-App Feature Reference & About**
A Help menu provides an in-app Features reference and an About dialog showing the current version. Users can discover capabilities and confirm their build without leaving the app.

---

## Platform Integration

**Native macOS Polish**
The app diverts the auto-injected Help search field, tidies stray menu icons, handles the system full-screen menu item, and supports native trackpad gestures in the diagram. These details make it feel like a first-class native application rather than a ported one.

**Standard OS Shortcuts**
Common actions use each platform's standard key sequences for Open, Close, Find, Copy and Zoom. This means the shortcuts you already know simply work.

**Packaged Installers**
The app is packaged into a native `.app` for macOS and `.exe` installer for Windows. Users get a normal double-click install with no runtime or dependencies to manage.

**Large-File Open Confirmation**
Before eagerly opening a very large file that could strain memory, the app asks for confirmation. This protects users from accidentally overwhelming their machine.
