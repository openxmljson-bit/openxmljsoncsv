"""Validate the JSON parser port against stdlib json (SPEC §15).

- Edge cases: acceptance AND rejection must agree with stdlib json
  (except the documented NDJSON extension, tested separately).
- Fuzz: 5,000 random documents; the tree reconstructed from the port's
  index must equal json.loads of the same text.

Usage: python validate_json.py [n_docs]
"""

from __future__ import annotations

import json
import random
import sys

import json_port
from index_port import ARRAY, BOOL, DOCUMENT, KEY, NULL, NUMBER, OBJECT, STRING


# ---------------------------------------------------------------------------
# Reconstruction: index + bytes -> Python value(s)
# ---------------------------------------------------------------------------

def _value_of(idx, data: bytes, node: int):
    n = idx.node(node)
    window = data[n.offset : n.offset + n.len]
    if n.kind == OBJECT:
        out = {}
        for kid in idx.children(node):
            k = idx.node(kid)
            assert k.kind == KEY, "object child must be a Key"
            kids = list(idx.children(kid))
            assert len(kids) == 1, "a Key owns exactly one value"
            name = json.loads(data[k.offset : k.offset + k.len].decode("utf-8"))
            out[name] = _value_of(idx, data, kids[0])
        return out
    if n.kind == ARRAY:
        return [_value_of(idx, data, kid) for kid in idx.children(node)]
    if n.kind in (STRING, NUMBER, BOOL, NULL):
        return json.loads(window.decode("utf-8"))
    raise AssertionError(f"unexpected kind {n.kind}")


def reconstruct(idx, data: bytes):
    root = idx.root()
    assert idx.node(root).kind == DOCUMENT
    return [_value_of(idx, data, kid) for kid in idx.children(root)]


# ---------------------------------------------------------------------------
# Fuzz generator
# ---------------------------------------------------------------------------

STR_POOL = (
    "abcdefghijklmnopqrstuvwxyz0123456789 _-.,:;!?/\\\"'\n\téü世界\U0001f600"
)


def rand_string(rng: random.Random) -> str:
    return "".join(rng.choice(STR_POOL) for _ in range(rng.randint(0, 12)))


def rand_value(rng: random.Random, depth: int):
    if depth <= 0:
        kind = rng.randint(0, 3)
    else:
        kind = rng.randint(0, 5)
    if kind == 0:
        return rng.choice([None, True, False])
    if kind == 1:
        r = rng.random()
        if r < 0.4:
            return rng.randint(-10**12, 10**12)
        if r < 0.8:
            return round(rng.uniform(-1e6, 1e6), rng.randint(0, 8))
        return rng.uniform(-1e30, 1e30)
    if kind in (2, 3):
        return rand_string(rng)
    if kind == 4:
        return [rand_value(rng, depth - 1) for _ in range(rng.randint(0, 6))]
    return {
        rand_string(rng): rand_value(rng, depth - 1)
        for _ in range(rng.randint(0, 6))
    }


def rand_dump(rng: random.Random, value) -> str:
    style = rng.randint(0, 3)
    ensure_ascii = rng.random() < 0.5
    if style == 0:
        return json.dumps(value, ensure_ascii=ensure_ascii)
    if style == 1:
        return json.dumps(value, ensure_ascii=ensure_ascii, separators=(",", ":"))
    if style == 2:
        return json.dumps(value, ensure_ascii=ensure_ascii, indent=rng.randint(1, 4))
    return json.dumps(value, ensure_ascii=ensure_ascii, separators=(" , ", " : "))


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

# Both the port and stdlib must ACCEPT these, with equal values.
ACCEPT = [
    "0", "-0", "0.5", "1e10", "1E-2", "123.456e+7", "-1.5e+10",
    '"hi"', '""', '"a\\"b\\\\c"', '"\\u0041\\ud83d\\ude00"', '"\\n\\t\\b\\f\\r\\/"',
    "true", "false", "null",
    "[]", "{}", "[1,2,3]", '{"a":1}', '{"a":{"b":[1,{"c":null}]}}',
    ' { "a" : [ 1 , 2 ] } ', "[[[[[]]]]]", '{"":""}',
    '{"a":1,"a":2}',  # duplicate keys: last wins, like stdlib
    "[0.0,-0.0,1e-300,1e300]",
]

# Both the port and stdlib must REJECT these.
REJECT = [
    "", "   ", "[1,2,]", '{"a":1,}', "{", "[", "]", "}", ",", ":",
    '"abc', '{"a"}', '{"a":}', "{a:1}", "{'a':1}", "tru", "falsee?x",
    "nul", "01", "1.", ".5", "+1", "-", "1e", "1e+", "--1", "1..2",
    "[1 2]", '{"a":1 "b":2}', "[,1]", "{,}", '{"a",}', "[1,,2]",
    '{"a"::1}', "01x", '["a"',
]

# The documented NDJSON extension: port accepts, stdlib does not.
NDJSON = [
    ('{"a":1}\n{"b":2}', [{"a": 1}, {"b": 2}]),
    ("1 2 3", [1, 2, 3]),
    ('{"a":1}\n', [{"a": 1}]),
    ("[1]\t[2]\r\n[3]", [[1], [2], [3]]),
]


def run_edge_cases() -> None:
    for src in ACCEPT:
        want = json.loads(src)
        got = reconstruct(json_port.parse(src.encode()), src.encode())
        assert got == [want], f"ACCEPT mismatch on {src!r}: {got!r} != {[want]!r}"
    for src in REJECT:
        try:
            json.loads(src)
            raise AssertionError(f"stdlib unexpectedly accepts {src!r}")
        except json.JSONDecodeError:
            pass
        try:
            json_port.parse(src.encode())
            raise AssertionError(f"port unexpectedly accepts {src!r}")
        except json_port.ParseError:
            pass
    for src, want in NDJSON:
        got = reconstruct(json_port.parse(src.encode()), src.encode())
        assert got == want, f"NDJSON mismatch on {src!r}"
    # Rejections carry byte offsets.
    try:
        json_port.parse(b"[1,2,]")
    except json_port.ParseError as e:
        assert e.offset == 5 and e.message == "trailing comma"
    # BOM.
    got = reconstruct(json_port.parse(b'\xef\xbb\xbf{"a": 1}'), b'\xef\xbb\xbf{"a": 1}')
    assert got == [{"a": 1}]
    # Deep nesting (no recursion in the port either).
    deep = ("[" * 2500) + "1" + ("]" * 2500)
    idx = json_port.parse(deep.encode())
    assert len(idx) == 2502
    print("edge cases: OK "
          f"({len(ACCEPT)} accept, {len(REJECT)} reject, {len(NDJSON)} ndjson)")


def run_fuzz(n_docs: int, seed: int = 20260702) -> None:
    rng = random.Random(seed)
    ok = 0
    for i in range(n_docs):
        value = rand_value(rng, rng.randint(0, 5))
        if rng.random() < 0.1:
            # NDJSON document: several top-level values.
            values = [value] + [rand_value(rng, 2) for _ in range(rng.randint(1, 3))]
            text = "\n".join(rand_dump(rng, v) for v in values)
            want = values
        else:
            text = rand_dump(rng, value)
            want = [value]
        data = text.encode("utf-8")
        got = reconstruct(json_port.parse(data), data)
        assert got == want, f"fuzz doc {i} mismatch:\n{text[:400]}"
        ok += 1
    print(f"fuzz: {ok} / {n_docs} documents identical to stdlib json")


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 5000
    run_edge_cases()
    run_fuzz(n)
    print("validate_json: PASS")


if __name__ == "__main__":
    main()
