"""core_trend_sd / core_trend_persistence: core inflation's trend draws on its own key.

The trend's normals are `GameRng::keyed(key, CORE_TREND_TAG, month)` (and the opening's at `u64::MAX`), fresh
generators that take nothing from any stream. So an engine with the trend on at a negligible sd takes, stream for
stream, the draws the engine without it takes, and its prices stay within the sd's reach; at a real sd headline
moves, and the state hash carries the trend.
"""
import numpy as np
import tradefloor as tf

DAYS = 130


def _run(**dials):
    u = tf.Universe.random(12, seed=111, bonds=True)
    e = tf.Engine(seed=4242, universe=u, model=tf.ModelParams.from_preset("pt-v20", **dials))
    for _ in range(DAYS):
        e.open_market(); e.run_session(9, 30, 3, 390); e.close_market()
    return e


def _prices(e):
    return np.frombuffer(e.prices(), dtype=np.float64)


def test_the_trend_takes_no_draw_from_any_stream():
    off = _run()
    tiny = _run(core_trend_sd=1e-12, core_trend_persistence=0.993)
    assert dict(tiny.stream_positions()) == dict(off.stream_positions())
    a, b = _prices(off), _prices(tiny)
    assert np.all(np.abs(a - b) <= 1e-6 * np.maximum(1.0, np.abs(a)))
    eo, et = off.economy(), tiny.economy()
    assert all(abs(eo[k] - et[k]) <= 1e-6 * max(1.0, abs(eo[k])) for k in eo if isinstance(eo[k], float))


def test_at_zero_the_trend_is_the_engine_that_stood():
    # The persistence alone is read by nothing; the hash differs only by the model's digest, which carries it.
    off = _run()
    zero = _run(core_trend_sd=0.0, core_trend_persistence=0.993)
    assert dict(zero.stream_positions()) == dict(off.stream_positions())
    assert zero.prices() == off.prices()
    assert _run(core_trend_sd=0.0).state_hash() == off.state_hash()


def test_on_the_trend_moves_inflation_and_its_state_is_hashed():
    off = _run()
    on = _run(core_trend_sd=0.17, core_trend_persistence=0.993)
    assert on.state_hash() != off.state_hash()
    assert on.economy()["inflation_rate"] != off.economy()["inflation_rate"]
