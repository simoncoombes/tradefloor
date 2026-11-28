"""The long-run estimators: ONE implementation, used on the tape and on the
model. Moved here from programme/results/crashcheck/ (episodes.py,
compare.py, screen.py, reversion.py, replay.py), which now import from this
file. The arithmetic is unchanged; `reproduce.py` checks that the crash
check's published figures come out of this file to the printed digit.

Tape side (one history):
    summarise(level, vix, years)       drawdowns, tails, VIX shares  (episodes.py)
    stats([(level, vix)])              fear spells, same-day fear    (screen.py)
    table(rows([vix]))                 VIX half-life by level        (reversion.py)
Model side (many histories, first year of each discarded):
    pooled(runs)                       the same as summarise, pooled (compare.py)
    stats(pairs), table(rows(vixes))   as above, pooled
Replay (real VIX imposed):
    measures(index, names, calm_end)   drawdown, month vol, correlation (replay.py)
New here:
    vix_levels_part/_combine           share of sessions in each VIX band
    halflives(table)                   the half-life column of `table`
    year_stats(level, bounds)          return and crash rate in each year
"""
import math
import statistics as st

BURN = 252


# ---------------------------------------------------------------- episodes.py

def drawdowns(level, threshold):
    """Episodes where the index falls `threshold` from its running peak.
    Each: (peak index, trough index, recovery index or None, depth)."""
    out, peak_i, i, n = [], 0, 1, len(level)
    while i < n:
        if level[i] >= level[peak_i]:
            peak_i = i; i += 1; continue
        if level[i] <= level[peak_i] * (1 - threshold):
            trough_i = i
            j = i
            while j < n and level[j] < level[peak_i]:
                if level[j] < level[trough_i]:
                    trough_i = j
                j += 1
            rec = j if j < n else None
            out.append((peak_i, trough_i, rec, 1 - level[trough_i] / level[peak_i]))
            if rec is None:
                break
            peak_i, i = rec, rec + 1
            continue
        i += 1
    return out


def rv21(rets, i):
    w = rets[max(0, i - 20):i + 1]
    return st.pstdev(w) * math.sqrt(252) * 100 if len(w) > 2 else float("nan")


def summarise(level, vix, years):
    rets = [level[i] / level[i - 1] - 1 for i in range(1, len(level))]
    s = {"years": years, "sessions": len(rets)}
    for th in (0.10, 0.20):
        eps = drawdowns(level, th)
        key = "dd%d" % int(th * 100)
        s[key + "_per_decade"] = len(eps) / years * 10
        s[key + "_n"] = len(eps)
        if eps:
            s[key + "_depth_median"] = st.median(e[3] for e in eps)
            s[key + "_depth_max"] = max(e[3] for e in eps)
            s[key + "_to_trough_median"] = st.median(e[1] - e[0] for e in eps)
            recs = [e[2] - e[1] for e in eps if e[2] is not None]
            s[key + "_recover_median"] = st.median(recs) if recs else None
            if th == 0.20:
                s[key + "_peak_vix_median"] = st.median(max(vix[e[0]:e[1] + 1]) for e in eps)
                s[key + "_worst_rv21_median"] = st.median(
                    max(rv21(rets, k - 1) for k in range(max(1, e[0]), e[1] + 1)) for e in eps)
    for cut in (3, 5, 7):
        s["days_below_-%d_per_decade" % cut] = sum(1 for r in rets if r < -cut / 100) / years * 10
    s["worst_session_pct"] = min(rets) * 100
    for v in (30, 40, 60):
        s["vix_share_above_%d" % v] = sum(1 for x in vix if x > v) / len(vix)
    dec = [max(vix[i:i + 2520]) for i in range(0, len(vix) - 2519, 2520)] or [max(vix)]
    s["vix_max_per_decade_median"] = st.median(dec)
    s["vix_max"] = max(vix)
    s["ann_vol_pct"] = st.pstdev(rets) * math.sqrt(252) * 100
    s["ann_return_pct"] = ((level[-1] / level[0]) ** (1 / years) - 1) * 100
    return s


# ----------------------------------------------------------------- compare.py
# pooled(runs) is split into a per-history part and a combine step so a
# bootstrap over seeds reuses each history's episodes. combine(parts) does
# exactly the arithmetic of the crash check's pooled(runs), in run order.

