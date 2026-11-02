"""Probe 2b: passive market making, decomposed.

Quotes one cent inside the maker on wide-spread names, a fixed small size,
re-quoting every `step` ticks, with a hard inventory cap enforced by the
size of what is posted. Reports per name: shares bought and sold, the
average sell minus average buy (the spread captured on matched volume),
the P&L on matched volume, and the mark-to-market of the leftover.

Also runs an untraded twin of the same seed so the agent's footprint on the
close can be measured.

Usage: python probe_mm2.py seed days step [model]
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


def run(seed, days=5, step=5, model=None, useed=111, agent="alice", q_adv=0.0005,
        cap_adv=0.01, min_spread=0.035, max_adv=None):
    u = tf.Universe.random(40, seed=useed)
    e = _armE(seed=seed, universe=u, model=model)
    base = _armE(seed=seed, universe=u, model=model)
    tick = {t: i for i, t in enumerate(e.tickers)}
    adv = np.array([x.avg_volume for x in u])
    N = len(u)
    bq = np.zeros(N); bv = np.zeros(N); sq = np.zeros(N); sv = np.zeros(N)
    inv = np.zeros(N)
    quoted = np.zeros(N, dtype=bool)
    for d in range(days):
        e.open_market(); base.open_market()
        for k in range(0, 390, step):
            for o in e.open_orders(agent):
                e.cancel(o["order_id"], agent=agent)
            for i, t in enumerate(e.tickers[:N]):
                bk = e.book(t)
                if bk.best_bid is None or bk.best_ask is None:
                    continue
                if bk.best_ask - bk.best_bid < min_spread:
                    continue
                if max_adv is not None and adv[i] >= max_adv:
                    continue
                quoted[i] = True
                q = max(1.0, round(q_adv * adv[i]))
                cap = cap_adv * adv[i]
                qb = min(q, max(0.0, cap - inv[i]))
                qs = min(q, max(0.0, cap + inv[i]))
                if qb >= 1:
                    e.submit(agent, t, qb, limit_price=round(bk.best_bid + 0.01, 2))
                if qs >= 1:
                    e.submit(agent, t, -qs, limit_price=round(bk.best_ask - 0.01, 2))
            hh, mm = divmod(9 * 60 + 30 + k, 60)
            e.run_session(hh, mm, 3, step)
            base.run_session(hh, mm, 3, step)
            for f in e.take_fills(agent):
                i = tick[f["ticker"]]
                if f["side"] == "buy":
                    bq[i] += f["quantity"]; bv[i] += f["quantity"] * f["price"]; inv[i] += f["quantity"]
                else:
                    sq[i] += f["quantity"]; sv[i] += f["quantity"] * f["price"]; inv[i] -= f["quantity"]
        for o in e.open_orders(agent):
            e.cancel(o["order_id"], agent=agent)
        e.close_market(); base.close_market()
    px = f64(e.prices())[:N]
    px0 = f64(base.prices())[:N]
    rows = []
    for i in np.where(quoted)[0]:
        m = min(bq[i], sq[i])
        if bq[i] == 0 or sq[i] == 0:
            cap_spread = np.nan; matched = 0.0
        else:
            ab, as_ = bv[i] / bq[i], sv[i] / sq[i]
            cap_spread = (as_ - ab) / px[i] * 1e4
            matched = m * (as_ - ab)
        total = sv[i] - bv[i] + inv[i] * px[i]
        rows.append(dict(name=e.tickers[i], buys=bq[i], sells=sq[i], adv=adv[i],
                         captured_bp=cap_spread, matched_pnl=matched, total_pnl=total,
                         inventory_value=inv[i] * px[i],
                         close_vs_untraded_bp=(px[i] / px0[i] - 1) * 1e4))
    return rows


if __name__ == "__main__":
    seed = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    days = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    step = int(sys.argv[3]) if len(sys.argv) > 3 else 5
    model = sys.argv[4] if len(sys.argv) > 4 else None
    rows = run(seed, days, step, model)
    tot = 0; totm = 0
    for r in rows:
        tot += r["total_pnl"]; totm += r["matched_pnl"]
        print(f"{r['name']} buys {r['buys']:,.0f} sells {r['sells']:,.0f} (adv {r['adv']:,.0f}) "
              f"captured {r['captured_bp']:.1f}bp matched {r['matched_pnl']:,.0f} total {r['total_pnl']:,.0f} "
              f"inv {r['inventory_value']:,.0f} close_vs_untraded {r['close_vs_untraded_bp']:.1f}bp")
    print(f"TOTAL pnl {tot:,.0f} matched {totm:,.0f}")
