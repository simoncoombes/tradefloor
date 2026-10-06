"""The earnings calendar (`earnings_surprise_sigma` and its four companions).

Each public company reports once a quarter, on a reaction session drawn from
the forty real names' offsets into the quarter (EDGAR 8-K Item 2.02, 2015-2025)
with a jitter of up to three sessions. The report's surprise is realised at
that session's OPENING PRINT, the reaction session's own discovery and the
next session's follow-through are walked into fair value minute by minute
through those sessions, and the reaction session trades a multiple of its
volume. The calendar is public, as a real one is: `Engine.earnings_calendar()`
lists the dates ahead, every harness's market view serves them and the
framework adapters' payload carries each name's next report, and nothing
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


UNIVERSE_TICKERS = [i.ticker for i in UNIVERSE]


def floats(raw):
    return list(struct.unpack("<%dd" % (len(raw) // 8), raw))


def engine(seed=7, **dials):
    return tf.Engine(seed=seed, universe=UNIVERSE,
                     model=tf.ModelParams.from_preset("pt-v20", **{**ARM, **dials}))


# pt-v21, the default from 0.10.0, runs the calendar; the test after this
# one holds its values.
@pytest.mark.parametrize("preset", [p for p in tf.preset_names() if p != "pt-v21"])
def test_off_on_every_shipped_preset(preset):
    d = tf.ModelParams.from_preset(preset).to_dict()
    for name in ("earnings_surprise_sigma", "earnings_surprise_df", "earnings_session_sigma",
                 "earnings_followthrough_sigma", "earnings_volume_multiple"):
        assert d[name] == 0.0
    e = tf.Engine(seed=1, universe=UNIVERSE, model=preset)
    assert e.earnings_calendar() == []
    assert "earnings_key" not in e.state_snapshot()


def test_pt_v21_runs_the_calendar():
    d = tf.ModelParams.from_preset("pt-v21").to_dict()
    assert {name: d[name] for name in (
        "earnings_surprise_sigma", "earnings_surprise_df", "earnings_session_sigma",
        "earnings_followthrough_sigma", "earnings_volume_multiple")} == {
        "earnings_surprise_sigma": 3.5, "earnings_surprise_df": 0.0,
        "earnings_session_sigma": 1.9, "earnings_followthrough_sigma": 1.1,
        "earnings_volume_multiple": 1.2}
    e = tf.Engine(seed=1, universe=UNIVERSE, model="pt-v21")
    assert e.earnings_calendar() != []
    assert "earnings_key" in e.state_snapshot()


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


def _lockstep(dials, days, ticks=390):
    """A base arm and an arm with `dials`, run together: per day, per arm,
    the open and every tick's prices."""
    arms = [engine(), engine(**dials)]
    cal = arms[0].earnings_calendar(horizon=days)
    n = len(UNIVERSE)
    tape = []
    for _ in range(days):
        row = []
        for x in arms:
            x.open_market()
            opens = floats(x.column("open"))[:n]
            x.run_session(9, 30, 3, ticks)
            ticks_ = floats(x.session_prices())
            k = len(ticks_) // ticks
            row.append((opens, [ticks_[t * k:t * k + n] for t in range(ticks)]))
            x.close_market()
        tape.append(row)
    return cal, tape


def test_the_reaction_session_walks_its_part_in():
    # `earnings_session_sigma` is the reaction session's own discovery. It
    # leaves the open alone and is walked into fair value minute by minute,
    # so the session trades it in: the first tick carries 1/390 of it at the
    # open's intraday weight, not the whole of it. The review of 50dfeed
    # measured the first tick carrying 1.15 times the reaction session's
    # idiosyncratic variance when the part joined fair value in one piece.
    cal, tape = _lockstep({"earnings_session_sigma": 1.5}, days=12)
    tickers = list(UNIVERSE_TICKERS)
    first = min(r["session"] for r in cal)
    names = [tickers.index(r["ticker"]) for r in cal if r["session"] == first]
    assert names
    for day in range(first + 1):
        (oa, ta), (ob, tb) = tape[day]
        assert oa == ob
        if day < first:
            assert ta == tb
    (_, ta), (_, tb) = tape[first]
    for i in range(len(UNIVERSE)):
        d = [math.log(b[i] / a[i]) for a, b in zip(ta, tb)]
        if i not in names:
            assert d == [0.0] * len(d)
            continue
        steps = [d[0]] + [y - x for x, y in zip(d, d[1:])]
        walked = sum(x * x for x in steps)
        # About 1/390 of the walk's variance at the open's weight of 1.2^2
        # over the session's mean of about 1.03; a first-tick jump is 1.
        assert d[0] * d[0] / walked < 0.03
        # A walk, not a drift: the steps' own sizes carry the variance.
        assert max(abs(x) for x in steps) < 0.25 * math.sqrt(walked)


