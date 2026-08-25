"""Verify the re-sited economy crisis gates fire endogenously, and how hard.

Companion to `measure_vix_distribution.py`, run against a wheel built
with `CRISIS_VIX_THRESHOLD` (rust/src/economy/state.rs; the threshold is
repeated here as a default because the Python surface does not export
it). Six seeds by ten years on the published-method universe, macro
chain endogenous. For every day it records (vix, gdp_growth, gold_price,
usd_index, cycle_phase) from `state_snapshot`, then reports:

  - how many days cross the threshold, per seed and pooled;
  - how many of those days also satisfy the gold gate's `gdp < -1`
    condition (measured: all of them — endogenous VIX only exceeds the
    threshold in deep contraction or trough, so the two gates engage
    together);
  - the magnitude each gate contributes on its firing days, computed
    from the recorded state with the same formulas as `daily.rs`;
  - the VIX distribution of the run, so feedback drift from the newly
    live gates (USD -> inflation -> VIX) can be compared against the
    pre-change measurement.

Usage:
    .venv/bin/python tools/calibration/verify_crisis_gates.py --out results.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys


def fingerprint() -> str:
    sys.path.insert(0, __file__.rsplit("/", 1)[0])
    from measure_panel import trajectory_fingerprint

    return trajectory_fingerprint()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--seeds", default="1,2,3,4,5,6")
    parser.add_argument("--days", type=int, default=2520)
    parser.add_argument("--threshold", type=float, default=25.5)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    import pretium

    seeds = [int(s) for s in args.seeds.split(",")]
    threshold = args.threshold
    universe = pretium.Universe.random(40, seed=111)

    daily: dict[str, list[tuple[float, float, float, float, str]]] = {}
    for seed in seeds:
        engine = pretium.Engine(seed=seed, universe=universe)
        rows = []
        for _ in range(args.days):
            engine.open_market()
            engine.run_session(9, 30, 3, 390)
            engine.close_market()
            econ = engine.state_snapshot()["economy"]
            rows.append((econ["vix"], econ["gdp_growth"], econ["gold_price"],
                         econ["usd_index"], econ["cycle_phase"]))
        daily[str(seed)] = rows
        print(f"seed {seed} done", file=sys.stderr, flush=True)

    pooled = [row for rows in daily.values() for row in rows]
    crossing = [row for row in pooled if row[0] > threshold]
    gold_gate = [row for row in crossing if row[1] < -1.0]
    summary = {
        "days": len(pooled),
        "crossing_days": len(crossing),
        "crossing_fraction": len(crossing) / len(pooled),
        "crossing_days_per_seed": {
            seed: sum(1 for row in rows if row[0] > threshold)
            for seed, rows in daily.items()
        },
        "gold_gate_days": len(gold_gate),
        "usd_drift_index_pts_per_day": {
            "mean": statistics.mean((row[0] - threshold) * 0.05 for row in crossing),
            "max": max((row[0] - threshold) * 0.05 for row in crossing),
        } if crossing else None,
        "gold_premium_usd_per_day": {
            "mean": statistics.mean(
                min(5.0, abs(row[1]) + (row[0] - threshold) * 0.15) for row in gold_gate
            ),
            "max": max(
                min(5.0, abs(row[1]) + (row[0] - threshold) * 0.15) for row in gold_gate
            ),
        } if gold_gate else None,
    }
    print(json.dumps(summary, indent=1), file=sys.stderr)

    result = {
        "method": {
            "universe": "Universe.random(40, seed=111)",
            "seeds": seeds,
            "days": args.days,
            "threshold": threshold,
            "macro": "endogenous (no pins, no shocks)",
            "row": "(vix, gdp_growth, gold_price, usd_index, cycle_phase) per day",
        },
        "trajectory_fingerprint": fingerprint(),
        "summary": summary,
        "daily": daily,
    }
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(result, handle)
        handle.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
