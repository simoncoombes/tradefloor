"""Borrowing pays the policy rate in `evaluate` and `rank`.

Until 0.8.5 `Portfolio.accrue` booked nothing unless `cash_interest` was on,
and `evaluate`, `rank` and `World` all leave it off. An agent holding more
than its net worth under the default 2x leverage cap ran a negative cash
balance for free. On the 90 graded pt-v20 histories, 1.8 times the index
beat the index by 2.33 points a year and was ahead in 64% of one-year windows
with free borrowing, against 0.21 points and 57% with the policy rate
charged. A World booked no interest at all, whatever the portfolio asked
for.

Idle cash still earns nothing unless `cash_interest` is on. Decision 11
(2026-09-26) made a World's own portfolios pay for borrowing too, and gave
`evaluate`, `rank` and `World` the opt-out `margin_interest=False`.
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


def test_a_worlds_own_portfolio_pays_for_its_borrowing():
    """Decision 11: margin is charged at the published policy rate by default
    in World too. Until then a World's own portfolios borrowed for free, so a
    levered agent's score there was not the score `evaluate` gave it."""
    world = tf.World(seed=3, universe=UNIVERSE, agent=_Levered(),
                     steps_per_day=2)
    world.run(3)
    assert world.margin_interest
    assert world.portfolio.cash < 0
    assert world.portfolio.interest < 0
    assert "margin_interest" not in world.summary()


def test_a_world_charges_what_evaluate_charges():
    """The same agent, market and cadence: the interest a World books is the
    interest `evaluate` books, and the market under both is unchanged."""
    agent = _Levered()
    card = tf.evaluate({"levered": agent}, seed=3, universe=UNIVERSE, days=3,
                       steps_per_day=2)["levered"]
    world = tf.World(seed=3, universe=UNIVERSE, agent=_Levered(),
                     steps_per_day=2)
    world.run(3)
    assert world.portfolio.cash < 0
    assert world.summary()["final_net_worth"] == pytest.approx(
        card.final_net_worth, rel=1e-12)


def test_margin_interest_false_borrows_for_free_and_says_so():
    """The opt-out, in all three. It changes cash and scores, never prices."""
    free = tf.World(seed=3, universe=UNIVERSE, agent=_Levered(),
                    steps_per_day=2, margin_interest=False)
    charged = tf.World(seed=3, universe=UNIVERSE, agent=_Levered(),
                       steps_per_day=2)
    free.run(3)
    charged.run(3)
    assert free.portfolio.cash < 0
    assert free.portfolio.interest == 0.0
    assert charged.portfolio.interest < 0
    assert free.digest() == charged.digest()
    assert free.summary()["margin_interest"] is False
    assert free.manifest().agent_access == {"margin_interest": False}
    (arm,) = free.fork("arm")
    assert arm.margin_interest is False

    cards = {
        flag: tf.evaluate({"levered": _Levered()}, seed=3, universe=UNIVERSE,
                          days=3, steps_per_day=2,
                          margin_interest=flag)["levered"]
        for flag in (True, False)}
    assert cards[False].pnl > cards[True].pnl
    assert cards[False].margin_interest is False
    assert "free-borrowing" in repr(cards[False])
    assert "free-borrowing" not in repr(cards[True])

    ranked = {
        flag: tf.rank(lambda: {"levered": _Levered()}, seeds=[3, 4],
                      universe=UNIVERSE, days=3, steps_per_day=2,
                      margin_interest=flag)
        for flag in (True, False)}
    paid = ranked[True].as_dict()["agents"]["levered"]
    free_rank = ranked[False].as_dict()["agents"]["levered"]
    assert free_rank != paid


def test_a_portfolio_built_without_margin_interest_borrows_for_free():
    engine = _engine()
    portfolio = tf.Portfolio(cash=1_000_000.0, margin_interest=False)
    portfolio.cash = -500_000.0
    assert portfolio.accrue(engine) == 0.0
    assert portfolio.cash == -500_000.0


@pytest.mark.parametrize("bad", [None, "False", "no", 0, 1])
def test_the_interest_switches_must_be_true_or_false(bad):
    """`bool("False")` is True and `bool(None)` is False, so a string or None
    would have run as something the caller did not write. Every route that
    takes the switches refuses it before a market runs."""
    for name in ("margin_interest", "cash_interest"):
        with pytest.raises(tf.ValidationError, match=f"{name} must be True or False"):
            tf.Portfolio(cash=1e6, **{name: bad})
        with pytest.raises(tf.ValidationError, match=f"{name} must be True or False"):
            tf.evaluate({"b": _Levered()}, seed=3, universe=UNIVERSE, days=1,
                        **{name: bad})
    with pytest.raises(tf.ValidationError, match="margin_interest must be True or False"):
        tf.World(seed=3, universe=UNIVERSE, agent=_Levered(),
                 margin_interest=bad)
    with pytest.raises(tf.ValidationError, match="margin_interest must be True or False"):
        tf.rank(lambda: {"b": _Levered()}, seeds=[1], universe=UNIVERSE,
                days=1, margin_interest=bad)
