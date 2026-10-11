"""fed_rstar_sd / fed_rstar_persistence: the neutral real rate's process draws on its own key.

The process's normals are `GameRng::keyed(key, RSTAR_TAG, session)` (and the opening's at `u64::MAX`), fresh
generators that take nothing from any stream. So an engine with the process on at a negligible sd takes, stream for
stream, the draws the engine without it takes, and its prices stay within the sd's reach; at a real sd only the
rule moves, and the draws the economy takes stay those its own path asks for.
"""
import tradefloor as tf

ARM = dict(fed_neutral_real_rate=2.0, fed_put_gain=3.0, fed_put_half_life=126.0)
DAYS = 130


def _run(**dials):
    u = tf.Universe.random(12, seed=111, bonds=True)
    e = tf.Engine(seed=4242, universe=u, model=tf.ModelParams.from_preset("pt-v20", **ARM, **dials))
    for _ in range(DAYS):
        e.open_market(); e.run_session(9, 30, 3, 390); e.close_market()
    return e


def test_the_process_takes_no_draw_from_any_stream():
    off = _run()
    tiny = _run(fed_rstar_sd=1e-12, fed_rstar_persistence=0.9819)
    assert dict(tiny.stream_positions()) == dict(off.stream_positions())
    a, b = list(off.prices()), list(tiny.prices())
    assert all(abs(x - y) <= 1e-6 * max(1.0, abs(x)) for x, y in zip(a, b))


def test_at_zero_the_process_is_the_engine_that_stood():
    # The persistence alone is read by nothing; the hash differs only by the model's digest, which carries it.
    off = _run()
    zero = _run(fed_rstar_sd=0.0, fed_rstar_persistence=0.9819)
    assert dict(zero.stream_positions()) == dict(off.stream_positions())
    assert list(zero.prices()) == list(off.prices())
    assert _run(fed_rstar_sd=0.0).state_hash() == off.state_hash()


def test_on_the_process_moves_the_rule_and_its_state_is_hashed():
    off = _run()
    on = _run(fed_rstar_sd=0.1498, fed_rstar_persistence=0.9819)
    assert on.state_hash() != off.state_hash()
