"""The earnings calendar (`earnings_surprise_sigma` and its four companions).

Each public company reports once a quarter, on a reaction session drawn from
the forty real names' offsets into the quarter (EDGAR 8-K Item 2.02, 2015-2025)
with a jitter of up to three sessions. The report's surprise is realised at
that session's OPENING PRINT, the reaction session's own discovery and the
next session's follow-through join fair value after the print, and the
reaction session trades a multiple of its volume. The calendar is public, as a
real one is: `Engine.earnings_calendar()` lists the dates ahead, and nothing
about a surprise is readable before the open that prints it.

These tests hold that it is off on every preset, that the calendar is one
report per name per quarter and a function of the run alone, that every
surprise is realised at the opening print and only there, that the sizes move
nothing about the dates, and that a snapshot carries the calendar's key only
while the calendar runs and a restore into an engine built from another seed
reports on the same dates.
"""

import math
import struct

import pytest

import tradefloor as tf
from tradefloor import manifest

UNIVERSE = tf.Universe.random(8, seed=11)
ARM = {"overnight_market_share": 0.55, "overnight_idio_share": 0.2,
       "earnings_surprise_sigma": 3.1, "earnings_surprise_df": 4.0}


def floats(raw):
    return list(struct.unpack("<%dd" % (len(raw) // 8), raw))


def engine(seed=7, **dials):
    return tf.Engine(seed=seed, universe=UNIVERSE,
                     model=tf.ModelParams.from_preset("pt-v20", **{**ARM, **dials}))


@pytest.mark.parametrize("preset", tf.preset_names())
def test_off_on_every_shipped_preset(preset):
    d = tf.ModelParams.from_preset(preset).to_dict()
    for name in ("earnings_surprise_sigma", "earnings_surprise_df", "earnings_session_sigma",
                 "earnings_followthrough_sigma", "earnings_volume_multiple"):
        assert d[name] == 0.0
    e = tf.Engine(seed=1, universe=UNIVERSE, model=preset)
    assert e.earnings_calendar() == []
    assert "earnings_key" not in e.state_snapshot()


@pytest.mark.parametrize("dials", [
    # The surprise is realised at the opening print, which only a split prices.
    {"overnight_market_share": 0.0, "overnight_idio_share": 0.0},
    {"earnings_surprise_sigma": -1.0},
    {"earnings_surprise_df": 2.0},
    {"earnings_session_sigma": 25.0},
    {"earnings_volume_multiple": 11.0},
])
def test_the_ranges(dials):
    with pytest.raises(Exception):
        tf.ModelParams.from_preset("pt-v20", **{**ARM, **dials})


def test_one_report_a_name_a_quarter_from_the_real_offsets():
    e = engine()
    cal = e.earnings_calendar(horizon=63 * 8)
    assert cal == sorted(cal, key=lambda r: r["session"])
    tickers = list(e.tickers)
    for t in tickers:
        sessions = [r["session"] for r in cal if r["ticker"] == t]
        quarters = [s // 63 for s in sessions]
        assert quarters == list(range(8)), (t, sessions)
        # Inside the quarter, never on its first session, and within the
        # real offsets (8 to 58) plus the jitter of three.
        for s in sessions:
            assert 5 <= s % 63 <= 61
        for r in cal:
            assert r["sessions_ahead"] == r["session"]


def test_the_calendar_is_the_runs_and_not_the_sizes():
    # The same seed lists the same dates whatever the sizes; another seed
    # lists others. The dates ahead move with the day and nothing else.
    a = engine(earnings_session_sigma=1.5).earnings_calendar(horizon=252)
    b = engine(earnings_surprise_sigma=1.0, earnings_surprise_df=0.0).earnings_calendar(horizon=252)
    c = engine(seed=8).earnings_calendar(horizon=252)
    assert a == b
    assert [r["session"] for r in a] != [r["session"] for r in c]
    e = engine()
    e.run_days(10, record=False, ticks_per_day=30)
    later = e.earnings_calendar(horizon=242)
    assert [(r["ticker"], r["session"]) for r in later] == \
        [(r["ticker"], r["session"]) for r in a if r["session"] >= 10]
    assert all(r["sessions_ahead"] == r["session"] - 10 for r in later)


def test_every_surprise_is_realised_at_the_opening_print_and_only_there():
    e = engine()
    tickers = list(e.tickers)
    cal = {(r["ticker"], r["session"]) for r in e.earnings_calendar(horizon=130)}
    xs, gaps, quiet = [], [], []
    for day in range(130):
        closes, jumps = floats(e.prices()), floats(e.attribution("jump"))
        e.open_market()
        x = floats(e.earnings_surprises())
        opens = floats(e.column("open"))
        moved = floats(e.attribution("overnight"))
        for i, t in enumerate(tickers):
            reacts = (t, day) in cal
            assert (x[i] != 0.0) is reacts, (day, t)
            if day == 0:
                continue
            # The open's move from the close, less the night's move on `s`
            # and the close's jump. What else is in it is the last print's
            # settlement against the model price, which the open prints (a
            # few basis points, under one and a half per cent).
            gap = math.log(opens[i] / closes[i]) - moved[i] - jumps[i]
            if reacts:
                xs.append(x[i])
                gaps.append(gap)
            else:
                quiet.append(gap)
        e.run_session(9, 30, 3, 30)
        e.close_market()
    assert len(xs) == 2 * len(tickers)
    assert max(abs(g) for g in quiet) < 0.015
    # The surprise is about a per cent at this size (3.1 non-market draw
    # sigmas) and the open prints it whole: the slope of the gap on the
    # surprise is one, less the small fraction the buyback term's elasticity
    # hands back.
    assert sum(abs(x) for x in xs) / len(xs) > 0.006
    slope = sum(g * x for g, x in zip(gaps, xs)) / sum(x * x for x in xs)
    assert 0.93 < slope < 1.03
    for g, x in zip(gaps, xs):
        assert abs(g - x) < 0.015 + 0.05 * abs(x)


def test_the_session_and_the_day_after_trade_their_parts_in():
    # On the same seed the calendar and every draw before the first report
    # agree; the reaction session's own part and the follow-through leave
    # the open alone and move the session.
    arms = [engine(), engine(earnings_session_sigma=1.5, earnings_followthrough_sigma=1.1)]
    cal = arms[0].earnings_calendar(horizon=63)
    first = min(r["session"] for r in cal)
    names = [list(arms[0].tickers).index(r["ticker"]) for r in cal if r["session"] == first]
    for x in arms:
        x.run_days(first, record=False, ticks_per_day=30)
        x.open_market()
    a, b = (floats(x.column("open")) for x in arms)
    assert a == b
    for x in arms:
        x.run_session(9, 30, 3, 30)
    a, b = (floats(x.prices()) for x in arms)
    for i in names:
        assert a[i] != b[i]


def test_the_reaction_session_trades_its_volume_multiple():
    arms = [engine(), engine(earnings_volume_multiple=3.0)]
    cal = arms[0].earnings_calendar(horizon=63)
    first = min(r["session"] for r in cal)
    names = {list(arms[0].tickers).index(r["ticker"]) for r in cal if r["session"] == first}
    for x in arms:
        x.run_days(first, record=False, ticks_per_day=30)
        x.open_market()
        x.run_session(9, 30, 3, 30)
    n = len(UNIVERSE)
    va, vb = (floats(x.session_volumes()) for x in arms)
    for i in range(n):
        sa = sum(va[t * n + i] for t in range(30))
        sb = sum(vb[t * n + i] for t in range(30))
        if i in names:
            assert sb / sa == pytest.approx(3.0, rel=0.05)
        else:
            assert sb == sa


def test_the_key_is_carried_only_while_the_calendar_runs():
    split_only = tf.Engine(seed=7, universe=UNIVERSE, model=tf.ModelParams.from_preset(
        "pt-v20", overnight_idio_share=0.2))
    assert "earnings_key" not in split_only.state_snapshot()
    e = engine(earnings_session_sigma=1.5, earnings_followthrough_sigma=1.1,
               earnings_volume_multiple=1.3)
    e.run_days(12, record=False, ticks_per_day=30)
    e.open_market()
    e.run_session(9, 30, 3, 15)
    snap = e.state_snapshot()
    assert "earnings_key" in snap
    assert manifest.state_hash(snap) == e.state_hash()
    # Built from another seed and restored: the key comes with the snapshot,
    # so the twin reports on the same dates and prices the same reports.
    twin = engine(seed=123, earnings_session_sigma=1.5, earnings_followthrough_sigma=1.1,
                  earnings_volume_multiple=1.3)
    assert twin.earnings_calendar(horizon=252) != e.earnings_calendar(horizon=252)
    twin.restore_state(snap)
    assert twin.state_hash() == e.state_hash()
    assert twin.earnings_calendar(horizon=252) == e.earnings_calendar(horizon=252)
    for x in (e, twin):
        x.run_session(9, 45, 3, 15)
        x.close_market()
        x.run_days(40, record=False, ticks_per_day=30, first_day=13)
    assert floats(e.prices()) == floats(twin.prices())


def test_a_name_learns_its_part_of_the_cycle_from_its_own_report():
    # `earnings_cycle_report_share`: the share of the cycle's move each
    # traded name holds back is kept per name, the same for every name that
    # last reported on the same session, and given back (to zero) at the
    # name's next opening print. Carried by the snapshot only while it runs.
    assert "earnings_withheld" not in engine().state_snapshot()
    e = engine(earnings_cycle_report_share=1.0)
    tickers = list(e.tickers)
    cal = e.earnings_calendar(horizon=200)
    last = {t: -1 for t in tickers}
    for day in range(160):
        e.open_market()
        held = floats(e.state_snapshot()["earnings_withheld"])
        for r in cal:
            if r["session"] == day:
                last[r["ticker"]] = day
                assert held[tickers.index(r["ticker"])] == 0.0
        e.run_session(9, 30, 3, 10)
        e.close_market()
    snap = e.state_snapshot()
    held = floats(snap["earnings_withheld"])
    assert any(h != 0.0 for h in held)
    by_last = {}
    for t, h in zip(tickers, held):
        by_last.setdefault(last[t], set()).add(round(h, 12))
    assert all(len(v) == 1 for v in by_last.values())
    assert manifest.state_hash(snap) == e.state_hash()
    twin = engine(seed=5, earnings_cycle_report_share=1.0)
    twin.restore_state(snap)
    assert twin.state_hash() == e.state_hash()
    with pytest.raises(Exception):
        tf.ModelParams.from_preset("pt-v20", earnings_cycle_report_share=0.5)
