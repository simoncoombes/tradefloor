"""R7b, the audit's rate-news agent through tf.evaluate (twelfth registration).

At the first step of each day the agent reads the policy rate (already moved
by the previous close's meeting) and compares it with the one it read the day
before. Hike: sell the roster at the open, buy it back at the next step. Cut:
double up at the open (1.9x), back to 0.95x at the next step. Otherwise hold
0.95 of net worth equal-weighted. Compared with holding (0.95 equal-weighted,
bought at the first step) through tf.evaluate: six steps a day, the engine's
own book and costs, max leverage 2. The roster is the audit's,
Universe.random(40, seed=<seed>). The agents are the audit's
probe_ratenews_eval.RateNews and probe_cycle_eval.Hold, copied unchanged
(desk/ratenews/; box ratenews1 in box-ratenews1/ ran ratenews_eval.py on seeds
601-630, and this script gives the same numbers on the same seeds).

  R7b  the mean over histories of the annual excess over holding, at most 0;
       histories where the agent beats holding, at most 20 of 30.

Graded on 30 histories of 10 years (seeds 101-130, the grade's). Each arm is
read as the long run reads it, NAME[@BASE][:dial=v,...].

    python r7_eval.py OUT.json --arm pt-v19: --arm pt-v20@pt-v20: \\
        --seeds 101-130 --years 10 --workers 60
"""
import argparse
import json
import statistics as st
from concurrent.futures import ProcessPoolExecutor

import numpy as np

MAX_EXCESS = 0.0
MAX_AHEAD_OF_30 = 20


class Hold:
    def __init__(self, gross=0.95):
        self.gross = gross

    def act(self, obs):
        if obs.step != 0:
            return {}
        nw = obs.portfolio.net_worth(obs.engine)
        n = len(obs.tickers)
        return {t: float(np.round(self.gross * nw / n / obs.prices[i])) for i, t in enumerate(obs.tickers)}


class RateNews:
    def __init__(self):
        self.last = None; self.events = 0

    def target(self, obs, gross):
        nw = obs.portfolio.net_worth(obs.engine); n = len(obs.tickers)
        out = {}
        for i, t in enumerate(obs.tickers):
            d = gross * nw / n / obs.prices[i] - obs.position(t)
            if abs(d) * obs.prices[i] > 0.02 * nw / n:
                out[t] = float(np.round(d))
        return out

    def act(self, obs):
        k = obs.step_of_day
        if k == 0:
            fed = obs.engine.macro_fields["federal_funds_rate"]
            ch = 0.0 if self.last is None else fed - self.last
            self.last = fed
            if ch > 1e-9:
                self.events += 1; return self.target(obs, 0.0)
            if ch < -1e-9:
                self.events += 1; return self.target(obs, 1.9)
            return self.target(obs, 0.95) if obs.step == 0 else {}
        if k == 1:
            g = obs.portfolio.gross_exposure(obs.engine) / obs.portfolio.net_worth(obs.engine)
            if abs(g - 0.95) > 0.1:
                return self.target(obs, 0.95)
        return {}


def one(spec):
    base, dials, seed, years = spec
    import tradefloor as tf
    m = tf.ModelParams.from_preset(base, **dials) if dials else tf.ModelParams.from_preset(base)
    u = tf.Universe.random(40, seed=seed)
    agent = RateNews()
    sc = tf.evaluate({"hold": Hold(), "ratenews": agent}, seed=seed, universe=u,
                     days=252 * years, max_leverage=2.0, model=m)
    h, c = sc["hold"].return_pct, sc["ratenews"].return_pct
    ex = ((1 + c / 100) ** (1 / years) - (1 + h / 100) ** (1 / years)) * 100
    return seed, {"hold_pct": h, "ratenews_pct": c, "excess_pts_yr": ex,
                  "trades": sc["ratenews"].trades, "rejected": sc["ratenews"].rejected}


def seeds_of(text):
    out = []
    for part in text.split(","):
        lo, _, hi = part.partition("-")
        out += list(range(int(lo), int(hi or lo) + 1))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out"); ap.add_argument("--arm", action="append", required=True)
    ap.add_argument("--seeds", default="101-130"); ap.add_argument("--years", type=int, default=10)
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    import tradefloor as tf
    seeds = seeds_of(a.seeds)
    out = {"kind": "ptv20-r7-eval", "seeds": a.seeds, "years": a.years, "roster": "Universe.random(40, seed=<seed>)",
           "tradefloor_version": tf.version(), "steps_per_day": 6, "max_leverage": 2.0, "arms": {}}
    specs = []
    for arm in a.arm:
        head, _, body = arm.partition(":")
        name, _, base = head.partition("@")
        base = base or name
        dials = {k: float(v) for k, v in (x.split("=") for x in body.split(",") if x)}
        specs.append((name, base, dials))
    with ProcessPoolExecutor(a.workers) as ex:
        futs = {(name, s): ex.submit(one, (base, dials, s, a.years)) for name, base, dials in specs for s in seeds}
        for name, base, dials in specs:
            rows = dict(futs[(name, s)].result() for s in seeds)
            xs = [r["excess_pts_yr"] for r in rows.values()]
            n = len(xs)
            mean = st.fmean(xs)
            se = st.stdev(xs) / n ** 0.5 if n > 1 else float("nan")
            ahead = sum(x > 0 for x in xs)
            cap = MAX_AHEAD_OF_30 * n / 30.0
            ok = mean <= MAX_EXCESS + 1e-12 and ahead <= cap + 1e-12
            out["arms"][name] = {"base": base, "dials": dials,
                                 "fingerprint": tf.ModelParams.from_preset(base, **dials).fingerprint,
                                 "n": n, "mean_excess_pts_yr": mean, "se": se, "median_excess_pts_yr": st.median(xs),
                                 "ahead": ahead, "ahead_cap": cap, "pass": ok,
                                 "per_seed": {str(k): v for k, v in rows.items()}}
            print(f"{name:12s} rate-news agent: mean excess {mean:+.2f} pts/yr (se {se:.2f}), ahead {ahead}/{n} "
                  f"-> R7b {'pass' if ok else 'FAIL'}", flush=True)
    json.dump(out, open(a.out, "w"), indent=1, default=str)


if __name__ == "__main__":
    main()
