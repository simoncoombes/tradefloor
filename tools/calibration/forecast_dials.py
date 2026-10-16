"""Derive the forecast's seven derived dials, and check the forecast is unbiased.

    python tools/calibration/forecast_dials.py derive
    python tools/calibration/forecast_dials.py derive --law '{"vix_fear_uptake": 0.2, "vix_fear_half_life": 8.0}'
    python tools/calibration/forecast_dials.py check --dials '{"forecast_vix_dispersion": 0.315}'

The forecast (`forecast_horizon_sessions`) iterates the expected value of the
model's laws. Two parts of it are fitted on the model's own held-out
histories and frozen as dials, and this tool is how they were fitted:

* the log VIX's spread about the forecast (`forecast_vix_dispersion`, its
  long-horizon sd, and `forecast_vix_dispersion_half_life`), measured from
  the forecast's own errors in log VIX at 1 to 252 sessions and fitted to
  `sd^2 * (1 - 0.5^(2h / half-life))` by least squares on a grid, and its
  skewness at 126 to 252 sessions (`forecast_vix_dispersion_skew`). The
  forecast takes its expectations over the VIX across this spread, the
  published VIX's stress premium among them;
* the policy path's projection beyond the shadow of the next meeting
  (`forecast_policy_shadow_discount`, `forecast_policy_persistence`,
  `forecast_policy_reversion` and `forecast_policy_neutral`), fitted by least
  squares on the realised policy rate at 5 to 252 sessions, since the
  meeting ladder is discrete and has no expected-value step to iterate;
  with `--neutral-core` the neutral's move with the inflation the meeting
  reads above target (`forecast_policy_neutral_core`) is fitted with them.
  The fit's objective is least squares subject to an unbiased forecast:
  the mean squared error at each target horizon plus `BIAS_WEIGHT` (100)
  times that horizon's squared mean error, since an unbiased forecast is the
  forecast's job and row RF5 reads the mean error (changed 2026-10-16 from
  plain least squares, which on the pt-v22 candidate's law left +0.38 bp at
  63 sessions, t 1.8 on 270 histories). The policy dials want histories as
  long as RF5's, 21 years (`--sessions 5292`).

A history's policy rate level lasts its whole run, so the policy fit wants
many histories: 40 put the neutral rate at 1.4 per cent, and the forecast
then read 2.6 standard errors low on 96 others; 240 put it at 1.48, and the
same 96 read at most 1.7. The frozen values are the 240's.

`derive` runs held-out seeds with every derived dial at zero, fits the VIX's
spread on every close of 3001-3040 (`--seeds`) and the policy path on every
close after the first year of 3001-3240 (`--policy-seeds`, `--burn`), as RF5
reads it, and prints the seven values. `check` runs fresh seeds with the dials
given and prints, per series and horizon, the mean of realised less forecast
across histories, its standard error across histories and the ratio of the
two, over every close after the first year (`--burn`, as VF8, RF5 and OF4
read them). The series are the published VIX (VF8), oil (OF4), the policy
rate (RF5) and the index's one-session variance, all read at each close.

The model is the preset (pt-v21) with the three switches on, on
`Universe.random(40, seed=111)`, and `--law`'s dials on it in both modes: a
candidate law that changes the VIX's (the fear memory) changes the spread
the dials measure, so its dials are derived on it. A history is one seed run
from its opening; each history's mean error is one observation, so the
standard error is the spread of those means over the square root of their
number. Those means move with each history's own slow VIX level, which the
forecast cannot see, so on a set of calm histories the VIX reads high and on
a set of stressed ones it reads low.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from concurrent.futures import ProcessPoolExecutor

HORIZONS = (1, 2, 5, 10, 21, 42, 63, 126, 189, 252)
SWITCHES = dict(index_level_listed=1.0, vix_intraday_live=0.0, forecast_horizon_sessions=252.0)


def history(args):
    """One seed's per-close records: what the forecast said and what came."""
    import tradefloor as tf

    seed, sessions, dials, preset = args
    universe = tf.Universe.random(40, seed=111)
    engine = tf.Engine(seed=seed, universe=universe,
                       model=tf.ModelParams.from_preset(preset, **SWITCHES, **dials))
    core = engine.model.fed_core_inflation != 0.0
    rows = []
    for _ in range(sessions):
        # the session by hand: the same path as `run_days(1)` (checked to the
        # bit) without its record of every day, which a 21-year history
        # cannot hold in memory on many processes
        engine.open_market()
        engine.run_session(9, 30, 3, 390)
        engine.close_market()
        f = engine.forecast()
        econ = engine.economy()
        bank = engine.state_snapshot()["central_bank"]
        rows.append(dict(
            vix=engine.macro_fields["vix"], oil=econ["oil_price"],
            rate=econ["federal_funds_rate"], var=f["index_variance"][0],
            day=f["day"], next_meeting=bank["next_meeting_date"],
            f={h: (f["vix"][h - 1], f["oil"][h - 1], 100.0 * f["policy_rate"][h - 1],
                   f["index_variance"][h - 1]) for h in HORIZONS},
            # The policy path's last rate: the shadow, with every derived
            # dial at zero, which is all the fit reads of it.
            path_end=100.0 * f["policy_rate"][-1],
            # The inflation the meeting reads above the bank's target
            # (`forecast_policy_neutral_core`): core under
            # `fed_core_inflation`, headline without it.
            gap=(econ["core_inflation"] if core else econ["inflation_rate"]) - bank["target_inflation"],
        ))
    return rows


