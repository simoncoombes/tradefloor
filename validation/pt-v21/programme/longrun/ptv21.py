"""ptv21 -- the checks pt-v21 registers before any mechanism is tuned against
them (CRITERIA-pt-v21.md). One implementation of each statistic, run on the
real series and on the simulator's output alike, as estimators.py is for
the long run.

    python ptv21.py real [--out data/ptv21/real.json]
        the real side: every row's real value and band from the frozen
        series in data/ptv21/ (README there), and t(n) for the fixed band
        rule re-solved with the engine's own null and draws
    python ptv21.py measure --arm NAME[@BASE][:dial=v,...] --seeds 201-203 \\
        [--years 3] [--parts free,mo,xn,ac] [--names 12] [--workers 3] \\
        [--engine PATH] --out DIR
        the model side, per seed: a free history on the certified roster
        (VIX, unemployment, oil, the index and every name's first-tick open
        and close each session); the metaorder curve (metaorder_curve.py,
        the Q rows); the cross-name flow program (XN1); the multi-day
        program and the co-impact pair (AC1, AC2)
    python ptv21.py grade DIR [--real data/ptv21/real.json] [--json OUT]
        every row against its band

Nothing here starts a box. --engine puts an engine checkout's python/ first
on the path when a built _core sits there, as longrun.py does; without it
the tradefloor already installed is used.
"""
import argparse
import json
import math
import os
import pathlib
import random
import statistics as st
import sys
import time
from concurrent.futures import ProcessPoolExecutor

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
DATA = HERE / "data" / "ptv21"
ROSTER = (40, 111)
BURN = 252
YEAR = 252
MONTH = 21
WINDOW_MONTHS = 60
BOOT_B = 2000
BOOT_SEED = 20261004

#: The fixed band rule of facts.py: median +/- t(n) * trimmed sd, t(n) solved
#: so a fresh reading from the same law falls outside 0.06486 of the time,
#: on facts.band_rule_fixed_false_alarm's null (n + 1 iid normals, seed
#: 20260905, 200,000 draws). t(9), t(16) and t(35) are the engine's table;
#: solve_t() re-derives them and the counts this file needs.
FIXED_TOLERANCE = 0.06486
FACTS_T = {5: 5.039474, 7: 3.483891, 9: 2.982334, 16: 2.417688, 35: 2.111347}


# ====================================================================== maths

def skew(x):
    """Moment skew, population form: mean((x - m)^3) / sd^3."""
    x = np.asarray(x, float)
    m = x.mean(); s = x.std()
    return float(((x - m) ** 3).mean() / s ** 3) if s > 0 else float("nan")


def ar1(x):
    """OLS slope of x_t on x_(t-1), with an intercept."""
    x = np.asarray(x, float)
    a, b = x[:-1] - x[:-1].mean(), x[1:] - x[1:].mean()
    d = (a * a).sum()
    return float((a * b).sum() / d) if d > 0 else float("nan")


def trimmed_sd(v):
    """facts.trimmed_sd: the sample sd with the value farthest from the median dropped."""
    v = list(v)
    if len(v) < 3:
        return st.stdev(v) if len(v) > 1 else 0.0
    c = st.median(v)
    return st.stdev(sorted(v, key=lambda x: abs(x - c))[:-1])


def solve_t(n, draws=200_000, seed=20260905, target=FIXED_TOLERANCE):
    """t(n) for the fixed rule, on facts.band_rule_fixed_false_alarm's draws
    exactly (same generator, same order), solved as the quantile of
    |fresh - median| / trimmed_sd that leaves `target` above it."""
    rng = random.Random(seed)
    z = np.empty(draws)
    for k in range(draws):
        w = [rng.gauss(0.0, 1.0) for _ in range(n)]
        c, s = st.median(w), trimmed_sd(w)
        z[k] = abs(rng.gauss(0.0, 1.0) - c) / s
    z.sort()
    # rate(t) = share of z > t; the smallest t with rate <= target.
    k = int(math.ceil(draws * (1 - target))) - 1
    t = float(z[k])
    rate = float((z > t).mean())
    return t, rate


def fixed_band(values, t):
    c, s = st.median(values), trimmed_sd(values)
    return {"centre": c, "trimmed_sd": s, "t": t, "low": c - t * s, "high": c + t * s,
            "n": len(values), "min": min(values), "max": max(values)}


def boot_se(values, stat, B=BOOT_B, seed=BOOT_SEED):
    """sd of `stat` over B resamples of `values` (whole units: years or windows)."""
    rng = np.random.default_rng(seed)
    v = list(values); n = len(v)
    out = [stat([v[i] for i in rng.integers(0, n, n)]) for _ in range(B)]
    return float(np.std(out, ddof=1))


