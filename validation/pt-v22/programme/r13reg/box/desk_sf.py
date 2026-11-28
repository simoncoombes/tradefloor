"""Desk check for sim/r13-scenario-frontrun: the design's statistics per arm on
held-out seeds, paired against the arm's own no-scenario twin, certified
roster Universe.random(40, seed=111) with the rate indices, cap-weighted
index over the 40 names.

Per (arm, seed):
  SF1   VIX-only lever x2.5 held 25 sessions from day 50: paired log move from
        the day-49 close to the day-50 close as a share of the move to the
        day-54 close.
Per (arm, file, seed):
  morning        paired move of the onset's morning write, log %
  drift          paired log % from the onset's post-write mark to +24 closes
  vixtimer       SF2: out while the published VIX (after the morning write)
                 is above 40, back in below 30, edge over holding, log %,
                 onset to onset+120
  worst          SF3: largest one-session paired fall from the onset on,
                 morning write or close to close, log %
  corp_sd        SF5: corporate yield daily change sd, bp, onset+5..onset+250
  ig_sd          IGCORP daily log return sd, bp, onset+5..onset+250
For the recession files, additionally:
  S1a S1b S2     the twelfth registration's recession rows (rec_grid.py's
                 definitions)
  SF4            closes inside a phase hold whose true phase is not the
                 pinned one, and one-session troughs in the true phase
"""
import argparse, json, math
from concurrent.futures import ProcessPoolExecutor
import numpy as np

import os
_R = os.environ.get("REPO", "/Users/simoncoombes/Dev/tf-wt-r13")
D = f"{_R}/python/tradefloor/scenarios"
C = f"{_R}/tools/calibration/scenario_candidates"
# the packaged files of the engine under test (sim/r13) and the two NBER-phased candidates
FILES = {  # name: (path, onset, days, recession holds or None)
    "rec_pkg": (f"{D}/recession.yml", 50, 700, [(50, 70, "peak"), (71, 364, "contraction")]),  # r15: the packaged file is recession_I
    "rec_N16": (f"{C}/recession_N16.yml", 50, 700, [(50, 70, "peak"), (71, 364, "contraction")]),
    "rec_I": (f"{C}/recession_I.yml", 50, 700, [(50, 70, "peak"), (71, 364, "contraction")]),
    "lc": (f"{D}/liquidity_crisis.yml", 50, 300, None),
    "oil": (f"{D}/oil_price_spike.yml", 50, 300, None),
    "prs": (f"{D}/policy_regime_shift.yml", 30, 300, None),
    "rate": (f"{D}/rate_shock.yml", 50, 300, None),
    "curve": (f"{D}/curve_shock.yml", 50, 300, None),
    "geo": (f"{D}/geopolitical_conflict.yml", 50, 300, None),
}
LEVER = """version: 1
scenario:
  name: vix_lever
  description: >
    A VIX-only lever for SF1.
  shocks:
    - target: macro.vix
      operation: multiply
      value: 2.5
      at: 50
      shape: hold
      duration: 25
"""
S1A, S2 = (0.45, 1.00), (25.0, 80.0)


def seeds_of(text):
    out = []
    for part in text.split(","):
        lo, _, hi = part.partition("-")
        out += list(range(int(lo), int(hi or lo) + 1))
    return out


def run(dials, scen, seed, days):
    import tradefloor as tf
    u = tf.Universe.random(40, seed=111).with_bonds()
    n = 40
    ig = [i.ticker for i in u].index("IGCORP")
    cap = np.array([x.shares_outstanding for x in list(u)[:n]], dtype=float)
    e = tf.Engine(seed=seed, universe=u, model=tf.ModelParams.from_preset("pt-v20", **dials))
    sc = None if scen is None else (tf.Scenario.from_yaml(scen))
    r = {k: [] for k in ("pre", "post", "close", "vam", "corp", "ig", "true")}
    px = lambda: np.frombuffer(e.prices(), "<f8")
    lv = lambda: float(np.log((px()[:n] * cap).sum()))
    for d in range(days):
        r["pre"].append(lv())
        if sc is not None:
            sc.apply(e, d)
        r["post"].append(lv()); r["vam"].append(e.macro_fields["vix"])
        e.open_market(); e.run_session(9, 30, 3, 390); e.close_market()
        r["close"].append(lv()); r["ig"].append(float(px()[ig]))
        s = e.state_snapshot()["economy"]
        r["corp"].append(s["corporate_bond_yield"]); r["true"].append(s["cycle_phase"])
    return {k: (v if k == "true" else np.array(v)) for k, v in r.items()}


