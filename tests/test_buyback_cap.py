"""The buyback term's yield ceiling (`buyback_yield_cap`).

The buyback term compounds `buyback_payout_share * eps / price` over the
elapsed years at TODAY's price, so a collapsed price reads an enormous yield.
The cap bounds it. These tests hold that it is off on every shipped preset,
that a ceiling no name reaches changes nothing, that one every name reaches
changes the market, and that it reads nothing without buybacks.
"""

import struct

import pytest

import tradefloor as tf

UNIVERSE = list(tf.Universe.random(12, seed=3))


def prices(model, days=20):
    e = tf.Engine(seed=7, universe=UNIVERSE, model=model)
    e.run_days(days, record=False)
    raw = e.prices()
    return struct.unpack("<%dd" % (len(raw) // 8), raw)


@pytest.mark.parametrize("preset", tf.preset_names())
def test_off_on_every_shipped_preset(preset):
    assert tf.ModelParams.from_preset(preset).to_dict()["buyback_yield_cap"] == 0.0


def test_a_ceiling_no_name_reaches_changes_nothing():
    base = tf.ModelParams.from_preset("pt-v20", buyback_payout_share=0.75)
    capped = tf.ModelParams.from_preset("pt-v20", buyback_payout_share=0.75, buyback_yield_cap=1.0)
    assert prices(base) == prices(capped)


def test_a_binding_ceiling_moves_the_market():
    base = tf.ModelParams.from_preset("pt-v20", buyback_payout_share=0.75)
    capped = tf.ModelParams.from_preset("pt-v20", buyback_payout_share=0.75, buyback_yield_cap=0.001)
    assert prices(base) != prices(capped)


def test_it_reads_nothing_without_buybacks():
    base = tf.ModelParams.from_preset("pt-v20", buyback_payout_share=0.0)
    capped = tf.ModelParams.from_preset("pt-v20", buyback_payout_share=0.0, buyback_yield_cap=0.001)
    assert prices(base) == prices(capped)


@pytest.mark.parametrize("value", [-0.1, 1.5])
def test_it_is_a_yield(value):
    with pytest.raises(Exception):
        tf.ModelParams.from_preset("pt-v20", buyback_yield_cap=value)
