"""Flow-diagram canvas: renders a ``diagram.Graph`` as node cards linked by
edges on a QGraphicsScene, laid out left-to-right by depth.

Kept separate from the Qt-free ``diagram`` builder so the graph logic stays
headlessly testable; this module is imported only when the GUI runs.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetricsF,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import (
    QGraphicsPathItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
    QVBoxLayout,
    QWidget,
)

from .diagram import Graph

CARD_W = 230.0     # fixed card width keeps columns from overlapping
PAD = 8.0
ROW_H = 18.0
TITLE_H = 24.0
H_GAP = 90.0       # horizontal gap between depth columns
V_GAP = 22.0       # vertical gap between cards in a column


class _Canvas(QGraphicsView):
    """Scrollable, wheel-zoomable graphics view."""

    def __init__(self, scene: QGraphicsScene, parent=None):
        super().__init__(scene, parent)
        self.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)

    def wheelEvent(self, event):  # noqa: N802
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.scale(factor, factor)


class DiagramView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self._view = _Canvas(self._scene, self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._view)
        self._colors = _default_colors()

    def set_colors(self, colors: dict) -> None:
        """Override the palette (wired to the app theme by the caller)."""
        self._colors.update(colors)

    # -- rendering -------------------------------------------------------------

    def set_graph(self, graph: Graph) -> None:
        self._scene.clear()
        if not graph.nodes:
            return
        depths = self._depths(graph)
        sizes = {n.id: (CARD_W, self._card_height(n)) for n in graph.nodes}
        positions = self._layout(graph, depths, sizes)

        # Edges first, so cards paint on top of the connectors.
        for parent, child in graph.edges:
            self._draw_edge(positions[parent], sizes[parent],
                            positions[child], sizes[child])
        node_by_id = {n.id: n for n in graph.nodes}
        for node_id, (x, y) in positions.items():
            self._draw_card(node_by_id[node_id], x, y, sizes[node_id])

        rect = self._scene.itemsBoundingRect().adjusted(-40, -40, 40, 40)
        self._scene.setSceneRect(rect)
        self.fit()

    def fit(self) -> None:
        rect = self._scene.itemsBoundingRect()
        if not rect.isEmpty():
            self._view.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)

    # -- layout ----------------------------------------------------------------

    def _depths(self, graph: Graph) -> Dict[str, int]:
        depth = {graph.nodes[0].id: 0}
        children: Dict[str, List[str]] = {}
        for parent, child in graph.edges:
            children.setdefault(parent, []).append(child)
        queue = [graph.nodes[0].id]
        while queue:
            cur = queue.pop(0)
            for child in children.get(cur, []):
                if child not in depth:
                    depth[child] = depth[cur] + 1
                    queue.append(child)
        for n in graph.nodes:  # disconnected safety
            depth.setdefault(n.id, 0)
        return depth

    def _layout(self, graph: Graph, depths: Dict[str, int],
                sizes: Dict[str, Tuple[float, float]]) -> Dict[str, Tuple[float, float]]:
        columns: Dict[int, List[str]] = {}
        for n in graph.nodes:  # preserve builder order within a column
            columns.setdefault(depths[n.id], []).append(n.id)
        positions: Dict[str, Tuple[float, float]] = {}
        for depth, ids in sorted(columns.items()):
            x = depth * (CARD_W + H_GAP)
            y = 0.0
            for node_id in ids:
                positions[node_id] = (x, y)
                y += sizes[node_id][1] + V_GAP
        return positions

    def _card_height(self, node) -> float:
        return TITLE_H + max(1, len(node.fields)) * ROW_H + PAD

    # -- drawing ---------------------------------------------------------------

    def _draw_card(self, node, x: float, y: float,
                   size: Tuple[float, float]) -> None:
        w, h = size
        c = self._colors
        rect = QGraphicsRectItem(x, y, w, h)
        rect.setBrush(QBrush(c["card_bg"]))
        rect.setPen(QPen(c["border"], 1))
        self._scene.addItem(rect)

        header = QGraphicsRectItem(x, y, w, TITLE_H)
        header.setBrush(QBrush(c["header_bg"]))
        header.setPen(QPen(Qt.PenStyle.NoPen))
        self._scene.addItem(header)

        title = QGraphicsSimpleTextItem(_elide(node.title, w - 2 * PAD, _bold()))
        title.setFont(_bold())
        title.setBrush(QBrush(c["title_fg"]))
        title.setPos(x + PAD, y + (TITLE_H - _line_h(_bold())) / 2)
        self._scene.addItem(title)

        font = _regular()
        row_y = y + TITLE_H + PAD / 2
        for key, value in node.fields:
            text = _elide(f"{key}: {value}", w - 2 * PAD, font)
            item = QGraphicsSimpleTextItem(text)
            item.setFont(font)
            item.setBrush(QBrush(c["field_fg"]))
            item.setPos(x + PAD, row_y)
            self._scene.addItem(item)
            row_y += ROW_H

    def _draw_edge(self, p_pos, p_size, c_pos, c_size) -> None:
        px, py = p_pos
        pw, ph = p_size
        cx, cy = c_pos
        cw, ch = c_size
        start = QPointF(px + pw, py + ph / 2)
        end = QPointF(cx, cy + ch / 2)
        path = QPainterPath(start)
        mid_x = (start.x() + end.x()) / 2
        path.cubicTo(QPointF(mid_x, start.y()), QPointF(mid_x, end.y()), end)
        item = QGraphicsPathItem(path)
        item.setPen(QPen(self._colors["edge"], 1.2))
        self._scene.addItem(item)


# -- helpers -------------------------------------------------------------------

def _regular() -> QFont:
    f = QFont()
    f.setPointSizeF(9.5)
    return f


def _bold() -> QFont:
    f = _regular()
    f.setBold(True)
    return f


def _line_h(font: QFont) -> float:
    return QFontMetricsF(font).height()


def _elide(text: str, max_w: float, font: QFont) -> str:
    fm = QFontMetricsF(font)
    if fm.horizontalAdvance(text) <= max_w:
        return text
    ellipsis = "…"
    while text and fm.horizontalAdvance(text + ellipsis) > max_w:
        text = text[:-1]
    return text + ellipsis


def _default_colors() -> Dict[str, QColor]:
    return {
        "card_bg": QColor("#ffffff"),
        "header_bg": QColor("#eef1f6"),
        "border": QColor("#c7ccd6"),
        "title_fg": QColor("#1f2430"),
        "field_fg": QColor("#3a4150"),
        "edge": QColor("#9aa2b1"),
    }
