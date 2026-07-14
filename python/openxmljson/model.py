"""DocumentModel — a QAbstractItemModel over the native Document (SPEC §11.1).

QTreeView is inherently virtualized (with uniform row heights on), so the
model only ever answers rowCount/hasChildren/index/parent/data for rows Qt
actually paints.

Because the display tree drills through Key/Attribute nodes, a child's
*display* parent may differ from its structural parent. Qt always creates a
parent index before its children, so the model records the display parent
when it hands out each child index; ``parent()`` is then always answerable.
Children lists are cached per node so ``index(row, …)`` is O(1).

Rows are additionally exposed as typed segments (SegmentsRole) so the view
can color keys / strings / numbers / booleans / nulls / placeholders, and
array elements are labeled with their index (``[0] : {...}``) like the
reference viewer.
"""

from __future__ import annotations

import json
import re
from itertools import count, repeat
from typing import Dict, List, Optional, Set, Tuple

from PySide6.QtCore import (
    QAbstractItemModel,
    QModelIndex,
    QSortFilterProxyModel,
    Qt,
)
from PySide6.QtGui import QBrush, QColor

HIGHLIGHT = QColor(255, 240, 120)

#: Custom role: list of (role-name, text) segments for colored painting.
SegmentsRole = int(Qt.ItemDataRole.UserRole) + 1

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
TEXT = 11
ATTRIBUTE = 12
CDATA = 13

_SCALARS = (STRING, NUMBER, BOOL, NULL)

#: Drilling makes the single root container's children the top-level rows.
#: That forces the model to materialize *all* of those children on the first
#: paint (marshal the id list + build the row/parent maps), so it is only
#: worth doing when the container is modest. Above this many children the
#: root "[" / "{" row is kept instead, so huge files still open instantly.
_DRILL_CHILD_LIMIT = 20_000

KIND_NAMES = {
    DOCUMENT: "Document",
    OBJECT: "Object",
    ARRAY: "Array",
    KEY: "Member",
    STRING: "String",
    NUMBER: "Number",
    BOOL: "Boolean",
    NULL: "Null",
    ELEMENT_OPEN: "Element",
    TEXT: "Text",
    ATTRIBUTE: "Attribute",
    CDATA: "CDATA",
}

#: Jump-to-path tokens: .name  |  ["name"]  |  [123]
_PATH_TOKEN = re.compile(
    r"\.([A-Za-z_][\w\-]*)|\[\"((?:[^\"\\]|\\.)*)\"\]|\[(\d+)\]"
)


def _classify_value(text: str) -> str:
    """Map an engine-rendered scalar to a color role by shape."""
    if text.startswith('"'):
        return "string"
    if text in ("true", "false"):
        return "bool"
    if text == "null":
        return "null"
    return "number"


