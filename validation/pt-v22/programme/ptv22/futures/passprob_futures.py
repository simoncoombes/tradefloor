"""passprob_futures.py: each pt-v22 futures row's chance of passing a grade, in the free gate's format.

    python passprob_futures.py ARM --free DIR [--metaorder DIR] [--push DIR] [--traded DIR] [--draws K]
        [--json OUT] [--rest OUT] [--rows IF1,IF2,...]   (--rows: the rows that ship; the rest are read, not gated)

The free gate's method (programme/ptv22/results/r22o5czN-B/gate/passprob.py): a Bayesian bootstrap over the
histories in hand (Dirichlet(1) weights for the arm's unknown population), then the grade's number of histories
drawn from that weighted population, the row's statistic recomputed by the stage's own code and judged against its
band. PASSPROB_PLUGIN=1 draws with equal weights (the centre taken as known). Sizes are the criteria's
(CRITERIA-pt-v22.md): the free rows on 270 histories of 21 years (futrows.py's pieces, pooled exactly as
ptv22.free_rows pools them), IF4 and XD3 on 30 seeds, MG2, XD1 and XD2 on the traded stage's histories (270).

Construction rows (PLAN-pt-v22.md, gate amended 2026-10-14): a row whose band is an identity passes when its chance
is at least a perfectly stationary arm's mean chance on the same draws. VF8, RF5 and OF4 (a forecast unbiased
within 2 se) take as that arm the same histories with each horizon's mean error moved to zero across histories
("null"); IF3, VF7 and RF3 are exact identities (chance 1 or 0); MG2 and XD3 have no stationary analogue and are
judged as data rows. Data rows pass at 0.9.

--json writes passprob.py's shape ({"rows": {id: {p, point, pred_mean, pred_sd, band, n}}}); --rest writes
passprob_rest.py's ({"rows": [{id, group, p, p_pess, how, detail}], "joint", ...}) for joining with the underlying
gate's 159 rows. NS3 (bit-identity) and NS2's note are not read here.
"""
import json
import math
import os
import pathlib
import statistics as st
import sys

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "longrun"))
sys.path.insert(0, str(HERE))
import ptv22 as P  # noqa: E402
import ptv22traded as T  # noqa: E402
import futrows as F  # noqa: E402

GATE_ROW = 0.9
FREE_ROWS = ["IF1", "IF2", "IF3", "NS1", "NS2", "VF1", "VF2", "VF3", "VF4_F1", "VF4_F2", "VF4_F4", "VF5", "VF6_h21",
             "VF6_h63", "VF6_h126", "VF7", "VF8", "RF1_m1", "RF1_m3", "RF1_m6", "RF2", "RF3", "RF4", "RF5", "OF1", "OF2",
             "OF3", "OF4", "MG1"]
IDENTITY = {"IF3", "VF7", "RF3"}
FORECAST = {"VF8": "vf8", "RF5": "rf5", "OF4": "of4"}


def draw(n_b, n_g, rng):
    if os.environ.get("PASSPROB_PLUGIN"):
        return rng.integers(0, n_b, n_g)
    w = rng.dirichlet(np.ones(n_b))
    return rng.choice(n_b, size=n_g, replace=True, p=w)


def inb(v, lo, hi):
    return v is not None and v == v and (lo is None or v >= lo) and (hi is None or v <= hi)


def centred(pcs):
    """The stationary arm of VF8, RF5 and OF4: each horizon's per-history mean errors moved so their mean across
    histories is zero (a forecast unbiased by construction, with the arm's own spread and correlations)."""
    out = [dict(p) for p in pcs]
    for key in FORECAST.values():
        hs = list(pcs[0][key])
        for h in hs:
            v = np.array([np.nan if p[key][h] is None else p[key][h] for p in pcs], float)
            m = np.nanmean(v)
            for p, x in zip(out, v):
                p[key] = dict(p[key]); p[key][h] = float(x - m)
    return out


def tstat_chance(X, hi, K, rng, n_g=270):
    """The chance, over K bootstrap draws of n_g histories from X (Dirichlet-weighted, as draw()), that the
    largest |mean / se| over X's columns is at most hi."""
    n = len(X)
    ok = 0
    for _ in range(K):
        i = draw(n, n_g, rng)
        Y = X[i]
        t = np.abs(Y.mean(0)) / (Y.std(0, ddof=1) / math.sqrt(n_g))
        ok += float(t.max() <= hi)
    return ok / K


def null_chance(X, hi, K, rng, S=80):
    C = np.cov(X.T)
    return float(np.mean([tstat_chance(rng.multivariate_normal(np.zeros(len(C)), C, size=len(X)), hi, K, rng)
                          for _ in range(S)]))


