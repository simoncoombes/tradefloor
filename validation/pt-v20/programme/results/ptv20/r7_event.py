"""R7a, no drift after a published policy-rate decision (twelfth registration).

The event study behind R7a, per arm, on the certified roster
Universe.random(40, seed=111), its equal-weight (geometric) index. For each
session d: pre = the index after the previous close (the first moment the
close's policy decision is readable), opn = after open_market, b1 = after the
first 65-minute bar, cl = the close.

  R7a  the mean log move from the first readable price to the end of the
       first bar (log b1/pre) on sessions after a changed policy rate, for
       hikes and for cuts apart, less the mean first bar of all sessions
       (log b1/opn). Each within +-5 bp, or +-2 se where 2 se is wider (se of
       the difference: the event mean's and the all-days mean's, in
       quadrature).

Graded on 30 histories of 21 years (seeds 101-130, the grade's). The
measurement as first run (desk/ratenews/event.py; box ratenews1, in
box-ratenews1/, seeds 601-630) set the leading dials by hand, and this script
gives the same numbers on the same seeds; here each arm is read as the long
run reads it, NAME[@BASE][:dial=v,...].

    python r7_event.py OUT.json --arm pt-v19: --arm pt-v20@pt-v20: \\
        --seeds 101-130 --years 21 --workers 60
"""
import argparse
import json
import math
from concurrent.futures import ProcessPoolExecutor

import numpy as np

BOUND_BP = 5.0


def f64(b):
    return np.frombuffer(b, dtype="<f8").copy()


def one(spec):
    base, dials, seed, years = spec
    import tradefloor as tf
    m = tf.ModelParams.from_preset(base, **dials) if dials else tf.ModelParams.from_preset(base)
    u = tf.Universe.random(40, seed=111)
    e = tf.Engine(seed=seed, universe=u, model=m)
    D = 252 * years
    out = {k: np.zeros(D) for k in ("pre", "opn", "b1", "cl", "fed", "y")}
    ix = lambda: float(np.exp(np.log(f64(e.prices())[:40]).mean()))
    for d in range(D):
        mf = e.macro_fields
        out["fed"][d] = mf["federal_funds_rate"]; out["y"][d] = mf["corporate_bond_yield"]
        out["pre"][d] = ix()
        e.open_market(); out["opn"][d] = ix()
        e.run_session(9, 30, 3, 65); out["b1"][d] = ix()
        e.run_session(10, 35, 3, 325); out["cl"][d] = ix()
        e.close_market()
    return seed, out


def seeds_of(text):
    out = []
    for part in text.split(","):
        lo, _, hi = part.partition("-")
        out += list(range(int(lo), int(hi or lo) + 1))
    return out


def ms(x):
    """mean and standard error, in bp"""
    x = np.asarray(x, dtype=float)
    if len(x) == 0:
        return float("nan"), float("nan")
    return float(x.mean() * 1e4), float(x.std() / math.sqrt(len(x)) * 1e4)


def summarise(rows):
    KEYS = ("night", "open", "bar1", "rest", "day", "prevday")
    res = {"hike": {k: [] for k in KEYS}, "cut": {k: [] for k in KEYS}}
    allb1, cors = [], []
    for o in rows:
        for d in range(1, len(o["fed"])):
            ch = o["fed"][d] - o["fed"][d - 1]
            allb1.append(math.log(o["b1"][d] / o["opn"][d]))
            if abs(ch) > 1e-9:
                r = res["hike" if ch > 0 else "cut"]
                r["night"].append(math.log(o["pre"][d] / o["cl"][d - 1]))
                r["open"].append(math.log(o["opn"][d] / o["pre"][d]))
                r["bar1"].append(math.log(o["b1"][d] / o["pre"][d]))
                r["rest"].append(math.log(o["cl"][d] / o["b1"][d]))
                r["day"].append(math.log(o["cl"][d] / o["pre"][d]))
                r["prevday"].append(math.log(o["cl"][d - 1] / o["pre"][d - 1]))
        dy = np.diff(o["y"]); rr = np.log(o["cl"][1:] / o["pre"][1:])
        if dy.std() > 0:
            cors.append(float(np.corrcoef(dy, rr)[0, 1]))
    a_m, a_se = ms(allb1)
    out = {"all_bar1_bp": a_m, "all_bar1_se_bp": a_se, "all_days": len(allb1),
           "corr_dyield_next_session": float(np.mean(cors)) if cors else None}
    ok = True
    for side, r in res.items():
        blk = {"n": len(r["bar1"])}
        for k, v in r.items():
            blk[k + "_bp"], blk[k + "_se_bp"] = ms(v)
        ex = blk["bar1_bp"] - a_m
        se = math.hypot(blk["bar1_se_bp"], a_se)
        bound = max(BOUND_BP, 2 * se) if math.isfinite(se) else BOUND_BP
        blk.update({"excess_bp": ex, "excess_se_bp": se, "bound_bp": bound,
                    "pass": bool(blk["n"] > 0 and abs(ex) <= bound + 1e-12)})
        ok = ok and blk["pass"]
        out[side] = blk
    out["R7a_hike_bp"], out["R7a_cut_bp"] = out["hike"]["excess_bp"], out["cut"]["excess_bp"]
    out["pass"] = ok
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out"); ap.add_argument("--arm", action="append", required=True)
    ap.add_argument("--seeds", default="101-130"); ap.add_argument("--years", type=int, default=21)
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    import tradefloor as tf
    seeds = seeds_of(a.seeds)
    out = {"kind": "ptv20-r7-event", "seeds": a.seeds, "years": a.years, "roster": "Universe.random(40, seed=111)",
           "tradefloor_version": tf.version(), "bound_bp": BOUND_BP, "arms": {}}
    specs = []
    for arm in a.arm:
        head, _, body = arm.partition(":")
        name, _, base = head.partition("@")
        base = base or name
        dials = {k: float(v) for k, v in (x.split("=") for x in body.split(",") if x)}
        specs.append((name, base, dials))
    with ProcessPoolExecutor(a.workers) as ex:
        futs = {(name, s): ex.submit(one, (base, dials, s, a.years)) for name, base, dials in specs for s in seeds}
        for name, base, dials in specs:
            rows = [futs[(name, s)].result()[1] for s in seeds]
            blk = summarise(rows)
            blk.update({"base": base, "dials": dials,
                        "fingerprint": tf.ModelParams.from_preset(base, **dials).fingerprint})
            out["arms"][name] = blk
            h, c = blk["hike"], blk["cut"]
            print(f"{name:12s} all-days bar1 {blk['all_bar1_bp']:+.2f}bp | hike n={h['n']} bar1 {h['bar1_bp']:+.1f} "
                  f"-> excess {h['excess_bp']:+.1f} (bound {h['bound_bp']:.1f}) | cut n={c['n']} bar1 {c['bar1_bp']:+.1f} "
                  f"-> excess {c['excess_bp']:+.1f} (bound {c['bound_bp']:.1f}) | night hike {h['night_bp']:+.1f} "
                  f"cut {c['night_bp']:+.1f} -> R7a {'pass' if blk['pass'] else 'FAIL'}", flush=True)
    json.dump(out, open(a.out, "w"), indent=1, default=str)


if __name__ == "__main__":
    main()
