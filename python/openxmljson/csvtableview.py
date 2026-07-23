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

from PySide6.QtCore import (
    QEvent,
    QModelIndex,
    QObject,
    QRunnable,
    Qt,
    QThreadPool,
    Signal,
)
from PySide6.QtGui import QColor, QGuiApplication, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QSpinBox,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from openxmljson.checkboxstyle import indicator_qss
from openxmljson.csvtable import FILTER_OPS, RecordFilterProxy, RecordTableModel


class _ColumnList(QListWidget):
    """QListWidget where clicking anywhere on a row toggles its checkbox (not
    just the small indicator), so selecting a column name checks it too. A click
    on the checkbox itself keeps its normal behavior."""

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint()
            item = self.itemAt(pos)
            if item is not None and item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                index = self.indexFromItem(item)
                opt = QStyleOptionViewItem()
                opt.initFrom(self)
                opt.rect = self.visualRect(index)
                opt.features |= (
                    QStyleOptionViewItem.ViewItemFeature.HasCheckIndicator)
                cb = self.style().subElementRect(
                    QStyle.SubElement.SE_ItemViewItemCheckIndicator, opt, self)
                if self.visualRect(index).contains(pos) and not cb.contains(pos):
                    new = (Qt.CheckState.Unchecked
                           if item.checkState() == Qt.CheckState.Checked
                           else Qt.CheckState.Checked)
                    item.setCheckState(new)
        super().mousePressEvent(event)


class _CsvTable(QTableView):
    """QTableView that drops its cell selection when it loses focus, so the
    blue highlight doesn't linger after you click elsewhere. Popups (context
    menu) keep the selection."""

    def focusOutEvent(self, event):  # noqa: N802
        if event.reason() != Qt.FocusReason.PopupFocusReason:
            self.clearSelection()
            self.setCurrentIndex(QModelIndex())
        super().focusOutEvent(event)


class _ExportSignals(QObject):
    progress = Signal(int, int)   # (current, total)
    done = Signal(str)            # output path
    failed = Signal(str)
    cancelled = Signal()


class _CsvExportTask(QRunnable):
    """Stream the visible/filtered table to a file off the GUI thread. Each
    record is reconstructed one at a time (bounded memory), so exporting a
    large table doesn't freeze the app."""

    def __init__(self, model, nodes, cols, headers, fmt, out_path):
        super().__init__()
        self.signals = _ExportSignals()
        self._model = model
        self._nodes = nodes        # source record node ids, in display order
        self._cols = cols          # visible logical column indexes
        self._headers = headers
        self._fmt = fmt            # "json" or "csv"
        self._out_path = out_path
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def _record(self, node):
        """Reconstruct a record and keep only the visible columns, keyed by
        header name (dict records) or by position (headerless list records)."""
        val = self._model.reconstruct(node)
        out = {}
        for c in self._cols:
            key = self._headers[c] if c < len(self._headers) else f"Column {c+1}"
            if isinstance(val, dict):
                out[key] = val.get(key)
            elif isinstance(val, list):
                out[key] = val[c] if c < len(val) else None
            else:
                out[key] = val
        return out

    def run(self) -> None:
        import csv
        import json

        total = len(self._nodes)
        try:
            with open(self._out_path, "w", encoding="utf-8", newline="") as fh:
                if self._fmt == "json":
                    fh.write("[\n")
                    first = True
                    for i, node in enumerate(self._nodes):
                        if self._cancel:
                            return self._abort()
                        if i % 250 == 0:
                            self.signals.progress.emit(i, total)
                        rec = self._record(node)
                        fh.write(("" if first else ",\n")
                                 + json.dumps(rec, ensure_ascii=False))
                        first = False
                    fh.write("\n]\n")
                else:  # csv
                    fieldnames = [
                        self._headers[c] if c < len(self._headers)
                        else f"Column {c+1}" for c in self._cols]
                    writer = csv.DictWriter(fh, fieldnames=fieldnames)
                    writer.writeheader()
                    for i, node in enumerate(self._nodes):
                        if self._cancel:
                            return self._abort()
                        if i % 250 == 0:
                            self.signals.progress.emit(i, total)
                        writer.writerow(self._record(node))
        except BaseException as exc:  # noqa: BLE001 - reported to the UI
            self._unlink()
            self.signals.failed.emit(str(exc))
            return
        self.signals.progress.emit(total, total)
        self.signals.done.emit(self._out_path)

    def _abort(self):
        self._unlink()
        self.signals.cancelled.emit()

    def _unlink(self) -> None:
        import os
        try:
            os.unlink(self._out_path)
        except OSError:
            pass


