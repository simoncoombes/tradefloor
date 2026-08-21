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


# --------------------------------------------------------------------------
# The constant-time fork
# --------------------------------------------------------------------------


def test_branch_reaches_the_same_state_as_a_replay():
    """Two routes to one point, and they must agree.

    `branch` copies the engine's state; `Checkpoint.resume` replays its log.
    If those disagreed, one of them would be silently reconstructing a
    different market -- and prices alone would not show it, so the generator
    position is checked too.
    """
    engine, point = mark(days=4)
    fast, = pretium.branch(engine, 1, universe=UNIVERSE, seed=5)
    slow = point.resume()

    assert fast.prices() == engine.prices()
    assert fast.prices() == slow.prices()
    for field in ("garch_variance", "mispricing_s", "mispricing_momentum",
                  "maker_inventory", "volume"):
        assert fast.column(field) == slow.column(field), field
    # And the generators continue identically, which prices would not reveal.
    assert [fast.draw_normal() for _ in range(8)] == \
           [slow.draw_normal() for _ in range(8)]


def test_branches_from_a_snapshot_are_independent():
    engine, _ = mark(days=3)
    a, b = pretium.branch(engine, 2, universe=UNIVERSE, seed=5)
    before = b.prices()
    a.run_days(2, record=False)
    assert b.prices() == before
    assert a.prices() != before


def test_a_snapshot_will_not_restore_onto_a_resized_roster():
    # The columns are positional, so a roster of a different size or order
    # would attach every value to the wrong instrument.
    engine, _ = mark(days=2)
    smaller = pretium.Universe.random(4, seed=2)
    target = pretium.Engine(seed=5, universe=smaller)
    with pytest.raises(pretium.ValidationError, match="roster"):
        target.restore_state(engine.state_snapshot())


def test_matching_tickers_do_not_mean_a_matching_universe():
    """A real limit of the guard, asserted so nobody assumes otherwise.

    Tickers are generated positionally, so `Universe.random(8, seed=2)` and
    `Universe.random(8, seed=99)` share every name and share no fundamentals.
    The engine holds no earnings or sectors, so it cannot tell them apart --
    the check is on identity and ORDER, which is all it has.

    Restoring across those two succeeds and produces a market with the right
    prices and the wrong fair values. The caller must supply the universe the
    snapshot came from. Documented on `restore_state` and pinned here, because
    a guard people believe is stronger than it is is worse than no guard.
    """
    other = pretium.Universe.random(8, seed=99)
    assert [i.ticker for i in other] == [i.ticker for i in UNIVERSE]
    assert [i.eps for i in other] != [i.eps for i in UNIVERSE]

    engine, _ = mark(days=2)
    target = pretium.Engine(seed=5, universe=other)
    target.restore_state(engine.state_snapshot())      # accepted
    assert target.prices() == engine.prices()


def test_a_snapshot_covers_every_column():
    # Generated from COLUMN_FIELDS rather than listed, so a field added to the
    # engine appears without anyone remembering. Asserted so that stays true.
    engine, _ = mark(days=1)
    snapshot = engine.state_snapshot()
    for field in ("price", "previous_close", "previous_tick_price", "open",
                  "high", "low", "volume", "avg_volume", "market_cap",
                  "mispricing_s", "mispricing_s_prev_close",
                  "mispricing_momentum", "last_daily_return",
                  "maker_inventory", "garch_variance", "beta",
                  "short_interest", "float_shares"):
        assert field in snapshot["columns"], field
    assert len(snapshot["rng"]) == 3


def test_absence_survives_a_snapshot_round_trip():
    # NaN means "unset" in both directions. A snapshot that stored NaN as a
    # number would turn "no mispricing yet" into "a mispricing of NaN", and a
    # snapshot that stored it as zero would turn it into a real value.
    universe = pretium.Universe.random(4, seed=3)
    fresh = pretium.Engine(seed=1, universe=universe)
    restored, = pretium.branch(fresh, 1, universe=universe, seed=1)
    import math
    import struct
    unset = struct.unpack("<%dd" % len(universe),
                          restored.column("mispricing_s"))
    assert all(math.isnan(v) for v in unset)


def test_a_degenerate_branch_count_is_refused():
    engine, _ = mark(days=1)
    with pytest.raises(pretium.ValidationError, match="at least 1"):
        pretium.branch(engine, 0, universe=UNIVERSE, seed=5)
