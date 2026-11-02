"""A name's reports priced on the variance the engine draws for them
(`surface_name_earnings_premium`, pt-v22).

0.0 on every shipped preset, where a report adds `surface_earnings_weight`
times `earnings_surprise_sigma` squared times the name's whole one-session
variance. Off zero it adds the premium times the surprise's and the reaction
session's sigmas squared at the reaction session, and the follow-through's at
the session after, in the name's non-market daily variance. It prices options
only: no stock price moves.
"""
from __future__ import annotations

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


def reporting(e):
    """A listed name whose next report is inside 21 sessions."""
    listed = {c["root"] for c in e.contracts() if c.get("kind") == "option" and c.get("root") != "IDX"}
    for row in e.earnings_calendar(21):
        if row["ticker"] in listed and row["sessions_ahead"] >= 1:
            return row["ticker"], row["sessions_ahead"]
    pytest.skip("no listed name reports in the next 21 sessions")


def test_silent_at_zero_and_refused_without_its_switches():
    assert model(**ON).fingerprint == model(**ON, surface_name_earnings_premium=0.0).fingerprint
    assert model(**ON).fingerprint != model(**ON, surface_name_earnings_premium=1.2).fingerprint
    with pytest.raises(Exception, match="options_names_listed"):
        model(surface_name_earnings_premium=1.2)
    with pytest.raises(Exception, match="earnings_surprise_sigma"):
        model(**ON, surface_name_earnings_premium=1.2, earnings_surprise_sigma=0.0)
    with pytest.raises(Exception, match=r"\[0, 5\]"):
        model(**ON, surface_name_earnings_premium=6.0)


def test_no_stock_price_moves():
    a = engine(model(**ON, surface_earnings_weight=1.0), days=5)
    b = engine(model(**ON, surface_earnings_weight=1.0, surface_name_earnings_premium=1.2), days=5)
    assert a.prices() == b.prices()


def test_the_report_is_linear_in_the_premium_and_replaces_the_weight():
    base = engine(model(**ON))
    name, ahead = reporting(base)
    t = float(ahead + 3)
    flat = base.surface(name).theta(t)
    one = engine(model(**ON, surface_name_earnings_premium=1.0)).surface(name).theta(t) - flat
    two = engine(model(**ON, surface_name_earnings_premium=2.0)).surface(name).theta(t) - flat
    assert one > 0.0
    assert two == pytest.approx(2.0 * one, rel=1e-9)
    weighted = engine(model(**ON, surface_name_earnings_premium=1.0, surface_earnings_weight=3.0))
    assert weighted.surface(name).theta(t) - flat == pytest.approx(one, rel=1e-12)
    # Before the report the name's term structure is the one without reports.
    assert engine(model(**ON, surface_name_earnings_premium=1.0)).surface(name).theta(ahead - 0.5) == \
        pytest.approx(base.surface(name).theta(ahead - 0.5), rel=1e-12)


def test_the_session_after_carries_the_follow_through():
    on = dict(ON, surface_name_earnings_premium=1.0)
    with_after = engine(model(**on))
    name, ahead = reporting(with_after)
    without = engine(model(**on, earnings_followthrough_sigma=0.0))
    base = engine(model(**ON))
    at_report = float(ahead) + 0.5
    after = float(ahead) + 1.5
    # The same at the report, more the session after.
    d0 = with_after.surface(name).theta(at_report) - without.surface(name).theta(at_report)
    d1 = with_after.surface(name).theta(after) - without.surface(name).theta(after)
    assert d1 > d0 >= 0.0
    assert base.surface(name).theta(after) < without.surface(name).theta(after)