def _fast_pstdev(x):
    m = math.fsum(x) / len(x)
    return math.sqrt(math.fsum((v - m) ** 2 for v in x) / len(x))


def pooled_part(level, vix, burn=BURN):
    lvl, vx = level[burn:], vix[burn:]
    rets = [lvl[i] / lvl[i - 1] - 1 for i in range(1, len(lvl))]
    p = {"years": (len(level) - burn - 1) / 252, "rets": rets, "vix": vx,
         "ep": {10: [], 20: []}, "tails": {c: sum(1 for x in rets if x < -c / 100) for c in (3, 5, 7)},
         "worst": min(rets) if rets else 0.0, "vmax": max(vx),
         "vcnt": {v: sum(1 for x in vx if x > v) for v in (30, 40, 60)},
         "dec_max": [max(vx[i:i + 2520]) for i in range(0, len(vx) - 2519, 2520)],
         "cagr": ((level[-1] / level[burn]) ** (252 / (len(level) - burn - 1)) - 1) * 100}
    for th in (10, 20):
        for e in drawdowns(lvl, th / 100):
            d = {"depth": e[3], "to_trough": e[1] - e[0],
                 "recover": (e[2] - e[1]) if e[2] is not None else None}
            if th == 20:
                d["peak_vix"] = max(vx[e[0]:e[1] + 1])
                d["worst_rv21"] = max(rv21(rets, k - 1) for k in range(max(1, e[0]), e[1] + 1))
            p["ep"][th].append(d)
    return p


def pooled_combine(parts, exact=True):
    """Rates summed over every history; medians over every pooled episode."""
    years = sum(p["years"] for p in parts)
    out = {"years": years}
    worst = 0.0
    for p in parts:
        worst = min(worst, p["worst"])
    for th in (10, 20):
        E = [e for p in parts for e in p["ep"][th]]; k = f"dd{th}"
        out[k + "_per_decade"] = len(E) / years * 10
        out[k + "_n"] = len(E)
        if E:
            out[k + "_depth_median"] = st.median(e["depth"] for e in E)
            out[k + "_depth_max"] = max(e["depth"] for e in E)
            out[k + "_to_trough_median"] = st.median(e["to_trough"] for e in E)
            rec = [e["recover"] for e in E if e["recover"] is not None]
            out[k + "_recover_median"] = st.median(rec) if rec else None
            if th == 20:
                out[k + "_peak_vix_median"] = st.median(e["peak_vix"] for e in E)
                # NaN-safe: a fall too short for a 21-session window reads NaN,
                # and one NaN corrupts a median (fresh seed 140, calm-regime).
                w = [e["worst_rv21"] for e in E if e["worst_rv21"] == e["worst_rv21"]]
                out[k + "_worst_rv21_median"] = st.median(w) if w else float("nan")
    for c in (3, 5, 7):
        out[f"days_below_-{c}_per_decade"] = sum(p["tails"][c] for p in parts) / years * 10
    out["worst_session_pct"] = worst * 100
    nv = sum(len(p["vix"]) for p in parts)
    for v in (30, 40, 60):
        out[f"vix_share_above_{v}"] = sum(p["vcnt"][v] for p in parts) / nv
    dec_max = [x for p in parts for x in p["dec_max"]]
    out["vix_max_per_decade_median"] = st.median(dec_max) if dec_max else None
    out["vix_max"] = max(p["vmax"] for p in parts)
    rets_all = [x for p in parts for x in p["rets"]]
    out["ann_vol_pct"] = (st.pstdev(rets_all) if exact else _fast_pstdev(rets_all)) * math.sqrt(252) * 100
    out["ann_return_pct"] = st.median(p["cagr"] for p in parts)
    return out


def pooled(runs, burn=BURN):
    """runs: dicts with "level" and "vix", the burn year still in."""
    return pooled_combine([pooled_part(r["level"], r["vix"], burn) for r in runs])


# ------------------------------------------------------------------ screen.py

