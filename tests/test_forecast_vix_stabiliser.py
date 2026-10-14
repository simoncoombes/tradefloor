"""The forecast's model of the stabilised VIX law: `forecast_news_drift`, the read-back dials and
`forecast_vix_level_path`.

0.0 on every preset and silent in the digest; the forecast feeds nothing back, so the market is the same with them on
as off; off zero the forecast moves.
"""
import math

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


# ---- the transient quote (`vix_quote_transient_share`, `vix_quote_transient_persistence`) ----

QUOTE = dict(vix_quote_transient_share=0.81, vix_quote_transient_persistence=0.942)


@pytest.mark.parametrize("preset", PRESETS)
@pytest.mark.parametrize("name", sorted(QUOTE))
def test_every_preset_ships_the_quote_off(preset, name):
    assert tf.ModelParams.from_preset(preset).to_dict()[name] == 0.0


@pytest.mark.parametrize("name", sorted(QUOTE))
def test_the_quote_is_silent_in_the_digest_at_zero(name):
    assert name in set(tf.ModelParams.digest_silent_at_zero())
    assert tf.ModelParams.from_preset("pt-v21", **{name: 0.0}).fingerprint == \
        tf.ModelParams.from_preset("pt-v21").fingerprint


def _quote_run(days=40, pin=None, **dials):
    e = tf.Engine(seed=13, universe=tf.Universe.random(12, seed=111),
                  model=tf.ModelParams.from_preset("pt-v21", **LAW, **dials))
    out = []
    for _ in range(days):
        if pin is not None:
            e.pin_macro(vix=pin)
        e.run_days(1)
        out.append((e.macro_fields["vix"], e.economy()["vix"], e.state_snapshot().get("vix_quote_transient")))
    return e, out


def test_the_quote_is_the_state_times_its_transient_part():
    e, out = _quote_run(**QUOTE)
    assert any(abs(q) > 0 for _, _, q in out)
    for pub, state, q in out:
        assert pub == pytest.approx(min(state * math.exp(q), 181.32949829101562), rel=1e-12)


def test_off_there_is_no_quote_state_and_the_quote_is_the_state():
    _, out = _quote_run()
    assert all(q is None and pub == state for pub, state, q in out)


def test_a_pinned_vix_clears_the_transient_part():
    _, out = _quote_run(pin=25.0, **QUOTE)
    assert all(q == 0.0 and pub == 25.0 for pub, _, q in out)


def test_a_restore_reproduces_the_quote():
    e, _ = _quote_run(days=20, **QUOTE)
    snap = e.state_snapshot()
    f = tf.Engine(seed=13, universe=tf.Universe.random(12, seed=111),
                  model=tf.ModelParams.from_preset("pt-v21", **LAW, **QUOTE))
    f.restore_state(snap)
    assert f.state_hash() == e.state_hash()
    e.run_days(5); f.run_days(5)
    assert f.macro_fields["vix"] == e.macro_fields["vix"] and f.state_hash() == e.state_hash()


@pytest.mark.parametrize("name,bad", [("vix_quote_transient_share", 1.0), ("vix_quote_transient_share", -0.1),
                                      ("vix_quote_transient_persistence", 1.0)])
def test_the_quote_out_of_range_is_refused(name, bad):
    with pytest.raises(Exception):
        tf.ModelParams.from_preset("pt-v21", **{name: bad})


# ---- the old VIX law on a pinned close (`pinned_vix_coupling`, `pinned_vix_exponent`) ----

PINNED = dict(pinned_vix_coupling=0.75, pinned_vix_exponent=4.0)
#: The stabilised law the dials are for: the excursion on, a weak coupling and a low exponent.
STAB = dict(LAW, market_vol_vix_coupling=0.25, market_vol_vix_exponent=1.5)


@pytest.mark.parametrize("preset", PRESETS)
@pytest.mark.parametrize("name", sorted(PINNED))
def test_every_preset_ships_the_pinned_law_off(preset, name):
    assert tf.ModelParams.from_preset(preset).to_dict()[name] == 0.0


@pytest.mark.parametrize("name", sorted(PINNED))
def test_the_pinned_law_is_silent_in_the_digest_at_zero(name):
    assert name in set(tf.ModelParams.digest_silent_at_zero())
    assert tf.ModelParams.from_preset("pt-v21", **{name: 0.0}).fingerprint == \
        tf.ModelParams.from_preset("pt-v21").fingerprint


def _pinned_run(free=20, pinned=10, pin=60.0, forced=False, law=STAB, **dials):
    e = tf.Engine(seed=17, universe=tf.Universe.random(12, seed=111),
                  model=tf.ModelParams.from_preset("pt-v21", **law, **dials))
    out = []
    for d in range(free + pinned):
        if d >= free:
            e.pin_macro(vix=pin, vix_sets_variance=forced)
        e.run_days(1)
        out.append((e.macro_fields["vix"], tuple(e.prices()), e.state_snapshot()["market_variance"][0]))
    return e, out


def test_free_closes_are_untouched():
    e_off, off = _pinned_run(pinned=0)
    e_on, on = _pinned_run(pinned=0, **PINNED)
    assert on == off
    # Every piece of state but the model's own fingerprint is the same (repr: the RNG's words read as floats
    # include NaN payloads, which compare unequal to themselves).
    a, b = e_off.state_snapshot(), e_on.state_snapshot()
    assert set(a) == set(b)
    assert all(repr(a[k]) == repr(b[k]) for k in a if k != "model_fingerprint")


@pytest.mark.parametrize("forced", [False, True])
def test_a_pinned_vix_reaches_the_variance(forced):
    _, off = _pinned_run(forced=forced)
    _, on = _pinned_run(forced=forced, **PINNED)
    assert on[:20] == off[:20]
    # The stabilised law barely moves the variance at a pinned 60; the old law's coupling and exponent do.
    assert on[-1][2] > 2.0 * off[-1][2]


def test_a_forced_close_sets_the_old_laws_level():
    # From the same state, one forced close under the dials sets the variance the excursion-off law at the old
    # coupling and exponent sets.
    e, _ = _pinned_run(pinned=0, **PINNED)
    snap = e.state_snapshot()
    old_model = tf.ModelParams.from_preset("pt-v21", **dict(STAB, market_vol_vix_excursion=0.0,
                                                            market_vol_vix_coupling=0.75, market_vol_vix_exponent=4.0))
    old = tf.Engine(seed=17, universe=tf.Universe.random(12, seed=111), model=old_model)
    # A restore refuses another model's snapshot; here the point is one close's arithmetic from the same state under
    # two sets of coefficients, so the snapshot is relabelled.
    snap["model_fingerprint"] = old_model.fingerprint
    old.restore_state(snap)
    for x in (e, old):
        x.pin_macro(vix=60.0, vix_sets_variance=True)
        x.run_days(1)
    assert e.state_snapshot()["market_variance"][0] == pytest.approx(old.state_snapshot()["market_variance"][0],
                                                                     rel=1e-12)


@pytest.mark.parametrize("name,bad", [("pinned_vix_coupling", 1.5), ("pinned_vix_coupling", -0.1),
                                      ("pinned_vix_exponent", 9.0), ("pinned_vix_exponent", -1.0)])
def test_the_pinned_law_out_of_range_is_refused(name, bad):
    with pytest.raises(Exception):
        tf.ModelParams.from_preset("pt-v21", **{name: bad})
