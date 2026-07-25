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


def _full(v: Any) -> Any:
    """The value itself for structured export (MISSING → None)."""
    return None if v is MISSING else v


def _cell(v: Any, limit: int = 200) -> str:
    """A one-line string of a value for table cells (longer than _brief)."""
    if v is MISSING:
        return "—"
    s = json.dumps(v, ensure_ascii=False)
    return s if len(s) <= limit else s[: limit - 1] + "…"


def summarize(changes: List[Change]) -> Tuple[int, int, int]:
    """(added, removed, changed) counts."""
    added = sum(1 for c in changes if c[0] == "added")
    removed = sum(1 for c in changes if c[0] == "removed")
    changed = sum(1 for c in changes if c[0] == "changed")
    return added, removed, changed


def _depth(path: str) -> int:
    """Nesting depth of a $.a[0].b path (number of . and [ segments)."""
    return path.count(".") + path.count("[")


def change_rows(changes: List[Change]) -> List[dict]:
    """Structured per-change records for HTML/JSON/CSV export: kind, path,
    depth, old/new value + JSON type of each side."""
    rows: List[dict] = []
    for kind, path, left, right in changes:
        rows.append({
            "kind": kind,
            "path": path,
            "depth": _depth(path),
            "old": _full(left),
            "new": _full(right),
            "old_type": "—" if left is MISSING else _type_tag(left),
            "new_type": "—" if right is MISSING else _type_tag(right),
        })
    return rows


def type_breakdown(changes: List[Change]) -> dict:
    """Count changes grouped by the JSON type involved (new side, else old),
    e.g. {'string': 4, 'number': 2, 'object': 1}."""
    out: dict = {}
    for _kind, _path, left, right in changes:
        v = right if right is not MISSING else left
        t = _type_tag(v) if v is not MISSING else "null"
        out[t] = out.get(t, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: (-kv[1], kv[0])))


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


# -- structured & rich exports --------------------------------------------------


def to_txt_report(changes: List[Change], meta: dict | None = None) -> str:
    """Plain-text report with an optional metadata header block."""
    head = ""
    if meta:
        head = (
            "OPENXMLJSON — Document Comparison\n"
            f"Left : {meta.get('left', '')}\n"
            f"Right: {meta.get('right', '')}\n"
            f"When : {meta.get('when', '')}\n"
            + "=" * 60 + "\n\n"
        )
    return head + format_report(changes)


def to_json_report(changes: List[Change], meta: dict | None = None) -> str:
    """A machine-readable diff: metadata, summary counts, type breakdown, and
    one object per change with full (untruncated) old/new values."""
    added, removed, changed = summarize(changes)
    report = {
        "meta": meta or {},
        "summary": {
            "total": len(changes),
            "added": added,
            "removed": removed,
            "changed": changed,
            "by_type": type_breakdown(changes),
        },
        "changes": change_rows(changes),
    }
    return json.dumps(report, indent=2, ensure_ascii=False)


def to_csv_report(changes: List[Change]) -> str:
    """CSV with header kind,path,depth,old_type,new_type,old,new — full
    values (nested serialized as compact JSON)."""
    import csv
    import io

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        ["kind", "path", "depth", "old_type", "new_type", "old", "new"])
    for r in change_rows(changes):
        old = "" if r["old"] is None and r["old_type"] == "—" else (
            r["old"] if isinstance(r["old"], str)
            else json.dumps(r["old"], ensure_ascii=False))
        new = "" if r["new"] is None and r["new_type"] == "—" else (
            r["new"] if isinstance(r["new"], str)
            else json.dumps(r["new"], ensure_ascii=False))
        writer.writerow([r["kind"], r["path"], r["depth"],
                         r["old_type"], r["new_type"], old, new])
    return buf.getvalue()


