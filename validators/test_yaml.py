"""Tests for the YAML converters (Qt-free)."""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

yaml = pytest.importorskip("yaml")

from openxmljson.convert import to_yaml, yaml_to_json_text  # noqa: E402


def test_yaml_to_json_basic():
    text = "name: test\nitems:\n  - a\n  - b\ncount: 3\nprice: 1.5\nok: true\n"
    value = json.loads(yaml_to_json_text(text))
    assert value == {"name": "test", "items": ["a", "b"], "count": 3,
                     "price": 1.5, "ok": True}


def test_yaml_multi_document_becomes_array():
    text = "a: 1\n---\nb: 2\n"
    assert json.loads(yaml_to_json_text(text)) == [{"a": 1}, {"b": 2}]


def test_yaml_anchors_and_aliases_resolve():
    text = "base: &b\n  x: 1\nchild:\n  <<: *b\n  y: 2\n"
    value = json.loads(yaml_to_json_text(text))
    assert value["child"] == {"x": 1, "y": 2}


def test_invalid_yaml_raises_value_error():
    with pytest.raises(ValueError):
        yaml_to_json_text("key: [unclosed\n  broken: {")


def test_empty_yaml_is_null():
    assert json.loads(yaml_to_json_text("")) is None


def test_to_yaml_round_trip_preserves_value_and_key_order():
    value = {"z_first": 1, "a_second": [1, 2, {"k": None}], "s": "héllo"}
    out = to_yaml(value)
    assert yaml.safe_load(out) == value
    # sort_keys=False: insertion order kept
    assert out.index("z_first") < out.index("a_second")
    # allow_unicode: no escaped sequences
    assert "héllo" in out


def test_json_yaml_json_full_cycle():
    src = {"products": [{"id": 1, "tags": ["a"]}, {"id": 2, "tags": []}]}
    cycled = json.loads(yaml_to_json_text(to_yaml(src)))
    assert cycled == src
