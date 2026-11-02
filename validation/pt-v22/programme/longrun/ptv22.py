"""ptv22 -- the checks pt-v22 registers before any derivative is built
(CRITERIA-pt-v22.md): futures (phase 1), index options (phase 2) and
single-stock options (phase 3). One implementation of each statistic, run on
the real series and on the simulator's output alike, as ptv21.py does.

    python ptv22.py real [--out data/ptv22/real.json]
        the real side: every row's real value and band from the frozen
        series in data/ptv22/ (README there), t(n) for the fixed band rule
        re-solved on the engine's own null, and the ES=F close-time finding
    python ptv22.py measure --arm NAME[@BASE][:dial=v,...] --seeds 201-203 \\
        [--years 21] [--parts free,chain] [--workers 3] [--engine PATH] --out DIR
        the model side, per seed, through the derivatives API the plan
        specifies (Engine.index_level, contracts(), quote(), chain(),
        surface(), settlements(), forecast()); it stops with a clear message
        on an engine that does not have it yet; --parts traded,push,metaorder
        [--switches phase1] runs the ptv22traded stage (ptv22traded.py:
        MG2, XD1 to XD3 and IF4 on traded accounts, MG3, XD4, XD5 and AX2
        once options are listed)
    python ptv22.py grade DIR [--real data/ptv22/real.json] [--json OUT]
        every row against its band

Nothing here starts a box. The real side needs only numpy; the frozen
inputs were written by ptv22_fetch.py.
"""
import argparse
import csv
import datetime as dt
import gzip
import json
import math
import pathlib
import random
import statistics as st
import sys
import time
from concurrent.futures import ProcessPoolExecutor

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
DATA = HERE / "data" / "ptv22"
ROSTER = (40, 111)
YEAR = 252
MONTH = 21
QUARTER = 63
BOOT_B = 2000
BOOT_SEED = 20261007

#: The fixed band rule of facts.py: median +/- t(n) * trimmed sd, t(n) solved
#: so a fresh reading from the same law falls outside 0.06486 of the time, on
#: facts.band_rule_fixed_false_alarm's null (n + 1 iid normals, seed
#: 20260905, 200,000 draws). The engine's table; solve_t() re-derives it and
#: every other count this file needs.
FIXED_TOLERANCE = 0.06486
FACTS_T = {5: 5.039474, 7: 3.483891, 9: 2.982334, 16: 2.417688, 35: 2.111347}

#: Yahoo's ES=F daily close is CME's daily settlement, set at 16:00 New York
#: from trade date 2020-10-26 (CME SER-8591) and at 16:15 before it
#: (real.json IF.close_time has the evidence). IF1 and IF2 read the aligned
#: span only.
IF_ALIGNED_FROM = "2020-10-26"
#: CFE quoted VX at ten times the VIX until this trade date (exclusive).
VX_RESCALE_BEFORE = "2007-03-26"
#: The archive files hold expiries up to 2014-08; the per-expiry files from
#: 2013-01. Where both hold a contract, the archive wins (the per-expiry files
#: carry no settlement before 2013-05-20).
VX_ARCHIVE_THROUGH = "2014-08-20"


# ====================================================================== maths

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


_T_CACHE = {}


def solve_t(n, draws=200_000, seed=20260905, target=FIXED_TOLERANCE):
    """t(n) for the fixed rule, on facts.band_rule_fixed_false_alarm's draws
    exactly (same generator, same order: n window values, then the fresh
    one, per draw), solved as the quantile of |fresh - median| /
    trimmed_sd that leaves `target` above it. ptv21.solve_t with the
    median and trimmed sd taken in numpy; it reproduces the engine's table
    (FACTS_T) to six digits, which the tests check."""
    if (n, draws, seed) in _T_CACHE:
        return _T_CACHE[(n, draws, seed)]
    rng = random.Random(seed)
    g = rng.gauss
    x = np.array([g(0.0, 1.0) for _ in range(draws * (n + 1))]).reshape(draws, n + 1)
    w, fresh = x[:, :n], x[:, n]
    c = np.median(w, axis=1)
    if n >= 3:
        far = np.argmax(np.abs(w - c[:, None]), axis=1)
        keep = np.ones_like(w, bool)
        keep[np.arange(draws), far] = False
        s = w[keep].reshape(draws, n - 1).std(axis=1, ddof=1)
    else:
        s = w.std(axis=1, ddof=1)
    z = np.sort(np.abs(fresh - c) / s)
    k = int(math.ceil(draws * (1 - target))) - 1
    t = float(z[k])
    out = (t, float((z > t).mean()))
    _T_CACHE[(n, draws, seed)] = out
    return out


def fixed_band(values, label=None):
    """The fixed rule on per-window values: median +/- t(n) * trimmed sd."""
    values = [float(v) for v in values if v == v]
    n = len(values)
    t = solve_t(n)[0]
    c, s = st.median(values), trimmed_sd(values)
    out = {"rule": "fixed", "centre": c, "trimmed_sd": s, "t": t, "low": c - t * s, "high": c + t * s,
           "n": n, "min": min(values), "max": max(values), "windows": values}
    if label is not None:
        out["window_labels"] = label
    return out


def boot_se(units, stat, B=BOOT_B, seed=BOOT_SEED):
    """sd of `stat` over B resamples of whole units (calendar years)."""
    rng = np.random.default_rng(seed)
    v = list(units); n = len(v)
    out = [stat([v[i] for i in rng.integers(0, n, n)]) for _ in range(B)]
    return float(np.std(out, ddof=1))


def boot_band(units, stat, labels=None):
    """Real +/- 2 bootstrap se: `stat` on every unit pooled, se from
    resampling whole units."""
    real = stat(list(units))
    se = boot_se(units, stat)
    out = {"rule": "2se", "real": real, "se": se, "low": real - 2 * se, "high": real + 2 * se, "units": len(units)}
    if labels is not None:
        out["unit_labels"] = [labels[0], labels[-1]]
    return out


def windows_back(n, w):
    """Non-overlapping windows of w consecutive points anchored at the LAST
    point and walking back; the remainder at the start is dropped (the anchor
    rule of facts.INDEX_TAIL_WINDOWS)."""
    k = n // w
    start = n - k * w
    return [(start + i * w, start + (i + 1) * w) for i in range(k)]