def ks_2samp(a, b):
    """Two-sample Kolmogorov-Smirnov D and its asymptotic p (Smirnov)."""
    a, b = np.sort(np.asarray(a, float)), np.sort(np.asarray(b, float))
    x = np.concatenate([a, b])
    d = float(np.max(np.abs(np.searchsorted(a, x, "right") / len(a) - np.searchsorted(b, x, "right") / len(b))))
    ne = len(a) * len(b) / (len(a) + len(b))
    lam = (math.sqrt(ne) + 0.12 + 0.11 / math.sqrt(ne)) * d
    p = 2 * sum((-1) ** (k - 1) * math.exp(-2 * k * k * lam * lam) for k in range(1, 101))
    return d, float(min(1.0, max(0.0, p)))


def ols_slope(x, y):
    x, y = np.asarray(x, float), np.asarray(y, float)
    return float(np.polyfit(x, y, 1)[0])


# ================================================================ estimators
# Each takes plain series, so the real side and the model side call the same
# function. Model series are daily; the macro rows read monthly means of
# 21-session months, as the real series are monthly means or monthly surveys.

def monthly_means(daily, m=MONTH):
    d = np.asarray(daily, float)
    k = len(d) // m
    return d[:k * m].reshape(k, m).mean(1)


def windows_back(n, w):
    """Non-overlapping windows of w consecutive points anchored at the LAST
    point and walking back; the remainder at the start is dropped (the
    anchor rule of facts.INDEX_TAIL_WINDOWS)."""
    k = n // w
    start = n - k * w
    return [(start + i * w, start + (i + 1) * w) for i in range(k)]


def acf_level(x, k=12):
    """Lag-k autocorrelation of a window's own demeaned level. A window that
    never moves (a state held at a clamp) reads 1.0: it is as persistent as
    a level can be, and leaving it undefined would drop exactly the windows
    the row exists to catch."""
    x = np.asarray(x, float) - np.mean(x)
    d = (x * x).sum()
    return float((x[:-k] * x[k:]).sum() / d) if d > 1e-12 else 1.0


def unemployment_window(u):
    """One 60-month window of the unemployment rate, in per cent:
    U1 share of months within 0.2 points of the window's own low;
    U2 the window's range, points;
    U3 lag-12 autocorrelation of the monthly level (persistence);
    U4 sd of the 12-month change, points."""
    u = np.asarray(u, float)
    ch = u[12:] - u[:-12]
    return {"U1": float((u <= u.min() + 0.2 + 1e-9).mean()), "U2": float(u.max() - u.min()),
            "U3": acf_level(u), "U4": float(ch.std(ddof=1)), "ar1": ar1(u)}


def oil_window(p):
    """One 60-month window of the oil price: O1 log range; O2 share of
    months within 2% (log) of the window's own high or low; O3 lag-12
    autocorrelation of the monthly log price; O4 sd of the 12-month log
    change."""
    lp = np.log(np.asarray(p, float))
    hi, lo = lp.max(), lp.min()
    pinned = (lp >= hi - 0.02) | (lp <= lo + 0.02)
    ch = lp[12:] - lp[:-12]
    return {"O1": float(hi - lo), "O2": float(pinned.mean()), "O3": acf_level(lp),
            "O4": float(ch.std(ddof=1)), "ar1": ar1(lp)}


