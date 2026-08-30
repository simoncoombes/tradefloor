"""Fear-gap card stage, basket side: crash co-movement and basket noise
for the screen-one finalists. The era's second motivation (round 120:
v16 crash co 0.518 vs real 0.781) is measured per candidate BEFORE
qualification, so a cell cannot qualify on fear statistics alone."""
import argparse, json, statistics, sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

CELLS = {
 "v16":      {},
 "w4d6m6":   {"vix_realised_vol_weight":0.4,"vix_decay_ratio":0.6,"vix_mean_reversion":0.06},
 "w6d6m12":  {"vix_realised_vol_weight":0.6,"vix_decay_ratio":0.6},
 "w4d4m12":  {"vix_realised_vol_weight":0.4,"vix_decay_ratio":0.4},
 "w6d10m12": {"vix_realised_vol_weight":0.6},
 "w6d4m12":  {"vix_realised_vol_weight":0.6,"vix_decay_ratio":0.4},
}
SEEDS = list(range(1, 31))


def one(job):
    label, seed, roster = job
    import gate_pick
    import tradefloor as pt
    m = pt.ModelParams.from_preset("pt-v16", **CELLS[label])
    r = gate_pick.driven_basket(m, seed=seed, roster_path=roster)
    r["label"], r["seed"] = label, seed
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--roster", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    jobs = [(l, s, a.roster) for l in CELLS for s in SEEDS]
    rows = []
    with ProcessPoolExecutor(max_workers=min(a.workers, 24)) as ex:
        futs = {ex.submit(one, j): j for j in jobs}
        for f in futs:
            try:
                rows.append(f.result())
            except Exception as e:
                print(f"FAILED {futs[f]}: {e}", flush=True)
    summary = {}
    for l in CELLS:
        pr = [r for r in rows if r["label"] == l]
        summary[l] = {k: statistics.median(r[k] for r in pr)
                      for k in ("basket_noise_ratio", "crash_comovement_sim",
                                "dispersion_trough_ratio", "noise_ratio_iqr")}
        summary[l]["n"] = len(pr)
    json.dump({"rows": rows, "summary": summary}, open(a.out, "w"), indent=1)
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
