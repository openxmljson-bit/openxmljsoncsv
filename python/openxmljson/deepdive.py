"""The Deep Dive dialog: a checkable tree of a document's fields (from its
schema) so the user can pick which fields to keep. Returns the top-most checked
field paths; ``project.project_value`` turns those into a pruned document.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStyle,
    QStyleOptionViewItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

Path = Tuple[str, ...]
_PATH_ROLE = Qt.ItemDataRole.UserRole


class _FieldTree(QTreeWidget):
    """QTreeWidget where clicking anywhere on a row toggles its checkbox (not
    just the small indicator), so selecting a field name checks it too. A click
    on the checkbox itself, or the expand arrow, keeps its normal behavior."""

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint()
            index = self.indexAt(pos)
            if index.isValid():
                item = self.itemFromIndex(index)
                row = self.visualRect(index)
                opt = QStyleOptionViewItem()
                opt.initFrom(self)
                opt.rect = row
                opt.features |= (
                    QStyleOptionViewItem.ViewItemFeature.HasCheckIndicator)
                cb = self.style().subElementRect(
                    QStyle.SubElement.SE_ItemViewItemCheckIndicator, opt, self)
                # On the row content but NOT on the checkbox → toggle it.
                if (item is not None
                        and item.flags() & Qt.ItemFlag.ItemIsUserCheckable
                        and row.contains(pos) and not cb.contains(pos)):
                    new = (Qt.CheckState.Unchecked
                           if item.checkState(0) == Qt.CheckState.Checked
                           else Qt.CheckState.Checked)
                    item.setCheckState(0, new)
        super().mousePressEvent(event)


class DeepDiveDialog(QDialog):
    """Pick fields to keep. ``field_tree`` is the {name, path, children} tree
    from ``project.schema_field_tree``."""

    def __init__(self, field_tree: Dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Deep Dive — select fields")
        self.resize(420, 560)
        lay = QVBoxLayout(self)

        lay.addWidget(QLabel(
            "Tick the fields to keep. The result opens in a new tab."))

        self._search = QLineEdit()
        self._search.setPlaceholderText("Filter fields…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._filter)
        lay.addWidget(self._search)

        btnrow = QHBoxLayout()
        sel_all = QPushButton("Select All")
        sel_all.clicked.connect(lambda: self._set_all(Qt.CheckState.Checked))
        sel_none = QPushButton("Select None")
        sel_none.clicked.connect(lambda: self._set_all(Qt.CheckState.Unchecked))
        btnrow.addWidget(sel_all)
        btnrow.addWidget(sel_none)
        btnrow.addStretch(1)
        lay.addLayout(btnrow)

        self._tree = _FieldTree()
        self._tree.setHeaderHidden(True)
        self._tree.itemChanged.connect(self._on_item_changed)
        lay.addWidget(self._tree, 1)

        self._build_items(field_tree)
        self._tree.expandToDepth(0)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        self._ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self._ok.setText("Show Selected")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self._update_ok_enabled()

    # -- build ---------------------------------------------------------------
    def _build_items(self, field_tree: Dict) -> None:
        # The synthetic root isn't shown; its children become top-level items.
        for child in field_tree["children"]:
            self._tree.addTopLevelItem(self._make_item(child))

    def _make_item(self, node: Dict) -> QTreeWidgetItem:
        item = QTreeWidgetItem([node["name"]])
        item.setData(0, _PATH_ROLE, node["path"])
        flags = item.flags() | Qt.ItemFlag.ItemIsUserCheckable
        if node["children"]:
            flags |= Qt.ItemFlag.ItemIsAutoTristate
        item.setFlags(flags)
        item.setCheckState(0, Qt.CheckState.Unchecked)
        for child in node["children"]:
            item.addChild(self._make_item(child))
        return item

    # -- checkbox behavior ---------------------------------------------------
    def _on_item_changed(self, item: QTreeWidgetItem, _col: int) -> None:
        # Propagate a parent's Checked/Unchecked state down to its children
        # (Qt's autotristate only propagates child → parent).
        state = item.checkState(0)
        if state != Qt.CheckState.PartiallyChecked and item.childCount():
            self._tree.blockSignals(True)
            self._set_subtree(item, state)
            self._tree.blockSignals(False)
        self._update_ok_enabled()

    def _set_subtree(self, item: QTreeWidgetItem, state) -> None:
        for i in range(item.childCount()):
            child = item.child(i)
            child.setCheckState(0, state)
            self._set_subtree(child, state)

    def _set_all(self, state) -> None:
        self._tree.blockSignals(True)
        for i in range(self._tree.topLevelItemCount()):
            top = self._tree.topLevelItem(i)
            top.setCheckState(0, state)
            self._set_subtree(top, state)
        self._tree.blockSignals(False)
        self._update_ok_enabled()

    # -- search --------------------------------------------------------------
    def _filter(self, text: str) -> None:
        needle = text.strip().lower()

        def visit(item: QTreeWidgetItem) -> bool:
            child_match = False
            for i in range(item.childCount()):
                child_match = visit(item.child(i)) or child_match
            self_match = needle in item.text(0).lower()
            visible = self_match or child_match or not needle
            item.setHidden(not visible)
            return self_match or child_match

        for i in range(self._tree.topLevelItemCount()):
            visit(self._tree.topLevelItem(i))

    # -- results -------------------------------------------------------------
    def selected_paths(self) -> List[Path]:
        """Top-most fully-checked paths (a checked node keeps its whole
        subtree, so its children need not be listed)."""
        out: List[Path] = []

        def walk(item: QTreeWidgetItem) -> None:
            state = item.checkState(0)
            if state == Qt.CheckState.Checked:
                out.append(tuple(item.data(0, _PATH_ROLE)))
            elif state == Qt.CheckState.PartiallyChecked:
                for i in range(item.childCount()):
                    walk(item.child(i))

        for i in range(self._tree.topLevelItemCount()):
            walk(self._tree.topLevelItem(i))
        return out

    def _has_selection(self) -> bool:
        for i in range(self._tree.topLevelItemCount()):
            top = self._tree.topLevelItem(i)
            if top.checkState(0) != Qt.CheckState.Unchecked:
                return True
        return False

    def _update_ok_enabled(self) -> None:
        self._ok.setEnabled(self._has_selection())
