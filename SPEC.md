# OPENXMLJSON — Complete Technical Specification

Version 0.1.0 · Status: living document · Applies to the code in this repository.

---

## 1. Overview

OPENXMLJSON is a cross-platform (macOS + Windows) desktop application for
viewing, navigating, and searching very large **JSON**, **XML**, and **CSV**
documents — files up to roughly 10 GB — without loading them into a conventional
in-memory object graph.

It is built as a **hybrid**: a Python/Qt application shell wrapping a Rust
parsing/search engine that is compiled to a native Python module. The engine can
also be driven from a pure-Rust GUI with no Python at all.

The central idea is a **zero-copy, memory-mapped structural index**: the file is
memory-mapped, parsed once into a flat array of fixed-size node records that hold
byte offsets rather than copied data, and every subsequent operation (tree
navigation, display, search, export) works on those offsets. This is what makes
1:1 file-to-RAM behavior and interactive performance on multi-gigabyte files
possible.

---

## 2. Goals and non-goals

### 2.1 Goals

The application opens JSON (including JSON-Lines/NDJSON), XML, and CSV/TSV files
and presents them as a single, uniform, navigable tree. It supports regular
expression search across the whole document or scoped to keys, values, tag names,
or attributes. It keeps memory consumption proportional to the file's working set
rather than its full size, and it stays responsive (no full-document freezes,
no modal progress bars) while opening and searching large files. It installs as a
normal desktop application on macOS and Windows.

### 2.2 Non-goals (current version)

The tool does not edit or write back documents in place (export is a separate,
planned capability). It does not validate documents against schemas (JSON Schema,
XSD, DTD). It does not resolve XML namespaces into fully-qualified names
(prefixes are preserved verbatim). It does not decode XML character/entity
references or JSON `\u` escapes into their target characters except for on-screen
display. It is not a query language engine (no JSONPath/XPath/SQL) in this
version, though the index is designed to support one later.

### 2.3 Performance positioning (honest statement)

The original design brief targeted ~2 GB/s parsing, 130M objects/second, and
seamless 10 GB handling. Those figures are achievable only with native,
SIMD-aware code; the Rust engine is designed for that tier. The Python UI layer
does not participate in the hot path — it only requests the handful of tree rows
on screen — so the end-to-end experience is governed by the native engine, not by
Python. Where a specific number cannot be guaranteed on all hardware, this spec
states the design intent and the mechanism rather than a marketing figure.

---

## 3. Requirements

### 3.1 Functional requirements

| ID | Requirement |
|----|-------------|
| F1 | Open and parse JSON, JSON-Lines, and NDJSON files. |
| F2 | Open and parse well-formed XML files (elements, attributes, text, CDATA). |
| F3 | Open and parse CSV and TSV files with quoting and delimiter detection. |
| F4 | Present any opened file as a single virtualized, expandable tree. |
| F5 | Regex search over the document, scoped to All / Keys / Values / Attributes. |
| F6 | Highlight or list search matches without blocking the UI. |
| F7 | Report file size, node count, index size, and detected format. |
| F8 | Choose the parser automatically from the file extension. |
| F9 | Reject malformed input with a byte-offset error message. |

### 3.2 Non-functional requirements

| ID | Requirement |
|----|-------------|
| N1 | Memory proportional to working set, not file size (memory-mapped I/O). |
| N2 | Structural index cost bounded and predictable: exactly 24 bytes per node. |
| N3 | No stack recursion proportional to document nesting depth. |
| N4 | UI renders only visible rows; frame cost independent of document size. |
| N5 | Search parallelized across CPU cores. |
| N6 | Installable, signed/notarizable on macOS; installable on Windows. |
| N7 | Single engine codebase reused by both the Python app and the Rust GUI. |

---

## 4. System architecture

