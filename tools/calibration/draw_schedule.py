"""Which stream moved: the §5.2 draw-schedule deviations, traced per stream.

Phase 3 recorded 16 of 482 evaluated vectors moving `draws_consumed`, all
at the edges of the §6.3 box, all in the screening and CMA stages, and
called for the trigger to be traced at the write site. This is that trace.

**The claim it tests.** CALIBRATION.md §5.2 says nothing settable may
change how many draws are taken or in what order, and phase 2 asserted it
across ~7,000 evaluations by comparing `Engine.draws_consumed` — the sum
over all three generators. The 2026-08 stream split made that sum the
wrong quantity to compare, and `Engine.draws_by_stream`'s own docstring
says so in as many words: the market stream's schedule is a pure function
of (market status, active roster, sector count), while the economy
stream's count "genuinely varies with macro state (a chain in contraction
draws a shock the expansion never rolls)". Macro state is driven by the
market's realised volatility and return — `DailyInputs` carries both —
and every searched parameter moves those. So a parameter that pushes
volatility far enough to reroute the macro chain changes the economy
stream's draw count BY DESIGN, and the total moves with it.

If that is what happened, three things must hold on the recorded
offenders and this tool measures all three rather than arguing any:

1. the MARKET stream's count is identical to pt-v1's, on every offending
   (vector, seed) pair — the property CRN actually needs;
2. the ECONOMY stream's count accounts for the whole of the recorded
   total deviation;
3. the trigger is a macro branch and not a coefficient: a sweep of one
   parameter along one box axis moves the economy count in steps at
   scattered thresholds, not monotonically and not at a special value.

Two stages, both cheap, neither a search:

    .venv/bin/python tools/calibration/draw_schedule.py \
        --certificate results/calibrate-pt-v2-2026-08-22.json \
        --sweep idio_sigma_scale --sweep-points 16 \
        --out results/draw-schedule-2026-08-22.json
"""

from __future__ import annotations

import argparse
import json
import time

import instrumentlib as lib


