"""A small JSONPath / XPath query engine evaluated over the structural
index.

Supported JSONPath (JSON / CSV / TSV):
    $               the root value
    .name  ["name"] object member
    [n]             array element (0-based)
    [*]  .*         all children (object members or array elements)
    ..name  ..*     recursive descent
    [?(@.k=="v")]   filter: keep children whose field k matches. Operators
                    == != < <= > > (numeric if both sides are numbers, else
                    string); the bare form [?(@.k)] tests that k exists.
                    e.g. $.products[?(@.productType=="ring")]

Supported XPath (XML):
    /tag            child element
    //tag           descendant element
    tag[n]          the n-th matching child (1-based)
    @attr           attribute
    text()          character data
    *   //*         any element (child / descendant)

Evaluation walks the index node-by-node in Python (no full-document
materialization) with traversal/result caps, so it stays responsive on
large files. It is deliberately a *subset*: filter predicates compare a
child's field to a literal (no arbitrary sub-expressions), and functions
are out of scope; a native, unbounded evaluator is a future engine feature.
"""

from __future__ import annotations

import re
from typing import List

from openxmljson.model import (
    ATTRIBUTE,
    CDATA,
    DOCUMENT,
    ELEMENT_OPEN,
    KEY,
    TEXT,
)

#: Stop recursive-descent traversal after visiting this many nodes.
DESCEND_VISIT_CAP = 2_000_000
#: Cap the returned result set.
RESULT_CAP = 200_000


class QueryError(ValueError):
    pass


# ---------------------------------------------------------------------------
# JSONPath
# ---------------------------------------------------------------------------

def _read_name(text: str, i: int):
    """Read a bare member name until '.', '[' or end."""
    start = i
    while i < len(text) and text[i] not in ".[":
        i += 1
    if i == start:
        raise QueryError("expected a name")
    return text[start:i], i


#: `@.name OP literal` inside a [?(...)] filter.
_PRED_CMP = re.compile(r"^@\.([^\s=!<>]+)\s*(==|!=|>=|<=|>|<)\s*(.+?)$")
#: bare existence test `@.name`.
_PRED_EXISTS = re.compile(r"^@\.([^\s=!<>]+)$")


def _literal(rhs: str):
    """Parse a filter right-hand side into (value, is_number)."""
    rhs = rhs.strip()
    if len(rhs) >= 2 and rhs[0] in "'\"" and rhs[-1] == rhs[0]:
        return rhs[1:-1], False          # quoted string
    if re.fullmatch(r"-?\d+(?:\.\d+)?", rhs):
        return rhs, True                 # number
    return rhs, False                    # bareword / true / false / null


def parse_predicate(expr: str) -> tuple:
    """Parse the inside of a [?(...)] filter. Supports `@.name OP literal`
    and the bare `@.name` existence test."""
    expr = expr.strip()
    m = _PRED_CMP.match(expr)
    if m:
        value, is_num = _literal(m.group(3))
        return ("cmp", m.group(1), m.group(2), value, is_num)
    m = _PRED_EXISTS.match(expr)
    if m:
        return ("exists", m.group(1))
    raise QueryError(f"unsupported filter: [?({expr})]")


def parse_jsonpath(text: str) -> List[tuple]:
    text = text.strip()
    if text.startswith("$"):
        text = text[1:]
    steps: List[tuple] = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c == ".":
            if i + 1 < n and text[i + 1] == ".":
                i += 2
                name, i = _read_name(text, i)
                steps.append(("descend", None if name == "*" else name))
            else:
                i += 1
                name, i = _read_name(text, i)
                steps.append(("wildcard",) if name == "*" else ("key", name))
        elif c == "[":
            j = text.find("]", i)
            if j < 0:
                raise QueryError("unterminated '['")
            inner = text[i + 1 : j].strip()
            i = j + 1
            if inner == "*":
                steps.append(("wildcard",))
            elif inner.startswith("?(") and inner.endswith(")"):
                steps.append(("filter", parse_predicate(inner[2:-1])))
            elif len(inner) >= 2 and inner[0] in "'\"" and inner[-1] == inner[0]:
                steps.append(("key", inner[1:-1]))
            elif re.fullmatch(r"-?\d+", inner):
                steps.append(("index", int(inner)))
            else:
                raise QueryError(f"bad subscript: [{inner}]")
        else:
            raise QueryError(f"unexpected character {c!r} in path")
    return steps


def _children(model, node) -> List[int]:
    return model._doc.child_nodes(node)


_CMP = {
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
}


def _field_value(model, elem, name: str):
    """Scalar value of ``elem``'s direct child field ``name``, or None."""
    for c in _children(model, elem):
        if model._doc.kind(c) == KEY and model._key_name(c) == name:
            return model.value_of_node(c)
    return None


