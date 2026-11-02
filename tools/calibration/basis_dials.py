"""Derive the index futures' basis dials, and read the futures' rows.

    python tools/calibration/basis_dials.py derive [--seeds 8101-8124] [--years 21]
    python tools/calibration/basis_dials.py measure --dials '{"basis_sd": 3.9}' \\
        [--seeds 8201-8240] [--years 21] [--night 10]

The index futures' basis noise (`basis_sd`, `basis_persistence`) is the one
part of their price fitted to data. The real statistics are those of rows IF1
and IF2: the closing basis of ES against its carry fair value on the S&P 500,
2020-10-26 to 2026-09-09 (CME's settlement moved to the 16:00 index close on
2020-10-26), read per window of 252 observations as the residual of a
regression on time to expiry, with sessions six or fewer before an expiry
left out. The windows' median residual sd is 3.738 bp and their median lag-1
autocorrelation 0.421.

`derive` runs held-out histories at the real values, then scales `basis_sd`
by the real sd over the model's (the basis is the noise times `basis_sd`, so
the read is linear in it) and moves `basis_persistence` by the real lag-1
autocorrelation less the model's (the estimator's bias on a 252-session window
is a small shift), and repeats until both read within a hundredth of their
targets. It prints the two values. On seeds 8101 to 8124, 24 histories of
21 years, it gives `basis_sd` 3.753 and `basis_persistence` 0.429 in one
step, which read 3.736 and 0.420. `measure` runs fresh seeds with the dials
given and prints, against their bands (on seeds 8201 to 8224 at the fitted
values: IF1 3.72, IF2 0.418, NS1 0.516, NS2 3.66, NS2_tick 12.4, NS2_any
12.8, IF3 0):

* IF1 and IF2, by the estimator above, pooled over every history's windows;
* NS1, the night's share of the front future's daily variance, night from the
  last close's mark to the 09:29 mid after a whole night and day from there to
  the close, per history over sessions whose front is one contract from close
  to close, median over histories (band 0.30 to 0.60);
* NS2, the sd of the 09:29 mid over the same contract's fair value on the
  opening prints, the index the futures settle on, bp, per history, median
  over histories (at most 6.27 bp, IF1's ceiling); beside it the same read
  against fair value after the session's first tick (`NS2_tick`), and that
  read with the contract the first listed after the open, which on an expiry
  morning is the next one (`NS2_any`);
* IF3, the largest settlement less its reference over the reference.

The model is pt-v21 with the phase 1 switches on (the index, the forecast at
252 sessions with its derived dials, the futures and `--night` night steps),
on `Universe.random(40, seed=111)`. The first year of each history is left
out, as the long-run macro rows leave it out.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics as st
from concurrent.futures import ProcessPoolExecutor

YEAR = 252
#: Row IF1's and IF2's real centres and bands, and NS1's and NS2's bands.
REAL = {"IF1": 3.738, "IF2": 0.421}
BANDS = {"IF1": (1.209, 6.267), "IF2": (0.127, 0.716), "NS1": (0.300, 0.600),
         "NS2": (0.0, 6.267), "NS2_tick": (0.0, 6.267), "NS2_any": (0.0, 6.267),
         "IF3": (0.0, 1e-12)}
#: The forecast's derived dials (tools/calibration/forecast_dials.py).
FORECAST = dict(forecast_vix_dispersion=0.3, forecast_vix_dispersion_half_life=31.5,
                forecast_policy_shadow_discount=0.31, forecast_policy_persistence=0.62,
                forecast_policy_reversion=0.0475, forecast_policy_neutral=1.4)
SWITCHES = dict(index_level_listed=1.0, forecast_horizon_sessions=252.0,
                futures_index_listed=1.0, **FORECAST)


def history(args):
    """One seed's front-future reads: per close the expiry, mark and fair
    value; per morning the 09:29 mid, its contract, that contract's fair
    value on the opening prints and after the first tick, and the first
    listed contract's after the first tick."""
    import tradefloor as tf

    seed, years, dials, night = args
    params = tf.ModelParams.from_preset(
        "pt-v21", **SWITCHES, night_session_steps=float(night), **dials)
    engine = tf.Engine(seed=seed, universe=tf.Universe.random(40, seed=111), model=params)
    closes, mornings, worst = [], [], 0.0
    for day in range(years * YEAR):
        morning = None
        if day:
            engine.run_night()
            front = engine.contracts()[0]["symbol"]
            morning = [day, front, engine.quote(front)["mid"]]
        engine.open_market()
        listed = [c["symbol"] for c in engine.contracts()]
        opening = engine.quote(morning[1])["fair"] if morning and morning[1] in listed else None
        engine.run_session(9, 30, 3, 1)
        if morning is not None:
            tick = engine.quote(morning[1])["fair"] if opening is not None else None
            morning += [opening, tick, engine.quote(listed[0])["fair"]]
            mornings.append(morning)
        engine.run_session(9, 31, 3, 389)
        engine.close_market()
        c = engine.contracts()[0]
        q = engine.quote(c["symbol"])
        closes.append((day, c["symbol"], c["expiry"], q["mark"], q["fair"]))
        for s in engine.settlements(day):
            worst = max(worst, abs(s["value"] - s["reference"]) / s["reference"])
    return {"seed": seed, "closes": closes, "mornings": mornings, "if3": worst}