def run(seeds, sessions, dials, preset, workers):
    with ProcessPoolExecutor(workers) as pool:
        return list(pool.map(history, [(s, sessions, dials, preset) for s in seeds]))


SERIES = {"vix": 0, "oil": 1, "rate": 2, "var": 3}


def errors(rows, h, series, log=False, burn=0):
    """Realised less forecast at `h` sessions, from every close from `burn`
    on (VF8, RF5 and OF4 read every close after the first year, 252)."""
    q = SERIES[series]
    out = []
    for t in range(burn, len(rows) - h):
        forecast = rows[t]["f"][h][q]
        realised = rows[t + h - 1]["var"] if series == "var" else rows[t + h][series]
        out.append(math.log(realised) - math.log(forecast) if log else realised - forecast)
    return out


def table(histories, horizons=(5, 21, 63, 126, 252), burn=0):
    lines = []
    for series in SERIES:
        cells = []
        for h in horizons:
            means = [sum(e) / len(e) for e in (errors(rows, h, series, burn=burn) for rows in histories)]
            m = sum(means) / len(means)
            sd = math.sqrt(sum((x - m) ** 2 for x in means) / (len(means) - 1))
            se = sd / math.sqrt(len(means))
            cells.append(f"h{h} {m:+.4g} se {se:.2g} t {m / se if se else 0.0:+.1f}")
        lines.append(f"{series:5s}" + "  ".join(cells))
    return "\n".join(lines)


#: The horizons the spread's skewness is read at, where the spread has
#: reached its long-horizon level.
SKEW_HORIZONS = (126, 189, 252)


def fit_dispersion(histories):
    """The log VIX's spread about the forecast: `sd`, `half-life` and the
    skewness at SKEW_HORIZONS, pooled."""
    spread, tail = {}, []
    for h in HORIZONS:
        e = [x for rows in histories for x in errors(rows, h, "vix", log=True)]
        m = sum(e) / len(e)
        spread[h] = sum((x - m) ** 2 for x in e) / len(e)
        if h in SKEW_HORIZONS:
            tail += [x - m for x in e]
    best = None
    for i in range(0, 400):
        sd = 0.05 + 0.0025 * i
        for j in range(0, 400):
            hl = 1.0 + 0.5 * j
            sse = sum((sd * sd * (1.0 - 0.5 ** (2.0 * h / hl)) - v) ** 2 for h, v in spread.items())
            if best is None or sse < best[0]:
                best = (sse, sd, hl)
    var = sum(x * x for x in tail) / len(tail)
    skew = sum(x ** 3 for x in tail) / len(tail) / var ** 1.5
    return best[1], best[2], skew, spread


#: The policy fit's arrays, handed to the grid's processes (see fit_policy).
_FIT = {}


def _paths(d, phi, kappa, neutral, k):
    import numpy as np

    F = _FIT
    target = neutral + k * F["gap"]
    levels, rate, push = [], F["r0"].copy(), (1.0 - d) * F["shadow"]
    for _ in F["offsets"]:
        rate = rate + push + kappa * (target - rate)
        levels.append(rate.copy())
        push = phi * push
    out = {}
    for h in F["targets"]:
        f = F["r0"].copy()
        for j, o in enumerate(F["offsets"]):
            f = np.where(F["first"] + o <= h, levels[j], f)
        out[h] = f
    return out


