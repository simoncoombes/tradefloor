"""The positive half of every claim this library makes by absence.

Five separate defects this week were the same shape: a check whose only signal
was that something did NOT happen, satisfied equally well by nothing happening
at all.

- a test double copied from the interface it was meant to verify, so the two
  agreed with each other and neither had met the real module
- a signature helper that returned an empty list for every callable, which
  failed loudly only because the comparison happened to run that way round
- a determinism gate dispatched against the wrong commit, agreeing with itself
  on all five platforms
- a NaN-blind comparator reporting identical state as different
- an end-to-end test asserting "no downgrade warning appeared", which is
  equally true when nothing ever calls the engine

Every one of them was green. So this module asserts the OTHER side: that the
mechanisms behind those absences are live, reachable, and capable of producing
a non-empty answer. A failure here means an assertion elsewhere has quietly
become decoration.
"""

import struct

import pytest

import pretium
from pretium.baselines import BuyAndHold, reference_agents

UNIVERSE = pretium.Universe.random(20, seed=7)


def column(engine, field):
    raw = engine.column(field)
    return list(struct.unpack("<%dd" % (len(raw) // 8), raw))


# --------------------------------------------------------------------------
# The counterfactual
# --------------------------------------------------------------------------


def test_untouched_moved_can_be_non_empty():
    """Empty is only meaningful if non-empty is reachable.

    It reports instruments the trader never touched whose price moved anyway.
    Order flow consumes no draws, so it is empty for a real counterfactual --
    but a method returning an unconditional empty list would satisfy every
    test that uses it. Constructed here from two worlds that genuinely differ.
    """
    from pretium.tca import Execution

    execution = Execution(
        tickers=["AAA", "BBB"], fills=[],
        baseline_path=[[10.0, 20.0]], actual_path=[[10.0, 21.0]],
        seed=1, portfolio=None, steps=1,
    )
    assert execution.untouched_moved() == ["BBB"]
    assert execution.moved() != {}


def test_a_real_counterfactual_moves_the_traded_name():
    # The positive counterpart to "nothing else moved". If the flow were
    # silently dropped, nothing at all would move, and the no-leak assertion
    # would pass while comparing two identical worlds.
    cf = pretium.flow_impact(
        seed=42, universe=UNIVERSE,
        order_flow={UNIVERSE[0].ticker: (6e6, 0.0)}, ticks=390,
    )
    assert cf.untouched_moved() == []
    assert cf.impact_bps[0] != 0.0


# --------------------------------------------------------------------------
# The agents
# --------------------------------------------------------------------------


def test_every_reference_agent_actually_trades():
    # `errors == []` is satisfied by an agent that never acted.
    scores = pretium.evaluate(reference_agents(seed=3), seed=2026,
                              universe=UNIVERSE, days=5)
    for name, card in scores.items():
        assert card.errors == [], (name, card.errors)
        assert card.trades > 0, name
        assert card.turnover > 0.0, name


def test_an_agent_that_does_nothing_is_visibly_different():
    # And the harness must be able to TELL. If a silent agent scored the same
    # as a trading one, nothing above would mean anything.
    class Idle:
        def act(self, obs):
            return {}

    scores = pretium.evaluate({"idle": Idle(), "active": BuyAndHold()},
                              seed=2026, universe=UNIVERSE, days=5)
    assert scores["idle"].trades == 0
    assert scores["active"].trades > 0
    assert scores["idle"].pnl != scores["active"].pnl


# --------------------------------------------------------------------------
# The ground truth
# --------------------------------------------------------------------------


def test_every_truth_component_can_be_non_zero():
    """All seven reachable. A column of structural zeros is a lie by column.

    `short_squeeze_effect` was exactly that until the universe generator
    started drawing short interest: present in the schema, named in the
    README, and unreachable in any generated universe.
    """
    pa = pytest.importorskip("pyarrow")

    # TWO days, not one, and the reason is a finding in itself. `momentum` is
    # the carry from the previous close, so it is structurally zero for the
    # whole of day one -- there is no previous close to carry from. A
    # single-day version of this test reported it dead alongside
    # short_squeeze_effect, which needs both a heavily shorted name and a 3%
    # move and simply had not fired yet.
    #
    # Neither was a dead column; the test was too short. Worth the comment
    # because "this component is always zero" and "you did not run long enough
    # to see it" look identical from one day of data.
    universe = pretium.Universe.random(40, seed=5)
    engine = pretium.Engine(seed=3, universe=universe)
    ticker = engine.tickers[0]
    for day in range(2):
        engine.open_market()
        engine.run_session(
            9, 30, 3, 390,
            news=[pretium.News(ticker=ticker, price_impact=0.06)] if day == 0 else None,
            order_flow={ticker: (800_000.0, 0.0)},
        )
        engine.close_market()
        engine.record(day)

    table = pa.table(engine.truth()).to_pydict()
    dead = [name for name in pretium.Engine.FACTORS
            if all(v == 0.0 for v in table[name])]
    assert dead == [], dead
    # And momentum specifically is zero on day one and non-zero on day two,
    # which is the shape of "not yet" rather than "never".
    day_one = [v for v, d in zip(table["momentum"], table["day"]) if d == 0]
    day_two = [v for v, d in zip(table["momentum"], table["day"]) if d == 1]
    assert all(v == 0.0 for v in day_one)
    assert any(v != 0.0 for v in day_two)


def test_every_column_can_differ_between_two_markets():
    # A column returning a constant would satisfy any equality test comparing
    # two runs of the same seed. Two different seeds must differ.
    a = pretium.Engine(seed=5, universe=UNIVERSE)
    b = pretium.Engine(seed=6, universe=UNIVERSE)
    a.run_days(2, record=False)
    b.run_days(2, record=False)

    varying = ("price", "volume", "market_cap", "mispricing_s",
               "garch_variance", "last_daily_return", "mispricing_momentum")
    identical = [f for f in varying if column(a, f) == column(b, f)]
    assert identical == [], identical


# --------------------------------------------------------------------------
# The determinism claim
# --------------------------------------------------------------------------


def test_the_known_answer_digest_responds_to_the_market():
    """The gate exists to notice a difference.

    A digest over an empty buffer, or over a constant, would agree across
    every platform forever and certify nothing.
    """
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parent))
    import known_answer

    assert len(known_answer.known_answer_digest()) == 64
    assert len(known_answer.known_answer_buffer()) > 1000

    one = pretium.Engine(seed=1, universe=UNIVERSE)
    two = pretium.Engine(seed=2, universe=UNIVERSE)
    one.run_days(1, record=False)
    two.run_days(1, record=False)
    assert one.prices() != two.prices()


def test_replay_can_fail():
    # `replay(...) == original` proves nothing if replay returned the engine
    # it was handed, or if both were empty. A different seed must not match.
    import json

    engine = pretium.Engine(seed=42, universe=UNIVERSE)
    engine.open_market()
    engine.run_session(9, 30, 3, 60)
    engine.close_market()

    log = json.loads(json.dumps(engine.order_log))
    assert log, "an empty log would replay anything"
    assert pretium.replay(log, seed=42, universe=UNIVERSE).prices() == engine.prices()
    assert pretium.replay(log, seed=43, universe=UNIVERSE).prices() != engine.prices()


# --------------------------------------------------------------------------
# The measurements
# --------------------------------------------------------------------------


def test_shortfall_can_be_both_signs():
    # A shortfall stuck at zero would satisfy every loosely written inequality
    # around it. Both directions are reachable, and which one you get is a
    # fact about the trade rather than about the code.
    class Buyer:
        def act(self, obs):
            ticker = obs.tickers[0]
            return {ticker: 0.01 * obs.avg_volume(ticker)} if obs.step == 0 else {}

    class RoundTrip:
        def act(self, obs):
            ticker = obs.tickers[0]
            if obs.step == 0:
                return {ticker: 0.01 * obs.avg_volume(ticker)}
            if obs.step == 3:
                return {ticker: -obs.position(ticker)}
            return {}

    held = pretium.tca.analyse(Buyer(), seed=2026, universe=UNIVERSE, days=1,
                               steps_per_day=6)
    traded = pretium.tca.analyse(RoundTrip(), seed=2026, universe=UNIVERSE,
                                 days=1, steps_per_day=6)
    assert held.shortfall() > 0
    assert traded.shortfall() < 0


def test_the_stylised_facts_are_not_all_within_range():
    # If every statistic matched, `compare_to_real_markets` would be doing no
    # work and the module's honesty would be untested. Two are known
    # mismatches; that must stay visible.
    facts = pretium.facts.measure(
        seed=3, universe=pretium.Universe.random(40, seed=111), days=120)
    verdicts = pretium.facts.compare_to_real_markets(facts)
    assert any(not v["matches"] for v in verdicts.values())
    assert any(v["matches"] for v in verdicts.values())
