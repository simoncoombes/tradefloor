"""Measure named vectors on the four §8 axes, as a certificate.

`calibrate.py` searches; this evaluates. It exists because the last step
of a calibration is not a search at all — it is the question "does this
particular vector hold up on the axes the search never saw", asked of a
handful of candidates that a finding, not an optimiser, produced.

The finding that produced its first use: the search's own optimum reached
its 252-day targets partly by pushing the market-factor variance process
to a persistence of 0.9964 — a 192-day half-life against a 252-day
measurement window. A variance memory longer than the window it is
measured through is not identified by that window; the panel cannot tell
it from a random walk, so the loss cannot price it, and the 504-day
hold-out is where the difference surfaces. Testing that diagnosis means
evaluating the same vector with the factor-variance process put back
where it started, which is a comparison of named vectors and not a
search.

Each vector is measured on all four axes — the training seeds and §8's
three held-out ones — and the JSON carries the same per-statistic rows,
per-seed panels and provenance a `calibrate.py` certificate does, so the
two are readable side by side.

    .venv/bin/python tools/calibration/evaluate_axes.py \
        --certificate results/calibrate-pt-v2-2026-08-22.json \
        --variant "name=key:value,key:value" \
        --out results/calibrate-pt-v2-constrained-2026-08-22.json
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import time

import numpy as np

import instrumentlib as lib

AXES = {
    "train_seeds": (lib.TRAIN_SEEDS, lib.PANEL_UNIVERSE_N,
                    lib.PANEL_UNIVERSE_SEED, lib.PANEL_DAYS),
    "holdout_seeds": (lib.PUBLISHED_SEEDS, lib.PANEL_UNIVERSE_N,
                      lib.PANEL_UNIVERSE_SEED, lib.PANEL_DAYS),
    "holdout_universe": (lib.TRAIN_SEEDS, 60, 222, lib.PANEL_DAYS),
    # The horizon axis varies the horizon and NOTHING ELSE, so it carries
    # the training seeds rather than a subset of them. It used to run
    # three (101, 104, 107), and that made it two changes wearing one name:
    # the measured excess on `abs_return_acf5` at 504 days decomposes into
    # +0.0435 from the horizon and +0.1287 from those three seeds being
    # high-clustering draws. The seed term was three times the term the axis
    # is named for, and was being read as the horizon's.
    "holdout_horizon": (lib.TRAIN_SEEDS, lib.PANEL_UNIVERSE_N,
                        lib.PANEL_UNIVERSE_SEED, 504),
}


def parse_variant(text: str, base: dict[str, float]) -> tuple[str, dict]:
    """`name=param:value,param:value` — overrides applied ON TOP of base."""
    name, _, body = text.partition("=")
    out = dict(base)
    for item in body.split(","):
        if not item.strip():
            continue
        key, _, value = item.partition(":")
        key = key.strip()
        if key not in lib.PARAM_SPECS:
            raise SystemExit(f"{key} is not a settable parameter")
        out[key] = float(value)
    return name, out


def diagnostics(vector: dict[str, float], ship: dict[str, float]) -> dict:
    """The two persistences and their half-lives, in trading days.

    Reported on every vector because they are the quantity the panel's
    252-day window can and cannot resolve, and the reason this tool
    exists. A half-life at or beyond the window is a memory the
    measurement cannot see; the loss will happily buy one.
    """
    def val(name: str) -> float:
        return vector.get(name, ship[name])

    out = {}
    for label, total in (
        ("market_factor_variance",
         val("market_vol_alpha") + val("market_vol_beta")),
        ("per_name_garch",
         val("garch_alpha") + val("garch_beta") + val("garch_gamma") / 2.0),
    ):
        out[label] = {
            "persistence": total,
            "half_life_days": (math.log(0.5) / math.log(total)
                               if 0 < total < 1 else float("inf")),
            "window_days": lib.PANEL_DAYS,
        }
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--certificate", default=None,
                        help="a calibrate.py certificate; its best_vector "
                             "becomes the base every variant modifies")
    parser.add_argument("--variant", action="append", default=[],
                        help="name=param:value,... (repeatable)")
    parser.add_argument("--include-base", action="store_true",
                        help="also measure the certificate's own vector")
    parser.add_argument("--include-ship", action="store_true",
                        help="also measure pt-v1")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    import tradefloor.facts as facts
    import tradefloor.loss as loss_mod

    ship = lib.shipped_values()
    base: dict[str, float] = {}
    source = None
    if args.certificate:
        with open(args.certificate, encoding="utf-8") as handle:
            cert = json.load(handle)
        base = dict(cert["best_vector"])
        source = args.certificate

    vectors: list[tuple[str, dict]] = []
    if args.include_ship:
        vectors.append(("pt-v1", {}))
    if args.include_base:
        vectors.append(("search-optimum", dict(base)))
    for text in args.variant:
        vectors.append(parse_variant(text, base))

    started = time.perf_counter()
    panel_runs = 0
    results: dict[str, dict] = {}
    for name, vector in vectors:
        bad = lib.feasibility_violation(vector, ship)
        if bad:
            raise SystemExit(f"{name}: {bad}")
        row: dict = {"vector": vector,
                     "moves": {k: {"pt_v1": ship[k], "value": v}
                               for k, v in sorted(vector.items())
                               if v != ship[k]},
                     "diagnostics": diagnostics(vector, ship),
                     "axes": {}}
        for axis, (seeds, universe_n, universe_seed, days) in AXES.items():
            jobs = [(vector, seed, days, universe_n, universe_seed)
                    for seed in seeds]
            rows = lib.run_pool(jobs, args.workers)
            panel_runs += len(jobs)
            crn = lib.crn_streams(rows)
            panels = [r["panel"] for r in rows]
            breakdown = loss_mod.band_distance_loss(panels)
            # How far inside its band each statistic sits, in its own seed
            # noise. The band loss is flat inside the band and cannot see
            # this; it is what decides whether a statistic survives a
            # change of seeds, universe or horizon, so a tool whose whole
            # job is comparing named vectors across those axes reports it.
            for key, srow in breakdown["statistics"].items():
                lo, hi = srow["band"]
                sd = facts.SEED_SD.get(key)
                m = srow["measured"]
                srow["room_sd"] = (None if m is None or not sd
                                   else min(m - lo, hi - m) / sd)
            boot = np.random.default_rng(20260822)
            spread = float("nan")
            if len(panels) > 2:
                spread = statistics.stdev([
                    loss_mod.band_distance_loss(
                        [panels[i] for i in
                         boot.integers(0, len(panels), len(panels))])["loss"]
                    for _ in range(2000)])
            row["axes"][axis] = {
                "seeds": list(seeds), "days": days,
                "universe": f"Universe.random({universe_n}, "
                            f"seed={universe_seed})",
                "loss_real": breakdown["loss"],
                "bands_used_for_every_verdict_here": "the TRUE bands "
                                                     "(facts.REAL_MARKETS)",
                "bootstrap_spread": spread,
                "statistics": breakdown["statistics"],
                "crn_guard": {"asserted_stream": lib.CRN_STREAM,
                              "market": crn["market"],
                              "economy_deviations":
                                  crn["economy_deviations"]},
                "panels": panels,
            }
            print(f"{name:<22} {axis:<18} L_real "
                  f"{breakdown['loss']:8.4f}", flush=True)
        results[name] = row

    # §8's first clause, applied per vector: a statistic in band on train
    # and out of band on any validation axis. Scale-free, unlike the
    # 2x-bootstrap-spread clause, which a training loss near zero makes
    # almost impossible to satisfy.
    for name, row in results.items():
        train = row["axes"]["train_seeds"]["statistics"]
        flips = []
        for axis in ("holdout_seeds", "holdout_universe", "holdout_horizon"):
            stats = row["axes"][axis]["statistics"]
            for key in (list(loss_mod.LIVE_TARGETS)
                        + list(loss_mod.CONSTRAINTS)):
                if train[key]["distance"] == 0 and stats[key]["distance"] > 0:
                    flips.append({"statistic": key, "axis": axis,
                                  "measured": stats[key]["measured"],
                                  "band": stats[key]["band"],
                                  "scaled": stats[key]["scaled"]})
        out_on_train = [k for k in loss_mod.LIVE_TARGETS
                        if train[k]["distance"] > 0]
        row["verdict"] = {
            "live_targets_out_of_band_on_train": out_on_train,
            "in_band_on_train_out_on_validation": flips,
            "generalises": not flips and not out_on_train,
        }
        print(f"\n{name}: "
              f"{'PASSES' if row['verdict']['generalises'] else 'FAILS'} "
              f"— targets out on train: {out_on_train or 'none'}; "
              f"band exits on validation: "
              f"{[f['statistic'] + '/' + f['axis'] for f in flips] or 'none'}")

    wall = time.perf_counter() - started
    lib.write_json(args.out, {
        "provenance": lib.provenance(),
        "claim": {
            "kind": "axis evaluation of named vectors",
            "base_certificate": source,
            "targets": list(loss_mod.LIVE_TARGETS),
            "constraints": list(loss_mod.CONSTRAINTS),
            "structural_excluded": list(loss_mod.STRUCTURAL),
        },
        "method": {
            "axes": {k: {"seeds": list(v[0]), "universe_n": v[1],
                         "universe_seed": v[2], "days": v[3]}
                     for k, v in AXES.items()},
            "bands": {k: list(v) for k, v in facts.REAL_MARKETS.items()},
            "seed_sd": dict(facts.SEED_SD),
            "workers": args.workers,
        },
        "vectors": results,
        "panel_runs": panel_runs,
        "six_seed_vector_equivalents": panel_runs / 6.0,
        "wall_seconds": wall,
    })


if __name__ == "__main__":
    main()
