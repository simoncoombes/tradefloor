"""The forecast's nowcast of the VIX's slow level (`forecast_vix_level_nowcast`).

0.0 on every shipped preset and left out of the digest there; refused without
`forecast_horizon_sessions` and `vix_level_sigma`. Off zero the engine keeps
the published VIX's log average and the forecast iterates the law with the
level at `g (x - c)` decaying at `vix_level_persistence`. It changes only what
the forecast publishes: the stocks, closes, economy and VIX are the run's
without it.
"""
from __future__ import annotations

import math

import pytest

import tradefloor as tf

SMALL = tf.Universe.random(8, seed=5)
LAW = dict(forecast_horizon_sessions=63.0, vix_level_sigma=0.0181, index_level_listed=1.0)
NOW = dict(forecast_vix_level_nowcast=0.4, forecast_vix_level_nowcast_half_life=63.0,
           forecast_vix_level_nowcast_centre=2.85)
NEW = tuple(NOW)


def model(**dials):
    return tf.ModelParams.from_preset("pt-v21", **dials)


def day(engine):
    engine.open_market()
    engine.run_session(9, 30, 3, 390)
    engine.close_market()


def test_off_on_every_preset_silent_and_refused_without_the_level_or_horizon():
    silent = set(tf.ModelParams.digest_silent_at_zero())
    for name in tf.preset_names():
        p = tf.ModelParams.from_preset(name)
        for dial in NEW:
            assert getattr(p, dial) == 0.0, (name, dial)
    for dial in NEW:
        assert dial in silent
    assert model(**{d: 0.0 for d in NEW}).fingerprint == "pt-v21"
    with pytest.raises(ValueError, match="vix_level_sigma"):
        model(forecast_horizon_sessions=63.0, vix_level_sigma=0.0, **NOW)
    with pytest.raises(ValueError, match="forecast_horizon_sessions"):
        model(vix_level_sigma=0.0181, **NOW)


def test_the_average_is_kept_from_the_centre_and_the_forecast_alone_moves():
    a = tf.Engine(seed=11, universe=SMALL, model=model(**LAW))
    b = tf.Engine(seed=11, universe=SMALL, model=model(**LAW, **NOW))
    assert "vix_log_average" not in a.state_snapshot()
    alpha = 1 - 0.5 ** (1 / 63.0)
    x = 2.85
    moved = 0
    for _ in range(40):
        day(a); day(b)
        x = x + alpha * (math.log(b.macro_fields["vix"]) - x)
        assert b.state_snapshot()["vix_log_average"] == pytest.approx(x, abs=1e-12)
        assert a.prices() == b.prices()
        assert a.macro_fields["vix"] == b.macro_fields["vix"]
        fa, fb = a.forecast()["vix"], b.forecast()["vix"]
        moved += fa != fb
        # The level's sign: above the centre the forecast reads higher.
        q = 0.4 * (x - 2.85)
        if abs(q) > 1e-3:
            assert (fb[-1] > fa[-1]) == (q > 0)
    assert moved == 40