def _match_predicate(model, elem, pred: tuple) -> bool:
    if pred[0] == "exists":
        return _field_value(model, elem, pred[1]) is not None
    _, name, op, rhs, is_num = pred
    val = _field_value(model, elem, name)
    if val is None:
        return False
    if is_num:
        try:
            return _CMP[op](float(val), float(rhs))
        except (TypeError, ValueError):
            return False
    return _CMP[op](val, rhs)


def _eval_jsonpath(model, steps: List[tuple]) -> List[int]:
    root = model._root
    # `$` addresses the top-level value. For an eager document the model has
    # already drilled `_root` to that value (object/array), so `$` IS root —
    # don't unwrap again (double-unwrapping broke a single-key wrapper like
    # {"products":[...]}, where `$.products` then matched nothing). Only unwrap
    # a raw Document node holding a single top-level value; a Document with
    # several (NDJSON) stays as-is so `[n]`/`[*]` can index the records.
    if model._doc.kind(root) == DOCUMENT and model._doc.child_count(root) == 1:
        current = _children(model, root)
    else:
        current = [root]

    for step in steps:
        kind = step[0]
        nxt: List[int] = []
        if kind == "key":
            name = step[1]
            for node in current:
                for child in _children(model, node):
                    if (
                        model._doc.kind(child) == KEY
                        and model._key_name(child) == name
                    ):
                        nxt.append(child)
        elif kind == "index":
            idx = step[1]
            for node in current:
                kids = _children(model, node)
                if -len(kids) <= idx < len(kids):
                    nxt.append(kids[idx])
        elif kind == "wildcard":
            for node in current:
                nxt.extend(_children(model, node))
        elif kind == "filter":
            pred = step[1]
            for node in current:
                for child in _children(model, node):
                    if _match_predicate(model, child, pred):
                        nxt.append(child)
        elif kind == "descend":
            name = step[1]
            visited = 0
            for node in current:
                stack = list(_children(model, node))
                while stack and visited < DESCEND_VISIT_CAP:
                    cur = stack.pop()
                    visited += 1
                    is_key = model._doc.kind(cur) == KEY
                    if name is None:
                        nxt.append(cur)
                    elif is_key and model._key_name(cur) == name:
                        nxt.append(cur)
                    stack.extend(_children(model, cur))
        else:  # pragma: no cover
            raise QueryError(f"unknown step {kind}")
        current = nxt
        if len(current) > RESULT_CAP:
            current = current[:RESULT_CAP]
    return current


# ---------------------------------------------------------------------------
# XPath
# ---------------------------------------------------------------------------

def parse_xpath(text: str) -> List[tuple]:
    text = text.strip()
    steps: List[tuple] = []
    i = 0
    n = len(text)
    if i >= n or text[0] != "/":
        # Relative query — treat as child-of-root.
        text = "/" + text
        n = len(text)
    while i < n:
        recursive = False
        if text[i] == "/":
            if i + 1 < n and text[i + 1] == "/":
                recursive = True
                i += 2
            else:
                i += 1
        j = i
        while j < n and text[j] != "/":
            j += 1
        seg = text[i:j].strip()
        i = j
        if not seg:
            if recursive:
                raise QueryError("'//' must be followed by a step")
            continue
        if seg == "*":
            steps.append(("any_elem", recursive))
        elif seg.startswith("@"):
            steps.append(("attr", seg[1:]))
        elif seg == "text()":
            steps.append(("text",))
        else:
            m = re.fullmatch(r"([^\[\]]+)(?:\[(\d+)\])?", seg)
            if not m:
                raise QueryError(f"bad step: {seg}")
            pos = int(m.group(2)) if m.group(2) else None
            steps.append(("elem", m.group(1), pos, recursive))
    return steps


def _elem_children(model, node) -> List[int]:
    return [
        c
        for c in _children(model, node)
        if model._doc.kind(c) == ELEMENT_OPEN
    ]


def _descendant_elems(model, node) -> List[int]:
    out: List[int] = []
    stack = _elem_children(model, node)[::-1]
    visited = 0
    while stack and visited < DESCEND_VISIT_CAP:
        cur = stack.pop()
        visited += 1
        out.append(cur)
        stack.extend(_elem_children(model, cur)[::-1])
    return out


