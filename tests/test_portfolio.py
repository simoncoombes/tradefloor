"""Positions, execution and P&L."""

import pytest

import tradefloor

UNIVERSE = tradefloor.Universe.random(5, seed=3)
TICKER = UNIVERSE[0].ticker


def market(seed=42, ticks=60, model=None):
    e = tradefloor.Engine(seed=seed, universe=UNIVERSE, model=model)
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
    #
    # On pt-v19, where the portfolio prices off a snapshot of the book and
    # the harness carries the flow. On pt-v20, the default, the order
    # executes in the engine's own book, the engine applies its flow, and
    # there is nothing left for the harness to feed.
    e = market(model="pt-v19")
    p = tradefloor.Portfolio(cash=1e9)
    p.execute(e, TICKER, 30_000)
    p.execute(e, TICKER, -10_000)
    assert p.pending_flow() == {TICKER: (30_000.0, 10_000.0)}

    p.clear_flow()
    assert p.pending_flow() == {}

    live = market(model="pt-v20")
    assert live.book_live
    q = tradefloor.Portfolio(cash=1e9)
    q.execute(live, TICKER, 30_000)
    q.execute(live, TICKER, -10_000)
    assert q.pending_flow() == {}


def test_flow_fed_back_actually_moves_the_market():
    # Traded on AAB, not the usual TICKER (AAA), and the reason is worth
    # keeping: AAA is a 44M-share-a-day mega-cap, information impact
    # saturates at 10x the average minute volume under the shipped default
    # (`order_flow_impact_law` removes that ceiling), and its saturated ceiling
    # works out to about one cent over this session -- which the cent grid
    # then hides or shows depending on where the seed's noise lands. It
    # showed until the stream-split re-deal and hides after, but the
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
    traded.run_session(10, 30, 3, 200, fills=p.pending_flow())

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
            e.run_session(10, 0, 3, 60, fills=p.pending_flow())
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


# --------------------------------------------------------------------------
# What counts as a quantity, and what an agent's act() may return
# --------------------------------------------------------------------------

def test_a_string_a_bool_or_an_infinity_is_not_a_quantity():
    """ "100" traded 100 shares and True traded one, where the framework
    adapters refuse "100" as not a number of shares, and -inf was refused
    as "got inf" (0.8.5 review: Jordan Okafor, Marcus Bell)."""
    e = market(model="pt-v19")
    p = tradefloor.Portfolio(cash=1e9)
    for bad, said in (("100", "got '100' (str)"), (True, "got True (bool)"),
                      (1 + 1j, "got (1+1j) (complex)"),
                      (float("inf"), "must be finite, got inf"),
                      (float("-inf"), "must be finite, got -inf")):
        with pytest.raises(tradefloor.ValidationError, match=said.replace(
                "(", r"\(").replace(")", r"\)").replace("+", r"\+")):
            p.execute(e, TICKER, bad)
    assert p.positions == {} and p.fills == []
    with pytest.raises(tradefloor.ValidationError):
        tradefloor.Limit("100", 10.0)
    with pytest.raises(tradefloor.ValidationError):
        tradefloor.Limit(float("inf"), 10.0)
    with pytest.raises(tradefloor.ValidationError):
        tradefloor.Limit(10, True)


def test_a_leverage_refusal_is_an_order_error_of_its_own_kind():
    from tradefloor.portfolio import LeverageError

    e = market()
    p = tradefloor.Portfolio(cash=5e6, max_leverage=2.0)
    with pytest.raises(LeverageError, match="above the 2.00x limit"):
        p.execute(e, TICKER, 200_000)
    assert issubclass(LeverageError, tradefloor.OrderError)


def test_order_items_takes_a_mapping_and_refuses_anything_else():
    from tradefloor.portfolio import order_items

    assert order_items(None) == []
    assert order_items({}) == []
    assert order_items({"AAA": 10}) == [("AAA", 10)]
    for bad, kind in (([("AAA", 10)], "list"), ("buy AAA", "str"),
                      (10, "int"), ((("AAA", 10),), "tuple")):
        with pytest.raises(tradefloor.ValidationError,
                           match=f"It returned a {kind}"):
            order_items(bad)


def test_check_order_says_what_each_entry_is():
    from tradefloor.portfolio import check_order

    limit, cancel = tradefloor.Limit(10, 5.0), tradefloor.Cancel()
    assert check_order("AAA", limit) is limit
    assert check_order("AAA", cancel) is cancel
    assert check_order("AAA", None) is None
    assert check_order("AAA", 0) is None
    assert check_order("AAA", -0.0) is None
    assert check_order("AAA", 7) == 7.0 and type(check_order("AAA", 7)) is float
    for ticker, value in ((0, 100), ("AAA", "100"), ("AAA", True),
                          ("AAA", float("nan")), ("AAA", [1])):
        with pytest.raises(tradefloor.ValidationError):
            check_order(ticker, value)


def test_the_part_of_a_limit_order_that_fills_at_once_is_in_fills():
    """A marketable limit order moved the position and the cash and was
    left out of Portfolio.fills, so obs.portfolio.fills, fills_table and
    the scorecard's impact missed it (0.8.5 review: Tomas Herrera)."""
    e = market()                      # pt-v20: the book is the engine's
    assert e.book_live
    p = tradefloor.Portfolio(cash=1e9)
    ask = e.book(TICKER).best_ask
    report = p.submit_limit(e, TICKER, 100, round(ask + 0.05, 2))
    assert report["filled"] > 0
    assert len(p.fills) == 1
    fill = p.fills[0]
    assert fill["quantity"] == report["filled"]
    assert fill["price"] == report["average_price"]
    assert fill["order_id"] == report["order_id"]
    assert fill["limit"] is True and fill["liquidity"] == "taker"
    assert p.positions[TICKER].quantity == sum(f["quantity"] for f in p.fills)
    assert fill["notional"] == pytest.approx(p.starting_cash - p.cash)


def test_a_limit_order_that_fills_nothing_at_once_adds_no_fill():
    e = market()
    p = tradefloor.Portfolio(cash=1e9)
    bid = e.book(TICKER).best_bid
    report = p.submit_limit(e, TICKER, 100, round(bid * 0.8, 2))
    assert report["filled"] == 0
    assert p.fills == []


def test_the_agent_sees_its_own_limit_fills():
    """Tomas Herrera's case: a buy limit above the ask for 10,000 shares on
    step 19. The position was 10,000 and the fills list was empty."""
    universe = tradefloor.Universe.random(20, seed=111)
    seen = []

    class Buyer:
        def act(self, obs):
            seen.append(sum(f["quantity"] for f in obs.portfolio.fills
                            if f["ticker"] == "AAC"))
            if obs.step == 19:
                ask = obs.book("AAC").best_ask
                return {"AAC": tradefloor.Limit(10_000, round(ask + 0.05, 2))}
            return {}

    world = tradefloor.World(seed=7, universe=universe, agent=Buyer(),
                             cash=1e9, max_leverage=None)
    world.run(4)
    held = world.portfolio.positions["AAC"].quantity
    assert held == 10_000
    assert sum(f["quantity"] for f in world.portfolio.fills
               if f["ticker"] == "AAC") == held
    assert seen[20] == held
    # World's own trace records the order once, from the engine's report,
    # and its summary does not count the new fill a second time.
    assert world.summary()["trades"] == 1
