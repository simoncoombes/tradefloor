"""Two questions, one instance (round 121):

1. Is the ho_seeds corr_persistence_acf1 miss real or a six-seed
   artifact? Measure it at 100 seeds on universe 111 and on the
   held-out 60-name universe 909, 252 days, pt-v16.
2. Why does pt-v16 undershoot the real crash co-movement (round 120)?
   Freeze-channel attribution on the basket instrument.
"""
import argparse, json, statistics, sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


def one(job):
    kind, arg, seed, roster = job
    import gate_pick
    import tradefloor as pt
    m = pt.ModelParams.from_preset("pt-v16")
    if kind == "cp":
        u = (pt.Universe.random(40, seed=111) if arg == "u111"
             else pt.Universe.random(60, seed=909))
        f = pt.facts.measure(seed=seed, universe=u, days=252, model=m)
        fd = f if isinstance(f, dict) else f.to_dict()
        return {"kind": kind, "arg": arg, "seed": seed,
                "corr_persistence_acf1": fd["corr_persistence_acf1"],
                "volume_abs_return_corr": fd["volume_abs_return_corr"]}
    r = gate_pick.driven_basket(m, seed=seed, roster_path=roster,
                                freeze=(arg,) if arg != "none" else ())
    return {"kind": kind, "arg": arg, "seed": seed, **r}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--roster", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    jobs = ([("cp", u, s, a.roster) for u in ("u111", "u909")
             for s in range(1, 101)]
            + [("basket", ch, s, a.roster)
               for ch in ("none", "vix", "credit", "policy")
               for s in range(1, 31)])
    rows = []
    with ProcessPoolExecutor(max_workers=a.workers) as ex:
        for r in ex.map(one, jobs, chunksize=4):
            rows.append(r)
            if len(rows) % 25 == 0:
                print(f"{len(rows)}/{len(jobs)}", flush=True)
    summary = {}
    for u in ("u111", "u909"):
        pr = [r for r in rows if r["kind"] == "cp" and r["arg"] == u]
        summary[f"cp_{u}"] = {
            "corr_persistence_median": statistics.median(
                r["corr_persistence_acf1"] for r in pr),
            "volume_abs_median": statistics.median(
                r["volume_abs_return_corr"] for r in pr),
            "n": len(pr)}
    for ch in ("none", "vix", "credit", "policy"):
        pr = [r for r in rows if r["kind"] == "basket" and r["arg"] == ch]
        summary[f"basket_{ch}"] = {
            k: statistics.median(r[k] for r in pr)
            for k in ("basket_noise_ratio", "crash_comovement_sim",
                      "dispersion_trough_ratio")}
    json.dump({"rows": rows, "summary": summary}, open(a.out, "w"), indent=1)
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
