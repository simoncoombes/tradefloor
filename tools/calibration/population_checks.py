"""Measure what a population does to agents: crowding, front-running, cost.

    python tools/calibration/population_checks.py ac4 --seeds 201-208 [--model M]
    python tools/calibration/population_checks.py ac3 --seeds 1-8 [--model M]
    python tools/calibration/population_checks.py cx --limited A --free B [--limited4 C --free4 D]
    python tools/calibration/population_checks.py runtime [--model M]

`--model` is a preset name or a file holding `NAME@BASE:dial=value,...` (an
arm). `--workers` runs seeds in parallel (default 4). Every comparison is
paired on the seed, and its one-sided p-value is a sign-flip permutation test
of the mean (20,000 flips from a fixed generator), so no distribution is
assumed. The population is `Population.standard()` unless `--population`
names a JSON file holding `Population.as_dict()`, `none`, a shipped
population (`standard`, `crowded`), or `crowded:` with arguments to
`Population.crowded` (`crowded:reversal=0.008,momentum=0.04,stop=0`).
`--crowd size=...,stop=...,count=N` adds, for each rule in ac3, a crowd that
trades that rule's own signal.

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
(b) that excess against the randomised programme's, (c) the detectors' P&L
on the predictable programme (traded fork less the twin, in dollars and in
units of the name's daily dollar volume times its daily sigma; all detectors
together, and each on its own), and the
populated predictable cost against the populated randomised cost. Each
mode's cost is in its own warm-up sigma, as registered;
`a_excess_predictable_iso_sigma` also reads the populated cost in the isolated
market's sigma, because a population that moves a name's volatility moves the
first reading by that share without any change in what the programme paid.

**ac3, an edge as more capital trades it.** Per seed, `Universe.random(20,
seed=93000 + i)` at seed `92000 + i`, 60 days of 6 steps of 65 ticks, $1M
and leverage 2 per agent, for two price-only rules: the one-day reversal at
step cadence and the five-day momentum at daily cadence. (a) In populated
mode, k = 1, 2, 4 and 8 identical copies of the rule share one `World` with
one buy-and-hold agent; the per-copy excess return over buy-and-hold against
k, an OLS slope per seed, and the mean slope. (b) `evaluate` with the rule
and buy-and-hold, isolated and populated: the excess return in each mode.
Beside it, two readings that split the edge: `paper`, what the rule's
positions made at the step prices it saw, before what it paid to trade; and
`signal`, the frictionless return of the rule's ranked book on the prices
the buy-and-hold agent saw (its market has no rule trading in it), which is
what a study of an anomaly measures, so `1 - signal_pop / signal_iso` is the
decay a crowd causes. The rule's daily returns in each mode, and in populated
mode the mean exposure of the crowds trading its signal, are kept per seed
for `cx`.

**cx, crowded exits.** From two ac3 part-b outputs on the same seeds, a
population whose crowds carry loss limits (`--limited`) and the same one
without (`--free`): on each day a crowd trading the rule's signal sold out,
the rule's daily return in the limited run less the free run, in the rule's
isolated daily sd, averaged per seed; (a) below zero; the five sessions
after (the rebound of a liquidity event); and with `--limited4` and
`--free4` at four times the crowd capital, (b) deeper there.

**runtime.** Wall time of `--days` sessions (60 by default) on the certified
roster, isolated and populated, untraded (`Engine`) and traded (a `World`
whose agent buys and sells five names every step), interleaved, best of
five.
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
    if path == "none":
        return None
    if not path.endswith(".json"):
        head, _, args = path.partition(":")
        kw = {k: float(v) for k, v in (kv.split("=") for kv in args.split(",") if kv)}
        if "members" in kw:
            kw["members"] = int(kw["members"])
        return tf.Population.crowded(**kw) if head == "crowded" else tf.Population.named(head)
    return tf.Population.from_dict(json.load(open(path)))


def crowd_of(text, signal, lookback):
    """A crowd participant from `size=0.03,stop=0.02,...` for one rule's
    signal and lookback, or None when `text` is empty."""
    from tradefloor.population import Participant
    if not text:
        return None
    fields = {k: float(v) for k, v in (kv.split("=") for kv in text.split(",") if kv)}
    count = int(fields.pop("count", 1))
    spread = fields.pop("stop_spread", 0.0)
    for k in ("interval", "top_k"):
        if k in fields:
            fields[k] = int(fields[k])
    out = []
    for j in range(count):
        f = dict(fields)
        if count > 1 and f.get("stop", 0.0) > 0:
            f["stop"] = f["stop"] * (1 + spread * (j - (count - 1) / 2) / max(count - 1, 1))
        out.append(Participant.crowd(signal=signal, lookback=lookback, name=f"crowd{j}", **f))
    return out


def population_for(base_path, crowd_text, rule_name):
    """The base population plus, when `crowd_text` is given, a crowd that
    trades `rule_name`'s own signal at its own lookback."""
    import tradefloor as tf
    base = population_of(base_path)
    signal, lookback = RULE_SIGNAL[rule_name]
    crowd = crowd_of(crowd_text, signal, lookback)
    if not crowd:
        return base
    items = (list(base.participants) if base is not None else []) + crowd
    return tf.Population(items, name="crowded")


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
    """A detector's P&L, or with `name` None every detector's together."""
    return sum(r["pnl"] for r in engine.population_report()
               if r["kind"] == "detector" and (name is None or r["name"] == name))


