"""Free histories for the r13 screen's leak repros: pt-v20 with each arm's
dials on the certified roster Universe.random(40, seed=111, bonds=True), one
session per day (open_market, 390 ticks, close_market), recording after
each close:
  pre / post     cap-weighted equity index at the last print and after the
                 close's re-mark (post is the level a holder carries)
  rpre / rpost   the three rate indices (tf.rate_specs() order) likewise
  cyc / true     published and true phase (index in CYC)
  m_*            the ten published macro fields C10 reads
  ffr gdp unemp infl corp t10 vix ant ec vfb   true economy after the close
  yld            published 2y, 10y, corporate yields (macro_fields)
  meet           1 on a close where a central-bank meeting was held
  pi             the nowcast belief when the arm sets it
usage: r13gen.py OUTDIR ARMS_FILE SEEDS DAYS WORKERS"""
import sys, os, time, json
from concurrent.futures import ProcessPoolExecutor, as_completed
import numpy as np

CYC = ("expansion", "peak", "contraction", "trough", "recovery")
FIELDS = ("vix", "federal_funds_rate", "corporate_bond_yield", "inflation_rate", "gdp_growth",
          "unemployment_rate", "oil_price", "treasury_yield_2y", "treasury_yield_10y",
          "fear_greed_index")


def one(spec):
    out, name, dials, seed, days = spec
    path = f"{out}/{name}/{seed}-free.npz"
    if os.path.exists(path):
        return name, seed, 0.0, "cached"
    import tradefloor as tf
    u = tf.Universe.random(40, seed=111, bonds=True)
    m = tf.ModelParams.from_preset("pt-v20", **dials)
    e = tf.Engine(seed=seed, universe=u, model=m)
    specs = [x["ticker"] for x in tf.rate_specs()]
    tick = list(e.tickers)
    ri = [tick.index(t) for t in specs]
    eq = [i for i, t in enumerate(tick) if t not in specs]
    shares = {x.ticker: float(x.shares_outstanding) for x in u}
    sh = np.array([shares[tick[i]] for i in eq])
    f64 = lambda: np.frombuffer(e.prices(), dtype="<f8").copy()
    scal = ("pre", "post", "ffr", "gdp", "unemp", "infl", "corp", "t10", "vix", "ant", "ec", "vfb")
    rec = {k: np.zeros(days) for k in scal}
    rec["cyc"] = np.zeros(days, np.int8); rec["true"] = np.zeros(days, np.int8); rec["meet"] = np.zeros(days, np.int8)
    rec["rpre"] = np.zeros((days, 3)); rec["rpost"] = np.zeros((days, 3)); rec["yld"] = np.zeros((days, 3))
    for f in FIELDS:
        rec["m_" + f] = np.zeros(days)
    m0 = e.macro_fields
    rec["yld0"] = np.array([m0["treasury_yield_2y"], m0["treasury_yield_10y"], m0["corporate_bond_yield"]])
    rec["rp0"] = f64()[ri]
    last_meet = e.state_snapshot()["central_bank"]["last_meeting_date"]
    PI = []
    t0 = time.time()
    for d in range(days):
        e.open_market(); e.run_session(9, 30, 3, 390)
        p = f64(); rec["pre"][d] = (p[eq] * sh).sum(); rec["rpre"][d] = p[ri]
        e.close_market()
        p = f64(); rec["post"][d] = (p[eq] * sh).sum(); rec["rpost"][d] = p[ri]
        s = e.state_snapshot(); ec = s["economy"]; cb = s["central_bank"]
        mf = e.macro_fields
        rec["cyc"][d] = CYC.index(mf["cycle"]); rec["true"][d] = CYC.index(ec["cycle_phase"])
        for f in FIELDS:
            rec["m_" + f][d] = float(mf[f])
        rec["yld"][d] = [mf["treasury_yield_2y"], mf["treasury_yield_10y"], mf["corporate_bond_yield"]]
        rec["ffr"][d] = ec["federal_funds_rate"]; rec["gdp"][d] = ec["gdp_growth"]
        rec["unemp"][d] = ec["unemployment_rate"]; rec["infl"][d] = ec["inflation_rate"]
        rec["corp"][d] = ec["corporate_bond_yield"]; rec["t10"][d] = ec["treasury_yield_10y"]
        rec["vix"][d] = ec["vix"]; rec["ant"][d] = e.earnings_anticipation
        rec["ec"][d] = ec.get("earnings_cycle", 0.0); rec["vfb"][d] = ec.get("vix_feedback", 0.0)
        rec["meet"][d] = 1 if cb["last_meeting_date"] != last_meet else 0
        last_meet = cb["last_meeting_date"]
        if "cycle_nowcast" in ec:
            PI.append(list(ec["cycle_nowcast"]) if not isinstance(ec["cycle_nowcast"], dict) else
                      [ec["cycle_nowcast"].get(c, np.nan) for c in CYC])
    rec["level"] = rec["post"]
    rec["spread"] = rec["corp"] - rec["t10"]
    if PI:
        try:
            rec["pi"] = np.array(PI, dtype=float)
        except (TypeError, ValueError):
            pass
    rec["hash"] = np.array(e.state_hash())
    os.makedirs(f"{out}/{name}", exist_ok=True)
    tmp = f"{out}/{name}/.{seed}.tmp.npz"
    np.savez_compressed(tmp, **rec)
    os.replace(tmp, path)
    return name, seed, time.time() - t0, m.fingerprint


def arms_of(path):
    arms = []
    for line in open(path):
        line = line.split("#")[0].strip()
        if not line:
            continue
        head, _, body = line.partition(":")
        name = head.split("@")[0]
        arms.append((name, {k: float(v) for k, v in (x.split("=") for x in body.split(",") if x)}))
    return arms


def seeds_of(text):
    out = []
    for part in text.split(","):
        lo, _, hi = part.partition("-")
        out += list(range(int(lo), int(hi or lo) + 1))
    return out


def main():
    out, arms_file, seeds, days, workers = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]), int(sys.argv[5])
    arms = arms_of(arms_file)
    specs = [(out, n, d, s, days) for n, d in arms for s in seeds_of(seeds)]
    fps = {}
    t0 = time.time()
    with ProcessPoolExecutor(workers) as ex:
        futs = [ex.submit(one, sp) for sp in specs]
        for f in as_completed(futs):
            try:
                name, seed, secs, fp = f.result()
            except Exception as exc:  # keep the rest of the grid
                print("ERROR", repr(exc), flush=True); continue
            fps[name] = fp
            print(name, seed, f"{secs:.0f}s", fp, f"t+{time.time() - t0:.0f}", flush=True)
    json.dump({"kind": "r13gen", "seeds": seeds, "days": days, "fingerprints": fps,
               "arms": {n: d for n, d in arms}}, open(f"{out}/r13gen.json", "w"), indent=1)


if __name__ == "__main__":
    main()