class _CoverageTask(QRunnable):
    """Column 'coverage': tally the distinct values of one column and their
    counts across the (filtered) rows, off the GUI thread, and write the result
    as CSV. Reads just the target cell per record (no full reconstruct) and
    caps distinct values so a high-cardinality column can't exhaust memory."""

    MAX_DISTINCT = 100_000
    BAR_WIDTH = 20            # characters in the textual share bar

    def __init__(self, source, nodes, col, header, out_path,
                 case_insensitive=False, trim=True, top_n=0):
        super().__init__()
        self.signals = _ExportSignals()
        self._source = source
        self._nodes = nodes
        self._col = col
        self._header = header
        self._out_path = out_path
        self._ci = case_insensitive
        self._trim = trim
        self._top_n = top_n      # 0 = all
        self._cancel = False
        self.summary_text = ""   # filled on success; shown in the status bar

    def cancel(self) -> None:
        self._cancel = True

    def _bar(self, pct: float) -> str:
        filled = int(round(pct / 100.0 * self.BAR_WIDTH))
        filled = max(0, min(self.BAR_WIDTH, filled))
        return "█" * filled + "░" * (self.BAR_WIDTH - filled)

    @staticmethod
    def _as_float(text: str):
        t = text.strip().replace(",", "")
        if not t:
            return None
        try:
            return float(t)
        except ValueError:
            return None

    @staticmethod
    def _fmt_num(x: float) -> str:
        return f"{x:.4g}"

    def run(self) -> None:
        import csv

        doc = self._source._doc
        col = self._col
        counts: dict = {}
        other = 0                # values beyond the distinct cap
        total = 0
        # numeric profile
        num_count = 0
        num_min = num_max = None
        num_sum = 0.0
        n = len(self._nodes)
        try:
            for i, node in enumerate(self._nodes):
                if self._cancel:
                    self._unlink()
                    self.signals.cancelled.emit()
                    return
                if i % 250 == 0:
                    self.signals.progress.emit(i, n)
                total += 1
                kids = doc.child_nodes(node)
                raw = self._source.value_of_node(kids[col]) if col < len(kids) else ""

                f = self._as_float(raw)
                if f is not None:
                    num_count += 1
                    num_sum += f
                    num_min = f if num_min is None else min(num_min, f)
                    num_max = f if num_max is None else max(num_max, f)

                key = raw.strip() if self._trim else raw
                if self._ci:
                    key = key.lower()
                if key == "":
                    key = "(empty)"
                if key in counts:
                    counts[key] += 1
                elif len(counts) < self.MAX_DISTINCT:
                    counts[key] = 1
                else:
                    other += 1

            items = sorted(counts.items(), key=lambda kv: (-kv[1], str(kv[0])))
            empty = counts.get("(empty)", 0)
            filled = total - empty
            distinct = len(counts) - (1 if "(empty)" in counts else 0)

            # Top-N: keep the largest N, roll the rest into the "other" bucket.
            tail = 0
            if self._top_n and len(items) > self._top_n:
                tail = sum(c for _, c in items[self._top_n:])
                items = items[: self._top_n]
            other_total = other + tail

            with open(self._out_path, "w", encoding="utf-8", newline="") as fh:
                w = csv.writer(fh)
                w.writerow([self._header or "value", "count", "percent",
                            "cumulative_%", "share"])
                cumulative = 0.0
                for val, c in items:
                    pct = c * 100.0 / total if total else 0.0
                    cumulative += pct
                    w.writerow([val, c, f"{pct:.2f}",
                                f"{cumulative:.2f}", self._bar(pct)])
                if other_total:
                    pct = other_total * 100.0 / total if total else 0.0
                    cumulative += pct
                    w.writerow(["(other values)", other_total, f"{pct:.2f}",
                                f"{cumulative:.2f}", self._bar(pct)])

            fill_pct = filled * 100.0 / total if total else 0.0
            uniq_pct = distinct * 100.0 / total if total else 0.0
            distinct_txt = f"{distinct:,}{'+' if other else ''}"
            parts = [
                f"Coverage of “{self._header}”: {total:,} rows",
                f"{distinct_txt} distinct ({uniq_pct:.1f}% unique)",
                f"{empty:,} empty ({fill_pct:.1f}% filled)",
            ]
            non_empty = [(v, c) for v, c in items if v != "(empty)"]
            if non_empty and total:
                mv, mc = non_empty[0]
                parts.append(f"top: {mv} ({mc * 100.0 / total:.1f}%)")
            # Numeric stats when the column is mostly numeric.
            if num_count and num_count >= 0.8 * total:
                mean = num_sum / num_count
                parts.append(
                    f"min {self._fmt_num(num_min)} · max {self._fmt_num(num_max)}"
                    f" · mean {self._fmt_num(mean)} · sum {self._fmt_num(num_sum)}")
            self.summary_text = " · ".join(parts)
        except BaseException as exc:  # noqa: BLE001 - reported to the UI
            self._unlink()
            self.signals.failed.emit(str(exc))
            return
        self.signals.progress.emit(n, n)
        self.signals.done.emit(self._out_path)

    def _unlink(self) -> None:
        import os
        try:
            os.unlink(self._out_path)
        except OSError:
            pass


