"""S1a, S1b and S2, the packaged recession recovers (twelfth registration).

Grades the recession.yml the engine ships (tf.Scenario.load("recession"), the
file inside the installed tradefloor package at the box's commit, not a
copy). Per seed, the certified roster Universe.random(40, seed=111), its index
the cap-weighted closes at fixed shares, run twice from the same seed: once
with the scenario applied from day 0 (it fires at day 50) and once without.

  S1a  the share of the paired log fall, at its lowest, won back 252
       sessions later: (d[t+252] - d[t]) / -d[t], d the log index against
       the twin and t its minimum. Mean over seeds, in 45% to 100%.
  S1b  seeds whose (true) cycle phase has left contraction and trough within
       504 sessions (24 months) of the onset, the first contraction session
       from day 50: all of them.
  S2   the index's own rise in the 252 sessions after its lowest close from
       day 50 on, mean over seeds, in +25% to +80%.

The phase is the engine's true one (state_snapshot()["economy"]["cycle_phase"]):
with cycle_publication_lag set, macro_fields["cycle"] publishes it a year
late. Both are recorded. The grade reads seeds 101-130 (the file was tuned on
301-330), 900 sessions. The statistics are the
desk's measure.py (the same numbers on the same seeds), which set the leading
dials by hand; here each arm is read as the long run reads it,
NAME[@BASE][:dial=v,...].

    python recession_rows.py OUT.json --arm pt-v19: --arm pt-v20@pt-v20: \\
        --seeds 301-330 --days 900 --workers 60
"""
import argparse
import hashlib
import json
import math
from concurrent.futures import ProcessPoolExecutor

import numpy as np

SHOCK = 50
AFTER = 252
EXIT_WITHIN = 504
S1A = (0.45, 1.00)
S2 = (25.0, 80.0)
AT = (21, 63, 120, 300, 365, 450, 599, 700, 899)


def f64(raw):
    return np.frombuffer(raw, dtype="<f8")


def path(base, dials, seed, days, scen):
    import tradefloor as tf
    u = tf.Universe.random(40, seed=111)
    cap = np.array([x.shares_outstanding for x in u])
    m = tf.ModelParams.from_preset(base, **dials) if dials else tf.ModelParams.from_preset(base)
    sc = tf.Scenario.load(scen) if scen else None
    e = tf.Engine(seed=seed, universe=u, model=m)
    ix, true, pub = [], [], []
    for d in range(days):
        if sc is not None:
            sc.apply(e, d)
        e.open_market(); e.run_session(9, 30, 3, 390); e.close_market()
        p = f64(e.prices())[:len(u)]
        if not (np.all(np.isfinite(p)) and np.all(p > 0)):
            raise RuntimeError(f"seed {seed} day {d}: a price is not finite and positive")
        ix.append(float(np.log((p * cap).sum())))
        true.append(e.state_snapshot()["economy"]["cycle_phase"])
        pub.append(e.macro_fields["cycle"])
    return np.array(ix), true, pub


def one(spec):
    base, dials, seed, days, scen = spec
    c_ix, _, _ = path(base, dials, seed, days, None)
    s_ix, true, pub = path(base, dials, seed, days, scen)
    dd = (s_ix - c_ix) * 100.0                     # log per cent against the twin
    t = int(np.argmin(dd))
    after_i = min(t + AFTER, days - 1)
    regain = (dd[after_i] - dd[t]) / (-dd[t]) if dd[t] < 0 else float("nan")
    ts = int(np.argmin(s_ix[SHOCK:])) + SHOCK
    end = min(ts + AFTER, days - 1)
    rise = 100.0 * (math.exp(s_ix[end] - s_ix[ts]) - 1.0)
    onset = next((d for d in range(SHOCK, days) if true[d] == "contraction"), None)
    exit_d = None
    if onset is not None:
        exit_d = next((d for d in range(onset + 1, days) if true[d] not in ("contraction", "trough")), None)
    out_in_time = onset is not None and exit_d is not None and exit_d - onset <= EXIT_WITHIN
    pub_exit = None
    if onset is not None:
        pub_exit = next((d for d in range(onset + 1, days) if pub[d] not in ("contraction", "trough")
                         and any(p == "contraction" for p in pub[onset:d])), None)
    return seed, {
        "twin_trough_log_pct": float(dd[t]), "twin_trough_day": t,
        "twin_after_log_pct": float(dd[after_i]), "regain": regain, "regain_truncated": t + AFTER > days - 1,
        "own_low_day": ts, "own_drawdown_pct": float(100.0 * (math.exp(s_ix[ts] - s_ix[:ts + 1].max()) - 1.0)),
        "own_rise_252_pct": rise, "rise_truncated": ts + AFTER > days - 1,
        "onset_day": onset, "exit_day": exit_d, "out_within_24m": bool(out_in_time),
        "published_exit_day": pub_exit, "phase_end": true[-1], "published_phase_end": pub[-1],
        "twin_path_log_pct": {str(d): float(dd[d]) for d in AT if d < days},
    }


