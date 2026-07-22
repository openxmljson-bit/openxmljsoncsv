"""The CSV/TSV table widget: a toolbar over a QTableView, plus a collapsible
left panel of column checkboxes.

Kept separate from ``csvtable`` (which is QtCore-only, so the model/proxy stay
headlessly importable for tests) because this module pulls in QtWidgets/QtGui.

Features: open-in-new-tab, a collapsible left Columns panel (search + checkboxes
+ show/hide all), clear filters, export filtered table to a new tab (CSV/JSON),
movable columns, numeric-aware sorting, pin-to-left, per-column filter dialogs,
and cell copy (Ctrl+C).
"""

from __future__ import annotations

from typing import Dict, List

from PySide6.QtCore import QEvent, QModelIndex, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QApplication,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from openxmljson.csvtable import FILTER_OPS, RecordFilterProxy, RecordTableModel


class _CsvTable(QTableView):
    """QTableView that drops its cell selection when it loses focus, so the
    blue highlight doesn't linger after you click elsewhere. Popups (context
    menu) keep the selection."""

    def focusOutEvent(self, event):  # noqa: N802
        if event.reason() != Qt.FocusReason.PopupFocusReason:
            self.clearSelection()
            self.setCurrentIndex(QModelIndex())
        super().focusOutEvent(event)


