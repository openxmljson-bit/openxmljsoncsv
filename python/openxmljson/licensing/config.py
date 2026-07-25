"""Shopify Customer Account API configuration (public identifiers only).

Everything here is loaded from the environment so no ID or secret is ever
committed or embedded in the shipped binary. Defaults exist only for the
non-secret Shop ID; the Client ID must be provided by the environment for a
real login.

Endpoints follow Shopify's New Customer Accounts scheme. Confirm the exact
Authorization / Token / GraphQL URLs in your Shopify admin under
Sales channels ▸ Headless ▸ Customer Account API settings ▸ Application
endpoints, and override via env if they differ.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass(frozen=True)
class ShopifyConfig:
    shop_id: str
    client_id: str
    api_version: str
    redirect_uri: str
    scope: str
    authorize_url: str
    token_url: str
    logout_url: str
    graphql_url: str
    store_url: str

    @classmethod
    def from_env(cls) -> "ShopifyConfig":
        # Shop ID is a public identifier — safe to default.
        shop_id = _env("SHOPIFY_SHOP_ID", "82002379006")
        client_id = _env("SHOPIFY_CLIENT_ID")     # public, but no default
        api_version = _env("SHOPIFY_API_VERSION", "2025-07")

        # Desktop app = "Mobile" public client → custom URI scheme callback.
        # Shopify rejects localhost/http callbacks (RFC 8252 §8.3), and the
        # mobile scheme must start with shop.{shop_id}. to be globally unique.
        redirect_uri = _env(
            "SHOPIFY_REDIRECT_URI", f"shop.{shop_id}.openxmljson://callback")

        # openid+email for the id_token identity; the customer-account scope
        # for GraphQL. Confirm the exact scope strings in your admin.
        scope = _env(
            "SHOPIFY_SCOPE",
            "openid email https://api.customers.com/auth/customer.graphql")

        base_auth = f"https://shopify.com/authentication/{shop_id}"
        authorize_url = _env(
            "SHOPIFY_AUTHORIZE_URL", f"{base_auth}/oauth/authorize")
        token_url = _env("SHOPIFY_TOKEN_URL", f"{base_auth}/oauth/token")
        logout_url = _env("SHOPIFY_LOGOUT_URL", f"{base_auth}/logout")
        graphql_url = _env(
            "SHOPIFY_GRAPHQL_URL",
            f"https://shopify.com/{shop_id}/account/customer/api/"
            f"{api_version}/graphql")
        store_url = _env("SHOPIFY_STORE_URL", "https://www.openxmljson.com")

        return cls(
            shop_id=shop_id, client_id=client_id, api_version=api_version,
            redirect_uri=redirect_uri, scope=scope,
            authorize_url=authorize_url, token_url=token_url,
            logout_url=logout_url, graphql_url=graphql_url,
            store_url=store_url,
        )

    def is_configured(self) -> bool:
        """True when enough is set to attempt a real login."""
        return bool(self.shop_id and self.client_id)

    def keyring_service(self) -> str:
        return f"OPENXMLJSON.shopify.{self.shop_id}"


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
            base_url=_env("OXJ_API_BASE", "https://api.openxmljson.com")
            .rstrip("/"),
            store_url=_env("OXJ_STORE_URL", "https://www.openxmljson.com"),
            request_timeout=int(_env("OXJ_API_TIMEOUT", "20") or "20"),
            cache_hours=int(_env("OXJ_LICENSE_CACHE_HOURS", "24") or "24"),
        )

    def url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    def keyring_service(self) -> str:
        return "OPENXMLJSON.license"
