"""Tests for the Deep Dive projection core (Qt-free)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

from openxmljson.project import (  # noqa: E402
    all_paths,
    project_value,
    schema_field_tree,
)
from openxmljson.schemagen import infer_schema  # noqa: E402


NESTED = {
    "response": {
        "products": [
            {"id": 1, "title": "A", "price": 10,
             "meta": {"sku": "x", "color": "red"}},
            {"id": 2, "title": "B", "price": 20, "meta": {"sku": "y"}},
        ]
    }
}


def _tree():
    return schema_field_tree(infer_schema(NESTED))


def test_field_tree_is_array_transparent():
    paths = set(all_paths(_tree()))
    assert ("response", "products", "price") in paths
    assert ("response", "products", "meta", "sku") in paths
    # no numeric array index segments
    assert all(all(isinstance(p, str) for p in path) for path in paths)


def test_project_keeps_only_selected_fields():
    sel = [("response", "products", "title"),
           ("response", "products", "meta", "sku")]
    out = project_value(NESTED, sel)
    prods = out["response"]["products"]
    assert prods[0] == {"title": "A", "meta": {"sku": "x"}}
    assert prods[1] == {"title": "B", "meta": {"sku": "y"}}


def test_selecting_parent_keeps_whole_subtree():
    out = project_value(NESTED, [("response", "products", "meta")])
    assert out["response"]["products"][0]["meta"] == {"sku": "x", "color": "red"}


def test_top_level_array_of_records():
    arr = [{"a": 1, "b": 2, "c": 3}, {"a": 4, "b": 5}]
    out = project_value(arr, [("a",), ("c",)])
    assert out == [{"a": 1, "c": 3}, {"a": 4}]


def test_empty_selection_yields_empty():
    assert project_value(NESTED, []) == {}
