"""A complete research workflow, start to finish, in about ten seconds.

Run it:

    python examples/research_workflow.py

This exercises all four things the library exists to provide — a reproducible
market, ground truth about why prices moved, emergent impact through a real
book, and a counterfactual — in the order a researcher would actually reach for
them.

It is also run by the test suite, deliberately. Running the library the way a
user would, rather than only running its unit tests, is what caught the two
worst defects this project has had: a parallel sweep that hung forever in any
notebook, and an order log that did not compare equal to itself after a JSON
round trip. Both had green unit tests. Neither survived one honest end-to-end
run.
"""

import time

import pretium as pt
from pretium.baselines import Momentum, capture_ratio, reference_agents


def main() -> dict:
    started = time.time()
    report: dict = {}

    # 1. A universe. Pin it by serialising: `random(60, seed=11)` is only a
    #    citable specification while the generator is versioned with the model,
    #    and to_json() pins the exact roster regardless.
    universe = pt.Universe.random(60, seed=11)
    report["universe"] = len(universe)
    print(f"1. universe: {len(universe)} instruments")

    # 2. A sweep. Per seed is the only safe parallel boundary — the engine has
    #    one shared RNG stream, so there is no decomposition WITHIN a run that
    #    preserves the draw schedule.
    mark = time.time()
    sweep = pt.run_many(seeds=list(range(20)), universe=universe, days=5,
                        workers=8, collect="summary")
    report["sweep"] = len(sweep)
    print(f"2. swept {len(sweep)} seeds x 5 days in {time.time() - mark:.1f}s")

    # 3. Rank agents on one market. Same seed for every agent, so the
    #    comparison is between strategies rather than between markets — order
    #    flow consumes no RNG draws, which is what makes that exact.
    mark = time.time()
    scores = pt.evaluate(reference_agents(seed=3), seed=7, universe=universe,
                         days=10)
    print(f"3. evaluated {len(scores)} agents in {time.time() - mark:.1f}s")
    for card in pt.leaderboard(scores):
        print(f"     {card.name:16s} {card.return_pct:+7.2f}%  "
              f"impact {card.impact_bps:+8.2f} bps")

    # The number worth reporting. Raw P&L is not comparable across markets —
    # a seed with more dispersion pays every strategy more — and dividing by
    # what perfect information earned in THAT market removes exactly that.
    ratios = capture_ratio(scores)
    report["capture"] = ratios
    print(f"     capture vs the oracle: "
          f"{ {k: round(v, 3) for k, v in ratios.items()} }")

    # 4. What did the winner's trading cost? Every fill priced against a market
    #    where it never traded — the benchmark real TCA cannot have.
    mark = time.time()
    execution = pt.tca.analyse(Momentum(), seed=7, universe=universe, days=10)
    report["shortfall_bps"] = execution.shortfall_bps()
    print(f"4. shortfall {execution.shortfall_bps():+.2f} bps over "
          f"{len(execution.fills)} fills ({time.time() - mark:.1f}s)")
    print(f"     partial fills: {len(execution.partial_fills())}  "
          f"(a cheap execution that did not happen is not a cheap execution)")

    # Nothing the trader did not touch should have moved. Order flow consumes
    # no draws, so the untraded names follow byte-identical paths — this is
    # the assertion that the subtraction was clean, not an explanation of why
    # it might not be.
    leaked = execution.untouched_moved()
    report["leaked"] = leaked
    assert not leaked, f"impact leaked into untraded names: {leaked}"
    print("     untouched instruments moved: none, as they must not")

    # 5. Ground truth for the same market. One row per instrument per tick,
    #    and the seven components sum to the change in mispricing — so the
    #    label can be checked rather than trusted.
    mark = time.time()
    engine = pt.Engine(seed=7, universe=universe)
    engine.run_days(10, record=True)
    truth = engine.truth()
    report["truth_rows"] = truth.num_rows
    print(f"5. ground truth: {truth.num_rows:,} rows x {len(truth.columns)} "
          f"columns in {time.time() - mark:.1f}s")

    # 6. What actually drove this market? The day-grain view of the same
    #    quantity, so it agrees with the table above by construction.
    import struct

    totals = {
        factor: sum(abs(x) for x in struct.unpack(
            "<%dd" % len(universe), engine.attribution(factor)))
        for factor in pt.Engine.FACTORS
    }
    ranked = sorted(totals.items(), key=lambda kv: -kv[1])
    report["dominant"] = ranked[0][0]
    print("6. drivers, largest first: "
          + ", ".join(f"{k} {v:.1e}" for k, v in ranked[:3]))

    # 7. Archive it. The log plus the seed plus the universe reproduce the run
    #    without this script.
    import json

    archive = {
        "seed": 7,
        "universe": json.loads(universe.to_json()),
        "log": engine.order_log,
    }
    blob = json.dumps(archive)
    report["archive_bytes"] = len(blob)
    restored = json.loads(blob)
    replayed = pt.replay(
        restored["log"], seed=restored["seed"],
        universe=pt.Universe.from_json(json.dumps(restored["universe"])),
    )
    assert replayed.prices() == engine.prices(), "replay diverged"
    print(f"7. archived {len(blob):,} bytes and replayed it to identical prices")

    print(f"\ntotal {time.time() - started:.1f}s")
    return report


if __name__ == "__main__":
    main()