def _eval_xpath(model, steps: List[tuple]) -> List[int]:
    current = [model._root]
    for step in steps:
        kind = step[0]
        nxt: List[int] = []
        if kind == "elem":
            _, tag, pos, recursive = step
            for node in current:
                pool = (
                    _descendant_elems(model, node)
                    if recursive
                    else _elem_children(model, node)
                )
                matched = [
                    c for c in pool if model._doc.raw_text(c) == tag
                ]
                if pos is not None:
                    if 1 <= pos <= len(matched):
                        nxt.append(matched[pos - 1])
                else:
                    nxt.extend(matched)
        elif kind == "any_elem":
            recursive = step[1]
            for node in current:
                nxt.extend(
                    _descendant_elems(model, node)
                    if recursive
                    else _elem_children(model, node)
                )
        elif kind == "attr":
            name = step[1]
            for node in current:
                for c in _children(model, node):
                    if (
                        model._doc.kind(c) == ATTRIBUTE
                        and model._doc.raw_text(c) == name
                    ):
                        nxt.append(c)
        elif kind == "text":
            for node in current:
                for c in _children(model, node):
                    if model._doc.kind(c) in (TEXT, CDATA):
                        nxt.append(c)
        else:  # pragma: no cover
            raise QueryError(f"unknown step {kind}")
        current = nxt
        if len(current) > RESULT_CAP:
            current = current[:RESULT_CAP]
    return current


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def split_union(text: str) -> List[str]:
    """Split on top-level ``|`` (union), ignoring ``|`` inside quotes or
    brackets. So `$.a.name | $.a.price` and `//item/@id | //item/@sku`
    each become two sub-queries whose results are combined."""
    parts: List[str] = []
    depth = 0
    quote = ""
    buf = []
    for ch in text:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = ""
        elif ch in "'\"":
            quote = ch
            buf.append(ch)
        elif ch == "[":
            depth += 1
            buf.append(ch)
        elif ch == "]":
            depth = max(0, depth - 1)
            buf.append(ch)
        elif ch == "|" and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    parts.append("".join(buf))
    return [p.strip() for p in parts if p.strip()]


#: Plain-text shorthand: `key:value` (quotes optional) — the simplest way to
#: ask "field key equals value", anywhere in the document. Not a path (no $ / /
#: prefix, no brackets), so it can't be confused with JSONPath/XPath.
_SHORTHAND = re.compile(r"^[^\s:$/\[\]]+:.+$")

#: Stricter form used to decide whether a *filter box* entry is a key:value
#: query vs. plain substring text: the key must look like an identifier. Keeps
#: substring searches such as "12:30" (starts with a digit) or a URL (has "://")
#: out of the query path.
_FILTER_SHORTHAND = re.compile(r"^[A-Za-z_][\w.\-]*\s*:\s*\S")


def is_structured(text: str, is_xml: bool = False) -> bool:
    """Whether ``text`` should be run as a query rather than a substring
    filter: a JSONPath/XPath path (`$`/`/`), or a key:value shorthand."""
    t = text.strip()
    if not t:
        return False
    if t[0] in "$/":
        return True
    if is_xml or "://" in t:
        return False
    return bool(_FILTER_SHORTHAND.match(t))


def _parse_shorthand(text: str):
    """(key, value) for a `key:value` shorthand, or None if ``text`` isn't
    one. Surrounding quotes on either side are stripped."""
    t = text.strip()
    if t.startswith(("$", "/")) or not _SHORTHAND.match(t):
        return None
    key, _, value = t.partition(":")
    strip = lambda s: s.strip().strip("\"'")  # noqa: E731
    key, value = strip(key), strip(value)
    return (key, value) if key and value else None


def _eval_shorthand(model, key: str, value: str) -> List[int]:
    """Every KEY node named ``key`` whose scalar value equals ``value``,
    anywhere in the document (a bounded descent, like `$..key` + a value test)."""
    out: List[int] = []
    visited = 0
    stack = [model._root]
    while stack and visited < DESCEND_VISIT_CAP and len(out) < RESULT_CAP:
        node = stack.pop()
        visited += 1
        for c in _children(model, node):
            if (
                model._doc.kind(c) == KEY
                and model._key_name(c) == key
                and model.value_of_node(c) == value
            ):
                out.append(c)
            stack.append(c)
    return out


def _evaluate_one(model, text: str, fmt: str) -> List[int]:
    if fmt == "XML":
        return _eval_xpath(model, parse_xpath(text))
    sh = _parse_shorthand(text)
    if sh is not None:
        return _eval_shorthand(model, sh[0], sh[1])
    return _eval_jsonpath(model, parse_jsonpath(text))


def evaluate(model, text: str) -> List[int]:
    """Return the node ids selected by ``text`` for ``model``'s format.
    Multiple paths may be combined with ``|`` (union)."""
    text = text.strip()
    if not text:
        return []
    fmt = model.format()
    seen = set()
    result: List[int] = []
    for part in split_union(text):
        for n in _evaluate_one(model, part, fmt):
            # Never surface the synthetic document root; dedup the union.
            if model._doc.kind(n) != DOCUMENT and n not in seen:
                seen.add(n)
                result.append(n)
    return result


def example_for(fmt: str) -> str:
    return "//item/@id" if fmt == "XML" else "$.response.products[*].name"
