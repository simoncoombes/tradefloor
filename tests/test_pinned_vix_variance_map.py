"""A forced close can set the variance from the tape's VIX-to-variance map (`pinned_vix_variance_map`).

Every preset ships the map at 0.0 and its exponent at 2.327. The digest leaves both out at those values, so every
known-answer digest stays as it was (tests/test_known_answer.py). With the map on, a forced close (a pin with
`vix_sets_variance=True`) sets the market factor's variance so that the index identity's total equals
rho * (VIX / 100)^2 / 252, with rho = map * (VIX / 20)^(exponent - 2). This replaces the law's fixed point. The map is
read only at a forced close. These tests check on pt-v20 that a free run is the run that stood, bit for bit. They
check that an unforced pin is the pin that stood. They check that a forced pin under the map is not the forced pin
without it, and that the map takes no draw of its own.
"""

import math

import pytest

import tradefloor as tf

from test_pinned_vix_sets_variance import same

UNIVERSE = list(tf.Universe.random(12, seed=3))
ON = {"pinned_vix_variance_map": 0.263}


def engine(seed=7, **dials):
    return tf.Engine(seed=seed, universe=UNIVERSE, model=tf.ModelParams.from_preset("pt-v20", **dials))


def pinned(e, days, sets):
    for day in range(days):
        e.pin_macro(vix=20.0 + 30.0 * math.exp(-day / 15.0), vix_sets_variance=sets)
        e.run_days(1, record=False, first_day=day)
    return e


@pytest.mark.parametrize("preset", tf.preset_names())
def test_off_on_every_shipped_preset(preset):
    d = tf.ModelParams.from_preset(preset).to_dict()
    assert d["pinned_vix_variance_map"] == 0.0
    assert d["pinned_vix_variance_map_exponent"] == 2.327


@pytest.mark.parametrize("preset", tf.preset_names())
def test_left_out_of_the_digest(preset):
    shipped = tf.Engine(seed=1, universe=UNIVERSE, model=tf.ModelParams.from_preset(preset))
    named = tf.Engine(seed=1, universe=UNIVERSE, model=tf.ModelParams.from_preset(
        preset, pinned_vix_variance_map=0.0, pinned_vix_variance_map_exponent=2.327))
    assert named.model_fingerprint == shipped.model_fingerprint


def test_on_names_the_model():
    a, b = engine(), engine(**ON)
    assert a.model_fingerprint != b.model_fingerprint


@pytest.mark.parametrize("dial,value", [
    ("pinned_vix_variance_map", -0.1),
    ("pinned_vix_variance_map", 4.5),
    ("pinned_vix_variance_map_exponent", 0.5),
    ("pinned_vix_variance_map_exponent", 6.5),
])
def test_out_of_range_is_refused(dial, value):
    with pytest.raises(Exception):
        tf.ModelParams.from_preset("pt-v20", **{dial: value})


def test_a_free_run_is_the_run_that_stood():
    a, b = engine(), engine(**ON)
    for day in range(300):
        a.run_days(1, record=False, first_day=day)
        b.run_days(1, record=False, first_day=day)
    assert a.prices() == b.prices()
    assert same(a, b)


def test_an_unforced_pin_is_the_pin_that_stood():
    a = pinned(engine(), 60, sets=False)
    b = pinned(engine(**ON), 60, sets=False)
    assert a.prices() == b.prices()
    assert same(a, b)


def test_a_forced_pin_under_the_map_sets_another_variance():
    a = pinned(engine(), 60, sets=True)
    b = pinned(engine(**ON), 60, sets=True)
    assert a.state_snapshot()["market_variance"] != b.state_snapshot()["market_variance"]


def test_it_takes_no_draw():
    a = pinned(engine(), 40, sets=True)
    b = pinned(engine(**ON), 40, sets=True)
    assert a.state_snapshot()["draw_counts"] == b.state_snapshot()["draw_counts"]
