"""C10, no public macro signal predicts returns (eleventh registration).

Reads the long run's free histories (longrun.py writes the published macro
series beside the index level: `cyc`, the phase's index in CYCLES, and
`m_<field>` for each published macro field) and runs timing rules on the
certified roster's cap-weighted index against holding it.

Timing, as a trader could act: the position held over the session that ends
at close d+1 is decided from what was published after close d-1 (the macro
step runs at the close, and its values are read before the next open). A
switch costs 5 bp. The first 252 sessions are left out, as the long run's
other rows leave them out.

C10a, for every rule: the median over histories of the annualised excess
return over buy-and-hold at most +1.0 point, and the share of histories where
the rule beats buy-and-hold at most two thirds. The rules (twelfth
registration adds the event rules): the four phase rules; for each published
field, out while its 21-session change is up, and while it is down; and out
for 21, 63 or 126 sessions after one of its largest one-session or
five-session moves up, or down (above the 95th, 99th or 99.9th percentile of the
history's nonzero moves over that span).
C10b, the index's mean log return over the 63 sessions after the published
entry into a contraction, and into a recovery, less the unconditional mean
over 63 sessions, in points: no lower than -5.9 after a contraction's entry
and no higher than +4.3 after a recovery's.

    python c10.py BOXDIR --arms A,B,... [--lag N] --out FILE.json
    (--lag N reads the phase N sessions late, the case of a phase published as
    the NBER publishes it; it is reported, not graded)
"""
import json
import math
import pathlib
import statistics as st
import sys

import numpy as np

CYCLES = ("expansion", "peak", "contraction", "trough", "recovery")
FIELDS = ("vix", "federal_funds_rate", "corporate_bond_yield", "inflation_rate", "gdp_growth",
          "unemployment_rate", "oil_price", "treasury_yield_2y", "treasury_yield_10y",
          "fear_greed_index")
COST = 5e-4
EVENT_K = (21, 63, 126)
EVENT_SPAN = (1, 5)
EVENT_Q = (95, 99, 99.9)
BURN = 252


def rules(z, lag):
    """Each rule's in-market flag for the session ending at close d+1, from
    what was published by close d-1 (index d-1 of the arrays)."""
    cyc = z["cyc"].astype(int)
    if lag:
        cyc = np.concatenate([np.full(lag, cyc[0]), cyc[:-lag]])
    names = {i: n for i, n in enumerate(CYCLES)}
    ph = np.array([names[c] for c in cyc])
    out = {
        "out_contraction_trough": ~np.isin(ph, ("contraction", "trough")),
        "out_peak_contraction": ~np.isin(ph, ("peak", "contraction")),
        "in_trough_recovery": np.isin(ph, ("trough", "recovery")),
        "out_contraction": ph != "contraction",
    }
    for f in FIELDS:
        x = z["m_" + f]
        ch = np.concatenate([np.zeros(21), x[21:] - x[:-21]])
        out[f"out_{f}_up21"] = ~(ch > 0)
        out[f"out_{f}_down21"] = ~(ch < 0)
        # Event rules (twelfth registration): out for K sessions after one of
        # the field's largest one-session or five-session moves, up or down.
        # The threshold is the 95th, 99th or 99.9th percentile of the history's
        # nonzero moves over that span (a look-ahead that can only help the
        # rule).
        for span in EVENT_SPAN:
            d = np.concatenate([np.zeros(span), x[span:] - x[:-span]])
            nz = np.abs(d[d != 0])
            if not len(nz):
                continue
            for q in EVENT_Q:
                thr = np.percentile(nz, q)
                tag = ("" if span == 1 else f"{span}d_") + ("" if q == 99 else f"p{q}_")
                for sign, ev in (("up", d >= thr), ("down", d <= -thr)):
                    for k in EVENT_K:
                        off = np.zeros(len(x), dtype=bool)
                        for i in np.nonzero(ev & (d != 0))[0]:
                            off[i:i + k] = True
                        out[f"out{k}_after_{f}_{tag}{sign}"] = ~off
    return out


def excess(level, flag):
    r = np.diff(np.log(level))            # r[d] is close d -> close d+1
    pos = np.zeros(len(r), dtype=bool)
    pos[1:] = flag[:len(r) - 1]           # decided from index d-1
    pos = pos[BURN:]; rr = r[BURN:]
    switches = np.abs(np.diff(pos.astype(float))).sum()
    rule = (rr * pos).sum() - COST * switches
    hold = rr.sum()
    years = len(rr) / 252.0
    return 100.0 * (rule - hold) / years


def events(level, cyc, lag):
    if lag:
        cyc = np.concatenate([np.full(lag, cyc[0]), cyc[:-lag]])
    li = np.log(level)
    out = {"contraction": [], "recovery": []}
    for d in range(max(BURN, 1), len(cyc) - 64):
        if cyc[d] != cyc[d - 1] and CYCLES[cyc[d]] in out:
            # published after close d; the first tradable close is d+1
            out[CYCLES[cyc[d]]].append(li[d + 1 + 63] - li[d + 1])
    uncond = float(np.mean(li[BURN + 63:] - li[BURN:-63]))
    return out, uncond


def main():
    a = sys.argv
    box = pathlib.Path(a[1])
    arms = a[a.index("--arms") + 1].split(",")
    lag = int(a[a.index("--lag") + 1]) if "--lag" in a else 0
    outp = a[a.index("--out") + 1]
    res = {}
    for arm in arms:
        files = sorted((box / "longrun" / arm).glob("*-free.npz"))
        ex, ev, un = {}, {"contraction": [], "recovery": []}, []
        n = 0
        for f in files:
            z = np.load(f)
            if "cyc" not in z.files:
                continue
            n += 1
            for name, flag in rules(z, lag).items():
                ex.setdefault(name, []).append(excess(z["level"], flag))
            e, u = events(z["level"], z["cyc"].astype(int), lag)
            for k in ev:
                ev[k] += e[k]
            un.append(u)
        if not n:
            res[arm] = {"measured": False}
            continue
        table = {k: {"median_excess": st.median(v), "share_ahead": sum(x > 0 for x in v) / len(v)}
                 for k, v in ex.items()}
        worst = max(table, key=lambda k: (table[k]["median_excess"], table[k]["share_ahead"]))
        u = st.fmean(un)
        drift = {k: 100.0 * (st.fmean(v) - u) if v else float("nan") for k, v in ev.items()}
        c10a = all(t["median_excess"] <= 1.0 and t["share_ahead"] <= 2 / 3 for t in table.values())
        c10b = drift["contraction"] >= -5.9 and drift["recovery"] <= 4.3
        res[arm] = {"histories": n, "lag": lag, "rules": table, "worst": worst,
                    "worst_median_excess": table[worst]["median_excess"],
                    "worst_share_ahead": table[worst]["share_ahead"],
                    "drift_63": drift, "events": {k: len(v) for k, v in ev.items()},
                    "C10a": c10a, "C10b": c10b, "pass": c10a and c10b}
        print(f"{arm:12s} worst {worst:32s} {table[worst]['median_excess']:+.2f} pts/yr, ahead "
              f"{table[worst]['share_ahead']:.2f}; drift63 contraction {drift['contraction']:+.1f} "
              f"recovery {drift['recovery']:+.1f} -> {'pass' if c10a and c10b else 'FAIL'}", flush=True)
    json.dump(res, open(outp, "w"), indent=1)


if __name__ == "__main__":
    main()
