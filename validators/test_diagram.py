"""Headless tests for openxmljson.diagram — the Qt-free flow-diagram graph
builder. No Qt or native engine required."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

from openxmljson import diagram  # noqa: E402


FRUITS = {
    "fruits": [
        {"name": "Apple", "color": "#FF0000",
         "nutrients": {"calories": 52, "fiber": "2.4g"}},
        {"name": "Banana", "color": "#FFFF00",
         "nutrients": {"calories": 89, "fiber": "2.6g"}},
    ]
}

XML_NETWORK = {
    "tag": "network",
    "attributes": {"type": "NetworkGraph"},
    "children": [
        {"tag": "node", "attributes": {"id": "123", "label": "node A"}, "children": []},
        {"tag": "node", "attributes": {"id": "60", "label": "node B"}, "children": []},
    ],
}


def _fields(node):
    return {f[0]: f for f in node.fields}


def test_root_inlines_array_and_links_to_elements():
    g = diagram.build_graph(FRUITS)
    root = g.nodes[0]
    # Root card is the object with one row: fruits: [2 items] (a link row).
    assert root.fields == [("fruits", "[2 items]", "array", True)]
    # One edge per array element, all labelled with the key.
    fruit_edges = [e for e in g.edges if e[0] == root.id]
    assert len(fruit_edges) == 2
    assert all(lbl == "fruits" and row == 0 for _, _, lbl, row in fruit_edges)


def test_scalar_rows_are_typed():
    g = diagram.build_graph(FRUITS)
    # Find a fruit card (has a 'name' row).
    fruit = next(n for n in g.nodes if "name" in _fields(n))
    f = _fields(fruit)
    assert f["name"] == ("name", "Apple", "string", False)
    assert f["color"][2] == "string"        # "#FF0000"
    assert f["nutrients"][2] == "object" and f["nutrients"][3] is True  # link row


def test_number_type_detected():
    g = diagram.build_graph(FRUITS)
    nutrients = next(n for n in g.nodes if "calories" in _fields(n))
    assert _fields(nutrients)["calories"][2] == "number"
    assert _fields(nutrients)["calories"][1] == "52"


def test_edges_are_labelled_and_reference_real_nodes():
    g = diagram.build_graph(FRUITS)
    ids = {n.id for n in g.nodes}
    labels = set()
    for parent, child, label, row in g.edges:
        assert parent in ids and child in ids
        assert isinstance(row, int)
        labels.add(label)
    assert {"fruits", "nutrients"} <= labels


def test_empty_container_is_not_a_link():
    g = diagram.build_graph({"a": {}, "b": [], "c": 1})
    root = _fields(g.nodes[0])
    assert root["a"] == ("a", "{}", "object", False)
    assert root["b"] == ("b", "[]", "array", False)
    assert g.edges == []  # nothing to link to


def test_respects_max_nodes():
    big = {"items": [{"v": i} for i in range(50)]}
    g = diagram.build_graph(big, max_nodes=10)
    assert len(g.nodes) <= 10
    assert g.truncated is True


def test_scalar_root():
    g = diagram.build_graph("hello")
    assert len(g.nodes) == 1
    assert g.nodes[0].fields == [("", "hello", "string", False)]
    assert g.edges == []


def test_xml_attributes_become_fields():
    g = diagram.build_graph(XML_NETWORK)
    assert any("@id" in _fields(n) for n in g.nodes)


def test_nodes_have_titles():
    g = diagram.build_graph(FRUITS, root_title="fruits.json")
    titles = [n.title for n in g.nodes]
    assert g.nodes[0].title == "fruits.json"        # root uses the given title
    assert "fruits[0]" in titles                     # array element titled key[i]
    assert "nutrients" in titles                     # nested object titled by key
