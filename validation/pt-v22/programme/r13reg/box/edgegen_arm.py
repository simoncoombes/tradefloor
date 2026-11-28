"""VARIANT (r14 screen): EDGE_DIALS (k=v,...) sets the model, pt-v20 with those dials, for gen and eval.
edgegen -- stock-level pt-v20 histories and harness checks for the free-lunch audit.

  python edgegen.py gen  --seeds A-B --years Y --roster same|vary --workers W --out DIR
  python edgegen.py eval --seeds A-B --days D --workers W --out DIR [--cash-interest 0|1]
  python edgegen.py check                     (stepped day == run_days, a few days)

gen: per seed, Universe.random(40, seed=seed if vary else 111), the shipped
default model (pt-v20), each day stepped as the harness steps it
(open_market, six 65-tick sessions, close_market). Saved per seed,
DIR/gen/SEED.npz, float32 unless noted:
  px[d, k, i]  price of name i at snapshot k of day d: k=0 after open_market
               (what an agent's first step sees and trades at), k=1..6 after
               each 65-tick step (k=6 the last print before the close's macro
               step), k=7 after close_market (the close's re-mark included)
  vol[d, i]    the day's volume; hs[d, i] the book's half-spread / mid at the open
  news[d, i]   1 if the agent-visible news list names i on day d
  m_<field>[d] published macro fields after close d (float64); cyc[d] published phase
  (the true phase is the published one 252 sessions on: cycle_publication_lag)
  sector, beta, mcap0, shares, adv, si (short interest / float)
eval: tf.evaluate on the certified roster, days D, for the agents below; one
(seed, agent) per task; writes DIR/eval/SEED-AGENT.json. Each record carries
the agent's own mean gross exposure over the run (mean_gross: gross position
over net worth, read at the start of every step before the agent trades, so
the position carried into each step; the thirteenth registration's exploit
gate, owner's decision of 2026-09-28 (5)).
evalem: for every levered agent in --agents (LEVERED) and every seed, the
exposure-matched position: the constant agent at that run's mean_gross (read
from eval's record, so eval runs first), in a run of its own on the same seed.
Writes DIR/eval/SEED-em_AGENT.json with em_of, em_level and its own mean_gross.
"""
import argparse
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

import numpy as np

_DIALS = {k: float(v) for k, v in (x.split("=") for x in os.environ.get("EDGE_DIALS", "").split(",") if x)}


def _model():
    import tradefloor as tf
    return tf.ModelParams.from_preset("pt-v20", **_DIALS)

CYCLES = ("expansion", "peak", "contraction", "trough", "recovery")
MACRO = ("vix", "federal_funds_rate", "corporate_bond_yield", "inflation_rate", "gdp_growth",
         "unemployment_rate", "oil_price", "treasury_yield_2y", "treasury_yield_10y",
         "fear_greed_index", "tariff_rate")


def f64(b):
    return np.frombuffer(b, dtype="<f8").copy()


