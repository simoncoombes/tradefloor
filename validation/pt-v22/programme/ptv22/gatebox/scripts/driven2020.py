"""D2, the driven 2020-21 market (registered in programme/ptv20-registration.md,
fifth registration, before any grade).

The real 2020-21 macro path, 2 January 2020 to 31 December 2021 (505
sessions): the VIX close, the fed funds target midpoint, Moody's Baa as the
corporate yield (FRED DBAA; notebook 09 used a high-yield proxy from HYG, 11
per cent at the peak against Baa's 4.9, for a yield the model reads as
investment grade), and the QE ramp from 23 March 2020, with the NBER phases in
the model's own labels (peak to February 2020, contraction March, trough
April, the NBER trough month, recovery to April 2021, expansion after); the
ninth registration corrected both. Pinned each morning, from the engine's own
opening, on the certified roster Universe.random(40, seed=111), cap-weighted at
fixed shares.

Two readings per history, each graded on its median over histories:
  D2a  the index's maximum drawdown over the path; real (S&P 500) 0.339
  D2b  sessions from the pre-crash high (the highest close of the first 40
       sessions) back to it; real 126 (19 Feb to 18 Aug 2020); a history that
       never returns counts as never.

    python driven2020.py OUT.json --arm NAME[@BASE][:dial=v,...] [...] --seeds 101-130,401-430,701-730 --workers N
"""
import argparse, csv, datetime as dt, json, math, pathlib, statistics as st, sys
from concurrent.futures import ProcessPoolExecutor

HERE = pathlib.Path(__file__).resolve().parent
# replay2020 correction: the gate box's own data, Baa and bands, read in place; only the earnings leg is added.
LEG = HERE / "earnings-2020.json"
HERE = pathlib.Path(__import__("os").environ.get("GATEBOX", HERE / ".." / ".." / ".." / "gatebox" / "scripts")).resolve()
DATA = next(p for p in (HERE / "data" / "covid-2020-2021.json", HERE / "covid-2020-2021.json") if p.exists())
BAA = next(p for p in (HERE / "edgar" / "DBAA.csv", HERE / "DBAA.csv") if p.exists())


def phases(dates):
    out = []
    for d in dates:
        if d < dt.date(2020, 3, 1): out.append("peak")
        elif d < dt.date(2020, 4, 1): out.append("contraction")
        elif d < dt.date(2020, 5, 1): out.append("trough")
        elif d < dt.date(2021, 5, 1): out.append("recovery")
        else: out.append("expansion")
    return out


def path():
    raw = json.load(open(DATA))
    dates = [dt.date.fromisoformat(d) for d in raw["dates"]]

    def fed(d):
        if d < dt.date(2020, 3, 3): return 0.01625
        if d < dt.date(2020, 3, 15): return 0.01125
        return 0.00125
    baa = {r[0]: float(r[1]) for r in csv.reader(open(BAA)) if r[1] not in ("", ".", "DBAA")}

    def baa_on(d):
        k = max(x for x in baa if x <= d.isoformat())
        return baa[k] / 100.0
    qe0 = dt.date(2020, 3, 23)
    ph = phases(dates)
    return [{"day": i, "vix": raw["vix"][i], "federal_funds_rate": fed(d),
             "corporate_bond_yield": baa_on(d),
             "qe_pe_boost": 0.0 if d < qe0 else min(0.10, 0.10 * (d - qe0).days / 90),
             "cycle": ph[i]} for i, d in enumerate(dates)], raw


def quarter_end(dates, i):
    """The last session on or before the end of the calendar quarter session i falls in (draft item 24)."""
    d = dates[i]; q = (d.month - 1) // 3
    end = dt.date(d.year + (q == 3), (3 * q + 3) % 12 + 1, 1) - dt.timedelta(days=1)
    return max(j for j, x in enumerate(dates) if x <= end)


