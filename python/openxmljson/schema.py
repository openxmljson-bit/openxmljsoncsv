"""A compact, dependency-free JSON Schema validator (practical subset).

Pure functions (no Qt) so they're headlessly testable. Given a reconstructed
instance (dicts / lists / scalars) and a schema (parsed JSON), it returns a
list of (instance_path, message) errors — empty means valid.

Supported keywords (draft-07 / 2020-12 core, the ones that matter for
day-to-day validation):

- type (single or list), enum, const
- object: properties, required, additionalProperties (bool or schema),
  patternProperties, minProperties, maxProperties
- array: items (schema), prefixItems, minItems, maxItems, uniqueItems
- number: minimum, maximum, exclusiveMinimum, exclusiveMaximum, multipleOf
- string: minLength, maxLength, pattern, (format is accepted but not enforced)
- combinators: allOf, anyOf, oneOf, not
- local references: {"$ref": "#/..."} resolved against the root schema

Unsupported keywords are ignored (documented limitation): remote $ref,
$dynamicRef, if/then/else, dependentSchemas, content*, unevaluated*. A `true`
schema accepts anything; a `false` schema rejects everything.
"""

from __future__ import annotations

import math
import re
from typing import Any, List, Tuple

Error = Tuple[str, str]


def validate(instance: Any, schema: Any) -> List[Error]:
    """Validate ``instance`` against ``schema``; return errors (empty = ok)."""
    errors: List[Error] = []
    _validate(instance, schema, "$", schema, errors)
    return errors


# -- type helpers ------------------------------------------------------------

def _is_integer(v: Any) -> bool:
    if isinstance(v, bool):
        return False
    if isinstance(v, int):
        return True
    return isinstance(v, float) and math.isfinite(v) and v.is_integer()


def _matches_type(v: Any, t: str) -> bool:
    if t == "null":
        return v is None
    if t == "boolean":
        return isinstance(v, bool)
    if t == "integer":
        return _is_integer(v)
    if t == "number":
        return not isinstance(v, bool) and isinstance(v, (int, float))
    if t == "string":
        return isinstance(v, str)
    if t == "array":
        return isinstance(v, list)
    if t == "object":
        return isinstance(v, dict)
    return False


def _type_name(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, int):
        return "integer"
    if isinstance(v, float):
        return "number"
    if isinstance(v, str):
        return "string"
    if isinstance(v, list):
        return "array"
    if isinstance(v, dict):
        return "object"
    return "unknown"


def _equal(a: Any, b: Any) -> bool:
    """JSON equality: bool is distinct from numbers; 1 == 1.0; dicts/lists
    compared structurally."""
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_equal(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_equal(x, y) for x, y in zip(a, b))
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b  # JSON numbers: 1 and 1.0 are equal
    if type(a) is not type(b):
        return False
    return a == b


