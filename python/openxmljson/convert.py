"""Cross-format converters: reconstructed Python values → JSON / XML / CSV.

Pure functions (no Qt) so they're headlessly testable. Values come from
DocumentModel.reconstruct(): dict / list / scalars, with XML subtrees
represented as {"tag", "attributes", "children"}.
"""

from __future__ import annotations

import io
import json
import re
from typing import Any, List


def to_pretty_json(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False)


def to_minified_json(value: Any) -> str:
    """Compact JSON: no spaces after separators, no newlines."""
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


# -- XML ------------------------------------------------------------------------

_TAG_OK = re.compile(r"[A-Za-z_][\w\-.]*$")


def _xml_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _tag_name(name: str) -> str:
    name = str(name)
    cleaned = re.sub(r"[^\w\-.]", "_", name) or "field"
    if not re.match(r"[A-Za-z_]", cleaned):
        cleaned = "_" + cleaned
    return cleaned


def _is_xml_node(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and set(value.keys()) == {"tag", "attributes", "children"}
        and isinstance(value.get("attributes"), dict)
        and isinstance(value.get("children"), list)
    )


def _scalar_text(value: Any) -> str:
    if value is None:
        return ""
    if value is True:
        return "true"
    if value is False:
        return "false"
    return str(value)


def _xml_write(
    value: Any, tag: str, out: List[str], indent: int, pretty: bool = True
) -> None:
    pad = "  " * indent if pretty else ""
    if _is_xml_node(value):
        attrs = "".join(
            f' {_tag_name(k)}="{_xml_escape(str(v))}"'
            for k, v in value["attributes"].items()
        )
        children = value["children"]
        name = _tag_name(value["tag"])
        if not children:
            out.append(f"{pad}<{name}{attrs}/>")
            return
        if len(children) == 1 and not isinstance(children[0], (dict, list)):
            text = _xml_escape(_scalar_text(children[0]))
            out.append(f"{pad}<{name}{attrs}>{text}</{name}>")
            return
        out.append(f"{pad}<{name}{attrs}>")
        for child in children:
            _xml_write(child, "item", out, indent + 1, pretty)
        out.append(f"{pad}</{name}>")
    elif isinstance(value, dict):
        out.append(f"{pad}<{tag}>")
        for key, item in value.items():
            _xml_write(item, _tag_name(key), out, indent + 1, pretty)
        out.append(f"{pad}</{tag}>")
    elif isinstance(value, list):
        out.append(f"{pad}<{tag}>")
        for item in value:
            _xml_write(item, "item", out, indent + 1, pretty)
        out.append(f"{pad}</{tag}>")
    else:
        text = _xml_escape(_scalar_text(value))
        out.append(f"{pad}<{tag}>{text}</{tag}>")


def to_xml(value: Any, root_tag: str = "root", pretty: bool = True) -> str:
    """Serialize any reconstructed value as XML. XML-origin subtrees keep
    their tags/attributes; JSON/CSV values map keys→elements and list
    items→<item>. With ``pretty=False`` the output is minified (no
    indentation or inter-tag newlines)."""
    out: List[str] = ['<?xml version="1.0" encoding="UTF-8"?>']
    _xml_write(value, _tag_name(root_tag), out, 0, pretty)
    if not pretty:
        return "".join(out)
    return "\n".join(out) + "\n"


# -- CSV ---------------------------------------------------------------------------

def _cell_text(value: Any) -> str:
    if value is None or isinstance(value, (str, int, float, bool)):
        return _scalar_text(value)
    return json.dumps(value, ensure_ascii=False)  # nested → compact JSON


def _quote(cell: str, delimiter: str) -> str:
    if any(c in cell for c in (delimiter, '"', "\n", "\r")):
        return '"' + cell.replace('"', '""') + '"'
    return cell


def _tabular_records(value: Any):
    """Find the list to tabulate: a list itself, or a dict's single list."""
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        lists = [v for v in value.values() if isinstance(v, list)]
        if len(lists) == 1:
            return lists[0]
    return None


def to_csv(value: Any, delimiter: str = ",") -> str:
    """Serialize a list of records as CSV. Records that are dicts share a
    header (union of keys, first-seen order); scalar records become a
    single 'value' column; nested cells become compact JSON. Raises
    ValueError when the value has no tabular shape."""
    records = _tabular_records(value)
    if records is None:
        raise ValueError(
            "This node is not tabular — export it as JSON or XML instead."
        )
    header: List[str] = []
    dict_records = all(isinstance(r, dict) for r in records) and records
    if dict_records:
        for record in records:
            for key in record.keys():
                if key not in header:
                    header.append(key)
    out = io.StringIO()
    if dict_records:
        out.write(delimiter.join(_quote(h, delimiter) for h in header) + "\n")
        for record in records:
            out.write(
                delimiter.join(
                    _quote(_cell_text(record.get(h, "")), delimiter)
                    for h in header
                )
                + "\n"
            )
    else:
        out.write("value\n")
        for record in records:
            out.write(_quote(_cell_text(record), delimiter) + "\n")
    return out.getvalue()