def measure(level, earnings=None, dates=None):
    peak, worst = level[0], 0.0
    for v in level:
        peak = max(peak, v)
        worst = min(worst, v / peak - 1)
    k = max(range(40), key=lambda i: level[i])
    t = min(range(k, len(level)), key=lambda i: level[i])
    back = next((i - k for i in range(t, len(level)) if level[i] >= level[k]), math.inf)
    out = {"mdd": -worst, "recovery_sessions": back}
    # F1 (eighth registration): the fast crash, the high to the lowest close
    # within the 60 sessions after it, and how many sessions it took.
    f = min(range(k, min(k + 61, len(level))), key=lambda i: level[i])
    out["fast_drop"] = 1.0 - level[f] / level[k]
    out["fast_sessions"] = f - k
    # L1 (eighth registration): sessions from the index's trough to the
    # aggregate earnings level's trough, both within the first 250 sessions.
    # Draft item 24 (2026-10-27): read on the real's own convention, the
    # earnings trough stamped at the end of its quarter (the real, 68, stamps
    # operating EPS's trough quarter, Q2 2020, at 30 June); the index trough
    # on its day, as before. The day-stamped lead is reported beside.
    if earnings is not None:
        pt = min(range(0, 250), key=lambda i: level[i])
        et = min(range(0, 250), key=lambda i: earnings[i])
        out["earnings_lead_day"] = et - pt
        out["earnings_lead"] = (quarter_end(dates, et) if dates is not None else et) - pt
    return out


