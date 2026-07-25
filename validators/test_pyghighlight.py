"""Tests for the Pygments-backed highlighter's pure (Qt-free) core."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

from openxmljson.pyghighlight import (  # noqa: E402
    available,
    lexer_for,
    line_ranges,
)

pytestmark = pytest.mark.skipif(not available(), reason="pygments not installed")


def _roles_on_line(ranges, line):
    return {role for _, _, role in ranges.get(line, [])}


def test_lexer_selection():
    assert lexer_for("script.py") is not None
    assert lexer_for("app.js") is not None
    assert lexer_for("data.rb") is not None       # breadth beyond built-ins
    assert lexer_for("notes.txt") is None         # plain text: no highlighting
    assert lexer_for("mystery.zzz-unknown") is None


def test_python_ranges_basic():
    src = 'def f(x):\n    # comment\n    return "s" + 42\n'
    ranges = line_ranges(src, lexer_for("a.py"))
    assert "keyword" in _roles_on_line(ranges, 0)      # def
    assert "definition" in _roles_on_line(ranges, 0)   # f
    assert _roles_on_line(ranges, 1) == {"comment"}
    line2 = _roles_on_line(ranges, 2)
    assert {"keyword", "string", "number"} <= line2    # return, "s", 42


def test_multiline_string_spans_lines():
    src = 's = """line one\nline two\nline three"""\nx = 1\n'
    ranges = line_ranges(src, lexer_for("a.py"))
    # the triple-quoted body must be marked as string on every line it spans
    for line in (0, 1, 2):
        assert "string" in _roles_on_line(ranges, line)
    # and the following line is code again, not string
    assert "number" in _roles_on_line(ranges, 3)


def test_columns_and_lengths_match_text():
    src = "x = 42\n"
    ranges = line_ranges(src, lexer_for("a.py"))
    spans = [(c, l) for c, l, role in ranges[0] if role == "number"]
    assert spans == [(4, 2)]   # "42" at column 4, length 2
