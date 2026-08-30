"""Flow composition, screen one (round 139): the forced-flow ladder on
the crash-cohesion instrument. Pre-registered judgment (round 138):
crash-co median toward the real 0.781 AND the seed-IQR collapsing
(v16 reads a wide 0.22-0.73); basket noise ratio and dispersion-trough
not degraded."""
import argparse, json, statistics, sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

CELLS = {"v16": {}}
for g in (0.00025, 0.0005, 0.001):
    for th in (35.0, 40.0, 45.0):
        CELLS[f"g{int(g*100000)}t{int(th)}"] = {
            "forced_flow_gain": g, "forced_flow_threshold": th}
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
        pr = sorted(r["crash_comovement_sim"] for r in rows if r["label"] == l)
        if not pr:
            continue
        n = len(pr)
        summary[l] = {
            "crash_co_median": statistics.median(pr),
            "crash_co_iqr": pr[(3 * n) // 4] - pr[n // 4],
            "crash_co_min": pr[0],
            "basket_noise": statistics.median(
                r["basket_noise_ratio"] for r in rows if r["label"] == l),
            "disp_trough": statistics.median(
                r["dispersion_trough_ratio"] for r in rows if r["label"] == l),
            "n": n}
    json.dump({"rows": rows, "summary": summary}, open(a.out, "w"), indent=1)
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
