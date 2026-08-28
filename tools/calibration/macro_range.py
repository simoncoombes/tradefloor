"""What range the endogenous macro state actually reaches, per candidate.

The `macro-range` gap says the economy left to itself stays in a moderate
band: endogenous inflation peaks at 4.0% on every seed with sd 1.2 around a
mean of 2.0%, against US CPI year-on-year 2015-2025 with sd 2.18, a peak of
9.0% in June 2022 and monthly AR(1) 0.978 against the model's 0.958.

`gate_batch.py` scores what a candidate COSTS on the equity panel. It does not
measure what the candidate BUYS, because the panel is measured at a flat VIX
over 252 and 504 days and the macro range is a five-year property of the
endogenous chain. A round that gates the cost without measuring the gain is
half a result: it can only ever reject.

This runs the chain with no scenario and no pins, records `inflation_rate` at
every close, and reports the peak, the sd, the mean and the monthly AR(1) that
the gap compares against. It also counts the days the central bank's crisis
cadence could fire, which is the gap's second half: the bank pulls its next
meeting in to 21-30 days when a decision leaves it more than 2pp behind an
inflation rate above 4%, a path that fires in 22.0% of the 11,898
central-bank cases in the parity corpus and that a default run cannot reach
because inflation does not get there.

Usage:
    python tools/calibration/macro_range.py --candidates c.json \\
        --seeds 30 --years 5 --workers 190 --out macro.json
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import pathlib
import statistics
import sys

import pretium as pt

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import gate_pick  # noqa: E402

#: The real side the gap compares against, from the gap's own detail:
#: US CPI year-on-year 2015-2025 (FRED CPIAUCSL).
REAL = {"peak": 9.0, "sd": 2.18, "mean": 2.87, "ar1": 0.978}

#: The bank pulls its meeting in when a decision leaves it more than this far
#: behind an inflation rate above this level. Both are read from the gap's
#: statement rather than from the engine, because the Python surface does not
#: export them and a second copy of a constant is a second thing to drift.
CRISIS_INFLATION = 4.0
CRISIS_GAP_PP = 2.0

UNIVERSE_N, UNIVERSE_SEED = 40, 111


def _job(spec):
    label, base, ov, seed, days = spec
    model = gate_pick.model(base, ov)
    u = pt.Universe.random(UNIVERSE_N, seed=UNIVERSE_SEED)
    e = pt.Engine(seed=seed, universe=u, model=model)
    infl, behind = [], 0
    for _ in range(days):
        e.open_market()
        e.run_session(9, 30, 3, 390)
        e.close_market()
        ec = e.state_snapshot()["economy"]
        # `state_snapshot()["economy"]` is in PERCENT, not fractions: a fresh
        # run reads inflation 2.22, federal funds 2.5, corporate bond yield
        # 4.56, and the `inflation_ceiling` dial is 6.0. Scaling by 100 here
        # put the peak at 408% and pinned every candidate to its own clamp,
        # which is the shape of an answer and none of the content.
        i = ec["inflation_rate"]
        infl.append(i)
        if i > CRISIS_INFLATION and (i - ec["federal_funds_rate"]) > CRISIS_GAP_PP:
            behind += 1
    return label, seed, infl, behind


def _ar1(xs: list[float]) -> float:
    """Lag-1 autocorrelation of the monthly series, as the gap reports it."""
    monthly = xs[::21]
    if len(monthly) < 3:
        return float("nan")
    m = statistics.fmean(monthly)
    num = sum((a - m) * (b - m) for a, b in zip(monthly, monthly[1:]))
    den = sum((x - m) ** 2 for x in monthly)
    return num / den if den else float("nan")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", required=True)
    ap.add_argument("--seeds", type=int, default=30)
    ap.add_argument("--seed-start", type=int, default=None,
                    help="first seed of a disjoint confirmation block")
    ap.add_argument("--years", type=int, default=5)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    cands = json.loads(pathlib.Path(args.candidates).read_text(encoding="utf-8"))
    days = args.years * 252
    if args.seed_start is None:
        seeds = list(gate_pick.TRAIN[: args.seeds])
    else:
        seeds = list(range(args.seed_start, args.seed_start + args.seeds))
        if set(seeds) & set(gate_pick.TRAIN):
            sys.exit("the confirmation block overlaps the calibration seeds")
    jobs = [(c["label"], c["base"], c["overrides"], s, days)
            for c in cands for s in seeds]
    print(f"{len(cands)} candidates x {len(seeds)} seeds x {days} days "
          f"= {len(jobs)} runs on {args.workers} workers", flush=True)

    got: dict[str, list] = {}
    # A pool, and therefore the __main__ guard below: macOS spawns rather than
    # forks, so a module-level pool re-imports this module in every worker.
    with mp.Pool(args.workers) as pool:
        for n, (label, seed, infl, behind) in enumerate(
                pool.imap_unordered(_job, jobs, chunksize=1), 1):
            got.setdefault(label, []).append((infl, behind))
            if n % 25 == 0:
                print(f"  {n}/{len(jobs)}", flush=True)

    out = {"real": REAL, "seeds": len(seeds), "years": args.years, "candidates": {}}
    for c in cands:
        runs = got[c["label"]]
        peaks = [max(r[0]) for r in runs]
        sds = [statistics.pstdev(r[0]) for r in runs]
        means = [statistics.fmean(r[0]) for r in runs]
        ars = [_ar1(r[0]) for r in runs]
        crisis_days = [r[1] for r in runs]
        out["candidates"][c["label"]] = {
            "overrides": c["overrides"],
            "peak_median": statistics.median(peaks),
            "peak_max": max(peaks),
            "sd_median": statistics.median(sds),
            "mean_median": statistics.median(means),
            "ar1_median": statistics.median(ars),
            "crisis_day_pct": 100.0 * statistics.fmean(crisis_days) / days,
            "seeds_reaching_4pct": sum(1 for p in peaks if p > CRISIS_INFLATION),
        }
        r = out["candidates"][c["label"]]
        print(f"{c['label']:<20} peak {r['peak_median']:5.2f}  sd {r['sd_median']:4.2f}  "
              f"mean {r['mean_median']:4.2f}  ar1 {r['ar1_median']:5.3f}  "
              f"crisis-days {r['crisis_day_pct']:4.1f}%  "
              f"seeds>4% {r['seeds_reaching_4pct']}/{len(runs)}", flush=True)

    pathlib.Path(args.out).write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"wrote {args.out}", flush=True)


if __name__ == "__main__":
    main()
