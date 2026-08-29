"""Fork a market and apply a scenario from a YAML file to one branch.

Run it:

    python examples/11-scenario-fork.py

Example 10 forks a market and changes one variable by hand. This does the
same thing with a scenario read off disk, which is the difference between an
experiment you can run and one you can hand to somebody else.

    scenarios/liquidity_crisis.yml
              |
        Scenario.from_yaml
              |
        checkpoint.branch(2)
         /            \\
    control          stress   <- scenario applied here only
         \\            /
          tf.compare

It should take under five minutes to read and about three seconds to run.

## What the scenario says, and what it does not

`liquidity_crisis.yml` declares two shocks -- quoted depth to 40%, volatility
doubled -- and one ASSUMPTION, that credit widens 50 basis points alongside
them. The file keeps those apart, and so does everything that prints them,
because this simulator does not derive the third from the first two. It runs
the market you describe; the description is yours.

That is also why the depth shock barely moves prices and moves fills a lot.
An evaluation that reads only the price series will score an agent as though
it traded for free.
"""

import pathlib
import struct
import time

import tradefloor as tf

SCENARIO = (pathlib.Path(__file__).resolve().parent.parent
            / "scenarios" / "liquidity_crisis.yml")

UNIVERSE_SIZE = 24
SEED = 7
TICKS_PER_DAY = 65
DAYS_BEFORE_FORK = 50
DAYS_AFTER_FORK = 80


def trading_day(engine, day, universe):
    flow = {universe[0].ticker: (300_000.0, 0.0),
            universe[1].ticker: (0.0, 220_000.0)}
    engine.open_market()
    engine.run_session(9, 30, 3, TICKS_PER_DAY, order_flow=flow)
    engine.record(day)
    engine.close_market()


def prices(engine, universe):
    return struct.unpack("<%dd" % len(universe), engine.prices())


def sweep_cost_bps(engine, universe, shares=50_000):
    """What it costs to buy `shares` of the median name, right now.

    The unambiguous measurement of a liquidity shock. An agent's realised
    impact depends on the agent; the cost of walking a book does not.
    """
    costs = []
    for instrument in universe:
        book = engine.book(instrument.ticker)
        if book.mid_price is None:
            continue
        cost = book.sweep_cost("buy", shares)
        if cost is not None and cost.filled > 0:
            costs.append((cost.average_price / book.mid_price - 1.0) * 10_000)
    costs.sort()
    return costs[len(costs) // 2] if costs else float("nan")


def check(label, passed):
    print(f"  {label:<46} {'PASS' if passed else 'FAIL'}")
    return passed


def main() -> dict:
    started = time.time()
    report: dict = {}
    universe = tf.Universe.random(UNIVERSE_SIZE, seed=11)

    # 1. The scenario is a file. Read it, look at it, and cite its
    #    fingerprint -- which covers the resolved experiment rather than the
    #    file's bytes, so reformatting the YAML does not move it.
    scenario = tf.Scenario.from_yaml(str(SCENARIO))
    print(scenario.describe())
    print()
    print(f"  library            {tf.__version__}")
    print(f"  preset             {tf.ModelParams.from_preset().fingerprint}")
    print(f"  seed               {SEED}")

    # 2. Run a market to the point the question starts from.
    engine = tf.Engine(seed=SEED, universe=universe)
    for day in range(DAYS_BEFORE_FORK):
        trading_day(engine, day, universe)
    print(f"\n  checkpoint         step {DAYS_BEFORE_FORK}")

    # 3. Fork it. Two branches with the same history, sharing no memory.
    control, stress = tf.branch(engine, 2)

    # 4. Apply the scenario to ONE of them.
    #
    #    `at` counts from where the run loop starts, and this loop starts at
    #    zero, so the file's `at: 50` lands fifty steps AFTER THE FORK --
    #    step 100 of the parent's history. That is the only reading under
    #    which one file means one experiment on both sides of a checkpoint,
    #    and it is why nothing here rewrites the scenario to suit the branch.
    #
    #    The control branch gets nothing at all. It is not a scenario with
    #    the values turned down; it is the same world without the shock.
    first = min(item.at for item in scenario.interventions)
    print(f"  scenario applied   to the stress branch only, firing at "
          f"step {DAYS_BEFORE_FORK + first} of the parent's history")

    for i in range(DAYS_AFTER_FORK):
        day = DAYS_BEFORE_FORK + i
        scenario.apply(stress, i)
        trading_day(control, day, universe)
        trading_day(stress, day, universe)

    # 5. What actually fired, with the values it saw. Not the recipe: the
    #    trail records that depth went from N shares to 0.4N on the day it
    #    happened, which is what a reader needs afterwards.
    print("\n  INTERVENTIONS APPLIED")
    for firing in scenario.log[:6]:
        print(f"    {firing}")
    if len(scenario.log) > 6:
        print(f"    ... {len(scenario.log)} firings in total")
    report["firings"] = len(scenario.log)

    # 6. What the scenario did.
    moves = sorted(s / c - 1.0 for s, c in
                   zip(prices(stress, universe), prices(control, universe)))
    median = moves[len(moves) // 2]
    report["median_move_pct"] = median * 100.0
    control_cost = sweep_cost_bps(control, universe)
    stress_cost = sweep_cost_bps(stress, universe)
    report["sweep_cost_bps"] = stress_cost
    report["sweep_cost_bps_control"] = control_cost

    print("\n  WHAT IT DID")
    print(f"    median name        {median * 100:+.2f}%")
    print(f"    cost to buy 50k    {control_cost:.2f}bp control, "
          f"{stress_cost:.2f}bp stress")
    print(f"    fingerprint        {scenario.fingerprint}")

    ok = True
    ok &= check("the scenario fired", bool(scenario.log))
    ok &= check("it moved the stress branch",
                prices(control, universe) != prices(stress, universe))
    # The point of this scenario: depth, not price. A liquidity shock that
    # showed up mostly in prices would mean the lever is reaching valuation,
    # which it is not supposed to.
    ok &= check("it cost more to trade in the stress branch",
                stress_cost > control_cost)
    ok &= check("both branches stayed on one draw schedule",
                control.draws_by_stream()["market"]
                == stress.draws_by_stream()["market"])

    # 7. The scenario is DATA, so the run that used it is reproducible from
    #    its manifest even if the YAML file is edited or deleted afterwards.
    manifest = tf.RunManifest.of(stress, seed=SEED, universe=universe,
                                 scenario=scenario)
    recovered = tf.RunManifest.from_json(manifest.to_json()).scenario
    ok &= check("the manifest carries the resolved scenario",
                recovered.fingerprint == scenario.fingerprint)

    report["passed"] = bool(ok)
    print(f"\n  scenario fork      {'PASS' if ok else 'FAIL'}")
    print(f"  total              {time.time() - started:.1f}s")
    assert ok, "a scenario guarantee failed; see the checks above"
    return report


if __name__ == "__main__":
    main()
