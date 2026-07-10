"""Entry point: ``python -m openxmljson [file]``."""

import sys


def main() -> int:
    from openxmljson.app import run

    return run(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
