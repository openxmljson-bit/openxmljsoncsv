"""Exact Python port of crates/oxj-core/src/index.rs (SPEC §15).

Used by the parser ports so their build logic is byte-for-byte the same as
the Rust engine's. Kept deliberately literal — clarity over cleverness.
"""

from __future__ import annotations

from typing import Iterator, List

NIL = 0xFFFFFFFF
MAX_DEPTH = 4096

# NodeKind discriminants (stable; SPEC §5.1).
DOCUMENT = 0
OBJECT = 1
ARRAY = 2
KEY = 3
STRING = 4
NUMBER = 5
BOOL = 6
NULL = 7
ELEMENT_OPEN = 8
ELEMENT_CLOSE = 9
ELEMENT_SELF_CLOSE = 10
TEXT = 11
ATTRIBUTE = 12
CDATA = 13
COMMENT = 14

KIND_NAMES = {
    DOCUMENT: "Document",
    OBJECT: "Object",
    ARRAY: "Array",
    KEY: "Key",
    STRING: "String",
    NUMBER: "Number",
    BOOL: "Bool",
    NULL: "Null",
    ELEMENT_OPEN: "ElementOpen",
    ELEMENT_CLOSE: "ElementClose",
    ELEMENT_SELF_CLOSE: "ElementSelfClose",
    TEXT: "Text",
    ATTRIBUTE: "Attribute",
    CDATA: "CData",
    COMMENT: "Comment",
}

CONTAINERS = {DOCUMENT, OBJECT, ARRAY, KEY, ELEMENT_OPEN}


class Node:
    __slots__ = ("offset", "len", "parent", "first_child", "next_sibling", "kind")

    def __init__(self, kind: int, offset: int, length: int):
        self.offset = offset
        self.len = length
        self.parent = NIL
        self.first_child = NIL
        self.next_sibling = NIL
        self.kind = kind

    def __repr__(self):
        return (
            f"Node({KIND_NAMES[self.kind]}, off={self.offset}, len={self.len})"
        )


class Index:
    def __init__(self, nodes: List[Node]):
        self.nodes = nodes

    def root(self) -> int:
        return 0

    def node(self, i: int) -> Node:
        return self.nodes[i]

    def __len__(self) -> int:
        return len(self.nodes)

    def children(self, parent: int) -> Iterator[int]:
        cur = self.nodes[parent].first_child
        while cur != NIL:
            yield cur
            cur = self.nodes[cur].next_sibling

    def child_count(self, parent: int) -> int:
        return sum(1 for _ in self.children(parent))


class IndexBuilder:
    """Port of oxj_core::IndexBuilder — same operations, same linking."""

    def __init__(self):
        self.nodes: List[Node] = [Node(DOCUMENT, 0, 0)]
        self.stack: List[int] = [0]
        self.last_child: List[int] = [NIL]

    def _push_node(self, kind: int, offset: int, length: int) -> int:
        idx = len(self.nodes)
        parent = self.stack[-1]
        node = Node(kind, offset, length)
        node.parent = parent
        self.nodes.append(node)
        last = self.last_child[-1]
        if last == NIL:
            self.nodes[parent].first_child = idx
        else:
            self.nodes[last].next_sibling = idx
        self.last_child[-1] = idx
        return idx

    def leaf(self, kind: int, offset: int, length: int) -> int:
        return self._push_node(kind, offset, length)

    def open(self, kind: int, offset: int) -> int:
        idx = self._push_node(kind, offset, 0)
        self.stack.append(idx)
        self.last_child.append(NIL)
        return idx

    def open_fixed(self, kind: int, offset: int, length: int) -> int:
        idx = self._push_node(kind, offset, length)
        self.stack.append(idx)
        self.last_child.append(NIL)
        return idx

    def close(self, end: int) -> None:
        idx = self.stack.pop()
        self.last_child.pop()
        node = self.nodes[idx]
        node.len = min(max(end - node.offset, 0), NIL)

    def pop(self) -> None:
        self.stack.pop()
        self.last_child.pop()

    def depth(self) -> int:
        return len(self.stack) - 1

    def top_window(self):
        idx = self.stack[-1]
        n = self.nodes[idx]
        return (n.kind, n.offset, n.len)

    def top_offset(self) -> int:
        return self.nodes[self.stack[-1]].offset

    def finish(self, total_len: int) -> Index:
        assert len(self.stack) == 1, "unclosed containers at finish"
        self.nodes[0].len = min(total_len, NIL)
        return Index(self.nodes)
