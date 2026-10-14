"""Every pinned VIX sets the variance (`pinned_vix_sets_variance`).

Every preset ships the switch at 0.0, which is left out of the digest, and every known-answer digest is the one it
was (tests/test_known_answer.py). Under the switch `pin_macro(vix=...)` marks tonight's close forced whatever its
`vix_sets_variance` argument says, so the close sets the market factor's variance to the law's fixed point at the
pinned VIX (`MarketVarianceState::close_day_forced`). These hold it on pt-v20: a free run is the run that stood; a pin
under the switch is the pin with `vix_sets_variance=True`, bit for bit in prices and draws; a pin without the switch
is not; and it takes no draw of its own.
"""

import math

import pytest

import tradefloor as tf

UNIVERSE = list(tf.Universe.random(12, seed=3))


def engine(seed=7, **dials):
    return tf.Engine(seed=seed, universe=UNIVERSE, model=tf.ModelParams.from_preset("pt-v20", **dials))


def pinned(e, days, sets=False):
    for day in range(days):
        e.pin_macro(vix=20.0 + 30.0 * math.exp(-day / 15.0), vix_sets_variance=sets)
        e.run_days(1, record=False, first_day=day)
    return e


def same(a, b):
    """The two engines' states agree, the model's fingerprint apart (a dial set off zero names the model)."""
    sa, sb = a.state_snapshot(), b.state_snapshot()
    for k in set(sa) | set(sb):
        if k == "model_fingerprint":
            continue
        x, y = sa.get(k), sb.get(k)
        if isinstance(x, list) and isinstance(y, list):
            if len(x) != len(y) or any(not (u == v or (u != u and v != v)) for u, v in zip(x, y)):
                return False
        elif x != y:
            return False
    return True


@pytest.mark.parametrize("preset", tf.preset_names())
def test_off_on_every_shipped_preset(preset):
    assert tf.ModelParams.from_preset(preset).to_dict()["pinned_vix_sets_variance"] == 0.0


@pytest.mark.parametrize("value", [0.5, 2.0, -1.0])
def test_only_a_switch(value):
    with pytest.raises(Exception):
        tf.ModelParams.from_preset("pt-v20", pinned_vix_sets_variance=value)


def test_a_free_run_is_the_run_that_stood():
    a, b = engine(), engine(pinned_vix_sets_variance=1.0)
    for day in range(300):
        a.run_days(1, record=False, first_day=day)
        b.run_days(1, record=False, first_day=day)
    assert a.prices() == b.prices()
    assert same(a, b)


def test_a_pin_under_the_switch_is_a_forced_pin():
    a = pinned(engine(), 60, sets=True)
    b = pinned(engine(pinned_vix_sets_variance=1.0), 60)
    assert a.prices() == b.prices()
    assert same(a, b)


def test_without_the_switch_a_pin_steps():
    a = pinned(engine(), 60)
    b = pinned(engine(pinned_vix_sets_variance=1.0), 60)
    assert a.state_snapshot()["market_variance"] != b.state_snapshot()["market_variance"]


def test_it_takes_no_draw():
    a = pinned(engine(), 40, sets=True)
    b = pinned(engine(pinned_vix_sets_variance=1.0), 40)
    assert a.state_snapshot()["draw_counts"] == b.state_snapshot()["draw_counts"]
