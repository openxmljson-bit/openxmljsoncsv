"""Exact Python port of crates/oxj-core/src/xml.rs (SPEC §15).

Cross-checked against xml.etree by validate_xml.py.
"""

from __future__ import annotations

from index_port import (
    ATTRIBUTE,
    CDATA,
    ELEMENT_OPEN,
    MAX_DEPTH,
    STRING,
    TEXT,
    Index,
    IndexBuilder,
)

WS = b" \t\n\r"


class XmlError(Exception):
    def __init__(self, offset: int, message: str):
        super().__init__(f"{message} at byte {offset}")
        self.offset = offset
        self.message = message


def _is_ws(b: int) -> bool:
    return b in WS


def _is_name_end(b: int) -> bool:
    return b in WS or b in b"/>="


def _all_ws(data: bytes) -> bool:
    return all(b in WS for b in data)


def parse_xml(data: bytes) -> Index:
    n = len(data)
    pos = 0
    if data.startswith(b"\xef\xbb\xbf"):
        pos = 3

    builder = IndexBuilder()

    while pos < n:
        # ---- character data up to the next '<' -------------------------
        text_start = pos
        while pos < n and data[pos] != 0x3C:  # '<'
            pos += 1
        if pos > text_start and not _all_ws(data[text_start:pos]):
            builder.leaf(TEXT, text_start, pos - text_start)
        if pos >= n:
            break

        # ---- markup, data[pos] == '<' -----------------------------------
        if data.startswith(b"<!--", pos):
            end = data.find(b"-->", pos + 4)
            if end < 0:
                raise XmlError(pos, "unterminated comment")
            pos = end + 3  # comments are skipped
        elif data.startswith(b"<![CDATA[", pos):
            inner = pos + 9
            end = data.find(b"]]>", inner)
            if end < 0:
                raise XmlError(pos, "unterminated CDATA section")
            builder.leaf(CDATA, inner, end - inner)
            pos = end + 3
        elif data.startswith(b"<!", pos):
            i = pos + 2
            brackets = 0
            while True:
                if i >= n:
                    raise XmlError(pos, "unterminated doctype")
                b = data[i]
                if b == 0x5B:  # '['
                    brackets += 1
                elif b == 0x5D:  # ']'
                    brackets -= 1
                elif b == 0x3E and brackets == 0:  # '>'
                    break
                i += 1
            pos = i + 1
        elif data.startswith(b"<?", pos):
            end = data.find(b"?>", pos + 2)
            if end < 0:
                raise XmlError(pos, "unterminated processing instruction")
            pos = end + 2
        elif data.startswith(b"</", pos):
            i = pos + 2
            name_start = i
            while i < n and not _is_ws(data[i]) and data[i] != 0x3E:
                i += 1
            if i == name_start:
                raise XmlError(name_start, "expected tag name")
            name = data[name_start:i]
            while i < n and _is_ws(data[i]):
                i += 1
            if i >= n or data[i] != 0x3E:
                raise XmlError(min(i, n), "expected '>'")
            if builder.depth() == 0:
                raise XmlError(pos, "unexpected close tag")
            kind, off, length = builder.top_window()
            open_name = data[off : off + length]
            if kind != ELEMENT_OPEN or open_name != name:
                raise XmlError(pos, "mismatched close tag")
            builder.pop()
            pos = i + 1
        else:
            # Open tag.
            i = pos + 1
            name_start = i
            while i < n and not _is_name_end(data[i]):
                i += 1
            if i == name_start:
                raise XmlError(name_start, "expected tag name")
            if builder.depth() >= MAX_DEPTH:
                raise XmlError(pos, "nesting too deep")
            builder.open_fixed(ELEMENT_OPEN, name_start, i - name_start)
            # Attribute loop.
            while True:
                while i < n and _is_ws(data[i]):
                    i += 1
                if i >= n:
                    raise XmlError(pos, "unterminated tag")
                b = data[i]
                if b == 0x3E:  # '>'
                    i += 1
                    break
                if b == 0x2F:  # '/'
                    if i + 1 >= n or data[i + 1] != 0x3E:
                        raise XmlError(i + 1, "expected '>'")
                    builder.pop()
                    i += 2
                    break
                if b == 0x3D:  # '='
                    raise XmlError(i, "expected attribute name")
                aname_start = i
                while i < n and not _is_name_end(data[i]):
                    i += 1
                aname_len = i - aname_start
                while i < n and _is_ws(data[i]):
                    i += 1
                if i >= n or data[i] != 0x3D:
                    raise XmlError(min(i, n), "expected '='")
                i += 1
                while i < n and _is_ws(data[i]):
                    i += 1
                if i >= n or data[i] not in b"\"'":
                    raise XmlError(min(i, n), "expected quoted attribute value")
                quote = data[i]
                q_off = i
                i += 1
                v_start = i
                while i < n and data[i] != quote:
                    i += 1
                if i >= n:
                    raise XmlError(q_off, "unterminated attribute value")
                builder.open_fixed(ATTRIBUTE, aname_start, aname_len)
                builder.leaf(STRING, v_start, i - v_start)
                builder.pop()
                i += 1
            pos = i

    if builder.depth() > 0:
        raise XmlError(builder.top_offset(), "unclosed element")
    return builder.finish(n)
