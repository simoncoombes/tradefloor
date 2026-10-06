"""The opening cache: an engine served from it is the engine built cold.

pt-v21 plays a 504-session market prehistory every time an engine is built,
about 1.45 s over 20 names. `Engine` keeps the engines whose build played
one, up to `Engine.opening_cache_info()["capacity"]` of them, and serves a
later build from the same seed, universe and model as a copy. These tests
hold that copy to the engine a build with the cache off produces: the same
state hash and draw counts at the open, and the same market day after day,
traded, forked and restored. They also hold every argument to the key, so a
different engine is never served.

The core's own tests (`rust/src/opening_cache.rs`) check the key against
each argument to the bit, a NaN and a roster too large to keep.
"""

from __future__ import annotations

import json
import math
import sys
import threading
import time
from pathlib import Path

import pytest

import tradefloor as tf

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import known_answer_traded  # noqa: E402

Engine = tf.Engine


def info() -> dict:
    return Engine.opening_cache_info()


@pytest.fixture
def emptied():
    """The cache emptied and at its default size, put back as it was."""
    capacity = info()["capacity"]
    Engine.set_opening_cache_capacity(0)
    Engine.set_opening_cache_capacity(16)
    yield
    Engine.set_opening_cache_capacity(capacity)


def cold(**kwargs) -> tf.Engine:
    """An engine built with the cache off, so nothing in it was served."""
    capacity = info()["capacity"]
    Engine.set_opening_cache_capacity(0)
    try:
        before = info()
        engine = Engine(**kwargs)
        assert info()["hits"] == before["hits"]
        return engine
    finally:
        Engine.set_opening_cache_capacity(capacity)


def served(**kwargs) -> tf.Engine:
    """An engine the cache serves: built once if it is not kept, then again."""
    Engine(**kwargs)
    before = info()
    engine = Engine(**kwargs)
    assert info()["hits"] == before["hits"] + 1, "the second build was not served"
    return engine


def same_state(a: tf.Engine, b: tf.Engine, *, counts: bool = True) -> None:
    """The same state to the bit. `counts` compares the draws each engine
    object has taken too, which a restore does not carry."""
    assert a.state_hash() == b.state_hash()
    assert a.stream_positions() == b.stream_positions()
    assert a.prices() == b.prices()
    if counts:
        assert a.draws_consumed == b.draws_consumed
        assert a.draws_by_stream() == b.draws_by_stream()


CASES = [
    (0, 3, 1),
    (7, 12, 3),
    (2**63 + 11, 5, 8),
]


@pytest.mark.parametrize("seed,names,roster", CASES)
def test_a_served_engine_is_the_cold_build_day_after_day(emptied, seed, names, roster):
    universe = tf.Universe.random(names, seed=roster)
    a = cold(seed=seed, universe=universe)
    b = served(seed=seed, universe=universe)
    same_state(a, b)
    assert a.macro_fields == b.macro_fields
    for _ in range(10):
        a.run_days(1)
        b.run_days(1)
        same_state(a, b)
        assert a.session_prices() == b.session_prices()
        assert a.session_volumes() == b.session_volumes()


def test_a_traded_run_from_the_cache_is_the_known_answer(emptied):
    """The traded known answer, pt-v21 and twelve names, gives its committed
    digest whether its engine was built cold or served."""
    expected = json.loads((HERE / "known_answer_traded.json")
                          .read_text(encoding="utf-8"))["sha256"]
    capacity = info()["capacity"]
    Engine.set_opening_cache_capacity(0)
    try:
        assert known_answer_traded.traded_digest() == expected
    finally:
        Engine.set_opening_cache_capacity(capacity)
    for _ in range(2):
        before = info()["hits"]
        assert known_answer_traded.traded_digest() == expected
    assert info()["hits"] > before, "the traded run's engine was not served"


def test_a_fork_of_a_served_engine_trades_as_a_fork_of_a_cold_one(emptied):
    universe = tf.Universe.random(6, seed=4)
    a = cold(seed=31, universe=universe)
    b = served(seed=31, universe=universe)
    for engine in (a, b):
        engine.run_days(3)
    fa, untraded = a.fork(2)
    fb, = b.fork(1)
    ticker = universe[0].ticker
    for day in range(4, 9):
        for engine, traded in ((fa, True), (fb, True), (untraded, False)):
            engine.open_market(day=day)
            engine.run_session(9, 30, 3, 195,
                               fills={ticker: (2e5, 0.0)} if traded else None)
            engine.run_session(12, 45, 3, 195, close_at_end=True,
                               flow_per_tick={ticker: (0.0, 1e3)} if traded else None)
        same_state(fa, fb)
    same_state(a, b)
    assert untraded.prices() != fa.prices(), "the trades moved nothing"


def test_a_served_engine_restores_into_a_cold_one(emptied):
    universe = tf.Universe.random(5, seed=9)
    a = cold(seed=12, universe=universe)
    b = served(seed=12, universe=universe)
    b.run_days(4)
    a.restore_state(b.state_snapshot())
    same_state(a, b, counts=False)
    a.run_days(3)
    b.run_days(3)
    same_state(a, b, counts=False)


