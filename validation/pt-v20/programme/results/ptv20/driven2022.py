"""R5 and R6, the driven 2022 market (registered in programme/ptv20-registration.md,
eighth registration, before any grade).

The real 2022 macro path, 3 January to 30 December 2022 (251 sessions): the
VIX close, the effective fed funds rate, Moody's Baa as the corporate yield,
the 2- and 10-year Treasury yields (FRED DFF, DBAA, DGS2, DGS10), with the
cycle phase held at expansion (NBER dates no recession in 2022). Pinned each
morning, from the engine's own opening, on the certified roster
Universe.random(40, seed=111), cap-weighted at fixed shares.

  R5  the index's maximum drawdown over the path; real (S&P 500) 0.254
  R6  the market P/E's log change per 100 bp of the corporate yield, the
      slope of monthly-average changes January-October; real -5.2 per cent

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
    lvl, pe = [], []
    for i in range(len(p)):
        sc.apply(e, i)
        e.run_days(1, record=False, first_day=i)
        a = array.array("d"); a.frombytes(e.prices())
        lvl.append(sum(x * y for x, y in zip(a, shares)))
        pe.append(e.state_snapshot()["economy"]["market_pe"])
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
    return seed, {"mdd": -worst, "pe_per_100bp": slope(db, dl), "index_2022": lvl[-1] / lvl[0] - 1}


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
            pe = st.median(r["pe_per_100bp"] for r in rows.values())
            verdict = {"R5": bands["R5"]["band"][0] <= mdd <= bands["R5"]["band"][1],
                       "R6": bands["R6"]["band"][0] <= pe <= bands["R6"]["band"][1]}
            out["arms"][name] = {"base": base, "dials": dials, "R5_mdd": mdd, "R6_pe_per_100bp": pe,
                                 "verdict": verdict, "n": len(rows), "per_seed": {str(k): v for k, v in rows.items()}}
            print(f"{name:10s} R5 mdd {mdd:.3f} {'pass' if verdict['R5'] else 'FAIL'}  "
                  f"R6 P/E {pe:+.2f}%/100bp {'pass' if verdict['R6'] else 'FAIL'}", flush=True)
    json.dump(out, open(a.out, "w"), indent=1, default=str)


if __name__ == "__main__":
    main()
