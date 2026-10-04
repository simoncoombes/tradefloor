"""The Fed put's unanswered fall and the drawdown hold (`fed_put_carry`,
`fed_drawdown_hold`).

The meeting's arithmetic of the carry (what a cut answers, what is carried,
the floor, the ceiling and a pinned rate) is held on a hand-built economy in
rust/src/economy/central_bank.rs (`put_carry`). These hold the engine around
it: every preset ships both at 0.0; the ranges are refused; the hold's
window is carried, hashed and restored only while it is set; with the carry
on, a meeting restarts the put's clock at a fall it did not answer, never at
a rise, and between meetings the clock telescopes from there; and a hold whose level the index's
fall from its year's high never reaches runs bit for bit as the hold off,
while at a meeting past it no rise is taken.
"""

import math
import struct

import pytest

import tradefloor as tf

UNIVERSE = list(tf.Universe.random(12, seed=3))
DIALS = ("fed_put_carry", "fed_drawdown_hold")
PUT = {"fed_put_gain": 3.0, "fed_put_half_life": 126.0}


def floats(raw):
    return struct.unpack("<%dd" % (len(raw) // 8), raw)


def engine(seed=7, **dials):
    return tf.Engine(seed=seed, universe=UNIVERSE,
                     model=tf.ModelParams.from_preset("pt-v20", **dials))


@pytest.mark.parametrize("preset", tf.preset_names())
def test_off_on_every_shipped_preset(preset):
    values = tf.ModelParams.from_preset(preset).to_dict()
    assert all(values[name] == 0.0 for name in DIALS)


@pytest.mark.parametrize("dials", [
    {"fed_put_carry": -0.1},
    {"fed_put_carry": 1.5},
    {"fed_drawdown_hold": -0.1},
    {"fed_drawdown_hold": 1.5},
])
def test_out_of_range_is_refused(dials):
    with pytest.raises(Exception):
        tf.ModelParams.from_preset("pt-v20", **dials)


def test_a_meeting_restarts_the_clock_at_an_unanswered_fall():
    e = engine(seed=6, **PUT, fed_put_carry=1.0)
    snap = e.state_snapshot()
    last = snap["central_bank"]["last_meeting_date"]
    base = snap["economy"]["fed_put_mcap_prev"]
    carried_at = 0.0
    meetings = negative = checked = 0
    for day in range(400):
        e.run_days(1, record=False, first_day=day)
        snap = e.state_snapshot()
        cap = snap["economy"]["fed_put_mcap_prev"]
        clock = snap["economy"]["intermeeting_return"]
        if snap["central_bank"]["last_meeting_date"] != last:
            meetings += 1
            assert clock <= 0.0
            negative += clock < 0.0
            last, base, carried_at = snap["central_bank"]["last_meeting_date"], cap, clock
        else:
            assert clock == pytest.approx(carried_at + math.log(cap / base), abs=1e-9)
            checked += 1
    assert meetings > 5 and checked > 100
    assert negative > 0, "no meeting carried a fall in 400 sessions"


def test_the_carry_off_restarts_at_zero():
    e = engine(**PUT)
    last = e.state_snapshot()["central_bank"]["last_meeting_date"]
    for day in range(200):
        e.run_days(1, record=False, first_day=day)
        snap = e.state_snapshot()
        if snap["central_bank"]["last_meeting_date"] != last:
            assert snap["economy"]["intermeeting_return"] == 0.0
            last = snap["central_bank"]["last_meeting_date"]


def run_path(days, **dials):
    e = engine(seed=11, **dials)
    rates, prices = [], []
    for day in range(days):
        e.run_days(1, record=False, first_day=day)
        rates.append(e.state_snapshot()["economy"]["federal_funds_rate"])
        prices.append(floats(e.prices()))
    return rates, prices


def drawdown(snap):
    level = high = 0.0
    for r in floats(snap["fed_drawdown_returns"]):
        level += r
        high = max(high, level)
    return high - level


def test_the_window_is_carried_only_while_the_hold_is_set():
    keys = {"fed_drawdown_returns", "fed_drawdown_mcap_prev"}
    assert not keys & set(engine().state_snapshot())
    e = engine(fed_drawdown_hold=0.1)
    assert keys <= set(e.state_snapshot())
    e.run_days(300, record=False)
    snap = e.state_snapshot()
    assert len(floats(snap["fed_drawdown_returns"])) == 252
    assert snap["fed_drawdown_mcap_prev"] == pytest.approx(sum(floats(e.column("market_cap"))), rel=0.05)


def test_the_hashes_agree_and_a_restore_reproduces_the_run():
    from tradefloor import manifest
    dials = {**PUT, "fed_drawdown_hold": 0.05, "fed_put_carry": 1.0}
    e = engine(**dials)
    e.run_days(60, record=False)
    snap = e.state_snapshot()
    assert manifest.state_hash(snap) == e.state_hash()
    # The window and its base both reach the hash.
    moved = {**snap, "fed_drawdown_mcap_prev": snap["fed_drawdown_mcap_prev"] * 1.01}
    assert manifest.state_hash(moved) != e.state_hash()
    raw = bytearray(snap["fed_drawdown_returns"]); raw[0] ^= 1
    assert manifest.state_hash({**snap, "fed_drawdown_returns": bytes(raw)}) != e.state_hash()
    twin = engine(**dials)
    twin.restore_state(snap)
    assert twin.state_hash() == e.state_hash()
    for x in (e, twin):
        x.run_days(40, record=False, first_day=60)
    assert floats(e.prices()) == floats(twin.prices())
    assert twin.state_hash() == e.state_hash()
    # A model with the hold off refuses the window.
    with pytest.raises(Exception):
        engine(**PUT, fed_put_carry=1.0).restore_state(snap)


def test_a_hold_the_index_never_reaches_is_the_hold_off():
    off = run_path(300, **PUT)
    never = run_path(300, **PUT, fed_drawdown_hold=1.0)
    assert off == never


def test_a_hold_the_index_reaches_holds_every_rise_it_meets():
    # At a level just above zero the hold is on whenever the index sits
    # under its highest close of the year.
    e = engine(seed=11, **PUT, fed_drawdown_hold=1e-6)
    snap = e.state_snapshot()
    rate = snap["economy"]["federal_funds_rate"]
    last = snap["central_bank"]["last_meeting_date"]
    held = 0
    for day in range(600):
        e.run_days(1, record=False, first_day=day)
        snap = e.state_snapshot()
        new = snap["economy"]["federal_funds_rate"]
        if snap["central_bank"]["last_meeting_date"] != last:
            last = snap["central_bank"]["last_meeting_date"]
            if drawdown(snap) >= 1e-6 and snap["economy"]["inflation_rate"] < 3.5:
                assert new <= rate + 1e-12
                held += 1
        rate = new
    assert held > 0
