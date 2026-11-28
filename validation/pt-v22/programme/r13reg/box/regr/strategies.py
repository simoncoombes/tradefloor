"""Probe 1: simple price-only (and public-data) strategies on cached markets.

Each strategy maps a market dict (lib.gen) to a weight matrix W[d, i]: the
position held from the close of day d to the close of day d+1, decided with
information available at that close (closes up to d, macro read at the open
of day d, which is the close of d-1 plus nothing new, fundamentals). Costs:
each unit of turnover pays the name's quoted half-spread plus 1 bp.

Long-only rules are compared with equal-weight buy-and-hold; long-short rules
(dollar neutral, gross 1) with zero.

Usage: python strategies.py set   where set is short | var | long
"""
from __future__ import annotations

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

from lib import gen, CYCLES

EXTRA_COST = 1e-4


def rets(close):
    return close[1:] / close[:-1] - 1.0


def run_weights(m, W):
    close = m["close"]
    r = rets(close)
    cost = m["hspread"] + EXTRA_COST
    prev = np.vstack([np.zeros(W.shape[1]), W[:-1]])
    turn = np.abs(W - prev)
    tc = (turn * cost).sum(1)
    gross = (W[:-1] * r).sum(1)
    return gross - tc[:-1]


def ew(m):
    D, N = m["close"].shape
    return np.full((D, N), 1.0 / N)


