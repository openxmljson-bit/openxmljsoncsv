"""The Qt application shell (SPEC §11.1).

Main window: menu bar (File / Edit / View / Help), a find bar with case
("Aa") and regex (".*") toggles and scope combo, and a tab strip of open
documents (up to MAX_TABS) — each tab an independent tree with its own
search state. Defaults to the classic dark theme.
"""

from __future__ import annotations

import base64
import os
import shutil
import time
from contextlib import contextmanager
import ssl
import sys
import tempfile
import urllib.request

from PySide6.QtCore import QObject, QRunnable, QSettings, Qt, QThreadPool, Signal
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTabBar,
    QTabWidget,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from openxmljson import Document, LazyDocument, NATIVE_AVAILABLE
from openxmljson.activity import ActivityLights
from openxmljson.docview import DocumentView
from openxmljson.styles import WATERMARK_TEXT, resolve, stylesheet
from openxmljson.tree import EXPAND_ALL_CONFIRM_NODES, EXPAND_ALL_MAX_NODES

FILE_FILTER = (
    "Documents (*.json *.jsonl *.ndjson *.xml *.csv *.tsv *.tab);;All files (*)"
)

SCOPES = ["All", "Keys", "Values", "Attributes"]

#: Tab cap: every open tab keeps its node index resident (32 B/node), so
#: a bound keeps memory predictable even with several huge files open.
MAX_TABS = 12

#: Slack kept free beyond a write's own size, so we don't fill the disk to the
#: last byte (which makes macOS itself unstable).
DISK_HEADROOM_BYTES = 50 * 1024 * 1024
#: Below this free space, warn the user on startup that the disk is nearly full.
LOW_DISK_WARN_BYTES = 500 * 1024 * 1024
#: On startup, delete leftover ``oxj_*`` temp files older than this — cleanup
#: for a previous run that crashed or was force-quit (a normal close already
#: removes them). The age check spares a concurrent instance's fresh temps.
STALE_TEMP_AGE_SECONDS = 24 * 60 * 60


def _fmt_size(n: int) -> str:
    """Human byte size for user-facing messages."""
    if n >= 1024 ** 3:
        return f"{n / 1024 ** 3:.1f} GB"
    return f"{n / 1024 ** 2:.0f} MB"

#: On launch, tabs for files at or below this size are re-opened
#: automatically (fast); larger files are restored as click-to-load
#: placeholders so re-indexing a multi-GB file never blocks startup.
RESTORE_AUTOLOAD_LIMIT = 256 * 1024 * 1024  # 256 MB

#: Whole-document pretty-JSON export guard (bytes).
PRETTY_EXPORT_LIMIT = 64 * 1024 * 1024

MAX_RECENT = 10

#: Keep references to extra windows (File ▸ New Window).
_windows: list = []

#: Default viewer font: a clean proportional face, first installed wins
#: (monospace remains a Change Font… pick away).
DEFAULT_FAMILIES = [
    "Helvetica Neue",
    "SF Pro Text",
    "Segoe UI",
    "Noto Sans",
    "DejaVu Sans",
    "Arial",
]


def _default_font_family() -> str:
    from PySide6.QtGui import QFontDatabase

    installed = set(QFontDatabase.families())
    for family in DEFAULT_FAMILIES:
        if family in installed:
            return family
    return "Sans Serif"


def _fmt_mb(n: int) -> str:
    return f"{n / 1e6:.1f} MB"


def _fmt_size(n: int) -> str:
    if n >= 1_000_000_000:
        return f"{n / 1e9:.1f} GB"
    if n >= 1_000_000:
        return f"{n / 1e6:.0f} MB"
    return f"{n / 1e3:.0f} KB"


class PlaceholderView(QWidget):
    """Stand-in for a restored tab whose file is too large to auto-load on
    launch. It carries a ``path`` (so the session still remembers it) and a
    no-op ``cleanup`` (so tab-close paths work), but indexes nothing until
    the user clicks — keeping startup instant for multi-GB sessions."""

    def __init__(self, path: str, size_bytes: int, on_open, parent=None):
        super().__init__(parent)
        self.path = path
        self._on_open = on_open

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(10)

        name = QLabel(os.path.basename(path))
        name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = name.font()
        font.setPointSize(font.pointSize() + 5)
        font.setBold(True)
        name.setFont(font)

        sub = QLabel(f"{_fmt_size(size_bytes)} · not loaded")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setEnabled(False)  # muted

        button = QPushButton("Open this file")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(lambda: self._on_open(self))

        layout.addStretch(1)
        layout.addWidget(name)
        layout.addWidget(sub)
        layout.addWidget(button, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addStretch(1)

    def cleanup(self) -> None:  # symmetry with DocumentView; nothing to free
        pass


def _total_ram_bytes():
    """Total physical RAM, or None if it can't be determined."""
    try:  # macOS / Linux
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, AttributeError, OSError):
        pass
    try:  # Windows
        import ctypes

        class _MemStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        stat = _MemStatus()
        stat.dwLength = ctypes.sizeof(_MemStatus)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
            return int(stat.ullTotalPhys)
    except Exception:
        pass
    return None


#: Auto lazy-mode policy. An eager open builds a fully-resident index that is
#: roughly file-sized AND keeps the file mmap'd, so the real footprint is about
#: TWICE the file size. To stay safely within RAM (and never wedge the machine
#: into a swap death-spiral), a file opens eagerly only when it is BOTH under an
#: absolute cap and small relative to RAM; anything larger uses on-demand lazy
#: indexing, whose memory scales with the viewed portion, not the file size.
LAZY_ABS_BYTES = 2 * 1024 ** 3       # hard cap: >= 2 GB always opens lazily
LAZY_EAGER_FRACTION = 0.35           # eager only if file <= 35% RAM (~70% at 2x)
#: Fraction of RAM above which the "large file may exhaust memory" warning
#: fires (only reachable when lazy mode is forced off for an oversized file).
LAZY_AUTO_FRACTION = 0.70


class _OpenSignals(QObject):
    #: (path, Document-or-None, error-or-None, load_ms) on the GUI thread.
    done = Signal(str, object, object, float)


class _OpenTask(QRunnable):
    """Parse a document off the GUI thread. The native parser releases the
    GIL, so the main event loop keeps running (activity LEDs animate)."""

    def __init__(self, path: str, signals: _OpenSignals, lazy: bool = False):
        super().__init__()
        self._path = path
        self._signals = signals
        self._lazy = lazy

    def run(self) -> None:
        t0 = time.perf_counter()
        try:
            if self._lazy and LazyDocument is not None:
                doc = LazyDocument.open(self._path)
                # open() only mmaps; the top-level scan would otherwise land
                # on the GUI thread at first paint and freeze it. prime()
                # runs that scan here with the GIL released, so the window
                # keeps animating and the first paint finds it materialized.
                try:
                    doc.prime()
                except Exception:
                    pass  # best-effort; a real error resurfaces on render
            else:
                doc = Document.open(self._path)
            err = None
        except Exception as exc:  # surfaced on the GUI thread
            doc, err = None, exc
        load_ms = (time.perf_counter() - t0) * 1000.0
        self._signals.done.emit(self._path, doc, err, load_ms)


def _clipboard_suffix(text: str):
    """Guess a file extension for pasted content, or None if it doesn't look
    like any supported format — so the caller can say so plainly instead of
    letting the parser fail with a cryptic 'unexpected character' error."""
    stripped = text.lstrip("﻿ \t\r\n")
    if not stripped:
        return None
    if stripped[0] == "<":
        return ".xml"
    if stripped[0] in ("{", "["):
        return ".json"
    first = stripped.splitlines()[0]
    if "\t" in first:
        return ".tsv"
    if any(d in first for d in (",", ";", "|")):
        return ".csv"
    return None  # plain text — not a structured document


