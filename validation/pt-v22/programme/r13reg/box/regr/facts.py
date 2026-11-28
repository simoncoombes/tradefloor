"""Probe 3: stylised facts on the cached markets, for comparison with US equities.

Usage: python facts.py short|var|long
"""
import sys

import numpy as np

from strategies import load, index_series


def acf(x, k):
    x = x - x.mean()
    return (x[:-k] * x[k:]).sum() / (x * x).sum()


def kurt(x):
    x = x - x.mean()
    return (x ** 4).mean() / (x ** 2).mean() ** 2 - 3


def skew(x):
    x = x - x.mean()
    return (x ** 3).mean() / (x ** 2).mean() ** 1.5


def avg_corr(R):
    C = np.corrcoef(R.T)
    n = C.shape[0]
    return (C.sum() - n) / (n * (n - 1))


def drawdown_events(ix, th):
    """Count peak-to-trough declines of at least th, each ended by a new high."""
    peak = ix[0]; trough = ix[0]; n = 0; inside = False
    for p in ix:
        if p > peak:
            if inside:
                inside = False
            peak = p; trough = p
        else:
            if not inside and p / peak - 1 <= -th:
                n += 1; inside = True
    return n


def stock_facts(m):
    c = m["close"]
    r = np.log(c[1:] / c[:-1])
    out = {}
    out["stock_vol_ann"] = np.median(r.std(0)) * np.sqrt(252)
    out["stock_kurt"] = np.median([kurt(r[:, i]) for i in range(r.shape[1])])
    out["stock_acf1"] = np.median([acf(r[:, i], 1) for i in range(r.shape[1])])
    for k in (1, 5, 20, 50, 100):
        if r.shape[0] > 3 * k:
            out[f"stock_absacf{k}"] = np.median([acf(np.abs(r[:, i]), k) for i in range(r.shape[1])])
    out["stock_leverage"] = np.median([np.corrcoef(r[:-1, i], np.abs(r[1:, i]))[0, 1] for i in range(r.shape[1])])
    out["avg_corr"] = avg_corr(r)
    v = m["m_vix"][1:]
    hi = v > 30; lo = v < 20
    if hi.sum() > 30:
        out["avg_corr_vix_gt30"] = avg_corr(r[hi])
    if lo.sum() > 30:
        out["avg_corr_vix_lt20"] = avg_corr(r[lo])
    vol = m["vol"][1:]
    out["vol_absret_corr"] = np.median([np.corrcoef(np.log(vol[:, i] + 1), np.abs(r[:, i]))[0, 1] for i in range(r.shape[1])])
    ix = index_series(m)
    ri = np.log(ix[1:] / ix[:-1])
    out["index_vol_ann"] = ri.std() * np.sqrt(252)
    out["index_kurt"] = kurt(ri)
    out["index_skew"] = skew(ri)
    out["index_acf1"] = acf(ri, 1)
    out["index_absacf1"] = acf(np.abs(ri), 1)
    out["index_absacf20"] = acf(np.abs(ri), 20)
    if len(ri) > 500:
        out["index_absacf100"] = acf(np.abs(ri), 100)
    out["index_leverage"] = np.corrcoef(ri[:-1], np.abs(ri[1:]))[0, 1]
    # weekly and monthly autocorrelation of the index
    for h, name in ((5, "w"), (21, "m")):
        n = len(ri) // h
        agg = ri[: n * h].reshape(n, h).sum(1)
        if n > 20:
            out[f"index_acf1_{name}"] = acf(agg, 1)
    out["index_ret_ann"] = ix[-1] / ix[0]
    out["pct_down5"] = np.mean(ri < np.log(0.95)) * 100
    out["pct_dn3"] = np.mean(ri < np.log(0.97)) * 100
    out["vix_mean"] = m["m_vix"].mean()
    out["vix_gt30"] = np.mean(m["m_vix"] > 30)
    out["vix_lt15"] = np.mean(m["m_vix"] < 15)
    out["cash_rate"] = m["m_federal_funds_rate"].mean()
    # annual cross-sectional dispersion
    yrs = len(c) // 252
    disp = [np.std(np.log(c[(y + 1) * 252 - 1] / c[y * 252])) for y in range(yrs)]
    out["xs_annual_ret_sd"] = np.mean(disp)
    return out


def long_facts(m):
    ix = index_series(m)
    D = len(ix)
    yrs = D // 252
    ann = np.array([ix[(y + 1) * 252 - 1] / ix[y * 252 if y else 0] - 1 for y in range(yrs)])
    out = {}
    out["cagr"] = (ix[-1] / ix[0]) ** (252 / D) - 1
    out["cagr_y2_21"] = (ix[-1] / ix[252]) ** (252 / (D - 252)) - 1
    out["annual_sd_y2_21"] = np.std(np.log(1 + ann[1:]))
    out["pct_neg_years"] = np.mean(ann[1:] < 0)
    out["bear_per_decade"] = drawdown_events(ix, 0.20) / (D / 2520)
    out["corr_per_decade"] = drawdown_events(ix, 0.10) / (D / 2520)
    out["max_dd"] = np.max(1 - ix / np.maximum.accumulate(ix))
    out["cash"] = m["m_federal_funds_rate"].mean()
    out["excess"] = out["cagr"] - out["cash"]
    ri = np.log(ix[1:] / ix[:-1])
    # variance ratios at 1, 3, 5 years (Poterba-Summers)
    for h in (21, 63, 252, 756, 1260):
        n = len(ri) // h
        agg = ri[: n * h].reshape(n, h).sum(1)
        out[f"VR_{h}"] = agg.var() / (h * ri.var())
    return out


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "short"
    ms = load(which)
    fs = [stock_facts(m) for m in ms]
    keys = sorted(set().union(*fs))
    for k in keys:
        x = np.array([f[k] for f in fs if k in f])
        if k == "index_ret_ann":
            x = (x ** (252 / (ms[0]["close"].shape[0] - 1)) - 1) * 100
            print(f"{k:22s} mean {x.mean():8.3f} median {np.median(x):8.3f} sd {x.std():7.3f}  n={len(x)}  (per cent a year)")
            continue
        print(f"{k:22s} mean {x.mean():8.3f} median {np.median(x):8.3f} sd {x.std():7.3f}  n={len(x)}")
    if which == "long":
        ls = [long_facts(m) for m in ms]
        print("--- long")
        for k in ls[0]:
            x = np.array([f[k] for f in ls])
            print(f"{k:22s} mean {x.mean():8.4f} median {np.median(x):8.4f} sd {x.std():7.4f} min {x.min():8.4f} max {x.max():8.4f}")
