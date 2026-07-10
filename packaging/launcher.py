"""PyInstaller entry point (referenced by openxmljson.spec)."""

import sys

from openxmljson.app import run

if __name__ == "__main__":
    raise SystemExit(run(sys.argv))