class _ColumnFilterDialog(QDialog):
    """Pick a column, operator and value. ``columns`` is a list of
    (col_index, name); ``preselect`` is the column to start on."""

    def __init__(self, columns, preselect=0, current=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Filter rows")
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Show rows where:"))

        self.col = QComboBox()
        for col_index, name in columns:
            self.col.addItem(name or f"Column {col_index + 1}", col_index)
        idx = self.col.findData(preselect)
        if idx >= 0:
            self.col.setCurrentIndex(idx)
        lay.addWidget(self.col)

        row = QHBoxLayout()
        self.op = QComboBox()
        for label, op in FILTER_OPS:
            self.op.addItem(label, op)
        self.value = QLineEdit()
        self.value.setPlaceholderText("value")
        row.addWidget(self.op)
        row.addWidget(self.value, 1)
        lay.addLayout(row)

        if current:
            cur_op, cur_val = current
            i = self.op.findData(cur_op)
            if i >= 0:
                self.op.setCurrentIndex(i)
            self.value.setText(cur_val)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.value.setFocus()

    def result_filter(self):
        return self.col.currentData(), self.op.currentData(), self.value.text()


class _SortDialog(QDialog):
    """Pick a column + direction to sort by (or clear sorting). ``columns`` is
    a list of (col_index, name); a leading '(original order)' entry clears."""

    def __init__(self, columns, current_col=-1, current_desc=False, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Sort rows")
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Sort by:"))

        self.col = QComboBox()
        self.col.addItem("(original order)", -1)
        for col_index, name in columns:
            self.col.addItem(name or f"Column {col_index + 1}", col_index)
        idx = self.col.findData(current_col)
        self.col.setCurrentIndex(idx if idx >= 0 else 0)
        lay.addWidget(self.col)

        self.direction = QComboBox()
        self.direction.addItem("Ascending", False)
        self.direction.addItem("Descending", True)
        self.direction.setCurrentIndex(1 if current_desc else 0)
        lay.addWidget(self.direction)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def result_sort(self):
        return self.col.currentData(), self.direction.currentData()


class CsvTableView(QWidget):
    """Toolbar + collapsible Columns panel + QTableView for a CSV/TSV doc."""

    PANEL_WIDTH = 210

    def __init__(self, doc_view, parent=None):
        super().__init__(parent)
        self._doc_view = doc_view
        self.model = RecordTableModel(doc_view.model, self)
        self.proxy = RecordFilterProxy(self.model, self)
        self._col_items: Dict[int, QListWidgetItem] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # -- toolbar ---------------------------------------------------------
        bar = QHBoxLayout()
        bar.setContentsMargins(6, 4, 6, 4)
        bar.setSpacing(6)

        self._columns_toggle = QPushButton("☰ Columns")
        self._columns_toggle.setObjectName("csvBtnColumns")
        self._columns_toggle.setCheckable(True)
        self._columns_toggle.setToolTip("Show/hide the columns panel")
        self._columns_toggle.toggled.connect(self._toggle_columns_panel)
        bar.addWidget(self._columns_toggle)

        filter_btn = QPushButton("Filter…")
        filter_btn.setObjectName("csvBtnFilter")
        filter_btn.setToolTip("Add a column filter (or right-click a header)")
        filter_btn.clicked.connect(self._add_filter)
        bar.addWidget(filter_btn)

        sort_btn = QPushButton("Sort…")
        sort_btn.setObjectName("csvBtnSort")
        sort_btn.setToolTip("Sort the table by a column")
        sort_btn.clicked.connect(self._sort_dialog)
        bar.addWidget(sort_btn)

        clear_btn = QPushButton("Clear Filters")
        clear_btn.setObjectName("csvBtnClear")
        clear_btn.clicked.connect(self._clear_filters)
        bar.addWidget(clear_btn)

        bar.addStretch(1)

        self._csv_btn = QPushButton("Export CSV")
        self._csv_btn.setObjectName("csvBtnCsv")
        self._csv_btn.setToolTip(
            "Export the visible columns to a new tab as CSV "
            "(enabled once you hide some columns)")
        self._csv_btn.clicked.connect(lambda: self._export("csv"))
        bar.addWidget(self._csv_btn)

        self._json_btn = QPushButton("Export JSON")
        self._json_btn.setObjectName("csvBtnJson")
        self._json_btn.setToolTip(
            "Export the visible columns to a new tab as JSON "
            "(enabled once you hide some columns)")
        self._json_btn.clicked.connect(lambda: self._export("json"))
        bar.addWidget(self._json_btn)
        outer.addLayout(bar)

        # -- body: [Columns panel | table] -----------------------------------
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        self._panel = self._build_columns_panel()
        self._panel.setVisible(False)  # collapsed by default
        body.addWidget(self._panel)

        # Sorting is explicit (header right-click), NOT click-to-sort: enabling
        # QTableView sorting triggers an immediate full sort on load, which
        # materializes every row and makes big files slow to open. We sort the
        # proxy on demand instead, and disable dynamic re-sorting on filter.
        self.proxy.setDynamicSortFilter(False)
        self._sort_col = -1
        self._sort_order = Qt.SortOrder.AscendingOrder

        self.view = _CsvTable()
        self.view.setModel(self.proxy)
        self.view.setAlternatingRowColors(True)
        self.view.setSelectionBehavior(
            QTableView.SelectionBehavior.SelectItems)
        self.view.setSelectionMode(
            QTableView.SelectionMode.ExtendedSelection)
        hh = self.view.horizontalHeader()
        hh.setStretchLastSection(True)
        hh.setSectionsMovable(True)
        hh.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        hh.customContextMenuRequested.connect(self._header_menu)
        body.addWidget(self.view, 1)
        outer.addLayout(body, 1)
        # Ctrl+C is handled by the window's Copy action, which dispatches to
        # copy_selection() when the table is active (a widget-level shortcut
        # here would clash with that window action).

        self.apply_style(getattr(doc_view, "_style", None))
        self._sync_export_enabled()

        # Clear the cell selection when the user clicks anywhere outside the
        # table (focusOut alone misses clicks on non-focusable empty space).
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    def eventFilter(self, obj, event):  # noqa: N802
        if event.type() == QEvent.Type.MouseButtonPress:
            sm = self.view.selectionModel()
            if sm is not None and sm.hasSelection():
                w = obj
                inside = False
                while w is not None:
                    if w is self.view:
                        inside = True
                        break
                    w = w.parent()
                if not inside:
                    self.view.clearSelection()
                    self.view.setCurrentIndex(QModelIndex())
        return super().eventFilter(obj, event)

    # -- theming -------------------------------------------------------------
    def apply_style(self, style) -> None:
        """Colorize headers, gridlines, selection and the toolbar buttons from
        the app palette (with fixed accent colors per button)."""
        def hex_of(attr, fallback):
            color = getattr(style, attr, None) if style is not None else None
            return color.name() if color is not None else fallback

        dark = getattr(style, "dark", True) if style is not None else True
        view_bg = hex_of("view_bg", "#1e1e1e")
        alt_bg = hex_of("view_alt_bg", "#262728")
        text = hex_of("key", "#d8dee6")
        grid = hex_of("guide", "#4a4a4a")
        sel = hex_of("selection_bg", "#094771")
        count = hex_of("count", "#d7ba7d")
        border = hex_of("chrome_border", "#3c3c3c")
        # Grey header bars (a shade off the base), so they read as headers
        # without the loud blue. Column header slightly lighter than the row
        # (count) header to tell them apart.
        if dark:
            col_head_bg, row_head_bg = "#3a3d42", "#2e3033"
        else:
            col_head_bg, row_head_bg = "#e6e9ee", "#eef1f4"
        col_head_fg = text

        # Muted grey toolbar buttons: two alternating shades so neighbours are
        # distinguishable, each with an accent-colored label (kept subtle).
        if dark:
            b1, b2, bh = "#34373b", "#3b3f44", "#484c52"
            c_col, c_filter, c_sort = "#6ea8f0", "#4fc3c3", "#b49af0"
            c_clear, c_csv, c_json = "#e6b25a", "#6bc98a", "#4fc3d8"
            dis_bg, dis_fg = "#2b2d30", "#5c6066"
        else:
            b1, b2, bh = "#e9ecf1", "#dfe3e9", "#d1d6dd"
            c_col, c_filter, c_sort = "#1d4ed8", "#0e7490", "#6d28d9"
            c_clear, c_csv, c_json = "#b45309", "#197a3e", "#0e7490"
            dis_bg, dis_fg = "#eceef1", "#a8adb4"

        self.setStyleSheet(f"""
            QTableView {{
                background: {view_bg};
                alternate-background-color: {alt_bg};
                color: {text};
                gridline-color: {grid};
                selection-background-color: {sel};
                selection-color: #ffffff;
                border: 1px solid {border};
            }}
            QHeaderView::section:horizontal {{
                background: {col_head_bg};
                color: {col_head_fg};
                font-weight: bold;
                padding: 5px 8px;
                border: none;
                border-right: 1px solid rgba(255,255,255,0.15);
            }}
            QHeaderView::section:vertical {{
                background: {row_head_bg};
                color: {count};
                font-weight: bold;
                padding: 2px 6px;
                border: none;
                border-bottom: 1px solid {grid};
            }}
            QTableCornerButton::section {{
                background: {row_head_bg};
                border: none;
            }}
            QPushButton#csvColClose {{
                background: transparent;
                color: {text};
                border: none;
                font-size: 14px;
                font-weight: bold;
            }}
            QPushButton#csvColClose:hover {{
                background: {sel};
                color: #ffffff;
                border-radius: 4px;
            }}
            QPushButton#csvBtnColumns, QPushButton#csvBtnFilter,
            QPushButton#csvBtnSort, QPushButton#csvBtnClear,
            QPushButton#csvBtnCsv, QPushButton#csvBtnJson {{
                border: none;
                border-radius: 5px;
                padding: 5px 12px;
                font-weight: bold;
            }}
            QPushButton#csvBtnColumns {{ background: {b1}; color: {c_col}; }}
            QPushButton#csvBtnColumns:checked {{ background: {bh}; }}
            QPushButton#csvBtnFilter  {{ background: {b2}; color: {c_filter}; }}
            QPushButton#csvBtnSort    {{ background: {b1}; color: {c_sort}; }}
            QPushButton#csvBtnClear   {{ background: {b2}; color: {c_clear}; }}
            QPushButton#csvBtnCsv     {{ background: {b1}; color: {c_csv}; }}
            QPushButton#csvBtnJson    {{ background: {b2}; color: {c_json}; }}
            QPushButton#csvBtnColumns:hover, QPushButton#csvBtnFilter:hover,
            QPushButton#csvBtnSort:hover, QPushButton#csvBtnClear:hover,
            QPushButton#csvBtnCsv:hover, QPushButton#csvBtnJson:hover {{
                background: {bh};
            }}
            QPushButton#csvBtnCsv:disabled, QPushButton#csvBtnJson:disabled {{
                background: {dis_bg};
                color: {dis_fg};
            }}
        """)

    # -- columns panel -------------------------------------------------------
    def _build_columns_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("csvColPanel")
        panel.setFixedWidth(self.PANEL_WIDTH)
        panel.setStyleSheet(
            "#csvColPanel { border-right: 1px solid rgba(128,128,128,0.35); }")
        v = QVBoxLayout(panel)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(6)

        head = QHBoxLayout()
        head.setSpacing(4)
        title = QLabel("Columns")
        title.setStyleSheet("font-weight: bold;")
        head.addWidget(title)
        head.addStretch(1)
        close = QPushButton("✕")
        close.setObjectName("csvColClose")
        close.setFlat(True)
        close.setFixedSize(22, 22)
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setToolTip("Collapse panel")
        close.clicked.connect(lambda: self._columns_toggle.setChecked(False))
        head.addWidget(close)
        v.addLayout(head)

        self._col_search = QLineEdit()
        self._col_search.setPlaceholderText("Filter columns…")
        self._col_search.setClearButtonEnabled(True)
        self._col_search.textChanged.connect(self._filter_column_list)
        v.addWidget(self._col_search)

        allrow = QHBoxLayout()
        allrow.setSpacing(4)
        show_all = QPushButton("Show All")
        show_all.clicked.connect(lambda: self._set_all_columns(True))
        hide_all = QPushButton("Hide All")
        hide_all.clicked.connect(lambda: self._set_all_columns(False))
        allrow.addWidget(show_all)
        allrow.addWidget(hide_all)
        v.addLayout(allrow)

        self._col_list = QListWidget()
        self._col_list.setObjectName("csvColList")
        for col, name in enumerate(self.model.headers()):
            item = QListWidgetItem(name or f"Column {col + 1}")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            item.setData(Qt.ItemDataRole.UserRole, col)
            self._col_list.addItem(item)
            self._col_items[col] = item
        self._col_list.itemChanged.connect(self._on_col_item_changed)
        v.addWidget(self._col_list, 1)
        return panel

    def _toggle_columns_panel(self, shown: bool) -> None:
        self._panel.setVisible(shown)
        if shown:
            self._col_search.setFocus()

    def _filter_column_list(self, text: str) -> None:
        needle = text.strip().lower()
        for i in range(self._col_list.count()):
            item = self._col_list.item(i)
            item.setHidden(bool(needle) and needle not in item.text().lower())

    def _on_col_item_changed(self, item: QListWidgetItem) -> None:
        col = item.data(Qt.ItemDataRole.UserRole)
        self.view.setColumnHidden(
            col, item.checkState() != Qt.CheckState.Checked)
        self._sync_export_enabled()

    def _sync_export_enabled(self) -> None:
        """Export only makes sense once the displayed table differs from the
        file — enable it when some (but not all) columns are hidden OR a column
        filter is active. (Always requires at least one visible column.)"""
        total = self.model.columnCount()
        visible = len(self._visible_columns())
        has_rows = self.proxy.rowCount() > 0
        changed = visible > 0 and has_rows and (
            visible < total or self.proxy.any_column_filter())
        self._csv_btn.setEnabled(changed)
        self._json_btn.setEnabled(changed)

    def _set_all_columns(self, visible: bool) -> None:
        self._col_list.blockSignals(True)
        state = Qt.CheckState.Checked if visible else Qt.CheckState.Unchecked
        for col, item in self._col_items.items():
            item.setCheckState(state)
            self.view.setColumnHidden(col, not visible)
        self._col_list.blockSignals(False)
        self._sync_export_enabled()

    def _sync_checkbox(self, col: int, visible: bool) -> None:
        item = self._col_items.get(col)
        if item is None:
            return
        self._col_list.blockSignals(True)
        item.setCheckState(
            Qt.CheckState.Checked if visible else Qt.CheckState.Unchecked)
        self._col_list.blockSignals(False)

    def _show_all_columns(self) -> None:
        self._set_all_columns(True)

    # -- header context menu -------------------------------------------------
    def _header_menu(self, pos) -> None:
        hh = self.view.horizontalHeader()
        col = hh.logicalIndexAt(pos)
        if col < 0:
            return
        headers = self.model.headers()
        name = headers[col] if col < len(headers) else f"Column {col + 1}"
        menu = QMenu(self)
        menu.addAction(f"Filter “{name}”…", lambda: self._filter_column(col))
        if self.proxy.has_column_filter(col):
            menu.addAction(
                "Clear this filter", lambda: self._clear_one_filter(col))
        menu.addSeparator()
        menu.addAction("Pin to Left", lambda: self._pin_column(col))
        menu.addAction("Hide Column", lambda: self._hide_column(col))
        menu.addAction("Show All Columns", self._show_all_columns)
        menu.exec(hh.mapToGlobal(pos))

    def _hide_column(self, col: int) -> None:
        self.view.setColumnHidden(col, True)
        self._sync_checkbox(col, False)
        self._sync_export_enabled()

    def _pin_column(self, col: int) -> None:
        hh = self.view.horizontalHeader()
        hh.moveSection(hh.visualIndex(col), 0)

    def _column_choices(self):
        headers = self.model.headers()
        return [
            (c, headers[c] if c < len(headers) else f"Column {c + 1}")
            for c in range(self.model.columnCount())
        ]

    def _add_filter(self) -> None:
        """Toolbar 'Filter…': choose any column, then operator + value."""
        self._open_filter_dialog(preselect=0)

    def _filter_column(self, col: int) -> None:
        """Header menu 'Filter …': preselect the clicked column."""
        self._open_filter_dialog(preselect=col)

    def _open_filter_dialog(self, preselect: int) -> None:
        dlg = _ColumnFilterDialog(
            self._column_choices(), preselect,
            self.proxy.column_filter(preselect), self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            col, op, value = dlg.result_filter()
            if value == "" and op in ("contains", "!contains", "starts", "ends"):
                self.proxy.clear_column_filter(col)
            else:
                self.proxy.set_column_filter(col, op, value)
            self._sync_export_enabled()

    def _clear_one_filter(self, col: int) -> None:
        self.proxy.clear_column_filter(col)
        self._sync_export_enabled()

    def _clear_filters(self) -> None:
        self.proxy.clear_column_filters()
        self._sync_export_enabled()

    def _sort_dialog(self) -> None:
        """Toolbar 'Sort…': sort by a chosen column + direction, on demand."""
        dlg = _SortDialog(
            self._column_choices(), self._sort_col,
            self._sort_order == Qt.SortOrder.DescendingOrder, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        col, desc = dlg.result_sort()
        self._sort_col = col
        self._sort_order = (
            Qt.SortOrder.DescendingOrder if desc
            else Qt.SortOrder.AscendingOrder)
        if col < 0:
            self.proxy.sort(-1)  # restore original document order
        else:
            self.proxy.sort(col, self._sort_order)

    # -- copy / export -------------------------------------------------------
    def copy_selection(self) -> None:
        sel = self.view.selectionModel()
        if sel is None:
            return
        indexes = sel.selectedIndexes()
        if not indexes:
            return
        rows = sorted({i.row() for i in indexes})
        cols = sorted({i.column() for i in indexes})
        by_cell = {(i.row(), i.column()): (i.data() or "") for i in indexes}
        lines = [
            "\t".join(str(by_cell.get((r, c), "")) for c in cols)
            for r in rows
        ]
        QGuiApplication.clipboard().setText("\n".join(lines))

    def _visible_columns(self) -> List[int]:
        """Logical column indexes currently shown, in visual (display) order."""
        hh = self.view.horizontalHeader()
        order = sorted(
            range(self.model.columnCount()),
            key=lambda c: hh.visualIndex(c),
        )
        return [c for c in order if not self.view.isColumnHidden(c)]

    def _filtered_records(self) -> List[dict]:
        """Rows currently passing all filters, as dicts of the visible columns
        in display order."""
        cols = self._visible_columns()
        headers = self.model.headers()
        out = []
        for prow in range(self.proxy.rowCount()):
            src = self.proxy.mapToSource(self.proxy.index(prow, 0)).row()
            rec = {}
            for c in cols:
                key = headers[c] if c < len(headers) else f"Column {c + 1}"
                rec[key] = self.model.cell_text(src, c)
            out.append(rec)
        return out

    def _export(self, fmt: str) -> None:
        window = self.window()
        if not hasattr(window, "_open_text_as_tab"):
            return
        records = self._filtered_records()
        if fmt == "json":
            import json

            content = json.dumps(records, indent=2, ensure_ascii=False)
            window._open_text_as_tab(content, ".json")
        else:  # csv
            import csv
            import io

            buf = io.StringIO()
            fieldnames = list(records[0].keys()) if records else []
            writer = csv.DictWriter(buf, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(records)
            window._open_text_as_tab(buf.getvalue(), ".csv")
