"""The document tree view: colored segment rendering, classic ⊞/⊟
collapsers with guide lines, zebra striping (via the stylesheet), the
back-to-top button, and format-aware node context menus.
"""

from __future__ import annotations

import json

from PySide6.QtCore import QPoint, QPointF, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import (
    QAction,
    QFontMetricsF,
    QGuiApplication,
    QPainter,
    QPen,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QMenu,
    QMessageBox,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTreeView,
)

from openxmljson.model import SegmentsRole
from openxmljson.styles import Style, role_color

ROW_PAD = 1.35  # comfortable row height

#: "Expand All" node-count tiers, based on the document's total node count
#: (expanding lays out a tree item per node, so cost scales with nodes, not
#: file size). Below CONFIRM: expand immediately. CONFIRM..MAX: confirm first.
#: Above MAX: not offered at all (would make the UI unresponsive too long).
EXPAND_ALL_CONFIRM_NODES = 500_000
EXPAND_ALL_MAX_NODES = 1_000_000

#: Cap on how many characters of a single segment we draw/measure. Long
#: values are reachable by horizontal scroll up to this length; beyond it the
#: text is truncated with an ellipsis. Kept modest because drawing/measuring
#: text goes through CoreText glyph shaping (O(chars)) — a huge value on many
#: rows can otherwise freeze the UI.
MAX_SEGMENT_CHARS = 2_000