def vixtimer(r, lo, hi):
    out, edge = False, 0.0
    for d in range(lo, min(hi, len(r["vam"]) - 1)):
        v = r["vam"][d]
        if not out and v > 40:
            out = True
        elif out and v < 30:
            out = False
        if out:
            edge -= r["post"][d + 1] - r["post"][d]
    return 100 * edge


def recession_rows(c, s, days, holds):
    dd = (s["close"] - c["close"]) * 100.0
    t = int(np.argmin(dd))
    regain = (dd[min(t + 252, days - 1)] - dd[t]) / (-dd[t]) if dd[t] < 0 else float("nan")
    ts = int(np.argmin(s["close"][50:])) + 50
    rise = 100.0 * (math.exp(s["close"][min(ts + 252, days - 1)] - s["close"][ts]) - 1.0)
    true = s["true"]
    onset = next((d for d in range(50, days) if true[d] == "contraction"), None)
    exit_d = None if onset is None else next(
        (d for d in range(onset + 1, days) if true[d] not in ("contraction", "trough")), None)
    out_in = onset is not None and exit_d is not None and exit_d - onset <= 504
    off = sum(1 for lo, hi, ph in holds for d in range(lo, hi + 1) if true[d] != ph)
    one_day = sum(1 for d in range(50, days - 1)
                  if true[d] == "trough" and true[d - 1] != "trough" and true[d + 1] != "trough")
    return {"regain": regain, "rise": rise, "out_in": bool(out_in), "off_phase": off,
            "one_day_troughs": one_day, "low_day": t, "low_log": float(dd[t])}


