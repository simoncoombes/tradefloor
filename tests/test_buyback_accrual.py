"""The buyback term as accrued state (`buyback_accrual`).

The term that stood, `market::tick::buyback_scale`, reads the buyback yield
at TODAY's price and applies it to every elapsed year. Fair value then moves
against the price it anchors, `d ln FV / d ln P = -b t`, with a gain that
grows with the elapsed years, and the elapsed days enter the level, so
relabelling the calendar origin moves prices.

Under the switch each name carries a running log share-count reduction `L`
that the close adds `min(payout * E * exp(L) / P_close, cap) / 252` to, and
fair value reads `exp(L)`. These tests hold that the switch is off on every
shipped preset and inert there; that `L` is the closed-form sum on the
engine's own close path; that relabelling the calendar origin leaves prices
unchanged; that fair value does not read the price within a session; and
that a snapshot carries `L`, the state hash covers it, and a restore
mid-run continues bit for bit.
"""

import math
import struct

import pytest

import tradefloor as tf
from tradefloor.manifest import state_hash

UNIVERSE = list(tf.Universe.random(12, seed=3))
N = len(UNIVERSE)


def floats(raw):
    return struct.unpack("<%dd" % (len(raw) // 8), raw)


def model(**dials):
    return tf.ModelParams.from_preset("pt-v20", **dials)


def engine(m, seed=7):
    return tf.Engine(seed=seed, universe=UNIVERSE, model=m)


def log_shares(e):
    return floats(e.state_snapshot()["buyback_log_shares"])


@pytest.mark.parametrize("preset", tf.preset_names())
def test_off_on_every_shipped_preset(preset):
    assert tf.ModelParams.from_preset(preset).to_dict()["buyback_accrual"] == 0.0


def test_off_the_snapshot_and_the_hash_are_the_ones_that_stood():
    # At 0.0 nothing is carried, and with payouts off the switch carries
    # nothing either: there is nothing to accrue.
    for m in (model(), model(buyback_payout_share=0.0, buyback_accrual=1.0)):
        e = engine(m)
        e.run_days(3, record=False)
        assert "buyback_log_shares" not in e.state_snapshot()
    on = engine(model(buyback_accrual=1.0))
    assert "buyback_log_shares" in on.state_snapshot()


def test_without_payouts_the_switch_moves_nothing():
    a = engine(model(buyback_payout_share=0.0))
    b = engine(model(buyback_payout_share=0.0, buyback_accrual=1.0))
    for e in (a, b):
        e.run_days(20, record=False)
    assert a.prices() == b.prices()


def test_the_switch_moves_the_market_with_payouts_on():
    a = engine(model())
    b = engine(model(buyback_accrual=1.0))
    for e in (a, b):
        e.run_days(20, record=False)
    assert a.prices() != b.prices()
    assert a.draws_consumed == b.draws_consumed


@pytest.mark.parametrize("value", [-1.0, 0.5, 2.0])
def test_it_is_a_switch(value):
    with pytest.raises(Exception):
        model(buyback_accrual=value)


def test_the_share_count_is_the_closed_form_sum_on_the_close_path():
    # On a model where the earnings the valuation holds are the name's own
    # fixed eps (no nominal growth, no earnings cycle, no fair-value levels)
    # and nothing re-marks the close, L after each session is
    # L + min(payout * eps * exp(L) / P_close, cap) / 252 on the close the
    # engine printed. The exp(L) is the per-share earnings rising as the
    # share count falls, and it is visible: without it the sum is lower.
    payout, cap = 0.75, 0.15
    m = tf.ModelParams.from_preset(
        "pt-v20", buyback_accrual=1.0, buyback_payout_share=payout,
        buyback_yield_cap=cap, earnings_nominal_growth=0.0,
        earnings_cycle_depth=0.0, fair_value_news_share=0.0,
        fair_value_market_share=0.0, opening_mispricing_sigma=0.0,
        opening_market_sigma=0.0, macro_publication_repricing=0.0)
    e = engine(m)
    want = [0.0] * N
    without_growth = [0.0] * N
    for day in range(40):
        e.run_days(1, record=False, first_day=day)
        closes = floats(e.prices())
        for i, inst in enumerate(UNIVERSE):
            if not (inst.eps > 0.0):
                continue
            want[i] += min(payout * inst.eps * math.exp(want[i]) / closes[i], cap) / 252.0
            without_growth[i] += min(payout * inst.eps / closes[i], cap) / 252.0
    got = log_shares(e)
    for i, inst in enumerate(UNIVERSE):
        if inst.eps > 0.0:
            assert got[i] > 0.0
            assert got[i] == pytest.approx(want[i], rel=1e-12), (i, got[i], want[i])
            assert got[i] > without_growth[i]
        else:
            # A loss-maker neither retires nor issues.
            assert got[i] == 0.0


def test_relabelling_the_calendar_origin_leaves_prices_unchanged():
    # Both forms read the elapsed days the engine has run, not the day
    # label: `first_day` relabels the days and moves no price, for the term
    # that stood (since 0.8.5 split the label from the valuation's clock)
    # and for the accrued share count, which does not read a calendar.
    def run(m, first):
        e = engine(m)
        for d in range(15):
            if first is None:
                e.run_days(1, record=False)
            else:
                e.run_days(1, record=False, first_day=first + d)
        return e.prices()

    stood = model()
    assert run(stood, None) == run(stood, 10000)
    on = model(buyback_accrual=1.0)
    assert run(on, None) == run(on, 10000)


def _first_tick_fundamentals(e, day, ticks=2):
    import pyarrow as pa
    e.open_market()
    e.run_session(9, 30, 3, ticks)
    e.record(day)
    truth = pa.table(e.truth(day=day)).to_pydict()
    return {slot: fv for slot, tick, fv in zip(
        truth["instrument_id"], truth["tick"], truth["fundamental_value"])
        if tick == 0}


def _moved_price_twin(e, m, slot, factor):
    snap = e.state_snapshot()
    snap["columns"] = dict(snap["columns"])
    prices = list(floats(snap["columns"]["price"]))
    prices[slot] *= factor
    snap["columns"]["price"] = struct.pack("<%dd" % len(prices), *prices)
    twin = engine(m)
    twin.restore_state(snap)
    return twin


def test_fair_value_does_not_read_the_price_within_a_session():
    # The same state with one name's price 20 per cent lower: under the term
    # that stood the first tick's fair value rises by about b * t times the
    # fall; under the accrual it does not move at all.
    pa = pytest.importorskip("pyarrow")  # noqa: F841
    days = 30
    for accrual, moves in ((0.0, True), (1.0, False)):
        m = model(buyback_accrual=accrual, macro_publication_repricing=0.0)
        e = engine(m)
        e.run_days(days, record=False)
        slot = max(range(N), key=lambda i: UNIVERSE[i].eps / UNIVERSE[i].initial_price)
        twin = _moved_price_twin(e, m, slot, 0.8)
        a = _first_tick_fundamentals(e, days)
        b = _first_tick_fundamentals(twin, days)
        if moves:
            assert b[slot] > a[slot]
        else:
            assert b[slot] == a[slot]


def test_a_restore_mid_run_continues_bit_for_bit_and_the_hash_covers_the_state():
    m = model(buyback_accrual=1.0)
    e = engine(m)
    e.run_days(10, record=False)
    snap = e.state_snapshot()
    assert any(v != 0.0 for v in floats(snap["buyback_log_shares"]))
    # The Python leaf agrees with the engine's own hash, and moving the
    # accrued state moves both.
    assert state_hash(snap) == e.state_hash()
    moved = dict(snap)
    values = list(floats(snap["buyback_log_shares"]))
    values[0] += 1e-9
    moved["buyback_log_shares"] = struct.pack("<%dd" % len(values), *values)
    assert state_hash(moved) != state_hash(snap)
    other = engine(m)
    other.restore_state(moved)
    assert other.state_hash() != e.state_hash()

    twin = engine(m)
    twin.restore_state(snap)
    assert twin.state_hash() == e.state_hash()
    for x in (e, twin):
        x.run_days(10, record=False, first_day=10)
    assert e.prices() == twin.prices()
    assert log_shares(e) == log_shares(twin)
    assert e.state_hash() == twin.state_hash()


def test_a_restore_refuses_a_roster_mismatch():
    m = model(buyback_accrual=1.0)
    e = engine(m)
    e.run_days(2, record=False)
    snap = dict(e.state_snapshot())
    snap["buyback_log_shares"] = snap["buyback_log_shares"][:-8]
    with pytest.raises(Exception):
        engine(m).restore_state(snap)