def gen_one(seed, years, roster, outdir, days=None):
    import tradefloor as tf
    from tradefloor.harness import session_clock
    path = os.path.join(outdir, f"{seed}.npz")
    if os.path.exists(path):
        return seed, "exists", 0.0
    t0 = time.time()
    u = list(tf.Universe.random(40, seed=seed if roster == "vary" else 111))
    e = tf.Engine(seed=seed, universe=u, model=_model())
    N = len(e.tickers); D = days or years * 252
    tick_of = {t: i for i, t in enumerate(e.tickers)}
    px = np.zeros((D, 8, N), np.float32); vol = np.zeros((D, N), np.float32)
    hs = np.zeros((D, N), np.float32); news = np.zeros((D, N), np.int8)
    mac = {k: np.zeros(D) for k in MACRO}; cyc = np.zeros(D, np.int8)
    fl0 = f64(e.column("float_shares"))
    start = (9, 30, 3)
    for d in range(D):
        e.open_market()
        px[d, 0] = f64(e.prices())
        for ev in e.session_news():
            i = tick_of.get(ev.get("ticker"))
            if i is not None:
                news[d, i] = 1
        for i, t in enumerate(e.tickers):
            bk = e.book(t)
            if bk.spread is not None and bk.mid_price:
                hs[d, i] = 0.5 * bk.spread / bk.mid_price
        for k in range(6):
            e.run_session(*session_clock(start, k, 65), 65)
            px[d, k + 1] = f64(e.prices())
        e.close_market()
        px[d, 7] = f64(e.prices())
        vol[d] = f64(e.column("volume"))
        mf = e.macro_fields
        for k in MACRO:
            mac[k][d] = float(mf.get(k, np.nan))
        cyc[d] = CYCLES.index(mf["cycle"])
    out = dict(px=px, vol=vol, hs=hs, news=news, cyc=cyc,
               sector=np.array([i.sector for i in u]), beta=np.array([i.beta for i in u]),
               mcap0=np.array([i.market_cap for i in u]), shares=np.array([float(i.shares_outstanding) for i in u]),
               adv=np.array([i.avg_volume for i in u]),
               si=np.array([float(i.short_interest) for i in u]) / np.where(fl0 > 0, fl0, np.nan),
               hash=np.array(e.state_hash()))
    for k, v in mac.items():
        out["m_" + k] = v
    tmp = path + ".tmp.npz"
    np.savez_compressed(tmp, **out)
    os.replace(tmp, path)
    return seed, "ok", time.time() - t0


# ---------------------------------------------------------------- agents

class Timed:
    """Equal weight across the roster at `lev_on` gross while the public
    signal is on and `lev_off` while it is off. Decides once a day, at the
    first step, from the published macro fields (what the close before
    published). Rebalances when the signal flips or the gross drifts more
    than 0.1 from target."""

    def __init__(self, signal, lev_on=1.8, lev_off=1.0):
        self.signal = signal; self.lev_on = lev_on; self.lev_off = lev_off
        self.prev_ff = None; self.cut_age = 10 ** 9; self.prev_on = None; self.days = 0; self.unemp = []

    def on(self, mf):
        ff = mf["federal_funds_rate"]
        if self.prev_ff is not None and ff < self.prev_ff - 1e-9:
            self.cut_age = 0
        else:
            self.cut_age += 1
        self.prev_ff = ff
        self.unemp.append(mf["unemployment_rate"])
        s = self.signal
        if s == "fedcut63":
            return self.cut_age < 63
        if s == "pubpeakcon":
            return mf["cycle"] in ("peak", "contraction")
        if s == "gdp1":
            return mf["gdp_growth"] < 0.01
        if s == "fg40":
            return mf["fear_greed_index"] < 40
        if s == "unemp126":
            return len(self.unemp) > 126 and self.unemp[-1] > self.unemp[-127]
        if s == "const":
            return True
        raise ValueError(s)

    def act(self, obs):
        if not obs.is_first_step_of_day:
            return {}
        from tradefloor.baselines import rebalance
        mf = obs.engine.macro_fields
        on = self.on(mf)
        target = self.lev_on if on else self.lev_off
        worth = obs.portfolio.net_worth(obs.engine)
        gross = sum(abs(obs.position(t) * obs.price(t)) for t in obs.tickers) / worth if worth > 0 else 0
        self.days += 1
        if on == self.prev_on and abs(gross - target) < 0.1:
            return {}
        self.prev_on = on
        w = target / len(obs.tickers)
        return rebalance(obs, {t: w for t in obs.tickers}, max_participation=0.05)


