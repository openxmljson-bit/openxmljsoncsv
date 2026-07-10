"""Validate the CSV parser port against stdlib csv (SPEC §15).

- Edge cases: quoting, escaped quotes, embedded delimiters/newlines,
  ragged rows, blank lines, CRLF, sniffing, header detection, typing.
- Fuzz: 5,000 random tables written by csv.writer with varied delimiters,
  line terminators and quoting; the rows tokenized by the port (with ""
  unescaped, as the display layer does) must equal csv.reader's rows.

Usage: python validate_csv.py [n_tables]
"""

from __future__ import annotations

import csv
import io
import random
import sys

import csv_port
from csv_port import Cell, CsvOptions
from index_port import ARRAY, KEY, NUMBER, OBJECT, STRING


# ---------------------------------------------------------------------------
# Row extraction from the tokenizer (comparison path, mirrors on-screen
# unescaping of "" — SPEC §7.3).
# ---------------------------------------------------------------------------

def port_rows(data: bytes, delim: int):
    rows = []
    pos = 0
    if data.startswith(b"\xef\xbb\xbf"):
        pos = 3
    while True:
        nxt = csv_port.next_record(data, pos, delim)
        if nxt is None:
            return rows
        rec, pos = nxt
        row = []
        for cell in rec.cells:
            raw = data[cell.offset : cell.offset + cell.len].decode("utf-8")
            row.append(raw.replace('""', '"') if cell.quoted else raw)
        rows.append(row)


def ref_rows(text: str, delim: str):
    # csv.reader yields [] for blank lines; the port skips them (SPEC §7.3).
    return [
        row
        for row in csv.reader(io.StringIO(text, newline=""), delimiter=delim)
        if row != []
    ]


# ---------------------------------------------------------------------------
# Fuzz generator
# ---------------------------------------------------------------------------

WORDS = ["alpha", "beta", "gamma", "délta", "x y", "z", "née", "世界", "_"]
DELIMS = [",", ";", "\t", "|"]


def rand_cell(rng: random.Random, delim: str) -> str:
    r = rng.random()
    if r < 0.25:
        return rng.choice(WORDS)
    if r < 0.40:
        n = rng.randint(-10**6, 10**6)
        return str(n) if rng.random() < 0.7 else f"{rng.uniform(-1e3, 1e3):.4f}"
    if r < 0.50:
        return "0" + str(rng.randint(0, 99999)).zfill(4)  # leading zero: ZIP-like
    if r < 0.60:
        return ""  # empty cell
    if r < 0.75:  # needs quoting: embedded delimiter
        return f"a{delim}b{rng.choice(WORDS)}"
    if r < 0.85:  # embedded quotes
        return f'he said "{rng.choice(WORDS)}" loudly'
    if r < 0.95:  # embedded newline
        return f"line1\nline2 {rng.choice(WORDS)}"
    return f"mix\"{delim}\n{rng.choice(WORDS)}"


def rand_table(rng: random.Random):
    delim = rng.choice(DELIMS)
    lineterm = rng.choice(["\n", "\r\n"])
    n_rows = rng.randint(1, 12)
    n_cols = rng.randint(1, 6)
    rows = []
    for _ in range(n_rows):
        cols = n_cols
        if rng.random() < 0.15:  # ragged
            cols = max(1, n_cols + rng.randint(-2, 2))
        row = [rand_cell(rng, delim) for _ in range(cols)]
        if all(c == "" for c in row) and len(row) == 1:
            row = ["x"]  # avoid rows that serialize to blank lines
        rows.append(row)
    buf = io.StringIO()
    writer = csv.writer(
        buf,
        delimiter=delim,
        lineterminator=lineterm,
        quoting=csv.QUOTE_MINIMAL,
    )
    writer.writerows(rows)
    return rows, buf.getvalue(), delim


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def check(text: str, delim: str = ",") -> None:
    data = text.encode("utf-8")
    got = port_rows(data, ord(delim))
    want = ref_rows(text, delim)
    assert got == want, f"{text!r}: {got!r} != {want!r}"