def job(spec):
    base, dials, seed = spec
    import array, tradefloor as tf
    p, _ = path()      # _ is the raw path: its dates stamp L1's quarter (item 24)
    u = list(tf.Universe.random(40, seed=111))
    shares = [float(i.shares_outstanding) for i in u]
    model = tf.ModelParams.from_preset(base, **dials) if dials else tf.ModelParams.from_preset(base)
    e = tf.Engine(seed=seed, universe=u, model=model)
    sc = tf.Scenario.from_json(json.dumps({"schema": 1, "label": "driven-2020", "days": len(p), "path": p}))
    if __import__("os").environ.get("LEG", "on") != "off":
        for w in json.load(open(LEG))["windows"]:
            sc.shock(w["target"], operation=w["operation"], value=w["value"], at=w["at"], duration=w["duration"],
                     shape=w["shape"])
    import numpy as np
    lvl, earn, cyc, rep_, repn = [], [], [], [], []
    for i in range(len(p)):
        sc.apply(e, i)
        e.run_days(1, record=False, first_day=i)
        a = array.array("d"); a.frombytes(e.prices())
        lvl.append(sum(x * y for x, y in zip(a, shares)))
        # L1 (item 25, rule amendments A, B and E; session 5's driven2020e.py): a name's reported EPS is the published
        # figure (fundamentals, which the leg writes) x (gdp cpi / base) x exp(earnings_cycle) x exp(lbs) x exp(v);
        # the index's is the sum with the index's shares. The published figure alone and the cycle are reported
        st_ = e.state_snapshot(); ec_ = st_["economy"]
        eps = np.array(e.fundamentals()[0], float)
        lbs = np.frombuffer(st_["buyback_log_shares"], dtype="<f8")[:len(eps)] if st_.get("buyback_log_shares") else 0.0
        v_ = np.frombuffer(st_["fair_value_offset"], dtype="<f8")[:len(eps)] if st_.get("fair_value_offset") else 0.0
        nom = ec_["gdp"] * ec_["cpi"] / st_["nominal_output_base"]
        b_ = np.array(shares) * eps * nom * np.exp(ec_.get("earnings_cycle", 0.0)) * np.exp(lbs)
        rep_.append(float((b_ * np.exp(v_)).sum())); repn.append(float(b_.sum()))
        earn.append(float(np.nansum(eps * np.array(shares))))
        cyc.append(ec_.get("earnings_cycle", 0.0))
    dates = [dt.date.fromisoformat(d) for d in _["dates"]]
    out = measure(lvl, earn, dates)
    out["earnings_lead_published_daily"] = out["earnings_lead"]           # reported: amendment B's first reader
    out["earnings_lead_cycle"] = measure(lvl, cyc, dates)["earnings_lead"]  # reported: item 24's, on the cycle
    # L1 (amendment A): the trough quarter of the index's quarterly reported EPS (the quarter's mean rate / 4, calendar
    # quarters on the replay's dates) among the quarters within the first 250 sessions, stamped at its end, less the
    # index's trough day
    qk = [(d.year, (d.month - 1) // 3) for d in dates]
    first = sorted(set(qk[:250]))
    def lead(series):
        qe = {q: sum(series[i] for i in range(len(series)) if qk[i] == q) / qk.count(q) / 4.0 for q in first}
        tq = min(first, key=lambda q: qe[q])
        pt = min(range(0, 250), key=lambda i: lvl[i])
        last = max(i for i in range(len(dates)) if qk[i] == tq)
        return quarter_end(dates, last) - pt, f"{tq[0]}Q{tq[1] + 1}"
    out["earnings_lead"], out["reported_trough_quarter"] = lead(rep_)
    out["earnings_lead_reported_no_v"], _ = lead(repn)
    return seed, out


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
    out = {"kind": "ptv20-driven2020", "seeds": a.seeds, "arms": {}}
    with ProcessPoolExecutor(a.workers) as ex:
        for arm in a.arm:
            head, _, body = arm.partition(":")
            name, _, base = head.partition("@")
            base = base or name
            dials = {k: float(v) for k, v in (x.split("=") for x in body.split(",") if x)}
            rows = dict(ex.map(job, [(base, dials, s) for s in seeds]))
            mdd = st.median(r["mdd"] for r in rows.values())
            rec = st.median(r["recovery_sessions"] for r in rows.values())
            never = sum(math.isinf(r["recovery_sessions"]) for r in rows.values())
            by_set = {}
            for part in a.seeds.split(","):
                ss = seeds_of(part)
                by_set[part] = {"mdd": st.median(rows[s]["mdd"] for s in ss),
                                "recovery_sessions": st.median(rows[s]["recovery_sessions"] for s in ss)}
            va = bands["D2a"]["band"]; vb = bands["D2b"]["band"]
            verdict = {"D2a": va[0] <= mdd <= va[1], "D2b": vb[0] <= rec <= vb[1]}
            fd = st.median(r["fast_drop"] for r in rows.values())
            fs = st.median(r["fast_sessions"] for r in rows.values())
            lead = st.median(r["earnings_lead"] for r in rows.values())
            lead_day = st.median(r["earnings_lead_day"] for r in rows.values())   # reported (the day stamp)
            if "F1a" in bands:
                verdict["F1"] = (bands["F1a"]["band"][0] <= fd <= bands["F1a"]["band"][1]
                                 and bands["F1b"]["band"][0] <= fs <= bands["F1b"]["band"][1])
                verdict["L1"] = bands["L1"]["band"][0] <= lead <= bands["L1"]["band"][1]
            out["arms"][name] = {"base": base, "dials": dials, "D2a_mdd": mdd, "D2b_recovery_sessions": rec,
                                 "F1a_fast_drop": fd, "F1b_fast_sessions": fs, "L1_earnings_lead": lead, "L1_earnings_lead_day": lead_day,
                                 "never_recovered": never, "n": len(rows), "by_set": by_set, "verdict": verdict,
                                 "per_seed": {str(k): v for k, v in rows.items()}}
            print(f"{name:10s} D2a mdd {mdd:.3f} {'pass' if verdict['D2a'] else 'FAIL'}  "
                  f"D2b recovery {rec} sessions {'pass' if verdict['D2b'] else 'FAIL'}  never {never}/{len(rows)}  "
                  + f"F1 {fd:.3f} in {fs} sessions  L1 lead {lead}  "
                  + "  ".join(f"{k}: {v['mdd']:.3f}/{v['recovery_sessions']}" for k, v in by_set.items()), flush=True)
    json.dump(out, open(a.out, "w"), indent=1, default=str)


if __name__ == "__main__":
    main()
