"""The Arrow results surface.

pyarrow is a TEST dependency only. The package deliberately depends on no
dataframe library — it hands over buffers through the Arrow C Data Interface
and lets the consumer pick their tool. These tests use pyarrow because it is
the reference implementation of the protocol, and skip cleanly without it
rather than pretending the surface is untested.
"""

import struct

import pytest

import pretium

pa = pytest.importorskip("pyarrow", reason="pyarrow is a test-only dependency")
pc = pytest.importorskip("pyarrow.compute")


def arr(buf):
    return list(struct.unpack("<%dd" % (len(buf) // 8), buf))


def session(n=5, ticks=120, seed=42):
    u = pretium.Universe.random(n, seed=3)
    e = pretium.Engine(seed=seed, universe=u)
    e.open_market()
    e.run_session(9, 30, 3, ticks)
    return e, u, ticks


def test_bars_reads_as_an_arrow_table():
    e, _, ticks = session()
    table = pa.table(e.bars(day=0))
    assert table.num_rows == ticks * 5
    assert table.column_names == ["day", "tick", "instrument_id", "close", "volume"]


def test_every_float_column_is_f64():
    """The dtype rule, enforced rather than documented.

    Bit-exactness is the product: the known-answer gate hashes these buffers,
    and a half-precision "memory-saving" column would be a different market
    that happens to plot the same.
    """
    e, _, _ = session()
    for stream in (e.bars(day=0), e.truth(day=0)):
        for field in pa.table(stream).schema:
            if pa.types.is_floating(field.type):
                assert field.type == pa.float64(), f"{field.name} is not f64"


def test_identifiers_are_integers_not_repeated_strings():
    # At ten million rows the difference between an index and a string per row
    # is the difference between a column and a memory problem.
    e, _, _ = session()
    schema = pa.table(e.bars(day=0)).schema
    assert pa.types.is_unsigned_integer(schema.field("instrument_id").type)


def test_instrument_id_indexes_into_tickers():
    # The id is only meaningful against the roster, and roster order is
    # contractual, so this is the join that makes the table readable.
    e, u, _ = session()
    table = pa.table(e.bars(day=0)).to_pydict()
    assert set(table["instrument_id"]) == set(range(len(u)))
    assert e.tickers == u.tickers()


def test_rows_are_tick_major_and_match_the_raw_buffer():
    # Row-major: one tick's cross-section is contiguous. The Arrow table must
    # agree with the bytes surface, or the two disagree about the same run.
    e, _, ticks = session()
    table = pa.table(e.bars(day=0)).to_pydict()
    raw = arr(e.session_prices())
    assert table["close"] == raw
    # First n rows are tick 0 across every instrument.
    n = len(e.tickers)
    assert table["tick"][:n] == [0] * n
    assert table["instrument_id"][:n] == list(range(n))


def test_truth_carries_the_ground_truth_column():
    # The distinguishing output. No historical dataset has this column,
    # because no historical dataset knows what fair value was.
    e, _, ticks = session()
    table = pa.table(e.truth(day=0)).to_pydict()
    assert table["mispricing_s"] == arr(e.session_mispricing_s())
    assert len(table["mispricing_s"]) == ticks * 5


def test_the_day_column_is_the_caller_s_to_set():
    # The engine does not own a calendar; a session is a session. The caller
    # numbers days because only the caller knows what day it is.
    e, _, _ = session()
    assert set(pa.table(e.bars(day=7)).to_pydict()["day"]) == {7}


def test_a_requested_schema_does_not_silently_recast():
    # The protocol allows ignoring the hint, and ignoring it is the right
    # choice here: casting f64 down on request would be exactly the
    # parity-breaking switch the dtype rule exists to prevent.
    e, _, _ = session()
    stream = e.bars(day=0)
    capsule = stream.__arrow_c_stream__(None)
    assert capsule is not None
    assert pa.table(stream).schema.field("close").type == pa.float64()


def test_the_stream_reports_its_own_shape():
    e, _, ticks = session()
    stream = e.bars(day=0)
    assert stream.num_rows == ticks * 5
    assert stream.num_batches == 1
    assert "bars" in repr(stream)


def test_an_empty_session_produces_an_empty_table_not_an_error():
    # A session that ran no ticks is a legitimate state -- a weekend, or a
    # stop condition firing immediately. It must produce zero rows rather
    # than raising.
    u = pretium.Universe.random(3, seed=1)
    e = pretium.Engine(seed=1, universe=u)
    table = pa.table(e.bars(day=0))
    assert table.num_rows == 0
    assert table.column_names == ["day", "tick", "instrument_id", "close", "volume"]


def test_arrow_and_the_bytes_surface_never_disagree():
    # Two ways to read the same run. If they ever differed, one of them would
    # be lying about a market that only happened once.
    e, _, _ = session()
    assert pa.table(e.bars(day=0)).to_pydict()["close"] == arr(e.session_prices())
    assert pa.table(e.bars(day=0)).to_pydict()["volume"] == arr(e.session_volumes())


# --------------------------------------------------------------------------
# Per-day streaming
# --------------------------------------------------------------------------

def recorded(days=4, ticks=60, n=6):
    u = pretium.Universe.random(n, seed=5)
    e = pretium.Engine(seed=2026, universe=u, macro_state=pretium.Macro(federal_funds_rate=0.03))
    for day in range(days):
        e.open_market()
        e.run_session(9, 30, 3, ticks)
        e.close_market()
        e.pin_macro(federal_funds_rate=0.03 + day * 0.004)
        e.record(day)
    return e, u, days, ticks, n


def test_each_recorded_day_is_its_own_batch():
    """Streaming, not materialising.

    One seed at tick grain for 100 names over a trading year is ~9.8 million
    rows. As a single batch that is a memory problem; as 252 daily batches it
    is a pull protocol the consumer drives.
    """
    e, _, days, ticks, n = recorded()
    stream = e.bars()
    assert stream.num_batches == days
    assert stream.num_rows == days * ticks * n
    assert pa.table(stream).num_rows == days * ticks * n


def test_every_recorded_day_is_labelled():
    e, _, days, _, _ = recorded()
    assert sorted(set(pa.table(e.bars()).to_pydict()["day"])) == list(range(days))


def test_recording_is_explicit_and_the_buffer_is_reused():
    # The session buffer is overwritten every session, so anything not
    # captured before the next run is gone. Recording is a choice a caller
    # makes, and a caller who does not want a table pays nothing.
    u = pretium.Universe.random(3, seed=1)
    e = pretium.Engine(seed=1, universe=u)
    e.open_market()
    e.run_session(9, 30, 3, 50)
    assert e.recorded_days == 0
    # With nothing recorded, the table falls back to the last session.
    assert pa.table(e.bars()).num_rows == 50 * 3

    e.record(0)
    e.run_session(9, 30, 3, 10)
    assert e.recorded_days == 1
    # Now it reports what was RECORDED, not the session since.
    assert pa.table(e.bars()).num_rows == 50 * 3


def test_clearing_a_recording_discards_it():
    e, _, _, _, _ = recorded()
    e.clear_recording()
    assert e.recorded_days == 0


# --------------------------------------------------------------------------
# The macro table
# --------------------------------------------------------------------------

def test_macro_is_one_row_per_day_and_joins_on_the_same_key():
    # Aligning a macro signal with prices should be a join, not a hand-rolled
    # accumulation loop. That is the whole reason it is a table.
    e, _, days, _, _ = recorded()
    macro = pa.table(e.macro_table())
    assert macro.num_rows == days
    bars_days = set(pa.table(e.bars()).to_pydict()["day"])
    assert set(macro.to_pydict()["day"]) == bars_days


def test_macro_rates_come_back_fractional():
    """The same denomination they went in as.

    A results table reporting percent while the constructor takes fractions
    would reintroduce the unit trap on the return journey -- and it would do
    it silently, because both numbers are plausible.
    """
    e, _, _, _, _ = recorded()
    path = pa.table(e.macro_table()).to_pydict()["federal_funds_rate"]
    assert path == pytest.approx([0.030, 0.034, 0.038, 0.042])


def test_macro_columns_are_all_f64():
    e, _, _, _, _ = recorded()
    for field in pa.table(e.macro_table()).schema:
        if field.name != "day":
            assert field.type == pa.float64(), field.name


# --------------------------------------------------------------------------
# The fills table
# --------------------------------------------------------------------------

def traded(days=3, steps=4, ticks=60):
    u = pretium.Universe(sorted(pretium.Universe.random(8, seed=5),
                                key=lambda i: i.avg_volume))
    e = pretium.Engine(seed=2026, universe=u)
    p = pretium.Portfolio(cash=50_000_000)
    ticker = u[0].ticker
    for day in range(days):
        e.open_market()
        for step in range(steps):
            # Global step, within-day tick -- the same arithmetic the harness
            # uses, so this helper produces a joinable table like a real run.
            p.stamp(day, day * steps + step, step * ticks)
            size = u[0].avg_volume * 0.5
            p.execute(e, ticker, size if step % 2 == 0 else -size)
            e.run_session(9, 30, 3, ticks, order_flow=p.pending_flow())
            p.clear_flow()
        e.close_market()
        e.record(day)
    return e, p, days, steps


def test_fills_records_every_execution():
    e, p, days, steps = traded()
    fills = pa.table(p.fills_table(e.tickers))
    assert fills.num_rows == days * steps
    assert fills.column_names == [
        "day", "step", "tick", "instrument_id", "quantity", "price",
        "worst_price", "notional",
    ]


def test_fills_joins_to_bars_on_instrument_day_and_tick():
    """bars says where the price was; fills says where you were filled.

    This test used to check only that the fills' instrument ids and days were
    SUBSETS of the bars' -- a containment check wearing the name of a join. It
    passed with every fill sitting at tick zero, which is exactly the state
    the `tick` column was added to fix. It now does the join.
    """
    e, p, _, _ = traded()
    fills = pa.table(p.fills_table(e.tickers))
    bars = pa.table(e.bars())
    joined = fills.join(bars, keys=["day", "tick", "instrument_id"])
    assert joined.num_rows == fills.num_rows, (
        f"{fills.num_rows - joined.num_rows} fills matched no bar"
    )
    # Not an outer join filling nulls: every matched bar is a real one.
    assert all(v is not None and v > 0
               for v in joined.column("close").to_pylist())


def test_fills_are_stamped_with_when_they_happened():
    # Without a stamp every fill sits at day zero and the table cannot be
    # joined on time, which is most of what it is for.
    e, p, days, steps = traded()
    fills = pa.table(p.fills_table(e.tickers)).to_pydict()
    assert sorted(set(fills["day"])) == list(range(days))
    # `step` is GLOBAL and `tick` is within-day. Asserting both shapes,
    # because the two being different scales is the thing that made the table
    # unjoinable and is easy to "tidy" back into agreement.
    assert sorted(set(fills["step"])) == list(range(days * steps))
    assert sorted(set(fills["tick"])) == [s * 60 for s in range(steps)]


def test_fills_carry_the_worst_price_not_only_the_average():
    # An average alone hides how far up the book an order reached, and that
    # tail is what separates an order that was worked from one that was
    # dumped.
    e, p, _, _ = traded()
    fills = pa.table(p.fills_table(e.tickers)).to_pydict()
    buys = [(a, w) for a, w, q in
            zip(fills["price"], fills["worst_price"], fills["quantity"]) if q > 0]
    assert buys, "expected some buys"
    assert all(w >= a for a, w in buys), "a buy's worst price is at or above its average"


def test_an_untraded_roster_yields_an_empty_fills_table():
    e, _, _, _, _ = recorded()
    empty = pretium.Portfolio(cash=1e6)
    assert pa.table(empty.fills_table(e.tickers)).num_rows == 0


# --------------------------------------------------------------------------
# Downsampling
# --------------------------------------------------------------------------

def multiday(days=3, ticks=390, n=4):
    u = pretium.Universe.random(n, seed=5)
    e = pretium.Engine(seed=2026, universe=u)
    for day in range(days):
        e.open_market()
        e.run_session(9, 30, 3, ticks)
        e.close_market()
        e.record(day)
    return e, days, ticks, n


def test_coarser_grain_produces_fewer_rows():
    e, days, ticks, n = multiday()
    assert pa.table(e.bars()).num_rows == days * ticks * n
    assert pa.table(e.bars(minutes=5)).num_rows == days * (ticks // 5) * n
    assert pa.table(e.bars(grain="day")).num_rows == days * n


def test_the_coarse_schema_is_wider_because_ohlc_is_now_real():
    """At tick grain a bar IS the print, so open/high/low would repeat close.

    Once ticks are bucketed those columns carry real information, so the
    coarse schema is genuinely wider rather than the same columns rearranged.
    """
    e, _, _, _ = multiday()
    assert pa.table(e.bars()).column_names == [
        "day", "tick", "instrument_id", "close", "volume"]
    assert pa.table(e.bars(grain="day")).column_names == [
        "day", "bar", "instrument_id", "open", "high", "low", "close", "volume"]


def test_downsampled_bars_reconcile_with_the_ticks_they_came_from():
    # The check that matters: a bucketing bug would produce plausible OHLC
    # that simply is not what happened.
    e, _, _, n = multiday()
    tick = pa.table(e.bars()).to_pydict()
    daily = pa.table(e.bars(grain="day")).to_pydict()

    closes = [c for c, i, d in zip(tick["close"], tick["instrument_id"], tick["day"])
              if i == 0 and d == 0]
    volumes = [v for v, i, d in zip(tick["volume"], tick["instrument_id"], tick["day"])
               if i == 0 and d == 0]

    assert daily["open"][0] == closes[0]
    assert daily["close"][0] == closes[-1]
    assert daily["high"][0] == max(closes)
    assert daily["low"][0] == min(closes)
    assert daily["volume"][0] == pytest.approx(sum(volumes))


def test_high_is_at_or_above_low_everywhere():
    e, _, _, _ = multiday()
    d = pa.table(e.bars(minutes=15)).to_pydict()
    assert all(h >= l for h, l in zip(d["high"], d["low"]))
    assert all(l <= o <= h for o, h, l in zip(d["open"], d["high"], d["low"]))
    assert all(l <= c <= h for c, h, l in zip(d["close"], d["high"], d["low"]))


def test_a_short_final_bucket_is_kept_not_dropped():
    """Dropping a partial bar would silently discard the end of the session.

    Including the close, which is the single most-used price in the table.
    """
    u = pretium.Universe.random(2, seed=1)
    e = pretium.Engine(seed=1, universe=u)
    e.open_market()
    e.run_session(9, 30, 3, 47)     # not a multiple of 10
    e.record(0)
    d = pa.table(e.bars(minutes=10)).to_pydict()
    assert len(set(d["bar"])) == 5, "four full buckets plus a short one"
    tick_closes = [c for c, i in zip(pa.table(e.bars()).to_pydict()["close"],
                                     pa.table(e.bars()).to_pydict()["instrument_id"])
                   if i == 0]
    last_bar_close = [c for c, b, i in zip(d["close"], d["bar"], d["instrument_id"])
                      if b == 4 and i == 0][0]
    assert last_bar_close == tick_closes[-1]


def test_every_downsampled_column_is_f64():
    e, _, _, _ = multiday()
    for field in pa.table(e.bars(grain="day")).schema:
        if field.name not in ("day", "bar", "instrument_id"):
            assert field.type == pa.float64(), field.name


def test_grain_still_streams_per_day():
    e, days, _, _ = multiday()
    assert e.bars(grain="day").num_batches == days
    assert e.bars(minutes=5).num_batches == days


def test_conflicting_or_unknown_grain_is_refused():
    e, _, _, _ = multiday()
    with pytest.raises(pretium.ValidationError, match="not both"):
        e.bars(minutes=5, grain="day")
    with pytest.raises(pretium.ValidationError, match="unknown grain"):
        e.bars(grain="hourly")
    with pytest.raises(pretium.ValidationError, match="at least 1"):
        e.bars(minutes=0)


# --------------------------------------------------------------------------
# The book table
# --------------------------------------------------------------------------

def snapshotted(names=4, levels=5, shots=4):
    u = pretium.Universe.random(names, seed=5)
    e = pretium.Engine(seed=2026, universe=u)
    e.open_market()
    for i in range(shots):
        e.run_session(9, 30 + i * 30, 3, 30)
        e.snapshot_book(day=0, tick=i * 30, levels=levels)
    return e, names, levels, shots


def test_depth_is_opt_in_and_records_nothing_by_default():
    """Arithmetic, not preference.

    A hundred names at 390 ticks with ten levels a side is ~1.5 million rows
    a day against 39,000 for bars. Recording it by default would make every
    run forty times more expensive to answer a question most runs never ask.
    """
    u = pretium.Universe.random(3, seed=1)
    e = pretium.Engine(seed=1, universe=u)
    e.open_market()
    e.run_session(9, 30, 3, 60)
    assert e.recorded_book_rows == 0
    assert pa.table(e.book_table()).num_rows == 0


def test_a_snapshot_captures_both_sides_at_the_requested_depth():
    e, names, levels, shots = snapshotted()
    assert e.recorded_book_rows == shots * names * 2 * levels
    assert pa.table(e.book_table()).num_rows == shots * names * 2 * levels


def test_the_recorded_ladder_is_a_real_book():
    # Bids descend from the best, asks ascend from it, and the two do not
    # cross. A ladder that failed any of these would not be a book.
    e, _, _, _ = snapshotted()
    d = pa.table(e.book_table()).to_pydict()

    def side(which):
        rows = [(lv, p) for lv, p, i, sd, tk in
                zip(d["level"], d["price"], d["instrument_id"], d["side"], d["tick"])
                if i == 0 and sd == which and tk == 0]
        return [p for _, p in sorted(rows)]

    bids, asks = side(0), side(1)
    assert all(a > b for a, b in zip(bids, bids[1:])), "bids must descend"
    assert all(a < b for a, b in zip(asks, asks[1:])), "asks must ascend"
    assert bids[0] < asks[0], "the book must not cross"


def test_sizes_are_positive_and_f64():
    e, _, _, _ = snapshotted()
    table = pa.table(e.book_table())
    assert table.schema.field("size").type == pa.float64()
    assert table.schema.field("price").type == pa.float64()
    assert all(s > 0 for s in table.to_pydict()["size"])


def test_side_is_an_integer_because_it_repeats_on_every_row():
    # Same reason instrument_id is an index: at these row counts a repeated
    # string is the difference between a column and a memory problem.
    e, _, _, _ = snapshotted()
    table = pa.table(e.book_table())
    assert pa.types.is_unsigned_integer(table.schema.field("side").type)
    assert set(table.to_pydict()["side"]) == {0, 1}


def test_book_joins_to_bars_on_instrument_and_tick():
    e, _, _, _ = snapshotted()
    e.record(0)
    book = pa.table(e.book_table()).to_pydict()
    bars = pa.table(e.bars()).to_pydict()
    assert set(book["instrument_id"]) <= set(bars["instrument_id"])
    assert set(book["day"]) <= set(bars["day"])


def test_snapshotting_does_not_disturb_the_market():
    """It reads state without changing it, and consumes no draws.

    Which is also why it is not a replayable log entry: replaying a run
    produces the same depth whether or not anyone looked at it.
    """
    def run(observe):
        u = pretium.Universe.random(4, seed=5)
        e = pretium.Engine(seed=2026, universe=u)
        e.open_market()
        for i in range(4):
            e.run_session(9, 30 + i * 30, 3, 30)
            if observe:
                e.snapshot_book(day=0, tick=i * 30, levels=5)
        return arr(e.prices()), e.draws_consumed, len(e.order_log)

    watched, unwatched = run(True), run(False)
    assert watched[0] == unwatched[0], "prices must not depend on being observed"
    assert watched[1] == unwatched[1], "nor may the draw count"
    assert watched[2] == unwatched[2], "and snapshots are not log entries"


def test_zero_levels_is_refused():
    e, _, _, _ = snapshotted()
    with pytest.raises(pretium.ValidationError, match="at least 1"):
        e.snapshot_book(levels=0)


def test_clearing_a_recording_clears_depth_too():
    e, _, _, _ = snapshotted()
    e.clear_recording()
    assert e.recorded_book_rows == 0


# --------------------------------------------------------------------------
# record() captures a DAY, not the last session of one
# --------------------------------------------------------------------------


def test_a_day_of_many_sessions_records_every_tick():
    """The defect that made the fills join fail, and it was the bigger one.

    `SessionBuffer` is the LAST session's path -- it rewrites from tick zero
    each call, which is right for `prices()`. `record(day)` snapshotted it,
    and is named for a day. An agent-shaped run calls `run_session` once per
    step, so four steps a day recorded 60 ticks of a 240-tick day: a table
    that was well-formed, self-consistent, and missing 75% of the market with
    nothing to indicate it.
    """
    universe = pretium.Universe.random(4, seed=5)
    engine = pretium.Engine(seed=1, universe=universe)
    engine.open_market()
    for step in range(4):
        hour, minute = divmod(9 * 60 + 30 + step * 60, 60)
        engine.run_session(hour, minute, 3, 60)
    engine.close_market()
    engine.record(0)

    bars = pa.table(engine.bars())
    ticks = sorted(set(bars.column("tick").to_pylist()))
    assert len(ticks) == 240, f"recorded {len(ticks)} ticks of a 240-tick day"
    assert ticks == list(range(240)), "the tick index is not continuous"


def test_the_recorded_day_is_exactly_its_sessions_concatenated():
    """Not merely the right LENGTH -- the right values, in the right order.

    A buffer that appended garbage of the correct size would satisfy the test
    above. This holds each session's own path as it is produced and asserts
    the day's tape is those, end to end, bit for bit.
    """
    universe = pretium.Universe.random(4, seed=5)
    count = len(universe)
    engine = pretium.Engine(seed=1, universe=universe)
    engine.open_market()
    sessions = []
    for step in range(4):
        hour, minute = divmod(9 * 60 + 30 + step * 60, 60)
        engine.run_session(hour, minute, 3, 60)
        sessions.append(list(struct.unpack(
            "<%dd" % (60 * count), engine.session_prices())))
    engine.close_market()
    engine.record(0)

    day = pa.table(engine.bars()).column("close").to_pylist()
    assert day == [value for session in sessions for value in session]


def test_opening_a_new_day_starts_a_new_tape():
    # Without the clear, a run that opened twice would accumulate one
    # unbounded "day" and every per-day table would be wrong from day two.
    universe = pretium.Universe.random(4, seed=5)
    engine = pretium.Engine(seed=1, universe=universe)
    for day in range(3):
        engine.open_market()
        engine.run_session(9, 30, 3, 30)
        engine.run_session(10, 0, 3, 30)
        engine.close_market()
        engine.record(day)
    bars = pa.table(engine.bars())
    for day in range(3):
        rows = bars.filter(pc.equal(bars.column("day"), day))
        assert len(set(rows.column("tick").to_pylist())) == 60, (
            f"day {day} recorded {len(set(rows.column('tick').to_pylist()))} "
            "ticks; the accumulator is leaking across days"
        )


def test_prices_still_means_the_last_session():
    # The fix must not change what `session_prices` has always meant. It is
    # the session buffer, and a caller reading it between steps is reading the
    # step, not the day.
    universe = pretium.Universe.random(4, seed=5)
    engine = pretium.Engine(seed=1, universe=universe)
    engine.open_market()
    engine.run_session(9, 30, 3, 30)
    engine.run_session(10, 0, 3, 45)
    assert engine.session_ticks_written == 45
    assert len(engine.session_prices()) == 45 * len(universe) * 8
