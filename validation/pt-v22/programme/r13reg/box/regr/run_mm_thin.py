"""Market making on thin wide-spread names only (ADV < 500k shares), per-name P&L over seeds.
python run_mm_thin.py seeds days step"""
import os
import sys
from multiprocessing import Pool
import numpy as np
from probe_mm2 import run

def one(a):
    seed, days, step = a
    # quoting restricted to the thin names (ADV < 500k): each name's P&L depends only on its own book
    return [r for r in run(seed, days, step, max_adv=5e5) if r["adv"] < 5e5]

if __name__ == "__main__":
    seeds, days, step = int(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3])
    with Pool(int(os.environ.get("AUDIT_PROCS", "4"))) as p:
        p0 = int(os.environ.get("AUDIT_SEED_PROBE", "2401"))   # thirteenth registration's seed variable
        out = p.map(one, [(s, days, step) for s in range(p0, p0 + seeds)])
    tot = np.array([sum(r["total_pnl"] for r in rows) for rows in out])
    gross = np.array([sum(r["buys"] * 0 + abs(r["inventory_value"]) for r in rows) for rows in out])
    cap = np.array([np.nanmedian([r["captured_bp"] for r in rows]) for rows in out])
    print("per-seed total pnl on thin names:", np.round(tot).astype(int))
    print("mean", tot.mean(), "sd", tot.std(), "t", tot.mean() / tot.std() * np.sqrt(len(tot)), "positive", (tot > 0).sum(), "/", len(tot))
    print("median captured bp per seed", np.round(cap, 1))
    print("mean end |inventory| $", gross.mean())
