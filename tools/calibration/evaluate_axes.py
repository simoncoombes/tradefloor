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

EVERY AXIS IS GRADED ON THE RULER FOR ITS OWN HORIZON. That was not true
until 2026-09-05: the 504-day axis was scored by the default
`band_distance_loss`, which is the 252-day bands and the 252-day noise
scale, and the artefact labelled the result "the TRUE bands". The one axis
whose purpose is to vary the horizon was measuring at one horizon and
grading at the other, and its verdict is half of `generalises`. See
`ruler_for` below.

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


def ruler_for(days: int):
    """The bands and the noise scale derived at this axis's own horizon.

    THE FIX THIS FILE EXISTED WITHOUT. Every axis was scored by
    `band_distance_loss(panels)` on the defaults -- `facts.REAL_MARKETS` and
    `facts.SEED_SD`, both derived at 252 days -- and `room_sd` divided by
    `facts.SEED_SD` explicitly, and the artefact labelled the result "the
    TRUE bands". So `holdout_horizon`, the one axis whose entire purpose is
    to vary the horizon and nothing else, was graded on the horizon it was
    varying away from, and its verdict fed `generalises`, which
    `calibrate.py`, `emit_preset.py` and `report_tables.py` read.

    `section8_check.py:106-113` fixed exactly this for its own horizon axes
    and its comment names it "§32's error"; this file was left. The lookup
    below is `facts.rulers_for_horizon`, which refuses a horizon with no band
    set rather than falling back, so an axis added at 756 or 1,008 days fails
    at the ruler rather than quietly scoring on the 252-day one.
    """
    import tradefloor.facts as facts

    bands, seed_sd = facts.rulers_for_horizon(
        days, what=f"the {days}-day axis")
    return bands, seed_sd, facts.RULERS_BY_HORIZON[days]["bands_name"]


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
            bands, seed_sd, ruler_name = ruler_for(days)
            breakdown = loss_mod.band_distance_loss(
                panels, bands=bands, seed_sd=seed_sd)
            # How far inside its band each statistic sits, in its own seed
            # noise. The band loss is flat inside the band and cannot see
            # this; it is what decides whether a statistic survives a
            # change of seeds, universe or horizon, so a tool whose whole
            # job is comparing named vectors across those axes reports it.
            # In THIS axis's noise scale: `facts.SEED_SD` was hard-coded
            # here, so every 504-day room reading was divided by the
            # 252-day denominator, which differs by factors from 0.80 to
            # 3.23 across rows.
            for key, srow in breakdown["statistics"].items():
                lo, hi = srow["band"]
                sd = seed_sd.get(key)
                m = srow["measured"]
                srow["room_sd"] = (None if m is None or not sd
                                   else min(m - lo, hi - m) / sd)
            boot = np.random.default_rng(20260822)
            spread = float("nan")
            if len(panels) > 2:
                spread = statistics.stdev([
                    loss_mod.band_distance_loss(
                        [panels[i] for i in
                         boot.integers(0, len(panels), len(panels))],
                        bands=bands, seed_sd=seed_sd)["loss"]
                    for _ in range(2000)])
            row["axes"][axis] = {
                "seeds": list(seeds), "days": days,
                "universe": f"Universe.random({universe_n}, "
                            f"seed={universe_seed})",
                "loss_real": breakdown["loss"],
                # Named per axis, because it differs per axis. This field
                # read "the TRUE bands (facts.REAL_MARKETS)" on every axis
                # including the 504-day one, which is how the wrong ruler
                # survived: the artefact asserted the right answer.
                "bands_used_for_every_verdict_here": ruler_name,
                "ruler_horizon_days": days,
                "seed_sd_used": ("facts.SEED_SD" if days == 252
                                 else "facts.SEED_SD_504"),
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
    #
    # Each side is now graded on its own horizon's ruler, so a flip on
    # `holdout_horizon` means what the axis is named for: in band at 252
    # days against the 252-day bands, out of band at 504 against the
    # 504-day ones. Before the fix above it meant "the 504-day reading of
    # this row fell outside the 252-day band", which is a statement about
    # two rulers and cannot be attributed to the horizon at all.
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
            # The ruler is recorded PER AXIS. One `bands` block for the whole
            # file described the 252-day set and stood over a 504-day axis
            # graded (then, wrongly; now, correctly) against a different one,
            # so a reader checking the artefact's own provenance was told the
            # thing that was not true.
            "axes": {k: {"seeds": list(v[0]), "universe_n": v[1],
                         "universe_seed": v[2], "days": v[3],
                         "bands": ruler_for(v[3])[2],
                         "seed_sd": ("facts.SEED_SD" if v[3] == 252
                                     else "facts.SEED_SD_504")}
                     for k, v in AXES.items()},
            "bands_by_horizon": {
                str(days): {k: list(v) for k, v in
                            facts.RULERS_BY_HORIZON[days]["bands"].items()}
                for days in sorted({v[3] for v in AXES.values()})},
            "seed_sd_by_horizon": {
                str(days): dict(facts.RULERS_BY_HORIZON[days]["seed_sd"])
                for days in sorted({v[3] for v in AXES.values()})},
            # Kept under the old names, and they mean what they always
            # described: the CERTIFIED-horizon tables, which are the ruler
            # for three of the four axes and the row order every reader of
            # this file uses. What changed is that they no longer stand for
            # the fourth axis; `axes[*]["bands"]` names each axis's own.
            "bands": {k: list(v) for k, v in facts.REAL_MARKETS.items()},
            "seed_sd": dict(facts.SEED_SD),
            "bands_note": (
                f"the {facts.CERTIFIED_HORIZON_DAYS}-day tables. Read "
                f"axes[*].bands for the ruler each axis was actually graded "
                f"with; the 504-day set holds the fourteen shape rows only, "
                f"so the level and crisis rows are ungraded on that axis "
                f"rather than graded against a 252-day band."),
            "workers": args.workers,
        },
        "vectors": results,
        "panel_runs": panel_runs,
        "six_seed_vector_equivalents": panel_runs / 6.0,
        "wall_seconds": wall,
    })


if __name__ == "__main__":
    main()