def year_skews(level, burn=0):
    """Skew of daily log returns in each 252-return year after `burn` sessions."""
    r = np.diff(np.log(np.asarray(level, float)[burn:]))
    return [skew(r[a:b]) for a, b in [(k * YEAR, (k + 1) * YEAR) for k in range(len(r) // YEAR)]]


def tail_counts(level, burn=0, cut=0.03):
    r = np.diff(np.log(np.asarray(level, float)[burn:]))
    return int((r < -cut).sum()), int((r > cut).sum())


def overnight(C, O):
    """C closes, O first prints, [days, names]. Night g = log(O_t / C_(t-1)),
    session i = log(C_t / O_t). ON1 and G1: median over names of
    var(g) / (var(g) + var(i)); G1c the same on the equal-weighted means
    (common_eg.measure's definitions)."""
    C, O = np.asarray(C, float), np.asarray(O, float)
    g = np.log(O[1:] / C[:-1]); it = np.log(C[1:] / O[1:])
    share = g.var(0) / (g.var(0) + it.var(0))
    mg, mi = g.mean(1), it.mean(1)
    return {"share_median": float(np.median(share)), "ew_share": float(mg.var() / (mg.var() + mi.var())),
            "first_tick_sd": float(np.median(g.std(0))), "day_sd": float(np.median(np.diff(np.log(C), axis=0).std(0)))}


# ====================================================================== real

def _csv(path, date_fmt=None):
    rows = [l.strip().split(",") for l in open(path) if l.strip()]
    head, body = rows[0], rows[1:]
    out = []
    for r in body:
        if r[-1] in (".", ""):
            continue
        d = r[0]
        if date_fmt == "mdy":
            m, dd, y = d.split("/"); d = f"{y}-{m}-{dd}"
        out.append((d, float(r[-1] if len(r) == 2 else r[head.index("CLOSE")])))
    return out


def real(a):
    out = {"made": time.strftime("%Y-%m-%d"), "boot": {"B": BOOT_B, "seed": BOOT_SEED}}
    # ---------------------------------------------------------------- t(n)
    ts = {}
    for n in (9, 10, 15, 16):
        t, rate = solve_t(n)
        ts[n] = {"t": t, "rate": rate, "facts": FACTS_T.get(n)}
    out["t"] = ts

    # ---------------------------------------------------- OS: opening state
    vix = _csv(DATA / "VIX_History.csv", "mdy")
    first = {}
    for d, c in vix:
        first.setdefault(d[:4], (d, c))
    years = sorted(y for y in first if "1990" <= y <= "2026")
    v0 = [first[y][1] for y in years]
    l0 = [math.log(x) for x in v0]
    sd_ln = lambda v: st.stdev(v)
    os_ = {"years": f"{years[0]}-{years[-1]}", "n": len(v0), "first_days": {y: first[y] for y in years}}
    med, sdl = st.median(v0), sd_ln(l0)
    se_med = boot_se(v0, st.median); se_sd = boot_se(l0, sd_ln)
    os_["OS1"] = {"real": med, "se": se_med, "low": med - 2 * se_med, "high": med + 2 * se_med}
    os_["OS2"] = {"real": sdl, "se": se_sd, "low": sdl - 2 * se_sd, "high": sdl + 2 * se_sd}
    allv = [c for d, c in vix if d < "2026-01-01"]
    os_["all_days"] = {"p10": float(np.percentile(allv, 10)), "p50": float(np.median(allv)),
                       "p90": float(np.percentile(allv, 90)), "sd_ln": float(np.std(np.log(allv), ddof=1))}
    out["OS"] = os_

    # ------------------------------------------------------------ SK: skew
    import gzip
    tp = json.load(gzip.open(HERE / "data" / "tape.json.gz"))
    lvl = tp["level"]
    r = np.diff(np.log(np.asarray(lvl, float)))
    wins = windows_back(len(r), YEAR)
    sk = [skew(r[a:b]) for a, b in wins]
    m = st.median(sk); se = boot_se(sk, st.median)
    dn, up = int((r < -0.03).sum()), int((r > 0.03).sum())
    # dn/up: calendar-year block bootstrap
    yr = [d[:4] for d in tp["dates"][1:]]
    byyear = {}
    for y, x in zip(yr, r):
        byyear.setdefault(y, []).append(x)
    blocks = [(int((np.asarray(v) < -0.03).sum()), int((np.asarray(v) > 0.03).sum())) for v in byyear.values()]
    ratio = lambda bl: sum(b[0] for b in bl) / max(1, sum(b[1] for b in bl))
    se_r = boot_se(blocks, ratio)
    out["SK"] = {"tape": f"{tp['dates'][0]}..{tp['dates'][-1]} ({tp['source'][:40]}...)",
                 "windows": len(sk), "window_skews": sk, "pooled_skew": skew(r),
                 "SK1": {"real": m, "se": se, "low": m - 2 * se, "high": m + 2 * se},
                 "SK2": {"real": dn / up, "dn": dn, "up": up, "years": len(blocks), "se": se_r,
                         "low": dn / up - 2 * se_r, "high": dn / up + 2 * se_r}}

    # ----------------------------------------------- U, O: macro, FRED
    un = _csv(DATA / "UNRATE.csv")
    uv = [x for _, x in un]
    uw = windows_back(len(uv), WINDOW_MONTHS)
    U = [unemployment_window(uv[a:b]) | {"from": un[a][0], "to": un[b - 1][0]} for a, b in uw]
    out["U"] = {"series": "FRED UNRATE", "span": f"{un[uw[0][0]][0]}..{un[-1][0]}", "windows": U}
    tn = ts[len(U)]["t"] if len(U) in ts else solve_t(len(U))[0]
    for k in ("U1", "U2", "U3", "U4"):
        out["U"][k] = fixed_band([w[k] for w in U], tn)
    oil = [(d, x) for d, x in _csv(DATA / "WTISPLC.csv")]
    ov = [x for _, x in oil]
    ow = windows_back(len(ov), WINDOW_MONTHS)
    ow = [w for w in ow if oil[w[0]][0] >= "1974-01-01"]
    Ow = [oil_window(ov[a:b]) | {"from": oil[a][0], "to": oil[b - 1][0]} for a, b in ow]
    out["O"] = {"series": "FRED WTISPLC", "span": f"{Ow[0]['from']}..{Ow[-1]['to']}", "windows": Ow}
    tn = ts[len(Ow)]["t"] if len(Ow) in ts else solve_t(len(Ow))[0]
    for k in ("O1", "O2", "O3", "O4"):
        out["O"][k] = fixed_band([w[k] for w in Ow], tn)

    # ------------------------------------------------- ON: overnight share
    ot = json.load(open(HERE.parent / "overnight-target.json"))
    wv = [w["share_median"] for w in ot["windows"] if not w["crisis"]]
    out["ON"] = {"source": "programme/overnight-target.json (tools/calibration/overnight_band.py, "
                           "the reference panel's forty names, ten 253-bar windows 2015-07..2025-07)",
                 "ON1": fixed_band(wv, FACTS_T[9]),
                 "crisis_window": [w["share_median"] for w in ot["windows"] if w["crisis"]][0]}
    p = pathlib.Path(a.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    json.dump(out, open(p, "w"), indent=1)
    print(f"wrote {p}")
    for k in ("OS1", "OS2"):
        print(k, {x: round(y, 4) for x, y in out["OS"][k].items()})
    for k in ("SK1", "SK2"):
        print(k, {x: (round(y, 4) if isinstance(y, float) else y) for x, y in out["SK"][k].items()})
    for g in ("U", "O"):
        for k in sorted(x for x in out[g] if len(x) == 2):
            b = out[g][k]
            print(k, f"centre {b['centre']:.4f} sd {b['trimmed_sd']:.4f} n {b['n']} t {b['t']:.4f} "
                     f"band [{b['low']:.4f}, {b['high']:.4f}] range [{b['min']:.4f}, {b['max']:.4f}]")
    print("ON1", {x: round(y, 4) for x, y in out["ON"]["ON1"].items()})
    print("t(n)", ts)


# ===================================================================== model

_TF = {}


def _engine(path):
    if "tf" not in _TF:
        if path:
            p = str(pathlib.Path(path) / "python")
            if p not in sys.path and any(pathlib.Path(p, "tradefloor").glob("_core*.so")):
                sys.path.insert(0, p)
        import tradefloor as tf
        _TF["tf"] = tf
    return _TF["tf"]


def _f64(b):
    return np.frombuffer(b, "<f8").copy()


def _params(tf, base, dials):
    return tf.ModelParams.from_preset(base, **dials) if dials else tf.ModelParams.from_preset(base)


def run_free(tf, params, seed, years):
    """One free history from the opening state: per session the first
    tick's print (the open an order sent before the bell can fill at), the
    close, and the VIX, unemployment and oil at the close. The day is run as
    open_market, one tick, 389 ticks with the clock advanced, close_market,
    which reproduces run_days(1) to the bit (checked by `selfcheck`)."""
    u = list(tf.Universe.random(*ROSTER[:1], seed=ROSTER[1]))
    shares = np.array([float(i.shares_outstanding) for i in u])
    e = tf.Engine(seed=seed, universe=u, model=params)
    vix_open = float(e.macro_fields["vix"])
    O, C, vix, un, oil = [], [], [], [], []
    for _ in range(years * YEAR):
        e.open_market()
        e.run_session(9, 30, 3, 1)
        O.append(_f64(e.prices()))
        e.run_session(9, 31, 3, 389)
        e.close_market()
        C.append(_f64(e.prices()))
        mf = e.macro_fields
        vix.append(float(mf["vix"])); un.append(float(mf["unemployment_rate"]) * 100); oil.append(float(mf["oil_price"]))
    C, O = np.array(C), np.array(O)
    return {"seed": seed, "vix_state0": vix_open, "vix": vix, "unemployment": un, "oil": oil,
            "level": (C * shares).sum(1).tolist(), "open": O.tolist(), "close": C.tolist()}


def run_open(tf, params, seed):
    """OS: the opening state alone, the VIX before any session and at the
    first close. Cheap, so OS can read many more seeds than the free run."""
    u = list(tf.Universe.random(ROSTER[0], seed=ROSTER[1]))
    e = tf.Engine(seed=seed, universe=u, model=params)
    v = float(e.macro_fields["vix"])
    e.run_days(1, record=False)
    return {"seed": seed, "vix_state0": v, "vix_first_close": float(e.macro_fields["vix"])}


def selfcheck(tf, params, seed=201, days=5):
    u = list(tf.Universe.random(ROSTER[0], seed=ROSTER[1]))
    a = tf.Engine(seed=seed, universe=u, model=params); b = tf.Engine(seed=seed, universe=u, model=params)
    for d in range(days):
        a.run_days(1, record=False)
        b.open_market(); b.run_session(9, 30, 3, 1); b.run_session(9, 31, 3, 389); b.close_market()
    return bool(np.array_equal(_f64(a.prices()), _f64(b.prices())))


def _session(e):
    e.open_market(); e.run_session(9, 30, 3, 390, close_at_end=True); e.close_market()


def _warm(tf, params, seed, warm=60):
    """metaorder_curve's convention: the certified roster, `warm` sessions to
    read each name's close-to-close daily sigma, then 15 ticks of the next."""
    u = tf.Universe.random(ROSTER[0], seed=ROSTER[1])
    e = tf.Engine(seed=seed, universe=u, model=params)
    closes = [_f64(e.prices())]
    for _ in range(warm):
        _session(e); closes.append(_f64(e.prices()))
    sig = np.diff(np.log(np.array(closes)), axis=0).std(axis=0)
    return u, e, sig


def run_xn(tf, params, seed, f=0.1):
    """XN1, the flow path: a standing buy of f of each name's daily volume
    spread evenly over one session (`flow_per_tick`, avg_volume * f / 390 a
    tick), every name at the same participation, forked against a twin
    with none. The impact is log(close / twin's close) in the name's daily
    sigma."""
    u, base, sig = _warm(tf, params, seed)
    flow = {x.ticker: (x.avg_volume * f / 390.0, 0.0) for x in u}
    e, ctl = base.fork(2)
    for x, fl in ((e, flow), (ctl, None)):
        x.open_market()
        if fl:
            x.run_session(9, 30, 3, 390, close_at_end=True, flow_per_tick=fl)
        else:
            x.run_session(9, 30, 3, 390, close_at_end=True)
        x.close_market()
    imp = np.log(_f64(e.prices()) / _f64(ctl.prices())) / sig
    px = _f64(ctl.prices())
    return {"seed": seed, "f": f, "impact_sigma": imp.tolist(), "sigma": sig.tolist(),
            "dollar_volume": [x.avg_volume * p for x, p in zip(u, px)], "adv": [x.avg_volume for x in u]}


def _twap_day(e, ctl, tk, q_day, n_slices=36, gap=10, t0=15, agents=("me",)):
    """One day TWAP of q_day shares in n_slices every `gap` ticks, from tick
    t0 of a session both forks have just opened (metaorder_curve's `day`
    schedule: 15 ticks in, 36 slices 10 ticks apart); returns per-agent (sum fill*(avg-mid), sum fill*mid,
    filled) priced at the twin's mid when the slice is sent. With several
    agents each sends q_day/len(agents)/n_slices per slice, in label order."""
    acc = {a: [0.0, 0.0, 0.0] for a in agents}
    if t0:
        for x in (e, ctl):
            x.run_session(9, 30, 3, t0)
    t = t0
    for k in range(n_slices):
        mid = ctl.book(tk).mid_price
        for a in agents:
            r = e.submit(a, tk, q_day / len(agents) / n_slices)
            acc[a][0] += r["filled"] * (r["average_price"] - mid)
            acc[a][1] += r["filled"] * mid
            acc[a][2] += r["filled"]
        step = gap if k < n_slices - 1 else 390 - t
        m = 9 * 60 + 30 + t
        for x in (e, ctl):
            x.run_session(m // 60, m % 60, 3, step, close_at_end=(t + step == 390))
        t += step
    return acc


def run_ac(tf, params, seed, names=4, days=12, f=0.1, k_agents=4):
    """AC1: one program buying f of daily volume every day for `days` days,
    a day TWAP of 36 slices each day, on names spread by market cap, forked
    against a twin that does not trade; per-share cost of the first n days
    in the name's daily sigma, n = 1..days. AC2: the same day TWAP split
    across k_agents labels sending together, against one label sending the
    whole, the first day only (co-impact: the market reacts to the net)."""
    u, base, sig = _warm(tf, params, seed)
    for x in (base,):
        x.open_market()
    mcap = np.array([x.market_cap for x in u])
    order = np.argsort(mcap)[::-1]
    pick = [int(order[int(round(r))]) for r in np.linspace(0, len(u) - 1, names)]
    out = []
    for i in pick:
        tk = u[i].ticker; q = f * u[i].avg_volume
        e, ctl = base.fork(2)
        cum = [0.0, 0.0]; per_n = []; disp = []
        for d in range(days):
            if d > 0:
                for x in (e, ctl):
                    x.open_market()
            acc = _twap_day(e, ctl, tk, q)["me"]
            for x in (e, ctl):
                x.close_market()
            cum[0] += acc[0]; cum[1] += acc[1]
            per_n.append(cum[0] / cum[1] / sig[i])
            disp.append(math.log(_f64(e.prices())[i] / _f64(ctl.prices())[i]) / sig[i])
        e1, c1 = base.fork(2)
        one = _twap_day(e1, c1, tk, q)["me"]
        ek, ck = base.fork(2)
        many = _twap_day(ek, ck, tk, q, agents=tuple(f"a{j}" for j in range(k_agents)))
        many_is = st.fmean(v[0] / v[1] for v in many.values()) / sig[i]
        out.append({"seed": seed, "name": i, "ticker": tk, "sigma": float(sig[i]),
                    "is_by_days": per_n, "displacement_by_days": disp,
                    "coimpact_one": one[0] / one[1] / sig[i], "coimpact_many": many_is})
    return out


def _task(spec):
    part, engine, base, dials, seed, years, names = spec
    tf = _engine(engine)
    p = _params(tf, base, dials)
    t0 = time.time()
    if part == "free":
        res = run_free(tf, p, seed, years)
    elif part == "open":
        res = run_open(tf, p, seed)
    elif part == "xn":
        res = run_xn(tf, p, seed)
    elif part == "ac":
        res = run_ac(tf, p, seed)
    elif part == "mo":
        sys.path.insert(0, str(HERE))
        import metaorder_curve as mc
        res = mc.run_seed(seed, p, names, quick=False)
    return part, seed, res, time.time() - t0


def _parse_arm(text):
    head, _, tail = text.partition(":")
    name, _, base = head.partition("@")
    dials = {k: float(v) for k, v in (kv.split("=") for kv in tail.split(",") if kv)}
    return name, base or "pt-v20", dials


def _parse_seeds(text):
    out = []
    for part in text.split(","):
        a, _, b = part.partition("-")
        out.extend(range(int(a), int(b or a) + 1))
    return out


def measure(a):
    name, base, dials = _parse_arm(a.arm)
    if a.arm_file:
        name, base, dials = _parse_arm(open(a.arm_file).read().strip())
    outdir = pathlib.Path(a.out) / name
    outdir.mkdir(parents=True, exist_ok=True)
    tf = _engine(a.engine)
    meta = {"arm": name, "base": base, "dials": dials, "years": a.years, "names": a.names,
            "tradefloor": getattr(tf, "__version__", "?"), "fingerprint": _params(tf, base, dials).fingerprint,
            "selfcheck_run_days_bit_identical": selfcheck(tf, _params(tf, base, dials))}
    json.dump(meta, open(outdir / "meta.json", "w"), indent=1)
    specs = [(part, a.engine, base, dials, s, a.years, a.names)
             for part in a.parts.split(",") for s in _parse_seeds(a.seeds)
             if not (outdir / f"{part}-{s}.json").exists()]
    with ProcessPoolExecutor(max_workers=a.workers) as ex:
        for part, seed, res, dt in ex.map(_task, specs):
            json.dump(res, open(outdir / f"{part}-{seed}.json", "w"))
            print(f"{name} {part} {seed} {dt:.0f}s", flush=True)


# ===================================================================== grade

def _load(adir, part):
    fs = sorted(pathlib.Path(adir).glob(f"{part}-*.json"))
    return [json.load(open(f)) for f in fs]


def model_rows(adir):
    rows, rep = {}, {}
    free = _load(adir, "free")
    opn = _load(adir, "open")
    if opn:
        v0 = [h["vix_first_close"] for h in opn]
        rows["OS1"] = st.median(v0)
        rows["OS2"] = float(np.std(np.log(v0), ddof=1))
        rep["OS"] = {"seeds": len(opn), "vix_state0": sorted({round(h["vix_state0"], 2) for h in opn}),
                     "first_close_range": [min(v0), max(v0)]}
    if free:
        v0 = [h["vix"][0] for h in free]
        if not opn:
            rows["OS1"] = st.median(v0)
            rows["OS2"] = float(np.std(np.log(v0), ddof=1)) if len(v0) > 1 else float("nan")
        later = [h["vix"][k * YEAR] for h in free for k in range(1, len(h["vix"]) // YEAR)]
        if len(later) >= 5:
            d, p = ks_2samp(v0, later)
            rep["OS3_ks"] = {"D": d, "p": p, "n_open": len(v0), "n_year_starts": len(later)}
        rep["vix_state0"] = sorted({round(h["vix_state0"], 2) for h in free})
        burn = BURN if all(len(h["level"]) > 2 * YEAR for h in free) else 0
        sk = [s for h in free for s in year_skews(h["level"], burn)]
        rows["SK1"] = st.median(sk)
        dn = sum(tail_counts(h["level"], burn)[0] for h in free); up = sum(tail_counts(h["level"], burn)[1] for h in free)
        rows["SK2"] = dn / up if up else float("nan")
        rep["SK"] = {"model_years": len(sk), "burn": burn, "dn": dn, "up": up,
                     "pooled_skew": float(np.median([skew(np.diff(np.log(np.asarray(h["level"][burn:])))) for h in free]))}
        # ON1: year one from the opening (the certified horizon); G1 and G1c:
        # every session up to 2660, as the thirteenth registration read them.
        on1 = [overnight(h["close"][:YEAR + 1], h["open"][:YEAR + 1]) for h in free]
        on = [overnight(h["close"][:2661], h["open"][:2661]) for h in free]
        rows["ON1"] = st.median(o["share_median"] for o in on1)
        rows["G1"] = st.median(o["share_median"] for o in on)
        rows["G1c"] = st.median(o["ew_share"] for o in on)
        rep["ON"] = {"first_tick_sd_median": st.median(o["first_tick_sd"] for o in on),
                     "day_sd_median": st.median(o["day_sd"] for o in on),
                     "year1_ew_share": st.median(o["ew_share"] for o in on1)}
        # macro: monthly means after the burn year, 60-month windows
        U, O = [], []
        for h in free:
            um = monthly_means(h["unemployment"][burn:]); om = monthly_means(h["oil"][burn:])
            for s, e in windows_back(len(um), WINDOW_MONTHS):
                U.append(unemployment_window(um[s:e])); O.append(oil_window(om[s:e]))
            if not windows_back(len(um), WINDOW_MONTHS):   # short local run: one partial window
                U.append(unemployment_window(um)); O.append(oil_window(om))
                rep["macro_partial_window_months"] = len(um)
        for k in ("U1", "U2", "U3", "U4"):
            rows[k] = st.median(w[k] for w in U)
        for k in ("O1", "O2", "O3", "O4"):
            rows[k] = st.median(w[k] for w in O)
        allu = np.concatenate([np.asarray(h["unemployment"][burn:]) for h in free])
        rep["unemployment_share_at_2.5_floor"] = float((allu <= 2.5 + 1e-6).mean())
        allo = np.concatenate([np.asarray(h["oil"][burn:]) for h in free])
        rep["oil_share_within_0.5pct_of_150_or_36.8"] = float(((allo >= 150 * 0.995) | (allo <= 36.8 * 1.005)).mean())
        rep["free"] = {"histories": len(free), "sessions": len(free[0]["level"])}
    mo = [r for f in _load(adir, "mo") for r in f]
    if mo:
        sys.path.insert(0, str(HERE))
        import metaorder_curve as mc
        s = mc.rows_statistics(mo)
        for k, v in s["Q"].items():
            rows[k] = v
        rows["MO1"] = s["curves"]["is_"]["day"]["exponent"]
        rep["MO_curves_is"] = {k: v["by_f"] for k, v in s["curves"]["is_"].items()}
        rep["MO_sliced_over_block"] = s["sliced_over_block"]
        # XN2: the book path, day TWAP at 10%, across the names measured
        tf = _engine(None)
        u = tf.Universe.random(ROSTER[0], seed=ROSTER[1])
        dv = {i: u[i].avg_volume * u[i].initial_price for i in range(len(u))}
        sl = []
        for seed in sorted({r["seed"] for r in mo}):
            pts = [(math.log(dv[r["name"]]), math.log(r["is_"])) for r in mo
                   if r["seed"] == seed and r["sched"] == "day" and r["f"] == 0.1 and r["is_"] > 0]
            if len(pts) >= 3:
                sl.append(ols_slope(*zip(*pts)))
        rows["XN2"] = st.median(sl) if sl else float("nan")
    xn = _load(adir, "xn")
    if xn:
        # XN1 is the slope over names whose impact is positive; a name at or
        # below zero (a move under the cent grid, #166) cannot take a log, so
        # the row also fails when more than a tenth of the names read so:
        # under any cited law 10% of a day's volume moves every name by a
        # tenth of a daily sigma or more.
        sl, q41, zero, lvl, n = [], [], 0, [], 0
        for r in xn:
            imp, dv = np.asarray(r["impact_sigma"]), np.asarray(r["dollar_volume"])
            ok = imp > 0
            zero += int((~ok).sum()); n += len(imp)
            sl.append(ols_slope(np.log(dv[ok]), np.log(imp[ok])))
            o = np.argsort(dv); k = max(1, len(o) // 4)
            q41.append(float(np.median(imp[o[:k]]) / max(1e-12, np.median(imp[o[-k:]]))))
            lvl.append(float(np.median(imp)))
        rows["XN1"] = st.median(sl) if zero <= 0.1 * n else float("nan")
        rows["XN3"] = st.median(lvl)
        rep["XN1"] = {"seeds": len(xn), "slope_ignoring_non_positive": st.median(sl),
                      "non_positive": zero, "names": n,
                      "least_over_most_liquid_quartile_median": st.median(q41)}
    ac = [r for f in _load(adir, "ac") for r in f]
    if ac:
        n = len(ac[0]["is_by_days"])
        rat = {k: st.median(r["is_by_days"][k - 1] / r["is_by_days"][0] for r in ac if r["is_by_days"][0] > 0)
               for k in (1, 3, 5, n)}
        rows["AC1"] = rat[n]
        rep["AC1_curve"] = rat
        rep["AC1_displacement_per_day_ratio"] = st.median(
            (r["displacement_by_days"][-1] / n) / r["displacement_by_days"][0] for r in ac if r["displacement_by_days"][0] > 0)
        rows["AC2"] = st.median(r["coimpact_many"] / r["coimpact_one"] for r in ac if r["coimpact_one"] > 0)
    return rows, rep


#: (words, low, high, source key) for the rows with a band; the bands of the
#: real-derived rows are read from real.json, the others are stated here
#: with their derivation in CRITERIA-pt-v21.md.
STATED = {
    "Q1": ("print peak exponent in f, half-day", 0.4, 0.7),
    "Q2": ("print peak at 10% / sqrt(0.1)", 0.3, 1.0),
    "Q3": ("share of final s displacement at halfway, day TWAP 10%", 0.62, 0.82),
    "Q4": ("close / peak, half-day, f >= 3%", 0.55, 0.80),
    "Q5": ("next close / peak", 0.40, 0.70),
    "Q6": ("IS of a day TWAP at 10% ADV, daily sigma", 0.10, 0.21),
    "Q7": ("IS day TWAP / IS one-hour TWAP at 10%", 0.5, 0.9),
    "Q8": ("IS day TWAP / IS block at 10%", 0.5, 0.8),
    "Q9": ("IS day TWAP / IS block at 3%", 0.5, 0.8),
    "MO1": ("exponent of the day TWAP's IS in f, 1-30% ADV", 0.4, 0.7),
    "G1": ("overnight share of name variance, median name, to 2660 sessions", 0.30, 0.48),
    "G1c": ("overnight share of the EW index variance, to 2660 sessions", 0.30, 0.60),
    "XN1": ("flow path: slope of log impact/sigma on log dollar volume", -1 / 3, 1 / 3),
    "XN3": ("flow path: median close displacement at 10% ADV, daily sigma", 0.095, 0.32),
    "XN2": ("book path: slope of log IS/sigma on log dollar volume, day TWAP 10%", -1 / 3, 1 / 3),
    "AC1": ("per-share IS of a 12-day program over a 1-day one, 10% ADV a day", 1.8, 5.0),
    "AC2": ("co-impact: per-share IS split across 4 labels over one label", 0.8, 1.25),
}
WORDS = {
    "OS1": "median VIX at the first close of a history", "OS2": "sd of log VIX at the first close",
    "SK1": "median one-year skew of daily index log returns", "SK2": "index sessions under -3% over over +3% (log)",
    "U1": "unemployment: share of months within 0.2 pt of the 5-year low", "U2": "unemployment: 5-year range, points",
    "U3": "unemployment: lag-12 autocorrelation of the monthly level", "U4": "unemployment: sd of the 12-month change, points",
    "O1": "oil: 5-year log range", "O2": "oil: share of months within 2% of the 5-year high or low",
    "O3": "oil: lag-12 autocorrelation of the monthly log price", "O4": "oil: sd of the 12-month log change", "ON1": "overnight share of name variance, one year from the opening",
}
ORDER = ["MO1", "Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7", "Q8", "Q9", "OS1", "OS2", "ON1", "G1", "G1c",
         "U1", "U2", "U3", "U4", "O1", "O2", "O3", "O4", "SK1", "SK2", "XN1", "XN3", "XN2", "AC1", "AC2"]


def bands(realj):
    b = {k: (w, lo, hi) for k, (w, lo, hi) in STATED.items()}
    for k in ("OS1", "OS2"):
        b[k] = (WORDS[k], realj["OS"][k]["low"], realj["OS"][k]["high"])
    for k in ("SK1", "SK2"):
        b[k] = (WORDS[k], realj["SK"][k]["low"], realj["SK"][k]["high"])
    for g, ks in (("U", ("U1", "U2", "U3", "U4")), ("O", ("O1", "O2", "O3", "O4"))):
        for k in ks:
            b[k] = (WORDS[k], realj[g][k]["low"], realj[g][k]["high"])
    b["ON1"] = (WORDS["ON1"], realj["ON"]["ON1"]["low"], realj["ON"]["ON1"]["high"])
    return b


def grade(a):
    realj = json.load(open(a.real))
    B = bands(realj)
    out = {}
    for adir in a.dirs:
        rows, rep = model_rows(adir)
        meta = json.load(open(pathlib.Path(adir) / "meta.json"))
        print(f"== {meta['arm']} on {meta['base']} ({meta['fingerprint']}), tradefloor {meta['tradefloor']}")
        res = {}
        for k in ORDER:
            if k not in rows:
                continue
            w, lo, hi = B[k]
            v = rows[k]
            ok = v == v and (lo is None or v >= lo - 1e-12) and (hi is None or v <= hi + 1e-12)
            res[k] = {"value": v, "low": lo, "high": hi, "pass": bool(ok)}
            band = f"[{lo:.3f}, {hi:.3f}]" if hi is not None else f">= {lo:.3f}"
            print(f"  {k:4s} {w[:62]:62s} {v:8.3f}  {band:18s} {'pass' if ok else 'FAIL'}")
        print("  reported:", json.dumps(rep, default=float)[:2000])
        out[meta["arm"]] = {"meta": meta, "rows": res, "reported": rep}
    if a.json:
        json.dump(out, open(a.json, "w"), indent=1, default=float)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("real"); r.add_argument("--out", default=str(DATA / "real.json"))
    m = sub.add_parser("measure")
    m.add_argument("--arm", default="pt-v20"); m.add_argument("--arm-file")
    m.add_argument("--seeds", default="201-203"); m.add_argument("--years", type=int, default=3)
    m.add_argument("--parts", default="free,mo,xn,ac"); m.add_argument("--names", type=int, default=12)
    m.add_argument("--workers", type=int, default=3); m.add_argument("--engine")
    m.add_argument("--out", required=True)
    g = sub.add_parser("grade"); g.add_argument("dirs", nargs="+")
    g.add_argument("--real", default=str(DATA / "real.json")); g.add_argument("--json")
    a = ap.parse_args()
    {"real": real, "measure": measure, "grade": grade}[a.cmd](a)


if __name__ == "__main__":
    main()
