"""Infer a JSON Schema (draft-07) from data.

Companion to ``schema.py`` (which *validates*): this *generates*. Two entry
points share one merge core:

* ``infer_schema(value)`` — from an already-reconstructed Python value
  (dict / list / scalars). Pure and Qt-free; used for tests and small inputs.
* ``infer_schema_from_model(model)`` — walks the native node index and, for the
  common large shapes (a top-level array, or CSV/TSV records), reconstructs one
  element at a time and merges it, so peak memory tracks the *schema* size and
  tree depth, not the file size. This keeps schema generation usable on the
  multi-GB documents the engine targets.

The emitted schema uses only keywords ``schema.py`` understands (``type`` as a
string or list, ``properties``, ``required``, ``additionalProperties``,
``items``), so generating a schema from a value and validating that same value
against it yields no errors (round-trip).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .schema import _type_name  # reuse the validator's type naming

SCHEMA_URI = "http://json-schema.org/draft-07/schema#"


class _Acc:
    """Accumulates observed shapes at one position in the tree. Memory is
    proportional to the schema (distinct keys/types), never the data."""

    __slots__ = ("types", "props", "key_count", "obj_count", "items")

    def __init__(self) -> None:
        self.types: set[str] = set()
        self.props: Dict[str, "_Acc"] = {}   # insertion order = first-seen keys
        self.key_count: Dict[str, int] = {}
        self.obj_count: int = 0
        self.items: Optional["_Acc"] = None

    def prop(self, key: str) -> "_Acc":
        acc = self.props.get(key)
        if acc is None:
            acc = _Acc()
            self.props[key] = acc
        return acc

    def item(self) -> "_Acc":
        if self.items is None:
            self.items = _Acc()
        return self.items


def _observe(acc: _Acc, value: Any) -> None:
    t = _type_name(value)
    acc.types.add(t)
    if t == "object":
        acc.obj_count += 1
        for key, sub in value.items():
            acc.key_count[key] = acc.key_count.get(key, 0) + 1
            _observe(acc.prop(key), sub)
    elif t == "array":
        if value:
            item_acc = acc.item()
            for element in value:
                _observe(item_acc, element)


def _to_schema(acc: _Acc) -> Dict[str, Any]:
    types = set(acc.types)
    if "integer" in types and "number" in types:
        types.discard("integer")   # "number" subsumes "integer"
    types.discard("unknown")

    schema: Dict[str, Any] = {}
    if types:
        ordered = sorted(types)
        schema["type"] = ordered[0] if len(ordered) == 1 else ordered

    if "object" in acc.types and acc.props:
        schema["properties"] = {k: _to_schema(a) for k, a in acc.props.items()}
        required = [k for k in acc.props if acc.key_count.get(k, 0) == acc.obj_count]
        if required:
            schema["required"] = required
        schema["additionalProperties"] = False

    if "array" in acc.types and acc.items is not None:
        schema["items"] = _to_schema(acc.items)

    return schema


def _finalize(body: Dict[str, Any]) -> Dict[str, Any]:
    return {"$schema": SCHEMA_URI, **body}


def infer_schema(value: Any) -> Dict[str, Any]:
    """Infer a draft-07 schema from a reconstructed Python value."""
    acc = _Acc()
    _observe(acc, value)
    return _finalize(_to_schema(acc))


def infer_schema_from_model(model: Any) -> Dict[str, Any]:
    """Infer a schema by walking a DocumentModel's native index.

    Streams top-level array elements / records (one reconstruct at a time) so
    peak memory stays bounded on large documents.
    """
    from .model import ARRAY  # lazy: avoids importing Qt for the pure path

    doc = model._doc
    root = doc.root()
    kids = doc.child_nodes(root)
    acc = _Acc()

    if len(kids) == 1 and doc.kind(kids[0]) == ARRAY:
        acc.types.add("array")
        for element in doc.child_nodes(kids[0]):
            _observe(acc.item(), model.reconstruct(element))
    elif len(kids) == 1:
        _observe(acc, model.reconstruct(kids[0]))
    else:  # many top-level records (CSV/TSV): treat as an array, streamed
        acc.types.add("array")
        for record in kids:
            _observe(acc.item(), model.reconstruct(record))

    return _finalize(_to_schema(acc))
