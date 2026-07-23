"""Crisp, theme-independent checkbox indicators for item views.

Fusion (used on macOS to avoid the native icon-engine crash) draws unchecked
check indicators very faintly, and overriding them with plain QSS boxes loses
the tick. Here we render three small indicator images once — an empty bordered
box, a box with a white tick, and a box with a dash (partial) — and expose a
QSS snippet that points a given selector's ``::indicator`` at them. The images
read well on both light and dark backgrounds (grey border, blue fill + white
mark when set).
"""

from __future__ import annotations

import os
import tempfile

_cache: dict = {}

_BORDER = "#8a8f98"
_FILL = "#3B82F6"


def _draw(kind: str, path: str) -> None:
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QPixmap

    px = 18                      # logical size
    scale = 2                    # render @2x for crisp retina
    pm = QPixmap(px * scale, px * scale)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.scale(scale, scale)

    radius = 4
    rect = (2, 2, px - 4, px - 4)   # x, y, w, h
    if kind == "off":
        p.setPen(QPen(QColor(_BORDER), 1.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(*rect, radius, radius)
    else:
        p.setPen(QPen(QColor(_FILL), 1.5))
        p.setBrush(QBrush(QColor(_FILL)))
        p.drawRoundedRect(*rect, radius, radius)
        p.setPen(QPen(QColor("#ffffff"), 2.0))
        if kind == "on":       # tick
            p.drawPolyline([QPointF(5, 9), QPointF(8, 12), QPointF(13, 5)])
        else:                  # dash (indeterminate)
            p.drawLine(QPointF(5, 9), QPointF(13, 9))
    p.end()
    pm.save(path, "PNG")


def _images() -> dict:
    if _cache:
        return _cache
    d = tempfile.mkdtemp(prefix="oxj_cb_")
    for kind in ("off", "on", "tri"):
        path = os.path.join(d, f"{kind}.png")
        try:
            _draw(kind, path)
            _cache[kind] = path.replace("\\", "/")   # QSS-friendly path
        except Exception:
            return {}   # fall back to native indicators on any failure
    return _cache


def indicator_qss(selector: str) -> str:
    """QSS pointing ``selector``'s check indicator at the rendered images.
    Returns '' if the images couldn't be created (native indicator is used)."""
    imgs = _images()
    if not imgs:
        return ""
    return (
        f"{selector}::indicator {{ width: 18px; height: 18px; }}"
        f"{selector}::indicator:unchecked {{ image: url(\"{imgs['off']}\"); }}"
        f"{selector}::indicator:checked {{ image: url(\"{imgs['on']}\"); }}"
        f"{selector}::indicator:indeterminate {{"
        f" image: url(\"{imgs['tri']}\"); }}"
    )
