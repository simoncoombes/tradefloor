"""The secant Jacobian of the realism panel over the settable surface.

CALIBRATION.md §4.3, built: at a chosen base vector, central differences
of all eight panel statistics with respect to every runtime-settable
parameter, under common random numbers, with the CRN guard asserted on
every evaluation. The h-sweep (`secant_hsweep.py`) established that the
secants are deterministic difference quotients with a stable plateau for
h at or above ~0.02-0.04 deviation units; the default step h = 0.05
lands inside that measured plateau rather than at a guessed value.

What one run produces, in one JSON:

- the base panel per seed — for the thirty-seed training list this IS
  phase 2's re-measured baseline panel, and the across-seed sd per
  statistic is the s_k re-estimate `pretium.loss` §6.1 asks for;
- per parameter: the bracket actually evaluated (central, or one-sided
  where a hard range pins the base to a boundary — pt-v1 ships
  `market_vol_vix_coupling` at 1.0, the top of its range), the per-seed
  secants for every panel statistic, and mean / median / across-seed sd
  aggregates;
- the wall-clock bill, because §7.2's economics are claims this tool
  either reproduces or corrects.

Secants are expressed per DEVIATION unit (§6.3: log units for scale
parameters, raw units for bounded shares and multiples), so a column
already means "panel movement per unit of regularised deviation" and
`identifiability.py` only has to divide rows by s_k to make the matrix
dimensionless.

Usage:

    .venv/bin/python tools/calibration/jacobian.py \
        --base pt-v1 --params all --seeds train --workers 8 \
        --out tools/calibration/results/jacobian-pt-v1-$(date +%F).json
"""

from __future__ import annotations

import argparse
import statistics
import time

import instrumentlib as lib

ALL_STATS = lib.PANEL_STATS + lib.SUPPLEMENTARY_STATS