def traded_pieces(h):
    """One traded history's pieces of MG2, XD1 and XD2 (ptv22traded.traded_rows' own quantities)."""
    out = {"seed": h["seed"], "mg2": 0, "xd1": {}, "xd2": None}
    for name, a in h["accounts"].items():
        m = a.get("margin")
        if m:
            out["mg2"] += m["n_failures"]
    for name, r in T.XD1_RULES.items():
        a = h["accounts"].get(name)
        if not a or not a.get("nw"):
            continue
        if r["bound"] == "VF5":
            out["xd1"][name] = T.window_returns(a["nw"], a["start"])
        else:
            b = h["accounts"]["hold_" + r["hold"]]
            out["xd1"][name] = 100 * (T.ann_log(a["nw"], a["start"]) - T.ann_log(b["nw"], b["start"]))
    a, b = h["accounts"].get("basket_night"), h["accounts"].get("hold_basket")
    if a and b and a.get("nw") and b.get("nw"):
        out["xd2"] = 100 * (T.ann_log(a["nw"], a["start"]) - T.ann_log(b["nw"], b["start"]))
    return out


def tape_allowance():
    """XD1's tape allowance (PLAN-pt-v22.md, registration 19, decided 2026-10-15): {rule: (median excess, share
    ahead)} on the real tape's 21-year windows, for each rule the tape itself puts over C10c's bound
    (gate/xd1-tape.json, xd1tape.py). A model rule over the bound passes when its median and its ahead-share are
    both no larger than the tape's."""
    f = HERE / "gate" / "xd1-tape.json"
    if not f.exists():
        return {}
    t = json.load(open(f))
    out = {}
    for key, rule in (("oil", "oil_long_backwardation"), ("oil_calendar", "oil_calendar_backwardation")):
        o = t.get(key, {})
        if "windows_21y_median_excess" in o:
            med, ah = o["windows_21y_median_excess"], o["windows_21y_share_ahead"]
            if T.c10_exploit(med, ah, "C10c"):
                out[rule] = (med, ah)
    return out


TAPE = tape_allowance()


def traded_rows(tp, vf5):
    lo, hi = vf5
    r = {"MG2": float(sum(p["mg2"] for p in tp))}
    breaks, worst = 0, {}
    for name, rule in T.XD1_RULES.items():
        vals = [p["xd1"][name] for p in tp if name in p["xd1"]]
        if not vals:
            continue
        if rule["bound"] == "VF5":
            v = st.median([x for w in vals for x in w])
            b = not (lo <= v <= hi)
            worst[name] = v
        else:
            med, share = st.median(vals), sum(x > 0 for x in vals) / len(vals)
            b = T.c10_exploit(med, share, rule["bound"])
            if b and name in TAPE:              # the tape breaks it too: allowed up to the tape's edge
                b = not (med <= TAPE[name][0] and share <= TAPE[name][1])
            worst[name] = (med, share)
        breaks += b
    r["XD1"] = float(breaks)
    ex = [p["xd2"] for p in tp if p["xd2"] is not None]
    if ex:
        med, share = st.median(ex), sum(x > 0 for x in ex) / len(ex)
        r["XD2"] = float(T.c10_exploit(med, share, "C10c"))
        r["XD2_median"], r["XD2_ahead"] = med, share
    return r, worst


def push_worst(hs):
    groups = {}
    for h in hs:
        for t in h["trials"]:
            groups.setdefault((t["root"], t["window_name"], t["frac"]), []).append(t["pnl"])
    means = {k: float(np.mean(v)) for k, v in groups.items()}
    return max(means.values()), max(means, key=means.get)


def if4_of(hs):
    v = [r["is_"] for h in hs for r in h["rows"] if r["f"] == 0.1 and r["is_"] is not None and r["is_"] == r["is_"]]
    return float(np.median(v)) if v else float("nan")


