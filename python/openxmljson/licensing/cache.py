"""Local entitlement cache so the app doesn't re-verify on every launch.

Stores the last positive result plus a TTL. This is a convenience/deterrent,
NOT a security boundary — the server is the source of truth, and a determined
user could edit the file. That's an accepted limitation of any client-side
license gate. We keep it in the OS keyring when available (harder to tamper),
falling back to a JSON file in the user config dir.
"""

from __future__ import annotations

import json
import os
import time
from typing import Optional

from openxmljson.licensing.client import Entitlement
from openxmljson.licensing.config import ApiConfig

_ACCOUNT = "entitlement"


def _keyring():
    try:
        import keyring
        return keyring
    except Exception:
        return None


def _fallback_path() -> str:
    base = (os.environ.get("XDG_CONFIG_HOME")
            or os.path.join(os.path.expanduser("~"), ".config"))
    d = os.path.join(base, "OPENXMLJSON")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "license.json")


def save(cfg: ApiConfig, ent: Entitlement) -> None:
    """Cache a positive entitlement with an expiry ``cache_hours`` from now."""
    if not ent.valid:
        clear(cfg)
        return
    # Lifetime licenses (no expiry date) are cached effectively forever so the
    # app never needs to re-verify them online after the first activation.
    if ent.expires_at:
        cached_until = time.time() + cfg.cache_hours * 3600
    else:
        cached_until = time.time() + 100 * 365 * 24 * 3600   # ~100 years
    record = {
        "valid": True, "tier": ent.tier, "email": ent.email,
        "status": ent.status, "expires_at": ent.expires_at,
        "cached_until": cached_until,
    }
    blob = json.dumps(record)
    kr = _keyring()
    if kr is not None:
        try:
            kr.set_password(cfg.keyring_service(), _ACCOUNT, blob)
            return
        except Exception:
            pass
    try:
        with open(_fallback_path(), "w", encoding="utf-8") as fh:
            fh.write(blob)
    except OSError:
        pass


def load(cfg: ApiConfig) -> Optional[Entitlement]:
    """Return a cached, still-valid entitlement, or None if absent/expired."""
    blob = None
    kr = _keyring()
    if kr is not None:
        try:
            blob = kr.get_password(cfg.keyring_service(), _ACCOUNT)
        except Exception:
            blob = None
    if blob is None:
        try:
            with open(_fallback_path(), encoding="utf-8") as fh:
                blob = fh.read()
        except OSError:
            return None
    try:
        record = json.loads(blob)
    except (ValueError, TypeError):
        return None
    if record.get("cached_until", 0) < time.time():
        return None
    return Entitlement(
        valid=True, tier=record.get("tier", ""),
        email=record.get("email", ""), status=record.get("status", ""),
        expires_at=record.get("expires_at", ""),
    )


def clear(cfg: ApiConfig) -> None:
    kr = _keyring()
    if kr is not None:
        try:
            kr.delete_password(cfg.keyring_service(), _ACCOUNT)
        except Exception:
            pass
    try:
        os.remove(_fallback_path())
    except OSError:
        pass
