"""Read the survey for the scenario-versus-horizon frontier.

The question this exists to answer, stated before the data arrives so the
answer cannot be fitted to it:

  Is "restoring the crisis transient costs long-horizon realism" a structural
  limit of this model class, or an artifact of the handful of configurations
  that have been tried by hand?

`docs/realism-envelope.md` Gap 5 says structural, citing a 504-day loss that
doubles, 0.9887 to 2.0164, when the fast variance component is capped at a
14-day half-life. `CALIBRATION-FOLLOWUPS.md` §14 reports the same mechanism
succeeding at 8 of 10 with `L_real` 0.0128 -- on the 252-day panel. Those are
two rulers on one experiment, not a disagreement, and neither is a search.

The survey is a search. It carries `vol_lever`, `shock_ratio_median` and
`corr_blend` alongside `loss_252` and `loss_504` for every vector, so the
frontier between them is a query rather than another hand-picked run.

Two cautions carried from the calibration record, both bought with real
mistakes:

  * **Do not quote "shock response retained".** §39: it divides two small
    excesses over 1.0, so shock ratios of 1.062 and 1.038 become an
    eleven-point headline. Read `shock_ratio_median` directly.
  * **Do not read a 504-day panel against 252-day bands.** The library refuses
    it, and this tool reads the two losses as separate columns for the same
    reason.
"""

from __future__ import annotations

import argparse
import json

from pretium.atlas import Survey

#: Real markets read 6.16x on the steady-state lever, measured on the 40-name
#: reference roster (17.2% annualised at VIX<12 against 106.1% at VIX 45+).
#: pt-v3 reads 3.07x and pt-v6 2.68x, so higher is better and nothing is close.
REAL_VOL_LEVER = 6.16

#: pt-v1's shock ratio, the only transient anyone has matched. Not a target:
#: there is no published figure for what a real transient should be.
PT_V1_SHOCK = 1.225


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("survey", help="atlas-survey.json from atlas_survey.py collect")
    p.add_argument("--chars", type=int, default=1600,
                   help="how much of each block to print (default: %(default)s)")
    return p


def show(label: str, obj: object, chars: int) -> None:
    print(f"\n=== {label} ===")
    print("  " + json.dumps(obj, default=str, indent=1)[:chars].replace("\n", "\n  "))


def attempt(label: str, fn, chars: int) -> None:
    """Run one query, reporting a refusal rather than dying on it.

    `pretium.atlas` refuses a rank correlation over too few usable rows, which
    is correct: five points describe the sample rather than the model. But
    `atlas_survey.py collect` builds a readable survey from whatever is on
    disk, ON PURPOSE, so that a run can be read mid-flight, and that is exactly
    when rows are scarce. Dying in a traceback there turns a partial answer
    into no answer.
    """
    try:
        show(label, fn(), chars)
    except Exception as exc:                       # noqa: BLE001
        print(f"\n=== {label} ===")
        print(f"  unavailable: {type(exc).__name__}: {exc}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    survey = Survey.load(args.survey)
    outputs = survey.outputs()
    print(f"  outputs: {len(outputs)}")

    need = ["vol_lever", "shock_ratio_median", "loss_252", "loss_504"]
    missing = [n for n in need if n not in outputs]
    if missing:
        print(f"  missing {missing}; this survey predates the scenario gates "
              "being recorded, and there is nothing to read. Re-run the survey "
              "rather than substituting a different output.")
        return 1

    attempt("what moves the steady-state lever",
            lambda: survey.sensitivity("vol_lever"), args.chars)
    attempt("what moves the shock transient",
            lambda: survey.sensitivity("shock_ratio_median"), args.chars)

    # The frontier proper. If Gap 5 is right, every vector with a high lever
    # carries a high loss_504 and this front is empty of anything interesting.
    attempt(f"frontier: lever toward {REAL_VOL_LEVER}x against 504-day loss",
            lambda: survey.pareto({"vol_lever": "max", "loss_504": "min"}),
            args.chars)
    attempt("frontier: transient against 504-day loss",
            lambda: survey.pareto({"shock_ratio_median": "max", "loss_504": "min"}), args.chars)

    # Both horizons at once is the claim that has never been tested: §14 held
    # 252 and Gap 5 measured 504, on the same experiment, separately.
    attempt("frontier: lever against BOTH horizons",
            lambda: survey.pareto({"vol_lever": "max", "loss_252": "min",
                        "loss_504": "min"}), args.chars)

    attempt("moves neither the lever nor either loss",
            lambda: survey.unidentified(["vol_lever", "loss_252", "loss_504"]), args.chars)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