def ols(x, y):
    """(intercept, slope) of y on x; NaN when x does not vary."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    if len(x) < 2 or not np.ptp(x) > 0:
        return float("nan"), float("nan")
    b, a = np.polyfit(x, y, 1)
    return float(a), float(b)


def corr(x, y):
    x, y = np.asarray(x, float), np.asarray(y, float)
    return float(np.corrcoef(x, y)[0, 1])


def max_drawdown(level):
    """Worst peak-to-trough fall of a level series, as a positive fraction."""
    x = np.asarray(level, float)
    peak = np.maximum.accumulate(x)
    return float((1 - x / peak).max())


def realised_vol(r):
    """Annualised volatility, per cent, from daily log returns about zero."""
    r = np.asarray(r, float)
    return float(100 * math.sqrt(YEAR * (r * r).mean()))


def by_year(dates, values):
    """{year: [values]} for calendar years."""
    out = {}
    for d, v in zip(dates, values):
        out.setdefault(d[:4], []).append(v)
    return out


def full_years(groups, lo, hi):
    return [(y, groups[y]) for y in sorted(groups) if lo <= y <= hi]


# ================================================================= frozen data

def _rows(path):
    op = gzip.open(path, "rt", newline="") if str(path).endswith(".gz") else open(path, newline="")
    with op as fh:
        return [r for r in csv.reader(fh) if r]


def _iso(d):
    d = d.strip()
    if "/" in d:
        m, dd, y = d.split("/")
        return f"{int(y):04d}-{int(m):02d}-{int(dd):02d}"
    return d


def cboe(name):
    """(dates, closes) of a CBOE index history; files are DATE,OPEN,HIGH,LOW,CLOSE
    or DATE,<NAME>."""
    rows = _rows(DATA / "cboe" / f"{name}_History.csv")
    head = [h.strip().upper() for h in rows[0]]
    k = head.index("CLOSE") if "CLOSE" in head else len(head) - 1
    out = [(_iso(r[0]), float(r[k])) for r in rows[1:] if r[k].strip()]
    return [d for d, _ in out], np.array([v for _, v in out])


def fred(name):
    """{date: value} of a FRED series, missing values ('.') dropped."""
    return {r[0]: float(r[1]) for r in _rows(DATA / "fred" / f"{name}.csv")[1:] if r[1] not in (".", "")}


def eia(name):
    rows = _rows(DATA / "eia" / f"{name}.csv")[1:]
    return [r[0] for r in rows], np.array([float(r[1]) for r in rows])


def yahoo(name):
    """Daily bars: (dates, {field: array}) with adjclose and volume."""
    rows = _rows(DATA / "yahoo" / f"{name}.csv.gz")
    head = rows[0]
    cols = {h: np.array([float(r[i]) if r[i] else np.nan for r in rows[1:]]) for i, h in enumerate(head) if h != "date"}
    return [r[0] for r in rows[1:]], cols


def acm():
    rows = _rows(DATA / "nyfed" / "ACM_monthly.csv")
    head = rows[0]
    return [r[0] for r in rows[1:]], {h: np.array([float(r[i]) for r in rows[1:]]) for i, h in enumerate(head) if h != "date"}


def to_map(dates, values):
    return dict(zip(dates, (float(v) for v in values)))


def bill_cc(discount_pct, days=91):
    """A T-bill's bank-discount quote (per cent) as a continuously compounded
    yield, decimal, ACT/365."""
    p = 1 - discount_pct / 100 * days / 360
    return -math.log(p) * 365 / days


def asof(series, dates):
    """series {date: v}: for each date the last value on or before it."""
    keys = sorted(series)
    out, j, last = [], 0, float("nan")
    for d in dates:
        while j < len(keys) and keys[j] <= d:
            last = series[keys[j]]
            j += 1
        out.append(last)
    return np.array(out)


# ============================================================ IF: index futures

def quarterly_expiries(sessions, years=range(1999, 2029)):
    """Index futures expire at the opening print of the third Friday of March,
    June, September and December; when that day is not a session the expiry
    moves to the session before it (Juneteenth 2026, Good Friday)."""
    sset, out = set(sessions), []
    for y in years:
        for m in (3, 6, 9, 12):
            d = dt.date(y, m, 15)
            while d.weekday() != 4:
                d += dt.timedelta(1)
            if d.isoformat() <= sessions[-1]:
                while d.isoformat() not in sset:
                    d -= dt.timedelta(1)
            out.append(d.isoformat())
    return out


def if_window(basis_bp, tau, adjacent):
    """One window of IF1 and IF2. The basis (front future over its carry fair
    value, log, bp) is regressed on time to expiry with an intercept; the
    slope absorbs a financing rate that differs from the one used for the
    fair value by a constant (real: the ES implied repo over the bill rate;
    model: none), so it is removed on both sides. IF1 is the residuals' sd,
    IF2 their lag-1 autocorrelation over pairs of adjacent sessions
    (`adjacent[i]` is True when observation i follows i-1 by one session)."""
    b, t = np.asarray(basis_bp, float), np.asarray(tau, float)
    a, s = ols(t, b)
    e = b - a - s * t
    adj = np.asarray(adjacent, bool)
    idx = np.where(adj[1:])[0] + 1
    return {"IF1": float(e.std(ddof=2)), "IF2": corr(e[idx - 1], e[idx]), "mean": float(b.mean()),
            "slope_bp_per_year": s, "n": int(len(b))}


def es_basis():
    """The real front-future basis, every session from Yahoo's first ES=F bar:
    F the ES=F close; S the S&P 500 close; dividends the S&P 500 total-return
    index over the price index, D_u = S_(u-1) TR_u / TR_(u-1) - S_u index
    points going ex on session u, taken as known (they are declared, or
    nearly, a quarter ahead); r the 3-month bill (FRED DTB3) as a continuously
    compounded rate; fair value (S - PV(D in (t, T])) exp(r tau), tau ACT/365.
    Sessions within six of an expiry (the model's roll window) and the expiry
    session itself (Yahoo prints the final settlement there) are dropped: on
    every other session Yahoo's ES=F is the contract expiring at the next
    quarterly expiry (checked: the basis is continuous through each roll)."""
    sd, s = yahoo("GSPC"); td, tr = yahoo("SP500TR"); ed, es = yahoo("ESF")
    S, TR, F = to_map(sd, s["close"]), to_map(td, tr["close"]), to_map(ed, es["close"])
    bill = fred("DTB3")
    days = [d for d in sd if d in TR]
    idx = {d: i for i, d in enumerate(days)}
    D = {b: S[a] * TR[b] / TR[a] - S[b] for a, b in zip(days[:-1], days[1:])}
    exp = quarterly_expiries(days)
    rates = asof(bill, days)
    rows = []
    for d in days:
        if d not in F:
            continue
        T = min(x for x in exp if x >= d)
        if T > days[-1]:
            continue
        n = idx[T] - idx[d]
        if n <= 6:
            continue
        r = bill_cc(rates[idx[d]])
        d0 = dt.date.fromisoformat(d)
        tau = (dt.date.fromisoformat(T) - d0).days / 365
        pv = sum(D[u] * math.exp(-r * (dt.date.fromisoformat(u) - d0).days / 365) for u in days[idx[d] + 1: idx[T] + 1])
        fv = (S[d] - pv) * math.exp(r * tau)
        rows.append({"date": d, "expiry": T, "n": n, "tau": tau, "basis_bp": 1e4 * math.log(F[d] / fv),
                     "i": idx[d]})
    return rows


def _if_windows(rows, w):
    out = []
    for a, b in windows_back(len(rows), w):
        part = rows[a:b]
        adj = [False] + [part[k]["i"] == part[k - 1]["i"] + 1 for k in range(1, len(part))]
        r = if_window([x["basis_bp"] for x in part], [x["tau"] for x in part], adj)
        r.update({"from": part[0]["date"], "to": part[-1]["date"]})
        out.append(r)
    return out


def es_close_time():
    """Is Yahoo's ES=F daily close the 16:00 New York print? Two reads.
    (1) Intraday: the daily close against the last 5-minute print before
    16:00 and before 17:00 New York, over the 60 sessions Yahoo serves
    (days where the intraday series has rolled to the next contract and the
    daily has not are left out: |daily - 16:00| above 20 points).
    (2) Daily, all years: the sd of the day-to-day change of log(ES/SPX) on
    sessions away from expiries, by year; a close after 16:00 adds the
    index's move after the bell to it, about a fifth of a daily sd for 15
    minutes."""
    ed, es = yahoo("ESF")
    daily = to_map(ed, es["close"])
    rows = _rows(DATA / "yahoo" / "ESF-5m.csv.gz")[1:]
    bars = {r[0]: float(r[4]) for r in rows if r[4]}
    cmp_ = []
    for d in sorted({k[:10] for k in bars}):
        if d not in daily:
            continue
        p16, p17 = bars.get(f"{d}T15:55"), bars.get(f"{d}T16:55")
        if p16 is None or p17 is None:
            continue
        cmp_.append((d, daily[d] - p16, daily[d] - p17))
    same = [x for x in cmp_ if abs(x[1]) <= 20]
    intraday = {"sessions": len(cmp_), "used": len(same), "left_out_contract_mismatch": [x[0] for x in cmp_ if abs(x[1]) > 20],
                "median_abs_daily_less_1600_pts": float(np.median([abs(x[1]) for x in same])),
                "median_abs_daily_less_1700_pts": float(np.median([abs(x[2]) for x in same])),
                "max_abs_daily_less_1600_pts": float(max(abs(x[1]) for x in same)),
                "max_abs_daily_less_1700_pts": float(max(abs(x[2]) for x in same)),
                "closer_to_1600": int(sum(abs(x[1]) < abs(x[2]) for x in same)),
                "closer_to_1700": int(sum(abs(x[1]) > abs(x[2]) for x in same))}
    sd, s = yahoo("GSPC")
    S = to_map(sd, s["close"])
    days = [d for d in sd if d in daily]
    idx = {d: i for i, d in enumerate(sd)}
    exp = set()
    for T in quarterly_expiries(sd):
        if T in idx:
            exp.update(sd[max(0, idx[T] - 10): idx[T] + 4])
    b = {d: math.log(daily[d] / S[d]) for d in days}
    yr = {}
    for a, c in zip(days[:-1], days[1:]):
        if a in exp or c in exp or idx[c] != idx[a] + 1:
            continue
        yr.setdefault(c[:4], []).append(1e4 * (b[c] - b[a]))
    by = {y: float(np.std(v)) for y, v in sorted(yr.items())}
    return {"intraday_5m": intraday, "daily_basis_change_sd_bp_by_year": by,
            "cme_notice": "CME SER-8591 (2020-09-22): daily settlement of E-mini S&P 500 futures determined at "
                          "15:00 CT (16:00 New York) from trade date 2020-10-26, previously 15:15 CT (16:15)",
            "aligned_from": IF_ALIGNED_FROM}


def real_if():
    rows = es_basis()
    aligned = [r for r in rows if r["date"] >= IF_ALIGNED_FROM]
    early = [r for r in rows if r["date"] < IF_ALIGNED_FROM]
    w = _if_windows(aligned, YEAR)
    out = {"source": "Yahoo ES=F, ^GSPC, ^SP500TR daily closes; FRED DTB3",
           "span": f"{aligned[0]['date']}..{aligned[-1]['date']}", "sessions": len(aligned), "windows": w,
           "IF1": fixed_band([x["IF1"] for x in w]), "IF2": fixed_band([x["IF2"] for x in w]),
           "half_year_windows": {k: fixed_band([x[k] for x in _if_windows(aligned, YEAR // 2)]) for k in ("IF1", "IF2")},
           "before_2020_10_26_not_aligned": {"span": f"{early[0]['date']}..{early[-1]['date']}",
                                             "windows": _if_windows(early, YEAR)},
           "close_time": es_close_time()}
    return out


# ================================================================ VF: VIX futures

def load_vx():
    """Monthly VX contracts: {expiry: {"dates": [...], "settle": array, "final": VRO}}.
    Cleaning, in order:
    1. Source: the archive file for expiries up to VX_ARCHIVE_THROUGH, the
       per-expiry file after (the per-expiry files for 2013 carry no
       settlement before 2013-05-20).
    2. Rows with no settlement (0 or blank) are dropped.
    3. The rescale: settlements on trade dates before 2007-03-26 are divided
       by ten. CFE quoted VX on ten times the VIX until then (Cboe DataShop:
       "Prior to 3/23/2007, VIX had a $100x multiplier. On 3/26/2007 we
       changed this multiplier to $1000x and divided the display price by
       10"; CFE rule filing 13800). The data agree: each of the 3,918
       settlements to 2007-03-23 is 7.2 to 16.4 times the VIX close, each of
       the 43,389 from 2007-03-26 0.29 to 2.02 times (bar two bad prints).
    4. A settlement more than a factor of two from the median of the
       contract's five settlements centred on it is a bad print and is
       dropped (2008-04-21 reads 1.00 on two contracts with the VIX at
       20.5); the list is in real.json.
    The row on the expiry date is the final settlement (the special opening
    quotation, VRO); it is kept as `final` and is not a live quote."""
    rows = _rows(DATA / "vx" / "vx_settlements.csv.gz")
    head = rows[0]
    k = {h: i for i, h in enumerate(head)}
    by = {}
    for r in rows[1:]:
        exp, kind = r[k["expiry"]], r[k["kind"]]
        if (kind == "archive") != (exp <= VX_ARCHIVE_THROUGH):
            continue
        s = r[k["settle"]].strip()
        if not s or float(s) <= 0:
            continue
        v = float(s) / (10.0 if r[k["trade_date"]] < VX_RESCALE_BEFORE else 1.0)
        by.setdefault(exp, {})[r[k["trade_date"]]] = v
    out, dropped, overlap = {}, [], {"rows": 0, "differ": 0}
    # agreement of the two sources where both hold a settlement
    alt = {}
    for r in rows[1:]:
        if r[k["kind"]] == "new" and r[k["expiry"]] <= VX_ARCHIVE_THROUGH and r[k["settle"]].strip() and float(r[k["settle"]]) > 0:
            alt[(r[k["expiry"]], r[k["trade_date"]])] = float(r[k["settle"]])
    for exp, m in sorted(by.items()):
        ds = sorted(m)
        v = np.array([m[d] for d in ds])
        keep = np.ones(len(v), bool)
        for i in range(len(v)):
            med = np.median(v[max(0, i - 2): i + 3])
            if abs(math.log(v[i] / med)) > math.log(2):
                keep[i] = False
                dropped.append((exp, ds[i], float(v[i]), float(med)))
            if (exp, ds[i]) in alt:
                overlap["rows"] += 1
                overlap["differ"] += int(abs(alt[(exp, ds[i])] - v[i]) > 0.0051)
        ds = [d for d, kk in zip(ds, keep) if kk]
        v = v[keep]
        final = float(v[-1]) if ds[-1] == exp else float("nan")
        live = [i for i, d in enumerate(ds) if d < exp]
        out[exp] = {"dates": [ds[i] for i in live], "settle": v[live], "final": final}
    return out, {"dropped_bad_prints": dropped, "archive_vs_per_expiry_overlap": overlap}


def session_counter(sessions):
    """n(d, T): sessions in (d, T]. Beyond the last real session, weekdays."""
    idx = {d: i for i, d in enumerate(sessions)}
    last = dt.date.fromisoformat(sessions[-1])

    def n(d, T):
        if d not in idx:          # a CFE session with no VIX (Good Friday 2015)
            return None
        if T in idx:
            return idx[T] - idx[d]
        if T > sessions[-1]:
            extra = int(np.busday_count(last + dt.timedelta(1), dt.date.fromisoformat(T) + dt.timedelta(1)))
            return len(sessions) - 1 - idx[d] + extra
        # an expiry that is not a session in this calendar: the last session before it
        j = int(np.searchsorted(np.array(sessions), T)) - 1
        return j - idx[d]
    return n


def vx_curve(contracts, sessions, max_gap_days=42):
    """Per session: the live monthly contracts in expiry order, as
    (expiry, sessions to expiry, settlement), cut at the first gap of more
    than `max_gap_days` between successive expiries, so F1, F2, F4 are always
    consecutive months (CFE listed some months only until late 2005)."""
    n = session_counter(sessions)
    by_date = {}
    for exp, c in contracts.items():
        for d, v in zip(c["dates"], c["settle"]):
            by_date.setdefault(d, []).append((exp, v))
    curve = {}
    for d in sessions:
        live = sorted(by_date.get(d, []))
        out = []
        for j, (exp, v) in enumerate(live):
            if j and (dt.date.fromisoformat(exp) - dt.date.fromisoformat(live[j - 1][0])).days > max_gap_days:
                break
            out.append((exp, n(d, exp), float(v)))
        if out:
            curve[d] = out
    return curve


def vf_session_stats(curve, vix, sessions):
    """Per-session pieces of VF1 to VF4, keyed by session, for pooling and
    for the bootstrap over years. vix: {session: VIX close}.
      contango: F2 > F1 (VF1); slope: ln(F2/F1) * 252 / (n2 - n1) (VF2);
      back30: F2 < F1 on sessions with the VIX at 30 or more (VF3);
      dF_k, dVIX: change of the k-th contract (the same contract on both
      sessions, k = 1, 2, 4) and of the VIX between adjacent sessions (VF4)."""
    out = {}
    for i, d in enumerate(sessions):
        c = curve.get(d)
        if not c or d not in vix:
            continue
        rec = {}
        if len(c) >= 2:
            rec["contango"] = c[1][2] > c[0][2]
            rec["slope"] = math.log(c[1][2] / c[0][2]) * YEAR / (c[1][1] - c[0][1])
            if vix[d] >= 30:
                rec["back30"] = c[1][2] < c[0][2]
        if i:
            p = sessions[i - 1]
            cp = curve.get(p)
            if cp and p in vix:
                for kk in (1, 2, 4):
                    if len(c) >= kk and len(cp) >= kk and c[kk - 1][0] == cp[kk - 1][0]:
                        rec[f"dF{kk}"] = (c[kk - 1][2] - cp[kk - 1][2], vix[d] - vix[p])
        out[d] = rec
    return out


def vf5_returns(curve, contracts, sessions, target=MONTH):
    """Daily excess return of a long position at a constant 21-session
    maturity: weights on F1 and F2 set at the previous close, w1 =
    (n2 - 21) / (n2 - n1) clipped to [0, 1]; a contract that expires
    overnight is valued at its final settlement. Returns {session: r}."""
    out = {}
    for i in range(1, len(sessions)):
        p, d = sessions[i - 1], sessions[i]
        cp = curve.get(p)
        if not cp or len(cp) < 2:
            continue
        (e1, n1, f1), (e2, n2, f2) = cp[0], cp[1]
        w1 = min(1.0, max(0.0, (n2 - target) / (n2 - n1)))
        now = {}
        for e in (e1, e2):
            c = contracts[e]
            if d == e:
                now[e] = c["final"]
            else:
                j = c.get("_ix", {}).get(d)
                now[e] = float(c["settle"][j]) if j is not None else None
        if now[e1] is None or now[e2] is None or now[e1] != now[e1]:
            continue
        base = w1 * f1 + (1 - w1) * f2
        out[d] = (w1 * (now[e1] - f1) + (1 - w1) * (now[e2] - f2)) / base
    return out


def vf6_errors(contracts, sessions, horizons=(21, 63, 126)):
    """For each contract and horizon h: settlement h sessions before expiry
    less the final settlement. {h: [(expiry, error)]}."""
    n = session_counter(sessions)
    out = {h: [] for h in horizons}
    for exp, c in contracts.items():
        if c["final"] != c["final"] or exp > sessions[-1]:
            continue
        at = {n(d, exp): v for d, v in zip(c["dates"], c["settle"]) if n(d, exp) is not None}
        for h in horizons:
            if h in at:
                out[h].append((exp, float(at[h]) - c["final"]))
    return out


def _pool_share(units, key):
    v = [x for u in units for x in u]
    return float(np.mean(v)) if v else float("nan")


def real_vf():
    contracts, clean = load_vx()
    for c in contracts.values():
        c["_ix"] = {d: i for i, d in enumerate(c["dates"])}
    vd, vv = cboe("VIX")
    vix = to_map(vd, vv)
    sessions = [d for d in vd if d >= "2004-03-26"]
    curve = vx_curve(contracts, sessions)
    ss = vf_session_stats(curve, vix, sessions)
    years = sorted({d[:4] for d in ss})
    ys = [y for y in years if y <= "2026"]
    U = {y: [r for d, r in ss.items() if d[:4] == y] for y in ys}
    out = {"source": "CFE VX settlements (Cboe), monthly contracts; Cboe VIX", "contracts": len(contracts),
           "span": f"{min(ss)}..{max(ss)}", "cleaning": {"dropped_bad_prints": clean["dropped_bad_prints"],
                                                          "archive_vs_per_expiry_overlap": clean["archive_vs_per_expiry_overlap"],
                                                          "rescaled_before": VX_RESCALE_BEFORE}}
    vf1 = [[r["contango"] for r in U[y] if "contango" in r] for y in ys]
    out["VF1"] = boot_band(vf1, lambda u: _pool_share(u, None), ys)
    vf2 = [[r["slope"] for r in U[y] if "slope" in r] for y in ys]
    out["VF2"] = boot_band(vf2, lambda u: float(np.median([x for a in u for x in a])), ys)
    y3 = [y for y in ys if any("back30" in r for r in U[y])]
    vf3 = [[r["back30"] for r in U[y] if "back30" in r] for y in y3]
    out["VF3"] = boot_band(vf3, lambda u: _pool_share(u, None), y3)
    out["VF3"]["sessions"] = sum(len(u) for u in vf3)
    for kk in (1, 2, 4):
        units = [[r[f"dF{kk}"] for r in U[y] if f"dF{kk}" in r] for y in ys]
        units = [u for u in units if len(u) > 20]
        out[f"VF4_F{kk}"] = boot_band(units, lambda u: ols([x[1] for a in u for x in a], [x[0] for a in u for x in a])[1])
    ret = vf5_returns(curve, contracts, sessions)
    rd = sorted(ret)
    w = windows_back(len(rd), YEAR)
    ann = [float(np.log1p(np.array([ret[d] for d in rd[a:b]])).sum()) for a, b in w]
    out["VF5"] = fixed_band(ann, [f"{rd[a]}..{rd[b - 1]}" for a, b in w])
    out["VF5"]["eraker_wu_2017"] = ("the constant maturity portfolio of 1-month VIX futures loses about 30% per year "
                                    "over our sample period (2006-2013)")
    errs = vf6_errors(contracts, sessions)
    for h, e in errs.items():
        yrs = sorted({x[0][:4] for x in e})
        units = [[x[1] for x in e if x[0][:4] == y] for y in yrs]
        out[f"VF6_h{h}"] = boot_band(units, lambda u: float(np.mean([x for a in u for x in a])), yrs)
        out[f"VF6_h{h}"]["contracts"] = len(e)
    return out


# ================================================================ RF: rate futures

#: Piazzesi and Swanson, "Futures prices as risk-adjusted forecasts of
#: monetary policy", Journal of Monetary Economics 55(4) 677-691, 2008,
#: Table 1 panel A: mean excess return on fed funds futures n months ahead,
#: unannualised bp (= futures rate less the realised average), and the HAC t
#: statistic; 1988:10 to 2005:12, month-end sampling. Read from the published
#: version (web.stanford.edu/~piazzesi/fff.pdf) by a reader on 2026-10-07.
PIAZZESI_SWANSON = {1: (2.9, 3.7), 2: (6.3, 3.4), 3: (10.5, 3.0), 4: (16.1, 2.9), 5: (23.2, 2.9), 6: (30.7, 2.7)}


def month_average(series, ym):
    """Calendar-day average of a daily FRED rate over month 'YYYY-MM'; None
    unless every calendar day is present (DFF is published for every day)."""
    y, m = map(int, ym.split("-"))
    n = (dt.date(y + (m == 12), m % 12 + 1, 1) - dt.date(y, m, 1)).days
    v = [series.get(f"{ym}-{d:02d}") for d in range(1, n + 1)]
    return sum(v) / n if all(x is not None for x in v) else None


def rf1_front(dates, rate, dff):
    """RF1 at one month, free data: on the first session of each month the
    front fed funds future (Yahoo ZQ=F holds the current month's contract
    for the whole month; checked: on the last session of a month it is
    within a median 0.1 bp of the month's average DFF, against 1.4 bp for
    the next month's) less the month's realised average DFF, bp."""
    first = {}
    for d, r in zip(dates, rate):
        first.setdefault(d[:7], r)
    out = []
    for ym, r in sorted(first.items()):
        a = month_average(dff, ym)
        if a is not None:
            out.append((ym, 100 * (r - a)))
    return out


def rf2_changes(dates, rate, meetings):
    """Daily change of the front policy-rate future, bp, split into scheduled
    decision sessions and other sessions. The first session of a month is
    left out (Yahoo's ZQ=F rolls there), as are unscheduled actions, calls
    and notation votes."""
    meet, other = [], []
    for i in range(1, len(dates)):
        d, p = dates[i], dates[i - 1]
        if d[:7] != p[:7]:
            continue
        k = meetings.get(d)
        if k and k != "scheduled":
            continue
        (meet if k == "scheduled" else other).append((d, 100 * (rate[i] - rate[i - 1])))
    return meet, other


def rms_ratio(meet, other):
    """RF2: root mean square (sd about zero) of the daily changes on meeting
    sessions over that on other sessions. About zero, because in a hiking
    or easing run every meeting moves the future the same way and an sd
    about the mean would call those moves small."""
    m, o = np.asarray(meet, float), np.asarray(other, float)
    return float(math.sqrt((m * m).mean() / (o * o).mean()))


def _sd_ratio(units):
    return rms_ratio([x for u in units for x in u[0]], [x for u in units for x in u[1]])


def real_rf():
    zd, z = yahoo("ZQF")
    rate = 100 - z["close"]
    dff = fred("DFF")
    out = {}
    e1 = rf1_front(zd, rate, dff)
    yrs = sorted({ym[:4] for ym, _ in e1})
    out["RF1_m1"] = boot_band([[x for ym, x in e1 if ym[:4] == y] for y in yrs],
                              lambda u: float(np.mean([x for a in u for x in a])), yrs)
    out["RF1_m1"]["source"] = "Yahoo ZQ=F (front fed funds future) on the first session of each month; FRED DFF"
    out["RF1_m1"]["months"] = len(e1)
    for h in (3, 6):
        m, t = PIAZZESI_SWANSON[h]
        out[f"RF1_m{h}"] = {"rule": "literature", "real": m, "se": m / t, "low": m - 2 * m / t, "high": m + 2 * m / t,
                            "source": "Piazzesi and Swanson (2008) Table 1 panel A, n = %d months, 1988:10-2005:12" % h}
    out["RF1_m12"] = {"rule": "reported", "note": "no verified published mean at 12 months; reported, not gated"}
    meetings = {r[0]: r[1] for r in _rows(DATA / "fomc" / "fomc_meetings.csv")[1:]}
    have = sorted({d[:4] for d, k in meetings.items() if k == "scheduled"})
    meet, other = rf2_changes(zd, rate, meetings)
    ys = [y for y in have if y <= zd[-1][:4]]
    units = [([x for d, x in meet if d[:4] == y], [x for d, x in other if d[:4] == y]) for y in ys]
    out["RF2"] = boot_band(units, _sd_ratio, ys)
    out["RF2"].update({"years": ys, "meeting_sessions": sum(len(u[0]) for u in units),
                       "other_sessions": sum(len(u[1]) for u in units),
                       "by_year": {y: _sd_ratio([u]) for y, u in zip(ys, units)},
                       "note": "FOMC dates from the Fed's calendar page (2021-2027) and its 2015 historical page; "
                               "the 2000-2014 and 2016-2020 pages could not be fetched at freeze time"})
    # RF4: the eight-quarter strip less the 2-year yield. No free source
    # holds the strip; in the ACM model the risk-neutral 2-year yield is the
    # expected average short rate, so a strip that prices the expected path
    # with no premium of its own reads ACMRNY02 - ACMY02 = -ACMTP02.
    ad, a = acm()
    x = 100 * (a["ACMRNY02"] - a["ACMY02"])
    w = windows_back(len(ad), 12)
    out["RF4"] = fixed_band([float(np.median(x[i:j])) for i, j in w], [f"{ad[i][:7]}..{ad[j - 1][:7]}" for i, j in w])
    out["RF4"].update({"source": "NY Fed ACM term structure model, monthly, ACMRNY02 and ACMY02",
                       "span": f"{ad[w[0][0]][:7]}..{ad[-1][:7]}",
                       "proxy": "the strip prices the expected path with no premium of its own: RF4 = -ACMTP02, bp; "
                                "statistic per 12-month window, the window's median month"})
    return out


# ================================================================== OF: oil futures

def cl_expiries(sessions):
    """NYMEX WTI: trading in a contract ends three business days before the
    25th calendar day of the month before delivery, four if the 25th is not a
    business day. Business days are the sessions of the series."""
    sset = set(sessions)
    out = []
    first, last = dt.date.fromisoformat(sessions[0]), dt.date.fromisoformat(sessions[-1])
    y, m = first.year, first.month
    while dt.date(y, m, 1) <= last:
        d25 = dt.date(y, m, 25)
        before, d = [], d25 - dt.timedelta(1)
        while len(before) < 4 and d >= first:
            if d.isoformat() in sset:
                before.append(d.isoformat())
            d -= dt.timedelta(1)
        if len(before) == 4:
            out.append(before[2] if d25.isoformat() in sset else before[3])
        y, m = y + (m == 12), m % 12 + 1
    return out


def of_window(c1, c4):
    """OF1 for one window: share of sessions with contract 1 above contract 4."""
    c1, c4 = np.asarray(c1, float), np.asarray(c4, float)
    return float((c1 > c4).mean())


def of2_window(slope, inv_dev):
    """OF2 for one window: correlation of the curve's slope, ln(F4/F1), with
    inventory's log deviation from its trailing five-year mean."""
    return corr(slope, inv_dev)


def of3_window(d1, d4):
    """OF3 for one window: OLS slope of contract 4's daily log change on
    contract 1's, over sessions where neither contract rolled."""
    return ols(d1, d4)[1]


def real_of():
    C = {}
    for k in (1, 2, 3, 4):
        d, v = eia(f"RCLC{k}")
        C[k] = dict(zip(d, v))
    days = sorted(set(C[1]) & set(C[4]))
    exp = cl_expiries(days)
    idx = {d: i for i, d in enumerate(days)}
    near = set()
    for e in exp:
        i = idx[e]
        near.update(days[max(0, i - 2): i + 4])        # expiry -2 .. +3: EIA's switch day is not documented to the session
    c1 = np.array([C[1][d] for d in days]); c4 = np.array([C[4][d] for d in days])
    out = {"source": "EIA NYMEX WTI futures contracts 1 and 4 (RCLC1, RCLC4), daily; EIA WCESTUS1 weekly",
           "span": f"{days[0]}..{days[-1]}", "sessions": len(days)}
    w = windows_back(len(days), 5 * YEAR)
    lab = [f"{days[a]}..{days[b - 1]}" for a, b in w]
    # OF1: the fixed rule over the seven 5-year windows has no bound (low
    # below 0, high above 1), so the gate is the whole-sample share with 2
    # bootstrap se over calendar years; the window band is reported.
    yrs = sorted({d[:4] for d in days})
    out["OF1"] = boot_band([list(c1[[i for i, d in enumerate(days) if d[:4] == y]] >
                                 c4[[i for i, d in enumerate(days) if d[:4] == y]]) for y in yrs],
                           lambda u: float(np.mean([x for a in u for x in a])), yrs)
    out["OF1"]["fixed_rule_5y_windows"] = fixed_band([of_window(c1[a:b], c4[a:b]) for a, b in w], lab)
    # OF3: daily log changes away from rolls; prices at or below zero (2020-04-20) cannot take a log
    pos = (c1 > 0) & (c4 > 0)
    of3 = []
    for a, b in w:
        ii = [i for i in range(max(a, 1), b) if pos[i] and pos[i - 1] and days[i] not in near and idx[days[i]] == idx[days[i - 1]] + 1]
        ii = np.array(ii)
        of3.append(of3_window(np.log(c1[ii] / c1[ii - 1]), np.log(c4[ii] / c4[ii - 1])))
    out["OF3"] = fixed_band(of3, lab)
    out["OF3"]["sessions_left_out_near_expiry"] = len(near)
    out["OF3"]["bessembinder_1995"] = ("point estimates indicate that 44 percent of a typical spot oil price shock is "
                                       "expected to be reversed over the subsequent eight months")
    # OF2: weekly, on each stock report's date (or the last session before it)
    sd, sv = eia("WCESTUS1")
    inv = np.log(sv)
    dev = inv[260:] - np.array([inv[i - 260:i].mean() for i in range(260, len(inv))])
    dd = sd[260:]
    arr = np.array(days)
    pts = []
    for d, x in zip(dd, dev):
        j = int(np.searchsorted(arr, d, "right")) - 1
        if j < 0 or d > days[-1] or not pos[j]:
            continue
        pts.append((d, math.log(c4[j] / c1[j]), float(x)))
    w2 = windows_back(len(pts), 260)
    py = sorted({p[0][:4] for p in pts})
    out["OF2"] = boot_band([[p for p in pts if p[0][:4] == y] for y in py],
                           lambda u: of2_window([p[1] for a in u for p in a], [p[2] for a in u for p in a]), py)
    out["OF2"]["weeks"] = len(pts)
    out["OF2"]["fixed_rule_5y_windows"] = fixed_band(
        [of2_window([p[1] for p in pts[a:b]], [p[2] for p in pts[a:b]]) for a, b in w2],
        [f"{pts[a][0]}..{pts[b - 1][0]}" for a, b in w2])
    out["OF2"]["inventory"] = "WCESTUS1, log deviation from the trailing 260-week mean"
    out["OF2"]["gorton_hayashi_rouwenhorst_2013"] = ("the slope of the futures curve becomes steeper as inventories "
                                                     "decline (convenience yield a decreasing, non-linear function of inventories)")
    return out


# ================================================================ SV: the index surface

def _year_units(dates, values, lo="0000", hi="9999"):
    g = by_year(dates, values)
    ys = [y for y in sorted(g) if lo <= y <= hi]
    return ys, [g[y] for y in ys]


def _pooled_median(u):
    return float(np.median([x for a in u for x in a]))


def _pooled_mean(u):
    return float(np.mean([x for a in u for x in a]))


def _pooled_sd(u):
    return float(np.std([x for a in u for x in a], ddof=1))


def ratio_series(num, den):
    """{date: num/den} on common dates."""
    return {d: num[d] / den[d] for d in num if d in den and den[d] > 0}


def vrp_ratios(vix_dates, vix, spx):
    """SV8 per session: the VIX over the realised volatility of the next 21
    sessions' index log returns, close to close, annualised about zero
    (per cent). spx {date: close}; sessions without 21 following index
    closes are dropped."""
    sd = [d for d in sorted(spx)]
    sidx = {d: i for i, d in enumerate(sd)}
    lv = np.log(np.array([spx[d] for d in sd]))
    r = np.diff(lv)
    out = {}
    for d, v in zip(vix_dates, vix):
        i = sidx.get(d)
        if i is None or i + MONTH >= len(sd):
            continue
        out[d] = float(v) / realised_vol(r[i:i + MONTH])
    return out


def vix_beta_pairs(vix_dates, vix, spx):
    """SV9 per session: (index log return, VIX log change) close to close."""
    vm = dict(zip(vix_dates, vix))
    sd = [d for d in sorted(spx) if d in vm]
    return {b: (math.log(spx[b] / spx[a]), math.log(vm[b] / vm[a])) for a, b in zip(sd[:-1], sd[1:])}


def real_sv():
    out = {}
    vd, vv = cboe("VIX")
    vix = to_map(vd, vv)
    for key, name, lo in (("SV3", "VIX3M", "2009"), ("SV5_9D", "VIX9D", "2011"), ("SV5_6M", "VIX6M", "2008"),
                          ("SV5_1Y", "VIX1Y", "2007")):
        d, v = cboe(name)
        rat = ratio_series(to_map(d, v), vix)
        ys, units = _year_units(sorted(rat), [rat[x] for x in sorted(rat)], lo, "2026")
        out[key] = boot_band(units, _pooled_median, ys)
        out[key]["series"] = f"Cboe {name} over VIX, {min(rat)}..{max(rat)}, {len(rat)} sessions"
        if key == "SV3":
            ys4, u4 = _year_units(sorted(rat), [1.0 / rat[x] > 1.0 for x in sorted(rat)], lo, "2026")
            out["SV4"] = boot_band(u4, _pooled_mean, ys4)
            out["SV4"]["series"] = f"share of sessions with the VIX above VIX3M, {min(rat)}..{max(rat)}"
    sd_, sv_ = cboe("SKEW")
    ys, units = _year_units(sd_, list(sv_), "1990", "2026")
    out["SV6_median"] = boot_band(units, _pooled_median, ys)
    out["SV6_sd"] = boot_band(units, _pooled_sd, ys)
    sk = to_map(sd_, sv_)
    common = [d for d in sd_ if d in vix]
    w = windows_back(len(common), YEAR)
    out["SV7"] = fixed_band([corr([sk[d] for d in common[a:b]], [vix[d] for d in common[a:b]]) for a, b in w],
                            [f"{common[a]}..{common[b - 1]}" for a, b in w])
    gd, g = yahoo("GSPC")
    spx = to_map(gd, g["close"])
    vr = vrp_ratios(vd, vv, spx)
    ys, units = _year_units(sorted(vr), [vr[x] for x in sorted(vr)], "1990", "2026")
    out["SV8"] = boot_band(units, _pooled_median, ys)
    out["SV8"]["series"] = f"Cboe VIX over the next 21 sessions' realised S&P 500 volatility, {min(vr)}..{max(vr)}"
    pr = vix_beta_pairs(vd, vv, spx)
    ys, units = _year_units(sorted(pr), [pr[x] for x in sorted(pr)], "1990", "2026")
    out["SV9"] = boot_band(units, lambda u: ols([x[0] for a in u for x in a], [x[1] for a in u for x in a])[1], ys)
    out["SV9"]["series"] = "OLS slope of the VIX's daily log change on the S&P 500's daily log return, 1990-2026"
    return out


# ============================================================ OB: option benchmarks

OB_INDICES = {"OB1": "PUT", "OB2": "BXM", "OB3": "CNDR", "OB4": "BFLY"}


def ob_window(strategy, underlying):
    """One window of an option benchmark against the underlying's total
    return over the same sessions: (r) annualised log return less the
    underlying's, (v) annualised volatility over the underlying's, (d) worst
    drawdown over the underlying's. Both are total-return levels (real: the
    Cboe index and the S&P 500 total return; model: the rule's account and
    the model's index with dividends reinvested). Cash earns the bill rate in
    the Cboe indices and nothing in the model; the return difference cancels
    it for the collateral both carry."""
    s, u = np.asarray(strategy, float), np.asarray(underlying, float)
    rs, ru = np.diff(np.log(s)), np.diff(np.log(u))
    k = YEAR / len(rs)
    return {"r": float(k * (rs.sum() - ru.sum())), "v": float(rs.std(ddof=1) / ru.std(ddof=1)),
            "d": max_drawdown(s) / max(max_drawdown(u), 1e-9),
            "abs_return": float(k * rs.sum()), "abs_vol": float(math.sqrt(YEAR) * rs.std(ddof=1)), "abs_dd": max_drawdown(s)}


def real_ob():
    """The plan's five-year windows leave three (PUT, daily from 2007) and four
    (BXM, from 2002) windows, where t(3) and t(4) give no usable band, so the
    windows are one year (252 sessions) for every index, anchored at the last
    session."""
    td, t = yahoo("SP500TR")
    tr = to_map(td, t["close"])
    out = {}
    for row, name in OB_INDICES.items():
        d, v = cboe(name)
        lo = "2007-01-01" if name == "PUT" else "0000"          # PUT is daily only from 2007
        m = to_map(d, v)
        common = [x for x in d if x in tr and x >= lo]
        w = windows_back(len(common), YEAR)
        wins = [ob_window([m[x] for x in common[a:b]], [tr[x] for x in common[a:b]]) for a, b in w]
        lab = [f"{common[a]}..{common[b - 1]}" for a, b in w]
        for k in ("r", "v", "d"):
            out[f"{row}{k}"] = fixed_band([x[k] for x in wins], lab)
        out[f"{row}_index"] = {"name": name, "span": f"{common[0]}..{common[-1]}",
                               "absolute_median": {k: float(np.median([x[k] for x in wins])) for k in ("abs_return", "abs_vol", "abs_dd")}}
    out["OB5"] = {"rule": "literature", "real": -0.03, "low": -0.06, "high": -0.015,
                  "source": "Coval and Shumway, 'Expected option returns', Journal of Finance 56(3) 983-1009, 2001, "
                            "abstract: 'zero-beta, at-the-money straddle positions produce average losses of "
                            "approximately three percent per week' (S&P index options); band a chosen factor of two "
                            "either side of -3%; sample years and standard error not verified"}
    out["OB6"] = {"rule": "signed", "real": -0.0010, "low": None, "high": 0.0,
                  "source": "Bakshi and Kapadia, 'Delta-hedged gains and the negative market volatility risk premium', "
                            "Review of Financial Studies 16(2) 527-566, 2003: S&P 500 index options 1988-1995, 14-60 "
                            "days, held to maturity with daily rebalancing; 'for at-the-money calls ... the strategy "
                            "loses about 0.10%' of the index level. Gate: the mean is below zero, one-sided p < 0.05 "
                            "over histories; the magnitude is reported against -0.10%"}
    return out


# ====================================================== SS, EV: single-stock options

SS_NAMES = {"AAPL": "VXAPL", "AMZN": "VXAZN", "GOOGL": "VXGOG", "GS": "VXGS", "IBM": "VXIBM"}
#: EDGAR names the five as AAPL, AMZN, GOOG (Alphabet at both CIKs), GS, IBM.
EDGAR_NAME = {"AAPL": "AAPL", "AMZN": "AMZN", "GOOGL": "GOOG", "GS": "GS", "IBM": "IBM"}


def iv_rv_ratios(dates, iv, closes):
    """SS1 per session: 30-day implied volatility (per cent) over the next 21
    sessions' realised volatility of the name's daily log returns (per cent,
    about zero). closes {date: adjusted close}."""
    sd = sorted(closes)
    sidx = {d: i for i, d in enumerate(sd)}
    r = np.diff(np.log(np.array([closes[d] for d in sd])))
    out = {}
    for d, v in zip(dates, iv):
        i = sidx.get(d)
        if i is None or i + MONTH >= len(sd):
            continue
        rv = realised_vol(r[i:i + MONTH])
        if rv > 0:
            out[d] = float(v) / rv
    return out


def implied_correlation(index_var, name_var, w):
    """Cboe's implied correlation: (sigma_I^2 - sum w_i^2 sigma_i^2) /
    (sum_{i != j} w_i w_j sigma_i sigma_j), variances annualised, weights
    summing to one. The same formula reads realised correlation from
    realised variances."""
    w = np.asarray(w, float); s = np.sqrt(np.asarray(name_var, float))
    num = index_var - (w * w * s * s).sum()
    den = (w * s).sum() ** 2 - (w * w * s * s).sum()
    return float(num / den)


def realised_correlation(R):
    """Realised correlation of a window of daily log returns [days, names],
    equal weights, by the implied-correlation formula on the equal-weighted
    portfolio."""
    R = np.asarray(R, float)
    n = R.shape[1]
    w = np.full(n, 1.0 / n)
    port = R @ w
    return implied_correlation(port.var(ddof=1), R.var(axis=0, ddof=1), w)


def earnings_events(rows, sessions):
    """Reaction sessions from 8-K item 2.02 filings: the first session whose
    open follows the filing (a filing accepted at or after 16:00 New York
    reacts on the next session; one accepted before it, on that session's
    own day, since issuers release before the open and file during the day).
    A name's filings within 20 days of the first of a cluster are one event
    (IBM filed twice a quarter until 2016; Alphabet filed a segment recast
    five days before its April 2023 report); the event is the cluster's
    first filing whose items are among 2.02, 7.01 and 9.01, else its first."""
    arr = np.array(sessions)
    plain = {"2.02", "7.01", "9.01"}
    clusters = []
    for r in sorted(rows, key=lambda x: (x[0], x[5])):
        name, ny, items = r[0], r[5], set(r[6].split(","))
        c = clusters[-1] if clusters else None
        if c and c[0] == name and (dt.date.fromisoformat(ny[:10]) - dt.date.fromisoformat(c[1][0][0][:10])).days < 20:
            c[1].append((ny, items))
        else:
            clusters.append((name, [(ny, items)]))
    ev = []
    for name, fs in clusters:
        ny = next((x for x, it in fs if it <= plain), fs[0][0])
        day, hhmm = ny[:10], ny[11:16]
        j = int(np.searchsorted(arr, day, "left"))
        if hhmm >= "16:00" and j < len(arr) and arr[j] == day:
            j += 1
        if j < len(arr):
            ev.append((name, arr[j], ny))
    return ev


def ev_event(iv, close, sessions, react, pre=15, T=30 / 365):
    """EV1 to EV3 for one event. iv, close {session: value}; react the
    reaction session R. EV1 ln IV(R) - ln IV(R-1); EV2 ln IV(R-1) -
    ln IV(R-1-pre); implied event move m = sqrt(max(0, IV(R-1)^2 -
    IV(R)^2) T) (the term-structure decomposition of Dubinsky, Johannes,
    Kaeck and Seeger with one 30-day maturity), realised move |ln C(R) /
    C(R-1)|."""
    i = sessions.index(react) if react in sessions else None
    if i is None or i - 1 - pre < 0:
        return None
    a, b, c = sessions[i - 1 - pre], sessions[i - 1], sessions[i]
    if not all(x in iv for x in (a, b, c)) or b not in close or c not in close:
        return None
    s0, s1 = iv[b] / 100, iv[c] / 100
    return {"EV1": math.log(iv[c] / iv[b]), "EV2": math.log(iv[b] / iv[a]),
            "implied": math.sqrt(max(0.0, (s0 * s0 - s1 * s1) * T)), "realised": abs(math.log(close[c] / close[b]))}


def ev3_ratio(evs):
    """EV3 over a set of events: root mean square implied event move over
    root mean square realised reaction move (1 when the implied event
    variance is unbiased)."""
    m = np.array([e["implied"] for e in evs]); r = np.array([e["realised"] for e in evs])
    return float(math.sqrt((m * m).mean() / (r * r).mean()))


def real_ss():
    out = {}
    ss1 = {}
    stock = {}
    for tk, ix in SS_NAMES.items():
        d, v = cboe(ix)
        yd, y = yahoo(tk)
        stock[tk] = to_map(yd, y["adjclose"])
        ss1[tk] = iv_rv_ratios(d, v, stock[tk])
    pooled = {}
    for tk, m in ss1.items():
        for d, x in m.items():
            pooled.setdefault(d[:4], []).append(x)
    ys = [y for y in sorted(pooled) if "2011" <= y <= "2025"]
    out["SS1"] = fixed_band([float(np.median(pooled[y])) for y in ys], ys)
    out["SS1"]["series"] = "Cboe VXAPL, VXAZN, VXGOG, VXGS, VXIBM over the next 21 sessions' realised volatility of AAPL, AMZN, GOOGL, GS, IBM (Yahoo, adjusted); per calendar year the median over name-days"
    cd, cv = cboe("COR3M")
    ys2, u2 = _year_units(cd, list(cv), "2006", "2026")
    out["SS2"] = boot_band(u2, _pooled_median, ys2)
    out["SS2"]["series"] = f"Cboe COR3M, {cd[0]}..{cd[-1]}, index points (correlation x 100)"
    # SS3: COR3M less the certified forty's realised correlation over the next 63 sessions
    rc = json.load(open(HERE.parent.parent / "corpus" / "civ" / "real_closes.json"))
    names = list(rc)
    stamps = rc[names[0]]["ts"]
    assert all(rc[n]["ts"] == stamps for n in names)
    fd = [dt.datetime.fromtimestamp(t, dt.timezone.utc).date().isoformat() for t in stamps]
    P = np.log(np.array([rc[n]["adj"] for n in names]).T)
    R = np.diff(P, axis=0)
    cor = to_map(cd, cv)
    gaps = []
    for i, d in enumerate(fd[:-1]):
        if d in cor and i + QUARTER <= len(R):
            gaps.append((d, cor[d] / 100 - realised_correlation(R[i:i + QUARTER])))
    w = windows_back(len(gaps), YEAR)
    out["SS3"] = fixed_band([float(np.median([g[1] for g in gaps[a:b]])) for a, b in w],
                            [f"{gaps[a][0]}..{gaps[b - 1][0]}" for a, b in w])
    out["SS3"]["series"] = ("Cboe COR3M / 100 less the realised correlation of the certified forty "
                            "(corpus/civ/real_closes.json, adjusted closes 2015-01-02..2025-07-31) over the next 63 "
                            "sessions, equal weights")
    # EV1 to EV3
    rows = _rows(DATA / "edgar" / "earnings_8k_item202.csv")[1:]
    evs = []
    for tk, ix in SS_NAMES.items():
        d, v = cboe(ix)
        iv = to_map(d, v)
        sess = [x for x in sorted(stock[tk]) if x >= "2010-06-01"]
        for name, react, ny in earnings_events([r for r in rows if r[0] == EDGAR_NAME[tk]], sess):
            e = ev_event(iv, stock[tk], sess, react)
            if e:
                e.update({"name": tk, "reaction": react, "filed_new_york": ny})
                evs.append(e)
    yrs = [y for y in sorted({e["reaction"][:4] for e in evs}) if "2011" <= y <= "2025"]
    grp = {y: [e for e in evs if e["reaction"][:4] == y] for y in yrs}
    out["EV1"] = fixed_band([float(np.median([e["EV1"] for e in grp[y]])) for y in yrs], yrs)
    out["EV2"] = fixed_band([float(np.median([e["EV2"] for e in grp[y]])) for y in yrs], yrs)
    out["EV3"] = fixed_band([ev3_ratio(grp[y]) for y in yrs], yrs)
    out["EV"] = {"events": len(evs), "per_year": {y: len(grp[y]) for y in yrs},
                 "pooled": {"EV1_median": float(np.median([e["EV1"] for e in evs])),
                            "EV2_median": float(np.median([e["EV2"] for e in evs])),
                            "EV3_rms_ratio": ev3_ratio(evs),
                            "implied_move_median": float(np.median([e["implied"] for e in evs])),
                            "realised_move_median": float(np.median([e["realised"] for e in evs]))}}
    out["SS4"] = {"rule": "directional",
                  "source": "Bakshi, Kapadia and Madan, Review of Financial Studies 16(1) 101-143, 2003: 'individual "
                            "risk-neutral distributions differ from that of the market index by being far less "
                            "negatively skewed' (OEX options and 30 stocks)"}
    return out


# ====================================================================== real

def _clean(o):
    """JSON-safe: numpy scalars to floats, NaN to None, tuples to lists."""
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (np.floating, float)):
        return None if o != o else float(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    return o


def real(a):
    t0 = time.time()
    out = {"made": time.strftime("%Y-%m-%d"), "boot": {"B": BOOT_B, "seed": BOOT_SEED},
           "data": json.load(open(DATA / "MANIFEST.json"))["summary"]["downloaded"]}
    out["IF"] = real_if()
    out["VF"] = real_vf()
    out["RF"] = real_rf()
    out["OF"] = real_of()
    out["SV"] = real_sv()
    out["OB"] = real_ob()
    out["SS"] = real_ss()
    out["t"] = {str(n): {"t": t, "rate": r, "facts": FACTS_T.get(n)} for (n, _, _), (t, r) in sorted(_T_CACHE.items())}
    out = _clean(out)
    p = pathlib.Path(a.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    json.dump(out, open(p, "w"), indent=1, sort_keys=False)
    B = bands(out)
    print(f"wrote {p} ({time.time() - t0:.0f}s)")
    for k in ORDER:
        if k in B:
            w, lo, hi, rule = B[k]
            print(f"  {k:10s} {w[:70]:70s} {_fmt(lo):>9s} {_fmt(hi):>9s}  {rule}")


def _fmt(x):
    return "-" if x is None else f"{x:.4g}"


# ================================================================ the rows

#: (words, low, high, rule) for rows whose band is not read from real.json:
#: adopted rows, construction rows with exact bounds, literature rows stated
#: in CRITERIA-pt-v22.md. None is an open side.
STATED = {
    "IF3": ("front index future at expiry less the opening-print index, largest absolute, over the index", 0.0, 1e-12, "construction"),
    "IF4": ("day TWAP shortfall of the front index future at 10% of its volume, daily sigma (Q6)", 0.10, 0.21, "adopted Q6"),
    "NS1": ("night share of the front index future's daily variance (G1c)", 0.30, 0.60, "adopted G1c"),
    "NS3": ("untraded, every switch on: known answers and the 159 pt-v21 rows equal pt-v21's", None, None, "construction, read by the bit-identity stage"),
    "VF7": ("VIX future settlement less the published VIX at the expiry open, largest absolute", 0.0, 1e-12, "construction"),
    "VF8": ("forecast of the VIX unbiased at 21, 63, 126 sessions: largest absolute mean error over its se", 0.0, 2.0, "construction"),
    "RF3": ("policy-rate future settlement less the realised average policy rate, largest absolute, rate points", 0.0, 1e-9, "construction"),
    "RF5": ("forecast of the policy rate unbiased at 63, 126, 252 sessions: largest absolute mean error over its se", 0.0, 2.0, "construction"),
    "OF4": ("forecast of oil unbiased at 21, 63, 126 sessions: largest absolute mean error over its se", 0.0, 2.0, "construction"),
    "MG1": ("share of one-session moves past initial margin, front contracts", 0.005, 0.015, "construction (99% scan)"),
    "MG2": ("margin calls not met or liquidated by the next open; accounts below zero without a liquidation", 0, 0, "construction, traded stage"),
    "XD1": ("exploit gate, futures rules on published data", None, None, "C10a bound, traded stage"),
    "XD2": ("buying the basket at the open on the night futures move", None, None, "C10a bound, traded stage"),
    "XD3": ("pushing the expiry prints at the position limit: P&L after cost", None, 0.0, "construction, traded stage"),
    "SV1": ("Cboe VIX formula on the 21-session strip less the published VIX, largest absolute, points", 0.0, 0.10, "construction"),
    "SV2": ("static-arbitrage violations on quoted chains (butterfly, calendar, parity within half a tick)", 0, 0, "construction"),
    "MG3": ("single-name option portfolio sessions whose loss passes the margin set the close before (item 22)", 0.0, 0.00112,
            "2se (tape: the forty, 2015-2025, moves past +-15%), traded stage"),
    "XD4": ("option rules inside their OB band or C10a's bound; parity, box, conversion and the expiry push lose", None, None, "C10a bound and construction, traded stage"),
    "AX1": ("exercise violations: off the pricer's boundary, calls not on the session before an ex-date, American below European", 0, 0, "construction"),
    "AX2": ("delivery conserves shares and cash to the cent: largest absolute error, dollars", 0.0, 0.005, "construction, traded stage"),
    "XD5": ("single-name option rules (earnings straddles, covered calls, conversions, dividend capture, pinning)", None, None, "as XD4, traded stage"),
}

WORDS = {
    "IF1": "sd of the front index future's basis to carry fair value, bp (residual of tau)",
    "IF2": "lag-1 autocorrelation of the same basis",
    "NS2": "front index future at 09:29 less its fair value on the opening prints, sd bp (at most IF1's ceiling)",
    "VF1": "share of sessions with the second VIX future above the first",
    "VF2": "median annualised slope, ln(F2/F1) * 252 / (n2 - n1)",
    "VF3": "share in backwardation (F2 < F1) on sessions with the VIX at 30 or more",
    "VF4_F1": "slope of the first VIX future's daily change on the VIX's",
    "VF4_F2": "slope of the second VIX future's daily change on the VIX's",
    "VF4_F4": "slope of the fourth VIX future's daily change on the VIX's",
    "VF5": "annual log return of a constant 21-session long VIX future",
    "VF6_h21": "mean VIX future 21 sessions before expiry less its final settlement, points",
    "VF6_h63": "the same at 63 sessions",
    "VF6_h126": "the same at 126 sessions",
    "RF1_m1": "front policy-rate future at the month's first session less the month's realised average, bp",
    "RF1_m3": "policy-rate future's rate 3 months out less the realised average, bp",
    "RF1_m6": "the same 6 months out, bp",
    "RF2": "RMS of the front policy-rate future's daily change on meeting sessions over other sessions",
    "RF4": "eight-quarter term-rate strip less the 2-year yield, bp (proxy: -ACM TP02)",
    "OF1": "share of sessions with oil contract 1 above contract 4",
    "OF2": "correlation of ln(F4/F1) with inventory's log deviation from its 5-year mean",
    "OF3": "elasticity of contract 4's daily log change to contract 1's",
    "SV3": "median VIX3M over VIX, by the VIX method on the 63-session strip",
    "SV4": "share of sessions with the VIX above VIX3M",
    "SV5_9D": "median VIX9D over VIX (6-session strip)",
    "SV5_6M": "median VIX6M over VIX (126-session strip)",
    "SV5_1Y": "median VIX1Y over VIX (252-session strip)",
    "SV6_median": "median SKEW, Cboe method on the 21-session strip",
    "SV6_sd": "sd of SKEW",
    "SV7": "correlation of SKEW with the VIX, one-year windows",
    "SV8": "median VIX over the next 21 sessions' realised index volatility",
    "SV9": "slope of the live VIX's log change on the index's log return (daily real slope)",
    "OB1r": "PUT rule: annual log return less the index total return's",
    "OB1v": "PUT rule: volatility over the index total return's",
    "OB1d": "PUT rule: worst drawdown over the index total return's",
    "OB2r": "BXM rule: annual log return less the index total return's",
    "OB2v": "BXM rule: volatility over the index total return's",
    "OB2d": "BXM rule: worst drawdown over the index total return's",
    "OB3r": "CNDR rule: annual log return less the index total return's",
    "OB3v": "CNDR rule: volatility over the index total return's",
    "OB3d": "CNDR rule: worst drawdown over the index total return's",
    "OB4r": "BFLY rule: annual log return less the index total return's",
    "OB4v": "BFLY rule: volatility over the index total return's",
    "OB4d": "BFLY rule: worst drawdown over the index total return's",
    "OB5": "mean weekly return of a zero-beta at-the-money straddle",
    "OB6": "mean delta-hedged gain of a one-month at-the-money call over the index level (below zero)",
    "SS1": "median single-name 30-day IV over the next 21 sessions' realised volatility",
    "SS2": "median 3-month implied correlation from the index and name surfaces, x 100",
    "SS3": "median implied less next-63-session realised correlation",
    "EV1": "median log change of a name's 30-day IV across its earnings report",
    "EV2": "median log change of 30-day IV over the 15 sessions before a report",
    "EV3": "RMS implied event move over RMS realised reaction move",
    "SS4": "name skew less steep than index skew (median name; directional)",
}

ORDER = ["IF1", "IF2", "IF3", "IF4", "NS1", "NS2", "NS3", "VF1", "VF2", "VF3", "VF4_F1", "VF4_F2", "VF4_F4", "VF5",
         "VF6_h21", "VF6_h63", "VF6_h126", "VF7", "VF8", "RF1_m1", "RF1_m3", "RF1_m6", "RF2", "RF3", "RF4", "RF5",
         "OF1", "OF2", "OF3", "OF4", "MG1", "MG2", "XD1", "XD2", "XD3",
         "SV1", "SV2", "SV3", "SV4", "SV5_9D", "SV5_6M", "SV5_1Y", "SV6_median", "SV6_sd", "SV7", "SV8", "SV9",
         "OB1r", "OB1v", "OB1d", "OB2r", "OB2v", "OB2d", "OB3r", "OB3v", "OB3d", "OB4r", "OB4v", "OB4d", "OB5", "OB6",
         "MG3", "XD4", "SS1", "SS2", "SS3", "EV1", "EV2", "EV3", "SS4", "AX1", "AX2", "XD5"]

#: where each real-derived band sits in real.json
REAL_KEY = {"IF1": ("IF", "IF1"), "IF2": ("IF", "IF2"),
            **{k: ("VF", k) for k in ("VF1", "VF2", "VF3", "VF4_F1", "VF4_F2", "VF4_F4", "VF5", "VF6_h21", "VF6_h63", "VF6_h126")},
            **{k: ("RF", k) for k in ("RF1_m1", "RF1_m3", "RF1_m6", "RF2", "RF4")},
            **{k: ("OF", k) for k in ("OF1", "OF2", "OF3")},
            **{k: ("SV", k) for k in ("SV3", "SV4", "SV5_9D", "SV5_6M", "SV5_1Y", "SV6_median", "SV6_sd", "SV7", "SV8", "SV9")},
            **{f"OB{i}{x}": ("OB", f"OB{i}{x}") for i in (1, 2, 3, 4) for x in "rvd"},
            "OB5": ("OB", "OB5"), "OB6": ("OB", "OB6"),
            **{k: ("SS", k) for k in ("SS1", "SS2", "SS3", "EV1", "EV2", "EV3", "SS4")}}


def bands(realj):
    """{row: (words, low, high, rule)} for every row of phases 1 to 3."""
    b = {k: v for k, v in STATED.items()}
    for k, (g, kk) in REAL_KEY.items():
        x = realj[g][kk]
        b[k] = (WORDS[k], x.get("low"), x.get("high"), x["rule"])
    # NS2's ceiling is IF1's ceiling (the night quote may not drift further
    # from fair value than the day's basis does)
    b["NS2"] = (WORDS["NS2"], 0.0, realj["IF"]["IF1"]["high"], "construction, IF1's ceiling")
    return b


# ============================================================ strip formulas

def bs_price(F, K, T, sigma, df, call):
    """Black (1976) on the forward; T in years, df the discount factor."""
    if T <= 0 or sigma <= 0:
        return df * max(0.0, (F - K) if call else (K - F))
    v = sigma * math.sqrt(T)
    d1 = (math.log(F / K) + 0.5 * v * v) / v
    d2 = d1 - v
    N = lambda x: 0.5 * math.erfc(-x / math.sqrt(2))
    return df * (F * N(d1) - K * N(d2)) if call else df * (K * N(-d2) - F * N(-d1))


def strip(iv, F, T, r, n=400, width=8.0):
    """Out-of-the-money option prices on a log-moneyness grid of +/- `width`
    total standard deviations, strikes K_i, spacing dK_i (the Cboe rule:
    half the distance between the neighbours), K0 = the forward. iv(k) is
    the surface's implied volatility at k = ln(K/F)."""
    s0 = iv(0.0) * math.sqrt(T)
    ks = np.linspace(-width * s0, width * s0, n)
    K = F * np.exp(ks)
    df = math.exp(-r * T)
    Q = np.array([bs_price(F, k, T, iv(float(x)), df, k >= F) for k, x in zip(K, ks)])
    dK = np.empty_like(K)
    dK[1:-1] = (K[2:] - K[:-2]) / 2
    dK[0], dK[-1] = K[1] - K[0], K[-1] - K[-2]
    return K, dK, Q


def strip_vix(iv, F, T, r):
    """The Cboe VIX formula on one strip, K0 = F: 100 * sqrt((2/T) e^{rT}
    sum dK/K^2 Q(K)). T in years."""
    K, dK, Q = strip(iv, F, T, r)
    return 100 * math.sqrt(2 / T * math.exp(r * T) * (dK / K ** 2 * Q).sum())


def strip_skew(iv, F, T, r):
    """The Cboe SKEW formula on one strip with K0 = F (so the epsilon terms
    vanish): SKEW = 100 - 10 S, S the risk-neutral skewness of the log
    return from P1, P2, P3 (Cboe SKEW white paper)."""
    K, dK, Q = strip(iv, F, T, r)
    g = math.exp(r * T)
    lk = np.log(K / F)
    p1 = -g * (dK / K ** 2 * Q).sum()
    p2 = g * (2 / K ** 2 * (1 - lk) * dK * Q).sum()
    p3 = g * (3 / K ** 2 * (2 * lk - lk ** 2) * dK * Q).sum()
    s = (p3 - 3 * p1 * p2 + 2 * p1 ** 3) / (p2 - p1 ** 2) ** 1.5
    return 100 - 10 * s


# ===================================================================== model

class MissingAPI(RuntimeError):
    pass


#: The derivatives API PLAN-pt-v22.md names (phase 1: index_level,
#: contracts, quote, settlements, forecast, run_night; phase 2: chain,
#: surface). This runner assumes these shapes; the builder of phase 1
#: either ships them or changes ModelAPI below, and nothing else here.
#:   index_level        float, the price index at the moment
#:   index_total_return float, the same with dividends reinvested (optional)
#:   contracts()        [{"symbol", "root", "kind": "future"|"option", "expiry": session,
#:                        "right": "C"|"P", "strike": float}, ...]
#:   quote(symbol)      {"bid", "ask", "mid", "mark", "fair", "initial_margin", "multiplier"}
#:   settlements(day)   [{"symbol", "root", "kind", "session", "value", "reference"}, ...];
#:                      "reference" is what the contract settles on (the opening-print
#:                      index, the published VIX at the expiry open, the realised
#:                      average policy rate, the oil spot)
#:   forecast()         {"vix": {h: E}, "policy_rate": {h: E}, "oil": {h: E}, "next_meeting": session},
#:                      h in sessions; session numbers count from 0 at the first session, as
#:                      earnings_calendar's do
#:   run_night(steps)   the night session for index futures
#:   surface(root)      an object or dict with iv(k, T) (k = ln K/F, T sessions),
#:                      forward(T) and rate(T) (decimal a year)
#:   chain(root, expiry) rows {"strike", "right", "bid", "ask", "mid", "iv", "delta", ...}
PLANNED = {"free": ("index_level", "contracts", "quote", "settlements", "forecast"),
           "chain": ("index_level", "contracts", "quote", "chain", "surface", "settlements")}
FUTURE_ROOTS = ("IDX", "VIX", "FF", "TR3", "OIL")


def _traded():
    """The ptv22traded stage (ptv22traded.py): the parts `traded`, `push`
    and `metaorder`, and the rows MG2, MG3, XD1 to XD5, AX2 and IF4."""
    import ptv22traded
    return ptv22traded


def require(e, part):
    need = PLANNED[part] if part in PLANNED else _traded().PLANNED[part]
    missing = [m for m in need if not hasattr(e, m)]
    if missing:
        raise MissingAPI("this engine has no " + ", ".join(f"Engine.{m}" for m in missing) +
                         ": the pt-v22 derivatives API (PLAN-pt-v22.md, phases 1 and 2) is not built yet")


class ModelAPI:
    """Every read of the derivatives API goes through here."""

    def __init__(self, e):
        self.e = e

    def index(self):
        x = self.e.index_level
        x = x() if callable(x) else x
        # The engine gives {"level", "close"}; the stand-in a number.
        return float(x["level"] if isinstance(x, dict) else x)

    def futures(self):
        """{root: [(symbol, expiry), ...] in expiry order} for listed futures."""
        out = {}
        for c in self.e.contracts():
            if c.get("kind") == "future":
                out.setdefault(c["root"], []).append((c["symbol"], int(c["expiry"])))
        return {k: sorted(v, key=lambda x: x[1]) for k, v in out.items()}

    def quote(self, sym):
        q = self.e.quote(sym)
        return {k: (float(q[k]) if q.get(k) is not None else None)
                for k in ("bid", "ask", "mid", "mark", "fair", "initial_margin", "multiplier") if k in q}

    def settlements(self, day):
        return [dict(x) for x in self.e.settlements(day)]

    #: The horizons the rows read (VF8 and OF4 at 21, 63, 126; RF5 at 63,
    #: 126, 252); a history keeps only these, not the whole path.
    FORECAST_KEEP = (21, 63, 126, 252)

    def forecast(self):
        f = self.e.forecast() or {}

        def path(v):
            # the engine gives a list, entry h - 1 the expectation h sessions
            # ahead; the stand-in a {h: E} dict
            if isinstance(v, dict):
                return {int(h): float(x) for h, x in v.items() if int(h) in self.FORECAST_KEEP}
            return {h: float(v[h - 1]) for h in self.FORECAST_KEEP if h <= len(v)}
        out = {k: path(f.get(k, {})) for k in ("vix", "policy_rate", "oil")}
        out["next_meeting"] = int(f["next_meeting"]) if f.get("next_meeting") is not None else None
        return out

    def surface(self, root):
        s = self.e.surface(root)
        get = (lambda n: s[n]) if isinstance(s, dict) else (lambda n: getattr(s, n))
        return get("iv"), get("forward"), get("rate")

    def strips(self, root, tenors, skew_tenor=None):
        """({tenor: strip VIX}, SKEW at `skew_tenor` or None) on `root`'s
        surface: the engine surface's own Cboe formulas where it has them
        (`VolSurface.strip_vix`, `strip_skew`, the strip pt-v22 phase 2 fixed),
        else this module's on its iv, forward and rate, which agree."""
        s = self.e.surface(root)
        if hasattr(s, "strip_vix") and hasattr(s, "strip_skew"):
            vix = {h: s.strip_vix(float(h)) for h in tenors}
            skew = s.strip_skew(float(skew_tenor)) if skew_tenor else None
            return vix, skew
        iv, fwd, rate = self.surface(root)
        vix = {h: strip_vix(lambda k, h=h: iv(k, h), fwd(h), h / YEAR, rate(h)) for h in tenors}
        skew = (strip_skew(lambda k: iv(k, skew_tenor), fwd(skew_tenor), skew_tenor / YEAR, rate(skew_tenor))
                if skew_tenor else None)
        return vix, skew

    def name_roots(self, tickers):
        """The names whose surfaces the engine keeps: `name_option_roots()`
        on an engine with pt-v22 phase 3, every ticker on one without it
        (the stand-in), none where the names list no options."""
        if hasattr(self.e, "name_option_roots"):
            return list(self.e.name_option_roots())
        return list(tickers)

    def chain(self, root, expiry):
        rows = self.e.chain(root, expiry)
        rows = rows.to_pylist() if hasattr(rows, "to_pylist") else rows
        return [dict(r) for r in rows]


def _engine(path):
    if path:
        p = str(pathlib.Path(path) / "python")
        if p not in sys.path and any(pathlib.Path(p, "tradefloor").glob("_core*.so")):
            sys.path.insert(0, p)
    import tradefloor as tf
    return tf


def _params(tf, base, dials):
    return tf.ModelParams.from_preset(base, **dials) if dials else tf.ModelParams.from_preset(base)


def _macro(e):
    m = e.macro_fields
    return {"vix": float(m["vix"]), "policy": float(m["federal_funds_rate"]), "y2": float(m["treasury_yield_2y"]),
            "oil": float(m["oil_price"])}


def _vix_now(e):
    """The live VIX within a session (`vix_intraday_live`), which SV9 reads;
    the published VIX where the engine has none. The published VIX holds at
    the last close's value through a session."""
    v = getattr(e, "live_vix", None)
    v = v() if callable(v) else v
    return float(v) if v is not None else _macro(e)["vix"]


def _inventory(e):
    # the engine keeps oil's inventory in its economy dict; the stand-in in
    # its macro state
    ec = getattr(e, "economy", None)
    ec = ec() if callable(ec) else ec
    if isinstance(ec, dict) and ec.get("oil_inventory_level") is not None:
        return float(ec["oil_inventory_level"])
    s = e.macro_state
    s = s() if callable(s) else s
    for k in ("oil_inventory_level", "oil_inventory"):
        if hasattr(s, k):
            return float(getattr(s, k))
        if hasattr(s, "get") and s.get(k) is not None:
            return float(s.get(k))
    return float("nan")


def run_free(tf, params, seed, years, engine_factory=None, night_steps=10):
    """One untraded history with every phase-1 switch on, from the opening
    state, on the certified roster. Per session after the close: the index
    and its total return, the published VIX, the policy rate, the 2-year
    yield, oil and its inventory, every listed future's mark, fair value and
    initial margin with its expiry session, the forecasts, and the session's
    settlement events; before the open, after the night session, the front
    index future against the same contract's fair value on the opening
    prints (NS2)."""
    u = list(tf.Universe.random(ROSTER[0], seed=ROSTER[1]))
    shares = np.array([float(x.shares_outstanding) for x in u])
    e = engine_factory(seed) if engine_factory else tf.Engine(seed=seed, universe=u, model=params)
    require(e, "free")
    api = ModelAPI(e)
    rec = {"seed": seed, "index": [], "tr": [], "index_open": [], "vix": [], "policy": [], "y2": [], "oil": [],
           "inventory": [], "futures": [], "forecast": [], "settlements": [], "night": [], "night_mid": []}
    tr = 1.0
    last = None
    for day in range(years * YEAR):
        night, sym09 = None, None
        if hasattr(e, "run_night") and day:
            e.run_night(night_steps)
            fr = api.futures().get("IDX")
            if fr:
                sym09 = fr[0][0]
                q09 = api.quote(sym09)
                night = q09.get("mid")
        rec["night_mid"].append(night)
        e.open_market()
        # NS2 reads the 09:29 quote against the same contract's fair value on
        # the opening prints, the index the futures settle on (IF3), before
        # the first tick (owner's ruling, 2026-10-08). A contract that settled
        # at this open has no quote after it and gives no reading that day.
        if night is not None:
            try:
                fv = api.quote(sym09).get("fair")
            except Exception:
                fv = None
            rec["night"].append(1e4 * math.log(night / fv) if fv else None)
        e.run_session(9, 30, 3, 1)
        i_open = api.index()
        e.run_session(9, 31, 3, 389)
        e.close_market()
        idx = api.index()
        px = np.frombuffer(e.prices(), "<f8")
        dv = np.asarray(e.dividends_today() if callable(e.dividends_today) else e.dividends_today, float)
        if last is not None:
            tr *= idx / last * (1 + float((shares * dv).sum() / (shares * px).sum()))
        last = idx
        m = _macro(e)
        rec["index"].append(idx); rec["tr"].append(tr); rec["index_open"].append(i_open)
        for k in ("vix", "policy", "y2", "oil"):
            rec[k].append(m[k])
        rec["inventory"].append(_inventory(e))
        fut = {}
        for root, lst in api.futures().items():
            fut[root] = [[s, x, api.quote(s)] for s, x in lst]
        rec["futures"].append(fut)
        rec["forecast"].append(api.forecast())
        rec["settlements"].append(api.settlements(day))
    return rec


def run_chain(tf, params, seed, years, every=1, engine_factory=None):
    """One untraded history with the phase-2 and phase-3 switches on: at each
    close the index surface's strip VIX at 6, 21, 63, 126 and 252 sessions
    and SKEW at 21, each name's 21- and 63-session strip VIX, the published
    VIX, the index, the names' closes, and the next monthly expiry's index
    chain (for SV2 and the option rules)."""
    u = list(tf.Universe.random(ROSTER[0], seed=ROSTER[1]))
    e = engine_factory(seed) if engine_factory else tf.Engine(seed=seed, universe=u, model=params)
    require(e, "chain")
    api = ModelAPI(e)
    tickers = [x.ticker for x in u]
    shares = [float(x.shares_outstanding) for x in u]
    rec = {"seed": seed, "tickers": tickers, "shares": shares, "rows": []}
    rec["intraday"] = []
    sh = np.array(shares)
    tr, last = 1.0, None
    for day in range(years * YEAR):
        e.open_market()
        if day % MONTH == 0:
            # SV9: the live VIX and the index every 5 minutes, one session in 21
            path = [(api.index(), _vix_now(e))]
            for k in range(78):
                m = 9 * 60 + 30 + 5 * k
                e.run_session(m // 60, m % 60, 3, 5)
                path.append((api.index(), _vix_now(e)))
            rec["intraday"].append(path)
        else:
            e.run_session(9, 30, 3, 390)
        # One close: a session run with close_at_end and then close_market
        # closes twice, and the engine's day count runs two a loop.
        e.close_market()
        idx = api.index()
        px = np.frombuffer(e.prices(), "<f8")
        dv = np.asarray(e.dividends_today() if callable(e.dividends_today) else e.dividends_today, float)
        if last is not None:
            tr *= idx / last * (1 + float((sh * dv).sum() / (sh * px).sum()))
        last = idx
        if day % every:
            continue
        iv, fwd, rate = api.surface("IDX")
        row = {"day": day, "index": idx, "tr": tr, "vix": _macro(e)["vix"],
               "closes": np.frombuffer(e.prices(), "<f8").tolist(), "strip": {}, "names": {},
               "earnings": [dict(x) for x in e.earnings_calendar(21)] if hasattr(e, "earnings_calendar") else []}
        row["strip"], row["skew21"] = api.strips("IDX", (6, 21, 63, 126, 252), 21)
        row["skew21_names"] = {}
        for tk in api.name_roots(tickers):
            row["names"][tk], row["skew21_names"][tk] = api.strips(tk, (21, 63), 21)
        opts = sorted({c["expiry"] for c in e.contracts() if c.get("root") == "IDX" and c.get("kind") == "option"})
        if opts:
            # SV2 is read here on the three nearest chains and kept as a
            # count; the front chain alone is kept, with the fields the
            # estimators read, so a 21-year history stays near 0.5 GB
            row["chain_expiry"] = opts[0]
            chains = {x: _slim(api.chain("IDX", x)) for x in opts[:3]}
            row["forward"] = {x: fwd(x - day) for x in opts[:3]}
            row["rate"] = {x: rate(x - day) for x in opts[:3]}
            row["sv2"] = arbitrage_violations({**row, "chains_all": chains})
            row["chain"] = chains[opts[0]]
        # The index's settlements only: the option rules read the index
        # options' opening prints, and a model that lists single-name options
        # settles some 1,700 of theirs at each expiry, which the chain's rows
        # do not read.
        row["settlements"] = [x for x in api.settlements(day) if x.get("root") == "IDX"]
        rec["rows"].append(row)
    return rec


# ============================================================ model estimators

BURN = YEAR          # the model's first year is left out of every statistic, as ptv21's macro rows do


def _pct(x):
    """Rates in per cent whatever the engine reports (a fraction below 1)."""
    return x * 100 if abs(x) < 1 else x


def _front_series(h, root, k=0):
    """Per session after BURN: (session, symbol, expiry, quote) of the k-th listed future of `root`."""
    out = []
    for s, fut in enumerate(h["futures"]):
        lst = fut.get(root, [])
        if s >= BURN and len(lst) > k:
            sym, x, q = lst[k]
            out.append((s, sym, x, q))
    return out


def _forecast_t(h, key, real_series, horizons):
    """Mean realised less forecast at each horizon, per history."""
    out = {}
    for hh in horizons:
        err = [real_series[s + hh] - f[key][hh] for s, f in enumerate(h["forecast"])
               if s >= BURN and hh in f.get(key, {}) and s + hh < len(real_series)]
        out[hh] = float(np.mean(err)) if err else float("nan")
    return out


def _unbiased_stat(per_history):
    """max over horizons of |mean of per-history mean errors| / its se (histories independent)."""
    worst = 0.0
    for hh in per_history[0]:
        v = np.array([p[hh] for p in per_history if p[hh] == p[hh]])
        if len(v) < 2:
            return float("nan")
        se = v.std(ddof=1) / math.sqrt(len(v))
        worst = max(worst, abs(v.mean()) / se if se > 0 else (0.0 if v.mean() == 0 else float("inf")))
    return worst


def free_rows(free):
    rows, rep = {}, {}
    # IF1, IF2: the front index future against its carry fair value, windows of 252 kept sessions
    w_if = []
    for h in free:
        fr = [(s, x, q) for s, sym, x, q in _front_series(h, "IDX") if x - s > 6 and q.get("fair")]
        obs = [{"basis_bp": 1e4 * math.log(q["mark"] / q["fair"]), "tau": (x - s) / YEAR, "i": s, "date": s}
               for s, x, q in fr]
        w_if += _if_windows(obs, YEAR) if len(obs) >= YEAR else []
    if w_if:
        rows["IF1"] = st.median(x["IF1"] for x in w_if)
        rows["IF2"] = st.median(x["IF2"] for x in w_if)
    # IF3, VF7, RF3: settlement equals what it settles on
    for row, root in (("IF3", "IDX"), ("VF7", "VIX"), ("RF3", "FF")):
        ev = [x for h in free for day in h["settlements"] for x in day if x.get("root") == root and x.get("kind") == "future"]
        if ev:
            if row == "IF3":
                rows[row] = max(abs(x["value"] - x["reference"]) / x["reference"] for x in ev)
            else:
                rows[row] = max(abs(x["value"] - x["reference"]) for x in ev)
    # NS1, NS2: the night
    ns1, ns2 = [], []
    for h in free:
        fr = {s: q["mark"] for s, sym, x, q in _front_series(h, "IDX")}
        nm = h.get("night_mid", [])
        g, it = [], []
        for s in range(BURN + 1, len(nm)):
            if nm[s] and s in fr and s - 1 in fr and _front_series_same(h, s):
                g.append(math.log(nm[s] / fr[s - 1])); it.append(math.log(fr[s] / nm[s]))
        if len(g) > 20:
            g, it = np.array(g), np.array(it)
            ns1.append(float(g.var() / (g.var() + it.var())))
        nb = [x for x in h.get("night", [])[BURN:] if x is not None]
        if len(nb) > 20:
            ns2.append(float(np.std(nb, ddof=1)))
    if ns1:
        rows["NS1"] = st.median(ns1)
    if ns2:
        rows["NS2"] = st.median(ns2)
    # VF1 to VF6
    pieces, ret, errs = [], [], {21: [], 63: [], 126: []}
    for h in free:
        sessions = list(range(BURN, len(h["vix"])))
        curve, contracts = {}, {}
        for s in sessions:
            lst = h["futures"][s].get("VIX", [])
            live = [(x, x - s, q["mark"]) for sym, x, q in lst if x > s]
            if live:
                curve[s] = live
            for sym, x, q in lst:
                if x > s:
                    c = contracts.setdefault(x, {"dates": [], "settle": [], "final": float("nan")})
                    c["dates"].append(s); c["settle"].append(q["mark"])
        for day in h["settlements"]:
            for x in day:
                if x.get("root") == "VIX" and x.get("kind") == "future" and x.get("session") in contracts:
                    contracts[x["session"]]["final"] = x["value"]
        for c in contracts.values():
            c["settle"] = np.array(c["settle"]); c["_ix"] = {d: i for i, d in enumerate(c["dates"])}
        vix = {s: h["vix"][s] for s in sessions}
        pieces += list(vf_session_stats(curve, vix, sessions).values())
        r = vf5_returns(curve, contracts, sessions)
        rd = sorted(r)
        ret += [float(np.log1p(np.array([r[d] for d in rd[a:b]])).sum()) for a, b in windows_back(len(rd), YEAR)]
        for x, c in contracts.items():
            if c["final"] == c["final"]:
                for hh in errs:
                    j = c["_ix"].get(x - hh)
                    if j is not None:
                        errs[hh].append(float(c["settle"][j]) - c["final"])
    if pieces:
        rows["VF1"] = float(np.mean([p["contango"] for p in pieces if "contango" in p]))
        rows["VF2"] = float(np.median([p["slope"] for p in pieces if "slope" in p]))
        b30 = [p["back30"] for p in pieces if "back30" in p]
        rows["VF3"] = float(np.mean(b30)) if b30 else float("nan")
        rep["VF3_sessions"] = len(b30)
        for kk in (1, 2, 4):
            pts = [p[f"dF{kk}"] for p in pieces if f"dF{kk}" in p]
            if len(pts) > 2:
                rows[f"VF4_F{kk}"] = ols([x[1] for x in pts], [x[0] for x in pts])[1]
    if ret:
        rows["VF5"] = st.median(ret)
    for hh, e in errs.items():
        if e:
            rows[f"VF6_h{hh}"] = float(np.mean(e))
    # VF8, RF5, OF4: the forecast module is unbiased
    for row, key, real_key, hs in (("VF8", "vix", "vix", (21, 63, 126)), ("RF5", "policy_rate", "policy", (63, 126, 252)),
                                   ("OF4", "oil", "oil", (21, 63, 126))):
        per = [_forecast_t(h, key, h[real_key], hs) for h in free]
        if per and all(per[0][x] == per[0][x] for x in hs):
            rows[row] = _unbiased_stat(per)
    # RF1: policy-rate futures less the realised average over the contract's 21-session month
    rf1 = {1: [], 3: [], 6: [], 12: []}
    rf2m, rf2o = [], []
    for h in free:
        pol = [_pct(x) for x in h["policy"]]
        for s in range(BURN, len(h["futures"])):
            for sym, x, q in h["futures"][s].get("FF", []):
                n = x - s
                for m in rf1:
                    if n == 21 * m and x < len(pol):
                        rf1[m].append(100 * ((100 - q["mark"]) - float(np.mean(pol[x - MONTH + 1: x + 1]))))
        # RF2: the front contract's change on meeting sessions and others, same contract on both sessions
        nxt = [f.get("next_meeting") for f in h["forecast"]]
        for s in range(BURN + 1, len(h["futures"])):
            a, b = h["futures"][s - 1].get("FF", []), h["futures"][s].get("FF", [])
            if a and b and a[0][0] == b[0][0]:
                d = 100 * (b[0][2]["mark"] - a[0][2]["mark"])
                (rf2m if nxt[s - 1] == s else rf2o).append(d)
    for m, key in ((1, "RF1_m1"), (3, "RF1_m3"), (6, "RF1_m6"), (12, "RF1_m12")):
        if rf1[m]:
            rows[key] = float(np.mean(rf1[m]))
    if len(rf2m) > 2 and len(rf2o) > 2:
        rows["RF2"] = rms_ratio(rf2m, rf2o)
    # RF4: eight quarterly term-rate futures' average rate less the 2-year yield, per 252-session window
    rf4 = []
    for h in free:
        v = []
        for s in range(BURN, len(h["futures"])):
            lst = h["futures"][s].get("TR3", [])
            if len(lst) >= 8:
                v.append(100 * (float(np.mean([100 - q["mark"] for sym, x, q in lst[:8]])) - _pct(h["y2"][s])))
        rf4 += [float(np.median(v[a:b])) for a, b in windows_back(len(v), YEAR)]
    if rf4:
        rows["RF4"] = st.median(rf4)
    # OF1 to OF3
    of1, slope, invd, of3 = [], [], [], []
    for h in free:
        inv = np.log(np.maximum(np.asarray(h["inventory"], float), 1e-12))
        d1, d4 = [], []
        for s in range(BURN, len(h["futures"])):
            lst = h["futures"][s].get("OIL", [])
            if len(lst) < 4:
                continue
            f1, f4 = lst[0][2]["mark"], lst[3][2]["mark"]
            of1.append(f1 > f4)
            if s >= 5 * YEAR and inv[s] == inv[s]:
                slope.append(math.log(f4 / f1)); invd.append(float(inv[s] - inv[s - 5 * YEAR:s].mean()))
            p = h["futures"][s - 1].get("OIL", [])
            if len(p) >= 4 and p[0][0] == lst[0][0] and p[3][0] == lst[3][0]:
                d1.append(math.log(f1 / p[0][2]["mark"])); d4.append(math.log(f4 / p[3][2]["mark"]))
        of3 += [of3_window(d1[a:b], d4[a:b]) for a, b in windows_back(len(d1), 5 * YEAR)]
    if of1:
        rows["OF1"] = float(np.mean(of1))
    if len(slope) > 2:
        rows["OF2"] = of2_window(slope, invd)
    if of3:
        rows["OF3"] = st.median(of3)
    # MG1: one-session moves of the front contracts past the initial margin set at the previous close
    past, n = 0, 0
    for h in free:
        for root in FUTURE_ROOTS:
            for s in range(BURN + 1, len(h["futures"])):
                a, b = h["futures"][s - 1].get(root, []), h["futures"][s].get(root, [])
                if a and b and a[0][0] == b[0][0] and a[0][2].get("initial_margin"):
                    mult = a[0][2].get("multiplier") or 1.0
                    past += abs(b[0][2]["mark"] - a[0][2]["mark"]) * mult > a[0][2]["initial_margin"]
                    n += 1
    if n:
        rows["MG1"] = past / n
    return rows, rep


def _front_series_same(h, s):
    """The front index future is the same contract at s-1 and s."""
    a, b = h["futures"][s - 1].get("IDX", []), h["futures"][s].get("IDX", [])
    return bool(a and b and a[0][0] == b[0][0])


def _by_strike(chain, right):
    return {float(r["strike"]): r for r in chain if r["right"] == right}


def _nearest(strikes, x, side=0):
    """Nearest strike to x; side -1 at or below, +1 at or above."""
    s = sorted(strikes)
    if side < 0:
        c = [k for k in s if k <= x]
        return c[-1] if c else s[0]
    if side > 0:
        c = [k for k in s if k >= x]
        return c[0] if c else s[-1]
    return min(s, key=lambda k: abs(k - x))


def _by_delta(book, target):
    return min(book, key=lambda k: abs(float(book[k]["delta"]) - target))


def _bid(q):
    """A sale's price: the dealer's bid, 0 where it shows none (a bid under a tick)."""
    return float(q["bid"]) if q.get("bid") is not None else 0.0


def _ask(q):
    """A purchase's price: the dealer's ask, the mid where it shows none."""
    return float(q["ask"]) if q.get("ask") is not None else float(q["mid"])


def option_rules(rows, settle_index):
    """The four Cboe benchmark rules on the model's quotes, cash at zero
    interest, rolled at each monthly expiry at the close of the expiry
    session (sells at the bid, buys at the ask), marked at mid each close,
    settled on the opening-print index at expiry (settle_index {expiry:
    value}). Sizes: PUT sells account/strike puts (cash-secured, Cboe PUT);
    BXM holds the total-return index and sells one at-the-money call per
    index unit (Cboe BXM); CNDR sells the 0.20-delta put and call and buys the
    0.05-delta wings, BFLY sells the at-the-money put and call and buys wings
    5% away, each one spread per index unit of account (cash equal to the
    notional). That scale reproduces the Cboe indices' volatility (CNDR 7%,
    BFLY 11% a year, median year); Cboe's sizing rule is to be checked
    against its methodology document before phase 2 grades. Returns {rule:
    [account by recorded session]} and the index's total-return level on the
    same sessions."""
    out = {k: [] for k in ("PUT", "BXM", "CNDR", "BFLY")}
    acct = {k: 1.0 for k in out}
    pos = {k: None for k in out}
    tr = [r["tr"] for r in rows]
    for i, r in enumerate(rows):
        x = r.get("chain_expiry")
        S = r["index"]
        for k in out:
            p = pos[k]
            if p and r["day"] >= p["expiry"]:
                Sx = settle_index.get(p["expiry"], S)
                pay = sum(n * (max(Sx - K, 0) if rt == "C" else max(K - Sx, 0)) for rt, K, n in p["legs"])
                acct[k] = p["cash"] + pay + (p["units"] * r["tr"] if k == "BXM" else 0.0)
                pos[k] = None
            elif p:
                chain = {rt: _by_strike(p["chain_now"](r), rt) for rt in "CP"}
                val = 0.0
                for rt, K, n in p["legs"]:
                    q = chain[rt].get(K)
                    if q:
                        p["last"][(rt, K)] = float(q["mid"])
                    val += n * p["last"].get((rt, K), 0.0)          # an unquoted leg keeps its last mark
                acct[k] = p["cash"] + val + (p["units"] * r["tr"] if k == "BXM" else 0.0)
            # a position opens at a roll, on the next month; the first one
            # waits for a front month at least 10 sessions out, so a history
            # does not start on an expiry a few sessions away
            if pos[k] is None and x is not None and r.get("chain") and x - r["day"] >= 10:
                C, P = _by_strike(r["chain"], "C"), _by_strike(r["chain"], "P")
                A = acct[k]
                if k == "PUT":
                    K = _nearest(P, S, -1); n = A / K
                    legs = [("P", K, -n)]; cash = A + n * _bid(P[K]); units = 0.0
                elif k == "BXM":
                    K = _nearest(C, S, +1); units = A / r["tr"]; n = A / S
                    legs = [("C", K, -n)]; cash = n * _bid(C[K])
                else:
                    if k == "CNDR":
                        sp, sc, lp, lc = _by_delta(P, -0.20), _by_delta(C, 0.20), _by_delta(P, -0.05), _by_delta(C, 0.05)
                    else:
                        sp = sc = _nearest(C, S); lp, lc = _nearest(P, 0.95 * S), _nearest(C, 1.05 * S)
                    n = A / S
                    legs = [("P", sp, -n), ("C", sc, -n), ("P", lp, n), ("C", lc, n)]
                    credit = n * (_bid(P[sp]) + _bid(C[sc]) - _ask(P[lp]) - _ask(C[lc]))
                    cash = A + credit; units = 0.0
                book = {"C": C, "P": P}
                pos[k] = {"expiry": x, "legs": legs, "cash": cash, "units": units,
                          "last": {(rt, K): float(book[rt][K]["mid"]) for rt, K, n in legs},
                          "chain_now": (lambda rr, xx=x: rr.get("chains_all", {}).get(xx) or rr.get("chain") or [])}
            out[k].append(acct[k])
    return out, tr


def straddle_weeks(rows):
    """OB5: each fifth recorded session, a zero-beta at-the-money straddle of
    the nearest expiry at least 10 sessions out (Coval and Shumway's
    weights: call share theta = -beta_P / (beta_C - beta_P), option beta =
    delta * S / price), held five sessions at mid."""
    rets = []
    for i in range(0, len(rows) - 5, 5):
        r, q = rows[i], rows[i + 5]
        x = r.get("chain_expiry")
        if x is None or x - r["day"] < 10 or q["day"] >= x:
            continue
        C, P = _by_strike(r["chain"], "C"), _by_strike(r["chain"], "P")
        K = _nearest(C, r["index"])
        c0, p0 = float(C[K]["mid"]), float(P[K]["mid"])
        # a leg priced under a tick is not a straddle the dealer quotes, and a
        # return over a mid near zero swamps the mean
        if min(c0, p0) < 0.05:
            continue
        bc, bp = float(C[K]["delta"]) * r["index"] / c0, float(P[K]["delta"]) * r["index"] / p0
        th = -bp / (bc - bp)
        later = q.get("chains_all", {}).get(x) or (q["chain"] if q.get("chain_expiry") == x else None)
        if not later:
            continue
        C1, P1 = _by_strike(later, "C"), _by_strike(later, "P")
        if K not in C1 or K not in P1:
            continue
        rets.append(th * (float(C1[K]["mid"]) / c0 - 1) + (1 - th) * (float(P1[K]["mid"]) / p0 - 1))
    return rets


def atm_variance(rows):
    """Each fifth recorded session where the front month is at least 10
    sessions out (OB5's weeks): (at-the-money implied volatility, the VIX,
    the next five sessions' realised variance), annualised by 252."""
    out = []
    lr = np.diff(np.log([r["index"] for r in rows]))
    for i in range(0, len(rows) - 5, 5):
        r = rows[i]
        x = r.get("chain_expiry")
        if x is None or x - r["day"] < 10 or not r.get("chain"):
            continue
        C = _by_strike(r["chain"], "C")
        iv = float(C[_nearest(C, r["index"])]["iv"])
        out.append((iv, r["vix"] / 100, float(np.sum(lr[i:i + 5] ** 2)) * YEAR / 5))
    return out


def delta_hedged_gains(rows, settle_index):
    """OB6: at each monthly roll, buy the at-the-money call of the next
    expiry at mid, hedge its delta at each recorded close at no interest,
    hold to expiry; the gain over the index level at purchase."""
    out = []
    for i, r in enumerate(rows):
        x = r.get("chain_expiry")
        if x is None or not r.get("chain") or (i and rows[i - 1].get("chain_expiry") == x):
            continue
        C = _by_strike(r["chain"], "C")
        K = _nearest(C, r["index"])
        c0, d, S0 = float(C[K]["mid"]), float(C[K]["delta"]), r["index"]
        hedge, S = 0.0, S0
        for q in rows[i + 1:]:
            if q["day"] >= x:
                break
            hedge += d * (q["index"] - S)
            S = q["index"]
            cq = _by_strike(q.get("chains_all", {}).get(x) or q.get("chain") or [], "C").get(K)
            d = float(cq["delta"]) if cq else d
        Sx = settle_index.get(x)
        if Sx is None:
            continue
        hedge += d * (Sx - S)
        out.append((max(Sx - K, 0) - c0 - hedge) / S0)
    return out


def _eps(x):
    return 1e-9 * max(1.0, abs(x))


def arbitrage_violations(row, tick=0.05):
    """SV2 on one close: per expiry, call prices non-increasing in strike,
    butterflies non-negative, put-call parity within half a tick; across
    expiries, total implied variance non-decreasing in T at the same k."""
    v = 0
    tv = {}
    for x, chain in (row.get("chains_all") or {}).items():
        # the chain's own carry: after the close the engine's clock stands at
        # the next session, so `x - day` is a session longer than the tenor
        # the dealer priced, and a forward read at it misses parity by the
        # carry of that session
        q0 = chain[0] if chain else {}
        F, r = q0.get("forward", row["forward"][x]), q0.get("rate", row["rate"][x])
        T = q0.get("sessions_to_expiry", x - row["day"]) / YEAR
        df = math.exp(-r * T)
        C, P = _by_strike(chain, "C"), _by_strike(chain, "P")
        ks = sorted(set(C) & set(P))
        c = [float(C[k]["mid"]) for k in ks]
        # float noise is relative: a deep in-the-money call is priced in the
        # thousands, where a line through three strikes misses by 1e-12
        v += sum(c[i + 1] > c[i] + _eps(c[i]) for i in range(len(c) - 1))
        for i in range(1, len(ks) - 1):
            a, b = ks[i] - ks[i - 1], ks[i + 1] - ks[i]
            v += (c[i - 1] * b + c[i + 1] * a) / (a + b) < c[i] - _eps(c[i])
        v += sum(abs(float(C[k]["mid"]) - float(P[k]["mid"]) - df * (F - k)) > tick / 2 for k in ks)
        for k in ks:
            tv.setdefault(round(math.log(k / F), 2), []).append((T, float(C[k]["iv"]) ** 2 * T))
    for pts in tv.values():
        pts.sort()
        v += sum(pts[i + 1][1] < pts[i][1] - _eps(pts[i][1]) for i in range(len(pts) - 1))
    return int(v)


CHAIN_FIELDS = ("strike", "right", "bid", "ask", "mid", "iv", "delta", "forward", "rate", "sessions_to_expiry")


def _slim(chain):
    """A chain's rows with only the fields the estimators read."""
    return [{k: q[k] for k in CHAIN_FIELDS if k in q} for q in chain]


def _int_keys(row):
    """Expiry-keyed maps back to integer keys: a history read from JSON has
    them as strings, and the estimators look them up by session."""
    for k in ("chains_all", "forward", "rate"):
        if isinstance(row.get(k), dict):
            row[k] = {int(x): v for x, v in row[k].items()}
    return row


def chain_pieces(h):
    """One `chain` history's pieces of every chain row: the per-close,
    per-window, per-week and per-event values `rows_from_pieces` pools across
    histories, so a row read on any multiset of histories (a grade's draw) is
    the row `chain_rows` reads on them. None for a history with nothing after
    BURN."""
    for r in h["rows"]:
        _int_keys(r)
    R = [r for r in h["rows"] if r["day"] >= BURN]
    if not R:
        return None
    settle_index = {x.get("session"): x["reference"] for r in h["rows"] for x in r.get("settlements", [])
                    if x.get("root") == "IDX" and x.get("kind") == "option"}
    f = lambda xs: np.asarray(xs, float)
    P = {"sv1": max(abs(r["strip"][21] - r["vix"]) for r in R),
         "sv2": int(sum(r["sv2"] if "sv2" in r else arbitrage_violations(r) for r in R
                        if "sv2" in r or r.get("chains_all"))),
         "r63": f([r["strip"][63] / r["strip"][21] for r in R]),
         "gt": f([r["strip"][21] > r["strip"][63] for r in R]),
         "r6": f([r["strip"][6] / r["strip"][21] for r in R]),
         "r126": f([r["strip"][126] / r["strip"][21] for r in R]),
         "r252": f([r["strip"][252] / r["strip"][21] for r in R]),
         "sk": f([r["skew21"] for r in R]),
         "w7": [corr([r["skew21"] for r in R[a:b]], [r["vix"] for r in R[a:b]]) for a, b in windows_back(len(R), YEAR)]}
    idx = np.array([r["index"] for r in R])
    lr = np.diff(np.log(idx))
    P["vr"] = f([R[i]["vix"] / realised_vol(lr[i:i + MONTH]) for i in range(len(R) - MONTH)])
    # option rules against the total-return index
    ob = {f"OB{i}{x}": [] for i in (1, 2, 3, 4) for x in "rvd"}
    tr = [r.get("tr") for r in R]
    if all(t is not None for t in tr):
        acc, trl = option_rules(R, settle_index)
        for j, k in enumerate(("PUT", "BXM", "CNDR", "BFLY"), 1):
            for a, b in windows_back(len(R), YEAR):
                w = ob_window(acc[k][a:b], trl[a:b])
                for x in "rvd":
                    ob[f"OB{j}{x}"].append(w[x])
    P["ob"] = ob
    P["ob5"] = f(straddle_weeks(R))
    P["atm"] = f(atm_variance(R)).reshape(-1, 3)
    P["ob6"] = f(delta_hedged_gains(R, settle_index))
    # names: SS1, SS2, SS3, SS4, EV
    P["names"] = all(r.get("names") for r in R)
    if P["names"]:
        a = ss1_pairs(R, h["tickers"], h["shares"])   # item 25: the five largest, by name-day
        P["ss1"] = (np.array([d for d, _ in a], int), f([x for _, x in a]))
        a, b = ss2_ss3_values(R, h["tickers"], h["shares"])
        P["ss2"] = f(a)
        P["ss3"] = (np.array([d for d, _ in b], int), f([x for _, x in b]))
        P["ss4"] = f(ss4_values(R, h["tickers"]))
        ev = ev_events(R, h["tickers"])
        P["ev"] = {k: f([x[k] for x in ev]) for k in ("EV1", "EV2", "implied", "realised")}
        P["ev"]["day"] = np.array([x["reaction"] for x in ev], int)   # item 25: each event's model year
    intr = []
    for path in h.get("intraday", []):
        q = np.array(path, float)
        intr += list(zip(np.diff(np.log(q[:, 0])), np.diff(np.log(q[:, 1]))))
    P["intr"] = f(intr).reshape(-1, 2)
    return P


def _by_year(pairs):
    """{year: values} over (days, values) pieces, years in order of first appearance."""
    g = {}
    for d, x in pairs:
        for y in dict.fromkeys(d // YEAR):
            g.setdefault(int(y), []).append(x[d // YEAR == y])
    return {y: np.concatenate(v) for y, v in g.items()}


def rows_from_pieces(pieces):
    """The chain rows pooled over histories' pieces (`chain_pieces`), a
    history repeated as often as it is drawn."""
    rows, rep = {}, {}
    pieces = [p for p in pieces if p is not None]
    if not pieces:
        return rows, rep
    cat = lambda k: np.concatenate([p[k] for p in pieces])
    rows["SV1"] = max(p["sv1"] for p in pieces); rows["SV2"] = sum(p["sv2"] for p in pieces)
    rows["SV3"] = float(np.median(cat("r63"))); rows["SV4"] = float(np.mean(cat("gt")))
    rows["SV5_9D"] = float(np.median(cat("r6"))); rows["SV5_6M"] = float(np.median(cat("r126")))
    rows["SV5_1Y"] = float(np.median(cat("r252")))
    sk = cat("sk")
    rows["SV6_median"] = float(np.median(sk)); rows["SV6_sd"] = float(np.std(sk, ddof=1))
    rows["SV7"] = st.median([x for p in pieces for x in p["w7"]]); rows["SV8"] = float(np.median(cat("vr")))
    intr = cat("intr")
    if len(intr) > 2:
        rows["SV9"] = ols(intr[:, 0], intr[:, 1])[1]
    for k in pieces[0]["ob"]:
        v = [x for p in pieces for x in p["ob"][k]]
        if v:
            rows[k] = st.median(v)
    a = cat("atm")
    if len(a):
        # reported, not graded: the at-the-money level OB5 and OB6 trade
        rep["ATM"] = {"atm_iv_over_vix": float(np.median(a[:, 0] / a[:, 1])),
                      "rv5_over_atm_iv2": float(a[:, 2].mean() / (a[:, 0] ** 2).mean()),
                      "rv5_over_vix2": float(a[:, 2].mean() / (a[:, 1] ** 2).mean()), "weeks": len(a)}
    ob5 = cat("ob5")
    if len(ob5):
        rows["OB5"] = float(np.mean(ob5))
    ob6 = cat("ob6")
    if len(ob6) > 2:
        m, se = float(np.mean(ob6)), float(np.std(ob6, ddof=1) / math.sqrt(len(ob6)))
        rows["OB6"] = m
        rep["OB6"] = {"mean": m, "t": m / se if se else float("nan"), "against": -0.0010}
    named = [p for p in pieces if p["names"]]
    if named and sum(len(p["ss1"][1]) for p in named):
        rows["SS1"] = st.median(float(np.median(v)) for v in _by_year([p["ss1"] for p in named]).values())
        rows["SS2"] = float(np.median(np.concatenate([p["ss2"] for p in named])))
        rows["SS3"] = st.median(float(np.median(v)) for v in _by_year([p["ss3"] for p in named]).values())
        rows["SS4"] = float(np.median(np.concatenate([p["ss4"] for p in named])))
    if named:
        ev = {k: np.concatenate([p["ev"][k] for p in named]) for k in ("EV1", "EV2", "implied", "realised")}
        if len(ev["EV1"]) and all("day" in p["ev"] for p in named):
            # item 25 (the convention round): each row the median over model years of its per-year statistic (EV1 and
            # EV2 the year's median, EV3 the year's RMS ratio), as the band's centre is built (ev_rows, per calendar
            # year); the pooled readings are reported. Pieces without event days are not read.
            yr = lambda k: _by_year([(p["ev"]["day"], p["ev"][k]) for p in named])
            e1, e2, im, re_ = yr("EV1"), yr("EV2"), yr("implied"), yr("realised")
            rows["EV1"] = st.median(float(np.median(v)) for v in e1.values())
            rows["EV2"] = st.median(float(np.median(v)) for v in e2.values())
            rows["EV3"] = st.median(float(math.sqrt((im[y] * im[y]).mean() / (re_[y] * re_[y]).mean())) for y in im)
            m, r = ev["implied"], ev["realised"]
            rep["EV_events"] = len(m); rep["EV_years"] = len(im)
            rep["EV_pooled"] = {"EV1": float(np.median(ev["EV1"])), "EV2": float(np.median(ev["EV2"])),
                                "EV3": float(math.sqrt((m * m).mean() / (r * r).mean()))}
        elif len(ev["EV1"]):
            rep["EV_not_read"] = "pieces without event days (before item 25)"
    # AX1 is read on traded histories, where the dealer holds options and
    # exercises them (ptv22traded's `names` part); an untraded history has
    # nothing for it to read.
    return rows, rep


def chain_rows(chain):
    return rows_from_pieces([chain_pieces(h) for h in chain])


# ------------------------------------- SS, EV: the single-name estimators
#
# Each reads the `chain` part's closes from BURN on (`R`, one per recorded
# close): `names[t][h]`, the Cboe VIX formula on name t's h-session strip
# (its 30-day implied volatility at h = 21, as Cboe's VXAPL family reads
# it), `skew21_names[t]` its SKEW, `strip[63]` the index's VIX3M analogue,
# `skew21` the index's SKEW, `closes`, and the earnings calendar the close
# published.

def ss1_pairs(R, tickers, shares=None):
    """SS1 per name-day: (session, the name's 21-session strip volatility over
    its next 21 sessions' realised volatility), for every close with 63
    sessions after it (as SS3 reads). Item 25 (the convention round): the five
    largest roster names by capitalisation at the history's first close, a
    fixed set as Cboe's five single-name indices are, each name-day a value,
    so a year's median is over its pooled name-days, as the real's is. Without
    `shares` (a caller before item 25) every listed name, as before."""
    C = np.array([r["closes"] for r in R])
    nr = np.diff(np.log(C), axis=0)
    if shares is not None:
        cap = C[0] * np.asarray(shares, float)
        keep = {tickers[j] for j in np.argsort(-cap, kind="stable")[:5]}
    else:
        keep = set(tickers)
    out = []
    for i, r in enumerate(R[:-QUARTER]):
        for j, t in enumerate(tickers):
            if t in keep and t in r["names"]:
                out.append((r["day"], float(r["names"][t][21] / realised_vol(nr[i:i + MONTH, j]))))
    return out


def ss2_ss3_values(R, tickers, shares):
    """SS2 and SS3 per close: the three-month implied correlation from the
    index's 63-session strip and the names' (Cboe's formula, capitalisation
    weights), x 100; and (session, implied less the next 63 sessions'
    realised correlation, equal weights)."""
    C = np.array([r["closes"] for r in R])
    nr = np.diff(np.log(C), axis=0)
    sh = np.asarray(shares, float)
    ss2, ss3 = [], []
    for i, r in enumerate(R[:-QUARTER]):
        if not all(t in r["names"] for t in tickers):
            continue
        w = sh * C[i] / (sh * C[i]).sum()
        imp = implied_correlation((r["strip"][63] / 100) ** 2,
                                  (np.array([r["names"][t][63] for t in tickers]) / 100) ** 2, w)
        ss2.append(100 * imp)
        ss3.append((r["day"], imp - realised_correlation(nr[i:i + QUARTER])))
    return ss2, ss3


def ss4_values(R, tickers):
    """SS4 per close: the median over names of the index's 21-session SKEW
    less the name's, positive when names are less negatively skewed than
    the index (Bakshi, Kapadia and Madan 2003)."""
    return [float(np.median([r["skew21"] - r["skew21_names"][t] for t in tickers if t in r["skew21_names"]]))
            for r in R if r.get("skew21_names")]


def ev_events(R, tickers):
    """EV1 to EV3's events: each report the calendar listed at the close
    before its reaction session (`sessions_ahead` 0 after a close), with 16
    recorded closes before it, read by `ev_event` on the name's 21-session
    strip volatility and its closes (T one model month)."""
    day_ix = {r["day"]: i for i, r in enumerate(R)}
    days = [r["day"] for r in R]
    out = []
    for r in R:
        for e in r.get("earnings", []):
            if e.get("sessions_ahead") != 0 or e["ticker"] not in tickers:
                continue
            i = day_ix.get(e["session"])
            if i is None or i < 16 or not all(e["ticker"] in R[q]["names"] for q in (i - 16, i - 1, i)):
                continue
            j = tickers.index(e["ticker"])
            ivn = {R[q]["day"]: R[q]["names"][e["ticker"]][21] for q in (i - 16, i - 1, i)}
            cl = {R[q]["day"]: R[q]["closes"][j] for q in (i - 1, i)}
            x = ev_event(ivn, cl, days, R[i]["day"], T=MONTH / YEAR)
            if x:
                x.update({"ticker": e["ticker"], "reaction": R[i]["day"]})
                out.append(x)
    return out


# =============================================================== measure / grade

def _revive(o):
    """JSON keeps dict keys as strings; horizons, expiries and sessions are
    integers in every recorded dict, so digit keys come back as ints."""
    if isinstance(o, dict):
        return {(int(k) if isinstance(k, str) and k.lstrip("-").isdigit() else k): _revive(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_revive(v) for v in o]
    return o


def _load(adir, part):
    return [_revive(json.load(open(f))) for f in sorted(pathlib.Path(adir).glob(f"{part}-*.json"))]


def model_rows(adir):
    rows, rep = {}, {}
    free, chain = _load(adir, "free"), _load(adir, "chain")
    if free:
        r, p = free_rows(free); rows.update(r); rep.update(p)
        rep["free_histories"] = len(free)
    if chain:
        r, p = chain_rows(chain); rows.update(r); rep.update(p)
        rep["chain_histories"] = len(chain)
    # the ptv22traded stage's parts: traded (MG2, XD1, XD2; MG3, XD4, XD5,
    # AX2 once options are listed), push (XD3) and metaorder (IF4)
    r, p = _traded().part_rows(adir); rows.update(r); rep.update(p)
    return rows, rep


def _task(spec):
    part, engine, base, dials, seed, years = spec
    tf = _engine(engine)
    p = _params(tf, base, dials)
    t0 = time.time()
    if part == "free":
        res = run_free(tf, p, seed, years)
    elif part == "chain":
        res = run_chain(tf, p, seed, years)
    else:
        res = _traded().RUN[part](tf, p, seed, years)
    return part, seed, res, time.time() - t0


def _parse_arm(text):
    head, _, tail = text.partition(":")
    name, _, base = head.partition("@")
    dials = {k: float(v) for k, v in (kv.split("=") for kv in tail.split(",") if kv)}
    return name, base or "pt-v22", dials


def _parse_seeds(text):
    out = []
    for part in text.split(","):
        a, _, b = part.partition("-")
        out.extend(range(int(a), int(b or a) + 1))
    return out


def measure(a):
    name, base, dials = _parse_arm(a.arm)
    # --switches phase1: the phase 1 switches beneath the arm's own dials
    # (ptv22traded.PHASE1), for an arm written before they existed
    switches = getattr(a, "switches", "none") or "none"
    if switches != "none":
        dials = {**_traded().SWITCHES[switches], **dials}
    outdir = pathlib.Path(a.out) / name
    tf = _engine(a.engine)
    probe = tf.Engine(seed=1, universe=tf.Universe.random(ROSTER[0], seed=ROSTER[1]), model=_params(tf, base, dials))
    for part in a.parts.split(","):
        require(probe, part)
    outdir.mkdir(parents=True, exist_ok=True)
    json.dump({"arm": name, "base": base, "dials": dials, "years": a.years, "switches": switches,
               "tradefloor": getattr(tf, "__version__", "?"), "fingerprint": _params(tf, base, dials).fingerprint},
              open(outdir / "meta.json", "w"), indent=1)
    specs = [(part, a.engine, base, dials, s, a.years) for part in a.parts.split(",") for s in _parse_seeds(a.seeds)
             if not (outdir / f"{part}-{s}.json").exists()]
    with ProcessPoolExecutor(max_workers=a.workers) as ex:
        for part, seed, res, t in ex.map(_task, specs):
            json.dump(_clean(res), open(outdir / f"{part}-{seed}.json", "w"))
            print(f"{name} {part} {seed} {t:.0f}s", flush=True)


def grade(a):
    realj = json.load(open(a.real))
    B = bands(realj)
    out = {}
    count_rows = _traded().COUNT_ROWS
    for adir in a.dirs:
        rows, rep = model_rows(adir)
        meta = json.load(open(pathlib.Path(adir) / "meta.json"))
        print(f"== {meta['arm']} on {meta['base']} ({meta['fingerprint']}), tradefloor {meta['tradefloor']}")
        res = {}
        for k in ORDER:
            w, lo, hi, rule = B[k]
            if k not in rows:
                why = rule
                if k in ("MG3", "XD4", "XD5", "AX2") and rep.get("options_listed") is False:
                    why = "the engine lists no options; " + rule
                print(f"  {k:10s} {w[:64]:64s} {'not read':>9s}  ({why})")
                continue
            v = rows[k]
            ok = v == v and (lo is None or v >= lo - 1e-12) and (hi is None or v <= hi + 1e-12)
            if k == "OB6":
                ok = ok and rep.get("OB6", {}).get("t", 0) < -1.645
            if k == "SS4":
                ok = v == v and v > 0
            if k in count_rows:          # the number of rules that break their bound
                ok = v == 0
            res[k] = {"value": v, "low": lo, "high": hi, "rule": rule, "pass": bool(ok)}
            print(f"  {k:10s} {w[:64]:64s} {v:9.4g}  [{_fmt(lo)}, {_fmt(hi)}] {'pass' if ok else 'FAIL'}")
        print("  reported:", json.dumps(_clean(rep))[:2000])
        out[meta["arm"]] = {"meta": meta, "rows": res, "reported": _clean(rep)}
    if a.json:
        json.dump(out, open(a.json, "w"), indent=1)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("real"); r.add_argument("--out", default=str(DATA / "real.json"))
    m = sub.add_parser("measure")
    m.add_argument("--arm", default="pt-v22"); m.add_argument("--seeds", default="201-203")
    m.add_argument("--years", type=int, default=21); m.add_argument("--parts", default="free,chain")
    m.add_argument("--workers", type=int, default=3); m.add_argument("--engine")
    m.add_argument("--switches", default="none", choices=("none", "phase1"),
                   help="phase1: the phase 1 switches beneath the arm's dials (ptv22traded.PHASE1)")
    m.add_argument("--out", required=True)
    g = sub.add_parser("grade"); g.add_argument("dirs", nargs="+")
    g.add_argument("--real", default=str(DATA / "real.json")); g.add_argument("--json")
    a = ap.parse_args(argv)
    try:
        return {"real": real, "measure": measure, "grade": grade}[a.cmd](a)
    except MissingAPI as e:
        sys.exit(f"ptv22: {e}")


if __name__ == "__main__":
    main()
