//! PyO3 bindings: the native module `openxmljson._native` (SPEC §10.2).
//!
//! The native/Python boundary is crossed only for visible rows and for
//! search, so Python overhead is never on the hot path (SPEC §13). Built
//! with `panic = "unwind"` so a Rust panic becomes a Python exception
//! rather than aborting the interpreter (SPEC §12).

use oxj_core::lazy::LazyIndex;
use oxj_core::lazy_csv::LazyCsv;
use oxj_core::lazy_xml::LazyXml;
use oxj_core::{
    container_for, display_text, highlight_span, is_expandable, node_at_offset, parse_document,
    record_at_offset, search_nodes, search_raw_parallel, CsvOptions, Format, Index, Mapping, Match,
    NodeKind, SearchScope, NIL,
};
use pyo3::exceptions::{PyIOError, PyValueError};
use pyo3::prelude::*;
use regex::bytes::Regex;
use std::path::Path;
use std::sync::Arc;

/// A parsed document: the shared mapping plus its structural index.
#[pyclass(frozen)]
struct Document {
    mapping: Arc<Mapping>,
    index: Arc<Index>,
    format: Format,
}

#[pymethods]
impl Document {
    /// Open and parse a file, picking the parser by extension (F8).
    #[staticmethod]
    fn open(py: Python<'_>, path: &str) -> PyResult<Document> {
        let mapping =
            Mapping::open(Path::new(path)).map_err(|e| PyIOError::new_err(e.to_string()))?;
        let format = Format::from_path(Path::new(path));
        // The parse is a single linear pass over the mapping; release the
        // GIL so the UI thread stays live.
        let index = py
            .allow_threads(|| parse_document(mapping.bytes(), format))
            .map_err(PyValueError::new_err)?;
        Ok(Document {
            mapping: Arc::new(mapping),
            index: Arc::new(index),
            format,
        })
    }

    fn node_count(&self) -> usize {
        self.index.len()
    }

    fn file_bytes(&self) -> u64 {
        self.mapping.len()
    }

    /// Index cost: 24 bytes per node (packed layout).
    fn index_bytes(&self) -> u64 {
        self.index.byte_size()
    }

