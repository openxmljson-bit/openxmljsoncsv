"""The welcome / empty-state screen: a central card with quick actions and
recent files, surrounded by feature boxes connected by animated flow
lines (hub-and-spoke).
"""

from __future__ import annotations

import html
import os

from PySide6.QtCore import QPointF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from openxmljson import memory
from openxmljson.edition import EDITION_BADGE_COLOR, EDITION_LABEL
from openxmljson.styles import Style

#: Accent color for the file-size hint in the recent list (amber/orange).
RECENT_SIZE_COLOR = "#E39A2D"


class _RecentLink(QLabel):
    """A clickable, rich-text recent-file row (so the size can be colored)."""

    clicked = Signal()

    def mousePressEvent(self, event):  # noqa: N802 (Qt override)
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


def _middle_ellipsis(name: str, limit: int = 32) -> str:
    """Shorten an over-long filename with a middle '…', keeping the start and
    the tail (so the extension stays visible). E.g.
    'very_long_export_name.json' -> 'very_long_e…rt_name.json'."""
    if len(name) <= limit:
        return name
    keep = limit - 1  # room for the ellipsis
    head = keep // 2
    tail = keep - head
    return f"{name[:head]}…{name[-tail:]}"


def _human_size(path: str) -> str:
    """Compact human-readable size for a file, or '' if it can't be read."""
    try:
        n = os.path.getsize(path)
    except OSError:
        return ""
    if n >= 1024 ** 3:
        return f"{n / 1024 ** 3:.1f}GB"
    if n >= 1024 ** 2:
        return f"{n / 1024 ** 2:.1f}MB"
    if n >= 1024:
        return f"{n / 1024:.0f}KB"
    return f"{n}B"

#: Feature boxes (icon, title, subtitle). All shown down the left side.
FEATURES = [
    ("🔍", "Find Any", "Regex · scopes", "left"),
    ("▤", "Filter Box", "Show matches", "left"),
    ("λ", "Query Bar", "JSONPath · XPath", "left"),
    ("⤓", "Export", "JSON · XML · CSV", "left"),
    ("❑", "Tabs UI", "Up to 12 files", "left"),
    ("≣", "CSV Table", "Spreadsheet view", "left"),
    ("⟳", "Live Tail", "Follow appends", "left"),
    ("Σ", "Statistics", "Counts · min/max", "left"),
]

BOX_W = 190
BOX_H = 54
#: Below this window width the feature boxes are hidden (just the card).
MIN_WIDTH_FOR_BOXES = 980
#: Top-right "files served" stats panel.
STATS_W = 240
STATS_BAR_W = 110
#: Accent for the stat bars — a fixed terracotta orange (per request),
#: independent of the light/dark theme.
STATS_ORANGE = "#D97757"
STATS_TRACK = "#3A3A3A"


def _fmt_count(n: int) -> str:
    """Compact count for the fixed-width stats panel, so a large tally can't
    widen the label past the panel and clip/collide with the bar. Bounded to
    <=5 chars at any magnitude; the exact number is shown as a tooltip.
        172 -> "172"   9,999 -> "9,999"   10_000 -> "10k"
        172_000 -> "172k"   1_200_000 -> "1.2M"   3_000_000_000 -> "3.0B"
    """
    if n < 10_000:
        return f"{n:,}"
    if n < 1_000_000:
        return f"{n // 1000}k"          # 10k .. 999k
    if n < 1_000_000_000:
        return f"{n / 1_000_000:.1f}M".replace(".0M", "M")
    return f"{n / 1_000_000_000:.1f}B".replace(".0B", "B")