def main(argv):
    arm = argv[1]
    arg = lambda k: argv[argv.index(k) + 1] if k in argv else None  # noqa: E731
    K = int(arg("--draws") or 400)
    realj = json.load(open(P.DATA / "real.json"))
    B = P.bands(realj)
    rng = np.random.default_rng(20261015)
    rows = {}

    def put(key, vals, band, point, n, flag=False):
        if flag:
            p = float(np.mean([v == 0 for v in vals]))
        else:
            p = float(np.mean([inb(v, *band) for v in vals]))
        arr = np.array([v for v in vals if v is not None and v == v], float)
        rows[key] = {"p": p, "point": point, "pred_mean": float(arr.mean()) if len(arr) else None,
                     "pred_sd": float(arr.std(ddof=1)) if len(arr) > 1 else None, "band": list(band), "n": n}

    if arg("--free"):
        pcs = F.load([arg("--free")])
        nb = len(pcs)
        point = F.rows(pcs)
        null_pcs = centred(pcs)
        D, N = [], []
        for _ in range(K):
            idx = draw(nb, 270, rng)
            D.append(F.rows([pcs[i] for i in idx]))
            N.append(F.rows([null_pcs[i] for i in idx]))
        for k in FREE_ROWS:
            _, lo, hi, rule = B[k]
            put(k, [d.get(k) for d in D], (lo, hi), point.get(k), f"{nb} -> 270")
            if k in FORECAST:
                # the stationary arm as passprob.py --null builds PH5's: samples of 270 histories from a normal
                # population with the arm's covariance of the per-history mean errors and mean 0, each read with
                # the same bootstrap; "null" is the mean of the samples' chances
                X = np.array([[p[FORECAST[k]][h] for h in sorted(pcs[0][FORECAST[k]])] for p in pcs], float)
                X = X[np.all(np.isfinite(X), 1)]
                rows[f"{k} null"] = {"p": null_chance(X, hi, K, np.random.default_rng(20261018)), "point": None,
                                     "pred_mean": None, "pred_sd": None, "band": [lo, hi],
                                     "n": "normal population, the arm's covariance, mean 0; 80 samples of 270"}
                put(f"{k} centred", [d.get(k) for d in N], (lo, hi), None, f"{nb} -> 270, the arm's own histories centred")
        rows["RF1_m12"] = {"p": 1.0, "point": point.get("RF1_m12"), "pred_mean": None, "pred_sd": None,
                           "band": None, "n": "reported, not gated"}
    if arg("--metaorder"):
        mo = [json.load(open(f)) for f in sorted(pathlib.Path(arg("--metaorder")).glob("metaorder-*.json"))]
        vals = [if4_of([mo[i] for i in draw(len(mo), 30, rng)]) for _ in range(K)]
        r, rep = T.metaorder_rows(mo)
        put("IF4", vals, (0.10, 0.21), {"IF4": r.get("IF4"), **rep.get("IF4", {})}, f"{len(mo)} -> 30")
    if arg("--push"):
        pu = [json.load(open(f)) for f in sorted(pathlib.Path(arg("--push")).glob("push-*.json"))]
        vals = [push_worst([pu[i] for i in draw(len(pu), 30, rng)])[0] for _ in range(K)]
        put("XD3", vals, (None, 0.0), push_worst(pu), f"{len(pu)} -> 30")
    if arg("--traded"):
        d = pathlib.Path(arg("--traded"))
        tp = [json.load(open(f)) for f in sorted(d.glob("tpieces-*.json"))]
        if not tp:
            tp = [traded_pieces(P._revive(json.load(open(f)))) for f in sorted(d.glob("traded-*.json"))]
        vf5 = T.vf5_band(realj)
        pt, worst = traded_rows(tp, vf5)
        D = [traded_rows([tp[i] for i in draw(len(tp), 270, rng)], vf5)[0] for _ in range(K)]
        for k in ("MG2", "XD1", "XD2"):
            if k in pt:
                put(k, [x.get(k) for x in D], (0.0, 0.0), {"value": pt[k], "rules": worst} if k == "XD1" else pt[k],
                    f"{len(tp)} -> 270", flag=True)
    # the gate, on the rows that ship (--rows), or every row read
    ship = set(arg("--rows").split(",")) if arg("--rows") else None
    out_rows = []
    for k, r in rows.items():
        if k.endswith(" null") or k.endswith(" centred") or r["band"] is None:
            continue
        if ship is not None and k not in ship:
            continue
        p, how = r["p"], "predictive " + r["n"]
        if k in FORECAST and f"{k} null" in rows:
            null = rows[f"{k} null"]["p"]
            ratio = p / null if null > 0 else 0.0
            how += (f"; chance {p:.3f} vs the stationary arm's mean {null:.3f}, ratio {ratio:.3f} (construction: passes at 1;"
                    f" the arm's own histories centred read {rows[k + ' centred']['p']:.3f})")
            p = min(1.0, ratio)
        elif k in IDENTITY:
            how += "; identity"
        out_rows.append({"id": k, "group": "ptv22f", "p": p, "p_pess": p, "how": how,
                         "detail": f"point {r['point']}; band {r['band']}; predictive mean {r['pred_mean']} sd {r['pred_sd']}"})
    joint = float(np.prod([x["p"] for x in out_rows])) if out_rows else float("nan")
    rest = {"arm": arm, "rows": out_rows, "joint": joint, "joint_pess": joint, "n_rows": len(out_rows),
            "not_read": [], "failing_unmodelled": [],
            "note": "pt-v22 futures rows only; join with passprob_rest.py's 159 by multiplying the joints"}
    if arg("--json"):
        json.dump({"arm": arm, "draws": K, "rows": rows}, open(arg("--json"), "w"), indent=1,
                  default=lambda o: float(o) if hasattr(o, "__float__") else str(o))
    if arg("--rest"):
        json.dump(rest, open(arg("--rest"), "w"), indent=1, default=lambda o: float(o) if hasattr(o, "__float__") else str(o))
    for x in sorted(out_rows, key=lambda x: x["p"]):
        pt = rows[x["id"]]["point"]
        ptxt = f"{pt:.4g}" if isinstance(pt, float) else str(pt)[:60]
        print(f"{x['group']:6s} {x['id']:9s} p {x['p']:.3f}  {'pass' if x['p'] >= GATE_ROW else 'FAIL'}  point {ptxt:12s} "
              f"band {rows[x['id']]['band']}  {x['how'][:90]}")
    print(f"{len(out_rows)} futures rows; joint {joint:.4f}; failing: "
          f"{', '.join(x['id'] for x in out_rows if x['p'] < GATE_ROW) or 'none'}")
    return rest


if __name__ == "__main__":
    main(sys.argv)