    fn format_name(&self) -> &'static str {
        self.format.name()
    }

    /// Eager documents parse the whole file up front.
    fn is_lazy(&self) -> bool {
        false
    }

    fn root(&self) -> u32 {
        self.index.root()
    }

    /// NodeKind discriminant (stable; see node_kind_names()).
    fn kind(&self, node: u32) -> PyResult<u8> {
        self.check(node)?;
        Ok(self.index.node(node).kind as u8)
    }

    fn is_expandable(&self, node: u32) -> PyResult<bool> {
        self.check(node)?;
        Ok(is_expandable(&self.index, node))
    }

    /// Drilled display children: for Key/Attribute rows these are the
    /// children of the *value* (SPEC §8).
    fn child_nodes(&self, node: u32) -> PyResult<Vec<u32>> {
        self.check(node)?;
        Ok(match container_for(&self.index, node) {
            Some(c) => self.index.children(c).collect(),
            None => Vec::new(),
        })
    }

    fn child_count(&self, node: u32) -> PyResult<usize> {
        self.check(node)?;
        Ok(match container_for(&self.index, node) {
            Some(c) => self.index.child_count(c),
            None => 0,
        })
    }

    /// Format a row for display (decodes escapes on demand, SPEC §8).
    fn display_text(&self, node: u32) -> PyResult<String> {
        self.check(node)?;
        Ok(display_text(
            self.mapping.bytes(),
            &self.index,
            self.format,
            node,
        ))
    }

    /// The raw (undecoded) token window, lossy UTF-8.
    fn raw_text(&self, node: u32) -> PyResult<String> {
        self.check(node)?;
        let n = self.index.node(node);
        match self.mapping.slice(n.offset(), n.len) {
            Some(w) => Ok(String::from_utf8_lossy(w).into_owned()),
            None => Ok(String::new()),
        }
    }

    /// The raw byte window of a node: (offset, len) — tree→source sync.
    fn node_span(&self, node: u32) -> PyResult<(u64, u32)> {
        self.check(node)?;
        let n = self.index.node(node);
        Ok((n.offset(), n.len))
    }

    /// The byte span to highlight in the raw source: whole `<tag>…</tag>`
    /// extents for XML elements, `name="value"` for attributes, the
    /// full `<![CDATA[…]]>` wrapper, and plain token windows otherwise.
    fn highlight_span(&self, node: u32) -> PyResult<(u64, u64)> {
        self.check(node)?;
        Ok(highlight_span(self.mapping.bytes(), &self.index, node))
    }

    /// The node at (or nearest before) a byte offset — source→tree sync.
    /// CSV/TSV resolves to the record row (header Key windows repeat
    /// earlier offsets, so token-level lookup is JSON/XML only).
    fn node_at_offset(&self, offset: u64) -> u32 {
        match self.format {
            Format::Csv | Format::Tsv => record_at_offset(&self.index, offset),
            _ => node_at_offset(&self.index, offset),
        }
    }

    /// A lossy-UTF-8 slice of the raw document — the source panel's
    /// windowed reads. Out-of-range requests are clamped. Note: a slice
    /// boundary can split a multi-byte character; the edge then decodes
    /// as U+FFFD (harmless for display).
    fn read_bytes(&self, offset: u64, len: u32) -> String {
        let total = self.mapping.len();
        let start = offset.min(total);
        let len = (len as u64).min(total - start) as u32;
        match self.mapping.slice(start, len) {
            Some(w) => String::from_utf8_lossy(w).into_owned(),
            None => String::new(),
        }
    }

    /// Structural parent (NIL-free: returns None for the root).
    fn parent(&self, node: u32) -> PyResult<Option<u32>> {
        self.check(node)?;
        let p = self.index.node(node).parent;
        Ok(if p == NIL { None } else { Some(p) })
    }

    /// Regex search scoped to All/Keys/Values/Attributes; returns sorted
    /// matching node ids for row highlighting (SPEC §9).
    fn search(&self, py: Python<'_>, pattern: &str, scope: &str) -> PyResult<Vec<u32>> {
        let scope = SearchScope::from_name(scope)
            .ok_or_else(|| PyValueError::new_err(format!("unknown search scope: {scope}")))?;
        let re = Regex::new(pattern).map_err(|e| PyValueError::new_err(e.to_string()))?;
        let mapping = Arc::clone(&self.mapping);
        let index = Arc::clone(&self.index);
        // Search fans out across the rayon pool; release the GIL.
        Ok(py.allow_threads(move || search_nodes(mapping.bytes(), &re, scope, &index)))
    }

    fn __repr__(&self) -> String {
        format!(
            "<Document {} · {} bytes · {} nodes>",
            self.format.name(),
            self.mapping.len(),
            self.index.len()
        )
    }
}

impl Document {
    fn check(&self, node: u32) -> PyResult<()> {
        if (node as usize) < self.index.len() {
            Ok(())
        } else {
            Err(PyValueError::new_err(format!("no such node: {node}")))
        }
    }
}

/// Cap on distinct matching nodes returned by a lazy search: resolving a
/// match materializes its root-to-node path, so an unbounded match set on a
/// huge file could otherwise approach eager memory. 50k rows is far more
/// than any UI navigates and keeps the work bounded.
const LAZY_SEARCH_CAP: usize = 50_000;

/// The format-specific lazy engine behind a [`LazyDocument`]. Both variants
/// expose the identical navigation/display/search surface; this enum forwards
/// to whichever is in use so `LazyDocument`'s methods stay format-agnostic.
enum LazyEngine {
    Json(LazyIndex),
    Csv(LazyCsv),
    Xml(LazyXml),
}

impl LazyEngine {
    fn root(&self) -> u32 {
        match self {
            LazyEngine::Json(i) => i.root(),
            LazyEngine::Csv(c) => c.root(),
            LazyEngine::Xml(x) => x.root(),
        }
    }

    fn materialized(&self) -> usize {
        match self {
            LazyEngine::Json(i) => i.materialized(),
            LazyEngine::Csv(c) => c.materialized(),
            LazyEngine::Xml(x) => x.materialized(),
        }
    }

