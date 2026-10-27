"""The growth oil's forecast reads (`oil_forecast_cycle_growth`).

A switch, 0.0 on every shipped preset. At 1.0 the forecast's oil reads
growth's expectation over the cycle's phases instead of the believed phase
held for the whole horizon. It reads the forecast only, so stock prices and
the economy are the same with it on as off, and the oil futures, which mark
at the forecast, are what move.
"""
from __future__ import annotations

import tradefloor as tf

UNIVERSE = tf.Universe.random(8, seed=5)
ON = dict(index_level_listed=1.0, vix_intraday_live=1.0, forecast_horizon_sessions=130.0,
          futures_oil_listed=1.0, cycle_nowcast_accuracy=0.4)


def model(**dials):
    return tf.ModelParams.from_preset("pt-v21", **dials)


def day(engine):
    engine.open_market()
    engine.run_session(9, 30, 3, 390)
    engine.close_market()


def test_the_switch_is_off_on_every_shipped_preset_and_silent_in_the_digest():
    for name in tf.preset_names():
        assert tf.ModelParams.from_preset(name).oil_forecast_cycle_growth == 0.0, name
    assert "oil_forecast_cycle_growth" in set(tf.ModelParams.digest_silent_at_zero())
    assert model(oil_forecast_cycle_growth=0.0).fingerprint == "pt-v21"


def test_it_moves_no_price_and_no_economy_and_only_the_forecasts_oil():
    off = tf.Engine(seed=11, universe=UNIVERSE, model=model(**ON))
    on = tf.Engine(seed=11, universe=UNIVERSE, model=model(**ON, oil_forecast_cycle_growth=1.0))
    moved = 0.0
    for _ in range(60):
        day(off)
        day(on)
        assert on.prices() == off.prices()
        assert repr(on.economy()) == repr(off.economy())
        a, b = off.forecast(), on.forecast()
        for k in ("vix", "index_variance", "policy_rate"):
            assert a[k] == b[k], k
        # The first close reads the published growth in every phase, so it is
        # the held phase's to rounding; the rest move as the belief spreads.
        assert abs(a["oil"][0] - b["oil"][0]) < 1e-9
        moved = max(moved, abs(a["oil"][-1] - b["oil"][-1]))
        for c in (x for x in on.contracts() if x["root"] == "OIL"):
            assert on.quote(c["symbol"])["mark"] > 0.0
    assert moved > 0.0


def test_the_path_law_is_off_on_every_shipped_preset_and_silent_in_the_digest():
    for name in tf.preset_names():
        assert tf.ModelParams.from_preset(name).oil_forecast_path_law == 0.0, name
    assert "oil_forecast_path_law" in set(tf.ModelParams.digest_silent_at_zero())
    assert model(oil_forecast_path_law=0.0).fingerprint == "pt-v21"


def test_the_path_law_moves_no_price_and_no_economy_and_only_the_forecasts_oil():
    off = tf.Engine(seed=11, universe=UNIVERSE, model=model(**ON))
    on = tf.Engine(seed=11, universe=UNIVERSE, model=model(**ON, oil_forecast_path_law=1.0))
    moved = 0.0
    for _ in range(20):
        day(off)
        day(on)
        assert on.prices() == off.prices()
        assert repr(on.economy()) == repr(off.economy())
        a, b = off.forecast(), on.forecast()
        for k in ("index_variance", "policy_rate"):
            assert a[k] == b[k], k
        moved = max(moved, abs(a["oil"][-1] - b["oil"][-1]))
    assert moved > 0.0