def ols(x, y):
    n = len(x)
    mx, my = sum(x) / n, sum(y) / n
    sxx = sum((a - mx) ** 2 for a in x)
    s = sum((a - mx) * (b - my) for a, b in zip(x, y)) / sxx if sxx > 0 else 0.0
    return my - s * mx, s


def corr(x, y):
    n = len(x)
    mx, my = sum(x) / n, sum(y) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
    sxx = sum((a - mx) ** 2 for a in x)
    syy = sum((b - my) ** 2 for b in y)
    return sxy / math.sqrt(sxx * syy)


def basis_windows(h):
    """IF1 and IF2 per window of 252 kept closes, anchored at the last."""
    obs = [(s, 1e4 * math.log(mark / fair), (x - s) / YEAR)
           for s, _, x, mark, fair in h["closes"] if s >= YEAR and x - s > 6]
    out = []
    n = len(obs)
    for end in range(n, YEAR - 1, -YEAR):
        part = obs[end - YEAR:end]
        b = [o[1] for o in part]
        t = [o[2] for o in part]
        a, s = ols(t, b)
        e = [bb - a - s * tt for bb, tt in zip(b, t)]
        m = sum(e) / len(e)
        sd = math.sqrt(sum((x - m) ** 2 for x in e) / (len(e) - 2))
        pairs = [(e[k - 1], e[k]) for k in range(1, len(part)) if part[k][0] == part[k - 1][0] + 1]
        out.append({"IF1": sd, "IF2": corr([p[0] for p in pairs], [p[1] for p in pairs])})
    return out


def night_reads(h):
    """NS1's share and NS2's three sds for one history."""
    close = {s: (sym, mark) for s, sym, _, mark, _ in h["closes"]}
    g, it, opening, tick, any_ = [], [], [], [], []
    for day, sym, mid, fair_open, fair_tick, fair_any in h["mornings"]:
        if day < YEAR + 1 or mid is None:
            continue
        any_.append(1e4 * math.log(mid / fair_any))
        if fair_open is not None:
            opening.append(1e4 * math.log(mid / fair_open))
            tick.append(1e4 * math.log(mid / fair_tick))
        prev, now = close.get(day - 1), close.get(day)
        if prev and now and prev[0] == now[0] == sym:
            g.append(math.log(mid / prev[1]))
            it.append(math.log(now[1] / mid))
    vg, vi = st.pvariance(g), st.pvariance(it)
    return vg / (vg + vi), st.stdev(opening), st.stdev(tick), st.stdev(any_)


def run(seeds, years, dials, night, workers):
    with ProcessPoolExecutor(workers) as pool:
        return list(pool.map(history, [(s, years, dials, night) for s in seeds]))


def rows(histories):
    windows = [w for h in histories for w in basis_windows(h)]
    nights = [night_reads(h) for h in histories]
    return {
        "IF1": st.median(w["IF1"] for w in windows),
        "IF2": st.median(w["IF2"] for w in windows),
        "NS1": st.median(n[0] for n in nights),
        "NS2": st.median(n[1] for n in nights),
        "NS2_tick": st.median(n[2] for n in nights),
        "NS2_any": st.median(n[3] for n in nights),
        "IF3": max(h["if3"] for h in histories),
        "windows": len(windows),
        "histories": len(histories),
    }


def seed_range(text):
    a, b = (int(x) for x in text.split("-"))
    return list(range(a, b + 1))


def derive(a):
    seeds = seed_range(a.seeds)
    sd, rho = REAL["IF1"], REAL["IF2"]
    for k in range(a.rounds):
        r = rows(run(seeds, a.years, dict(basis_sd=sd, basis_persistence=rho), a.night, a.workers))
        print(json.dumps({"round": k, "basis_sd": sd, "basis_persistence": rho,
                          "IF1": r["IF1"], "IF2": r["IF2"], "windows": r["windows"]}))
        if abs(r["IF1"] - REAL["IF1"]) < 0.01 and abs(r["IF2"] - REAL["IF2"]) < 0.01:
            break
        sd = round(sd * REAL["IF1"] / r["IF1"], 3)
        rho = round(rho + REAL["IF2"] - r["IF2"], 3)
    print(json.dumps({"basis_sd": sd, "basis_persistence": rho}))


def measure(a):
    dials = json.loads(a.dials)
    r = rows(run(seed_range(a.seeds), a.years, dials, a.night, a.workers))
    for k, (lo, hi) in BANDS.items():
        print(f"{k:8s} {r[k]:.6g}  band {lo:g} to {hi:g}  {'in' if lo <= r[k] <= hi else 'OUT'}")
    print(json.dumps({"dials": dials, **r}))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("derive", help="fit basis_sd and basis_persistence on held-out seeds")
    d.add_argument("--seeds", default="8101-8124")
    d.add_argument("--rounds", type=int, default=4)
    m = sub.add_parser("measure", help="read IF1, IF2, NS1, NS2 and IF3 on fresh seeds")
    m.add_argument("--dials", required=True, help="a JSON object of dials")
    m.add_argument("--seeds", default="8201-8240")
    for q in (d, m):
        q.add_argument("--years", type=int, default=21)
        q.add_argument("--night", type=int, default=10, help="night_session_steps")
        q.add_argument("--workers", type=int, default=8)
    a = p.parse_args(argv)
    {"derive": derive, "measure": measure}[a.cmd](a)


if __name__ == "__main__":
    main()
