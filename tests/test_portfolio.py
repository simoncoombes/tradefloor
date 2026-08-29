"""Positions, execution and P&L."""

import pytest

import tradefloor

UNIVERSE = tradefloor.Universe.random(5, seed=3)
TICKER = UNIVERSE[0].ticker


def market(seed=42, ticks=60):
    e = tradefloor.Engine(seed=seed, universe=UNIVERSE)
    e.open_market()
    e.run_session(9, 30, 3, ticks)
    return e


# --------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------

def test_a_fill_is_priced_by_the_book_not_by_a_coefficient():
    """Slippage is levels consumed. There is no coefficient on this path.

    A small order fills inside the best level; a large one walks up the book
    and pays a worse average. That difference is the book, not a model of the
    book.
    """
    e = market()
    small = tradefloor.Portfolio(cash=1e9).execute(e, TICKER, 1_000)
    large = tradefloor.Portfolio(cash=1e9).execute(e, TICKER, 500_000)
    assert large["price"] > small["price"]
    assert large["worst_price"] > small["worst_price"]


def test_cash_and_position_move_together():
    e = market()
    p = tradefloor.Portfolio(cash=5e6)
    fill = p.execute(e, TICKER, 10_000)
    assert p.positions[TICKER].quantity == 10_000
    assert p.cash == pytest.approx(5e6 - fill["notional"])


def test_selling_short_is_allowed():
    # A harness that could not express a short would quietly narrow what an
    # agent can be evaluated on.
    e = market()
    p = tradefloor.Portfolio(cash=5e6)
    p.execute(e, TICKER, -10_000)
    assert p.positions[TICKER].quantity == -10_000
    assert p.cash > 5e6, "a short sale brings cash in"


def test_a_partial_fill_is_reported_as_partial():
    # Filling a remainder at the last level would be inventing liquidity that
    # was not there -- the kind of convenience that makes a backtest
    # profitable and a live strategy not.
    e = market()
    p = tradefloor.Portfolio(cash=1e12)
    depth = e.book(TICKER).depth("sell")
    fill = p.execute(e, TICKER, depth * 2)
    assert fill["partial"] is True
    assert abs(fill["quantity"]) < depth * 2


def test_a_zero_or_nan_trade_is_refused():
    e = market()
    p = tradefloor.Portfolio()
    for bad in (0, float("nan")):
        with pytest.raises(tradefloor.ValidationError):
            p.execute(e, TICKER, bad)


# --------------------------------------------------------------------------
# P&L accounting
# --------------------------------------------------------------------------

def test_a_round_trip_realises_the_spread_it_paid():
    e = market()
    p = tradefloor.Portfolio(cash=1e9)
    p.execute(e, TICKER, 50_000)
    p.execute(e, TICKER, -50_000)
    assert p.positions[TICKER].quantity == 0
    # Crossing the spread twice with no price move loses money. A round trip
    # that broke even would mean execution was free.
    assert p.realised() < 0


def test_crossing_through_zero_realises_only_the_part_that_closed():
    """The subtle branch, and the one that looks plausible when wrong.

    Selling more than you hold flips you short. Only the shares that actually
    closed realise P&L; booking the whole trade as a close would report profit
    on shares that were never held -- and the number would still be finite and
    the direction still right, so nothing would look obviously broken.
    """
    e = market()
    p = tradefloor.Portfolio(cash=1e9)
    p.execute(e, TICKER, 10_000)
    entry = p.positions[TICKER].avg_cost

    p.execute(e, TICKER, -30_000)
    position = p.positions[TICKER]

    assert position.quantity == -20_000, "should be short the excess"
    # Realised P&L is on 10,000 shares, not 30,000.
    exit_price = p.fills[-1]["price"]
    assert position.realised == pytest.approx((exit_price - entry) * 10_000)
    # And the new short's cost basis starts at the price it was opened at,
    # not at a basis inherited from the long that just closed.
    assert position.avg_cost == pytest.approx(exit_price)


