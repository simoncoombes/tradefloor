"""The index surface's own curvature (`surface_index_curvature`).

One dial, 0.0 on every shipped preset, where the index and the names share
`surface_curvature`. Off zero the index surface is fitted at its own
curvature and the names keep theirs: a name's correlation is its share of the
one the index's VIX and skewness give at the shared curvature, so the names'
smiles are the ones they have without the dial. These tests hold that, the
index's at-the-money volatility against the VIX, rows SV1 and SV2, and the
stock prices, which no surface moves.
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


def engines(own, seed=3, days=3):
    shared = tf.Engine(seed=seed, universe=SMALL, model=model(**ON))
    split = tf.Engine(seed=seed, universe=SMALL, model=model(**dict(ON, surface_index_curvature=own)))
    for _ in range(days):
        day(shared)
        day(split)
    return shared, split


def test_off_on_every_shipped_preset_and_silent_in_the_digest():
    for name in tf.preset_names():
        assert tf.ModelParams.from_preset(name).surface_index_curvature == 0.0, name
    assert "surface_index_curvature" in set(tf.ModelParams.digest_silent_at_zero())
    assert model(**ON).fingerprint == model(**dict(ON, surface_index_curvature=0.0)).fingerprint


@pytest.mark.parametrize("dials, words", [
    (dict(BASE, surface_index_curvature=0.8), "surface_ssvi"),
    (dict(ON, surface_index_curvature=2.0), "surface_index_curvature"),
    (dict(ON, surface_index_curvature=-0.1), "surface_index_curvature"),
])
def test_a_value_outside_its_range_or_without_the_surface_is_refused(dials, words):
    with pytest.raises(tf.ValidationError, match=words):
        model(**dials)


def test_the_shared_value_set_as_the_index_own_changes_nothing():
    shared, split = engines(1.0)
    a, b = shared.surface("IDX"), split.surface("IDX")
    assert (a.rho, a.eta, a.theta(21.0)) == (b.rho, b.eta, b.theta(21.0))
    for c in SMALL:
        x, y = shared.surface(c.ticker), split.surface(c.ticker)
        assert (x.rho, x.eta, x.iv(-0.1, 63.0)) == (y.rho, y.eta, y.iv(-0.1, 63.0))


def test_the_index_takes_its_own_curvature_and_the_names_keep_theirs():
    shared, split = engines(0.7)
    a, b = shared.surface("IDX"), split.surface("IDX")
    assert (a.eta, b.eta) == (1.0, 0.7)
    # The same VIX and the same skewness, read off a flatter smile.
    assert b.strip_vix(21.0) == pytest.approx(split.macro_state.vix, abs=1e-6)
    assert b.skewness_target == a.skewness_target
    assert b.strip_skew(21.0) == pytest.approx(a.strip_skew(21.0), abs=1e-4)
    # Less of the strip's variance in the wings: at the money nearer the VIX.
    assert b.iv(0.0, 21.0) > a.iv(0.0, 21.0)
    for c in SMALL:
        x, y = shared.surface(c.ticker), split.surface(c.ticker)
        assert (x.rho, x.eta) == (y.rho, y.eta)
        assert x.iv(0.05, 21.0) == y.iv(0.05, 21.0)
    assert split.option_arbitrage() == []


def test_a_skewness_past_the_lower_curvature_reach_is_met_at_the_bound():
    deep = dict(ON, surface_skew_premium=-3.0, surface_index_curvature=0.65)
    engine = tf.Engine(seed=3, universe=SMALL, model=model(**deep))
    day(engine)
    s = engine.surface("IDX")
    assert not s.reached
    assert s.strip_skew(21.0) < 100.0 - 10.0 * s.skewness_target
    assert s.strip_vix(21.0) == pytest.approx(engine.macro_state.vix, abs=1e-6)


def test_the_prices_are_the_same_with_the_index_curvature_set():
    def run(params):
        engine = tf.Engine(seed=7, universe=SMALL, model=params)
        out = []
        for _ in range(6):
            day(engine)
            engine.surface("IDX").strip_skew(21.0)
            out.append((engine.prices(), repr(sorted(engine.economy().items()))))
        return out
    assert run(model(**ON)) == run(model(**dict(ON, surface_index_curvature=0.7)))
