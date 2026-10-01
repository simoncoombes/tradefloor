"""The cross-sectional rows registered for pt-v20 (C5-C8), one implementation
for the real panel and the model.

    C5  idiosyncratic variance ratio at 60 days (median name)
    C6  value signal rank IC against the next 20 trading days
    C7  6-1 and 12-1 month momentum rank IC against the next 20 trading days
    C8  daily loser-minus-winner (Lo-MacKinlay 1-day formation, next day), bps

Every estimator takes a [T, n] matrix of daily closes (and, for C6, a [T, n]
matrix of the value signal log(FV_published / P)), so the same function reads
the certified forty and a model history.
"""
import numpy as np


def idio(R):
    """Cross-sectionally demeaned daily log returns (equal weight)."""
    return R - R.mean(1, keepdims=True)


def variance_ratio(R, h):
    """Median over names of var(non-overlapping h-day idio sums) / (h var(daily idio))."""
    ri = idio(R)
    T = (len(ri) // h) * h
    rh = ri[:T].reshape(-1, h, ri.shape[1]).sum(1)
    return float(np.median(rh.var(0) / (h * ri.var(0))))


def _rank(x):
    return np.argsort(np.argsort(x)).astype(float)


def rank_ic(a, b):
    m = np.isfinite(a) & np.isfinite(b)
    if m.sum() < 5:
        return np.nan
    return float(np.corrcoef(_rank(a[m]), _rank(b[m]))[0, 1])


def ic_series(signal, lp, start, step, fwd=20):
    """Rank IC of signal[t] against lp[t+fwd]-lp[t], every `step` days from `start`."""
    out = []
    for t in range(start, len(lp) - fwd, step):
        out.append(rank_ic(signal[t], lp[t + fwd] - lp[t]))
    return np.array(out)


def momentum_ic(lp, lookback, skip=21, step=21, start=None):
    """Rank IC of the (lookback, skip) momentum return against the next 20 days,
    monthly, as real_mom.py computes it on the tape."""
    start = 252 if start is None else start
    sig = np.full_like(lp, np.nan)
    sig[lookback:] = lp[lookback - skip:len(lp) - skip] - lp[:len(lp) - lookback]
    return ic_series(sig, lp, start, step)


def lo_mackinlay(R, form=1, hold=1):
    """Contrarian book, $1 a side, weights -(r_i - rbar); per-period return."""
    T, n = R.shape
    out = []
    for t in range(form - 1, T - hold):
        f = R[t - form + 1:t + 1].sum(0)
        w = -(f - f.mean())
        s = np.abs(w).sum() / 2
        if s == 0:
            continue
        out.append(((w / s) * R[t + 1:t + 1 + hold].sum(0)).sum())
    return np.array(out)


def rows(close, value=None, value_windows=((0, 60),), value_step=5):
    """Every row for one history of daily closes [T, n] (and the value signal)."""
    lp = np.log(close)
    R = np.diff(lp, axis=0)
    o = {}
    for h in (20, 60, 120, 250):
        o[f"vr{h}"] = variance_ratio(R, h)
    lm = lo_mackinlay(R)
    o["lm1_bps"] = float(lm.mean() * 1e4)
    o["lm1_se_bps"] = float(lm.std() / np.sqrt(len(lm)) * 1e4)
    for lab, L in (("mom12_1", 252), ("mom6_1", 126)):
        v = momentum_ic(lp, L)
        o[lab] = float(np.nanmean(v))
        o[lab + "_n"] = int(np.isfinite(v).sum())
    if value is not None:
        v = ic_series(value, lp, 0, value_step)
        o["value_ic"] = float(np.nanmean(v))
        for lo, hi in value_windows:
            w = ic_series(value[lo:hi + 20], lp[lo:hi + 20], 0, value_step)
            o[f"value_ic_{lo}_{hi}"] = float(np.nanmean(w))
    return o
