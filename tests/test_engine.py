"""Layer 2: the engine.

Deliberately free of the golden corpus, like the known-answer tests and unlike
`test_parity.py`. Reference parity for the tick loop is already gated on the
Rust side; what needs asserting here is that the Python surface preserves the
properties a user depends on — determinism, that the fast path is the same
simulation as the slow one, and that the columnar contract holds.
"""

import itertools
import math
import struct

import pytest

import tradefloor


def arr(buf):
    """Decode a returned column. Little-endian f64, always."""
    return list(struct.unpack("<%dd" % (len(buf) // 8), buf))


def universe(n=6):
    sectors = tradefloor.sectors()
    return [
        tradefloor.Instrument(
            f"C{i}", sectors[i % 12],
            initial_price=50.0 + i * 3,
            shares_outstanding=1e8,
            eps=2.0 + i * 0.4,
            book_value_per_share=15.0 + i,
            revenue_growth=0.05 + i * 0.01,
        )
        for i in range(n)
    ]


def engine(seed=42, n=6, **macro):
    return tradefloor.Engine(
        seed=seed, universe=universe(n), macro_state=tradefloor.Macro(**macro)
    )


# --------------------------------------------------------------------------
# Determinism
# --------------------------------------------------------------------------

def test_same_seed_same_market():
    a, b = engine(), engine()
    for e in (a, b):
        e.open_market()
        e.run_session(9, 30, 3, 120)
    assert arr(a.prices()) == arr(b.prices())
    assert a.draws_consumed == b.draws_consumed


def test_different_seed_different_market():
    a, b = engine(seed=1), engine(seed=2)
    for e in (a, b):
        e.open_market()
        e.run_session(9, 30, 3, 120)
    assert arr(a.prices()) != arr(b.prices())


def test_run_session_is_the_same_simulation_as_ticking():
    """The fast path must be the SAME market, not merely a faster one.

    `run_session` exists so 390 ticks cost one boundary crossing instead of
    390. If it produced even slightly different prices it would be a second
    engine wearing the same name, and the difference would surface as
    irreproducible results long after anyone remembered which path they took.
    """
    fast = engine(seed=99)
    fast.open_market()
    fast.run_session(9, 30, 3, 120)

    slow = engine(seed=99)
    slow.open_market()
    for i in range(120):
        slow.tick(9 + (30 + i) // 60, (30 + i) % 60, 3)

    assert arr(fast.prices()) == arr(slow.prices())
    assert fast.draws_consumed == slow.draws_consumed


def test_engines_do_not_share_state():
    # Two engines in one process must be independent, or "same seed, same
    # market" would hold only for whichever was built first.
    a = engine(seed=7)
    a.open_market()
    a.run_session(9, 30, 3, 60)
    b = engine(seed=7)
    b.open_market()
    b.run_session(9, 30, 3, 60)
    assert arr(a.prices()) == arr(b.prices())


# --------------------------------------------------------------------------
# Market hours
# --------------------------------------------------------------------------

def test_a_closed_market_costs_nothing():
    e = engine()
    before = arr(e.prices())
    result = e.tick(11, 0, 6)  # Saturday
    assert result.market_status == "closed"
    assert result.draws_consumed == 0
    assert arr(e.prices()) == before


def test_session_boundaries_are_half_open():
    # 16:00 exactly is after-hours, not the last minute of the session.
    assert tradefloor.market_status(9, 30, 3) == "open"
    assert tradefloor.market_status(15, 59, 3) == "open"
    assert tradefloor.market_status(16, 0, 3) == "after_hours"
    assert tradefloor.market_status(9, 29, 3) == "pre_market"
    assert tradefloor.market_status(11, 0, 0) == "closed"  # Sunday


def test_extended_hours_still_trade():
    # Kept deliberately: excluding them from the API would not remove them
    # from the engine, only hide them.
    e = engine()
    e.open_market()
    result = e.tick(8, 0, 3)
    assert result.market_status == "pre_market"
    assert result.draws_consumed > 0


# --------------------------------------------------------------------------
# The columnar contract
# --------------------------------------------------------------------------

def test_columns_are_little_endian_f64_in_roster_order():
    e = engine(n=6)
    prices = e.prices()
    assert len(prices) == 6 * 8, "six instruments, eight bytes each"
    assert len(arr(prices)) == 6
    assert e.tickers == [f"C{i}" for i in range(6)]


def test_market_cap_is_derived_from_price_and_shares():
    # Not an input. If it were, a caller could pass an inconsistent triple and
    # the spread tier would disagree with the priced value of the company.
    e = engine(n=3)
    for price, cap in zip(arr(e.prices()), arr(e.column("market_cap"))):
        assert cap == pytest.approx(price * 1e8, rel=1e-12)


def test_instrument_market_cap_tracks_its_inputs():
    inst = tradefloor.Instrument("X", "technology", initial_price=25.0,
                              shares_outstanding=4e8)
    assert inst.market_cap == 25.0 * 4e8


def test_session_buffer_is_row_major_and_sized_to_what_was_written():
    e = engine(n=4)
    e.open_market()
    written = e.run_session(9, 30, 3, 50)
    assert written == 50
    assert e.session_ticks_written == 50

    buf = arr(e.session_prices())
    assert len(buf) == 50 * 4
    # Row-major: the final tick's cross-section is the last row, and equals
    # the engine's current prices.
    assert buf[49 * 4:50 * 4] == arr(e.prices())


def test_session_buffer_does_not_leak_a_previous_session():
    # The buffer is reused. Returning past `ticks_written` would hand back the
    # previous session's data as though it were this one's.
    e = engine(n=3)
    e.open_market()
    e.run_session(9, 30, 3, 100)
    e.run_session(11, 10, 3, 10)
    assert len(arr(e.session_prices())) == 10 * 3


def test_ground_truth_columns_are_available():
    # The distinguishing feature: the simulator knows what produced each price.
    e = engine(n=3)
    e.open_market()
    e.run_session(9, 30, 3, 20)
    assert len(arr(e.session_mispricing_s())) == 20 * 3
    assert len(arr(e.session_volumes())) == 20 * 3
    assert len(arr(e.column("garch_variance"))) == 3


def test_unknown_column_names_the_valid_ones():
    e = engine()
    with pytest.raises(tradefloor.ValidationError, match="mispricing_s"):
        e.column("close")


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

def test_seed_is_required():
    # No clock fallback. A simulator that seeds itself when you forget
    # produces a run nobody can reproduce, and nothing reports it.
    with pytest.raises(TypeError):
        tradefloor.Engine(universe=universe())


def test_an_empty_universe_is_refused():
    with pytest.raises(tradefloor.ValidationError, match="empty"):
        tradefloor.Engine(seed=1, universe=[])


def test_instrument_rejects_bad_input():
    with pytest.raises(tradefloor.ValidationError, match="sector"):
        tradefloor.Instrument("X", "tecnology", initial_price=10.0, shares_outstanding=1e6)
    with pytest.raises(tradefloor.ValidationError, match="initial_price"):
        tradefloor.Instrument("X", "technology", initial_price=0.0, shares_outstanding=1e6)
    with pytest.raises(tradefloor.ValidationError, match="finite"):
        tradefloor.Instrument("X", "technology", initial_price=float("nan"),
                           shares_outstanding=1e6)


def test_macro_rates_are_fractional():
    with pytest.raises(tradefloor.ValidationError, match="percent"):
        tradefloor.Macro(federal_funds_rate=4.5)
    # And the real range is admitted.
    assert tradefloor.Macro(federal_funds_rate=-0.005) is not None
    assert tradefloor.Macro(federal_funds_rate=0.20) is not None


def test_macro_rejects_an_unknown_cycle():
    with pytest.raises(tradefloor.ValidationError, match="expansion"):
        tradefloor.Macro(cycle="boom")


def test_invalid_clock_values_are_refused():
    e = engine()
    for bad in ((24, 0, 3), (9, 60, 3), (9, 30, 7)):
        with pytest.raises(tradefloor.ValidationError):
            e.tick(*bad)


# --------------------------------------------------------------------------
# Draw accounting
# --------------------------------------------------------------------------

def test_embedder_draws_share_the_engine_stream():
    # A caller's own subsystems must draw from HERE. A second generator would
    # interleave differently and change every price.
    e = engine()
    before = e.draws_consumed
    e.draw_uniform()
    e.draw_normal()
    assert e.draws_consumed == before + 2


def test_draw_count_reports_alignment():
    # Diagnostic, not enforcement: two runs agreeing here consumed the
    # generator identically, which is the precondition for their prices
    # agreeing.
    a, b = engine(seed=5), engine(seed=5)
    for e in (a, b):
        e.open_market()
        e.run_session(9, 30, 3, 30)
    assert a.draws_consumed == b.draws_consumed


# --------------------------------------------------------------------------
# Roster mutation
# --------------------------------------------------------------------------

def test_listing_and_delisting_reproduce_on_replay():
    """One seed plus the same edits at the same ticks gives the same market.

    This is the guarantee. It is what lets a seed identify a run whose universe
    changed partway through — an IPO, a bankruptcy, an acquisition — which a
    static roster could not represent at all.
    """
    def run():
        e = engine(seed=7, n=4)
        e.open_market()
        e.run_session(9, 30, 3, 20)
        e.list_instrument(tradefloor.Instrument(
            "IPO", "energy", initial_price=33.0, shares_outstanding=5e7, eps=1.5))
        e.run_session(9, 50, 3, 20)
        e.delist(1)
        e.run_session(10, 10, 3, 20)
        return arr(e.prices()), e.draws_consumed, e.tickers

    assert run() == run()


def test_an_edit_moves_the_rest_of_the_market():
    """The half that is NOT guaranteed, asserted so nobody relies on it.

    Listing an instrument does not append a name to an otherwise-unchanged
    market. The tick draws per instrument, so a larger roster shifts every
    subsequent draw. Anyone building on the assumption that existing paths are
    untouched needs to see this fail loudly if the model ever changed.
    """
    untouched = engine(seed=11, n=4)
    untouched.open_market()
    untouched.run_session(9, 30, 3, 40)

    edited = engine(seed=11, n=4)
    edited.open_market()
    edited.run_session(9, 30, 3, 20)
    edited.list_instrument(tradefloor.Instrument(
        "IPO", "energy", initial_price=33.0, shares_outstanding=5e7, eps=1.5))
    edited.run_session(9, 50, 3, 20)

    assert arr(untouched.prices()) != arr(edited.prices())[:4]


def test_delisting_preserves_the_order_of_the_rest():
    e = engine(n=5)
    assert e.tickers == ["C0", "C1", "C2", "C3", "C4"]
    assert e.delist(1) == "C1"
    assert e.tickers == ["C0", "C2", "C3", "C4"]
    # Indices shift down; a held index is stale after a delisting.
    assert e.index_of("C2") == 1


def test_roster_edits_draw_nothing():
    # An edit is bookkeeping, not simulation. If it consumed draws, the
    # market would move merely by being observed.
    e = engine(n=3)
    e.open_market()
    e.run_session(9, 30, 3, 10)
    before = e.draws_consumed
    e.list_instrument(tradefloor.Instrument("X", "utilities", initial_price=10.0,
                                         shares_outstanding=1e7))
    e.delist(0)
    assert e.draws_consumed == before


def test_delisting_out_of_range_reports_the_roster_size():
    e = engine(n=2)
    with pytest.raises(tradefloor.ValidationError, match="roster holds 2"):
        e.delist(5)
    assert len(e) == 2, "a failed delisting must not disturb the roster"


def test_columns_follow_the_edited_roster():
    e = engine(n=3)
    e.list_instrument(tradefloor.Instrument("NEW", "materials", initial_price=77.0,
                                         shares_outstanding=1e8))
    assert len(arr(e.prices())) == 4
    assert e.tickers[3] == "NEW"
    assert arr(e.prices())[3] == 77.0


# --------------------------------------------------------------------------
# Tick inputs: news and order flow
# --------------------------------------------------------------------------

def test_news_reaches_the_price():
    e_none = engine(n=4)
    e_none.open_market()
    e_none.run_session(9, 30, 3, 60)

    e_news = engine(n=4)
    e_news.open_market()
    e_news.run_session(9, 30, 3, 60,
                       news=[tradefloor.News(ticker="C0", price_impact=0.05)])

    assert arr(e_none.prices()) != arr(e_news.prices())


def test_order_flow_moves_the_targeted_name_in_the_right_direction():
    """Sustained pressure, because a single tick's effect is sub-cent.

    Worth stating plainly: order flow at one tick moves the model price by
    roughly 5e-6 in log terms, which on a $12 stock is $0.00006 and rounds
    away on the cent grid. A test asserting a one-tick price change would
    fail while the model was working perfectly -- the same quantisation that
    hides the cross-language `cos` divergence.
    """
    def run(flow=None):
        e = engine(seed=42, n=3)
        e.open_market()
        e.run_session(9, 30, 3, 300, flow_per_tick=flow)
        return arr(e.column("mispricing_s")), arr(e.prices())

    s_none, p_none = run()
    s_buy, p_buy = run({"C0": (5e6, 0.0)})
    s_sell, p_sell = run({"C0": (0.0, 5e6)})

    assert s_buy[0] > s_none[0] > s_sell[0], "buying pressure must lift the name"
    assert p_buy[0] > p_sell[0]
    assert p_none[1:] == p_buy[1:], "untargeted names must be untouched"


def test_order_flow_is_visible_in_the_state_even_when_the_print_rounds_away():
    # The single-tick case the test above avoids. It IS working; the cent grid
    # is simply coarser than one tick of pressure.
    def one(flow=None):
        e = engine(seed=42, n=3)
        e.open_market()
        e.tick(9, 30, 3, order_flow=flow)
        return arr(e.column("mispricing_s")), arr(e.prices())

    s_none, p_none = one()
    s_buy, p_buy = one({"C0": (5e6, 0.0)})
    assert s_buy[0] != s_none[0], "the model must respond"
    assert p_buy == p_none, "and the cent grid hides it at this size"


def test_an_unknown_ticker_is_refused_not_ignored():
    # Silently dropping flow would mean a study believing it applied pressure
    # that never reached the book, with nothing to say otherwise.
    e = engine(n=3)
    with pytest.raises(tradefloor.ValidationError, match="NOPE"):
        e.tick(9, 30, 3, order_flow={"NOPE": (1.0, 1.0)})
    with pytest.raises(tradefloor.ValidationError, match="NOPE"):
        e.tick(9, 30, 3, news=[tradefloor.News(ticker="NOPE", price_impact=0.1)])


def test_news_scope_is_decided_by_which_fields_are_set():
    # An event with neither ticker nor sector is MARKET-WIDE, not inert. That
    # asymmetry surprises people, so it is pinned.
    market_wide = engine(n=4)
    market_wide.open_market()
    market_wide.run_session(9, 30, 3, 60, news=[tradefloor.News(price_impact=0.04)])

    quiet = engine(n=4)
    quiet.open_market()
    quiet.run_session(9, 30, 3, 60)

    a, b = arr(quiet.prices()), arr(market_wide.prices())
    assert a != b
    assert sum(1 for x, y in zip(a, b) if x != y) > 1, "market-wide must touch more than one name"


def test_news_validates_its_sector():
    with pytest.raises(tradefloor.ValidationError, match="sector"):
        tradefloor.News(sector="tecnology", price_impact=0.1)
    with pytest.raises(tradefloor.ValidationError, match="finite"):
        tradefloor.News(ticker="C0", price_impact=float("nan"))


def test_negative_order_flow_is_refused():
    e = engine(n=3)
    with pytest.raises(tradefloor.ValidationError, match="negative"):
        e.tick(9, 30, 3, order_flow={"C0": (-1.0, 0.0)})


def test_news_driven_runs_are_reproducible():
    def run():
        e = engine(seed=5, n=4)
        e.open_market()
        e.run_session(9, 30, 3, 80,
                      news=[tradefloor.News(ticker="C1", price_impact=0.03)],
                      news_impacts=[tradefloor.NewsImpact(ticker="C1",
                                                       remaining_impact=0.02)],
                      flow_per_tick={"C0": (2e6, 1e5)})
        return arr(e.prices()), e.draws_consumed

    assert run() == run()


# --------------------------------------------------------------------------
# Ground truth: factor attribution
# --------------------------------------------------------------------------

def test_attribution_names_the_cause_of_each_move():
    """The labelled-dataset output, and the thing no real dataset has.

    You can observe from history that a stock fell. You cannot observe that
    sixty per cent of the fall was order-flow pressure and the rest was noise.
    The simulator knows, because it computed the reasons.
    """
    u = tradefloor.Universe.random(4, seed=1)
    e = tradefloor.Engine(seed=42, universe=u)
    e.open_market()
    e.run_session(9, 30, 3, 200,
                  flow_per_tick={u[0].ticker: (4e6, 0.0)},
                  news=[tradefloor.News(ticker=u[1].ticker, price_impact=0.04)])

    news = arr(e.attribution("company_news"))
    flow = arr(e.attribution("order_flow_impact"))
    noise = arr(e.attribution("random_noise"))

    # The cause lands on the instrument it was applied to, and nowhere else.
    assert news[1] != 0 and news[0] == 0 and news[2] == 0
    assert flow[0] != 0 and flow[1] == 0
    # Noise touches everything -- it is the residual, not a targeted effect.
    assert all(x != 0 for x in noise)


def test_attribution_reports_every_component_that_moves_a_price():
    # Four shocks and the three pieces of the model's own dynamics. No column
    # is structurally zero -- the "knobs wired to nothing" lie this model has
    # already had to correct once -- and none that moves a price is missing,
    # which is the failure that made the old four-column version misrank.
    assert tradefloor.Engine.FACTORS == [
        "reversion", "momentum", "crowd_lean",
        "company_news", "order_flow_impact", "short_squeeze_effect",
        "random_noise",
        # The eighth and ninth arrived 2026-08-26. `apply_jumps` moves `s`
        # after the tick loop, and the session breaker rewrites it from the
        # clamped price; the seven above reconstructed neither a jump day nor
        # a halted one on any preset that has jumps, which is every preset
        # from pt-v4 (§74, §79).
        "circuit_breaker",
        "jump",
        # The tenth arrived 2026-09-04: `apply_overnight` moves `s` at the
        # open before any tick, and the tape books it on the day's first row.
        "overnight",
        # The eleventh arrived with pt-v20 (0.8.5): what left `s` for the
        # fair-value level under the permanent share, which the ten report
        # as the whole shock. Zero on every earlier preset.
        "fair_value_shift",
    ]


def test_an_unknown_factor_names_the_valid_ones():
    e = tradefloor.Engine(seed=1, universe=tradefloor.Universe.random(3, seed=1))
    with pytest.raises(tradefloor.ValidationError, match="reversion"):
        e.attribution("sentiment")


def test_attribution_is_per_day_and_survives_the_close():
    # Reset at open, not at close, so a caller can still read the day's
    # decomposition after closing -- which is when they actually want it.
    e = engine(n=3)
    e.open_market()
    e.run_session(9, 30, 3, 100)
    e.close_market()
    after_close = arr(e.attribution("random_noise"))
    assert any(x != 0 for x in after_close)

    e.open_market()
    assert all(x == 0 for x in arr(e.attribution("random_noise"))), "open must reset"


def test_attribution_tracks_roster_edits():
    # The buffer is positional like every other column, so a listing or
    # delisting that did not move it would attribute one company's causes to
    # another.
    e = engine(n=3)
    e.open_market()
    e.run_session(9, 30, 3, 50)
    assert len(arr(e.attribution("random_noise"))) == 3

    e.list_instrument(tradefloor.Instrument("NEW", "utilities", initial_price=10.0,
                                         shares_outstanding=1e7))
    assert len(arr(e.attribution("random_noise"))) == 4
    assert arr(e.attribution("random_noise"))[3] == 0, "a new listing starts at zero"

    e.delist(0)
    assert len(arr(e.attribution("random_noise"))) == 3


def test_an_unknown_factor_names_the_valid_ones():
    e = engine(n=2)
    with pytest.raises(tradefloor.ValidationError, match="order_flow_impact"):
        e.attribution("sentiment")


def test_attribution_is_reproducible():
    def run():
        e = engine(seed=9, n=3)
        e.open_market()
        e.run_session(9, 30, 3, 60, flow_per_tick={"C0": (1e6, 5e5)})
        return [arr(e.attribution(f)) for f in tradefloor.Engine.FACTORS]

    assert run() == run()


def test_close_at_end_is_the_same_close_as_calling_it_yourself():
    """Two spellings of one close must produce one market.

    `run_session(close_at_end=True)` and `run_session(); close_market()` roll
    the same day, so every field they touch must agree. They did not.

    The close takes a per-company innovation and falls back to the day's total
    return when it is absent. On the `close_at_end` path the request is built
    BEFORE the session runs, so the caller is being asked for the noise the
    session has not accumulated yet -- a parameter that cannot be supplied
    correctly. It was therefore always absent, always the fallback, and the
    two paths rolled GARCH from different quantities: measured elsewhere at a
    median factor of 0.82 apart, with a tenth percentile of 0.22.

    The engine now fills an empty slot from its own accumulator. Asserted on
    garch_variance specifically because that is the field the innovation
    drives -- prices alone would have matched either way and hidden it.
    """
    universe = tradefloor.Universe.random(6, seed=2)

    inline = tradefloor.Engine(seed=5, universe=universe)
    inline.open_market()
    inline.run_session(9, 30, 3, 390, close_at_end=True)

    explicit = tradefloor.Engine(seed=5, universe=universe)
    explicit.open_market()
    explicit.run_session(9, 30, 3, 390)
    explicit.close_market()

    assert arr(inline.column("garch_variance")) == arr(
        explicit.column("garch_variance"))
    assert arr(inline.column("mispricing_momentum")) == arr(
        explicit.column("mispricing_momentum"))
    assert inline.prices() == explicit.prices()
    assert inline.draws_consumed == explicit.draws_consumed


def test_run_until_does_not_close_the_day():
    """It is the interactive shape: you stop mid-day and look.

    I claimed the opposite when fixing the innovation fallback -- that
    `run_until` closed internally and so took the same correction. It does
    not: it passes `close_at_end: false`, so the innovations and sector
    variances it builds are never read. It was never affected by that bug.

    The test I wrote on that false premise asserted `garch_variance > 0` after
    a run, which is true before any close and would have passed whatever
    happened. Replaced with the property that actually distinguishes the two:
    `run_until` leaves the day OPEN, so its state matches an unclosed session
    exactly and differs from a closed one on precisely the fields a close
    rolls.
    """
    universe = tradefloor.Universe.random(6, seed=2)

    halted = tradefloor.Engine(seed=5, universe=universe)
    halted.open_market()
    # A band nothing crosses, so it runs the full budget and stops on ticks.
    assert halted.run_until(ticker=halted.tickers[0], above=1e12,
                            max_ticks=100) is None

    unclosed = tradefloor.Engine(seed=5, universe=universe)
    unclosed.open_market()
    unclosed.run_session(9, 30, 3, 100)

    closed = tradefloor.Engine(seed=5, universe=universe)
    closed.open_market()
    closed.run_session(9, 30, 3, 100, close_at_end=True)

    def matches(a, b, field):
        # NaN-aware. `last_daily_return` is NaN until the first close, and
        # NaN != NaN -- comparing raw lists reported three columns as
        # differing when nothing did, which is how I nearly filed a bug
        # against the engine for my own comparator.
        left, right = arr(a.column(field)), arr(b.column(field))
        return all(
            (math.isnan(x) and math.isnan(y)) or x == y
            for x, y in zip(left, right)
        )

    for field in ("price", "garch_variance", "mispricing_s",
                  "mispricing_momentum", "last_daily_return"):
        assert matches(halted, unclosed, field), field

    # And it is genuinely a different state from a closed day, on exactly the
    # fields the close rolls.
    assert not matches(halted, closed, "garch_variance")
    assert not matches(halted, closed, "mispricing_momentum")
    # On pt-v20, the default, the price is one of them: the close re-marks
    # every traded name to the macro state it publishes
    # (`macro_publication_repricing`), after the day's last print. What the
    # halted run holds is that last print, the closed session's last row,
    # to the bit; before the re-mark the closed engine's price was it too.
    assert not matches(halted, closed, "price")
    last_print = arr(closed.session_prices())[-len(universe):]
    assert list(arr(halted.column("price"))) == list(last_print)


# --------------------------------------------------------------------------
# What the docs promise about the clock, the macro table and attribution
# --------------------------------------------------------------------------

def _clock_warnings(record):
    return [w for w in record if issubclass(w.category, RuntimeWarning)
            and "run_session started at" in str(w.message)]


def test_a_session_that_starts_before_the_last_one_ended_warns():
    """Passing 9:30 to every session of a day replays the opening minutes.

    The time of day sets the activity profile, so the repeated start is a
    different market. Measured on 40 names and 78 five-tick sessions, one
    name returned -25.0% where `run_days(1)` gave it +0.6%. It used to run
    without a word; it now warns, and the warning names the fix.
    """
    import warnings

    e = tradefloor.Engine(seed=404, universe=tradefloor.Universe.random(
        8, seed=111))
    e.open_market()
    with warnings.catch_warnings(record=True) as record:
        warnings.simplefilter("always")
        e.run_session(9, 30, 3, 5)
        assert _clock_warnings(record) == [], "a day's first session"
        e.run_session(9, 30, 3, 5)
    [w] = _clock_warnings(record)
    message = str(w.message)
    assert "09:30" in message and "09:35" in message
    assert "harness.session_clock" in message
    assert "(9 + m // 60, m % 60)" in message
    # Pointed at the caller's line, not at the extension.
    assert w.filename == __file__

    # An overlap warns too, not only an exact repeat.
    with pytest.warns(RuntimeWarning, match="started at 09:33"):
        e.run_session(9, 33, 3, 5)


def test_the_clock_warning_writes_nothing_and_changes_nothing():
    """A warning filter set to "error" leaves the engine as it was.

    The check runs after every argument check and before the day is opened
    or the call logged, so the raised warning leaves no log entry, and a run
    that only warns is the same market it was before the warning existed.
    """
    import warnings

    u = tradefloor.Universe.random(6, seed=3)
    e = tradefloor.Engine(seed=9, universe=u)
    e.open_market()
    e.run_session(9, 30, 3, 20)
    log, state = len(e.order_log), e.state_hash()
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        with pytest.raises(RuntimeWarning, match="session_clock"):
            e.run_session(9, 30, 3, 20)
    assert len(e.order_log) == log
    assert e.state_hash() == state

    # Warned or not, the call runs as asked: same prices either way.
    quiet, loud = e.fork(2)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        quiet.run_session(9, 30, 3, 20)
    with pytest.warns(RuntimeWarning):
        loud.run_session(9, 30, 3, 20)
    assert quiet.prices() == loud.prices()
    assert quiet.order_log == loud.order_log


def test_the_clock_warning_stays_silent_on_a_clock_that_moves_forward():
    """No warning on a day's first session, an advancing clock, a gap, a
    change of day of week, or a long day that wraps past midnight the way
    `harness.session_clock` wraps it."""
    import warnings

    from tradefloor.harness import session_clock

    u = tradefloor.Universe.random(6, seed=3)
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)

        e = tradefloor.Engine(seed=9, universe=u)
        for _ in range(2):
            e.open_market()
            for step in range(6):
                e.run_session(*session_clock((9, 30, 3), step, 65), 65)
            e.close_market()
        # The next day's first session, however the last one was closed.
        e.run_session(9, 30, 3, 30, close_at_end=True)
        e.run_session(9, 30, 3, 30)
        # A gap forward, then an exact continuation.
        e.run_session(11, 0, 3, 30)
        e.run_session(11, 30, 3, 30)
        # A new day of week starts the comparison again.
        e.run_session(9, 30, 4, 30)
        e.close_market()

        # 22:00 in 65-tick steps reaches 00:10 on the third step. The day of
        # week stays put, as `session_clock` leaves it.
        late = tradefloor.Engine(seed=9, universe=u)
        late.open_market()
        starts = [session_clock((22, 0, 3), step, 65) for step in range(4)]
        assert starts[2][:2] == (0, 10)
        for start in starts:
            late.run_session(*start, 65)
        late.close_market()

        # A restored engine has no clock to compare against.
        mid = tradefloor.Engine(seed=9, universe=u)
        mid.open_market()
        mid.run_session(9, 30, 3, 60)
        again = tradefloor.Engine(seed=9, universe=u)
        again.restore_state(mid.state_snapshot())
        again.run_session(9, 30, 3, 10)

    # The wrapped day ran to 02:20, so a session at 01:00 goes back.
    late.open_market()
    for start in starts:
        late.run_session(*start, 65)
    with pytest.warns(RuntimeWarning, match="started at 01:00 .* ran to 02:20"):
        late.run_session(1, 0, 3, 65)


def test_a_replay_runs_a_recorded_repeated_clock_without_warning():
    """A log from before 0.8.5 can hold a repeated start, and nobody can act
    on the warning at replay time. `replay` runs it as recorded and stays
    quiet, even under warnings-as-errors."""
    import warnings

    u = tradefloor.Universe.random(6, seed=3)
    run = tradefloor.Engine(seed=9, universe=u)
    run.open_market()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for _ in range(3):
            run.run_session(9, 30, 3, 20)
    run.close_market()
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        again = tradefloor.replay(run.order_log, seed=9, universe=u)
    assert again.prices() == run.prices()
    assert again.state_hash() == run.state_hash()


def test_run_session_docs_say_how_to_advance_the_clock():
    doc = tradefloor.Engine.run_session.__doc__
    assert "harness.session_clock" in doc
    assert "(9 + m // 60, m % 60)" in doc
    assert "RuntimeWarning" in doc


def test_macro_table_row_d_holds_what_day_d_traded_under():
    """Row d is recorded before day d's close, so it holds the values the
    day traded under, and what day d's close produced is row d + 1.

    The docstring used to say the table was keyed like `bars`, "so aligning
    a macro signal with prices is a join". A join pairs each return with the
    macro move BEFORE it: on the reviewer's run it put the correlation of the
    VIX change with the index return at -0.08 against -0.78 aligned.
    """
    import pyarrow as pa

    e = tradefloor.Engine(seed=101, universe=tradefloor.Universe.random(
        8, seed=111))
    before = e.macro_state.vix
    after = []
    for _ in range(4):
        e.run_days(1, record=True)
        after.append(e.macro_state.vix)
    vix = pa.table(e.macro_table()).column("vix").to_pylist()
    assert vix[0] == before
    assert vix[1:] == after[:-1]
    assert after[-1] not in vix, "the last close's values are on macro_state"

    doc = tradefloor.Engine.macro_table.__doc__
    assert "aligning a macro signal with prices is a join" not in doc
    assert "row `d + 1`" in doc and "macro_state" in doc


def _factor_names_in_the_stub():
    import ast
    import pathlib

    stub = pathlib.Path(tradefloor.__file__).with_name("_core.pyi")
    for node in ast.parse(stub.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and node.targets[0].id == "FactorName":
            return [elt.value for elt in node.value.slice.elts]
    raise AssertionError("no FactorName in the stub")


def test_the_attribution_docs_count_what_factors_holds():
    """The docstring said "Nine components" and listed seven; FACTORS has
    eleven. And the stub's `FactorName` listed seven, so a type checker
    refused `attribution("jump")`."""
    words = {9: "Nine", 10: "Ten", 11: "Eleven", 12: "Twelve"}
    doc = tradefloor.Engine.attribution.__doc__
    factors = tradefloor.Engine.FACTORS
    assert f"# {words[len(factors)]} components" in doc
    assert "Nine components" not in doc
    for name in factors:
        assert f"`{name}`" in doc, name
    # It decomposes the mispricing, and it says so.
    assert "WHY each price moved" not in doc
    assert "change in `mispricing_s`" in doc
    assert _factor_names_in_the_stub() == factors


@pytest.mark.parametrize("preset", ["pt-v19", "pt-v20"])
def test_attribution_sums_to_the_change_in_mispricing(preset):
    """What the docstring says the eleven add up to, on both sides of
    pt-v20: the day's change in `mispricing_s`, with `fair_value_shift`
    zero through pt-v19 and large on pt-v20, where it mostly cancels
    `random_noise`."""
    u = tradefloor.Universe.random(12, seed=111)
    e = tradefloor.Engine(seed=101, universe=u, model=preset)
    e.run_days(2)
    n = len(u)
    fair_value, noise = 0.0, 0.0
    for _ in range(5):
        s0 = arr(e.column("mispricing_s"))[:n]
        e.run_days(1)
        s1 = arr(e.column("mispricing_s"))[:n]
        parts = {f: arr(e.attribution(f))[:n]
                 for f in tradefloor.Engine.FACTORS}
        for i in range(n):
            total = math.fsum(parts[f][i] for f in parts)
            assert total == pytest.approx(s1[i] - s0[i], abs=1e-12)
        fair_value += sum(abs(x) for x in parts["fair_value_shift"])
        noise += sum(abs(x) for x in parts["random_noise"])
    if preset == "pt-v19":
        assert fair_value == 0.0
    else:
        assert fair_value > 0.5 * noise


def test_tick_grain_volume_is_each_minutes_own_volume():
    """What the `bars` docstring says about tick volume: each row is that
    minute's volume, the engine's running total less the total a minute
    earlier, counted across the sessions a day is split into, so the rows
    add up to the day's volume."""
    import pyarrow as pa

    from tradefloor.harness import session_clock

    e = tradefloor.Engine(seed=5, universe=tradefloor.Universe.random(
        3, seed=1))
    e.open_market()
    for step in range(3):
        e.run_session(*session_clock((9, 30, 3), step, 130), 130)
    e.record(0)
    rows = pa.table(e.bars()).to_pylist()
    volume = [r["volume"] for r in rows if r["instrument_id"] == 0]
    assert len(volume) == 390
    assert all(v >= 0 for v in volume)
    running = list(itertools.accumulate(volume))
    assert running[-1] == pytest.approx(arr(e.column("volume"))[0], rel=1e-12)
    # The second session's first minute is a minute, not the day so far.
    assert volume[130] < 0.2 * running[129]
    assert "volume traded inside the bar" in \
        " ".join(tradefloor.Engine.bars.__doc__.split())
