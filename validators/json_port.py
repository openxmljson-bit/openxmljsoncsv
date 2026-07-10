"""Exact Python port of crates/oxj-core/src/json.rs (SPEC §15).

Same mode machine, same error messages, same byte offsets. Cross-checked
against stdlib json by validate_json.py.
"""

from __future__ import annotations

from index_port import (
    ARRAY,
    BOOL,
    KEY,
    MAX_DEPTH,
    NULL,
    NUMBER,
    OBJECT,
    STRING,
    Index,
    IndexBuilder,
)

WS = b" \t\n\r"
DIGITS = b"0123456789"


class ParseError(Exception):
    def __init__(self, offset: int, message: str):
        super().__init__(f"{message} at byte {offset}")
        self.offset = offset
        self.message = message


# Modes — one slot per open container (port of json.rs::Mode).
ROOT_VALUES = 0
ARR_VALUE = 1
ARR_VALUE_ONLY = 2
ARR_COMMA = 3
OBJ_KEY = 4
OBJ_KEY_ONLY = 5
OBJ_COMMA = 6
KEY_COLON = 7
KEY_VALUE = 8

_VALUE_MODES = (ROOT_VALUES, ARR_VALUE, ARR_VALUE_ONLY, KEY_VALUE)


def scan_string(data: bytes, start: int) -> int:
    """Return the exclusive end (one past the closing quote)."""
    i = start + 1
    n = len(data)
    while i < n:
        b = data[i]
        if b == 0x5C:  # backslash
            i += 2
        elif b == 0x22:  # quote
            return i + 1
        else:
            i += 1
    raise ParseError(start, "unterminated string")


def scan_number(data: bytes, start: int) -> int:
    n = len(data)
    i = start
    if i < n and data[i] == 0x2D:  # '-'
        i += 1
    if i >= n or data[i] not in DIGITS:
        raise ParseError(start, "invalid number")
    if data[i] == 0x30:  # '0'
        i += 1
    else:
        while i < n and data[i] in DIGITS:
            i += 1
    if i < n and data[i] == 0x2E:  # '.'
        i += 1
        if i >= n or data[i] not in DIGITS:
            raise ParseError(start, "invalid number")
        while i < n and data[i] in DIGITS:
            i += 1
    if i < n and data[i] in b"eE":
        i += 1
        if i < n and data[i] in b"+-":
            i += 1
        if i >= n or data[i] not in DIGITS:
            raise ParseError(start, "invalid number")
        while i < n and data[i] in DIGITS:
            i += 1
    return i


def is_number_token(data: bytes) -> bool:
    if not data:
        return False
    try:
        return scan_number(data, 0) == len(data)
    except ParseError:
        return False


