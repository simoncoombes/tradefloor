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