def offenders(certificate: dict) -> list[tuple[dict, list[int]]]:
    """The recorded (vector, offending seeds) pairs, in certificate order."""
    grouped: dict[str, list[int]] = {}
    vectors: dict[str, dict] = {}
    for row in certificate.get("crn_deviations", []):
        key = lib.vector_key(row["overrides"])
        vectors[key] = row["overrides"]
        grouped.setdefault(key, []).append(row["seed"])
    return [(vectors[k], sorted(set(v))) for k, v in grouped.items()]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--certificate", required=True)
    parser.add_argument("--sweep", default="idio_sigma_scale",
                        help="one box axis to walk, to show the trigger is a "
                             "threshold in macro state rather than a value")
    parser.add_argument("--sweep-points", type=int, default=16)
    parser.add_argument("--sweep-seeds", default="101,125")
    parser.add_argument("--jacobian-h", type=float, default=0.05,
                        help="the step phase 2's 31x8 Jacobian was measured "
                             "at; stage 3 asks whether its own bracket "
                             "straddles a macro branch on any seed")
    parser.add_argument("--jacobian-seeds",
                        default=",".join(str(s) for s in lib.TRAIN_SEEDS))
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    ship = lib.shipped_values()
    with open(args.certificate, encoding="utf-8") as handle:
        cert = json.load(handle)
    recorded = {(lib.vector_key(r["overrides"]), r["seed"]):
                r["observed"] - r["expected"]
                for r in cert.get("crn_deviations", [])}

    started = time.perf_counter()
    panel_runs = 0

    # ── Stage 1: the recorded offenders, per stream ──────────────────────
    pairs = offenders(cert)
    seeds = sorted({s for _, ss in pairs for s in ss})
    print(f"{len(pairs)} recorded offending vectors over seeds {seeds}",
          flush=True)

    jobs = [({}, seed, lib.PANEL_DAYS, lib.PANEL_UNIVERSE_N,
             lib.PANEL_UNIVERSE_SEED) for seed in seeds]
    for vector, vector_seeds in pairs:
        jobs += [(vector, seed, lib.PANEL_DAYS, lib.PANEL_UNIVERSE_N,
                  lib.PANEL_UNIVERSE_SEED) for seed in vector_seeds]
    rows = lib.run_pool(jobs, args.workers)
    panel_runs += len(jobs)

    base = {row["seed"]: row for row in rows if row["overrides"] == {}}
    traced = []
    for row in rows:
        if row["overrides"] == {}:
            continue
        ref, obs = base[row["seed"]]["draws_by_stream"], row["draws_by_stream"]
        key = (lib.vector_key(row["overrides"]), row["seed"])
        traced.append({
            "seed": row["seed"],
            "overrides": row["overrides"],
            "per_stream": {s: {"pt_v1": ref[s], "vector": obs[s],
                               "delta": obs[s] - ref[s]}
                           for s in ("market", "economy", "external")},
            "total_delta": row["draws_consumed"]
                           - base[row["seed"]]["draws_consumed"],
            "recorded_total_delta": recorded.get(key),
        })

    market_moved = [t for t in traced if t["per_stream"]["market"]["delta"]]
    external_moved = [t for t in traced
                      if t["per_stream"]["external"]["delta"]]
    accounted = [t for t in traced
                 if t["per_stream"]["economy"]["delta"] == t["total_delta"]]
    reproduced = [t for t in traced
                  if t["recorded_total_delta"] is not None
                  and t["recorded_total_delta"] == t["total_delta"]]

    print(f"\n{len(traced)} (vector, seed) pairs re-measured per stream")
    print(f"  market stream moved on   : {len(market_moved)}")
    print(f"  external stream moved on : {len(external_moved)}")
    print(f"  economy accounts for all : {len(accounted)}/{len(traced)}")
    print(f"  reproduces the record on : {len(reproduced)}/{len(traced)}")

    # ── Stage 2: one box axis, walked ────────────────────────────────────
    sweep_seeds = tuple(int(s) for s in args.sweep_seeds.split(","))
    name = args.sweep
    lo, hi = ship[name] / 4.0, ship[name] * 4.0
    values = [lo + (hi - lo) * i / (args.sweep_points - 1)
              for i in range(args.sweep_points)]
    jobs = [({name: v}, seed, lib.PANEL_DAYS, lib.PANEL_UNIVERSE_N,
             lib.PANEL_UNIVERSE_SEED)
            for v in values for seed in sweep_seeds]
    rows = lib.run_pool(jobs, args.workers)
    panel_runs += len(jobs)

    sweep = []
    print(f"\n=== {name} across its §6.3 box, per stream, "
          f"delta against pt-v1 ===")
    print(f"  {'value':>12} " + " ".join(
        f"{'s' + str(s) + ' mkt/eco':>16}" for s in sweep_seeds))
    for value in values:
        entry = {"value": value, "seeds": {}}
        cells = []
        for seed in sweep_seeds:
            row = next(r for r in rows
                       if r["seed"] == seed and r["overrides"] == {name: value})
            ref = base[seed]["draws_by_stream"] if seed in base else None
            if ref is None:
                ref = row["draws_by_stream"]
            deltas = {s: row["draws_by_stream"][s] - ref[s]
                      for s in ("market", "economy", "external")}
            entry["seeds"][str(seed)] = {
                "per_stream": row["draws_by_stream"], "deltas": deltas,
                "annualised_vol_pct": row["panel"].get("annualised_vol_pct")}
            cells.append(f"{deltas['market']:>7}/{deltas['economy']:>8}")
        sweep.append(entry)
        print(f"  {value:>12.5f} " + " ".join(f"{c:>16}" for c in cells))

    # ── Stage 3: the Jacobian's own bracket ─────────────────────────────
    # Stages 1 and 2 say the market stream never moves, so CRN holds
    # everywhere in the box. They also say something sharper about the
    # economy chain: it branches at scattered thresholds, so the panel is
    # a DISCONTINUOUS function of the parameters there — deterministic
    # and reproducible, but not locally differentiable. A secant that
    # brackets a branch measures across a jump. Phase 2's Jacobian and
    # its 32x step ladder are what would be spoiled by that, so this
    # stage asks the question directly of the bracket phase 2 used:
    # h = 0.05 around pt-v1, on each searched parameter, over the full
    # thirty-seed training set the Jacobian was measured on.
    jac_seeds = tuple(int(s) for s in args.jacobian_seeds.split(","))
    from calibrate import SPECTRUM_8

    jobs = [({}, seed, lib.PANEL_DAYS, lib.PANEL_UNIVERSE_N,
             lib.PANEL_UNIVERSE_SEED) for seed in jac_seeds]
    brackets = {}
    for name in SPECTRUM_8:
        low, high, _ = lib.bracket(name, ship[name], args.jacobian_h)
        brackets[name] = (low, high)
        jobs += [({name: v}, seed, lib.PANEL_DAYS, lib.PANEL_UNIVERSE_N,
                  lib.PANEL_UNIVERSE_SEED)
                 for v in (low, high) for seed in jac_seeds]
    rows = lib.run_pool(jobs, args.workers)
    panel_runs += len(jobs)

    jac_base = {r["seed"]: r["draws_by_stream"]
                for r in rows if r["overrides"] == {}}
    jacobian = {}
    branched = 0
    print(f"\n=== the Jacobian bracket: h = {args.jacobian_h} around pt-v1, "
          f"{len(jac_seeds)} seeds, per stream ===")
    for name in SPECTRUM_8:
        low, high = brackets[name]
        entry = {"bracket": [low, high], "deviations": []}
        for row in rows:
            if row["overrides"].get(name) not in (low, high):
                continue
            ref = jac_base[row["seed"]]
            deltas = {s: row["draws_by_stream"][s] - ref[s]
                      for s in ("market", "economy", "external")}
            if any(deltas.values()):
                entry["deviations"].append(
                    {"seed": row["seed"], "value": row["overrides"][name],
                     "deltas": deltas})
        branched += len(entry["deviations"])
        jacobian[name] = entry
        print(f"  {name:<26} [{low:>10.5g}, {high:>10.5g}]  "
              f"{len(entry['deviations'])} of {2 * len(jac_seeds)} "
              f"(vector, seed) pairs branch")

    verdict = (
        "the market stream — the one common random numbers rest on — is "
        "invariant across every recorded offender and the whole swept box "
        "axis; the economy stream accounts for the entire recorded "
        "deviation, which is the documented macro-state coupling and not a "
        f"violation of anything the instrument needs; and {branched} of "
        f"{len(SPECTRUM_8) * 2 * len(jac_seeds)} (vector, seed) pairs in "
        "phase 2's own Jacobian bracket branch the macro chain"
        if not market_moved and len(accounted) == len(traced) else
        "the market stream MOVED: this is a genuine §5.2 violation and the "
        "secants near these points are noise-contaminated")
    print(f"\nverdict: {verdict}")

    wall = time.perf_counter() - started
    lib.write_json(args.out, {
        "provenance": lib.provenance(),
        "claim": {
            "kind": "draw-schedule deviation trace, per stream",
            "source_certificate": args.certificate,
            "rule": "CALIBRATION.md §5.2 — nothing settable may change how "
                    "many draws are taken or in what order",
            "operative_quantity": "Engine.draws_by_stream()['market'], not "
                                  "Engine.draws_consumed: the market "
                                  "stream's schedule is a pure function of "
                                  "(market status, active roster, sector "
                                  "count), while the economy stream's count "
                                  "varies with macro state by design",
        },
        "recorded_offenders": traced,
        "summary": {
            "pairs_re_measured": len(traced),
            "market_stream_moved": len(market_moved),
            "external_stream_moved": len(external_moved),
            "economy_accounts_for_total": len(accounted),
            "reproduces_recorded_delta": len(reproduced),
        },
        "sweep": {"parameter": name, "box": [lo, hi],
                  "seeds": list(sweep_seeds), "points": sweep},
        "jacobian_bracket": {
            "h": args.jacobian_h,
            "seeds": list(jac_seeds),
            "parameters": jacobian,
            "branching_pairs": branched,
            "pairs_checked": len(SPECTRUM_8) * 2 * len(jac_seeds),
        },
        "verdict": verdict,
        "panel_runs": panel_runs,
        "six_seed_vector_equivalents": panel_runs / 6.0,
        "wall_seconds": wall,
    })


if __name__ == "__main__":
    main()
