"""Flow-diagram canvas: renders a ``diagram.Graph`` as titled node cards linked
by edges on a QGraphicsScene, with a floating control bar (fit, zoom, rotate
layout, export) and free pan/zoom — inspired by jsoncrack.com.

Cards have a shaded title bar and a body of ``key: value`` rows coloured by JSON
type; edges are plain smooth beziers. Layout direction can be rotated between
left→right, top→bottom, right→left and bottom→top. The diagram can be exported
to PNG / JPG / PDF. Adapts to the app's light/dark theme.

Kept separate from the Qt-free ``diagram`` builder so the graph logic stays
headlessly testable; this module is imported only when the GUI runs.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Dict, List, Tuple

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetricsF,
    QImage,
    QPainter,
    QPainterPath,
    QPdfWriter,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QGraphicsPathItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
    QHBoxLayout,
    QMenu,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .diagram import Graph

PAD_X = 14.0
BODY_PAD = 7.0
ROW_H = 26.0
TITLE_H = 28.0
GUTTER = 22.0
SWATCH = 16.0
H_GAP = 90.0
V_GAP = 30.0
RADIUS = 10.0
MIN_W = 150.0
MAX_W = 480.0
PAN_MARGIN = 2500.0   # scene padding so the canvas can be dragged far (free pan)

ORIENTS = ("LR", "TB", "RL", "BT")

_HEX_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


class _Canvas(QGraphicsView):
    """Scrollable, wheel-zoomable, hand-draggable view with a tiled dotted
    background and no visible scrollbars (drag to pan freely)."""

    def __init__(self, scene: QGraphicsScene, parent=None):
        super().__init__(scene, parent)
        self.owner = None          # set by DiagramView; used for smart-zoom fit
        self._smart_on = False
        self.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.set_bg(QColor("#0f0f12"), QColor("#23242c"))

    def set_bg(self, bg: QColor, dot: QColor) -> None:
        step = 24
        pm = QPixmap(step, step)
        pm.fill(bg)
        p = QPainter(pm)
        p.setPen(QPen(dot, 1.4))
        p.drawPoint(1, 1)
        p.end()
        self.setBackgroundBrush(QBrush(pm))

    def wheelEvent(self, event):  # noqa: N802
        # Ctrl + wheel zooms (for mouse users); plain two-finger scroll pans.
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
            self.scale(factor, factor)
            self._smart_on = False
            event.accept()
            return
        delta = event.pixelDelta()
        if delta.isNull():
            a = event.angleDelta()
            delta = QPoint(a.x() // 8, a.y() // 8)
        self.horizontalScrollBar().setValue(
            self.horizontalScrollBar().value() - delta.x())
        self.verticalScrollBar().setValue(
            self.verticalScrollBar().value() - delta.y())
        event.accept()

    def event(self, e):  # noqa: N802
        # macOS trackpad pinch / smart-zoom arrive as native gesture events.
        if e.type() == QEvent.Type.NativeGesture:
            gtype = e.gestureType()
            if gtype == Qt.NativeGestureType.ZoomNativeGesture:
                factor = 1.0 + e.value()   # spread > 0 (in), pinch < 0 (out)
                if factor > 0:
                    self.scale(factor, factor)
                    self._smart_on = False
                return True
            if gtype == Qt.NativeGestureType.SmartZoomNativeGesture:
                self._smart_zoom(e)
                return True
        return super().event(e)

    def _smart_zoom(self, e) -> None:
        if self._smart_on:
            if self.owner is not None:
                self.owner.fit()
            self._smart_on = False
        else:
            scene_pt = self.mapToScene(e.position().toPoint())
            self.scale(2.2, 2.2)
            self.centerOn(scene_pt)
            self._smart_on = True


class DiagramView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self._view = _Canvas(self._scene, self)
        self._view.owner = self
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._view)
        self._font = _mono_font(False)
        self._bold = _mono_font(True)
        self._fm = QFontMetricsF(self._font)
        self._fm_bold = QFontMetricsF(self._bold)
        self._c = _dark_palette()
        self._graph: Graph = None
        self._orient = "LR"
        self._toolbar = self._build_toolbar()

    # -- theming ---------------------------------------------------------------

    def apply_style(self, style) -> None:
        is_dark = True
        bg = getattr(style, "view_bg", None)
        if isinstance(bg, QColor):
            is_dark = bg.lightnessF() < 0.5
        self._c = _dark_palette() if is_dark else _light_palette()
        self._view.set_bg(self._c["canvas"], self._c["dot"])

    # -- public API ------------------------------------------------------------

    def set_graph(self, graph: Graph) -> None:
        self._graph = graph
        self._orient = "LR"
        self._render()

    def fit(self) -> None:
        rect = self._scene.itemsBoundingRect()
        vw = self._view.viewport().width()
        vh = self._view.viewport().height()
        if rect.isEmpty() or vw <= 1:
            return
        # Scale to fit, with a little padding so nothing touches the edges.
        pad = max(rect.width(), rect.height()) * 0.06
        padded = rect.adjusted(-pad, -pad, pad, pad)
        s = min(vw / padded.width(), vh / padded.height())
        self._view.resetTransform()
        self._view.scale(s, s)
        # Left-align with a 10% margin (the root card sits ~10% from the left),
        # vertically centered — rather than centering the whole graph.
        left_frac = 0.10
        cx = rect.left() + (0.5 * vw - left_frac * vw) / s
        cy = rect.center().y()
        self._view.centerOn(cx, cy)

    def zoom(self, factor: float) -> None:
        self._view.scale(factor, factor)

    def rotate_layout(self) -> None:
        """Cycle layout direction LR → TB → RL → BT (jsoncrack's rotate)."""
        self._orient = ORIENTS[(ORIENTS.index(self._orient) + 1) % len(ORIENTS)]
        self._render()

    # -- events ----------------------------------------------------------------

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        self._place_toolbar()
        if self._graph is not None and self._graph.nodes:
            QTimer.singleShot(0, self.fit)

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._place_toolbar()

    # -- rendering -------------------------------------------------------------

    def _render(self) -> None:
        self._scene.clear()
        self._view.set_bg(self._c["canvas"], self._c["dot"])
        graph = self._graph
        if graph is None or not graph.nodes:
            return
        sizes = {n.id: (self._node_width(n), self._node_height(n))
                 for n in graph.nodes}
        depths = self._depths(graph)
        positions = self._layout(graph, depths, sizes)

        node_by_id = {n.id: n for n in graph.nodes}
        for parent, child, _label, _row in graph.edges:
            self._draw_edge(positions[parent], sizes[parent],
                            positions[child], sizes[child])
        for node_id, (x, y) in positions.items():
            self._draw_card(node_by_id[node_id], x, y, sizes[node_id])

        content = self._scene.itemsBoundingRect()
        self._scene.setSceneRect(content.adjusted(
            -PAN_MARGIN, -PAN_MARGIN, PAN_MARGIN, PAN_MARGIN))
        QTimer.singleShot(0, self.fit)

    # -- geometry --------------------------------------------------------------

    def _node_width(self, node) -> float:
        has_link = any(f[3] for f in node.fields)
        gutter = GUTTER if has_link else 0.0
        widest = self._fm_bold.horizontalAdvance(node.title)
        for key, value, vtype, _is_link in node.fields:
            label = f"{key}: {value}" if key else value
            w = gutter + self._fm.horizontalAdvance(label)
            if vtype == "string" and _HEX_RE.match(value):
                w += SWATCH
            widest = max(widest, w)
        return min(MAX_W, max(MIN_W, PAD_X * 2 + widest))

    def _title_h(self, node) -> float:
        return TITLE_H if node.title else 0.0

    def _node_height(self, node) -> float:
        return self._title_h(node) + BODY_PAD * 2 + max(1, len(node.fields)) * ROW_H

    def _row_top(self, node_y: float, i: int, title_h: float) -> float:
        return node_y + title_h + BODY_PAD + i * ROW_H

    def _depths(self, graph: Graph) -> Dict[str, int]:
        depth = {graph.nodes[0].id: 0}
        children: Dict[str, List[str]] = {}
        for parent, child, _label, _row in graph.edges:
            children.setdefault(parent, []).append(child)
        queue = [graph.nodes[0].id]
        while queue:
            cur = queue.pop(0)
            for child in children.get(cur, []):
                if child not in depth:
                    depth[child] = depth[cur] + 1
                    queue.append(child)
        for n in graph.nodes:
            depth.setdefault(n.id, 0)
        return depth

    def _layout(self, graph, depths, sizes) -> Dict[str, Tuple[float, float]]:
        """Tidy-tree layout: position along the depth axis by level, and along
        the cross axis so each parent is centered on its children (like
        jsoncrack), instead of every column stacking from the top."""
        horizontal = self._orient in ("LR", "RL")

        def cross_ext(nid):
            return sizes[nid][1] if horizontal else sizes[nid][0]

        def depth_ext(nid):
            return sizes[nid][0] if horizontal else sizes[nid][1]

        depth_gap = H_GAP if horizontal else V_GAP
        cross_gap = V_GAP if horizontal else H_GAP

        children: Dict[str, List[str]] = {n.id: [] for n in graph.nodes}
        for parent, child, _label, _row in graph.edges:
            children[parent].append(child)

        # Depth-axis offsets (per level, by the largest node in that level).
        columns: Dict[int, List[str]] = {}
        for n in graph.nodes:
            columns.setdefault(depths[n.id], []).append(n.id)
        depth_size = {d: max(depth_ext(i) for i in ids)
                      for d, ids in columns.items()}
        depth_off = {}
        acc = 0.0
        for d in sorted(columns):
            depth_off[d] = acc
            acc += depth_size[d] + depth_gap
        total_depth = acc - depth_gap

        # Cross-axis centers: leaves take the next slot; a parent centers on the
        # span of its children. Iterative post-order avoids recursion limits.
        center: Dict[str, float] = {}
        cursor = 0.0
        root = graph.nodes[0].id
        stack = [(root, False)]
        while stack:
            nid, processed = stack.pop()
            if processed:
                kids = children[nid]
                if kids:
                    center[nid] = (center[kids[0]] + center[kids[-1]]) / 2
                else:
                    center[nid] = cursor + cross_ext(nid) / 2
                    cursor += cross_ext(nid) + cross_gap
            else:
                stack.append((nid, True))
                for k in reversed(children[nid]):
                    stack.append((k, False))
        for n in graph.nodes:  # safety for any node not reached from root
            if n.id not in center:
                center[n.id] = cursor + cross_ext(n.id) / 2
                cursor += cross_ext(n.id) + cross_gap

        positions = {}
        for n in graph.nodes:
            nid = n.id
            w, h = sizes[nid]
            d = depths[nid]
            cross_pos = center[nid] - cross_ext(nid) / 2
            if self._orient == "LR":
                x, y = depth_off[d], cross_pos
            elif self._orient == "RL":
                x, y = total_depth - depth_off[d] - w, cross_pos
            elif self._orient == "TB":
                x, y = cross_pos, depth_off[d]
            else:  # BT
                x, y = cross_pos, total_depth - depth_off[d] - h
            positions[nid] = (x, y)
        return positions

    # -- drawing ---------------------------------------------------------------

    def _draw_card(self, node, x, y, size) -> None:
        w, h = size
        c = self._c
        th = self._title_h(node)
        card_path = QPainterPath()
        card_path.addRoundedRect(QRectF(x, y, w, h), RADIUS, RADIUS)
        card = QGraphicsPathItem(card_path)
        # With a title, the card fill is the header shade and the body is
        # overlaid below the title bar; without one, the whole card is the
        # body shade (no header strip).
        card.setBrush(QBrush(c["header"] if th else c["card"]))
        card.setPen(QPen(c["border"], 1.2))
        self._scene.addItem(card)
        if th:
            body = QGraphicsPathItem(_body_path(x, y, w, h, RADIUS, th))
            body.setBrush(QBrush(c["card"]))
            body.setPen(QPen(Qt.PenStyle.NoPen))
            self._scene.addItem(body)
            self._text(node.title, c["title"], x + PAD_X,
                       y + (TITLE_H - self._fm_bold.height()) / 2, bold=True)

        has_link = any(f[3] for f in node.fields)
        gutter = GUTTER if has_link else 0.0
        for i, (key, value, vtype, is_link) in enumerate(node.fields):
            row_top = self._row_top(y, i, th)
            cx = x + PAD_X
            if is_link:
                self._draw_marker(cx, row_top)
            cx += gutter
            if key:
                self._text(f"{key}: ", c["key"], cx, row_top)
                cx += self._fm.horizontalAdvance(f"{key}: ")
            if vtype == "string" and _HEX_RE.match(value):
                self._draw_swatch(value, cx, row_top)
                cx += SWATCH
            self._text(value, self._value_color(vtype), cx, row_top)

    def _draw_marker(self, x: float, row_top: float) -> None:
        c = self._c
        size = 14.0
        top = row_top + (ROW_H - size) / 2
        path = QPainterPath()
        path.addRoundedRect(QRectF(x, top, size, size), 3, 3)
        box = QGraphicsPathItem(path)
        box.setBrush(QBrush(c["marker_bg"]))
        box.setPen(QPen(c["marker_border"], 1))
        self._scene.addItem(box)
        line = QGraphicsPathItem()
        lp = QPainterPath()
        lp.moveTo(x + 3, top + size / 2)
        lp.lineTo(x + size - 3, top + size / 2)
        line.setPath(lp)
        line.setPen(QPen(c["marker_glyph"], 1.4))
        self._scene.addItem(line)

    def _draw_swatch(self, hex_value: str, x: float, row_top: float) -> None:
        size = 11.0
        top = row_top + (ROW_H - size) / 2
        path = QPainterPath()
        path.addRoundedRect(QRectF(x, top, size, size), 2, 2)
        item = QGraphicsPathItem(path)
        item.setBrush(QBrush(QColor(hex_value)))
        item.setPen(QPen(self._c["border"], 1))
        self._scene.addItem(item)

    def _draw_edge(self, p_pos, p_size, c_pos, c_size) -> None:
        """Connect parent to child from the middle of the card's outgoing side
        to the middle of the child's incoming side."""
        horizontal = self._orient in ("LR", "RL")
        px, py = p_pos
        pw, ph = p_size
        cx, cy = c_pos
        cw, ch = c_size
        if self._orient == "LR":
            start = QPointF(px + pw, py + ph / 2)
            end = QPointF(cx, cy + ch / 2)
        elif self._orient == "RL":
            start = QPointF(px, py + ph / 2)
            end = QPointF(cx + cw, cy + ch / 2)
        elif self._orient == "TB":
            start = QPointF(px + pw / 2, py + ph)
            end = QPointF(cx + cw / 2, cy)
        else:  # BT
            start = QPointF(px + pw / 2, py)
            end = QPointF(cx + cw / 2, cy + ch)

        path = QPainterPath(start)
        if horizontal:
            mid = (start.x() + end.x()) / 2
            path.cubicTo(QPointF(mid, start.y()), QPointF(mid, end.y()), end)
        else:
            mid = (start.y() + end.y()) / 2
            path.cubicTo(QPointF(start.x(), mid), QPointF(end.x(), mid), end)
        item = QGraphicsPathItem(path)
        pen = QPen(self._c["edge"], 1.3)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        item.setPen(pen)
        self._scene.addItem(item)

    # -- export ----------------------------------------------------------------

    def export_image(self, fmt: str) -> None:
        rect = self._scene.itemsBoundingRect()
        if rect.isEmpty():
            return
        path, _ = QFileDialog.getSaveFileName(
            self, f"Export {fmt.upper()}", _default_name(fmt),
            f"{fmt.upper()} image (*.{fmt})")
        if not path:
            return
        rect = rect.adjusted(-24, -24, 24, 24)
        scale = 2.0
        w = max(1, int(rect.width() * scale))
        h = max(1, int(rect.height() * scale))
        img = QImage(w, h, QImage.Format.Format_ARGB32)
        img.fill(self._c["canvas"])
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        self._scene.render(p, QRectF(0, 0, w, h), rect)
        p.end()
        img.save(path, "JPG" if fmt.lower() in ("jpg", "jpeg") else "PNG")

    def export_pdf(self) -> None:
        rect = self._scene.itemsBoundingRect()
        if rect.isEmpty():
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export PDF", _default_name("pdf"), "PDF document (*.pdf)")
        if not path:
            return
        writer = QPdfWriter(path)
        writer.setResolution(150)
        p = QPainter(writer)
        self._scene.render(
            p, QRectF(0, 0, writer.width(), writer.height()),
            rect.adjusted(-24, -24, 24, 24), Qt.AspectRatioMode.KeepAspectRatio)
        p.end()

    # -- toolbar ---------------------------------------------------------------

    def _build_toolbar(self) -> QFrame:
        bar = QFrame(self)
        bar.setObjectName("diagramToolbar")
        bar.setStyleSheet(
            "#diagramToolbar { background: rgba(28,28,34,0.92);"
            " border: 1px solid rgba(255,255,255,0.10); border-radius: 12px; }"
            " QToolButton { background: transparent; border: none; color: #d5d5db;"
            " font-size: 16px; padding: 6px 9px; }"
            " QToolButton:hover { background: rgba(255,255,255,0.12);"
            " border-radius: 8px; }"
            " QToolButton::menu-indicator { image: none; }"
        )
        row = QHBoxLayout(bar)
        row.setContentsMargins(8, 4, 8, 4)
        row.setSpacing(2)

        def btn(glyph, tip, slot):
            b = QToolButton(bar)
            b.setText(glyph)
            b.setToolTip(tip)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(slot)
            row.addWidget(b)
            return b

        btn("◎", "Fit to view", self.fit)
        btn("−", "Zoom out", lambda: self.zoom(1 / 1.2))
        btn("+", "Zoom in", lambda: self.zoom(1.2))

        self._export_btn = QToolButton(bar)
        self._export_btn.setText("⇩")
        self._export_btn.setToolTip("Export image")
        self._export_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._export_btn.clicked.connect(self._show_export_menu)
        row.addWidget(self._export_btn)

        btn("⟳", "Rotate layout direction", self.rotate_layout)
        bar.adjustSize()
        return bar

    def _show_export_menu(self) -> None:
        menu = QMenu(self)
        menu.addAction("Export PNG…", lambda: self.export_image("png"))
        menu.addAction("Export JPG…", lambda: self.export_image("jpg"))
        menu.addAction("Export PDF…", self.export_pdf)
        size = menu.sizeHint()
        # Align the menu's right edge to the view's right wall (15px margin),
        # opening above the toolbar.
        right_edge = self.mapToGlobal(QPoint(self.width(), 0)).x()
        btn_top = self._export_btn.mapToGlobal(QPoint(0, 0)).y()
        x = right_edge - size.width() - 15
        y = btn_top - size.height() - 6
        menu.exec(QPoint(x, y))

    def _place_toolbar(self) -> None:
        if self._toolbar is None:
            return
        self._toolbar.adjustSize()
        tw = self._toolbar.width()
        th = self._toolbar.height()
        x = self.width() - tw - 16    # bottom-right corner
        y = self.height() - th - 16
        self._toolbar.move(max(0, x), max(0, y))
        self._toolbar.raise_()

    # -- helpers ---------------------------------------------------------------

    def _text(self, s: str, color: QColor, x: float, y: float, bold: bool = False):
        item = QGraphicsSimpleTextItem(s)
        item.setFont(self._bold if bold else self._font)
        item.setBrush(QBrush(color))
        if not bold:
            y = y + (ROW_H - self._fm.height()) / 2
        item.setPos(x, y)
        self._scene.addItem(item)
        return item

    def _value_color(self, vtype: str) -> QColor:
        c = self._c
        return {
            "number": c["num"], "boolean": c["bool"], "null": c["null"],
            "string": c["str"], "object": c["link"], "array": c["link"],
        }.get(vtype, c["str"])


# -- helpers / palettes / font -------------------------------------------------

def _default_name(ext: str) -> str:
    """Default export filename with a timestamp, e.g. diagram_20260712_143005.png."""
    return f"diagram_{datetime.now():%Y%m%d_%H%M%S}.{ext}"


def _body_path(x, y, w, h, r, title_h) -> QPainterPath:
    bx, bw = x + 1, w - 2
    top, bottom = y + title_h, y + h - 1
    p = QPainterPath()
    p.moveTo(bx, top)
    p.lineTo(bx + bw, top)
    p.lineTo(bx + bw, bottom - r)
    p.quadTo(bx + bw, bottom, bx + bw - r, bottom)
    p.lineTo(bx + r, bottom)
    p.quadTo(bx, bottom, bx, bottom - r)
    p.lineTo(bx, top)
    p.closeSubpath()
    return p


def _mono_font(bold: bool) -> QFont:
    f = QFont()
    f.setStyleHint(QFont.StyleHint.Monospace)
    f.setFamilies(["Menlo", "DejaVu Sans Mono", "Consolas", "monospace"])
    f.setPointSizeF(10.0)
    f.setBold(bold)
    return f


def _dark_palette() -> Dict[str, QColor]:
    return {
        "canvas": QColor("#0f0f12"), "dot": QColor("#23242c"),
        "card": QColor("#1b1b1f"), "header": QColor("#292933"),
        "title": QColor("#e7e7ee"), "border": QColor("#3a3a44"),
        "key": QColor("#6ca9f5"), "str": QColor("#d7d7db"),
        "num": QColor("#e0a54a"), "bool": QColor("#c586c0"),
        "null": QColor("#8a8a94"), "link": QColor("#9aa0aa"),
        "marker_bg": QColor("#2a2a31"), "marker_border": QColor("#4a4a54"),
        "marker_glyph": QColor("#b6b6c0"), "edge": QColor("#585863"),
    }


def _light_palette() -> Dict[str, QColor]:
    return {
        "canvas": QColor("#f6f7f9"), "dot": QColor("#e2e5ea"),
        "card": QColor("#ffffff"), "header": QColor("#eef1f6"),
        "title": QColor("#1f2430"), "border": QColor("#c7ccd6"),
        "key": QColor("#1a5fb4"), "str": QColor("#24292e"),
        "num": QColor("#b25000"), "bool": QColor("#8250df"),
        "null": QColor("#8a8f98"), "link": QColor("#55606e"),
        "marker_bg": QColor("#eef1f6"), "marker_border": QColor("#c2c8d2"),
        "marker_glyph": QColor("#55606e"), "edge": QColor("#aab0bb"),
    }
