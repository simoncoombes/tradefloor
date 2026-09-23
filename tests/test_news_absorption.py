"""The day's news as the engine holds it, and how fast its move is priced.

Two read-only accessors, `Engine.session_news()` and `Engine.session_tick`,
for `tradefloor.headlines`: they must change nothing, so a run that calls
them at every tick hashes the same as one that never does.

And `news_absorption_half_life` with its two companions, which move WHEN an
endogenous event's move lands and never how much of it: shipped at 0.0, the
straight line over the session every preset has always priced.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

import tradefloor as tf

ROSTER = dict(n=40, seed=111)


def _engine(model="pt-v19", seed=7):
    return tf.Engine(seed=seed, universe=tf.Universe.random(ROSTER["n"], seed=ROSTER["seed"]),
                     model=model)


def _col(e, name):
    return np.frombuffer(e.attribution(name), "<f8")


# -- the accessors ---------------------------------------------------------------


def test_reading_the_news_and_the_tick_changes_nothing():
    """A run that reads both accessors at every tick and every boundary
    reaches the same state hash, prices and draw counts as one that never
    reads them."""
    quiet, loud = _engine(), _engine()
    for _ in range(6):
        quiet.open_market()
        quiet.run_session(9, 30, 3, 390)
        quiet.close_market()

        loud.session_news(), loud.session_tick
        loud.open_market()
        loud.session_news(), loud.session_tick
        for m in range(390):
            loud.run_session(9 + (30 + m) // 60, (30 + m) % 60, 3, 1)
            loud.session_news(), loud.session_tick
        loud.close_market()
        loud.session_news(), loud.session_tick
    assert loud.state_hash() == quiet.state_hash()
    assert loud.prices() == quiet.prices()
    assert loud.draws_by_stream() == quiet.draws_by_stream()


def test_session_news_names_real_tickers_and_matches_the_snapshot():
    e = _engine()
    tickers = list(e.tickers)
    by_id = dict(zip(e.state_snapshot()["tickers"], tickers))
    seen = 0
    for day in range(30):
        e.open_market()
        mine = e.session_news()
        snap = e.state_snapshot()
        assert len(mine) == len(snap["session_news"])
        for ours, raw in zip(mine, snap["session_news"]):
            assert ours["ticker"] == by_id[raw["ticker"]]
            assert ours["ticker"] in tickers
            assert ours["sector"] == raw["sector"]
            assert ours["price_impact"] == raw["price_impact"]
            assert ours["day"] == day
        e.run_session(9, 30, 3, 390)
        e.close_market()
        # Kept through the close, and still the day just finished.
        assert [n["day"] for n in e.session_news()] == [day] * len(mine)
        seen += len(mine)
    assert seen > 0


def test_session_news_is_empty_before_the_first_open_and_with_news_off():
    assert _engine().session_news() == []
    off = _engine(model="pt-v10")
    off.run_days(5)
    assert off.session_news() == []


def test_session_tick_counts_the_ticks_since_the_open():
    e = _engine()
    assert e.session_tick is None
    e.open_market()
    assert e.session_tick == 0
    e.run_session(9, 30, 3, 30)
    assert e.session_tick == 30
    e.tick(10, 0, 3)
    assert e.session_tick == 31
    e.run_session(10, 1, 3, 359)
    assert e.session_tick == 390
    e.close_market()
    assert e.session_tick == 390
    e.open_market()
    assert e.session_tick == 0


def test_a_restored_engine_does_not_claim_a_session_tick():
    """The count is recording state, like the day's tape: a snapshot does not
    carry it, so a restored engine says it does not know until its next
    open, rather than reporting a wrong clock."""
    e = _engine()
    e.open_market()
    e.run_session(9, 30, 3, 100)
    snap = e.state_snapshot()
    r = _engine()
    r.restore_state(snap)
    assert r.session_tick is None
    r.run_session(11, 10, 3, 290)
    r.close_market()
    r.open_market()
    assert r.session_tick == 0


# -- the absorption dial -----------------------------------------------------------


def _share(n, h, d=0.0, hd=0.0):
    """The profile, written independently of the engine."""
    def comp(n, h):
        if n <= 0:
            return 0.0
        if n >= 390:
            return 1.0
        if h == 0.0:
            return n / 390.0
        return (1 - 2 ** (-n / h)) / (1 - 2 ** (-390 / h))
    return (1 - d) * comp(n, h) + d * comp(n, hd)


def test_it_ships_inert_on_every_preset():
    for name in tf.preset_names():
        m = tf.ModelParams.from_preset(name).to_dict()
        assert m["news_absorption_half_life"] == 0.0, name
        assert m["news_absorption_drift_share"] == 0.0, name
        assert m["news_absorption_drift_half_life"] == 0.0, name
        assert m["news_quote_revision"] == 0.0, name


FAST = dict(news_absorption_half_life=1.0, news_absorption_drift_share=0.2,
            news_absorption_drift_half_life=30.0)


@pytest.mark.parametrize("dials", [
    dict(news_absorption_half_life=1.0),
    FAST,
    dict(news_absorption_half_life=2.0, news_absorption_drift_share=0.3),
])
def test_the_profile_moves_when_the_move_lands_not_how_much(dials):
    """Same seed, same events (the NEWS stream does not read prices). Tick by
    tick the announcer's `company_news` follows the profile; by the close it
    is the whole `price_impact`, as on the shipped straight line."""
    m = tf.ModelParams.from_preset("pt-v19", **dials)
    fast, line = _engine(model=m), _engine()
    for e in (fast, line):
        e.run_days(3)
        e.open_market()
    events = fast.session_news()
    assert events == line.session_news() and events
    idx = [list(fast.tickers).index(ev["ticker"]) for ev in events]
    h = dials["news_absorption_half_life"]
    d = dials.get("news_absorption_drift_share", 0.0)
    hd = dials.get("news_absorption_drift_half_life", 0.0)
    done = 0
    for upto in (1, 5, 30, 390):
        for e in (fast, line):
            e.run_session(9 + (30 + done) // 60, (30 + done) % 60, 3, upto - done)
        done = upto
        a_fast, a_line = _col(fast, "company_news"), _col(line, "company_news")
        checked = 0
        for i, ev in zip(idx, events):
            if sum(o["sector"] == ev["sector"] for o in events) > 1:
                continue    # a peer's transfer would ride on the same column
            checked += 1
            x = ev["price_impact"]
            assert a_fast[i] == pytest.approx(x * _share(upto, h, d, hd), rel=1e-9)
            assert a_line[i] == pytest.approx(x * upto / 390, rel=1e-9)
        assert checked
    # The whole day: every name's news total is the same on both clocks,
    # peers' transfer included.
    np.testing.assert_allclose(_col(fast, "company_news"), _col(line, "company_news"),
                               rtol=1e-9, atol=1e-15)


def test_the_first_tick_carries_the_fast_part():
    m = tf.ModelParams.from_preset("pt-v19", **FAST)
    e = _engine(model=m)
    for _ in range(40):
        e.open_market()
        events = e.session_news()
        e.run_session(9, 30, 3, 1)
        if len(events) == 1:
            i = list(e.tickers).index(events[0]["ticker"])
            got = _col(e, "company_news")[i] / events[0]["price_impact"]
            assert got == pytest.approx(_share(1, 1.0, 0.2, 30.0), rel=1e-9)
            assert got > 0.35
            return
        e.run_session(9, 31, 3, 389)
        e.close_market()
    pytest.fail("no single-event day in 40")


def test_off_session_ticks_price_none_of_it():
    """The shipped line prices 1/390 on a pre-market tick too; the profile is
    keyed on the minute of the session and prices nothing off it."""
    m = tf.ModelParams.from_preset("pt-v19", **FAST)
    e = _engine(model=m)
    for _ in range(40):
        e.open_market()
        if e.session_news():
            break
        e.run_session(9, 30, 3, 390)
        e.close_market()
    e.tick(8, 0, 3)
    assert not _col(e, "company_news").any()


@pytest.mark.parametrize("bad, match", [
    (dict(news_absorption_half_life=-1.0), "half-life"),
    (dict(news_absorption_half_life=400.0), "half-life"),
    (dict(news_absorption_drift_share=0.2), "read by nothing"),
    (dict(news_absorption_half_life=1.0, news_absorption_drift_share=1.5), "share"),
    (dict(news_absorption_half_life=1.0, news_absorption_drift_half_life=30.0),
     "read by nothing"),
])
def test_out_of_domain_values_are_refused(bad, match):
    with pytest.raises(Exception, match=match):
        tf.ModelParams.from_preset("pt-v19", **bad)


def test_the_weights_sum_to_the_whole_move():
    """390 ticks of the profile price the whole move, up to rounding."""
    for h, d, hd in [(0.5, 0.0, 0.0), (1.0, 0.2, 30.0), (5.0, 0.4, 0.0), (390.0, 0.0, 0.0)]:
        total = sum(_share(m + 1, h, d, hd) - _share(m, h, d, hd) for m in range(390))
        assert math.isclose(total, 1.0, rel_tol=1e-12)


def _print_share_at_tick_one(model, days=80):
    """Slope of the announcer's first-tick print move on its impact."""
    e = _engine(model=model, seed=11)
    n = len(e.tickers)
    xs, ys = [], []
    for _ in range(days):
        e.open_market()
        events = e.session_news()
        before = np.frombuffer(e.prices(), "<f8").copy()
        e.run_session(9, 30, 3, 1)
        after = np.frombuffer(e.session_prices(), "<f8").reshape(1, n)[0]
        for ev in events:
            i = list(e.tickers).index(ev["ticker"])
            xs.append(ev["price_impact"])
            ys.append(math.log(after[i] / before[i]))
        e.run_session(9, 31, 3, 389)
        e.close_market()
    x, y = np.array(xs), np.array(ys)
    assert len(x) > 50
    return float((x * y).sum() / (x * x).sum())


def test_the_maker_requotes_on_news_so_the_print_follows_the_model():
    """Priced fast in the model, the move still reaches the tape only as
    fast as the flow walks a book quoted around the last print. With the
    re-quote on, the first tick's print carries about the profile's share."""
    profile = dict(news_absorption_half_life=0.6, news_absorption_drift_share=0.12,
                   news_absorption_drift_half_life=42.0)
    lagged = _print_share_at_tick_one(tf.ModelParams.from_preset("pt-v19", **profile))
    requoted = _print_share_at_tick_one(
        tf.ModelParams.from_preset("pt-v19", **profile, news_quote_revision=1.0))
    target = _share(1, 0.6, 0.12, 42.0)
    assert lagged < 0.3 * target
    assert abs(requoted - target) < 0.1
