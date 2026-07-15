"""Tests for openxmljson.update version parsing/comparison (Qt-free)."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

from openxmljson import update  # noqa: E402


def test_parse_version():
    assert update.parse_version("v1.2.3") == (1, 2, 3)
    assert update.parse_version("0.2") == (0, 2, 0)
    assert update.parse_version("2.10.1-rc1") == (2, 10, 1)
    assert update.parse_version("garbage") == (0, 0, 0)


def test_is_newer():
    assert update.is_newer("0.2.2", "0.1.0")
    assert update.is_newer("v1.0.0", "0.9.9")
    assert update.is_newer("0.2.10", "0.2.2")     # numeric, not lexical
    assert not update.is_newer("0.1.0", "0.1.0")
    assert not update.is_newer("0.1.0", "0.2.0")
    # dev builds (0.0.0) treat any real release as newer
    assert update.is_newer("0.1.0", "0.0.0+dev")


def test_pick_asset():
    assets = [
        {"name": "OPENXMLJSON-0.2.2-universal2.dmg", "url": "u-dmg"},
        {"name": "OPENXMLJSON-0.2.2-setup.exe", "url": "u-exe"},
        {"name": "notes.txt", "url": "u-txt"},
    ]
    assert update.pick_asset(assets, "darwin") == "u-dmg"
    assert update.pick_asset(assets, "win32") == "u-exe"
    assert update.pick_asset(assets, "linux") is None
    assert update.pick_asset([], "darwin") is None
