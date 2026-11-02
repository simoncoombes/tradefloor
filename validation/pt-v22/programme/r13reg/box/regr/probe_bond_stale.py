"""Probe 4b: rate indices quote yesterday's level after the close.

After close_market the curve (engine.curve) already holds the next day's
yields, but UST10Y's book still quotes the old level until open_market.
Portfolio.execute accepts an order in between. The rule: after each close,
buy 5,000 units of UST10Y if the 10-year yield fell, sell if it rose; unwind
at the next open. Prints the P&L and the share of winning days.
"""
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

ub = tf.Universe.random(10, seed=1, bonds=True)
e = _armE(seed=17, universe=ub); e.run_days(3)
pf = tf.Portfolio(cash=1e6)
wins = []
for d in range(120):
    r = [x for x in e.rate_instruments if x["ticker"] == "UST10Y"][0]
    dy = e.curve["treasury_10y"] - r["yield"]
    q = 5000.0 * (1 if dy < 0 else -1)
    f = pf.execute(e, "UST10Y", q)
    e.open_market()
    f2 = pf.execute(e, "UST10Y", -q)
    wins.append((f2["price"] - f["price"]) * q)
    e.run_session(9, 30, 3, 390, fills=pf.pending_flow()); pf.clear_flow(); e.close_market()
w = np.array(wins)
print(f"after-close UST10Y trades: {len(w)}, P&L ${w.sum():,.0f} on ~$500k notional, winning {np.sum(w>0)}/{len(w)}")
