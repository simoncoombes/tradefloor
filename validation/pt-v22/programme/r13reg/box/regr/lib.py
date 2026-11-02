"""Shared helpers for the pt-v20 audit probes.

gen() runs one untraded market day by day and returns what a trader could
see (closes, 65-minute bars, volume, the macro fields at each open, the
fundamentals, the half-spread) plus, for diagnostics only, the hidden
mispricing. Cached to cache/<key>.npz so every probe reads the same markets.
"""
from __future__ import annotations

import hashlib
import json
import os
import struct
import sys
from multiprocessing import Pool

import numpy as np
import pyarrow as pa
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

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "cache")
os.makedirs(CACHE, exist_ok=True)

CYCLES = ["expansion", "peak", "contraction", "trough", "recovery"]
MACRO_KEYS = ["vix", "federal_funds_rate", "corporate_bond_yield", "inflation_rate",
              "gdp_growth", "unemployment_rate", "oil_price", "treasury_yield_2y",
              "treasury_yield_10y", "fear_greed_index"]


def f64(b):
    return np.frombuffer(b, dtype="<f8").copy()


def _key(**kw):
    kw["commit"] = os.environ.get("AUDIT_COMMIT", tf.__version__)
    return hashlib.sha1(json.dumps(kw, sort_keys=True).encode()).hexdigest()[:16]


def gen(seed, useed=111, n=40, days=252, model=None, bars=True, cache=True):
    key = _key(seed=seed, useed=useed, n=n, days=days, model=model, bars=bars)
    path = os.path.join(CACHE, key + ".npz")
    if cache and os.path.exists(path):
        z = np.load(path, allow_pickle=True)
        return {k: z[k] for k in z.files}
    u = tf.Universe.random(n, seed=useed)
    e = _armE(seed=seed, universe=u, model=model)
    N = len(e.tickers)
    close = np.zeros((days, N)); opn = np.zeros((days, N)); vol = np.zeros((days, N))
    s = np.zeros((days, N)); hspread = np.zeros((days, N))
    b65 = np.zeros((days, 6, N)) if bars else np.zeros((0,))
    macro = {k: np.zeros(days) for k in MACRO_KEYS}
    cyc = np.zeros(days, dtype=int)
    cyc_true = np.zeros(days, dtype=int)
    gdp_true = np.zeros(days); ecyc = np.zeros(days)
    crisis = np.zeros(days, dtype=bool)
    eps0, bv0, g0 = [np.array(x) for x in e.fundamentals()]
    for d in range(days):
        mf = e.macro_fields
        for k in MACRO_KEYS:
            macro[k][d] = mf[k]
        cyc[d] = CYCLES.index(mf["cycle"])
        ec = e.state_snapshot()["economy"]
        cyc_true[d] = CYCLES.index(ec["cycle_phase"])
        gdp_true[d] = ec["gdp_growth"] / 100.0
        ecyc[d] = ec.get("earnings_cycle", 0.0)
        opn[d] = f64(e.prices())
        if d % 5 == 0:
            for i, t in enumerate(e.tickers):
                bk = e.book(t)
                if bk.spread is not None and bk.mid_price:
                    hspread[d, i] = 0.5 * bk.spread / bk.mid_price
        else:
            hspread[d] = hspread[d - 1]
        e.run_days(1, record=True)
        close[d] = f64(e.prices())
        s[d] = f64(e.column("mispricing_s"))
        crisis[d] = e.crisis_episode[0]
        if bars:
            t = pa.table(e.bars(day=e.recorded_days - 1 if False else None, minutes=65)).to_pandas()
            t = t[t.day == t.day.max()]
            arr = t.pivot(index="bar", columns="instrument_id", values="close").values
            b65[d, :arr.shape[0]] = arr
            vol[d] = t.groupby("instrument_id").volume.sum().values
        e.clear_recording()
    out = dict(close=close, open=opn, vol=vol, s=s, hspread=hspread, b65=b65,
               cyc=cyc, cyc_true=cyc_true, gdp_true=gdp_true, ecyc=ecyc, crisis=crisis, eps=eps0, bv=bv0, growth=g0,
               sector=np.array([ins.sector for ins in u]),
               beta=np.array([ins.beta for ins in u]),
               adv=np.array([ins.avg_volume for ins in u]),
               mcap=np.array([ins.market_cap for ins in u]),
               price0=np.array([ins.initial_price for ins in u]),
               tickers=np.array(e.tickers), hash=np.array(e.state_hash()))
    for k, v in macro.items():
        out["m_" + k] = v
    if cache:
        np.savez(path, **out)
    return out


def _gen_star(a):
    return gen(**a)


def gen_many(argsets, procs=int(os.environ.get("AUDIT_PROCS", "4"))):
    with Pool(procs) as p:
        return p.map(_gen_star, argsets)


# ---------------------------------------------------------------- backtest

def backtest(close, weights, cost=None, rebalance=1):
    """Daily close-to-close backtest.

    weights[d] is the target set at the close of day d (known then) and held
    over d -> d+1. cost is a per-name one-way cost fraction (half-spread),
    array (N,) or (D,N). Returns daily net returns (D-1,).
    """
    D, N = close.shape
    r = close[1:] / close[:-1] - 1.0
    w = np.array(weights, dtype=float)
    held = np.zeros_like(w)
    cur = np.zeros(N)
    for d in range(D):
        if d % rebalance == 0:
            cur = w[d]
        held[d] = cur
    gross = (held[:-1] * r).sum(1)
    if cost is None:
        return gross
    c = cost if np.ndim(cost) == 2 else np.broadcast_to(cost, (D, N))
    turn = np.abs(np.diff(np.vstack([np.zeros(N), held]), axis=0))  # D x N
    tc = (turn * c).sum(1)
    return gross - tc[:-1] - np.r_[0, 0 * tc[:-2]][:D - 1] * 0


def ann(x, per=252):
    x = np.asarray(x)
    return x.mean() * per, x.std() * np.sqrt(per), (x.mean() / x.std() * np.sqrt(per)) if x.std() > 0 else np.nan


if __name__ == "__main__":
    import time
    t = time.time()
    m = gen(1, days=20, cache=False)
    print({k: (v.shape if hasattr(v, "shape") else v) for k, v in m.items()})
    print(time.time() - t)
