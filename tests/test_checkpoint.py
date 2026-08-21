"""Forking a simulation mid-flight.

The third counterfactual this library offers. `tca.analyse` asks what your
trading cost against a world where you did not trade; `scenario.compare` asks
what a macro path did; this asks what happens NEXT from a state you have
already reached.
"""

import json
import struct

import pytest

import pretium
from pretium.scenario import Scenario

UNIVERSE = pretium.Universe.random(8, seed=2)


def mark(days=3, seed=5, macro=None):
    engine = pretium.Engine(seed=seed, universe=UNIVERSE, macro_state=macro)
    engine.run_days(days, record=False)
    return engine, pretium.Checkpoint.of(engine, universe=UNIVERSE, seed=seed,
                                         macro=macro)


def test_resuming_lands_on_the_exact_state():
    engine, point = mark()
    restored = point.resume()
    assert restored.prices() == engine.prices()
    assert restored.draws_consumed == engine.draws_consumed


def test_branches_are_identical_until_they_are_driven_apart():
    _, point = mark()
    a, b = point.branch(2)
    assert a.prices() == b.prices()
    assert a.draws_consumed == b.draws_consumed

    # Same day, one with order flow. Everything before the fork was identical,
    # so the difference is the flow and nothing else.
    a.open_market(); a.run_session(9, 30, 3, 390); a.close_market()
    b.open_market()
    b.run_session(9, 30, 3, 390, order_flow={b.tickers[0]: (500_000.0, 0.0)})
    b.close_market()

    assert a.prices() != b.prices()
    # And order flow costs no draws, so the branches stayed on one schedule.
    assert a.draws_consumed == b.draws_consumed


def test_branches_do_not_share_memory():
    # The property that makes a fork an experiment rather than two similar
    # runs. Driving one must not perturb the other.
    _, point = mark()
    a, b = point.branch(2)
    before = b.prices()
    a.run_days(2, record=False)
    assert b.prices() == before


def test_a_macro_fork_isolates_the_path():
    # Sixty days of shared history, then two futures that differ only in the
    # macro path. This is the shape the module exists for.
    _, point = mark(days=3, macro=pretium.Macro(federal_funds_rate=0.025,
                                                corporate_bond_yield=0.045))
    calm, hiked = point.branch(2)
    shock = Scenario.rate_shock(start=0.025, end=0.06, over=3)
    flat = Scenario().hold(federal_funds_rate=0.025, corporate_bond_yield=0.045)

    for engine, scenario in ((calm, flat), (hiked, shock)):
        for day in range(4):
            scenario.apply(engine, day)
            engine.open_market()
            engine.run_session(9, 30, 3, 390)
            engine.close_market()

    n = len(UNIVERSE)
    hiked_prices = struct.unpack("<%dd" % n, hiked.prices())
    calm_prices = struct.unpack("<%dd" % n, calm.prices())
    moves = [h / c - 1.0 for h, c in zip(hiked_prices, calm_prices)]
    assert any(m != 0.0 for m in moves), "the shock did nothing"
    assert sum(1 for m in moves if m < 0) > n // 2, "a hike should price down"


def test_a_checkpoint_round_trips_through_json():
    engine, point = mark()
    restored = pretium.Checkpoint.from_json(point.to_json())
    assert restored.seed == point.seed
    assert len(restored.universe) == len(point.universe)
    assert restored.resume().prices() == engine.prices()


def test_a_checkpoint_carries_its_macro():
    macro = pretium.Macro(vix=28.0, federal_funds_rate=0.05, cycle="contraction")
    engine, point = mark(macro=macro)
    restored = pretium.Checkpoint.from_json(point.to_json())
    assert restored.macro is not None
    assert restored.macro.vix == 28.0
    assert restored.macro.cycle == "contraction"
    # And the macro actually matters: resuming without it would give a
    # different market, so a round trip that dropped it would show here.
    assert restored.resume().prices() == engine.prices()


def test_a_newer_schema_is_refused():
    engine, point = mark()
    payload = json.loads(point.to_json())
    payload["schema"] = 99
    with pytest.raises(pretium.ValidationError, match="newer"):
        pretium.Checkpoint.from_json(json.dumps(payload))


def test_a_degenerate_branch_count_is_refused():
    _, point = mark()
    with pytest.raises(pretium.ValidationError, match="at least 1"):
        point.branch(0)


def test_the_checkpoint_is_small_and_grows_with_history():
    _, short = mark(days=2)
    _, long = mark(days=8)
    assert len(long.to_json()) > len(short.to_json())
    # A few hundred bytes a day, not a memory image.
    assert len(short.to_json()) < 20_000


def test_resuming_twice_gives_the_same_market():
    _, point = mark()
    assert point.resume().prices() == point.resume().prices()
