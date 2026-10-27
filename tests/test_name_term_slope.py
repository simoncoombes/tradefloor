"""A single name's premium shaped in tenor (`surface_name_term_slope`, pt-v22).

0.0 on every shipped preset. Off zero a name's expected variance to `t`
sessions takes a further log premium of the slope times ln(t / 63): the
63-session point is unchanged, and a positive slope lowers the shorter tenors
and raises the longer. The index surface is untouched, and it prices options
only: no stock price moves.
"""
from __future__ import annotations

import math

import pytest

import tradefloor as tf

UNIVERSE = tf.Universe.random(30, seed=111)
ON = dict(index_level_listed=1.0, vix_intraday_live=1.0, forecast_horizon_sessions=252.0,
          forecast_vix_dispersion=0.3, forecast_vix_dispersion_half_life=31.5, forecast_vix_dispersion_skew=0.81,
          surface_ssvi=1.0, surface_skew_physical=-0.8, surface_skew_physical_slope=0.3, surface_skew_premium=-1.0,
          surface_skew_premium_slope=0.5, surface_curvature=1.0, surface_curvature_exponent=0.5,
          surface_term_premium_short=-0.03, surface_term_premium_long=0.1, options_names_listed=1.0,
          surface_name_premium=0.1, surface_name_skew=0.5)


def model(**dials):
    return tf.ModelParams.from_preset("pt-v21", **dials)


def engine(params, days=3):
    e = tf.Engine(seed=7, universe=UNIVERSE, model=params)
    for _ in range(days):
        e.open_market(); e.run_session(9, 30, 3, 390); e.close_market()
    return e


def names(e):
    return sorted({c["root"] for c in e.contracts() if c.get("kind") == "option" and c.get("root") != "IDX"})


def test_silent_at_zero_and_refused_without_its_switch():
    assert model(**ON).fingerprint == model(**ON, surface_name_term_slope=0.0).fingerprint
    assert model(**ON).fingerprint != model(**ON, surface_name_term_slope=0.2).fingerprint
    with pytest.raises(Exception, match="options_names_listed"):
        model(surface_name_term_slope=0.2)
    with pytest.raises(Exception, match=r"\[-1, 1\]"):
        model(**ON, surface_name_term_slope=1.5)


def test_no_stock_price_moves_and_the_index_is_untouched():
    a = engine(model(**ON), days=5)
    b = engine(model(**ON, surface_name_term_slope=0.2), days=5)
    assert a.prices() == b.prices()
    for t in (5.0, 21.0, 63.0, 200.0):
        assert a.surface("IDX").theta(t) == b.surface("IDX").theta(t)


def test_the_slope_is_a_log_premium_in_ln_tenor_anchored_at_63_sessions():
    # without reports (no surprise, so no report's variance in the curve) the ratio is exp(s ln(t / 63)) at every knot
    quiet = dict(ON, earnings_surprise_sigma=0.0)
    base = engine(model(**quiet))
    s = 0.2
    shaped = engine(model(**quiet, surface_name_term_slope=s))
    listed = names(base)
    assert listed
    for name in listed:
        a, b = base.surface(name), shaped.surface(name)
        for t in (21.0, 42.0, 63.0, 126.0):
            assert b.theta(t) / a.theta(t) == pytest.approx(math.exp(s * math.log(t / 63.0)), rel=1e-9), (name, t)
