"""lever_lr.py BOX ARM...: the audit's seven levered macro rules on the long run's free histories, against holding
and against the exposure-matched position, gated as C10c is.

The rules are the ones grade_all.py prints as `lever:` (deps/eval_arms.lever_flags): 2x while the flag is on, 1x
otherwise, the position of session d set by the flag at d-1, the part above 1x financed at the published policy
rate of close d-1, 5 bp a switch, years 2-21 (the sessions deps/lib.lever_excess grades). The owner's decision of
2026-09-28 (5) grades a levered rule against the exposure-matched position "as C10c is": a constant position at
the rule's own mean exposure over the graded sessions, rebalanced daily, financed the same way, no switching cost.

Both sides are compounded daily, as C10c's mirror (deps/eval_arms.mirror) and every audit edge script
(gate.em_excess) compound them:

    rule_d  = log(1 + pos_d R_d + (1 - pos_d) f_d) - cost |pos_d - pos_{d-1}|,   R_d = exp(r_d) - 1
    bench_d = log(1 + pbar R_d + (1 - pbar) f_d),                                pbar = mean pos_d
    excess over holding               = (sum rule_d - sum r_d) / years
    excess over the exposure-matched  = (sum rule_d - sum bench_d) / years          (pts/yr, x100)

lib.lever_excess (the `lever:` line) sums pos_d r_d in log returns instead, which leaves out the volatility drag of
the 2x sessions: it overstates a rule that levers in volatile phases. Those log-form readings are carried in the
RULEJSON line (log_hold_*, log_em_*) and printed, reported and not graded.

The gate is C10c's bound: a rule BREACHES when its median over the histories is above +1.0 pts/yr OR it is ahead in
more than 2/3 of them (gate.py, bound "C10c"). One RULEJSON line per rule for exploits_summary.py, then a table.
exploits.py writes the output to OUTDIR/ARM/lever_lr.txt."""
import glob
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "audit085"))
sys.path.insert(0, os.path.join(HERE, "deps"))
import gate  # noqa: E402
from lib import BURN, COST  # noqa: E402

RULES = ["2x while pub peak|con", "2x while pub con|tro", "2x 126 after pub peak", "2x 252 after pub con",
         "2x 252 after first cut", "2x 63 after any cut", "2x while pub GDP < 1"]


def positions(n, flag, ffr, lev=2.0, base=1.0):
    """lib.lever_excess's position and financing over n sessions: pos_d = lev when flag[d-1] else base,
    financed at ffr[d-1] / 252."""
    flag = np.asarray(flag, bool); ffr = np.asarray(ffr, float)
    pos = np.full(n, base)
    f = np.zeros(n, dtype=bool); f[1:] = flag[:n - 1]
    pos[f] = lev
    fin = np.zeros(n); fin[1:] = ffr[:n - 1] / 252.0
    return pos, fin


def lever_hold_em(level, flag, ffr, lev=2.0, base=1.0, cost=COST, burn=BURN):
    """One history -> dict of pts/yr readings, compounded daily (graded): hold, em; in log form (reported):
    log_hold (= lib.lever_excess), log_em; and the rule's mean position over the graded sessions, pbar."""
    r = np.diff(np.log(np.asarray(level, float)))
    pos, fin = positions(len(r), flag, ffr, lev, base)
    pos, rr, fin = pos[burn:], r[burn:], fin[burn:]
    yrs = len(rr) / 252.0
    sw = np.abs(np.diff(np.r_[pos[:1], pos]))                 # a switch inside the graded sessions costs `cost`
    R = np.expm1(rr)
    rule = np.log1p(pos * R + (1 - pos) * fin) - cost * sw
    pbar = float(pos.mean())
    log_rule = (rr * pos).sum() - ((pos - base).clip(0) * fin).sum() + ((base - pos).clip(0) * fin).sum() - cost * sw.sum()
    log_bench = pbar * rr.sum() - (pbar - base) * fin.sum()
    return dict(hold=100.0 * (rule.sum() - rr.sum()) / yrs,
                em=100.0 * gate.em_excess(pos, R, fin, rule) / yrs,
                log_hold=100.0 * (log_rule - base * rr.sum()) / yrs,
                log_em=100.0 * (log_rule - log_bench) / yrs,
                pbar=pbar)


def load_lr(box, arm):
    """The long run's free histories with the published phase, as screen_eval.load_lr reads them."""
    H = []
    for f in sorted(glob.glob(f"{box}/longrun/{arm}/*-free.npz")):
        z = dict(np.load(f)); z["pub"] = z["cyc"].astype(int)
        z["seed"] = int(os.path.basename(f).split("-")[0]); H.append(z)
    return H


def readings(H):
    """{rule: {reading: array over histories}} for H."""
    import eval_arms as E
    keys = ("hold", "em", "log_hold", "log_em", "pbar")
    out = {k: {x: [] for x in keys} for k in RULES}
    for z in H:
        for k, fl in E.lever_flags(z, z["pub"]).items():
            d = lever_hold_em(z["level"], fl, z["m_federal_funds_rate"])
            for x in keys:
                out[k][x].append(d[x])
    return {k: {x: np.array(v) for x, v in d.items()} for k, d in out.items()}


def grade(H, script="lever_lr"):
    """[(rule, RULEJSON record, breach)] for histories H; prints the RULEJSON lines."""
    R = readings(H)
    rows = []
    for k in RULES:
        d = R[k]
        lh, la, _ = gate.summary(d["log_hold"]); lm, lma, _ = gate.summary(d["log_em"])
        rec = gate.emit(script, k, True, d["hold"], d["em"], bound="C10c",
                        mean_exposure=float(np.mean(d["pbar"])) if len(d["pbar"]) else float("nan"),
                        em_ahead_count=int(np.sum(d["em"] > 0)),
                        seeds=",".join(str(z["seed"]) for z in H[:1]) + (f"..{H[-1]['seed']}" if len(H) > 1 else ""),
                        log_hold_median=lh, log_hold_ahead=la, log_em_median=lm, log_em_ahead=lma)
        rows.append((k, rec, gate.verdict(rec)))
    return rows


def main():
    box = sys.argv[1]
    for arm in sys.argv[2:]:
        H = load_lr(box, arm)
        if not H:
            raise SystemExit(f"lever_lr: no long-run free histories for {arm} under {box}/longrun")
        rows = grade(H)
        print(f"{arm}: the long run's levered macro rules, n {len(H)} histories, compounded daily, "
              f"C10c's bound on the exposure-matched reading (median <= +1.0 and ahead <= 2/3)")
        for k, rec, bad in rows:
            print(f"  {k:24s} exposure {rec['mean_exposure']:.3f} | vs hold median {rec['hold_median']:+.2f} ahead {rec['hold_ahead']:.2f}"
                  f" | exposure-matched median {rec['em_median']:+.2f} ahead {rec['em_ahead']:.3f} ({rec['em_ahead_count']}/{rec['n']})"
                  f" {'BREACH' if bad else 'pass'}"
                  f" | log form (reported): hold {rec['log_hold_median']:+.2f}/{rec['log_hold_ahead']:.2f},"
                  f" em {rec['log_em_median']:+.2f}/{rec['log_em_ahead']:.2f}")
        print(f"  breaches {sum(b for _, _, b in rows)} of {len(rows)}")


if __name__ == "__main__":
    main()