class _Box(QFrame):
    clicked = Signal()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class WelcomeWidget(QWidget):
    def __init__(self, style: Style, window, parent=None):
        super().__init__(parent)
        self._style = style
        self._window = window
        self._paths = []      # [(polyline_points, phase_offset)]
        self._phase = 0.0
        # Display mode (View ▸ Welcome Screen):
        #   center   — just the central card (default)
        #   static   — card + feature boxes + elbow links (no animation)
        #   animated — static + a pulse traveling along each link
        #   none     — blank empty state
        self._mode = "center"

        # -- central card ----------------------------------------------------
        self._card = QFrame(self)
        self._card.setObjectName("welcomeCard")
        self._card.setFixedWidth(460)
        col = QVBoxLayout(self._card)
        col.setContentsMargins(40, 32, 40, 32)
        col.setSpacing(6)

        self._title = QLabel("OPENXMLJSON")
        self._title.setObjectName("welcomeTitle")
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        col.addWidget(self._title)
        self._tagline = QLabel()  # rich text set in set_style (needs colors)
        self._tagline.setObjectName("welcomeTagline")
        self._tagline.setTextFormat(Qt.TextFormat.RichText)
        self._tagline.setWordWrap(True)
        self._tagline.setAlignment(Qt.AlignmentFlag.AlignCenter)
        col.addWidget(self._tagline)

        chips = QHBoxLayout()
        chips.setSpacing(6)
        chips.setContentsMargins(0, 10, 0, 10)
        self._chips = []
        for fmt in ("JSON", "NDJSON", "XML", "CSV", "TSV"):
            chip = QLabel(fmt)
            chip.setObjectName("welcomeChip")
            chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
            chips.addWidget(chip)
            self._chips.append(chip)
        chips.addStretch(1)
        col.addLayout(chips)

        actions = QHBoxLayout()
        actions.setSpacing(6)
        for label, slot in (
            ("Open File…", window.open_dialog),
            ("Open URL…", window.open_url),
            ("Open Clipboard", window.open_clipboard),
        ):
            btn = QPushButton(label)
            btn.setObjectName("welcomeButton")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(slot)
            actions.addWidget(btn)
        col.addLayout(actions)

        self._recent_title = QLabel("Recent")
        self._recent_title.setObjectName("welcomeSection")
        col.addWidget(self._recent_title)
        self._recent_box = QVBoxLayout()
        self._recent_box.setSpacing(2)
        col.addLayout(self._recent_box)

        self._hint = QLabel(
            "Tip: drag a file onto the window, or press F1 for all features."
        )
        self._hint.setObjectName("welcomeHint")
        col.addSpacing(10)
        col.addWidget(self._hint)

        # Byline below the card (not inside it).
        self._byline = QLabel("Built for GIGABYTE Files", self)
        self._byline.setObjectName("welcomeByline")
        self._byline.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Edition badge — a colored pill positioned on the card's top edge in
        # _relayout (a real child widget so it renders ABOVE the card).
        self._edition_badge = QLabel(EDITION_LABEL, self)
        _badge_bg = EDITION_BADGE_COLOR
        self._edition_badge.setStyleSheet(
            "QLabel { background: %s; color: #ffffff; font-weight: 600;"
            " font-size: 13px; padding: 5px 12px; border-radius: 6px; }"
            % _badge_bg)
        self._edition_badge.adjustSize()

        # -- feature boxes ---------------------------------------------------
        self._boxes = []
        for icon, title, subtitle, side in FEATURES:
            box = _Box(self)
            box.setObjectName("featureBox")
            box.setFixedSize(BOX_W, BOX_H)
            # Feature boxes are informational: keep the hover highlight but make
            # them non-interactive (no click opens the Features window).
            bl = QHBoxLayout(box)
            bl.setContentsMargins(12, 6, 12, 6)
            bl.setSpacing(10)
            ic = QLabel(icon)
            ic.setObjectName("featureIcon")
            bl.addWidget(ic)
            text = QVBoxLayout()
            text.setSpacing(0)
            t = QLabel(title)
            t.setObjectName("featureTitle")
            sub = QLabel(subtitle)
            sub.setObjectName("featureSub")
            text.addWidget(t)
            text.addWidget(sub)
            bl.addLayout(text)
            bl.addStretch(1)
            self._boxes.append((box, side))

        # -- right-side "files opened" stats panel ---------------------------
        self._stats = QFrame(self)
        self._stats.setObjectName("welcomeCard")
        self._stats.setFixedWidth(STATS_W)
        sc = QVBoxLayout(self._stats)
        sc.setContentsMargins(20, 18, 20, 18)
        sc.setSpacing(8)
        self._stats_title = QLabel("Files Served")
        self._stats_title.setObjectName("statsTitle")
        sc.addWidget(self._stats_title)
        self._stats_rows = QVBoxLayout()
        self._stats_rows.setSpacing(10)
        sc.addLayout(self._stats_rows)
        sc.addStretch(1)
        self._has_stats = False
        self._stats.hide()

        # -- right-side "Memory" panel (below the stats card) ----------------
        self._mem = QFrame(self)
        self._mem.setObjectName("welcomeCard")
        self._mem.setFixedWidth(STATS_W)
        mc = QVBoxLayout(self._mem)
        mc.setContentsMargins(20, 18, 20, 18)
        mc.setSpacing(8)
        mem_head = QHBoxLayout()
        mem_head.setContentsMargins(0, 0, 0, 0)
        mem_head.setSpacing(6)
        self._mem_title = QLabel("Memory")
        self._mem_title.setObjectName("statsTitle")
        mem_head.addWidget(self._mem_title)
        mem_head.addStretch(1)
        self._mem_refresh = QPushButton("Refresh")
        self._mem_refresh.setObjectName("memRefresh")
        self._mem_refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        self._mem_refresh.setFlat(True)
        self._mem_refresh.setToolTip("Refresh memory stats")
        self._mem_refresh.clicked.connect(self._build_memory)
        mem_head.addWidget(self._mem_refresh)
        mc.addLayout(mem_head)
        self._mem_rows = QVBoxLayout()
        self._mem_rows.setSpacing(10)
        mc.addLayout(self._mem_rows)
        # "Free up temp files" link — shown only when leftovers exist.
        self._mem_free = QPushButton("Free up temp files")
        self._mem_free.setObjectName("memFree")
        self._mem_free.setCursor(Qt.CursorShape.PointingHandCursor)
        self._mem_free.setFlat(True)
        self._mem_free.setToolTip(
            "Delete leftover temporary working files from crashes/force-quits")
        self._mem_free.clicked.connect(self._free_temp_files)
        self._mem_free.setVisible(False)
        mc.addWidget(self._mem_free)
        mc.addStretch(1)
        self._mem.hide()

        self._timer = QTimer(self)
        self._timer.setInterval(33)  # ~30 fps
        self._timer.timeout.connect(self._tick)

        self.set_style(style)

    # -- recent files ---------------------------------------------------------

    def refresh(self) -> None:
        while self._recent_box.count():
            item = self._recent_box.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        recent = self._window._recent_list()[:10]
        self._recent_title.setVisible(bool(recent))
        for path in recent:
            size = _human_size(path)
            name = html.escape(_middle_ellipsis(os.path.basename(path)))
            text = f"&nbsp;&nbsp;{name}"
            if size:
                text += (f"&nbsp;&nbsp;<span style='color:{RECENT_SIZE_COLOR};"
                         f"font-weight:bold;'>~{html.escape(size)}</span>")
            link = _RecentLink()
            link.setTextFormat(Qt.TextFormat.RichText)
            link.setText(text)
            link.setObjectName("welcomeRecent")
            link.setToolTip(path)
            link.setCursor(Qt.CursorShape.PointingHandCursor)
            link.clicked.connect(lambda p=path: self._window.open_path(p))
            self._recent_box.addWidget(link)
        self._build_stats()
        self._build_memory()
        self._relayout()  # reposition/show the stats panel for new counts

    def _build_stats(self) -> None:
        """Rebuild the right-side 'files opened' bars from the per-format
        counts the window has tallied. Only formats served at least once
        (count > 0) are shown."""
        while self._stats_rows.count():
            item = self._stats_rows.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        counts = {
            fmt: n for fmt, n in self._window.file_type_counts().items() if n > 0
        }
        self._has_stats = bool(counts)
        self._stats_title.setVisible(self._has_stats)
        if not self._has_stats:
            return
        peak = max(counts.values())
        # Stable, familiar order; unknown formats appended.
        order = ["JSON", "NDJSON", "XML", "CSV", "TSV", "TXT", "JS"]
        keys = [k for k in order if k in counts] + [
            k for k in counts if k not in order
        ]
        for fmt in keys:
            n = counts[fmt]
            row = QWidget()
            row.setObjectName("statRow")
            hb = QHBoxLayout(row)
            hb.setContentsMargins(0, 0, 0, 0)
            hb.setSpacing(8)
            name = QLabel(fmt)
            name.setObjectName("statName")
            name.setFixedWidth(48)
            track = QFrame()
            track.setObjectName("statTrack")
            track.setFixedSize(STATS_BAR_W, 8)
            tl = QHBoxLayout(track)
            tl.setContentsMargins(0, 0, 0, 0)
            tl.setSpacing(0)
            fill = QFrame()
            fill.setObjectName("statFill")
            fill.setFixedWidth(max(3, round(STATS_BAR_W * n / peak)))
            tl.addWidget(fill)
            tl.addStretch(1)
            value = QLabel(_fmt_count(n))
            value.setObjectName("statCount")
            if n >= 10_000:  # exact figure available on hover
                value.setToolTip(f"{n:,}")
            hb.addWidget(name)
            hb.addWidget(track)
            hb.addStretch(1)
            hb.addWidget(value)
            self._stats_rows.addWidget(row)

    def _build_memory(self) -> None:
        """Rebuild the Memory panel: available RAM plus the size thresholds
        that decide in-memory vs. lazy loading, reflecting the current Lazy
        Indexing mode."""
        while self._mem_rows.count():
            item = self._mem_rows.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        try:
            mode = str(self._window._settings.value("lazy_mode", "auto"))
        except Exception:
            mode = "auto"
        rows = list(memory.summary_rows(mode=mode))
        # Leftover temp files (from crashes/force-quits) that can be reclaimed.
        count, size = 0, 0
        try:
            count, size = self._window.temp_files_summary()
        except Exception:
            pass
        rows.append((
            "Temp files",
            f"{count}  ~{memory.human_bytes(size)}" if count else "0"))
        for label, value in rows:
            row = QWidget()
            row.setObjectName("statRow")
            hb = QHBoxLayout(row)
            hb.setContentsMargins(0, 0, 0, 0)
            hb.setSpacing(8)
            name = QLabel(label)
            name.setObjectName("statName")
            val = QLabel(value)
            val.setObjectName("statCount")
            hb.addWidget(name)
            hb.addStretch(1)
            hb.addWidget(val)
            self._mem_rows.addWidget(row)
        self._mem_free.setVisible(count > 0)
        if count:
            self._mem_free.setText(
                f"Free up {count} temp file(s) · {memory.human_bytes(size)}")

    def _free_temp_files(self) -> None:
        if hasattr(self._window, "free_temp_files"):
            self._window.free_temp_files()
        self._build_memory()

    # -- layout & animation ---------------------------------------------------

    def set_mode(self, mode: str) -> None:
        """center = card only; static = card + feature boxes + elbow links;
        animated = static + a traveling pulse; none = blank empty state."""
        if mode not in ("center", "static", "animated", "none"):
            mode = "center"
        self._mode = mode
        self._card.setVisible(mode != "none")
        self._relayout()
        if mode == "animated" and self.isVisible():
            self._timer.start()
        else:
            self._timer.stop()
        self.update()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if self._mode == "animated":
            self._timer.start()

    def hideEvent(self, event) -> None:  # noqa: N802
        super().hideEvent(event)
        self._timer.stop()

    def _tick(self) -> None:
        self._phase = (self._phase + 0.005) % 1.0
        self.update()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._relayout()

    def _relayout(self) -> None:
        w, h = self.width(), self.height()
        self._card.adjustSize()
        cw, ch = self._card.width(), self._card.height()
        cx = (w - cw) // 2
        # Bias the card upward: top gap ~20% of the leftover space (rather than
        # dead-centered) so it sits nearer the top.
        cy = max(20, int((h - ch) * 0.20))
        self._card.move(cx, cy)
        card_mid_y = cy + ch / 2

        # Edition badge straddling the card's top-center edge, above the card.
        badge = self._edition_badge
        badge.adjustSize()
        badge.move(cx + (cw - badge.width()) // 2, int(cy - badge.height() / 2))
        badge.setVisible(self._mode != "none")
        badge.raise_()

        # Byline sits just below the card, centered, and hides with it.
        self._byline.setVisible(self._mode != "none")
        self._byline.adjustSize()
        self._byline.move((w - self._byline.width()) // 2, cy + ch + 12)

        # Feature boxes + links only in the static/animated modes (and only
        # when the window is wide enough to place them clear of the card).
        show_boxes = self._mode in ("static", "animated") and w >= MIN_WIDTH_FOR_BOXES
        left = [b for b, s in self._boxes if s == "left"]
        right = [b for b, s in self._boxes if s == "right"]
        self._paths = []

        def place(column, side):
            n = len(column)
            if n == 0:
                return
            # Center the box band on the card (not the window) so the boxes and
            # their connector lines follow the upward-shifted card. The band is
            # capped and clamped on-screen.
            per = BOX_H + 26
            band_h = min(h - 80, n * per)
            top = card_mid_y - band_h / 2
            bottom = card_mid_y + band_h / 2
            if top < 40:
                bottom += 40 - top
                top = 40
            if bottom > h - 40:
                top -= bottom - (h - 40)
                bottom = h - 40
                top = max(40, top)
            step = (bottom - top) / max(n, 1)
            card_left, card_right = cx, cx + cw
            for i, box in enumerate(column):
                box.setVisible(show_boxes)
                if not show_boxes:
                    continue
                by = int(top + step * i + (step - BOX_H) / 2)
                if side == "left":
                    # Anchor near the window's left edge (well clear of the
                    # card) so the boxes spread out.
                    bx = min(cx - 320, max(56, int(w * 0.06)))
                    box.move(bx, by)
                    start = QPointF(card_left, self._clamp(
                        card_mid_y, cy, cy + ch, by + BOX_H / 2))
                    end = QPointF(bx + BOX_W, by + BOX_H / 2)
                else:
                    bx = max(cx + cw + 320 - BOX_W,
                             min(w - 56 - BOX_W, int(w * 0.94) - BOX_W))
                    box.move(bx, by)
                    start = QPointF(card_right, self._clamp(
                        card_mid_y, cy, cy + ch, by + BOX_H / 2))
                    end = QPointF(bx, by + BOX_H / 2)
                mid_x = (start.x() + end.x()) / 2
                pts = [
                    start,
                    QPointF(mid_x, start.y()),
                    QPointF(mid_x, end.y()),
                    end,
                ]
                self._paths.append((pts, i / max(n, 1)))

        place(left, "left")
        place(right, "right")

        # Top-right stats panel — shown whenever files have been served, in
        # every mode (independent of the feature boxes), when there's room to
        # the right of the centered card.
        self._stats.adjustSize()
        sx = w - STATS_W - 32
        has_room = sx > cx + cw + 20
        show_stats = self._has_stats and has_room
        self._stats.setVisible(show_stats)
        if show_stats:
            self._stats.move(sx, 28)  # anchored near the top

        # Memory panel: directly below the stats card (or at the top if the
        # stats card is hidden), whenever there's room right of the card and
        # we're not in the blank mode.
        self._mem.adjustSize()
        show_mem = has_room and self._mode != "none"
        self._mem.setVisible(show_mem)
        if show_mem:
            my = (28 + self._stats.height() + 16) if show_stats else 28
            self._mem.move(sx, my)

    @staticmethod
    def _clamp(value, lo, hi, toward):
        # Nudge the card-edge start point vertically toward the box so the
        # connectors fan out instead of all leaving from one point.
        v = (value + toward) / 2
        return max(lo + 8, min(hi - 8, v))

    @staticmethod
    def _sample(points, t):
        """Point at arc-length fraction t (0..1) along a polyline."""
        segs = []
        total = 0.0
        for a, b in zip(points, points[1:]):
            d = ((b.x() - a.x()) ** 2 + (b.y() - a.y()) ** 2) ** 0.5
            segs.append((a, b, d))
            total += d
        if total == 0:
            return points[0]
        target = t * total
        for a, b, d in segs:
            if target <= d or d == 0:
                f = 0 if d == 0 else target / d
                return QPointF(a.x() + (b.x() - a.x()) * f,
                               a.y() + (b.y() - a.y()) * f)
            target -= d
        return points[-1]

    def _draw_watermark(self, painter: QPainter) -> None:
        """Big, bold, semi-transparent 'NARIK' in the bottom-right corner —
        subtle and theme-aware (light on dark, dark on light)."""
        text = "NARIK"
        font = QFont(self.font())
        font.setBold(True)
        font.setPixelSize(max(40, int(self.height() * 0.16)))
        painter.setFont(font)
        fm = QFontMetrics(font)
        margin = 28
        x = self.width() - fm.horizontalAdvance(text) - margin
        y = self.height() - margin  # text baseline
        color = QColor(self._style.text)
        color.setAlpha(30)          # transparent grey
        painter.setPen(color)
        painter.drawText(max(margin, x), y, text)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self._draw_watermark(painter)
        if not self._paths:
            painter.end()
            return
        line = QColor(self._style.guide)
        pulse = QColor(self._style.placeholder)
        pen = QPen(line, 1.4)
        for idx, (pts, offset) in enumerate(self._paths):
            painter.setPen(pen)
            for a, b in zip(pts, pts[1:]):
                painter.drawLine(a, b)
            if self._mode == "animated":
                # Alternate lines pulse in opposite directions: even indices
                # travel card → box, odd indices travel box → card.
                t = (self._phase + offset) % 1.0
                if idx % 2:
                    t = 1.0 - t
                p = self._sample(pts, t)
            else:
                # Static: alternate which end holds the steady dot — even
                # lines at the card end, odd lines at the feature-box end.
                p = pts[-1] if idx % 2 else pts[0]
            painter.setPen(Qt.PenStyle.NoPen)
            glow = QColor(pulse)
            glow.setAlpha(70)
            painter.setBrush(glow)
            painter.drawEllipse(p, 6, 6)
            painter.setBrush(pulse)
            painter.drawEllipse(p, 3, 3)
        painter.end()

    # -- theming --------------------------------------------------------------

    def set_style(self, style: Style) -> None:
        self._style = style
        s = style
        # Punchy one-liner with the formats accented (the "gigabyte files"
        # line now lives in the byline below the card).
        self._tagline.setText(
            "A rapid <b style='color:{c}'>JSON · XML · CSV</b> "
            "loading engine".format(c=s.placeholder.name())
        )
        self.setStyleSheet(
            f"""
            QWidget {{ background: {s.window_bg.name()}; }}
            /* Labels/links are transparent so the card/box color shows
               through (no stray window-colored rectangles). */
            #welcomeCard QLabel, #featureBox QLabel, #statsTitle,
            #welcomeChip, #welcomeRecent, #statRow QLabel {{
                background: transparent;
            }}
            #statsTitle {{
                color: {s.key.name()}; font-size: 14px; font-weight: bold;
            }}
            #memRefresh {{
                background: transparent; border: none;
                color: #3B82F6; font-size: 11px; font-weight: bold;
            }}
            #memRefresh:hover {{ color: #60A5FA; }}
            #memFree {{
                background: transparent; border: none; text-align: left;
                color: #3B82F6; font-size: 12px; font-weight: bold;
                padding: 2px 0;
            }}
            #memFree:hover {{ color: #60A5FA; }}
            #statName {{ color: {s.text.name()}; font-size: 13px; }}
            #statCount {{
                color: {s.key.name()}; font-size: 13px; font-weight: bold;
            }}
            #statTrack {{
                background: {STATS_TRACK};
                border-radius: 4px;
            }}
            #statFill {{
                background: {STATS_ORANGE};
                border-radius: 4px;
            }}
            #welcomeCard {{
                background: {s.view_bg.name()};
                border: 1px solid {s.chrome_border.name()};
                border-radius: 12px;
            }}
            #welcomeTitle {{
                color: {s.key.name()}; font-size: 28px; font-weight: bold;
            }}
            #welcomeTagline {{ color: {s.text.name()}; font-size: 14px; }}
            #welcomeSection {{
                color: {s.count.name()}; font-size: 12px; font-weight: bold;
                padding-top: 6px;
            }}
            #welcomeHint {{ color: {s.guide.name()}; font-size: 12px; }}
            #welcomeByline {{
                background: transparent; color: {s.text.name()};
                font-size: 16px; font-weight: 600; letter-spacing: 0.5px;
            }}
            #welcomeChip {{
                color: {s.placeholder.name()};
                border: 1px solid {s.chrome_border.name()};
                border-radius: 10px; padding: 2px 10px;
                font-size: 12px; font-weight: bold;
            }}
            #welcomeButton {{
                background: {s.window_bg.name()}; color: {s.key.name()};
                border: 1px solid {s.chrome_border.name()};
                border-radius: 6px; padding: 8px 10px; font-size: 13px;
            }}
            #welcomeButton:hover {{ border-color: {s.placeholder.name()}; }}
            #welcomeRecent {{
                color: {s.string.name()}; text-align: left; border: none;
                padding: 2px 0; font-size: 13px;
            }}
            #welcomeRecent:hover {{ color: {s.placeholder.name()}; }}
            #featureBox {{
                background: {s.view_bg.name()};
                border: 1px solid {s.chrome_border.name()};
                border-radius: 10px;
            }}
            #featureBox:hover {{ border-color: {s.placeholder.name()}; }}
            #featureIcon {{ color: {s.placeholder.name()}; font-size: 16px; }}
            #featureTitle {{
                color: {s.key.name()}; font-size: 13px; font-weight: bold;
            }}
            #featureSub {{ color: {s.text.name()}; font-size: 11px; }}
            """
        )
        self.update()
