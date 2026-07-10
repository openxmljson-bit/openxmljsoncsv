"""Headless tests for the JSONPath/XPath query engine, evaluated over real
indexes from the validator parser ports.

Usage: python test_query.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))
sys.path.insert(0, os.path.dirname(__file__))

from test_qt_model import StubDocument  # noqa: E402 (installs QtGui stub)
from test_batch2 import StubCsvDocument  # noqa: E402

from openxmljson import query  # noqa: E402
from openxmljson.model import DocumentModel  # noqa: E402

import xml_port  # noqa: E402
import index_port as ip  # noqa: E402


class StubXmlDocument(StubDocument):
    def __init__(self, data: bytes):
        self._data = data
        self._idx = xml_port.parse_xml(data)

    def format_name(self) -> str:
        return "XML"

    def display_text(self, node: int) -> str:
        n = self._idx.node(node)
        if n.kind == ip.ELEMENT_OPEN:
            return f"<{self.raw_text(node)}>"
        if n.kind == ip.ATTRIBUTE:
            v = self._idx.node(n.first_child)
            return f'@{self.raw_text(node)} = "{self._data[v.offset:v.offset+v.len].decode()}"'
        return self.raw_text(node)


def names(model, nodes):
    """Human-readable labels for asserting result sets."""
    out = []
    for n in nodes:
        kind = model._doc.kind(n)
        if kind == ip.KEY:
            out.append(model._key_name(n))
        elif kind == ip.ELEMENT_OPEN:
            out.append("<" + model._doc.raw_text(n) + ">")
        elif kind == ip.ATTRIBUTE:
            out.append("@" + model._doc.raw_text(n))
        else:
            out.append(model.value_of_node(n))
    return out


def test_jsonpath_parse() -> None:
    assert query.parse_jsonpath("$.a[0].b") == [
        ("key", "a"), ("index", 0), ("key", "b")
    ]
    assert query.parse_jsonpath("$.a.*") == [("key", "a"), ("wildcard",)]
    assert query.parse_jsonpath("$..name") == [("descend", "name")]
    assert query.parse_jsonpath('$["a b"][2]') == [
        ("key", "a b"), ("index", 2)
    ]
    for bad in ("$.a[", "$.a[x]", "$.["):
        try:
            query.parse_jsonpath(bad)
            raise AssertionError(f"expected error for {bad!r}")
        except query.QueryError:
            pass
    print("jsonpath parse: PASS")


SAMPLE = (
    b'{"store": {"items": ['
    b'{"name": "A", "price": 10}, {"name": "B", "price": 20},'
    b'{"name": "C", "price": 30}], "open": true}, "id": 7}'
)


def test_jsonpath_eval() -> None:
    model = DocumentModel(StubDocument(SAMPLE))

    r = query.evaluate(model, "$.store.items[*].name")
    assert names(model, r) == ["name", "name", "name"]

    r = query.evaluate(model, "$.store.items[0].price")
    assert model.value_of_node(r[0]) == "10"

    r = query.evaluate(model, "$.store.items[-1].name")
    assert model.value_of_node(query.evaluate(
        model, "$.store.items[-1].name")[0]) == "C"

    r = query.evaluate(model, "$..price")
    assert sorted(model.value_of_node(n) for n in r) == ["10", "20", "30"]

    r = query.evaluate(model, "$.store.*")
    assert names(model, r) == ["items", "open"]

    r = query.evaluate(model, "$.id")
    assert model.value_of_node(r[0]) == "7"

    assert query.evaluate(model, "$.nope") == []
    assert query.evaluate(model, "$.store.items[99]") == []

    # Union: two different fields in one query.
    r = query.evaluate(
        model, "$.store.items[0].name | $.store.items[0].price"
    )
    assert names(model, r) == ["name", "price"]
    assert [model.value_of_node(n) for n in r] == ["A", "10"]
    # Union dedups overlapping results, preserving first-seen order.
    r = query.evaluate(model, "$.id | $.id")
    assert len(r) == 1
    print("jsonpath eval: PASS")


def test_jsonpath_filter() -> None:
    data = (
        b'{"products": ['
        b'{"productType": "ring", "price": 50},'
        b'{"productType": "necklace", "price": 120},'
        b'{"productType": "ring", "price": 80}]}'
    )
    model = DocumentModel(StubDocument(data))

    # sanity: 3 products total
    assert len(query.evaluate(model, "$.products[*]")) == 3

    # key:value match — the core ask.
    r = query.evaluate(model, '$.products[?(@.productType=="ring")]')
    assert len(r) == 2, len(r)
    # each matched element really has productType == ring
    for elem in r:
        pt = [c for c in model._doc.child_nodes(elem)
              if model._key_name(c) == "productType"][0]
        assert model.value_of_node(pt) == "ring"

    # numeric comparison
    r = query.evaluate(model, "$.products[?(@.price>60)]")
    assert len(r) == 2  # 120 and 80

    # != and existence
    assert len(query.evaluate(model, '$.products[?(@.productType!="ring")]')) == 1
    assert len(query.evaluate(model, "$.products[?(@.price)]")) == 3

    # chain a field after the filter: values of matching rings' prices
    r = query.evaluate(model, '$.products[?(@.productType=="ring")].price')
    assert sorted(model.value_of_node(n) for n in r) == ["50", "80"]

    # parse-level sanity
    assert query.parse_jsonpath('$.a[?(@.k=="v")]') == [
        ("key", "a"), ("filter", ("cmp", "k", "==", "v", False))
    ]

    # plain-text shorthand key:value (quotes optional) — same result as the
    # predicate, matching the KEY nodes anywhere in the doc.
    for q in ("productType:ring", '"productType":"ring"', "productType: ring"):
        r = query.evaluate(model, q)
        assert len(r) == 2, (q, len(r))
        assert all(model._key_name(n) == "productType" for n in r)
        assert all(model.value_of_node(n) == "ring" for n in r)
    assert query.evaluate(model, "productType:sock") == []
    print("jsonpath filter + shorthand: PASS")


def test_is_structured() -> None:
    # routed to the query engine
    assert query.is_structured("productType:ring")
    assert query.is_structured("$.products[*]")
    assert query.is_structured("//item/@id", is_xml=True)
    assert query.is_structured('$.a[?(@.k=="v")]')
    # routed to plain substring filter
    assert not query.is_structured("ring")
    assert not query.is_structured("12:30")            # not an identifier key
    assert not query.is_structured("https://x.com/a")  # URL
    assert not query.is_structured("")
    assert not query.is_structured("productType:ring", is_xml=True)  # XML: not shorthand
    print("is_structured routing: PASS")


def test_ndjson_eval() -> None:
    model = DocumentModel(StubDocument(b'{"a":1}\n{"a":2}\n{"a":3}'))
    r = query.evaluate(model, "$[1].a")
    assert model.value_of_node(r[0]) == "2"
    r = query.evaluate(model, "$[*].a")
    assert [model.value_of_node(n) for n in r] == ["1", "2", "3"]
    print("ndjson eval: PASS")


def test_csv_eval() -> None:
    model = DocumentModel(
        StubCsvDocument(b"name,qty\nblinds,2\nshades,5")
    )
    r = query.evaluate(model, "$[*].name")
    assert [model.value_of_node(n) for n in r] == ["blinds", "shades"]
    r = query.evaluate(model, "$[1].qty")
    assert model.value_of_node(r[0]) == "5"
    print("csv eval: PASS")


XML = (
    b"<catalog>"
    b'<item id="1"><title>Alpha</title></item>'
    b'<item id="2"><title>Beta</title><tag>x</tag></item>'
    b"<group><item id=\"3\"><title>Gamma</title></item></group>"
    b"</catalog>"
)


def test_xpath() -> None:
    model = DocumentModel(StubXmlDocument(XML))

    r = query.evaluate(model, "/catalog/item")
    assert names(model, r) == ["<item>", "<item>"]  # direct children only

    r = query.evaluate(model, "//item")
    assert names(model, r) == ["<item>", "<item>", "<item>"]  # incl. nested

    r = query.evaluate(model, "//item/@id")
    # value_of_node JSON-encodes attribute values; strip quotes to compare.
    assert [model.value_of_node(n).strip('"') for n in r] == ["1", "2", "3"]

    r = query.evaluate(model, "//title")
    assert names(model, r) == ["<title>", "<title>", "<title>"]  # elements

    r = query.evaluate(model, "/catalog/item[2]/title/text()")
    assert model.value_of_node(r[0]) == "Beta"

    r = query.evaluate(model, "//item/title")
    assert len(r) == 3

    r = query.evaluate(model, "/catalog/*")
    assert names(model, r) == ["<item>", "<item>", "<group>"]

    r = query.evaluate(model, "//title/text()")
    assert sorted(model.value_of_node(n) for n in r) == ["Alpha", "Beta", "Gamma"]

    assert query.evaluate(model, "//nonexistent") == []

    # Union of two attribute paths.
    r = query.evaluate(model, "/catalog/item[1]/@id | //title")
    assert model.value_of_node(r[0]).strip('"') == "1"
    assert names(model, r[1:]) == ["<title>", "<title>", "<title>"]

    # split_union ignores '|' inside quotes/brackets.
    assert query.split_union('$["a|b"] | $.c') == ['$["a|b"]', "$.c"]
    print("xpath eval: PASS")


if __name__ == "__main__":
    test_jsonpath_parse()
    test_jsonpath_eval()
    test_jsonpath_filter()
    test_is_structured()
    test_ndjson_eval()
    test_csv_eval()
    test_xpath()
    print("test_query: PASS")