class SegmentDelegate(QStyledItemDelegate):
    """Paints rows from their typed segments with theme colors. Each row is a
    single fixed-height line; long values are reachable by horizontal scroll
    (the view sizes column 0 to its widest visible content)."""

    def __init__(self, view: QTreeView, style: Style):
        super().__init__(view)
        self._view = view
        self._style = style
        #: Compiled Python regex mirroring the engine search; used to draw
        #: chips behind the matched substrings only.
        self.search_re = None

    def set_search(self, compiled_or_none) -> None:
        self.search_re = compiled_or_none

    def set_style(self, style: Style) -> None:
        self._style = style

    def paint(self, painter: QPainter, option, index) -> None:  # noqa: N802
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        s = self._style
        painter.save()

        model = index.model()
        # Resolve through a filter proxy so chips and collapsed-count
        # suffixes keep working in filtered view.
        src_model, src_index = model, index
        if hasattr(model, "mapToSource"):
            src_index = model.mapToSource(index)
            src_model = model.sourceModel()
        node = (
            src_model.node_id(src_index)
            if hasattr(src_model, "node_id")
            else None
        )
        is_match = (
            node is not None
            and hasattr(src_model, "is_match")
            and src_model.is_match(node)
        )

        # Background priority: selection (current match) > zebra stripe.
        # Other matches get substring chips, not a full-row band.
        if opt.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(opt.rect, s.selection_bg)
        else:
            if opt.features & QStyleOptionViewItem.ViewItemFeature.Alternate:
                painter.fillRect(opt.rect, s.view_alt_bg)
            else:
                painter.fillRect(opt.rect, s.view_bg)
            if is_match and self.search_re is None:
                # Fallback tint when the pattern can't be mirrored in Python.
                painter.fillRect(opt.rect, s.match_bg)

        segments = index.data(SegmentsRole) or [
            ("punct", str(index.data(Qt.ItemDataRole.DisplayRole) or ""))
        ]
        # Collapsed containers show their size ("{...}, 63 keys"); expanded
        # ones drop the placeholder down to the opening bracket, like a
        # code editor ("children : [").
        if hasattr(src_model, "collapsed_suffix") and model.hasChildren(index):
            if not self._view.isExpanded(index):
                suffix = src_model.collapsed_suffix(src_index)
                if suffix:
                    segments = list(segments) + [
                        ("punct", ", "),
                        ("count", suffix),
                    ]
            else:
                open_bracket = {"{...}": "{", "[...]": "["}
                segments = [
                    (role, open_bracket.get(text, text))
                    if role == "placeholder"
                    else (role, text)
                    for role, text in segments
                ]

        fm = QFontMetricsF(opt.font)
        left = float(opt.rect.x()) + 4.0
        painter.setFont(opt.font)

        # One line of sequential colored segments. The row is sized to its
        # content (see sizeHint) and the view offers horizontal scroll, so
        # long values are never wrapped or elided — they extend rightward.
        from PySide6.QtGui import QFont

        # Counts read in color, not weight — bold made them shout.
        bold = QFont(opt.font)
        bold_fm = QFontMetricsF(bold)
        x = left
        rect = QRectF(opt.rect)
        for role, text in segments:
            if len(text) > MAX_SEGMENT_CHARS:
                text = text[:MAX_SEGMENT_CHARS] + "…"
            if role == "count":
                painter.setFont(bold)
                seg_fm = bold_fm
            else:
                painter.setFont(opt.font)
                seg_fm = fm
            width = seg_fm.horizontalAdvance(text)
            # Chips behind the matched substrings only (like the reference
            # viewer), keeping the syntax color on top.
            if is_match and self.search_re is not None and role != "count":
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(s.match_bg)
                for m in self.search_re.finditer(text):
                    if m.start() == m.end():
                        continue
                    x0 = x + seg_fm.horizontalAdvance(text[: m.start()])
                    x1 = x + seg_fm.horizontalAdvance(text[: m.end()])
                    painter.drawRoundedRect(
                        QRectF(
                            x0,
                            rect.y() + 1.0,
                            x1 - x0 + 1.0,
                            rect.height() - 2.0,
                        ),
                        3.0,
                        3.0,
                    )
            painter.setPen(QPen(role_color(s, role)))
            painter.drawText(
                QRectF(x, rect.y(), width + 2.0, rect.height()),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                text,
            )
            x += width
        painter.restore()

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        # Fixed height (keeps uniformRowHeights fast) + an *estimated* content
        # width for horizontal scroll. Width is estimated from the character
        # count times a single glyph's advance — NOT by measuring the actual
        # text, because QFontMetrics.horizontalAdvance shapes every glyph
        # through CoreText (O(chars)) and doing that per row freezes the UI on
        # big files. (The default font is monospace, so this is close; for
        # proportional fonts it is a good-enough scroll extent.)
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        fm = QFontMetricsF(opt.font)
        char_w = fm.horizontalAdvance("0") or (fm.height() * 0.6)
        segments = index.data(SegmentsRole) or []
        chars = 0
        for _role, text in segments:
            chars += min(len(text), MAX_SEGMENT_CHARS)
        return QSize(int(chars * char_w) + 12, int(fm.height() * ROW_PAD))


