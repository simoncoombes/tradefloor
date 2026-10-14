"""The forecast's model of the stabilised VIX law: `forecast_news_drift`, the read-back dials and
`forecast_vix_level_path`.

0.0 on every preset and silent in the digest; the forecast feeds nothing back, so the market is the same with them on
as off; off zero the forecast moves.
"""
import pytest

import tradefloor as tf

DIALS = {
    "forecast_news_drift": 1.0,
    "forecast_readback_dispersion": 0.32,
    "forecast_readback_dispersion_half_life": 36.5,
    "forecast_readback_dispersion_first": 0.18,
    "forecast_readback_correlation": 0.79,
    "forecast_vix_level_path": 1.0,
}
PRESETS = ("pt-v19", "pt-v20", "pt-v21")
#: A law that reads every one of them: the forecast on, the excursion coupling, the news and the slow level.
LAW = dict(index_level_listed=1.0, forecast_horizon_sessions=40.0, forecast_vix_dispersion=0.27,
           forecast_vix_dispersion_half_life=66.0, market_vol_vix_excursion=1.0, market_vol_vix_news=1.0,
           vix_fear_uptake=0.35, vix_fear_half_life=19.0)


@pytest.mark.parametrize("preset", PRESETS)
@pytest.mark.parametrize("name", sorted(DIALS))
def test_every_preset_ships_it_off(preset, name):
    assert tf.ModelParams.from_preset(preset).to_dict()[name] == 0.0


@pytest.mark.parametrize("name", sorted(DIALS))
def test_it_is_silent_in_the_digest_at_zero(name):
    assert name in set(tf.ModelParams.digest_silent_at_zero())
    explicit = tf.ModelParams.from_preset("pt-v21", **{name: 0.0})
    assert explicit.fingerprint == tf.ModelParams.from_preset("pt-v21").fingerprint


def _run(days=30, **dials):
    e = tf.Engine(seed=11, universe=tf.Universe.random(12, seed=111),
                  model=tf.ModelParams.from_preset("pt-v21", **LAW, **dials))
    path, forecasts = [], []
    for _ in range(days):
        e.run_days(1)
        path.append((e.macro_fields["vix"], tuple(e.prices())))
        f = e.forecast()
        forecasts.append((tuple(f["vix"]), tuple(f["index_variance"])))
    return path, forecasts


def test_the_forecast_moves_and_the_market_does_not():
    off_path, off_forecast = _run()
    on_path, on_forecast = _run(**DIALS)
    assert on_path == off_path
    assert on_forecast != off_forecast


@pytest.mark.parametrize("name,bad", [
    ("forecast_news_drift", 1.5), ("forecast_readback_dispersion", -0.1),
    ("forecast_readback_dispersion_half_life", 3000.0), ("forecast_readback_dispersion_first", 2.5),
    ("forecast_readback_correlation", 1.2), ("forecast_vix_level_path", -1.0)])
def test_out_of_range_is_refused(name, bad):
    with pytest.raises(Exception):
        tf.ModelParams.from_preset("pt-v21", **{name: bad})
