"""openings.py ARM_FILE LO HI WORKERS OUT.json: the grader's opening() (grade_xsec.py) on suite openings seed s, roster
s + 1000, for s in LO..HI, with the opening's VIX, phase and the roster's largest cap weight (addendum T-2)."""
import sys, json
import numpy as np
from multiprocessing import Pool
import grade_xsec as G
arm = open(sys.argv[1]).read().strip(); name, _, b = arm.partition("@"); base, _, ds = b.partition(":")
dials = {k: float(v) for k, v in (x.split("=") for x in ds.split(","))}
def one(s):
    import tradefloor as tf
    r = G.opening((base, name, dials, s, s + 1000))
    u = tf.Universe.random(20, seed=s + 1000)
    e = tf.Engine(seed=s, universe=u, model=tf.ModelParams.from_preset(base, **dials))
    ec = e.state_snapshot()["economy"]
    caps = np.array([i.initial_price * i.shares_outstanding for i in u])
    return {"seed": s, "first60": r["b9_first60"], "steady60": r["b9_steady60"], "vix0": ec["vix"],
            "phase0": ec["cycle_phase"], "wmax": float(caps.max() / caps.sum())}
if __name__ == "__main__":
    lo, hi = int(sys.argv[2]), int(sys.argv[3])
    with Pool(int(sys.argv[4])) as p:
        R = p.map(one, range(lo, hi + 1), chunksize=4)
    json.dump(R, open(sys.argv[5], "w"))
    f = np.array([r["first60"] for r in R]); st = np.concatenate([r["steady60"] for r in R])
    print(name, len(R), "first60 below -0.4: %d (%.4f); year-2 windows below -0.4: %d of %d (%.4f)" % ((f < -0.4).sum(), (f < -0.4).mean(), (st < -0.4).sum(), len(st), (st < -0.4).mean()))