class DocumentTreeView(QTreeView):
    """Classic tree rendering + back-to-top button + context menu."""

    BOX = 9  # collapser box size (px)

    def __init__(self, style: Style, parent=None):
        super().__init__(parent)
        self._style = style
        # Fixed-height rows: with uniformRowHeights Qt asks for ONE size hint
        # and lays out multi-million-row documents instantly (requirement N4).
        # Long values are never wrapped/elided — the column is sized to its
        # content and reached by horizontal scroll.
        self.setUniformRowHeights(True)
        self.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.setHeaderHidden(True)
        self.setAlternatingRowColors(True)  # zebra stripes
        self.setIndentation(20)
        self.setRootIsDecorated(True)
        # Horizontal scroll for long values: let the single column exceed the
        # viewport width and scroll smoothly by pixel.
        self.header().setStretchLastSection(False)
        self.setHorizontalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerPixel
        )
        self._delegate = SegmentDelegate(self, style)
        self.setItemDelegate(self._delegate)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)
        # Re-fit column 0 to content width when the visible/expanded set
        # changes, so the horizontal scroll range matches the longest row.
        # COALESCED: Expand/Collapse All emit expanded/collapsed per node
        # (tens of thousands of times) — without coalescing that would queue
        # an autosize per node and stall. A pending flag collapses them into
        # a single pass after the event loop settles.
        self._autosize_pending = False
        self._autosize_reset = False
        self.expanded.connect(self._on_expand_collapse)
        self.collapsed.connect(self._on_expand_collapse)
        #: Set by the app: called with +1 / -1 on Ctrl+wheel (zoom).
        self.zoom_callback = None
        #: Whether the current doc is eligible for a full expand (opened
        #: eagerly), and its total node count — together they drive the
        #: immediate / confirm / disabled tiers for "Expand All".
        self._allow_expand_all = False
        self._expand_nodes = 0
        # Back-to-top button, shown once scrolled down.
        self._fab = QPushButton("↑", self)
        self._fab.setFixedSize(40, 40)
        self._fab.setCursor(Qt.CursorShape.PointingHandCursor)
        self._fab.setToolTip("Back to top")
        self._fab.setStyleSheet(self._fab_stylesheet())
        self._fab.hide()
        self._fab.clicked.connect(lambda: self.verticalScrollBar().setValue(0))
        self.verticalScrollBar().valueChanged.connect(self._on_scroll)

    def wheelEvent(self, event) -> None:  # noqa: N802
        if (
            self.zoom_callback is not None
            and event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            self.zoom_callback(1 if event.angleDelta().y() > 0 else -1)
            event.accept()
            return
        super().wheelEvent(event)

    def set_style(self, style: Style) -> None:
        self._style = style
        self._delegate.set_style(style)
        self._fab.setStyleSheet(self._fab_stylesheet())
        self.viewport().update()

    def set_search(self, compiled_or_none) -> None:
        """Pattern used to draw chips behind matched substrings."""
        self._delegate.set_search(compiled_or_none)
        self.viewport().update()

    def set_expand_all(self, eager: bool, node_count: int = 0) -> None:
        """Record whether the doc can be fully expanded (opened eagerly) and
        its total node count, which decide the 'Expand All' tier."""
        self._allow_expand_all = bool(eager)
        self._expand_nodes = int(node_count or 0)

    def _expand_all_at(self, index) -> None:
        """Recursively expand ``index``'s subtree, confirming first when the
        document is node-dense (see the EXPAND_ALL_* tiers)."""
        if self._expand_nodes > EXPAND_ALL_CONFIRM_NODES:
            if QMessageBox.question(
                self,
                "Expand All",
                f"This document has {self._expand_nodes:,} nodes. Expanding "
                "may make the app unresponsive for a few seconds.\n\nContinue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            ) != QMessageBox.StandardButton.Yes:
                return
        self.expandRecursively(index)

    def setModel(self, model) -> None:  # noqa: N802
        super().setModel(model)
        self._schedule_autosize(reset=True)

    def _on_expand_collapse(self, *_args) -> None:
        # A structural change to what's shown: recompute the column width to
        # fit the currently-visible rows, allowing it to SHRINK. Collapsing a
        # node can remove the widest row, so the grow-only autosize used while
        # scrolling would otherwise leave the column (and its horizontal
        # scrollbar) stuck at the old, wider size.
        self._schedule_autosize(reset=True)

    def _schedule_autosize(self, *_args, reset: bool = False) -> None:
        # Defer so the view has finished (re)laying out the affected rows,
        # and coalesce a burst of expand/collapse signals into ONE pass.
        self._autosize_reset = self._autosize_reset or reset
        if self._autosize_pending:
            return
        self._autosize_pending = True
        QTimer.singleShot(0, self._run_autosize)

    def _run_autosize(self) -> None:
        self._autosize_pending = False
        reset = self._autosize_reset
        self._autosize_reset = False
        self._autosize_column(reset)

    def _autosize_column(self, reset: bool = False) -> None:
        """Size column 0 for horizontal scroll by measuring ONLY the rows
        currently in the viewport (~a screenful), so it stays O(visible) and
        never scans the whole document.

        If the widest visible row fits the viewport, the column is pinned to
        the viewport width so no horizontal scrollbar is shown — even if the
        column had previously grown wider (a collapsed wide row, or the
        vertical scrollbar narrowing the viewport, would otherwise leave a
        phantom scrollbar over short rows). If a visible row genuinely exceeds
        the viewport, the column grows to fit it and stays grown while
        scrolling (``reset`` lets an explicit relayout re-fit exactly), so
        there is no width jitter as rows pass through the viewport."""
        if self.model() is None:
            return
        vpw = max(self.viewport().width(), 1)
        opt = QStyleOptionViewItem()
        vp_h = self.viewport().height()
        widest = 0
        idx = self.indexAt(QPoint(2, 2))
        guard = 0
        while idx.isValid() and guard < 400:
            rect = self.visualRect(idx)
            if not rect.isValid() or rect.top() > vp_h:
                break
            right = rect.left() + int(self._delegate.sizeHint(opt, idx).width())
            if right > widest:
                widest = right
            idx = self.indexBelow(idx)
            guard += 1
        content = widest + 8
        if content <= vpw:
            target = vpw                       # everything fits -> no scrollbar
        elif reset:
            target = content                   # explicit relayout -> exact fit
        else:
            target = max(content, self.columnWidth(0))  # grow-only while scrolling
        self.setColumnWidth(0, target)

    # -- classic branches: guide lines + ⊞ / ⊟ boxes ------------------------

    def drawBranches(self, painter: QPainter, rect, index) -> None:  # noqa: N802
        s = self._style
        indent = self.indentation()
        levels = max(rect.width() // indent, 0)
        if levels == 0:
            return
        painter.save()
        pen = QPen(s.guide)
        pen.setWidth(1)
        painter.setPen(pen)

        cell_left = rect.x() + (levels - 1) * indent
        cx = cell_left + indent / 2.0
        cy = rect.y() + rect.height() / 2.0

        # Vertical guides for ancestor levels.
        for level in range(levels - 1):
            gx = rect.x() + level * indent + indent / 2.0
            painter.drawLine(QPointF(gx, rect.y()), QPointF(gx, rect.y() + rect.height()))

        model = self.model()
        expandable = model is not None and model.hasChildren(index)
        half = self.BOX / 2.0

        # Horizontal stub from the collapser cell toward the row.
        painter.drawLine(
            QPointF(cx + (half if expandable else 0.0), cy),
            QPointF(cell_left + indent, cy),
        )

        if expandable:
            # ⊞ / ⊟ box — drawn in the theme text color so it stands out
            # clearly against the stripes (the guide gray was too faint).
            strong = QPen(s.text)
            strong.setWidth(1)
            painter.setPen(strong)
            box = QRectF(cx - half, cy - half, self.BOX, self.BOX)
            painter.fillRect(box, s.view_bg)
            painter.drawRect(box)
            painter.drawLine(
                QPointF(cx - half + 2.0, cy), QPointF(cx + half - 2.0, cy)
            )
            if not self.isExpanded(index):
                painter.drawLine(
                    QPointF(cx, cy - half + 2.0), QPointF(cx, cy + half - 2.0)
                )
        painter.restore()

    # -- back to top -----------------------------------------------------------

    def _fab_stylesheet(self) -> str:
        s = self._style
        return (
            "QPushButton {"
            f" background: {s.placeholder.name()};"
            f" color: {s.view_bg.name()};"
            " border: none; border-radius: 20px;"
            " font-size: 16px; font-weight: bold; }"
            "QPushButton:hover { opacity: 0.8; }"
        )

    def _position_fab(self) -> None:
        self._fab.move(
            self.width() - self._fab.width() - 24,
            self.height() - self._fab.height() - 24,
        )

    def _on_scroll(self, value: int) -> None:
        if value > 300 and not self._fab.isVisible():
            self._position_fab()
            self._fab.show()
            self._fab.raise_()
        elif value <= 300 and self._fab.isVisible():
            self._fab.hide()
        # Grow the column to fit rows scrolled into view (O(visible), cheap).
        self._autosize_column()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._fab.isVisible():
            self._position_fab()
        # Keep the column at least as wide as the viewport (so short content
        # still fills the row) after a resize.
        self._schedule_autosize()

    # -- context menu ------------------------------------------------------------

    def _show_context_menu(self, pos) -> None:
        """Context menu tailored to the document format and node kind:
        JSON members, XML elements/attributes, CSV rows/cells each get
        their own actions."""
        index = self.indexAt(pos)
        model = self.model()
        if model is None or not index.isValid():
            return
        # A filter proxy may be installed — work on the source model.
        if hasattr(model, "mapToSource"):
            index = model.mapToSource(index)
            model = model.sourceModel()
        clipboard = QGuiApplication.clipboard()
        node = model.node_id(index)
        kind = model.node_kind(index)
        fmt = model.format()
        menu = QMenu(self)

        def add(menu_, label, fn):
            action = QAction(label, menu_)
            action.triggered.connect(fn)
            menu_.addAction(action)
            return action

        def put(text_fn):
            return lambda: clipboard.setText(text_fn())

        ELEMENT, ATTR, TEXTK, CDATAK = 8, 12, 11, 13
        OBJ, ARR, KEYK = 1, 2, 3

        if fmt == "XML":
            if kind == ELEMENT:
                add(menu, "Copy Tag Name", put(lambda: model.name_text(index)))
                add(menu, "Copy XML", put(lambda: model.xml_text(node)))
                add(
                    menu,
                    "Copy Text Content",
                    put(lambda: model.element_text_content(node)),
                )
                add(
                    menu,
                    "Copy Attributes (JSON)",
                    put(
                        lambda: json.dumps(
                            model.reconstruct(node).get("attributes", {}),
                            indent=2,
                            ensure_ascii=False,
                        )
                    ),
                )
            elif kind == ATTR:
                add(menu, "Copy Attribute Name", put(lambda: model.name_text(index)))
                add(menu, "Copy Attribute Value", put(lambda: model.value_text(index)))
                add(menu, "Copy As name=\"value\"", put(lambda: model.xml_text(node)))
            elif kind in (TEXTK, CDATAK):
                add(menu, "Copy Text", put(lambda: model.value_text(index)))
            copy_as = QMenu("Copy Value As", menu)
            if kind == ELEMENT:
                add(copy_as, "XML", put(lambda: model.xml_text(node)))
            add(copy_as, "Pretty JSON", lambda: clipboard.setText(self._as_json(index)))
            add(copy_as, "Display Text", put(lambda: str(index.data() or "")))
            menu.addMenu(copy_as)
            menu.addSeparator()
            add(menu, "Copy XPath", put(lambda: model.path_text(index)))
        elif fmt in ("CSV", "TSV"):
            is_record = kind in (OBJ, ARR)
            if is_record:
                add(menu, f"Copy Row ({fmt})", put(lambda: model.csv_row_text(node)))
                add(menu, "Copy Row As JSON", lambda: clipboard.setText(self._as_json(index)))
            elif kind == KEYK:
                add(menu, "Copy Column Name", put(lambda: model.name_text(index)))
                add(menu, "Copy Cell Value", put(lambda: model.value_text(index)))
            else:
                add(menu, "Copy Cell Value", put(lambda: model.value_text(index)))
            copy_as = QMenu("Copy Value As", menu)
            add(copy_as, "Pretty JSON", lambda: clipboard.setText(self._as_json(index)))
            add(copy_as, "Raw Token", put(lambda: model.raw_token(index)))
            add(copy_as, "Display Text", put(lambda: str(index.data() or "")))
            menu.addMenu(copy_as)
            menu.addSeparator()
            add(menu, "Copy Path", put(lambda: model.path_text(index)))
        else:  # JSON
            add(menu, "Copy Name", put(lambda: model.name_text(index)))
            add(menu, "Copy Value", put(lambda: model.value_text(index)))
            copy_as = QMenu("Copy Value As", menu)
            add(copy_as, "Pretty JSON", lambda: clipboard.setText(self._as_json(index)))
            add(copy_as, "Raw Token", put(lambda: model.raw_token(index)))
            add(copy_as, "Display Text", put(lambda: str(index.data() or "")))
            menu.addMenu(copy_as)
            menu.addSeparator()
            add(menu, "Copy Path", put(lambda: model.path_text(index)))

        menu.addSeparator()
        export_as = QMenu("Export Value As", menu)
        if fmt == "XML" and kind == ELEMENT:
            add(export_as, "XML…", lambda: self._export(index, mode="xml"))
        if fmt in ("CSV", "TSV") and kind in (OBJ, ARR):
            add(export_as, f"{fmt} Row…", lambda: self._export(index, mode="csv"))
        add(export_as, "JSON…", lambda: self._export(index, mode="json"))
        add(export_as, "Text…", lambda: self._export(index, mode="text"))
        menu.addMenu(export_as)

        if model.hasChildren(index):
            menu.addSeparator()
            add(menu, "Statistics…", lambda: self._show_statistics(index, model))

        menu.addSeparator()
        view_index = self.indexAt(pos)  # expansion uses the view's model
        # One level only: reveal the node's immediate children (collapsed).
        # Cost is bounded by the direct-child count, not the whole subtree —
        # unlike a recursive expand, which can blow up on a large document.
        add(
            menu,
            "Expand Children",
            lambda: self.expand(view_index),
        )
        # Recursive whole-subtree expand — offered for an eagerly-opened JSON
        # doc up to EXPAND_ALL_MAX_NODES (above that a full expand would freeze
        # the UI too long). Between CONFIRM and MAX nodes it asks first.
        if (
            self._allow_expand_all
            and fmt == "JSON"
            and model.hasChildren(index)
            and self._expand_nodes <= EXPAND_ALL_MAX_NODES
        ):
            add(menu, "Expand All", lambda: self._expand_all_at(view_index))
        add(menu, "Collapse", lambda: self.collapse(view_index))

        menu.exec(self.viewport().mapToGlobal(pos))

    def _show_statistics(self, index, model) -> None:
        stats = model.statistics(index)
        title = model.name_text(index) or "Node"
        QMessageBox.information(self, f"Statistics — {title}", stats)

    def _source_model(self, index):
        """(source_index, DocumentModel) regardless of an active proxy."""
        model = self.model()
        if hasattr(model, "mapToSource"):
            if index.isValid() and index.model() is model:
                index = model.mapToSource(index)
            model = model.sourceModel()
        return index, model

    def _as_json(self, index) -> str:
        """Pretty JSON of the node — Key/Attribute rows include their
        name, e.g. {"price": 12.5}."""
        index, model = self._source_model(index)
        try:
            return json.dumps(
                model.reconstruct_named(model.node_id(index)),
                indent=2,
                ensure_ascii=False,
            )
        except (RecursionError, MemoryError) as exc:
            return f"<value too large to serialize: {exc}>"

    def _export(self, index, mode: str = "json", as_json=None) -> None:
        if as_json is not None:  # backward-compatible boolean form
            mode = "json" if as_json else "text"
        index, model = self._source_model(index)
        node = model.node_id(index)
        suffix = {"json": "json", "xml": "xml", "csv": "csv", "text": "txt"}[mode]
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export value",
            f"value.{suffix}",
            f"{suffix.upper()} files (*.{suffix});;All files (*)",
        )
        if not path:
            return
        try:
            if mode == "json":
                content = self._as_json(index)
            elif mode == "xml":
                content = model.xml_text(node)
            elif mode == "csv":
                content = model.csv_row_text(node)
            else:
                content = model.value_text(index)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(content)
        except OSError as exc:
            QMessageBox.critical(self, "Export failed", str(exc))