def parse_seeds(text: str) -> list[int]:
    if text == "train":
        return list(lib.TRAIN_SEEDS)
    if text == "published":
        return list(lib.PUBLISHED_SEEDS)
    return [int(s) for s in text.split(",") if s]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--base", default="pt-v1", choices=["pt-v1", "legacy"])
    parser.add_argument("--params", default="all",
                        help="'all' (31), 'searched9', or a comma list")
    parser.add_argument("--h", type=float, default=0.05,
                        help="step in deviation units (see secant_hsweep)")
    parser.add_argument("--seeds", default="published",
                        help="'train' (101-130), 'published' (1-6), or a list")
    parser.add_argument("--days", type=int, default=lib.PANEL_DAYS)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    if args.params == "all":
        params = list(lib.PARAM_SPECS)
    elif args.params == "searched9":
        params = list(lib.SEARCHED_9)
    else:
        params = [p for p in args.params.split(",") if p]
        unknown = [p for p in params if p not in lib.PARAM_SPECS]
        if unknown:
            raise SystemExit(f"not settable parameters: {unknown}")

    seeds = parse_seeds(args.seeds)
    base = dict(lib.LEGACY_OVERRIDES) if args.base == "legacy" else {}
    ship = lib.shipped_values()

    def base_value(name: str) -> float:
        return base.get(name, ship[name])

    # ── Choose each parameter's bracket, falling back to one-sided where
    # a hard constraint pins the base to a boundary. ─────────────────────
    brackets: dict[str, dict] = {}
    for name in params:
        value = base_value(name)
        lo, hi, dev = lib.bracket(name, value, args.h)
        lo_vec, hi_vec = dict(base), dict(base)
        lo_vec[name], hi_vec[name] = lo, hi
        lo_bad = lib.feasibility_violation(lo_vec, ship)
        hi_bad = lib.feasibility_violation(hi_vec, ship)
        if lo_bad and hi_bad:
            raise SystemExit(
                f"{name}: both bracket sides infeasible at h={args.h} "
                f"({lo_bad}; {hi_bad})"
            )
        if hi_bad:
            brackets[name] = {"mode": "one-sided-down", "points": {"lo": lo},
                              "dev_distance": dev / 2.0, "note": hi_bad}
        elif lo_bad:
            brackets[name] = {"mode": "one-sided-up", "points": {"hi": hi},
                              "dev_distance": dev / 2.0, "note": lo_bad}
        else:
            brackets[name] = {"mode": "central", "points": {"lo": lo, "hi": hi},
                              "dev_distance": dev}
        brackets[name]["base_value"] = value
        brackets[name]["kind"] = lib.PARAM_SPECS[name]["kind"]

    jobs: list[tuple] = []
    labels: list[tuple[str, str]] = []  # (param or __base__, side)
    for seed in seeds:
        jobs.append((dict(base), seed, args.days, lib.PANEL_UNIVERSE_N,
                     lib.PANEL_UNIVERSE_SEED))
        labels.append(("__base__", "center"))
    for name in params:
        for side, value in brackets[name]["points"].items():
            vec = dict(base)
            vec[name] = value
            for seed in seeds:
                jobs.append((vec, seed, args.days, lib.PANEL_UNIVERSE_N,
                             lib.PANEL_UNIVERSE_SEED))
                labels.append((name, side))

    one_sided = [n for n in params if brackets[n]["mode"] != "central"]
    print(f"{len(params)} parameters, {len(seeds)} seeds, "
          f"{len(jobs)} evaluations on {args.workers} workers"
          + (f"; one-sided: {one_sided}" if one_sided else ""))

    wall_started = time.perf_counter()
    results = lib.run_pool(jobs, args.workers)
    wall = time.perf_counter() - wall_started
    print(f"wall clock: {wall:.1f}s "
          f"({len(jobs) / wall * 3600.0:,.0f} evaluations/hour)")

    draws = lib.assert_crn(results)
    print(f"CRN guard: draws_consumed identical across all vectors per seed")

    indexed: dict[tuple[str, str, int], dict] = {}
    for row, (name, side) in zip(results, labels):
        # The duplicate-free structure of the job list makes this safe:
        # each (name, side, seed) occurs exactly once.
        indexed[(name, side, row["seed"])] = row

    center_panels = {
        str(seed): indexed[("__base__", "center", seed)]["panel"]
        for seed in seeds
    }

    # s_k re-estimate from the base panels (the §6.1 discipline), via the
    # library's own estimator so the convention cannot drift.
    from pretium.loss import seed_sd_from_panels
    seed_sd = (seed_sd_from_panels(list(center_panels.values()))
               if len(seeds) >= 2 else None)

    columns: dict[str, dict] = {}
    for name in params:
        info = brackets[name]
        dev = info["dev_distance"]
        per_seed: dict[str, dict[str, float]] = {}
        for seed in seeds:
            if info["mode"] == "central":
                lo_p = indexed[(name, "lo", seed)]["panel"]
                hi_p = indexed[(name, "hi", seed)]["panel"]
            elif info["mode"] == "one-sided-down":
                lo_p = indexed[(name, "lo", seed)]["panel"]
                hi_p = indexed[("__base__", "center", seed)]["panel"]
            else:
                lo_p = indexed[("__base__", "center", seed)]["panel"]
                hi_p = indexed[(name, "hi", seed)]["panel"]
            per_seed[str(seed)] = {
                stat: (hi_p[stat] - lo_p[stat]) / dev for stat in ALL_STATS
            }
        aggregate = {}
        for stat in ALL_STATS:
            vals = [per_seed[str(s)][stat] for s in seeds]
            aggregate[stat] = {
                "mean": statistics.mean(vals),
                "median": statistics.median(vals),
                "sd": statistics.stdev(vals) if len(vals) > 1 else None,
            }
        columns[name] = {**info, "per_seed": per_seed, "aggregate": aggregate}

    # A compact table: mean secant per deviation unit, panel stats only.
    header = f"{'parameter':<28}" + "".join(
        f"{s[:14]:>16}" for s in lib.PANEL_STATS)
    print("\n" + header)
    for name in params:
        agg = columns[name]["aggregate"]
        print(f"{name:<28}" + "".join(
            f"{agg[s]['mean']:>16.4f}" for s in lib.PANEL_STATS))

    lib.write_json(args.out, {
        "provenance": lib.provenance(),
        "method": {
            "base": args.base,
            "base_overrides": base,
            "h": args.h,
            "params": params,
            "seeds": seeds,
            "days": args.days,
            "universe": f"Universe.random({lib.PANEL_UNIVERSE_N}, "
                        f"seed={lib.PANEL_UNIVERSE_SEED})",
            "workers": args.workers,
            "deviation_units": "log for scale parameters, raw for bounded "
                               "(CALIBRATION.md section 6.3)",
            "statistics": list(ALL_STATS),
        },
        "wall_seconds": wall,
        "evaluations": len(jobs),
        "draws_consumed_per_seed": {str(k): v for k, v in draws.items()},
        "base_panels": center_panels,
        "seed_sd": seed_sd,
        "columns": columns,
    })


if __name__ == "__main__":
    main()
