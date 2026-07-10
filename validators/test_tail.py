"""Headless tests for live-tail: SegmentedDocument routing and the
incremental model row-append. Uses the JSON stub document as segments.

Usage: python test_tail.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))
sys.path.insert(0, os.path.dirname(__file__))

from test_qt_model import StubDocument  # noqa: E402 (installs QtGui stub)

from openxmljson.model import DocumentModel  # noqa: E402
from openxmljson.tail import SegmentedDocument, last_complete_line  # noqa: E402

from PySide6.QtCore import QModelIndex  # noqa: E402


class SearchableStub(StubDocument):
    """StubDocument plus a naive node-window search (the native Document
    provides this; the stub doesn't)."""

    def search(self, pattern, scope):
        import re as _re

        rx = _re.compile(pattern)
        out = []
        for i in range(1, len(self._idx)):
            n = self._idx.node(i)
            window = self._data[n.offset:n.offset + n.len]
            if rx.search(window.decode("utf-8", "replace")):
                out.append(i)
        return out


def test_last_complete_line() -> None:
    assert last_complete_line(b'{"a":1}\n{"a":2}\n') == 16
    assert last_complete_line(b'{"a":1}\n{"a":2}') == 8  # trailing partial
    assert last_complete_line(b'{"a":1}') == 0  # nothing complete yet
    print("last_complete_line: PASS")


def test_segmented_routing() -> None:
    base = SearchableStub(b'{"id": 1}\n{"id": 2}')  # NDJSON: 2 top-level
    seg = SegmentedDocument(base)
    assert seg.child_count(seg.root()) == 2

    # Wrapping the base leaves segment-0 ids unchanged (encode(0,x)==x).
    base_children = base.child_nodes(base.root())
    assert seg.child_nodes(seg.root()) == base_children

    # Append a chunk with two more records.
    chunk = SearchableStub(b'{"id": 3}\n{"id": 4}')
    added = seg.append_segment(chunk)
    assert added == 2
    kids = seg.child_nodes(seg.root())
    assert len(kids) == 4

    # New rows carry composite ids (segment 1) and route correctly.
    third, fourth = kids[2], kids[3]
    assert third >= (1 << 32)  # segment 1
    assert seg.kind(third) == base.kind(base_children[0])  # both Objects
    # parent of a top-level record is the segmented ROOT.
    assert seg.parent(third) == seg.root()
    # The appended objects' "id" members render 3 / 4 (Key rows show
    # "id: N"; scalar values are drilled onto the Key row).
    id_key = seg.child_nodes(third)[0]
    assert seg.display_text(id_key) == "id: 3"
    id_key4 = seg.child_nodes(fourth)[0]
    assert seg.display_text(id_key4) == "id: 4"

    # Search combines segments with composite ids.
    hits = seg.search("4", "all")
    assert any(h >= (1 << 32) for h in hits)
    print("segmented routing: PASS")


def test_model_tail_extend() -> None:
    base = StubDocument(b'{"n": 1}\n{"n": 2}')
    model = DocumentModel(base)
    root = QModelIndex()
    assert model.rowCount(root) == 2
    # Snapshot a persistent reference to row 0 to prove it stays valid.
    first_before = model.node_id(model.index(0, 0, root))

    seg = SegmentedDocument(base)
    chunk = StubDocument(b'{"n": 3}')
    added = seg.append_segment(chunk)
    model.tail_extend(seg, added)

    assert model.rowCount(root) == 3
    # Existing row 0 unchanged.
    assert model.node_id(model.index(0, 0, root)) == first_before
    # New row 2 is the appended record; its value reads 3.
    new_row = model.index(2, 0, root)
    assert new_row.isValid()
    key = model.index(0, 0, new_row)  # the "n" member
    assert model.value_text(key) == "3"
    # Path of the new record is $[2].
    assert model.path_text(new_row) == "$[2]"
    print("model tail_extend: PASS")


if __name__ == "__main__":
    test_last_complete_line()
    test_segmented_routing()
    test_model_tail_extend()
    print("test_tail: PASS")