class OpenUrlDialog(QDialog):
    """Open URL… with optional authentication (None / Basic / Bearer
    Token / API Key) and a remember-credential option."""

    AUTH_MODES = ["None", "Basic", "Bearer Token", "API Key"]

    def __init__(self, parent, settings: QSettings):
        super().__init__(parent)
        self._settings = settings
        self.setWindowTitle("Open URL")
        self.setModal(True)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )

        self.url_edit = QLineEdit()
        self.url_edit.setMinimumWidth(480)
        self.url_edit.setPlaceholderText("https://example.com/data.json")
        form.addRow("URL:", self.url_edit)

        self.auth_combo = QComboBox()
        self.auth_combo.addItems(self.AUTH_MODES)
        self.auth_combo.currentIndexChanged.connect(self._auth_changed)
        form.addRow("Authentication:", self.auth_combo)

        self.user_edit = QLineEdit()
        form.addRow("Username:", self.user_edit)

        self.pass_edit = QLineEdit()
        self.pass_edit.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Password:", self.pass_edit)

        self.token_edit = QLineEdit()
        self.token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Token:", self.token_edit)

        # API Key mode: the header is sent verbatim — no "Bearer " prefix
        # (e.g. Unbxd:  Authorization: <api-key>).
        self.header_edit = QLineEdit()
        self.header_edit.setText("Authorization")
        form.addRow("Header:", self.header_edit)

        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Key:", self.key_edit)

        layout.addLayout(form)
        self._form = form

        self.remember_check = QCheckBox("Remember credential")
        self.remember_check.setChecked(True)
        layout.addWidget(self.remember_check)

        buttons = QDialogButtonBox()
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        open_button = buttons.addButton(
            "Open", QDialogButtonBox.ButtonRole.AcceptRole
        )
        open_button.setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._restore()
        self._auth_changed(self.auth_combo.currentIndex())

    def _set_row_visible(self, field, visible: bool) -> None:
        field.setVisible(visible)
        label = self._form.labelForField(field)
        if label is not None:
            label.setVisible(visible)

    def _auth_changed(self, index: int) -> None:
        mode = self.AUTH_MODES[index]
        self._set_row_visible(self.user_edit, mode == "Basic")
        self._set_row_visible(self.pass_edit, mode == "Basic")
        self._set_row_visible(self.token_edit, mode == "Bearer Token")
        self._set_row_visible(self.header_edit, mode == "API Key")
        self._set_row_visible(self.key_edit, mode == "API Key")
        self.remember_check.setVisible(mode != "None")
        self.adjustSize()

    def _restore(self) -> None:
        mode = str(self._settings.value("openurl/auth", "None"))
        if mode in self.AUTH_MODES:
            self.auth_combo.setCurrentIndex(self.AUTH_MODES.index(mode))
        self.url_edit.setText(str(self._settings.value("openurl/last_url", "")))
        self.user_edit.setText(str(self._settings.value("openurl/user", "")))
        header = str(self._settings.value("openurl/header", "Authorization"))
        self.header_edit.setText(header or "Authorization")
        for key, edit in (("openurl/pass", self.pass_edit),
                          ("openurl/token", self.token_edit),
                          ("openurl/key", self.key_edit)):
            stored = str(self._settings.value(key, ""))
            if stored:
                try:
                    edit.setText(base64.b64decode(stored).decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    pass

    def save_or_clear(self) -> None:
        """Persist the credential when asked to; wipe it otherwise.
        (Stored base64-obfuscated in app settings — not encrypted.)"""
        self._settings.setValue("openurl/last_url", self.url_edit.text())
        if self.remember_check.isChecked() and self.auth_mode() != "None":
            self._settings.setValue("openurl/auth", self.auth_mode())
            self._settings.setValue("openurl/user", self.user_edit.text())
            self._settings.setValue("openurl/header", self.header_edit.text())
            self._settings.setValue(
                "openurl/pass",
                base64.b64encode(self.pass_edit.text().encode()).decode(),
            )
            self._settings.setValue(
                "openurl/token",
                base64.b64encode(self.token_edit.text().encode()).decode(),
            )
            self._settings.setValue(
                "openurl/key",
                base64.b64encode(self.key_edit.text().encode()).decode(),
            )
        else:
            for key in ("openurl/auth", "openurl/user", "openurl/pass",
                        "openurl/token", "openurl/header", "openurl/key"):
                self._settings.remove(key)

    def auth_mode(self) -> str:
        return self.AUTH_MODES[self.auth_combo.currentIndex()]

    def auth_header_pair(self):
        """(header-name, value) to send, or None."""
        mode = self.auth_mode()
        if mode == "Basic":
            raw = f"{self.user_edit.text()}:{self.pass_edit.text()}"
            value = "Basic " + base64.b64encode(raw.encode("utf-8")).decode()
            return ("Authorization", value)
        if mode == "Bearer Token":
            token = self.token_edit.text().strip()
            return ("Authorization", f"Bearer {token}") if token else None
        if mode == "API Key":
            name = self.header_edit.text().strip() or "Authorization"
            key = self.key_edit.text().strip()
            return (name, key) if key else None
        return None

    def url(self) -> str:
        url = self.url_edit.text().strip()
        if url and "://" not in url:
            url = "https://" + url
        return url


class TightTabBar(QTabBar):
    """Trims the platform style's generous tab width so the close button
    sits right next to the filename."""

    TRIM = 18  # px of style-imposed spacing to reclaim

    def tabSizeHint(self, index: int):  # noqa: N802
        size = super().tabSizeHint(index)
        size.setWidth(max(size.width() - self.TRIM, 60))
        return size


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("OPENXMLJSON")
        self.resize(1000, 700)
        self._settings = QSettings("openxmljson", "viewer")

        appearance = str(self._settings.value("appearance", "dark"))
        self._style = resolve(appearance)

        # Font / zoom state (persisted). Defaults: Helvetica Neue (or the
        # closest installed) at 15 pt. Keys versioned so the new defaults
        # apply; Change Font… / zoom still override and persist.
        self._default_font_size = 15.0
        self._font_family = str(
            self._settings.value("font_family_v3", _default_font_family())
        )
        self._font_size = float(
            self._settings.value("font_size_v4", self._default_font_size)
        )

        # Tabbed documents.
        self.tabs = QTabWidget()
        # Compact tab bar: trims the platform style's extra width so the
        # ✕ hugs the filename.
        self.tabs.setTabBar(TightTabBar())
        # Close buttons are custom themed QToolButtons installed per tab
        # (the native ones ignore stylesheets on macOS and vanish in the
        # light theme), so built-in closable tabs stay off.
        self.tabs.setTabsClosable(False)
        self.tabs.setMovable(True)
        from PySide6.QtCore import QSize

        self.tabs.setIconSize(QSize(18, 10))  # dot pixmap rendered 1:1
        # Tabs hug their content instead of stretching to fill the bar
        # (stretching is what pushed the ✕ far away from the filename).
        self.tabs.tabBar().setExpanding(False)
        # (documentMode suppresses tab borders/radii on some platforms —
        # keep it off so the styled rounded tabs render.)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.tabs.currentChanged.connect(self._on_tab_changed)
        # Right-click a tab for close-others / left / right.
        tab_bar = self.tabs.tabBar()
        tab_bar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        tab_bar.customContextMenuRequested.connect(self._tab_context_menu)
        # Dragging tabs around changes indices — refresh the dot/✕ marks.
        self.tabs.tabBar().tabMoved.connect(
            lambda *_: self._update_tab_marks()
        )

        # Central area: welcome screen when empty, tabs when open.
        from PySide6.QtWidgets import QStackedWidget

        from openxmljson.welcome import WelcomeWidget

        self._welcome = WelcomeWidget(self._style, self)
        self._central = QStackedWidget()
        self._central.addWidget(self._welcome)  # index 0
        self._central.addWidget(self.tabs)       # index 1
        self.setCentralWidget(self._central)

        self._build_find_bar()
        self._build_jq_bar()
        self._build_menus(appearance)

        # Permanent widgets (right group, added left-to-right). The load
        # badge goes first so it sits toward the center; permanent widgets
        # are NOT hidden by transient showMessage() calls, unlike widgets
        # added with addWidget().
        self._load_label = QLabel("")
        self.statusBar().addPermanentWidget(self._load_label)
        self._type_label = QLabel("")
        self.statusBar().addPermanentWidget(self._type_label)
        self._name_label = QLabel(WATERMARK_TEXT)
        self.statusBar().addPermanentWidget(self._name_label)
        self._lights = ActivityLights(self._style)
        self.statusBar().addPermanentWidget(self._lights)
        # Async open state.
        self._pending_opens = set()      # paths currently being parsed
        self._pending_url = {}           # path -> source URL (async open assoc)
        self._temp_files = set()         # app-created temp files, deleted on close
        self._open_signals = []          # keep signal objects alive
        self._restore_current_path = None  # tab to select once restored

        # Watch open files for on-disk changes.
        from PySide6.QtCore import QFileSystemWatcher

        self._watcher = QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(self._on_file_changed)

        # Drag & drop files onto the window.
        self.setAcceptDrops(True)

        self._apply_style()
        self._welcome.set_mode(str(self._settings.value("welcome_mode", "center")))
        self._update_central()  # show the welcome screen until a file opens

        self.statusBar().showMessage("Open a JSON / XML / CSV file to begin.")
        # Nudge if the disk is already nearly full (shown after the intro), and
        # sweep temp files a crashed/force-quit prior run left behind.
        from PySide6.QtCore import QTimer

        QTimer.singleShot(1500, self.check_disk_space)
        QTimer.singleShot(2500, self._sweep_stale_temps)

    # -- tab plumbing ----------------------------------------------------------

    def current_view(self):
        widget = self.tabs.currentWidget()
        return widget if isinstance(widget, DocumentView) else None

    def _views(self):
        return [
            self.tabs.widget(i)
            for i in range(self.tabs.count())
            if isinstance(self.tabs.widget(i), DocumentView)
        ]

    def _current_font(self):
        from PySide6.QtGui import QFont

        font = QFont(self._font_family)
        font.setPointSizeF(self._font_size)
        return font

    def _new_view(self) -> DocumentView:
        view = DocumentView(
            self._style,
            font=self._current_font(),
            zoom_callback=self.zoom_by,
        )
        view.status_message.connect(self.statusBar().showMessage)
        view.node_badge.connect(self._type_label.setText)
        view.activity_pulse.connect(self._lights.pulse)
        view.set_xml_highlight(
            str(self._settings.value("xml_highlight", "false")).lower() == "true"
        )
        return view

    @contextmanager
    def _busy(self):
        """Light the activity LEDs for the duration of an operation."""
        self._lights.begin()
        try:
            yield
        finally:
            self._lights.end()

    # -- drag & drop ---------------------------------------------------------

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        for url in event.mimeData().urls():
            if url.isLocalFile():
                self.open_path(url.toLocalFile())
        event.acceptProposedAction()

    # -- file watching -----------------------------------------------------------

    def _on_file_changed(self, path: str) -> None:
        # Some editors replace the file, which drops the watch — re-add.
        if os.path.exists(path) and path not in self._watcher.files():
            self._watcher.addPath(path)
        for i in range(self.tabs.count()):
            widget = self.tabs.widget(i)
            if isinstance(widget, DocumentView) and widget.path == path:
                base = os.path.basename(path)
                self.tabs.setTabText(i, f"{base} ●")
                self.tabs.setTabToolTip(
                    i, f"{path}\nChanged on disk — File ▸ Reload (F5)"
                )
        self.statusBar().showMessage(
            f"{os.path.basename(path)} changed on disk — File ▸ Reload (F5)",
            8000,
        )

    def _watch(self, path: str) -> None:
        if os.path.exists(path):
            self._watcher.addPath(path)

    def _unwatch_if_unused(self, path: str) -> None:
        for view in self._views():
            if view.path == path:
                return
        self._watcher.removePath(path)

    def _clear_dirty_mark(self, view) -> None:
        index = self.tabs.indexOf(view)
        if index >= 0 and view.path:
            self.tabs.setTabText(index, os.path.basename(view.path))
            self.tabs.setTabToolTip(index, view.path)

    # -- session restore ------------------------------------------------------------

    def closeEvent(self, event) -> None:  # noqa: N802
        # Save every tab that has a path — including not-yet-loaded
        # placeholders — so the session survives even if a big file was
        # never opened this run.
        # Persist only real files — transient temps (URL/clipboard/beautify
        # output) are deleted below, so restoring their paths would be futile.
        paths = [
            self.tabs.widget(i).path
            for i in range(self.tabs.count())
            if getattr(self.tabs.widget(i), "path", None)
            and not self._is_temp_path(self.tabs.widget(i).path)
        ]
        self._settings.setValue("session/tabs", paths)
        self._settings.setValue("session/current", self.tabs.currentIndex())
        for view in self._views():
            view.cleanup()  # stop tail timers, remove tail temp chunks
        # Remove the temp files this app created (URL responses, clipboard
        # pastes, Beautify/Minify output). Done at app close — not per-tab —
        # so a Duplicate Tab sharing the same temp isn't pulled out from under.
        for path in self._temp_files:
            try:
                os.unlink(path)
            except OSError:
                pass
        self._temp_files.clear()
        super().closeEvent(event)

    def restore_session(self) -> None:
        value = self._settings.value("session/tabs", [])
        paths = [value] if isinstance(value, str) else list(value or [])
        existing = [p for p in paths if p and os.path.exists(p)]
        try:
            current = int(self._settings.value("session/current", 0))
        except (TypeError, ValueError):
            current = 0
        current_path = paths[current] if 0 <= current < len(paths) else None
        for path in existing:
            try:
                size = os.path.getsize(path)
            except OSError:
                size = 0
            if size > RESTORE_AUTOLOAD_LIMIT:
                # Too big to re-index on launch — restore as a placeholder
                # the user can open on demand.
                self._add_placeholder(path, size)
            else:
                # Opens are async — remember which tab to select and honor
                # it when that document finishes loading (see _on_open_done).
                if path == current_path:
                    self._restore_current_path = path
                self.open_path(path)
        # If the previously-current tab is a (synchronous) placeholder,
        # select it now.
        if current_path in existing:
            for i in range(self.tabs.count()):
                widget = self.tabs.widget(i)
                if isinstance(widget, PlaceholderView) and widget.path == current_path:
                    self.tabs.setCurrentIndex(i)
                    break

    def _add_placeholder(self, path: str, size: int) -> int:
        view = PlaceholderView(path, size, self._load_placeholder)
        index = self.tabs.addTab(view, os.path.basename(path))
        self.tabs.setTabToolTip(index, path)
        self.tabs.tabBar().setTabButton(
            index, QTabBar.ButtonPosition.RightSide,
            self._make_close_button(view),
        )
        self._update_central()
        return index

    def _load_placeholder(self, placeholder: "PlaceholderView") -> None:
        """User asked to open a restored large file: drop the placeholder
        and kick off the normal (async) load."""
        path = placeholder.path
        index = self.tabs.indexOf(placeholder)
        if index != -1:
            self.tabs.removeTab(index)
            placeholder.deleteLater()
        self.open_path(path)

    def _update_central(self) -> None:
        """Show the welcome screen when no tabs are open, else the tabs."""
        if self.tabs.count() == 0:
            self._welcome.refresh()
            self._central.setCurrentWidget(self._welcome)
        else:
            self._central.setCurrentWidget(self.tabs)

    def close_tab(self, index: int) -> None:
        widget = self.tabs.widget(index)
        self.tabs.removeTab(index)
        if widget is not None:
            path = getattr(widget, "path", None)
            if hasattr(widget, "cleanup"):
                widget.cleanup()  # stop tail timer, remove temp chunks
            widget.deleteLater()
            if path:
                self._unwatch_if_unused(path)
        self._update_central()
        if self.tabs.count() == 0:
            self.setWindowTitle("OPENXMLJSON")
            self.statusBar().showMessage(
                "Open a JSON / XML / CSV file to begin."
            )

    def close_current_tab(self) -> None:
        if self.tabs.count():
            self.close_tab(self.tabs.currentIndex())

    def close_all_tabs(self) -> None:
        while self.tabs.count():
            self.close_tab(0)

    def _close_indices(self, indices) -> None:
        # Remove from the right so earlier indices stay valid.
        for i in sorted(indices, reverse=True):
            self.close_tab(i)

    def _tab_context_menu(self, pos) -> None:
        from PySide6.QtWidgets import QMenu

        bar = self.tabs.tabBar()
        index = bar.tabAt(pos)
        if index < 0:
            return
        count = self.tabs.count()
        menu = QMenu(self)

        def add(label, fn, enabled=True):
            act = QAction(label, menu)
            act.triggered.connect(fn)
            act.setEnabled(enabled)
            menu.addAction(act)

        can_dupe = bool(getattr(self.tabs.widget(index), "path", None))
        add("Duplicate Tab", lambda: self.duplicate_tab(index), enabled=can_dupe)
        menu.addSeparator()
        add("Close Tab", lambda: self.close_tab(index))
        add(
            "Close Other Tabs",
            lambda: self._close_indices(
                [i for i in range(count) if i != index]
            ),
            enabled=count > 1,
        )
        add(
            "Close Tabs to the Left",
            lambda: self._close_indices(list(range(index))),
            enabled=index > 0,
        )
        add(
            "Close Tabs to the Right",
            lambda: self._close_indices(range(index + 1, count)),
            enabled=index < count - 1,
        )
        menu.exec(bar.mapToGlobal(pos))

    def duplicate_current_tab(self) -> None:
        """File ▸ Duplicate Tab: open another independent view of the current
        document (same file, fresh tab)."""
        self.duplicate_tab(self.tabs.currentIndex())

    def duplicate_tab(self, index: int) -> None:
        """Open the document shown in tab ``index`` again in a new tab. Each
        tab owns its own mmap + index, so this is a genuinely independent
        second view (own expansion, scroll, search)."""
        widget = self.tabs.widget(index) if 0 <= index < self.tabs.count() else None
        if not isinstance(widget, DocumentView) or not widget.path:
            self.statusBar().showMessage("This tab can't be duplicated.")
            return
        if not os.path.exists(widget.path):
            self.statusBar().showMessage(
                "Can't duplicate: the file no longer exists on disk."
            )
            return
        # Carry over URL origin so the copy keeps 'Expand All' / Copy as cURL.
        src_url = getattr(widget, "source_url", None)
        if src_url:
            self._pending_url[widget.path] = (
                src_url, getattr(widget, "curl_command", "") or ""
            )
        self.open_path(widget.path, allow_duplicate=True)

    def _close_button_css(self) -> str:
        s = self._style
        return (
            "QToolButton { border: none; background: transparent;"
            f" color: {s.text.name()};"
            " font-size: 14px; font-weight: bold; padding: 1px; }"
            "QToolButton:hover {"
            f" background: {s.selection_bg.name()};"
            f" color: {s.key.name()};"
            " border-radius: 10px; }"
        )

    def _make_close_button(self, view):
        """A themed ✕ wrapped in a container whose layout margins give it
        breathing room from the tab edge (the tab bar itself ignores
        widget margins)."""
        from PySide6.QtWidgets import QHBoxLayout, QWidget

        button = QToolButton()
        button.setText("✕")
        button.setFixedSize(20, 20)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setToolTip("Close tab")
        button.setStyleSheet(self._close_button_css())
        button.clicked.connect(lambda: self._close_view(view))
        wrapper = QWidget()
        layout = QHBoxLayout(wrapper)
        layout.setContentsMargins(2, 2, 8, 2)  # left, top, right, bottom
        layout.setSpacing(0)
        layout.addWidget(button)
        return wrapper

    def _close_view(self, view) -> None:
        index = self.tabs.indexOf(view)
        if index >= 0:
            self.close_tab(index)

    def _tab_dot_icon(self):
        """A small accent-colored dot marking the active tab. The pixmap
        carries transparent padding on its right, which reads as spacing
        between the dot and the filename (icon-text gap isn't directly
        styleable)."""
        from PySide6.QtGui import QIcon, QPainter, QPixmap

        pm = QPixmap(18, 10)  # 8px dot + 8px transparent right padding
        pm.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pm)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._style.placeholder)
        painter.drawEllipse(1, 1, 8, 8)
        painter.end()
        return QIcon(pm)

    def _update_tab_marks(self) -> None:
        from PySide6.QtGui import QIcon
        from PySide6.QtWidgets import QTabBar

        dot = self._tab_dot_icon()
        empty = QIcon()
        current = self.tabs.currentIndex()
        bar = self.tabs.tabBar()
        for i in range(self.tabs.count()):
            self.tabs.setTabIcon(i, dot if i == current else empty)
            # Close button only on the active tab.
            for side in (
                QTabBar.ButtonPosition.RightSide,
                QTabBar.ButtonPosition.LeftSide,
            ):
                button = bar.tabButton(i, side)
                if button is not None:
                    button.setVisible(i == current)

    def _on_tab_changed(self, _index: int) -> None:
        self._update_tab_marks()
        self._type_label.setText("")
        view = self.current_view()
        # Reflect the tab's own filter and table-mode state in the chrome.
        if hasattr(self, "filter_edit"):
            self.filter_edit.blockSignals(True)
            # The box holds whichever the tab last ran (a query or a filter).
            self.filter_edit.setText(
                (view.query_text or view.filter_text) if view else ""
            )
            self.filter_edit.blockSignals(False)
        # With no document open, clear the find box too (the filter/query
        # boxes above already reset to empty).
        if view is None and hasattr(self, "search_edit"):
            self.search_edit.blockSignals(True)
            self.search_edit.clear()
            self.search_edit.blockSignals(False)
            self.statusBar().clearMessage()
        if hasattr(self, "_follow_action"):
            self._follow_action.blockSignals(True)
            self._follow_action.setChecked(view.following if view else False)
            self._follow_action.setEnabled(view is not None and view.can_tail())
            self._follow_action.blockSignals(False)
        if hasattr(self, "_expand_doc_action"):
            can_expand = False
            if view is not None and getattr(view, "eager", False):
                try:
                    can_expand = view.doc.node_count() <= EXPAND_ALL_MAX_NODES
                except Exception:
                    can_expand = True
            self._expand_doc_action.setEnabled(can_expand)
        self._sync_table_controls()
        self._sync_xml_controls()
        self._sync_diagram_controls()
        self._sync_jq_controls()
        self._sync_tools_controls()
        self._sync_scope_combo()
        if view is None or view.path is None:
            self.setWindowTitle("OPENXMLJSON")
            self._load_label.setText("")
            return
        self.setWindowTitle(f"{os.path.basename(view.path)} — OPENXMLJSON")
        self.statusBar().showMessage(view.info)
        self._load_label.setText(
            f"Load: {view.load_ms:.0f}ms" if view.load_ms else ""
        )

    def _apply_filter_now(self) -> None:
        """The single filter box. It routes by what you type:
          - key:value        -> field equals value (e.g. productType:ring)
          - $.path / //xpath  -> JSONPath / XPath query (incl. [?(@.k=="v")])
          - anything else     -> plain substring filter
        All collapse the tree to matches + ancestors."""
        from openxmljson import query

        view = self.current_view()
        if view is None or view.model is None:
            return
        text = self.filter_edit.text().strip()
        is_xml = view.model.format() == "XML"
        with self._busy():
            if not text:
                view.clear_filter()
            elif query.is_structured(text, is_xml):
                view.run_query(text)
            else:
                view.apply_filter(text)

    def _on_filter_text_changed(self, text: str) -> None:
        # The filter only runs on Enter (see returnPressed), but emptying the
        # box should drop the filter right away so the full tree comes back
        # without a second keypress.
        if not text.strip():
            view = self.current_view()
            if view is not None:
                view.clear_filter()

    # -- find bar -----------------------------------------------------------------

    def _build_find_bar(self) -> None:
        bar = QToolBar("Find")
        bar.setMovable(False)
        self.addToolBar(bar)

        self.case_button = QToolButton()
        self.case_button.setText("Aa")
        self.case_button.setCheckable(True)
        self.case_button.setChecked(False)  # case-insensitive by default
        self.case_button.setToolTip("Match case")
        bar.addWidget(self.case_button)

        self.regex_button = QToolButton()
        self.regex_button.setText(".*")
        self.regex_button.setCheckable(True)
        self.regex_button.setChecked(True)  # regular expression by default
        self.regex_button.setToolTip("Regular expression")
        bar.addWidget(self.regex_button)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Find")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setMinimumWidth(220)
        self.search_edit.setMaximumWidth(340)
        self.search_edit.returnPressed.connect(self.find_next)
        self.search_edit.textChanged.connect(self._on_search_text_changed)
        bar.addWidget(self.search_edit)

        self.scope_combo = QComboBox()
        self.scope_combo.addItems(SCOPES)
        bar.addWidget(self.scope_combo)

        find_button = QPushButton("Find")
        find_button.clicked.connect(self.run_search)
        bar.addWidget(find_button)

        prev_button = QToolButton()
        prev_button.setText("▲")
        prev_button.setToolTip("Previous match (Shift+F3)")
        prev_button.clicked.connect(self.find_prev)
        bar.addWidget(prev_button)

        next_button = QToolButton()
        next_button.setText("▼")
        next_button.setToolTip("Next match (F3 / Enter)")
        next_button.clicked.connect(self.find_next)
        bar.addWidget(next_button)

        bar.addSeparator()

        # Filter: hides everything except matching rows (+ ancestors). Plain
        # text filters by substring; a leading $ or / runs a JSONPath/XPath
        # query (merged single box — no separate query bar).
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Filter rows / key:value / $.path")
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.setMinimumWidth(200)
        self.filter_edit.setMaximumWidth(320)
        self.filter_edit.setToolTip(
            "Show only matching rows (and their parents). Enter:\n"
            "  • plain text   — rows containing that text\n"
            "  • key:value    — field equals value, e.g. productType:ring\n"
            "  • $.path / //xpath — JSONPath / XPath, e.g. "
            "$.products[?(@.productType==\"ring\")]  or  //item/@id\n"
            "Press Enter to apply."
        )
        # Apply on Enter (like Find) rather than live on every keystroke —
        # filtering a large document is expensive, so it shouldn't run mid-type.
        self.filter_edit.returnPressed.connect(self._apply_filter_now)
        # Still clear the filter the moment the box is emptied (e.g. the clear
        # button), so a filter is never left stuck when there's no text.
        self.filter_edit.textChanged.connect(self._on_filter_text_changed)
        bar.addWidget(self.filter_edit)

        # Table View: a quick toggle beside the filter box, shown only for
        # CSV/TSV tabs (mirrors View ▸ CSV Table View).
        self._table_button = QToolButton()
        self._table_button.setText("Table View")
        self._table_button.setCheckable(True)
        self._table_button.setToolTip("Show CSV/TSV as a spreadsheet grid")
        self._table_button.toggled.connect(self.set_table_mode)
        # addWidget wraps the button in a QWidgetAction; toggle THAT action's
        # visibility (toggling the inner widget doesn't update the toolbar).
        self._table_button_action = bar.addWidget(self._table_button)
        self._table_button_action.setVisible(False)

        # XML Source view toggle — shown only for XML tabs (mirrors the CSV
        # Table View button).
        self._xml_button = QToolButton()
        self._xml_button.setText("XML View")
        self._xml_button.setCheckable(True)
        self._xml_button.setToolTip("Show the raw XML markup")
        self._xml_button.toggled.connect(self.set_xml_view)
        self._xml_button_action = bar.addWidget(self._xml_button)
        self._xml_button_action.setVisible(False)

        # Flow Diagram toggle — shown only for documents small enough to draw
        # (mirrors the Table / XML view buttons).
        self._diagram_button = QToolButton()
        self._diagram_button.setText("Diagram")
        self._diagram_button.setCheckable(True)
        self._diagram_button.setToolTip("Show the data as a flow diagram")
        self._diagram_button.toggled.connect(self.set_diagram_view)
        self._diagram_button_action = bar.addWidget(self._diagram_button)
        self._diagram_button_action.setVisible(False)

    def _build_jq_bar(self) -> None:
        """A jq filter bar on its own row below the Find bar. Shown only for
        small/medium eager documents (see _sync_jq_controls)."""
        self.addToolBarBreak()
        bar = QToolBar("jq")
        bar.setMovable(False)
        self.addToolBar(bar)
        self._jq_toolbar = bar

        bar.addWidget(QLabel(" jq "))
        self.jq_edit = QLineEdit()
        self.jq_edit.setPlaceholderText("jq filter — e.g. .fruits | map(.name)")
        self.jq_edit.setClearButtonEnabled(True)
        self.jq_edit.setMinimumWidth(360)
        self.jq_edit.setToolTip(
            "Run a jq filter over the document; the result opens in a new tab.\n"
            "Press Enter to run. e.g.  .items | sort_by(.price)"
        )
        self.jq_edit.returnPressed.connect(self._run_jq_now)
        bar.addWidget(self.jq_edit)

        jq_button = QPushButton("Run")
        jq_button.setToolTip("Run the jq filter")
        jq_button.clicked.connect(self._run_jq_now)
        bar.addWidget(jq_button)
        bar.setVisible(False)   # revealed by _sync_jq_controls per document

    # -- menus --------------------------------------------------------------------

    def _action(self, menu, label, slot, shortcut=None) -> QAction:
        action = QAction(label, self)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(slot)
        menu.addAction(action)
        return action

    def _build_menus(self, appearance: str) -> None:
        menubar = self.menuBar()

        # File --------------------------------------------------------------
        file_menu = menubar.addMenu("&File")
        self._action(file_menu, "New Window", self.new_window, "Ctrl+Shift+N")
        file_menu.addSeparator()
        self._action(file_menu, "Open…", self.open_dialog,
                     QKeySequence.StandardKey.Open)
        self._action(file_menu, "Open URL…", self.open_url, "Alt+Shift+O")
        self._action(file_menu, "Open Clipboard", self.open_clipboard,
                     "Ctrl+Shift+V")
        self._recent_menu = file_menu.addMenu("Open Recent")
        self._recent_menu.aboutToShow.connect(self._populate_recent)
        file_menu.addSeparator()
        self._action(file_menu, "Reload", self.reload_file, "F5")
        file_menu.addSeparator()
        export_menu = file_menu.addMenu("Export Data As")
        self._action(export_menu, "Raw Copy…", self.export_raw_copy)
        self._action(export_menu, "Pretty JSON…", self.export_pretty_json)
        self._action(export_menu, "XML…", lambda: self.export_converted("xml"))
        self._action(export_menu, "CSV…", lambda: self.export_converted("csv"))
        export_menu.addSeparator()
        self._action(export_menu, "Search Matches as JSON…",
                     lambda: self.export_matches("json"))
        self._action(export_menu, "Search Matches as CSV…",
                     lambda: self.export_matches("csv"))
        sel_menu = file_menu.addMenu("Export Selection As")
        self._action(sel_menu, "JSON…", lambda: self._export_selection(True))
        self._action(sel_menu, "Text…", lambda: self._export_selection(False))
        file_menu.addSeparator()
        self._action(file_menu, "Duplicate Tab", self.duplicate_current_tab,
                     "Ctrl+Shift+D")
        self._action(file_menu, "Close Tab", self.close_current_tab,
                     QKeySequence.StandardKey.Close)
        self._action(file_menu, "Close All Tabs", self.close_all_tabs,
                     "Ctrl+Alt+W")
        self._action(file_menu, "Close Window", self.close, "Ctrl+Shift+W")

        # Edit ---------------------------------------------------------------
        edit_menu = menubar.addMenu("&Edit")
        self._action(edit_menu, "Find", self.focus_find,
                     QKeySequence.StandardKey.Find)
        self._action(edit_menu, "Find Next", self.find_next, "F3")
        self._action(edit_menu, "Find Previous", self.find_prev, "Shift+F3")
        edit_menu.addSeparator()
        self._action(edit_menu, "Jump to Path…", self.jump_to_path, "Ctrl+L")
        edit_menu.addSeparator()
        self._action(edit_menu, "Copy Row", self.copy_row,
                     QKeySequence.StandardKey.Copy)
        self._action(edit_menu, "Copy as cURL", self.copy_as_curl)

        # Bookmarks -----------------------------------------------------------
        self._bookmarks_menu = menubar.addMenu("&Bookmarks")
        self._bookmarks_menu.aboutToShow.connect(self._populate_bookmarks)
        # Persistent action so Ctrl+D works before the menu is ever opened.
        self._add_bookmark_action = QAction("Add Bookmark", self)
        self._add_bookmark_action.setShortcut(QKeySequence("Ctrl+D"))
        self._add_bookmark_action.triggered.connect(self.add_bookmark)
        self.addAction(self._add_bookmark_action)

        # Tools ---------------------------------------------------------------
        tools_menu = menubar.addMenu("&Tools")
        # Beautify/Minify only make sense for JSON and XML — enabled per the
        # active tab's format (see _sync_tools_controls).
        self._beautify_action = self._action(
            tools_menu, "Beautify (Pretty-Print) → New Tab",
            lambda *_: self.reformat_document(True))
        self._minify_action = self._action(
            tools_menu, "Minify (Compact) → New Tab",
            lambda *_: self.reformat_document(False))
        tools_menu.addSeparator()
        self._action(tools_menu, "Generate JSON Schema → New Tab",
                     self.generate_schema)
        self._action(tools_menu, "Validate Against JSON Schema…",
                     self.validate_against_schema)
        self._action(tools_menu, "Compare With Open Tab…",
                     self.compare_documents)

        # View ---------------------------------------------------------------
        view_menu = menubar.addMenu("&View")
        self._action(view_menu, "Expand Children", self.expand_children,
                     "Ctrl+Shift+E")
        # Whole-document expand — enabled for eager (in-memory) docs, gated by
        # node count (see _on_tab_changed / expand_document). Labeled "Expand
        # All" to match the tree context-menu action.
        self._expand_doc_action = self._action(
            view_menu, "Expand All", self.expand_document,
            "Ctrl+Shift+X",
        )
        self._expand_doc_action.setEnabled(False)
        self._action(view_menu, "Collapse All", self.collapse_all,
                     "Ctrl+Shift+C")
        view_menu.addSeparator()
        self._table_action = QAction("CSV Table View", self, checkable=True)
        self._table_action.setEnabled(False)
        self._table_action.toggled.connect(self.set_table_mode)
        view_menu.addAction(self._table_action)
        self._xml_action = QAction("XML Source View", self, checkable=True)
        self._xml_action.setEnabled(False)
        self._xml_action.toggled.connect(self.set_xml_view)
        view_menu.addAction(self._xml_action)
        self._diagram_action = QAction("Flow Diagram", self, checkable=True)
        self._diagram_action.setEnabled(False)
        self._diagram_action.toggled.connect(self.set_diagram_view)
        view_menu.addAction(self._diagram_action)
        self._xml_highlight_action = QAction(
            "XML Syntax Highlighting", self, checkable=True)
        self._xml_highlight_action.setChecked(
            str(self._settings.value("xml_highlight", "false")).lower() == "true"
        )
        self._xml_highlight_action.setEnabled(False)
        self._xml_highlight_action.toggled.connect(self.set_xml_highlight)
        view_menu.addAction(self._xml_highlight_action)
        view_menu.addSeparator()
        self._follow_action = QAction("Follow Tail (tail -f)", self, checkable=True)
        self._follow_action.setShortcut(QKeySequence("Ctrl+T"))
        self._follow_action.toggled.connect(self.set_follow)
        view_menu.addAction(self._follow_action)
        view_menu.addSeparator()
        self._action(view_menu, "Zoom In", lambda: self.zoom_by(1),
                     QKeySequence.StandardKey.ZoomIn)
        self._action(view_menu, "Zoom Out", lambda: self.zoom_by(-1),
                     QKeySequence.StandardKey.ZoomOut)
        self._action(view_menu, "Reset Zoom", self.reset_zoom, "Ctrl+0")
        self._action(view_menu, "Change Font…", self.change_font)
        view_menu.addSeparator()
        appearance_menu = view_menu.addMenu("Appearance")
        group = QActionGroup(self)
        for label, key in (("Dark", "dark"), ("Light", "light"),
                           ("System", "system")):
            action = QAction(label, self, checkable=True)
            action.setChecked(appearance == key)
            action.triggered.connect(lambda _=False, k=key: self.set_appearance(k))
            group.addAction(action)
            appearance_menu.addAction(action)

        lazy_menu = view_menu.addMenu("Lazy Indexing (large JSON)")
        lazy_mode = str(self._settings.value("lazy_mode", "auto")).lower()
        lazy_group = QActionGroup(self)
        for label, key in (
            ("Auto (lazy above 70% of RAM)", "auto"),
            ("Always (all JSON)", "always"),
            ("Never (always full index)", "never"),
        ):
            action = QAction(label, self, checkable=True)
            action.setChecked(lazy_mode == key)
            action.triggered.connect(
                lambda _=False, k=key: self._settings.setValue("lazy_mode", k)
            )
            lazy_group.addAction(action)
            lazy_menu.addAction(action)

        welcome_menu = view_menu.addMenu("Welcome Screen")
        welcome_mode = str(self._settings.value("welcome_mode", "center")).lower()
        welcome_group = QActionGroup(self)
        for label, key in (
            ("Center box only", "center"),
            ("Feature boxes + links", "static"),
            ("Feature boxes + animated links", "animated"),
            ("None (blank)", "none"),
        ):
            action = QAction(label, self, checkable=True)
            action.setChecked(welcome_mode == key)
            action.triggered.connect(
                lambda _=False, k=key: self.set_welcome_mode(k)
            )
            welcome_group.addAction(action)
            welcome_menu.addAction(action)

        # Help ---------------------------------------------------------------
        # macOS injects a Search field into the Help menu; it's diverted to a
        # throwaway menu in _suppress_macos_help_search() at startup.
        help_menu = menubar.addMenu("&Help")
        self._action(help_menu, "Features", self.show_features, "F1")
        help_menu.addSeparator()
        self._action(help_menu, "About OPENXMLJSON", self.show_about)

        # Initial enable/disable for the empty (no-document) state.
        self._sync_tools_controls()
        self._sync_scope_combo()

    # -- styling / fonts --------------------------------------------------------------

    def _apply_style(self) -> None:
        self.setStyleSheet(stylesheet(self._style))
        self._name_label.setStyleSheet(
            f"color: {self._style.text.name()};"
            " font-weight: bold; padding: 0 8px;"
        )
        self._type_label.setStyleSheet(
            f"color: {self._style.count.name()}; padding: 0 8px;"
        )
        self._load_label.setStyleSheet(
            f"color: {self._style.text.name()}; padding: 0 8px;"
        )
        self._lights.set_style(self._style)
        self._welcome.set_style(self._style)
        for view in self._views():
            view.set_style(self._style)
        # Re-theme the custom per-tab close buttons.
        from PySide6.QtWidgets import QTabBar

        bar = self.tabs.tabBar()
        for i in range(self.tabs.count()):
            wrapper = bar.tabButton(i, QTabBar.ButtonPosition.RightSide)
            if wrapper is not None:
                button = wrapper.findChild(QToolButton)
                if button is not None:
                    button.setStyleSheet(self._close_button_css())
        self._update_tab_marks()

    def set_appearance(self, key: str) -> None:
        self._settings.setValue("appearance", key)
        self._style = resolve(key)
        self._apply_style()

    def _apply_font(self) -> None:
        font = self._current_font()
        for view in self._views():
            view.set_font(font)
        self._settings.setValue("font_family_v3", self._font_family)
        self._settings.setValue("font_size_v4", self._font_size)

    def zoom_by(self, step: int) -> None:
        self._font_size = min(max(self._font_size + step, 6.0), 40.0)
        self._apply_font()
        self.statusBar().showMessage(f"Font size: {self._font_size:.0f} pt", 2000)

    def reset_zoom(self) -> None:
        self._font_size = self._default_font_size
        self._apply_font()

    def change_font(self) -> None:
        from PySide6.QtWidgets import QFontDialog

        result = QFontDialog.getFont(self._current_font(), self,
                                     "Choose viewer font")
        first, second = result
        ok, font = (
            (first, second) if isinstance(first, bool) else (second, first)
        )
        if ok:
            self._font_family = font.family()
            self._font_size = font.pointSizeF() or self._font_size
            self._apply_font()

    # -- opening --------------------------------------------------------------------

    def open_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open document", "",
                                              FILE_FILTER)
        if path:
            self.open_path(path)

    def _confirm_large_file(self, path: str) -> bool:
        """Return False if the file is big enough to risk exhausting RAM and
        the user declines. The index is roughly file-sized and fully
        resident, so a file near total RAM can destabilize the system."""
        try:
            size = os.path.getsize(path)
        except OSError:
            return True
        ram = _total_ram_bytes()
        if not ram or size <= LAZY_AUTO_FRACTION * ram:
            return True
        answer = QMessageBox.warning(
            self,
            "Large file — may exhaust memory",
            f"{os.path.basename(path)} is {size / 1e9:.1f} GB.\n\n"
            f"Building its in-memory index needs roughly the same amount of "
            f"RAM (a resident structure, unlike the file itself). This "
            f"machine has {ram / 1e9:.1f} GB total, so opening it may exhaust "
            f"memory and make the system unresponsive.\n\nOpen anyway?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _lazy_for(self, path: str) -> bool:
        """Whether to open `path` with on-demand (lazy) indexing.

        Lazy mode (setting ``lazy_mode``): ``never`` = always eager;
        ``always`` = lazy for every supported file; ``auto`` (default) = eager
        only for files that are BOTH under ``LAZY_ABS_BYTES`` (2 GB) and within
        ``LAZY_EAGER_FRACTION`` (35%) of physical RAM — since the eager index is
        resident and roughly file-sized, that keeps peak use near/below 70% of
        RAM. Everything larger uses on-demand lazy indexing (memory scales with
        the viewed portion), so opening a big file can't exhaust RAM and wedge
        the machine. JSON/NDJSON, CSV/TSV and XML are all supported. (Note: lazy
        XML is lenient and does not validate well-formedness — but it only kicks
        in for files large enough that an eager parse would risk RAM.)"""
        if LazyDocument is None:
            return False
        mode = str(self._settings.value("lazy_mode", "auto")).lower()
        if mode == "never":
            return False
        if mode == "always":
            return True
        try:
            size = os.path.getsize(path)
        except OSError:
            return False
        # Lazy above an absolute cap regardless of RAM: even on a big machine a
        # multi-GB eager index (≈2x file resident) invites heavy swapping, and
        # with a full disk that can hard-wedge the system.
        if size >= LAZY_ABS_BYTES:
            return True
        ram = _total_ram_bytes()
        return bool(ram) and size > LAZY_EAGER_FRACTION * ram

    def open_path(self, path: str, allow_duplicate: bool = False) -> None:
        # Already open? Just switch to that tab (File ▸ Reload re-parses) —
        # unless the caller explicitly wants a second copy (Duplicate Tab).
        if not allow_duplicate:
            for i in range(self.tabs.count()):
                widget = self.tabs.widget(i)
                if isinstance(widget, DocumentView) and widget.path == path:
                    self.tabs.setCurrentIndex(i)
                    return
            if path in self._pending_opens:
                return  # already loading (rapid double-open)
        use_lazy = self._lazy_for(path)
        # The RAM warning is only relevant to an eager, file-sized index;
        # lazy indexing loads on demand and won't exhaust memory.
        if not use_lazy and not self._confirm_large_file(path):
            return
        if self.tabs.count() + len(self._pending_opens) >= MAX_TABS:
            QMessageBox.information(
                self,
                "Tab limit reached",
                f"Up to {MAX_TABS} documents can be open at once — each tab "
                "keeps its structural index in memory. Close a tab first.",
            )
            return
        # Parse off the GUI thread so the window stays responsive and the
        # activity LEDs animate during load.
        self._lights.begin()
        self._pending_opens.add(path)
        signals = _OpenSignals()
        signals.done.connect(self._on_open_done)
        self._open_signals.append(signals)  # keep alive until delivered
        self.statusBar().showMessage(f"Loading {os.path.basename(path)}…")
        QThreadPool.globalInstance().start(_OpenTask(path, signals, use_lazy))

    def _on_open_done(self, path, doc, err, load_ms=0.0) -> None:
        self._pending_opens.discard(path)
        self._lights.end()
        sender = self.sender()
        if sender is not None:
            self._open_signals = [s for s in self._open_signals
                                  if s is not sender]
        if err is not None:
            if self._restore_current_path == path:
                self._restore_current_path = None
            QMessageBox.critical(self, "Cannot open file", str(err))
            return
        view = self._new_view()
        # Apply any URL association stashed by open_url before the async open,
        # BEFORE load() (load() reads source_url to enable "Expand All").
        meta = self._pending_url.pop(path, None)
        if meta is not None:
            view.source_url, view.curl_command = meta
        view.load(doc, path)
        view.load_ms = load_ms
        index = self.tabs.addTab(view, os.path.basename(path))
        self.tabs.setTabToolTip(index, path)
        from PySide6.QtWidgets import QTabBar

        self.tabs.tabBar().setTabButton(
            index, QTabBar.ButtonPosition.RightSide,
            self._make_close_button(view),
        )
        self.tabs.setCurrentIndex(index)
        self._update_tab_marks()
        self._bump_file_type(doc.format_name())
        self._update_central()
        self._remember_recent(path)
        self._watch(path)
        self.statusBar().showMessage(view.info)
        self._load_label.setText(f"Load: {load_ms:.0f}ms")
        self.setWindowTitle(f"{os.path.basename(path)} — OPENXMLJSON")
        # Session restore: reselect the previously-current tab once its
        # (async) load completes.
        if self._restore_current_path == path:
            self.tabs.setCurrentIndex(index)
            self._restore_current_path = None

    def reload_file(self) -> None:
        """Re-parse the current tab's file — for when it changed on disk."""
        view = self.current_view()
        if view is None or not view.path:
            self.statusBar().showMessage("Nothing to reload.")
            return
        path = view.path
        use_lazy = self._lazy_for(path)
        if not use_lazy and not self._confirm_large_file(path):
            return
        self._lights.begin()
        signals = _OpenSignals()
        signals.done.connect(self._on_reload_done)
        self._open_signals.append(signals)
        self.statusBar().showMessage(f"Reloading {os.path.basename(path)}…")
        QThreadPool.globalInstance().start(_OpenTask(path, signals, use_lazy))

    def _on_reload_done(self, path, doc, err, load_ms=0.0) -> None:
        self._lights.end()
        sender = self.sender()
        self._open_signals = [s for s in self._open_signals if s is not sender]
        if err is not None:
            QMessageBox.critical(self, "Cannot reload file", str(err))
            return
        # Find the tab still showing this path (it may have moved/closed).
        view = None
        for i in range(self.tabs.count()):
            w = self.tabs.widget(i)
            if isinstance(w, DocumentView) and w.path == path:
                view = w
                break
        if view is None:
            return
        view.load(doc, path)
        view.load_ms = load_ms
        self._clear_dirty_mark(view)
        self._watch(path)  # editors that replace the file drop watches
        if view is self.current_view():
            self._on_tab_changed(self.tabs.currentIndex())
            if self.search_edit.text():
                self.run_search()
            else:
                self.statusBar().showMessage("Reloaded.", 3000)

    @staticmethod
    def _ssl_context(verify: bool = True) -> ssl.SSLContext:
        """A TLS context that works on Pythons without system CA access
        (common on macOS): prefer certifi's CA bundle when installed."""
        if not verify:
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            return context
        try:
            import certifi  # type: ignore

            return ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            return ssl.create_default_context()

    def _fetch(self, url: str, headers: dict, verify: bool) -> bytes:
        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(
            request, timeout=30, context=self._ssl_context(verify)
        ) as resp:
            return resp.read()

    def _write_temp_or_warn(self, data: bytes, suffix: str, what: str):
        """Write ``data`` to a temp file the viewer can open, notifying the
        user clearly if the disk is full or no temp directory is writable
        (rather than failing silently). Returns the path, or None on failure
        so the caller can abort."""
        need = len(data)
        # Proactive: if we can read free space and it clearly won't fit, say so
        # up front. gettempdir() itself can raise when no temp dir is usable —
        # that's handled by the write's except below.
        try:
            free = shutil.disk_usage(tempfile.gettempdir()).free
        except (OSError, FileNotFoundError):
            free = None
        if free is not None and free < need + DISK_HEADROOM_BYTES:
            QMessageBox.critical(
                self,
                "Not enough disk space",
                f"Opening this {what} needs about {_fmt_size(need)}, but only "
                f"{_fmt_size(free)} is free on your disk.\n\n"
                "Free up space (remember to empty the Trash) and try again.",
            )
            return None
        try:
            fd, path = tempfile.mkstemp(suffix=suffix, prefix="oxj_")
            try:
                with os.fdopen(fd, "wb") as fh:
                    fh.write(data)
            except OSError:
                try:
                    os.unlink(path)
                except OSError:
                    pass
                raise
            self._temp_files.add(path)   # deleted on app close
            return path
        except (OSError, FileNotFoundError) as exc:
            QMessageBox.critical(
                self,
                "Can't open — disk problem",
                f"Couldn't save the {what} to a temporary file. Your disk may "
                "be full, or the system temporary folder isn't writable.\n\n"
                f"Free up space and try again.\n\nDetails: {exc}",
            )
            return None

    def _sweep_stale_temps(self) -> None:
        """Remove ``oxj_*`` temp files a previous run left behind after a crash
        or force-quit (a normal close deletes them via closeEvent). Restricted
        to our own prefix and to files untouched for STALE_TEMP_AGE_SECONDS, so
        a concurrently-running instance's fresh temps are never touched."""
        import glob

        try:
            root = tempfile.gettempdir()
        except (OSError, FileNotFoundError):
            return
        cutoff = time.time() - STALE_TEMP_AGE_SECONDS
        for path in glob.glob(os.path.join(root, "oxj_*")):
            try:
                if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                    os.unlink(path)
            except OSError:
                pass

    def check_disk_space(self) -> None:
        """One-time startup nudge if the disk is already nearly full, so a
        later save/open failure isn't a surprise."""
        try:
            free = shutil.disk_usage(tempfile.gettempdir()).free
        except (OSError, FileNotFoundError):
            self.statusBar().showMessage(
                "Warning: no writable temporary folder — opening URLs or "
                "pasting may fail until you free disk space.", 15000
            )
            return
        if free < LOW_DISK_WARN_BYTES:
            self.statusBar().showMessage(
                f"Low disk space: only {_fmt_size(free)} free. Opening large "
                "files or URLs may fail until you free space.", 15000
            )

    def open_url(self) -> None:
        dialog = OpenUrlDialog(self, self._settings)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        url = dialog.url()
        if not url:
            return
        dialog.save_or_clear()
        headers = {"User-Agent": "OPENXMLJSON/0.1"}
        pair = dialog.auth_header_pair()
        if pair:
            headers[pair[0]] = pair[1]
        suffix = os.path.splitext(url.split("?")[0])[1] or ".json"
        try:
            try:
                data = self._fetch(url, headers, verify=True)
            except (OSError, ValueError) as exc:
                if "CERTIFICATE_VERIFY_FAILED" not in str(exc):
                    raise
                answer = QMessageBox.warning(
                    self,
                    "Certificate not verified",
                    "The server's TLS certificate could not be verified "
                    "(this Python has no CA bundle — installing the "
                    "'certifi' package fixes it permanently).\n\n"
                    "Continue WITHOUT certificate verification?",
                    QMessageBox.StandardButton.Yes
                    | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return
                data = self._fetch(url, headers, verify=False)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Cannot fetch URL", str(exc))
            return
        path = self._write_temp_or_warn(data, suffix, "downloaded data")
        if path is None:
            return  # disk full / no temp dir — user already notified
        # open_path parses on a background thread, so the tab does not exist
        # yet here. Stash the request keyed by path; _on_open_done applies it
        # to the view once it has been created (marks it URL-sourced, which
        # enables the tree's recursive "Expand All", and records the cURL).
        quoted = url.replace("'", "'\\''")
        parts = [f"curl '{quoted}'"]
        for name, value in headers.items():
            header = f"{name}: {value}".replace("'", "'\\''")
            parts.append(f"-H '{header}'")
        self._pending_url[path] = (url, " ".join(parts))
        self.open_path(path)

    def open_clipboard(self) -> None:
        text = QApplication.clipboard().text()
        if not text.strip():
            self.statusBar().showMessage("Clipboard is empty.")
            return
        suffix = _clipboard_suffix(text)
        if suffix is None:
            QMessageBox.information(
                self,
                "Open Clipboard",
                "The clipboard doesn't look like JSON, XML, or CSV data, so "
                "there's nothing to open as a document.",
            )
            return
        path = self._write_temp_or_warn(
            text.encode("utf-8"), suffix, "clipboard contents"
        )
        if path is None:
            return  # disk full / no temp dir — user already notified
        self.open_path(path)

    def new_window(self) -> None:
        window = MainWindow()
        window.showMaximized()
        _windows.append(window)

    # -- recent files ---------------------------------------------------------------------

    @staticmethod
    def _is_temp_path(path: str) -> bool:
        """A transient file we created (clipboard paste, Beautify/Minify
        output, tail chunk) — never worth listing in Recent."""
        base = os.path.basename(path)
        if base.startswith("tmp") or base.startswith("oxj_"):
            return True
        try:
            tmp = os.path.realpath(tempfile.gettempdir())
            return os.path.realpath(path).startswith(tmp + os.sep)
        except OSError:
            return False

    def file_type_counts(self) -> dict:
        """Persistent tally of how many documents of each format have been
        opened (shown on the welcome screen)."""
        import json as _json

        raw = str(self._settings.value("file_counts", "{}"))
        try:
            data = _json.loads(raw)
            return (
                {str(k): int(v) for k, v in data.items()}
                if isinstance(data, dict)
                else {}
            )
        except (ValueError, TypeError):
            return {}

    def _bump_file_type(self, fmt: str) -> None:
        import json as _json

        counts = self.file_type_counts()
        counts[fmt] = counts.get(fmt, 0) + 1
        self._settings.setValue("file_counts", _json.dumps(counts))

    def _remember_recent(self, path: str) -> None:
        if self._is_temp_path(path):
            return  # don't remember temp/scratch files
        recent = [p for p in self._recent_list() if p != path]
        recent.insert(0, path)
        self._settings.setValue("recent", recent[:MAX_RECENT])

    def _recent_list(self) -> list:
        value = self._settings.value("recent", [])
        if isinstance(value, str):
            items = [value] if value else []
        else:
            items = list(value or [])
        # Filter out any temp files already recorded by an older build.
        return [p for p in items if not self._is_temp_path(p)]

    def _populate_recent(self) -> None:
        self._recent_menu.clear()
        recent = self._recent_list()
        if not recent:
            action = QAction("(empty)", self)
            action.setEnabled(False)
            self._recent_menu.addAction(action)
            return
        for path in recent:
            action = QAction(os.path.basename(path), self)
            action.setToolTip(path)
            action.triggered.connect(lambda _=False, p=path: self.open_path(p))
            self._recent_menu.addAction(action)
        self._recent_menu.addSeparator()
        clear = QAction("Clear Menu", self)
        clear.triggered.connect(lambda: self._settings.setValue("recent", []))
        self._recent_menu.addAction(clear)

    # -- exports -----------------------------------------------------------------------------

    def export_raw_copy(self) -> None:
        view = self.current_view()
        if view is None or not view.path:
            self.statusBar().showMessage("Open a file first.")
            return
        suffix = os.path.splitext(view.path)[1] or ".txt"
        path, _ = QFileDialog.getSaveFileName(
            self, "Export raw copy", f"export{suffix}"
        )
        if not path:
            return
        try:
            shutil.copyfile(view.path, path)
            self.statusBar().showMessage(f"Exported raw copy to {path}")
        except OSError as exc:
            QMessageBox.critical(self, "Export failed", str(exc))

    def export_pretty_json(self) -> None:
        view = self.current_view()
        if view is None or view.doc is None or view.model is None:
            self.statusBar().showMessage("Open a file first.")
            return
        if view.doc.file_bytes() > PRETTY_EXPORT_LIMIT:
            QMessageBox.warning(
                self,
                "File too large",
                "Pretty-JSON export re-serializes the whole document in "
                "memory and is limited to 64 MB. Use Export Data As ▸ "
                "Raw Copy for large files.",
            )
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export pretty JSON", "export.json", "JSON files (*.json)"
        )
        if not path:
            return
        import json as _json

        try:
            root = view.doc.root()
            kids = view.doc.child_nodes(root)
            values = [view.model.reconstruct(k) for k in kids]
            payload = values[0] if len(values) == 1 else values
            with open(path, "w", encoding="utf-8") as fh:
                _json.dump(payload, fh, indent=2, ensure_ascii=False)
            self.statusBar().showMessage(f"Exported pretty JSON to {path}")
        except (OSError, RecursionError, MemoryError) as exc:
            QMessageBox.critical(self, "Export failed", str(exc))

    def set_table_mode(self, enabled: bool) -> None:
        view = self.current_view()
        if view is not None:
            view.set_table_mode(enabled)
        # Keep the menu item and the toolbar button consistent (set_table_mode
        # is a no-op for non-CSV, so re-read the view's actual mode).
        self._sync_table_controls()

    def set_xml_view(self, enabled: bool) -> None:
        view = self.current_view()
        if view is not None:
            with self._busy():
                view.set_xml_view(enabled)
        self._sync_xml_controls()

    def set_diagram_view(self, enabled: bool) -> None:
        view = self.current_view()
        if view is not None:
            with self._busy():
                view.set_diagram_view(enabled)
        self._sync_diagram_controls()

    def set_xml_highlight(self, enabled: bool) -> None:
        self._settings.setValue("xml_highlight", "true" if enabled else "false")
        with self._busy():
            for view in self._views():
                view.set_xml_highlight(enabled)

    def _sync_xml_controls(self) -> None:
        """Reflect the current tab's XML-source support/mode in the View ▸
        XML Source View action and the toolbar XML View button."""
        view = self.current_view()
        supported = view is not None and view.supports_xml_view()
        active = view is not None and view.xml_view_mode()
        if hasattr(self, "_xml_action"):
            self._xml_action.blockSignals(True)
            self._xml_action.setEnabled(supported)
            self._xml_action.setChecked(active)
            self._xml_action.blockSignals(False)
        if hasattr(self, "_xml_button"):
            self._xml_button.blockSignals(True)
            self._xml_button.setChecked(active)
            # Label names the view you'll switch TO.
            self._xml_button.setText("Tree View" if active else "XML View")
            self._xml_button.setToolTip(
                "Switch to the tree view" if active
                else "Show the raw XML markup"
            )
            self._xml_button.blockSignals(False)
            self._xml_button_action.setVisible(supported)
        if hasattr(self, "_xml_highlight_action"):
            can_hl = view is not None and view.supports_xml_highlight()
            self._xml_highlight_action.blockSignals(True)
            self._xml_highlight_action.setEnabled(can_hl)
            self._xml_highlight_action.setChecked(
                can_hl and getattr(view, "_xml_highlight", False))
            self._xml_highlight_action.blockSignals(False)

    def _sync_table_controls(self) -> None:
        """Reflect the current tab's table support/mode in the View ▸ CSV
        Table View action and the toolbar Table View button."""
        view = self.current_view()
        supported = view is not None and view.supports_table()
        active = view is not None and view.table_mode()
        if hasattr(self, "_table_action"):
            self._table_action.blockSignals(True)
            self._table_action.setEnabled(supported)
            self._table_action.setChecked(active)
            self._table_action.blockSignals(False)
        if hasattr(self, "_table_button"):
            self._table_button.blockSignals(True)
            self._table_button.setChecked(active)
            # Label names the view you'll switch TO.
            self._table_button.setText("Tree View" if active else "Table View")
            self._table_button.setToolTip(
                "Switch to the tree view" if active
                else "Show CSV/TSV as a spreadsheet grid"
            )
            self._table_button.blockSignals(False)
            self._table_button_action.setVisible(supported)

    def _sync_diagram_controls(self) -> None:
        """Reflect the current tab's diagram support/mode in the View ▸ Flow
        Diagram action and the toolbar Diagram button."""
        view = self.current_view()
        supported = view is not None and view.supports_diagram()
        active = view is not None and view.diagram_mode()
        if hasattr(self, "_diagram_action"):
            self._diagram_action.blockSignals(True)
            self._diagram_action.setEnabled(supported)
            self._diagram_action.setChecked(active)
            self._diagram_action.blockSignals(False)
        if hasattr(self, "_diagram_button"):
            self._diagram_button.blockSignals(True)
            self._diagram_button.setChecked(active)
            # Label names the view you'll switch TO.
            self._diagram_button.setText("Tree View" if active else "Diagram")
            self._diagram_button.setToolTip(
                "Switch to the tree view" if active
                else "Show the data as a flow diagram"
            )
            self._diagram_button.blockSignals(False)
            self._diagram_button_action.setVisible(supported)

    def _sync_scope_combo(self) -> None:
        """The 'Attributes' search scope is XML-only — show it only for XML
        tabs, hide it (and fall back to 'All') otherwise."""
        view = self.current_view()
        is_xml = (
            view is not None
            and view.model is not None
            and view.model.format() == "XML"
        )
        self.scope_combo.blockSignals(True)
        idx = self.scope_combo.findText("Attributes")
        if is_xml and idx == -1:
            self.scope_combo.addItem("Attributes")
        elif not is_xml and idx != -1:
            if self.scope_combo.currentIndex() == idx:
                self.scope_combo.setCurrentIndex(self.scope_combo.findText("All"))
            self.scope_combo.removeItem(idx)
        self.scope_combo.blockSignals(False)

    def _sync_tools_controls(self) -> None:
        """Enable format-specific Tools items only where they apply:
        Beautify/Minify for JSON and XML (not CSV/TSV, and not the empty
        welcome state)."""
        view = self.current_view()
        fmt = view.model.format() if (view and view.model) else None
        reformattable = fmt in ("JSON", "XML")
        if hasattr(self, "_beautify_action"):
            self._beautify_action.setEnabled(reformattable)
            self._minify_action.setEnabled(reformattable)

    def set_follow(self, enabled: bool) -> None:
        view = self.current_view()
        if view is None or (enabled and not view.can_tail()):
            if enabled:
                self.statusBar().showMessage(
                    "Follow Tail applies to JSON / NDJSON / log files.", 4000
                )
            self._follow_action.setChecked(False)
            return
        view.set_follow(enabled)

    def _reconstruct_document(self, view):
        """Whole-document Python value (guarded by size)."""
        if view.doc.file_bytes() > PRETTY_EXPORT_LIMIT:
            QMessageBox.warning(
                self,
                "File too large",
                "Format conversion re-serializes the whole document in "
                "memory and is limited to 64 MB. Convert a selected node "
                "instead (right-click ▸ Export Value As).",
            )
            return None
        root = view.doc.root()
        kids = view.doc.child_nodes(root)
        values = [view.model.reconstruct(k) for k in kids]
        return values[0] if len(values) == 1 else values

    def export_converted(self, target: str) -> None:
        """File ▸ Export Data As ▸ XML/CSV — cross-format conversion."""
        from openxmljson import convert

        view = self.current_view()
        if view is None or view.doc is None or view.model is None:
            self.statusBar().showMessage("Open a file first.")
            return
        value = self._reconstruct_document(view)
        if value is None:
            return
        try:
            content = (
                convert.to_xml(value)
                if target == "xml"
                else convert.to_csv(value)
            )
        except (ValueError, RecursionError, MemoryError) as exc:
            QMessageBox.warning(self, "Cannot convert", str(exc))
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            f"Export as {target.upper()}",
            f"export.{target}",
            f"{target.upper()} files (*.{target})",
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(content)
            self.statusBar().showMessage(f"Exported {target.upper()} to {path}")
        except OSError as exc:
            QMessageBox.critical(self, "Export failed", str(exc))

    MATCH_EXPORT_LIMIT = 100_000

    def export_matches(self, target: str) -> None:
        """Export the current search's matches (path + value each)."""
        from openxmljson import convert

        view = self.current_view()
        if view is None or view.model is None:
            self.statusBar().showMessage("Open a file first.")
            return
        nodes = view.match_nodes()
        if not nodes:
            self.statusBar().showMessage("Run a search first.")
            return
        if len(nodes) > self.MATCH_EXPORT_LIMIT:
            QMessageBox.warning(
                self,
                "Too many matches",
                f"Match export is limited to "
                f"{self.MATCH_EXPORT_LIMIT:,} matches; narrow the search.",
            )
            return
        records = []
        for node in nodes:
            index = view.model.index_for_node(node)
            if not index.isValid():
                continue
            records.append(
                {
                    "path": view.model.path_text(index),
                    "value": view.model.reconstruct(
                        view.model.node_id(index)
                    ),
                }
            )
        try:
            content = (
                convert.to_pretty_json(records)
                if target == "json"
                else convert.to_csv(records)
            )
        except (ValueError, RecursionError, MemoryError) as exc:
            QMessageBox.warning(self, "Cannot export", str(exc))
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export search matches",
            f"matches.{target}",
            f"{target.upper()} files (*.{target})",
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(content)
            self.statusBar().showMessage(
                f"Exported {len(records):,} matches to {path}"
            )
        except OSError as exc:
            QMessageBox.critical(self, "Export failed", str(exc))

    def _export_selection(self, as_json: bool) -> None:
        view = self.current_view()
        if view is None:
            return
        index = view.tree.currentIndex()
        if not index.isValid():
            self.statusBar().showMessage("Select a row first.")
            return
        view.tree._export(index, as_json=as_json)

    # -- parity tools: reformat / validate / compare ---------------------------

    def _open_text_as_tab(self, content: str, suffix: str) -> None:
        """Write generated text to a temp file and open it as a new tab."""
        try:
            fd, path = tempfile.mkstemp(suffix=suffix, prefix="oxj_")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(content)
        except OSError as exc:
            QMessageBox.critical(self, "Could not create output", str(exc))
            return
        self._temp_files.add(path)   # deleted on app close
        self.open_path(path)

    def reformat_document(self, pretty: bool) -> None:
        """Tools ▸ Beautify / Minify — reformat the whole document into a new
        tab. JSON and XML only (CSV/TSV has no meaningful beautify)."""
        from openxmljson import convert

        view = self.current_view()
        if view is None or view.doc is None or view.model is None:
            self.statusBar().showMessage("Open a file first.")
            return
        fmt = view.model.format()
        if fmt not in ("JSON", "XML"):
            self.statusBar().showMessage(
                "Beautify / Minify applies to JSON and XML documents."
            )
            return
        value = self._reconstruct_document(view)
        if value is None:
            return
        try:
            if fmt == "JSON":
                content = (
                    convert.to_pretty_json(value)
                    if pretty
                    else convert.to_minified_json(value)
                )
                suffix = ".json"
            else:
                content = convert.to_xml(value, pretty=pretty)
                suffix = ".xml"
        except (RecursionError, MemoryError, ValueError) as exc:
            QMessageBox.warning(self, "Cannot reformat", str(exc))
            return
        self._open_text_as_tab(content, suffix)
        self.statusBar().showMessage(
            f"{'Beautified' if pretty else 'Minified'} {fmt} opened in a new tab.",
            4000,
        )

    #: jq reconstructs the whole document, so it's a small/medium-doc feature.
    JQ_MAX_NODES = 200_000

    def _sync_jq_controls(self) -> None:
        """Show the jq bar only for eager documents under the node-count cap."""
        if not hasattr(self, "_jq_toolbar"):
            return
        view = self.current_view()
        ok = view is not None and view.doc is not None and getattr(view, "eager", False)
        if ok:
            try:
                ok = view.doc.node_count() <= self.JQ_MAX_NODES
            except Exception:
                ok = False
        self._jq_toolbar.setVisible(ok)

    def _run_jq_now(self) -> None:
        """Run the jq bar's filter (via the native jaq engine) over the current
        document and open the result in a new tab."""
        import json

        view = self.current_view()
        if view is None or view.doc is None or view.model is None:
            self.statusBar().showMessage("Open a file first.")
            return
        program = self.jq_edit.text().strip()
        if not program:
            return
        value = self._reconstruct_document(view)
        if value is None:
            return
        try:
            with self._busy():
                from openxmljson import _native
                outputs = _native.run_jq(
                    program, json.dumps(value, ensure_ascii=False))
        except AttributeError:
            QMessageBox.warning(
                self, "jq unavailable",
                "This build's native engine has no jq support. Rebuild with "
                "'maturin develop --release'.")
            return
        except ValueError as exc:   # parse / compile / runtime errors from jaq
            self.statusBar().showMessage(f"jq error: {exc}", 6000)
            return
        if not outputs:
            self.statusBar().showMessage("jq produced no output.", 4000)
            return
        # One value → .json; multiple → newline-delimited JSON (.ndjson).
        suffix = ".json" if len(outputs) == 1 else ".ndjson"
        self._open_text_as_tab("\n".join(outputs), suffix)
        self.statusBar().showMessage(
            f"jq produced {len(outputs)} result(s) in a new tab.", 4000)

    def generate_schema(self) -> None:
        """Tools ▸ Generate JSON Schema — infer a draft-07 schema from the
        current document and open it in a new tab. Streams over the node index
        so it stays usable on large documents."""
        import json

        from openxmljson import schemagen

        view = self.current_view()
        if view is None or view.doc is None or view.model is None:
            self.statusBar().showMessage("Open a file first.")
            return
        try:
            with self._busy():
                schema_doc = schemagen.infer_schema_from_model(view.model)
        except (RecursionError, MemoryError, ValueError) as exc:
            QMessageBox.warning(self, "Cannot generate schema", str(exc))
            return
        content = json.dumps(schema_doc, indent=2, ensure_ascii=False)
        self._open_text_as_tab(content, ".json")
        self.statusBar().showMessage("Generated JSON Schema opened in a new tab.",
                                     4000)

    def validate_against_schema(self) -> None:
        """Tools ▸ Validate — validate the current document against a chosen
        JSON Schema file."""
        import json

        from openxmljson import schema as schema_mod

        view = self.current_view()
        if view is None or view.doc is None or view.model is None:
            self.statusBar().showMessage("Open a file first.")
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose JSON Schema", "",
            "JSON files (*.json);;All files (*)",
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as fh:
                schema_doc = json.load(fh)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Cannot read schema", str(exc))
            return
        value = self._reconstruct_document(view)
        if value is None:
            return
        try:
            errors = schema_mod.validate(value, schema_doc)
        except RecursionError:
            QMessageBox.warning(
                self, "Too deep",
                "The schema or document nests too deeply to validate.",
            )
            return
        name = os.path.basename(path)
        if not errors:
            self._show_report_dialog(
                "Schema validation",
                f"✓ Valid.\n\nThe document conforms to {name}.",
            )
        else:
            lines = [
                f"✗ {len(errors)} validation error(s) against {name}:",
                "",
            ]
            lines += [f"{p}: {m}" for p, m in errors]
            self._show_report_dialog("Schema validation", "\n".join(lines))

    def compare_documents(self) -> None:
        """Tools ▸ Compare — structural diff of the current tab against
        another open tab."""
        from PySide6.QtWidgets import QInputDialog

        from openxmljson import difftool

        view = self.current_view()
        if view is None or view.doc is None or view.model is None:
            self.statusBar().showMessage("Open a file first.")
            return
        others = [
            self.tabs.widget(i)
            for i in range(self.tabs.count())
            if isinstance(self.tabs.widget(i), DocumentView)
            and self.tabs.widget(i) is not view
            and self.tabs.widget(i).doc is not None
        ]
        if not others:
            self.statusBar().showMessage(
                "Open a second document in another tab to compare."
            )
            return
        labels = [os.path.basename(w.path or "untitled") for w in others]
        # Disambiguate identical basenames with their full path so the
        # picker always maps unambiguously back to a tab.
        for i, base in enumerate(labels):
            if labels.count(base) > 1:
                labels[i] = f"{base}  ({others[i].path})"
        choice, ok = QInputDialog.getItem(
            self,
            "Compare With",
            f"Compare “{os.path.basename(view.path or 'current')}” with:",
            labels,
            0,
            False,
        )
        if not ok:
            return
        other = others[labels.index(choice)]
        left = self._reconstruct_document(view)
        if left is None:
            return
        right = self._reconstruct_document(other)
        if right is None:
            return
        try:
            changes = difftool.diff(left, right)
        except (RecursionError, MemoryError) as exc:
            QMessageBox.warning(self, "Cannot compare", str(exc))
            return
        header = (
            f"Compare: {os.path.basename(view.path or 'current')}  ↔  "
            f"{os.path.basename(other.path or 'other')}\n\n"
        )
        self._show_report_dialog(
            "Compare documents", header + difftool.format_report(changes)
        )

    def _show_report_dialog(self, title: str, text: str) -> None:
        """A read-only, monospace, scrollable report with a Save button —
        used by Validate and Compare."""
        from PySide6.QtWidgets import (
            QDialog as _QDialog,
            QPlainTextEdit,
            QVBoxLayout as _QVBoxLayout,
        )

        dialog = _QDialog(self)
        dialog.setWindowTitle(title)
        dialog.resize(680, 560)
        layout = _QVBoxLayout(dialog)
        edit = QPlainTextEdit()
        edit.setReadOnly(True)
        edit.setPlainText(text)
        edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        edit.setStyleSheet("font-family: monospace;")
        layout.addWidget(edit)
        buttons = QDialogButtonBox()
        save = buttons.addButton(
            "Save…", QDialogButtonBox.ButtonRole.ActionRole
        )
        buttons.addButton(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)

        def _save():
            p, _ = QFileDialog.getSaveFileName(
                dialog, "Save report", "report.txt",
                "Text files (*.txt);;All files (*)",
            )
            if p:
                try:
                    with open(p, "w", encoding="utf-8") as fh:
                        fh.write(text)
                except OSError as exc:
                    QMessageBox.critical(dialog, "Save failed", str(exc))

        save.clicked.connect(_save)
        layout.addWidget(buttons)
        dialog.exec()

    # -- edit / view actions ---------------------------------------------------------------------

    def focus_find(self) -> None:
        self.search_edit.setFocus()
        self.search_edit.selectAll()

    def copy_row(self) -> None:
        focus = QApplication.focusWidget()
        if isinstance(focus, QLineEdit):
            focus.copy()
            return
        view = self.current_view()
        if view is None:
            return
        index = view.tree.currentIndex()
        if index.isValid():
            QApplication.clipboard().setText(
                str(index.data(Qt.ItemDataRole.DisplayRole) or "")
            )

    def jump_to_path(self) -> None:
        view = self.current_view()
        if view is None or view.model is None:
            self.statusBar().showMessage("Open a file first.")
            return
        from PySide6.QtWidgets import QInputDialog

        hint = "/root/item[2]/@attr" if view.model.format() == "XML" \
            else "$.response.products[3].name"
        text, ok = QInputDialog.getText(
            self, "Jump to Path", f"Path (e.g. {hint}):"
        )
        if not ok or not text.strip():
            return
        index = view.model.resolve_path(text)
        if index.isValid():
            view.select_index(index)
        else:
            self.statusBar().showMessage(f"Path not found: {text.strip()}")

    # -- bookmarks -------------------------------------------------------------

    def _bookmark_list(self) -> list:
        import json as _json

        raw = str(self._settings.value("bookmarks", "[]"))
        try:
            data = _json.loads(raw)
            return data if isinstance(data, list) else []
        except ValueError:
            return []

    def _save_bookmarks(self, bookmarks: list) -> None:
        import json as _json

        self._settings.setValue("bookmarks", _json.dumps(bookmarks[:50]))

    def add_bookmark(self) -> None:
        view = self.current_view()
        if view is None or view.model is None or not view.path:
            self.statusBar().showMessage("Open a file first.")
            return
        index = view._to_source_index(view.tree.currentIndex())
        if not index.isValid():
            self.statusBar().showMessage("Select a row to bookmark.")
            return
        node_path = view.model.path_text(index)
        bookmarks = self._bookmark_list()
        entry = {"file": view.path, "path": node_path}
        if entry not in bookmarks:
            bookmarks.insert(0, entry)
            self._save_bookmarks(bookmarks)
        self.statusBar().showMessage(f"Bookmarked {node_path}", 4000)

    def _goto_bookmark(self, entry: dict) -> None:
        path = entry.get("file", "")
        if not os.path.exists(path):
            self.statusBar().showMessage(f"File missing: {path}")
            return
        self.open_path(path)  # switches to the tab if already open
        view = self.current_view()
        if view is None or view.model is None or view.path != path:
            return  # e.g. the tab cap refused the open
        index = view.model.resolve_path(entry.get("path", ""))
        if index.isValid():
            view.select_index(index)
        else:
            self.statusBar().showMessage(
                f"Bookmark path not found: {entry.get('path', '')}"
            )

    def _populate_bookmarks(self) -> None:
        self._bookmarks_menu.clear()
        self._bookmarks_menu.addAction(self._add_bookmark_action)
        self._bookmarks_menu.addSeparator()
        bookmarks = [b for b in self._bookmark_list() if isinstance(b, dict)]
        if not bookmarks:
            action = QAction("(no bookmarks)", self)
            action.setEnabled(False)
            self._bookmarks_menu.addAction(action)
            return
        for entry in bookmarks:
            label = f"{os.path.basename(entry.get('file', ''))} — " \
                    f"{entry.get('path', '')}"
            action = QAction(label[:80], self)
            action.setToolTip(f"{entry.get('file', '')}\n{entry.get('path', '')}")
            action.triggered.connect(
                lambda _=False, e=entry: self._goto_bookmark(e)
            )
            self._bookmarks_menu.addAction(action)
        self._bookmarks_menu.addSeparator()
        clear = QAction("Clear Bookmarks", self)
        clear.triggered.connect(lambda: self._save_bookmarks([]))
        self._bookmarks_menu.addAction(clear)

    def copy_as_curl(self) -> None:
        view = self.current_view()
        command = getattr(view, "curl_command", None) if view else None
        if not command:
            self.statusBar().showMessage(
                "The current tab was not loaded from a URL."
            )
            return
        QApplication.clipboard().setText(command)
        self.statusBar().showMessage("cURL command copied.", 4000)

    def expand_children(self) -> None:
        view = self.current_view()
        if view is not None:
            with self._busy():
                view.expand_children()

    def expand_document(self) -> None:
        """Expand the whole document. Enabled for any eagerly-opened doc (a file
        that fits in memory, or a URL response). Because the cost scales with
        node count — not file size — a very node-dense document is confirmed
        first, so a one-shot expand can't surprise-freeze the UI."""
        view = self.current_view()
        if view is None:
            return
        if not getattr(view, "eager", False):
            self.statusBar().showMessage(
                "Expand All needs a document that fits in memory "
                "(very large files open lazily and can't be fully expanded)."
            )
            return
        try:
            nodes = view.doc.node_count() if view.doc is not None else 0
        except Exception:
            nodes = 0
        if nodes > EXPAND_ALL_MAX_NODES:
            self.statusBar().showMessage(
                f"Too many nodes ({nodes:,}) to expand at once — expand "
                "sections individually instead."
            )
            return
        if nodes > EXPAND_ALL_CONFIRM_NODES:
            answer = QMessageBox.question(
                self,
                "Expand All",
                f"This document has {nodes:,} nodes. Expanding all of them at "
                f"once may make the app unresponsive for a few seconds.\n\n"
                "Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        with self._busy():
            view.expand_document()

    def collapse_all(self) -> None:
        view = self.current_view()
        if view is not None:
            view.tree.collapseAll()

    def set_welcome_mode(self, mode: str) -> None:
        self._settings.setValue("welcome_mode", mode)
        self._welcome.set_mode(mode)

    def show_features(self) -> None:
        from PySide6.QtWidgets import (
            QDialog as _QDialog,
            QLabel as _QLabel,
            QScrollArea,
            QVBoxLayout as _QVBoxLayout,
        )

        from openxmljson import features

        dialog = _QDialog(self)
        dialog.setWindowTitle("Features — OPENXMLJSON")
        dialog.resize(640, 620)
        layout = _QVBoxLayout(dialog)
        s = self._style

        # A read-only rich-text label in a scroll area — no editor/search UI.
        label = _QLabel()
        label.setTextFormat(Qt.TextFormat.RichText)
        label.setWordWrap(True)
        label.setOpenExternalLinks(True)
        label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextBrowserInteraction
        )
        label.setMargin(10)
        label.setText(
            features.features_html(
                s.text.name(), s.placeholder.name(), s.count.name()
            )
        )
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(label)
        scroll.setStyleSheet(
            f"QScrollArea {{ background: {s.view_bg.name()};"
            f" border: 1px solid {s.chrome_border.name()}; }}"
            f" QLabel {{ background: {s.view_bg.name()}; }}"
        )
        layout.addWidget(scroll)
        dialog.exec()

    def show_about(self) -> None:
        QMessageBox.about(
            self,
            "About OPENXMLJSON",
            "OPENXMLJSON 0.1.0\n\n"
            "Viewer for very large JSON / XML / CSV files, built on a "
            "zero-copy memory-mapped structural index.\n\n"
            "Author: Kiran Peddikuppa",
        )

    # -- search --------------------------------------------------------------------------------

    def _find_args(self):
        return (
            self.search_edit.text(),
            self.scope_combo.currentText().lower(),
            self.case_button.isChecked(),
            self.regex_button.isChecked(),
        )

    def _on_search_text_changed(self, text: str) -> None:
        view = self.current_view()
        if not text and view is not None:
            view.clear_matches()
            self.statusBar().clearMessage()

    def run_search(self) -> None:
        view = self.current_view()
        if view is None:
            self.statusBar().showMessage("Open a file first.")
            return
        raw, scope, case, regex = self._find_args()
        if not raw:
            view.clear_matches()
            self.statusBar().showMessage("Empty pattern.")
            return
        with self._busy():
            view.run_search(raw, scope, case, regex)

    def find_next(self) -> None:
        self._step(+1)

    def find_prev(self) -> None:
        self._step(-1)

    def _step(self, direction: int) -> None:
        view = self.current_view()
        if view is None:
            self.statusBar().showMessage("Open a file first.")
            return
        raw, scope, case, regex = self._find_args()
        if not raw:
            self.statusBar().showMessage("Empty pattern.")
            return
        view.step_match(direction, raw, scope, case, regex)


def _set_macos_process_name() -> None:
    """When run as a bare python process on macOS, the application menu
    shows "Python". Rewrite the bundle name if pyobjc is available; the
    packaged .app (PyInstaller) carries the right name in its Info.plist
    regardless."""
    if sys.platform != "darwin":
        return
    try:
        from Foundation import NSBundle  # type: ignore

        bundle = NSBundle.mainBundle()
        for info in (bundle.localizedInfoDictionary(), bundle.infoDictionary()):
            if info is not None:
                info["CFBundleName"] = "OPENXMLJSON"
                info["CFBundleDisplayName"] = "OPENXMLJSON"
    except Exception:  # pyobjc not installed — harmless
        pass


def _suppress_macos_help_search() -> None:
    """macOS auto-inserts a (non-resizable, full-width) Search field into the
    Help menu. Point AppKit's help menu at a throwaway NSMenu so the search
    goes there instead of our visible Help menu, which stays clean."""
    if sys.platform != "darwin":
        return
    try:
        from AppKit import NSApplication, NSMenu  # type: ignore

        app = NSApplication.sharedApplication()
        app.setHelpMenu_(NSMenu.alloc().initWithTitle_(""))
    except Exception:  # pyobjc not installed — harmless
        pass


def run(argv=None) -> int:
    argv = list(argv or sys.argv)
    if not NATIVE_AVAILABLE:
        print(
            "openxmljson._native is not built.\n"
            "Run: pip install maturin pyside6 && maturin develop --release",
            file=sys.stderr,
        )
        return 1
    _set_macos_process_name()
    app = QApplication(argv)
    app.setApplicationName("OPENXMLJSON")
    app.setApplicationDisplayName("OPENXMLJSON")
    app.setDesktopFileName("openxmljson")
    window = MainWindow()
    # Open maximized ("full view") — full screen only via the OS control.
    window.showMaximized()
    # After the native menu bar is realized, divert macOS's Help search field.
    _suppress_macos_help_search()
    if len(argv) > 1:
        for path in argv[1:]:
            window.open_path(path)
    else:
        window.restore_session()  # reopen last session's tabs
    return app.exec()
