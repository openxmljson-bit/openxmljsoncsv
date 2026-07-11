"""Qt-free builder that turns a reconstructed value into a node/edge graph
for the flow-diagram view.

Consumes ``DocumentModel.reconstruct()`` output (dict / list / scalars, with
XML subtrees as ``{"tag", "attributes", "children"}``). Each object/array
becomes a card; scalar children are listed inline as ``key: value`` fields;
each nested object/array child becomes its own card linked by an edge.

Pure and dependency-light (only ``convert`` helpers) so it is unit-testable
without Qt or the native engine. A hard node cap keeps a pathological document
from producing an unbounded graph — the flow diagram is a small/medium-document
feature by nature (a multi-GB file has too many nodes to draw meaningfully).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .convert import _cell_text, _is_xml_node

#: Stop adding cards past this many; ``Graph.truncated`` records that we did.
MAX_GRAPH_NODES = 2000


def _is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def cell_display(value: Any) -> str:
    """Human-readable text for a single inline field value."""
    return _cell_text(value)


def _xml_to_plain(value: Any) -> Any:
    """Collapse an XML {tag, attributes, children} node into a plain
    dict/scalar so XML renders like JSON. Attributes become ``@attr`` keys;
    a single scalar child becomes the element's value."""
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
    """One card: a title, a stable id, and inline (key, value-text) fields for
    its scalar children. Nested children are edges to other cards."""

    __slots__ = ("id", "title", "fields")

    def __init__(self, node_id: str, title: str):
        self.id = node_id
        self.title = title
        self.fields: List[Tuple[str, str]] = []

    def as_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "title": self.title, "fields": list(self.fields)}


class Graph:
    def __init__(self) -> None:
        self.nodes: List[GraphNode] = []
        self.edges: List[Tuple[str, str]] = []
        self.truncated = False

    def as_dict(self) -> Dict[str, Any]:
        return {
            "nodes": [n.as_dict() for n in self.nodes],
            "edges": list(self.edges),
            "truncated": self.truncated,
        }


def build_graph(
    value: Any,
    root_title: str = "root",
    max_nodes: int = MAX_GRAPH_NODES,
) -> Graph:
    """Turn a reconstructed value into a node/edge graph. Scalars are inline
    fields; nested objects/arrays become linked cards. Array items are titled
    ``key[i]``. Stops past ``max_nodes`` and sets ``graph.truncated``."""
    graph = Graph()
    counter = [0]

    def new_id() -> str:
        counter[0] += 1
        return f"n{counter[0]}"

    def add_node(title: str, val: Any) -> Optional[str]:
        if len(graph.nodes) >= max_nodes:
            graph.truncated = True
            return None
        node = GraphNode(new_id(), title)
        graph.nodes.append(node)

        val = _xml_to_plain(val)
        if isinstance(val, dict):
            items = list(val.items())
        elif isinstance(val, list):
            items = [(f"{title}[{i}]", v) for i, v in enumerate(val)]
        else:
            node.fields.append(("value", cell_display(val)))
            return node.id

        for key, child in items:
            child = _xml_to_plain(child)
            if _is_scalar(child):
                node.fields.append((str(key), cell_display(child)))
            else:
                if isinstance(child, dict):
                    node.fields.append((str(key), "{…}"))
                elif isinstance(child, list):
                    node.fields.append((str(key), f"[{len(child)}]"))
                child_id = add_node(str(key), child)
                if child_id is not None:
                    graph.edges.append((node.id, child_id))
        return node.id

    add_node(root_title, value)
    return graph
