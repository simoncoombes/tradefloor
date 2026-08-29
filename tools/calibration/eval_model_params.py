"""The seam, doing the job it was built for: the realism panel at several
parameter vectors, one build, no rebuilds — and the wall-clock bill.

Under the compile-time regime this file could not exist. Sweeping one
constant across eight values cost eight wheel builds tonight (~60 s per
vector serially: patch, build, reinstall, measure — CALIBRATION.md §7.2);
sweeping a VECTOR was priced at worktree parallelism, 5x disk and the
stale-wheel hazard class. Post-seam, a parameter vector is an argument:

    .venv/bin/python tools/calibration/eval_model_params.py \
        --seeds 1,2,3 --workers 8

The panel is `tradefloor.facts.measure` — the library's own instrument, never
a reimplementation (the Appendix B rule) — at the published method:
`Universe.random(40, seed=111)`, 252 days, per seed. The model reaches it
through `Engine(model=...)`.

## The two runtime assertions that replace the stale-wheel fingerprint

The old sweep hashed a fixed trajectory per wheel so that a failed rebuild
could not record one constant under two labels. The seam's equivalents,
asserted on every run of this tool (Appendix B):

- **Distinct vectors carry distinct `ModelParams` fingerprints**, and a
  run's engine reports the fingerprint of the vector it was asked to run —
  a mislabelled evaluation is structurally impossible, not merely unlikely.
- **`draws_consumed` is identical across vectors for each seed** — the CRN
  guard (§4.1): every vector consumed the same draw schedule, so their
  panel differences are parameter effects, not reshuffled noise.

## The facts.measure shim, stated plainly

`facts.measure` builds its engine internally and does not (yet) take
`model=` — `facts.py` belongs to the phase-0 stream. Until it grows the
argument, this tool substitutes the `Engine` symbol in `tradefloor.facts`
with a partial application that adds `model=`, calls the UNMODIFIED
`measure`, and restores the symbol in a `finally:`. Every statistic is
computed by the library's own code; only the constructor call is
intercepted. When `facts.measure(model=...)` lands, `_measure_with_model`
collapses to one line.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from concurrent.futures import ProcessPoolExecutor

#: The demo vectors: the shipped preset plus perturbations of exactly the
#: parameters the calibration search will move (§3.9 plus tonight's new
#: surface: the GJR gamma, the factor-vol process, the idiosyncratic
#: scale). Values are within-bounds nudges, not candidates.
DEMO_VECTORS: list[dict[str, float]] = [
    {},  # pt-v1 itself
    {"garch_alpha": 0.03, "garch_beta": 0.79},
    {"garch_gamma": 0.28},
    {"market_factor_sigma": 0.018},
    {"idio_sigma_scale": 0.78},
    {"market_vol_alpha": 0.35, "market_vol_beta": 0.60},
    {"momentum_theta": 0.30},
    {"mispricing_half_life_days": 45.0},
]

#: The panel statistics reported per vector, in one fixed order.
PANEL_KEYS = (
    "annualised_vol_pct", "excess_kurtosis", "return_acf1",
    "abs_return_acf1", "cross_sectional_corr", "leverage_effect",
)


def _measure_with_model(overrides: dict[str, float], seed: int,
                        days: int, universe_n: int,
                        universe_seed: int) -> dict:
    """One (vector, seed) evaluation: facts.measure under a model.

    Runs in a worker process; imports live here so the parent can fork or
    spawn without payload. Returns the panel plus the identity that makes
    the two runtime assertions checkable in the parent.
    """
    import tradefloor
    import tradefloor.facts as facts

    model = tradefloor.ModelParams.from_preset("pt-v1", **overrides)
    universe = tradefloor.Universe.random(universe_n, seed=universe_seed)

    engines: list = []
    original = facts.Engine

    def engine_with_model(**kwargs):
        engine = original(model=model, **kwargs)
        engines.append(engine)
        return engine

    started = time.perf_counter()
    facts.Engine = engine_with_model
    try:
        panel = facts.measure(seed=seed, universe=universe, days=days)
    finally:
        facts.Engine = original
    elapsed = time.perf_counter() - started

    engine = engines[0]
    assert engine.model_fingerprint == model.fingerprint, (
        "the engine ran a different model than the vector asked for — "
        "the shim failed, and recording this panel would mislabel it"
    )
    return {
        "fingerprint": model.fingerprint,
        "overrides": overrides,
        "seed": seed,
        "seconds": elapsed,
        "draws_consumed": engine.draws_consumed,
        "panel": {key: panel.get(key) for key in PANEL_KEYS},
    }


def _star(args):
    return _measure_with_model(*args)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--seeds", default="1,2,3",
                        help="comma-separated simulation seeds per vector")
    parser.add_argument("--days", type=int, default=252)
    parser.add_argument("--universe-n", type=int, default=40)
    parser.add_argument("--universe-seed", type=int, default=111)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--vectors", default=None,
                        help="JSON file: a list of override dicts "
                             "(default: the built-in demo vectors)")
    parser.add_argument("--out", default=None,
                        help="write the full JSON report here as well")
    args = parser.parse_args()

    seeds = [int(s) for s in args.seeds.split(",") if s]
    if args.vectors:
        with open(args.vectors, encoding="utf-8") as handle:
            vectors = json.load(handle)
    else:
        vectors = DEMO_VECTORS

    jobs = [(overrides, seed, args.days, args.universe_n, args.universe_seed)
            for overrides in vectors for seed in seeds]

    print(f"{len(vectors)} vectors x {len(seeds)} seeds = {len(jobs)} "
          f"evaluations, {args.days} days x {args.universe_n} instruments, "
          f"{args.workers} workers, ONE build\n")

    wall_started = time.perf_counter()
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(_star, jobs))
    wall = time.perf_counter() - wall_started

    # ── Assertion 1: distinct vectors, distinct fingerprints ─────────────
    fingerprints: dict[str, str] = {}
    for row in results:
        key = json.dumps(row["overrides"], sort_keys=True)
        fingerprints.setdefault(key, row["fingerprint"])
    seen = list(fingerprints.values())
    assert len(set(seen)) == len(seen), (
        f"two distinct vectors share a fingerprint: {seen} — "
        "the label can no longer identify the model"
    )

    # ── Assertion 2: draws_consumed equal across vectors per seed ────────
    by_seed: dict[int, set[int]] = {}
    for row in results:
        by_seed.setdefault(row["seed"], set()).add(row["draws_consumed"])
    for seed, counts in sorted(by_seed.items()):
        assert len(counts) == 1, (
            f"seed {seed}: draw counts differ across vectors ({counts}) — "
            "a parameter moved the draw schedule, which no preset member "
            "may (CALIBRATION.md section 5.2)"
        )
    print("CRN guard: draws_consumed identical across vectors for every "
          f"seed ({', '.join(str(s) for s in sorted(by_seed))})")
    print("label guard: "
          f"{len(seen)} distinct vectors, {len(set(seen))} distinct "
          "fingerprints\n")

    # ── The panel, per vector (median across seeds) ──────────────────────
    header = f"{'fingerprint':<16}" + "".join(f"{k[:14]:>16}" for k in PANEL_KEYS)
    print(header)
    for overrides in vectors:
        key = json.dumps(overrides, sort_keys=True)
        rows = [r for r in results
                if json.dumps(r["overrides"], sort_keys=True) == key]
        cells = []
        for stat in PANEL_KEYS:
            values = [r["panel"][stat] for r in rows
                      if r["panel"][stat] is not None]
            cells.append(f"{statistics.median(values):>16.4f}" if values
                         else f"{'—':>16}")
        print(f"{rows[0]['fingerprint']:<16}" + "".join(cells))

    # ── The bill, against section 7.2 ────────────────────────────────────
    per_eval = statistics.median(r["seconds"] for r in results)
    per_hour = len(jobs) / wall * 3600.0
    six_seed_per_hour = per_hour / 6.0
    print(f"\nper (vector, seed) evaluation: {per_eval:.2f}s median "
          "(under full worker load)")
    print(f"batch wall clock: {wall:.1f}s for {len(jobs)} evaluations on "
          f"{args.workers} workers")
    print(f"throughput: {per_hour:,.0f} single-seed evaluations/hour "
          f"= {six_seed_per_hour:,.0f} six-seed vectors/hour")
    print("section 7.2's prediction: ~19 s per six-seed vector per worker, "
          "~1,500 six-seed vectors/hour on eight workers — against ~60 "
          "rebuild-evaluations/hour serially under the compile-time "
          "regime. The prediction assumes eight workers each running at "
          "the uncontended serial rate; on a machine with fewer "
          "performance cores than workers, the per-evaluation time above "
          "is the honest contended figure.")

    if args.out:
        report = {
            "method": {
                "universe": f"Universe.random({args.universe_n}, "
                            f"seed={args.universe_seed})",
                "days": args.days, "seeds": seeds,
                "workers": args.workers,
            },
            "wall_seconds": wall,
            "evaluations_per_hour": per_hour,
            "results": results,
        }
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, sort_keys=True)
        print(f"\nfull report written to {args.out}")


if __name__ == "__main__":
    main()
