"""futrows.py: the pt-v22 futures rows read on many long histories without keeping each one.

`ptv22.py measure --parts free` keeps every history whole (about 40 MB of JSON for 21 years), and `free_rows` pools
them all in memory, which does not scale to the grade's 270 histories. This runs the same `ptv22.run_free` in each
worker, reduces the history at once to each row's poolable pieces (sums, counts and per-window values), writes those
and a slim series for diagnosis, and drops the history. `rows()` pools the pieces back into exactly what
`ptv22.free_rows` reads on the same histories (`check` compares the two), and the gate (passprob_futures.py) draws
histories from the pieces.

    python futrows.py run --arm "NAME@BASE:dials" [--switches phase1] --seeds 9001-9270 --years 21 \\
        --workers 9 --out DIR [--engine PATH]
    python futrows.py rows DIR [DIR ...]          every row on the pieces in DIR
    python futrows.py check FREE_DIR              pieces against ptv22.free_rows on histories kept whole
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import statistics as st
import sys
import time
from concurrent.futures import ProcessPoolExecutor

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
LONGRUN = HERE.parents[1] / "longrun"
sys.path.insert(0, str(LONGRUN))
import ptv22 as P  # noqa: E402

YEAR, MONTH, BURN = P.YEAR, P.MONTH, P.BURN
#: VIX contracts and oil contracts kept in the diagnosis series.
DIAG_VIX, DIAG_OIL = 6, 4
#: Quote fields kept beside ModelAPI.quote's (for diagnosis only; no estimator reads them).
EXTRA_QUOTE = ("expected", "premium", "basis_bp", "daily_volume", "loading")


def _quote_with_extras(self, sym):
    q = self.e.quote(sym)
    return {k: (float(q[k]) if q.get(k) is not None else None)
            for k in ("bid", "ask", "mid", "mark", "fair", "initial_margin", "multiplier") + EXTRA_QUOTE if k in q}


# ------------------------------------------------------------------ pieces


def _sums(x, y):
    x, y = np.asarray(x, float), np.asarray(y, float)
    return [int(len(x)), float(x.sum()), float(y.sum()), float((x * x).sum()), float((y * y).sum()), float((x * y).sum())]


def _slope(s):
    n, sx, sy, sxx, syy, sxy = s
    d = n * sxx - sx * sx
    return (n * sxy - sx * sy) / d if n > 1 and d > 0 else float("nan")


def _corr(s):
    n, sx, sy, sxx, syy, sxy = s
    a, b = n * sxx - sx * sx, n * syy - sy * sy
    return (n * sxy - sx * sy) / math.sqrt(a * b) if n > 1 and a > 0 and b > 0 else float("nan")


def _add(a, b):
    return [x + y for x, y in zip(a, b)]


def pieces(h):
    """One history's pieces of every row free_rows reads, in free_rows' own order and code."""
    out = {"seed": h.get("seed")}
    # IF1, IF2: per-window values (free_rows takes the median over every history's windows)
    fr = [(s, x, q) for s, sym, x, q in P._front_series(h, "IDX") if x - s > 6 and q.get("fair")]
    obs = [{"basis_bp": 1e4 * math.log(q["mark"] / q["fair"]), "tau": (x - s) / YEAR, "i": s, "date": s}
           for s, x, q in fr]
    w = P._if_windows(obs, YEAR) if len(obs) >= YEAR else []
    out["if_w"] = [[x["IF1"], x["IF2"]] for x in w]
    # IF3, VF7, RF3: the largest difference per history
    for row, root in (("IF3", "IDX"), ("VF7", "VIX"), ("RF3", "FF")):
        ev = [x for day in h["settlements"] for x in day if x.get("root") == root and x.get("kind") == "future"]
        if ev:
            out[row] = (max(abs(x["value"] - x["reference"]) / x["reference"] for x in ev) if row == "IF3"
                        else max(abs(x["value"] - x["reference"]) for x in ev))
    # NS1, NS2: per history (free_rows takes the median over histories)
    frm = {s: q["mark"] for s, sym, x, q in P._front_series(h, "IDX")}
    nm = h.get("night_mid", [])
    g, it = [], []
    for s in range(BURN + 1, len(nm)):
        if nm[s] and s in frm and s - 1 in frm and P._front_series_same(h, s):
            g.append(math.log(nm[s] / frm[s - 1])); it.append(math.log(frm[s] / nm[s]))
    if len(g) > 20:
        g, it = np.array(g), np.array(it)
        out["NS1"] = float(g.var() / (g.var() + it.var()))
    nb = [x for x in h.get("night", [])[BURN:] if x is not None]
    if len(nb) > 20:
        out["NS2"] = float(np.std(nb, ddof=1))
    # VF1 to VF6
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
    pcs = list(P.vf_session_stats(curve, vix, sessions).values())
    con = [p["contango"] for p in pcs if "contango" in p]
    out["vf1"] = [float(sum(con)), len(con)]
    out["vf2"] = [p["slope"] for p in pcs if "slope" in p]
    b30 = [p["back30"] for p in pcs if "back30" in p]
    out["vf3"] = [float(sum(b30)), len(b30)]
    for kk in (1, 2, 4):
        pts = [p[f"dF{kk}"] for p in pcs if f"dF{kk}" in p]
        out[f"vf4_{kk}"] = _sums([x[1] for x in pts], [x[0] for x in pts])
    r = P.vf5_returns(curve, contracts, sessions)
    rd = sorted(r)
    out["vf5"] = [float(np.log1p(np.array([r[d] for d in rd[a:b]])).sum()) for a, b in P.windows_back(len(rd), YEAR)]
    errs = {21: [], 63: [], 126: []}
    for x, c in contracts.items():
        if c["final"] == c["final"]:
            for hh in errs:
                j = c["_ix"].get(x - hh)
                if j is not None:
                    errs[hh].append(float(c["settle"][j]) - c["final"])
    out["vf6"] = {hh: [float(np.sum(e)), len(e)] for hh, e in errs.items()}
    # VF8, RF5, OF4: per-history mean errors by horizon
    for row, key, real_key, hs in (("VF8", "vix", "vix", (21, 63, 126)), ("RF5", "policy_rate", "policy", (63, 126, 252)),
                                   ("OF4", "oil", "oil", (21, 63, 126))):
        out[row.lower()] = P._forecast_t(h, key, h[real_key], hs)
    # RF1, RF2
    rf1 = {1: [], 3: [], 6: [], 12: []}
    rf2m, rf2o = [], []
    pol = [P._pct(x) for x in h["policy"]]
    for s in range(BURN, len(h["futures"])):
        for sym, x, q in h["futures"][s].get("FF", []):
            n = x - s
            for m in rf1:
                # item 25: RF1_m1 reads the contract at the first session of its month (n = 20), as ZQ=F on the
                # month's first session is; the longer horizons at 21 m as before
                if n == (20 if m == 1 else 21 * m) and x < len(pol):
                    rf1[m].append(100 * ((100 - q["mark"]) - float(np.mean(pol[x - MONTH + 1: x + 1]))))
    nxt = [f.get("next_meeting") for f in h["forecast"]]
    for s in range(BURN + 1, len(h["futures"])):
        a, b = h["futures"][s - 1].get("FF", []), h["futures"][s].get("FF", [])
        if a and b and a[0][0] == b[0][0]:
            d = 100 * (b[0][2]["mark"] - a[0][2]["mark"])
            if nxt[s - 1] == s:
                rf2m.append(d)
            elif abs(pol[s] - pol[s - 1]) < 1e-9:
                rf2o.append(d)
            # item 25: an unscheduled move (the rate changed off a meeting) is left out of "other", as the real does
    out["rf1"] = {m: [float(np.sum(v)), len(v)] for m, v in rf1.items()}
    m_, o_ = np.asarray(rf2m, float), np.asarray(rf2o, float)
    out["rf2"] = [float((m_ * m_).sum()), len(m_), float((o_ * o_).sum()), len(o_)]
    # RF4: per-window medians
    v = []
    for s in range(BURN, len(h["futures"])):
        lst = h["futures"][s].get("TR3", [])
        if len(lst) >= 8:
            v.append(100 * (float(np.mean([100 - q["mark"] for sym, x, q in lst[:8]])) - P._pct(h["y2"][s])))
    out["rf4"] = [float(np.median(v[a:b])) for a, b in P.windows_back(len(v), YEAR)]
    # OF1 to OF3
    inv = np.log(np.maximum(np.asarray([np.nan if x is None else x for x in h["inventory"]], float), 1e-12))
    of1, slope, invd, d1, d4 = [], [], [], [], []
    # OF3 (item 25): the changes from 2 sessions before to 3 after each front contract's expiry are left out, as the
    # real's are (ptv22.py's tape reading of CL1/CL4)
    exp1 = sorted({lst_[0][1] for f_ in h["futures"] for lst_ in [f_.get("OIL", [])] if lst_})
    near_expiry = lambda s_: any(x_ - 2 <= s_ <= x_ + 3 for x_ in exp1)
    for s in range(BURN, len(h["futures"])):
        lst = h["futures"][s].get("OIL", [])
        if len(lst) < 4:
            continue
        f1, f4 = lst[0][2]["mark"], lst[3][2]["mark"]
        of1.append(f1 > f4)
        if s >= 5 * YEAR and inv[s] == inv[s]:
            slope.append(math.log(f4 / f1)); invd.append(float(inv[s] - inv[s - 5 * YEAR:s].mean()))
        p = h["futures"][s - 1].get("OIL", [])
        if len(p) >= 4 and p[0][0] == lst[0][0] and p[3][0] == lst[3][0] and not near_expiry(s):
            d1.append(math.log(f1 / p[0][2]["mark"])); d4.append(math.log(f4 / p[3][2]["mark"]))
    out["of1"] = [float(sum(of1)), len(of1)]
    out["of2"] = _sums(slope, invd)
    out["of3"] = [P.of3_window(d1[a:b], d4[a:b]) for a, b in P.windows_back(len(d1), 5 * YEAR)]
    # MG1, and by root for diagnosis
    mg = {}
    for root in P.FUTURE_ROOTS:
        past = n = 0
        for s in range(BURN + 1, len(h["futures"])):
            a, b = h["futures"][s - 1].get(root, []), h["futures"][s].get(root, [])
            if a and b and a[0][0] == b[0][0] and a[0][2].get("initial_margin"):
                mult = a[0][2].get("multiplier") or 1.0
                past += abs(b[0][2]["mark"] - a[0][2]["mark"]) * mult > a[0][2]["initial_margin"]
                n += 1
        mg[root] = [int(past), int(n)]
    out["mg1"] = mg
    return out


def diag(h):
    """A slim series per history for diagnosis: float32 arrays by session."""
    S = len(h["vix"])
    vf = np.full((S, DIAG_VIX), np.nan, np.float32); vn = np.full((S, DIAG_VIX), np.nan, np.float32)
    ve = np.full((S, DIAG_VIX), np.nan, np.float32); vp = np.full((S, DIAG_VIX), np.nan, np.float32)
    of = np.full((S, DIAG_OIL), np.nan, np.float32); on = np.full((S, DIAG_OIL), np.nan, np.float32)
    idx_basis = np.full(S, np.nan, np.float32); ff1 = np.full(S, np.nan, np.float32); ffn = np.full(S, np.nan, np.float32)
    fcv = np.full((S, 3), np.nan, np.float32)
    for s in range(S):
        fut = h["futures"][s]
        for k, (sym, x, q) in enumerate(fut.get("VIX", [])[:DIAG_VIX]):
            vf[s, k] = q["mark"] if q.get("mark") is not None else np.nan; vn[s, k] = x - s
            ve[s, k] = q.get("expected") if q.get("expected") is not None else np.nan
            vp[s, k] = q.get("premium") if q.get("premium") is not None else np.nan
        for k, (sym, x, q) in enumerate(fut.get("OIL", [])[:DIAG_OIL]):
            of[s, k] = q["mark"]; on[s, k] = x - s
        i = fut.get("IDX", [])
        if i and i[0][2].get("fair"):
            idx_basis[s] = 1e4 * math.log(i[0][2]["mark"] / i[0][2]["fair"])
        f = fut.get("FF", [])
        if f:
            ff1[s] = f[0][2]["mark"]; ffn[s] = f[0][1] - s
        fc = h["forecast"][s].get("vix", {})
        for j, hh in enumerate((21, 63, 126)):
            if hh in fc:
                fcv[s, j] = fc[hh]
    f32 = lambda k: np.asarray([np.nan if x is None else x for x in h[k]], np.float32)  # noqa: E731
    return dict(vix=f32("vix"), index=f32("index"), policy=f32("policy"), y2=f32("y2"), oil=f32("oil"),
                inventory=f32("inventory"), vix_f=vf, vix_n=vn, vix_expected=ve, vix_premium=vp, oil_f=of, oil_n=on,
                idx_basis=idx_basis, ff1=ff1, ff_n=ffn, fc_vix=fcv,
                night=np.asarray([np.nan if x is None else x for x in h["night"]], np.float32))


# ------------------------------------------------------------------ rows


def rows(pcs):
    """Every row free_rows reads, pooled from the pieces of the histories in pcs (repeats allowed)."""
    r = {}
    w = [x for p in pcs for x in p["if_w"]]
    if w:
        r["IF1"] = st.median(x[0] for x in w); r["IF2"] = st.median(x[1] for x in w)
    for row in ("IF3", "VF7", "RF3"):
        v = [p[row] for p in pcs if p.get(row) is not None]
        if v:
            r[row] = max(v)
    for row in ("NS1", "NS2"):
        v = [p[row] for p in pcs if p.get(row) is not None]
        if v:
            r[row] = st.median(v)
    c = [sum(p["vf1"][i] for p in pcs) for i in (0, 1)]
    if c[1]:
        r["VF1"] = c[0] / c[1]
        r["VF2"] = float(np.median(np.concatenate([np.asarray(p["vf2"], float) for p in pcs])))
        b = [sum(p["vf3"][i] for p in pcs) for i in (0, 1)]
        r["VF3"] = b[0] / b[1] if b[1] else float("nan")
        for kk in (1, 2, 4):
            s = [0, 0.0, 0.0, 0.0, 0.0, 0.0]
            for p in pcs:
                s = _add(s, p[f"vf4_{kk}"])
            if s[0] > 2:
                r[f"VF4_F{kk}"] = _slope(s)
    v = [x for p in pcs for x in p["vf5"]]
    if v:
        r["VF5"] = st.median(v)
    for hh in (21, 63, 126):
        s = [sum(p["vf6"][hh][i] for p in pcs) for i in (0, 1)]
        if s[1]:
            r[f"VF6_h{hh}"] = s[0] / s[1]
    for row, hs in (("VF8", (21, 63, 126)), ("RF5", (63, 126, 252)), ("OF4", (21, 63, 126))):
        per = [p[row.lower()] for p in pcs]
        if per and all(per[0][x] == per[0][x] for x in hs):
            r[row] = P._unbiased_stat(per)
    for m, key in ((1, "RF1_m1"), (3, "RF1_m3"), (6, "RF1_m6"), (12, "RF1_m12")):
        s = [sum(p["rf1"][m][i] for p in pcs) for i in (0, 1)]
        if s[1]:
            r[key] = s[0] / s[1]
    s = [sum(p["rf2"][i] for p in pcs) for i in range(4)]
    if s[1] > 2 and s[3] > 2:
        r["RF2"] = math.sqrt((s[0] / s[1]) / (s[2] / s[3]))
    v = [x for p in pcs for x in p["rf4"]]
    if v:
        r["RF4"] = st.median(v)
    s = [sum(p["of1"][i] for p in pcs) for i in (0, 1)]
    if s[1]:
        r["OF1"] = s[0] / s[1]
    s = [0, 0.0, 0.0, 0.0, 0.0, 0.0]
    for p in pcs:
        s = _add(s, p["of2"])
    if s[0] > 2:
        r["OF2"] = _corr(s)
    v = [x for p in pcs for x in p["of3"]]
    if v:
        r["OF3"] = st.median(v)
    past = sum(p["mg1"][k][0] for p in pcs for k in p["mg1"]); n = sum(p["mg1"][k][1] for p in pcs for k in p["mg1"])
    if n:
        r["MG1"] = past / n
    return r


def load(dirs):
    out = []
    for d in dirs:
        for f in sorted(pathlib.Path(d).glob("pieces-*.json"), key=lambda f: int(f.stem.split("-")[1])):
            p = P._revive(json.load(open(f)))
            for key in ("vf8", "rf5", "of4"):      # JSON wrote NaN as None
                p[key] = {h: (float("nan") if v is None else v) for h, v in p[key].items()}
            out.append(p)
    return out


# ------------------------------------------------------------------ run


def _task(spec):
    engine, base, dials, seed, years, out = spec
    P.ModelAPI.quote = _quote_with_extras
    tf = P._engine(engine)
    t0 = time.time()
    h = P._revive(P._clean(P.run_free(tf, P._params(tf, base, dials), seed, years)))
    t1 = time.time()
    pc = pieces(h)
    json.dump(P._clean(pc), open(pathlib.Path(out) / f"pieces-{seed}.json", "w"))
    np.savez_compressed(pathlib.Path(out) / f"diag-{seed}.npz", **diag(h))
    return seed, t1 - t0, time.time() - t1


def run(a):
    text = open(a.arm).read().strip() if pathlib.Path(a.arm).exists() else a.arm
    name, base, dials = P._parse_arm(text)
    if a.switches != "none":
        dials = {**P._traded().SWITCHES[a.switches], **dials}
    out = pathlib.Path(a.out) / name
    out.mkdir(parents=True, exist_ok=True)
    tf = P._engine(a.engine)
    json.dump({"arm": name, "base": base, "dials": dials, "years": a.years, "switches": a.switches,
               "tradefloor": getattr(tf, "__version__", "?"), "fingerprint": P._params(tf, base, dials).fingerprint,
               "engine": a.engine}, open(out / "meta.json", "w"), indent=1)
    specs = [(a.engine, base, dials, s, a.years, str(out)) for s in P._parse_seeds(a.seeds)
             if not (out / f"pieces-{s}.json").exists()]
    with ProcessPoolExecutor(max_workers=a.workers) as ex:
        for seed, t, tp in ex.map(_task, specs):
            print(f"{name} {seed} run {t:.0f}s pieces {tp:.0f}s", flush=True)


def check(a):
    free = P._load(a.dir, "free")
    want, _ = P.free_rows(free)
    got = rows([P._revive(P._clean(pieces(h))) for h in free])
    bad = 0
    for k in sorted(set(want) | set(got)):
        x, y = want.get(k), got.get(k)
        ok = (x is None and y is None) or (x is not None and y is not None and
                                            (x == y or abs(x - y) <= 1e-9 * max(1.0, abs(x)) or (x != x and y != y)))
        bad += not ok
        print(f"{k:8s} free_rows {x!s:24s} pieces {y!s:24s} {'ok' if ok else 'DIFFERS'}")
    print(f"{len(free)} histories; {bad} rows differ")
    return bad


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("run")
    m.add_argument("--arm", required=True); m.add_argument("--switches", default="none", choices=("none", "phase1"))
    m.add_argument("--seeds", required=True); m.add_argument("--years", type=int, default=21)
    m.add_argument("--workers", type=int, default=3); m.add_argument("--engine"); m.add_argument("--out", required=True)
    g = sub.add_parser("rows"); g.add_argument("dirs", nargs="+")
    c = sub.add_parser("check"); c.add_argument("dir")
    a = ap.parse_args(argv)
    if a.cmd == "run":
        run(a)
    elif a.cmd == "rows":
        r = rows(load(a.dirs))
        print(json.dumps({k: (round(v, 6) if isinstance(v, float) else v) for k, v in r.items()}, indent=1))
    else:
        sys.exit(1 if check(a) else 0)


if __name__ == "__main__":
    main()
