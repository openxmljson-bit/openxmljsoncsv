"""Feature reference shown by Help ▸ Features (and mirrored in
docs/FEATURES.md). Kept as structured data so the dialog and the docs
stay in sync.
"""

from __future__ import annotations

APP_TAGLINE = (
    "A fast viewer for very large JSON, XML and CSV files, built on a "
    "zero-copy memory-mapped structural index — files up to ~10 GB open "
    "and search without loading them into memory."
)

#: (Section title, intro, [(feature, description, shortcut)]).
FEATURE_SECTIONS = [
    (
        "Opening documents",
        "Every file is memory-mapped and parsed once into a compact index; "
        "only the rows on screen are ever built, so opening stays fast "
        "regardless of size.",
        [
            ("Open File", "JSON, JSON-Lines/NDJSON, XML, CSV and TSV. The "
             "parser is chosen from the extension.", "Ctrl+O"),
            ("Open URL", "Fetch a document over HTTP(S) with optional auth "
             "(None / Basic / Bearer Token / API Key). Credentials can be "
             "remembered.", "Alt+Shift+O"),
            ("Open Clipboard", "Paste JSON/XML/CSV text straight into a new "
             "tab; the format is auto-detected.", "Ctrl+Shift+V"),
            ("Open Recent", "Quick access to recently opened files.", ""),
            ("Drag & drop", "Drop files from the file manager onto the "
             "window to open them as tabs.", ""),
            ("Reload", "Re-read the current file from disk; changed files "
             "are flagged with a dot on the tab.", "F5"),
        ],
    ),
    (
        "Navigating the tree",
        "Documents render as a uniform, virtualized tree — objects, arrays, "
        "elements and records all in one view.",
        [
            ("Expand / Collapse All", "Open or close the whole tree (guarded "
             "on very large documents).", "Ctrl+Shift+E / Ctrl+Shift+C"),
            ("Index labels", "Array elements are labelled [0], [1], … like a "
             "code editor.", ""),
            ("Collapsed counts", "A collapsed container shows its size, e.g. "
             "“products : [...], 31 items”.", ""),
            ("Jump to Path", "Go straight to a node by path: $.a[0].b for "
             "JSON/CSV, /root/item[2]/@attr for XML.", "Ctrl+L"),
            ("Bookmarks", "Pin a node and jump back to it later, across "
             "files.", "Ctrl+D"),
            ("Type badge", "The status bar shows the selected node's type "
             "and size (“Object · 63 keys”).", ""),
        ],
    ),
    (
        "Searching, filtering and querying",
        "Three complementary ways to find things — all backed by the "
        "parallel native search.",
        [
            ("Find", "Regex or literal search (“Aa” match case, "
             "“.*” regex), scoped to All / Keys / Values / "
             "Attributes; matched text is chip-highlighted and ▲ ▼ "
             "step through hits.", "Ctrl+F, F3 / Shift+F3"),
            ("Filter box", "Hide every row except those that match (and "
             "their parents). One box, auto-detected input: plain text "
             "(row substring), key:value (field equals), or a path query — "
             "JSONPath for JSON/CSV ($.store.items[*].name, $..price, [*], "
             "[-1]) and XPath for XML (//item/@id, /catalog/item[2]/title, "
             "text(), *). Combine several paths with '|' "
             "($.a.name | $.a.price). Press Enter to apply.", ""),
        ],
    ),
    (
        "CSV tools",
        "CSV/TSV files get a spreadsheet experience in addition to the tree.",
        [
            ("Table view", "CSV/TSV opens in a spreadsheet grid by default; "
             "toggle back to the tree from the View menu.", ""),
            ("Header detection", "A text first row becomes column names; "
             "leading-zero cells (ZIP codes) stay strings.", ""),
        ],
    ),
    (
        "Live data",
        "Watch data as it arrives.",
        [
            ("Follow Tail (tail -f)", "Follow a growing JSON/NDJSON/log file: "
             "only newly appended lines are parsed and appended as rows, so "
             "it stays cheap even on huge files.", "Ctrl+T"),
        ],
    ),
    (
        "Statistics and export",
        "Understand and re-shape your data.",
        [
            ("Node statistics", "Right-click any container for child counts, "
             "a type histogram, distinct values and numeric min/max/avg.", ""),
            ("Export Data As", "Save the whole document as a raw copy, "
             "pretty JSON, XML or CSV (cross-format conversion).", ""),
            ("Export search matches", "Write the current search's matches "
             "(path + value) to JSON or CSV.", ""),
            ("Copy / Export node", "Right-click a node to copy its name, "
             "value, path, or pretty JSON (member name included), or export "
             "it — with format-specific options for JSON, XML and CSV.", ""),
            ("Copy as cURL", "Reproduce an Open URL request, including auth "
             "headers, as a cURL command.", ""),
        ],
    ),
    (
        "Appearance and layout",
        "Make it comfortable.",
        [
            ("Tabs", "Open up to 12 documents at once; reorder by dragging; "
             "right-click for close others / left / right.", "Ctrl+W"),
            ("Appearance", "Dark, Light or System theme (View ▸ "
             "Appearance).", ""),
            ("Font & zoom", "Change the font, zoom in/out (also Ctrl+wheel), "
             "or reset — remembered between sessions.",
             "Ctrl++ / Ctrl+- / Ctrl+0"),
            ("Wrap long values", "Optionally wrap long text onto multiple "
             "lines (off by default for speed).", ""),
            ("Session restore", "Your open tabs reopen on the next launch.",
             ""),
        ],
    ),
]


def features_html(text_color: str, accent: str, muted: str) -> str:
    parts = [
        f"<h2 style='color:{accent};margin-bottom:2px'>OPENXMLJSON</h2>",
        f"<p style='color:{muted};margin-top:0'>{APP_TAGLINE}</p>",
    ]
    for title, intro, items in FEATURE_SECTIONS:
        parts.append(
            f"<h3 style='color:{accent};margin-bottom:2px'>{title}</h3>"
        )
        parts.append(f"<p style='color:{muted};margin-top:0'>{intro}</p>")
        parts.append("<ul style='margin-top:2px'>")
        for name, desc, shortcut in items:
            sc = (
                f" <span style='color:{muted}'>[{shortcut}]</span>"
                if shortcut
                else ""
            )
            parts.append(
                f"<li style='margin-bottom:5px;color:{text_color}'>"
                f"<b>{name}</b>{sc}<br>"
                f"<span style='color:{muted}'>{desc}</span></li>"
            )
        parts.append("</ul>")
    return "".join(parts)


def features_markdown() -> str:
    lines = ["# OPENXMLJSON — Features", "", APP_TAGLINE, ""]
    for title, intro, items in FEATURE_SECTIONS:
        lines.append(f"## {title}")
        lines.append("")
        lines.append(intro)
        lines.append("")
        for name, desc, shortcut in items:
            sc = f" _({shortcut})_" if shortcut else ""
            lines.append(f"- **{name}**{sc} — {desc}")
        lines.append("")
    return "\n".join(lines)