def test_every_argument_is_in_the_key(emptied):
    """A build that differs in anything is built, not served: the seed, one
    ulp of one price, the roster's order, one ulp of one dial, and the sign
    of a zero the model's fingerprint cannot see."""
    universe = list(tf.Universe.random(3, seed=2))
    Engine(seed=5, universe=universe)
    first = universe[0]
    nudged = tf.Instrument(first.ticker, first.sector,
                           initial_price=math.nextafter(first.initial_price, math.inf),
                           shares_outstanding=first.shares_outstanding,
                           eps=first.eps, book_value_per_share=first.book_value_per_share,
                           revenue_growth=first.revenue_growth,
                           avg_volume=first.avg_volume, beta=first.beta,
                           short_interest=first.short_interest)
    sigma = tf.ModelParams.from_preset("pt-v21").market_factor_sigma
    off = next(name for name in tf.ModelParams.digest_silent_at_zero()
               if getattr(tf.ModelParams.from_preset("pt-v21"), name) == 0.0)
    signed = tf.ModelParams.from_preset("pt-v21", **{off: -0.0})
    assert signed.fingerprint == "pt-v21"
    variants = {
        "seed": dict(seed=6, universe=universe),
        "price": dict(seed=5, universe=[nudged, *universe[1:]]),
        "order": dict(seed=5, universe=[universe[1], universe[0], universe[2]]),
        "dial": dict(seed=5, universe=universe, model=tf.ModelParams.from_preset(
            "pt-v21", market_factor_sigma=math.nextafter(sigma, 1.0))),
        "signed zero": dict(seed=5, universe=universe, model=signed),
    }
    for name, kwargs in variants.items():
        before = info()
        Engine(**kwargs)
        after = info()
        assert after["hits"] == before["hits"], f"{name}: served the wrong engine"
        assert after["misses"] == before["misses"] + 1, name


def test_only_a_build_that_plays_a_prehistory_is_kept(emptied):
    """A named opening plays no prehistory, and neither does pt-v20."""
    universe = tf.Universe.random(4, seed=6)
    before = info()
    Engine(seed=3, universe=universe, macro_state=tf.Macro())
    Engine(seed=3, universe=universe, model="pt-v20")
    assert info() == before
    Engine(seed=3, universe=universe)
    assert info()["entries"] == 1
    assert info()["names"] == 4


def test_the_least_recently_used_engine_leaves_first(emptied):
    Engine.set_opening_cache_capacity(2)
    universe = tf.Universe.random(2, seed=1)
    model = tf.ModelParams.from_preset("pt-v21", market_prehistory_sessions=21.0)

    def build(seed):
        before = info()["hits"]
        Engine(seed=seed, universe=universe, model=model)
        return info()["hits"] > before

    # 1 is used again before 3 arrives, so 2 is the one that leaves.
    assert [build(s) for s in (1, 2, 1, 3, 1, 2)] == \
        [False, False, True, False, True, False]
    assert info()["entries"] == 2


def test_the_capacity_is_a_count_and_zero_turns_it_off(emptied):
    universe = tf.Universe.random(2, seed=1)
    Engine(seed=1, universe=universe)
    assert info()["entries"] == 1
    Engine.set_opening_cache_capacity(0)
    assert info()["entries"] == 0
    assert info()["capacity"] == 0
    before = info()
    Engine(seed=1, universe=universe)
    assert info() == before
    with pytest.raises(tf.ValidationError, match="0 or more"):
        Engine.set_opening_cache_capacity(-1)


def test_threads_that_build_one_engine_build_the_same_one(emptied):
    universe = tf.Universe.random(3, seed=5)
    model = tf.ModelParams.from_preset("pt-v21", market_prehistory_sessions=63.0)
    hashes = []

    def build():
        hashes.append(Engine(seed=77, universe=universe, model=model).state_hash())

    threads = [threading.Thread(target=build) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(hashes) == 6
    assert len(set(hashes)) == 1
    assert hashes[0] == cold(seed=77, universe=universe, model=model).state_hash()


def test_a_build_lets_other_threads_run(emptied):
    """The build releases the GIL, as a session does, so a threaded sweep
    builds its engines side by side and a host's other threads keep running
    through a prehistory. Measured as the rate another thread counts at
    during a cold build against its rate during a sleep: 0.7 to 0.85 with
    the GIL released, 0.01 with it held."""
    universe = tf.Universe.random(3, seed=2)
    count, stop = [0], [False]

    def spin():
        while not stop[0]:
            count[0] += 1

    def rate(fn):
        before, start = count[0], time.perf_counter()
        fn()
        return (count[0] - before) / (time.perf_counter() - start)

    spinner = threading.Thread(target=spin)
    spinner.start()
    try:
        idle = rate(lambda: time.sleep(0.2))
        building = rate(lambda: cold(seed=91, universe=universe))
    finally:
        stop[0] = True
        spinner.join()
    assert building > 0.2 * idle, (building, idle)


def test_engines_built_on_threads_are_the_engines_built_in_turn(emptied):
    universe = tf.Universe.random(3, seed=8)
    model = tf.ModelParams.from_preset("pt-v21", market_prehistory_sessions=21.0)
    seeds = range(40, 46)
    Engine.set_opening_cache_capacity(0)
    in_turn = {s: Engine(seed=s, universe=universe, model=model).state_hash()
               for s in seeds}
    built = {}

    def build(seed):
        built[seed] = Engine(seed=seed, universe=universe, model=model).state_hash()

    threads = [threading.Thread(target=build, args=(s,)) for s in seeds]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert built == in_turn


def test_a_batch_served_from_the_cache_is_the_batch_built_cold(emptied):
    universe = tf.Universe.random(3, seed=8)
    model = tf.ModelParams.from_preset("pt-v21", market_prehistory_sessions=21.0)
    seeds = [50, 51, 52]

    def batch():
        b = tf.EngineBatch(seeds=seeds, universe=universe, model=model)
        b.open_market()
        b.run_session(9, 30, 3, 120)
        return b.prices(), b.draws_consumed

    Engine.set_opening_cache_capacity(0)
    built_cold = batch()
    Engine.set_opening_cache_capacity(16)
    batch()
    before = info()["hits"]
    assert batch() == built_cold
    assert info()["hits"] == before + len(seeds)
