"""rulecut_lr.py BOX ARM...: leaks/rule_cut's rule (2x for 126 sessions after a first cut, i.e. a cut after a hike, financed at
the policy rate, 10 bp a switch) on the long run's 90 free histories (index level, published policy rate, years 2-21),
against holding and against the exposure-matched constant position. The owner's decision of 2026-09-28 (5) grades
rule_cut here, against the exposure-matched position (gate.py)."""
import os, sys, glob, numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "audit085"))
import gate  # noqa: E402
box = sys.argv[1]
for arm in sys.argv[2:]:
    ex, em = [], []
    for f in sorted(glob.glob(f"{box}/longrun/{arm}/*-free.npz")):
        z = np.load(f); L = np.log(z["level"].astype(float))[252:]; fr = z["m_federal_funds_rate"][252:]
        T = len(L); r = np.diff(L); extra = np.zeros(T); last = 0
        for d in range(1, T):
            ch = fr[d] - fr[d - 1]
            if abs(ch) > 1e-9:
                dirn = 1 if ch > 0 else -1
                if dirn < 0 and last > 0: extra[d + 1:d + 127] = 1.0   # act from the next session's close
                last = dirn
        pos = extra[:-1]; sw = np.abs(np.diff(np.concatenate([[0.0], pos])))
        rf = fr[:-1] / 252
        g = (pos * r).sum() - 0.001 * sw.sum(); fin = (pos * rf).sum()
        pb = pos.mean(); yrs = T / 252
        ex.append(100 * (g - fin) / yrs); em.append(100 * ((g - fin) - pb * (r.sum() - rf.sum())) / yrs)
    ex, em = np.array(ex), np.array(em)
    gate.emit("rule_cut_lr", f"2x 126 after a first cut, financed, {len(ex)} long-run histories", True, ex, em)
    print(f"{arm:8s} n {len(ex)}: vs hold median {np.median(ex):+.2f} ahead {np.mean(ex>0):.2f} | exposure-matched median {np.median(em):+.2f} mean {em.mean():+.2f} ahead {np.mean(em>0):.2f}")
