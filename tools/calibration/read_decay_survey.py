"""Read the survey for the decay exponent: what moves it, and what it costs.

§55 to §57 approached this one hand-picked vector at a time, and §57 showed
nine points cannot separate a real trade from statistics jittering across
band edges. This asks the survey, now that decay_slope_504 is one of its
recorded outputs (§56a).

Real markets read -0.436. pt-v6 reads -0.917. Closer to zero is better.

This took `sys.argv[1]` and nothing else until 2026-08-25, which meant
`--help` opened a file called "--help" and died in a traceback. That is the
exact failure `tests/test_tool_help.py` exists to catch, and it was invisible
to it: that test collects scripts containing `add_argument`, so a tool with no
argument parser is not an entry point as far as it is concerned and goes
unchecked. Parsing arguments properly is what puts it back under the test.
"""

from __future__ import annotations

import argparse
import json

from pretium.atlas import Survey

#: Real markets read -0.436 against pt-v6's -0.917, and closer to zero is
#: better, so the frontier below maximises this rather than minimising it.
DEFAULT_TARGET = "decay_slope_504"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "survey",
        help="path to an atlas-survey.json written by atlas_survey.py collect",
    )
    p.add_argument(
        "--target",
        default=DEFAULT_TARGET,
        help="the output to read the decay exponent from (default: %(default)s)",
    )
    p.add_argument(
        "--chars",
        type=int,
        default=1400,
        help="how much of each block to print (default: %(default)s)",
    )
    return p


def show(label: str, obj: object, chars: int) -> None:
    print(f"\n=== {label} ===")
    print("  " + json.dumps(obj, default=str, indent=1)[:chars].replace("\n", "\n  "))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    survey = Survey.load(args.survey)
    outputs = survey.outputs()
    decay = [o for o in outputs if "decay" in o]
    print(f"  outputs: {len(outputs)}   decay present: {decay}")

    if args.target not in outputs:
        print(f"  no {args.target} recorded in this survey.")
        print("  A survey planned before §56a wired the decay exponent in will not")
        print("  have it, and there is nothing to read; re-run the survey rather")
        print("  than reaching for a different output.")
        return 1

    show(f"what moves {args.target}", survey.sensitivity(args.target), args.chars)
    show(
        "frontier: slope toward zero against lowest loss",
        survey.pareto({args.target: "max", "loss": "min"}),
        args.chars,
    )
    show("moves nothing", survey.unidentified([args.target, "loss"]), args.chars)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
