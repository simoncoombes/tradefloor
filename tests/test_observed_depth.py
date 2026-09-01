"""The depth an agent is shown, and the order size it is allowed.

`market.liquidity` is the only scenario lever that touches execution, and
`liquidity_crisis` is built around it: quoted depth to 40%, so every ladder
level thins and the same trade costs more. Its own header says an evaluation
reading only the price series will score an agent as though it traded for
free.

The harness was doing a version of that. `World._adv` was built once at
construction from the `Instrument` list and never read again, and
`evaluate` built its `adv` outside the day loop that applies the scenario.
So under a depth shock the engine's book thinned and two things did not
move: what the agent was shown, and the participation cap its orders were
clipped against. Measured on `Universe.random(6, seed=11)` at x0.40, the
engine column went 543,983 -> 217,593 and the cap stayed at 27,199 in both
arms. The clip that exists to keep an order realistic was sized against a
market that no longer existed.

`Observation.book()` on the same object DID show the thinned ladder, so the
observation contradicted itself.
"""

from __future__ import annotations

import struct

import pytest

import tradefloor as tf
from tradefloor.counterfactual import World
from tradefloor.harness import Observation
from tradefloor.integrations.common import (Action, Decision, orders_from,
                                            serialize_observation)

SEED = 7
#: The multiplier `liquidity_crisis` applies to `market.liquidity`.
DEPTH = 0.40
#: Its window, counted from where the scenario is applied.
FIRES_AT = 50
ENDS_AFTER = 25


def universe(n: int = 6):
    return list(tf.Universe.random(n, seed=11))


class Flat:
    """No trades. The subject here is what the agent is SHOWN."""

    def act(self, obs) -> dict[str, float]:
        return {}


def world(uni, **over) -> World:
    kwargs = dict(seed=SEED, universe=uni, agent=Flat(), cash=50_000_000.0,
                  steps_per_day=6, ticks_per_step=65,
                  pins={"federal_funds_rate": 0.04,
                        "corporate_bond_yield": 0.055})
    kwargs.update(over)
    return World(**kwargs)


def observation(w: World) -> Observation:
    prices = list(struct.unpack("<%dd" % len(w.engine.tickers),
                                w.engine.prices()))
    return Observation(w.step, w.day, w.engine.tickers, prices, w.portfolio,
                       w.engine, w._adv, w.steps_per_day)


def first_asset(w: World, max_participation: float = 0.05) -> dict:
    return serialize_observation(
        observation(w), history=[observation(w).prices], fundamentals={},
        max_participation=max_participation)["assets"][0]


