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

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "python"))

import gate_pick  # noqa: E402
from pretium import envelope, facts  # noqa: E402

KINDS = ("p252", "p504", "vix45", "ho_seeds", "ho_universe")


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
        if kind == "vix45":
            out["vix45"] = {k: med[k] for k in (
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
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--out", default=None, help="write the verdicts as JSON")
    args = ap.parse_args()

    cands = load(args.candidates)
    train = gate_pick.TRAIN[:args.seeds]

    jobs = []
    for c in cands:
        label, base, ov = c["label"], c["base"], c["overrides"]
        m = gate_pick.model(base, ov)
        print(f"gate: {label}  = {base} + {ov}  fingerprint {m.fingerprint}",
              flush=True)
        jobs += [(label, base, ov, "p252", s) for s in train]
        jobs += [(label, base, ov, "p504", s) for s in train]
        jobs += [(label, base, ov, "vix45", s) for s in train]
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
        results[label] = {
            "base": c["base"], "overrides": c["overrides"],
            "fingerprint": gate_pick.model(c["base"], c["overrides"]).fingerprint,
            **verdict(acc[label]),
        }

    if args.out:
        Path(args.out).write_text(json.dumps(results, indent=2, default=float),
                                  encoding="utf-8")
        print(f"\nwrote {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
