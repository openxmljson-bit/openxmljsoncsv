"""Licensing configuration (public values only, from the environment).

No secret is ever committed or embedded in the shipped binary — the desktop
app talks to our backend (api.openxmljson.com), which holds the Shopify Admin
secret server-side.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass(frozen=True)
class ApiConfig:
    """Config for the backend-verification path (Netlify endpoint).

    This is the active licensing model: the desktop app never talks to Shopify
    directly. It calls our own endpoint, which holds the Shopify Admin secret
    server-side and returns entitlement. Nothing secret is embedded here — only
    the public base URL and the storefront URL.
    """

    base_url: str          # e.g. https://api.openxmljson.com
    store_url: str         # where to send un-licensed users to buy
    request_timeout: int   # seconds
    cache_hours: int       # how long a positive check is trusted offline

    @classmethod
    def from_env(cls) -> "ApiConfig":
        return cls(
            base_url=_env("OXJ_API_BASE", "https://checkapi.openxmljson.com")
            .rstrip("/"),
            store_url=_env("OXJ_STORE_URL", "https://www.openxmljson.com"),
            request_timeout=int(_env("OXJ_API_TIMEOUT", "20") or "20"),
            cache_hours=int(_env("OXJ_LICENSE_CACHE_HOURS", "24") or "24"),
        )

    def url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    def keyring_service(self) -> str:
        return "OPENXMLJSON.license"
