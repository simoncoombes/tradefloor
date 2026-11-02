"""R5 and R6, the driven 2022 market (registered in programme/ptv20-registration.md,
eighth registration, before any grade).

The real 2022 macro path, 3 January to 30 December 2022 (251 sessions): the
VIX close, the effective fed funds rate, Moody's Baa as the corporate yield,
the 2- and 10-year Treasury yields (FRED DFF, DBAA, DGS2, DGS10), with the
cycle phase held at expansion (NBER dates no recession in 2022). Pinned each
morning, from the engine's own opening, on the certified roster
Universe.random(40, seed=111), cap-weighted at fixed shares.

  R5  the index's maximum drawdown over the path; real (S&P 500) 0.254
  R6  the market P/E's log change per 100 bp of the corporate yield; real -5.43, band -10.86 to -2.72
      (convention rule amendments D and E, draft item 25): per seed, the level-on-level slope of
      100 ln(sum sh P / sum sh E_TTM) on Baa over 11 monthly points, December 2021 (the engine's opening, before
      the first pinned session, with DBAA's December 2021 average 3.296%) and January-October 2022 (monthly averages
      over the replay's sessions). E is the index's reported EPS with v, sum sh x eps x (gdp cpi / base) x
      exp(earnings_cycle) x exp(lbs) x exp(v); TTM is the last four complete calendar quarters, each the quarter's
      mean rate / 4, the quarters before the replay padded with the opening's rate / 4. R6 is the median over
      seeds. The registered form (the market P/E's monthly-average changes, real -5.2) is reported beside as
      pe_per_100bp

    python driven2022.py OUT.json --arm NAME[@BASE][:dial=v,...] [...] --seeds 101-130,401-430,701-730 --workers N
"""
import argparse, json, math, pathlib, statistics as st
from concurrent.futures import ProcessPoolExecutor

HERE = pathlib.Path(__file__).resolve().parent
DATA = next(p for p in (HERE / "data" / "driven-2022.json", HERE / "driven-2022.json") if p.exists())


def path():
    raw = json.load(open(DATA))
    return [{"day": i, "vix": raw["vix"][i], "federal_funds_rate": raw["dff"][i] / 100.0,
             "corporate_bond_yield": raw["baa"][i] / 100.0,
             "treasury_yield_10y": raw["dgs10"][i] / 100.0, "treasury_yield_2y": raw["dgs2"][i] / 100.0,
             "cycle": "expansion"} for i in range(len(raw["dates"]))], raw


DEC_BAA = 3.296


def slope(x, y):
    mx, my = st.fmean(x), st.fmean(y)
    sxx = sum((a - mx) ** 2 for a in x)
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / sxx if sxx > 0 else float("nan")


