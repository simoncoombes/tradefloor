"""Execution regimes: chunked and interactive."""

import struct

import pytest

import pretium

UNIVERSE = pretium.Universe.random(6, seed=5)


def arr(buf):
    return list(struct.unpack("<%dd" % (len(buf) // 8), buf))


def engine(seed=2026):
    return pretium.Engine(seed=seed, universe=UNIVERSE)


# --------------------------------------------------------------------------
# Chunked
# --------------------------------------------------------------------------

def test_run_days_is_the_same_simulation_as_doing_it_by_hand():
    """A convenience that produced a different market would be a second engine.

    Worth stating what this is NOT: measured, the boundary crossing it saves
    costs 0.357 us against 249 us of engine work per tick at a hundred
    instruments. Chunking is the natural shape for columnar output and it
    records for you; it is not a meaningful speedup.
    """
    chunked = engine()
    chunked.run_days(5, ticks_per_day=200)

    manual = engine()
    for day in range(5):
        manual.open_market()
        manual.run_session(9, 30, 3, 200)
        manual.close_market()
        manual.record(day)

    assert arr(chunked.prices()) == arr(manual.prices())
    assert chunked.draws_consumed == manual.draws_consumed
    assert chunked.recorded_days == manual.recorded_days


def test_run_days_records_each_day_so_the_tables_stream():
    e = engine()
    e.run_days(4, ticks_per_day=100)
    assert e.recorded_days == 4
    assert e.bars().num_batches == 4


def test_recording_can_be_turned_off():
    # A caller who does not want tables should not pay to build them.
    e = engine()
    e.run_days(3, ticks_per_day=60, record=False)
    assert e.recorded_days == 0


def test_days_are_numbered_from_first_day():
    e = engine()
    e.run_days(3, ticks_per_day=60, first_day=10)
    import pyarrow as pa
    assert sorted(set(pa.table(e.bars()).to_pydict()["day"])) == [10, 11, 12]


def test_run_days_appears_in_the_log_as_its_constituent_calls():
    # The log records what actually crossed into the engine, so a replay does
    # not need to know that run_days exists.
    e = engine()
    e.run_days(2, ticks_per_day=50)
    ops = [x["op"] for x in e.order_log]
    # Record comes BEFORE the close: the close advances the macro chain into
    # the next day, and the recorded macro row must carry the values the day
    # traded under.
    assert ops == ["open_market", "run_session", "record", "close_market"] * 2

    replayed = pretium.replay(e.order_log, seed=2026, universe=UNIVERSE)
    assert arr(replayed.prices()) == arr(e.prices())


def test_degenerate_spans_are_refused():
    e = engine()
    with pytest.raises(pretium.ValidationError, match="days must be"):
        e.run_days(0)
    with pytest.raises(pretium.ValidationError, match="ticks_per_day"):
        e.run_days(1, ticks_per_day=0)


# --------------------------------------------------------------------------
# Interactive
# --------------------------------------------------------------------------

def test_run_until_stops_when_the_price_leaves_the_band():
    """The point of the interactive regime.

    An algorithm watching for a level crosses the boundary when the level is
    hit, not 390 times a day hoping. A crossing per DECISION is irreducible;
    the goal is to make decisions sparser than ticks.
    """
    e = engine(seed=7)
    e.open_market()
    start = arr(e.prices())[0]
    ticker = e.tickers[0]

    fired = e.run_until(ticker=ticker, above=start * 1.004, below=start * 0.996,
                        max_ticks=390)
    assert fired is not None
    price = arr(e.prices())[0]
    assert price > start * 1.004 or price < start * 0.996


def test_an_unreachable_band_returns_none_which_is_an_answer():
    # "It never got there" is usually the thing you needed to know, not a
    # failure to report.
    e = engine(seed=7)
    e.open_market()
    start = arr(e.prices())[0]
    assert e.run_until(ticker=e.tickers[0], above=start * 100, max_ticks=50) is None


def test_a_one_sided_band_works():
    e = engine(seed=7)
    e.open_market()
    start = arr(e.prices())[0]
    assert e.run_until(ticker=e.tickers[0], below=start * 0.999, max_ticks=390) is not None


def test_run_until_is_reproducible():
    def run():
        e = engine(seed=13)
        e.open_market()
        start = arr(e.prices())[0]
        return e.run_until(ticker=e.tickers[0], above=start * 1.003,
                           below=start * 0.997, max_ticks=390), arr(e.prices())

    assert run() == run()


def test_a_condition_is_required():
    # Without one this is just run_session, and silently behaving like it
    # would hide a caller's mistake.
    e = engine()
    e.open_market()
    with pytest.raises(pretium.ValidationError, match="at least one"):
        e.run_until(ticker=e.tickers[0])


def test_an_inverted_band_is_refused():
    # below above above fires on the first tick, always. That is never what
    # anyone meant.
    e = engine()
    e.open_market()
    with pytest.raises(pretium.ValidationError, match="inverted band"):
        e.run_until(ticker=e.tickers[0], above=10.0, below=20.0)


def test_an_unknown_ticker_is_refused():
    e = engine()
    e.open_market()
    with pytest.raises(pretium.ValidationError, match="NOPE"):
        e.run_until(ticker="NOPE", above=100.0)


def test_nonsense_bounds_are_refused():
    e = engine()
    e.open_market()
    with pytest.raises(pretium.ValidationError, match="finite and positive"):
        e.run_until(ticker=e.tickers[0], above=float("nan"))
    with pytest.raises(pretium.ValidationError, match="max_ticks"):
        e.run_until(ticker=e.tickers[0], above=100.0, max_ticks=0)
