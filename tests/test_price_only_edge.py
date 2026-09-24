"""C4b: price-only rules on the published suite, against their line.

The long-run criteria (design repo, ``programme/longrun/CRITERIA.md``) gained
C4 on 2026-09-24: no price-only edge. C4b runs the simple rules a user
would try first through ``tf.evaluate`` on the published suite,
tf-suite-2026.1: 20 markets of 60 days, 20 companies, pt-v19, six 65-tick
steps a day, $1M at leverage 2, 8 plain markets and each of the six packaged
scenarios on 2. Each rule's excess over buy-and-hold in the same market must
be at most +5 points at the median and positive in at most 14 of the 20.

Measured 2026-09-24, median points over buy-and-hold (markets beaten):

    rule                           0.8.1           fills applied once
    mean reversion, 1 step         +94.6 (20/20)   +13.6 (18/20)  misses
    mean reversion, 1 day          +42.1 (20/20)    +0.5 (10/20)
    mean reversion, 5 days daily   -14.1  (1/20)   -15.3  (1/20)
    momentum, 1 step               -54.6  (0/20)   -70.7  (0/20)
    momentum, 1 day                -25.3  (1/20)   -42.4  (0/20)
    momentum, 5 days daily         +10.9 (19/20)    +7.1 (17/20)  misses

The one-day reversal is the suite's own spec and its +42 was the harness
alone: every harness held an agent's fills on all 65 ticks of a step. The two
rules still over the line are the market's. The one-step reversal feeds on a
tape whose 65-minute returns reverse (C4a: -0.187 against a floor of -0.05),
and five-day momentum feeds on the suite's markets opening every name far
from fair value. Both are the next preset's to fix, so they are expected
failures here, strict, so that the day one of them comes inside its line this
file says so and the expectation is removed rather than left to rot.

Slow: 180 sixty-day runs, about thirty seconds on eight threads. Set
``TRADEFLOOR_SLOW_TESTS=1``.
"""

from __future__ import annotations

import os
import statistics
from concurrent.futures import ThreadPoolExecutor

import pytest

import tradefloor as tf

pytestmark = pytest.mark.skipif(
    not os.environ.get("TRADEFLOOR_SLOW_TESTS"),
    reason="180 sixty-day runs; set TRADEFLOOR_SLOW_TESTS=1 to run them")

#: tf-suite-2026.1, as tradefloor-serve publishes it: (sim seed, roster seed,
#: packaged scenario or None).
CELLS = tuple(
    (92000 + i, 93000 + i, sc) for i, sc in enumerate(
        [None] * 8 + ["geopolitical_conflict"] * 2 + ["liquidity_crisis"] * 2
        + ["oil_price_spike"] * 2 + ["policy_regime_shift"] * 2
        + ["rate_shock"] * 2 + ["recession"] * 2, start=1))

MEDIAN_CEILING = 5.0
WINS_CEILING = 14


def _daily(kind: str) -> tf.StrategySpec:
    return tf.StrategySpec(signal={"kind": kind, "lookback_days": 5.0},
                           execution={"cadence": "daily"})


RULES = {
    "mean_reversion_1step": tf.StrategySpec.mean_reversion(lookback_days=1 / 6),
    "mean_reversion_1day": tf.StrategySpec.mean_reversion(lookback_days=1.0),
    "mean_reversion_5day_daily": _daily("mean_reversion"),
    "momentum_1step": tf.StrategySpec.momentum(lookback_days=1 / 6),
    "momentum_1day": tf.StrategySpec.momentum(lookback_days=1.0),
    "momentum_5day_daily": _daily("momentum"),
}

#: The rules pt-v19 leaves over the line, with what they read on 2026-09-24.
MISSES = {
    "mean_reversion_1step": "+13.6 points at the median, 18 of 20: the 65-minute "
                            "tape reversal (C4a), pt-v20's to fix",
    "momentum_5day_daily": "+7.1 points at the median, 17 of 20: the suite's "
                           "start-up transient in mispricing, pt-v20's to fix",
}


def _cell(cell):
    seed, roster, scenario = cell
    agents = dict(RULES, buy_and_hold=tf.StrategySpec.hold())
    cards = tf.evaluate(
        agents, seed=seed, universe=tf.Universe.random(20, seed=roster),
        scenario=tf.Scenario.load(scenario) if scenario else None,
        days=60, steps_per_day=6, ticks_per_step=65, cash=1_000_000.0,
        max_leverage=2.0, model="pt-v19")
    bh = cards["buy_and_hold"].return_pct
    return {name: cards[name].return_pct - bh for name in RULES}


@pytest.fixture(scope="module")
def excess():
    # Threads, not processes: `run_session` releases the GIL for the whole
    # session, so eight threads keep eight cores busy without re-importing
    # the package in each worker.
    with ThreadPoolExecutor(8) as pool:
        cells = list(pool.map(_cell, CELLS))
    return {name: [c[name] for c in cells] for name in RULES}


@pytest.mark.parametrize("rule", [
    pytest.param(name, marks=pytest.mark.xfail(strict=True, reason=MISSES[name]))
    if name in MISSES else name
    for name in RULES])
def test_a_price_only_rule_stays_inside_its_line(excess, rule):
    values = excess[rule]
    assert len(values) == 20
    median = statistics.median(values)
    wins = sum(v > 0 for v in values)
    assert median <= MEDIAN_CEILING and wins <= WINS_CEILING, (
        f"{rule}: median {median:+.2f} points over buy-and-hold (line "
        f"+{MEDIAN_CEILING}), beats it in {wins} of 20 (line {WINS_CEILING}); "
        f"over by {max(0.0, median - MEDIAN_CEILING):.2f} points and "
        f"{max(0, wins - WINS_CEILING)} markets")


def test_the_suite_spec_no_longer_collects_its_own_impact(excess):
    """The rule the hosted suite reported beating buy-and-hold on 20 of 20
    by a median +42 points. With its fills applied once it is a coin flip:
    measured +0.46 points and 10 of 20. Pinned tighter than the line,
    because this is the one number the harness fix was for."""
    values = excess["mean_reversion_1day"]
    assert abs(statistics.median(values)) < 3.0
    assert 6 <= sum(v > 0 for v in values) <= 14
