"""The model's volatility-versus-VIX curve, against the real one, bucket by bucket.

The crisis lever is a RATIO OF TWO POINTS: realised volatility at a held VIX
65 over a held VIX 5. `pt-v12` reads 6.04 there and `d3-f10` reads 6.16
against a real 6.16, which looks like an exact match and says nothing at all
about the five buckets in between. Two points fit any monotone curve.

That mattered from round 15. Funded regime-aware jumps closed 83% of the
calm-market floor -- 22.72% annualised at a held VIX 5 down to 18.15%
against a real 17.2% -- and made the driven window WORSE, 1.610 to 1.630.
The excess in a driven 2020-21 path is therefore not in the calm days, and
at a held VIX 45 the model reads 96.0 against a real 106.1, so it is not a
simple deficit at the crisis end either. Both ends are close and the middle
has never been looked at.

`real_vix_lever.py` already measured the real side in seven buckets, from
the same forty-name roster, and `real-vix-lever.json` holds it. This
measures the model at those buckets' midpoints on the same estimator, so
the two curves can be laid side by side.

The construction difference is stated rather than hidden, and stays the same
one `real_vix_lever.py` records: a real bucket mixes days whose VIX arrived
from different directions, while a held run is a steady state real markets
never occupy. A gap that appears only in the middle of the curve is
therefore evidence about the model's steady-state transfer function, and a
gap that appears only in a DRIVEN window is evidence about its dynamics.
Separating those two is what running this is for.

    python tools/calibration/vix_transfer.py --candidates c.json \\
        --seeds 30 --workers 190 --out transfer.json
"""

from __future__ import annotations

import argparse
import json
import pathlib
import statistics
import sys
from concurrent.futures import ProcessPoolExecutor

import tradefloor as pt
from tradefloor import facts
from tradefloor import Scenario

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import gate_pick  # noqa: E402

#: The real curve, from `real_vix_lever.py` via `real-vix-lever.json`, keyed
#: by the bucket's midpoint. Quoted rather than re-fetched: that script is
#: the measurement of record and a second copy would be a second thing to
#: drift. The last bucket is open-ended at 45+ and its VIX mean over the
#: sample is nearer 55 than its nominal midpoint, so it reads 55.
REAL = {
    9.0: 17.21,
    14.0: 20.57,
    18.0: 24.59,
    22.5: 29.76,
    27.5: 36.09,
    37.5: 46.23,
    55.0: 106.07,
}

UNIVERSE_N, UNIVERSE_SEED, DAYS = 40, 111, 252


def _job(spec):
    label, base, ov, vix, seed = spec
    m = gate_pick.model(base, ov)
    f = facts.measure(seed=seed, universe=pt.Universe.random(UNIVERSE_N, seed=UNIVERSE_SEED),
                      days=DAYS, model=m, scenario=Scenario().hold(vix=vix))
    return label, vix, f.get("annualised_vol_pct")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", required=True)
    ap.add_argument("--seeds", type=int, default=30)
    ap.add_argument("--seed-start", type=int, default=None,
                    help="first seed of a disjoint block")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    cands = json.loads(pathlib.Path(args.candidates).read_text(encoding="utf-8"))
    if args.seed_start is None:
        seeds = list(gate_pick.TRAIN[: args.seeds])
    else:
        seeds = list(range(args.seed_start, args.seed_start + args.seeds))
        if set(seeds) & set(gate_pick.TRAIN):
            sys.exit("the confirmation block overlaps the calibration seeds")

    vixes = sorted(REAL)
    jobs = [(c["label"], c["base"], c["overrides"], v, s)
            for c in cands for v in vixes for s in seeds]
    print(f"{len(cands)} candidates x {len(vixes)} VIX levels x {len(seeds)} seeds "
          f"= {len(jobs)} panels on {args.workers} workers", flush=True)

    acc: dict[tuple, list] = {}
    with ProcessPoolExecutor(args.workers) as ex:
        for n, (label, vix, vol) in enumerate(ex.map(_job, jobs), 1):
            if vol is not None:
                acc.setdefault((label, vix), []).append(vol)
            if n % 200 == 0:
                print(f"  ... {n}/{len(jobs)}", flush=True)

    out = {"real": REAL, "seeds": len(seeds), "days": DAYS, "candidates": {}}
    head = "".join(f"{v:>9.1f}" for v in vixes)
    print(f"\n{'candidate':<14}{head}     lever")
    print(f"{'REAL':<14}" + "".join(f"{REAL[v]:>9.2f}" for v in vixes)
          + f"{REAL[55.0]/REAL[9.0]:>10.2f}")
    for c in cands:
        row = {str(v): statistics.median(acc[(c["label"], v)]) for v in vixes}
        out["candidates"][c["label"]] = {
            "overrides": c["overrides"],
            "vol_by_vix": row,
            # The ratio the model is graded on, and the ratio of the ratios
            # per bucket, which is where a shape error shows up and a lever
            # cannot.
            "ratio_to_real": {str(v): row[str(v)] / REAL[v] for v in vixes},
            "lever": row[str(55.0)] / row[str(9.0)],
        }
        print(f"{c['label']:<14}" + "".join(f"{row[str(v)]:>9.2f}" for v in vixes)
              + f"{out['candidates'][c['label']]['lever']:>10.2f}")
    print(f"\n{'ratio to real':<14}")
    for c in cands:
        r = out["candidates"][c["label"]]["ratio_to_real"]
        print(f"{c['label']:<14}" + "".join(f"{r[str(v)]:>9.2f}" for v in vixes))

    pathlib.Path(args.out).write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"\nwrote {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
