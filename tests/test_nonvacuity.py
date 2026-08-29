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

import tradefloor
from tradefloor.baselines import BuyAndHold, reference_agents

UNIVERSE = tradefloor.Universe.random(20, seed=7)


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
    from tradefloor.tca import Execution

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
    cf = tradefloor.flow_impact(
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
    scores = tradefloor.evaluate(reference_agents(seed=3), seed=2026,
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

    scores = tradefloor.evaluate({"idle": Idle(), "active": BuyAndHold()},
                              seed=2026, universe=UNIVERSE, days=5)
    assert scores["idle"].trades == 0
    assert scores["active"].trades > 0
    assert scores["idle"].pnl != scores["active"].pnl


# --------------------------------------------------------------------------
# The ground truth
# --------------------------------------------------------------------------


def test_every_truth_component_can_be_non_zero():
    """Every component reachable. A column of structural zeros is a lie by
    column.

    `circuit_breaker` is excluded from the dead-column check below and gets
    its own test: it only fires when a name's model price leaves the session
    band, which two ordinary days never do. It is reachable, and the test
    that proves it drives a name through the band deliberately.

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
    universe = tradefloor.Universe.random(40, seed=5)
    engine = tradefloor.Engine(seed=3, universe=universe)
    ticker = engine.tickers[0]
    for day in range(2):
        engine.open_market()
        engine.run_session(
            9, 30, 3, 390,
            news=[tradefloor.News(ticker=ticker, price_impact=0.06)] if day == 0 else None,
            order_flow={ticker: (800_000.0, 0.0)},
        )
        engine.close_market()
        engine.record(day)

    table = pa.table(engine.truth()).to_pydict()
    dead = [name for name in tradefloor.Engine.FACTORS
            if name != "circuit_breaker" and all(v == 0.0 for v in table[name])]
    assert dead == [], dead
    # And momentum specifically is zero on day one and non-zero on day two,
    # which is the shape of "not yet" rather than "never".
    day_one = [v for v, d in zip(table["momentum"], table["day"]) if d == 0]
    day_two = [v for v, d in zip(table["momentum"], table["day"]) if d == 1]
    assert all(v == 0.0 for v in day_one)
    assert any(v != 0.0 for v in day_two)


def test_the_circuit_breaker_component_fires_when_the_breaker_binds():
    """The ninth component, and the only one an ordinary day never reaches.

    When a name's model price leaves the session band the tick clamps it and
    re-derives the mispricing state from the clamped price. Until 2026-08-26
    that rewrite was booked to no component, so the columns did not
    reconstruct the move on a halted day: over one crisis window they summed
    to -0.204 against a change of -0.190 (calibration record §79).

    Driven here with a scenario violent enough to hit the band rather than
    waiting for one, because "never fires" and "has not fired yet" look the
    same from a quiet run.
    """
    pa = pytest.importorskip("pyarrow")

    universe = tradefloor.Universe.random(20, seed=5)
    scenario = (tradefloor.Scenario()
                .hold(vix=15.0, corporate_bond_yield=0.055)
                .ramp("vix", start=65.0, end=15.0, over=20, begin=5)
                .step("qe_pe_boost", before=0.0, after=-0.30, at=5))
    engine = tradefloor.Engine(seed=2024, universe=universe)
    for day in range(25):
        scenario.apply(engine, day)
        engine.run_days(1, first_day=day, record=True)

    table = pa.table(engine.truth()).to_pydict()
    fired = [v for v in table["circuit_breaker"] if v != 0.0]
    assert fired, "the breaker never bound, so this proves nothing"

    # And with it recorded, the columns reconstruct the move exactly.
    rows = list(zip(table["instrument_id"], table["mispricing_s"],
                    *(table[c] for c in tradefloor.Engine.FACTORS)))
    first = [r for r in rows if r[0] == 0]
    delta = first[-1][1] - first[0][1]
    total = sum(sum(r[2:]) for r in first[1:])
    assert delta == pytest.approx(total, abs=1e-12), (
        f"components sum to {total:+.6f} against a change of {delta:+.6f}"
    )


def test_every_column_can_differ_between_two_markets():
    # A column returning a constant would satisfy any equality test comparing
    # two runs of the same seed. Two different seeds must differ.
    a = tradefloor.Engine(seed=5, universe=UNIVERSE)
    b = tradefloor.Engine(seed=6, universe=UNIVERSE)
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

    one = tradefloor.Engine(seed=1, universe=UNIVERSE)
    two = tradefloor.Engine(seed=2, universe=UNIVERSE)
    one.run_days(1, record=False)
    two.run_days(1, record=False)
    assert one.prices() != two.prices()


def test_replay_can_fail():
    # `replay(...) == original` proves nothing if replay returned the engine
    # it was handed, or if both were empty. A different seed must not match.
    import json

    engine = tradefloor.Engine(seed=42, universe=UNIVERSE)
    engine.open_market()
    engine.run_session(9, 30, 3, 60)
    engine.close_market()

    log = json.loads(json.dumps(engine.order_log))
    assert log, "an empty log would replay anything"
    assert tradefloor.replay(log, seed=42, universe=UNIVERSE).prices() == engine.prices()
    assert tradefloor.replay(log, seed=43, universe=UNIVERSE).prices() != engine.prices()


# --------------------------------------------------------------------------
# The measurements
# --------------------------------------------------------------------------


def test_shortfall_can_be_both_signs():
    """A shortfall stuck at zero would satisfy every loose inequality near it.

    Both directions are reachable, and which one you get is a fact about the
    trade rather than about the code.

    Measured ACROSS SEEDS rather than on one. This test used to pin seed 2026,
    where a round trip recouped; it stopped recouping there when a stepped day
    was fixed to stop re-opening the market at every step, and the test failed
    while the phenomenon it names was as true as ever -- 6 of 8 seeds still
    recoup. Pinning one seed pins the seed, which is a lesson this repository
    has now learned twice.
    """
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

    positive = negative = 0
    for seed in (2026, 1, 2, 3, 4, 5, 7, 11):
        held = tradefloor.tca.analyse(Buyer(), seed=seed, universe=UNIVERSE,
                                   days=1, steps_per_day=6)
        traded = tradefloor.tca.analyse(RoundTrip(), seed=seed, universe=UNIVERSE,
                                     days=1, steps_per_day=6)
        # A one-way buyer always pays: they moved the price and never sold
        # into it. That direction is structural, so it is asserted per seed.
        assert held.shortfall() > 0, f"seed {seed}: a one-way buyer got paid"
        positive += 1
        if traded.shortfall() < 0:
            negative += 1

    assert positive, "no positive shortfall observed"
    assert negative, (
        "no round trip recouped on any seed -- either impact stopped "
        "persisting or the exit leg stopped being priced against the "
        "untraded world"
    )


def test_the_stylised_facts_are_not_all_within_range():
    # If every statistic matched, `compare_to_real_markets` would be doing no
    # work and the module's honesty would be untested. Two are known
    # mismatches; that must stay visible.
    facts = tradefloor.facts.measure(
        seed=3, universe=tradefloor.Universe.random(40, seed=111), days=120)
    verdicts = tradefloor.facts.compare_to_real_markets(facts)
    assert any(not v["matches"] for v in verdicts.values())
    assert any(v["matches"] for v in verdicts.values())
