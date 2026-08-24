"""Turn an Atlas survey into a verdict on the mechanisms that ship inert.

Nineteen parameters across seven mechanisms ship at 0.0, and two of those
mechanisms -- endogenous jumps and news peer transfer -- have never been
measured at all. This reads a completed survey and classifies each one, so
the question "is this worth keeping" gets an answer with a measurement
attached rather than an impression.

# Why a classifier rather than a sensitivity table

`Survey.sensitivity` already ranks parameters. What it cannot do is tell you
which KIND of "moves nothing" you are looking at, and the four kinds imply
different actions (CALIBRATION-FOLLOWUPS §25):

1. **untestable here** -- the mechanism cannot fire in the measured
   configuration at any parameter value. `regime_stress_points` is the
   worked example: the 252-day panel never leaves the expansion phase,
   whose stress intensity is 0.0 by construction. §19 counted it among four
   measured levers when it had measured nothing.
2. **below screening noise** -- real but smaller than six seeds can see.
3. **conditional** -- flat marginally, live inside some region. This is what
   `where=` asks, and a marginal survey will not volunteer it.
4. **inert** -- nothing, anywhere the survey looked.

Only (4) is evidence for removal, and even then removal is an era boundary
rather than a cleanup: the preset fingerprint hashes the sorted parameter
NAMES, so dropping one changes every preset's fingerprint and orphans every
published manifest. The policy that follows is keep-at-zero, document the
measurement, and exclude from search parameter sets -- which recovers the
budget an inert dimension wastes at no fingerprint cost.

# What this script will not do

It does not decide. It prints a classification and the numbers behind it,
and every "inert" verdict it emits is a claim about the sampled ranges at
screening resolution -- never a claim about the mechanism. A parameter that
this calls inert and a parameter that does nothing are different things, and
the report says so on every line.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pretium import atlas  # noqa: E402

#: The seven mechanisms that ship inert, and what is already known about
#: each. The `status` line is not decoration: a survey verdict has to be
#: read against what has already been measured, or a "no effect" reading
#: silently overwrites a prior positive one.
MECHANISMS = {
    "endogenous jumps": {
        "params": ["jump_intensity_market", "jump_intensity_idio",
                   "jump_mean_market", "jump_sigma_market", "jump_sigma_idio"],
        "status": "NEVER MEASURED. The only built mechanism that could "
                  "plausibly move excess_kurtosis at 504 days (5.2 against a "
                  "real band of 7.1-22), which nothing else has touched.",
    },
    "news peer transfer": {
        "params": ["news_peer_weight", "news_peer_weight_down"],
        "status": "NEVER MEASURED.",
    },
    "size / spread curves": {
        "params": ["size_effect_smoothness", "spread_size_smoothness"],
        "status": "never swept. Blend weights between a step function and a "
                  "continuous curve; they remove tier cliffs rather than "
                  "targeting a panel statistic.",
    },
    "two-timescale variance": {
        "params": ["market_vol_slow_weight", "market_vol_slow_persistence",
                   "market_vol_slow_gain", "market_vol_slow_vix_damp"],
        "status": "REFUTED (§18): restores the VIX transient and doubles the "
                  "504-day loss; raising slow weight makes the horizon "
                  "monotonically worse. damp separately refuted (§14).",
    },
    "volume process": {
        "params": ["volume_persistence", "volume_innovation_sigma",
                   "volume_variance_gain"],
        "status": "swept: reaches the 'structurally unreachable' "
                  "volume_change_acf1, at the cost of volume_abs_return_corr "
                  "which currently passes.",
    },
    "universe stress memory": {
        "params": ["universe_stress_weight", "universe_stress_decay"],
        "status": "swept (§19): the ONLY lever that moves the transient "
                  "(+0.022) without going through variance persistence; "
                  "costs 0.9887 -> 1.2058 on the combined loss.",
    },
    "cycle reaches market": {
        "params": ["regime_stress_points"],
        "status": "UNTESTABLE on the standard panel: it never leaves the "
                  "expansion phase, whose stress intensity is 0.0 by "
                  "construction, so the cycle cannot reach the market at any "
                  "value. A 'no effect' reading here is an artifact.",
    },
}

#: Parameters whose mechanism cannot fire in the survey's configuration, so
#: a flat sensitivity is a fact about the measurement rather than about them.
UNTESTABLE_HERE = {"regime_stress_points"}


def classify(survey: atlas.Survey, param: str, outputs: list[str],
             threshold: float) -> dict:
    """The strongest signal this parameter shows across every output."""
    best_output, best_rho = None, 0.0
    for name in outputs:
        try:
            rho = survey.sensitivity(name)["correlations"].get(param, 0.0)
        except Exception:
            continue
        if abs(rho) > abs(best_rho):
            best_output, best_rho = name, rho

    if param in UNTESTABLE_HERE:
        verdict = "untestable here"
        action = ("keep; a flat reading is the panel's expansion phase, not "
                  "the mechanism. Needs a scenario that leaves expansion.")
    elif abs(best_rho) >= threshold:
        verdict = "moves something"
        action = "keep and SEARCH it"
    else:
        verdict = "below screening noise"
        action = ("keep at 0.0, document the measurement, EXCLUDE from "
                  "search parameter sets")
    return {"param": param, "verdict": verdict, "rho": best_rho,
            "output": best_output, "action": action}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--survey", required=True,
                    help="atlas-survey.json from a completed run")
    ap.add_argument("--threshold", type=float, default=None,
                    help="|rho| above which a parameter counts as moving "
                         "something. Default: the survey's own noise-scaled "
                         "threshold, which tightens as the sample grows.")
    args = ap.parse_args()

    s = atlas.Survey.load(args.survey)
    outputs = s.outputs()
    prov = s.provenance()
    n = prov.get("rows_measured", prov.get("rows", "?"))
    threshold = args.threshold
    if threshold is None:
        try:
            threshold = 3.0 / max(1.0, (float(n) - 1.0)) ** 0.5
        except Exception:
            threshold = 0.05

    print(f"survey: {args.survey}")
    print(f"  rows measured {n}   outputs {len(outputs)}   "
          f"threshold |rho| >= {threshold:.4f}")
    print(f"  dropped rows: {prov.get('rows_errored', '?')}  "
          f"(check their LOCATION, not just the count -- infrastructure "
          f"failures cluster in expensive corners and bias every marginal)")
    print()

    keep_search, exclude, untestable = [], [], []
    for mech, spec in MECHANISMS.items():
        print(f"=== {mech} ===")
        print(f"    prior: {spec['status']}")
        for p in spec["params"]:
            r = classify(s, p, outputs, threshold)
            mark = {"moves something": "MOVES",
                    "below screening noise": "flat ",
                    "untestable here": "n/a  "}[r["verdict"]]
            out = f" via {r['output']}" if r["output"] else ""
            print(f"    [{mark}] {p:28s} max|rho| {abs(r['rho']):.4f}{out}")
            {"moves something": keep_search,
             "below screening noise": exclude,
             "untestable here": untestable}[r["verdict"]].append(r)
        print()

    print("=== verdict ===")
    print(f"  keep and search       : {len(keep_search):2d}  "
          + ", ".join(r["param"] for r in keep_search))
    print(f"  keep, exclude from search: {len(exclude):2d}  "
          + ", ".join(r["param"] for r in exclude))
    print(f"  untestable here       : {len(untestable):2d}  "
          + ", ".join(r["param"] for r in untestable))
    print()
    print("  'below screening noise' means NO EFFECT DETECTABLE at this")
    print("  resolution over the sampled range. It is not a claim that the")
    print("  parameter does nothing, and it is not grounds for deletion:")
    print("  the preset fingerprint hashes the sorted parameter NAMES, so")
    print("  removing one changes every preset's fingerprint and orphans")
    print("  every published manifest. See CALIBRATION-FOLLOWUPS §25.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