def seeds_of(text):
    out = []
    for part in text.split(","):
        lo, _, hi = part.partition("-")
        out += list(range(int(lo), int(hi or lo) + 1))
    return out


def scenario_file(name):
    """The packaged file the engine loads, and its sha256."""
    import importlib.resources as res
    p = res.files("tradefloor") / "scenarios" / f"{name}.yml"
    try:
        raw = p.read_bytes()
    except (FileNotFoundError, OSError):
        return None, None
    return str(p), hashlib.sha256(raw).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out"); ap.add_argument("--arm", action="append", required=True)
    ap.add_argument("--seeds", default="301-330"); ap.add_argument("--days", type=int, default=900)
    ap.add_argument("--scenario", default="recession",
                    help="a packaged scenario's name (the graded file is the engine's own)")
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    import tradefloor as tf
    seeds = seeds_of(a.seeds)
    fpath, fsha = scenario_file(a.scenario)
    sc = tf.Scenario.load(a.scenario)
    out = {"kind": "ptv20-recession-rows", "seeds": a.seeds, "days": a.days, "scenario": a.scenario,
           "scenario_file": fpath, "scenario_sha256": fsha, "scenario_fingerprint": sc.fingerprint,
           "tradefloor_version": tf.version(), "roster": "Universe.random(40, seed=111), cap-weighted",
           "bands": {"S1a": S1A, "S1b": "all seeds", "S2": S2}, "arms": {}}
    print(f"{a.scenario}: {fpath} sha256 {fsha}", flush=True)
    specs = []
    for arm in a.arm:
        head, _, body = arm.partition(":")
        name, _, base = head.partition("@")
        base = base or name
        dials = {k: float(v) for k, v in (x.split("=") for x in body.split(",") if x)}
        specs.append((name, base, dials))
    with ProcessPoolExecutor(a.workers) as ex:
        futs = {(name, s): ex.submit(one, (base, dials, s, a.days, a.scenario))
                for name, base, dials in specs for s in seeds}
        for name, base, dials in specs:
            rows = dict(futs[(name, s)].result() for s in seeds)
            n = len(rows)
            reg = [r["regain"] for r in rows.values() if math.isfinite(r["regain"])]
            s1a = float(np.mean(reg)) if reg else float("nan")
            s1b = sum(r["out_within_24m"] for r in rows.values())
            s2 = float(np.mean([r["own_rise_252_pct"] for r in rows.values()]))
            v = {"S1a": bool(reg) and S1A[0] - 1e-12 <= s1a <= S1A[1] + 1e-12,
                 "S1b": s1b == n,
                 "S2": S2[0] - 1e-12 <= s2 <= S2[1] + 1e-12}
            ex_days = [r["exit_day"] - r["onset_day"] for r in rows.values() if r["exit_day"] is not None]
            out["arms"][name] = {
                "base": base, "dials": dials, "fingerprint": tf.ModelParams.from_preset(base, **dials).fingerprint,
                "n": n, "S1a_regain_mean": s1a, "S1a_regain_min": min(reg) if reg else None,
                "S1a_regain_max": max(reg) if reg else None, "S1a_seeds_with_a_fall": len(reg),
                "S1b_out_within_24m": s1b, "S1b_exit_sessions_max": max(ex_days) if ex_days else None,
                "S2_rise_mean_pct": s2,
                "S2_rise_min_pct": min(r["own_rise_252_pct"] for r in rows.values()),
                "S2_rise_max_pct": max(r["own_rise_252_pct"] for r in rows.values()),
                "truncated": {"regain": sum(r["regain_truncated"] for r in rows.values()),
                              "rise": sum(r["rise_truncated"] for r in rows.values())},
                "twin_trough_mean_log_pct": float(np.mean([r["twin_trough_log_pct"] for r in rows.values()])),
                "verdict": v, "pass": all(v.values()),
                "per_seed": {str(k): r for k, r in rows.items()}}
            print(f"{name:12s} S1a regain {100 * s1a:.0f}% {'pass' if v['S1a'] else 'FAIL'} | "
                  f"S1b out within 24m {s1b}/{n} {'pass' if v['S1b'] else 'FAIL'} | "
                  f"S2 rise {s2:+.1f}% {'pass' if v['S2'] else 'FAIL'}", flush=True)
    json.dump(out, open(a.out, "w"), indent=1, default=str)


if __name__ == "__main__":
    main()
