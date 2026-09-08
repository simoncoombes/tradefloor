"""The panel past two years, which nobody has ever measured.

The `horizon` gap has said, through three eras, that "nothing beyond 504
days has been measured at all" and that "a five-year study is reading
numbers nobody has checked". That sentence has been true every day it has
been published. This measures the numbers.

**What this can and cannot establish.** It measures the MODEL at 756, 1260
and 2520 days on thirty seeds. It does NOT grade those readings, because
the real-market bands in `facts` were derived from real windows outside this
repository and there is no committed tool to re-derive them at a five-year
window. So the output is a DRIFT CURVE, not a band count: how each statistic
moves as the horizon lengthens, against its own 252- and 504-day readings.

That is enough to answer the question the gap actually raises, which is
whether the model is stable over a five-year run or quietly degenerates.
A statistic that walks monotonically away from its 504-day value is a
process with a trend in it; one that flattens has simply been measured at a
longer window. This names which is which.

    python tools/calibration/long_horizon.py --seeds 30 --workers 94 \
        --out long-horizon.json
"""
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent.parent / "python"))

import tradefloor as pt  # noqa: E402
from tradefloor import envelope, facts  # noqa: E402

HORIZONS = (252, 504, 756, 1260, 2520)
UNIVERSE_N = 40
UNIVERSE_SEED = 111


def one(job):
    """Module level and picklable: macOS spawns rather than forks."""
    preset, days, seed = job
    m = pt.ModelParams.from_preset(preset)
    row = facts.measure(seed=seed, universe=pt.Universe.random(UNIVERSE_N,
                                                               seed=UNIVERSE_SEED),
                        days=days, model=m)
    return days, seed, dict(row)


def tables(acc: dict[int, list[dict]]) -> tuple[dict[int, dict[str, float]],
                                                list[str]]:
    """The drift curve and the indicative band count, from per-seed panels.

    `acc` maps a horizon to its `facts.measure` panels, one per seed. Each
    graded row is read by its own estimator through `facts.aggregate_panels`:
    the median for a shape row, the mean for the level row, the pooled
    median for the pooled fear row, and a row absent from every panel at a
    horizon is printed as n/a rather than medianed over None.

    The band count is of the SHAPE rows, which is what a gate reads; the
    level and crisis rows are printed beside it with their own verdicts and
    never added to it, because the shipped preset holds them red on purpose
    and a total that folded them in would read fourteen of seventeen where
    fourteen of fourteen is the fact.
    """
    med = {d: facts.aggregate_panels(acc[d]) for d in HORIZONS}
    lines = [
        f"\n{'statistic':26}" + "".join(f"{d:>10}" for d in HORIZONS)
        + "   drift 504->2520",
    ]
    for k in sorted(facts.REAL_MARKETS):
        far, near = med[2520].get(k), med[504].get(k)
        drift = ("n/a" if far is None or near is None or near == 0
                 else f"{(far - near) / abs(near):+.1%}")
        cells = "".join(f"{med[d][k]:10.4f}" if k in med[d] else f"{'n/a':>10}"
                        for d in HORIZONS)
        lines.append(f"{k:26}" + cells + f"   {drift:>16}")

    # Graded against the 504 bands, which is the WRONG ruler at 2520 and is
    # labelled as such. Printed because a reader will ask, and because a
    # statistic leaving a band it was inside at 504 is the signal worth
    # having even when the band is not horizon-matched.
    lines.append("\nAgainst BANDS_504 (not horizon-matched -- indicative only):")

    def in_band(d: int, k: str) -> bool:
        return (k in med[d]
                and envelope.BANDS_504[k][0] <= med[d][k] <= envelope.BANDS_504[k][1])

    for d in HORIZONS:
        out = [k for k in facts.SHAPE if not in_band(d, k)]
        others = ", ".join(
            f"{k} {'in' if in_band(d, k) else 'OUT' if k in med[d] else 'n/a'}"
            for k in facts.LEVEL + facts.CRISIS)
        lines.append(
            f"  {d:5}d: {len(facts.SHAPE) - len(out)}/{len(facts.SHAPE)} "
            "shape rows in band"
            + (f", out: {', '.join(sorted(out))}" if out else "")
            + f"; level and crisis rows beside the count: {others}")
    return med, lines


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--preset", default=None,
                    help="default: whatever ships")
    ap.add_argument("--seeds", type=int, default=30)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--mem-gb", type=float, default=150.0,
                    help="memory budget for the pool; workers per horizon are "
                         "sized from it, because a long panel is large")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    preset = args.preset or pt.ModelParams.from_preset().fingerprint
    seeds = list(range(101, 101 + args.seeds))
    print(f"{preset}: {len(HORIZONS)} horizons x {len(seeds)} seeds "
          f"= {len(HORIZONS) * len(seeds)} panels", flush=True)

    # ONE POOL PER HORIZON, sized by MEMORY rather than by cores. This is the
    # opposite of `gate_batch`'s flat pool and the reason is measured: the
    # first launch of this tool put all five horizons in one 94-worker pool
    # and died with BrokenProcessPool inside a minute. A 504-day 40-name
    # panel is about 1.6 GB resident, so a 2520-day one is about 8 GB, and 94
    # of those is forty times the box.
    #
    # A flat pool is right when every task costs the same. These differ by
    # 10x, so the cheap horizons would finish instantly and the expensive
    # ones would OOM the box they were sharing.
    panel_gb = lambda days: 1.6 * days / 504.0
    acc: dict[int, list] = {d: [] for d in HORIZONS}
    for days in sorted(HORIZONS, reverse=True):
        workers = max(1, min(args.workers, int(args.mem_gb / panel_gb(days))))
        print(f"\n  {days}d: ~{panel_gb(days):.1f} GB per panel, "
              f"{workers} workers", flush=True)
        jobs = [(preset, days, s) for s in seeds]
        with ProcessPoolExecutor(workers) as ex:
            for d, seed, row in ex.map(one, jobs):
                acc[d].append(row)
        print(f"  {days}d: {len(acc[days])}/{len(seeds)} done", flush=True)

    med, lines = tables(acc)
    for line in lines:
        print(line, flush=True)

    if args.out:
        Path(args.out).write_text(json.dumps(
            {"preset": preset, "seeds": seeds, "universe_n": UNIVERSE_N,
             "universe_seed": UNIVERSE_SEED,
             "median": {str(d): med[d] for d in HORIZONS}},
            indent=2, default=float), encoding="utf-8")
        print(f"\nwrote {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
