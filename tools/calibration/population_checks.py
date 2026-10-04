"""Measure what a population does to agents: crowding, front-running, cost.

    python tools/calibration/population_checks.py ac4 --seeds 201-208 [--model M]
    python tools/calibration/population_checks.py ac3 --seeds 1-8 [--model M]
    python tools/calibration/population_checks.py runtime [--model M]

`--model` is a preset name or a file holding `NAME@BASE:dial=value,...` (an
arm). `--workers` runs seeds in parallel (default 4). Every comparison is
paired on the seed, and its one-sided p-value is a sign-flip permutation test
of the mean (20,000 flips from a fixed generator), so no distribution is
assumed. The population is `Population.standard()` unless `--population`
names a JSON file holding `Population.as_dict()`.

**ac4, front-running of a programme.** Per seed, the certified roster
(`Universe.random(40, seed=111)`), 60 untraded sessions to read each name's
daily sigma, then on four names spread by market cap a programme of 10% of
the name's daily volume a day for 5 days, against a twin that does not trade,
priced at the twin's mid when each slice is sent: per-share cost in the
name's daily sigma. Predictable: 36 equal slices every 10 ticks from tick 15,
the same every day. Randomised: 36 slices at ticks drawn uniformly over 15 to
389 with normalised exponential sizes, a fresh draw each day from the
programme's own generator. Each schedule runs as a buy and as a sell from the
same state, and the two costs are averaged, which cancels the first-order
part of any difference between the isolated and the populated market at the
start (a price or an inventory leaning one way) and the detector's exposure
to the market's own moves. Isolated and populated runs share the seed. The
rows: (a) the predictable programme's populated cost over its isolated cost,
(b) that excess against the randomised programme's, (c) the detector's P&L
on the predictable programme (traded fork less the twin, in dollars and in
units of the name's daily dollar volume times its daily sigma), and the
populated predictable cost against the populated randomised cost.

**ac3, an edge as more capital trades it.** Per seed, `Universe.random(20,
seed=93000 + i)` at seed `92000 + i`, 60 days of 6 steps of 65 ticks, $1M
and leverage 2 per agent, for two price-only rules: the one-day reversal at
step cadence and the five-day momentum at daily cadence. (a) In populated
mode, k = 1, 2, 4 and 8 identical copies of the rule share one `World` with
one buy-and-hold agent; the per-copy excess return over buy-and-hold against
k, an OLS slope per seed, and the mean slope. (b) `evaluate` with the rule
and buy-and-hold, isolated and populated: the excess return in each mode.

**runtime.** Wall time of 20 sessions on the certified roster, isolated and
populated, untraded (`Engine`) and traded (a `World` whose agent buys and
sells five names every step), interleaved, best of three.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics as st
import struct
import sys
import time
from concurrent.futures import ProcessPoolExecutor

ROSTER = (40, 111)
PREDICTABLE = [(15 + 10 * k, 1 / 36) for k in range(36)]


# ------------------------------------------------------------------ helpers

def model_of(text):
    import tradefloor as tf
    if text.endswith(".txt"):
        head, _, tail = open(text).read().strip().partition(":")
        base = head.partition("@")[2] or "pt-v20"
        dials = {k: float(v) for k, v in (kv.split("=") for kv in tail.split(",") if kv)}
        return tf.ModelParams.from_preset(base, **dials)
    return tf.ModelParams.from_preset(text)


def population_of(path):
    import tradefloor as tf
    if not path:
        return tf.Population.standard()
    return tf.Population.from_dict(json.load(open(path)))


def seeds_of(text):
    out = []
    for part in text.split(","):
        a, _, b = part.partition("-")
        out.extend(range(int(a), int(b or a) + 1))
    return out


def f64(raw):
    return list(struct.unpack("<%dd" % (len(raw) // 8), raw))


def one_sided(values, flips=20_000, seed=20261004):
    """Mean, its standard error, and P(mean <= 0) by sign flips."""
    n = len(values)
    mean = st.fmean(values)
    se = st.stdev(values) / math.sqrt(n) if n > 1 else float("nan")
    rng = random.Random(seed)
    hits = 0
    for _ in range(flips):
        if st.fmean(v if rng.random() < 0.5 else -v for v in values) >= mean:
            hits += 1
    return {"mean": mean, "se": se, "n": n, "p": (hits + 1) / (flips + 1)}


def session(engine):
    engine.open_market()
    engine.run_session(9, 30, 3, 390, close_at_end=True)
    engine.close_market()


# ---------------------------------------------------------------------- ac4

def _warm(model, seed, population, sessions=60):
    import tradefloor as tf
    universe = tf.Universe.random(ROSTER[0], seed=ROSTER[1])
    engine = tf.Engine(seed=seed, universe=universe, model=model, population=population)
    closes = [f64(engine.prices())]
    for _ in range(sessions):
        session(engine)
        closes.append(f64(engine.prices()))
    sigma = []
    for i in range(len(universe)):
        r = [math.log(b[i] / a[i]) for a, b in zip(closes, closes[1:])]
        sigma.append(st.pstdev(r))
    return universe, engine, sigma


def _programme(e, twin, ticker, q_day, days, plan_of, side):
    num = den = 0.0
    sign = 1.0 if side > 0 else -1.0
    for d in range(days):
        for x in (e, twin):
            x.open_market()
        t = 0
        for tick, share in plan_of(d):
            if tick > t:
                m = 9 * 60 + 30 + t
                for x in (e, twin):
                    x.run_session(m // 60, m % 60, 3, tick - t)
                t = tick
            mid = twin.book(ticker).mid_price
            r = e.submit("programme", ticker, sign * q_day * share)
            num += r["filled"] * sign * (r["average_price"] - mid)
            den += r["filled"] * mid
        m = 9 * 60 + 30 + t
        for x in (e, twin):
            x.run_session(m // 60, m % 60, 3, 390 - t, close_at_end=True)
            x.close_market()
    return num / den


def _pnl(engine, name):
    for r in engine.population_report():
        if r["kind"] == "detector" and (name is None or r["name"] == name):
            return r["pnl"]
    return 0.0


def ac4_seed(args):
    model_text, population_path, seed, days = args
    model = model_of(model_text)
    population = population_of(population_path)
    bases = {"iso": _warm(model, seed, None), "pop": _warm(model, seed, population)}
    universe = bases["iso"][0]
    by_cap = sorted(range(len(universe)), key=lambda i: -universe[i].market_cap)
    pick = [by_cap[round(r * (len(universe) - 1) / 3)] for r in range(4)]
    rows = []
    for i in pick:
        ticker, q = universe[i].ticker, 0.1 * universe[i].avg_volume
        row = {"seed": seed, "name": i, "ticker": ticker}
        for sched in ("pred", "rand"):
            for mode in ("iso", "pop"):
                _, base, sigma = bases[mode]
                costs, pnl = [], 0.0
                for side in (1, -1):
                    rng = random.Random(seed * 1000 + i)

                    def plan(d, rng=rng):
                        if sched == "pred":
                            return PREDICTABLE
                        ticks = sorted(rng.sample(range(15, 390), 36))
                        w = [rng.expovariate(1.0) for _ in range(36)]
                        s = sum(w)
                        return [(t, x / s) for t, x in zip(ticks, w)]
                    e, twin = base.fork(2)
                    costs.append(_programme(e, twin, ticker, q, days, plan, side) / sigma[i])
                    if mode == "pop":
                        pnl += _pnl(e, None) - _pnl(twin, None)
                row[f"{sched}_{mode}"] = st.fmean(costs)
                if mode == "pop":
                    price = f64(base.prices())[i]
                    row[f"{sched}_detector_pnl"] = pnl
                    row[f"{sched}_detector_pnl_units"] = pnl / (
                        universe[i].avg_volume * price * sigma[i])
        rows.append(row)
    return rows


def ac4(a):
    jobs = [(a.model, a.population, s, 5) for s in seeds_of(a.seeds)]
    rows = []
    with ProcessPoolExecutor(a.workers) as pool:
        for part in pool.map(ac4_seed, jobs):
            rows.extend(part)
            for r in part:
                print(f"{r['seed']} {r['ticker']:5s} pred iso {r['pred_iso']:.4f} pop "
                      f"{r['pred_pop']:.4f}  rand iso {r['rand_iso']:.4f} pop {r['rand_pop']:.4f}  "
                      f"detector {r['pred_detector_pnl_units']:+.5f}", file=sys.stderr, flush=True)
    ex_pred = [r["pred_pop"] - r["pred_iso"] for r in rows]
    ex_rand = [r["rand_pop"] - r["rand_iso"] for r in rows]
    out = {
        "kind": "population.ac4", "model": a.model,
        "population": population_of(a.population).fingerprint,
        "cost": {k: st.fmean(r[k] for r in rows)
                 for k in ("pred_iso", "pred_pop", "rand_iso", "rand_pop")},
        "a_excess_predictable": one_sided(ex_pred),
        "excess_randomised": one_sided(ex_rand),
        "b_excess_predictable_over_randomised": one_sided(
            [x - y for x, y in zip(ex_pred, ex_rand)]),
        "c_detector_pnl_units": one_sided([r["pred_detector_pnl_units"] for r in rows]),
        "c_detector_pnl_dollars": one_sided([r["pred_detector_pnl"] for r in rows]),
        "detector_pnl_units_randomised": one_sided(
            [r["rand_detector_pnl_units"] for r in rows]),
        "isolated_predictable_over_randomised": one_sided(
            [r["pred_iso"] - r["rand_iso"] for r in rows]),
        "populated_predictable_over_randomised": one_sided(
            [r["pred_pop"] - r["rand_pop"] for r in rows]),
        "excess_share_of_isolated": st.fmean(ex_pred) / st.fmean(r["pred_iso"] for r in rows),
        "rows": rows,
    }
    return out


# ---------------------------------------------------------------------- ac3

RULES = ("mean_reversion_1day", "momentum_5day_daily")
SUITE = dict(steps_per_day=6, ticks_per_step=65, cash=1_000_000.0, max_leverage=2.0)


def rule(name):
    import tradefloor as tf
    if name == "mean_reversion_1day":
        return tf.StrategySpec.mean_reversion(lookback_days=1.0)
    return tf.StrategySpec(signal={"kind": "momentum", "lookback_days": 5.0},
                           execution={"cadence": "daily"})


def ac3_seed(args):
    import tradefloor as tf
    model_text, population_path, i, days = args
    model = model_of(model_text)
    population = population_of(population_path)
    seed, universe = 92000 + i, tf.Universe.random(20, seed=93000 + i)
    out = {"seed": seed}
    for name in RULES:
        per_k = {}
        for k in (1, 2, 4, 8):
            agents = {f"copy{j}": rule(name).build() for j in range(k)}
            agents["hold"] = tf.StrategySpec.hold().build()
            world = tf.World(seed=seed, universe=universe, agents=agents, model=model,
                             population=population, **SUITE)
            world.run(days)
            worth = {label: world.net_worth(agent=label) for label in agents}
            hold = worth["hold"] / SUITE["cash"] - 1
            per_k[k] = st.fmean(worth[f"copy{j}"] / SUITE["cash"] - 1 - hold for j in range(k))
        ks = list(per_k)
        mk, my = st.fmean(ks), st.fmean(per_k.values())
        slope = (sum((x - mk) * (per_k[x] - my) for x in ks)
                 / sum((x - mk) ** 2 for x in ks))
        out[name] = {"excess_by_k": per_k, "slope": slope}
        for mode, pop in (("iso", None), ("pop", population)):
            cards = tf.evaluate({"rule": rule(name), "hold": tf.StrategySpec.hold()},
                                seed=seed, universe=universe, days=days, model=model,
                                population=pop, **SUITE)
            out[name][f"excess_{mode}"] = (cards["rule"].return_pct
                                           - cards["hold"].return_pct) / 100.0
    return out


def ac3(a):
    jobs = [(a.model, a.population, s, a.days) for s in seeds_of(a.seeds)]
    rows = []
    with ProcessPoolExecutor(a.workers) as pool:
        for r in pool.map(ac3_seed, jobs):
            rows.append(r)
            print(json.dumps({k: (v if k == "seed" else {kk: vv for kk, vv in v.items()})
                              for k, v in r.items()}), file=sys.stderr, flush=True)
    out = {"kind": "population.ac3", "model": a.model,
           "population": population_of(a.population).fingerprint, "rows": rows}
    for name in RULES:
        out[name] = {
            "a_slope_below_zero": one_sided([-r[name]["slope"] for r in rows]),
            "excess_by_k": {k: st.fmean(r[name]["excess_by_k"][k] for r in rows)
                            for k in (1, 2, 4, 8)},
            "b_isolated_over_populated": one_sided(
                [r[name]["excess_iso"] - r[name]["excess_pop"] for r in rows]),
            "excess_iso": st.fmean(r[name]["excess_iso"] for r in rows),
            "excess_pop": st.fmean(r[name]["excess_pop"] for r in rows),
        }
    return out


# ------------------------------------------------------------------ runtime

class _Trader:
    def __init__(self, tickers):
        self.tickers = tickers

    def act(self, obs):
        return {t: (500.0 if obs.step % 2 == 0 else -500.0) for t in self.tickers}


def runtime(a):
    import tradefloor as tf
    model = model_of(a.model)
    population = population_of(a.population)
    universe = tf.Universe.random(ROSTER[0], seed=ROSTER[1])
    days = 20

    def untraded(pop):
        e = tf.Engine(seed=5, universe=universe, model=model, population=pop)
        t = time.perf_counter()
        for _ in range(days):
            session(e)
        return time.perf_counter() - t

    def traded(pop):
        w = tf.World(seed=5, universe=universe, agent=_Trader([x.ticker for x in universe[:5]]),
                     model=model, population=pop, cash=1e12, max_leverage=None)
        t = time.perf_counter()
        w.run(days)
        return time.perf_counter() - t

    out = {"kind": "population.runtime", "model": a.model, "days": days}
    for label, fn in (("untraded", untraded), ("traded", traded)):
        iso, pop = [], []
        for _ in range(3):
            iso.append(fn(None))
            pop.append(fn(population))
        out[label] = {"isolated_s": min(iso), "populated_s": min(pop),
                      "ratio": min(pop) / min(iso)}
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("check", choices=("ac3", "ac4", "runtime"))
    ap.add_argument("--model", default="pt-v20")
    ap.add_argument("--population", default="")
    ap.add_argument("--seeds", default="201-204")
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    result = {"ac3": ac3, "ac4": ac4, "runtime": runtime}[a.check](a)
    text = json.dumps(result, indent=1)
    if a.out:
        open(a.out, "w").write(text)
    summary = {k: v for k, v in result.items() if k != "rows"}
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