    fn arena_bytes(&self) -> u64 {
        match self {
            LazyEngine::Json(i) => i.arena_bytes(),
            LazyEngine::Csv(c) => c.arena_bytes(),
            LazyEngine::Xml(x) => x.arena_bytes(),
        }
    }

    fn kind(&self, node: u32) -> NodeKind {
        match self {
            LazyEngine::Json(i) => i.kind(node),
            LazyEngine::Csv(c) => c.kind(node),
            LazyEngine::Xml(x) => x.kind(node),
        }
    }

    fn parent(&self, node: u32) -> u32 {
        match self {
            LazyEngine::Json(i) => i.parent(node),
            LazyEngine::Csv(c) => c.parent(node),
            LazyEngine::Xml(x) => x.parent(node),
        }
    }

    fn window_of(&self, node: u32) -> (u64, u32) {
        match self {
            LazyEngine::Json(i) => i.window_of(node),
            LazyEngine::Csv(c) => c.window_of(node),
            LazyEngine::Xml(x) => x.window_of(node),
        }
    }

    fn is_expandable(&mut self, bytes: &[u8], node: u32) -> bool {
        match self {
            LazyEngine::Json(i) => i.is_expandable(bytes, node),
            LazyEngine::Csv(c) => c.is_expandable(bytes, node),
            LazyEngine::Xml(x) => x.is_expandable(bytes, node),
        }
    }

    fn display_children(&mut self, bytes: &[u8], node: u32) -> Vec<u32> {
        match self {
            LazyEngine::Json(i) => i.display_children(bytes, node),
            LazyEngine::Csv(c) => c.display_children(bytes, node),
            LazyEngine::Xml(x) => x.display_children(bytes, node),
        }
    }

    fn display_child_count(&mut self, bytes: &[u8], node: u32) -> usize {
        match self {
            LazyEngine::Json(i) => i.display_child_count(bytes, node),
            LazyEngine::Csv(c) => c.display_child_count(bytes, node),
            LazyEngine::Xml(x) => x.display_child_count(bytes, node),
        }
    }

    fn display_text(&mut self, bytes: &[u8], node: u32) -> String {
        match self {
            LazyEngine::Json(i) => i.display_text(bytes, node),
            LazyEngine::Csv(c) => c.display_text(bytes, node),
            LazyEngine::Xml(x) => x.display_text(bytes, node),
        }
    }

    fn locate_matches(
        &mut self,
        bytes: &[u8],
        matches: &[Match],
        scope: SearchScope,
        cap: usize,
    ) -> Vec<u32> {
        match self {
            LazyEngine::Json(i) => i.locate_matches(bytes, matches, scope, cap),
            LazyEngine::Csv(c) => c.locate_matches(bytes, matches, scope, cap),
            LazyEngine::Xml(x) => x.locate_matches(bytes, matches, scope, cap),
        }
    }
}

/// A lazily-indexed document (on-demand indexing, SPEC "lazy" mode).
///
/// Only the synthetic root exists after `open`; a node's children are
/// materialized the first time they are requested, so resident memory
/// tracks the *viewed* portion of the file rather than its full size. This
/// is what lets files near or beyond available RAM open without exhausting
/// memory (the eager `Document` builds a ~file-sized resident index).
///
/// The method surface mirrors `Document` so the same Qt model can drive
/// either; unlike `Document` it is **not** `frozen` because materialization
/// mutates the arena. JSON, CSV/TSV and XML are all supported. (Lazy XML is
/// lenient — it does not validate well-formedness the way eager parsing does.)
#[pyclass]
struct LazyDocument {
    mapping: Arc<Mapping>,
    engine: LazyEngine,
    format: Format,
}

#[pymethods]
impl LazyDocument {
    /// Open a JSON/NDJSON, CSV/TSV or XML file for lazy indexing.
    #[staticmethod]
    fn open(path: &str) -> PyResult<LazyDocument> {
        let format = Format::from_path(Path::new(path));
        let mapping =
            Mapping::open(Path::new(path)).map_err(|e| PyIOError::new_err(e.to_string()))?;
        // open() only reads the BOM/header — no full parse, so no GIL dance.
        let engine = match format {
            Format::Json => LazyEngine::Json(LazyIndex::open(mapping.bytes())),
            Format::Csv => LazyEngine::Csv(LazyCsv::open(mapping.bytes(), CsvOptions::default())),
            Format::Tsv => LazyEngine::Csv(LazyCsv::open(mapping.bytes(), CsvOptions::tsv())),
            Format::Xml => LazyEngine::Xml(LazyXml::open(mapping.bytes())),
        };
        Ok(LazyDocument {
            mapping: Arc::new(mapping),
            engine,
            format,
        })
    }

