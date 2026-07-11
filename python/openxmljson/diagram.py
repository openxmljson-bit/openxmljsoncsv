"""Qt-free builder that turns a reconstructed value into a node/edge graph
for the flow-diagram view.

Consumes ``DocumentModel.reconstruct()`` output (dict / list / scalars, with
XML subtrees as ``{"tag", "attributes", "children"}``). Modelled after the
ToDiagram layout:

* An object (or the document root) is one **card**. Each of its keys is a row.
* A scalar value is shown inline on the row, tagged with its JSON type so the
  renderer can colour it (keys blue, numbers amber, strings light, …).
* A nested object, or each element of an array of objects, becomes its **own
  card**, linked by an edge that is **labelled with the key**. Arrays do not
  get an intermediate card: the parent row reads ``key: [N items]`` and edges
  fan out from that row directly to the element cards.

Pure and dependency-light (only ``convert`` helpers) so it is unit-testable
without Qt or the native engine. A hard node cap keeps a pathological document
from producing an unbounded graph — the flow diagram is a small/medium-document
feature by nature.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .convert import _cell_text, _is_xml_node

#: Stop adding cards past this many; ``Graph.truncated`` records that we did.
#: Kept modest: each card becomes several QGraphicsItems, and a diagram with
#: many hundreds of cards is both unreadable and slow to paint. The document is
#: separately gated by node count before a graph is built at all.
MAX_GRAPH_NODES = 800

#: A field row: (key, value_text, value_type, is_link). ``value_type`` is one of
#: object/array/string/number/boolean/null. ``is_link`` marks rows that point to
#: a child card (drawn with an expand marker).
Field = Tuple[str, str, str, bool]

#: An edge: (parent_id, child_id, label, source_row_index).
Edge = Tuple[str, str, str, int]


def _is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _value_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    return "string"


def cell_display(value: Any) -> str:
    """Human-readable text for a single inline value."""
    return _cell_text(value)


def _xml_to_plain(value: Any) -> Any:
    """Collapse an XML {tag, attributes, children} node into a plain
    dict/scalar so XML renders like JSON."""
    if not _is_xml_node(value):
        return value
    children = value["children"]
    attrs = value["attributes"]
    if not children and not attrs:
        return None
    if len(children) == 1 and _is_scalar(children[0]) and not attrs:
        return children[0]
    out: Dict[str, Any] = {}
    for key, attr_val in attrs.items():
        out[f"@{key}"] = attr_val
    for child in children:
        if _is_xml_node(child):
            tag = child["tag"]
            plain = _xml_to_plain(child)
            if tag in out:
                existing = out[tag]
                if isinstance(existing, list):
                    existing.append(plain)
                else:
                    out[tag] = [existing, plain]
            else:
                out[tag] = plain
        elif _is_scalar(child):
            out.setdefault("#text", child)
    return out


class GraphNode:
    """One card: a stable id, a title (shown in the card's header bar), and a
    list of typed field rows."""

    __slots__ = ("id", "title", "fields")

    def __init__(self, node_id: str, title: str):
        self.id = node_id
        self.title = title
        self.fields: List[Field] = []

    def as_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "title": self.title, "fields": list(self.fields)}


class Graph:
    def __init__(self) -> None:
        self.nodes: List[GraphNode] = []
        self.edges: List[Edge] = []
        self.truncated = False

    def as_dict(self) -> Dict[str, Any]:
        return {
            "nodes": [n.as_dict() for n in self.nodes],
            "edges": list(self.edges),
            "truncated": self.truncated,
        }


def build_graph(
    value: Any,
    root_title: str = "root",   # kept for API compatibility; not rendered
    max_nodes: int = MAX_GRAPH_NODES,
) -> Graph:
    """Turn a reconstructed value into a labelled node/edge graph."""
    graph = Graph()
    counter = [0]

    def new_id() -> str:
        counter[0] += 1
        return f"n{counter[0]}"

    def emit(val: Any, title: str) -> Optional[str]:
        if len(graph.nodes) >= max_nodes:
            graph.truncated = True
            return None
        val = _xml_to_plain(val)
        node = GraphNode(new_id(), title)
        graph.nodes.append(node)

        if isinstance(val, dict):
            items = list(val.items())
        elif isinstance(val, list):
            items = [(f"[{i}]", el) for i, el in enumerate(val)]
        else:  # a scalar as a whole card (rare: scalar document root)
            node.fields.append(("", cell_display(val), _value_type(val), False))
            return node.id

        for key, child in items:
            child = _xml_to_plain(child)
            vt = _value_type(child)
            key = str(key)
            if vt == "object":
                if not child:
                    node.fields.append((key, "{}", "object", False))
                    continue
                row = len(node.fields)
                node.fields.append((key, f"{{{len(child)} keys}}", "object", True))
                cid = emit(child, key)
                if cid is not None:
                    graph.edges.append((node.id, cid, key, row))
            elif vt == "array":
                if not child:
                    node.fields.append((key, "[]", "array", False))
                    continue
                row = len(node.fields)
                node.fields.append((key, f"[{len(child)} items]", "array", True))
                for i, element in enumerate(child):
                    element = _xml_to_plain(element)
                    if _value_type(element) in ("object", "array"):
                        cid = emit(element, f"{key}[{i}]")
                        if cid is not None:
                            graph.edges.append((node.id, cid, key, row))
                    # scalar array elements stay summarised as the count above
            else:
                node.fields.append((key, cell_display(child), vt, False))
        return node.id

    emit(value, root_title)
    return graph
