"""c4 -- criterion C4, "no price-only edge", measured for one or more arms.

    ENGINE/.venv/bin/python c4.py --arm NAME[@BASE]:dial=v,... [--arm ...]
        [--part a|b|both] [--seeds 8] [--workers 8] --out C4.json

Two parts, both hard lines in CRITERIA.md's C family. Each catches one half
of what let a spec mean reversion rule beat buy-and-hold on 20 of 20 markets
of the published suite (programme/meanrev-edge-ptv19-2026-09-24.md).

C4a, the tape. On the certified roster, `Universe.random(40, seed=111)`,
252 sessions untraded, seeds from 101: the price at the end of each of the
six 65-tick steps of a session, the decision grid `tf.evaluate` samples at
its defaults. Per name, the lag-1 autocorrelation of those 65-minute log
returns (the series runs across sessions, so the first return of a day
carries the night, as a step-cadence agent's does), and the Roll (1984)
spread they imply, 2 sqrt(-cov(r_t, r_t-1)), zero where the covariance is
positive. The quoted spread is the book's (ask - bid) / mid at each step
boundary, a name's median over the run. Per seed: the median over names of
the autocorrelation, and the median Roll spread over the median quoted
spread. Passes when the seed-mean autocorrelation is at or above -0.05, OR
the seed-mean Roll-to-quoted ratio is at or below 2.

C4b, the user's view. The simple price-only rules, run through `tf.evaluate`
on the published suite's 20 markets (tf-suite-2026.1: 8 plain and each of
the 6 packaged scenarios on 2, sim seeds 92001-92020 on
`Universe.random(20, seed=93001-93020)`, 60 days, 6 steps of 65 ticks, $1M,
leverage 2): mean reversion and momentum at a one-step lookback, a one-day
lookback at step cadence, and a five-day lookback at daily cadence. Each
spec against buy-and-hold in the same market: the median excess return over
the 20 markets, in points, and the markets it beats. A spec passes at a
median of at most +5 points AND at most 14 of 20 beaten; C4b passes when
every spec does. The oracle runs beside them and is reported, not graded.

The report is JSON: {"kind": "longrun.c4", "arms": {NAME: {"c4a": ...,
"c4b": ...}}}. `criteria.py --c4 FILE` reads it.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics as st
import sys
from concurrent.futures import ProcessPoolExecutor

C4A_ACF_FLOOR = -0.05
C4A_ROLL_RATIO_CEILING = 2.0
C4B_MEDIAN_CEILING = 5.0
C4B_WINS_CEILING = 14

#: tf-suite-2026.1, as tradefloor-serve's suites/definitions.py publishes it.
SUITE_CELLS = tuple(
    (92000 + i, 93000 + i, sc) for i, sc in enumerate(
        [None] * 8 + ["geopolitical_conflict"] * 2 + ["liquidity_crisis"] * 2
        + ["oil_price_spike"] * 2 + ["policy_regime_shift"] * 2
        + ["rate_shock"] * 2 + ["recession"] * 2, start=1))
SUITE = dict(days=60, steps_per_day=6, ticks_per_step=65, cash=1_000_000.0,
             max_leverage=2.0)


def specs():
    import tradefloor as tf
    daily = lambda kind: tf.StrategySpec(  # noqa: E731
        signal={"kind": kind, "lookback_days": 5.0},
        execution={"cadence": "daily"})
    return {
        "mean_reversion_1step": tf.StrategySpec.mean_reversion(lookback_days=1 / 6),
        "mean_reversion_1day": tf.StrategySpec.mean_reversion(lookback_days=1.0),
        "mean_reversion_5day_daily": daily("mean_reversion"),
        "momentum_1step": tf.StrategySpec.momentum(lookback_days=1 / 6),
        "momentum_1day": tf.StrategySpec.momentum(lookback_days=1.0),
        "momentum_5day_daily": daily("momentum"),
    }


def parse_arm(text: str, default_base: str = "pt-v19"):
    """`NAME[@BASE]:dial=v,...` -> (name, base, dials), as edge.py reads it."""
    head, _, rest = text.partition(":")
    name, _, base = head.strip().partition("@")
    dials = {}
    for part in filter(None, rest.split(",")):
        k, _, v = part.partition("=")
        dials[k.strip()] = float(v)
    return name.strip(), (base.strip() or default_base), dials


def _model(base, dials):
    import tradefloor as tf
    if not dials:
        return base
    try:
        return tf.ModelParams.from_preset(base, **dials)
    except Exception:  # an arm off the preset's identities; measured as given
        return tf.ModelParams.from_preset_unchecked(base, **dials)


# ------------------------------------------------------------------ C4a

def _acf1(x):
    m = sum(x) / len(x)
    d = [v - m for v in x]
    var = sum(v * v for v in d)
    cov = sum(d[i] * d[i + 1] for i in range(len(d) - 1))
    return cov / var if var > 0 else float("nan"), cov / len(d)


def c4a_seed(args):
    base, dials, seed, days = args
    import struct
    import tradefloor as tf
    universe = tf.Universe.random(40, seed=111)
    e = tf.Engine(seed=seed, universe=universe, model=_model(base, dials))
    n = len(universe)
    grid, quoted = [], [[] for _ in range(n)]
    for _ in range(days):
        e.open_market()
        for k in range(6):
            for i, t in enumerate(e.tickers):
                b = e.book(t)
                if b.best_ask and b.best_bid and b.best_ask > b.best_bid:
                    mid = (b.best_ask + b.best_bid) / 2
                    quoted[i].append((b.best_ask - b.best_bid) / mid)
            at = 30 + 65 * k
            e.run_session(9 + at // 60, at % 60, 3, 65)
            grid.append(struct.unpack("<%dd" % n, e.prices()))
        e.close_market()
    acfs, rolls, quotes = [], [], []
    for i in range(n):
        r = [math.log(grid[s + 1][i] / grid[s][i]) for s in range(len(grid) - 1)]
        a, cov = _acf1(r)
        acfs.append(a)
        rolls.append(2 * math.sqrt(-cov) if cov < 0 else 0.0)
        quotes.append(st.median(quoted[i]) if quoted[i] else float("nan"))
    med_roll, med_quote = st.median(rolls), st.median(quotes)
    return {"seed": seed, "acf1_median": st.median(acfs),
            "roll_bps_median": med_roll * 1e4, "quoted_bps_median": med_quote * 1e4,
            "roll_to_quoted": med_roll / med_quote,
            "names_below_floor": sum(a < C4A_ACF_FLOOR for a in acfs)}


def c4a(base, dials, seeds, first_seed, days, workers):
    jobs = [(base, dials, first_seed + k, days) for k in range(seeds)]
    with ProcessPoolExecutor(workers) as pool:
        rows = list(pool.map(c4a_seed, jobs))
    acf = st.fmean(r["acf1_median"] for r in rows)
    ratio = st.fmean(r["roll_to_quoted"] for r in rows)
    se = (st.stdev(r["acf1_median"] for r in rows) / math.sqrt(len(rows))
          if len(rows) > 1 else float("nan"))
    return {"roster": "Universe.random(40, seed=111)", "days": days,
            "seeds": seeds, "first_seed": first_seed,
            "acf1": acf, "acf1_se": se, "roll_to_quoted": ratio,
            "roll_bps": st.fmean(r["roll_bps_median"] for r in rows),
            "quoted_bps": st.fmean(r["quoted_bps_median"] for r in rows),
            "floor": C4A_ACF_FLOOR, "ratio_ceiling": C4A_ROLL_RATIO_CEILING,
            "pass": acf >= C4A_ACF_FLOOR or ratio <= C4A_ROLL_RATIO_CEILING,
            "per_seed": rows}


# ------------------------------------------------------------------ C4b

def c4b_cell(args):
    base, dials, seed, useed, scenario = args
    import tradefloor as tf
    agents = dict(specs())
    agents["buy_and_hold"] = tf.StrategySpec.hold()
    agents["oracle"] = tf.StrategySpec.oracle()
    sc = tf.Scenario.load(scenario) if scenario else None
    cards = tf.evaluate(agents, seed=seed, universe=tf.Universe.random(20, seed=useed),
                        scenario=sc, model=_model(base, dials), **SUITE)
    return {"seed": seed, "universe_seed": useed, "scenario": scenario,
            "returns": {k: v.return_pct for k, v in cards.items()},
            "turnover": {k: v.turnover for k, v in cards.items()}}


def c4b(base, dials, workers):
    jobs = [(base, dials, s, u, sc) for s, u, sc in SUITE_CELLS]
    with ProcessPoolExecutor(workers) as pool:
        cells = list(pool.map(c4b_cell, jobs))
    out = {}
    for name in list(specs()) + ["oracle"]:
        ex = [c["returns"][name] - c["returns"]["buy_and_hold"] for c in cells]
        beats_oracle = sum(c["returns"][name] > c["returns"]["oracle"] for c in cells)
        med, wins = st.median(ex), sum(x > 0 for x in ex)
        out[name] = {"median_excess": med, "wins": wins, "markets": len(ex),
                     "median_return": st.median(c["returns"][name] for c in cells),
                     "beats_oracle": beats_oracle,
                     "median_turnover": st.median(c["turnover"][name] for c in cells) / SUITE["cash"],
                     "graded": name != "oracle",
                     "pass": med <= C4B_MEDIAN_CEILING and wins <= C4B_WINS_CEILING}
    graded = {k: v for k, v in out.items() if v["graded"]}
    worst = max(graded, key=lambda k: (graded[k]["median_excess"] / C4B_MEDIAN_CEILING,
                                       graded[k]["wins"] / C4B_WINS_CEILING))
    return {"suite": "tf-suite-2026.1", "median_ceiling": C4B_MEDIAN_CEILING,
            "wins_ceiling": C4B_WINS_CEILING, "specs": out,
            "failing": [k for k, v in graded.items() if not v["pass"]],
            "worst": worst, "pass": all(v["pass"] for v in graded.values()),
            "buy_and_hold_median_return": st.median(c["returns"]["buy_and_hold"] for c in cells),
            "cells": cells}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--arm", action="append", required=True)
    ap.add_argument("--base", default="pt-v19")
    ap.add_argument("--part", choices=("a", "b", "both"), default="both")
    ap.add_argument("--seeds", type=int, default=8)
    ap.add_argument("--first-seed", type=int, default=101)
    ap.add_argument("--days", type=int, default=252)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--engine-commit", default=None,
                    help="the engine commit the installed build was made from, recorded "
                         "beside the version (a development build reports the last "
                         "release's version)")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    import tradefloor as tf
    report = {"kind": "longrun.c4", "tradefloor_version": tf.version(),
              "engine_commit": a.engine_commit, "arms": {}}
    for text in a.arm:
        name, base, dials = parse_arm(text, a.base)
        arm = {"base": base, "dials": dials,
               "fingerprint": (tf.ModelParams.from_preset(base, **dials).fingerprint
                               if dials else base)}
        if a.part in ("a", "both"):
            arm["c4a"] = c4a(base, dials, a.seeds, a.first_seed, a.days, a.workers)
            x = arm["c4a"]
            print(f"{name} C4a: acf1 {x['acf1']:+.3f} (se {x['acf1_se']:.3f}), Roll "
                  f"{x['roll_bps']:.1f} bp over quoted {x['quoted_bps']:.1f} bp = "
                  f"{x['roll_to_quoted']:.2f}x -> {'pass' if x['pass'] else 'FAIL'}",
                  file=sys.stderr)
        if a.part in ("b", "both"):
            arm["c4b"] = c4b(base, dials, a.workers)
            for k, v in arm["c4b"]["specs"].items():
                print(f"{name} C4b {k:26s} median {v['median_excess']:+7.2f} pts, beats "
                      f"{v['wins']:2d}/20, beats oracle {v['beats_oracle']:2d}/20"
                      + ("" if not v["graded"] else f"  {'pass' if v['pass'] else 'FAIL'}"),
                      file=sys.stderr)
        report["arms"][name] = arm
    json.dump(report, open(a.out, "w"), indent=1)


if __name__ == "__main__":
    main()
