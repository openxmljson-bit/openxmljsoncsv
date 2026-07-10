"""Structural diff of two reconstructed documents.

Pure functions (no Qt) so they're headlessly testable. Inputs are the
Python values produced by ``DocumentModel.reconstruct()`` — dicts, lists and
scalars (XML subtrees appear as {"tag","attributes","children"} dicts, which
diff structurally like any other dict).

The diff is deterministic and position-based for arrays (element *i* on the
left is compared with element *i* on the right); a shorter/longer array
yields removed/added tail elements. Objects diff by key. This is predictable
and O(n) — not a minimal edit script, but the right model for "what changed
between these two files" in a viewer.
"""

from __future__ import annotations

import json
from typing import Any, List, Tuple

#: A single difference: (kind, path, left, right).
#: kind ∈ {"added", "removed", "changed"}; the absent side is ``MISSING``.
Change = Tuple[str, str, Any, Any]

MISSING = object()

_IDENT = None  # compiled lazily to avoid importing re at module load for tests


def _key_segment(key: str) -> str:
    import re

    global _IDENT
    if _IDENT is None:
        _IDENT = re.compile(r"[A-Za-z_][\w\-]*$")
    if isinstance(key, str) and _IDENT.match(key):
        return f".{key}"
    safe = str(key).replace("\\", "\\\\").replace('"', '\\"')
    return f'["{safe}"]'


def _type_tag(v: Any) -> str:
    """A JSON-ish type tag so a dict→list or number→string counts as a
    change (and True is a bool, not the int 1)."""
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, (int, float)):
        return "number"
    if isinstance(v, str):
        return "string"
    if isinstance(v, list):
        return "array"
    if isinstance(v, dict):
        return "object"
    return "other"


def diff(a: Any, b: Any, path: str = "$") -> List[Change]:
    """Return the list of differences turning ``a`` into ``b``."""
    out: List[Change] = []
    _diff(a, b, path, out)
    return out


def _diff(a: Any, b: Any, path: str, out: List[Change]) -> None:
    if _type_tag(a) != _type_tag(b):
        out.append(("changed", path, a, b))
        return
    if isinstance(a, dict):
        # Left keys first (stable), then right-only keys in their order.
        keys = list(a.keys()) + [k for k in b.keys() if k not in a]
        for k in keys:
            cp = f"{path}{_key_segment(k)}"
            in_a, in_b = k in a, k in b
            if in_a and in_b:
                _diff(a[k], b[k], cp, out)
            elif in_b:
                out.append(("added", cp, MISSING, b[k]))
            else:
                out.append(("removed", cp, a[k], MISSING))
    elif isinstance(a, list):
        for i in range(max(len(a), len(b))):
            cp = f"{path}[{i}]"
            if i < len(a) and i < len(b):
                _diff(a[i], b[i], cp, out)
            elif i < len(b):
                out.append(("added", cp, MISSING, b[i]))
            else:
                out.append(("removed", cp, a[i], MISSING))
    else:
        if a != b:
            out.append(("changed", path, a, b))


def _brief(v: Any, limit: int = 60) -> str:
    if v is MISSING:
        return "∅"
    s = json.dumps(v, ensure_ascii=False)
    return s if len(s) <= limit else s[: limit - 1] + "…"


def summarize(changes: List[Change]) -> Tuple[int, int, int]:
    """(added, removed, changed) counts."""
    added = sum(1 for c in changes if c[0] == "added")
    removed = sum(1 for c in changes if c[0] == "removed")
    changed = sum(1 for c in changes if c[0] == "changed")
    return added, removed, changed


def format_report(changes: List[Change]) -> str:
    """A human-readable, copy-pasteable diff report.

    ``~ path: old → new`` (changed), ``+ path: value`` (added),
    ``- path: value`` (removed). Paths use the same $.a[0].b grammar as
    Jump to Path, so a line locates the node."""
    if not changes:
        return "No differences — the documents are structurally identical."
    added, removed, changed = summarize(changes)
    lines = [
        f"{len(changes)} difference(s): "
        f"{added} added, {removed} removed, {changed} changed.",
        "",
    ]
    for kind, path, left, right in changes:
        if kind == "changed":
            lines.append(f"~ {path}: {_brief(left)} → {_brief(right)}")
        elif kind == "added":
            lines.append(f"+ {path}: {_brief(right)}")
        else:
            lines.append(f"- {path}: {_brief(left)}")
    return "\n".join(lines)
