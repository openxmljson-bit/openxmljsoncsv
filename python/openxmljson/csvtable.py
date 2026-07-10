"""Spreadsheet-style table model for CSV/TSV tabs.

Wraps the existing DocumentModel: rows are the document's records, columns
the header keys. Cell values are fetched lazily per painted cell (QTableView
virtualizes), with a small per-row cache, so huge files stay fast.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QSortFilterProxyModel,
    Qt,
)

from openxmljson.model import KEY, OBJECT, DocumentModel


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

    def record_node(self, row: int) -> Optional[int]:
        return self._records[row] if 0 <= row < len(self._records) else None


class RecordFilterProxy(QSortFilterProxyModel):
    """Applies the toolbar filter to the table view: shows only records
    whose node is in the visible set (None = no filter)."""

    def __init__(self, source: RecordTableModel, parent=None):
        super().__init__(parent)
        self._visible = None
        self.setSourceModel(source)

    def set_visible(self, visible) -> None:
        self._visible = visible
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, _parent) -> bool:  # noqa: N802
        if self._visible is None:
            return True
        node = self.sourceModel().record_node(source_row)
        return node in self._visible