#: The weight on each horizon's squared mean error in the policy fit's loss,
#: against 1 on its mean squared error: the fit is least squares subject to
#: an unbiased forecast at every target horizon (RF5 reads the mean error),
#: the constraint held by a penalty, since five dials cannot zero six means
#: exactly.
BIAS_WEIGHT = 100.0


def _loss(x):
    p = _paths(*x)
    out = 0.0
    for h in _FIT["targets"]:
        e = _FIT["realised"][h] - p[h]
        out += float((e * e).mean()) + _FIT.get("bias_weight", 0.0) * float(e.mean()) ** 2
    return out


def _block(points):
    best = None
    for i, x in points:
        v = _loss(x)
        if best is None or v < best[0]:
            best = (v, i, x)
    return best


def _init(fit):
    _FIT.update(fit)


def _search(points, workers):
    """The first point, in the grid's order, with the strictly smallest loss:
    what a serial loop over the grid returns, searched in blocks on
    `workers` processes."""
    points = list(enumerate(points))
    n = max(1, min(workers, len(points)))
    if n == 1:
        return _block(points)
    with ProcessPoolExecutor(n, initializer=_init, initargs=(dict(_FIT),)) as pool:
        found = [b for b in pool.map(_block, [points[i::n] for i in range(n)]) if b is not None]
    return min(found, key=lambda b: (b[0], b[1]))


