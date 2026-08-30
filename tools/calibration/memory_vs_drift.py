"""Volatility MEMORY or volatility DRIFT? They look identical at one lag.

§119 measured the panel out to 2520 days and found `abs_return_acf20` rising
monotonically with horizon: 0.0040 at 252 days, 0.0442 at 2520. Two readings
fit that equally well and they call for opposite conclusions.

  * MEMORY. The 252-day reading is noise -- its across-seed spread is 0.0467,
    more than ten times the value -- and a longer sample simply estimates a
    long-memory process better. On this reading the `decay-shape` gap is
    overstated and was built on an estimator that cannot see what it grades.

  * DRIFT. The variance LEVEL creeps upward over a multi-year run. A trending
    level is serially correlated by construction, so |r| autocorrelation
    rises at every lag with no long memory in the process at all. On this
    reading the model has a defect nobody has named, and §119's own rising
    volatility, kurtosis and cross-sectional correlation are the same defect
    seen four ways.

They separate cleanly. Remove a slow LEVEL from |r| and re-measure: genuine
memory survives, a level trend does not.

    a_detrended(t) = |r(t)| - rolling_mean(|r|, W)(t)

with W long enough to leave lag-20 structure intact (W = 252 by default, an
order of magnitude above the lag under test) and centred so it introduces no
phase shift. The drift is also measured DIRECTLY, as annualised volatility
year by year, which needs no estimator argument at all.

    python tools/calibration/memory_vs_drift.py --seeds 20 --workers 80 \
        --out memory-vs-drift.json
"""
from __future__ import annotations

import argparse
import json
import math
import statistics as st
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

sys.path.append(str(Path(__file__).resolve().parent.parent.parent / "python"))

import tradefloor as pt  # noqa: E402

DAYS = 2520
NAMES = 40
UNIVERSE_SEED = 111
LAGS = (1, 5, 20, 60, 120)


def acf(x: np.ndarray, lag: int) -> float:
    d = x - x.mean()
    den = float((d * d).sum())
    return float((d[lag:] * d[:-lag]).sum() / den) if den > 0 else 0.0


def loglog_slope(x: np.ndarray, lo: int = 1, hi: int = 20) -> float:
    pts = [(math.log(k), math.log(a)) for k in range(lo, hi + 1)
           if (a := acf(x, k)) > 0]
    if len(pts) < 4:
        return float("nan")
    mx = sum(p[0] for p in pts) / len(pts)
    my = sum(p[1] for p in pts) / len(pts)
    sxx = sum((p[0] - mx) ** 2 for p in pts)
    return sum((p[0] - mx) * (p[1] - my) for p in pts) / sxx


def detrend(a: np.ndarray, window: int) -> np.ndarray:
    """|r| minus its CENTRED rolling mean.

    Centred, so the filter introduces no phase shift and cannot manufacture
    or destroy autocorrelation by shifting the series against itself. The
    edges are handled by reflecting the series, as `np.pad` in 'reflect'
    mode does: truncating instead would drop the first and last
    half-windows, and those are exactly the years a drift makes most extreme.
    """
    pad = window // 2
    padded = np.pad(a, pad, mode="reflect")
    kernel = np.ones(window) / window
    smooth = np.convolve(padded, kernel, mode="valid")[:len(a)]
    return a - smooth


