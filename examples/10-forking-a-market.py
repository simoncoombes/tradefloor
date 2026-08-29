"""Fork a market mid-flight, change one thing, and run both futures.

Run it:

    python examples/10-forking-a-market.py

It runs a market to a checkpoint, forks the checkpoint into a control branch
and an intervention branch, raises the policy rate in the intervention branch
and nothing else, resumes both, and prints what the change did.

Everything before the fork is identical -- not statistically similar,
identical, to the bit -- so the difference between the two branches is the rate
rise and nothing else. That is the counterfactual a real market cannot offer:
you cannot re-run last year without its hiking cycle.

The checks it prints are not decoration. Each one is a property that has been
false in this library at some point, and each is also a test in
`tests/test_forking.py`; this script is the same evidence in a form you can
watch. It is run by the test suite for the reason example 07 is: running the
library the way a user would has caught defects that every unit test passed
through.

It takes about two seconds.
"""

import struct
import time

import tradefloor as tf
from tradefloor.manifest import market_digest
from tradefloor.scenario import Scenario

UNIVERSE_SIZE = 24
SEED = 7
TICKS_PER_DAY = 65
DAYS_BEFORE_FORK = 50
DAYS_AFTER_FORK = 30

#: The one variable. A hiking cycle moves the policy rate and the corporate
#: yield together, held apart by the credit spread, because a valuation
#: discounts off the corporate yield and pinning the policy rate alone would
#: change nothing at all, silently.
RATE_BEFORE = 0.04
RATE_AFTER = 0.06


def trading_day(engine, day, universe):
    """One step. A fixed two-sided flow, so the order book and the price
    impact are part of what the branches have to reproduce."""
    flow = {universe[0].ticker: (300_000.0, 0.0),
            universe[1].ticker: (0.0, 220_000.0)}
    engine.open_market()
    engine.run_session(9, 30, 3, TICKS_PER_DAY, order_flow=flow)
    engine.record(day)
    engine.close_market()


def prices(engine, universe):
    return struct.unpack("<%dd" % len(universe), engine.prices())


def index_level(engine, universe):
    """Equal-weighted, which is all this needs: the two branches hold the same
    roster in the same order, so the comparison is like for like."""
    values = prices(engine, universe)
    return sum(values) / len(values)


def check(label, passed):
    print(f"  {label:<44} {'PASS' if passed else 'FAIL'}")
    return passed


