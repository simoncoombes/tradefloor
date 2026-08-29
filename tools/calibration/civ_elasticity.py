"""The CIV elasticity, for any candidate: how much a name's OWN variance
rises with the market's.

Herskovic, Kelly, Lustig and Van Nieuwerburgh (JFE 2016) call this common
idiosyncratic volatility. The elasticity is the log-log slope of median
residual variance on realised common variance, both from the same held-VIX-45
run, so the estimator is identical on the model and the real side.

Round 86 measured it and closed the direction it was built to open. Real is
+0.283 with a standard error of 0.104 over ten non-overlapping annual windows,
a 95% interval of +0.079 to +0.487, and every shipped or candidate preset sits
INSIDE it: pt-v14 at +0.173, d37ship at +0.186, d32ship at +0.198, each to
about +/- 0.017 on twenty-four seeds. The model side is tight and the real side
is not, because ten windows is all the non-overlapping data a decade holds.

So this script exists to be re-run against a BETTER REAL ESTIMATE, not against
a new dial. Overlapping windows, a roster wider than forty names, or a panel
estimator pooling across windows would tighten the real interval; if one of
those puts real outside the model's, the gap is real and a coupling dial is
worth building. Until then it would be fitting to a point estimate whose own
interval spans a factor of six.
"""
import json, math, sys, os
from concurrent.futures import ProcessPoolExecutor
SP = "/private/tmp/claude-503/-Users-simoncoombes-nw-Dev/76cab463-16f4-4a89-baac-68bc86680c4c/scratchpad"
sys.path.append(f"{SP}/rnd/tools/calibration")

def stats_from_run(m, seed, vix=45.0):
    import pyarrow as pa, pyarrow.compute as pc
    import tradefloor as pt
    u = pt.Universe.random(40, seed=111)
    e = pt.Engine(seed=seed, universe=u, model=m)
    scen = pt.Scenario().hold(vix=vix)
    for day in range(252):
        scen.apply(e, day)
        e.run_days(1, first_day=day)
    b = pa.table(e.bars(grain="day"))
    ids = sorted(set(b["instrument_id"].to_pylist()))
    mat = []
    for i in ids:
        close = pc.filter(b, pc.equal(b["instrument_id"], i))["close"].to_pylist()
        mat.append([math.log(close[t]/close[t-1]) for t in range(1, len(close))
                    if close[t] > 0 and close[t-1] > 0])
    n = min(len(r) for r in mat); mat = [r[:n] for r in mat]
    T, N = n, len(mat)
    mkt = [sum(mat[k][t] for k in range(N))/N for t in range(T)]
    mm = sum(mkt)/T
    v_common = sum((x-mm)**2 for x in mkt)/T
    resids = []
    for row in mat:
        mu = sum(row)/T
        v_tot = sum((x-mu)**2 for x in row)/T
        cov = sum((row[t]-mu)*(mkt[t]-mm) for t in range(T))/T
        beta = cov/v_common
        resids.append(v_tot - beta*beta*v_common)
    resids.sort()
    return {"v_common": v_common, "v_resid_med": resids[N//2]}

def one(job):
    label, base, ov, seed = job
    import gate_pick
    return label, seed, stats_from_run(gate_pick.model(base, ov), seed)

if __name__ == "__main__":
    cands = json.load(open(f"{SP}/{sys.argv[1]}"))
    seeds = list(range(101, 125))
    jobs = [(c["label"], c["base"], c["overrides"], s) for c in cands for s in seeds]
    out = {}
    with ProcessPoolExecutor(8) as ex:
        for label, seed, row in ex.map(one, jobs):
            out.setdefault(label, {})[seed] = row
    json.dump(out, open(f"{SP}/civ/decomp-r86.json", "w"), indent=1)
    print(f"wrote {len(out)} candidates x {len(seeds)} seeds")
