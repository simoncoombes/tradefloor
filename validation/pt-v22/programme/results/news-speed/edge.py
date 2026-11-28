"""What a headline is worth to a bot that reads it, per arm.

The test the headlines work used (docs/serve/HEADLINES.md on feat/headlines),
rebuilt so it can be rerun on any engine and arm. Per arm and seed, on the
certified roster `Universe.random(40, seed=111)`, day by day:

    open_market(); news = session_news(); run_session(9, 30, 3, 390); close_market()

For every endogenous news event (company i, day d, impact x):

- captured(k): buy good news and sell bad at the price after k ticks, hold to
  the close. sign(x) * [log(P_close / P_k) - the same for the roster's other
  names, equal-weighted]. Gross, before costs. Reported for k = 1, 5, 30
  (the headline is released at tick 1), and 60, 120, 210, 360.
- share(t): how much of x is in the price after t ticks, the slope of the
  market-adjusted log(P_t / P_open) on x through the origin, for t = 1, 5,
  15, 30, 60, 210, 390 (close), and the next day's close-to-close.

Run with the engine's own python:

    ENGINE/.venv/bin/python edge.py --arm NAME[@BASE]:dial=v,... [--arm ...] [--arms-file F]
        --seeds 8 --days 250 --workers 3 --out FILE.json

`NAME@BASE` puts one arm on another preset than --base (a preset measured by
name: `pt-v20@pt-v20:`). From Python, `measure(["NAME:dials", ...], ...)` returns
the same report dict the script writes; `programme/longrun/criteria.py` calls it
for criterion C3 (captured at k = 5 under 20 bp).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from concurrent.futures import ProcessPoolExecutor

import numpy as np

TRADE_TICKS = (1, 5, 30, 60, 120, 210, 360)
SHARE_TICKS = (1, 5, 15, 30, 60, 210, 390)


def parse_arm(text: str, default_base: str = "pt-v19") -> tuple[str, str, dict[str, float]]:
    """`NAME[@BASE]:dial=v,...` -> (name, base, dials)."""
    head, _, rest = text.partition(":")
    name, _, base = head.strip().partition("@")
    dials = {}
    for part in filter(None, rest.split(",")):
        k, _, v = part.partition("=")
        dials[k.strip()] = float(v)
    return name.strip(), (base.strip() or default_base), dials


def run_seed(args: tuple[str, dict[str, float], str, int, int]) -> dict:
    name, dials, base, seed, days = args
    import tradefloor as tf

    model = tf.ModelParams.from_preset(base, **dials) if dials else base
    e = tf.Engine(seed=seed, universe=tf.Universe.random(40, seed=111), model=model)
    for k, v in dials.items():
        got = e.model_params[k]
        assert got == v, (k, got, v)
    tickers = list(e.tickers)
    idx = {t: i for i, t in enumerate(tickers)}
    n = len(tickers)
    rows = []          # per event: impact, adjusted log moves
    rows_next = []     # per event: impact, the next day's adjusted return
    prev = None        # (events, close) of the previous day, for next-day drift
    for _ in range(days):
        e.open_market()
        news = [(idx[ev["ticker"]], ev["price_impact"]) for ev in e.session_news()
                if ev["ticker"] is not None and ev["price_impact"]]
        p_open = np.frombuffer(e.prices(), "<f8").copy()
        e.run_session(9, 30, 3, 390)
        path = np.frombuffer(e.session_prices(), "<f8").reshape(390, n).copy()
        e.close_market()
        close = path[-1]
        lp = np.log(np.vstack([p_open, path]))          # row t = after t ticks
        if prev is not None:
            ev_prev, close_prev = prev
            day_ret = np.log(close / close_prev)
            for i, x in ev_prev:
                others = np.delete(day_ret, i).mean()
                rows_next.append((x, day_ret[i] - others))
        for i, x in news:
            others = np.ones(n, bool)
            others[i] = False
            # to-close from tick k, market-adjusted
            to_close = {k: (lp[390, i] - lp[k, i]) - (lp[390, others] - lp[k, others]).mean()
                        for k in TRADE_TICKS}
            from_open = {t: (lp[t, i] - lp[0, i]) - (lp[t, others] - lp[0, others]).mean()
                         for t in SHARE_TICKS}
            rows.append({"x": x, "to_close": to_close, "from_open": from_open})
        prev = (news, close)
    return {"arm": name, "seed": seed, "rows": rows, "next": rows_next}


def _worker(args):
    return run_seed(args)


def summarise(results: list[dict]) -> dict:
    rows = [r for res in results for r in res["rows"]]
    nxt = [r for res in results for r in res["next"]]
    x = np.array([r["x"] for r in rows])
    s = np.sign(x)
    out = {"events": len(rows), "mean_abs_impact_bp": float(np.abs(x).mean() * 1e4)}
    cap = {}
    for k in TRADE_TICKS:
        c = s * np.array([r["to_close"][k] for r in rows]) * 1e4
        cap[k] = {"mean_bp": float(c.mean()), "se_bp": float(c.std(ddof=1) / math.sqrt(len(c))),
                  "hit": float((c > 0).mean())}
    out["captured"] = cap
    share = {}
    for t in SHARE_TICKS:
        y = np.array([r["from_open"][t] for r in rows])
        b = float((x * y).sum() / (x * x).sum())
        resid = y - b * x
        se = float(math.sqrt((resid ** 2).sum() / (len(y) - 1) / (x * x).sum()))
        share[t] = {"share": b, "se": se}
    if nxt:
        xn = np.array([a for a, _ in nxt])
        yn = np.array([b for _, b in nxt])
        b = float((xn * yn).sum() / (xn * xn).sum())
        resid = yn - b * xn
        se = float(math.sqrt((resid ** 2).sum() / (len(yn) - 1) / (xn * xn).sum()))
        share["next_day"] = {"share": b, "se": se}
    out["share"] = share
    return out


def measure(texts: list[str], base: str = "pt-v19", seeds: int = 8, first_seed: int = 101,
            days: int = 250, workers: int = 3) -> dict:
    """The edge report for arms given as `NAME[@BASE]:dial=v,...` strings."""
    arms = [parse_arm(t, base) for t in texts]
    jobs = [(name, dials, abase, seed, days)
            for name, abase, dials in arms for seed in range(first_seed, first_seed + seeds)]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(_worker, jobs))
    import tradefloor as tf
    report = {"base": base, "seeds": seeds, "first_seed": first_seed, "days": days,
              "tradefloor_version": tf.version(), "arms": {}}
    for name, abase, dials in arms:
        mine = [r for r in results if r["arm"] == name]
        report["arms"][name] = {"dials": dials, **({"base": abase} if abase != base else {}),
                                "fingerprint": tf.ModelParams.from_preset(abase, **dials).fingerprint,
                                **summarise(mine)}
    return report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", action="append", default=[])
    ap.add_argument("--arms-file", help="one NAME:dial=value,... per line; # comments")
    ap.add_argument("--base", default="pt-v19")
    ap.add_argument("--seeds", type=int, default=8)
    ap.add_argument("--first-seed", type=int, default=101)
    ap.add_argument("--days", type=int, default=250)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    texts = list(a.arm)
    if a.arms_file:
        with open(a.arms_file) as f:
            texts += [line.strip() for line in f if line.strip() and not line.startswith("#")]
    report = measure(texts, a.base, a.seeds, a.first_seed, a.days, a.workers)
    with open(a.out, "w") as f:
        json.dump(report, f, indent=1)
    for name, body in report["arms"].items():
        cap = body["captured"]
        sh = body["share"]
        print(f"{name}: {body['events']} events, mean |impact| {body['mean_abs_impact_bp']:.0f}bp")
        print("  captured, bp (hit):  " + "  ".join(
            f"t{k} {cap[k]['mean_bp']:+.1f}±{cap[k]['se_bp']:.1f} ({cap[k]['hit']:.0%})"
            for k in TRADE_TICKS))
        print("  share in price:      " + "  ".join(
            f"t{t} {sh[t]['share']:.3f}" for t in SHARE_TICKS)
            + (f"  next-day {sh['next_day']['share']:+.3f}±{sh['next_day']['se']:.3f}"
               if "next_day" in sh else ""))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
