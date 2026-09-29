"""The market's prehistory (`market_prehistory_sessions`; 0.8.5, sim/r18-opening).

The dial ships at 0.0 on every preset, and there the market opens at the
constructor's baseline, as it stood. Off zero, a copy of the opening engine
lives the last this many sessions of the macro burn-in on the economy's
recorded phases, drawing from generators of its own, and the run opens with
the copy's volatility state: the factor variance and its return memory, the
VIX and its slow level, the anchor's and stress premium's memories, the
cycle's volatility multiplier, and each sector's and name's variance. Prices,
the economy (its VIX apart) and the run's own draw schedule are the ones the
engine would open with. These tests hold the default, the domain, that the
run's draws and prices are untouched, that the opening lands near the level
the run holds in its opening phase, determinism and the snapshot round trip.
"""

import struct

import numpy as np
import pytest

import tradefloor as tf

UNIVERSE = list(tf.Universe.random(12, seed=3))


def floats(raw):
    return struct.unpack("<%dd" % (len(raw) // 8), raw)


def engine(seed=7, preset="pt-v20", **dials):
    return tf.Engine(seed=seed, universe=UNIVERSE,
                     model=tf.ModelParams.from_preset(preset, **dials))


@pytest.mark.parametrize("preset", tf.preset_names())
def test_off_on_every_shipped_preset(preset):
    assert tf.ModelParams.from_preset(preset).market_prehistory_sessions == 0.0


@pytest.mark.parametrize("value", [-1.0, 2521.0, 10.5, float("nan")])
def test_out_of_domain_is_refused(value):
    with pytest.raises(Exception):
        tf.ModelParams.from_preset("pt-v20", market_prehistory_sessions=value)


def test_zero_is_the_opening_that_stood():
    a, b = engine(), engine(market_prehistory_sessions=0.0)
    assert a.state_hash() == b.state_hash()


def test_the_run_opens_on_its_own_draws_and_prices_with_the_copys_volatility():
    off, on = engine(), engine(market_prehistory_sessions=42.0)
    assert on.draws_consumed == off.draws_consumed
    assert floats(on.prices()) == floats(off.prices())
    so, sn = off.state_snapshot(), on.state_snapshot()
    assert sn["market_variance"] != so["market_variance"]
    assert sn["economy"]["vix"] != so["economy"]["vix"]
    assert sn["columns"]["garch_variance"] != so["columns"]["garch_variance"]
    for key in ("unemployment_rate", "federal_funds_rate", "gdp", "cycle_phase",
                "months_in_current_phase", "inflation_rate"):
        assert sn["economy"][key] == so["economy"][key], key


def test_the_same_seed_opens_the_same_and_a_snapshot_restores_it():
    a, b = engine(11, market_prehistory_sessions=21.0), engine(11, market_prehistory_sessions=21.0)
    assert a.state_hash() == b.state_hash()
    for _ in range(3):
        for e in (a, b):
            e.open_market(); e.run_session(9, 30, 3, 60); e.close_market()
    assert floats(a.prices()) == floats(b.prices())
    snap = a.state_snapshot()
    c = engine(11, market_prehistory_sessions=21.0)
    c.restore_state(snap)
    assert c.state_hash() == a.state_hash()


def test_an_expansion_opens_below_the_phase_free_baseline():
    """With the cycle's volatility multiplier at 0.85 in an expansion (R17T's
    vector), a run that opens in one opens without the prehistory at the
    constructor's baseline factor variance, which is the phase-free level;
    with it the median opening sits below that baseline, where the run's
    expansions live."""
    dials = dict(market_vol_cycle_ratio=2.4705882352941178, market_vol_cycle_expansion=0.85,
                 market_vol_cycle_half_life=10.0, market_vol_cycle_relative=0.75,
                 market_vol_cycle_cap_relative=1.0)
    opened, cold = [], []
    for seed in range(300001, 300031):
        s = engine(seed, **dials, market_prehistory_sessions=63.0).state_snapshot()
        if s["economy"]["cycle_phase"] != "expansion":
            continue
        opened.append(s["market_variance"][0])
        cold.append(engine(seed, **dials).state_snapshot()["market_variance"][0])
        if len(opened) == 10:
            break
    assert len(opened) == 10
    assert len(set(cold)) == 1
    assert np.median(opened) < 0.85 * cold[0]
