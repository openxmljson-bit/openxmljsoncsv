"""Edition / feature-flag configuration — the one place to switch the build
between the free, premium and Unbxd feature sets.

Set EDITION to "free", "premium", or "unbxd". Everything edition-specific is
derived below, so the rest of the app just reads these flags.

    free     -> JSON size gate ON,  update checks OFF
    premium  -> JSON size gate OFF, update checks ON
    unbxd    -> same as premium (Netcore Unbxd Edition)
"""

from __future__ import annotations

#: The build's edition: "essential", "premium", or "unbxd".
EDITION = "premium"

#: JSON file-size cap (bytes) applied when the size gate is enforced.
JSON_MAX_BYTES = 100 * 1024 * 1024   # 100 MB

_EDITIONS = {
    "essential":    {"size_gate": True,  "updates": False,
                "label": "Essential Edition",          "badge": "#D9433B"},
    "premium": {"size_gate": False, "updates": True,
                "label": "Premium Edition",       "badge": "#2FA55A"},
    "narik":   {"size_gate": False, "updates": True,
                "label": "Narik AI Edition", "badge": "#2F6BE3"},
}
_flags = _EDITIONS.get(EDITION, _EDITIONS["essential"])

#: True to enforce the JSON size gate (free); False allows any size (premium).
ENFORCE_SIZE_GATE = _flags["size_gate"]

#: True if the app checks for updates (Help menu item + daily startup check).
UPDATES_ENABLED = _flags["updates"]

#: Display name for the edition badge on the welcome screen.
EDITION_LABEL = _flags["label"]

#: Badge background color for the edition pill.
EDITION_BADGE_COLOR = _flags["badge"]
