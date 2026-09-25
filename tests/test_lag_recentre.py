"""The lagged tilt's mean, given back (`market_beta_down_asym_lag_recentre`).

`market_beta_down_asym_recentre` gives back the mean the down-tick tilt puts
into the market input at a lag multiplier of one. On a session after a down
day the lagged wire multiplies the whole transmission, the tilt included, by
`1 + lag`, so `lag` times that mean is left. These tests hold what the dial
is for: it is off on every shipped preset, it does nothing on a session the
wire does not touch, and on a lagged session it adds to every name's market
input exactly `beta_i` times one common amount, upward.
"""

import struct

import pytest

import tradefloor as tf

UNIVERSE = list(tf.Universe.random(12, seed=3))


def floats(raw):
    return struct.unpack("<%dd" % (len(raw) // 8), raw)


def market_input(engine):
    return floats(engine.noise_split("market"))


@pytest.mark.parametrize("preset", tf.preset_names())
def test_off_on_every_shipped_preset(preset):
    assert tf.ModelParams.from_preset(preset).to_dict()[
        "market_beta_down_asym_lag_recentre"] == 0.0


def test_only_a_lagged_session_moves_and_by_beta_times_one_amount():
    off = tf.Engine(seed=7, universe=UNIVERSE, model=tf.ModelParams.from_preset("pt-v20"))
    on = tf.Engine(seed=7, universe=UNIVERSE, model=tf.ModelParams.from_preset(
        "pt-v20", market_beta_down_asym_lag_recentre=1.0))
    betas = [ins.beta for ins in UNIVERSE]
    for day in range(40):
        off.run_days(1, record=False, first_day=day)
        on.run_days(1, record=False, first_day=day)
        a, b = market_input(off), market_input(on)
        if a == b:
            # Every session so far was unlagged: the dial has done nothing.
            assert floats(off.prices()) == floats(on.prices())
            continue
        # The first session it touched: every name's market input is higher
        # by beta_i times one common amount.
        diffs = [(y - x) / beta for x, y, beta in zip(a, b, betas)]
        assert all(d > 0.0 for d in diffs)
        assert max(diffs) - min(diffs) < 1e-9 * max(diffs)
        return
    pytest.fail("no lagged session in 40 days")


def test_it_reads_nothing_without_the_wire():
    base = tf.ModelParams.from_preset("pt-v20", market_beta_down_asym_lag=0.0)
    with_dial = tf.ModelParams.from_preset(
        "pt-v20", market_beta_down_asym_lag=0.0, market_beta_down_asym_lag_recentre=1.0)
    runs = []
    for model in (base, with_dial):
        e = tf.Engine(seed=7, universe=UNIVERSE, model=model)
        e.run_days(20, record=False)
        runs.append(floats(e.prices()))
    assert runs[0] == runs[1]


@pytest.mark.parametrize("value", [-0.1, 1.1])
def test_it_is_a_share(value):
    with pytest.raises(Exception):
        tf.ModelParams.from_preset("pt-v20", market_beta_down_asym_lag_recentre=value)