def spells(v, thr, gap=10):
    out, i, n = [], 0, len(v)
    while i < n:
        if v[i] > thr:
            j = i
            while j < n and v[j] > thr:
                j += 1
            if out and i - (out[-1][0] + out[-1][1]) <= gap:
                out[-1] = (out[-1][0], j - out[-1][0])
            else:
                out.append((i, j - i))
            i = j
        else:
            i += 1
    return out


def stats_part(l, v):
    p = {"n": len(v), "sp": {thr: spells(v, thr) for thr in (30, 40)},
         "n40": sum(1 for x in v if x > 40), "vmax": max(v),
         "r": [l[i] / l[i - 1] - 1 for i in range(1, len(l))], "d1": [], "d3": []}
    p["c5"] = sum(1 for x in p["r"] if x < -0.05); p["c3"] = sum(1 for x in p["r"] if x < -0.03)
    for i in range(1, len(l)):
        x = l[i] / l[i - 1] - 1
        if -0.015 <= x <= -0.005:
            p["d1"].append(v[i] - v[i - 1])
        elif x < -0.03:
            p["d3"].append(v[i] - v[i - 1])
    return p


def stats_combine(parts, exact=True):
    yrs = sum(p["n"] for p in parts) / 252
    s = {}
    for thr in (30, 40):
        sp = [x for p in parts for x in p["sp"][thr]]
        s[f"spells{thr}_per_decade"] = len(sp) / yrs * 10
        s[f"spell{thr}_mean_len"] = st.fmean(x[1] for x in sp) if sp else 0.0
    s["share_vix40"] = sum(p["n40"] for p in parts) / sum(p["n"] for p in parts)
    r = [x for p in parts for x in p["r"]]
    s["dn5_per_decade"] = sum(p["c5"] for p in parts) / yrs * 10
    s["dn3_per_decade"] = sum(p["c3"] for p in parts) / yrs * 10
    s["index_vol"] = (st.pstdev(r) if exact else _fast_pstdev(r)) * math.sqrt(252) * 100
    # Same-day fear: mean VIX change (points) on sessions the index falls
    # between 0.5 and 1.5 per cent, and falls more than 3 per cent. One
    # estimator on both sides, not facts' pooled gauge.
    d1 = [x for p in parts for x in p["d1"]]; d3 = [x for p in parts for x in p["d3"]]
    s["fear_dn1"] = st.fmean(d1) if d1 else float("nan")
    s["fear_dn3"] = st.fmean(d3) if d3 else float("nan")
    s["ceiling_hits"] = sum(1 for p in parts if p["vmax"] >= 181.3)
    return s


def stats(pairs):
    """pairs: list of (level, vix) histories, burn already removed."""
    return stats_combine([stats_part(l, v) for l, v in pairs])


# --------------------------------------------------------------- reversion.py

B = [(0, 15), (15, 20), (20, 25), (25, 30), (30, 40), (40, 60), (60, 999)]


def rows(vixes):
    """(VIX, log distance from trailing one-year median, next-session change
    in log VIX), pooled over histories. The median is refreshed every fifth
    session. KNOWN QUIRK, kept so the published figures reproduce: `a` is
    carried across histories, so sessions 252-254 of every history after
    the first use the previous history's last median. Fixing it moves
    pt-v19's 20-25 half-life from 31.0 to 31.3 sessions and nothing else in
    reversion.txt (reproduce.py); rows_one() is the fixed form, used only
    for the seed bootstrap's standard errors."""
    out = []
    for vix in vixes:
        lv = [math.log(x) for x in vix]
        for i in range(252, len(lv) - 1, 1):
            a = st.median(lv[i - 252:i]) if i % 5 == 0 or not out else a  # noqa: F821
            out.append((vix[i], lv[i] - a, lv[i + 1] - lv[i]))
    return out


def rows_one(vix):
    """rows([vix]) for one history, without the carry-over."""
    return rows([vix])


def table(out):
    res = {}
    for lo, hi in B:
        sub = [(d, c) for v, d, c in out if lo <= v < hi]
        if len(sub) < 20:
            res[lo] = None; continue
        mx, my = st.fmean(d for d, _ in sub), st.fmean(c for _, c in sub)
        k = -my / mx if abs(mx) > 1e-9 else float("nan")
        res[lo] = (len(sub), mx, k, math.log(0.5) / math.log(1 - k) if 0 < k < 1 else float("nan"))
    return res