def _detectors(engine):
    return [r["name"] for r in engine.population_report() if r["kind"] == "detector"]


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
                each = {}
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
                        for d in _detectors(e):
                            each[d] = each.get(d, 0.0) + _pnl(e, d) - _pnl(twin, d)
                row[f"{sched}_{mode}"] = st.fmean(costs)
                # The same cost in the isolated market's sigma, so the two
                # modes are read in one unit: a population that moves the
                # name's own volatility otherwise moves the reading too.
                row[f"{sched}_{mode}_iso_sigma"] = st.fmean(costs) * sigma[i] / bases["iso"][2][i]
                row[f"sigma_{mode}"] = sigma[i]
                if mode == "pop":
                    price = f64(base.prices())[i]
                    row[f"{sched}_detector_pnl"] = pnl
                    row[f"{sched}_detector_pnl_units"] = pnl / (
                        universe[i].avg_volume * price * sigma[i])
                    row[f"{sched}_each_detector_pnl_units"] = {
                        d: v / (universe[i].avg_volume * price * sigma[i]) for d, v in each.items()}
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
        "c_each_detector_pnl_units": {
            d: one_sided([r["pred_each_detector_pnl_units"][d] for r in rows])
            for d in rows[0]["pred_each_detector_pnl_units"]},
        "detector_pnl_units_randomised": one_sided(
            [r["rand_detector_pnl_units"] for r in rows]),
        "isolated_predictable_over_randomised": one_sided(
            [r["pred_iso"] - r["rand_iso"] for r in rows]),
        "populated_predictable_over_randomised": one_sided(
            [r["pred_pop"] - r["rand_pop"] for r in rows]),
        "excess_share_of_isolated": st.fmean(ex_pred) / st.fmean(r["pred_iso"] for r in rows),
        "a_excess_predictable_iso_sigma": one_sided(
            [r["pred_pop_iso_sigma"] - r["pred_iso"] for r in rows]),
        "excess_randomised_iso_sigma": one_sided(
            [r["rand_pop_iso_sigma"] - r["rand_iso"] for r in rows]),
        "sigma_populated_over_isolated": st.fmean(r["sigma_pop"] / r["sigma_iso"] for r in rows),
        "rows": rows,
    }
    return out


# ---------------------------------------------------------------------- ac3

RULES = ("mean_reversion_1day", "momentum_5day_daily")
#: The signal and lookback, in open ticks, of a crowd trading each rule's
#: own signal: the one-day reversal over 390 ticks, the five-day momentum
#: over 1950.
RULE_SIGNAL = {"mean_reversion_1day": ("reversal", 390),
               "momentum_5day_daily": ("momentum", 1950)}


