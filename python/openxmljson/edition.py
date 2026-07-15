"""Edition / feature-flag configuration — the one place to switch the build
between the free and premium feature sets.

Set EDITION to "free" or "premium". Everything edition-specific is derived
below, so the rest of the app just reads these flags.

    free     -> JSON size gate ON,  update checks OFF
    premium  -> JSON size gate OFF, update checks ON
"""

from __future__ import annotations

#: The build's edition: "free" or "premium".
EDITION = "free"

#: JSON file-size cap (bytes) applied when the size gate is enforced.
JSON_MAX_BYTES = 100 * 1024 * 1024   # 100 MB

_EDITIONS = {
    "free":    {"size_gate": True,  "updates": False},
    "premium": {"size_gate": False, "updates": True},
}
_flags = _EDITIONS.get(EDITION, _EDITIONS["free"])

#: True to enforce the JSON size gate (free); False allows any size (premium).
ENFORCE_SIZE_GATE = _flags["size_gate"]

#: True if the app checks for updates (Help menu item + daily startup check).
UPDATES_ENABLED = _flags["updates"]
