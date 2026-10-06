"""Registered rows PH1 and PH3 on the wired harness (sim/real-prehistory).

PH1: seeds 2001-2005, Universe.random(8, seed=111), pt-v20, N in {0,21,252},
T=5. After prehistory(N) the harness forks k+1 engines; each fork advanced T
untraded days has the state hash of an untraded run of N+T, all forks are
identical, and evaluate()'s baseline equals _run_untraded(N+T).

PH3: seeds 2001-2030, history_days=252, T=252 scored days, a 200-day SMA
filter and a 252-day TSMOM agent on the equal-weight roster: active fraction
of scored days with history, and cold (history_days=0) for comparison.

usage: ph_rows.py ph1 SEED | ph3 SEED HISTORY_DAYS   (prints one JSON line)
"""
import json
import sys

import tradefloor as tf
from tradefloor.harness import _advance_untraded, _f64, _run_untraded
from tradefloor.history import prehistory

SPD, TPS = 6, 65
U = list(tf.Universe.random(8, seed=111))
# r14 screen: PH_DIALS (k=v,...) runs the rows on an arm's model rather than pt-v20 as graded
import os as _os
_D = {k: float(v) for k, v in (x.split("=") for x in _os.environ.get("PH_DIALS", "").split(",") if x)}
MODEL = tf.ModelParams.from_preset("pt-v20", **_D) if _D else "pt-v20"


def ph1(seed):
    out = {"seed": seed, "rows": []}
    for n in (0, 21, 252):
        t = 5
        root = tf.Engine(seed=seed, universe=U, model=MODEL)
        prehistory(root, n)
        forks = root.fork(4)
        hashes = []
        for f in forks:
            _advance_untraded(f, t, SPD, TPS, 9, 30, 3)
            hashes.append(f.state_hash())
        off = tf.Engine(seed=seed, universe=U, model=MODEL)
        prehistory(off, n + t)
        base = _run_untraded(seed, U, None, n + t, SPD, TPS, 9, 30, 3, None,
                             MODEL)
        out["rows"].append({
            "n": n, "forks_identical": len(set(hashes)) == 1,
            "equals_off": hashes[0] == off.state_hash(),
            "baseline_prices_equal": _f64(forks[0].prices()) == base,
            "hash": hashes[0][:16]})
    return out


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
        if obs.history is not None:
            rows = obs.history.closes()
        else:
            rows = self.own
        return [sum(r) / len(r) for r in rows]

    def act(self, obs):
        if not obs.is_first_step_of_day:
            return {}
        s = self.series(obs)
        if obs.history is None:
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
    return {"seed": seed, "history_days": history_days,
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
