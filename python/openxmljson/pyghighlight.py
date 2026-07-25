"""Pygments-backed syntax highlighting for the plain-text tabs.

The text tabs are read-only and their content is set once, so instead of
re-lexing per line inside ``highlightBlock`` (which breaks multiline strings
and comments), the whole text is lexed **once** up front into per-line format
ranges; the Qt highlighter then just applies the precomputed ranges for each
block. That keeps multiline constructs correct and the per-paint cost trivial.

Split in two so the core stays testable without Qt:

* ``line_ranges(text, lexer)`` / ``lexer_for(filename)`` — pure functions.
* ``PygmentsHighlighter`` — the ``QSyntaxHighlighter`` that applies them.

Pygments is a soft dependency: ``available()`` gates every entry point, and
callers fall back to the built-in highlighters when it's missing.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

#: Full-text lexing cost scales with size; above this the caller should fall
#: back to the cheap built-in per-line highlighters. Matches the text viewer's
#: own 32 MB cap (docview.TEXT_VIEW_MAX_BYTES), so every text tab the viewer
#: can show gets highlighting.
PYGMENTS_MAX_BYTES = 32 * 1024 * 1024   # 32 MB

#: (line_number → [(column, length, role)]) — role is one of ROLES.
Ranges = Dict[int, List[Tuple[int, int, str]]]

ROLES = ("keyword", "string", "number", "comment", "literal",
         "decorator", "definition")


def available() -> bool:
    try:
        import pygments  # noqa: F401
        return True
    except ImportError:
        return False


def lexer_for(filename: str):
    """A Pygments lexer for ``filename``, or None (unknown type / no pygments).
    Plain ``.txt`` intentionally resolves to None (nothing to highlight)."""
    if not available():
        return None
    from pygments.lexers import get_lexer_for_filename
    from pygments.util import ClassNotFound

    try:
        lexer = get_lexer_for_filename(filename, stripnl=False, ensurenl=False)
    except ClassNotFound:
        return None
    if lexer.__class__.__name__ == "TextLexer":
        return None
    return lexer


def _role_of(token_type) -> Optional[str]:
    """Map a Pygments token type onto one of our theme roles (None = plain)."""
    from pygments.token import Comment, Keyword, Literal, Name, Number, String

    if token_type in Comment:
        return "comment"
    if token_type in String:
        return "string"
    if token_type in Number:
        return "number"
    if token_type in Keyword:
        if token_type in Keyword.Constant:
            return "literal"
        return "keyword"
    if token_type in Name.Decorator:
        return "decorator"
    if token_type in (Name.Function, Name.Class):
        return "definition"
    if token_type in Name.Builtin or token_type in Name.Constant:
        return "literal"
    if token_type in Literal:
        return "string"
    return None


def line_ranges(text: str, lexer) -> Ranges:
    """Lex ``text`` once and return per-line format ranges. Token values that
    span newlines (multiline strings, block comments) are split per line."""
    ranges: Ranges = {}
    line = 0
    col = 0
    for _, token_type, value in lexer.get_tokens_unprocessed(text):
        role = _role_of(token_type)
        for i, part in enumerate(value.split("\n")):
            if i > 0:
                line += 1
                col = 0
            if part and role is not None:
                ranges.setdefault(line, []).append((col, len(part), role))
            col += len(part)
    return ranges


# -- Qt side -------------------------------------------------------------------

def make_highlighter(document, style, filename: str, text: str):
    """A ``PygmentsHighlighter`` for ``document``, or None when Pygments is
    unavailable, the file type is unknown, or the text is too large."""
    if not available() or len(text) > PYGMENTS_MAX_BYTES:
        return None
    lexer = lexer_for(filename)
    if lexer is None:
        return None
    try:
        ranges = line_ranges(text, lexer)
    except Exception:  # noqa: BLE001 - lexing is best-effort
        return None
    return PygmentsHighlighter(document, style, ranges)


try:
    from PySide6.QtGui import QFont, QSyntaxHighlighter, QTextCharFormat
except ImportError:   # headless test environment without Qt
    QSyntaxHighlighter = object   # type: ignore[misc,assignment]


class PygmentsHighlighter(QSyntaxHighlighter):   # type: ignore[misc]
    """Applies precomputed per-line ranges. ``set_style`` rebuilds the
    role → format map and rehighlights (theme switches)."""

    def __init__(self, document, style, ranges: Ranges):
        super().__init__(document)
        self._ranges = ranges
        self.set_style(style)

    @staticmethod
    def _fmt(color, bold=False) -> "QTextCharFormat":
        f = QTextCharFormat()
        f.setForeground(color)
        if bold:
            f.setFontWeight(QFont.Weight.Bold)
        return f

    def set_style(self, style) -> None:
        self._formats = {
            "keyword": self._fmt(style.boolean, bold=True),
            "string": self._fmt(style.string),
            "number": self._fmt(style.number),
            "comment": self._fmt(style.guide),
            "literal": self._fmt(style.null),
            "decorator": self._fmt(style.boolean),
            "definition": self._fmt(style.key, bold=True),
        }
        self.rehighlight()

    def highlightBlock(self, text: str) -> None:  # noqa: N802
        for col, length, role in self._ranges.get(
                self.currentBlock().blockNumber(), ()):
            fmt = self._formats.get(role)
            if fmt is not None:
                self.setFormat(col, length, fmt)
