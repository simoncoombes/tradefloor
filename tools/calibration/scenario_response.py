"""The guard for behaviour the realism panel cannot see.

Why this exists. On 2026-08-22 the pt-v3 era boundary halved a documented
headline feature -- "VIX is a volatility lever" -- and nothing in the
calibration noticed. Every guard this project runs reads the ten-statistic
panel, and the panel is measured at a single flat VIX on one horizon. A
vector can therefore be perfect on all four axes while the market stops
responding to the scenario machinery entirely, which is what happened:
`L_real` went to 0.0058 and the VIX 5->65 volatility lever fell from x2.51
to x1.54 in the same step.

The cause is now measured and it is not subtle. The market factor's
variance reverts to a target that scales with `(VIX/15)^2`, and how fast it
gets there is `market_vol_alpha + market_vol_beta`:

    pt-v1    persistence 0.950000   half-life  13.5 d   lever x2.51
    capped   persistence 0.989058   half-life  63.0 d   lever x1.97
    pt-v3    persistence 0.996402   half-life 192.3 d   lever x1.54

A twenty-day VIX spike cannot move a variance that reverts over 192 days.
And CALIBRATION-FOLLOWUPS.md §2 established that the 252-day panel cannot
DISTINGUISH those half-lives -- `L_real` is 0.0000 on all three 252-day axes
at both 63 and 192 days. So the panel is indifferent to the exact quantity
that sets this feature, which is the definition of a blind spot: the search
was free to spend it, and it did, for nothing.

What this measures, all against the same vector:

  1. the held-VIX lever -- annualised volatility at VIX 5, 15, 25, 45, 65,
     and the 5->65 ratio. The README quotes 49/59/107/124 at pt-v1.
  2. the shock response -- realised volatility under `vix_shock` over the
     same run flat at VIX 15, on IDENTICAL draws, so the ratio is the
     mechanism and not the seeds.
  3. the crisis correlation blend -- mean pairwise correlation calm against
     VIX 45, the "diversification fails under stress" claim.

Thresholds are deliberately NOT set here. This measurement was taken after
the regression it describes, so any bar chosen now would be a description of
a known answer rather than a criterion -- the distinction CALIBRATION-PTV2.md
§10.1 exists to keep. It reports the numbers and the ratio against a named
reference vector; the owner fixes the bars before it becomes a gate.

    ../../.venv/bin/python scenario_response.py \
        --vector results/calibrate-pt-v3-converged-2026-08-22.json \
        --reference pt-v1 \
        --out results/scenario-response-pt-v3.json
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import time

import pretium
import pretium.facts as facts
from pretium import Scenario
from pretium.scenario import run_scenario

HELD_VIX = (5.0, 15.0, 25.0, 45.0, 65.0)
SHOCK_SEEDS = (3, 5, 7)
UNIVERSE_N, UNIVERSE_SEED = 20, 11
HELD_DAYS = 120
SHOCK_DAYS, SHOCK_TICKS = 40, 80


def load_vector(spec: str):
    """`pt-v1`/`pt-v2`/`pt-v3`, or a certificate path (optionally `#name`)."""
    if "/" not in spec and not spec.endswith(".json"):
        pretium.ModelParams.from_preset(spec)   # refuses an unknown name
        return spec, spec
    path, _, name = spec.partition("#")
    with open(path, encoding="utf-8") as handle:
        doc = json.load(handle)
    vector = doc["vectors"][name]["vector"] if name else doc["best_vector"]
    return spec, pretium.ModelParams.from_preset("pt-v1", **vector)


def pooled_vol(scenario, seed, model) -> float:
    import pyarrow as pa
    engine = run_scenario(scenario, seed=seed,
                          universe=pretium.Universe.random(UNIVERSE_N,
                                                           seed=UNIVERSE_SEED),
                          days=SHOCK_DAYS, ticks_per_day=SHOCK_TICKS,
                          record=True, model=model)
    bars = pa.table(engine.bars(grain="day")).to_pydict()
    series: dict = {}
    for ticker, close in zip(bars["instrument_id"], bars["close"]):
        series.setdefault(ticker, []).append(close)
    returns = []
    for closes in series.values():
        returns += [math.log(closes[i + 1] / closes[i])
                    for i in range(len(closes) - 1)]
    return statistics.pstdev(returns)


def measure_vector(model) -> dict:
    universe = pretium.Universe.random(UNIVERSE_N, seed=UNIVERSE_SEED)

    held = {}
    for vix in HELD_VIX:
        panel = facts.measure(seed=3, universe=universe, days=HELD_DAYS,
                              scenario=Scenario().hold(vix=vix), model=model)
        held[vix] = {
            "annualised_vol_pct": panel["annualised_vol_pct"],
            "cross_sectional_corr": panel["cross_sectional_corr"],
        }

    shock = Scenario.vix_shock(calm=15.0, peak=45.0, at=10, over=20)
    flat = Scenario().hold(vix=15.0)
    ratios = {}
    for seed in SHOCK_SEEDS:
        ratios[seed] = pooled_vol(shock, seed, model) / pooled_vol(flat, seed,
                                                                   model)

    lo, hi = HELD_VIX[0], HELD_VIX[-1]
    return {
        "held_vix": {str(k): v for k, v in held.items()},
        "vol_lever": held[hi]["annualised_vol_pct"]
        / held[lo]["annualised_vol_pct"],
        "corr_blend": held[45.0]["cross_sectional_corr"]
        / held[15.0]["cross_sectional_corr"],
        "shock_ratio": {str(k): v for k, v in ratios.items()},
        "shock_ratio_median": statistics.median(ratios.values()),
    }


def persistence(model) -> dict:
    """The quantity that sets all of it, reported so the cause is on the page."""
    if isinstance(model, str):
        model = pretium.ModelParams.from_preset(model)
    total = model.market_vol_alpha + model.market_vol_beta
    return {
        "market_vol_alpha": model.market_vol_alpha,
        "market_vol_beta": model.market_vol_beta,
        "factor_variance_persistence": total,
        "half_life_days": (math.log(0.5) / math.log(total)
                           if 0.0 < total < 1.0 else None),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--vector", required=True,
                        help="preset name, or certificate path[#vector]")
    parser.add_argument("--reference", default="pt-v1",
                        help="preset or certificate to report against")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    started = time.perf_counter()
    out = {}
    for label, spec in (("vector", args.vector), ("reference", args.reference)):
        name, model = load_vector(spec)
        out[label] = {"spec": name, **persistence(model),
                      **measure_vector(model)}

    v, r = out["vector"], out["reference"]
    print(f"{'':22s} {'vector':>12s} {'reference':>12s}")
    print(f"{'spec':22s} {v['spec'][:12]:>12s} {r['spec'][:12]:>12s}")
    print(f"{'persistence':22s} {v['factor_variance_persistence']:>12.6f} "
          f"{r['factor_variance_persistence']:>12.6f}")
    print(f"{'half-life (days)':22s} {v['half_life_days']:>12.1f} "
          f"{r['half_life_days']:>12.1f}")
    print()
    print(f"{'annualised vol %':22s}")
    for vix in HELD_VIX:
        k = str(vix)
        print(f"  VIX {vix:<18.0f} "
              f"{v['held_vix'][k]['annualised_vol_pct']:>12.1f} "
              f"{r['held_vix'][k]['annualised_vol_pct']:>12.1f}")
    print(f"{'vol lever 5->65':22s} {v['vol_lever']:>11.2f}x "
          f"{r['vol_lever']:>11.2f}x")
    print(f"{'corr blend 15->45':22s} {v['corr_blend']:>11.2f}x "
          f"{r['corr_blend']:>11.2f}x")
    print(f"{'shock ratio (median)':22s} {v['shock_ratio_median']:>11.3f}x "
          f"{r['shock_ratio_median']:>11.3f}x")
    print()
    print(f"vol lever retained: "
          f"{v['vol_lever'] / r['vol_lever']:.1%} of reference")
    print(f"shock response retained: "
          f"{(v['shock_ratio_median'] - 1) / (r['shock_ratio_median'] - 1):.1%} "
          f"of reference")
    print("\nNo pass/fail: thresholds are the owner's to fix BEFORE this "
          "becomes a gate (CALIBRATION-PTV2.md §10.1).")

    out["wall_seconds"] = time.perf_counter() - started
    out["method"] = {
        "held_vix": list(HELD_VIX), "held_days": HELD_DAYS,
        "shock_seeds": list(SHOCK_SEEDS),
        "shock": "vix_shock(calm=15, peak=45, at=10, over=20) over "
                 "hold(vix=15) on identical draws",
        "universe": f"Universe.random({UNIVERSE_N}, seed={UNIVERSE_SEED})",
    }
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(out, handle, indent=1, sort_keys=True)
        print(f"written: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
