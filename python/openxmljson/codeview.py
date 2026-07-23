"""A lightweight read-only code view: a QPlainTextEdit with a line-number
gutter, plus a JavaScript syntax highlighter. Used by the plain-text (.txt/.js)
tabs. Folding is intentionally not implemented (QPlainTextEdit has no native
folding); this stays Essentials-only with no heavy editor dependency.
"""

from __future__ import annotations

import re

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QPainter,
    QSyntaxHighlighter,
    QTextCharFormat,
)
from PySide6.QtWidgets import QPlainTextEdit, QWidget


def beautify_js(src: str, indent: int = 2) -> str:
    """Beautify JavaScript source with jsbeautifier (raises ImportError if the
    package isn't installed; the caller decides how to fall back)."""
    import jsbeautifier

    opts = jsbeautifier.default_options()
    opts.indent_size = indent
    return jsbeautifier.beautify(src, opts)


# -- code editor with a line-number gutter ------------------------------------


class _LineNumberArea(QWidget):
    def __init__(self, editor: "CodeEditor"):
        super().__init__(editor)
        self._editor = editor

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self._editor.line_number_area_width(), 0)

    def paintEvent(self, event):  # noqa: N802
        self._editor.paint_line_numbers(event)


class CodeEditor(QPlainTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._gutter = _LineNumberArea(self)
        self._gutter_bg = QColor("#f0f0f0")
        self._gutter_fg = QColor("#999999")
        self.blockCountChanged.connect(lambda _=0: self._update_margins())
        self.updateRequest.connect(self._on_update_request)
        self._update_margins()

    # -- gutter geometry ------------------------------------------------------

    def line_number_area_width(self) -> int:
        digits = max(2, len(str(max(1, self.blockCount()))))
        return 14 + self.fontMetrics().horizontalAdvance("9") * digits

    def _update_margins(self) -> None:
        self.setViewportMargins(self.line_number_area_width(), 0, 0, 0)

    def _on_update_request(self, rect, dy) -> None:
        if dy:
            self._gutter.scroll(0, dy)
        else:
            self._gutter.update(0, rect.y(), self._gutter.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_margins()

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        cr = self.contentsRect()
        self._gutter.setGeometry(
            QRect(cr.left(), cr.top(), self.line_number_area_width(), cr.height()))

    def paint_line_numbers(self, event) -> None:
        painter = QPainter(self._gutter)
        painter.fillRect(event.rect(), self._gutter_bg)
        block = self.firstVisibleBlock()
        number = block.blockNumber()
        offset = self.contentOffset()
        top = self.blockBoundingGeometry(block).translated(offset).top()
        bottom = top + self.blockBoundingRect(block).height()
        painter.setPen(self._gutter_fg)
        h = self.fontMetrics().height()
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                painter.drawText(
                    0, int(top), self._gutter.width() - 6, int(h),
                    int(Qt.AlignmentFlag.AlignRight), str(number + 1))
            block = block.next()
            top = bottom
            bottom = top + self.blockBoundingRect(block).height()
            number += 1

    # -- theming --------------------------------------------------------------

    def set_style(self, style) -> None:
        self._gutter_bg = (style.view_bg.darker(108) if style.dark
                           else style.view_bg.darker(104))
        self._gutter_fg = style.guide if hasattr(style, "guide") else QColor("#999")
        self.setStyleSheet(
            "QPlainTextEdit {"
            f" background: {style.view_bg.name()};"
            f" color: {style.text.name()};"
            f" selection-background-color: {style.selection_bg.name()};"
            f" selection-color: {style.text.name()};"
            " border: none; padding: 4px; }"
        )
        self._gutter.update()


# -- JavaScript syntax highlighter --------------------------------------------

_JS_KEYWORDS = {
    "abstract", "arguments", "await", "async", "break", "case", "catch",
    "class", "const", "continue", "debugger", "default", "delete", "do",
    "else", "enum", "export", "extends", "finally", "for", "function", "get",
    "if", "implements", "import", "in", "instanceof", "interface", "let",
    "new", "of", "package", "private", "protected", "public", "return", "set",
    "static", "super", "switch", "this", "throw", "try", "typeof", "var",
    "void", "while", "with", "yield",
}
_JS_LITERALS = {"true", "false", "null", "undefined", "NaN", "Infinity"}


class JsHighlighter(QSyntaxHighlighter):
    NORMAL, BLOCK_COMMENT = 0, 1

    def __init__(self, document, style):
        super().__init__(document)
        self.set_style(style)

    @staticmethod
    def _fmt(color) -> QTextCharFormat:
        f = QTextCharFormat()
        f.setForeground(color)
        return f

    def set_style(self, style) -> None:
        self.f_keyword = self._fmt(style.boolean)   # purple
        self.f_literal = self._fmt(style.null)       # purple
        self.f_string = self._fmt(style.string)
        self.f_number = self._fmt(style.number)
        self.f_comment = self._fmt(style.guide)
        self.rehighlight()

    def highlightBlock(self, text: str) -> None:  # noqa: N802
        n = len(text)
        i = 0
        # Continue a /* … */ block comment from the previous line.
        if self.previousBlockState() == self.BLOCK_COMMENT:
            end = text.find("*/")
            if end == -1:
                self.setFormat(0, n, self.f_comment)
                self.setCurrentBlockState(self.BLOCK_COMMENT)
                return
            self.setFormat(0, end + 2, self.f_comment)
            i = end + 2
        self.setCurrentBlockState(self.NORMAL)

        while i < n:
            c = text[i]
            if text.startswith("//", i):
                self.setFormat(i, n - i, self.f_comment)
                return
            if text.startswith("/*", i):
                end = text.find("*/", i + 2)
                if end == -1:
                    self.setFormat(i, n - i, self.f_comment)
                    self.setCurrentBlockState(self.BLOCK_COMMENT)
                    return
                self.setFormat(i, end + 2 - i, self.f_comment)
                i = end + 2
                continue
            if c in "\"'`":
                j = self._string_end(text, i, c)
                self.setFormat(i, j - i, self.f_string)
                i = j
                continue
            if c.isdigit() or (c == "." and i + 1 < n and text[i + 1].isdigit()):
                j = self._number_end(text, i)
                self.setFormat(i, j - i, self.f_number)
                i = j
                continue
            if c.isalpha() or c in "_$":
                j = i + 1
                while j < n and (text[j].isalnum() or text[j] in "_$"):
                    j += 1
                word = text[i:j]
                if word in _JS_KEYWORDS:
                    self.setFormat(i, j - i, self.f_keyword)
                elif word in _JS_LITERALS:
                    self.setFormat(i, j - i, self.f_literal)
                i = j
                continue
            i += 1

    @staticmethod
    def _string_end(text: str, i: int, quote: str) -> int:
        n = len(text)
        j = i + 1
        while j < n:
            if text[j] == "\\":
                j += 2
                continue
            if text[j] == quote:
                return j + 1
            j += 1
        return n  # unterminated on this line

    @staticmethod
    def _number_end(text: str, i: int) -> int:
        n = len(text)
        j = i
        while j < n and (text[j].isalnum() or text[j] in "._"):
            j += 1
        return j


# -- log file syntax highlighter ----------------------------------------------

#: Timestamp at the start of a line: ISO (2026-07-24T10:11:12.345) or a bare
#: clock (10:11:12,345), optionally bracketed.
_LOG_TS = re.compile(
    r"^\s*\[?("
    r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?"
    r"|\d{2}:\d{2}:\d{2}(?:[.,]\d+)?"
    r")\]?"
)

#: Severity keywords → a color role (resolved in set_style).
_LOG_LEVELS = [
    (re.compile(r"\b(ERROR|ERR|FATAL|CRITICAL|SEVERE|PANIC|EMERG|ALERT)\b",
                re.IGNORECASE), "error"),
    (re.compile(r"\b(WARN|WARNING)\b", re.IGNORECASE), "warn"),
    (re.compile(r"\b(INFO|NOTICE)\b", re.IGNORECASE), "info"),
    (re.compile(r"\b(DEBUG|TRACE|FINE|FINER|FINEST|VERBOSE)\b", re.IGNORECASE),
     "debug"),
]

_QUOTED = re.compile(r"\"[^\"]*\"|'[^']*'")


class LogHighlighter(QSyntaxHighlighter):
    """Colorizes plain-text log files: leading timestamps, severity levels
    (ERROR/WARN/INFO/DEBUG …), and quoted strings."""

    def __init__(self, document, style):
        super().__init__(document)
        self.set_style(style)

    @staticmethod
    def _fmt(color, bold=False) -> QTextCharFormat:
        f = QTextCharFormat()
        f.setForeground(color)
        if bold:
            f.setFontWeight(QFont.Weight.Bold)
        return f

    def set_style(self, style) -> None:
        dark = getattr(style, "dark", True)
        error = QColor("#e05561" if dark else "#d1242f")
        warn = QColor("#e0a33e" if dark else "#b45309")
        info = QColor("#4cae7a" if dark else "#197a3e")
        debug = getattr(style, "guide", QColor("#7a7a7a"))
        self._levels = {
            "error": self._fmt(error, bold=True),
            "warn": self._fmt(warn, bold=True),
            "info": self._fmt(info, bold=True),
            "debug": self._fmt(debug),
        }
        self.f_ts = self._fmt(style.number)      # timestamps in the number color
        self.f_string = self._fmt(style.string)
        self.rehighlight()

    def highlightBlock(self, text: str) -> None:  # noqa: N802
        m = _LOG_TS.match(text)
        if m:
            self.setFormat(0, m.end(), self.f_ts)
        for rx, role in _LOG_LEVELS:
            for mm in rx.finditer(text):
                self.setFormat(mm.start(), mm.end() - mm.start(),
                               self._levels[role])
        for mm in _QUOTED.finditer(text):
            self.setFormat(mm.start(), mm.end() - mm.start(), self.f_string)


# -- Python syntax highlighter ------------------------------------------------

_PY_KEYWORDS = {
    "and", "as", "assert", "async", "await", "break", "class", "continue",
    "def", "del", "elif", "else", "except", "finally", "for", "from", "global",
    "if", "import", "in", "is", "lambda", "nonlocal", "not", "or", "pass",
    "raise", "return", "try", "while", "with", "yield", "match", "case",
}
_PY_LITERALS = {"True", "False", "None"}
_PY_STR_PREFIXES = {"r", "b", "f", "u", "rb", "br", "fr", "rf", "bf", "fb"}


class PythonHighlighter(QSyntaxHighlighter):
    """Highlights Python source: keywords, literals, strings (incl. triple-
    quoted spanning lines), comments, numbers and decorators."""

    NORMAL, TRIPLE_SINGLE, TRIPLE_DOUBLE = 0, 1, 2

    def __init__(self, document, style):
        super().__init__(document)
        self.set_style(style)

    @staticmethod
    def _fmt(color, bold=False) -> QTextCharFormat:
        f = QTextCharFormat()
        f.setForeground(color)
        if bold:
            f.setFontWeight(QFont.Weight.Bold)
        return f

    def set_style(self, style) -> None:
        self.f_keyword = self._fmt(style.boolean, bold=True)   # purple
        self.f_literal = self._fmt(style.null)                 # purple
        self.f_string = self._fmt(style.string)
        self.f_number = self._fmt(style.number)
        self.f_comment = self._fmt(style.guide)
        self.f_decorator = self._fmt(style.boolean)
        self.rehighlight()

    def highlightBlock(self, text: str) -> None:  # noqa: N802
        n = len(text)
        i = 0
        st = self.previousBlockState()
        if st in (self.TRIPLE_SINGLE, self.TRIPLE_DOUBLE):
            delim = "'''" if st == self.TRIPLE_SINGLE else '"""'
            end = text.find(delim)
            if end == -1:
                self.setFormat(0, n, self.f_string)
                self.setCurrentBlockState(st)
                return
            self.setFormat(0, end + 3, self.f_string)
            i = end + 3
        self.setCurrentBlockState(self.NORMAL)

        while i < n:
            c = text[i]
            if c == "#":
                self.setFormat(i, n - i, self.f_comment)
                return
            if text.startswith("'''", i) or text.startswith('"""', i):
                delim = text[i:i + 3]
                end = text.find(delim, i + 3)
                if end == -1:
                    self.setFormat(i, n - i, self.f_string)
                    self.setCurrentBlockState(
                        self.TRIPLE_SINGLE if delim == "'''"
                        else self.TRIPLE_DOUBLE)
                    return
                self.setFormat(i, end + 3 - i, self.f_string)
                i = end + 3
                continue
            if c in "\"'":
                j = self._string_end(text, i, c)
                self.setFormat(i, j - i, self.f_string)
                i = j
                continue
            if c == "@" and not text[:i].strip():
                j = i + 1
                while j < n and (text[j].isalnum() or text[j] in "_."):
                    j += 1
                self.setFormat(i, j - i, self.f_decorator)
                i = j
                continue
            if c.isdigit() or (c == "." and i + 1 < n and text[i + 1].isdigit()):
                j = self._number_end(text, i)
                self.setFormat(i, j - i, self.f_number)
                i = j
                continue
            if c.isalpha() or c == "_":
                j = i + 1
                while j < n and (text[j].isalnum() or text[j] == "_"):
                    j += 1
                word = text[i:j]
                # String prefix (r"", f'', rb"" …) directly before a quote.
                if (word.lower() in _PY_STR_PREFIXES
                        and j < n and text[j] in "\"'"):
                    k = self._string_end(text, j, text[j])
                    self.setFormat(i, k - i, self.f_string)
                    i = k
                    continue
                if word in _PY_KEYWORDS:
                    self.setFormat(i, j - i, self.f_keyword)
                elif word in _PY_LITERALS:
                    self.setFormat(i, j - i, self.f_literal)
                i = j
                continue
            i += 1

    @staticmethod
    def _string_end(text: str, i: int, quote: str) -> int:
        n = len(text)
        j = i + 1
        while j < n:
            if text[j] == "\\":
                j += 2
                continue
            if text[j] == quote:
                return j + 1
            j += 1
        return n

    @staticmethod
    def _number_end(text: str, i: int) -> int:
        n = len(text)
        j = i
        while j < n and (text[j].isalnum() or text[j] in "._"):
            j += 1
        return j