def _html_escape(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


# Soft palette sampled from a light infographic: tinted cell backgrounds with
# dark text (never colored text). Each change kind gets a tint + a stronger
# accent used for its header/badge.
_INK = "#2E3A45"        # dark slate — all body text
_MUTED = "#5f6b76"      # secondary text
_PAGE = "#ffffff"       # page background (plain white)
_BAND = "#c3ccd4"       # grey-blue table column-header band
_GRID = "#d8dee3"       # cell borders

#: kind -> (tint background, accent, glyph, word)
_KIND_STYLE = {
    "added":   ("#dcebe1", "#8fbaa0", "＋", "Added"),    # light green
    "removed": ("#eedadb", "#c39b9b", "－", "Removed"),  # light rose
    "changed": ("#dbe6ed", "#6e96ac", "~", "Changed"),  # light blue
}


def to_html_report(changes: List[Change], meta: dict | None = None,
                   theme: dict | None = None) -> str:
    """A self-contained, light-themed HTML report styled like a printed
    comparison sheet: a grey-blue header band, tinted summary cards, a
    type-breakdown line, and a change table whose rows are softly tinted by
    change kind (green=added, rose=removed, blue=changed) with dark text.

    ``theme`` is accepted for signature compatibility but ignored — the
    report is intentionally a fixed light page so it reads the same in the
    preview, saved HTML, and the browser."""
    added, removed, changed = summarize(changes)
    meta = meta or {}

    def card(label, value, tint, accent):
        return (
            f'<td style="padding:12px 20px;background:{tint};'
            f'border-top:4px solid {accent};text-align:center">'
            f'<div style="font-size:26px;font-weight:800;color:{_INK}">'
            f'{value}</div>'
            f'<div style="font-size:11px;color:{_MUTED};font-weight:600;'
            f'text-transform:uppercase;letter-spacing:.6px">{label}</div>'
            f'</td>')

    p: List[str] = []
    p.append(
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        '<title>Document Comparison</title></head>')
    p.append(
        f'<body style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,'
        f'sans-serif;color:{_INK};background:{_PAGE};margin:0;'
        f'padding:20px 26px">')
    p.append(
        f'<div style="width:100%;margin:0;background:#ffffff;'
        f'box-sizing:border-box">')

    # Header (plain white, with a thin rule for separation).
    p.append(
        f'<div style="background:#ffffff;padding:22px 28px;'
        f'border-bottom:1px solid {_GRID}">'
        f'<div style="font-size:24px;font-weight:800;color:{_INK};'
        f'letter-spacing:.3px">DOCUMENT COMPARISON</div>'
        f'<div style="font-size:13px;color:{_INK};margin-top:6px">'
        f'<b>{_html_escape(meta.get("left", "current"))}</b>'
        f'&nbsp;&nbsp;↔&nbsp;&nbsp;'
        f'<b>{_html_escape(meta.get("right", "other"))}</b>'
        f'{"&nbsp;&nbsp;•&nbsp;&nbsp;" + _html_escape(meta["when"]) if meta.get("when") else ""}'
        f'</div></div>')

    p.append('<div style="padding:22px 28px">')

    # Summary cards.
    p.append('<table cellspacing="10" cellpadding="0" '
             'style="margin:0 0 6px 0"><tr>')
    p.append(card("Total", len(changes), "#e7ebee", "#9aa6b0"))
    p.append(card("Added", added, *_KIND_STYLE["added"][:2]))
    p.append(card("Removed", removed, *_KIND_STYLE["removed"][:2]))
    p.append(card("Changed", changed, *_KIND_STYLE["changed"][:2]))
    p.append('</tr></table>')

    if not changes:
        p.append(
            f'<p style="margin-top:18px;padding:14px 18px;'
            f'background:{_KIND_STYLE["added"][0]};color:{_INK};'
            f'font-weight:600;border-left:4px solid '
            f'{_KIND_STYLE["added"][1]}">'
            f'✓ No differences — the documents are structurally identical.'
            f'</p></div></div></body></html>')
        return "".join(p)

    # Type breakdown line.
    bt = type_breakdown(changes)
    if bt:
        chips = "  ".join(
            f'<span style="background:{_PAGE};border:1px solid {_GRID};'
            f'border-radius:11px;padding:2px 10px;font-size:11px;'
            f'color:{_MUTED}">{_html_escape(k)}: '
            f'<b style="color:{_INK}">{v}</b></span>'
            for k, v in bt.items())
        p.append(
            f'<div style="margin:14px 0 10px 0">'
            f'<span style="font-size:11px;color:{_MUTED};font-weight:600;'
            f'text-transform:uppercase;letter-spacing:.6px;'
            f'margin-right:8px">By type&nbsp;&nbsp;</span>{chips}</div>')

    # Change table. Fixed layout + explicit column widths so the Old/New
    # value columns always get room and long values wrap (no per-character
    # squeeze, no horizontal scrollbar).
    wrap = ("word-break:break-word;overflow-wrap:anywhere;"
            "-qt-word-wrap:break-word")
    p.append(
        f'<table cellspacing="0" cellpadding="0" width="100%" '
        f'style="border-collapse:collapse;font-size:12.5px;'
        f'table-layout:fixed;width:100%">')
    p.append(
        '<colgroup>'
        '<col width="12%"><col width="26%">'
        '<col width="31%"><col width="31%"></colgroup>')
    p.append(
        f'<tr style="background:{_BAND}">'
        + "".join(
            f'<th width="{w}" style="text-align:left;padding:9px 11px;'
            f'color:{_INK};font-size:11px;font-weight:700;'
            f'text-transform:uppercase;letter-spacing:.5px">{h}</th>'
            for h, w in (("Change", "12%"), ("Path", "26%"),
                         ("Old", "31%"), ("New", "31%")))
        + '</tr>')
    for kind, path, left, right in changes:
        tint, accent, glyph, word = _KIND_STYLE.get(
            kind, ("#ffffff", _GRID, "?", kind))
        p.append(f'<tr style="background:{tint}">')
        p.append(
            f'<td width="12%" style="padding:8px 11px;'
            f'border-bottom:1px solid #ffffff;{wrap};'
            f'border-left:4px solid {accent}">'
            f'<b style="color:{_INK}">{glyph} {word}</b></td>')
        p.append(
            f'<td width="26%" style="padding:8px 11px;'
            f'border-bottom:1px solid #ffffff;'
            f'font-family:Menlo,Consolas,monospace;color:{_INK};{wrap}">'
            f'{_html_escape(path)}</td>')
        p.append(
            f'<td width="31%" style="padding:8px 11px;'
            f'border-bottom:1px solid #ffffff;'
            f'font-family:Menlo,Consolas,monospace;color:{_INK};{wrap}">'
            f'{_html_escape(_cell(left))}</td>')
        p.append(
            f'<td width="31%" style="padding:8px 11px;'
            f'border-bottom:1px solid #ffffff;'
            f'font-family:Menlo,Consolas,monospace;color:{_INK};{wrap}">'
            f'{_html_escape(_cell(right))}</td>')
        p.append('</tr>')
    p.append('</table>')
    p.append(
        f'<div style="margin-top:16px;font-size:10px;color:{_MUTED}">'
        f'Generated by OPENXMLJSON • position-based structural diff'
        f'</div>')
    p.append('</div></div></body></html>')
    return "".join(p)