def engine_depth(w: World) -> list[float]:
    raw = w.engine.column("avg_volume")
    return list(struct.unpack("<%dd" % (len(raw) // 8), raw))


def crisis_pair(days_after: int = 60):
    """A fork with `liquidity_crisis` on one arm, run past its start."""
    uni = universe()
    root = world(uni)
    root.run(days=5)
    control, crisis = root.fork("control", "crisis")
    crisis.apply(tf.Scenario.load("liquidity_crisis"))
    control.run(days=days_after)
    crisis.run(days=days_after)
    return control, crisis, uni


# --------------------------------------------------------------------------
# What the agent is shown
# --------------------------------------------------------------------------

def test_a_depth_shock_reaches_the_observation():
    """The engine thinned the book, so the figure the agent reads thins too.

    `Observation.avg_volume` is documented as public information a real
    trader has. A trader in a market whose quoted depth fell by 60% has
    that information.
    """
    control, crisis, _ = crisis_pair()

    shown = first_asset(crisis)["avg_daily_volume"]
    base = first_asset(control)["avg_daily_volume"]
    assert shown == pytest.approx(base * DEPTH, rel=1e-9)
    assert engine_depth(crisis)[0] == pytest.approx(shown, rel=1e-9), (
        "what the agent is shown must be what the engine holds")


def test_the_participation_cap_follows_the_depth():
    """The half that is not a matter of taste.

    The cap exists to stop an agent sending an order the market cannot
    absorb. Sized against pre-crisis depth it permits exactly the order the
    crisis made unrealistic, and the agent never learns it was unrealistic
    because the fill is simply worse.
    """
    control, crisis, _ = crisis_pair()
    symbol = crisis.engine.tickers[0]

    def allowed(arm):
        decision = Decision([Action(symbol, "BUY", 60_000.0)])
        orders, _ = orders_from(decision, observation(arm),
                                max_participation=0.05)
        return orders.get(symbol, 0.0)

    assert first_asset(crisis)["max_order_shares"] == pytest.approx(
        first_asset(control)["max_order_shares"] * DEPTH, rel=1e-9)
    assert allowed(crisis) == pytest.approx(allowed(control) * DEPTH,
                                            rel=1e-9)
    assert allowed(crisis) < allowed(control)


def test_the_observation_agrees_with_its_own_order_book():
    """It used to contradict itself: `book()` thinned, `avg_volume` did not.

    Both come off the same Observation, and an agent reading one and
    sizing against the other is reasoning about two different markets.
    """
    _, crisis, _ = crisis_pair()
    obs = observation(crisis)
    symbol = crisis.engine.tickers[0]
    assert obs.avg_volume(symbol) == pytest.approx(engine_depth(crisis)[0],
                                                   rel=1e-9)
    assert obs.book(symbol) is not None


def test_the_shock_ends_and_the_depth_comes_back():
    """The window closes, and the figure the agent reads closes with it.

    Nothing in the engine writes this column, so the scenario restores it.
    A re-read that only ever went one way would leave the agent in a
    crisis that had ended.
    """
    control, crisis, _ = crisis_pair(days_after=FIRES_AT + ENDS_AFTER + 8)
    assert first_asset(crisis)["avg_daily_volume"] == pytest.approx(
        first_asset(control)["avg_daily_volume"], rel=1e-9)


# --------------------------------------------------------------------------
# What does not change, which is what decides the blast radius
# --------------------------------------------------------------------------

def test_a_run_under_no_scenario_shows_the_universe_figure():
    """Every recording, digest and fixture made before this is unaffected.

    The engine's column starts equal to the instrument list and nothing in
    the engine writes it, so with no scenario in play the re-read returns
    the value the old code held. This is the test that says so.
    """
    uni = universe()
    plain = world(uni)
    plain.run(days=30)
    assert plain._adv == pytest.approx([i.avg_volume for i in uni], rel=1e-12)
    assert engine_depth(plain) == pytest.approx(
        [i.avg_volume for i in uni], rel=1e-12)


def test_the_control_arm_of_a_scenario_fork_is_untouched():
    """Only the arm carrying the scenario moves."""
    control, _, uni = crisis_pair()
    assert control._adv == pytest.approx([i.avg_volume for i in uni],
                                         rel=1e-12)


def test_evaluate_shows_the_same_figure_the_world_does():
    """`evaluate` had the identical defect, three lines apart in a
    different file, and a fix to one is not a fix to the other."""
    uni = universe()
    seen: list[float] = []

    class Watcher:
        def act(self, obs) -> dict[str, float]:
            seen.append(obs.avg_volume(obs.tickers[0]))
            return {}

    tf.evaluate({"watch": Watcher()}, seed=SEED, universe=uni,
                days=FIRES_AT + 6, steps_per_day=2, ticks_per_step=20,
                scenario=tf.Scenario.load("liquidity_crisis"))

    before, after = seen[0], seen[-1]
    assert before == pytest.approx(uni[0].avg_volume, rel=1e-9)
    assert after == pytest.approx(before * DEPTH, rel=1e-9), (
        "evaluate must show the shocked depth once the window opens")


def test_evaluate_with_no_scenario_never_re_reads():
    """The re-read is guarded on there being a scenario, so an ordinary
    run does not pay for a column copy a day."""
    uni = universe()
    seen: list[float] = []

    class Watcher:
        def act(self, obs) -> dict[str, float]:
            seen.append(obs.avg_volume(obs.tickers[0]))
            return {}

    tf.evaluate({"watch": Watcher()}, seed=SEED, universe=uni, days=8,
                steps_per_day=2, ticks_per_step=20)
    assert seen, "the agent was never asked"
    assert seen == pytest.approx([uni[0].avg_volume] * len(seen),
                                 rel=1e-12)