class Jump:
    """Price-only. At the start of each day's last step it notes the roster's
    total market cap; at the next day's first step it compares: the change is
    the last step plus the close's re-mark. After a rise above `th` it holds
    2x for `k_in` sessions; after a fall below -th it holds nothing for
    `k_out` sessions (`lever` False: 1x instead of 2x, a long-only rule)."""

    def __init__(self, th=0.03, k_out=63, k_in=126, lever=True):
        self.th = th; self.k_out = k_out; self.k_in = k_in; self.lever = lever
        self.last = None; self.out_left = 0; self.in_left = 0; self.prev_target = None

    def act(self, obs):
        import numpy as np
        caps = np.frombuffer(obs.engine.column("market_cap"), dtype="<f8")
        tot = float(caps.sum())
        if obs.step_of_day == obs.steps_per_day - 1:
            self.last = tot
            return {}
        if not obs.is_first_step_of_day:
            return {}
        self.out_left = max(0, self.out_left - 1); self.in_left = max(0, self.in_left - 1)
        if self.last:
            j = np.log(tot / self.last)
            if j < -self.th:
                self.out_left = self.k_out
            elif j > self.th and self.lever:
                self.in_left = self.k_in
        target = 0.0 if self.out_left else (1.8 if self.in_left else 1.0)
        worth = obs.portfolio.net_worth(obs.engine)
        gross = sum(abs(obs.position(t) * obs.price(t)) for t in obs.tickers) / worth if worth > 0 else 0
        if target == self.prev_target and abs(gross - target) < 0.1:
            return {}
        self.prev_target = target
        from tradefloor.baselines import rebalance
        w = target / len(obs.tickers)
        return rebalance(obs, {t: w for t in obs.tickers}, max_participation=0.05)


# the tf.evaluate agents that hold more than 1x at some point: every Timed signal (1.8x while on) and jumpboth
LEVERED = ("fedcut63", "pubpeakcon", "gdp1", "fg40", "unemp126", "jumpboth")


def gross(obs):
    """Gross position over net worth, the figure the Timed and Jump agents rebalance on."""
    worth = obs.portfolio.net_worth(obs.engine)
    return sum(abs(obs.position(t) * obs.price(t)) for t in obs.tickers) / worth if worth > 0 else 0


class Exposure:
    """Wraps an agent and records its gross exposure at the start of every step, before it trades: the
    position it carried into the step. mean() is the run's mean gross exposure. Attributes the harness looks
    for (explain, privileged) are the wrapped agent's."""

    def __init__(self, agent):
        self.agent = agent; self.total = 0.0; self.n = 0

    def __getattr__(self, name):
        if "agent" not in self.__dict__:
            raise AttributeError(name)
        return getattr(self.__dict__["agent"], name)

    def act(self, obs):
        self.total += gross(obs); self.n += 1
        return self.agent.act(obs)

    def mean(self):
        return self.total / self.n if self.n else float("nan")


def em_agent(level):
    """The exposure-matched position: equal weight across the roster at `level` gross throughout, rebalanced as
    the const agents are (when the gross drifts more than 0.1 from target)."""
    return Timed("const", lev_on=level, lev_off=level)


def make_agent(name):
    if name == "jumpboth":
        return Jump()
    if name == "jumpout":
        return Jump(lever=False)
    import tradefloor as tf
    if name == "bh":
        return tf.BuyAndHold() if hasattr(tf, "BuyAndHold") else __import__("tradefloor.baselines", fromlist=["BuyAndHold"]).BuyAndHold()
    if name == "bh18":
        from tradefloor.baselines import BuyAndHold
        return BuyAndHold(leverage=1.8)
    if name.startswith("const"):
        return Timed("const", lev_on=float(name[5:]) / 100, lev_off=1.0)
    return Timed(name)