    /// Lazy documents report how many nodes are materialized *so far*
    /// (the total is unknown without a full parse — which lazy avoids).
    fn node_count(&self) -> usize {
        self.engine.materialized()
    }

    fn file_bytes(&self) -> u64 {
        self.mapping.len()
    }

    /// Resident arena cost so far (grows as nodes are materialized).
    fn index_bytes(&self) -> u64 {
        self.engine.arena_bytes()
    }

    fn format_name(&self) -> &'static str {
        self.format.name()
    }

    /// Marks this as a lazy document so the UI can adapt (e.g. always
    /// treat it as "large": disable row-wrap, guard Expand All).
    fn is_lazy(&self) -> bool {
        true
    }

    fn root(&self) -> u32 {
        self.engine.root()
    }

    /// Materialize the root's display children (the top-level rows) with the
    /// GIL released. `LazyDocument.open` only mmaps the file, so this scan
    /// would otherwise happen on the GUI thread at first paint (freezing the
    /// window). Running it here — from the background open task, GIL free —
    /// lets the event loop keep animating, and the first paint then finds the
    /// level already materialized. Returns the number of top-level rows.
    fn prime(&mut self, py: Python<'_>) -> usize {
        let mapping = Arc::clone(&self.mapping);
        let engine = &mut self.engine;
        py.allow_threads(move || {
            let root = engine.root();
            engine.display_children(mapping.bytes(), root).len()
        })
    }

    fn kind(&self, node: u32) -> PyResult<u8> {
        self.check(node)?;
        Ok(self.engine.kind(node) as u8)
    }

    fn is_expandable(&mut self, node: u32) -> PyResult<bool> {
        self.check(node)?;
        let mapping = Arc::clone(&self.mapping);
        Ok(self.engine.is_expandable(mapping.bytes(), node))
    }

    /// Drilled display children (materialized on first access).
    fn child_nodes(&mut self, node: u32) -> PyResult<Vec<u32>> {
        self.check(node)?;
        let mapping = Arc::clone(&self.mapping);
        Ok(self.engine.display_children(mapping.bytes(), node))
    }

    fn child_count(&mut self, node: u32) -> PyResult<usize> {
        self.check(node)?;
        let mapping = Arc::clone(&self.mapping);
        Ok(self.engine.display_child_count(mapping.bytes(), node))
    }

    /// Row label — identical to the eager `Document::display_text`.
    fn display_text(&mut self, node: u32) -> PyResult<String> {
        self.check(node)?;
        let mapping = Arc::clone(&self.mapping);
        Ok(self.engine.display_text(mapping.bytes(), node))
    }

    /// The raw (undecoded) token window, lossy UTF-8.
    fn raw_text(&self, node: u32) -> PyResult<String> {
        self.check(node)?;
        let (off, len) = self.engine.window_of(node);
        match self.mapping.slice(off, len) {
            Some(w) => Ok(String::from_utf8_lossy(w).into_owned()),
            None => Ok(String::new()),
        }
    }

    /// The raw byte window of a node: (offset, len) — tree→source sync.
    fn node_span(&self, node: u32) -> PyResult<(u64, u32)> {
        self.check(node)?;
        Ok(self.engine.window_of(node))
    }

    /// Structural parent (None for the root).
    fn parent(&self, node: u32) -> PyResult<Option<u32>> {
        self.check(node)?;
        let p = self.engine.parent(node);
        Ok(if p == NIL { None } else { Some(p) })
    }

    /// A lossy-UTF-8 slice of the raw document (clamped to range).
    fn read_bytes(&self, offset: u64, len: u32) -> String {
        let total = self.mapping.len();
        let start = offset.min(total);
        let len = (len as u64).min(total - start) as u32;
        match self.mapping.slice(start, len) {
            Some(w) => String::from_utf8_lossy(w).into_owned(),
            None => String::new(),
        }
    }

    /// Regex search over a lazy document: a whole-file parallel raw scan
    /// (GIL released) followed by resolving each match offset to its
    /// display node, materializing only the paths touched. Scoped to
    /// All/Keys/Values/Attributes and capped at `LAZY_SEARCH_CAP` distinct
    /// matching nodes so a pathological pattern cannot exhaust memory.
    fn search(&mut self, py: Python<'_>, pattern: &str, scope: &str) -> PyResult<Vec<u32>> {
        let scope = SearchScope::from_name(scope)
            .ok_or_else(|| PyValueError::new_err(format!("unknown search scope: {scope}")))?;
        let re = Regex::new(pattern).map_err(|e| PyValueError::new_err(e.to_string()))?;
        let mapping = Arc::clone(&self.mapping);
        let bytes = mapping.bytes();
        let matches = py.allow_threads(|| search_raw_parallel(bytes, &re));
        Ok(self
            .engine
            .locate_matches(bytes, &matches, scope, LAZY_SEARCH_CAP))
    }

    fn __repr__(&self) -> String {
        format!(
            "<LazyDocument {} · {} bytes · {} nodes materialized>",
            self.format.name(),
            self.mapping.len(),
            self.engine.materialized()
        )
    }
}