class Paper:
    """Wraps an agent and books what its positions earned at the step
    prices it saw, before any cost of trading, and its worth at each step."""

    privileged = False

    def __init__(self, inner, signal=""):
        self.inner = inner
        self.signal = signal
        self.prev = None
        self.paper = 0.0
        self.worth = []
        self.marks = []
        self.exposure = []
        self.stops = 0.0

    def act(self, obs):
        # The measuring wrapper reads the live engine behind the agent's
        # view (the agent it wraps never sees it).
        from tradefloor.sandbox import _WRAPPED
        engine = _WRAPPED.get(obs.engine, obs.engine)
        crowds = [r for r in engine.population_report()
                  if r["kind"] == "crowd" and r["signal"] == self.signal]
        if crowds:
            self.exposure.append(st.fmean(r["exposure"] for r in crowds))
            self.stops = sum(r["stops"] for r in crowds)
        prices = list(obs.prices)
        pos = [obs.position(t) for t in obs.tickers]
        if self.prev is not None:
            self.paper += sum(q * (p - b) for q, p, b in zip(pos, prices, self.prev))
        self.prev = prices
        self.worth.append(obs.portfolio.net_worth(obs.engine))
        self.marks.append(self.paper)
        return self.inner.act(obs)

SUITE = dict(steps_per_day=6, ticks_per_step=65, cash=1_000_000.0, max_leverage=2.0)


def rule(name):
    import tradefloor as tf
    if name == "mean_reversion_1day":
        return tf.StrategySpec.mean_reversion(lookback_days=1.0)
    return tf.StrategySpec(signal={"kind": "momentum", "lookback_days": 5.0},
                           execution={"cadence": "daily"})