```
                    ┌─────────────────────────────────────────────┐
                    │  Desktop UI (choose one front-end)            │
                    │                                               │
                    │  A) Python + PySide6/Qt   B) Rust + egui       │
                    │     python/openxmljson       crates/oxj-gui    │
                    │     QAbstractItemModel       immediate-mode    │
                    └───────────────▲───────────────────▲───────────┘
                                    │ lazy row API        │ direct calls
                    PyO3 (oxj-py) → │ openxmljson._native │
                                    │                     │
                    ┌───────────────┴─────────────────────┴──────────┐
                    │              Engine — crates/oxj-core            │
                    │                                                  │
                    │  mapping.rs   mmap (Arc<Mmap>), zero-copy slices │
                    │  index.rs     Node (24 B) + Index + IndexBuilder │
                    │  json.rs ─┐                                      │
                    │  xml.rs  ─┼─▶ build one unified structural Index │
                    │  csv.rs ─┘                                       │
                    │  model.rs     virtualization + display + drill   │
                    │  search.rs    rayon + regex, scope filters       │
                    └──────────────────────┬───────────────────────────┘
                                           │ mmap
                                    ┌──────┴───────┐
                                    │ File ≤ ~10 GB │
                                    └──────────────┘
```

The engine is a plain Rust library crate (`oxj-core`) with no UI or Python
dependencies. `oxj-py` adds PyO3 bindings that expose it as the native module
`openxmljson._native`. `oxj-gui` is an optional pure-Rust front-end. The two
front-ends never touch file bytes except through the engine's zero-copy slices.

### 4.1 Threading model

The file is mapped once and shared as `Arc<Mapping>`. Parsing is a single linear
pass (it can run on a worker thread while the UI shows the already-built prefix).
Search fans out across a rayon thread pool over independent byte/index ranges and
returns results to the caller; the Python binding returns matching node ids, the
Rust API can stream `(offset, len)` matches through a channel. The UI thread only
ever builds widgets for on-screen rows.

---

## 5. Core data model

### 5.1 `NodeKind`

A one-byte tag shared by all three formats (stable discriminants; the Python
binding exposes the name↔code map via `node_kind_names()`):

| Code | Kind | Meaning |
|-----:|------|---------|
| 0 | `Document` | synthetic root owning all top-level values |
| 1 | `Object` | JSON object |
| 2 | `Array` | JSON array |
| 3 | `Key` | object member name; owns its value as its single child |
| 4 | `String` | JSON/CSV string, XML attribute value |
| 5 | `Number` | numeric scalar |
| 6 | `Bool` | `true` / `false` |
| 7 | `Null` | `null` |
| 8 | `ElementOpen` | XML element; window = tag name; owns attrs/text/children |
| 9 | `ElementClose` | reserved |
| 10 | `ElementSelfClose` | reserved |
| 11 | `Text` | XML character data |
| 12 | `Attribute` | XML attribute name; owns its value string as child |
| 13 | `CData` | XML `<![CDATA[…]]>` inner content |
| 14 | `Comment` | reserved |

`is_container()` is true for `Document`, `Object`, `Array`, `Key`, `ElementOpen`.

### 5.2 `Node` (exactly 24 bytes)

```rust
#[repr(C)]
struct Node {
    len: u32,           // byte length of the raw token window
    parent: u32,        // parent node index, or NIL (u32::MAX)
    first_child: u32,   // first child index, or NIL
    next_sibling: u32,  // next sibling index, or NIL
    offset_lo: u32,     // low 32 bits of the absolute byte offset
    kind: NodeKind,     // 1 byte
    offset_hi: u8,      // high 8 bits of the offset (offset = hi<<32 | lo)
}
// compile-time assert: size_of::<Node>() == 24
```

Rationale and consequences:

- No node owns heap data; a value is read by slicing the mapping at
  `offset .. offset+len` (offset = `offset_hi << 32 | offset_lo`). Strings are
  never copied until a visible row is drawn.
- The offset is split into a 32-bit low word + 8-bit high word so the struct's
  alignment drops from 8 (a `u64` field) to 4 and the trailing padding
  disappears — 24 bytes instead of 32, a 25% smaller index.
- The split offset supports files up to 2^40 (1 TiB), far beyond the ~10 GB
  target. `len` is `u32`, capping a single token at 4 GiB (a pathological case
  the parsers flag rather than truncate).
