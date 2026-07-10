"""A lightweight XML syntax highlighter for the XML source view.

Colors tags, attribute names, attribute values, comments, CDATA and PIs
using the active theme's colors (tag → placeholder, attr → key,
value/text → string, comment/PI → guide). Multi-line comments and CDATA
are tracked with block state; a rare open-tag spanning lines is highlighted
best-effort per line.
"""

from __future__ import annotations

import re

from PySide6.QtGui import QSyntaxHighlighter, QTextCharFormat

from openxmljson.styles import Style

_TAG_NAME = re.compile(r"</?\s*([\w:.\-]+)")
_ATTR = re.compile(r"([\w:.\-]+)\s*=\s*(\"[^\"]*\"|'[^']*')")


class XmlHighlighter(QSyntaxHighlighter):
    NORMAL, COMMENT, CDATA = 0, 1, 2

    def __init__(self, document, style: Style):
        super().__init__(document)
        self.set_style(style)

    @staticmethod
    def _fmt(color) -> QTextCharFormat:
        f = QTextCharFormat()
        f.setForeground(color)
        return f

    def set_style(self, style: Style) -> None:
        self.f_tag = self._fmt(style.placeholder)
        self.f_attr = self._fmt(style.key)
        self.f_val = self._fmt(style.string)
        self.f_text = self._fmt(style.text)
        self.f_comment = self._fmt(style.guide)
        self.f_punct = self._fmt(style.text)
        self.rehighlight()

    def highlightBlock(self, text: str) -> None:  # noqa: N802
        n = len(text)
        i = 0

        # Continue a comment / CDATA started on a previous line.
        st = self.previousBlockState()
        if st == self.COMMENT:
            end = text.find("-->")
            if end == -1:
                self.setFormat(0, n, self.f_comment)
                self.setCurrentBlockState(self.COMMENT)
                return
            self.setFormat(0, end + 3, self.f_comment)
            i = end + 3
        elif st == self.CDATA:
            end = text.find("]]>")
            if end == -1:
                self.setFormat(0, n, self.f_val)
                self.setCurrentBlockState(self.CDATA)
                return
            self.setFormat(0, end + 3, self.f_val)
            i = end + 3

        self.setCurrentBlockState(self.NORMAL)

        while i < n:
            lt = text.find("<", i)
            if lt == -1:
                self._text_run(i, n, text)
                return
            if lt > i:
                self._text_run(i, lt, text)
            rest = text[lt:]
            if rest.startswith("<!--"):
                end = text.find("-->", lt + 4)
                if end == -1:
                    self.setFormat(lt, n - lt, self.f_comment)
                    self.setCurrentBlockState(self.COMMENT)
                    return
                self.setFormat(lt, end + 3 - lt, self.f_comment)
                i = end + 3
            elif rest.startswith("<![CDATA["):
                end = text.find("]]>", lt + 9)
                if end == -1:
                    self.setFormat(lt, n - lt, self.f_val)
                    self.setCurrentBlockState(self.CDATA)
                    return
                self.setFormat(lt, end + 3 - lt, self.f_val)
                i = end + 3
            elif rest.startswith("<?") or rest.startswith("<!"):
                # PI or doctype/declaration.
                close = "?>" if rest.startswith("<?") else ">"
                end = text.find(close, lt + 2)
                stop = end + len(close) if end != -1 else n
                self.setFormat(lt, stop - lt, self.f_comment)
                i = stop
            else:
                gt = self._tag_end(text, lt)
                self._highlight_tag(text, lt, gt)
                i = gt

    # -- helpers --------------------------------------------------------------

    def _text_run(self, a: int, b: int, text: str) -> None:
        if text[a:b].strip():
            self.setFormat(a, b - a, self.f_text)

    @staticmethod
    def _tag_end(text: str, start: int) -> int:
        """Index just past the tag's '>', skipping '>' inside quotes."""
        n = len(text)
        i = start + 1
        quote = ""
        while i < n:
            c = text[i]
            if quote:
                if c == quote:
                    quote = ""
            elif c in "\"'":
                quote = c
            elif c == ">":
                return i + 1
            i += 1
        return n

    def _highlight_tag(self, text: str, start: int, end: int) -> None:
        seg = text[start:end]
        self.setFormat(start, end - start, self.f_punct)  # base: brackets/slashes
        m = _TAG_NAME.match(seg)
        if m:
            self.setFormat(start + m.start(1), m.end(1) - m.start(1), self.f_tag)
        for am in _ATTR.finditer(seg):
            self.setFormat(start + am.start(1), am.end(1) - am.start(1), self.f_attr)
            self.setFormat(start + am.start(2), am.end(2) - am.start(2), self.f_val)
