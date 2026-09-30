"""The prehistory's valuation carry (`market_prehistory_valuation`; 0.8.5, sim/r18-valopen).

The switch ships at 0.0 on every preset, and there the market's prehistory
(`market_prehistory_sessions`) hands back its volatility state alone. On, the
run also opens with the copy's valuation state: each name's mispricing, which
the opening's split takes in place of its draw, the VIX feedback's exposure,
the anticipation's drift, the earnings cycle, credit's leverage gap and the
Fed put's owed cut, with the corporate yield and the curve moved by what the
gap and the owed cut change. The split books all of it into the names'
fair-value levels, so no opening price moves. These tests hold the default,
the domain, that the run's draws and opening prices are untouched, that the
first print is where it stood, determinism and the snapshot round trip before
the opening.
"""

import struct

import pytest

import tradefloor as tf

UNIVERSE = list(tf.Universe.random(12, seed=3))
ARM = dict(market_prehistory_sessions=42.0, fed_put_gain=3.0, fed_put_half_life=126.0,
           corporate_spread_equity_gain=2.0, corporate_spread_equity_half_life=126.0,
           earnings_anticipation_drift_share=0.9, earnings_anticipation_drift_half_life=252.0,
           fair_value_vix_release_half_life=504.0)


def floats(raw):
    return struct.unpack("<%dd" % (len(raw) // 8), raw)


def engine(seed=7, on=1.0, **dials):
    d = dict(ARM, **dials)
    return tf.Engine(seed=seed, universe=UNIVERSE,
                     model=tf.ModelParams.from_preset("pt-v20", market_prehistory_valuation=on, **d))


@pytest.mark.parametrize("preset", tf.preset_names())
def test_off_on_every_shipped_preset(preset):
    assert tf.ModelParams.from_preset(preset).market_prehistory_valuation == 0.0


@pytest.mark.parametrize("value", [-1.0, 0.5, 2.0, float("nan")])
def test_out_of_domain_is_refused(value):
    with pytest.raises(Exception):
        tf.ModelParams.from_preset("pt-v20", market_prehistory_sessions=21.0,
                                   market_prehistory_valuation=value)


def test_it_is_refused_without_a_prehistory():
    with pytest.raises(Exception):
        tf.ModelParams.from_preset("pt-v20", market_prehistory_valuation=1.0)


def test_zero_is_the_opening_that_stood():
    a = tf.Engine(seed=7, universe=UNIVERSE, model=tf.ModelParams.from_preset("pt-v20", **ARM))
    assert a.state_hash() == engine(on=0.0).state_hash()


def test_the_run_opens_on_its_own_draws_and_prices_and_its_first_print_is_where_it_stood():
    off, on = engine(on=0.0), engine()
    assert on.draws_consumed == off.draws_consumed
    assert floats(on.prices()) == floats(off.prices())
    so, sn = off.state_snapshot(), on.state_snapshot()
    assert sn["market_variance"] == so["market_variance"]
    assert "opening_carry" in sn and len(floats(sn["opening_carry"])) == len(UNIVERSE)
    for key in ("unemployment_rate", "gdp", "cycle_phase", "months_in_current_phase",
                "inflation_rate", "vix"):
        assert sn["economy"][key] == so["economy"][key], key
    for e in (off, on):
        e.open_market(); e.run_session(9, 30, 3, 1)
    for a, b in zip(floats(off.prices()), floats(on.prices())):
        assert abs(a / b - 1) < 1e-3
    carried = floats(sn["opening_carry"])
    s_on = floats(on.state_snapshot()["columns"]["mispricing_s"])
    assert max(abs(x - y) for x, y in zip(s_on, carried)) < 0.01
    assert len(on.state_snapshot()["opening_carry"]) == 0


def test_the_same_seed_opens_the_same_and_a_snapshot_before_the_open_restores_it():
    a, b = engine(11), engine(11)
    assert a.state_hash() == b.state_hash()
    snap = a.state_snapshot()
    c = engine(11)
    c.restore_state(snap)
    assert c.state_hash() == a.state_hash()
    for _ in range(3):
        for e in (a, c):
            e.open_market(); e.run_session(9, 30, 3, 60); e.close_market()
    assert floats(a.prices()) == floats(c.prices())
    assert a.state_hash() == c.state_hash()