- Children form a singly linked list via `first_child`/`next_sibling`, built in
  document order, so a parse is a single forward pass.
- Node indices are `u32`, capping one document at ~4.29 billion nodes — far above
  the 130M design point — while keeping links at 4 bytes.
- Memory: the index costs `24 × node_count` (e.g. ~3.5 GB for 145M nodes). This
  is deliberately separate from the 1:1 file mapping and is documented as the
  bounded cost of random-access navigation.

### 5.3 `Index` and `IndexBuilder`

`Index` wraps `Vec<Node>`; node 0 is always the `Document` root. It offers
`root()`, `node(i)`, `children(parent)` (iterator), and `child_count(parent)`.

`IndexBuilder` builds the index during one pass. It keeps a stack of open
containers, each with its last-appended child, so linking a new child is O(1).
Its operations are `open`/`close` (containers whose byte span is `end − start`),
`open_fixed`/`pop` (containers whose length is known up front and must be
preserved — used for `Key`, `Attribute`, and `ElementOpen`, whose window is just
the name), and `leaf` (scalars/text). A `depth()` accessor lets parsers cap
nesting.

---

## 6. Memory-mapped I/O

`Mapping` opens a file read-only and maps it with `memmap2`, wrapping it in an
`Arc` so the parser, every search worker, and the UI share one mapping with no
copies. On Unix it advises sequential access for the initial parse. Slicing is
bounds-checked (`slice(offset, len) -> Option<&[u8]>`) so a corrupt index cannot
crash the process. The 1:1 RAM property comes from the OS: only the pages
actually touched are resident, and they are evicted under pressure.

---

## 7. Parsers

All three parsers share the builder and produce the same `Index`, so everything
downstream is format-agnostic. All are single-pass and allocation-free per token.

### 7.1 JSON (`json.rs`)

Accepts standard JSON plus JSON-Lines/NDJSON: the document root accepts one *or
more* whitespace-separated top-level values, so `{…}\n{…}\n` yields a root with
one `Object` child per line.

The parser is an **iterative state machine** over an explicit mode stack — no
recursion — so deeply nested input cannot overflow the call stack (tested to
2,000+ levels; hard cap `MAX_DEPTH = 4096`). Modes: `RootValues`, `ArrValue`,
`ArrValueOnly` (post-comma), `ArrComma`, `ObjKey`, `ObjKeyOnly` (post-comma),
`ObjComma`, `KeyColon`, `KeyValue`.

Tree mapping: an object member is a `Key` container (window = the `"key"` token
including quotes) that **owns its value as its single child**. This is what lets
the UI render `key: value` as one row and drill straight into container values.

Token scanning: strings scan to the closing quote honoring `\"` and `\\`;
numbers scan the JSON numeric grammar; `true`/`false`/`null` are matched
literally. A leading UTF-8 BOM is skipped.

Rejections (byte-offset error): trailing commas (`[1,2,]`, `{"a":1,}`),
unterminated containers/strings, missing colons, invalid literals, and stray
structural characters. Correctness is verified by a Python port cross-checked
against stdlib `json` on edge cases plus 5,000 fuzzed documents.

Not decoded (by design): `\u` escapes and other escape sequences are left raw in
the index and only interpreted for display.

### 7.2 XML (`xml.rs`)

Accepts well-formed XML. Tree mapping: `<Tag …>…</Tag>` becomes an `ElementOpen`
container whose window is just the tag name; its children are its attributes,
text, CDATA, and child elements. `name="value"` becomes an `Attribute` node
(window = name) owning a `String` value child (window = inner value, single or
double quoted). Character data becomes `Text`; `<![CDATA[…]]>` becomes `CData`.
Comments, processing instructions, and the doctype/declaration are recognized and
skipped. Self-closing `<Tag/>` yields an `ElementOpen` with no children.
Whitespace-only text between elements is dropped as layout noise.

Well-formedness: a close tag must match the element on top of the stack, else a
byte-offset error; unclosed elements at EOF are an error. Nesting is capped at
`MAX_DEPTH`. A BOM is skipped.

