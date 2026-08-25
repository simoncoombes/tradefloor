"""The guard for behaviour the realism panel cannot see.

Why this exists. On 2026-08-22 the pt-v3 era boundary moved a documented
headline feature -- "VIX is a volatility lever" -- and nothing in the
calibration noticed. Every guard this project runs reads the ten-statistic
panel, and the panel is measured at a single flat VIX on one horizon, so a
vector can be perfect on all four axes while the market's response to the
scenario machinery changes underneath it.

**Read the correction below before quoting any number from this file's
history.** This instrument shipped measuring its held-VIX half at ONE
hardcoded seed, and on 2026-08-23 it was re-measured at thirty. The
headline claim it was written to support -- that the era boundary halved
the lever -- did not survive:

                        1 seed (as shipped)    30 seeds (true)
    pt-v1 lever                  x2.51              x3.22
    pt-v3 lever                  x2.03              x3.07
    retained                       81%              95.2%

The steady-state lever is essentially intact. What IS real, and held at
both sample sizes, is the transient:

    shock response retained       31%              27.6%

So the defect is **transient response, not steady-state gain**, and that
distinction is physical rather than cosmetic. A 63-day half-life still
reaches the right level for a held VIX given enough days; what it cannot
do is track a twenty-day spike. The market factor's variance reverts to a
target scaling with `(VIX/15)^2`, and `market_vol_alpha + market_vol_beta`
sets how fast:

    pt-v1     persistence 0.950000   half-life  13.5 d   shock x1.225
    pt-v3     persistence 0.989058   half-life  63.0 d   shock x1.062

Both levers are also far below the real market's x6.16 (measured by
`real_vix_lever.py`: 17.2% annualised at VIX<12 against 106.1% at VIX 45+),
so "restore pt-v1's lever" was never the right target either.

CALIBRATION-FOLLOWUPS.md §2 established that the 252-day panel cannot
DISTINGUISH those half-lives -- `L_real` is 0.0000 on all three 252-day
axes at both 63 and 192 days. So the panel is indifferent to the quantity
that sets the transient, which is the blind spot this instrument exists
to cover.

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
import multiprocessing
import math
import statistics
import time

import pretium
import pretium.facts as facts
from pretium import Scenario
from pretium.scenario import run_scenario

HELD_VIX = (5.0, 15.0, 25.0, 45.0, 65.0)

#: Seeds for BOTH halves of the measurement. Thirty, not three.
#:
#: This instrument shipped reading three seeds, and three is how a whole
#: day of mistakes happened. The `holdout_horizon` axis was confounded
#: because it ran three seeds where every other axis runs thirty -- the
#: seed term on `abs_return_acf5` was +0.1287 against the horizon's
#: +0.0435, so the axis reported a seed effect three times larger than the
#: one it was named for. A three-seed decay measurement then produced a
#: finding that had to be retracted, and a three-seed tuning sweep landed
#: entirely inside its own noise.
#:
#: Every one of those was a sample-size problem wearing a different hat,
#: and each instrument was three-seed because thirty was slow on eight
#: cores. That is tooling shaped by a core count rather than by what the
#: measurement needs, so the fix is thirty seeds AND the worker pool that
#: makes thirty affordable -- not one or the other.
DEFAULT_SEEDS = tuple(range(101, 131))

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


def _held_job(job):
    """One (vix, seed) held-VIX panel, in a worker."""
    overrides, vix, seed = job
    model = _rebuild(overrides)
    panel = facts.measure(
        seed=seed, universe=pretium.Universe.random(UNIVERSE_N,
                                                    seed=UNIVERSE_SEED),
        days=HELD_DAYS, scenario=Scenario().hold(vix=vix), model=model)
    return vix, seed, panel["annualised_vol_pct"], panel["cross_sectional_corr"]


def _shock_job(job):
    """One seed's shocked/flat ratio, both runs on identical draws."""
    overrides, seed = job
    model = _rebuild(overrides)
    shock = Scenario.vix_shock(calm=15.0, peak=45.0, at=10, over=20)
    flat = Scenario().hold(vix=15.0)
    return seed, pooled_vol(shock, seed, model) / pooled_vol(flat, seed, model)


def _rebuild(overrides):
    """Workers get a dict, not a ModelParams: presets are named, vectors
    are overrides on pt-v1, and neither needs the object to survive a
    pickle."""
    if isinstance(overrides, str):
        return overrides
    return pretium.ModelParams.from_preset("pt-v1", **overrides)


def _as_overrides(model):
    if isinstance(model, str):
        return model
    ship = pretium.ModelParams.from_preset("pt-v1").to_dict()
    live = model.to_dict()
    return {k: v for k, v in live.items()
            if k in pretium.ModelParams.settable() and v != ship[k]}


