"""Status-bar activity lights: four LEDs that chase in blue while an
operation is running and settle to the theme color when idle.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QWidget

BLINK = QColor("#3b9dff")  # bright blue while busy


class ActivityLights(QWidget):
    COUNT = 4
    DIAMETER = 10
    GAP = 6

    def __init__(self, style, parent=None):
        super().__init__(parent)
        self._style = style
        self._depth = 0          # nesting count of active operations
        self._phase = 0          # which light is lit in the chase
        self.setFixedSize(
            self.COUNT * self.DIAMETER + (self.COUNT - 1) * self.GAP + 8, 16
        )
        self._chase = QTimer(self)
        self._chase.setInterval(160)  # chase cadence
        self._chase.timeout.connect(self._advance)
        # After the last operation ends, keep blinking briefly so even a
        # fast op is visible, then settle.
        self._settle = QTimer(self)
        self._settle.setSingleShot(True)
        self._settle.timeout.connect(self._stop)
        self.setToolTip("Activity")

    def set_style(self, style) -> None:
        self._style = style
        self.update()

    # -- activity control ----------------------------------------------------

    def begin(self) -> None:
        self._depth += 1
        self._settle.stop()
        if not self._chase.isActive():
            self._phase = 0
            self._chase.start()
        self.repaint()  # show blue immediately, before any blocking work

    def end(self) -> None:
        self._depth = max(0, self._depth - 1)
        if self._depth == 0:
            self._settle.start(500)

    def pulse(self) -> None:
        """A momentary blink for a one-shot event (e.g. a tail append)."""
        self.begin()
        self.end()

    def _advance(self) -> None:
        self._phase = (self._phase + 1) % self.COUNT  # light each in turn
        self.update()

    def _stop(self) -> None:
        self._chase.stop()
        self.update()

    def _busy(self) -> bool:
        return self._chase.isActive()

    # -- painting -------------------------------------------------------------

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        idle = self._style.guide
        dim = QColor(idle)
        dim.setAlpha(90)
        y = (self.height() - self.DIAMETER) / 2.0
        busy = self._busy()
        for i in range(self.COUNT):
            x = 4 + i * (self.DIAMETER + self.GAP)
            if busy:
                color = BLINK if i == self._phase else dim  # one at a time
            else:
                color = idle
            painter.setBrush(color)
            painter.drawEllipse(int(x), int(y), self.DIAMETER, self.DIAMETER)
        painter.end()