Not done (deferred, Prompt 9): namespace **resolution** — `xmlns:p="…"` prefixes
are kept verbatim as part of names; entity references are not decoded.

Correctness is verified by a Python port cross-checked against `xml.etree` on
edge/rejection cases plus 4,000 fuzzed documents.

### 7.3 CSV / TSV (`csv.rs`)

RFC-4180-style reader. **Delimiter** is auto-sniffed from the first line among
`, ; \t |` (override via `CsvOptions`; TSV preset forces tab). **Header
detection** is automatic: with ≥2 records, an all-text first row becomes the
column names; otherwise rows are treated as arrays of cells (overridable).

Tree mapping: with a header, each record is an `Object` and each cell is a `Key`
(window = the header cell's bytes, shared across rows) owning a typed value; so a
CSV renders like a JSON array-of-objects. Without a header, each record is an
`Array` of cells. **Typing**: an unquoted cell matching a strict numeric grammar
becomes `Number`; leading-zero values (e.g. ZIP codes) deliberately stay
`String`; quoted cells are always `String`.

Quoting: quoted fields may contain the delimiter and newlines; `""` is an escaped
quote. Field windows stay zero-copy (the outer quotes are excluded; embedded `""`
remains literal and is unescaped only for on-screen rows). Line endings `\n` and
`\r\n` are both handled; blank lines at record boundaries are skipped. **Ragged
rows**: short rows yield fewer keys; extra columns beyond the header become
unlabeled value rows — no data is dropped.

Correctness is verified by a Python port cross-checked against stdlib `csv` on
edge cases plus 5,000 fuzzed tables.

---

## 8. Tree / virtualization layer (`model.rs`)

The model presents the index as a display tree, independent of format, and is the
only surface the UI needs.

**Drilling.** The display tree "drills through" `Key` and `Attribute` nodes: the
children revealed when a `Key`/`Attribute` row is expanded are the children of its
*value* (for container values), not the intermediate value node. `container_for`
implements this: for `Key`/`Attribute` it returns the value if the value is a
container else `None`; for `Object`/`Array`/`Document`/`ElementOpen` it returns
the node itself. `is_expandable` is `container_for(node)` having any child.

**Virtualization.** `visible_row_count()` and `rows(start, len)` walk only visible
nodes (descending into expanded containers), so the total row count and any
viewport slice are computed without materializing the whole tree. The reference
implementation is O(start+len) visible nodes; a Fenwick/prefix-sum over expanded
subtrees can make random `rows(start,…)` O(log n + len) behind the same API.

**Display.** `display_text(node)` formats a row on demand (only for visible rows):
`key: value` or `key {n}` / `key [n]` for JSON members, `{n}`/`[n]` for bare
containers, `<tag>` for elements, `@name = "value"` for attributes, and raw
content for text/CDATA/scalars. Values are decoded via lossy UTF-8 only here.

---

## 9. Search engine (`search.rs` + binding)

Two complementary paths:

- **Raw parallel scan** (`search_parallel`, `search_streaming`) over the mapped
  bytes: the buffer is split into 8 MiB chunks with a 64 KiB overlap so a match
  straddling a boundary is found exactly once (a match is attributed to the chunk
  where it *starts*). Each chunk is scanned front-to-back by the `regex` crate
  (SIMD `memchr` prefilters), which is cache-friendly. Results are `(offset, len)`;
  the streaming variant delivers batches through a bounded channel so the UI can
  highlight incrementally with no progress bar.
- **Scoped scan** using the index: `SearchScope::{All, Keys, Values, Attributes}`
  restricts scanning to nodes of the relevant kind, skipping irrelevant bytes
  entirely. The Python binding's `Document.search(pattern, scope)` runs this in
  parallel over the node array and returns matching **node ids** (sorted), which
  the Qt model uses to highlight rows.

Scope kind mapping: Keys → `Key`, `ElementOpen`; Values → `String`, `Number`,
`Bool`, `Null`, `Text`, `CData`; Attributes → `Attribute`; All → every node
except the synthetic `Document`.

---

## 10. Public APIs

### 10.1 Rust engine (`oxj-core`)

```rust
Mapping::open(path) -> io::Result<Mapping>
Mapping::bytes(&self) -> &[u8]
Mapping::slice(&self, offset: u64, len: u32) -> Option<&[u8]>

parse(bytes) -> Result<Index, ParseError>              // JSON
parse_xml(bytes) -> Result<Index, XmlError>            // XML
parse_csv(bytes, CsvOptions) -> Index                  // CSV/TSV
parse_document(bytes, Format) -> Result<Index, String> // unified entry point
Format::from_path(path) -> Format                      // by extension

Index::{root, node, children, child_count, len}
container_for(&Index, node) -> Option<u32>
is_expandable(&Index, node) -> bool
TreeModel::{new, visible_row_count, rows, toggle, display_text, is_expandable}
search_parallel(bytes, &Regex, SearchScope, &Index) -> Vec<Match>
```

`Format` is `Json | Xml | Csv | Tsv`. Extension mapping: `.xml`→Xml, `.csv`→Csv,
`.tsv`/`.tab`→Tsv, everything else (incl. `.json`/`.jsonl`/`.ndjson`)→Json.

### 10.2 Python native module (`openxmljson._native`)

```python
Document.open(path: str) -> Document          # picks parser by extension
Document.node_count() -> int
Document.file_bytes() -> int
Document.index_bytes() -> int                 # 32 * node_count
Document.format_name() -> str                 # "JSON" | "XML" | "CSV" | "TSV"
Document.root() -> int
Document.kind(node: int) -> int               # NodeKind discriminant
Document.is_expandable(node: int) -> bool
Document.child_nodes(node: int) -> list[int]  # drilled display children
Document.child_count(node: int) -> int
Document.display_text(node: int) -> str
Document.raw_text(node: int) -> str
Document.search(pattern: str, scope: str) -> list[int]   # matching node ids
node_kind_names() -> list[tuple[int, str]]
```

Errors surface as Python `IOError` (open) and `ValueError` (parse/bad regex).

---

## 11. GUI specification

### 11.1 Python/Qt application (primary)

A single main window: a toolbar (Open button, search field, scope combo
[All/Keys/Values/Attributes], Find button) above a `QTreeView`, with a status bar.

- **Open** shows a native file dialog filtered to
  `.json .jsonl .ndjson .xml .csv .tsv .tab`; on success the tree loads and the
  status bar shows `FORMAT · N.N MB file · N,NNN nodes · N.N MB index`.
- **Tree** is a `DocumentModel(QAbstractItemModel)` over the native `Document`.
  `QTreeView` is inherently virtualized (uniform row heights on); the model
  answers `rowCount`/`hasChildren`/`index`/`parent`/`data` from the native index.
  Because the display tree drills through `Key`/`Attribute`, a child's *display*
  parent may differ from its structural parent; the model records the display
  parent when it hands out each child index (Qt always creates a parent index
  before its children), so `parent()` is always answerable. Children lists are
  cached per node so `index(row,…)` is O(1).
- **Search** runs `Document.search` on the native side and highlights matching
  rows (background color) via a `BackgroundRole`; the status bar shows the match
  count. Invalid regex is reported without crashing.

### 11.2 Rust/egui application (alternative, no Python)

`crates/oxj-gui` renders the same tree with `egui::ScrollArea::show_rows`, which
builds widgets only for the visible row range. It supports open, expand/collapse,
and regex search with the same scopes and inline highlighting. It exists so the
engine can ship as a single native binary with no Python runtime.

---

## 12. Error handling

Parse errors carry a byte offset and a short message (`ParseError`, `XmlError`;
`parse_document` normalizes to `String`). CSV never hard-fails on structure — it
tolerates ragged rows and malformed quoting by resyncing at the next boundary.
Out-of-range index slices return `None` rather than panicking. The engine ships
as a Python extension built with `panic = "unwind"` so a Rust panic is converted
to a Python exception rather than aborting the interpreter.

---

## 13. Performance characteristics

Memory is dominated by (a) the OS page cache for touched file regions (bounded by
the working set, not file size) and (b) the index at 24 bytes/node. Parsing is a
single linear pass with no per-token allocation. Search is embarrassingly
parallel and cache-friendly. UI cost per frame is proportional to visible rows
(~40), not document size. The native/Python boundary is crossed only for visible
rows and for search, so Python overhead is not on the hot path. Compilation uses
fat LTO, one codegen unit, and per-target AVX2/FMA (x86-64) or an Apple-silicon
baseline (aarch64) so the scanning loops vectorize.

---

## 14. Build, packaging, distribution

Development: `pip install maturin pyside6 && maturin develop --release &&
python -m openxmljson`. Wheels: `maturin build --release` (macOS
`--target universal2-apple-darwin` for a fat Intel+ARM binary). Installers:
PyInstaller (`packaging/openxmljson.spec`) produces `OPENXMLJSON.app` (wrap into a
DMG with `hdiutil`) and `OPENXMLJSON.exe` (wrap into an MSI with WiX or Inno
Setup); Briefcase is an alternative. macOS distribution requires `codesign` +
`notarytool` + `stapler`; Windows requires `signtool` with an Authenticode cert.
CI (`.github/workflows/build.yml`) runs the Rust tests and the three Python
validators on Ubuntu/macOS/Windows and produces wheels and app bundles as
artifacts. Full details in `docs/BUILD.md`.

---

## 15. Testing and verification

Each parser has Rust unit tests (`cargo test -p oxj-core`) covering structure,
edge cases, and rejections. Because Rust could not be compiled in the authoring
environment, each parser's logic is additionally validated by an **exact Python
port cross-checked against a trusted reference implementation**:

| Parser | Reference | Fuzz result |
|--------|-----------|-------------|
| JSON | stdlib `json` | 5,000 / 5,000 documents identical |
| CSV | stdlib `csv` | 5,000 / 5,000 tables identical |
| XML | `xml.etree` | 4,000 / 4,000 documents identical |

This process caught two real bugs before they reached Rust: JSON object members
built as flat `[key, value]` pairs instead of a key owning its value, and
acceptance of trailing commas after the "expect value/key" state. Both are fixed
and mirrored in the ports.

---

## 16. Security considerations

Files are mapped read-only and never mutated. All index slicing is bounds-checked.
Regex is user-supplied but run by the `regex` crate, which has linear-time
guarantees (no catastrophic backtracking). The mmap SIGBUS-on-truncation caveat
applies to externally truncated files; the planned log-tailing feature only ever
grows a file. Deep-nesting denial of service is bounded by `MAX_DEPTH`.

---

## 17. Roadmap and requirement traceability

Mapping to the original prompt set (see `docs/STATUS.md` for the live table):
architecture (1), JSON zero-copy index + mmap (2), parallel regex search (3),
virtualized tree (5), XML indexer (8), unified polymorphic tree (10), and
XML-aware scoped search (12) are implemented; CSV/TSV reading and the Python+Rust
hybrid packaging were added on request. Deferred: dataset merging + streaming
export (4), tail-`-f` log monitoring (6), XML namespace resolution (9),
cross-format export XML⇄JSON⇄CSV (11), and a query language over the index.

### 17.1 Known limitations

Single-token size is capped at 4 GiB (`u32` length). XML namespaces are not
resolved and entity references are not decoded. JSON `\u`/escape sequences and
CSV `""` escapes are shown raw except for on-screen unescaping. Export is not yet
implemented. The reference virtualization walk is linear in the scroll offset
until the Fenwick-tree index is added.

---

## Appendix A — glossary

**Structural index**: the flat `Vec<Node>` describing the document's tree using
byte offsets, not copied data. **Zero-copy**: reading a value by slicing the
mapping instead of allocating a string. **Drilling**: presenting an object
member or attribute and its value as one logical row. **Scope**: the subset of
node kinds a search is restricted to. **NIL**: `u32::MAX`, the "no such node"
link sentinel.
