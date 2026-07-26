"""Shopify-only licensing / entitlement gate for OPENXMLJSON.

No backend server: the desktop app authenticates the customer through
Shopify's *hosted* login page (opened in the system browser) using the
New Customer Accounts OAuth 2.0 + PKCE flow, then reads the customer's
subscription/order status through the Customer Account GraphQL API and shows
it read-only. If the customer has no active entitlement, the app sends them
to the storefront (``STORE_URL``).

Security: NOTHING secret is embedded. The Client ID and Shop ID are public
identifiers loaded from the environment (with sane defaults); the only stored
secret is the customer's own refresh token, kept in the OS keyring. There is
no Admin API token in the app (that would require a backend).

This subpackage is self-contained and is NOT wired into app startup — import
and call it explicitly (see licensing/README.md).
"""

from openxmljson.licensing.config import ApiConfig  # noqa: F401
from openxmljson.licensing.client import (  # noqa: F401
    Entitlement,
    LicenseClient,
    LicenseError,
)
