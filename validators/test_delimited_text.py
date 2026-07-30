"""Tests for delimited-text detection (opening a .txt as CSV). Qt-free: the
detector is imported by source so these run headlessly."""

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

# app.py imports PySide6, which may be unavailable/headless-hostile in CI, so
# exec just the helper out of the module source.
_APP = os.path.join(os.path.dirname(__file__), "..", "python", "openxmljson",
                    "app.py")


def _load_detector():
    src = open(_APP, encoding="utf-8").read()
    start = src.index("def _looks_delimited(")
    end = src.index("SCOPES = [", start)
    ns = {}
    exec(compile(src[start:end], "app_detector", "exec"), ns)
    return ns["_looks_delimited"]


_looks_delimited = _load_detector()


def _tmp(text: str, suffix=".txt") -> str:
    fh = tempfile.NamedTemporaryFile("w", suffix=suffix, delete=False,
                                     encoding="utf-8")
    fh.write(text)
    fh.close()
    return fh.name


VEHICLE_SAMPLE = (
    "VEHICLE_ID|DELETED|YEAR|MAKE|MODEL|ENGINE|CODE|TYPE\n"
    "507447|0|1960|Fairthorpe|Electrina|1.0L 998CC L4 Carb VIN: 99H|1489572|3\n"
    "507450|0|1961|Fairthorpe|Electrina|1.0L 998CC L4 Carb VIN: 99H|1489978|3\n"
    "507453|0|1962|Fairthorpe|Electrina|1.0L 998CC L4 Carb VIN: 99H|1490494|3\n"
    "507474|0|1951|Ferrari|212|2.6L 2562CC V12 Carb|1487305|3\n"
)


def test_pipe_delimited_vehicle_sample():
    p = _tmp(VEHICLE_SAMPLE)
    try:
        assert _looks_delimited(p) == "|"
    finally:
        os.unlink(p)


def test_comma_and_tab_and_semicolon():
    for text, expected in (
        ("a,b,c\n1,2,3\n4,5,6\n", ","),
        ("a\tb\tc\n1\t2\t3\n", "\t"),
        ("a;b;c\n1;2;3\n", ";"),
    ):
        p = _tmp(text)
        try:
            assert _looks_delimited(p) == expected
        finally:
            os.unlink(p)


def test_prose_is_not_delimited():
    p = _tmp("hello world\nthis is plain text\nno delimiters here\n")
    try:
        assert _looks_delimited(p) is None
    finally:
        os.unlink(p)


def test_ragged_field_counts_rejected():
    # Inconsistent column counts => not a table.
    p = _tmp("a|b|c\na|b\na|b|c|d\n")
    try:
        assert _looks_delimited(p) is None
    finally:
        os.unlink(p)


def test_single_line_is_not_enough():
    p = _tmp("only|one|line\n")
    try:
        assert _looks_delimited(p) is None
    finally:
        os.unlink(p)


def test_prefers_delimiter_with_most_columns():
    # Both ',' and '|' are consistent; '|' yields more fields, so it wins.
    p = _tmp("a|b|c|d,e\n1|2|3|4,5\n")
    try:
        assert _looks_delimited(p) == "|"
    finally:
        os.unlink(p)


def test_missing_file_returns_none():
    assert _looks_delimited("/nonexistent/path/file.txt") is None
