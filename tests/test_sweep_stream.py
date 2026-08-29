"""Streaming tables for many seeds, without holding them all.

The claim is that peak memory is one engine rather than N, and the way to
check it is not to measure bytes — it is to check that work has not HAPPENED
yet. A generator that eagerly ran every seed would still be a generator, and
would still look lazy from the outside.
"""

import pytest

pa = pytest.importorskip("pyarrow")

import tradefloor

UNIVERSE = tradefloor.Universe.random(12, seed=7)


class CountingScenario:
    """A scenario that records which seeds it has been applied to.

    `scenario.apply` is called once per day per seed, so it is a real hook
    into how much work has happened -- which is the only way to check
    laziness. Being a generator proves nothing: one that ran every seed into a
    list and yielded from it would behave identically from the outside.
    """

    def __init__(self):
        self.applied = 0

    def apply(self, engine, day):
        self.applied += 1


def test_nothing_runs_until_the_first_seed_is_asked_for():
    watcher = CountingScenario()
    stream = tradefloor.sweep(range(5), universe=UNIVERSE, days=2,
                           ticks_per_day=40, scenario=watcher)
    # Constructing the generator ran nothing at all.
    assert watcher.applied == 0

    iterator = iter(stream)
    seed, _ = next(iterator)
    assert seed == 0
    # Exactly ONE seed's two days, not five seeds' worth.
    assert watcher.applied == 2

    next(iterator)
    assert watcher.applied == 4


def test_a_bounded_window_runs_ahead_but_not_to_the_end():
    # workers=n keeps n engines in flight, so peak memory is n engines rather
    # than len(seeds). Checked by counting: after taking one result, at most
    # `workers` seeds have been started, not all twenty.
    watcher = CountingScenario()
    iterator = iter(tradefloor.sweep(range(20), universe=UNIVERSE, days=1,
                                  ticks_per_day=40, workers=3,
                                  scenario=watcher))
    next(iterator)
    assert watcher.applied <= 4, watcher.applied      # 3 in flight + 1 queued
    assert watcher.applied >= 1


def test_seeds_arrive_in_order_serially():
    seeds = [s for s, _ in tradefloor.sweep(range(6), universe=UNIVERSE, days=1,
                                         ticks_per_day=40)]
    assert seeds == list(range(6))


def test_seeds_arrive_in_order_with_workers():
    # Yielded in SEED order, never completion order. A sweep whose row order
    # depended on scheduling would be non-deterministic in the one way this
    # library exists to avoid.
    seeds = [s for s, _ in tradefloor.sweep(range(9), universe=UNIVERSE, days=1,
                                         ticks_per_day=40, workers=4)]
    assert seeds == list(range(9))


def test_a_swept_table_matches_a_hand_run_engine():
    swept = dict(tradefloor.sweep([5], universe=UNIVERSE, days=2,
                               ticks_per_day=100, collect="bars"))[5]

    engine = tradefloor.Engine(seed=5, universe=UNIVERSE)
    for day in range(2):
        engine.open_market()
        engine.run_session(9, 30, 3, 100)
        engine.close_market()
        engine.record(day)

    assert pa.table(swept).equals(pa.table(engine.bars()))


def test_workers_do_not_change_the_answer():
    # Threads share an address space, so a mistake here is a data race. Each
    # seed builds its own engine and they share nothing -- asserted, not
    # assumed.
    serial = {s: pa.table(t).to_pydict()["close"]
              for s, t in tradefloor.sweep(range(4), universe=UNIVERSE, days=1,
                                        ticks_per_day=60)}
    for workers in (2, 4):
        threaded = {s: pa.table(t).to_pydict()["close"]
                    for s, t in tradefloor.sweep(range(4), universe=UNIVERSE,
                                              days=1, ticks_per_day=60,
                                              workers=workers)}
        assert threaded == serial, workers


def test_every_collect_mode_streams_per_day():
    for collect, expected in (("bars", 3), ("truth", 3), ("macro", 1)):
        _, table = next(iter(tradefloor.sweep([1], universe=UNIVERSE, days=3,
                                           ticks_per_day=40, collect=collect)))
        assert table.num_rows > 0, collect
        # bars and truth batch per day; macro is one row per day in one batch.
        assert table.num_batches == expected, (collect, table.num_batches)


def test_a_scenario_reaches_the_sweep():
    from tradefloor.scenario import Scenario

    calm = dict(tradefloor.sweep([3], universe=UNIVERSE, days=4, ticks_per_day=60,
                              collect="macro",
                              scenario=Scenario().hold(vix=15.0)))[3]
    spiked = dict(tradefloor.sweep([3], universe=UNIVERSE, days=4,
                                ticks_per_day=60, collect="macro",
                                scenario=Scenario().hold(vix=45.0)))[3]
    assert pa.table(calm).to_pydict()["vix"] != pa.table(spiked).to_pydict()["vix"]


def test_degenerate_inputs_are_refused():
    with pytest.raises(tradefloor.ValidationError, match="no seeds"):
        next(iter(tradefloor.sweep([], universe=UNIVERSE)))
    with pytest.raises(tradefloor.ValidationError, match="unknown collect"):
        next(iter(tradefloor.sweep([1], universe=UNIVERSE, collect="prices")))
    with pytest.raises(tradefloor.ValidationError, match="workers"):
        next(iter(tradefloor.sweep([1], universe=UNIVERSE, workers=0)))
    with pytest.raises(tradefloor.ValidationError):
        next(iter(tradefloor.sweep([1], universe=UNIVERSE, days=0)))