class _ProfileTask(QRunnable):
    """Whole-file profile: one row per column with distinct count, fill/null %,
    a fill bar, and the most common value. Runs off the GUI thread; per-column
    distinct is capped so wide/high-cardinality files stay bounded."""

    MAX_DISTINCT = 10_000
    BAR_WIDTH = 20

    def __init__(self, source, nodes, cols, headers, out_path):
        super().__init__()
        self.signals = _ExportSignals()
        self._source = source
        self._nodes = nodes
        self._cols = cols
        self._headers = headers
        self._out_path = out_path
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def _bar(self, pct: float) -> str:
        filled = int(round(pct / 100.0 * self.BAR_WIDTH))
        filled = max(0, min(self.BAR_WIDTH, filled))
        return "█" * filled + "░" * (self.BAR_WIDTH - filled)

    def run(self) -> None:
        import csv

        doc = self._source._doc
        cols = self._cols
        counts = [dict() for _ in cols]
        empty = [0] * len(cols)
        over = [False] * len(cols)   # per-column distinct cap exceeded
        total = 0
        n = len(self._nodes)
        try:
            for i, node in enumerate(self._nodes):
                if self._cancel:
                    self._unlink()
                    self.signals.cancelled.emit()
                    return
                if i % 250 == 0:
                    self.signals.progress.emit(i, n)
                total += 1
                kids = doc.child_nodes(node)
                for j, ci in enumerate(cols):
                    val = self._source.value_of_node(kids[ci]) if ci < len(kids) else ""
                    if val == "":
                        empty[j] += 1
                        key = "(empty)"
                    else:
                        key = val
                    d = counts[j]
                    if key in d:
                        d[key] += 1
                    elif len(d) < self.MAX_DISTINCT:
                        d[key] = 1
                    else:
                        over[j] = True

            with open(self._out_path, "w", encoding="utf-8", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(["column", "distinct", "non_empty", "empty",
                            "fill_%", "top_value", "top_%"])
                for j, ci in enumerate(cols):
                    d = counts[j]
                    emp = empty[j]
                    non_empty = total - emp
                    distinct = len(d) - (1 if "(empty)" in d else 0)
                    distinct_txt = f"{distinct}{'+' if over[j] else ''}"
                    fill = non_empty * 100.0 / total if total else 0.0
                    if d:
                        tv, tc = max(d.items(), key=lambda kv: kv[1])
                    else:
                        tv, tc = "", 0
                    toppct = tc * 100.0 / total if total else 0.0
                    header = (self._headers[ci] if ci < len(self._headers)
                              else f"Column {ci + 1}")
                    w.writerow([header, distinct_txt, non_empty, emp,
                                f"{fill:.2f}", tv, f"{toppct:.2f}"])
        except BaseException as exc:  # noqa: BLE001 - reported to the UI
            self._unlink()
            self.signals.failed.emit(str(exc))
            return
        self.signals.progress.emit(n, n)
        self.signals.done.emit(self._out_path)

    def _unlink(self) -> None:
        import os
        try:
            os.unlink(self._out_path)
        except OSError:
            pass


class _ShareBarDelegate(QStyledItemDelegate):
    """Paints a column's text in an accent color — used for the coverage
    'share' bar so the blocks read as a colored mini-chart, not plain text."""

    def __init__(self, color="#D97757", parent=None):   # Claude orange
        super().__init__(parent)
        self._color = QColor(color)

    def initStyleOption(self, option, index):  # noqa: N802
        super().initStyleOption(option, index)
        option.palette.setColor(QPalette.ColorRole.Text, self._color)
        option.palette.setColor(QPalette.ColorRole.HighlightedText, self._color)


class _CoverageOptionsDialog(QDialog):
    """Options for a column coverage run."""

    def __init__(self, column_name, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Column coverage")
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"Value coverage for “{column_name}”"))

        self._trim = QCheckBox("Trim surrounding whitespace")
        self._trim.setChecked(True)
        self._ci = QCheckBox("Case-insensitive (group Aa / aa together)")
        lay.addWidget(self._trim)
        lay.addWidget(self._ci)

        row = QHBoxLayout()
        row.addWidget(QLabel("Show top:"))
        self._top = QSpinBox()
        self._top.setRange(0, 1_000_000)
        self._top.setValue(0)
        self._top.setSpecialValueText("All")   # 0 shows "All"
        row.addWidget(self._top)
        row.addWidget(QLabel("values"))
        row.addStretch(1)
        lay.addLayout(row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def options(self):
        return self._ci.isChecked(), self._trim.isChecked(), self._top.value()


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

        profile_btn = QPushButton("Profile")
        profile_btn.setObjectName("csvBtnProfile")
        profile_btn.setToolTip(
            "Whole-file profile: one row per column (distinct, fill %, top value)")
        profile_btn.clicked.connect(self._file_profile)
        bar.addWidget(profile_btn)

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
        hh.setStretchLastSection(False)   # don't blow the last column to full width
        hh.setSectionsMovable(True)
        hh.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        hh.customContextMenuRequested.connect(self._header_menu)
        # Colorize the coverage "share" bar column so the blocks read as a
        # colored mini-chart rather than plain white text.
        for c, h in enumerate(self.model.headers()):
            if h and h.strip().lower() == "share":
                self.view.setItemDelegateForColumn(
                    c, _ShareBarDelegate(parent=self.view))
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
        window_bg = hex_of("window_bg", "#252526")
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
            c_prof = "#e879a8"
            dis_bg, dis_fg = "#2b2d30", "#5c6066"
        else:
            b1, b2, bh = "#e9ecf1", "#dfe3e9", "#d1d6dd"
            c_col, c_filter, c_sort = "#1d4ed8", "#0e7490", "#6d28d9"
            c_clear, c_csv, c_json = "#b45309", "#197a3e", "#0e7490"
            c_prof = "#be185d"
            dis_bg, dis_fg = "#eceef1", "#a8adb4"

        self.setStyleSheet(f"""
            QFrame#csvColPanel {{
                background: {window_bg};
                border-right: 1px solid {border};
            }}
            QLineEdit#csvColSearch {{
                background: {view_bg};
                color: {text};
                border: 1px solid {border};
                border-radius: 4px;
                padding: 4px 6px;
            }}
            QListWidget#csvColList {{
                background: {view_bg};
                color: {text};
                border: 1px solid {border};
                border-radius: 4px;
            }}
            QListWidget#csvColList::item {{ padding: 2px 4px; }}
            QListWidget#csvColList::item:selected {{
                background: {sel};
                color: {text};
            }}
            QTableView {{
                background: {view_bg};
                alternate-background-color: {alt_bg};
                color: {text};
                gridline-color: {grid};
                selection-background-color: {sel};
                selection-color: {text};
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
                color: {text};
                border-radius: 4px;
            }}
            QPushButton#csvBtnColumns, QPushButton#csvBtnFilter,
            QPushButton#csvBtnSort, QPushButton#csvBtnProfile,
            QPushButton#csvBtnClear,
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
            QPushButton#csvBtnProfile {{ background: {b2}; color: {c_prof}; }}
            QPushButton#csvBtnClear   {{ background: {b1}; color: {c_clear}; }}
            QPushButton#csvBtnCsv     {{ background: {b1}; color: {c_csv}; }}
            QPushButton#csvBtnJson    {{ background: {b2}; color: {c_json}; }}
            QPushButton#csvBtnColumns:hover, QPushButton#csvBtnFilter:hover,
            QPushButton#csvBtnSort:hover, QPushButton#csvBtnProfile:hover,
            QPushButton#csvBtnClear:hover,
            QPushButton#csvBtnCsv:hover, QPushButton#csvBtnJson:hover {{
                background: {bh};
            }}
            QPushButton#csvBtnCsv:disabled, QPushButton#csvBtnJson:disabled {{
                background: {dis_bg};
                color: {dis_fg};
            }}
        """ + indicator_qss("QListWidget#csvColList"))

    # -- columns panel -------------------------------------------------------
    def _build_columns_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("csvColPanel")
        panel.setFixedWidth(self.PANEL_WIDTH)
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
        self._col_search.setObjectName("csvColSearch")
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

        self._col_list = _ColumnList()
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
        menu.addAction(
            f"Column coverage “{name}”…", lambda: self._column_coverage(col))
        menu.addSeparator()
        menu.addAction("Pin to Left", lambda: self._pin_column(col))
        menu.addAction("Hide Column", lambda: self._hide_column(col))
        menu.addAction("Show All Columns", self._show_all_columns)
        menu.exec(hh.mapToGlobal(pos))

    def _column_coverage(self, col: int) -> None:
        """Tally the distinct values of a column and their counts across the
        current (filtered) rows, opening the result as a new CSV tab. Runs on a
        background thread with progress, like Export."""
        import os as _os
        import tempfile

        from openxmljson.app import _JobProgressDialog

        window = self.window()
        if not hasattr(window, "open_path"):
            return
        headers = self.model.headers()
        header = headers[col] if col < len(headers) else f"Column {col + 1}"

        opts = _CoverageOptionsDialog(header, self)
        if opts.exec() != QDialog.DialogCode.Accepted:
            return
        case_insensitive, trim, top_n = opts.options()

        nodes = []
        for prow in range(self.proxy.rowCount()):
            src = self.proxy.mapToSource(self.proxy.index(prow, 0)).row()
            node = self.model.record_node(src)
            if node is not None:
                nodes.append(node)

        fd, out_path = tempfile.mkstemp(suffix=".csv", prefix="oxj_")
        _os.close(fd)

        prog = _JobProgressDialog(
            "Column coverage — opening in new tab",
            f"Counting values in “{header}” across {len(nodes):,} rows…",
            window)
        task = _CoverageTask(
            self.model._source, nodes, col, header, out_path,
            case_insensitive=case_insensitive, trim=trim, top_n=top_n)
        task.setAutoDelete(False)
        self._cov_prog = prog
        self._cov_task = task

        def cleanup():
            self._cov_prog = None
            self._cov_task = None
            prog.finish()

        def on_done(path):
            summary = task.summary_text
            cleanup()
            if hasattr(window, "_temp_files"):
                window._temp_files.add(path)
            window.open_path(path)
            if summary and hasattr(window, "statusBar"):
                window.statusBar().showMessage(summary, 12000)

        def on_failed(msg):
            cleanup()
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(window, "Coverage failed", msg)

        task.signals.progress.connect(prog.set_progress)
        task.signals.done.connect(on_done)
        task.signals.failed.connect(on_failed)
        task.signals.cancelled.connect(lambda: cleanup())
        prog.canceled.connect(task.cancel)
        prog.set_progress(0, len(nodes))
        prog.show()
        QApplication.processEvents()
        QThreadPool.globalInstance().start(task)

    def _file_profile(self) -> None:
        """Whole-file profile: one row per visible column (distinct, fill %,
        top value) opened as a new CSV tab. Background worker with progress."""
        import os as _os
        import tempfile

        from openxmljson.app import _JobProgressDialog

        window = self.window()
        if not hasattr(window, "open_path"):
            return
        cols = self._visible_columns()
        if not cols:
            return
        headers = self.model.headers()

        nodes = []
        for prow in range(self.proxy.rowCount()):
            src = self.proxy.mapToSource(self.proxy.index(prow, 0)).row()
            node = self.model.record_node(src)
            if node is not None:
                nodes.append(node)

        fd, out_path = tempfile.mkstemp(suffix=".csv", prefix="oxj_")
        _os.close(fd)

        prog = _JobProgressDialog(
            "File profile — opening in new tab",
            f"Profiling {len(cols)} columns across {len(nodes):,} rows…",
            window)
        task = _ProfileTask(self.model._source, nodes, cols, headers, out_path)
        task.setAutoDelete(False)
        self._prof_prog = prog
        self._prof_task = task

        def cleanup():
            self._prof_prog = None
            self._prof_task = None
            prog.finish()

        def on_done(path):
            cleanup()
            if hasattr(window, "_temp_files"):
                window._temp_files.add(path)
            window.open_path(path)

        def on_failed(msg):
            cleanup()
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(window, "Profile failed", msg)

        task.signals.progress.connect(prog.set_progress)
        task.signals.done.connect(on_done)
        task.signals.failed.connect(on_failed)
        task.signals.cancelled.connect(lambda: cleanup())
        prog.canceled.connect(task.cancel)
        prog.set_progress(0, len(nodes))
        prog.show()
        QApplication.processEvents()
        QThreadPool.globalInstance().start(task)

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

    def _export(self, fmt: str) -> None:
        """Export the visible/filtered table to a new tab (CSV or JSON).
        Streams on a background thread with a progress dialog so large tables
        don't freeze the app."""
        import tempfile

        from openxmljson.app import _JobProgressDialog

        window = self.window()
        if not hasattr(window, "open_path"):
            return

        cols = self._visible_columns()
        headers = self.model.headers()
        # Precompute the source record nodes in the current (filtered/sorted)
        # display order — cheap; the heavy reconstruct happens in the worker.
        nodes = []
        for prow in range(self.proxy.rowCount()):
            src = self.proxy.mapToSource(self.proxy.index(prow, 0)).row()
            node = self.model.record_node(src)
            if node is not None:
                nodes.append(node)

        suffix = ".json" if fmt == "json" else ".csv"
        fd, out_path = tempfile.mkstemp(suffix=suffix, prefix="oxj_")
        import os as _os
        _os.close(fd)

        prog = _JobProgressDialog(
            "Export — opening in new tab",
            f"Exporting {len(nodes):,} rows to {fmt.upper()}…",
            window)
        task = _CsvExportTask(
            self.model._source, nodes, cols, headers, fmt, out_path)
        task.setAutoDelete(False)
        self._export_prog = prog       # keep refs alive across the async hop
        self._export_task = task

        def cleanup():
            self._export_prog = None
            self._export_task = None
            prog.finish()

        def on_done(path):
            cleanup()
            if hasattr(window, "_temp_files"):
                window._temp_files.add(path)
            window.open_path(path)

        def on_failed(msg):
            cleanup()
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(window, "Export failed", msg)

        def on_cancelled():
            cleanup()

        task.signals.progress.connect(prog.set_progress)
        task.signals.done.connect(on_done)
        task.signals.failed.connect(on_failed)
        task.signals.cancelled.connect(on_cancelled)
        prog.canceled.connect(task.cancel)
        prog.set_progress(0, len(nodes))
        prog.show()
        QApplication.processEvents()   # paint the dialog before the worker runs
        QThreadPool.globalInstance().start(task)
