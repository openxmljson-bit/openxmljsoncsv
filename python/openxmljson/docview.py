"""One document tab: the tree view plus everything that is per-document —
model, search matches, filter/query, CSV table, live tail.

Each tab owns an independent mmap + index, so opening a file in a tab is
exactly the engine's normal open path; background tabs are idle widgets
that cost nothing, preserving the 10 GB fast path (requirement N4).
"""

from __future__ import annotations

import os
import re
import tempfile

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QMessageBox, QStackedLayout, QWidget

from openxmljson.model import DocumentModel, NodeFilterProxy
from openxmljson.styles import Style
from openxmljson.tree import DocumentTreeView

#: Above this node count a document is treated as "big": the top level is
#: not auto-expanded on open, keeping layout cost bounded.
BIG_DOC_NODES = 2_000_000

#: Cap on how much raw markup the XML source view loads into the text widget.
#: QPlainTextEdit handles large plain text well, so this is generous — it only
#: exists to stop a pathological multi-GB file from flooding memory.
XML_VIEW_MAX_BYTES = 64 * 1024 * 1024

#: Cap for the plain-text viewer (.txt/.js): read at most this many bytes into
#: the widget, appending a truncation note beyond it.
TEXT_VIEW_MAX_BYTES = 32 * 1024 * 1024

#: After a search, expand the tree to reveal up to this many matches.
REVEAL_MATCH_LIMIT = 500

#: Refuse filters matching more rows than this (ancestor-set size guard).
FILTER_MATCH_LIMIT = 500_000

#: Auto-expand the filtered tree when it has at most this many rows.
FILTER_EXPAND_LIMIT = 5_000

#: The flow diagram reconstructs the document in Python and lays out a card per
#: node, so it is a small/medium-document feature. Above this node count it is
#: not offered (a multi-GB file has far too many nodes to draw meaningfully).
DIAGRAM_MAX_NODES = 50_000

#: XML syntax highlighting runs a QSyntaxHighlighter over the whole markup,
#: which is expensive on large documents — so (like the diagram) it is offered
#: only for eager documents under this node count.
XML_HIGHLIGHT_MAX_NODES = 50_000


def _is_lazy(doc) -> bool:
    """True for on-demand (lazy) documents. Tolerant of wrappers that
    predate the flag (e.g. the tail SegmentedDocument)."""
    fn = getattr(doc, "is_lazy", None)
    return bool(fn()) if callable(fn) else False


