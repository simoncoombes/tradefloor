"""cert_reg18.py SUPPBOX --engine ENGINE_WORKTREE --gen-seeds LIST --d1pool-seeds LIST [--commit SHA] [--out FILE]

The eighteenth registration's readings of R4 and D1 for the certification box's arms, from the supplementary box
(ptv21c1s, cert_reg18_box.py), written for criteria.py --definitions reg18 --reg18 FILE.

  R4 held    bt:R4 held (r13reg/deps/screen_eval.bt_stats, as grade_all.py reads it): the mean over the true-phase
             histories SUPPBOX/r13gen/ARM of each history's daily correlation of the index with minus the IG
             yield's change at the held close
  level      index_tail_dn3_pct (100 x the sum of hits over the sum of sessions) and index_drift_pct (the mean over
             seeds) from SUPPBOX/d1pool/ARM.json, pooled by the engine's facts.aggregate_panels with the standard
             errors certgrade_box_hr.py gives them (owner decisions 11 and 12)

Refuses unless each arm's seed sets are exactly the launched lists (seedplan.py refuses these, the twelfth
protocol's, seeds always), every history and every pooled panel ran on the box's engine commit under the arm's own
preset by name (fingerprint equal to the arm's name), and there are at least 360 pooled seeds.
"""
import argparse
import glob
import json
import os
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
PROG = HERE.parent.parent
sys.path[:0] = [str(PROG / "r13reg" / "box"), str(PROG / "r13reg" / "deps")]
import seedplan  # noqa: E402

POOL_MIN = 360
POOL_ROWS = ("index_tail_dn3_pct", "index_drift_pct")


def seed_of(f):
    return int(re.findall(r"(\d+)", os.path.basename(f))[0])


def pooled_se(row, panels):
    import statistics as st
    n = len(panels)
    if n < 2:
        return None
    if row == "index_tail_dn3_pct":
        h = [p["index_tail_dn3_hits"] for p in panels]
        return 100.0 * st.stdev(h) / n ** 0.5 / st.fmean(p["index_tail_dn3_sessions"] for p in panels)
    v = [p[row] for p in panels if p.get(row) is not None]
    return st.stdev(v) / len(v) ** 0.5 if len(v) > 1 else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("box")
    ap.add_argument("--engine", required=True)
    ap.add_argument("--gen-seeds", required=True)
    ap.add_argument("--d1pool-seeds", required=True)
    ap.add_argument("--commit", help="the engine commit the certification box ran (ptv21c1's commit.txt)")
    ap.add_argument("--out")
    a = ap.parse_args()
    eng = pathlib.Path(a.engine)
    if any((eng / "python" / "tradefloor").glob("_core*.so")):
        sys.path.insert(0, str(eng / "python"))
    import numpy as np
    import screen_eval as SE
    from tradefloor import facts
    box = pathlib.Path(a.box)
    commit = (box / "commit.txt").read_text().strip()
    if a.commit and commit != a.commit:
        sys.exit(f"REFUSED: the supplement ran engine {commit}, the certification box {a.commit}")
    gen_want, pool_want = seedplan.parse(a.gen_seeds), seedplan.parse(a.d1pool_seeds)
    out = {"kind": "cert-reg18", "box": str(box), "engine_commit": commit,
           "gen_seeds": a.gen_seeds, "d1pool_seeds": a.d1pool_seeds, "arms": {}}
    arms = sorted(os.path.basename(p) for p in glob.glob(f"{box}/r13gen/*") if os.path.isdir(p))
    for arm in arms:
        fs = sorted(glob.glob(f"{box}/r13gen/{arm}/*-free.npz"), key=seed_of)
        seeds = [seed_of(f) for f in fs]
        if seeds != gen_want:
            sys.exit(f"REFUSED: {arm}'s true-phase seeds {seedplan.fmt(seeds)} are not the launched {a.gen_seeds}")
        for s in seeds:
            n = np.load(f"{box}/r13gen/{arm}/names-{s}.npz")
            if str(n["preset"]) != arm or str(n["dials"]) != "[]":
                sys.exit(f"REFUSED: {arm} seed {s} ran preset {n['preset']} with dials {n['dials']}, not {arm} by name")
        H = SE.load_gen(str(box), arm)
        bt = SE.bt_stats(H)
        out["arms"][arm] = {"R4_held": {"value": bt["R4 held"], "n": len(H), "seeds": seedplan.fmt(seeds),
                                        "R4_pre": bt.get("R4 pre"), "R4m": bt.get("R4m")}}
        f = box / "d1pool" / f"{arm}.json"
        if not f.exists():
            sys.exit(f"REFUSED: no pooled record {f}")
        rec = json.load(open(f))
        panels = rec.get("panels") or []
        ps = [int(p["seed"]) for p in panels]
        if ps != pool_want or len(set(ps)) != len(ps):
            sys.exit(f"REFUSED: {arm}'s pooled seeds {seedplan.fmt(ps)} are not the launched {a.d1pool_seeds}")
        if len(ps) < POOL_MIN:
            sys.exit(f"REFUSED: {arm} has {len(ps)} pooled seeds, fewer than {POOL_MIN}")
        fps = {p.get("model_fingerprint") for p in panels} | {rec.get("fingerprint")}
        if fps != {arm}:
            sys.exit(f"REFUSED: {arm}'s pooled fingerprints {sorted(map(str, fps))} are not {arm}")
        agg = facts.aggregate_panels(panels, POOL_ROWS)
        out["arms"][arm]["level"] = {r: {"value": agg.get(r), "se": pooled_se(r, panels), "n": len(ps)}
                                     for r in POOL_ROWS}
        print(f"{arm}: R4 held {bt['R4 held']:+.4f} over {len(H)} histories; "
              + "; ".join(f"{r} {agg.get(r):.4f} over {len(ps)} seeds" for r in POOL_ROWS))
    if not arms:
        sys.exit(f"REFUSED: no true-phase histories under {box}/r13gen")
    if a.out:
        json.dump(out, open(a.out, "w"), indent=1, default=float)


if __name__ == "__main__":
    main()
