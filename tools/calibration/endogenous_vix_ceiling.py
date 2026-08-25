"""The fourth verdict, measured: endogenous VIX against the crisis trigger.

CALIBRATION.md §3.7 argued from source that the crisis-correlation
trigger was unreachable from inside the old model: the daily VIX target
tops out near 26 without exogenous shocks, against a trigger at 40, so
the blend could only ever be measured under `pin_macro`. The threshold
has since been promoted and recalibrated (25.5 on this build,
compile-time), which is the fix's half; this tool supplies the
instrument's half — the measurement that the ceiling is a property of
the ECONOMY side that no market-side parameter vector can lift:

- run long endogenous simulations (no pins, no shocks) under several
  market parameter vectors, including deliberately violent ones sitting
  at the corners of the search box, since the one feedback channel from
  market to VIX is the return-spike term;
- record the daily VIX, its per-run maximum, and the fraction of days
  above both the OLD trigger (40) and the CURRENT one (25.5);
- certify: if no vector's VIX ever approaches 40, the old model's
  crisis trigger was unreachable from inside at ANY searched market
  parameterisation — the §3.7 verdict, now measured across the box
  rather than argued from the target arithmetic — while the current
  threshold is crossed endogenously at a measurable rate.

`draws_consumed` is recorded per run but not asserted across vectors:
economy-side conditional draws are genuine model response under
parameter changes (§4.1 insulates the market schedule, not the economy
path), and this tool measures levels, not differences.

Usage:

    .venv/bin/python tools/calibration/endogenous_vix_ceiling.py \
        --days 2520 --seeds 1,2,3,4,5,6 --workers 8 \
        --out results/endogenous-vix-ceiling-$(date +%F).json
"""

from __future__ import annotations

import argparse
import time

import instrumentlib as lib

OLD_TRIGGER = 40.0
CURRENT_TRIGGER = 25.5

#: The vectors measured. "violent" sits at the loud corner of the search
#: box (maximum factor sigma, maximum shock response at held
#: stationarity, unfunded idiosyncratic scale) to give the return-spike
#: feedback its best case; "quiet" at the opposite corner, where a
#: depressed VIX baseline would reveal any downward sensitivity.
VECTORS: dict[str, dict[str, float]] = {
    "pt-v1": {},
    "legacy": dict(lib.LEGACY_OVERRIDES),
    "violent": {
        "market_factor_sigma": 0.064,       # 4x ship, box ceiling
        "garch_alpha": 0.40, "garch_beta": 0.55, "garch_gamma": 0.05,
        "idio_sigma_scale": 3.36,           # box ceiling
        "market_vol_alpha": 0.90, "market_vol_beta": 0.09,
        "market_vol_ceiling_multiple": 32.0,
    },
    "quiet": {
        "market_factor_sigma": 0.004,       # box floor
        "garch_alpha": 0.01, "garch_beta": 0.60, "garch_gamma": 0.0,
        "idio_sigma_scale": 0.21,           # box floor
        "market_vol_alpha": 0.01, "market_vol_beta": 0.10,
    },
}


def run_vix(job: tuple) -> dict:
    label, overrides, seed, days = job
    import pretium

    model = pretium.ModelParams.from_preset("pt-v1", **overrides)
    universe = pretium.Universe.random(lib.PANEL_UNIVERSE_N,
                                       seed=lib.PANEL_UNIVERSE_SEED)
    engine = pretium.Engine(seed=seed, universe=universe, model=model)
    vix_path = []
    for _ in range(days):
        engine.open_market()
        engine.run_session(9, 30, 3, 390)
        engine.close_market()
        vix_path.append(engine.state_snapshot()["economy"]["vix"])
    return {
        "label": label,
        "fingerprint": model.fingerprint,
        "seed": seed,
        "days": days,
        "draws_consumed": engine.draws_consumed,
        "max_vix": max(vix_path),
        "mean_vix": sum(vix_path) / len(vix_path),
        "days_above_current": sum(1 for v in vix_path if v > CURRENT_TRIGGER),
        "days_above_old": sum(1 for v in vix_path if v > OLD_TRIGGER),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--days", type=int, default=2520)
    parser.add_argument("--seeds", default="1,2,3,4,5,6")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    seeds = [int(s) for s in args.seeds.split(",") if s]
    ship = lib.shipped_values()
    for label, overrides in VECTORS.items():
        bad = lib.feasibility_violation(overrides, ship)
        if bad:
            raise SystemExit(f"vector {label!r} infeasible: {bad}")

    jobs = [(label, overrides, seed, args.days)
            for label, overrides in VECTORS.items() for seed in seeds]
    print(f"{len(jobs)} runs of {args.days} days on {args.workers} workers")
    wall_started = time.perf_counter()
    from concurrent.futures import ProcessPoolExecutor
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(run_vix, jobs, chunksize=1))
    wall = time.perf_counter() - wall_started

    summary: dict[str, dict] = {}
    print(f"\n{'vector':<10}{'max VIX':>9}{'mean':>7}"
          f"{'>25.5 (current)':>17}{'>40 (old)':>11}")
    for label in VECTORS:
        rows = [r for r in results if r["label"] == label]
        total_days = sum(r["days"] for r in rows)
        summary[label] = {
            "overrides": VECTORS[label],
            "fingerprint": rows[0]["fingerprint"],
            "max_vix": max(r["max_vix"] for r in rows),
            "max_vix_per_seed": {str(r["seed"]): r["max_vix"] for r in rows},
            "mean_vix": sum(r["mean_vix"] * r["days"] for r in rows)
            / total_days,
            "fraction_above_current": sum(
                r["days_above_current"] for r in rows) / total_days,
            "fraction_above_old": sum(
                r["days_above_old"] for r in rows) / total_days,
            "draws_consumed_per_seed": {
                str(r["seed"]): r["draws_consumed"] for r in rows},
        }
        s = summary[label]
        print(f"{label:<10}{s['max_vix']:>9.2f}{s['mean_vix']:>7.2f}"
              f"{s['fraction_above_current']:>16.2%}"
              f"{s['fraction_above_old']:>11.2%}")

    pooled_max = max(s["max_vix"] for s in summary.values())
    verdict = (
        f"endogenous VIX peaked at {pooled_max:.2f} across "
        f"{len(jobs)} runs of {args.days} days spanning the search box's "
        f"corners — the old trigger at {OLD_TRIGGER} was unreachable from "
        "inside the model at any tested market parameterisation, so the "
        "crisis blend it gated was structurally inert without pinned "
        "macro; the promoted threshold at "
        f"{CURRENT_TRIGGER} is crossed endogenously (fraction per vector "
        "above)."
        if pooled_max < OLD_TRIGGER else
        f"endogenous VIX reached {pooled_max:.2f} — ABOVE the old trigger; "
        "the section 3.7 source reading is wrong somewhere and that is "
        "the finding"
    )
    print(f"\nverdict: {verdict}")
    print(f"wall {wall:.0f}s")

    lib.write_json(args.out, {
        "provenance": lib.provenance(),
        "claim": {
            "verdict": verdict,
            "old_trigger": OLD_TRIGGER,
            "current_trigger": CURRENT_TRIGGER,
            "pooled_max_vix": pooled_max,
        },
        "method": {
            "days": args.days,
            "seeds": seeds,
            "universe": f"Universe.random({lib.PANEL_UNIVERSE_N}, "
                        f"seed={lib.PANEL_UNIVERSE_SEED})",
            "macro": "endogenous (no pins, no shocks)",
            "workers": args.workers,
        },
        "vectors": summary,
        "wall_seconds": wall,
    })


if __name__ == "__main__":
    main()
