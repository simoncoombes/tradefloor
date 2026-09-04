"""What the certification owes to a sector-BALANCED roster.

`Universe.random()` deals sectors round-robin, so the certified 40 names put
four in each of four sectors and three in each of the other eight. No real
index is shaped like that: the S&P is roughly a third technology and the
Nasdaq more so.

The `roster-concentration` gap has carried three numbers since the pt-v3
era -- balanced 9, S&P-like 8, all-technology 7 -- and says plainly that
"those three counts are out of the TEN-statistic panel of the pt-v3 era and
have not been re-measured on pt-v10". They are now two eras and four
statistics out of date, and they were measured at six seeds.

This re-measures them: thirty seeds, the fourteen-statistic panel, the
shipped preset, at 252 and 504 days.

    python tools/calibration/roster_shapes.py --seeds 30 --workers 94 \
        --out roster.json
"""
from __future__ import annotations

import argparse
import json
import statistics as st
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent.parent / "python"))

import tradefloor as pt  # noqa: E402
from tradefloor import envelope, facts  # noqa: E402

N = 40

#: Sector mixes as counts out of forty. "balanced" is what `Universe.random`
#: deals and what the envelope certifies; the rest are shapes a reader might
#: actually hold. The S&P weights are rounded index sector weights, not a
#: precise replication -- the question is what CONCENTRATION does, and a
#: third-technology roster answers it whether or not the tail is exact.
SHAPES: dict[str, dict[str, int]] = {
    "balanced": {},                      # round-robin, the certified shape
    "sp500_like": {
        "technology": 13, "financial_services": 5, "healthcare": 5,
        "consumer_discretionary": 4, "industrials": 3, "consumer_staples": 3,
        "energy": 2, "telecommunications": 2, "utilities": 1,
        "real_estate": 1, "materials": 1,
    },
    "tech_heavy": {"technology": 24, "consumer_discretionary": 6,
                   "healthcare": 5, "financial_services": 5},
    "all_technology": {"technology": 40},
    "defensive": {"consumer_staples": 10, "utilities": 10, "healthcare": 10,
                  "real_estate": 10},
}


def roster(shape: str, seed: int):
    """A universe of N names with the given sector mix.

    Built by RELABELLING a `Universe.random` draw rather than by constructing
    instruments, so every other property -- cap, price, book value, beta,
    volume -- comes from the same generator the certified roster used. That
    isolates sector composition, which is the one thing this file is about.
    """
    u = pt.Universe.random(N, seed=seed)
    counts = SHAPES[shape]
    if not counts:
        return u
    wanted: list[str] = []
    for sector, k in counts.items():
        wanted += [sector] * k
    if len(wanted) != N:
        raise SystemExit(f"{shape}: sectors sum to {len(wanted)}, not {N}")
    out = pt.Universe([])
    for inst, sector in zip(u, wanted):
        out.append(pt.Instrument(
            inst.ticker, sector, initial_price=inst.initial_price,
            shares_outstanding=inst.shares_outstanding, eps=inst.eps,
            book_value_per_share=inst.book_value_per_share,
            revenue_growth=inst.revenue_growth, avg_volume=inst.avg_volume,
            beta=inst.beta, short_interest=inst.short_interest))
    return out


