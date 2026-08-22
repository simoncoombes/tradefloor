"""Verify the noise-free secant claim empirically, before anything trusts it.

CALIBRATION.md §4.2 claims that under common random numbers the secant

    D(h) = [m(theta + h) - m(theta - h)] / dev_distance(h)

is an exact difference quotient of a deterministic, piecewise-smooth map
— limited by truncation and kink density, not by Monte Carlo noise. The
claim is checkable, so this tool checks it instead of assuming it:

1. **Determinism**: one (vector, seed) evaluation repeated in different
   worker processes must agree to the bit on every statistic. If it does
   not, nothing downstream means anything.
2. **CRN alignment**: `draws_consumed` identical across all bracket
   points per seed — every shock one simulation saw, the other saw, in
   the same position.
3. **The halving ladder**: secants at h, h/2, h/4, ... If the panel
   difference were dominated by comparison noise of scale s_k (the
   across-seed sd), the secant would behave as noise/h — exploding as h
   halves and flipping sign at random. A deterministic piecewise-smooth
   map instead yields secants that settle, drifting only as truncation
   shrinks and kinks enter and leave the bracket. The tool reports both
   the measured ladder and the noise/h curve a no-CRN measurement would
   have produced, so the comparison is visible in one table.

The per-seed spread of the secant is itself a measurement worth having:
under CRN it reflects genuine seed-dependence of the local response, not
comparison noise, and dividing the hypothetical no-CRN sd by the
measured spread is the variance-reduction factor CRN buys at each h.

If the ladder explodes noise-fashion, THE INSTRUMENT IS WRONG and the
right output is this file's JSON saying so — that finding would
supersede everything else phase 2 was going to build.

Usage:

    .venv/bin/python tools/calibration/secant_hsweep.py \
        --base pt-v1 --seeds 1,2,3,4,5,6 --workers 8 \
        --out tools/calibration/results/secant-hsweep-$(date +%F).json
"""

from __future__ import annotations

import argparse
import statistics
import time

import instrumentlib as lib

#: Parameters exercised by default: both deviation classes, and the
#: mechanisms the four verdicts hang on (factor sigma, GARCH shape,
#: momentum, the funding scale, the factor-variance persistence).
DEFAULT_PARAMS = (
    "market_factor_sigma",
    "idio_sigma_scale",
    "garch_alpha",
    "momentum_theta",
    "market_vol_beta",
)

