"""margins.py BOX DESK ARM [--boot N] [--json OUT]: the screen's one-standard-error margin on the noisy rows.

A screen arm passes with margin when every gated row passes and each noisy row below sits at least one standard error
inside its band (margin_se >= 1). The standard errors are over seeds or histories: a bootstrap (histories resampled
with replacement, seed 20260929) for medians, shares and ratios, sd/sqrt(n) for means. The AO rows are exempt (owner
decision 10: graded as fairness tests, grade_all.py), and so is PH5 pooled (owner decision 16, 2026-09-30: a stationary
model reaches its one-se margin about 10% of the time); PH5's margin is computed and reported, and its row needs only
to pass.

  SF1          median over the graded seeds (sf.json, as grade_all reads it) of the per-seed priced share; >= 0.75
  G-rt         the best round trip over the trip seeds, bp; <= 0. se: the sd of the per-seed maxima, floored at
               0.16 bp (the max of three seeds' sd over 106 held-out seeds, sim/r17-wash)
  C10c rules   every one of the mirrored rules (eval_arms.grade) over the 270 pooled long-run histories (owner
               decision 14; grade_all.load_pool: longrun/ARM and longrun-pool/ARM), median <= +1.0 and share ahead <= 2/3,
               each with its bootstrap se. The margin row is the rules the fixes targeted (the thirteenth grade's
               breach and owner decision 6's post-VIX-spike rules, TARGET_RULES); the rest are reported with the
               count under 1 se and the count expected under 1 se by chance is printed beside it
  H1-100y      every decade: vol within 0.67x-1.5x of the first decade, |r| > 20% on at most 0.05% of name-days, tick
               AC mean >= -0.45 and worst > -0.95 (deps/h1.py); the margin is the tightest of those over decades,
               with a seed bootstrap; floor hits must be 0 (a count; reported with the lowest close)
  D1 tail      index_tail_dn3_pct pooled over the d1pool seeds (certgrade_box_hr.py), band [0.64, 2.34]
  D1 drift     index_drift_pct, the mean over the same seeds, band [1.1, 10.3] (owner decision 12)
  F-bear       share of 20% bears with a policy change of -0.5 or less, >= 0.5 (the continuous form of the median
               test); the median itself (<= -0.5) must pass and its bootstrap margin is reported
  R4 held      mean over histories of the held-close stock-IG correlation, [0.15, 0.39]
  B12          grade_all.bear_rows' share, bootstrap, [0.45, 0.85]
  D2           driven 2020: median drawdown [0.237, 0.441] and median sessions back [63, 252], bootstrap over seeds
               (4000 draws; D2b's se is the distance to the bootstrap's 16th or 84th percentile on the nearer edge's
               side, since histories that never regain the high make its bootstrap sd undefined)
  R7a          r7_event.py's hike and cut excess against their bounds (|x| <= bound), with its se; on 90 seeds
               (owner decision 13). R7a-pre (r7pre.json, the last-close baseline, the same 90) is reported beside it
  PH5 pooled   over the 270 pooled histories (owner decision 15). Return clause: |mean d| < 2 se(d); its margin is
               (2 se - |mean d|) / se. Volatility clause: for each year y = 1..7, gap_y = |mean v_y - mean v_0| <=
               bound_y = 2 sqrt(se_0^2 + se_y^2); its margin is min over y of (bound_y - gap_y) / se(gap_y), se(gap_y)
               the bootstrap sd of gap_y over histories (paired: v_0 and v_y come from the same history). The row's
               margin is the smaller of the two clauses'. The unpaired form, (bound_y - gap_y) / sqrt(se_0^2 + se_y^2),
               is reported beside it. Exempt from the margin (owner decision 16): margin_ok is the pass/fail alone
"""
import glob
import json
import math
import os
import re
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault("TF_PROGRAMME", os.path.dirname(HERE))
sys.path[:0] = [HERE, f"{HERE}/deps", f"{HERE}/box", os.path.join(os.path.dirname(HERE), "results", "ptv20")]
import c10  # noqa: E402
import eval_arms as E  # noqa: E402
import grade_all as GA  # noqa: E402
import screen_eval as SE  # noqa: E402

TARGET_RULES = ["out_federal_funds_rate_up21", "out126_after_treasury_yield_2y_5d_p95_up",
                "out126_after_vix_5d_p99.9_up", "out126_after_vix_p99.9_up", "out63_after_vix_5d_p99.9_up",
                "out63_after_vix_p99.9_up", "out21_after_treasury_yield_10y_p99.9_up",
                "out21_after_corporate_bond_yield_p99.9_up", "out21_after_treasury_yield_10y_5d_up"]
