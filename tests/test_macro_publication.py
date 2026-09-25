"""A macro decision is priced the moment it is published
(`macro_publication_repricing`).

The close's macro step -- the economy, the cycle, the central bank's meeting
and the corporate yield it re-anchors -- is readable as soon as the step ends,
and with the switch off fair value reached prices only at the next session's
first tick, so an agent that read a hike at the open sold at the price from
before it (pt-v20 audit, finding 3). With the switch on, each name is
re-marked as the step ends by the change in its fair value, its mispricing
untouched (`Engine::reprice_to_published_macro`), and a `pin_macro` the same
way. These tests hold what that is for: it is off on every shipped preset, a
published change is in the price before any trade can be made, the re-mark
moves no mispricing, and the first minutes after publication no longer carry
the published move.
"""

import math
import struct

import numpy as np
import pytest

import tradefloor as tf

UNIVERSE = list(tf.Universe.random(8, seed=3))
LEAD = dict(earnings_anticipation_half_life=126.0, rate_pe_sensitivity=3.0,
            buyback_payout_share=0.75)


def model(on):
    return tf.ModelParams.from_preset(
        "pt-v20", macro_publication_repricing=1.0 if on else 0.0, **LEAD)


def column(engine, field):
    raw = engine.column(field)
    return np.array(struct.unpack("<%dd" % (len(raw) // 8), raw))[:len(UNIVERSE)]


def prices(engine):
    raw = engine.prices()
    return np.array(struct.unpack("<%dd" % (len(raw) // 8), raw))[:len(UNIVERSE)]


def index(engine):
    return float(np.exp(np.log(prices(engine)).mean()))


@pytest.mark.parametrize("preset", tf.preset_names())
def test_off_on_every_shipped_preset(preset):
    assert tf.ModelParams.from_preset(preset).to_dict()[
        "macro_publication_repricing"] == 0.0


def test_it_is_a_switch():
    with pytest.raises(Exception, match="switch"):
        tf.ModelParams.from_preset("pt-v20", macro_publication_repricing=0.5)


def mid_session(on, seed=7):
    e = tf.Engine(seed=seed, universe=UNIVERSE, model=model(on))
    e.run_days(2)
    e.open_market()
    e.run_session(9, 30, 3, 30)
    return e


def test_a_pin_is_in_the_price_the_moment_it_is_written():
    """A one-point rise in the corporate yield, written mid-session: on, the
    prices fall before another minute trades and the mispricing does not
    move; off, nothing moves until the next tick."""
    for on in (True, False):
        e = mid_session(on)
        p0, s0 = prices(e), column(e, "mispricing_s")
        e.pin_macro(corporate_bond_yield=e.macro_fields["corporate_bond_yield"] + 0.01)
        p1, s1 = prices(e), column(e, "mispricing_s")
        assert np.array_equal(s0, s1), "a re-mark is not a mispricing"
        if on:
            assert np.all(p1 <= p0) and np.mean(np.log(p1 / p0)) < -0.01, (p0, p1)
        else:
            assert np.array_equal(p0, p1)


def test_the_first_minute_after_a_pin_carries_no_published_move():
    """The same market pinned the same way, on and off. Off, the first tick
    carries the whole move; on, it was in the price already, so after one
    tick the two markets stand at the same prices."""
    on, off = mid_session(True), mid_session(False)
    before = prices(off)
    for e in (on, off):
        e.pin_macro(corporate_bond_yield=e.macro_fields["corporate_bond_yield"] + 0.01)
    published = np.log(prices(on) / before)
    first_on = index(on)
    for e in (on, off):
        e.run_session(10, 0, 3, 1)
    moved_off = np.log(prices(off) / before)
    assert np.mean(moved_off) < -0.01, "off, the first tick takes the move"
    gap = np.abs(np.log(prices(on) / prices(off)))
    assert gap.max() < 0.1 * np.abs(published).mean(), (gap, published)
    # And on, the tick itself moved the index by a minute's noise, not the pin.
    assert abs(math.log(index(on) / first_on)) < 0.2 * abs(published.mean())


def overnight_study(on, days=160, seed=11):
    """Per close: the change in the published corporate yield, the index's
    move from the day's last print to the price readable after the close,
    and its move from there to the end of the next session's first half
    hour."""
    e = tf.Engine(seed=seed, universe=UNIVERSE, model=model(on))
    e.run_days(1)
    readable, y = index(e), e.macro_fields["corporate_bond_yield"]
    dy, night, after = [], [], []
    for day in range(days):
        e.open_market()
        e.run_session(9, 30, 3, 30)
        if day:
            after.append(math.log(index(e) / readable))
        e.run_session(10, 0, 3, 360)
        last = index(e)
        e.close_market()
        readable, y1 = index(e), e.macro_fields["corporate_bond_yield"]
        dy.append(y1 - y)
        night.append(math.log(readable / last))
        y = y1
    return np.array(dy[:-1]), np.array(night[:-1]), np.array(after)


def test_the_open_no_longer_trades_on_the_published_yield():
    """Off, the price readable after the close is the day's last print, and
    the next half hour moves against the change in the yield just published
    (a rise in the yield is a fall in fair value the price has not taken).
    On, the price takes the change between the last print and the price
    readable after the close, and the next half hour no longer carries it:
    the two markets share their draws, so the difference of their half hours
    is the published move the open was still trading on, and nothing else."""
    dy_off, night_off, after_off = overnight_study(False)
    dy_on, night_on, after_on = overnight_study(True)
    assert np.all(night_off == 0.0)
    assert np.corrcoef(dy_on, night_on)[0, 1] < -0.99
    slope_off = np.polyfit(dy_off, after_off, 1)[0]
    slope_on = np.polyfit(dy_on, after_on, 1)[0]
    assert slope_off < -1.5, slope_off
    assert abs(slope_on) < 0.4 * abs(slope_off), (slope_on, slope_off)
    assert np.corrcoef(dy_on, after_off - after_on)[0, 1] < -0.8


# ── With the publication dials ─────────────────────────────────────────────
#
# The re-mark prices the close's macro step on the engine's TRUE state (fair
# value reads the economy the engine runs: the corporate yield, the bank,
# QE), never on what it publishes. The publication lags and the fear/greed
# switch change only what an observer reads, so with them on the prices are
# the prices without them, re-mark and all; the unemployment half-life moves
# the true rate, so it is held equal on both sides. And the five together
# carry across a snapshot, a restore, a fork and a replayed checkpoint.

PUBLISHED = dict(cycle_publication_lag=25.0, gdp_publication_lag=21.0,
                 fear_greed_published_inputs=1.0)


def all_five(published=True):
    return tf.ModelParams.from_preset(
        "pt-v20", macro_publication_repricing=1.0,
        unemployment_adjustment_half_life=84.0,
        **(PUBLISHED if published else {}), **LEAD)


def trace(engine, days, pins=None):
    """Per close: the prices, what is published, the true phase, growth and
    unemployment rate, and the draws the session took.
    `pins[d]` is written before session `d` (a pin re-marks too)."""
    pins = pins or {}
    out = []
    for d in range(1, days + 1):
        if d in pins:
            engine.pin_macro(**pins[d])
        # The draws the session took, not the counter: a restore does not
        # carry the counter (a documented omission, tests/test_forking.py).
        drawn = engine.draws_consumed
        engine.run_days(1)
        eco = engine.state_snapshot()["economy"]
        out.append((tuple(prices(engine)), dict(engine.macro_fields),
                    eco["cycle_phase"], eco["gdp_growth"],
                    eco["unemployment_rate"], engine.draws_consumed - drawn))
    return out


def test_the_re_mark_reads_the_true_state_not_the_published():
    """A contraction pinned on day 5: true at once, published 25 sessions
    later. The prices, re-marks and all, are the prices of the same market
    publishing without a lag; only what is published differs."""
    pins = {5: dict(cycle="contraction")}
    lagged = trace(tf.Engine(seed=13, universe=UNIVERSE, model=all_five(True)),
                   40, pins)
    plain = trace(tf.Engine(seed=13, universe=UNIVERSE, model=all_five(False)),
                  40, pins)
    for a, b in zip(lagged, plain):
        assert a[0] == b[0], "a published field reached a price"
        assert a[2:] == b[2:], "the true economy moved"
    # The lag did hold the turn back from what is published ...
    assert any(a[1]["cycle"] != a[2] for a in lagged)
    assert any(a[1]["cycle"] != b[1]["cycle"] for a, b in zip(lagged, plain))
    # ... and the re-mark did run: after a close the price is not the last
    # print, so the switch was live in the combination.
    e = tf.Engine(seed=13, universe=UNIVERSE, model=all_five(True))
    e.run_days(3)
    e.open_market()
    e.run_session(9, 30, 3, 390)
    last = prices(e)
    e.close_market()
    assert not np.array_equal(prices(e), last)


def test_all_five_carry_across_a_restore_a_fork_and_a_checkpoint():
    pins = {5: dict(cycle="contraction")}
    later = {12: dict(cycle="expansion")}
    parent = tf.Engine(seed=14, universe=UNIVERSE, model=all_five())
    trace(parent, 30, pins)
    child = tf.Engine(seed=14, universe=UNIVERSE, model=all_five())
    child.restore_state(parent.state_snapshot())
    fork, = tf.branch(parent, 1)
    resumed = tf.Checkpoint.of(parent, universe=UNIVERSE, seed=14).resume()
    for engine in (child, fork, resumed):
        assert engine.state_hash() == parent.state_hash()
        assert engine.macro_fields == parent.macro_fields
    expected = trace(parent, 40, later)
    for engine in (child, fork, resumed):
        assert trace(engine, 40, later) == expected
        assert engine.state_hash() == parent.state_hash()
    # And the uninterrupted run is the one the copies continued.
    straight = tf.Engine(seed=14, universe=UNIVERSE, model=all_five())
    assert trace(straight, 70, {**pins, **{30 + d: p for d, p in later.items()}})[30:] == expected
    assert straight.state_hash() == parent.state_hash()
