"""Single source of truth for the app's current license state.

The paid gate is size-based: without a valid license the app runs in "Trial"
(all data formats capped at edition.TRIAL_MAX_BYTES); a valid Essential/Premium
license removes the cap. Both tiers unlock identically — they differ only in
billing period (Essential monthly, Premium annual).

All helpers read the local entitlement cache and never raise.
"""

from __future__ import annotations

from typing import Optional, Tuple

# Badge colors: Trial red, Essential blue, Premium green, Unbxd orange.
TRIAL_COLOR = "#D9433B"
ESSENTIAL_COLOR = "#2F6BE3"
PREMIUM_COLOR = "#2FA55A"
UNBXD_COLOR = "#D97757"


def current_entitlement():
    """Cached Entitlement, or None. Never raises."""
    try:
        from openxmljson.licensing import cache
        from openxmljson.licensing.config import ApiConfig

        return cache.load(ApiConfig.from_env())
    except Exception:
        return None


def is_licensed() -> bool:
    """True when a valid (unexpired) license is cached — removes the gate."""
    ent = current_entitlement()
    return bool(ent and ent.valid)


def tier() -> Optional[str]:
    ent = current_entitlement()
    return ent.tier if (ent and ent.valid) else None


def membership_badge() -> Tuple[str, str]:
    """(label, color) for the center-box / status badge."""
    ent = current_entitlement()
    if ent and ent.valid:
        t = (ent.tier or "").strip().lower()
        if t == "premium":
            return ("Premium", PREMIUM_COLOR)
        if t == "essential":
            return ("Essential", ESSENTIAL_COLOR)
        if t == "unbxd":
            return ("Netcore Unbxd", UNBXD_COLOR)
        return (ent.tier or "Licensed", PREMIUM_COLOR)
    return ("Trial", TRIAL_COLOR)
