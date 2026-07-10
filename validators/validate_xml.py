"""Validate the XML parser port against xml.etree (SPEC §15).

- Edge cases: rejection must agree with xml.etree for malformed inputs;
  structural cases (CDATA, self-closing, comments, PIs, doctype,
  namespaces-kept-verbatim) are checked explicitly.
- Fuzz: 4,000 random well-formed documents; the canonical tree built from
  the port's index must equal the one built from xml.etree.

The generator uses only characters that need no entity escaping, because
the engine deliberately does not decode entities (SPEC §2.2) while etree
does.

Usage: python validate_xml.py [n_docs]
"""

from __future__ import annotations

import random
import sys
import xml.etree.ElementTree as ET

import xml_port
from index_port import ATTRIBUTE, CDATA, ELEMENT_OPEN, STRING, TEXT

WS = set(" \t\n\r")


# ---------------------------------------------------------------------------
# Canonical form: (tag, {attr: value}, [item...]) where an item is either a
# text string or a child element tuple. Adjacent text (incl. CDATA) merges;
# whitespace-only text is dropped on both sides.
# ---------------------------------------------------------------------------

def _push_text(items, text: str) -> None:
    if items and isinstance(items[-1], str):
        items[-1] += text
    else:
        items.append(text)


def _finish_items(items):
    # The engine drops whitespace-only text runs (SPEC §7.2) while etree
    # merges them into adjacent text across comment/CDATA boundaries, so
    # boundary whitespace is canonicalized away on BOTH sides: text items
    # are stripped, and items that were only whitespace disappear.
    out = []
    for it in items:
        if isinstance(it, str):
            it = it.strip(" \t\n\r")
            if not it:
                continue
        out.append(it)
    return out


def canon_port(idx, data: bytes, node: int):
    n = idx.node(node)
    assert n.kind == ELEMENT_OPEN
    tag = data[n.offset : n.offset + n.len].decode("utf-8")
    attrs = {}
    items: list = []
    for kid in idx.children(node):
        k = idx.node(kid)
        if k.kind == ATTRIBUTE:
            name = data[k.offset : k.offset + k.len].decode("utf-8")
            kids = list(idx.children(kid))
            assert len(kids) == 1 and idx.node(kids[0]).kind == STRING
            v = idx.node(kids[0])
            attrs[name] = data[v.offset : v.offset + v.len].decode("utf-8")
        elif k.kind in (TEXT, CDATA):
            _push_text(items, data[k.offset : k.offset + k.len].decode("utf-8"))
        elif k.kind == ELEMENT_OPEN:
            items.append(canon_port(idx, data, kid))
        else:
            raise AssertionError(f"unexpected child kind {k.kind}")
    return (tag, attrs, _finish_items(items))


def canon_ref(elem):
    items: list = []
    if elem.text:
        _push_text(items, elem.text)
    for child in elem:
        items.append(canon_ref(child))
        if child.tail:
            _push_text(items, child.tail)
    return (elem.tag, dict(elem.attrib), _finish_items(items))


def port_roots(idx, data: bytes):
    return [
        canon_port(idx, data, kid)
        for kid in idx.children(idx.root())
        if idx.node(kid).kind == ELEMENT_OPEN
    ]


# ---------------------------------------------------------------------------
# Fuzz generator — well-formed by construction, entity-free character sets.
# ---------------------------------------------------------------------------

NAME_START = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_"
NAME_CHARS = NAME_START + "0123456789-."
TEXT_CHARS = "abcdefghijklmnopqrstuvwxyz0123456789 .,:!?()-_=+*#éü世界"
ATTR_CHARS = "abcdefghijklmnopqrstuvwxyz0123456789 .,:!?()-_*#éü"  # no quotes


def rand_name(rng: random.Random) -> str:
    # No namespace prefixes here: etree resolves (or rejects) them while the
    # engine keeps them verbatim by design (SPEC §7.2); prefix handling is
    # covered by an explicit port-only edge case instead.
    return rng.choice(NAME_START) + "".join(
        rng.choice(NAME_CHARS) for _ in range(rng.randint(0, 8))
    )

def rand_text(rng: random.Random) -> str:
    return "".join(rng.choice(TEXT_CHARS) for _ in range(rng.randint(1, 20)))


