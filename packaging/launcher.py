"""PyInstaller entry point (referenced by openxmljson.spec)."""

import os
import sys


def _selftest() -> int:
    """Import the app's modules and return an exit code — used by CI to verify
    the frozen bundle is complete (run the exe with OXJ_SELFTEST=1). Catching
    the error and using os._exit avoids the PyInstaller windowed bootloader's
    error dialog (which would hang CI) and yields a real process exit code.
    """
    try:
        import importlib

        for mod in (
            "openxmljson._native",
            "openxmljson.app",
            "openxmljson.model",
            "openxmljson.tree",
            "openxmljson.query",
            "openxmljson.docview",
            "openxmljson.welcome",
        ):
            importlib.import_module(mod)
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"OXJ selftest failed: {exc!r}\n")
        return 3
    return 0


if __name__ == "__main__":
    if os.environ.get("OXJ_SELFTEST"):
        os._exit(_selftest())

    from openxmljson.app import run

    raise SystemExit(run(sys.argv))
