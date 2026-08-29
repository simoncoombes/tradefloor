"""`python -m tradefloor` -- read a scenario file without running a market.

A scenario is configuration, and configuration that can only be checked by
running a hundred-day simulation is configuration nobody checks. These
commands parse, validate, resolve and fingerprint a file in milliseconds:

    tradefloor scenario validate scenarios/liquidity_crisis.yml
    tradefloor scenario show     scenarios/oil_price_spike.yml
    tradefloor scenario diff     scenarios/rate_shock.yml scenarios/recession.yml
    tradefloor scenario targets

`python -m tradefloor ...` is the same thing without the console script, for
a checkout or an environment where scripts are not on PATH.

Exit status is 0 for a valid document and 1 for an invalid one, so this is
usable as a pre-commit or CI check over a directory of scenarios.
"""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from . import __version__, yaml_subset
from ._core import ValidationError
from .interventions import SCENARIO_SCHEMA, TARGETS, UNSUPPORTED
from .scenario import Scenario, _wrap


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tradefloor",
        description="Inspect tradefloor scenario files.",
    )
    parser.add_argument("--version", action="version",
                        version=f"tradefloor {__version__}")
    top = parser.add_subparsers(dest="group", required=True)

    scenario = top.add_parser(
        "scenario", help="validate, show and compare scenario files")
    sub = scenario.add_subparsers(dest="command", required=True)

    validate = sub.add_parser(
        "validate", help="parse and resolve a scenario, reporting its fingerprint")
    validate.add_argument("files", nargs="+")

    show = sub.add_parser(
        "show", help="print the resolved scenario, shocks above assumptions")
    show.add_argument("file")

    diff = sub.add_parser(
        "diff", help="what two scenarios say differently about each target")
    diff.add_argument("left")
    diff.add_argument("right")

    sub.add_parser(
        "targets", help="every intervention target, and what it actually reaches")

    args = parser.parse_args(argv)
    if args.command == "validate":
        return _validate(args.files)
    if args.command == "show":
        return _show(args.file)
    if args.command == "diff":
        return _diff(args.left, args.right)
    return _targets()


def _load(path: str) -> Scenario:
    """Read one file, and let a missing one say so.

    `Scenario.from_yaml` takes a path OR a document, which is right for a
    notebook and wrong here: a typo in a filename would be parsed as YAML and
    reported as a schema error. Reading the file first means "no such file"
    stays "no such file".
    """
    with open(path, "r", encoding="utf-8") as handle:
        text = handle.read()
    return Scenario.from_document(yaml_subset.read(text), source=path)


def _validate(paths: Sequence[str]) -> int:
    failed = 0
    for index, path in enumerate(paths):
        if index:
            print()
        try:
            scenario = _load(path)
        except (ValidationError, OSError) as exc:
            failed += 1
            print(f"{path}\nScenario invalid.\n\n{exc}")
            continue
        print(
            f"{path}\n"
            f"Scenario valid.\n\n"
            f"Name:          {scenario.name}\n"
            f"Schema:        v{SCENARIO_SCHEMA}\n"
            f"Shocks:        {len(scenario.shocks)}\n"
            f"Transmission:  {len(scenario.transmission)}\n"
            f"Fingerprint:   {scenario.fingerprint}"
        )
    return 1 if failed else 0


def _show(path: str) -> int:
    try:
        print(_load(path).describe())
    except (ValidationError, OSError) as exc:
        print(f"Scenario invalid.\n\n{exc}")
        return 1
    return 0


def _diff(left_path: str, right_path: str) -> int:
    try:
        left, right = _load(left_path), _load(right_path)
    except (ValidationError, OSError) as exc:
        print(f"Scenario invalid.\n\n{exc}")
        return 1

    print("SCENARIO DIFF")
    print(f"  A  {left.name}   {left.fingerprint}")
    print(f"  B  {right.name}   {right.fingerprint}")
    if left.fingerprint == right.fingerprint:
        print("\nThe two resolve to the same scenario.")
        return 0
    if not (left.interventions or right.interventions):
        print("\nNeither declares any interventions, so there is nothing to "
              "line up target by target. They differ in name, description or "
              "macro path; `show` prints both in full.")
        return 0

    def by_target(scenario: Scenario) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for item in scenario.interventions:
            out.setdefault(item.target, []).append(
                f"{item.describe()}  [{item.role}]")
        return out

    a, b = by_target(left), by_target(right)
    for target in sorted(set(a) | set(b)):
        print()
        print(target)
        for label, side in (("A", a), ("B", b)):
            lines = side.get(target)
            if not lines:
                print(f"  {label}: unchanged")
                continue
            for line in lines:
                print(f"  {label}: {line.strip()}")
    return 0


def _targets() -> int:
    print("INTERVENTION TARGETS\n")
    for name in sorted(TARGETS):
        target = TARGETS[name]
        print(f"{name}  ({target.units})")
        for line in _wrap(target.note):
            print(f"    {line}")
        print()
    print("NOT SUPPORTED, and why\n")
    for name in sorted(UNSUPPORTED):
        print(f"{name}")
        for line in _wrap(UNSUPPORTED[name] + "."):
            print(f"    {line}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
