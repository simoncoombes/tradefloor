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
longer window. Naming which is which is the point.

    python tools/calibration/long_horizon.py --seeds 30 --workers 94 \
        --out long-horizon.json
"""
from __future__ import annotations

import argparse
import json
import statistics as st
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent.parent / "python"))

import pretium as pt  # noqa: E402
from pretium import envelope, facts  # noqa: E402

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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--preset", default=None,
                    help="default: whatever ships")
    ap.add_argument("--seeds", type=int, default=30)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    preset = args.preset or pt.ModelParams.from_preset().fingerprint
    seeds = list(range(101, 101 + args.seeds))
    jobs = [(preset, d, s) for d in HORIZONS for s in seeds]
    print(f"{preset}: {len(HORIZONS)} horizons x {len(seeds)} seeds "
          f"= {len(jobs)} panels, {args.workers} workers", flush=True)
    # A 2520-day panel is ten times a 252-day one, so the long horizons are
    # nearly all of the work. Submit longest first: the tail of a pool is
    # whatever is still running, and a 2520 starting last idles 93 cores.
    jobs.sort(key=lambda j: -j[1])

    acc: dict[int, list] = {d: [] for d in HORIZONS}
    done = 0
    with ProcessPoolExecutor(args.workers) as ex:
        for days, seed, row in ex.map(one, jobs):
            acc[days].append(row)
            done += 1
            if done % 25 == 0:
                print(f"  ... {done}/{len(jobs)}", flush=True)

    med = {d: {k: st.median([r[k] for r in acc[d]]) for k in facts.REAL_MARKETS}
           for d in HORIZONS}

    print(f"\n{'statistic':26}" + "".join(f"{d:>10}" for d in HORIZONS)
          + "   drift 504->2520", flush=True)
    for k in sorted(facts.REAL_MARKETS):
        far, near = med[2520][k], med[504][k]
        drift = "n/a" if near == 0 else f"{(far - near) / abs(near):+.1%}"
        print(f"{k:26}" + "".join(f"{med[d][k]:10.4f}" for d in HORIZONS)
              + f"   {drift:>16}", flush=True)

    # Graded against the 504 bands, which is the WRONG ruler at 2520 and is
    # labelled as such. Printed because a reader will ask, and because a
    # statistic leaving a band it was inside at 504 is the signal worth
    # having even when the band is not horizon-matched.
    print("\nAgainst BANDS_504 (not horizon-matched -- indicative only):",
          flush=True)
    for d in HORIZONS:
        out = [k for k in facts.REAL_MARKETS
               if not (envelope.BANDS_504[k][0] <= med[d][k]
                       <= envelope.BANDS_504[k][1])]
        print(f"  {d:5}d: {14 - len(out)}/14 in band"
              + (f", out: {', '.join(sorted(out))}" if out else ""), flush=True)

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
