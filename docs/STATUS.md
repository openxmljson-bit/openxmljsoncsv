# STATUS — requirement traceability (live table)

Mapping to the original prompt set (SPEC §17) and to this repository.

| # | Capability | Status | Where |
|---|------------|--------|-------|
| 1 | Architecture (hybrid Python/Qt + Rust engine) | Implemented | SPEC §4, crates/, python/ |
| 2 | JSON zero-copy index + mmap | Implemented | `json.rs`, `index.rs`, `mapping.rs` |
| 3 | Parallel regex search (chunked, overlap) | Implemented | `search.rs` |
| 4 | Dataset merging + streaming export | Partial (export done; merge deferred) | `convert.py` |
| 5 | Virtualized tree | Implemented | `model.rs`, `python/openxmljson/model.py` |
| 6 | tail -f log monitoring | Implemented | `tail.py`, Follow Tail (Ctrl+T) |
| 8 | XML indexer | Implemented | `xml.rs` |
| 9 | XML namespace resolution | **Deferred** (prefixes verbatim) | SPEC §7.2 |
| 10 | Unified polymorphic tree | Implemented | `index.rs` (one NodeKind space), `model.rs` |
| 11 | Cross-format export XML⇄JSON⇄CSV | Implemented | `convert.py`, Export Data As |
| 12 | XML-aware scoped search | Implemented | `search.rs` (SearchScope) |
| + | CSV/TSV reading (added on request) | Implemented | `csv.rs` |
| + | Python+Rust hybrid packaging (added on request) | Implemented | pyproject.toml, packaging/, docs/BUILD.md |
| + | Query language over the index (JSONPath/XPath) | Implemented | `query.py`, Query bar (Ctrl+Q) |
| + | On-demand (lazy) indexing for very large files | Implemented | `lazy.rs`, `lazy_csv.rs`, `lazy_xml.rs`, `LazyDocument` |

## Functional requirements (SPEC §3.1)

| ID | Status | Evidence |
|----|--------|----------|
| F1 | Done | `json.rs` + validate_json.py (5,000/5,000 fuzz) |
| F2 | Done | `xml.rs` + validate_xml.py (4,000/4,000 fuzz) |
| F3 | Done | `csv.rs` + validate_csv.py (5,000/5,000 fuzz) |
| F4 | Done | `model.rs` drilling + virtualization; both GUIs |
| F5 | Done | `search.rs` scopes; Document.search(pattern, scope) |
| F6 | Done | node-id highlighting (Qt BackgroundRole / egui bg); `search_streaming` |
| F7 | Done | status bar: FORMAT · file MB · nodes · index MB |
| F8 | Done | `Format::from_path` |
| F9 | Done | ParseError/XmlError carry byte offsets; tested |

## Non-functional requirements (SPEC §3.2)

| ID | Status | Evidence |
|----|--------|----------|
| N1 | Done | `mapping.rs` (memmap2, sequential advice) |
| N2 | Done | compile-time `size_of::<Node>() == 24` assert (packed layout; was 32) |
| N3 | Done | iterative parsers + iterative tree walks; 2,500-deep tests |
| N4 | Done | `rows(start,len)` slices; QTreeView uniform rows; egui show_rows |
| N5 | Done | rayon in `search.rs`; GIL released in bindings |
| N6 | Scripted | packaging/ + docs/BUILD.md (signing needs org certs) |
| N7 | Done | oxj-core consumed by both oxj-py and oxj-gui |

## Validation snapshot

| Parser | Reference | Fuzz result |
|--------|-----------|-------------|
| JSON | stdlib `json` | 5,000 / 5,000 documents identical |
| CSV | stdlib `csv` | 5,000 / 5,000 tables identical |
| XML | `xml.etree` | 4,000 / 4,000 documents identical |

Bugs caught by validation before reaching (or while in) Rust:

1. JSON object members originally built as flat `[key, value]` pairs
   instead of a Key owning its value — fixed (design phase).
2. Acceptance of trailing commas after the "expect value/key" state —
   fixed (design phase).
3. `ArrComma` state did not accept `]`, so `[1,2,3]` was rejected —
   caught by validate_json.py edge cases, fixed in `json.rs` + port.

## Known limitations (SPEC §17.1)

Single-token size capped at 4 GiB (u32 window length). XML namespaces not
resolved; entity references not decoded (display-layer only). JSON/CSV
escapes shown raw except on-screen. Reference virtualization walk is linear
in scroll offset until the Fenwick-tree index lands. Raw-scan matches longer
than the 64 KiB chunk overlap cannot be completed across a chunk seam.

On-demand (lazy) indexing limitations: lazy XML is lenient (does not validate
well-formedness the way the eager parser does); CSV column-name search does
not highlight (matches resolve against cell values, since the shared header
window sits outside every record). Lazy mode engages automatically only for
files large enough that a full index would risk exhausting RAM.
