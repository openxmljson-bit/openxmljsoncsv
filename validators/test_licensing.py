"""Headless tests for the desktop licensing client/config/cache (Qt-free)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

from openxmljson.licensing.client import (  # noqa: E402
    Entitlement,
    LicenseClient,
    LicenseError,
)
from openxmljson.licensing.config import ApiConfig  # noqa: E402


class _FakeResp:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class _FakeHTTP:
    """Records the last request and returns a queued response."""

    def __init__(self, resp):
        self.resp = resp
        self.calls = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        if isinstance(self.resp, Exception):
            raise self.resp
        return self.resp


def _cfg():
    return ApiConfig(base_url="https://api.example.com",
                     store_url="https://shop.example.com",
                     request_timeout=5, cache_hours=24)


def test_config_url_join():
    assert _cfg().url("/verify") == "https://api.example.com/verify"
    assert _cfg().url("verify") == "https://api.example.com/verify"


def test_request_otp_posts_email():
    http = _FakeHTTP(_FakeResp(200, {"ok": True}))
    LicenseClient(_cfg(), http=http).request_otp("A@B.com ")
    assert http.calls[0]["url"] == "https://api.example.com/request-otp"
    assert http.calls[0]["json"] == {"email": "a@b.com"}


def test_request_otp_rejects_bad_email():
    http = _FakeHTTP(_FakeResp(200, {"ok": True}))
    try:
        LicenseClient(_cfg(), http=http).request_otp("nope")
    except LicenseError:
        return
    raise AssertionError("expected LicenseError for invalid email")


def test_verify_otp_parses_entitlement():
    http = _FakeHTTP(_FakeResp(200, {
        "valid": True, "tier": "Pro", "email": "a@b.com",
        "status": "active", "expires_at": "2027-01-01T00:00:00Z"}))
    ent = LicenseClient(_cfg(), http=http).verify_otp("a@b.com", "123456")
    assert isinstance(ent, Entitlement)
    assert ent.valid and ent.tier == "Pro"
    assert http.calls[0]["json"] == {"email": "a@b.com", "code": "123456"}


def test_verify_key_sends_licenseKey_and_product():
    http = _FakeHTTP(_FakeResp(200, {"valid": False, "reason": "expired"}))
    ent = LicenseClient(_cfg(), http=http).verify_key("a@b.com", " KEY ")
    assert ent.valid is False and ent.reason == "expired"
    # `product` scopes the key to this app so a NARIK key can't unlock it.
    assert http.calls[0]["json"] == {
        "email": "a@b.com", "licenseKey": "KEY", "product": "openxmljson"}


def test_foreign_tier_is_not_licensed():
    """A valid key issued for another product must not unlock this one."""
    from openxmljson.licensing import status

    assert status.tier_allowed("Essential") is True
    assert status.tier_allowed("Premium") is True
    assert status.tier_allowed("Unbxd") is True      # internal, all products
    assert status.tier_allowed("Narik") is False     # NARIK Edition
    assert status.tier_allowed(None) is False


def test_429_raises_friendly_error():
    http = _FakeHTTP(_FakeResp(429, {"message": "slow down"}))
    try:
        LicenseClient(_cfg(), http=http).verify_otp("a@b.com", "1")
    except LicenseError as exc:
        assert "slow down" in str(exc)
        return
    raise AssertionError("expected LicenseError on 429")


def test_network_error_wrapped():
    http = _FakeHTTP(OSError("boom"))
    try:
        LicenseClient(_cfg(), http=http).verify_key("a@b.com", "k")
    except LicenseError as exc:
        assert "reach the licensing server" in str(exc)
        return
    raise AssertionError("expected LicenseError on network failure")


def test_cache_round_trip(tmp_path, monkeypatch):
    # Force the file fallback (no keyring) into a temp dir.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setitem(sys.modules, "keyring", None)
    from openxmljson.licensing import cache

    cfg = _cfg()
    cache.clear(cfg)
    assert cache.load(cfg) is None
    cache.save(cfg, Entitlement(valid=True, tier="Pro", email="a@b.com",
                                status="active"))
    got = cache.load(cfg)
    assert got is not None and got.valid and got.tier == "Pro"
    cache.clear(cfg)
    assert cache.load(cfg) is None
