"""Fork a market and apply a scenario from a YAML file to one branch.

Run it:

    python examples/11-scenario-fork.py

Example 10 forks a market and changes one variable by hand. This does the
same thing with a scenario read off disk, which is the difference between an
experiment you can run and one you can hand to somebody else.

    Scenario.load("liquidity_crisis")   # ships in the wheel
              |
              |
        checkpoint.branch(2)
         /            \\
    control          stress   <- scenario applied here only
         \\            /
          tf.compare

It should take under five minutes to read and about two seconds to run.

## What the scenario says, and what it does not

`liquidity_crisis.yml` declares shocks to three things and one ASSUMPTION.
Quoted depth goes to 40% and the VIX to three and a half times its level for
twenty-five days, and earnings fall 15% over two months and recover over the
next four. The assumption is that credit widens 50 basis points alongside
them. The file keeps the shocks apart from the assumption, and so does
everything that prints them, because this simulator does not derive the
credit move from the others. It runs the market you describe; the
description is yours.

On its own the depth shock leaves the median name's price where the control
has it (+0.00% on this seed) and moves fills a lot, so an evaluation that
reads only the price series will score an agent as though it traded for
free. The price move this prints comes from the
earnings cut, which is still falling when the run stops eighty days after
the fork. The file's recovery runs to day 175, past the end of this run.

The book is read twice, inside the window and after it. Both readings matter:
the first is what the shock cost, and the second is that the shock ENDED.
Nothing in the engine writes `avg_volume` back, so the scenario restores it
when the window closes -- and until it did, a twenty-five day crisis quietly
lasted for the rest of the run.
"""

import struct
import time

import tradefloor as tf

SCENARIO = "liquidity_crisis"

UNIVERSE_SIZE = 24
SEED = 7
TICKS_PER_DAY = 65
DAYS_BEFORE_FORK = 50
DAYS_AFTER_FORK = 80


def trading_day(engine, day, universe):
    flow = {universe[0].ticker: (300_000.0, 0.0),
            universe[1].ticker: (0.0, 220_000.0)}
    engine.open_market()
    engine.run_session(9, 30, 3, TICKS_PER_DAY, flow_per_tick=flow)
    engine.record(day)
    engine.close_market()


def prices(engine, universe):
    return struct.unpack("<%dd" % len(universe), engine.prices())


def sweep_cost_bps(engine, universe, shares=20_000):
    """What it costs to buy `shares` of the median name, right now.

    The unambiguous measurement of a liquidity shock. An agent's realised
    impact depends on the agent; the cost of walking a book does not.

    20,000 shares, and 50,000 until 0.8.0. A sweep that runs past the
    displayed depth fills only what is there and quotes the average of
    that, so an order bigger than the thinned book prices the whole book
    and stops rising. At 50,000 the stress branch's median name was past
    its depth on every preset measured, and on pt-v19 the two branches read
    14.10bp and 14.30bp, so the check below passed by a fifth of a basis
    point on a reading that had saturated. At 20,000 pt-v19 read 8.28bp
    against 12.62bp inside the window, under the file before 0.8.5. pt-v20
    with the 0.8.5 file reads 7.16bp against 17.39bp.
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
    scenario = tf.Scenario.load(SCENARIO)
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
    #
    #    The book is read against the DEPTH window, days 50 to 74, and not
    #    against every intervention in the file. The earnings ramp the file
    #    gained in 0.8.5 runs to day 175; taken over everything, the two
    #    reading days fell past the end of this eighty-day loop, neither
    #    reading was taken, and the script died on a TypeError halfway
    #    through its report.
    depth = [item for item in scenario.interventions
             if item.target == "market.liquidity"]
    first = min(item.at for item in depth)
    last = max(item.last_day or 0 for item in depth)
    assert last + 3 < DAYS_AFTER_FORK, (
        f"the depth window ends on day {last}, too late to read the book "
        f"three days after it in a {DAYS_AFTER_FORK}-day run")
    print(f"  scenario applied   to the stress branch only, firing at "
          f"step {DAYS_BEFORE_FORK + first} of the parent's history")

    # Read the book twice: once inside the window and once after it. The
    # first says what the shock did; the second says whether it ended.
    during = after = None
    for i in range(DAYS_AFTER_FORK):
        day = DAYS_BEFORE_FORK + i
        scenario.apply(stress, i)
        trading_day(control, day, universe)
        trading_day(stress, day, universe)
        if i == (first + last) // 2:
            during = (sweep_cost_bps(control, universe),
                      sweep_cost_bps(stress, universe))
        if i == last + 3:
            after = (sweep_cost_bps(control, universe),
                     sweep_cost_bps(stress, universe))

    # 5. What actually fired, with the values it saw. Not the recipe: the
    #    trail records that depth went from N shares to 0.4N on the day it
    #    happened, the thing a reader needs afterwards.
    print("\n  INTERVENTIONS APPLIED")
    for firing in scenario.log[:6]:
        print(f"    {firing}")
    if len(scenario.log) > 6:
        print(f"    ... {len(scenario.log)} firings in total")
    report["firings"] = len(scenario.log)

    # 6. What the scenario did.
    moves = sorted(s / c - 1.0 for s, c in
                   zip(prices(stress, universe),
                       prices(control, universe), strict=True))
    median = moves[len(moves) // 2]
    report["median_move_pct"] = median * 100.0
    report["sweep_cost_bps_in_window"] = during
    report["sweep_cost_bps_after"] = after

    print("\n  WHAT IT DID")
    print(f"    median name        {median * 100:+.2f}%")
    print("    cost to buy 20k    control  stress")
    print(f"      inside the window  {during[0]:5.2f}bp  {during[1]:5.2f}bp")
    print(f"      three days after   {after[0]:5.2f}bp  {after[1]:5.2f}bp")
    print(f"    fingerprint        {scenario.fingerprint}")

    ok = True
    ok &= check("the scenario fired", bool(scenario.log))
    ok &= check("it moved the stress branch",
                prices(control, universe) != prices(stress, universe))
    # The point of this scenario: depth, not price. A liquidity shock that
    # showed up mostly in prices would mean the lever is reaching valuation,
    # which it is not supposed to.
    ok &= check("trading cost more inside the window",
                during[1] > during[0])
    # And the window ENDED. Nothing in the engine writes `avg_volume` back,
    # so the scenario does it: without that, a twenty-five day crisis quietly
    # lasted for the rest of the run and this check read the same either way.
    ok &= check("the book came back after it",
                any(firing.target == "market.liquidity"
                    and firing.operation == "release"
                    for firing in scenario.log)
                and abs(after[1] - after[0]) < abs(during[1] - during[0]))
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
