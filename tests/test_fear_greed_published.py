"""The fear/greed index on published inputs (`fear_greed_published_inputs`).

The index's target reads the business-cycle phase (a bonus of +15 in an
expansion to -25 in a contraction) and GDP growth. Read true, it falls about
35 points in the five sessions after a contraction begins, and announces a
turn the engine publishes a year later. The switch makes it read the phase
and growth as published (`cycle_publication_lag`, `gdp_publication_lag`),
so it steps when the turn is published, which is public already.

These tests hold: the switch is off on every preset and inert with both
lags at 0; with them set, a pinned contraction moves the index only once it
is published; it moves no price and no draw (nothing a price reads is
downstream of the index); and it adds no state to the snapshot or the hash.
"""

import struct

import pytest

import tradefloor as tf
from tradefloor.manifest import state_hash

UNIVERSE = list(tf.Universe.random(4, seed=1))
LAG = 25


def model(switch=1.0, cycle_lag=LAG, gdp_lag=21):
    return tf.ModelParams.from_preset(
        "pt-v20", fear_greed_published_inputs=switch,
        cycle_publication_lag=float(cycle_lag), gdp_publication_lag=float(gdp_lag))


def prices(engine):
    raw = engine.prices()
    return struct.unpack("<%dd" % (len(raw) // 8), raw)


def walk(m, days, seed=2, pin_on=5):
    """The index after every close, with the economy pinned into a
    contraction before session `pin_on`; and the engine."""
    e = tf.Engine(seed=seed, universe=UNIVERSE, model=m)
    out = [e.macro_fields["fear_greed_index"]]
    for d in range(1, days + 1):
        if d == pin_on:
            e.pin_macro(cycle="contraction")
        e.run_days(1)
        out.append(e.macro_fields["fear_greed_index"])
    return out, e


@pytest.mark.parametrize("preset", tf.preset_names())
def test_off_on_every_shipped_preset(preset):
    assert tf.ModelParams.from_preset(preset).to_dict()[
        "fear_greed_published_inputs"] == 0.0


def test_inert_without_a_publication_lag():
    """Published is true with both lags at 0: the same index to the bit."""
    on, _ = walk(model(1.0, 0, 0), 30)
    off, _ = walk(model(0.0, 0, 0), 30)
    assert on == off


def test_a_contraction_moves_the_index_only_once_it_is_published():
    true, _ = walk(model(0.0), LAG + 15)
    published, _ = walk(model(1.0), LAG + 15)
    # Read true, the index falls hard within five sessions of the pin.
    assert true[4] - true[9] > 20
    # Read published, it does not: nothing moves until the phase is out.
    assert published[4] - min(published[5:5 + LAG]) < 15
    # ... and then it falls.
    assert published[4 + LAG] - published[9 + LAG] > 20


def test_it_moves_no_price_and_no_draw():
    runs = {}
    for switch in (0.0, 1.0):
        _, e = walk(model(switch), 80)
        runs[switch] = (prices(e), e.draws_consumed)
    assert runs[0.0] == runs[1.0]


def test_it_adds_no_state():
    _, off = walk(model(0.0), 10)
    _, on = walk(model(1.0), 10)
    assert set(on.state_snapshot()) == set(off.state_snapshot())
    assert set(on.state_snapshot()["economy"]) == set(off.state_snapshot()["economy"])
    assert state_hash(on.state_snapshot()) == on.state_hash()
    fork, = tf.branch(on, 1)
    on.run_days(30)
    fork.run_days(30)
    assert fork.state_hash() == on.state_hash()


@pytest.mark.parametrize("value", [0.5, 2.0, -1.0, float("nan")])
def test_only_a_switch_is_accepted(value):
    with pytest.raises(tf.ValidationError, match="fear_greed_published_inputs"):
        tf.ModelParams.from_preset("pt-v20", fear_greed_published_inputs=value)
