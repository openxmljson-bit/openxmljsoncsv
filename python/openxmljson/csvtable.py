"""Spreadsheet-style table model + view for CSV/TSV tabs.

``RecordTableModel`` wraps the existing DocumentModel: rows are the document's
records, columns the header keys. Cell values are fetched lazily per painted
cell (QTableView virtualizes), with a small per-row cache, so huge files stay
fast.

``RecordFilterProxy`` adds the toolbar's visible-node filter plus per-column
client-side filters and numeric-aware sorting.

``CsvTableView`` is the full widget: a toolbar (open in new tab, column
show/hide, clear filters, export) over a QTableView with movable columns,
sorting, pin-to-left, per-column filter dialogs, and cell copy.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QSortFilterProxyModel,
    Qt,
)

from openxmljson.model import KEY, OBJECT, DocumentModel

#: Filter operators offered per column (label -> internal op).
FILTER_OPS: List[Tuple[str, str]] = [
    ("contains", "contains"),
    ("does not contain", "!contains"),
    ("equals (=)", "="),
    ("not equal (≠)", "!="),
    ("greater than (>)", ">"),
    ("greater or equal (≥)", ">="),
    ("less than (<)", "<"),
    ("less or equal (≤)", "<="),
    ("starts with", "starts"),
    ("ends with", "ends"),
]


def _as_number(text: str):
    """Parse a cell as a float, or None if it isn't numeric."""
    if text is None:
        return None
    t = text.strip().replace(",", "")
    if not t:
        return None
    try:
        return float(t)
    except ValueError:
        return None


def match_op(cell: str, op: str, value: str) -> bool:
    """Evaluate a single column-filter predicate against a cell (pure, so it's
    unit-testable). Numeric comparison when both sides parse as numbers, else a
    case-insensitive string comparison."""
    cell = "" if cell is None else str(cell)
    value = "" if value is None else str(value)
    cn, vn = _as_number(cell), _as_number(value)
    both_num = cn is not None and vn is not None
    lc, lv = cell.lower(), value.lower()

    if op == "contains":
        return lv in lc
    if op == "!contains":
        return lv not in lc
    if op == "starts":
        return lc.startswith(lv)
    if op == "ends":
        return lc.endswith(lv)
    if op == "=":
        return (cn == vn) if both_num else (lc == lv)
    if op == "!=":
        return (cn != vn) if both_num else (lc != lv)
    if op == ">":
        return (cn > vn) if both_num else (lc > lv)
    if op == ">=":
        return (cn >= vn) if both_num else (lc >= lv)
    if op == "<":
        return (cn < vn) if both_num else (lc < lv)
    if op == "<=":
        return (cn <= vn) if both_num else (lc <= lv)
    return True


def compare_cells(a: str, b: str) -> bool:
    """True if a < b, numeric-aware (for sorting)."""
    an, bn = _as_number(a), _as_number(b)
    if an is not None and bn is not None:
        return an < bn
    return (a or "").lower() < (b or "").lower()


class RecordTableModel(QAbstractTableModel):
    CACHE_CAP = 4096  # cached rows of cell text

    def __init__(self, source: DocumentModel, parent=None):
        super().__init__(parent)
        self._source = source
        self._doc = source._doc
        self._records: List[int] = self._doc.child_nodes(self._doc.root())
        self._row_cache: Dict[int, List[str]] = {}
        self._headers = self._build_headers()

    def _build_headers(self) -> List[str]:
        if not self._records:
            return []
        first = self._records[0]
        if self._doc.kind(first) == OBJECT:
            return [
                self._source._key_name(k)
                for k in self._doc.child_nodes(first)
                if self._doc.kind(k) == KEY
            ]
        # Headerless CSV: number the widest early row's columns.
        width = max(
            len(self._doc.child_nodes(r)) for r in self._records[:100]
        )
        return [str(i + 1) for i in range(width)]

    # -- QAbstractTableModel ----------------------------------------------------

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._records)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._headers)

    def _cells(self, row: int) -> List[str]:
        cached = self._row_cache.get(row)
        if cached is not None:
            return cached
        if len(self._row_cache) > self.CACHE_CAP:
            self._row_cache.clear()
        record = self._records[row]
        cells = [
            self._source.value_of_node(kid)
            for kid in self._doc.child_nodes(record)
        ]
        self._row_cache[row] = cells
        return cells

    def cell_text(self, row: int, col: int) -> str:
        cells = self._cells(row)
        return cells[col] if 0 <= col < len(cells) else ""

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or role != Qt.ItemDataRole.DisplayRole:
            return None
        cells = self._cells(index.row())
        if index.column() < len(cells):
            return cells[index.column()]
        return ""

    def headerData(self, section: int, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            return (
                self._headers[section]
                if section < len(self._headers)
                else str(section + 1)
            )
        return str(section)  # row numbers, zero-based like the tree

    def headers(self) -> List[str]:
        return list(self._headers)

    def record_node(self, row: int) -> Optional[int]:
        return self._records[row] if 0 <= row < len(self._records) else None


class RecordFilterProxy(QSortFilterProxyModel):
    """Applies the toolbar's visible-node filter AND per-column client-side
    filters to the table, with numeric-aware sorting."""

    def __init__(self, source: RecordTableModel, parent=None):
        super().__init__(parent)
        self._visible = None
        self._col_filters: Dict[int, Tuple[str, str]] = {}
        self.setSourceModel(source)

    # -- toolbar (structural) filter -----------------------------------------
    def set_visible(self, visible) -> None:
        self._visible = visible
        self.invalidateFilter()

    # -- per-column filters ---------------------------------------------------
    def set_column_filter(self, col: int, op: str, value: str) -> None:
        self._col_filters[col] = (op, value)
        self.invalidateFilter()
        self.headerDataChanged.emit(Qt.Orientation.Horizontal, col, col)

    def clear_column_filter(self, col: int) -> None:
        if col in self._col_filters:
            del self._col_filters[col]
            self.invalidateFilter()
            self.headerDataChanged.emit(Qt.Orientation.Horizontal, col, col)

    def clear_column_filters(self) -> None:
        if self._col_filters:
            cols = list(self._col_filters)
            self._col_filters.clear()
            self.invalidateFilter()
            for col in cols:
                self.headerDataChanged.emit(Qt.Orientation.Horizontal, col, col)

    def column_filter(self, col: int):
        return self._col_filters.get(col)

    def has_column_filter(self, col: int) -> bool:
        return col in self._col_filters

    def any_column_filter(self) -> bool:
        return bool(self._col_filters)

    def filterAcceptsRow(self, source_row: int, _parent) -> bool:  # noqa: N802
        model: RecordTableModel = self.sourceModel()
        if self._visible is not None:
            node = model.record_node(source_row)
            if node not in self._visible:
                return False
        for col, (op, value) in self._col_filters.items():
            if not match_op(model.cell_text(source_row, col), op, value):
                return False
        return True

    def lessThan(self, left, right):  # noqa: N802
        model: RecordTableModel = self.sourceModel()
        a = model.cell_text(left.row(), left.column())
        b = model.cell_text(right.row(), right.column())
        return compare_cells(a, b)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        # Mark filtered columns with a small funnel so the filter is visible.
        if (
            role == Qt.ItemDataRole.DisplayRole
            and orientation == Qt.Orientation.Horizontal
            and section in self._col_filters
        ):
            base = super().headerData(section, orientation, role)
            return f"{base}  ⏷"
        return super().headerData(section, orientation, role)