def test_adding_to_a_position_averages_the_cost():
    e = market()
    p = tradefloor.Portfolio(cash=1e9)
    first = p.execute(e, TICKER, 10_000)
    second = p.execute(e, TICKER, 30_000)
    expected = (first["price"] * 10_000 + second["price"] * 30_000) / 40_000
    assert p.positions[TICKER].avg_cost == pytest.approx(expected)


def test_net_worth_is_cash_plus_marks():
    e = market()
    p = tradefloor.Portfolio(cash=5e6)
    p.execute(e, TICKER, 20_000)
    marks = p.marks(e)
    assert p.net_worth(e) == pytest.approx(p.cash + 20_000 * marks[TICKER])
    assert p.pnl(e) == pytest.approx(p.net_worth(e) - 5e6)


def test_unrealised_moves_with_the_market():
    e = market()
    p = tradefloor.Portfolio(cash=1e9)
    p.execute(e, TICKER, 20_000)
    before = p.unrealised(e)
    e.run_session(10, 30, 3, 120)
    assert p.unrealised(e) != before


# --------------------------------------------------------------------------
# Impact
# --------------------------------------------------------------------------

def test_pending_flow_is_what_the_market_should_feel():
    # Execution prices the fill; flow applies the pressure. A harness that
    # executed without feeding flow back would have a trader with realistic
    # fills and an invisible footprint.
    e = market()
    p = tradefloor.Portfolio(cash=1e9)
    p.execute(e, TICKER, 30_000)
    p.execute(e, TICKER, -10_000)
    assert p.pending_flow() == {TICKER: (30_000.0, 10_000.0)}

    p.clear_flow()
    assert p.pending_flow() == {}


def test_flow_fed_back_actually_moves_the_market():
    # Traded on AAB, not the usual TICKER (AAA), and the reason is worth
    # keeping: AAA is a 44M-share-a-day mega-cap, information impact
    # saturates at 10x the average minute volume, and its saturated ceiling
    # works out to about one cent over this session -- which the cent grid
    # then hides or shows depending on where the seed's noise lands. It
    # showed until the stream-split re-deal and hides after, but that is the
    # coin flip, not the claim. AAB's 2M-share day clears the grid by an
    # order of magnitude, so THIS assertion measures the mechanism.
    #
    # Probed at the re-deal: the same 500k order moves AAB +0.14, AAC +0.01,
    # AAD +12.26, AAE +0.09 -- and in every case only the traded name, which
    # is the stream split doing exactly what it promises.
    thin = UNIVERSE[1].ticker
    quiet = market()
    quiet.run_session(10, 30, 3, 200)

    traded = market()
    p = tradefloor.Portfolio(cash=1e10)
    p.execute(traded, thin, 500_000)
    traded.run_session(10, 30, 3, 200, order_flow=p.pending_flow())

    assert quiet.prices() != traded.prices()


# --------------------------------------------------------------------------
# Leverage
# --------------------------------------------------------------------------

def test_unconstrained_by_default():
    # A bare simulator should not impose a broker's risk policy on a
    # researcher studying what an unconstrained strategy does.
    e = market()
    p = tradefloor.Portfolio(cash=5e6)
    p.execute(e, TICKER, 200_000)
    assert p.cash < 0, "leverage is available when nothing caps it"


def test_a_leverage_cap_refuses_the_trade_before_taking_it():
    """For an evaluation harness this should almost always be set.

    An agent that can trade unlimited size is not being tested against the
    market. The book makes large trades cost more, but with no funding limit
    arbitrarily large is always available and "trade everything" becomes a
    strategy. The cap is what makes impact bite economically rather than only
    mechanically.
    """
    e = market()
    p = tradefloor.Portfolio(cash=5e6, max_leverage=2.0)
    with pytest.raises(tradefloor.OrderError, match="above the 2.00x limit"):
        p.execute(e, TICKER, 200_000)
    # And nothing moved: the check runs before any mutation.
    assert p.positions == {}
    assert p.cash == 5e6


def test_a_trade_within_the_cap_succeeds():
    e = market()
    p = tradefloor.Portfolio(cash=5e6, max_leverage=2.0)
    p.execute(e, TICKER, 90_000)
    assert p.leverage(e) <= 2.0


