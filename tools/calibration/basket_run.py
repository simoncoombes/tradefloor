"""Measure presets on the multi-name driven instrument (driven_basket).

Usage: python tools/calibration/basket_run.py --presets pt-v12,pt-v14,pt-v15,pt-v16
         --seeds 30 --roster <covid-roster json> --out <json> [--workers 8]

REPORTED instrument (MULTINAME-DRIVEN.md): no bands, trajectory only.
"""
import argparse, json, statistics, sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


def one(job):
    preset, seed, roster = job
    import gate_pick
    import tradefloor as pt
    m = pt.ModelParams.from_preset(preset)
    r = gate_pick.driven_basket(m, seed=seed, roster_path=roster)
    r["preset"], r["seed"] = preset, seed
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--presets", required=True)
    ap.add_argument("--seeds", type=int, default=30)
    ap.add_argument("--roster", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    jobs = [(p, s, a.roster) for p in a.presets.split(",")
            for s in range(1, a.seeds + 1)]
    rows = []
    with ProcessPoolExecutor(max_workers=a.workers) as ex:
        for r in ex.map(one, jobs):
            rows.append(r)
            print(f"{r['preset']} seed {r['seed']} basket {r['basket_noise_ratio']:.4f}",
                  flush=True)
    summary = {}
    for p in a.presets.split(","):
        pr = [r for r in rows if r["preset"] == p]
        summary[p] = {k: statistics.median(r[k] for r in pr)
                      for k in pr[0] if k not in ("preset", "seed")}
    json.dump({"rows": rows, "summary": summary}, open(a.out, "w"), indent=1)
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