def job(spec):
    name, dials, seed, files, extra = spec
    FILES.update(extra)
    res = {}
    days = max(FILES[f][2] for f in files) if files else 60
    c = run(dials, None, seed, days)
    if not files:
        s = run(dials, LEVER, seed, 60)
        p = s["close"] - c["close"][:60]
        res["SF1"] = float((p[50] - p[49]) / (p[54] - p[49]))
        res["SF1_d54"] = float(100 * (p[54] - p[49]))
        return name, seed, res
    for f in files:
        path, onset, fdays, holds = FILES[f]
        s = run(dials, open(path).read(), seed, fdays)
        cc = {k: v[:fdays] for k, v in c.items()}
        P = lambda k: s[k] - cc[k]
        m = 100 * (P("post")[onset] - P("pre")[onset])
        base = P("post")[onset]
        mo = 100 * (P("post") - P("pre"))[onset:]
        cl = 100 * np.diff(P("close")[onset - 1:])
        row = {"morning": m, "d24": float(100 * (P("close")[onset + 24] - base)),
               "vixtimer": vixtimer(s, onset, onset + 120),
               "twin_vixtimer": vixtimer(cc, onset, onset + 120),
               # SF3 (item 25): the single worst raw close-to-close log move of the index, as the real (the S&P 500's
               # close on 16 March 2020) is; the paired move with the morning write is reported
               "worst": float(100 * np.diff(s["close"][onset - 1:]).min()),
               "worst_paired": float(min(mo.min(), cl.min())),
               "corp_sd": float(np.std(np.diff(s["corp"][onset + 5:onset + 250])) * 100),
               "ig_sd": float(np.std(np.diff(np.log(s["ig"][onset + 5:onset + 250]))) * 1e4),
               "twin_corp_sd": float(np.std(np.diff(cc["corp"][onset + 5:onset + 250])) * 100)}
        if holds is not None:
            row.update(recession_rows(cc, s, fdays, holds))
        res[f] = row
    return name, seed, res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out"); ap.add_argument("--arm", action="append", required=True)
    ap.add_argument("--seeds", default="201-212"); ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--files", default=",".join(FILES))
    ap.add_argument("--rec", action="append", default=[]); ap.add_argument("--no-sf1", action="store_true")
    a = ap.parse_args()
    for spec in a.rec:
        k, p = spec.split("=", 1)
        FILES[k] = (p, 50, 700, [(50, 70, "peak"), (71, 364, "contraction")])
        import builtins
    arms = []
    for arm in a.arm:
        name, _, body = arm.partition(":")
        name = name.split("@")[0]
        arms.append((name, {k: float(v) for k, v in (x.split("=") for x in body.split(",") if x)}))
    files = a.files.split(",")
    rec = [f for f in files if FILES[f][3] is not None]
    rest = [f for f in files if FILES[f][3] is None]
    chunks = [[f] for f in rec] + [rest[i:i + 4] for i in range(0, len(rest), 4)] + ([[]] if not a.no_sf1 else [])
    specs = [(n, d, s, ch, {k: FILES[k] for k in files}) for n, d in arms for s in seeds_of(a.seeds) for ch in chunks]
    specs.sort(key=lambda x: -sum(FILES[f][2] for f in x[3]))
    res = {}
    with ProcessPoolExecutor(a.workers) as ex:
        for name, seed, out in ex.map(job, specs):
            for f, v in out.items():
                res.setdefault(name, {}).setdefault(f, {})[str(seed)] = v
    json.dump({"per_seed": res, "args": vars(a)}, open(a.out, "w"), indent=1, default=str)
    for name, fs in res.items():
        sf1 = list(fs.get("SF1", {}).values())
        d54 = list(fs.get("SF1_d54", {}).values())
        if sf1: print(f"{name}: SF1 median {np.median(sf1):.2f} mean {np.mean(sf1):.2f} "
              f"(>=0.75 on {sum(x >= 0.75 for x in sf1)}/{len(sf1)}), d54 paired {np.median(d54):+.1f}")
        for f in files:
            rows = list(fs.get(f, {}).values())
            if not rows:
                continue
            med = lambda k: float(np.median([r[k] for r in rows]))
            vt = [r["vixtimer"] for r in rows]
            line = (f"  {f:8s} morning {med('morning'):+5.1f} +24 {med('d24'):+6.1f} | timer mean "
                    f"{np.mean(vt):+5.1f} med {np.median(vt):+5.1f} ahead {sum(x > 0 for x in vt)}/{len(vt)} "
                    f"(twin mean {np.mean([r['twin_vixtimer'] for r in rows]):+.1f}) | worst med "
                    f"{med('worst'):+5.1f} min {min(r['worst'] for r in rows):+5.1f} | corp sd {med('corp_sd'):.2f}bp "
                    f"(twin {med('twin_corp_sd'):.2f}) IG sd {med('ig_sd'):.0f}bp")
            if "regain" in rows[0]:
                reg = [r["regain"] for r in rows if math.isfinite(r["regain"])]
                s1a, s2 = float(np.mean(reg)), float(np.mean([r["rise"] for r in rows]))
                s1b = sum(r["out_in"] for r in rows)
                line += (f"\n           S1a {s1a:.3f} S1b {s1b}/{len(rows)} S2 {s2:+.1f} "
                         f"[{'PASS' if S1A[0] <= s1a <= S1A[1] and s1b == len(rows) and S2[0] <= s2 <= S2[1] else 'fail'}]"
                         f" | SF4 off-phase closes {sum(r['off_phase'] for r in rows)} one-day troughs "
                         f"{sum(r['one_day_troughs'] for r in rows)} (seeds with any: "
                         f"{sum(r['off_phase'] + r['one_day_troughs'] > 0 for r in rows)}/{len(rows)})"
                         f" | paired low day {med('low_day'):.0f} {med('low_log'):+.0f} log%")
            print(line, flush=True)


if __name__ == "__main__":
    main()