def parse(data: bytes) -> Index:
    n = len(data)
    pos = 0
    if data.startswith(b"\xef\xbb\xbf"):
        pos = 3

    builder = IndexBuilder()
    modes = [ROOT_VALUES]
    root_values = 0
    last_root_end = -1

    def value_done(end: int) -> None:
        nonlocal root_values, last_root_end
        top = modes[-1]
        if top == ROOT_VALUES:
            root_values += 1
            last_root_end = end
        elif top in (ARR_VALUE, ARR_VALUE_ONLY):
            modes[-1] = ARR_COMMA
        elif top == KEY_VALUE:
            builder.pop()  # close the Key container (window preserved)
            modes[-1] = OBJ_COMMA
        else:  # pragma: no cover - unreachable
            raise AssertionError("value completed in non-value mode")

    while True:
        while pos < n and data[pos] in WS:
            pos += 1
        if pos >= n:
            break
        c = data[pos]
        mode = modes[-1]

        if mode in _VALUE_MODES:
            if c == 0x5D:  # ']'
                if mode == ARR_VALUE:
                    builder.close(pos + 1)
                    modes.pop()
                    pos += 1
                    value_done(pos)
                    continue
                if mode == ARR_VALUE_ONLY:
                    raise ParseError(pos, "trailing comma")
                raise ParseError(pos, "unexpected ']'")
            if mode == ROOT_VALUES and root_values > 0 and pos == last_root_end:
                raise ParseError(pos, "expected whitespace between top-level values")
            if c == 0x7B:  # '{'
                if builder.depth() >= MAX_DEPTH:
                    raise ParseError(pos, "nesting too deep")
                builder.open(OBJECT, pos)
                modes.append(OBJ_KEY)
                pos += 1
            elif c == 0x5B:  # '['
                if builder.depth() >= MAX_DEPTH:
                    raise ParseError(pos, "nesting too deep")
                builder.open(ARRAY, pos)
                modes.append(ARR_VALUE)
                pos += 1
            elif c == 0x22:  # '"'
                end = scan_string(data, pos)
                builder.leaf(STRING, pos, end - pos)
                pos = end
                value_done(pos)
            elif c == 0x2D or c in DIGITS:  # '-' or digit
                end = scan_number(data, pos)
                builder.leaf(NUMBER, pos, end - pos)
                pos = end
                value_done(pos)
            elif c == 0x74:  # 't'
                if data[pos : pos + 4] == b"true":
                    builder.leaf(BOOL, pos, 4)
                    pos += 4
                    value_done(pos)
                else:
                    raise ParseError(pos, "invalid literal")
            elif c == 0x66:  # 'f'
                if data[pos : pos + 5] == b"false":
                    builder.leaf(BOOL, pos, 5)
                    pos += 5
                    value_done(pos)
                else:
                    raise ParseError(pos, "invalid literal")
            elif c == 0x6E:  # 'n'
                if data[pos : pos + 4] == b"null":
                    builder.leaf(NULL, pos, 4)
                    pos += 4
                    value_done(pos)
                else:
                    raise ParseError(pos, "invalid literal")
            else:
                raise ParseError(pos, "unexpected character")
            continue

        if mode in (OBJ_KEY, OBJ_KEY_ONLY):
            if c == 0x22:  # '"'
                end = scan_string(data, pos)
                if builder.depth() >= MAX_DEPTH:
                    raise ParseError(pos, "nesting too deep")
                builder.open_fixed(KEY, pos, end - pos)
                modes[-1] = KEY_COLON
                pos = end
            elif c == 0x7D and mode == OBJ_KEY:  # '}'
                builder.close(pos + 1)
                modes.pop()
                pos += 1
                value_done(pos)
            elif c == 0x7D:
                raise ParseError(pos, "trailing comma")
            else:
                raise ParseError(
                    pos,
                    "expected '\"' or '}'" if mode == OBJ_KEY else "expected '\"'",
                )
        elif mode == KEY_COLON:
            if c == 0x3A:  # ':'
                modes[-1] = KEY_VALUE
                pos += 1
            else:
                raise ParseError(pos, "expected ':'")
        elif mode == OBJ_COMMA:
            if c == 0x2C:  # ','
                modes[-1] = OBJ_KEY_ONLY
                pos += 1
            elif c == 0x7D:  # '}'
                builder.close(pos + 1)
                modes.pop()
                pos += 1
                value_done(pos)
            else:
                raise ParseError(pos, "expected ',' or '}'")
        elif mode == ARR_COMMA:
            if c == 0x2C:  # ','
                modes[-1] = ARR_VALUE_ONLY
                pos += 1
            elif c == 0x5D:  # ']'
                builder.close(pos + 1)
                modes.pop()
                pos += 1
                value_done(pos)
            else:
                raise ParseError(pos, "expected ',' or ']'")
        else:  # pragma: no cover - unreachable
            raise AssertionError(f"bad mode {mode}")

    if len(modes) > 1:
        raise ParseError(pos, "unexpected end of input")
    if root_values == 0:
        raise ParseError(pos, "empty document")
    return builder.finish(n)
