"""The growth oil's forecast reads (`oil_forecast_cycle_growth`).

A switch, 0.0 on every shipped preset. At 1.0 the forecast's oil reads
growth's expectation over the cycle's phases instead of the believed phase
held for the whole horizon. It reads the forecast only, so stock prices and
the economy are the same with it on as off, and the oil futures, which mark
at the forecast, are what move.
"""
from __future__ import annotations


import pytest
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


def test_a_restored_engine_reads_the_path_laws_held_paths():
    """The held paths are snapshot state (`oil_path_hold`): a restored engine
    reads the paths the original held, and the two stay equal through the
    closes that read them and the next one that simulates afresh. Restored
    after the seventh close, it holds the paths the sixth simulated; the
    eighth to tenth read them and the eleventh simulates again."""
    m = model(**ON, oil_forecast_path_law=1.0)
    parent = tf.Engine(seed=11, universe=UNIVERSE, model=m)
    for _ in range(7):
        day(parent)
    snapshot = parent.state_snapshot()
    assert "oil_path_hold" in snapshot
    target = tf.Engine(seed=11, universe=UNIVERSE, model=m)
    target.restore_state(snapshot)
    for _ in range(5):
        day(parent)
        day(target)
        assert target.prices() == parent.prices()
        assert target.forecast()["oil"] == parent.forecast()["oil"]


def test_the_held_paths_are_carried_only_with_the_switch_on():
    for dials in (ON, dict(ON, oil_forecast_cycle_growth=1.0)):
        e = tf.Engine(seed=11, universe=UNIVERSE, model=model(**dials))
        for _ in range(3):
            day(e)
        assert "oil_path_hold" not in e.state_snapshot()


#: The path law's start fixes (v23 step 4c): each a switch that needs the path
#: law and changes only the start law its paths draw on.
START_FIXES = ("oil_path_law_zero_return",)


@pytest.mark.parametrize("name", START_FIXES)
def test_a_start_fix_is_off_on_every_shipped_preset_and_silent_in_the_digest(name):
    for preset in tf.preset_names():
        assert getattr(tf.ModelParams.from_preset(preset), name) == 0.0, preset
    assert name in set(tf.ModelParams.digest_silent_at_zero())
    assert model(**{name: 0.0}).fingerprint == "pt-v21"


@pytest.mark.parametrize("name", START_FIXES)
def test_a_start_fix_is_refused_without_the_path_law(name):
    with pytest.raises(tf.ValidationError, match=name):
        model(**ON, **{name: 1.0})


@pytest.mark.parametrize("name", START_FIXES)
def test_a_start_fix_moves_no_price_and_no_economy_and_only_the_forecasts_oil(name):
    law = tf.Engine(seed=11, universe=UNIVERSE, model=model(**ON, oil_forecast_path_law=1.0))
    fixed = tf.Engine(seed=11, universe=UNIVERSE, model=model(**ON, oil_forecast_path_law=1.0, **{name: 1.0}))
    moved = 0.0
    for _ in range(12):
        day(law)
        day(fixed)
        assert fixed.prices() == law.prices()
        assert repr(fixed.economy()) == repr(law.economy())
        a, b = law.forecast(), fixed.forecast()
        for k in ("index_variance", "policy_rate"):
            assert a[k] == b[k], k
        moved = max(moved, abs(a["oil"][-1] - b["oil"][-1]))
    assert moved > 0.0