impl LazyDocument {
    fn check(&self, node: u32) -> PyResult<()> {
        if (node as usize) < self.engine.materialized() {
            Ok(())
        } else {
            Err(PyValueError::new_err(format!("no such node: {node}")))
        }
    }
}

/// The stable name↔code map for NodeKind (SPEC §5.1).
#[pyfunction]
fn node_kind_names() -> Vec<(u8, &'static str)> {
    NodeKind::ALL.iter().map(|k| (*k as u8, k.name())).collect()
}

/// Run a jq program (via the jaq engine) over a JSON string, returning each
/// output value as a compact JSON string. The caller passes small/medium
/// documents only (the GUI gates by size), since jq materializes its input.
#[pyfunction]
fn run_jq(program: &str, input: &str) -> PyResult<Vec<String>> {
    use jaq_interpret::{Ctx, FilterT, ParseCtx, RcIter, Val};

    let input_val: serde_json::Value = serde_json::from_str(input)
        .map_err(|e| PyValueError::new_err(format!("invalid JSON input: {e}")))?;

    // Load jaq's native filters + standard library, then parse & compile.
    let mut defs = ParseCtx::new(Vec::new());
    defs.insert_natives(jaq_core::core());
    defs.insert_defs(jaq_std::std());

    let (parsed, errs) = jaq_parse::parse(program, jaq_parse::main());
    if !errs.is_empty() {
        let msg = errs
            .iter()
            .map(|e| e.to_string())
            .collect::<Vec<_>>()
            .join("; ");
        return Err(PyValueError::new_err(format!("jq parse error: {msg}")));
    }
    let parsed = parsed
        .ok_or_else(|| PyValueError::new_err("empty jq program".to_string()))?;
    let filter = defs.compile(parsed);
    if !defs.errs.is_empty() {
        let msg = defs
            .errs
            .iter()
            .map(|(e, _)| e.to_string())
            .collect::<Vec<_>>()
            .join("; ");
        return Err(PyValueError::new_err(format!("jq compile error: {msg}")));
    }

    let inputs = RcIter::new(core::iter::empty());
    let ctx = Ctx::new([], &inputs);
    let mut out = Vec::new();
    for result in filter.run((ctx, Val::from(input_val))) {
        match result {
            Ok(v) => out.push(serde_json::Value::from(v).to_string()),
            Err(e) => return Err(PyValueError::new_err(format!("jq error: {e}"))),
        }
    }
    Ok(out)
}

#[pymodule]
#[pyo3(name = "_native")]
fn native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<Document>()?;
    m.add_class::<LazyDocument>()?;
    m.add_function(wrap_pyfunction!(node_kind_names, m)?)?;
    m.add_function(wrap_pyfunction!(run_jq, m)?)?;
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    Ok(())
}
