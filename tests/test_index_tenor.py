"""The index smile's term scaling (`surface_index_tenor_exponent`).

One dial, 0.0 on every shipped preset, where the index's power law is SSVI's
own, `eta theta^-gamma (1 + theta)^(gamma - 1)`. Off zero the power law is read
at the 21-session fit's total variance and falls from there as `theta^-x`: the
21-session smile, so its VIX and SKEW, is unchanged, and the at-the-money skew
scales with the tenor as `T^-x`. These tests hold that, the names' smiles,
rows SV1 and SV2, and the stock prices, which no surface moves.
"""
from __future__ import annotations

import pytest

import tradefloor as tf

SMALL = tf.Universe.random(10, seed=5)
BASE = dict(index_level_listed=1.0, vix_intraday_live=1.0, forecast_horizon_sessions=252.0)
SURFACE = dict(surface_ssvi=1.0, surface_skew_physical=-0.8, surface_skew_physical_slope=0.3,
               surface_skew_premium=-1.0, surface_skew_premium_slope=0.5, surface_curvature=1.0,
               surface_curvature_exponent=0.5, surface_term_premium_short=-0.03,
               surface_term_premium_long=0.1)
ON = dict(BASE, **SURFACE, options_index_listed=1.0, options_names_listed=1.0, surface_name_premium=0.1,
          surface_name_skew=0.5)


def model(**dials):
    return tf.ModelParams.from_preset("pt-v21", **dials)


def day(engine):
    engine.open_market()
    engine.run_session(9, 30, 3, 390)
    engine.close_market()


def engines(x, seed=3, days=3):
    shared = tf.Engine(seed=seed, universe=SMALL, model=model(**ON))
    own = tf.Engine(seed=seed, universe=SMALL, model=model(**dict(ON, surface_index_tenor_exponent=x)))
    for _ in range(days):
        day(shared)
        day(own)
    return shared, own


def skew(s, t, h=0.005):
    return (s.iv(h, t) - s.iv(-h, t)) / (2 * h)


def test_off_on_every_shipped_preset_and_silent_in_the_digest():
    for name in tf.preset_names():
        assert tf.ModelParams.from_preset(name).surface_index_tenor_exponent == 0.0, name
    assert "surface_index_tenor_exponent" in set(tf.ModelParams.digest_silent_at_zero())
    assert model(**ON).fingerprint == model(**dict(ON, surface_index_tenor_exponent=0.0)).fingerprint


@pytest.mark.parametrize("dials, words", [
    (dict(BASE, surface_index_tenor_exponent=0.4), "surface_ssvi"),
    (dict(ON, surface_index_tenor_exponent=1.0), "surface_index_tenor_exponent"),
    (dict(ON, surface_index_tenor_exponent=-0.1), "surface_index_tenor_exponent"),
])
def test_a_value_outside_its_range_or_without_the_surface_is_refused(dials, words):
    with pytest.raises(tf.ValidationError, match=words):
        model(**dials)


def test_the_one_month_smile_is_unchanged_and_the_others_scale_from_it():
    shared, own = engines(0.4)
    a, b = shared.surface("IDX"), own.surface("IDX")
    assert (a.rho, a.eta, a.theta(21.0)) == (b.rho, b.eta, b.theta(21.0))
    for k in (-0.1, -0.02, 0.0, 0.03):
        assert b.iv(k, 21.0) == pytest.approx(a.iv(k, 21.0), rel=1e-12)
    assert b.strip_skew(21.0) == pytest.approx(a.strip_skew(21.0), abs=1e-9)
    # A flatter fall of the skew with the tenor: less skew short, more long.
    assert abs(skew(b, 8.0)) < abs(skew(a, 8.0))
    assert abs(skew(b, 126.0)) > abs(skew(a, 126.0))
    # At the measured exponent the skew scales as T^-0.4 from the month.
    ratio = skew(b, 10.5) / skew(b, 21.0)
    assert ratio == pytest.approx(2 ** 0.4, rel=0.03)
    for c in SMALL:
        x, y = shared.surface(c.ticker), own.surface(c.ticker)
        assert x.iv(0.05, 63.0) == y.iv(0.05, 63.0)
    assert own.option_arbitrage() == []


def test_the_prices_are_the_same_with_the_tenor_exponent_set():
    def run(params):
        engine = tf.Engine(seed=7, universe=SMALL, model=params)
        out = []
        for _ in range(6):
            day(engine)
            engine.surface("IDX").strip_skew(21.0)
            out.append((engine.prices(), repr(sorted(engine.economy().items()))))
        return out
    assert run(model(**ON)) == run(model(**dict(ON, surface_index_tenor_exponent=0.4)))