def aggregate(held_rows, shock_rows, seeds) -> dict:
    """Medians across seeds, from already-measured held and shock rows.

    `held_rows` are `_held_job` results -- (vix, seed, vol, corr) -- and
    `shock_rows` are `_shock_job` results, (seed, ratio). Split out of
    `measure_vector` so the atlas survey driver (`atlas_survey.py`) can run
    the SAME jobs on its own worker pool and aggregate identically: its
    workers are pool processes, and a pool spawned inside a pool worker is
    refused by multiprocessing ("daemonic processes are not allowed to
    have children"), so calling `measure_vector` from one is not an
    option. One aggregation, two schedulers -- the numbers cannot drift.
    """
    held = {}
    for vix in HELD_VIX:
        vols = [v for (x, _s, v, _c) in held_rows if x == vix]
        corrs = [c for (x, _s, _v, c) in held_rows if x == vix]
        held[vix] = {
            "annualised_vol_pct": statistics.median(vols),
            "cross_sectional_corr": statistics.median(corrs),
            "seeds": len(vols),
            "annualised_vol_pct_sd": (statistics.stdev(vols)
                                      if len(vols) > 1 else 0.0),
        }
    ratios = {seed: r for seed, r in shock_rows}

    lo, hi = HELD_VIX[0], HELD_VIX[-1]
    return {
        "held_vix": {str(k): v for k, v in held.items()},
        "vol_lever": held[hi]["annualised_vol_pct"]
        / held[lo]["annualised_vol_pct"],
        "corr_blend": held[45.0]["cross_sectional_corr"]
        / held[15.0]["cross_sectional_corr"],
        "seeds": list(seeds),
        "shock_ratio": {str(k): v for k, v in ratios.items()},
        "shock_ratio_sd": (statistics.stdev(ratios.values())
                           if len(ratios) > 1 else 0.0),
        "shock_ratio_median": statistics.median(ratios.values()),
    }


def measure_vector(model, seeds=DEFAULT_SEEDS, workers: int = 8) -> dict:
    """Every number here is a median across `seeds`, not a single draw.

    The medians matter more than the parallelism: a lever is a ratio of
    two volatilities, so a one-seed lever divides one noisy number by
    another and reports the result to two decimals.
    """
    overrides = _as_overrides(model)
    held_jobs = [(overrides, vix, seed) for vix in HELD_VIX for seed in seeds]
    shock_jobs = [(overrides, seed) for seed in seeds]

    with multiprocessing.Pool(processes=max(1, workers)) as pool:
        held_rows = pool.map(_held_job, held_jobs)
        shock_rows = pool.map(_shock_job, shock_jobs)

    return aggregate(held_rows, shock_rows, seeds)


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
    parser.add_argument("--seeds", default=None,
                        help="comma list; default is the thirty training "
                             "seeds. Fewer is how this instrument used to "
                             "mislead -- see DEFAULT_SEEDS.")
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    seeds = (tuple(int(x) for x in args.seeds.split(",")) if args.seeds
             else DEFAULT_SEEDS)
    started = time.perf_counter()
    out = {}
    print(f"seeds: {len(seeds)}  workers: {args.workers}\n")
    for label, spec in (("vector", args.vector), ("reference", args.reference)):
        name, model = load_vector(spec)
        out[label] = {"spec": name, **persistence(model),
                      **measure_vector(model, seeds, args.workers)}

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
    # NOT printed as a bare percentage, deliberately. It divides two small
    # excesses over 1.0, so a difference of 0.024 in the shock ratio becomes an
    # eleven-point headline, and it has been quoted that way twice: once as a
    # 112.1% "gain" (CALIBRATION-FOLLOWUPS §39) and once as pt-v6 retaining
    # 16.7% against pt-v3's 27.6% (2026-08-25). Both were a real number quoted
    # at a resolution it does not have. Printing the excesses it is built from
    # makes the smallness visible where it is read.
    v_ex = v["shock_ratio_median"] - 1.0
    r_ex = r["shock_ratio_median"] - 1.0
    print(f"shock response: {v_ex:+.1%} above flat, reference {r_ex:+.1%}")
    if abs(r_ex) > 1e-9:
        print(f"  their ratio is {v_ex / r_ex:.1%}, and quoting that bare "
              "overstates a small gap.")
        print(f"  quote the shock ratios: {v['shock_ratio_median']:.3f}x "
              f"against {r['shock_ratio_median']:.3f}x.")
    print("\nNo pass/fail: thresholds are the owner's to fix BEFORE this "
          "becomes a gate (CALIBRATION-PTV2.md §10.1).")

    out["wall_seconds"] = time.perf_counter() - started
    out["method"] = {
        "held_vix": list(HELD_VIX), "held_days": HELD_DAYS,
        "seeds": list(seeds),
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
