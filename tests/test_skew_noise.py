"""The skew premium's own noise (`surface_skew_noise_sd`, `surface_skew_noise_half_life`).

Two dials, 0.0 on every shipped preset. Off zero the skewness the index
surface's 21-session strip is fitted to carries an AR(1) of its own, so the
Cboe SKEW the strip prices moves apart from the VIX. Its draws are keyed on
the root seed and take nothing from any stream, and an option's price feeds
nothing a stock price reads, so a model with the noise prints the prices the
same model prints without it. These tests hold that, the process (its
opening, step and keep), the refusals, and the state across a snapshot, a
fork, a replay and the state hash.
"""
from __future__ import annotations

import math
import struct

import pytest

import tradefloor as tf
from tradefloor.manifest import state_hash
from tradefloor.replay import replay

SMALL = tf.Universe.random(10, seed=5)
DIALS = ("surface_skew_noise_sd", "surface_skew_noise_half_life")
SURFACE = dict(index_level_listed=1.0, vix_intraday_live=1.0, forecast_horizon_sessions=252.0,
               surface_ssvi=1.0, surface_skew_physical=-0.8, surface_skew_physical_slope=0.3,
               surface_skew_premium=-1.0, surface_skew_premium_slope=0.5, surface_curvature=1.0,
               surface_curvature_exponent=0.5, options_index_listed=1.0)
NOISE = dict(surface_skew_noise_sd=0.5, surface_skew_noise_half_life=20.0)
ON = dict(SURFACE, **NOISE)


def model(**dials):
    return tf.ModelParams.from_preset("pt-v21", **dials)


def day(engine):
    engine.open_market()
    engine.run_session(9, 30, 3, 390)
    engine.close_market()


def test_both_dials_are_off_on_every_shipped_preset_and_silent_in_the_digest():
    silent = set(tf.ModelParams.digest_silent_at_zero())
    for name in tf.preset_names():
        p = tf.ModelParams.from_preset(name)
        for dial in DIALS:
            assert getattr(p, dial) == 0.0, (name, dial)
    assert set(DIALS) <= silent
    assert model(**SURFACE).fingerprint == model(**dict(SURFACE, surface_skew_noise_sd=0.0)).fingerprint


@pytest.mark.parametrize("dials, words", [
    (dict(index_level_listed=1.0, surface_skew_noise_sd=0.5), "surface_ssvi"),
    (dict(SURFACE, surface_curvature=0.0, surface_skew_noise_sd=0.5), "surface_curvature"),
    (dict(SURFACE, surface_skew_noise_half_life=20.0), "surface_skew_noise_sd"),
    (dict(SURFACE, surface_skew_noise_sd=5.5), "surface_skew_noise_sd"),
    (dict(SURFACE, surface_skew_noise_sd=0.5, surface_skew_noise_half_life=-1.0), "surface_skew_noise_half_life"),
])
def test_a_dial_without_what_it_reads_is_refused(dials, words):
    with pytest.raises(tf.ValidationError, match=words):
        model(**dials)


def test_off_there_is_no_noise_and_nothing_is_carried():
    engine = tf.Engine(seed=3, universe=SMALL, model=model(**SURFACE))
    day(engine)
    assert engine.skew_noise is None
    assert "skew_noise" not in engine.state_snapshot()


def test_the_prices_are_the_same_with_the_noise_on():
    def run(params):
        engine = tf.Engine(seed=7, universe=SMALL, model=params)
        out = []
        for _ in range(8):
            day(engine)
            engine.surface("IDX").strip_skew(21.0)
            out.append((engine.prices(), repr(sorted(engine.economy().items()))))
        return out
    assert run(model(**SURFACE)) == run(model(**ON))


def test_the_noise_moves_the_skewness_asked_and_the_skew():
    off = tf.Engine(seed=11, universe=SMALL, model=model(**SURFACE))
    on = tf.Engine(seed=11, universe=SMALL, model=model(**ON))
    for _ in range(4):
        day(off)
        day(on)
        x = on.skew_noise
        assert x is not None and x != 0.0
        s, f = on.surface("IDX"), off.surface("IDX")
        assert s.skewness_target - f.skewness_target == pytest.approx(x, abs=1e-12)
        assert s.strip_skew(21.0) == pytest.approx(100.0 - 10.0 * s.skewness_target, abs=1e-5)
        assert s.strip_vix(21.0) == pytest.approx(on.macro_state.vix, abs=1e-6)
        assert on.option_arbitrage() == []


