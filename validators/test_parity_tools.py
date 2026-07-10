"""Headless tests for the parity tools: diff, JSON-schema validate, minify.

Pure-logic (no Qt). Usage: python test_parity_tools.py
"""

from __future__ import annotations

import os
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

from openxmljson import convert, difftool, schema  # noqa: E402

_failures = 0


def check(name, cond):
    global _failures
    if not cond:
        _failures += 1
        print("FAIL", name)
    else:
        print("PASS", name)


# ---- minify / beautify ----------------------------------------------------

def test_reformat():
    v = {"a": 1, "b": ["x", "y"], "c": {"d": True, "e": None}}
    check(
        "minify json compact",
        convert.to_minified_json(v)
        == '{"a":1,"b":["x","y"],"c":{"d":true,"e":null}}',
    )
    check("pretty json has newlines", "\n" in convert.to_pretty_json(v))
    xml_min = convert.to_xml(v, "root", pretty=False)
    xml_pretty = convert.to_xml(v, "root", pretty=True)
    check("xml minify has no newlines", xml_min.count("\n") == 0)
    check("xml pretty has newlines", xml_pretty.count("\n") > 3)
    ET.fromstring(xml_min.split("?>", 1)[1])  # raises if malformed
    check("xml minify is well-formed", True)


# ---- diff -----------------------------------------------------------------

def test_diff():
    a = {"name": "alice", "age": 30, "tags": ["x", "y"], "meta": {"k": 1}}
    b = {"name": "bob", "age": 30, "tags": ["x", "z", "w"], "extra": True}
    ch = difftool.diff(a, b)
    check("name changed", ("changed", "$.name", "alice", "bob") in ch)
    check("tags[1] changed", any(k == "changed" and p == "$.tags[1]" for k, p, *_ in ch))
    check("tags[2] added", any(k == "added" and p == "$.tags[2]" for k, p, *_ in ch))
    check("meta removed", any(k == "removed" and p == "$.meta" for k, p, *_ in ch))
    check("extra added", any(k == "added" and p == "$.extra" for k, p, *_ in ch))
    check("age unchanged", not any(p == "$.age" for _, p, *_ in ch))
    check("identical → empty", difftool.diff(a, a) == [])
    check("type change", difftool.diff({"x": 1}, {"x": [1]}) == [("changed", "$.x", 1, [1])])
    check("bool != int", difftool.diff({"x": True}, {"x": 1})[0][0] == "changed")
    a2, r2, c2 = difftool.summarize(ch)
    check("summary counts", (a2, r2, c2) == (2, 1, 2))


# ---- schema ---------------------------------------------------------------

SCH = {
    "type": "object",
    "required": ["id", "name"],
    "properties": {
        "id": {"type": "integer", "minimum": 1},
        "name": {"type": "string", "minLength": 1, "maxLength": 10},
        "role": {"enum": ["admin", "user"]},
        "tags": {"type": "array", "items": {"type": "string"},
                 "uniqueItems": True, "minItems": 1},
        "score": {"type": "number", "multipleOf": 0.5},
    },
    "additionalProperties": False,
}


def test_schema():
    ok = {"id": 5, "name": "al", "role": "user", "tags": ["a", "b"], "score": 2.5}
    check("valid passes", schema.validate(ok, SCH) == [])
    bad = {"id": 0, "name": "", "role": "root", "tags": ["a", "a"],
           "score": 2.3, "extra": 1}
    errs = schema.validate(bad, SCH)
    for p in ("$.id", "$.name", "$.role", "$.tags", "$.score"):
        check(f"error at {p}", any(ep == p for ep, _ in errs))
    check("additionalProperties", any("extra" in m for _, m in errs))
    check("required missing", any("required" in m for _, m in schema.validate({"id": 1}, SCH)))

    anyof = {"anyOf": [{"type": "string"}, {"type": "integer"}]}
    check("anyOf pass", schema.validate("hi", anyof) == [] and schema.validate(3, anyof) == [])
    check("anyOf fail", schema.validate([], anyof) != [])
    check("oneOf exactly one", schema.validate(5, {"oneOf": [{"type": "integer"}, {"minimum": 0}]}) != [])
    check("not pass/fail", schema.validate(5, {"not": {"type": "string"}}) == []
          and schema.validate("x", {"not": {"type": "string"}}) != [])
    ref = {"type": "object", "properties": {"a": {"$ref": "#/$defs/pos"}},
           "$defs": {"pos": {"type": "integer", "minimum": 0}}}
    check("$ref valid/invalid", schema.validate({"a": 3}, ref) == []
          and schema.validate({"a": -1}, ref) != [])
    check("integer rejects float", schema.validate(1.5, {"type": "integer"}) != [])
    check("number accepts int", schema.validate(3, {"type": "number"}) == [])
    check("bool is not integer", schema.validate(True, {"type": "integer"}) != [])


if __name__ == "__main__":
    test_reformat()
    test_diff()
    test_schema()
    if _failures:
        print(f"\n{_failures} FAILURE(S)")
        sys.exit(1)
    print("\nAll parity-tool tests passed.")
