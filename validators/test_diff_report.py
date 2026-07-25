"""Tests for the diff report formatters (Qt-free)."""

import csv
import io
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

from openxmljson import difftool as d  # noqa: E402

A = {"name": "x", "items": [1, 2, 3], "meta": {"v": 1}, "tags": ["p", "q"]}
B = {"name": "y", "items": [1, 9], "meta": {"v": 2}, "tags": ["p"],
     "extra": True}


def _changes():
    return d.diff(A, B)


def test_change_rows_have_types_and_values():
    rows = {r["path"]: r for r in d.change_rows(_changes())}
    assert rows["$.name"]["old"] == "x" and rows["$.name"]["new"] == "y"
    assert rows["$.name"]["old_type"] == "string"
    added = rows["$.extra"]
    assert added["kind"] == "added" and added["old_type"] == "—"
    removed = rows["$.items[2]"]
    assert removed["kind"] == "removed" and removed["new_type"] == "—"


def test_type_breakdown_counts():
    bt = d.type_breakdown(_changes())
    assert bt.get("number", 0) >= 1
    assert sum(bt.values()) == len(_changes())


def test_json_report_is_valid_and_complete():
    obj = json.loads(d.to_json_report(_changes(), {"left": "a", "right": "b"}))
    assert obj["meta"]["left"] == "a"
    s = obj["summary"]
    assert s["total"] == s["added"] + s["removed"] + s["changed"]
    assert len(obj["changes"]) == s["total"]


def test_csv_report_parses_with_header():
    text = d.to_csv_report(_changes())
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[0] == ["kind", "path", "depth", "old_type", "new_type",
                       "old", "new"]
    assert len(rows) - 1 == len(_changes())


def test_html_report_is_self_contained_and_escapes():
    evil = d.diff({"a": "<script>"}, {"a": "&bad"})
    html = d.to_html_report(evil, {"left": "l", "right": "r"})
    assert html.startswith("<!DOCTYPE html>") and html.rstrip().endswith(
        "</html>")
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "&amp;bad" in html


def test_html_identical_documents_message():
    html = d.to_html_report([], {"left": "l", "right": "r"})
    assert "identical" in html


def test_txt_report_has_meta_header():
    txt = d.to_txt_report(_changes(), {"left": "a.json", "right": "b.json"})
    assert "Document Comparison" in txt and "a.json" in txt
