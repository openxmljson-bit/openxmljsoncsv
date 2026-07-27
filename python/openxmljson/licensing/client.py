"""Desktop client for the OPENXMLJSON licensing endpoint.

Talks only to our own backend (api.openxmljson.com), never to Shopify. Two
verification modes, both server-verified:

  * OTP:   request_otp(email)  ->  verify_otp(email, code)
  * Key:   verify_key(email, key)

All calls return an ``Entitlement`` (or raise ``LicenseError`` with a
user-facing message). Pure ``requests``/stdlib — no Qt, so headlessly testable
by injecting a fake ``http`` object.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from openxmljson.licensing.config import ApiConfig


class LicenseError(Exception):
    pass


@dataclass
class Entitlement:
    valid: bool
    tier: str = ""
    email: str = ""
    expires_at: str = ""          # ISO8601 or "" if none
    status: str = ""              # active / paid / none / error
    reason: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_response(cls, data: Dict[str, Any]) -> "Entitlement":
        return cls(
            valid=bool(data.get("valid")),
            tier=str(data.get("tier") or ""),
            email=str(data.get("email") or ""),
            expires_at=str(data.get("expires_at") or data.get("expiry") or ""),
            status=str(data.get("status") or
                       ("active" if data.get("valid") else "none")),
            reason=str(data.get("reason") or ""),
            raw=data,
        )


class LicenseClient:
    def __init__(self, cfg: Optional[ApiConfig] = None, http=None):
        self.cfg = cfg or ApiConfig.from_env()
        self._http = http

    def _urllib_post(self, url, payload):
        """POST JSON with the stdlib (no `requests` dependency). Uses certifi
        for TLS verification when available. Returns (status, body_dict)."""
        import json as _json
        import ssl
        import urllib.error
        import urllib.request

        data = _json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, method="POST",
            headers={"Content-Type": "application/json",
                     "Accept": "application/json"})
        try:
            import certifi
            ctx = ssl.create_default_context(cafile=certifi.where())
        except Exception:
            ctx = ssl.create_default_context()
        try:
            with urllib.request.urlopen(
                    req, timeout=self.cfg.request_timeout, context=ctx) as r:
                status = r.getcode()
                raw = r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:   # 4xx/5xx carry a JSON body
            status = exc.code
            raw = exc.read().decode("utf-8", "replace") if exc.fp else ""
        except Exception as exc:                # network/DNS/TLS failure
            raise LicenseError(
                f"Couldn't reach the licensing server: {exc}") from exc
        try:
            body = _json.loads(raw) if raw else {}
        except ValueError:
            body = {}
        return status, body

    def _post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = self.cfg.url(path)
        if self._http is not None:      # injected fake in tests
            try:
                resp = self._http.post(
                    url, json=payload, headers={"Accept": "application/json"},
                    timeout=self.cfg.request_timeout)
            except Exception as exc:
                raise LicenseError(
                    f"Couldn't reach the licensing server: {exc}") from exc
            status = resp.status_code
            try:
                body = resp.json()
            except ValueError:
                body = {}
        else:
            status, body = self._urllib_post(url, payload)

        if status == 429:
            raise LicenseError(
                body.get("message")
                or "Too many attempts — please wait a minute and try again.")
        if status >= 400:
            raise LicenseError(
                body.get("message") or body.get("error")
                or f"Server error ({status}).")
        if not isinstance(body, dict):
            raise LicenseError("Unexpected response from the licensing server.")
        return body

    # -- OTP flow --------------------------------------------------------------
    def request_otp(self, email: str) -> None:
        """Ask the server to email a one-time code to ``email``."""
        email = (email or "").strip().lower()
        if "@" not in email:
            raise LicenseError("Enter a valid email address.")
        self._post("request-otp", {"email": email})

    def verify_otp(self, email: str, code: str) -> Entitlement:
        email = (email or "").strip().lower()
        code = (code or "").strip()
        if not code:
            raise LicenseError("Enter the code from your email.")
        return Entitlement.from_response(
            self._post("verify", {"email": email, "code": code}))

    # -- license-key flow ------------------------------------------------------
    def verify_key(self, email: str, key: str) -> Entitlement:
        email = (email or "").strip().lower()
        key = (key or "").strip()
        if not key:
            raise LicenseError("Enter your license key.")
        return Entitlement.from_response(
            self._post("verify", {"email": email, "licenseKey": key}))
