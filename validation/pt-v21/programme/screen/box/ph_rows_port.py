"""Registered rows PH1 and PH3 on the wired harness, on either history API.

The fifteenth registration's ph_rows.py (r13reg/box/ph_rows.py at
registration/fifteenth) imports sim/r21's history API:
tradefloor.history.prehistory and harness._advance_untraded. The pt-v21 base
(0.9.1 plus the sim/r21 vector) has 0.8.5's API instead: harness.History and
harness._warm_up for the warm-up, and harness._run_untraded(..., engine=E) to
advance an existing engine. The rows and their protocol are unchanged; only
the calls are mapped:

    sim/r21                                  base (0.8.5+)
    prehistory(E, N)                         _warm_up(E, N, steps_per_day=6, ticks_per_step=65,
                                                      start=(9, 30, 3), history=History(N))
    _advance_untraded(E, T, 6, 65, 9, 30, 3) _run_untraded(None, None, None, T, 6, 65, 9, 30, 3,
                                                           engine=E)
    obs.history.closes()                     obs.history.bars() grouped by day, in roster order
    obs.history is None (cold)               obs.history.warmup_days == 0 (cold)

Both warm-ups run the harness's untraded day (open, six sessions of 65 ticks
on the advancing clock from 9:30 Wednesday, close) and take no draw beyond
it, so after N + T days the market is the same on both APIs. A cold agent in
0.8.5+ is handed a History that grows with the scored days; it is treated as
cold here and keeps its own opening prices, exactly as it did on sim/r21, so
the cold reading is the same statistic. A warm agent reads the warm-up's
closes and then each scored day's close on both APIs. (sim/r21's bar close is
the price after the close and 0.8.5's is the last print before it; PH3 counts
active days, which do not depend on that, and its return_pct is reported only.)

Each PH1 row also carries prices_sha, the sha256 of the forked engine's prices
after N + T days, so a reading on one engine can be compared with another's.

PH1: seeds 2001-2005, Universe.random(8, seed=111), pt-v20, N in {0,21,252},
T=5. After a warm-up of N days the harness forks k+1 engines; each fork advanced T
untraded days has the state hash of an untraded run of N+T, all forks are
identical, and evaluate()'s baseline equals _run_untraded(N+T).

PH3: seeds 2001-2030, history_days=252, T=252 scored days, a 200-day SMA
filter and a 252-day TSMOM agent on the equal-weight roster: active fraction
of scored days with history, and cold (history_days=0) for comparison.

usage: ph_rows.py ph1 SEED | ph3 SEED HISTORY_DAYS   (prints one JSON line)
"""
import hashlib
import json
import os as _os
import sys

import tradefloor as tf
from tradefloor.harness import _f64, _run_untraded

try:  # sim/r21
    from tradefloor.harness import _advance_untraded
    from tradefloor.history import prehistory
    API = "prehistory"
except ImportError:  # 0.8.5 and later
    from tradefloor.harness import History, _warm_up
    API = "warm_up"

SPD, TPS = 6, 65
START = (9, 30, 3)
U = list(tf.Universe.random(8, seed=111))
# r14 screen: PH_DIALS (k=v,...) runs the rows on an arm's model rather than pt-v20 as graded
_D = {k: float(v) for k, v in (x.split("=") for x in _os.environ.get("PH_DIALS", "").split(",") if x)}
MODEL = tf.ModelParams.from_preset("pt-v20", **_D) if _D else "pt-v20"


def warm(engine, n):
    """N untraded harness days on ``engine``, as evaluate(history_days=N) runs them."""
    if API == "prehistory":
        prehistory(engine, n)
    else:
        _warm_up(engine, n, steps_per_day=SPD, ticks_per_step=TPS, start=START,
                 history=History(n))


def advance(engine, t):
    """T more untraded harness days on ``engine``."""
    if API == "prehistory":
        _advance_untraded(engine, t, SPD, TPS, *START)
    else:
        _run_untraded(None, None, None, t, SPD, TPS, *START, engine=engine)


def ph1(seed):
    out = {"seed": seed, "api": API, "rows": []}
    for n in (0, 21, 252):
        t = 5
        root = tf.Engine(seed=seed, universe=U, model=MODEL)
        warm(root, n)
        forks = root.fork(4)
        hashes = []
        for f in forks:
            advance(f, t)
            hashes.append(f.state_hash())
        off = tf.Engine(seed=seed, universe=U, model=MODEL)
        warm(off, n + t)
        base = _run_untraded(seed, U, None, n + t, SPD, TPS, *START, None, MODEL)
        prices = forks[0].prices()
        out["rows"].append({
            "n": n, "forks_identical": len(set(hashes)) == 1,
            "equals_off": hashes[0] == off.state_hash(),
            "baseline_prices_equal": _f64(prices) == base,
            "hash": hashes[0][:16],
            "prices_sha": hashlib.sha256(bytes(prices)).hexdigest()[:16]})
    return out


def _cold(obs):
    h = obs.history
    return h is None or (API == "warm_up" and h.warmup_days == 0)


def _closes(history):
    """One row of closes per day held, oldest first, in roster order."""
    if API == "prehistory":
        return history.closes()
    rows, day = [], None
    for bar in history.bars():
        if bar["day"] != day:
            day = bar["day"]
            rows.append([])
        rows[-1].append(bar["close"])
    return rows


class _Filter:
    """Long the equal-weight roster when the signal is on, flat otherwise.

    Decides once a day at the open. Its series is each day's close from
    obs.history, extended by what it has seen itself when there is none
    (cold): each scored day's opening prices, the only daily price a cold
    agent can collect."""

    def __init__(self, need, rule):
        self.need, self.rule = need, rule
        self.own = []
        self.days = self.active = 0
        self.on = False

    def series(self, obs):
        rows = self.own if _cold(obs) else _closes(obs.history)
        return [sum(r) / len(r) for r in rows]

    def act(self, obs):
        if not obs.is_first_step_of_day:
            return {}
        s = self.series(obs)
        if _cold(obs):
            self.own.append(list(obs.prices))
        self.days += 1
        if len(s) < self.need:
            want = False
        else:
            self.active += 1
            now = sum(obs.prices) / len(obs.prices)
            if self.rule == "sma":
                want = now > sum(s[-self.need:]) / self.need
            else:
                want = now > s[-self.need]
        if want == self.on:
            return {}
        self.on = want
        worth = obs.portfolio.net_worth()
        orders = {}
        for t, p in zip(obs.tickers, obs.prices):
            target = (worth / len(obs.tickers) / p) if want else 0.0
            delta = target - obs.position(t)
            if abs(delta) >= 1:
                orders[t] = delta
        return orders


def ph3(seed, history_days):
    agents = {"sma200": _Filter(200, "sma"), "tsmom252": _Filter(252, "ts")}
    cards = tf.evaluate(agents, seed=seed, universe=U, days=252,
                        model=MODEL, history_days=history_days,
                        max_leverage=1.0)
    return {"seed": seed, "history_days": history_days, "api": API,
            **{n: {"active": a.active / a.days, "days": a.days,
                   "return_pct": cards[n].return_pct,
                   "errors": cards[n].errors[:2],
                   "card_history_days": cards[n].history_days}
               for n, a in agents.items()}}


if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "ph1":
        print(json.dumps(ph1(int(sys.argv[2]))), flush=True)
    else:
        print(json.dumps(ph3(int(sys.argv[2]), int(sys.argv[3]))), flush=True)
