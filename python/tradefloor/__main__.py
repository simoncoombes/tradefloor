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
    validate.add_argument("files", nargs="+",
                          help="paths, or names from `scenario list`")

    show = sub.add_parser(
        "show", help="print the resolved scenario, shocks above assumptions")
    show.add_argument("file")

    diff = sub.add_parser(
        "diff", help="what two scenarios say differently about each target")
    diff.add_argument("left")
    diff.add_argument("right")

    sub.add_parser(
        "list", help="the scenarios that ship with the library")

    sub.add_parser(
        "targets", help="every intervention target, and what it actually reaches")

    args = parser.parse_args(argv)
    if args.command == "validate":
        return _validate(args.files)
    if args.command == "show":
        return _show(args.file)
    if args.command == "diff":
        return _diff(args.left, args.right)
    if args.command == "list":
        return _list()
    return _targets()


def _load(target: str) -> Scenario:
    """A path to a file, or the name of one that ships with the library.

    `tradefloor scenario show liquidity_crisis` and `... show
    ./my-scenario.yml` both work, and neither is ambiguous: a name that
    resolves to a shipped scenario is one, and everything else is a path.

    Reading the file rather than handing the string to
    `Scenario.from_yaml` -- which takes a path OR a document -- keeps "no
    such file" as "no such file". Passed a document, `from_yaml` would parse
    a mistyped filename as YAML and report a schema error.
    """
    if target in Scenario.available():
        return Scenario.load(target)
    with open(target, "r", encoding="utf-8") as handle:
        text = handle.read()
    return Scenario.from_document(yaml_subset.read(text), source=target)


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


def _list() -> int:
    """The pack, with each scenario's own one-line description."""
    names = Scenario.available()
    if not names:
        print("No scenarios ship with this build.")
        return 1
    print("SCENARIOS SHIPPED WITH TRADEFLOOR\n")
    for name in names:
        scenario = Scenario.load(name)
        shocks = len(scenario.shocks)
        assumed = len(scenario.transmission)
        print(f"{name}")
        print(f"    {shocks} shock(s), {assumed} assumption(s)   "
              f"{scenario.fingerprint}")
        for line in _wrap(scenario.description or "(no description)"):
            print(f"    {line}")
        print()
    print("Load one with `tf.Scenario.load(\"<name>\")`, or read it with")
    print("`tradefloor scenario show <name>`.")
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
