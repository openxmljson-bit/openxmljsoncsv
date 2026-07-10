"""Run all three parser validators (SPEC §15).

Usage: python run_all.py [--quick]
--quick runs 500/400/500 fuzz cases instead of the full 5000/4000/5000.
"""

from __future__ import annotations

import sys

import validate_csv
import validate_json
import validate_xml


def main() -> None:
    quick = "--quick" in sys.argv
    n_json, n_xml, n_csv = (500, 400, 500) if quick else (5000, 4000, 5000)

    print("== JSON vs stdlib json ==")
    validate_json.run_edge_cases()
    validate_json.run_fuzz(n_json)

    print("== XML vs xml.etree ==")
    validate_xml.run_edge_cases()
    validate_xml.run_fuzz(n_xml)

    print("== CSV vs stdlib csv ==")
    validate_csv.run_edge_cases()
    validate_csv.run_fuzz(n_csv)

    print("\nALL VALIDATORS PASS")


if __name__ == "__main__":
    main()
