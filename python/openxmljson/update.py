"""Lightweight update check against GitHub Releases.

Qt-free: version parsing/comparison and the (blocking) release fetch live here
so they're unit-testable and can be run off the GUI thread. The app wires a
'Check for Updates' menu item and a quiet once-a-day startup check on top.
"""

from __future__ import annotations

import json
import os
import re
import ssl
import sys
import urllib.request
from typing import List, Optional

#: owner/repo the releases are published under.
GITHUB_REPO = "openxmljson-bit/openxmljsoncsv"
RELEASES_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"


def parse_version(v: str) -> tuple:
    """Parse a version like 'v1.2.3' / '1.2' into a comparable (maj, min, patch)
    tuple, ignoring any pre-release/build suffix."""
    nums = []
    for part in str(v).strip().lstrip("vV").split(".")[:3]:
        m = re.match(r"\d+", part)
        nums.append(int(m.group()) if m else 0)
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums)


def is_newer(latest: str, current: str) -> bool:
    """True if release ``latest`` is a newer version than ``current``."""
    return parse_version(latest) > parse_version(current)


def pick_asset(assets: List[dict], plat: Optional[str] = None) -> Optional[str]:
    """Return the download URL of the asset for this platform (.dmg on macOS,
    .exe on Windows), or None if there's no matching asset. ``assets`` is the
    list returned in a release's ``assets`` field."""
    plat = plat or sys.platform
    if plat == "darwin":
        ext = ".dmg"
    elif plat.startswith("win"):
        ext = ".exe"
    else:
        return None
    for a in assets:
        if str(a.get("name", "")).lower().endswith(ext):
            return a.get("url") or None
    return None


def fetch_latest_release(timeout: float = 6.0) -> Optional[dict]:
    """Fetch the latest GitHub release. Returns {'tag', 'url', 'name'} or None
    on any failure (offline, rate-limited, no releases). Blocking — call this
    off the GUI thread.

    Testing hook: set OXJ_UPDATE_FAKE_TAG (e.g. ``v9.9.9`` to force an update,
    or the current version to test 'up to date') to bypass the network. Set
    OXJ_UPDATE_FAKE_TAG=error to simulate a failed check.
    """
    fake = os.environ.get("OXJ_UPDATE_FAKE_TAG")
    if fake:
        if fake == "error":
            return None
        ver = fake.lstrip("vV")
        return {
            "tag": fake,
            "url": f"https://github.com/{GITHUB_REPO}/releases",
            "name": fake,
            "assets": [
                {"name": f"OPENXMLJSON-{ver}-universal2.dmg",
                 "url": f"https://github.com/{GITHUB_REPO}/releases"},
                {"name": f"OPENXMLJSON-{ver}-setup.exe",
                 "url": f"https://github.com/{GITHUB_REPO}/releases"},
            ],
        }
    req = urllib.request.Request(
        RELEASES_API,
        headers={
            "User-Agent": "OPENXMLJSON-updater",
            "Accept": "application/vnd.github+json",
        },
    )
    try:
        ctx = ssl.create_default_context()
        try:
            import certifi
            ctx.load_verify_locations(certifi.where())
        except Exception:
            pass
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    tag = data.get("tag_name") or ""
    if not tag:
        return None
    assets = [
        {"name": a.get("name", ""), "url": a.get("browser_download_url", "")}
        for a in (data.get("assets") or [])
    ]
    return {
        "tag": tag,
        "url": data.get("html_url") or f"https://github.com/{GITHUB_REPO}/releases",
        "name": data.get("name") or tag,
        "assets": assets,
    }