class Observe:
    """Wraps the buy-and-hold agent and books, from the prices it sees, the
    frictionless return of each rule's ranked book: long the five names the
    rule ranks first and short the five it ranks last, equal weights summing
    to a gross of one, at the rule's own cadence. Nothing it books trades, so
    this is the signal's return on the market's prices (what a study of an
    anomaly measures), apart from what the rule's own trading does to them."""

    privileged = False

    def __init__(self, inner, steps_per_day):
        self.inner = inner
        self.per_day = steps_per_day
        self.history = []
        self.books = {name: None for name in RULES}
        self.returns = {name: [] for name in RULES}

    def _book(self, name, tickers):
        signal, lookback = RULE_SIGNAL[name]
        step = 1 if name == "mean_reversion_1day" else self.per_day
        back = lookback // 65 if step == 1 else lookback // 390 * self.per_day
        if len(self.history) <= back:
            return None
        now, past = self.history[-1], self.history[-1 - back]
        r = [(a / b - 1.0) if b > 0 else 0.0 for a, b in zip(now, past)]
        sign = -1.0 if signal == "momentum" else 1.0
        order = sorted(range(len(r)), key=lambda i: (sign * r[i], tickers[i]))
        k = min(5, len(r) // 2)
        w = [0.0] * len(r)
        for i in order[:k]:
            w[i] = 0.5 / k
        for i in order[-k:]:
            w[i] = -0.5 / k
        return w

    def act(self, obs):
        prices = list(obs.prices)
        self.history.append(prices)
        step = len(self.history) - 1
        for name in RULES:
            cadence = 1 if name == "mean_reversion_1day" else self.per_day
            book = self.books[name]
            if book is not None and step % cadence == 0:
                prev = self.history[-1 - cadence]
                self.returns[name].append(sum(
                    wi * (p / q - 1.0) for wi, p, q in zip(book, prices, prev) if q > 0))
            if step % cadence == 0:
                self.books[name] = self._book(name, obs.tickers)
        return self.inner.act(obs)


def ac3_seed(args):
    import tradefloor as tf
    model_text, population_path, i, days, parts, crowd_text, rules = args
    model = model_of(model_text)
    seed, universe = 92000 + i, tf.Universe.random(20, seed=93000 + i)
    out = {"seed": seed}
    for name in rules:
        population = population_for(population_path, crowd_text, name)
        per_k = {}
        for k in ((1, 2, 4, 8) if "a" in parts else ()):
            agents = {f"copy{j}": rule(name).build() for j in range(k)}
            agents["hold"] = tf.StrategySpec.hold().build()
            world = tf.World(seed=seed, universe=universe, agents=agents, model=model,
                             population=population, **SUITE)
            world.run(days)
            worth = {label: world.net_worth(agent=label) for label in agents}
            hold = worth["hold"] / SUITE["cash"] - 1
            per_k[k] = st.fmean(worth[f"copy{j}"] / SUITE["cash"] - 1 - hold for j in range(k))
        out[name] = {}
        if per_k:
            ks = list(per_k)
            mk, my = st.fmean(ks), st.fmean(per_k.values())
            out[name] = {"excess_by_k": per_k, "slope": (
                sum((x - mk) * (per_k[x] - my) for x in ks)
                / sum((x - mk) ** 2 for x in ks))}
        for mode, pop in ((("iso", None), ("pop", population)) if "b" in parts else ()):
            paper = Paper(rule(name).build(), RULE_SIGNAL[name][0])
            watch = Observe(tf.StrategySpec.hold().build(), SUITE["steps_per_day"])
            cards = tf.evaluate({"rule": paper, "hold": watch},
                                seed=seed, universe=universe, days=days, model=model,
                                population=pop, **SUITE)
            out[name][f"excess_{mode}"] = (cards["rule"].return_pct
                                           - cards["hold"].return_pct) / 100.0
            out[name][f"paper_{mode}"] = paper.paper / SUITE["cash"]
            out[name][f"signal_{mode}"] = sum(watch.returns[name])
            out[name][f"signal_steps_{mode}"] = watch.returns[name]
            per_day = SUITE["steps_per_day"]
            worth = paper.worth[::per_day]
            marks = paper.marks[::per_day]
            out[name][f"daily_{mode}"] = [b / a - 1 for a, b in zip(worth, worth[1:])]
            if mode == "pop":
                out[name]["crowd_stops"] = paper.stops
                out[name]["crowd_exposure_daily"] = paper.exposure[::per_day]
            out[name][f"paper_daily_{mode}"] = [(b - a) / SUITE["cash"]
                                                for a, b in zip(marks, marks[1:])]
    return out


def ac3(a):
    rules = tuple(r for r in RULES if not a.rules or r in a.rules.split(","))
    jobs = [(a.model, a.population, s, a.days, a.parts, a.crowd, rules)
            for s in seeds_of(a.seeds)]
    rows = []
    with ProcessPoolExecutor(a.workers) as pool:
        for r in pool.map(ac3_seed, jobs):
            rows.append(r)
            print(json.dumps({k: (v if k == "seed" else {kk: vv for kk, vv in v.items()
                                                         if "daily" not in kk and "steps" not in kk})
                              for k, v in r.items()}), file=sys.stderr, flush=True)
    out = {"kind": "population.ac3", "model": a.model, "crowd": a.crowd,
           "population": {name: getattr(population_for(a.population, a.crowd, name),
                                        "fingerprint", None) for name in rules},
           "rows": rows}
    for name in rules:
        out[name] = {}
        if "a" in a.parts:
            out[name].update({
                "a_slope_below_zero": one_sided([-r[name]["slope"] for r in rows]),
                "excess_by_k": {k: st.fmean(r[name]["excess_by_k"][k] for r in rows)
                                for k in (1, 2, 4, 8)}})
        if "b" in a.parts:
            out[name].update({
                "b_isolated_over_populated": one_sided(
                    [r[name]["excess_iso"] - r[name]["excess_pop"] for r in rows]),
                "excess_iso": st.fmean(r[name]["excess_iso"] for r in rows),
                "excess_pop": st.fmean(r[name]["excess_pop"] for r in rows),
                "paper_isolated_over_populated": one_sided(
                    [r[name]["paper_iso"] - r[name]["paper_pop"] for r in rows]),
                "paper_iso": st.fmean(r[name]["paper_iso"] for r in rows),
                "signal_isolated_over_populated": one_sided(
                    [r[name]["signal_iso"] - r[name]["signal_pop"] for r in rows]),
                "signal_iso": st.fmean(r[name]["signal_iso"] for r in rows),
                "signal_pop": st.fmean(r[name]["signal_pop"] for r in rows),
                "paper_pop": st.fmean(r[name]["paper_pop"] for r in rows)})
    return out


# ----------------------------------------------------------------------- cx

def _unwinds(limited, free, rule_name):
    """Per seed, from two ac3 part-b outputs on the same seeds (a crowd with
    loss limits, the same crowd without): the rule's daily excess of the
    limited run over the free one on the days a same-signal crowd member
    sold out, and over the five sessions after, each in the rule's isolated
    daily sd. Seeds with no sell-out are left out."""
    a = {r["seed"]: r[rule_name] for r in limited["rows"]}
    b = {r["seed"]: r[rule_name] for r in free["rows"]}
    out = {}
    for seed in sorted(set(a) & set(b)):
        x, y = a[seed], b[seed]
        sd = st.pstdev(x["daily_iso"])
        da, db, ex = x["daily_pop"], y["daily_pop"], x["crowd_exposure_daily"]
        on, after = [], []
        for i in range(min(len(ex) - 1, len(da))):
            if ex[i + 1] < ex[i] - 1e-12:
                on.append((da[i] - db[i]) / sd)
                nxt = range(i + 1, min(i + 6, len(da)))
                if nxt:
                    after.append(sum(da[j] - db[j] for j in nxt) / sd)
        if on:
            out[seed] = {"on": st.fmean(on), "after": st.fmean(after) if after else 0.0,
                         "days": len(on)}
    return out


def cx(a):
    """CX1, crowded exits: read from ac3 part-b outputs (see the module
    docstring)."""
    load = lambda path: json.load(open(path))
    base = (load(a.limited), load(a.free))
    big = (load(a.limited4), load(a.free4)) if a.limited4 else None
    out = {"kind": "population.cx"}
    for name in RULES:
        if name not in base[0]["rows"][0]:
            continue
        one = _unwinds(*base, name)
        row = {"seeds_with_unwinds": len(one),
               "unwind_days": sum(v["days"] for v in one.values()),
               "a_unwind_day_loss": one_sided([-v["on"] for v in one.values()]),
               "unwind_day_excess_sd": st.fmean(v["on"] for v in one.values()),
               "after_five_sessions_sd": one_sided([v["after"] for v in one.values()])}
        if big:
            four = _unwinds(*big, name)
            both = sorted(set(one) & set(four))
            row["unwind_day_excess_sd_4x"] = st.fmean(four[s]["on"] for s in four)
            row["b_deeper_at_4x"] = one_sided([one[s]["on"] - four[s]["on"] for s in both])
        out[name] = row
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
    days = a.days

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
        for _ in range(5):
            iso.append(fn(None))
            pop.append(fn(population))
        out[label] = {"isolated_s": min(iso), "populated_s": min(pop),
                      "ratio": min(pop) / min(iso)}
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("check", choices=("ac3", "ac4", "cx", "runtime"))
    ap.add_argument("--model", default="pt-v20")
    ap.add_argument("--population", default="")
    ap.add_argument("--seeds", default="201-204")
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--parts", default="ab", help="ac3: a (copies), b (modes) or both")
    ap.add_argument("--rules", default="", help="ac3: a comma list of rules (default both)")
    ap.add_argument("--crowd", default="",
                    help="ac3: add a crowd trading each rule's own signal, "
                         "as size=0.03,stop=0.02,rate=...,count=N")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limited", default="", help="cx: ac3 output, crowd with loss limits")
    ap.add_argument("--free", default="", help="cx: ac3 output, the same crowd without")
    ap.add_argument("--limited4", default="", help="cx: as --limited at four times the capital")
    ap.add_argument("--free4", default="", help="cx: as --free at four times the capital")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    result = {"ac3": ac3, "ac4": ac4, "cx": cx, "runtime": runtime}[a.check](a)
    text = json.dumps(result, indent=1)
    if a.out:
        open(a.out, "w").write(text)
    summary = {k: v for k, v in result.items() if k != "rows"}
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
