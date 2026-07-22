"""Memory sensing and the eager/lazy-loading policy — the single source of
truth for how physical RAM maps to indexing decisions.

Qt-free so it can be unit-tested headlessly. ``app.py`` imports the constants
and ``total_ram_bytes`` from here (so the lazy decision and the welcome-screen
panel never drift apart), and ``welcome.py`` uses ``summary_rows`` to render
the Memory panel.

Policy (auto mode): an eager open builds a fully-resident index roughly the
size of the file AND keeps the file mmapped, so the real footprint is about
twice the file size. A file therefore opens eagerly only when it is BOTH under
an absolute cap and small relative to RAM; anything larger uses on-demand lazy
indexing, whose memory scales with the viewed portion rather than the file.
"""

from __future__ import annotations

import os

#: Files at or above this size always open lazily, regardless of RAM.
#: (Retained for compatibility; the auto policy is now RAM-multiple based.)
LAZY_ABS_BYTES = 2 * 1024 ** 3       # 2 GB

#: Auto-mode policy: a file opens eagerly while its size is at or below this
#: multiple of *available* RAM; larger files use on-demand lazy indexing.
LAZY_EAGER_RAM_MULTIPLE = 2.5

#: Fallback fraction of *total* RAM used only when available RAM can't be read.
LAZY_EAGER_FRACTION = 0.35

#: Fraction of RAM above which the "large file may exhaust memory" warning
#: fires (only reachable when lazy mode is forced off for an oversized file).
LAZY_AUTO_FRACTION = 0.70


def total_ram_bytes():
    """Total physical RAM in bytes, or None if it can't be determined."""
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


def available_ram_bytes():
    """Currently available (allocatable) physical RAM in bytes, or None if it
    can't be determined on this platform."""
    # Linux (and some Unixes): free + reclaimable pages.
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_AVPHYS_PAGES")
    except (ValueError, AttributeError, OSError):
        pass
    # Windows: ullAvailPhys from GlobalMemoryStatusEx.
    try:
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
            return int(stat.ullAvailPhys)
    except Exception:
        pass
    # macOS: no sysconf for available pages — ask vm_stat.
    return _macos_available_ram_bytes()


def _macos_available_ram_bytes():
    """Approximate available RAM on macOS via `vm_stat` (free + inactive +
    speculative + purgeable pages). Returns None on any failure."""
    import sys

    if sys.platform != "darwin":
        return None
    try:
        import subprocess

        out = subprocess.run(
            ["/usr/bin/vm_stat"], capture_output=True, text=True, timeout=2
        ).stdout
    except Exception:
        return None
    page_size = 4096
    pages = {}
    for line in out.splitlines():
        if line.startswith("Mach Virtual Memory Statistics"):
            # e.g. "... (page size of 16384 bytes)"
            import re

            m = re.search(r"page size of (\d+) bytes", line)
            if m:
                page_size = int(m.group(1))
            continue
        if ":" in line:
            key, _, val = line.partition(":")
            digits = val.strip().rstrip(".").replace(",", "")
            if digits.isdigit():
                pages[key.strip()] = int(digits)
    free = (
        pages.get("Pages free", 0)
        + pages.get("Pages inactive", 0)
        + pages.get("Pages speculative", 0)
        + pages.get("Pages purgeable", 0)
    )
    return free * page_size if free else None


def eager_limit_bytes(available=None, total=None):
    """Largest file (bytes) that opens eagerly in auto mode: 2.5x *available*
    RAM. Falls back to a fraction of total RAM when available is unknown, or
    None if neither can be determined."""
    if available:
        return int(LAZY_EAGER_RAM_MULTIPLE * available)
    if total:
        return int(LAZY_EAGER_FRACTION * total)
    return None


def human_bytes(n) -> str:
    """Compact human-readable byte size."""
    if n is None:
        return "Unknown"
    if n >= 1024 ** 3:
        return f"{n / 1024 ** 3:.1f} GB"
    if n >= 1024 ** 2:
        return f"{n / 1024 ** 2:.0f} MB"
    if n >= 1024:
        return f"{n / 1024:.0f} KB"
    return f"{int(n)} B"


def summary_rows(mode="auto", total=None, available=None):
    """(label, value) rows for the welcome-screen Memory panel, reflecting the
    current Lazy Indexing mode ("auto" / "always" / "never")."""
    if total is None:
        total = total_ram_bytes()
    if available is None:
        available = available_ram_bytes()

    rows = [("Total RAM", human_bytes(total))]
    if available:
        rows.append(("Available RAM", human_bytes(available)))

    mode = (mode or "auto").lower()
    if mode == "always":
        rows.append(("In-memory", "Off (always lazy)"))
        rows.append(("Lazy loading", "All files"))
    elif mode == "never":
        rows.append(("In-memory", "All files"))
        rows.append(("Lazy loading", "Off (never)"))
    else:  # auto
        limit = eager_limit_bytes(available, total)
        limit_txt = human_bytes(limit) if limit else "—"
        rows.append(("In-memory", f"≤ {limit_txt}"))
        rows.append(("Lazy loading", f"> {limit_txt}"))
    return rows
