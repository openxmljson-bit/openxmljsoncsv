"""Exact Python port of crates/oxj-core/src/csv.rs (SPEC §15).

Cross-checked against stdlib csv by validate_csv.py.
"""

from __future__ import annotations

from typing import List, NamedTuple, Optional, Tuple

from index_port import ARRAY, KEY, NUMBER, OBJECT, STRING, Index, IndexBuilder
from json_port import is_number_token

CANDIDATES = b",;\t|"


class CsvOptions(NamedTuple):
    delimiter: Optional[int] = None
    has_header: Optional[bool] = None


def tsv_options() -> CsvOptions:
    return CsvOptions(delimiter=0x09, has_header=None)


class Cell(NamedTuple):
    offset: int
    len: int
    quoted: bool


class Record(NamedTuple):
    cells: List[Cell]
    start: int
    end: int


def sniff_delimiter(data: bytes) -> int:
    counts = [0, 0, 0, 0]
    in_quotes = False
    for b in data:
        if b == 0x22:  # '"'
            in_quotes = not in_quotes
        elif b == 0x0A and not in_quotes:  # '\n'
            break
        elif not in_quotes:
            for i, c in enumerate(CANDIDATES):
                if b == c:
                    counts[i] += 1
    best = 0
    for i in range(1, 4):
        if counts[i] > counts[best]:
            best = i
    return 0x2C if counts[best] == 0 else CANDIDATES[best]


def _at_newline(data: bytes, pos: int) -> bool:
    return data[pos] == 0x0A or (
        data[pos] == 0x0D and pos + 1 < len(data) and data[pos + 1] == 0x0A
    )


def _skip_newline(data: bytes, pos: int) -> int:
    return pos + 2 if data[pos] == 0x0D else pos + 1


def next_record(data: bytes, pos: int, delim: int) -> Optional[Tuple[Record, int]]:
    n = len(data)
    while pos < n and _at_newline(data, pos):
        pos = _skip_newline(data, pos)
    if pos >= n:
        return None
    start = pos
    cells: List[Cell] = []
    while True:
        # ---- one cell ---------------------------------------------------
        if pos < n and data[pos] == 0x22:  # '"'
            pos += 1
            v_start = pos
            while True:
                if pos >= n:
                    v_end = pos  # unterminated quote: resync at EOF
                    break
                if data[pos] == 0x22:
                    if pos + 1 < n and data[pos + 1] == 0x22:
                        pos += 2  # escaped quote, stays literal
                    else:
                        v_end = pos
                        pos += 1
                        break
                else:
                    pos += 1
            cells.append(Cell(v_start, v_end - v_start, True))
            while pos < n and data[pos] != delim and not _at_newline(data, pos):
                pos += 1
        else:
            v_start = pos
            while pos < n and data[pos] != delim and not _at_newline(data, pos):
                pos += 1
            cells.append(Cell(v_start, pos - v_start, False))
        # ---- cell terminator --------------------------------------------
        if pos >= n:
            return (Record(cells, start, pos), pos)
        if data[pos] == delim:
            pos += 1
            continue
        end = pos
        pos = _skip_newline(data, pos)
        return (Record(cells, start, end), pos)


def is_texty(data: bytes, cell: Cell) -> bool:
    if cell.len == 0:
        return False
    if cell.quoted:
        return True
    return not is_number_token(data[cell.offset : cell.offset + cell.len])


def value_kind(data: bytes, cell: Cell) -> int:
    if not cell.quoted and is_number_token(data[cell.offset : cell.offset + cell.len]):
        return NUMBER
    return STRING


def _emit(
    builder: IndexBuilder,
    data: bytes,
    rec: Record,
    header: Optional[List[Cell]],
) -> None:
    if header is not None:
        builder.open(OBJECT, rec.start)
        for i, cell in enumerate(rec.cells):
            kind = value_kind(data, cell)
            if i < len(header):
                h = header[i]
                builder.open_fixed(KEY, h.offset, h.len)
                builder.leaf(kind, cell.offset, cell.len)
                builder.pop()
            else:
                builder.leaf(kind, cell.offset, cell.len)
        builder.close(rec.end)
    else:
        builder.open(ARRAY, rec.start)
        for cell in rec.cells:
            builder.leaf(value_kind(data, cell), cell.offset, cell.len)
        builder.close(rec.end)


def parse_csv(data: bytes, opts: CsvOptions = CsvOptions()) -> Index:
    pos = 0
    if data.startswith(b"\xef\xbb\xbf"):
        pos = 3
    body = data[pos:]
    delim = opts.delimiter if opts.delimiter is not None else sniff_delimiter(body)

    builder = IndexBuilder()

    first = next_record(data, pos, delim)
    if first is None:
        return builder.finish(len(data))
    rec1, after1 = first
    second = next_record(data, after1, delim)

    if opts.has_header is not None:
        has_header = opts.has_header
    else:
        has_header = second is not None and all(
            is_texty(data, c) for c in rec1.cells
        )

    header = list(rec1.cells) if has_header else None

    if not has_header:
        _emit(builder, data, rec1, header)
    cursor = after1
    if second is not None:
        rec2, after2 = second
        _emit(builder, data, rec2, header)
        cursor = after2
    while True:
        nxt = next_record(data, cursor, delim)
        if nxt is None:
            break
        rec, cursor = nxt
        _emit(builder, data, rec, header)

    return builder.finish(len(data))
