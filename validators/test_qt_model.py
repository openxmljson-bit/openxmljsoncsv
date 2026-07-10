"""Headless test of the Qt DocumentModel row formatting, index labels,
path building and reconstruction — using the validator's exact parser
port as a stand-in for the native module.

Usage: QT_QPA_PLATFORM=offscreen python test_qt_model.py
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

import index_port as ip
import json_port

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# The sandbox lacks the system GL libraries QtGui links against; the model
# only uses QBrush/QColor as opaque values, so stub them out. On a real
# machine this test also passes with the genuine QtGui.
try:
    from PySide6 import QtGui  # noqa: F401
except ImportError:
    import types

    fake = types.ModuleType("PySide6.QtGui")

    class _Opaque:
        def __init__(self, *args, **kwargs):
            pass

    fake.QColor = _Opaque
    fake.QBrush = _Opaque
    sys.modules["PySide6.QtGui"] = fake

from PySide6.QtCore import QModelIndex  # noqa: E402

from openxmljson.model import DocumentModel  # noqa: E402


class StubDocument:
    """Mimics openxmljson._native.Document over the validator index,
    reproducing the engine's display_text rules (SPEC §8)."""

    def __init__(self, data: bytes):
        self._data = data
        self._idx = json_port.parse(data)

    # -- native API surface -------------------------------------------------

    def root(self) -> int:
        return self._idx.root()

    def format_name(self) -> str:
        return "JSON"

    def node_count(self) -> int:
        return len(self._idx)

    def file_bytes(self) -> int:
        return len(self._data)

    def index_bytes(self) -> int:
        return 32 * len(self._idx)

    def kind(self, node: int) -> int:
        return self._idx.node(node).kind

    def parent(self, node: int):
        p = self._idx.node(node).parent
        return None if p == ip.NIL else p

    def node_span(self, node: int):
        n = self._idx.node(node)
        return (n.offset, n.len)

    def node_at_offset(self, target: int) -> int:
        # Mirror of oxj_core::model::node_at_offset (binary search over
        # document-order offsets).
        n = len(self._idx)
        if n <= 1:
            return 0
        lo, hi = 1, n
        while lo < hi:
            mid = (lo + hi) // 2
            if self._idx.node(mid).offset <= target:
                lo = mid + 1
            else:
                hi = mid
        return lo - 1

    def read_bytes(self, offset: int, length: int) -> str:
        total = len(self._data)
        start = min(offset, total)
        end = min(start + length, total)
        return self._data[start:end].decode("utf-8", "replace")

    def raw_text(self, node: int) -> str:
        n = self._idx.node(node)
        return self._data[n.offset : n.offset + n.len].decode("utf-8", "replace")

    def _container_for(self, node: int):
        n = self._idx.node(node)
        if n.kind in (ip.KEY, ip.ATTRIBUTE):
            value = n.first_child
            if value == ip.NIL:
                return None
            return value if self._idx.node(value).kind in ip.CONTAINERS else None
        if n.kind in (ip.DOCUMENT, ip.OBJECT, ip.ARRAY, ip.ELEMENT_OPEN):
            return node
        return None

    def child_nodes(self, node: int):
        c = self._container_for(node)
        return [] if c is None else list(self._idx.children(c))

    def child_count(self, node: int) -> int:
        c = self._container_for(node)
        return 0 if c is None else self._idx.child_count(c)

    def is_expandable(self, node: int) -> bool:
        c = self._container_for(node)
        return c is not None and self._idx.node(c).first_child != ip.NIL

    def _key_name(self, node: int) -> str:
        raw = self.raw_text(node)
        return json.loads(raw) if raw.startswith('"') else raw

    def _scalar_display(self, node: int) -> str:
        kind = self.kind(node)
        raw = self.raw_text(node)
        if kind == ip.STRING:
            return '"' + json.loads(raw) + '"'  # decoded for display
        return raw

    def display_text(self, node: int) -> str:
        n = self._idx.node(node)
        if n.kind == ip.DOCUMENT:
            return f"document [{self._idx.child_count(node)}]"
        if n.kind == ip.OBJECT:
            return "{" + str(self._idx.child_count(node)) + "}"
        if n.kind == ip.ARRAY:
            return "[" + str(self._idx.child_count(node)) + "]"
        if n.kind == ip.KEY:
            name = self._key_name(node)
            value = n.first_child
            vkind = self._idx.node(value).kind
            if vkind == ip.OBJECT:
                return f"{name} {{{self._idx.child_count(value)}}}"
            if vkind == ip.ARRAY:
                return f"{name} [{self._idx.child_count(value)}]"
            return f"{name}: {self._scalar_display(value)}"
        return self._scalar_display(node)