def row_sums(out):
    """Per-bucket (n, sum of distance, sum of change): what table() needs,
    so a bootstrap over histories can add them instead of re-reading rows."""
    res = {lo: [0, 0.0, 0.0] for lo, _ in B}
    for v, d, c in out:
        for lo, hi in B:
            if lo <= v < hi:
                r = res[lo]; r[0] += 1; r[1] += d; r[2] += c; break
    return res


def table_from_sums(sums_list):
    """table() from added row_sums (bootstrap use; equal to table() up to
    floating-point summation order)."""
    res = {}
    for lo, _ in B:
        n = sum(s[lo][0] for s in sums_list)
        if n < 20:
            res[lo] = None; continue
        mx = sum(s[lo][1] for s in sums_list) / n; my = sum(s[lo][2] for s in sums_list) / n
        k = -my / mx if abs(mx) > 1e-9 else float("nan")
        res[lo] = (n, mx, k, math.log(0.5) / math.log(1 - k) if 0 < k < 1 else float("nan"))
    return res


def halflives(t):
    """{bucket low: half-life in sessions or None}"""
    return {lo: (None if t[lo] is None or t[lo][3] != t[lo][3] else t[lo][3]) for lo, _ in B}


# ------------------------------------------------------------------ replay.py

def rets(x):
    return [x[i] / x[i - 1] - 1 for i in range(1, len(x))]


def rolling_corr(names, w=21):
    """Mean pairwise correlation of daily returns over trailing w sessions."""
    R = [rets(n) for n in names]
    out = []
    for end in range(w, len(R[0]) + 1):
        cols = [r[end - w:end] for r in R]
        z = []
        for c in cols:
            m, s = st.fmean(c), st.pstdev(c)
            z.append([(x - m) / s for x in c] if s > 0 else None)
        z = [c for c in z if c]
        tot, k = 0.0, 0
        for a in range(len(z)):
            for b in range(a + 1, len(z)):
                tot += sum(x * y for x, y in zip(z[a], z[b])) / w; k += 1
        out.append(tot / k)
    return out


def measures(index, names, calm_end, paths=True):
    """calm_end: sessions before the real VIX first closes above 30."""
    r = rets(index)
    peak = index[0]; mdd = 0.0
    for x in index:
        peak = max(peak, x); mdd = max(mdd, 1 - x / peak)
    rv = [st.pstdev(r[i - 21:i]) * math.sqrt(252) * 100 for i in range(21, len(r) + 1)]
    rc = rolling_corr(names)
    k = max(1, calm_end - 21)
    out = {"max_drawdown": mdd, "peak_rv21": max(rv), "calm_rv21": st.median(rv[:k]),
           "corr_calm": st.median(rc[:k]), "corr_peak": max(rc)}
    if paths:
        out |= {"rv21_path": rv, "corr_path": rc,
                "dd_path": [1 - x / max(index[:i + 1]) for i, x in enumerate(index)]}
    return out


# ------------------------------------------------------------------------ new

LEVELS = [(0, 15), (15, 20), (20, 25), (25, 30), (30, 40), (40, 60), (60, math.inf)]


def vix_levels_part(vix):
    return [sum(1 for x in vix if lo <= x < hi) for lo, hi in LEVELS]


def vix_levels_combine(parts):
    n = sum(sum(p) for p in parts)
    return {lo: sum(p[k] for p in parts) / n for k, (lo, _) in enumerate(LEVELS)}


def year_stats(level, bounds):
    """bounds: list of (a, b) into the daily returns r[i] = level[i+1] /
    level[i] - 1, one pair per year, b exclusive. Each year's return is
    level[b] / level[a] - 1 and its crash count is the returns under -3 per
    cent in r[a:b]. (For a model history year k is r[252k : 252(k+1)], the
    convention of crashcheck/RESULT.md's by-year table.)
    Returns [(return %, sessions under -3%, sessions)]."""
    out = []
    for a, b in bounds:
        r = [level[i + 1] / level[i] - 1 for i in range(a, b)]
        out.append(((level[b] / level[a] - 1) * 100, sum(1 for x in r if x < -0.03), len(r)))
    return out