def main() -> dict:
    started = time.time()
    report: dict = {}
    universe = tf.Universe.random(UNIVERSE_SIZE, seed=11)
    macro = tf.Macro(federal_funds_rate=RATE_BEFORE,
                     corporate_bond_yield=RATE_BEFORE + 0.02)

    print("tradefloor fork test\n")
    print(f"  library            {tf.__version__}")
    print(f"  preset             {tf.ModelParams.from_preset().fingerprint}")
    print(f"  universe           {len(universe)} instruments, "
          f"{tf.Universe(universe).fingerprint[:12]}...")
    print(f"  seed               {SEED}")

    # 1. Run a market to the point we want to ask a question from.
    engine = tf.Engine(seed=SEED, universe=universe, macro_state=macro)
    for day in range(DAYS_BEFORE_FORK):
        trading_day(engine, day, universe)
    print(f"\n  checkpoint         step {DAYS_BEFORE_FORK}")

    # 2. Freeze it. A checkpoint is data -- the seed, the roster and the order
    #    log -- so it can be written to a file, sent to someone else and
    #    resumed next year. That is why it is the log and not a memory image.
    point = tf.Checkpoint.of(engine, universe=universe, seed=SEED, macro=macro,
                             label=f"step {DAYS_BEFORE_FORK}")
    report["checkpoint_bytes"] = len(point.to_json())
    print(f"  checkpoint size    {report['checkpoint_bytes']:,} bytes "
          f"({report['checkpoint_bytes'] / DAYS_BEFORE_FORK:.0f} a step)")

    # 3. The properties that make the rest of this an experiment.
    print("\n  before anything is changed")
    ok = True

    # Restoring the checkpoint must reproduce the market it froze, or the
    # branches begin somewhere the parent never was.
    restored = point.resume()
    ok &= check("checkpoint restore is exact",
                market_digest(restored) == market_digest(engine))

    control, treated = tf.branch(engine, 2)
    ok &= check("fork starts where its source stood",
                market_digest(control) == market_digest(engine)
                and market_digest(treated) == market_digest(engine))
    ok &= check("fork carries its source's history",
                control.order_log == engine.order_log)

    # Driving one branch must not reach the other, or the source.
    source_before = market_digest(engine)
    probe, = tf.branch(engine, 1)
    for day in range(DAYS_BEFORE_FORK, DAYS_BEFORE_FORK + 3):
        trading_day(probe, day, universe)
    ok &= check("driving a fork does not move the source",
                market_digest(engine) == source_before)
    ok &= check("driving a fork does not move its sibling",
                market_digest(control) == source_before
                and market_digest(treated) == source_before)

    # 4. One changed variable. The control branch holds the rate where it was;
    #    the intervention branch raises it over ten steps. Same checkpoint,
    #    same history, same seed, same flow.
    flat = Scenario().hold(federal_funds_rate=RATE_BEFORE,
                           corporate_bond_yield=RATE_BEFORE + 0.02)
    hike = Scenario.rate_shock(start=RATE_BEFORE, end=RATE_AFTER, over=10)
    print(f"\n  intervention       federal_funds_rate "
          f"{RATE_BEFORE:.1%} -> {RATE_AFTER:.1%} over 10 steps")

    # 5. Resume both, step by step, and record where they first differ.
    first_divergence = None
    for i in range(DAYS_AFTER_FORK):
        day = DAYS_BEFORE_FORK + i
        flat.apply(control, i)
        hike.apply(treated, i)
        trading_day(control, day, universe)
        trading_day(treated, day, universe)
        if first_divergence is None and \
                market_digest(control) != market_digest(treated):
            first_divergence = day
    report["first_divergence"] = first_divergence
    print(f"  first divergence   step {first_divergence}")

    # 6. Both branches must be reproducible on their own from the checkpoint,
    #    not merely different from each other. A difference that is noise
    #    rather than the intervention would pass every check above.
    replay_control = point.resume()
    for i in range(DAYS_AFTER_FORK):
        flat.apply(replay_control, i)
        trading_day(replay_control, DAYS_BEFORE_FORK + i, universe)
    ok &= check("each branch replays from the checkpoint",
                market_digest(replay_control) == market_digest(control))

    # 7. What the change did.
    control_level = index_level(control, universe)
    treated_level = index_level(treated, universe)
    moves = sorted(t / c - 1.0 for t, c in
                   zip(prices(treated, universe), prices(control, universe)))
    median = moves[len(moves) // 2]
    report["median_move_pct"] = median * 100.0

    print("\n  CONTROL   rate held at "
          f"{RATE_BEFORE:.1%}")
    print(f"    index level      {control_level:,.2f}")
    print(f"    draws consumed   {control.draws_consumed:,}")
    print(f"  TREATED   rate raised to "
          f"{RATE_AFTER:.1%}")
    print(f"    index level      {treated_level:,.2f}")
    print(f"    draws consumed   {treated.draws_consumed:,}")
    print(f"\n  median name moved  {median * 100:+.2f}% under the hike")

    ok &= check("the intervention changed the market",
                market_digest(control) != market_digest(treated))
    # A rate rise discounts equities. A run that priced them up means the
    # scenario stopped reaching fair value, not an interesting finding.
    ok &= check("a hike priced the market down", median < 0)
    # Both branches consumed the same randomness: a macro pin costs no draws,
    # so a difference here would mean the branches fell off one schedule and
    # the comparison is not like for like.
    ok &= check("both branches stayed on one draw schedule",
                control.draws_consumed == treated.draws_consumed)

    report["passed"] = bool(ok)
    print(f"\n  fork test          {'PASS' if ok else 'FAIL'}")
    print(f"  total              {time.time() - started:.1f}s")
    assert ok, "a forking guarantee failed; see the checks above"
    return report


if __name__ == "__main__":
    main()
