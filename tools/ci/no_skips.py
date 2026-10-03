"""Fail when a pytest run skipped anything.

    python -m pytest tests/test_examples.py --junitxml=report.xml
    python tools/ci/no_skips.py report.xml

pytest exits 0 when a test skips. For the notebook tests that is the
difference between "every notebook ran" and "none did":
`tests/test_examples.py` skips the executions without
TRADEFLOOR_SLOW_TESTS, and skips each one again without `nbclient`, and
both leave a green run. So the CI job that executes the examples reads the
report after pytest and fails on any skip, naming each one with its reason.
A run that collected no tests fails too.
"""

from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET


def skipped(path: str) -> tuple[int, list[str]]:
    """How many test cases the report holds, and each skip as a line."""
    root = ET.parse(path).getroot()
    cases = list(root.iter("testcase"))
    out = []
    for case in cases:
        for skip in case.findall("skipped"):
            name = f"{case.get('classname', '')}::{case.get('name', '')}"
            out.append(f"{name}: {skip.get('message', '').strip()}")
    return len(cases), out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("reports", nargs="+", help="junit XML files from pytest")
    args = ap.parse_args(argv)
    total, found = 0, []
    for path in args.reports:
        count, lines = skipped(path)
        total += count
        found += lines
    for line in found:
        print(f"SKIPPED  {line}")
    if total == 0:
        print("no test cases in the report; nothing ran")
        return 1
    if found:
        print(f"{len(found)} of {total} tests skipped. Each one is a test "
              f"that reported green without running.")
        return 1
    print(f"all {total} tests ran")
    return 0


if __name__ == "__main__":
    sys.exit(main())
