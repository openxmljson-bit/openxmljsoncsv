"""Live tail (tail -f): follow a growing log / NDJSON file, parsing only
the *appended* bytes each time — the already-parsed prefix is never
touched.

Each poll reads the bytes appended since the last commit, keeps up to the
last complete line, parses that slice as its own native ``Document`` (a
"segment"), and appends its top-level records as new tree rows. A
``SegmentedDocument`` presents the base document plus all appended
segments as one document to the model, using composite node ids
(``segment * 2**32 + local``) so nothing else in the app needs to change.

This is genuinely incremental: memory and parse time each tick are
proportional to the newly appended bytes, not the file size — faithful to
the 10 GB design point.
"""

from __future__ import annotations

from typing import List

from openxmljson.model import DOCUMENT

#: Composite id = segment_index * SHIFT + local_node_id.
SHIFT = 1 << 32
#: The segmented root is the base document's root (local 0 → global 0),
#: so wrapping the base leaves every existing node id unchanged.
ROOT = 0


class SegmentedDocument:
    """Presents a base Document + appended chunk Documents as one document.

    Implements the subset of the native ``Document`` API that
    ``DocumentModel`` uses, routing each composite id to its segment.
    """

    def __init__(self, base):
        self._docs = [base]  # segment 0 is the base document

    # -- segment management --------------------------------------------------

    def append_segment(self, doc) -> int:
        """Add a chunk document; return the number of new top-level rows."""
        self._docs.append(doc)
        return doc.child_count(doc.root())

    def segment_count(self) -> int:
        return len(self._docs)

    # -- id coding -----------------------------------------------------------

    @staticmethod
    def _encode(seg: int, local: int) -> int:
        return seg * SHIFT + local

    def _decode(self, node: int):
        return node // SHIFT, node % SHIFT

    # -- Document API (routed) ----------------------------------------------

    def root(self) -> int:
        return ROOT

    def format_name(self) -> str:
        return self._docs[0].format_name()

    def file_bytes(self) -> int:
        return sum(d.file_bytes() for d in self._docs)

    def index_bytes(self) -> int:
        return sum(d.index_bytes() for d in self._docs)

    def node_count(self) -> int:
        return sum(d.node_count() for d in self._docs)

    def _root_children(self) -> List[int]:
        out: List[int] = []
        for seg, doc in enumerate(self._docs):
            for c in doc.child_nodes(doc.root()):
                out.append(self._encode(seg, c))
        return out

    def child_nodes(self, node: int) -> List[int]:
        if node == ROOT:
            return self._root_children()
        seg, local = self._decode(node)
        return [self._encode(seg, c) for c in self._docs[seg].child_nodes(local)]

    def child_count(self, node: int) -> int:
        if node == ROOT:
            return sum(
                d.child_count(d.root()) for d in self._docs
            )
        seg, local = self._decode(node)
        return self._docs[seg].child_count(local)

    def kind(self, node: int) -> int:
        if node == ROOT:
            return DOCUMENT
        seg, local = self._decode(node)
        return self._docs[seg].kind(local)

    def is_expandable(self, node: int) -> bool:
        if node == ROOT:
            return self.child_count(ROOT) > 0
        seg, local = self._decode(node)
        return self._docs[seg].is_expandable(local)

    def display_text(self, node: int) -> str:
        if node == ROOT:
            return ""
        seg, local = self._decode(node)
        return self._docs[seg].display_text(local)

    def raw_text(self, node: int) -> str:
        if node == ROOT:
            return ""
        seg, local = self._decode(node)
        return self._docs[seg].raw_text(local)

    def parent(self, node: int):
        if node == ROOT:
            return None
        seg, local = self._decode(node)
        p = self._docs[seg].parent(local)
        if p is None or p == self._docs[seg].root():
            return ROOT
        return self._encode(seg, p)

    def search(self, pattern: str, scope: str) -> List[int]:
        out: List[int] = []
        for seg, doc in enumerate(self._docs):
            out.extend(self._encode(seg, i) for i in doc.search(pattern, scope))
        return out

    # Source-panel byte helpers are per-segment-file, so cross-segment
    # offsets aren't globally meaningful — the app disables the source
    # panel while a document is segmented. These keep the API total.
    def node_span(self, node: int):
        if node == ROOT:
            return (0, 0)
        seg, local = self._decode(node)
        return self._docs[seg].node_span(local)

    def highlight_span(self, node: int):
        if node == ROOT:
            return (0, 0)
        seg, local = self._decode(node)
        d = self._docs[seg]
        return d.highlight_span(local) if hasattr(d, "highlight_span") \
            else d.node_span(local)

    def node_at_offset(self, offset: int) -> int:
        return self._docs[0].node_at_offset(offset)

    def read_bytes(self, offset: int, length: int) -> str:
        return self._docs[0].read_bytes(offset, length)


def last_complete_line(data: bytes) -> int:
    """Byte length of ``data`` up to and including its last newline, or 0
    if it contains no complete line yet."""
    nl = data.rfind(b"\n")
    return nl + 1 if nl >= 0 else 0