def rank_ls(sig, k=None):
    """Dollar-neutral: long lowest-ranked quintile, short highest; gross 1."""
    D, N = sig.shape
    W = np.zeros((D, N))
    k = k or max(1, N // 5)
    for d in range(D):
        s = sig[d]
        if np.all(np.isnan(s)):
            continue
        o = np.argsort(np.where(np.isnan(s), np.nanmedian(s), s))
        W[d, o[:k]] = 0.5 / k
        W[d, o[-k:]] = -0.5 / k
    return W


def lookback_ret(close, L):
    out = np.full(close.shape, np.nan)
    out[L:] = close[L:] / close[:-L] - 1
    return out


def hold(W, h):
    """Rebalance every h days: hold the weights set at the last rebalance."""
    out = W.copy()
    for d in range(W.shape[0]):
        if d % h:
            out[d] = out[d - 1]
    return out


def index_series(m):
    return m["close"].mean(1) if False else np.exp(np.log(m["close"]).mean(1))


def ts_weights(m, on):
    """Long-only equal weight when `on[d]`, cash otherwise."""
    D, N = m["close"].shape
    return np.where(np.asarray(on, bool)[:, None], 1.0 / N, 0.0) * np.ones((D, N))


# ------------------------------------------------------------ the rules

def S_xs_reversal(L, h=1):
    def f(m):
        return hold(rank_ls(lookback_ret(m["close"], L)), h)  # long losers
    return f


def S_xs_momentum(L, skip=0, h=1):
    def f(m):
        c = m["close"]
        sig = np.full(c.shape, np.nan)
        for d in range(L, len(c)):
            sig[d] = c[d - skip] / c[d - L] - 1
        return hold(rank_ls(-sig), h)  # long winners
    return f


def S_value_ep(h=21):
    def f(m):
        eps = np.where(m["eps"] > 0, m["eps"], np.nan)
        ep = eps[None, :] / m["close"]
        # relative to the sector's anchor P/E, which is public (tf.sectors, MODEL.md)
        return hold(rank_ls(-ep), h)  # long high E/P
    return f


def S_value_fair(h=21):
    """Public fair value from published EPS, sector, growth and the corporate yield."""
    def f(m):
        D, N = m["close"].shape
        sig = np.full((D, N), np.nan)
        for d in range(0, D, h):
            y = m["m_corporate_bond_yield"][d]
            for i in range(N):
                try:
                    fv = tf.fair_value(eps=float(m["eps"][i]), sector=str(m["sector"][i]),
                                       revenue_growth=float(m["growth"][i]),
                                       corporate_bond_yield=float(y),
                                       book_value_per_share=float(m["bv"][i])).fair_value
                except Exception:
                    fv = np.nan
                sig[d, i] = np.log(m["close"][d, i] / fv) if fv and fv > 0 else np.nan
        for d in range(D):
            if d % h:
                sig[d] = sig[d - 1]
        return hold(rank_ls(sig), h)  # long cheap
    return f


def S_pairs(L=20, z=1.5):
    """Within-sector pairs: long the name below its sector mean by z sd of the spread."""
    def f(m):
        c = np.log(m["close"])
        D, N = c.shape
        W = np.zeros((D, N))
        sec = m["sector"]
        for s in np.unique(sec):
            idx = np.where(sec == s)[0]
            if len(idx) < 2:
                continue
            dev = c[:, idx] - c[:, idx].mean(1, keepdims=True)
            for d in range(L, D):
                mu = dev[d - L:d].mean(0); sd = dev[d - L:d].std(0) + 1e-9
                zz = (dev[d] - mu) / sd
                W[d, idx] = np.where(zz < -z, 1.0, np.where(zz > z, -1.0, 0.0))
        g = np.abs(W).sum(1, keepdims=True)
        return np.where(g > 0, W / np.maximum(g, 1e-12), 0.0)
    return f


def S_ts_mom(L):
    def f(m):
        ix = index_series(m)
        on = np.zeros(len(ix), bool)
        on[L:] = ix[L:] > ix[:-L]
        on[:L] = True
        return ts_weights(m, on)
    return f


def S_ts_rev(L, thresh):
    """Buy the index only after it has fallen `thresh` over L days; hold 20."""
    def f(m):
        ix = index_series(m)
        on = np.zeros(len(ix), bool)
        last = -999
        for d in range(L, len(ix)):
            if ix[d] / ix[d - L] - 1 < -thresh:
                last = d
            on[d] = d - last < 20
        return ts_weights(m, on)
    return f


def S_ma(L):
    def f(m):
        ix = index_series(m)
        on = np.ones(len(ix), bool)
        for d in range(L, len(ix)):
            on[d] = ix[d] > ix[d - L:d + 1].mean()
        return ts_weights(m, on)
    return f


def S_vix_out(th):
    def f(m):
        # the VIX read at the open of d+1 is the close of d
        v = np.r_[m["m_vix"][1:], m["m_vix"][-1]]
        return ts_weights(m, v < th)
    return f


def S_vix_in(th):
    """Buy fear: hold only after the VIX has closed above th, for 40 sessions."""
    def f(m):
        v = np.r_[m["m_vix"][1:], m["m_vix"][-1]]
        on = np.zeros(len(v), bool); last = -999
        for d in range(len(v)):
            if v[d] > th:
                last = d
            on[d] = d - last < 40
        return ts_weights(m, on)
    return f


def S_vol_target(target=0.15, L=20):
    def f(m):
        ix = index_series(m)
        r = np.r_[0, np.diff(np.log(ix))]
        D, N = m["close"].shape
        lev = np.ones(D)
        for d in range(L, D):
            sd = r[d - L + 1:d + 1].std() * np.sqrt(252)
            lev[d] = min(2.0, target / max(sd, 1e-4))
        return (lev[:, None] / N) * np.ones((D, N))
    return f


def S_cycle(bad=("peak", "contraction")):
    """Out of the market while the published business-cycle phase is in `bad`."""
    def f(m):
        # the phase read at the open of d+1 is set by the close of d
        cy = np.r_[m["cyc"][1:], m["cyc"][-1]]
        badi = [CYCLES.index(b) for b in bad]
        return ts_weights(m, ~np.isin(cy, badi))
    return f


def S_cycle_true(bad=("contraction", "trough")):
    """HIDDEN-STATE BENCHMARK: out while the TRUE phase (not public) is in `bad`."""
    def f(m):
        cy = np.r_[m["cyc_true"][1:], m["cyc_true"][-1]]
        badi = [CYCLES.index(b) for b in bad]
        return ts_weights(m, ~np.isin(cy, badi))
    return f


def S_level(field, th, below=True):
    """Out of the market while a published macro level is below (or above) th."""
    def f(m):
        x = np.r_[m["m_" + field][1:], m["m_" + field][-1]]
        out = (x < th) if below else (x > th)
        return ts_weights(m, ~out)
    return f


def S_cycle_long_only(good=("trough", "recovery")):
    def f(m):
        cy = np.r_[m["cyc"][1:], m["cyc"][-1]]
        return ts_weights(m, np.isin(cy, [CYCLES.index(g) for g in good]))
    return f


def S_macro(field, sign, L=21):
    """Out of the market when a macro series has moved against equities over L days."""
    def f(m):
        x = np.r_[m["m_" + field][1:], m["m_" + field][-1]]
        on = np.ones(len(x), bool)
        on[L:] = sign * (x[L:] - x[:-L]) <= 0
        return ts_weights(m, on)
    return f


def S_calendar(mod, keep):
    """Hold the market only on sessions whose index mod `mod` is in keep."""
    def f(m):
        D = m["close"].shape[0]
        d1 = np.arange(D) + 1  # the day being held is d+1
        return ts_weights(m, np.isin(d1 % mod, keep))
    return f


RULES = {
    "xs_reversal_1d": S_xs_reversal(1),
    "xs_reversal_5d": S_xs_reversal(5),
    "xs_reversal_5d_weekly": S_xs_reversal(5, 5),
    "xs_reversal_20d_monthly": S_xs_reversal(20, 21),
    "xs_momentum_20d_weekly": S_xs_momentum(20, 0, 5),
    "xs_momentum_60d_monthly": S_xs_momentum(60, 0, 21),
    "xs_momentum_120d_monthly": S_xs_momentum(120, 0, 21),
    "value_ep_monthly": S_value_ep(21),
    "value_fairvalue_monthly": S_value_fair(21),
    "pairs_sector_20d": S_pairs(20, 1.5),
    "ts_mom_20d": S_ts_mom(20),
    "ts_mom_120d": S_ts_mom(120),
    "ma_200d": S_ma(200),
    "ma_50d": S_ma(50),
    "ts_rev_5d_3pct": S_ts_rev(5, 0.03),
    "ts_rev_20d_8pct": S_ts_rev(20, 0.08),
    "vix_out_30": S_vix_out(30),
    "vix_out_25": S_vix_out(25),
    "vix_in_35": S_vix_in(35),
    "vol_target_15": S_vol_target(0.15),
    "cycle_out_peak_contraction": S_cycle(("peak", "contraction")),
    "cycle_out_contraction": S_cycle(("contraction",)),
    "cycle_in_trough_recovery": S_cycle_long_only(("trough", "recovery")),
    "cycle_out_contraction_trough": S_cycle(("contraction", "trough")),
    "macro_out_rates_up": S_macro("corporate_bond_yield", +1),
    "macro_out_growth_down": S_macro("gdp_growth", -1),
    "macro_out_unemp_up": S_macro("unemployment_rate", +1),
    "macro_out_unemp_up_63": S_macro("unemployment_rate", +1, 63),
    "macro_out_fed_cut_63": S_macro("federal_funds_rate", -1, 63),
    "macro_out_fed_hike_63": S_macro("federal_funds_rate", +1, 63),
    "macro_out_spread_up_21": S_macro("corporate_bond_yield", +1, 21),
    "macro_out_gdp_neg": S_level("gdp_growth", 0.0, True),
    "macro_out_feargreed_lt30": S_level("fear_greed_index", 30.0, True),
    "macro_out_feargreed_gt70": S_level("fear_greed_index", 70.0, False),
    "HIDDEN_true_cycle_out_contr_trough": S_cycle_true(("contraction", "trough")),
    "HIDDEN_true_cycle_out_contraction": S_cycle_true(("contraction",)),
    "cal_month_first_5": S_calendar(21, [1, 2, 3, 4, 5]),
    "cal_month_last_5": S_calendar(21, [16, 17, 18, 19, 20]),
}


def evaluate_rules(markets, rules=None, per_year=252):
    rules = rules or RULES
    bh = [run_weights(m, ew(m)) for m in markets]
    out = {}
    for name, f in rules.items():
        ex = []; beat = 0; sr = []; tot = []
        for m, b in zip(markets, bh):
            W = f(m)
            r = run_weights(m, W)
            longshort = abs(W.sum(1)).max() < 1e-9 or name.startswith(("xs_", "value", "pairs"))
            ref = np.zeros_like(r) if longshort else b
            yrs = len(r) / per_year
            e = (np.prod(1 + r) ** (1 / yrs) - 1) - (np.prod(1 + ref) ** (1 / yrs) - 1)
            ex.append(e * 100)
            beat += e > 0
            sr.append(r.mean() / (r.std() + 1e-12) * np.sqrt(per_year))
        ex = np.array(ex)
        out[name] = dict(excess_pct_yr_mean=ex.mean(), excess_median=np.median(ex),
                         t=ex.mean() / (ex.std() + 1e-12) * np.sqrt(len(ex)),
                         beat=int(beat), n=len(ex), sharpe_median=float(np.median(sr)),
                         ref="zero" if (name.startswith(("xs_", "value", "pairs"))) else "buy-and-hold")
    return out


def load(which):
    import os
    n_long = int(os.environ.get("AUDIT_LONG_N", "30")); d_long = int(os.environ.get("AUDIT_LONG_DAYS", "5292"))
    n_short = int(os.environ.get("AUDIT_SHORT_N", "60"))
    # the thirteenth registration's seed variables (defaults: the held-out screen's first seeds)
    s0, v0, l0 = (int(os.environ.get(k, d)) for k, d in (("AUDIT_SEED_SHORT", "2101"), ("AUDIT_SEED_VAR", "2201"),
                                                          ("AUDIT_SEED_LONG", "2301")))
    if which == "short":
        return [gen(seed=s, useed=111, n=40, days=252) for s in range(s0, s0 + n_short)]
    if which == "var":
        return [gen(seed=s, useed=s, n=40, days=252) for s in range(v0, v0 + n_short)]
    if which == "long":
        return [gen(seed=s, useed=s, n=40, days=d_long) for s in range(l0, l0 + n_long)]
    raise SystemExit(which)


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "short"
    ms = load(which)
    res = evaluate_rules(ms)
    print(f"{'rule':32s} {'ref':>12s} {'mean%/yr':>9s} {'median':>8s} {'t':>6s} {'beat':>7s} {'SR med':>7s}")
    for k, v in sorted(res.items(), key=lambda kv: -kv[1]["t"]):
        print(f"{k:32s} {v['ref']:>12s} {v['excess_pct_yr_mean']:9.2f} {v['excess_median']:8.2f} {v['t']:6.2f} {v['beat']:3d}/{v['n']:<3d} {v['sharpe_median']:7.2f}")