def _resolve_ref(ref: str, root: Any):
    """Resolve a local JSON Pointer ``#/a/b/0`` against the root schema."""
    if not ref.startswith("#"):
        return None  # remote refs unsupported
    pointer = ref[1:]
    if pointer.startswith("/"):
        pointer = pointer[1:]
    node = root
    if pointer == "":
        return root
    for raw in pointer.split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(node, dict):
            if token not in node:
                return None
            node = node[token]
        elif isinstance(node, list):
            try:
                node = node[int(token)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return node


# -- core --------------------------------------------------------------------

def _validate(inst: Any, schema: Any, path: str, root: Any,
              errors: List[Error]) -> None:
    # Boolean schemas.
    if schema is True:
        return
    if schema is False:
        errors.append((path, "schema is false: no value is valid"))
        return
    if not isinstance(schema, dict):
        return  # not a schema we understand; accept

    if "$ref" in schema:
        target = _resolve_ref(schema["$ref"], root)
        if target is None:
            errors.append((path, f"cannot resolve $ref {schema['$ref']!r}"))
        else:
            _validate(inst, target, path, root, errors)
        # draft-07: a $ref ignores sibling keywords. Keep that behaviour.
        return

    _check_type(inst, schema, path, errors)
    _check_enum_const(inst, schema, path, errors)
    _check_combinators(inst, schema, path, root, errors)

    if isinstance(inst, (int, float)) and not isinstance(inst, bool):
        _check_number(inst, schema, path, errors)
    if isinstance(inst, str):
        _check_string(inst, schema, path, errors)
    if isinstance(inst, list):
        _check_array(inst, schema, path, root, errors)
    if isinstance(inst, dict):
        _check_object(inst, schema, path, root, errors)


def _check_type(inst, schema, path, errors):
    if "type" not in schema:
        return
    types = schema["type"]
    if isinstance(types, str):
        types = [types]
    if not any(_matches_type(inst, t) for t in types):
        errors.append(
            (path, f"expected type {'/'.join(types)}, got {_type_name(inst)}")
        )


def _check_enum_const(inst, schema, path, errors):
    if "const" in schema and not _equal(inst, schema["const"]):
        errors.append((path, f"must equal const {schema['const']!r}"))
    if "enum" in schema and not any(_equal(inst, e) for e in schema["enum"]):
        errors.append((path, "value not in enum"))


def _check_combinators(inst, schema, path, root, errors):
    if "allOf" in schema:
        for sub in schema["allOf"]:
            _validate(inst, sub, path, root, errors)
    if "anyOf" in schema:
        if not any(
            not _collect(inst, sub, path, root) for sub in schema["anyOf"]
        ):
            errors.append((path, "does not match any schema in anyOf"))
    if "oneOf" in schema:
        matches = sum(
            1 for sub in schema["oneOf"] if not _collect(inst, sub, path, root)
        )
        if matches != 1:
            errors.append(
                (path, f"must match exactly one schema in oneOf (matched {matches})")
            )
    if "not" in schema:
        if not _collect(inst, schema["not"], path, root):
            errors.append((path, "must not match the 'not' schema"))


def _collect(inst, schema, path, root) -> List[Error]:
    """Validate into a fresh error list (for combinators)."""
    sub: List[Error] = []
    _validate(inst, schema, path, root, sub)
    return sub


def _check_number(inst, schema, path, errors):
    if "minimum" in schema and inst < schema["minimum"]:
        errors.append((path, f"must be >= {schema['minimum']}"))
    if "maximum" in schema and inst > schema["maximum"]:
        errors.append((path, f"must be <= {schema['maximum']}"))
    if "exclusiveMinimum" in schema and inst <= schema["exclusiveMinimum"]:
        errors.append((path, f"must be > {schema['exclusiveMinimum']}"))
    if "exclusiveMaximum" in schema and inst >= schema["exclusiveMaximum"]:
        errors.append((path, f"must be < {schema['exclusiveMaximum']}"))
    if "multipleOf" in schema:
        m = schema["multipleOf"]
        if m and not math.isclose(inst / m, round(inst / m), rel_tol=1e-9,
                                  abs_tol=1e-9):
            errors.append((path, f"must be a multiple of {m}"))


def _check_string(inst, schema, path, errors):
    if "minLength" in schema and len(inst) < schema["minLength"]:
        errors.append((path, f"shorter than minLength {schema['minLength']}"))
    if "maxLength" in schema and len(inst) > schema["maxLength"]:
        errors.append((path, f"longer than maxLength {schema['maxLength']}"))
    if "pattern" in schema:
        try:
            if re.search(schema["pattern"], inst) is None:
                errors.append((path, f"does not match pattern {schema['pattern']!r}"))
        except re.error:
            errors.append((path, f"invalid pattern in schema: {schema['pattern']!r}"))


def _check_array(inst, schema, path, root, errors):
    if "minItems" in schema and len(inst) < schema["minItems"]:
        errors.append((path, f"fewer than minItems {schema['minItems']}"))
    if "maxItems" in schema and len(inst) > schema["maxItems"]:
        errors.append((path, f"more than maxItems {schema['maxItems']}"))
    if schema.get("uniqueItems") is True:
        seen: List[Any] = []
        for item in inst:
            if any(_equal(item, s) for s in seen):
                errors.append((path, "items must be unique"))
                break
            seen.append(item)
    prefix = schema.get("prefixItems")
    if isinstance(prefix, list):
        for i, sub in enumerate(prefix):
            if i < len(inst):
                _validate(inst[i], sub, f"{path}[{i}]", root, errors)
        rest_start = len(prefix)
    else:
        rest_start = 0
    items = schema.get("items")
    if isinstance(items, dict) or isinstance(items, bool):
        for i in range(rest_start, len(inst)):
            _validate(inst[i], items, f"{path}[{i}]", root, errors)


def _check_object(inst, schema, path, root, errors):
    props = schema.get("properties", {})
    for key in schema.get("required", []):
        if key not in inst:
            errors.append((path, f"missing required property {key!r}"))
    if "minProperties" in schema and len(inst) < schema["minProperties"]:
        errors.append((path, f"fewer than minProperties {schema['minProperties']}"))
    if "maxProperties" in schema and len(inst) > schema["maxProperties"]:
        errors.append((path, f"more than maxProperties {schema['maxProperties']}"))
    pattern_props = schema.get("patternProperties", {})
    additional = schema.get("additionalProperties", True)
    for key, value in inst.items():
        cp = f"{path}{_key_seg(key)}"
        handled = False
        if key in props:
            _validate(value, props[key], cp, root, errors)
            handled = True
        for pat, sub in pattern_props.items():
            try:
                if re.search(pat, key):
                    _validate(value, sub, cp, root, errors)
                    handled = True
            except re.error:
                pass
        if not handled:
            if additional is False:
                errors.append((cp, f"additional property {key!r} not allowed"))
            elif isinstance(additional, dict):
                _validate(value, additional, cp, root, errors)


def _key_seg(key: str) -> str:
    if isinstance(key, str) and re.match(r"[A-Za-z_][\w\-]*$", key):
        return f".{key}"
    safe = str(key).replace("\\", "\\\\").replace('"', '\\"')
    return f'["{safe}"]'
