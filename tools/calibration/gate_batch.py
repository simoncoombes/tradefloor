"""The thirty-seed gate for MANY candidates in one flat pool.

`gate_pick.py` gates one candidate. That was the right shape while a
candidate arrived every few days; it is the wrong shape now that a survey
hands over a frontier of twenty and every one of them needs thirty seeds
before it means anything.

Two things make the difference, and neither is more hardware:

  * **One flat pool across all candidates.** Running `gate_pick` N times
    serialises N pools, and each pool's tail wastes cores while the last few
    seeds finish. One pool over `N x axes x seeds` jobs keeps every core busy
    to the end, which matters most exactly when N is large.
  * **The work is embarrassingly parallel and the machine is not.** On a
    laptop this is memory-bound long before it is core-bound: a 504-day
    40-name panel is about 1.6 GB resident, so six workers is the practical
    ceiling and the gate takes 20 minutes. The same work is about 20
    core-minutes, which a 96-core box finishes in one.

Usage:

    python tools/calibration/gate_batch.py --candidates cands.json \
        --seeds 30 --workers 94 --out gates.json

`cands.json` is a list of objects, each `{"label", "base", "overrides"}`:

    [{"label": "ptv11+sector0.8", "base": "pt-v11",
      "overrides": {"sector_vix_coupling": 0.8}}]

Every candidate is gated on the same axes `gate_pick` uses, and the printed
block per candidate is `gate_pick`'s own `summarise`, so the two tools cannot
drift into disagreeing about what a gate says. The response instrument is
NOT run here: it opens its own pool per vector, which would defeat the flat
pool. Run it on the one or two candidates that survive.
"""
from __future__ import annotations

import argparse
import json
import statistics as st
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

# The source tree is APPENDED, never inserted ahead of site-packages.
# Inserting it shadows an installed pretium wheel with a source copy that
# has no compiled `_core`, which is invisible locally (the dev venv is an
# editable install pointing at these same files, extension included) and
# fatal on a fresh box, where the wheel is a real install. That is exactly
# how the first AWS gate batch died: "cannot import name '_core' from
# partially initialized module 'pretium'". atlas_survey.py never had the
# bug because it only ever added its own directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.append(str(Path(__file__).resolve().parent.parent.parent / "python"))

import gate_pick  # noqa: E402
from pretium import envelope, facts  # noqa: E402

#: The real range for crisis co-movement, from the `scenario-magnitude` gap's
#: own text: "crisis co-movement reads 0.696 against a real 0.664 to 0.727".
#: Quoted from there rather than re-derived, because the gap is where the
#: measurement is documented and a second copy is a second thing to drift.
CRISIS_COMOVEMENT_REAL = (0.664, 0.727)

#: Real markets' crisis volatility lever, from `real_vix_lever.py`: 17.2%
#: annualised at VIX under 12 against 106.1% at VIX 45 and above.
CRISIS_LEVER_REAL = 6.16

#: How far from `CRISIS_LEVER_REAL` a candidate may sit before the gate says
#: so. Five percent is chosen, not derived: the `scenario-magnitude` gap
#: calls pt-v12's 6.04 "within two percent of real" and treats that as the
#: headline, so a candidate at eight percent has moved a published claim.
#:
#: This row exists because the gate has now hidden the same class of defect
#: TWICE. Crisis co-movement was printed and compared against nothing until
#: round 9, by which time the leading candidate had been outside its real
#: range on three seed blocks of five. The lever was printed and compared
#: against nothing until round 23, by which time p970 had been reported as
#: regressing nothing while costing 0.28 of lever on all five blocks. The
#: lesson generalises past these two: a number with a published real value
#: that nothing compares against is a defect waiting for a preset.
CRISIS_LEVER_TOLERANCE = 0.05

KINDS = ("p252", "p504", "vix5", "vix45", "vix65", "driven",
         "ho_seeds", "ho_universe")


def one(job):
    """Module level and picklable: macOS spawns rather than forks."""
    label, base, overrides, kind, seed = job
    kind, row = gate_pick.one((base, overrides, kind, seed))
    return label, kind, row


def load(path: str) -> list[dict]:
    cands = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(cands, list) or not cands:
        sys.exit(f"{path}: expected a non-empty list of candidates")
    seen = set()
    for c in cands:
        for key in ("label", "base", "overrides"):
            if key not in c:
                sys.exit(f"{path}: a candidate is missing {key!r}: {c}")
        if c["label"] in seen:
            # Two rows under one label silently merge into nonsense medians.
            sys.exit(f"{path}: duplicate label {c['label']!r}")
        seen.add(c["label"])
    return cands


