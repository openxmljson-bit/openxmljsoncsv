# OPENXMLJSON

View, navigate and regex-search very large **JSON**, **XML** and **CSV/TSV**
files (up to ~10 GB) without loading them into an in-memory object graph.

The core idea: a **zero-copy, memory-mapped structural index**. The file is
mmapped once and parsed into a flat array of fixed-size 24-byte node records
holding byte offsets — never copied data. Tree navigation, display and
search all work on those offsets, so memory tracks your working set, not
the file size. Files too large to index eagerly fall back to **on-demand
(lazy) indexing**, materializing nodes only as they are expanded, so
documents near or beyond available RAM still open. Full design:
[SPEC.md](SPEC.md); requirement status: [docs/STATUS.md](docs/STATUS.md).

## Layout

    crates/oxj-core    Rust engine: mmap, 24-byte node index, eager +
                       lazy (on-demand) JSON/XML/CSV
                       parsers, virtualized tree model, rayon+regex search
    crates/oxj-py      PyO3 bindings → the native module openxmljson._native
    crates/oxj-gui     optional pure-Rust egui front-end (no Python)
    python/openxmljson PySide6/Qt application shell (QAbstractItemModel)
    validators/        exact Python ports of the parsers, fuzz-checked
                       against stdlib json / xml.etree / csv (SPEC §15)
    packaging/         PyInstaller spec (→ .app / .exe)
    docs/              BUILD.md (build/sign/notarize), STATUS.md

## Quick start

    pip install maturin pyside6
    maturin develop --release
    python -m openxmljson            # or: python -m openxmljson big.json

Pure-Rust GUI instead: `cargo run -p oxj-gui --release`

## Tests

    cargo test -p oxj-core                       # engine unit tests
    cd validators && python run_all.py           # parser fuzz validation
                                                 # (no Rust toolchain needed)

## Highlights

- Exactly 24 bytes of index per node (compile-time asserted); ~3 GB for a
  130M-node document, independent of value sizes.
- On-demand (lazy) indexing for files too large to index eagerly (JSON,
  CSV/TSV, XML): only the root exists up front and children are materialized
  when expanded, so resident memory tracks the viewed subtree. Search/filter/
  query still work via a whole-file scan that resolves matches to nodes by
  materializing only their paths. Engages automatically above ~½ of RAM
  (configurable: View ▸ Lazy Indexing).
- No recursion anywhere: 2,500-level-deep documents parse fine; nesting is
  capped at 4,096 (DoS bound).
- Search: 8 MiB parallel chunks with 64 KiB overlap (raw scan) or
  index-scoped scanning of Keys / Values / Attributes only.
- UI cost per frame is proportional to visible rows (~40), never file size.
- CSV renders like a JSON array-of-objects when a header is detected;
  leading-zero cells (ZIP codes) deliberately stay strings.
- (The raw-source side panel was removed.)
- Live tail (View ▸ Follow Tail, Ctrl+T) for JSON / NDJSON / log files:
  follows a growing file, parsing only the newly appended lines each tick
  (the parsed prefix is never re-read) and appending them as new rows —
  memory and work per tick track the appended bytes, not the file size.
- Query bar (View ▸ Query Bar, Ctrl+Q): JSONPath for JSON/CSV
  (`$.store.items[*].name`, `$..price`, `[*]`, `[-1]`) and XPath for XML
  (`//item/@id`, `/catalog/item[2]/title`, `text()`, `*`). Results filter
  the tree to the matching rows; evaluated over the index with traversal
  caps so it stays responsive on large files.