def one(job):
    shape, days, seed, universe_seed, base, ov = job
    m = pt.ModelParams.from_preset(base, **ov) if base else pt.ModelParams.from_preset()
    row = facts.measure(seed=seed, universe=roster(shape, universe_seed),
                        days=days, model=m)
    return shape, days, dict(row)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=30)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--universe-seed", type=int, default=111)
    ap.add_argument(
        "--seed-start", type=int, default=101,
        help="first simulation seed. 101 is the calibration block, which is "
             "what the shipped roster numbers were measured on; a disjoint "
             "block says whether a one-block result was a draw.")
    ap.add_argument("--out", default=None)
    ap.add_argument(
        "--preset", default=None,
        help="preset to measure instead of the shipped one. The gap this "
             "script serves is about the SHIPPED preset, so the default "
             "stays the shipped preset and a candidate has to be asked for.")
    ap.add_argument(
        "--overrides", default="",
        help="comma separated name=value applied to --preset")
    args = ap.parse_args()

    ov = {}
    for item in filter(None, args.overrides.split(",")):
        k, _, v = item.partition("=")
        ov[k.strip()] = float(v)
    m = (pt.ModelParams.from_preset(args.preset, **ov) if args.preset
         else pt.ModelParams.from_preset())
    preset = m.fingerprint
    if ov and preset == pt.ModelParams.from_preset(args.preset).fingerprint:
        raise SystemExit(f"overrides {ov} did not change the fingerprint")
    seeds = list(range(args.seed_start, args.seed_start + args.seeds))
    jobs = [(s, d, seed, args.universe_seed, args.preset, ov)
            for s in SHAPES for d in (252, 504) for seed in seeds]
    print(f"{preset}: {len(SHAPES)} shapes x 2 horizons x {len(seeds)} seeds "
          f"= {len(jobs)} panels, {args.workers} workers", flush=True)
    jobs.sort(key=lambda j: -j[1])

    acc: dict[tuple, list] = {}
    done = 0
    with ProcessPoolExecutor(args.workers) as ex:
        for shape, days, row in ex.map(one, jobs):
            acc.setdefault((shape, days), []).append(row)
            done += 1
            if done % 50 == 0:
                print(f"  ... {done}/{len(jobs)}", flush=True)

    results = {}
    print(f"\n{'shape':16}{'days':>6}{'in band':>10}{'vol %':>9}"
          f"{'xs corr':>9}{'sect xs':>9}   out", flush=True)
    for shape in SHAPES:
        for days in (252, 504):
            rows = acc[(shape, days)]
            # A statistic can be UNDEFINED on a concentrated roster rather
            # than merely out of band: `sector_excess_corr` asks how much a
            # name moves with its own industry beyond the market, and on an
            # all-technology roster the two are the same thing, so `measure`
            # returns None. Scoring it as a miss would report a model defect
            # where there is a measurement that does not exist, and scoring
            # it as a pass would grant a certification nobody measured.
            # Dropped from the count and named in `undefined`.
            med, undefined = {}, []
            for k in facts.REAL_MARKETS:
                vals = [r[k] for r in rows if r[k] is not None]
                if len(vals) < max(1, len(rows) // 2):
                    undefined.append(k)
                else:
                    med[k] = st.median(vals)
            sc = envelope.score(med, horizon_days=days)
            out = [k for k, v in sc["statistics"].items()
                   if not v.get("in_band", True)]
            results[f"{shape}@{days}"] = {
                "in_band": sc["in_band"], "of": sc["of"],
                # The split: a gate reads the shape count; the level and
                # crisis rows are held red and never added to it.
                "shape_in_band": sc.get("shape_in_band"), "shape_of": sc.get("shape_of"),
                "level_in_band": sc.get("level_in_band"), "level_of": sc.get("level_of"),
                "crisis_in_band": sc.get("crisis_in_band"), "crisis_of": sc.get("crisis_of"),
                "out": out, "undefined": undefined, "median": med}
            g = lambda k: f"{med[k]:9.4f}" if k in med else "      n/a"
            print(f"{shape:16}{days:6}{sc['in_band']:>6}/{sc['of']:<3}"
                  f"{med.get('annualised_vol_pct', float('nan')):9.2f}"
                  + g("cross_sectional_corr") + g("sector_excess_corr")
                  + "   " + ", ".join(sorted(out))
                  + (f"   [undefined: {', '.join(undefined)}]"
                     if undefined else ""), flush=True)

    if args.out:
        Path(args.out).write_text(
            json.dumps({"preset": preset, "seeds": seeds, "shapes": SHAPES,
                        "results": results}, indent=2, default=float),
            encoding="utf-8")
        print(f"\nwrote {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