def test_the_noise_is_an_ar1_with_the_sd_and_half_life_asked():
    sd, h = 0.5, 20.0
    phi = 0.5 ** (1.0 / h)
    xs = []
    for seed in range(40):
        engine = tf.Engine(seed=seed, universe=SMALL, model=model(**dict(ON, surface_skew_noise_sd=sd,
                                                                            surface_skew_noise_half_life=h)))
        path = [engine.skew_noise]
        for _ in range(6):
            day(engine)
            path.append(engine.skew_noise)
        xs.append(path)
    opening = [p[0] for p in xs]
    # The opening is a draw from the stationary distribution.
    m = sum(opening) / len(opening)
    v = sum((x - m) ** 2 for x in opening) / (len(opening) - 1)
    assert abs(m) < 3 * sd / math.sqrt(len(opening))
    assert 0.5 * sd * sd < v < 1.6 * sd * sd
    # Each step keeps phi of the last and adds sd sqrt(1 - phi^2) of a normal.
    innov = [p[k + 1] - phi * p[k] for p in xs for k in range(6)]
    mi = sum(innov) / len(innov)
    vi = sum((x - mi) ** 2 for x in innov) / (len(innov) - 1)
    assert vi == pytest.approx(sd * sd * (1 - phi * phi), rel=0.35)


def test_the_same_seed_draws_the_same_noise_and_another_does_not():
    def path(seed):
        engine = tf.Engine(seed=seed, universe=SMALL, model=model(**ON))
        out = [engine.skew_noise]
        for _ in range(3):
            day(engine)
            out.append(engine.skew_noise)
        return out
    assert path(5) == path(5)
    assert path(5) != path(6)


def _engine(seed=13):
    engine = tf.Engine(seed=seed, universe=SMALL, model=model(**ON))
    for _ in range(4):
        day(engine)
    engine.open_market()
    engine.run_session(9, 30, 3, 40)
    return engine


def _go_on(engine):
    out = []
    engine.run_session(10, 10, 3, 350)
    engine.close_market()
    for _ in range(6):
        day(engine)
        out.append((engine.skew_noise, engine.surface("IDX").strip_skew(21.0)))
    out.append(engine.state_hash())
    return out


def test_a_snapshot_carries_the_noise_and_restores_it():
    engine = _engine()
    snap = engine.state_snapshot()
    assert "skew_noise" in snap
    assert state_hash(snap) == engine.state_hash()
    restored = tf.Engine(seed=99, universe=SMALL, model=model(**ON))
    restored.restore_state(snap)
    assert restored.skew_noise == engine.skew_noise
    assert restored.state_hash() == engine.state_hash()
    assert _go_on(restored) == _go_on(engine)


def test_the_state_hash_reads_the_noise():
    snap = _engine().state_snapshot()
    raw = snap["skew_noise"]
    x, = struct.unpack("<d", raw[:8])
    moved = dict(snap, skew_noise=struct.pack("<d", math.nextafter(x, math.inf)) + raw[8:])
    assert state_hash(moved) != state_hash(snap)


def test_a_fork_continues_like_the_original():
    engine = _engine(seed=14)
    fork, = engine.fork(1)
    assert fork.state_hash() == engine.state_hash()
    assert _go_on(fork) == _go_on(engine)


def test_a_snapshot_and_a_model_that_disagree_on_the_noise_are_refused():
    snap = _engine().state_snapshot()
    off = tf.Engine(seed=99, universe=SMALL, model=model(**SURFACE))
    with pytest.raises(tf.ValidationError, match="surface_skew_noise_sd"):
        off.restore_state(snap)
    plain = tf.Engine(seed=13, universe=SMALL, model=model(**SURFACE))
    day(plain)
    on = tf.Engine(seed=99, universe=SMALL, model=model(**ON))
    with pytest.raises(tf.ValidationError, match="skew_noise"):
        on.restore_state(plain.state_snapshot())


def test_the_order_log_replays_to_the_same_state():
    engine = _engine(seed=15)
    _go_on(engine)
    again = replay(engine.order_log, seed=15, universe=SMALL, model=model(**ON))
    assert again.state_hash() == engine.state_hash()
    assert again.skew_noise == engine.skew_noise