GRT_SE_FLOOR = 0.16


def boot(fn, n, B, rng):
    out = [fn(rng.integers(0, n, n)) for _ in range(B)]
    return float(np.nanstd(np.array(out, float), ddof=1))


def side_se(v, fn, n, B, rng, lo=None, hi=None):
    """The se toward the nearer band edge from bootstrap quantiles: the distance from the value to the 16th
    percentile when the lower edge is nearer, to the 84th when the upper is. For a median of a sparse sample with
    infinite members (D2b: histories that never regain the high), whose bootstrap sd is undefined."""
    b = np.array([fn(rng.integers(0, n, n)) for _ in range(B)], float)
    q16, q84 = np.percentile(b, 16), np.percentile(b, 84)
    below = hi is None or (lo is not None and v - lo <= hi - v)
    return float(v - q16) if below else float(q84 - v)


def margin(v, se, lo=None, hi=None):
    """(distance to the nearer band edge) / se; a zero se gives +inf inside the band and -inf outside."""
    if v is None or se is None or not np.isfinite(v):
        return None
    d = ([v - lo] if lo is not None else []) + ([hi - v] if hi is not None else [])
    if not d:
        return None
    if not se > 0:
        return math.inf if min(d) > 0 else -math.inf
    return min(d) / se


def main(argv):
    box, desk, arm = argv[1], argv[2], argv[3]
    B = int(argv[argv.index("--boot") + 1]) if "--boot" in argv else 300
    rng = np.random.default_rng(20260929)
    rows = {}

    def put(name, value, se, lo=None, hi=None, note="", gate=True, ok=None):
        m = margin(value, se, lo, hi)
        inb = ok if ok is not None else (value is not None and np.isfinite(value) and (lo is None or value >= lo)
                                          and (hi is None or value <= hi))
        rows[name] = {"value": value, "se": se, "band": [lo, hi], "margin_se": m, "pass": bool(inb),
                      "margin_ok": bool(inb and m is not None and m >= 1), "gate": gate, "note": note}

    # ---- SF1 (the graded file is the last grade_all reads: sf.json, else sf-rec.json)
    for fn in ("sf.json", "sf-rec.json"):
        f = f"{box}/{fn}"
        if os.path.exists(f):
            v = np.array([x for x in json.load(open(f))["per_seed"].get(arm, {}).get("SF1", {}).values()], float)
            if len(v):
                put("SF1", float(np.median(v)), boot(lambda i: np.median(v[i]), len(v), B, rng), 0.75, None,
                    f"{fn}, {len(v)} seeds")
                break
    # ---- G-rt
    per = {}
    for tf_ in glob.glob(f"{box}/trips/{arm}_*.txt"):
        seed = int(os.path.basename(tf_)[len(arm) + 1:].split(".")[0])      # ARM_SEED.txt (the arm name holds digits)
        for line in open(tf_):
            m = re.search(r"best round trip ([+-]?[\d.]+) bp", line)
            if m:
                per[seed] = max(per.get(seed, -math.inf), float(m.group(1)))
    if per:
        mx = np.array(list(per.values()))
        se = max(float(np.std(mx, ddof=1)) if len(mx) > 1 else 0.0, GRT_SE_FLOOR)
        put("G-rt", float(mx.max()), se, None, 0.0, "per-seed maxima " + ", ".join(f"{s} {v:+.2f}" for s, v in sorted(per.items())))
    # ---- C10c for every rule, over the 270 pooled long-run histories (owner decision 14)
    LR, pool_note = GA.load_pool(box, arm)
    for z in LR:
        z["pub"] = z["cyc"].astype(int)
    if not LR:
        put("C10c rules", None, None, None, 0, gate=True, ok=False, note="C10c " + pool_note)
    if LR:
        n = len(LR)
        mir = {}
        for z in LR:
            zz = dict(z); zz["cyc"] = z["pub"].astype(float)
            for name, flag in c10.rules(zz, 0).items():
                mir.setdefault(name, []).append(E.mirror(z["level"], flag, z["m_federal_funds_rate"])[1])
        allr = {}
        for k, v in mir.items():
            a = np.array(v)
            med, ah = float(np.median(a)), float(np.mean(a > 0))
            # Over the histories that have the rule (a history can lack one: c10.rules leaves out an event rule
            # its path never fires), so the bootstrap draws indices into `a`, not into all n histories.
            na = len(a)
            sm = boot(lambda i: np.median(a[i]), na, B // 3, rng); sa = math.sqrt(max(ah * (1 - ah), 1e-6) / na)
            allr[k] = (med, sm, margin(med, sm, None, 1.0), ah, sa, margin(ah, sa, None, 2 / 3))
        breaches = [k for k, t in allr.items() if t[0] > 1.0 or t[3] > 2 / 3]
        tm = [min(allr[k][2], allr[k][5]) for k in TARGET_RULES if k in allr]
        worst = sorted(allr, key=lambda k: min(allr[k][2], allr[k][5]))
        under1 = [k for k in worst if min(allr[k][2], allr[k][5]) < 1]
        put("C10c rules", float(len(breaches)), None, None, 0, gate=True, ok=not breaches,
            note=f"{n} pooled histories; {len(breaches)} of {len(allr)} breach; target rules' least margin {min(tm):+.2f} se; "
                 f"{len(under1)} of {len(allr)} rules under 1 se (reported); nearest: " + "; ".join(
                     f"{k} {allr[k][0]:+.2f}/{allr[k][3]:.3f} ({min(allr[k][2], allr[k][5]):+.2f} se)" for k in worst[:5]))
        rows["C10c rules"]["margin_se"] = float(min(tm)) if tm else None
        rows["C10c rules"]["margin_ok"] = bool(not breaches and tm and min(tm) >= 1)
        rows["C10c rules"]["targets"] = {k: {"median": allr[k][0], "median_se": allr[k][1], "ahead": allr[k][3],
                                             "ahead_se": allr[k][4], "margin_se": min(allr[k][2], allr[k][5])}
                                         for k in TARGET_RULES if k in allr}
        rows["C10c rules"]["nearest"] = {k: {"median": allr[k][0], "ahead": allr[k][3],
                                             "margin_se": min(allr[k][2], allr[k][5])} for k in worst[:10]}
    # ---- the true-phase histories: R4 held, F-bear, B12
    G = SE.load_gen(box, arm)
    if G:
        ng = len(G)
        v = np.array([SE.bt_stats([z])["R4 held"] for z in G])
        put("R4 held", float(v.mean()), float(v.std(ddof=1) / math.sqrt(ng)), 0.15, 0.39, f"{ng} histories")
        bears = [np.array([z["ffr"][ep["trough"]] - z["ffr"][ep["peak"]] for ep in SE.drawdown_episodes(z["post"], 0.2)])
                 for z in G]
        allb = np.concatenate(bears)
        cat = lambda idx: np.concatenate([bears[i] for i in idx])
        sh = float(np.mean(allb <= -0.5 + 1e-9)); md = float(np.median(allb))
        mse = boot(lambda i: np.median(cat(i)), ng, B, rng)
        put("F-bear", sh, boot(lambda i: np.mean(cat(i) <= -0.5 + 1e-9), ng, B, rng), 0.5, None,
            f"share form; median {md:+.3f} (se {mse:.3f}, margin {margin(md, mse, None, -0.5) or 0:+.2f} se, in band "
            f"{md <= -0.5}); {len(allb)} bears", ok=bool(sh >= 0.5 and md <= -0.5))
        rows["F-bear"]["median"] = md; rows["F-bear"]["median_se"] = mse
        rows["F-bear"]["median_margin_se"] = margin(md, mse, None, -0.5)
        br = GA.bear_rows(G)
        bb = [GA.bear_rows([G[i] for i in rng.integers(0, ng, ng)])["B12"] for _ in range(max(B // 4, 50))]
        put("B12", br["B12"], float(np.nanstd(bb, ddof=1)), 0.45, 0.85, f"{br['bears']} bears")
    # ---- D1 pooled rows (certgrade json on the desk)
    cg = sorted(glob.glob(f"{desk}/certgrade-*.json"))
    if cg:
        lvl = json.load(open(cg[0]))["arms"].get(arm, {}).get("level", {})
        for row, name in (("index_tail_dn3_pct", "D1 tail pooled"), ("index_drift_pct", "D1 drift pooled")):
            r = lvl.get(row)
            if r:
                put(name, r.get("value"), r.get("se"), *(r.get("band") or [None, None]),
                    f"{r.get('n')} seeds; 30-seed cert {r.get('cert30')}" + (f"; {r['note']}" if r.get("note") else ""),
                    ok=bool(r.get("in_band")))
    # ---- D2
    f = f"{box}/driven2020.json"
    if os.path.exists(f):
        a = json.load(open(f))["arms"].get(arm)
        if a:
            ps = list(a["per_seed"].values())
            mdd = np.array([p["mdd"] for p in ps], float); rec = np.array([p["recovery_sessions"] for p in ps], float)
            BD = max(B, 4000)            # the median of a sparse, partly infinite sample: many draws steady its se
            put("D2a", float(np.median(mdd)), boot(lambda i: np.median(mdd[i]), len(mdd), BD, rng), 0.237, 0.441)
            bb = np.array([np.median(rec[rng.integers(0, len(rec), len(rec))]) for _ in range(BD)])
            sdb = float(np.std(bb[np.isfinite(bb)], ddof=1))
            put("D2b", float(np.median(rec)), side_se(float(np.median(rec)), lambda i: np.median(rec[i]), len(rec), BD,
                                                   rng, 63, 252), 63, 252,
                f"never recovered {int(np.isinf(rec).sum())} of {len(rec)}; bootstrap share below 63 "
                f"{np.mean(bb < 63):.3f}, above 252 {np.mean(bb > 252):.3f}; sd of the finite bootstrap medians {sdb:.1f} "
                f"(margin {(np.median(rec) - 63) / sdb:+.2f} in that form)")
    # ---- R7a
    f = f"{box}/r7-event.json"
    if os.path.exists(f):
        a = json.load(open(f))["arms"].get(arm)
        if a:
            for k in ("hike", "cut"):
                x = a[k]
                put(f"R7a {k}", x["excess_bp"], x["excess_se_bp"], -x["bound_bp"], x["bound_bp"], f"n {x['n']}")
    f = f"{box}/r7pre.json"
    if os.path.exists(f):
        j = json.load(open(f)); a = j.get("arms", {}).get(arm) or j.get(arm)
        if a:
            for k in ("hike", "cut"):
                x = a[k]
                put(f"R7a-pre {k}", x["excess_pre_bp"], x["excess_pre_se_bp"], -max(5.0, 2 * x["excess_pre_se_bp"]),
                    max(5.0, 2 * x["excess_pre_se_bp"]), f"n {x['n']}; reported", gate=False)
    # ---- PH5 pooled (owner decision 15)
    PL = LR
    if PL:
        rows["PH5 pooled"] = ph5_margin(PL, B, rng)
    else:
        put("PH5 pooled", None, None, None, None, gate=True, ok=False, note="PH5 " + pool_note)
    # ---- H1-100y
    h = h1_margin(f"{box}/stab100", arm, B, rng)
    if h:
        rows["H1-100y"] = h
    out = {"box": box, "arm": arm, "rows": rows,
           "noisy_all_margin": all(r["margin_ok"] for r in rows.values() if r.get("gate", True)),
           "noisy_rows_short": [k for k, r in rows.items() if r.get("gate", True) and not r["margin_ok"]]}
    js = argv[argv.index("--json") + 1] if "--json" in argv else None
    if js:
        json.dump(out, open(js, "w"), indent=1, default=lambda o: float(o) if hasattr(o, "__float__") else str(o))
    for k, r in rows.items():
        v = r["value"]
        print(f"{arm:6s} {k:16s} {'-' if v is None else f'{v:+.4f}':>10s} se {'-' if not r['se'] else format(r['se'], '.4f'):>7s} "
              f"band {r['band']} margin {'-' if r['margin_se'] is None else format(r['margin_se'], '+.2f')} se "
              f"{'ok' if r['margin_ok'] else ('PASS, thin' if r['pass'] else 'FAIL')}  {r['note']}")
    print(f"{arm}: noisy rows with margin: {'all' if out['noisy_all_margin'] else 'short ' + str(out['noisy_rows_short'])}")
    return out


def ph5_margin(H, B, rng):
    """PH5 over the pooled histories H (grade_all.ph5, the registered formula) with the margin in se of each
    clause's own statistic (the module docstring)."""
    d, v0, v17 = (np.asarray(x, float) for x in GA.ph5_of(H))
    g = GA.ph5(d, v0, v17)
    n = len(d); se = float(d.std(ddof=1) / math.sqrt(n))
    m_ret = (2 * se - abs(float(d.mean()))) / se
    my = v17.mean(0); m0 = v0.mean(); gap = np.abs(my - m0)
    s0 = v0.std(ddof=1) / math.sqrt(n); sy = v17.std(0, ddof=1) / math.sqrt(n)
    sd_un = np.sqrt(s0 ** 2 + sy ** 2); bound = GA.PH5_VOL_Z * sd_un        # the registered clause's z
    bs = []
    for _ in range(max(B, 300)):
        i = rng.integers(0, n, n)
        bs.append(np.abs(v17[i].mean(0) - v0[i].mean()))
    se_gap = np.std(np.array(bs), axis=0, ddof=1)
    m_vol_y = (bound - gap) / se_gap; m_vol_un_y = (bound - gap) / sd_un
    w = int(np.argmin(m_vol_y))
    m = float(min(m_ret, m_vol_y.min()))
    ok = g["PH5"] == 1.0
    # owner decision 16: the one-se margin is dropped for pooled PH5; the margin is reported, the row needs only to pass
    return {"value": float(g["PH5_vol_use"]), "se": float(se_gap[w]), "band": [0, 1], "margin_se": m, "pass": bool(ok),
            "margin_ok": bool(ok), "margin_exempt": "owner decision 16", "gate": True,
            "ret_margin_se": float(m_ret), "vol_margin_se": float(m_vol_y.min()), "vol_margin_unpaired_se": float(m_vol_un_y.min()),
            "note": (f"{n} histories; return clause d {d.mean():+.4f} se {se:.4f} (margin {m_ret:+.2f} se); vol clause use "
                     f"{g['PH5_vol_use']:.2f}, worst year {w + 1} gap {gap[w]:.4f} bound {bound[w]:.4f} se(gap) "
                     f"{se_gap[w]:.4f} (margin {m_vol_y[w]:+.2f} se; unpaired form {m_vol_un_y.min():+.2f}); "
                     f"vol y0 {m0:.4f} y1-7 " + " ".join(f"{x:.4f}" for x in my))}


def h1_margin(d, arm, B, rng, CAP=50000 * 0.999):
    """deps/h1.py's decade clauses with a seed bootstrap: the tightest clause's margin over decades."""
    fs = sorted(glob.glob(f"{d}/{arm}-*.npz"))
    if not fs:
        return None
    Z = [np.load(f) for f in fs]
    D = Z[0]["closes"].shape[0]; Y = D // 252

    def stats(idx):
        rows = []
        for k in range(0, Y, 10):
            sl = slice(k * 252, (k + 10) * 252); vols, big, nd, fl, acm, acw = [], 0, 0, 0, [], []
            for si in idx:
                z = Z[si]; c = z["closes"].astype(float); full = np.vstack([z["p0"][None, :], c])
                r = np.diff(np.log(full), axis=0)[sl]; cc = c[sl]
                below = (cc < CAP) & (full[:-1][sl] < CAP)
                for i in range(c.shape[1]):
                    m = below[:, i]
                    if m.sum() > 252:
                        vols.append(np.std(r[m, i]) * np.sqrt(252))
                big += int((np.abs(r[below]) > np.log(1.2)).sum()); nd += int(below.sum())
                fl += int((cc <= 0.0101).sum())
                acm += list(z["ac_mean"][k:k + 10]); acw += list(z["ac_min"][k:k + 10])
            rows.append((np.median(vols), big / nd, fl, np.nanmean(acm), np.nanmin(acw)))
        return rows

    base = stats(range(len(Z)))
    v0 = base[0][0]

    def clauses(rows):
        v0 = rows[0][0]
        return {"vol_lo": min(r[0] / v0 for r in rows[1:]) - 0.67, "vol_hi": 1.5 - max(r[0] / v0 for r in rows[1:]),
                "big": 0.0005 - max(r[1] for r in rows), "ac_mean": min(r[3] for r in rows) + 0.45,
                "ac_worst": min(r[4] for r in rows) + 0.95}
    c0 = clauses(base)
    bs = [clauses(stats(rng.integers(0, len(Z), len(Z)))) for _ in range(max(B // 10, 30))]
    se = {k: float(np.std([b[k] for b in bs], ddof=1)) for k in c0}
    ms = {k: (c0[k] / se[k] if se[k] > 0 else (math.inf if c0[k] > 0 else -math.inf)) for k in c0}
    floor = sum(r[2] for r in base)
    lowest = min(float(np.load(f)["closes"].min()) for f in fs)
    worst = min(ms, key=ms.get)
    ok = all(c0[k] >= 0 for k in c0) and floor == 0
    return {"value": float(c0[worst]), "se": se[worst], "band": [0, None], "margin_se": float(ms[worst]),
            "pass": bool(ok), "margin_ok": bool(ok and ms[worst] >= 1), "gate": True,
            "note": f"tightest clause {worst}; margins " + ", ".join(f"{k} {m:+.1f}" for k, m in ms.items())
                    + f"; floor hits {floor}; lowest close {lowest:.4f} ({len(Z)} seeds)"}


if __name__ == "__main__":
    main(sys.argv)
