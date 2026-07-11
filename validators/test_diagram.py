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


def test_scalar_fields_and_edges():
    g = diagram.build_graph(FRUITS, root_title="Fruits")
    d = g.as_dict()
    titles = [n["title"] for n in d["nodes"]]
    assert "Fruits" in titles
    assert "fruits[0]" in titles
    assert "nutrients" in titles          # nested object becomes its own card

    ids = {n["id"] for n in d["nodes"]}
    for a, b in d["edges"]:               # every edge references real cards
        assert a in ids and b in ids

    apple = next(n for n in d["nodes"] if n["title"] == "fruits[0]")
    fields = dict(apple["fields"])
    assert fields["name"] == "Apple"      # scalar shown inline
    assert fields["nutrients"] == "{…}"   # nested object shown as a link marker


def test_edges_form_a_tree_from_root():
    g = diagram.build_graph(FRUITS, root_title="Fruits")
    # Root card has no incoming edge; every other card has exactly one parent.
    incoming = {}
    for a, b in g.edges:
        incoming[b] = incoming.get(b, 0) + 1
    root_id = g.nodes[0].id
    assert root_id not in incoming
    assert all(c == 1 for c in incoming.values())


def test_respects_max_nodes():
    big = {"items": [{"v": i} for i in range(50)]}
    g = diagram.build_graph(big, max_nodes=10)
    assert len(g.nodes) <= 10
    assert g.truncated is True


def test_scalar_root():
    g = diagram.build_graph("hello", root_title="value")
    assert len(g.nodes) == 1
    assert g.nodes[0].fields == [("value", "hello")]
    assert g.edges == []


def test_xml_attributes_become_fields():
    g = diagram.build_graph(XML_NETWORK, root_title="network")
    assert any("@id" in dict(n.fields) for n in g.nodes)
    titles = [n.title for n in g.nodes]
    assert "network" in titles
