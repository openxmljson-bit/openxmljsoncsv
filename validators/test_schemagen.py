"""Tests for openxmljson.schemagen — JSON Schema inference.

Pure value-based tests need no Qt. The model-based test reuses the validator's
StubDocument (json_port) + DocumentModel to prove the streaming node-walk
produces the same schema as inferring from the reconstructed value.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

from openxmljson import schema, schemagen  # noqa: E402


# -- infer_schema (pure) -------------------------------------------------------

def test_infer_object_types_and_required():
    s = schemagen.infer_schema({"name": "Ann", "age": 30, "vip": True})
    assert s["$schema"].endswith("draft-07/schema#")
    assert s["type"] == "object"
    assert s["properties"]["name"] == {"type": "string"}
    assert s["properties"]["age"] == {"type": "integer"}
    assert s["properties"]["vip"] == {"type": "boolean"}
    assert set(s["required"]) == {"name", "age", "vip"}
    assert s["additionalProperties"] is False


def test_optional_key_not_required():
    s = schemagen.infer_schema([{"a": 1, "b": 2}, {"a": 3}])
    props = s["items"]["properties"]
    assert set(props) == {"a", "b"}
    assert s["items"]["required"] == ["a"]


def test_int_then_float_widens_to_number():
    s = schemagen.infer_schema([1, 2, 3.5])
    assert s["type"] == "array"
    assert s["items"] == {"type": "number"}


def test_null_and_string_union():
    s = schemagen.infer_schema([{"x": "hi"}, {"x": None}])
    assert s["items"]["properties"]["x"]["type"] == ["null", "string"]


def test_empty_array_has_no_items():
    s = schemagen.infer_schema([])
    assert s["type"] == "array"
    assert "items" not in s


def test_nested_arrays_of_objects():
    data = {"users": [{"id": 1, "tags": ["a", "b"]}, {"id": 2, "tags": []}]}
    s = schemagen.infer_schema(data)
    users = s["properties"]["users"]
    assert users["type"] == "array"
    assert users["items"]["properties"]["id"] == {"type": "integer"}
    assert users["items"]["properties"]["tags"] == {
        "type": "array", "items": {"type": "string"}}


ROUND_TRIP_CASES = [
    {"name": "Ann", "age": 30, "scores": [1, 2, 3]},
    [{"a": 1}, {"a": 2, "b": "x"}],
    {"nested": {"deep": {"flag": True, "n": None}}},
    [1, 2, 3.5, 4],
    {"mixed": [1, "two", True, None]},
    [],
    {"empty_obj": {}},
]


def test_generated_schema_validates_source():
    for value in ROUND_TRIP_CASES:
        s = schemagen.infer_schema(value)
        errors = schema.validate(value, s)
        assert errors == [], f"{value!r} failed its own schema: {errors}"


def test_determinism():
    v = {"b": 1, "a": 2, "c": [{"z": 1, "y": 2}]}
    assert schemagen.infer_schema(v) == schemagen.infer_schema(v)


# -- infer_schema_from_model (streaming node walk) ----------------------------

def _model_matches_pure(json_text: str):
    import json

    from test_qt_model import StubDocument   # installs QtGui stub + offscreen
    from openxmljson.model import DocumentModel

    doc = StubDocument(json_text.encode("utf-8"))
    model = DocumentModel(doc)

    kids = doc.child_nodes(doc.root())
    whole = [model.reconstruct(k) for k in kids]
    whole = whole[0] if len(whole) == 1 else whole

    from_model = schemagen.infer_schema_from_model(model)
    assert from_model == schemagen.infer_schema(whole)
    assert schema.validate(json.loads(json_text), from_model) == []


def test_model_walk_matches_pure_infer():
    for text in (
        '[{"id": 1, "name": "a"}, {"id": 2, "name": "b", "extra": true}]',
        '{"user": {"id": 1, "tags": ["x", "y"]}, "active": false}',
        '[1, 2, 3.5]',
        '{"items": [{"v": null}, {"v": "hi"}]}',
    ):
        _model_matches_pure(text)
