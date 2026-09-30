"""Borrowing pays the policy rate in `evaluate` and `rank`.

Until 0.8.5 `Portfolio.accrue` booked nothing unless `cash_interest` was on,
and `evaluate`, `rank` and `World` all leave it off. An agent holding more
than its net worth under the default 2x leverage cap ran a negative cash
balance for free. On the 90 graded pt-v20 histories, 1.8 times the index
beat the index by 2.33 points a year and was ahead in 64% of one-year windows
with free borrowing, against 0.21 points and 57% with the policy rate
charged. A World booked no interest at all, whatever the portfolio asked
for.

Idle cash still earns nothing unless `cash_interest` is on, and a World's own
portfolios still book nothing, so recorded World runs replay.
"""

import pytest

import tradefloor as tf
from tradefloor.baselines import BuyAndHold

UNIVERSE = tf.Universe.random(4, seed=111)


class _Levered:
    """Buys 1.8 times net worth on the first step and holds, noting the cash
    it sees at every step."""

    def __init__(self):
        self.inner = BuyAndHold(leverage=1.8, max_participation=1.0)
        self.cash = []

    def act(self, obs):
        self.cash.append((obs.day, obs.step_of_day, obs.portfolio.cash))
        return self.inner.act(obs)


def _engine():
    engine = tf.Engine(seed=3, universe=UNIVERSE)
    engine.run_days(1, record=False)
    engine.open_market()
    return engine


def test_accrue_charges_a_negative_balance_with_cash_interest_off():
    engine = _engine()
    rate = engine.macro_fields["federal_funds_rate"]
    assert rate > 0
    portfolio = tf.Portfolio(cash=1_000_000.0)
    # What buying 1.5 million of stock leaves, without a market to buy it in.
    portfolio.cash = -500_000.0
    assert not portfolio.cash_interest
    charged = portfolio.accrue(engine)
    assert charged == pytest.approx(-500_000.0 * rate / 252.0)
    assert portfolio.interest == charged
    assert portfolio.cash == pytest.approx(-500_000.0 + charged)


def test_accrue_pays_idle_cash_nothing_with_cash_interest_off():
    engine = _engine()
    portfolio = tf.Portfolio(cash=500_000.0)
    assert portfolio.accrue(engine) == 0.0
    assert portfolio.cash == 500_000.0
    assert portfolio.interest == 0.0


def test_accrue_still_pays_idle_cash_with_cash_interest_on():
    engine = _engine()
    rate = engine.macro_fields["federal_funds_rate"]
    portfolio = tf.Portfolio(cash=500_000.0, cash_interest=True)
    assert portfolio.accrue(engine) == pytest.approx(500_000.0 * rate / 252.0)


def test_evaluate_charges_a_levered_agent_for_its_overnight_borrowing():
    """With the defaults. The cash an agent sees at one day's open is below
    what it saw at the last step of the day before, by a day's interest on
    the borrowed balance, although it traded nothing in between."""
    agent = _Levered()
    tf.evaluate({"levered": agent}, seed=3, universe=UNIVERSE, days=4,
                steps_per_day=2)
    by_step = {(day, step): cash for day, step, cash in agent.cash}
    for day in (1, 2, 3):
        before = by_step[(day - 1, 1)]
        after = by_step[(day, 0)]
        assert before < 0, "the agent should be borrowing"
        assert after < before, (
            f"day {day}: cash went {before:,.2f} -> {after:,.2f} overnight; "
            f"a negative balance must pay the policy rate")


def test_a_world_books_interest_for_a_portfolio_that_asks_for_it():
    """A World never called `accrue`, so a portfolio built with
    `cash_interest=True` earned nothing and paid nothing there."""
    world = tf.World(seed=3, universe=UNIVERSE, agent=_Levered(),
                     steps_per_day=2)
    world.portfolio = tf.Portfolio(cash=1_000_000.0, max_leverage=2.0,
                                   cash_interest=True)
    world.run(3)
    assert world.portfolio.cash < 0
    assert world.portfolio.interest < 0


def test_a_worlds_own_portfolio_books_no_interest():
    """Kept as it was: a World's own portfolios leave `cash_interest` off,
    and an LLM agent's observation shows its cash, so every recorded World
    run replays only against the cash it was shown. `tf.World`'s docstring
    says so and names the two ways to charge it."""
    world = tf.World(seed=3, universe=UNIVERSE, agent=_Levered(),
                     steps_per_day=2)
    world.run(3)
    assert world.portfolio.cash < 0
    assert world.portfolio.interest == 0.0