SAMPLE = b'{"a": [{"x": 1}, "s", true, null, 2.5], "b": {"k": "v"}, "n": 42}'


def texts(model, parent=QModelIndex()):
    return [
        str(model.index(i, 0, parent).data())
        for i in range(model.rowCount(parent))
    ]


def main() -> None:
    doc = StubDocument(SAMPLE)
    model = DocumentModel(doc)

    # A single top-level object/array is drilled through: its keys/items are
    # the top-level rows (no redundant root "{...}" row — matches Dadroit).
    top = texts(model)
    assert top == ["a : [...]", "b : {...}", "n : 42"], top

    # Multiple top-level values (NDJSON) are NOT drilled — keep their indices.
    ndjson_doc = StubDocument(b'{"a": 1}\n{"b": 2}')
    ndjson_model = DocumentModel(ndjson_doc)
    assert texts(ndjson_model) == ["[0] : {...}", "[1] : {...}"]

    # "a" is now a top-level row; its array children get [i] labels.
    a_index = model.index(0, 0)
    arr = texts(model, a_index)
    assert arr == [
        "[0] : {...}",
        '[1] : "s"',
        "[2] : true",
        "[3] : null",
        "[4] : 2.5",
    ], arr

    # Segment roles drive the colors.
    segs = model.segments(model.node_id(model.index(1, 0, a_index)))
    assert segs == [("index", "[1]"), ("punct", " : "), ("string", '"s"')], segs
    segs = model.segments(model.node_id(model.index(2, 0, a_index)))
    assert segs[-1][0] == "bool", segs
    n_index = model.index(2, 0)  # top-level "n : 42"
    assert model.segments(model.node_id(n_index))[-1][0] == "number"

    # Paths stay anchored at $ (relative to the drilled root).
    x_index = model.index(0, 0, model.index(0, 0, a_index))
    assert model.path_text(x_index) == "$.a[0].x", model.path_text(x_index)
    assert model.path_text(model.index(1, 0, a_index)) == "$.a[1]"

    # Copy helpers.
    assert model.name_text(x_index) == "x"
    assert model.value_text(x_index) == "1"
    assert model.name_text(model.index(1, 0, a_index)) == "[1]"
    assert model.value_text(model.index(1, 0, a_index)) == "s"

    # Reconstruction round-trips the document (from the drilled root object).
    rebuilt = model.reconstruct(model._root)
    assert rebuilt == json.loads(SAMPLE), rebuilt
    rebuilt_a = model.reconstruct(model.node_id(a_index))
    assert rebuilt_a == json.loads(SAMPLE)["a"], rebuilt_a

    # Match highlighting plumbing.
    model.set_matches([model.node_id(n_index)])
    assert model.is_match(model.node_id(n_index))

    # index_for_node: navigate from a raw node id back to its display row
    # (as the prev/next match buttons do). The "x" scalar's display row is
    # its Key; drilled ancestors resolve correctly.
    x_node = model.node_id(x_index)
    resolved = model.index_for_node(x_node)
    assert resolved.isValid() and model.node_id(resolved) == x_node
    assert model.path_text(resolved) == "$.a[0].x"
    # A fresh model (empty caches) can still resolve deep nodes.
    model2 = DocumentModel(doc)
    resolved2 = model2.index_for_node(x_node)
    assert resolved2.isValid() and model2.node_id(resolved2) == x_node

    # The value node UNDER the key (the number 1) resolves to the key row.
    # Find it: the key's structural first child in the stub index.
    x_value = doc._idx.node(x_node).first_child
    resolved3 = model.index_for_node(x_value)
    assert model.node_id(resolved3) == x_node

    # Collapsed-node annotations: objects count keys, arrays count items.
    assert model.collapsed_suffix(a_index) == "5 items"          # "a" array
    assert model.collapsed_suffix(model.index(1, 0)) == "1 key"  # "b" object
    assert model.collapsed_suffix(model.index(0, 0, a_index)) == "1 key"  # a[0]
    assert model.collapsed_suffix(n_index) is None  # scalar: no suffix

    # Named pretty-JSON: Key rows keep their name.
    named = model.reconstruct_named(x_node)
    assert named == {"x": 1}, named
    named_b = model.reconstruct_named(model.node_id(model.index(1, 0)))
    assert named_b == {"b": {"k": "v"}}, named_b
    # Non-key nodes are unchanged: the first array element is a bare object.
    elem0 = model.node_id(model.index(0, 0, a_index))
    assert model.reconstruct_named(elem0) == {"x": 1}

    # Source-panel sync: byte offset → node → display row (round trip).
    # SAMPLE = {"a": [{"x": 1}, "s", true, null, 2.5], "b": {"k": "v"}, ...}
    pos_of_1 = SAMPLE.index(b"1")
    node = doc.node_at_offset(pos_of_1)
    assert doc.kind(node) == 5  # NUMBER
    row = model.index_for_node(node)
    assert row.isValid() and model.name_text(row) == "x"
    # And tree → source: the row's key node span points at the raw token.
    off, length = doc.node_span(model.node_id(row))
    assert SAMPLE[off : off + length] == b'"x"'
    # A byte inside the "b" key token resolves to that key row.
    pos_of_b = SAMPLE.index(b'"b"') + 1
    node_b = doc.node_at_offset(pos_of_b)
    row_b = model.index_for_node(node_b)
    assert model.path_text(row_b) == "$.b"
    # read_bytes windows are clamped and decode cleanly.
    assert doc.read_bytes(0, 10) == SAMPLE[:10].decode()
    assert doc.read_bytes(10_000, 50) == ""

    # Jump to path (Batch 1): resolve JSONPath-ish strings to rows.
    idx = model.resolve_path("$.a[0].x")
    assert idx.isValid() and model.name_text(idx) == "x"
    assert model.path_text(idx) == "$.a[0].x"  # round trip
    idx = model.resolve_path(".a[4]")  # leading $ optional
    assert idx.isValid() and str(idx.data()).endswith("2.5")
    idx = model.resolve_path('$["b"].k')
    assert idx.isValid() and model.name_text(idx) == "k"
    assert not model.resolve_path("$.missing").isValid()
    assert not model.resolve_path("$.a[99]").isValid()
    assert not model.resolve_path("garbage!!").isValid()
    # NDJSON: top-level indices resolve.
    nd_idx = ndjson_model.resolve_path("$[1].b")
    assert nd_idx.isValid() and ndjson_model.name_text(nd_idx) == "b"

    # Type badges (Batch 1).
    assert model.describe(model.index(1, 0)) == "Object · 1 key"  # "b"
    assert model.describe(a_index) == "Array · 5 items"           # "a"
    assert model.describe(n_index) == "Number"
    s_index = model.index(1, 0, a_index)  # "s"
    assert model.describe(s_index) == "String · 1 chars"

    print("test_qt_model: PASS")


if __name__ == "__main__":
    main()
