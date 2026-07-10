"""Headless tests for Batch 2: converters, statistics, filter sets, and
the CSV table model. Reuses the StubDocument from test_qt_model.

Usage: python test_batch2.py
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))
sys.path.insert(0, os.path.dirname(__file__))

from test_qt_model import StubDocument  # noqa: E402  (installs QtGui stub)

from openxmljson import convert  # noqa: E402
from openxmljson.csvtable import RecordTableModel  # noqa: E402
from openxmljson.model import DocumentModel, NodeFilterProxy  # noqa: E402

from PySide6.QtCore import QModelIndex  # noqa: E402

import csv_port  # noqa: E402
import index_port as ip  # noqa: E402


# ---------------------------------------------------------------------------
# Converters
# ---------------------------------------------------------------------------

def test_convert() -> None:
    value = {"name": "Ann", "tags": ["a", "b"], "n": 3, "ok": True, "x": None}
    # JSON round trip.
    assert json.loads(convert.to_pretty_json(value)) == value
    # XML: keys → elements, list items → <item>, escaping applied.
    xml = convert.to_xml({"a<b": "x & y", "list": [1, 2]})
    assert "<a_b>x &amp; y</a_b>" in xml
    assert xml.count("<item>") == 2
    # XML-origin subtrees keep tags and attributes.
    xml_node = {
        "tag": "product",
        "attributes": {"id": "42"},
        "children": ["Fast > cheap"],
    }
    xml = convert.to_xml(xml_node)
    assert '<product id="42">Fast &gt; cheap</product>' in xml
    # CSV: list of dicts → header union; nested cells → compact JSON.
    records = [
        {"a": 1, "b": "x,y"},
        {"a": 2, "c": {"deep": True}},
    ]
    csv_text = convert.to_csv(records)
    lines = csv_text.strip().splitlines()
    assert lines[0] == "a,b,c"
    assert lines[1] == '1,"x,y",'
    assert lines[2] == '2,,"{""deep"": true}"'
    # Scalar list → single column; non-tabular → error.
    assert convert.to_csv([1, 2]).splitlines()[0] == "value"
    try:
        convert.to_csv({"a": 1, "b": 2})
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
    print("convert: PASS")


# ---------------------------------------------------------------------------
# Statistics + filter sets (JSON stub document)
# ---------------------------------------------------------------------------

def test_stats_and_filter() -> None:
    doc = StubDocument(
        b'{"items": [1, 2, 2, "x", null, 5.5], "name": "top"}'
    )
    model = DocumentModel(doc)
    # Root object is drilled → its keys ("items", "name") are the top rows.
    items_index = model.index(0, 0)
    stats = model.statistics(items_index)
    assert "Children: 6" in stats
    assert "Number: 4" in stats
    assert "Distinct values: 5" in stats
    assert "min 1" in stats and "max 5.5" in stats

    # Filter: matches for "x" → the string element + its ancestors.
    # Find the node id of "x" via path resolution.
    x_index = model.resolve_path("$.items[3]")
    assert x_index.isValid()
    x_node = model.node_id(x_index)
    visible = model.visible_filter_set([x_node])
    assert x_node in visible
    assert model.node_id(items_index) in visible  # ancestor key row

    proxy = NodeFilterProxy(model)
    proxy.set_visible(visible)
    # Top level shows only "items"; under it only "x" (drilled root).
    assert proxy.rowCount(QModelIndex()) == 1
    pitems = proxy.index(0, 0, QModelIndex())
    assert proxy.rowCount(pitems) == 1
    px = proxy.index(0, 0, pitems)
    assert "x" in str(px.data())
    # Map back to source.
    assert model.node_id(proxy.mapToSource(px)) == x_node
    print("stats + filter: PASS")


# ---------------------------------------------------------------------------
# CSV table model (CSV stub via the validator's parser port)
# ---------------------------------------------------------------------------

class StubCsvDocument(StubDocument):
    def __init__(self, data: bytes):
        self._data = data
        self._idx = csv_port.parse_csv(data)

    def format_name(self) -> str:
        return "CSV"

    def _key_name(self, node: int) -> str:
        return self.raw_text(node).replace('""', '"')

    def _scalar_display(self, node: int) -> str:
        kind = self.kind(node)
        raw = self.raw_text(node)
        if kind == ip.STRING:
            return '"' + raw.replace('""', '"') + '"'
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
            return f"{name}: {self._scalar_display(value)}"
        return self._scalar_display(node)


def test_csv_table() -> None:
    doc = StubCsvDocument(b"name,qty\nblinds,2\nshades,5\ncurtain rod,1")
    model = DocumentModel(doc)
    table = RecordTableModel(model)
    assert table.rowCount() == 3
    assert table.columnCount() == 2
    assert table.headerData(0, __import__("PySide6.QtCore", fromlist=["Qt"]).Qt.Orientation.Horizontal) == "name"
    assert table.data(table.index(0, 0)) == "blinds"
    assert table.data(table.index(1, 1)) == "5"
    assert table.data(table.index(2, 0)) == "curtain rod"
    assert table.record_node(1) is not None
    print("csv table: PASS")


if __name__ == "__main__":
    test_convert()
    test_stats_and_filter()
    test_csv_table()
    print("test_batch2: PASS")