def job(spec):
    base, dials, seed = spec
    import array, tradefloor as tf
    p, raw = path()
    u = list(tf.Universe.random(40, seed=111))
    shares = [float(i.shares_outstanding) for i in u]
    model = tf.ModelParams.from_preset(base, **dials) if dials else tf.ModelParams.from_preset(base)
    e = tf.Engine(seed=seed, universe=u, model=model)
    sc = tf.Scenario.from_json(json.dumps({"schema": 1, "label": "driven-2022", "days": len(p), "path": p}))
    import numpy as np
    sh = np.array(shares)

    def reported():         # the index's reported EPS with v (amendment E), from the engine's state
        st_ = e.state_snapshot(); ec_ = st_["economy"]
        eps = np.array(e.fundamentals()[0], float); n = len(eps)
        lbs = np.frombuffer(st_["buyback_log_shares"], "<f8")[:n] if st_.get("buyback_log_shares") else 0.0
        v = np.frombuffer(st_["fair_value_offset"], "<f8")[:n] if st_.get("fair_value_offset") else 0.0
        nom = ec_["gdp"] * ec_["cpi"] / st_["nominal_output_base"]
        return float((sh * eps * nom * np.exp(ec_.get("earnings_cycle", 0.0)) * np.exp(lbs) * np.exp(v)).sum())
    a = array.array("d"); a.frombytes(e.prices())
    lvl0, rep0 = sum(x * y for x, y in zip(a, shares)), reported()
    lvl, pe, rep = [], [], []
    for i in range(len(p)):
        sc.apply(e, i)
        e.run_days(1, record=False, first_day=i)
        a = array.array("d"); a.frombytes(e.prices())
        lvl.append(sum(x * y for x, y in zip(a, shares)))
        pe.append(e.state_snapshot()["economy"]["market_pe"])
        rep.append(reported())
    peak, worst = lvl[0], 0.0
    for v in lvl:
        peak = max(peak, v); worst = min(worst, v / peak - 1)
    months = {}
    for i, d in enumerate(raw["dates"]):
        m = d[:7]
        if m <= "2022-10":
            months.setdefault(m, []).append(i)
    keys = sorted(months)
    lpe = [math.log(st.fmean(pe[i] for i in months[k])) for k in keys]
    baa = [st.fmean(raw["baa"][i] for i in months[k]) for k in keys]
    dl = [100.0 * (lpe[j] - lpe[j - 1]) for j in range(1, len(keys))]
    db = [baa[j] - baa[j - 1] for j in range(1, len(keys))]
    # R6 (amendments D and E): TTM reported EPS from calendar quarters, the December point at the opening
    qk = [d[:4] + "Q" + str((int(d[5:7]) - 1) // 3 + 1) for d in raw["dates"]]; order = sorted(set(qk))
    qe = {k: st.fmean(rep[i] for i, x in enumerate(qk) if x == k) / 4 for k in order}
    ttm = []
    for i in range(len(lvl)):
        done = [k for k in order if k < qk[i]][-4:]
        ttm.append(sum(qe[k] for k in done) + (4 - len(done)) * rep0 / 4)
    y = [100 * math.log(lvl0 / rep0)] + [100 * math.log(st.fmean(lvl[i] / ttm[i] for i in months[k])) for k in keys]
    b = [DEC_BAA] + baa
    return seed, {"mdd": -worst, "r6": slope(b, y), "pe_per_100bp": slope(db, dl), "index_2022": lvl[-1] / lvl[0] - 1}


def seeds_of(text):
    out = []
    for part in text.split(","):
        lo, _, hi = part.partition("-")
        out += list(range(int(lo), int(hi or lo) + 1))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out"); ap.add_argument("--arm", action="append", required=True)
    ap.add_argument("--seeds", default="101-130,401-430,701-730"); ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    bands = json.load(open(HERE / "bands.json"))["rows"]
    seeds = seeds_of(a.seeds)
    out = {"kind": "ptv20-driven2022", "seeds": a.seeds, "arms": {}}
    with ProcessPoolExecutor(a.workers) as ex:
        for arm in a.arm:
            head, _, body = arm.partition(":")
            name, _, base = head.partition("@")
            base = base or name
            dials = {k: float(v) for k, v in (x.split("=") for x in body.split(",") if x)}
            rows = dict(ex.map(job, [(base, dials, s) for s in seeds]))
            mdd = st.median(r["mdd"] for r in rows.values())
            r6 = st.median(r["r6"] for r in rows.values())
            pe = st.median(r["pe_per_100bp"] for r in rows.values())
            verdict = {"R5": bands["R5"]["band"][0] <= mdd <= bands["R5"]["band"][1],
                       "R6": bands["R6"]["band"][0] <= r6 <= bands["R6"]["band"][1]}
            out["arms"][name] = {"base": base, "dials": dials, "R5_mdd": mdd, "R6": r6, "R6_pe_per_100bp": pe,
                                 "verdict": verdict, "n": len(rows), "per_seed": {str(k): v for k, v in rows.items()}}
            print(f"{name:10s} R5 mdd {mdd:.3f} {'pass' if verdict['R5'] else 'FAIL'}  "
                  f"R6 {r6:+.2f}%/100bp {'pass' if verdict['R6'] else 'FAIL'} (registered form {pe:+.2f})", flush=True)
    json.dump(out, open(a.out, "w"), indent=1, default=str)


if __name__ == "__main__":
    main()
