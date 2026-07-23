"""Field projection for "Deep Dive": pick fields from a document's schema and
produce a pruned copy that keeps only those fields.

Qt-free so it's unit-testable. Two pieces:

* ``schema_field_tree(schema)`` turns a draft-07 schema (from ``schemagen``)
  into a tree of selectable fields. Arrays are *transparent* — an array of
  objects contributes its element's fields, not an array level — so a field
  path is a tuple of object keys, e.g. ``("response", "products", "price")``.
* ``project_value(value, selected_paths)`` walks a reconstructed value and
  keeps only the selected paths (and the ancestors needed to reach them),
  applying each path to every element of any list it passes through.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Tuple

Path = Tuple[str, ...]


def schema_field_tree(schema: Any, name: str = "(root)",
                      path: Path = ()) -> Dict[str, Any]:
    """Build a nested field tree: {name, path, children[]} from a JSON Schema.
    Object → one child per property; array → transparent (its items' fields
    become this node's children); scalar → leaf."""
    node: Dict[str, Any] = {"name": name, "path": path, "children": []}
    if not isinstance(schema, dict):
        return node
    if "properties" in schema and isinstance(schema["properties"], dict):
        for key, sub in schema["properties"].items():
            node["children"].append(schema_field_tree(sub, key, path + (key,)))
    elif "items" in schema:
        inner = schema_field_tree(schema["items"], name, path)
        node["children"] = inner["children"]
    return node


def all_paths(node: Dict[str, Any]) -> List[Path]:
    """Every field path in a tree (excluding the synthetic root)."""
    out: List[Path] = []

    def walk(n: Dict[str, Any], is_root: bool) -> None:
        if not is_root and n["path"]:
            out.append(n["path"])
        for child in n["children"]:
            walk(child, False)

    walk(node, True)
    return out


def project_value(value: Any, selected_paths: Iterable[Path]) -> Any:
    """Return a copy of ``value`` keeping only the selected field paths (and
    the ancestors on the way to them). A selected path keeps that field's whole
    subtree. Lists are transparent: the same path applies to each element."""
    selected = {tuple(p) for p in selected_paths}
    # All prefixes (ancestors + the selections themselves) we must descend into.
    ancestors = set()
    for s in selected:
        for i in range(1, len(s) + 1):
            ancestors.add(s[:i])

    def go(val: Any, path: Path) -> Any:
        if isinstance(val, dict):
            out = {}
            for key, sub in val.items():
                p = path + (key,)
                if p in selected:
                    out[key] = sub            # keep the whole subtree
                elif p in ancestors:
                    out[key] = go(sub, p)     # on the way to a deeper selection
            return out
        if isinstance(val, list):
            return [go(item, path) for item in val]   # array transparent
        return val

    return go(value, ())
