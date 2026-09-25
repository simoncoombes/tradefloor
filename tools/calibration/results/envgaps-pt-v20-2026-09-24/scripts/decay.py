"""|return| autocorrelation decay on the certified protocol, per lag.

Universe.random(40, seed=111), seeds 101-130, 252 days, the engine loop
facts.measure runs, the per-name estimator panel_statistics uses (median
across names), then the median across seeds. Lags 1, 5 and 20 must equal
the record's abs_return_acf1/5/20, which is the check on the estimator.
"""
import json, math, statistics as st, sys
from concurrent.futures import ProcessPoolExecutor
import pyarrow as pa
import tradefloor as tf
from tradefloor import facts

LAGS = [1, 2, 3, 5, 8, 12, 20, 30, 45, 60]
FIT = [1, 2, 3, 5, 8, 12, 20]

def job(args):
    preset, seed = args
    u = tf.Universe.random(40, seed=111)
    e = tf.Engine(seed=seed, universe=u, model=preset)
    for day in range(252):
        e.open_market(); e.run_session(9, 30, 3, 390); e.record(day); e.close_market()
    bars = pa.table(e.bars(grain="day")).to_pydict()
    series = facts._daily_series(bars)
    per_lag = {k: [] for k in LAGS}
    for i in range(len(u)):
        r = facts._log_returns([row[1] for row in series.get(i, ())])
        if len(r) < 30:
            continue
        a = [abs(x) for x in r]
        for k in LAGS:
            per_lag[k].append(facts._autocorrelation(a, k))
    return preset, seed, {k: st.median(v) for k, v in per_lag.items()}

def slope(curve):
    xs, ys = [], []
    for k in FIT:
        if curve[k] <= 0:
            return None
        xs.append(math.log(k)); ys.append(math.log(curve[k]))
    mx, my = st.fmean(xs), st.fmean(ys)
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)

if __name__ == "__main__":
    presets = sys.argv[1].split(",")
    workers = int(sys.argv[2]); out = sys.argv[3]
    jobs = [(p, s) for p in presets for s in range(101, 131)]
    got = {}
    with ProcessPoolExecutor(workers) as ex:
        for p, s, c in ex.map(job, jobs):
            got.setdefault(p, {})[s] = c
    res = {}
    for p, seeds in got.items():
        curve = {k: st.median([seeds[s][k] for s in seeds]) for k in LAGS}
        # bootstrap the slope over seeds
        import random
        rng = random.Random(20260923)
        keys = sorted(seeds)
        boots, undefined = [], 0
        for _ in range(2000):
            pick = [rng.choice(keys) for _ in keys]
            c = {k: st.median([seeds[s][k] for s in pick]) for k in LAGS}
            b = slope(c)
            if b is None:
                undefined += 1
            else:
                boots.append(b)
        res[p] = {"curve": curve, "slope_1_20": slope(curve),
                  "slope_boot_sd": st.pstdev(boots) if len(boots) > 1 else None,
                  "slope_undefined_share": undefined / 2000,
                  "per_seed": {str(s): seeds[s] for s in keys}}
    json.dump({"protocol": "Universe.random(40, seed=111), seeds 101-130, 252 days, per-name median, seed median",
               "lags": LAGS, "fit_lags": FIT, "results": res}, open(out, "w"), indent=1)
    for p, r in res.items():
        print(p, {k: round(v, 4) for k, v in r["curve"].items()}, "slope", r["slope_1_20"], "sd", r["slope_boot_sd"])
