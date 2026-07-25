"""A floating "back to top" button for any scrollable view.

Mirrors the tree view's button: appears bottom-right once the view is
scrolled down, scrolls back to the top on click, and follows the theme.
Attach with ``BackToTopButton(scroll_area, style)`` — it wires itself to the
area's vertical scrollbar and repositions itself on resize.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QPushButton


class BackToTopButton(QPushButton):
    def __init__(self, area, style=None):
        super().__init__("↑", area)
        self._area = area
        self._style = style
        self.setFixedSize(40, 40)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Back to top")
        self._apply_css()
        self.hide()
        self.clicked.connect(
            lambda: area.verticalScrollBar().setValue(0))
        area.verticalScrollBar().valueChanged.connect(self._on_scroll)
        area.installEventFilter(self)   # reposition on the area's resizes

    # -- theming ---------------------------------------------------------------
    def set_style(self, style) -> None:
        self._style = style
        self._apply_css()

    def _apply_css(self) -> None:
        bg = (self._style.placeholder.name()
              if self._style is not None else "#62b2bc")
        self.setStyleSheet(
            "QPushButton {"
            f" background: {bg};"
            " color: #ffffff; border: none; border-radius: 20px;"
            " font-size: 16px; font-weight: bold; }"
            "QPushButton:hover { opacity: 0.8; }"
        )

    # -- show/hide/position ------------------------------------------------------
    def _threshold(self) -> int:
        # Scrollbar units differ per view (pixels, lines, rows) — "more than
        # ~1.5 screens down" adapts to all of them.
        page = self._area.verticalScrollBar().pageStep()
        return max(1, int(page * 1.5))

    def _position(self) -> None:
        self.move(self._area.width() - self.width() - 24,
                  self._area.height() - self.height() - 24)

    def _on_scroll(self, value: int) -> None:
        if value > self._threshold() and not self.isVisible():
            self._position()
            self.show()
            self.raise_()
        elif value <= self._threshold() and self.isVisible():
            self.hide()

    def eventFilter(self, obj, event):  # noqa: N802
        if obj is self._area and event.type() == QEvent.Type.Resize:
            if self.isVisible():
                self._position()
        return False