def verdict(rows_by_kind: dict[str, list]) -> dict:
    """The numbers a ranking needs, beside the human-readable block."""
    out = {}
    for kind in KINDS:
        rows = rows_by_kind.get(kind)
        if not rows:
            continue
        med = {k: st.median([r[k] for r in rows if r.get(k) is not None])
               for k in rows[0]}
        if kind == "driven":
            out["driven"] = med
            continue
        if kind in ("vix5", "vix45", "vix65"):
            out[kind] = {k: med[k] for k in (
                "sector_excess_corr", "cross_sectional_corr",
                "excess_kurtosis", "annualised_vol_pct")}
            continue
        days = 504 if kind == "p504" else 252
        sc = envelope.score(med, horizon_days=days)
        out[kind] = {
            "in_band": sc["in_band"], "of": sc["of"],
            "out": [k for k, v in sc["statistics"].items()
                    if not v.get("in_band", True)],
            "median": med,
        }
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", required=True)
    ap.add_argument("--seeds", type=int, default=30)
    ap.add_argument(
        "--seed-start", type=int, default=None,
        help="first seed of a DISJOINT confirmation block, e.g. 201. Without "
             "it the run uses the calibration seeds in gate_pick.TRAIN, which "
             "is discovery rather than validation: a candidate found on those "
             "seeds and re-measured on them reproduces its own fluctuation "
             "exactly. Principle 6.")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--out", default=None, help="write the verdicts as JSON")
    args = ap.parse_args()

    cands = load(args.candidates)
    if args.seed_start is None:
        train = gate_pick.TRAIN[:args.seeds]
    else:
        train = tuple(range(args.seed_start, args.seed_start + args.seeds))
        overlap = set(train) & set(gate_pick.TRAIN)
        if overlap:
            sys.exit(f"--seed-start {args.seed_start} overlaps the calibration "
                     f"seeds on {sorted(overlap)[:5]}; a confirmation block "
                     "that shares seeds with discovery confirms nothing")
    print(f"seeds {train[0]}-{train[-1]} "
          f"({'calibration' if args.seed_start is None else 'DISJOINT'})", flush=True)

    jobs = []
    for c in cands:
        label, base, ov = c["label"], c["base"], c["overrides"]
        m = gate_pick.model(base, ov)
        print(f"gate: {label}  = {base} + {ov}  fingerprint {m.fingerprint}",
              flush=True)
        jobs += [(label, base, ov, "p252", s) for s in train]
        jobs += [(label, base, ov, "p504", s) for s in train]
        jobs += [(label, base, ov, "vix5", s) for s in train]
        jobs += [(label, base, ov, "vix45", s) for s in train]
        jobs += [(label, base, ov, "vix65", s) for s in train]
        # Six seeds, not thirty: the driven window is one FIXED macro path,
        # so seeds vary only the noise around it, and each run is 505
        # sessions over forty names. Six is enough to median a ratio.
        jobs += [(label, base, ov, "driven", s) for s in train[:6]]
        jobs += [(label, base, ov, "ho_seeds", s) for s in gate_pick.HELDOUT]
        jobs += [(label, base, ov, "ho_universe", s) for s in gate_pick.HELDOUT]

    print(f"\n{len(cands)} candidates, {len(jobs)} tasks, {args.workers} workers",
          flush=True)

    acc: dict[str, dict[str, list]] = {c["label"]: {} for c in cands}
    done = 0
    with ProcessPoolExecutor(args.workers) as ex:
        for label, kind, row in ex.map(one, jobs):
            acc[label].setdefault(kind, []).append(row)
            done += 1
            if done % 250 == 0:
                print(f"  ... {done}/{len(jobs)}", flush=True)

    results = {}
    for c in cands:
        label = c["label"]
        print(f"\n=== {label} ===", flush=True)
        for kind in KINDS:
            if acc[label].get(kind):
                print(gate_pick.summarise(kind, acc[label][kind]), flush=True)
        v = verdict(acc[label])
        # Crisis co-movement is SCORED here, not merely printed. It carries a
        # stated real range in the scenario-magnitude gap, 0.664 to 0.727, it
        # is the statistic that rejected four gate batches of jump work, and
        # it is in none of LIVE_TARGETS, CONSTRAINTS or STRUCTURAL -- so the
        # fourteen-statistic panel cannot see it. Nine rounds of this
        # programme ran before anyone checked it against its own range, and
        # the leading candidate had been outside it on three seed blocks of
        # five the whole time. A number with a published range that nothing
        # compares against is how this project has lost an axis before.
        if "vix45" in v and v["vix45"].get("cross_sectional_corr") is not None:
            cm = v["vix45"]["cross_sectional_corr"]
            lo, hi = CRISIS_COMOVEMENT_REAL
            ok = lo <= cm <= hi
            v["crisis_comovement"] = cm
            v["crisis_comovement_in_range"] = ok
            print(f"  crisis co-movement  {cm:.3f}   "
                  f"(real {lo} to {hi})  {'ok' if ok else 'OUT OF RANGE'}",
                  flush=True)
        if "vix5" in v and "vix65" in v:
            lever = (v["vix65"]["annualised_vol_pct"]
                     / v["vix5"]["annualised_vol_pct"])
            v["vol_lever"] = lever
            err = abs(lever - CRISIS_LEVER_REAL) / CRISIS_LEVER_REAL
            v["vol_lever_error"] = err
            v["vol_lever_in_tolerance"] = err <= CRISIS_LEVER_TOLERANCE
            print(f"  crisis lever  vol(VIX 65)/vol(VIX 5) = {lever:.3f}"
                  f"   (real {CRISIS_LEVER_REAL})  {err:+.1%}  "
                  f"{'ok' if err <= CRISIS_LEVER_TOLERANCE else 'OUT OF TOLERANCE'}",
                  flush=True)
        results[label] = {
            "base": c["base"], "overrides": c["overrides"],
            "fingerprint": gate_pick.model(c["base"], c["overrides"]).fingerprint,
            **v,
        }

    if args.out:
        Path(args.out).write_text(json.dumps(results, indent=2, default=float),
                                  encoding="utf-8")
        print(f"\nwrote {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
