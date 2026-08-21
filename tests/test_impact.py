"""Market impact is emergent, and this is the file that proves it.

The claim is that a large order pays worse prices because it CONSUMED LEVELS,
not because a slippage coefficient said large orders cost more. That is a
falsifiable claim about a mechanism, and this checks the mechanism rather than
the marketing: walk a real ladder, and show the average price rises exactly
when the order crosses a level and not before.
"""

import pytest

import pretium


def book_after_a_session(n=6, seed=3, ticks=30):
    universe = pretium.Universe.random(n, seed=2)
    engine = pretium.Engine(seed=seed, universe=universe)
    engine.open_market()
    engine.run_session(9, 30, 3, ticks)
    return engine, engine.book(engine.tickers[0])


def test_a_bigger_order_never_pays_a_better_average_price():
    """Monotonic in size. No exceptions, including inside a single level.

    Measured on the ladder 17.83x20,711 / 17.84x15,931 / 17.85x12,944 / ...:
    1,000 and 20,000 shares both average 17.83 because both fit inside the top
    level, and 21,000 averages 17.83014 because a thousand shares had to reach
    the next one. That is the mechanism, visible.
    """
    _, book = book_after_a_session()
    sizes = [100, 1_000, 20_000, 21_000, 36_000, 55_000, 80_000, 104_000]
    averages = []
    for size in sizes:
        cost = book.sweep_cost("buy", size)
        assert cost is not None, size
        averages.append(cost.average_price)
    for smaller, larger in zip(averages, averages[1:]):
        assert larger >= smaller


def test_the_average_price_rises_exactly_when_a_level_is_crossed():
    # Within a level, size is free. Crossing one is not. If impact came from a
    # coefficient on size this test would fail, because the price would creep
    # up inside the level too.
    _, book = book_after_a_session()
    top = book.price_levels("sell", 1)[0]
    inside = book.sweep_cost("buy", top.quantity)
    just_over = book.sweep_cost("buy", top.quantity + 1_000)
    assert inside.average_price == top.price
    assert inside.worst_price == top.price
    assert just_over.average_price > inside.average_price
    assert just_over.worst_price > inside.worst_price


def test_an_order_cannot_fill_beyond_the_displayed_depth():
    # Displayed depth is executable depth, and it is also the ceiling. An
    # order for twice the book gets the book.
    _, book = book_after_a_session()
    depth = book.depth("sell")
    huge = book.sweep_cost("buy", depth * 10)
    assert huge.filled == pytest.approx(depth)
    # And asking for more than that changes nothing -- not the fill, not the
    # price. A model with a slippage coefficient would keep charging.
    huger = book.sweep_cost("buy", depth * 100)
    assert huger.filled == huge.filled
    assert huger.average_price == huge.average_price


def test_the_worst_price_is_the_last_level_touched():
    _, book = book_after_a_session()
    levels = book.price_levels("sell", 3)
    size = sum(level.quantity for level in levels[:2]) + 1
    cost = book.sweep_cost("buy", size)
    assert cost.worst_price == levels[2].price
    assert levels[0].price <= cost.average_price <= levels[2].price


def test_both_sides_of_the_book_charge_for_size():
    # Selling into the bids has to cost too, or the asymmetry would be an
    # arbitrage the model was handing out.
    _, book = book_after_a_session()
    depth = book.depth("buy")
    small = book.sweep_cost("sell", 100)
    large = book.sweep_cost("sell", depth * 0.9)
    # A seller wants a HIGH average price, so worse means lower.
    assert large.average_price < small.average_price
    assert large.worst_price < small.worst_price


def test_sizing_by_participation_is_what_makes_impact_comparable():
    """The unit that actually predicts cost.

    A flat share count means different things in different instruments -- the
    same 20,000 shares is nothing in one book and the whole ladder in another.
    This is why `Observation.participation` exists and why the baselines size
    by ADV rather than by share count.
    """
    universe = pretium.Universe.random(12, seed=2)
    engine = pretium.Engine(seed=3, universe=universe)
    engine.open_market()
    engine.run_session(9, 30, 3, 30)

    flat_quantity = 20_000
    consumed = []
    for ticker in engine.tickers:
        book = engine.book(ticker)
        depth = book.depth("sell")
        if depth > 0:
            consumed.append(min(flat_quantity, depth) / depth)
    # The same order eats wildly different fractions of different books.
    assert max(consumed) > 4 * min(consumed)


# --------------------------------------------------------------------------
# The quote and the fill are the same book
# --------------------------------------------------------------------------


def test_sweep_cost_predicts_exactly_what_the_portfolio_pays():
    """Two surfaces read the same book, so they must not disagree.

    `sweep_cost` is what a caller asks BEFORE trading -- what would this cost?
    `Portfolio.execute` is what they actually pay. If those drifted apart, an
    agent could size a trade against one number and be filled at another, and
    every impact measurement built on the quote would be describing a market
    nobody traded in.

    Checked at sizes either side of the first level, so it covers both the
    case that rests inside the touch and the case that walks the book.
    """
    universe = pretium.Universe.random(6, seed=5)
    ticker = universe[0].ticker
    walked = 0
    for size in (1_000.0, 100_000.0, 5_000_000.0, 50_000_000.0):
        engine = pretium.Engine(seed=1, universe=universe)
        engine.open_market()
        engine.run_session(9, 30, 3, 60)

        quote = engine.book(ticker).sweep_cost("buy", size)
        portfolio = pretium.Portfolio(cash=1e12)
        portfolio.stamp(0, 0, 0)
        fill = portfolio.execute(engine, ticker, size)

        assert quote.average_price == pytest.approx(fill["price"], abs=1e-12)
        assert quote.worst_price == pytest.approx(fill["worst_price"], abs=1e-12)
        assert quote.filled == pytest.approx(fill["quantity"], abs=1e-9)
        if quote.worst_price > quote.average_price:
            walked += 1
    assert walked, (
        "no size walked past the touch, so this only compared the easy case"
    )


def test_walking_the_book_costs_more_than_resting_inside_it():
    # The mechanism, stated as an ordering rather than a magnitude. A large
    # order reaches further up the book and pays for it; that is impact
    # emerging from depth rather than from a formula.
    universe = pretium.Universe.random(6, seed=5)
    engine = pretium.Engine(seed=1, universe=universe)
    engine.open_market()
    engine.run_session(9, 30, 3, 60)
    book = engine.book(universe[0].ticker)

    small = book.sweep_cost("buy", 1_000.0)
    large = book.sweep_cost("buy", 50_000_000.0)
    assert small.average_price == small.worst_price, "a small buy left the touch"
    assert large.average_price > small.average_price
    assert large.worst_price > large.average_price, "the large buy did not walk"