def kinds_of_first_record(text: str):
    data = text.encode("utf-8")
    idx = csv_port.parse_csv(data)
    root = idx.root()
    rec = next(iter(idx.children(root)))
    out = []
    for kid in idx.children(rec):
        node = idx.node(kid)
        if node.kind == KEY:
            val = idx.node(next(iter(idx.children(kid))))
            out.append(val.kind)
        else:
            out.append(node.kind)
    return idx.node(rec).kind, out


def run_edge_cases() -> None:
    # Tokenization vs stdlib.
    check("a,b\n1,2")
    check("a,b\r\n1,2\r\n")
    check('a,"b,c"\n"x\ny",z')
    check('"he said ""hi""",2\n3,4')
    check("a,,b\n,,\nx,y,z")
    check("a;b;c\n1;2;3", ";")
    check("a\tb\n1\t2", "\t")
    check("a|b\n1|2", "|")
    check("one\ntwo\nthree")  # single column
    check("a,b\n\n\n1,2\n")  # blank lines skipped on both sides
    check('",",","\na,b')  # quoted delimiters
    check('""\n"",""')  # quoted empties (not a blank line)

    # Sniffing.
    assert csv_port.sniff_delimiter(b"a,b,c\n1,2,3") == ord(",")
    assert csv_port.sniff_delimiter(b"a;b;c") == ord(";")
    assert csv_port.sniff_delimiter(b"a\tb\tc") == ord("\t")
    assert csv_port.sniff_delimiter(b"a|b|c") == ord("|")
    assert csv_port.sniff_delimiter(b"plain") == ord(",")
    assert csv_port.sniff_delimiter(b'"a;;;;",b\n1,2') == ord(",")

    # Header detection + tree shape + typing.
    kind, kids = kinds_of_first_record("name,age\nalice,30\nbob,41")
    assert kind == OBJECT and kids == [STRING, NUMBER]
    kind, kids = kinds_of_first_record("1,2\n3,4")  # numeric first row: no header
    assert kind == ARRAY
    kind, kids = kinds_of_first_record("alpha,beta")  # single record: no header
    assert kind == ARRAY
    kind, kids = kinds_of_first_record("zip,n\n02134,5\n10001,7")
    assert kids == [STRING, NUMBER], "leading-zero stays String"
    kind, kids = kinds_of_first_record('a,b\n"123",4\n5,6')
    assert kids == [STRING, NUMBER], "quoted cells are always String"

    # Ragged rows: no data dropped.
    idx = csv_port.parse_csv(b"a,b\n1\n2,3,4")
    recs = list(idx.children(idx.root()))
    assert idx.child_count(recs[0]) == 1  # short row: fewer keys
    assert idx.child_count(recs[1]) == 3  # extra column: unlabeled value

    # Unterminated quote resyncs at EOF; never a hard failure (SPEC §12).
    idx = csv_port.parse_csv(b'a,b\n"oops,2')
    assert idx.child_count(idx.root()) == 1

    # Header override.
    idx = csv_port.parse_csv(b"a,b\n1,2", CsvOptions(has_header=False))
    rec = next(iter(idx.children(idx.root())))
    assert idx.node(rec).kind == ARRAY

    # TSV preset.
    idx = csv_port.parse_csv(b"a\tb\n1\t2", csv_port.tsv_options())
    rec = next(iter(idx.children(idx.root())))
    assert idx.child_count(rec) == 2

    # Empty file.
    idx = csv_port.parse_csv(b"")
    assert idx.child_count(idx.root()) == 0
    print("edge cases: OK")


def run_fuzz(n_tables: int, seed: int = 20260702) -> None:
    rng = random.Random(seed)
    ok = 0
    for i in range(n_tables):
        rows, text, delim = rand_table(rng)
        data = text.encode("utf-8")
        got = port_rows(data, ord(delim))
        want = ref_rows(text, delim)
        assert got == want, (
            f"fuzz table {i} (delim={delim!r}) mismatch:\n{text[:400]!r}\n"
            f"port: {got!r}\nref:  {want!r}"
        )
        # The writer round-trips the original rows too (stringified).
        assert want == [[str(c) for c in row] for row in rows]
        ok += 1
    print(f"fuzz: {ok} / {n_tables} tables identical to stdlib csv")


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 5000
    run_edge_cases()
    run_fuzz(n)
    print("validate_csv: PASS")


if __name__ == "__main__":
    main()