def test_gross_exposure_does_not_net_longs_against_shorts():
    # A long and a short of equal size are two positions with two risks, not a
    # flat book. Netting them would report a hedged trader and a reckless one
    # as identical.
    e = market()
    p = tradefloor.Portfolio(cash=1e9)
    p.execute(e, UNIVERSE[0].ticker, 10_000)
    p.execute(e, UNIVERSE[1].ticker, -10_000)
    marks = p.marks(e)
    expected = 10_000 * marks[UNIVERSE[0].ticker] + 10_000 * marks[UNIVERSE[1].ticker]
    assert p.gross_exposure(e) == pytest.approx(expected)


def test_an_invalid_cap_is_refused():
    with pytest.raises(tradefloor.ValidationError, match="max_leverage"):
        tradefloor.Portfolio(cash=1e6, max_leverage=0)


# --------------------------------------------------------------------------
# Determinism
# --------------------------------------------------------------------------

def test_a_trading_session_is_reproducible():
    def run():
        e = market(seed=11)
        p = tradefloor.Portfolio(cash=1e8)
        for _ in range(3):
            p.execute(e, TICKER, 20_000)
            e.run_session(10, 0, 3, 60, order_flow=p.pending_flow())
            p.clear_flow()
        return p.pnl(e), p.realised(), [f["price"] for f in p.fills]

    assert run() == run()


# --------------------------------------------------------------------------
# The short side, where signs go wrong quietly
# --------------------------------------------------------------------------


def test_a_short_gains_when_the_price_falls():
    """Direction, on the side where a sign error is invisible.

    A long with a flipped sign is obvious the first time you look at a
    number. A short with a flipped sign looks like an unlucky position, and
    every P&L identity still balances -- cash and marks would simply both be
    wrong together.
    """
    engine = market()
    ticker = engine.tickers[0]
    portfolio = tradefloor.Portfolio(cash=1_000_000.0)
    portfolio.execute(engine, ticker, -500)

    import struct
    price = lambda: struct.unpack("<%dd" % len(UNIVERSE), engine.prices())[0]
    entry = price()
    before = portfolio.net_worth(engine)
    for _ in range(6):
        engine.run_session(9, 30, 3, 60)
    after = price()
    worth = portfolio.net_worth(engine)

    assert entry != after, "the price did not move; the test proves nothing"
    # Falling price, rising worth -- and the converse if the market went the
    # other way. Asserted as an equivalence so the test holds whichever way
    # this seed happens to run.
    assert (after < entry) == (worth > before)


def test_the_leverage_cap_counts_shorts():
    # Gross exposure, not net. A cap that only looked at longs would let an
    # account short without limit while reporting itself constrained.
    engine = market()
    ticker = engine.tickers[0]
    portfolio = tradefloor.Portfolio(cash=100_000.0, max_leverage=2.0)

    refused = False
    for _ in range(60):
        try:
            portfolio.execute(engine, ticker, -2000)
        except (tradefloor.OrderError, tradefloor.ValidationError):
            refused = True
            break
    assert refused, "shorting was never refused; the cap does not see it"
    assert portfolio.leverage(engine) <= 2.0 + 1e-9
    assert portfolio.positions[ticker].quantity < 0


def test_pnl_decomposes_into_realised_and_unrealised():
    # An identity rather than a value, so it holds for any market. If these
    # ever disagree, one of the three is computed from a different basis.
    engine = market()
    ticker = engine.tickers[0]
    portfolio = tradefloor.Portfolio(cash=1_000_000.0)
    portfolio.execute(engine, ticker, 150)
    engine.run_session(9, 30, 3, 60)
    portfolio.execute(engine, ticker, -50)
    engine.run_session(9, 30, 3, 60)

    assert portfolio.pnl(engine) == pytest.approx(
        portfolio.realised() + portfolio.unrealised(engine), abs=1e-9)
    assert portfolio.net_worth(engine) == pytest.approx(
        portfolio.cash + portfolio.market_value(engine), abs=1e-9)
    # And it is not trivially zero, which would satisfy both identities.
    assert portfolio.realised() != 0.0
