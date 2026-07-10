"""Mirror of oxj_core::model::highlight_span, exercised against real
indexes from the validator's exact XML parser port. Verifies that source-
panel highlight spans cover whole elements / attributes / CDATA — the
same arithmetic the Rust engine runs.

Usage: python test_highlight_span.py
"""

from __future__ import annotations

import index_port as ip
import xml_port

WS = b" \t\n\r"


def leaf_raw_end(idx: ip.Index, node: int) -> int:
    n = idx.node(node)
    if n.kind == ip.CDATA:
        return n.offset + n.len + 3
    if n.kind == ip.ATTRIBUTE:
        if n.first_child == ip.NIL:
            return n.offset + n.len
        v = idx.node(n.first_child)
        return v.offset + v.len + 1
    return n.offset + n.len


def scan_gt(data: bytes, pos: int) -> int:
    quote = 0
    while pos < len(data):
        b = data[pos]
        if quote:
            if b == quote:
                quote = 0
        elif b in (0x22, 0x27):
            quote = b
        elif b == 0x3E:
            return pos + 1
        pos += 1
    return len(data)


def scan_close_tag(data: bytes, pos: int) -> int:
    n = len(data)
    while True:
        while pos < n and data[pos] in WS:
            pos += 1
        if data.startswith(b"<!--", pos):
            p = data.find(b"-->", pos + 4)
            if p < 0:
                return pos
            pos = p + 3
        elif data.startswith(b"<?", pos):
            p = data.find(b"?>", pos + 2)
            if p < 0:
                return pos
            pos = p + 2
        elif data.startswith(b"</", pos):
            i = pos + 2
            while i < n and data[i] != 0x3E:
                i += 1
            return min(i + 1, n)
        else:
            return pos


def highlight_span(data: bytes, idx: ip.Index, node: int):
    nd = idx.node(node)
    if nd.kind == ip.ELEMENT_OPEN:
        start = max(nd.offset - 1, 0)
        stack = [node]
        leaf_end = None
        while True:
            cur = stack[-1]
            last_body = ip.NIL
            for c in idx.children(cur):
                if idx.node(c).kind != ip.ATTRIBUTE:
                    last_body = c
            if last_body == ip.NIL:
                break
            if idx.node(last_body).kind == ip.ELEMENT_OPEN:
                stack.append(last_body)
            else:
                leaf_end = leaf_raw_end(idx, last_body)
                break
        if leaf_end is not None:
            pos = leaf_end
        else:
            deepest = idx.node(stack[-1])
            pos = scan_gt(data, deepest.offset + deepest.len)
            if pos >= 2 and data[pos - 2] == 0x2F:  # '/'
                stack.pop()
        for _ in range(len(stack)):
            pos = scan_close_tag(data, pos)
        return (start, pos - start)
    if nd.kind == ip.ATTRIBUTE:
        return (nd.offset, leaf_raw_end(idx, node) - nd.offset)
    if nd.kind == ip.CDATA:
        start = max(nd.offset - 9, 0)
        return (start, nd.offset + nd.len + 3 - start)
    return (nd.offset, nd.len)


def span_str(data: bytes, idx: ip.Index, node: int) -> str:
    off, length = highlight_span(data, idx, node)
    return data[off : off + length].decode()


def main() -> None:
    src = (
        b"<root a=\"1\"><b>hi</b><c/><d x='2'></d>\n"
        b"  <!-- t --> <e><f>y</f></e></root>"
    )
    idx = xml_port.parse_xml(src)
    root = idx.node(idx.root()).first_child
    assert span_str(src, idx, root) == src.decode(), span_str(src, idx, root)

    elements = [
        k
        for k in idx.children(root)
        if idx.node(k).kind == ip.ELEMENT_OPEN
    ]
    assert span_str(src, idx, elements[0]) == "<b>hi</b>"
    assert span_str(src, idx, elements[1]) == "<c/>"
    assert span_str(src, idx, elements[2]) == "<d x='2'></d>"
    assert span_str(src, idx, elements[3]) == "<e><f>y</f></e>"

    attr = idx.node(root).first_child
    assert idx.node(attr).kind == ip.ATTRIBUTE
    assert span_str(src, idx, attr) == 'a="1"'

    src2 = b"<a><![CDATA[x < y]]></a>"
    idx2 = xml_port.parse_xml(src2)
    a = idx2.node(idx2.root()).first_child
    cd = idx2.node(a).first_child
    assert span_str(src2, idx2, cd) == "<![CDATA[x < y]]>"
    assert span_str(src2, idx2, a) == src2.decode()

    # Attribute value containing '>' must not end the open tag early.
    src3 = b"<a b=\"x>y\"><c/></a>"
    idx3 = xml_port.parse_xml(src3)
    a3 = idx3.node(idx3.root()).first_child
    assert span_str(src3, idx3, a3) == src3.decode()

    # Deep nesting with mixed text/comments.
    src4 = b"<a><b><c>deep</c><!-- x --></b> tail <d/></a>"
    idx4 = xml_port.parse_xml(src4)
    a4 = idx4.node(idx4.root()).first_child
    assert span_str(src4, idx4, a4) == src4.decode()
    b4 = idx4.node(a4).first_child
    assert span_str(src4, idx4, b4) == "<b><c>deep</c><!-- x --></b>"

    print("test_highlight_span: PASS")


if __name__ == "__main__":
    main()
