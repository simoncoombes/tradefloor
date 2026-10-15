"""The variance risk premium's slope on the variance-implied level (`vix_variance_premium_slope`).

0.0 on every shipped preset, where the VIX the identity derives is today's constant premium over the index's
variance. Off zero the premium is `(1 + vix_variance_premium) (L / 20)^slope - 1`, L the level the variance
implies before any premium: it reads the variance, never the VIX. These tests hold the refusals, the digest, the
map the read-back reports, and that the dial moves the VIX it was built to move.
"""
from __future__ import annotations

import math

import pytest

import tradefloor as tf

SMALL = tf.Universe.random(10, seed=5)


def model(**dials):
    return tf.ModelParams.from_preset("pt-v20", **dials)


def test_off_on_every_shipped_preset_and_silent_in_the_digest():
    for name in tf.preset_names():
        assert tf.ModelParams.from_preset(name).vix_variance_premium_slope == 0.0, name
    assert "vix_variance_premium_slope" in set(tf.ModelParams.digest_silent_at_zero())
    assert model(vix_variance_premium_slope=0.0).fingerprint == model().fingerprint


@pytest.mark.parametrize("dials, words", [
    (dict(vix_variance_premium_slope=0.6), "vix_variance_premium_slope"),
    (dict(vix_variance_premium_slope=-0.6), "vix_variance_premium_slope"),
])
def test_a_slope_outside_its_range_is_refused(dials, words):
    with pytest.raises(tf.ValidationError, match=words):
        model(**dials)


def test_refused_without_the_identity():
    with pytest.raises(tf.ValidationError, match="vix_level_identity"):
        tf.ModelParams.from_preset("pt-v15", vix_variance_premium_slope=-0.07)


def test_the_read_back_is_the_sloped_premium_over_the_variance_implied_level():
    e = tf.Engine(seed=3, universe=SMALL, model=model(vix_variance_premium_slope=-0.07))
    for d in range(5):
        e.run_days(1, record=False, first_day=d)
        t = e.index_variance_terms()
        level = 100.0 * math.sqrt(252.0 * t["total"])
        assert t["implied"] == pytest.approx(1.252 * (level / 20.0) ** -0.07 * level, rel=1e-12)


def test_the_slope_moves_the_vix_and_the_market():
    def run(params):
        e = tf.Engine(seed=11, universe=SMALL, model=params)
        out = []
        for d in range(30):
            e.run_days(1, record=False, first_day=d)
            out.append((e.economy()["vix"], tuple(e.prices())))
        return out
    flat, sloped = run(model()), run(model(vix_variance_premium_slope=-0.07))
    assert [x[0] for x in flat] != [x[0] for x in sloped]
