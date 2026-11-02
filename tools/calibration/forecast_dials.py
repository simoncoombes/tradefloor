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
  meeting ladder is discrete and has no expected-value step to iterate.

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
    rows = []
    for _ in range(sessions):
        engine.run_days(1)
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


def fit_policy(histories, burn=0):
    """The policy projection's four dials, by least squares on a grid, on
    every close from `burn` on."""
    import numpy as np

    targets = (5, 21, 42, 63, 126, 252)
    cadence = sum(round((42 + j) * 252 / 365) for j in range(14)) / 14.0
    r0, shadow, first, realised = [], [], [], {h: [] for h in targets}
    for rows in histories:
        for t in range(burn, len(rows) - max(targets)):
            row = rows[t]
            start = row["day"] + 1
            nm = row["next_meeting"]
            at = math.ceil(nm / 1440) if nm > 0 else start
            r0.append(row["rate"])
            shadow.append(row["path_end"] - row["rate"])
            first.append(max(at, start) - row["day"])
            for h in targets:
                realised[h].append(rows[t + h]["rate"])
    r0, shadow, first = np.array(r0), np.array(shadow), np.array(first)
    realised = {h: np.array(v) for h, v in realised.items()}
    offsets = [round(j * cadence) for j in range(10)]

    def paths(d, phi, kappa, neutral):
        levels, rate, push = [], r0.copy(), (1.0 - d) * shadow
        for _ in offsets:
            rate = rate + push + kappa * (neutral - rate)
            levels.append(rate.copy())
            push = phi * push
        out = {}
        for h in targets:
            f = r0.copy()
            for j, o in enumerate(offsets):
                f = np.where(first + o <= h, levels[j], f)
            out[h] = f
        return out

    def loss(x):
        p = paths(*x)
        return sum(float(((realised[h] - p[h]) ** 2).mean()) for h in targets)

    best = (loss((0.0, 0.0, 0.0, 2.5)), (0.0, 0.0, 0.0, 2.5))
    for d in np.arange(0.0, 0.55, 0.05):
        for phi in np.arange(0.0, 0.95, 0.1):
            for kappa in (0.0, 0.01, 0.02, 0.03, 0.05, 0.08, 0.12):
                for neutral in (1.0, 1.4, 1.8, 2.2):
                    v = loss((d, phi, kappa, neutral))
                    if v < best[0]:
                        best = (v, (d, phi, kappa, neutral))
    d0, p0, k0, n0 = best[1]
    for d in np.arange(max(0.0, d0 - 0.05), d0 + 0.051, 0.01):
        for phi in np.arange(max(0.0, p0 - 0.1), min(0.95, p0 + 0.101), 0.02):
            for kappa in np.arange(max(0.0, k0 - 0.02), k0 + 0.021, 0.0025):
                for neutral in np.arange(max(0.0, n0 - 0.4), n0 + 0.41, 0.02):
                    v = loss((d, phi, kappa, neutral))
                    if v < best[0]:
                        best = (v, (d, phi, kappa, neutral))
    return tuple(round(float(x), 4) for x in best[1])


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
        d, phi, kappa, neutral = fit_policy([by_seed[s] for s in policy_seeds], burn=args.burn)
        print(json.dumps(dict(
            forecast_vix_dispersion=round(sd, 4), forecast_vix_dispersion_half_life=hl,
            forecast_vix_dispersion_skew=round(skew, 2),
            forecast_policy_shadow_discount=d, forecast_policy_persistence=phi,
            forecast_policy_reversion=kappa, forecast_policy_neutral=neutral)))
    else:
        histories = run(seeds, args.sessions, {**law, **json.loads(args.dials)}, args.preset,
                        args.workers)
        print(f"{len(histories)} histories of {args.sessions} sessions, realised less forecast "
              f"from close {args.burn}:")
        print(table(histories, burn=args.burn))
    return 0


if __name__ == "__main__":
    sys.exit(main())