def one(job):
    preset, seed, window = job
    u = pt.Universe.random(NAMES, seed=UNIVERSE_SEED)
    e = pt.Engine(seed=seed, universe=u, model=pt.ModelParams.from_preset(preset))
    close = np.empty((DAYS, NAMES))
    for d in range(DAYS):
        e.run_days(1)
        close[d] = np.frombuffer(e.column("price"), dtype="<f8")
    ret = close[1:] / close[:-1] - 1.0
    absr = np.abs(ret)

    raw = {lag: st.median([acf(absr[:, j], lag) for j in range(NAMES)])
           for lag in LAGS}
    det = {lag: st.median([acf(detrend(absr[:, j], window), lag)
                           for j in range(NAMES)]) for lag in LAGS}
    slopes = (st.median([loglog_slope(absr[:, j]) for j in range(NAMES)]),
              st.median([loglog_slope(detrend(absr[:, j], window))
                         for j in range(NAMES)]))
    # The drift, measured directly rather than inferred from an estimator.
    years = [float(np.median([ret[y * 252:(y + 1) * 252, j].std()
                              * math.sqrt(252) * 100 for j in range(NAMES)]))
             for y in range(DAYS // 252)]
    return {"raw": raw, "detrended": det, "slope_raw": slopes[0],
            "slope_detrended": slopes[1], "vol_by_year": years}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--preset", default=None)
    ap.add_argument("--seeds", type=int, default=20)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--window", type=int, default=252)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    preset = args.preset or pt.ModelParams.from_preset().fingerprint
    seeds = list(range(101, 101 + args.seeds))
    print(f"{preset}: {len(seeds)} seeds x {DAYS} days x {NAMES} names, "
          f"detrend window {args.window}, {args.workers} workers", flush=True)

    rows = []
    with ProcessPoolExecutor(args.workers) as ex:
        for r in ex.map(one, [(preset, s, args.window) for s in seeds]):
            rows.append(r)
            print(f"  ... {len(rows)}/{len(seeds)}", flush=True)

    print(f"\n{'lag':>6}{'|r| acf raw':>14}{'de-trended':>14}{'kept':>9}",
          flush=True)
    for lag in LAGS:
        raw = st.median(r["raw"][lag] for r in rows)
        det = st.median(r["detrended"][lag] for r in rows)
        kept = "n/a" if raw == 0 else f"{det / raw:.0%}"
        print(f"{lag:6}{raw:14.4f}{det:14.4f}{kept:>9}", flush=True)

    sr = st.median(r["slope_raw"] for r in rows)
    sd = st.median(r["slope_detrended"] for r in rows)
    print(f"\nlog-log slope, lags 1-20:  raw {sr:+.3f}   de-trended {sd:+.3f}"
          f"   (real markets -0.436)", flush=True)

    print("\nannualised volatility by year -- the drift, measured directly",
          flush=True)
    n_years = len(rows[0]["vol_by_year"])
    by_year = [st.median(r["vol_by_year"][y] for r in rows)
               for y in range(n_years)]
    print("  " + "  ".join(f"y{y + 1} {v:.1f}%" for y, v in enumerate(by_year)),
          flush=True)
    first, last = by_year[0], by_year[-1]
    print(f"  year 1 {first:.2f}%  ->  year {n_years} {last:.2f}%   "
          f"{(last - first) / first:+.1%}", flush=True)

    print("\nVERDICT", flush=True)
    raw20 = st.median(r["raw"][20] for r in rows)
    det20 = st.median(r["detrended"][20] for r in rows)
    # A RATIO to a value sitting on zero says nothing, and says it loudly: a
    # 756-day smoke run read raw -0.0023 against de-trended -0.0114 and the
    # ratio printed "506% kept", which the arms below would have read as
    # overwhelming evidence of memory. The floor is a tenth of the across-seed
    # spread of this statistic at 252 days (0.0467), which is the scale at
    # which it is known to be noise.
    if abs(raw20) < 0.005:
        print(f"  UNDECIDABLE at this horizon: raw lag-20 autocorrelation is "
              f"{raw20:+.4f}, indistinguishable from zero, so the fraction "
              f"surviving de-trending is a ratio to noise. Run it longer.",
              flush=True)
        kept20 = float("nan")
    elif (kept20 := det20 / raw20) > 0.6:
        print("  MEMORY: lag-20 structure survives removing the level, so the "
              "rise with horizon is real memory an estimator could not see at "
              "252 days.", flush=True)
    elif kept20 < 0.25:
        print("  DRIFT: lag-20 structure is mostly a level trend. The model "
              "has a volatility drift, which is a NEW defect rather than an "
              "excuse for the decay-shape gap.", flush=True)
    else:
        print(f"  BOTH, and neither dominates: {kept20:.0%} of the lag-20 "
              "autocorrelation survives de-trending. Report as such.",
              flush=True)

    if args.out:
        Path(args.out).write_text(json.dumps(
            {"preset": preset, "seeds": seeds, "days": DAYS,
             "window": args.window, "rows": rows}, indent=2, default=float),
            encoding="utf-8")
        print(f"\nwrote {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