def test_the_follow_through_moves_the_session_after_and_not_the_reaction_session():
    # `earnings_followthrough_sigma` is walked in on the session AFTER the
    # reaction session: the reaction session is the base arm's to the tick
    # and the session after is not. The review of 50dfeed found no test that
    # would fail if the follow-through never ran.
    cal, tape = _lockstep({"earnings_followthrough_sigma": 1.1}, days=12)
    tickers = list(UNIVERSE_TICKERS)
    first = min(r["session"] for r in cal)
    names = {tickers.index(r["ticker"]) for r in cal if r["session"] == first}
    for day in range(first + 1):
        assert tape[day][0] == tape[day][1]
    (oa, ta), (ob, tb) = tape[first + 1]
    assert oa == ob
    for i in range(len(UNIVERSE)):
        moved = [a[i] != b[i] for a, b in zip(ta, tb)]
        if i in names:
            # Prints are in cents, so a minute's step can leave one alone.
            assert sum(moved) > 0.9 * len(moved)
        else:
            assert not any(moved)


def test_the_calendar_is_public_to_agents_and_the_surprise_is_not():
    # The calendar is what a real company announces, so every harness's
    # read-only market view serves it and the framework adapters' payload
    # says how far off each name's next report is. What the open realised
    # of the surprise is refused. On a model without the calendar the
    # payload is the one it was.
    from tradefloor import sandbox
    from tradefloor.integrations import common

    e = engine()
    e.run_days(3, record=False, ticks_per_day=30)
    view = sandbox.MarketView(e)
    assert view.earnings_calendar(40) == e.earnings_calendar(40) != []
    with pytest.raises(sandbox.SandboxError):
        view.earnings_surprises()
    assert "earnings_surprises" in sandbox.HIDDEN_STATE

    seen = {}

    class Reader:
        def act(self, obs):
            seen.setdefault("calendar", obs.engine.earnings_calendar())
            seen.setdefault("payload", common.serialize_observation(obs))
            return {}

    arm = tf.ModelParams.from_preset("pt-v20", **ARM)
    card = tf.evaluate({"reader": Reader()}, seed=7, universe=UNIVERSE, model=arm, days=1)
    assert not card["reader"].errors
    nxt = {}
    for r in seen["calendar"]:
        nxt.setdefault(r["ticker"], r["sessions_ahead"])
    assert nxt
    for asset in seen["payload"]["assets"]:
        assert asset["next_earnings_in_sessions"] == nxt.get(asset["symbol"])

    seen.clear()
    tf.evaluate({"reader": Reader()}, seed=7, universe=UNIVERSE, model="pt-v20", days=1)
    assert seen["calendar"] == []
    assert all("next_earnings_in_sessions" not in a for a in seen["payload"]["assets"])


def test_the_public_calendar_carries_no_return_signal():
    # The calendar is public, so it must not tell a trader which way a
    # report goes: the surprise, the session's part and the follow-through
    # are each mean one in level. Over many reports, the mean log move of a
    # reaction session's open against the last close is its Ito term and
    # nothing more, well inside its standard error of zero, and the session
    # before a report does not drift.
    cal_e = engine(earnings_session_sigma=1.9, earnings_followthrough_sigma=1.1)
    tickers = list(cal_e.tickers)
    n = len(tickers)
    days = 252
    cal = {(tickers.index(r["ticker"]), r["session"])
           for r in cal_e.earnings_calendar(horizon=days)}
    gaps, before = [], []
    for day in range(days):
        closes = floats(cal_e.prices())[:n]
        cal_e.open_market()
        opens = floats(cal_e.column("open"))[:n]
        cal_e.run_session(9, 30, 3, 30)
        ends = floats(cal_e.prices())[:n]
        cal_e.close_market()
        for i in range(n):
            if (i, day) in cal:
                gaps.append(math.log(opens[i] / closes[i]))
            if (i, day + 1) in cal:
                before.append(math.log(ends[i] / opens[i]))
    for xs in (gaps, before):
        m = sum(xs) / len(xs)
        sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))
        assert abs(m) < 3.0 * sd / math.sqrt(len(xs)), (m, sd, len(xs))


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
