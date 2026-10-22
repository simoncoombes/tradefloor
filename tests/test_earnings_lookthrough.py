"""The earnings look-through (`earnings_lookthrough_half_life`).

A `market.earnings` write passes its common factor to the engine, which holds
its log and an anchor; the valuation reads `a + c (m - a)` of it. A direct
`set_fundamentals` is a name's own news and is valued in full. With the dial
off, on every preset, nothing changes, and a run that never writes is the same
with it on.
"""

import json
import math
import struct

import tradefloor as tf

UNIVERSE = tf.Universe.random(8, seed=3)
DIALS = dict(earnings_lookthrough_half_life=598.0, earnings_lookthrough_gain=0.1605,
             earnings_lookthrough_discount_half_life=5267.0)


def arr(buf):
    return list(struct.unpack("<%dd" % (len(buf) // 8), buf))


def model(on):
    return tf.ModelParams.from_preset("pt-v20", **(DIALS if on else {}))


def run(on, write, days=12):
    e = tf.Engine(seed=5, universe=UNIVERSE, model=model(on))
    sc = tf.Scenario()
    if write:
        sc.shock("market.earnings", operation="multiply", value=0.8, at=2, duration=4, shape="ramp")
        sc.shock("market.earnings", operation="multiply", value=1.0, at=6, duration=20, shape="hold")
    closes = []
    for day in range(days):
        sc.apply(e, day)
        e.run_days(1, record=False, first_day=day)
        closes.append(arr(e.prices()))
    return e, closes


def test_a_run_without_a_write_is_the_same_with_the_dial_on():
    _, off = run(False, False)
    _, on = run(True, False)
    assert off == on


def test_the_dial_off_values_a_write_in_full_and_on_looks_through_part_of_it():
    _, off_none = run(False, False)
    e_off, off = run(False, True)
    e_on, on = run(True, True)
    # Earnings are written alike either way; only the valuation differs.
    assert e_off.fundamentals()[0] == e_on.fundamentals()[0]
    index = lambda rows, d: sum(rows[d])
    fall_off = math.log(index(off, 11) / index(off_none, 11))
    fall_on = math.log(index(on, 11) / index(off_none, 11))
    assert fall_off < fall_on < 0.0, (fall_off, fall_on)
    snap = e_on.state_snapshot()
    m, a = arr(snap["earnings_lookthrough"])
    assert abs(m - math.log(0.8)) < 1e-12
    assert math.log(0.8) < a < 0.0
    assert "earnings_lookthrough" not in e_off.state_snapshot()


def test_a_direct_fundamentals_write_is_not_looked_through():
    e = tf.Engine(seed=5, universe=UNIVERSE, model=model(True))
    e.run_days(1, record=False)
    eps, book, growth = e.fundamentals()
    e.set_fundamentals([v * 0.8 for v in eps], book, growth)
    assert arr(e.state_snapshot()["earnings_lookthrough"]) == [0.0, 0.0]


def test_the_write_is_logged_and_replayed_with_the_dial_on_and_absent_with_it_off():
    for on in (False, True):
        e = tf.Engine(seed=7, universe=UNIVERSE, model=model(on))
        sc = tf.Scenario().shock("market.earnings", operation="multiply", value=0.9, at=1,
                                 duration=2, shape="ramp")
        for day in range(4):
            sc.apply(e, day)
            e.open_market()
            e.run_session(9, 30, 3, 30)
            e.close_market()
        notes = [x for x in e.order_log if x["op"] == "note_market_earnings"]
        # Two ramp writes and the release that puts the earnings back.
        assert len(notes) == (3 if on else 0)
        log = json.loads(json.dumps(e.order_log, allow_nan=False))
        replayed = tf.replay(log, seed=7, universe=UNIVERSE, model=model(on))
        assert arr(replayed.prices()) == arr(e.prices())
