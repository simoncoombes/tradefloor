"""The VIX's own innovation as variance news (`market_vol_vix_news`).

0.0 on every preset and silent in the digest; off zero, the factor's variance moves with the VIX's own innovation,
and a pinned VIX takes none.
"""
import pytest

import tradefloor as tf

PRESETS = [p for p in ("pt-v19", "pt-v20", "pt-v21")]


@pytest.mark.parametrize("preset", PRESETS)
def test_every_preset_ships_it_off(preset):
    assert tf.ModelParams.from_preset(preset).to_dict()["market_vol_vix_news"] == 0.0


def test_it_is_silent_in_the_digest_at_zero():
    assert "market_vol_vix_news" in set(tf.ModelParams.digest_silent_at_zero())
    explicit = tf.ModelParams.from_preset("pt-v21", market_vol_vix_news=0.0)
    assert explicit.fingerprint == tf.ModelParams.from_preset("pt-v21").fingerprint


def _path(days=40, pin=None, **dials):
    e = tf.Engine(seed=7, universe=tf.Universe.random(12, seed=111),
                  model=tf.ModelParams.from_preset("pt-v21", **dials))
    out = []
    for _ in range(days):
        if pin is not None:
            e.pin_macro(vix=pin)
        e.run_days(1)
        out.append(e.index_variance_terms()["factor"])
    return out


def test_it_moves_the_factor_variance():
    assert _path(market_vol_vix_news=1.0) != _path()


def test_a_pinned_vix_takes_no_news():
    # The opening close is free and takes its news; every pinned close after it takes none, so the ratio of the
    # factor's variance with the dial to without it only decays from there, at the components' persistences.
    on, off = _path(pin=25.0, market_vol_vix_news=1.0), _path(pin=25.0)
    ratio = [a / b for a, b in zip(on, off)]
    assert ratio[0] != 1.0
    assert all(abs(r1 - 1.0) <= abs(r0 - 1.0) + 1e-12 for r0, r1 in zip(ratio, ratio[1:]))


@pytest.mark.parametrize("bad", [-0.1, 2.5])
def test_out_of_range_is_refused(bad):
    with pytest.raises(Exception):
        tf.ModelParams.from_preset("pt-v21", market_vol_vix_news=bad)