def fit_policy(histories, burn=0, workers=1, core=False, bias_weight=BIAS_WEIGHT):
    """The policy projection's dials, by least squares on a grid, on every
    close from `burn` on: the shadow's discount, its persistence, the
    reversion and the neutral rate, and with `core` the neutral's move with
    the meeting's inflation gap (`forecast_policy_neutral_core`), the fifth.
    The grid is searched on `workers` processes; the answer is the serial
    loop's."""
    import numpy as np

    targets = (5, 21, 42, 63, 126, 252)
    cadence = sum(round((42 + j) * 252 / 365) for j in range(14)) / 14.0
    r0, shadow, first, gap, realised = [], [], [], [], {h: [] for h in targets}
    for rows in histories:
        for t in range(burn, len(rows) - max(targets)):
            row = rows[t]
            start = row["day"] + 1
            nm = row["next_meeting"]
            at = math.ceil(nm / 1440) if nm > 0 else start
            r0.append(row["rate"])
            shadow.append(row["path_end"] - row["rate"])
            first.append(max(at, start) - row["day"])
            gap.append(row.get("gap", 0.0))
            for h in targets:
                realised[h].append(rows[t + h]["rate"])
    _FIT.update(r0=np.array(r0), shadow=np.array(shadow), first=np.array(first), gap=np.array(gap),
                realised={h: np.array(v) for h, v in realised.items()}, targets=targets,
                offsets=[round(j * cadence) for j in range(10)], bias_weight=bias_weight)
    ks = (0.0, 0.25, 0.5, 0.75, 1.0, 1.25) if core else (0.0,)
    # The neutral's grid reaches 4.2 per cent: a candidate law can hold its
    # rate above the 2.2 the grid once stopped at.
    coarse = [(0.0, 0.0, 0.0, 2.5, 0.0)] + [
        (d, phi, kappa, neutral, k)
        for d in np.arange(0.0, 0.55, 0.05)
        for phi in np.arange(0.0, 0.95, 0.1)
        for kappa in (0.0, 0.01, 0.02, 0.03, 0.05, 0.08, 0.12)
        for neutral in (1.0, 1.4, 1.8, 2.2, 2.6, 3.0, 3.4, 3.8, 4.2)
        for k in ks]
    _, _, best = _search(coarse, workers)
    d0, p0, k0, n0, c0 = best
    fine_k = np.arange(max(0.0, c0 - 0.15), c0 + 0.151, 0.05) if core else (0.0,)
    fine = [best] + [
        (d, phi, kappa, neutral, k)
        for d in np.arange(max(0.0, d0 - 0.05), d0 + 0.051, 0.01)
        for phi in np.arange(max(0.0, p0 - 0.1), min(0.95, p0 + 0.101), 0.02)
        for kappa in np.arange(max(0.0, k0 - 0.02), k0 + 0.021, 0.0025)
        for neutral in np.arange(max(0.0, n0 - 0.4), n0 + 0.41, 0.02 if not core else 0.04)
        for k in fine_k]
    _, _, best = _search(fine, workers)
    return tuple(round(float(x), 4) for x in best)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Derive the forecast's derived dials on held-out histories, "
                    "or check the forecast's bias on fresh ones.")
    parser.add_argument("mode", choices=("derive", "check"))
    parser.add_argument("--seeds", default=None,
                        help="first,last seed (inclusive); derive defaults to 3001,3040 "
                             "for the VIX's spread and check to 7001,7016")
    parser.add_argument("--policy-seeds", default="3001,3240",
                        help="first,last seed (inclusive) derive fits the policy dials on "
                             "(default 3001,3240): a history's rate level lasts its whole "
                             "run, so 40 histories pin the neutral rate to about 0.1")
    parser.add_argument("--sessions", type=int, default=2000,
                        help="sessions per history (default 2000)")
    parser.add_argument("--dials", default="{}",
                        help="a JSON object of forecast dials for check")
    parser.add_argument("--preset", default="pt-v21")
    parser.add_argument("--law", default="{}",
                        help="a JSON object of model dials set on the preset in both modes, "
                             "for a law no preset ships (a candidate arm); the forecast's "
                             "own dials go in --dials")
    parser.add_argument("--burn", type=int, default=252,
                        help="closes left out at the start of each history for check "
                             "(default 252: VF8, RF5 and OF4 read every close after the "
                             "first year); derive fits the VIX's spread on every close "
                             "and the policy dials from the same close RF5 reads")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--fit-workers", type=int, default=None,
                        help="processes for the policy fit's grid (default --workers); the "
                             "histories' processes hold a run each, the grid's only arrays")
    parser.add_argument("--bias-weight", type=float, default=BIAS_WEIGHT,
                        help="the policy fit's weight on each horizon's squared mean error "
                             f"(default {BIAS_WEIGHT:g}; 0 is plain least squares)")
    parser.add_argument("--fit-both", action="store_true",
                        help="fit the policy dials with and without forecast_policy_neutral_core "
                             "on the same histories, one JSON line each")
    parser.add_argument("--neutral-core", action="store_true",
                        help="also fit forecast_policy_neutral_core, the neutral's move with the "
                             "inflation the meeting reads above target")
    args = parser.parse_args(argv)
    default = "3001,3040" if args.mode == "derive" else "7001,7016"
    lo, hi = (int(x) for x in (args.seeds or default).split(","))
    seeds = range(lo, hi + 1)
    law = json.loads(args.law)
    if args.mode == "derive":
        plo, phi_ = (int(x) for x in args.policy_seeds.split(","))
        policy_seeds = range(plo, phi_ + 1)
        every = sorted(set(seeds) | set(policy_seeds))
        by_seed = dict(zip(every, run(every, args.sessions, law, args.preset, args.workers)))
        histories = [by_seed[s] for s in seeds]
        sd, hl, skew, spread = fit_dispersion(histories)
        print("log VIX spread about the forecast:",
              ", ".join(f"h{h} {v:.4f}" for h, v in spread.items()))
        for with_core in ((False, True) if args.fit_both else (args.neutral_core,)):
            d, phi, kappa, neutral, core = fit_policy(
                [by_seed[s] for s in policy_seeds], burn=args.burn,
                workers=args.fit_workers or args.workers, core=with_core, bias_weight=args.bias_weight)
            out = dict(
                forecast_vix_dispersion=round(sd, 4), forecast_vix_dispersion_half_life=hl,
                forecast_vix_dispersion_skew=round(skew, 2),
                forecast_policy_shadow_discount=d, forecast_policy_persistence=phi,
                forecast_policy_reversion=kappa, forecast_policy_neutral=neutral)
            if with_core:
                out["forecast_policy_neutral_core"] = core
            print(json.dumps(out), flush=True)
    else:
        histories = run(seeds, args.sessions, {**law, **json.loads(args.dials)}, args.preset,
                        args.workers)
        print(f"{len(histories)} histories of {args.sessions} sessions, realised less forecast "
              f"from close {args.burn}:")
        print(table(histories, burn=args.burn))
    return 0


if __name__ == "__main__":
    sys.exit(main())
