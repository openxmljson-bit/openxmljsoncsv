"""A lightweight read-only code view: a QPlainTextEdit with a line-number
gutter, plus a JavaScript syntax highlighter. Used by the plain-text (.txt/.js)
tabs. Folding is intentionally not implemented (QPlainTextEdit has no native
folding); this stays Essentials-only with no heavy editor dependency.
"""

from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import (
    QColor,
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