DEFAULT_LADDER = (0.16, 0.08, 0.04, 0.02, 0.01, 0.005)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--base", default="pt-v1", choices=["pt-v1", "legacy"])
    parser.add_argument("--params", default=",".join(DEFAULT_PARAMS))
    parser.add_argument("--ladder", default=",".join(str(h) for h in DEFAULT_LADDER))
    parser.add_argument("--seeds", default="1,2,3,4,5,6")
    parser.add_argument("--days", type=int, default=lib.PANEL_DAYS)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    params = [p for p in args.params.split(",") if p]
    ladder = [float(h) for h in args.ladder.split(",") if h]
    seeds = [int(s) for s in args.seeds.split(",") if s]
    base = dict(lib.LEGACY_OVERRIDES) if args.base == "legacy" else {}
    ship = lib.shipped_values()

    def base_value(name: str) -> float:
        return base.get(name, ship[name])

    # ── Build the job list: every bracket point, plus the base twice ─────
    # (the duplicate is the determinism check — same vector, same seed,
    # evaluated in two different worker processes).
    points: list[tuple[str, float, str, dict]] = []  # (param, h, side, overrides)
    ladders: dict[str, list[float]] = {}
    skipped: dict[str, list[float]] = {}
    for name in params:
        ladders[name] = []
        for h in ladder:
            lo, hi, _ = lib.bracket(name, base_value(name), h)
            sides = []
            for side, value in (("lo", lo), ("hi", hi)):
                vec = dict(base)
                vec[name] = value
                bad = lib.feasibility_violation(vec, ship)
                if bad:
                    # A rung whose bracket leaves the stationarity regime
                    # is skipped for THIS parameter, and the skip is
                    # recorded: the base sits near a hard constraint
                    # (pt-v1's GARCH persistence is 0.99), and a secant
                    # measured outside the regime would be a statement
                    # about a model the library's claims do not cover.
                    print(f"skipping {name} at h={h}: {bad}")
                    skipped.setdefault(name, []).append(h)
                    sides = []
                    break
                sides.append((name, h, side, vec))
            if sides:
                points.extend(sides)
                ladders[name].append(h)

    jobs: list[tuple] = []
    labels: list[tuple[str, float, str]] = []
    for name, h, side, vec in points:
        for seed in seeds:
            jobs.append((vec, seed, args.days, lib.PANEL_UNIVERSE_N,
                         lib.PANEL_UNIVERSE_SEED))
            labels.append((name, h, side))
    # base point, twice per seed
    for _ in range(2):
        for seed in seeds:
            jobs.append((dict(base), seed, args.days, lib.PANEL_UNIVERSE_N,
                         lib.PANEL_UNIVERSE_SEED))
            labels.append(("__base__", 0.0, "center"))

    print(f"{len(jobs)} evaluations ({len(points)} bracket points x "
          f"{len(seeds)} seeds + base twice), {args.workers} workers")
    wall_started = time.perf_counter()
    results = lib.run_pool(jobs, args.workers)
    wall = time.perf_counter() - wall_started
    print(f"wall clock: {wall:.1f}s")

    # ── Assertion 1: determinism to the bit ──────────────────────────────
    base_rows = [r for r, lab in zip(results, labels) if lab[0] == "__base__"]
    by_seed: dict[int, list[dict]] = {}
    for row in base_rows:
        by_seed.setdefault(row["seed"], []).append(row)
    for seed, rows in sorted(by_seed.items()):
        first, second = rows[0]["panel"], rows[1]["panel"]
        for key in first:
            assert first[key] == second[key] and (
                float(first[key]).hex() == float(second[key]).hex()
            ), (
                f"seed {seed}, {key}: two evaluations of the SAME vector "
                f"disagree ({first[key]!r} vs {second[key]!r}) — determinism "
                "is broken and no secant below means anything"
            )
    print("determinism: repeated base evaluations bit-identical on every "
          f"statistic for seeds {sorted(by_seed)}")

    # ── Assertion 2: the CRN guard ───────────────────────────────────────
    draws = lib.assert_crn(results)
    print(f"CRN guard: draws_consumed identical across all "
          f"{len(points) + 1} vectors per seed "
          f"({', '.join(str(s) for s in sorted(draws))})")

    # ── The secant ladders ───────────────────────────────────────────────
    indexed: dict[tuple[str, float, str, int], dict] = {}
    for row, (name, h, side) in zip(results, labels):
        indexed[(name, h, side, row["seed"])] = row

    stats = lib.PANEL_STATS
    import pretium.facts as facts

    report: dict[str, dict] = {}
    for name in params:
        per_h: dict[str, dict] = {}
        for h in ladders[name]:
            _, _, dev = lib.bracket(name, base_value(name), h)
            per_seed: dict[str, dict[str, float]] = {}
            for seed in seeds:
                lo_p = indexed[(name, h, "lo", seed)]["panel"]
                hi_p = indexed[(name, h, "hi", seed)]["panel"]
                per_seed[str(seed)] = {
                    stat: (hi_p[stat] - lo_p[stat]) / dev for stat in stats
                }
            agg = {}
            for stat in stats:
                vals = [per_seed[str(s)][stat] for s in seeds]
                agg[stat] = {
                    "mean": statistics.mean(vals),
                    "median": statistics.median(vals),
                    "sd": statistics.stdev(vals) if len(vals) > 1 else None,
                    # What a no-CRN central difference would carry at this
                    # h: two independent panel measurements differ with sd
                    # sqrt(2)*s_k, divided by the same dev distance.
                    "no_crn_noise_sd": (2 ** 0.5) * facts.SEED_SD[stat] / dev,
                }
            per_h[str(h)] = {"dev_distance": dev, "per_seed": per_seed,
                             "aggregate": agg}
        report[name] = per_h

    # ── The verdict per (param, stat): settle or explode ─────────────────
    # Ratio of successive-mean-secant change to the no-CRN noise scale at
    # the smaller h. Noise-dominated secants keep this near or above 1
    # (each halving redraws the value at the noise scale); deterministic
    # secants drive it toward 0.
    verdicts: dict[str, dict[str, dict]] = {}
    for name in params:
        verdicts[name] = {}
        hs = sorted(ladders[name], reverse=True)
        for stat in stats:
            pairs = []
            for bigger, smaller in zip(hs, hs[1:]):
                a = report[name][str(bigger)]["aggregate"][stat]["mean"]
                b = report[name][str(smaller)]["aggregate"][stat]["mean"]
                noise = report[name][str(smaller)]["aggregate"][stat][
                    "no_crn_noise_sd"]
                pairs.append({
                    "h_pair": (bigger, smaller),
                    "secants": (a, b),
                    "delta_over_no_crn_noise": abs(a - b) / noise,
                })
            verdicts[name][stat] = {
                "halvings": pairs,
                "max_delta_over_no_crn_noise": max(
                    p["delta_over_no_crn_noise"] for p in pairs),
            }

    worst = max(
        v["max_delta_over_no_crn_noise"]
        for per_stat in verdicts.values() for v in per_stat.values()
    )
    print(f"\nworst halving-step change across all (param, stat): "
          f"{worst:.4f}x the no-CRN noise sd at that h")
    print("(noise-dominated secants would hold this near or above 1; "
          "deterministic secants drive it toward 0)")

    # A compact table for the eyeball: mean secants down the ladder.
    for name in params:
        print(f"\n-- {name} (secants per deviation unit, mean over seeds)")
        header = f"{'h':>7}" + "".join(f"{s[:15]:>17}" for s in stats)
        print(header)
        for h in ladders[name]:
            row = report[name][str(h)]["aggregate"]
            print(f"{h:>7}" + "".join(
                f"{row[s]['mean']:>17.4f}" for s in stats))

    lib.write_json(args.out, {
        "provenance": lib.provenance(),
        "method": {
            "base": args.base,
            "base_overrides": base,
            "params": params,
            "ladder": ladder,
            "ladder_per_param": ladders,
            "skipped_rungs": skipped,
            "seeds": seeds,
            "days": args.days,
            "universe": f"Universe.random({lib.PANEL_UNIVERSE_N}, "
                        f"seed={lib.PANEL_UNIVERSE_SEED})",
            "workers": args.workers,
            "deviation_units": "log for scale parameters, raw for bounded "
                               "(CALIBRATION.md section 6.3)",
        },
        "wall_seconds": wall,
        "draws_consumed_per_seed": {str(k): v for k, v in draws.items()},
        "determinism": "repeated base evaluations bit-identical",
        "secants": report,
        "halving_verdicts": verdicts,
        "worst_delta_over_no_crn_noise": worst,
    })


if __name__ == "__main__":
    main()
