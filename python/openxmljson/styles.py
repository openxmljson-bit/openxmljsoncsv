"""Viewer styles — palettes modeled on the reference screenshots.

Light: white/gray zebra stripes, blue strings, green numbers, purple
booleans/nulls, teal ``{...}`` / ``[...]`` placeholders, classic tree
lines. Dark: the same mapping on a charcoal background ("classic dark").

The app defaults to the dark theme; View ▸ Appearance switches between
Dark / Light / System.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication

WATERMARK_TEXT = "Kiran Peddikuppa"


@dataclass(frozen=True)
class Style:
    dark: bool
    window_bg: QColor
    view_bg: QColor          # base row
    view_alt_bg: QColor      # zebra stripe
    text: QColor             # punctuation / default
    key: QColor              # keys and [i] index labels
    string: QColor
    number: QColor
    boolean: QColor
    null: QColor
    placeholder: QColor      # {...} / [...] and <tag>
    count: QColor            # ", 63 keys" on collapsed nodes
    guide: QColor            # tree branch lines + collapser box border
    selection_bg: QColor
    match_bg: QColor
    watermark: QColor
    chrome_border: QColor


LIGHT = Style(
    dark=False,
    window_bg=QColor("#f6f8fa"),
    view_bg=QColor("#ffffff"),
    view_alt_bg=QColor("#f4f6f8"),   # gentle stripe
    text=QColor("#57606a"),          # soft slate punctuation
    key=QColor("#24292f"),           # near-black, not pure black
    string=QColor("#2f54c9"),        # medium royal blue
    number=QColor("#1a7f37"),        # calm green
    boolean=QColor("#8250df"),       # soft purple
    null=QColor("#8250df"),
    placeholder=QColor("#316dca"),   # link-blue containers
    count=QColor("#9a6700"),         # quiet amber
    guide=QColor("#d0d7de"),
    selection_bg=QColor("#ddeeff"),
    match_bg=QColor("#fff3bf"),
    watermark=QColor(45, 45, 45, 150),
    chrome_border=QColor("#d0d7de"),
)

DARK = Style(
    dark=True,
    window_bg=QColor("#252526"),
    view_bg=QColor("#1e1e1e"),
    view_alt_bg=QColor("#262728"),   # gentler stripe
    text=QColor("#9da5b0"),          # muted punctuation
    key=QColor("#d8dee6"),
    string=QColor("#82aaf5"),        # softer blue
    number=QColor("#7ecb80"),        # softer green
    boolean=QColor("#c79bea"),       # softer purple
    null=QColor("#c79bea"),
    placeholder=QColor("#62b2bc"),   # muted teal containers
    count=QColor("#d7ba7d"),         # parchment amber, not neon
    guide=QColor("#4a4a4a"),
    selection_bg=QColor("#094771"),
    match_bg=QColor("#7a5f1a"),      # amber chip behind matched text
    watermark=QColor(235, 235, 235, 145),
    chrome_border=QColor("#3c3c3c"),
)

#: Segment role → Style attribute.
ROLE_ATTR = {
    "index": "key",
    "key": "key",
    "punct": "text",
    "string": "string",
    "number": "number",
    "bool": "boolean",
    "null": "null",
    "placeholder": "placeholder",
    "count": "count",
    "tag": "placeholder",
    "attr": "key",
    "text": "string",
}


def role_color(style: Style, role: str) -> QColor:
    return getattr(style, ROLE_ATTR.get(role, "text"))


def resolve(appearance: str) -> Style:
    """appearance ∈ {'dark', 'light', 'system'} → Style."""
    if appearance == "dark":
        return DARK
    if appearance == "light":
        return LIGHT
    hints = QGuiApplication.styleHints()
    scheme = getattr(hints, "colorScheme", None)
    if scheme is not None:
        return DARK if scheme() == Qt.ColorScheme.Dark else LIGHT
    return DARK if QGuiApplication.palette().window().color().lightness() < 128 else LIGHT


def stylesheet(s: Style) -> str:
    """Window chrome + the zebra-striped tree."""
    return f"""
    QMainWindow, QMenuBar, QToolBar, QStatusBar {{
        background: {s.window_bg.name()};
        color: {s.text.name()};
        border: none;
    }}
    QMenuBar::item:selected, QMenu::item:selected {{
        background: {s.selection_bg.name()};
    }}
    QDialog {{
        background: {s.window_bg.name()};
        color: {s.text.name()};
    }}
    QLabel, QCheckBox {{
        color: {s.text.name()};
    }}
    QMenu {{
        background: {s.window_bg.name()};
        color: {s.text.name()};
        border: 1px solid {s.chrome_border.name()};
    }}
    /* Explicit item padding stops Qt from reserving the wide native
       icon/checkmark gutter, so icon-less rows sit flush-left. */
    QMenu::item {{
        padding: 4px 24px 4px 18px;
    }}
    QMenu::icon {{
        left: 3px;
    }}
    QMenu::indicator {{
        width: 12px;
        height: 12px;
        left: 3px;
    }}
    QMenu::separator {{
        height: 1px;
        background: {s.chrome_border.name()};
        margin: 4px 8px;
    }}
    QTreeView {{
        background: {s.view_bg.name()};
        alternate-background-color: {s.view_alt_bg.name()};
        color: {s.text.name()};
        border: 1px solid {s.chrome_border.name()};
        outline: none;
    }}
    QTreeView::item {{
        padding: 1px 2px;
        border: none;
    }}
    QTreeView::item:selected {{
        background: {s.selection_bg.name()};
    }}
    QLineEdit, QComboBox {{
        background: {s.view_bg.name()};
        color: {s.text.name()};
        border: 1px solid {s.chrome_border.name()};
        border-radius: 4px;
        padding: 3px 6px;
    }}
    QComboBox {{
        min-width: 8em;              /* room for "Attributes" */
        padding: 3px 22px 3px 8px;
    }}
    QComboBox::drop-down {{
        border: none;
        width: 18px;
    }}
    /* The popup list is a separate view — style it explicitly so it
       follows the theme (it otherwise kept stale colors in light mode). */
    QComboBox QAbstractItemView {{
        background: {s.view_bg.name()};
        color: {s.text.name()};
        border: 1px solid {s.chrome_border.name()};
        selection-background-color: {s.selection_bg.name()};
        selection-color: {s.text.name()};
        outline: none;
    }}
    QPushButton, QToolButton {{
        background: {s.window_bg.name()};
        color: {s.text.name()};
        border: 1px solid {s.chrome_border.name()};
        border-radius: 4px;
        padding: 3px 9px;
    }}
    QToolButton:checked {{
        background: {s.selection_bg.name()};
    }}
    QPushButton:hover, QToolButton:hover {{
        border-color: {s.placeholder.name()};
    }}
    /* Tab strip: left-aligned, spaced tabs with rounded top corners.
       The active tab is marked by the accent dot icon (set from the app)
       plus a lighter background and brighter text. */
    QTabWidget::pane {{
        border: 1px solid {s.chrome_border.name()};
        top: -1px;
    }}
    QTabWidget::tab-bar {{
        left: 0;              /* keep tabs left-aligned (macOS centers) */
        alignment: left;
    }}
    QTabBar {{
        qproperty-drawBase: 0;
    }}
    QTabBar::tab {{
        background: {s.window_bg.name()};
        color: {s.text.name()};
        border: 1px solid {s.chrome_border.name()};
        border-bottom: none;
        padding: 6px 12px;   /* the ✕ wrapper reserves its own space */
        margin-right: 6px;
        margin-top: 3px;
        border-top-left-radius: 6px;
        border-top-right-radius: 6px;
    }}
    QTabBar::tab:selected {{
        background: {s.view_bg.name()};
        color: {s.key.name()};
    }}
    QTabBar::tab:!selected:hover {{
        background: {s.view_alt_bg.name()};
    }}
    /* (Tab close buttons are custom QToolButtons installed per tab —
       the native ::close-button ignores stylesheets on macOS.) */
    /* CSV table view. */
    QTableView {{
        background: {s.view_bg.name()};
        alternate-background-color: {s.view_alt_bg.name()};
        color: {s.text.name()};
        gridline-color: {s.chrome_border.name()};
        border: 1px solid {s.chrome_border.name()};
        selection-background-color: {s.selection_bg.name()};
        selection-color: {s.key.name()};
    }}
    QHeaderView::section {{
        background: {s.window_bg.name()};
        color: {s.key.name()};
        border: none;
        border-right: 1px solid {s.chrome_border.name()};
        border-bottom: 1px solid {s.chrome_border.name()};
        padding: 4px 8px;
    }}
    QTableView QTableCornerButton::section {{
        background: {s.window_bg.name()};
        border: none;
    }}
    /* Themed scrollbars: flat track, rounded handle, no arrow buttons. */
    QScrollBar:vertical {{
        background: {s.view_bg.name()};
        width: 12px;
        margin: 0;
        border: none;
    }}
    QScrollBar:horizontal {{
        background: {s.view_bg.name()};
        height: 12px;
        margin: 0;
        border: none;
    }}
    QScrollBar::handle:vertical {{
        background: {s.guide.name()};
        border-radius: 5px;
        min-height: 30px;
        margin: 2px;
    }}
    QScrollBar::handle:horizontal {{
        background: {s.guide.name()};
        border-radius: 5px;
        min-width: 30px;
        margin: 2px;
    }}
    QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {{
        background: {(s.guide.lighter(140) if s.dark else s.guide.darker(125)).name()};
    }}
    QScrollBar::add-line, QScrollBar::sub-line {{
        width: 0px;
        height: 0px;
        background: none;
        border: none;
    }}
    QScrollBar::add-page, QScrollBar::sub-page {{
        background: {s.view_bg.name()};
    }}
    """
