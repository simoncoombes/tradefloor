"""Probe 2c: profit from one's own footprint, on the shared engine book.

  roundtrip   buy q (fraction of ADV) at a step, sell it all one step later
  pump_mark   buy q in the last step of the run and hold to the final mark
  ladder      split a buy into n small orders over one step, sell in one block
  wash        agent A rests an ask 3% over the touch; agent B buys through to
              it; does the last print (the mark) move, and does A+B gain on
              a position A already held?
  stream      one-share buys every tick for a day on a held position

Each is measured against an untraded twin of the same seed, so the P&L is the
footprint's alone. Usage: python probe_impact.py [seeds]
"""
import sys

import numpy as np
import tradefloor as tf

# r14 screen: AUDIT_DIALS (k=v,...) runs the probe on pt-v20 with those dials, not the default preset
import os as _aos
_AD = {k: float(v) for k, v in (x.split("=") for x in _aos.environ.get("AUDIT_DIALS", "").split(",") if x)}
_ARM = tf.ModelParams.from_preset("pt-v20", **_AD)
def _armE(*a, **k):
    if k.get("model") is None:
        k["model"] = _ARM
    return tf.Engine(*a, **k)
def _armEval(*a, **k):
    if k.get("model") is None:
        k["model"] = _ARM
    return tf.evaluate(*a, **k)

from lib import f64


def mk(seed, useed=111, n=40):
    u = tf.Universe.random(n, seed=useed)
    return u, _armE(seed=seed, universe=u)


def fill(e, agent, t, q, limit=None):
    r = e.submit(agent, t, q, limit_price=limit)
    cash = 0.0; got = 0.0
    for f in r["fills"]:
        sgn = 1 if r["side"] == "buy" else -1
        cash -= sgn * f["quantity"] * f["price"]; got += sgn * f["quantity"]
    return cash, got


def roundtrip(seed, i, q_adv, gap_ticks=65, warm=3):
    u, e = mk(seed)
    e.run_days(warm)
    e.open_market()
    e.run_session(9, 30, 3, 100)
    t = e.tickers[i]
    q = max(1.0, round(q_adv * u[i].avg_volume))
    c1, g1 = fill(e, "a", t, q)
    e.run_session(11, 10, 3, gap_ticks)
    c2, g2 = fill(e, "a", t, -g1)
    mid0 = e.book(t).mid_price
    return (c1 + c2) / (abs(c1)) * 1e4  # bp of notional


def pump_mark(seed, i, q_adv, warm=3):
    """Buy at the start of the day's last 65 ticks; P&L at the close mark vs cost."""
    u, e = mk(seed); _, b = mk(seed)
    e.run_days(warm); b.run_days(warm)
    for x in (e, b):
        x.open_market(); x.run_session(9, 30, 3, 325)
    t = e.tickers[i]
    q = max(1.0, round(q_adv * u[i].avg_volume))
    c, g = fill(e, "a", t, q)
    for x in (e, b):
        x.run_session(14, 55, 3, 65); x.close_market()
    mark = f64(e.prices())[i]
    base = f64(b.prices())[i]
    pnl = c + g * mark
    return pnl / abs(c) * 1e4, (mark / base - 1) * 1e4


def stream(seed, i, warm=3, hold_adv=0.05):
    """Hold a position bought passively; then one-share buys every tick all day."""
    u, e = mk(seed); _, b = mk(seed)
    e.run_days(warm); b.run_days(warm)
    for x in (e, b):
        x.open_market()
    t = e.tickers[i]
    cost = 0.0
    for k in range(390):
        hh, mm = divmod(9 * 60 + 30 + k, 60)
        c, g = fill(e, "a", t, 1.0)
        cost += c
        e.run_session(hh, mm, 3, 1); b.run_session(hh, mm, 3, 1)
    e.close_market(); b.close_market()
    held = hold_adv * u[i].avg_volume
    mark, base = f64(e.prices())[i], f64(b.prices())[i]
    gain = held * (mark - base)
    return gain + cost + 390 * mark, (mark / base - 1) * 1e4


def wash(seed, i, warm=3, above=0.03, q_adv=0.001):
    u, e = mk(seed); _, b = mk(seed)
    e.run_days(warm); b.run_days(warm)
    for x in (e, b):
        x.open_market(); x.run_session(9, 30, 3, 100)
    t = e.tickers[i]
    bk = e.book(t)
    px = round(bk.best_ask * (1 + above), 2)
    q = max(1.0, round(q_adv * u[i].avg_volume))
    ra = e.submit("A", t, -q, limit_price=px)
    before = f64(e.prices())[i]
    cb, gb = fill(e, "B", t, q, limit=px)
    after = f64(e.prices())[i]
    fa = e.take_fills("A")
    ca = sum(f["quantity"] * f["price"] for f in fa)
    e.run_session(11, 10, 3, 1); b.run_session(11, 10, 3, 1)
    nexttick = f64(e.prices())[i] / f64(b.prices())[i] - 1
    return dict(print_before=before, print_after=after, b_filled=gb, a_filled=sum(f["quantity"] for f in fa),
                b_cost_bp=(-cb / max(gb, 1) / before - 1) * 1e4 if gb else None,
                next_tick_vs_twin_bp=nexttick * 1e4)


if __name__ == "__main__":
    seeds = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    p0 = int(_aos.environ.get("AUDIT_SEED_PROBE", "2401"))   # thirteenth registration's seed variable
    u = tf.Universe.random(40, seed=111)
    adv = np.array([x.avg_volume for x in u])
    thin = list(np.argsort(adv)[:4]); thick = list(np.argsort(adv)[-3:])
    print("thin", [u[i].ticker for i in thin], "thick", [u[i].ticker for i in thick])
    for q in (0.001, 0.01, 0.05, 0.2):
        rt = [roundtrip(s, i, q) for s in range(p0, p0 + seeds) for i in thin + thick]
        pm = [pump_mark(s, i, q) for s in range(p0, p0 + seeds) for i in thin + thick]
        print(f"q={q:.3f} ADV  roundtrip bp: mean {np.mean(rt):+.1f} max {np.max(rt):+.1f} positive {np.sum(np.array(rt) > 0)}/{len(rt)}"
              f" | buy-into-close pnl bp: mean {np.mean([p[0] for p in pm]):+.1f} max {np.max([p[0] for p in pm]):+.1f}"
              f" mark lift bp mean {np.mean([p[1] for p in pm]):+.1f}", flush=True)
    st = [stream(s, i) for s in range(p0, p0 + seeds) for i in thin]
    print("one-share stream on a 5%-ADV holding: net $ mean", np.mean([x[0] for x in st]), "mark lift bp", np.mean([x[1] for x in st]))
    for s in range(p0, p0 + 2):
        for i in thin[:2] + thick[:1]:
            print("wash", u[i].ticker, wash(s, i))
