"""Build configuration.

There is now a single build. The paid gate is **license-driven at runtime**
(see ``openxmljson.licensing``): with no valid license the app runs in Trial
mode with data files capped at ``TRIAL_MAX_BYTES``; a valid Essential or
Premium license removes the cap. Both tiers unlock identically and differ only
in billing period (Essential monthly, Premium annual).
"""

from __future__ import annotations

#: File-size cap (bytes) applied in Trial mode (no valid license) to all data
#: formats — JSON, XML, CSV, TSV, YAML. Plain text (.txt/.js/.log/.py) is not
#: capped. A valid Essential/Premium license removes the cap.
TRIAL_MAX_BYTES = 50 * 1024 * 1024   # 50 MB

#: The build checks for updates (Help menu item + daily startup check).
UPDATES_ENABLED = True
