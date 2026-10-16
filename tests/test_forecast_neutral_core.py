"""The forecast's neutral moving with inflation (`forecast_policy_neutral_core`, pt-v22).

0.0 on every shipped preset, where the policy path reverts toward the fixed
`forecast_policy_neutral`. Off zero the neutral moves with the inflation the
bank's meeting reads above its target. These tests hold it off on every
preset, inert on prices (the forecast writes nothing back), and the path's
far end moved by the dial's share of the gap.
"""
from __future__ import annotations

import pytest

import tradefloor as tf

UNIVERSE = tf.Universe.random(8, seed=5)
ON = dict(index_level_listed=1.0, forecast_horizon_sessions=252.0, forecast_policy_shadow_discount=0.24,
          forecast_policy_persistence=0.7, forecast_policy_reversion=0.05, forecast_policy_neutral=2.86,
          fed_core_inflation=1.0)


def model(**dials):
    return tf.ModelParams.from_preset("pt-v21", **dials)


def test_off_on_every_shipped_preset_silent_in_the_digest_and_bounded():
    for name in tf.preset_names():
        assert tf.ModelParams.from_preset(name).forecast_policy_neutral_core == 0.0, name
    assert "forecast_policy_neutral_core" in set(tf.ModelParams.digest_silent_at_zero())
    assert model(forecast_policy_neutral_core=0.0).fingerprint == "pt-v21"
    for bad in (-0.1, 3.1):
        with pytest.raises(tf.ValidationError):
            model(**ON, forecast_policy_neutral_core=bad)


def test_prices_untouched_and_the_far_path_moves_with_the_gap():
    out = []
    for k in (0.0, 0.75):
        e = tf.Engine(seed=7, universe=UNIVERSE, model=model(**ON, forecast_policy_neutral_core=k))
        e.run_days(30)
        f = e.forecast()
        gap = e.economy()["core_inflation"] - e.state_snapshot()["central_bank"]["target_inflation"]
        out.append((e.prices(), f["policy_rate"], gap))
    assert out[0][0] == out[1][0], "a price moved"
    gap = out[0][2]
    far = 100 * (out[1][1][-1] - out[0][1][-1])
    near = 100 * (out[1][1][0] - out[0][1][0])
    assert abs(near) < 1e-12
    if abs(gap) > 0.05:
        # the far end moves toward the moved neutral, in the gap's direction,
        # by no more than the dial's share of it
        assert far * gap > 0 and abs(far) <= 0.75 * abs(gap) + 1e-9, (far, gap)