def rand_element(rng: random.Random, depth: int) -> str:
    tag = rand_name(rng)
    n_attrs = rng.randint(0, 3)
    names = []
    while len(names) < n_attrs:
        a = rand_name(rng)
        if a not in names and not a.startswith(("xmlns", "xml")):
            names.append(a)
    attrs = ""
    for a in names:
        value = "".join(rng.choice(ATTR_CHARS) for _ in range(rng.randint(0, 10)))
        quote = "'" if rng.random() < 0.3 else '"'
        attrs += f" {a}={quote}{value}{quote}"
    if depth <= 0 or rng.random() < 0.25:
        if rng.random() < 0.5:
            return f"<{tag}{attrs}/>"
        return f"<{tag}{attrs}></{tag}>"
    # Build the content as items first: whitespace-only runs are only
    # emitted BETWEEN elements. Anywhere else the engine's
    # drop-ws-only-runs policy (SPEC §7.2) and etree's text merging
    # across comment/CDATA boundaries would disagree about boundary
    # whitespace by design.
    items = []
    for _ in range(rng.randint(1, 4)):
        r = rng.random()
        if r < 0.35:
            items.append(("text", rand_text(rng)))
        elif r < 0.75:
            items.append(("elem", rand_element(rng, depth - 1)))
        elif r < 0.85:
            items.append(("text", f"<![CDATA[{rand_text(rng)}]]>"))
        else:
            # "--" is illegal inside a comment; the port skips comments
            # wholesale either way, so keep the generator well-formed.
            items.append(("text", f"<!-- {rand_text(rng).replace('-', '_')} -->"))
    inner = ""
    prev_kind = None
    for kind, chunk in items:
        if prev_kind == "elem" and kind == "elem" and rng.random() < 0.4:
            inner += "\n  "
        inner += chunk
        prev_kind = kind
    return f"<{tag}{attrs}>{inner}</{tag}>"


def rand_document(rng: random.Random) -> str:
    doc = ""
    if rng.random() < 0.3:
        doc += "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
    if rng.random() < 0.1:
        doc += "<!DOCTYPE root>"
    if rng.random() < 0.2:
        doc += "\n"
    doc += rand_element(rng, rng.randint(0, 5))
    if rng.random() < 0.2:
        doc += "\n"
    return doc


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

# Both the port and etree must REJECT these.
REJECT_BOTH = [
    "<a></b>",              # mismatched close tag
    "<a>",                  # unclosed element
    "</a>",                 # close without open
    "<a><b></a></b>",       # interleaved
    "<a b></a>",            # attribute without value
    "<a b=c></a>",          # unquoted attribute value
    '<a b="c></a>',         # unterminated attribute value
    "<a",                   # unterminated tag
    "<!-- x",               # unterminated comment (etree: no element found)
    "<a><![CDATA[x]]>",     # unclosed element
    "<></>",                # empty tag name
]


def run_edge_cases() -> None:
    for src in REJECT_BOTH:
        try:
            ET.fromstring(src)
            raise AssertionError(f"etree unexpectedly accepts {src!r}")
        except ET.ParseError:
            pass
        try:
            xml_port.parse_xml(src.encode())
            raise AssertionError(f"port unexpectedly accepts {src!r}")
        except xml_port.XmlError:
            pass

    # Structure: element window is the tag name; attribute owns its value.
    src = b'<r a="1"><b/>text<![CDATA[cd]]></r>'
    idx = xml_port.parse_xml(src)
    got = port_roots(idx, src)
    want = [canon_ref(ET.fromstring(src.decode()))]
    assert got == want, f"{got!r} != {want!r}"

    # Namespace prefixes are kept verbatim (etree would resolve them, so
    # this is checked against the port only; SPEC §7.2).
    src = b'<p:a xmlns:p="urn:x"><p:b/></p:a>'
    idx = xml_port.parse_xml(src)
    root_id = next(iter(idx.children(idx.root())))
    n = idx.node(root_id)
    assert src[n.offset : n.offset + n.len] == b"p:a"

    # Offsets: mismatched close reported at the '<' of the close tag.
    try:
        xml_port.parse_xml(b"<a></b>")
    except xml_port.XmlError as e:
        assert e.offset == 3 and e.message == "mismatched close tag"

    # Deep nesting without recursion.
    deep = ("<a>" * 2500) + ("</a>" * 2500)
    idx = xml_port.parse_xml(deep.encode())
    assert len(idx) == 2501
    print(f"edge cases: OK ({len(REJECT_BOTH)} rejections + structure checks)")


def run_fuzz(n_docs: int, seed: int = 20260702) -> None:
    rng = random.Random(seed)
    ok = 0
    for i in range(n_docs):
        text = rand_document(rng)
        data = text.encode("utf-8")
        ref = canon_ref(ET.fromstring(text))
        idx = xml_port.parse_xml(data)
        got = port_roots(idx, data)
        assert got == [ref], (
            f"fuzz doc {i} mismatch:\n{text[:400]}\nport: {got!r}\nref:  {[ref]!r}"
        )
        ok += 1
    print(f"fuzz: {ok} / {n_docs} documents identical to xml.etree")


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 4000
    run_edge_cases()
    run_fuzz(n)
    print("validate_xml: PASS")


if __name__ == "__main__":
    main()
