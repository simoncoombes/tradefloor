"""Driven 2020-21 window (the envelope scenario-magnitude gap), OLS gains and return sd, per preset and seed.

Notebook 09's setup: AAPL on its FY2019 accounts plus
Universe.random(39, seed=2020), driven by the real VIX, the FOMC midpoint,
the HYG-converted credit yield and the S&P-against-EMA-200 valuation proxy
(qe_pe_boost), 505 days. Returns are simple daily returns of instrument 0.
"""
import json, sys, statistics as st, datetime as dt
from concurrent.futures import ProcessPoolExecutor
import pyarrow as pa, pyarrow.compute as pc
import tradefloor as tf

import os
ROOT = os.path.join(os.environ.get("REPO", "."), "examples/data/covid-2020-2021.json")


def inputs():
    raw = json.load(open(ROOT))
    dates = [dt.date.fromisoformat(d) for d in raw["dates"]]
    c1, c2 = dt.date(2020, 3, 3), dt.date(2020, 3, 15)
    policy = [0.01625 if d < c1 else (0.01125 if d < c2 else 0.00125) for d in dates]
    h0 = raw["hyg"][0]
    credit = [0.0554 + (h0 - x) / h0 / 3.8 for x in raw["hyg"]]
    spx = raw["spx"]
    k, trend = 2 / 201, [spx[0]]
    for v in spx[1:]:
        trend.append(trend[-1] + k * (v - trend[-1]))
    qe = [max(-0.35, min(0.35, spx[i] / trend[i] - 1)) for i in range(len(spx))]
    return raw, policy, credit, qe


def rets(x):
    return [x[i] / x[i - 1] - 1 for i in range(1, len(x))]


def diffs(x):
    return [x[i] - x[i - 1] for i in range(1, len(x))]


def beta(y, x):
    n = len(y); mx, my = sum(x) / n, sum(y) / n
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / sum((a - mx) ** 2 for a in x)


def corr(a, b):
    ma, mb = st.fmean(a), st.fmean(b)
    va = sum((v - ma) ** 2 for v in a) ** 0.5
    vb = sum((v - mb) ** 2 for v in b) ** 0.5
    return sum((v - ma) * (w - mb) for v, w in zip(a, b)) / (va * vb)


STUDY = [
    ("2020-02-19", "S&P peak, selloff begins"),
    ("2020-03-03", "Fed emergency cut 50bp"),
    ("2020-03-16", "VIX record close 82.69"),
    ("2020-03-23", "trough, unlimited QE"),
    ("2020-11-09", "vaccine efficacy"),
    ("2021-11-26", "Omicron"),
]
W_EV = 5


def events(sim, raw):
    """Notebook 09 cell 25: cumulative return over the five sessions after each event."""
    dates = raw["dates"]; n = len(dates); real = raw["aapl"]
    rows, agree = [], 0
    for d, name in STUDY:
        i = dates.index(d); j = min(i + W_EV, n - 1)
        s, r = sim[j] / sim[i] - 1, real[j] / real[i] - 1
        ok = (s >= 0) == (r >= 0); agree += ok
        rows.append({"date": d, "event": name, "sim": s, "real": r, "agree": ok})
    return {"rows": rows, "agree": agree}


def job(args):
    preset, seed = args
    raw, policy, credit, qe = inputs()
    n = len(raw["dates"])
    path = [{"day": i, "vix": raw["vix"][i], "federal_funds_rate": policy[i],
             "corporate_bond_yield": credit[i], "qe_pe_boost": qe[i]} for i in range(n)]
    scen = tf.Scenario.from_json(json.dumps({"schema": 1, "label": "covid", "days": n, "path": path}))
    aapl = tf.Instrument("AAPL", "technology", initial_price=raw["aapl"][0],
                         shares_outstanding=17.77e9, eps=2.97, book_value_per_share=5.09,
                         revenue_growth=-0.02, avg_volume=140e6, beta=1.2, short_interest=124e6)
    u = tf.Universe([aapl]); u.extend(tf.Universe.random(39, seed=2020))
    e = tf.Engine(seed=seed, universe=u, model=preset)
    for i in range(n):
        scen.apply(e, i)
        e.run_days(1, first_day=i)
    b = pa.table(e.bars(grain="day"))
    close = pc.filter(b, pc.equal(b["instrument_id"], 0))["close"].to_pylist()
    r = rets(close)
    return preset, seed, r, close


if __name__ == "__main__":
    presets = sys.argv[1].split(",")
    seeds = [int(s) for s in sys.argv[2].split(",")]
    out = sys.argv[3]
    raw, policy, credit, qe = inputs()
    r_real = rets(raw["aapl"])
    drivers = {"vix": diffs(raw["vix"]), "credit": diffs(credit), "valuation": diffs(qe)}
    real = {k: beta(r_real, d) for k, d in drivers.items()}
    realc = {k: corr(r_real, d) for k, d in drivers.items()}
    sd_real = st.pstdev(r_real)
    res = {"real": {"beta": real, "corr": realc, "sd": sd_real}}
    with ProcessPoolExecutor(int(sys.argv[4]) if len(sys.argv) > 4 else 4) as ex:
        for p, s, r, close in ex.map(job, [(p, s) for p in presets for s in seeds]):
            res.setdefault(p, {})[s] = {
                "beta": {k: beta(r, d) for k, d in drivers.items()},
                "corr": {k: corr(r, d) for k, d in drivers.items()},
                "sd": st.pstdev(r), "sd_ratio": st.pstdev(r) / sd_real,
                "abs_vs_vix": corr([abs(x) for x in r], raw["vix"][1:]),
                "events": events(close, raw),
            }
    json.dump(res, open(out, "w"), indent=1)
    print("real", {k: round(v, 5) for k, v in real.items()}, "corr", {k: round(v, 3) for k, v in realc.items()}, "sd", round(sd_real, 5))
    for p in presets:
        rows = res[p]
        for k in drivers:
            vals = [rows[s]["beta"][k] for s in seeds]
            print(f"{p} beta {k:9s} median {st.median(vals):+.5f}  range {min(vals):+.5f} .. {max(vals):+.5f}  real {real[k]:+.5f}  ratio {st.median(vals)/real[k]:.3f}")
        for k in drivers:
            vals = [rows[s]["corr"][k] for s in seeds]
            print(f"{p} corr {k:9s} median {st.median(vals):+.3f}  real {realc[k]:+.3f}")
        v = [rows[s]["sd_ratio"] for s in seeds]
        print(f"{p} sd ratio median {st.median(v):.3f} range {min(v):.3f} .. {max(v):.3f}")
        v = [rows[s]["abs_vs_vix"] for s in seeds]
        print(f"{p} |r| vs VIX median {st.median(v):+.3f}")
        ev = rows[2020]["events"]
        print(f"{p} seed 2020 event study: sign agreement {ev['agree']}/{len(ev['rows'])}")
        for row in ev["rows"]:
            print(f"    {row['date']} {row['event']:28s} sim {row['sim']:+.1%} real {row['real']:+.1%} {'yes' if row['agree'] else 'NO'}")