class DocumentView(QWidget):
    #: Status-bar text (selection path, match counts, …).
    status_message = Signal(str)
    #: Type badge for the selected node ("Object · 63 keys").
    node_badge = Signal(str)
    #: Emitted on a live-tail append so the status bar can blink.
    activity_pulse = Signal()

    def __init__(self, style: Style, font, zoom_callback=None, parent=None):
        super().__init__(parent)
        self.doc = None
        self.path = None
        self.model = None
        #: Set by the app when the tab was opened from a URL endpoint (kept for
        #: Edit ▸ Copy as cURL).
        self.source_url = None
        #: True when the document opened eagerly (fits in memory) rather than
        #: lazily — gates the "Expand All" actions (View menu + context menu),
        #: which lay out every node and would stall on a huge (lazy) doc.
        self.eager = False
        self.info = ""  # "FORMAT · N MB file · N nodes · N MB index"
        self.load_ms = 0.0  # native parse time, set by the app after open
        self._match_nodes: list = []
        self._match_pos = -1
        self._last_query = None
        self._style = style

        self.tree = DocumentTreeView(style)
        self.tree.zoom_callback = zoom_callback

        # Page 0: the tree. Page 1: CSV table view (lazy).
        self._stack = QStackedLayout(self)
        self._stack.setContentsMargins(0, 0, 0, 0)
        self._stack.addWidget(self.tree)
        self.table = None  # QTableView, created lazily for CSV tabs
        self._table_model = None
        self._table_proxy = None  # RecordFilterProxy over the table
        self.xml_view = None  # QPlainTextEdit, created lazily for XML tabs
        self.diagram = None  # DiagramView (QGraphicsView), created lazily
        self.text_view = None  # QPlainTextEdit for plain-text (.txt/.js) tabs
        self.is_text = False   # True when this tab is a plain-text file
        self._xml_highlighter = None
        self._xml_highlight = False  # syntax highlighting off by default (fast)
        self.filter_text = ""
        self.query_text = ""  # last query run in this tab (for the query bar)
        self._filter_proxy = None  # NodeFilterProxy while a filter is active
        self._current_visible = None  # active filter's visible-node set
        # Live tail state.
        self.following = False
        self._segmented = None       # SegmentedDocument while tailing
        self._tail_committed = 0     # bytes parsed so far
        self._tail_temps = []        # temp chunk files to clean up
        self._tail_timer = QTimer(self)
        self._tail_timer.setInterval(1000)
        self._tail_timer.timeout.connect(self._poll_tail)

        self.tree.setFont(font)

    # -- document binding ----------------------------------------------------

    def load(self, doc, path: str) -> None:
        """Bind (or re-bind, for Reload) a parsed document."""
        self.doc = doc
        self.path = path
        self.model = DocumentModel(doc)
        self.model.set_match_color(self._style.match_bg)
        self._match_nodes = []
        self._match_pos = -1
        self._last_query = None
        self.filter_text = ""
        self.query_text = ""
        self._current_visible = None
        # Reset tail state (Reload restarts from the current end of file).
        self._tail_timer.stop()
        self.following = False
        self._segmented = None
        self._tail_committed = 0
        # Detach views from any old proxies, then dispose them safely
        # (they were parented to this view for lifetime safety).
        if self.table is not None:
            self.table.setModel(None)
        self._stack.setCurrentIndex(0)  # always (re)bind on the tree page
        self.tree.set_search(None)
        self.tree.setModel(self.model)
        # Enable "Expand All" for any eagerly-opened doc (a file that fits in
        # memory, ~<5 GB, or a URL response). Lazy (huge) docs are excluded —
        # a whole-tree expand would stall. The app additionally confirms before
        # expanding a doc with a very large node count.
        self.eager = not _is_lazy(doc)
        _nodes = 0
        if self.eager:
            try:
                _nodes = doc.node_count()
            except Exception:
                _nodes = 0
        self.tree.set_expand_all(self.eager, _nodes)
        self._connect_selection()
        for old in (self._filter_proxy, self._table_proxy):
            if old is not None:
                old.setParent(None)
                old.deleteLater()
        self._filter_proxy = None
        self._table_proxy = None
        self._table_model = None
        # Lazy documents are always treated as "big": their node count is
        # unknown up front, and auto-expanding would force wholesale
        # materialization, defeating on-demand indexing.
        lazy = _is_lazy(doc)
        big = lazy or doc.node_count() > BIG_DOC_NODES
        # The model drills through a single JSON root container, so its
        # items/keys are already the top-level rows — no auto-expand needed
        # (expanding depth 0 would over-open every array element / record).
        # Only XML still expands its root element one level to reveal it.
        if not big and doc.format_name() == "XML":
            self.tree.expandToDepth(0)
        if lazy:
            self.info = (
                f"{doc.format_name()} · {doc.file_bytes() / 1e6:.1f} MB file · "
                f"lazy index (loads on demand)"
            )
        else:
            self.info = (
                f"{doc.format_name()} · {doc.file_bytes() / 1e6:.1f} MB file · "
                f"{doc.node_count():,} nodes · {doc.index_bytes() / 1e6:.1f} MB index"
            )
        # All formats — including CSV/TSV — open in the tree view. The
        # spreadsheet table is available on demand via View ▸ CSV Table View.

    # -- settings fan-out -----------------------------------------------------

    def load_text(self, path: str) -> None:
        """Open a non-structured file (.txt/.js) as read-only plain text — no
        engine index, tree, search, filter, or structural views. Bypasses the
        native parser entirely (those formats have no structure to index)."""
        from PySide6.QtWidgets import QPlainTextEdit

        self.doc = None
        self.model = None
        self.path = path
        self.is_text = True
        self.eager = False
        self._match_nodes = []
        self._match_pos = -1
        self._last_query = None
        self.filter_text = ""
        self.query_text = ""
        self._current_visible = None

        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        with open(path, "rb") as fh:
            raw = fh.read(TEXT_VIEW_MAX_BYTES + 1)
        truncated = len(raw) > TEXT_VIEW_MAX_BYTES
        text = raw[:TEXT_VIEW_MAX_BYTES].decode("utf-8", errors="replace")
        if truncated:
            text += (
                f"\n\n… showing the first {TEXT_VIEW_MAX_BYTES / 1e6:.0f} MB of "
                f"{size / 1e6:.1f} MB — open the file in an editor to see the rest."
            )

        if self.text_view is None:
            self.text_view = QPlainTextEdit()
            self.text_view.setReadOnly(True)
            self.text_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
            self.text_view.setFont(self.tree.font())
            self._stack.addWidget(self.text_view)
        self._style_text_view()
        self.text_view.setPlainText(text)
        self._stack.setCurrentWidget(self.text_view)
        self.info = f"TEXT · {size / 1e6:.1f} MB"

    def _style_text_view(self) -> None:
        if self.text_view is None:
            return
        s = self._style
        self.text_view.setStyleSheet(
            "QPlainTextEdit {"
            f" background: {s.view_bg.name()};"
            f" color: {s.text.name()};"
            f" selection-background-color: {s.selection_bg.name()};"
            f" selection-color: {s.text.name()};"
            " border: none; padding: 4px; }"
        )

    def set_style(self, style: Style) -> None:
        self._style = style
        self.tree.set_style(style)
        if self.model is not None:
            self.model.set_match_color(style.match_bg)
        if self.xml_view is not None:
            self._style_xml_view()
            if self._xml_highlighter is not None:
                self._xml_highlighter.set_style(style)
        if self.diagram is not None:
            self.diagram.apply_style(style)
            if self.diagram_mode():
                self.set_diagram_view(True)  # re-render with the new palette
        if self.text_view is not None:
            self._style_text_view()

    def set_font(self, font) -> None:
        self.tree.setFont(font)
        if self.table is not None:
            self.table.setFont(font)
        if self.xml_view is not None:
            self.xml_view.setFont(font)
        if self.text_view is not None:
            self.text_view.setFont(font)
        if self.model is not None:
            self.model.layoutChanged.emit()
        self.tree.doItemsLayout()

    # -- search ------------------------------------------------------------------

    def query_state(self):
        return self._last_query

    def clear_matches(self) -> None:
        if self.model is not None:
            self.model.set_matches([])
        self.tree.set_search(None)
        self._match_nodes = []
        self._match_pos = -1
        self._last_query = None

    def run_search(self, raw: str, scope: str, case: bool, regex: bool) -> None:
        if self.doc is None or self.model is None:
            self.status_message.emit("Open a file first.")
            return
        pattern = raw if regex else re.escape(raw)
        if not case:
            pattern = "(?i)" + pattern
        try:
            ids = self.doc.search(pattern, scope)  # native, parallel (N5)
        except ValueError as exc:
            self.status_message.emit(f"Invalid regex: {exc}")
            return
        if scope == "all":
            # Container byte windows span whole subtrees; keep only rows
            # where the text actually appears.
            kind = self.doc.kind
            ids = [i for i in ids if kind(i) > 2]  # 0/1/2 = Doc/Obj/Arr
        try:
            flags = 0 if case else re.IGNORECASE
            chip_re = re.compile(raw if regex else re.escape(raw), flags)
        except re.error:
            chip_re = None
        self.tree.set_search(chip_re)
        self.model.set_matches(ids)
        self._match_nodes = list(ids)
        self._match_pos = -1
        self._last_query = (raw, scope, case, regex)
        if self._match_nodes:
            self._reveal_matches()
            self._goto_match(0)
        else:
            self.status_message.emit("No matches")

    def step_match(self, direction: int, raw: str, scope: str, case: bool,
                   regex: bool) -> None:
        if self._last_query != (raw, scope, case, regex) or self.model is None:
            self.run_search(raw, scope, case, regex)
            return
        if not self._match_nodes:
            self.status_message.emit("No matches")
            return
        self._goto_match((self._match_pos + direction) % len(self._match_nodes))

    def _expand_to(self, index) -> None:
        parents = []
        parent = index.parent()
        while parent.isValid():
            parents.append(parent)
            parent = parent.parent()
        for p in reversed(parents):
            self.tree.expand(p)

    def _reveal_matches(self) -> None:
        for node in self._match_nodes[:REVEAL_MATCH_LIMIT]:
            index = self._to_view_index(self.model.index_for_node(node))
            if index.isValid():
                self._expand_to(index)

    def _goto_match(self, pos: int) -> None:
        self._match_pos = pos
        node = self._match_nodes[pos]
        index = self.model.index_for_node(node)
        if not index.isValid():
            self.status_message.emit(f"{len(self._match_nodes):,} matches")
            return
        self.select_index(index)
        extra = (
            f" (first {REVEAL_MATCH_LIMIT:,} revealed)"
            if len(self._match_nodes) > REVEAL_MATCH_LIMIT
            else ""
        )
        self.status_message.emit(
            f"Match {pos + 1:,} of {len(self._match_nodes):,}{extra}"
        )

    # -- filter-proxy index mapping ----------------------------------------------

    def _connect_selection(self) -> None:
        self.tree.selectionModel().currentChanged.connect(
            self._on_tree_current_changed
        )

    def _filtering(self) -> bool:
        """True iff the tree is currently showing the filter proxy."""
        return (
            self._filter_proxy is not None
            and self.tree.model() is self._filter_proxy
        )

    def _to_view_index(self, source_index):
        """Map a DocumentModel index to whatever the tree displays."""
        if self._filtering() and source_index.isValid():
            return self._filter_proxy.mapFromSource(source_index)
        return source_index

    def _to_source_index(self, view_index):
        if self._filtering() and view_index.isValid():
            return self._filter_proxy.mapToSource(view_index)
        return view_index

    def select_index(self, index) -> None:
        """Expand to, select and center a resolved (source) index."""
        target = self._to_view_index(index)
        if not target.isValid():
            return  # e.g. hidden by the active filter
        self._expand_to(target)
        self.tree.setCurrentIndex(target)
        self.tree.scrollTo(target, self.tree.ScrollHint.PositionAtCenter)
        # PositionAtCenter also centers horizontally, which on a wide row
        # (long values → wide column) scrolls the row's start off the left.
        # Keep the row anchored at the left edge so the key/beginning stays
        # visible; the user can scroll right to reach a far-right match.
        self.tree.horizontalScrollBar().setValue(0)

    def _on_tree_current_changed(self, current, _previous) -> None:
        current = self._to_source_index(current)
        if not current.isValid() or self.model is None:
            return
        self.status_message.emit(self.model.path_text(current))
        self.node_badge.emit(self.model.describe(current))

    # -- filter -------------------------------------------------------------------

    def match_nodes(self):
        return list(self._match_nodes)

    def apply_filter(self, text: str) -> None:
        """Engine-backed row filter: native search finds the matching
        nodes; the proxy shows only them plus their ancestors."""
        text = text.strip()
        self.filter_text = text
        if self.doc is None or self.model is None:
            return
        if not text:
            self.clear_filter()
            return
        pattern = "(?i)" + re.escape(text)
        try:
            ids = self.doc.search(pattern, "all")
        except ValueError:
            return
        kind = self.doc.kind
        ids = [i for i in ids if kind(i) > 2]
        if len(ids) > FILTER_MATCH_LIMIT:
            self.status_message.emit(
                f"Filter matches {len(ids):,} rows — type more to narrow it."
            )
            return
        self._show_visible(
            ids, f"Filter: {len(ids):,} matching rows" if ids
            else "Filter: no matches"
        )

    def _show_visible(self, ids, label: str) -> None:
        """Install/reuse the filter proxy to show only ``ids`` + ancestors.
        Shared by the filter box and the query bar."""
        visible = self.model.visible_filter_set(ids)
        self._current_visible = visible
        if self._filter_proxy is None:
            # Parented to the view so Qt owns its lifetime — never let
            # Python GC it while the tree/focus machinery still refers to
            # it (that caused a use-after-free crash on focus).
            self._filter_proxy = NodeFilterProxy(self.model, self)
        # If a filter is already showing, drop the current selection before
        # re-filtering: a row the new filter hides would otherwise leave a
        # stale current index that segfaults on the next focus change.
        if self.tree.model() is self._filter_proxy:
            sm = self.tree.selectionModel()
            if sm is not None:
                sm.clearSelection()
                sm.clearCurrentIndex()
        self._filter_proxy.set_visible(visible)
        if self.tree.model() is not self._filter_proxy:
            self.tree.setModel(self._filter_proxy)
            self._connect_selection()
        if self._table_proxy is not None:
            self._table_proxy.set_visible(visible)  # table view filters too
        if len(visible) <= FILTER_EXPAND_LIMIT:
            self.tree.expandAll()
        else:
            self.tree.expandToDepth(0)
        self.status_message.emit(label)

    def run_query(self, text: str) -> None:
        """Evaluate a JSONPath/XPath query and show its result rows."""
        from openxmljson import query

        if self.model is None or self.doc is None:
            return
        text = text.strip()
        self.query_text = text
        if not text:
            self.clear_filter()
            return
        self.filter_text = ""  # query and filter share the proxy
        try:
            ids = query.evaluate(self.model, text)
        except query.QueryError as exc:
            self.status_message.emit(f"Query error: {exc}")
            return
        if not ids:
            self._show_visible([], "Query: no results")
            return
        self._show_visible(ids, f"Query: {len(ids):,} result(s)")
        first = self._to_view_index(self.model.index_for_node(ids[0]))
        if first.isValid():
            self.tree.setCurrentIndex(first)
            self.tree.scrollTo(first, self.tree.ScrollHint.PositionAtCenter)
            self.tree.horizontalScrollBar().setValue(0)  # keep row start visible

    def clear_filter(self) -> None:
        self.filter_text = ""
        self.query_text = ""
        self._current_visible = None
        if self._table_proxy is not None:
            self._table_proxy.set_visible(None)
        # Swap the tree back to the source model but keep the proxy object
        # alive (it's parented to the view) so nothing dangles.
        if self.tree.model() is not self.model:
            self.tree.setModel(self.model)
            self._connect_selection()
            if (
                self.doc is not None
                and self.doc.format_name() == "XML"
                and self.doc.node_count() <= BIG_DOC_NODES
            ):
                self.tree.expandToDepth(0)

    # -- CSV table mode ----------------------------------------------------------------

    def supports_table(self) -> bool:
        return self.doc is not None and self.doc.format_name() in ("CSV", "TSV")

    def table_mode(self) -> bool:
        return self._stack.currentIndex() == 1

    def set_table_mode(self, enabled: bool) -> None:
        if not self.supports_table():
            return
        if enabled:
            if self.table is None:
                from PySide6.QtWidgets import QTableView

                self.table = QTableView()
                self.table.setAlternatingRowColors(True)
                self.table.horizontalHeader().setStretchLastSection(True)
                self.table.setFont(self.tree.font())
                self._stack.addWidget(self.table)
            if self._table_model is None:
                from openxmljson.csvtable import RecordFilterProxy, RecordTableModel

                # Parent to the view so Qt owns their lifetime.
                self._table_model = RecordTableModel(self.model, self)
                self._table_proxy = RecordFilterProxy(self._table_model, self)
            self._table_proxy.set_visible(self._current_visible)
            self.table.setModel(self._table_proxy)
            self._stack.setCurrentIndex(1)
        else:
            self._stack.setCurrentIndex(0)

    # -- XML source view --------------------------------------------------------------

    def supports_xml_view(self) -> bool:
        return self.doc is not None and self.doc.format_name() == "XML"

    def xml_view_mode(self) -> bool:
        return (
            self.xml_view is not None
            and self._stack.currentWidget() is self.xml_view
        )

    def set_xml_view(self, enabled: bool) -> None:
        """Show the XML markup (read-only) instead of the tree. Pretty-prints
        the parsed markup so minified/one-line documents read cleanly; falls
        back to the raw memory-mapped bytes for very large or lazy documents
        (which are too big to reconstruct)."""
        if not self.supports_xml_view():
            return
        if enabled:
            if self.xml_view is None:
                from PySide6.QtWidgets import QPlainTextEdit

                self.xml_view = QPlainTextEdit()
                self.xml_view.setReadOnly(True)
                self.xml_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
                self.xml_view.setFont(self.tree.font())
                self._style_xml_view()
                self._stack.addWidget(self.xml_view)
            self.xml_view.setPlainText(self._xml_source_text())
            self._apply_xml_highlight()        # colorize only if opted in
            self._stack.setCurrentWidget(self.xml_view)
        else:
            self._stack.setCurrentIndex(0)  # back to the tree

    def _xml_source_text(self) -> str:
        """Formatted (pretty-printed) markup when the document is small
        enough to reconstruct; otherwise the raw bytes (capped)."""
        total = self.doc.file_bytes()
        if (
            self.model is not None
            and not _is_lazy(self.doc)
            and total <= XML_VIEW_MAX_BYTES
        ):
            try:
                from openxmljson import convert

                root = self.doc.root()
                kids = self.doc.child_nodes(root)
                value = [self.model.reconstruct(k) for k in kids]
                value = value[0] if len(value) == 1 else value
                return convert.to_xml(value, pretty=True)
            except (RecursionError, MemoryError, ValueError):
                pass  # fall back to raw
        shown = min(total, XML_VIEW_MAX_BYTES)
        text = self.doc.read_bytes(0, int(shown))
        if total > shown:
            text += (
                f"\n\n… showing the first {shown / 1e6:.0f} MB of "
                f"{total / 1e6:.0f} MB — open the file in an editor to "
                f"see the rest."
            )
        return text

    def supports_xml_highlight(self) -> bool:
        """Highlighting is offered only for eager XML documents under the node
        cap (it colorizes the whole markup, which is slow on huge files)."""
        if not self.supports_xml_view() or not self.eager:
            return False
        try:
            return self.doc.node_count() <= XML_HIGHLIGHT_MAX_NODES
        except Exception:
            return False

    def set_xml_highlight(self, enabled: bool) -> None:
        """Turn XML syntax highlighting on/off (off = plain text, fast). Clamped
        off for large/lazy documents even if the saved preference is on."""
        self._xml_highlight = enabled and self.supports_xml_highlight()
        self._apply_xml_highlight()

    def _apply_xml_highlight(self) -> None:
        """Attach the highlighter when enabled, detach (and clear colors)
        when not. Highlighting a multi-MB document is the slow part, so it
        happens only on explicit opt-in."""
        if self.xml_view is None:
            return
        if self._xml_highlight:
            if self._xml_highlighter is None:
                from openxmljson.xmlhighlight import XmlHighlighter

                # Constructing it rehighlights the current text.
                self._xml_highlighter = XmlHighlighter(
                    self.xml_view.document(), self._style
                )
        elif self._xml_highlighter is not None:
            self._xml_highlighter.setDocument(None)
            self._xml_highlighter = None
            # Drop any formats the highlighter left behind.
            self.xml_view.setPlainText(self.xml_view.toPlainText())

    def _style_xml_view(self) -> None:
        """Theme the XML source view to match the current appearance."""
        s = self._style
        self.xml_view.setStyleSheet(
            "QPlainTextEdit {"
            f" background: {s.view_bg.name()};"
            f" color: {s.text.name()};"
            f" selection-background-color: {s.selection_bg.name()};"
            f" selection-color: {s.text.name()};"
            " border: none; padding: 4px; }"
        )

    # -- flow diagram -----------------------------------------------------------------

    def supports_diagram(self) -> bool:
        """The diagram is offered only for eager (in-memory) documents under a
        node-count cap; huge/lazy documents have too many nodes to draw."""
        if self.doc is None or self.model is None or not self.eager:
            return False
        try:
            return self.doc.node_count() <= DIAGRAM_MAX_NODES
        except Exception:
            return False

    def diagram_mode(self) -> bool:
        return (
            self.diagram is not None
            and self._stack.currentWidget() is self.diagram
        )

    def set_diagram_view(self, enabled: bool) -> None:
        """Show the node/edge flow diagram instead of the tree. Reconstructs
        the document and lays out a card per node; gated by ``supports_diagram``
        so it never runs on a huge/lazy file."""
        if not enabled:
            self._stack.setCurrentIndex(0)  # back to the tree
            return
        if not self.supports_diagram():
            self.status_message.emit(
                "The diagram is available only for smaller documents."
            )
            return
        try:
            root = self.doc.root()
            kids = self.doc.child_nodes(root)
            value = [self.model.reconstruct(k) for k in kids]
            value = value[0] if len(value) == 1 else value
        except (RecursionError, MemoryError, ValueError):
            self.status_message.emit("Couldn't build a diagram from this document.")
            return

        from openxmljson.diagram import build_graph
        from openxmljson.diagramview import DiagramView

        # Root card has no title bar (empty title) — the file name isn't useful
        # inside the diagram; child cards are titled by their key.
        graph = build_graph(value, root_title="")
        if self.diagram is None:
            self.diagram = DiagramView(self)
            self._stack.addWidget(self.diagram)
        self.diagram.apply_style(self._style)
        self.diagram.set_graph(graph)
        self._stack.setCurrentWidget(self.diagram)
        if graph.truncated:
            self.status_message.emit(
                f"Diagram shows the first {len(graph.nodes):,} nodes "
                f"(document is large)."
            )
        else:
            self.status_message.emit(f"Diagram: {len(graph.nodes):,} nodes")

    # -- view actions ----------------------------------------------------------------

    # -- live tail -----------------------------------------------------------

    def can_tail(self) -> bool:
        # Tail follows line-appended JSON/NDJSON/log files. CSV/TSV is
        # excluded — its table view holds a fixed record snapshot. Lazy
        # documents are excluded too: the tail path composes eager chunk
        # Documents and expects the eager index API.
        return (
            self.doc is not None
            and self.path is not None
            and not _is_lazy(self.doc)
            and self.doc.format_name() not in ("CSV", "TSV")
        )

    def set_follow(self, enabled: bool) -> None:
        """Start/stop following the file (tail -f). Tailing parses only
        the appended bytes each tick and appends them as new rows."""
        if enabled and not self.can_tail():
            return
        self.following = enabled
        if enabled:
            try:
                self._tail_committed = os.path.getsize(self.path)
            except OSError:
                self._tail_committed = self.doc.file_bytes()
            # Following clears any active filter/query.
            if self.filter_text or self.query_text:
                self.clear_filter()
            self._tail_timer.start()
            self.status_message.emit("Following… (tail -f)")
        else:
            self._tail_timer.stop()
            self.status_message.emit("Stopped following.")

    def _poll_tail(self) -> None:
        if not self.following or self.path is None or self.model is None:
            return
        try:
            size = os.path.getsize(self.path)
        except OSError:
            return
        if size <= self._tail_committed:
            if size < self._tail_committed:  # truncated/rotated
                self._tail_committed = size
            return
        try:
            with open(self.path, "rb") as fh:
                fh.seek(self._tail_committed)
                appended = fh.read(size - self._tail_committed)
        except OSError:
            return
        from openxmljson.tail import SegmentedDocument, last_complete_line

        keep = last_complete_line(appended)
        if keep == 0:
            return  # only a partial line so far; wait for more
        slice_bytes = appended[:keep]

        from openxmljson import Document

        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".ndjson")
        tmp.write(slice_bytes)
        tmp.close()
        self._tail_temps.append(tmp.name)
        try:
            chunk = Document.open(tmp.name)
        except (IOError, ValueError):
            self._tail_committed += keep  # skip an unparseable slice
            return

        if self._segmented is None:
            self._segmented = SegmentedDocument(self.doc)
        added = self._segmented.append_segment(chunk)
        self.doc = self._segmented
        self.model.tail_extend(self._segmented, added)
        self._tail_committed += keep
        if added:
            self.activity_pulse.emit()
            last = self.model.index(self.model.rowCount() - 1, 0)
            self.tree.scrollTo(last, self.tree.ScrollHint.PositionAtBottom)
            self.tree.horizontalScrollBar().setValue(0)  # keep row start visible
        self.status_message.emit(
            f"Following… {self.model.rowCount():,} records"
        )

    def cleanup(self) -> None:
        """Stop the timer and remove tail temp files (call on close)."""
        self._tail_timer.stop()
        for path in self._tail_temps:
            try:
                os.unlink(path)
            except OSError:
                pass
        self._tail_temps.clear()

    def expand_document(self) -> None:
        """Expand EVERY node in the whole document (not just a subtree). Works
        for a top-level array too — expandAll covers all rows regardless of
        whether there's a single root object. Gated by the app on ``eager``
        plus a node-count confirmation, so it can't stall on a huge doc."""
        if self.doc is not None:
            self.tree.expandAll()

    def expand_children(self) -> None:
        """Expand ONE level — the immediate children of the selected node,
        or the top level when nothing is selected. Never recursive, so it
        stays fast (and safe on multi-GB / lazy documents) unlike a full
        Expand All, which could materialize the whole tree."""
        if self.doc is None:
            return
        index = self.tree.currentIndex()
        if index.isValid():
            self.tree.expand(index)
        else:
            self.tree.expandToDepth(0)