def eval_one(seed, agent, days, cash_interest, outdir):
    import tradefloor as tf
    path = os.path.join(outdir, f"{seed}-{agent}-ci{int(cash_interest)}.json")
    if os.path.exists(path):
        return seed, agent, "exists"
    t0 = time.time()
    extra = {}
    if agent.startswith("em_"):
        # the exposure-matched position for agent em_X: X's own mean gross exposure on this seed, from its record
        src = os.path.join(outdir, f"{seed}-{agent[3:]}-ci{int(cash_interest)}.json")
        if not os.path.exists(src):
            return seed, agent, "no record for " + agent[3:]
        level = float(json.load(open(src))["mean_gross"])
        inner = em_agent(level)
        extra = dict(em_of=agent[3:], em_level=level)
    else:
        inner = make_agent(agent)
    wrapped = Exposure(inner)
    u = tf.Universe.random(40, seed=111)
    cards = tf.evaluate({agent: wrapped}, seed=seed, universe=u, days=days,
                        cash_interest=bool(cash_interest), model=_model())
    c = cards[agent]
    rec = dict(seed=seed, agent=agent, days=days, cash_interest=cash_interest, pnl=c.pnl,
               return_pct=c.return_pct, trades=c.trades, rejected=c.rejected, max_leverage=c.max_leverage,
               mean_gross=wrapped.mean(), gross_steps=wrapped.n,
               errors=c.errors[:5], n_errors=len(c.errors), model=c.model_fingerprint,
               elapsed=time.time() - t0, **extra)
    json.dump(rec, open(path + ".tmp", "w")); os.replace(path + ".tmp", path)
    return seed, agent, "ok"


def rng(s):
    a, b = s.split("-"); return range(int(a), int(b) + 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode")
    ap.add_argument("--seeds", default="2001-2002")
    ap.add_argument("--years", type=int, default=21)
    ap.add_argument("--days", type=int, default=5292)
    ap.add_argument("--gendays", type=int, default=0)
    ap.add_argument("--roster", default="vary")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--agents", default="bh,const112,fedcut63,pubpeakcon,gdp1")
    ap.add_argument("--cash-interest", type=int, default=1)
    ap.add_argument("--out", default="out")
    a = ap.parse_args()
    import tradefloor as tf
    print("tradefloor", tf.__version__, "default model", tf.ModelParams.from_preset().fingerprint if hasattr(tf.ModelParams.from_preset(), "fingerprint") else "", flush=True)
    if a.mode == "check":
        from tradefloor.harness import session_clock
        u = list(tf.Universe.random(40, seed=111))
        e1 = tf.Engine(seed=7, universe=u); e2 = tf.Engine(seed=7, universe=u)
        for d in range(15):
            e1.run_days(1, record=False, first_day=d)
            e2.open_market()
            for k in range(6):
                e2.run_session(*session_clock((9, 30, 3), k, 65), 65)
            e2.close_market()
        print("run_days vs stepped, max rel diff after 15 days:", float(np.max(np.abs(f64(e1.prices()) / f64(e2.prices()) - 1))),
              "hash equal:", e1.state_hash() == e2.state_hash())
        return
    if a.mode == "gen":
        od = os.path.join(a.out, "gen"); os.makedirs(od, exist_ok=True)
        with ProcessPoolExecutor(a.workers) as ex:
            futs = [ex.submit(gen_one, s, a.years, a.roster, od, a.gendays) for s in rng(a.seeds)]
            for f in as_completed(futs):
                print("gen", *f.result(), flush=True)
    elif a.mode in ("eval", "evalem"):
        od = os.path.join(a.out, "eval"); os.makedirs(od, exist_ok=True)
        agents = a.agents.split(",")
        if a.mode == "evalem":
            agents = ["em_" + x for x in agents if x in LEVERED]
        with ProcessPoolExecutor(a.workers) as ex:
            futs = [ex.submit(eval_one, s, ag, a.days, a.cash_interest, od) for s in rng(a.seeds) for ag in agents]
            for f in as_completed(futs):
                try:
                    print("eval", *f.result(), flush=True)
                except Exception as exc:  # noqa: BLE001
                    print("eval FAILED", repr(exc), flush=True)


if __name__ == "__main__":
    main()