class DocumentModel(QAbstractItemModel):
    def __init__(self, document, parent=None):
        super().__init__(parent)
        self._doc = document
        self._root = document.root()
        self._format = document.format_name()  # "JSON" | "XML" | "CSV" | "TSV"
        # Drill through a single top-level JSON container so its items/keys
        # are the top-level rows — no redundant root "[" / "{" row (matches
        # Dadroit). Only JSON Object/Array (not XML elements, not multi-value
        # NDJSON); the whole model is anchored on self._root, so paths,
        # filtering and navigation stay consistent relative to the new root.
        #
        # Skipped for lazy documents: making the container the root forces the
        # tree to parse the entire top level (millions of elements) on open,
        # which stalls a multi-GB file. Lazy docs keep the single root row and
        # parse its children only when it is expanded — so huge files still
        # open instantly. (StubDocument in tests is treated as eager.)
        #
        # Also skipped when the container has more than _DRILL_CHILD_LIMIT
        # children: even for an eager (already-parsed) document, drilling
        # makes the first paint materialize the whole child list (marshal +
        # row/parent maps) on the GUI thread, which is the load stall on files
        # with a huge top-level array/object. child_count() is a cheap Rust
        # walk (no marshaling), so it is safe to consult here.
        if type(self._doc).__name__ != "LazyDocument":
            top = list(self._doc.child_nodes(self._root))
            if (
                len(top) == 1
                and self._doc.kind(top[0]) in (OBJECT, ARRAY)
                and self._doc.child_count(top[0]) <= _DRILL_CHILD_LIMIT
            ):
                self._root = top[0]
        # node -> ordered display children (drilled), lazily filled.
        self._children: Dict[int, List[int]] = {}
        # node -> display parent node (recorded when the index is created).
        self._display_parent: Dict[int, int] = {}
        # row of node within its display parent's child list.
        self._row_of: Dict[int, int] = {}
        self._segments: Dict[int, List[Tuple[str, str]]] = {}
        self._matches: Set[int] = set()
        self._match_brush = QBrush(HIGHLIGHT)

    # -- match highlighting ---------------------------------------------------

    def set_matches(self, node_ids) -> None:
        """Highlight the given native node ids (BackgroundRole)."""
        self._matches = set(node_ids)
        if self.rowCount(QModelIndex()) > 0:
            top_left = self.index(0, 0, QModelIndex())
            self.dataChanged.emit(
                top_left, top_left, [Qt.ItemDataRole.BackgroundRole]
            )
            self.layoutChanged.emit()

    def set_match_color(self, color: QColor) -> None:
        self._match_brush = QBrush(color)

    def is_match(self, node: int) -> bool:
        return node in self._matches

    # -- row formatting ---------------------------------------------------------

    def _key_name(self, node: int) -> str:
        """Decoded member/column name — mirrors the engine's key_name."""
        raw = self._doc.raw_text(node)
        if len(raw) >= 2 and raw.startswith('"') and raw.endswith('"'):
            try:
                return json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                return raw[1:-1]
        if self._format in ("CSV", "TSV"):
            return raw.replace('""', '"')
        return raw

    def _segments_for(self, node: int, row: int) -> List[Tuple[str, str]]:
        kind = self._doc.kind(node)
        segs: List[Tuple[str, str]] = []

        # Array/document elements are labeled with their index, like the
        # reference viewer's "[0] : {...}" rows (JSON/CSV only — object
        # members are always Keys, so any non-Key child sits in an array
        # or at the top level). The label is dropped for a lone root value:
        # a single top-level object needs no "[0]".
        if self._format != "XML" and kind in (OBJECT, ARRAY) + _SCALARS:
            display_parent = self._display_parent.get(node, self._root)
            lone_root = (
                display_parent == self._root
                and self._doc.child_count(self._root) == 1
            )
            if not lone_root:
                segs.append(("index", f"[{row}]"))
                segs.append(("punct", " : "))

        if kind == KEY:
            name = self._key_name(node)
            display = self._doc.display_text(node)
            rest = display[len(name):] if display.startswith(name) else ""
            segs.append(("key", name))
            if rest.startswith(": "):
                value = rest[2:]
                segs.append(("punct", " : "))
                segs.append((_classify_value(value), value))
            elif rest.startswith(" {"):
                segs.append(("punct", " : "))
                segs.append(("placeholder",
                             "{}" if self._doc.child_count(node) == 0 else "{...}"))
            elif rest.startswith(" ["):
                segs.append(("punct", " : "))
                segs.append(("placeholder",
                             "[]" if self._doc.child_count(node) == 0 else "[...]"))
            elif rest:
                segs.append(("punct", rest))
        elif kind == OBJECT:
            segs.append(("placeholder",
                         "{}" if self._doc.child_count(node) == 0 else "{...}"))
        elif kind == ARRAY:
            segs.append(("placeholder",
                         "[]" if self._doc.child_count(node) == 0 else "[...]"))
        elif kind in _SCALARS:
            text = self._doc.display_text(node)
            segs.append((_classify_value(text), text))
        elif kind == ELEMENT_OPEN:
            segs.append(("tag", self._doc.display_text(node)))
        elif kind == ATTRIBUTE:
            display = self._doc.display_text(node)
            head, sep, tail = display.partition(" = ")
            if sep:
                segs.append(("attr", head))
                segs.append(("punct", " = "))
                segs.append(("string", tail))
            else:
                segs.append(("attr", display))
        else:  # TEXT / CDATA
            segs.append(("text", self._doc.display_text(node)))
        return segs

    def segments(self, node: int) -> List[Tuple[str, str]]:
        segs = self._segments.get(node)
        if segs is None:
            segs = self._segments_for(node, self._row_of.get(node, 0))
            self._segments[node] = segs
        return segs

    # -- copy / export helpers ---------------------------------------------------

    def name_text(self, index: QModelIndex) -> str:
        node = self._node_of(index)
        kind = self._doc.kind(node)
        if kind == KEY:
            return self._key_name(node)
        if kind in (ELEMENT_OPEN, ATTRIBUTE):
            return self._doc.raw_text(node)
        return f"[{self._row_of.get(node, index.row())}]"

    def value_text(self, index: QModelIndex) -> str:
        """Human-friendly value: decoded scalars without quotes; compact
        JSON reconstruction for containers."""
        return self.value_of_node(self._node_of(index))

    def value_of_node(self, node: int) -> str:
        kind = self._doc.kind(node)
        if kind == KEY:
            segs = self.segments(node)
            if segs and segs[-1][0] in ("string", "number", "bool", "null"):
                text = segs[-1][1]
                if text.startswith('"') and text.endswith('"') and len(text) >= 2:
                    return text[1:-1]
                return text
            return json.dumps(self.reconstruct(node), ensure_ascii=False)
        if kind in _SCALARS or kind in (TEXT, CDATA):
            text = self._doc.display_text(node)
            if text.startswith('"') and text.endswith('"') and len(text) >= 2:
                return text[1:-1]
            return text
        if kind in (OBJECT, ARRAY, ELEMENT_OPEN, ATTRIBUTE):
            return json.dumps(self.reconstruct(node), ensure_ascii=False)
        return self._doc.raw_text(node)

    def reconstruct(self, node: int):
        """Rebuild the subtree rooted at ``node`` as Python values (for
        Copy/Export as JSON). Objects → dict, arrays → list, XML elements
        → {"tag", "attributes", "children"}. Uses decoded display values;
        deep documents temporarily raise the recursion limit (engine cap
        is 4,096 levels)."""
        import sys

        old_limit = sys.getrecursionlimit()
        sys.setrecursionlimit(max(old_limit, 20_000))
        try:
            return self._reconstruct(node)
        finally:
            sys.setrecursionlimit(old_limit)

    def _scalar_value(self, role: str, text: str):
        if role == "string":
            return text[1:-1] if len(text) >= 2 and text.startswith('"') else text
        if role == "bool":
            return text == "true"
        if role == "null":
            return None
        try:
            return json.loads(text)
        except ValueError:
            return text

    def _reconstruct(self, node: int):
        kind = self._doc.kind(node)
        if kind == KEY:
            segs = self._segments_for(node, 0)
            last_role, last_text = segs[-1]
            if last_role == "placeholder":
                kids = self._doc.child_nodes(node)
                if last_text.startswith("{"):   # "{...}" or empty "{}"
                    return {
                        self._key_name(k): self._reconstruct(k) for k in kids
                    }
                return [self._reconstruct(k) for k in kids]
            return self._scalar_value(last_role, last_text)
        if kind == OBJECT:
            return {
                self._key_name(k): self._reconstruct(k)
                for k in self._doc.child_nodes(node)
            }
        if kind == ARRAY:
            return [self._reconstruct(k) for k in self._doc.child_nodes(node)]
        if kind in _SCALARS:
            text = self._doc.display_text(node)
            return self._scalar_value(_classify_value(text), text)
        if kind == ELEMENT_OPEN:
            attrs = {}
            children = []
            for k in self._doc.child_nodes(node):
                k_kind = self._doc.kind(k)
                if k_kind == ATTRIBUTE:
                    attrs[self._doc.raw_text(k)] = self._reconstruct(k)
                else:
                    children.append(self._reconstruct(k))
            return {
                "tag": self._doc.raw_text(node),
                "attributes": attrs,
                "children": children,
            }
        if kind == ATTRIBUTE:
            display = self._doc.display_text(node)
            _, sep, tail = display.partition(" = ")
            if sep and len(tail) >= 2 and tail.startswith('"'):
                return tail[1:-1]
            return tail or display
        # TEXT / CDATA
        return self._doc.display_text(node)

    def raw_token(self, index: QModelIndex) -> str:
        """The exact raw byte window of the node (undecoded)."""
        return self._doc.raw_text(self._node_of(index))

    def path_text(self, index: QModelIndex) -> str:
        """A JSONPath-ish ($.a[0].b) or XPath-ish (/root/child/@attr)
        location for the row."""
        node = self._node_of(index)
        parts: List[str] = []
        while node != self._root:
            kind = self._doc.kind(node)
            row = self._row_of.get(node, 0)
            if kind == KEY:
                name = self._key_name(node)
                # Mirror of the jump-to-path grammar so paths round-trip.
                if re.fullmatch(r"[A-Za-z_][\w\-]*", name or ""):
                    parts.append(f".{name}")
                else:
                    safe = name.replace("\\", "\\\\").replace('"', '\\"')
                    parts.append(f'["{safe}"]')
            elif kind == ELEMENT_OPEN:
                parts.append(f"/{self._doc.raw_text(node)}")
            elif kind == ATTRIBUTE:
                parts.append(f"/@{self._doc.raw_text(node)}")
            elif kind in (TEXT, CDATA):
                parts.append("/text()")
            else:
                display_parent = self._display_parent.get(node, self._root)
                lone_root = (
                    display_parent == self._root
                    and self._doc.child_count(self._root) == 1
                )
                if not lone_root:
                    parts.append(f"[{row}]")
            node = self._display_parent.get(node, self._root)
        parts.reverse()
        if self._format == "XML":
            return "".join(parts) or "/"
        return "$" + "".join(parts)

    def node_kind(self, index: QModelIndex) -> int:
        return self._doc.kind(self._node_of(index))

    def format(self) -> str:
        return self._format

    def describe(self, index: QModelIndex) -> str:
        """Short type badge for the status bar, e.g. 'Object · 63 keys',
        'String · 27 chars', 'Element <item> · 4 children'."""
        node = self._node_of(index)
        kind = self._doc.kind(node)
        if kind == KEY:
            suffix = self.collapsed_suffix(index)
            if suffix:
                segs = self.segments(node)
                shape = next(
                    (t for r, t in segs if r == "placeholder"), "{...}"
                )
                base = "Object" if shape.startswith("{") else "Array"
                return f"{base} · {suffix}"
            segs = self.segments(node)
            role = segs[-1][0] if segs else "punct"
            value = self.value_text(index)
            if role == "string":
                return f"String · {len(value):,} chars"
            return {
                "number": "Number",
                "bool": "Boolean",
                "null": "Null",
            }.get(role, "Value")
        if kind in (OBJECT, ARRAY, ELEMENT_OPEN):
            name = KIND_NAMES[kind]
            if kind == ELEMENT_OPEN:
                name = f"Element <{self._doc.raw_text(node)}>"
            suffix = self.collapsed_suffix(index)
            return f"{name} · {suffix}" if suffix else f"{name} · empty"
        if kind == STRING or kind in (TEXT, CDATA):
            return f"{KIND_NAMES[kind]} · {len(self.value_text(index)):,} chars"
        return KIND_NAMES.get(kind, "Value")

    # -- jump to path -----------------------------------------------------------

    def _find_child(self, parent: QModelIndex, matcher) -> QModelIndex:
        for row in range(self.rowCount(parent)):
            child = self.index(row, 0, parent)
            if matcher(child):
                return child
        return QModelIndex()

    def resolve_path(self, text: str) -> QModelIndex:
        """Resolve '$.a[0].b' (JSON/CSV) or '/root/item[2]/@attr' (XML)
        to a model index; invalid index when not found."""
        text = text.strip()
        if not text:
            return QModelIndex()
        if self._format == "XML" or text.startswith("/"):
            return self._resolve_xpathish(text)
        return self._resolve_jsonpath(text)

    def _resolve_jsonpath(self, text: str) -> QModelIndex:
        if text.startswith("$"):
            text = text[1:]
        current = QModelIndex()
        # A lone root value never appears in paths ("$.a", "$[0]" both
        # address content INSIDE it) — descend into it implicitly.
        if self._doc.child_count(self._root) == 1:
            current = self.index(0, 0, QModelIndex())
        pos = 0
        while pos < len(text):
            m = _PATH_TOKEN.match(text, pos)
            if m is None:
                return QModelIndex()
            pos = m.end()
            if m.group(2) is not None:
                name = m.group(2).replace('\\"', '"').replace("\\\\", "\\")
            else:
                name = m.group(1)
            if name is not None:
                current = self._find_child(
                    current,
                    lambda c, n=name: self.node_kind(c) == KEY
                    and self._key_name(self._node_of(c)) == n,
                )
            else:
                row = int(m.group(3))
                node = self._node_of(current) if current.isValid() else self._root
                kids = self._children_of(node)
                if row >= len(kids):
                    return QModelIndex()
                current = self.index(row, 0, current)
            if not current.isValid():
                return QModelIndex()
        return current

    def _resolve_xpathish(self, text: str) -> QModelIndex:
        current = QModelIndex()
        for token in [t for t in text.strip("/").split("/") if t]:
            position = 1
            m = re.fullmatch(r"(.+?)\[(\d+)\]", token)
            if m:
                token, position = m.group(1), int(m.group(2))
            if token.startswith("@"):
                name = token[1:]
                current = self._find_child(
                    current,
                    lambda c, n=name: self.node_kind(c) == ATTRIBUTE
                    and self._doc.raw_text(self._node_of(c)) == n,
                )
            elif token == "text()":
                current = self._find_child(
                    current, lambda c: self.node_kind(c) in (TEXT, CDATA)
                )
            else:
                seen = 0
                found = QModelIndex()
                for row in range(self.rowCount(current)):
                    child = self.index(row, 0, current)
                    if (
                        self.node_kind(child) == ELEMENT_OPEN
                        and self._doc.raw_text(self._node_of(child)) == token
                    ):
                        seen += 1
                        if seen == position:
                            found = child
                            break
                current = found
            if not current.isValid():
                return QModelIndex()
        return current

    def collapsed_suffix(self, index: QModelIndex) -> Optional[str]:
        """", 63 keys"-style annotation the view appends to collapsed
        container rows: keys for objects, items for arrays, children for
        XML elements."""
        node = self._node_of(index)
        kind = self._doc.kind(node)
        count = self._doc.child_count(node)  # drilled display children
        if count == 0:
            return None
        if kind == ELEMENT_OPEN:
            noun = "child" if count == 1 else "children"
            return f"{count:,} {noun}"
        segs = self.segments(node)
        placeholder = next(
            (text for role, text in segs if role == "placeholder"), None
        )
        if placeholder and placeholder.startswith("{"):
            noun = "key" if count == 1 else "keys"
        elif placeholder and placeholder.startswith("["):
            noun = "item" if count == 1 else "items"
        else:
            return None
        return f"{count:,} {noun}"

    # -- match navigation ----------------------------------------------------

    def _display_row_node(self, node: int) -> int:
        """The display row that represents ``node`` — a scalar or container
        that is a Key/Attribute *value* is shown on its owner's row."""
        kind = self._doc.kind(node)
        if kind in (KEY, ATTRIBUTE) or node == self._root:
            return node
        parent = self._doc.parent(node)
        if parent is not None and self._doc.kind(parent) in (KEY, ATTRIBUTE):
            return parent
        return node

    def index_for_node(self, node: int) -> QModelIndex:
        """Build (and cache) the QModelIndex chain down to the display row
        of ``node`` — used to jump to search matches."""
        chain: List[int] = []
        cur = self._display_row_node(node)
        while cur != self._root:
            chain.append(cur)
            parent = self._doc.parent(cur)
            if parent is None:
                break
            cur = self._display_row_node(parent)
        chain.reverse()
        parent_index = QModelIndex()
        parent_node = self._root
        result = QModelIndex()
        for n in chain:
            kids = self._children_of(parent_node)
            try:
                row = kids.index(n)
            except ValueError:
                return QModelIndex()
            result = self.index(row, 0, parent_index)
            parent_index = result
            parent_node = n
        return result

    # -- named / format-specific serialization ---------------------------------

    def reconstruct_named(self, node: int):
        """Like reconstruct, but Key/Attribute rows keep their name:
        ``{"name": value}`` — so a copied member is self-describing."""
        kind = self._doc.kind(node)
        if kind == KEY:
            return {self._key_name(node): self.reconstruct(node)}
        if kind == ATTRIBUTE:
            return {"@" + self._doc.raw_text(node): self.reconstruct(node)}
        return self.reconstruct(node)

    def _attr_value(self, node: int) -> str:
        display = self._doc.display_text(node)
        _, sep, tail = display.partition(" = ")
        if sep and len(tail) >= 2 and tail.startswith('"'):
            return tail[1:-1]
        return tail or ""

    @staticmethod
    def _xml_escape(text: str) -> str:
        return (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )

    def xml_text(self, node: int) -> str:
        """Serialize an XML subtree back to markup. Character data uses the
        raw window (entities preserved); attribute values are re-escaped."""
        import sys

        old_limit = sys.getrecursionlimit()
        sys.setrecursionlimit(max(old_limit, 20_000))
        try:
            return self._xml_text(node)
        finally:
            sys.setrecursionlimit(old_limit)

    def _xml_text(self, node: int) -> str:
        kind = self._doc.kind(node)
        if kind == ELEMENT_OPEN:
            tag = self._doc.raw_text(node)
            attrs = []
            kids = []
            for k in self._doc.child_nodes(node):
                if self._doc.kind(k) == ATTRIBUTE:
                    attrs.append(
                        f'{self._doc.raw_text(k)}="{self._xml_escape(self._attr_value(k))}"'
                    )
                else:
                    kids.append(k)
            head = tag + ("".join(" " + a for a in attrs))
            if not kids:
                return f"<{head}/>"
            inner = "".join(self._xml_text(k) for k in kids)
            return f"<{head}>{inner}</{tag}>"
        if kind == CDATA:
            return f"<![CDATA[{self._doc.raw_text(node)}]]>"
        if kind == ATTRIBUTE:
            return f'{self._doc.raw_text(node)}="{self._xml_escape(self._attr_value(node))}"'
        return self._doc.raw_text(node)  # Text: raw, entities preserved

    def element_text_content(self, node: int) -> str:
        """Concatenated direct Text/CDATA content of an XML element."""
        return "".join(
            self._doc.display_text(k)
            for k in self._doc.child_nodes(node)
            if self._doc.kind(k) in (TEXT, CDATA)
        )

    def csv_row_text(self, node: int) -> str:
        """Serialize a CSV/TSV record row back to one delimited line."""
        delim = "\t" if self._format == "TSV" else ","

        def quote(cell: str) -> str:
            if any(c in cell for c in (delim, '"', "\n", "\r")):
                return '"' + cell.replace('"', '""') + '"'
            return cell

        cells = []
        for k in self._doc.child_nodes(node):
            if self._doc.kind(k) == KEY:
                segs = self._segments_for(k, 0)
                text = segs[-1][1]
                if text.startswith('"') and text.endswith('"') and len(text) >= 2:
                    text = text[1:-1]
                cells.append(quote(text))
            else:
                text = self._doc.display_text(k)
                if text.startswith('"') and text.endswith('"') and len(text) >= 2:
                    text = text[1:-1]
                cells.append(quote(text))
        return delim.join(cells)

    # -- plumbing -----------------------------------------------------------------

    def _node_of(self, index: QModelIndex) -> int:
        return int(index.internalId()) if index.isValid() else self._root

    def _children_of(self, node: int) -> List[int]:
        kids = self._children.get(node)
        if kids is None:
            # child_nodes() already returns a fresh list, so don't copy it.
            kids = self._doc.child_nodes(node)
            self._children[node] = kids
            # Record each child's display parent (may differ from the
            # structural parent because of drilling) and its row. On a file
            # with millions of top-level rows, an interpreted per-child loop
            # here dominated the first paint; dict.update over zip runs the
            # same work in C, cutting it several-fold.
            self._display_parent.update(zip(kids, repeat(node)))
            self._row_of.update(zip(kids, count()))
        return kids

    # -- QAbstractItemModel ----------------------------------------------------------

    def index(self, row: int, column: int, parent: QModelIndex = QModelIndex()):
        if column != 0 or row < 0:
            return QModelIndex()
        parent_node = self._node_of(parent)
        kids = self._children_of(parent_node)
        if row >= len(kids):
            return QModelIndex()
        return self.createIndex(row, 0, kids[row])

    def parent(self, index: QModelIndex = QModelIndex()):
        if not index.isValid():
            return QModelIndex()
        node = self._node_of(index)
        display_parent = self._display_parent.get(node, self._root)
        if display_parent == self._root:
            return QModelIndex()
        return self.createIndex(self._row_of[display_parent], 0, display_parent)

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid() and parent.column() != 0:
            return 0
        return len(self._children_of(self._node_of(parent)))

    def hasChildren(self, parent: QModelIndex = QModelIndex()) -> bool:
        node = self._node_of(parent)
        if node == self._root:
            return self._doc.child_count(node) > 0
        return bool(self._doc.is_expandable(node))

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 1

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        node = self._node_of(index)
        if role == Qt.ItemDataRole.DisplayRole:
            return "".join(text for _, text in self.segments(node))
        if role == SegmentsRole:
            return self.segments(node)
        # Match highlighting is painted by the delegate as substring chips
        # (not a BackgroundRole full-row fill); see tree.SegmentDelegate.
        return None

    def flags(self, index: QModelIndex):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    # -- helpers for the app -------------------------------------------------------------

    def node_id(self, index: QModelIndex) -> Optional[int]:
        return self._node_of(index) if index.isValid() else None

    def tail_extend(self, new_doc, added: int) -> None:
        """Swap in a (segmented) document that has ``added`` new top-level
        rows appended, emitting the row insertion so the view updates in
        place. Existing node ids are unchanged (append-only), so open
        expansion and selection stay valid."""
        if added <= 0:
            self._doc = new_doc
            return
        root_index = QModelIndex()
        old = self.rowCount(root_index)  # populates/uses the root cache
        self.beginInsertRows(root_index, old, old + added - 1)
        self._doc = new_doc
        self._children.pop(self._root, None)  # re-fetch root children lazily
        self.endInsertRows()

    # -- node statistics -------------------------------------------------------

    STATS_CAP = 200_000
    DISTINCT_CAP = 10_000

    def statistics(self, index: QModelIndex) -> str:
        """Plain-text stats over a container's direct children: kind
        histogram, distinct values, numeric min/max/avg. Capped so a
        multi-million-row array cannot freeze the UI."""
        node = self._node_of(index)
        kids = self._doc.child_nodes(node)
        total = len(kids)
        sample = kids[: self.STATS_CAP]
        kinds: Dict[str, int] = {}
        distinct: Set[str] = set()
        distinct_overflow = False
        numeric_count = 0
        num_min = num_max = num_sum = None
        empty = 0
        for kid in sample:
            kind = self._doc.kind(kid)
            label = KIND_NAMES.get(kind, "Other")
            if kind == KEY:
                segs = self.segments(kid)
                role = segs[-1][0] if segs else "punct"
                label = {
                    "string": "String",
                    "number": "Number",
                    "bool": "Boolean",
                    "null": "Null",
                    "placeholder": "Container",
                }.get(role, "Member")
            kinds[label] = kinds.get(label, 0) + 1
            value = self.value_of_node(kid)
            if not value:
                empty += 1
            if len(distinct) < self.DISTINCT_CAP:
                distinct.add(value)
            else:
                distinct_overflow = True
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            numeric_count += 1
            num_sum = number if num_sum is None else num_sum + number
            num_min = number if num_min is None else min(num_min, number)
            num_max = number if num_max is None else max(num_max, number)

        lines = [f"Children: {total:,}"]
        if total > len(sample):
            lines.append(f"(statistics over the first {len(sample):,})")
        for label in sorted(kinds, key=kinds.get, reverse=True):
            lines.append(f"  {label}: {kinds[label]:,}")
        more = "+" if distinct_overflow else ""
        lines.append(f"Distinct values: {len(distinct):,}{more}")
        if empty:
            lines.append(f"Empty values: {empty:,}")
        if numeric_count:
            lines.append(
                f"Numeric ({numeric_count:,}): min {num_min:g} · "
                f"max {num_max:g} · avg {num_sum / numeric_count:g}"
            )
        return "\n".join(lines)

    # -- filter support ------------------------------------------------------------

    #: Cap on how many descendant rows a filter reveals for its matches, so
    #: matching a huge container can't explode the visible set.
    _FILTER_DESCEND_CAP = 100_000

    def visible_filter_set(self, node_ids) -> Set[int]:
        """Rows a NodeFilterProxy should show: each match, its display
        ancestors (so the path to it stays visible), AND each match's own
        subtree (bounded) — otherwise a match that is a container (object /
        array) shows as `{...}` but can't be expanded, because its children
        were filtered out."""
        visible: Set[int] = set()
        matches = []
        for node in node_ids:
            cur = self._display_row_node(node)
            matches.append(cur)
            while cur != self._root and cur not in visible:
                visible.add(cur)
                parent = self._doc.parent(cur)
                if parent is None:
                    break
                cur = self._display_row_node(parent)
        # Reveal each match's descendants so a matched container is openable.
        budget = self._FILTER_DESCEND_CAP
        stack = list(matches)
        while stack and budget > 0:
            node = stack.pop()
            for child in self._children_of(node):
                if child not in visible:
                    visible.add(child)
                    stack.append(child)
                    budget -= 1
                    if budget <= 0:
                        break
        return visible


class NodeFilterProxy(QSortFilterProxyModel):
    """Shows only rows in a precomputed visible set (matches + their
    ancestors). filterAcceptsRow is O(1), so filtering stays lazy and
    cheap even on huge documents."""

    def __init__(self, source: DocumentModel, parent=None):
        super().__init__(parent)
        self._visible: Set[int] = set()  # before setSourceModel: Qt may filter
        self.setSourceModel(source)

    def set_visible(self, visible: Set[int]) -> None:
        self._visible = set(visible)
        self.invalidateFilter()

    def buddy(self, index):  # noqa: N802
        """Return the index itself instead of mapping through the source.

        The tree is read-only, so no edit "buddy" is needed. The base
        implementation routes through mapToSource(), which segfaults when the
        view hands it a stale current index during a model swap or filter
        invalidation (buddy() is called from QAbstractItemView::currentChanged).
        Identity is safe here and sidesteps that crash path entirely."""
        return index

    def filterAcceptsRow(self, source_row: int, source_parent) -> bool:  # noqa: N802
        source: DocumentModel = self.sourceModel()
        parent_node = source._node_of(source_parent)
        kids = source._children_of(parent_node)
        if source_row >= len(kids):
            return False
        return kids[source_row] in self._visible
