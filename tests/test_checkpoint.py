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
    # (state, increment, spare) for each of the market, economy and external
    # streams, in that order. Three numbers -- one stream -- is the pre-split
    # format, and restore_state refuses it by design.
    assert len(snapshot["rng"]) == 9


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


# --------------------------------------------------------------------------
# Universe identity
# --------------------------------------------------------------------------


def test_a_fingerprint_distinguishes_universes_that_tickers_cannot():
    """The identity check that names cannot provide.

    Tickers are generated positionally, so two universes from different seeds
    share every name. Anything that asks "same universe?" by comparing tickers
    is checking almost nothing -- which is exactly the hole found in
    `restore_state`, where a substituted roster was accepted.
    """
    a = pretium.Universe.random(40, seed=1)
    b = pretium.Universe.random(40, seed=99)
    assert [i.ticker for i in a] == [i.ticker for i in b]
    assert a.fingerprint != b.fingerprint


def test_a_fingerprint_is_content_not_formatting():
    universe = pretium.Universe.random(12, seed=4)
    assert universe.fingerprint == pretium.Universe.random(12, seed=4).fingerprint
    # Survives a JSON round trip, so a checkpoint written on one machine and
    # read on another compares equal.
    assert pretium.Universe.from_json(universe.to_json()).fingerprint == \
        universe.fingerprint


def test_a_checkpoint_refuses_a_universe_that_arrived_changed():
    engine, point = mark(days=2)
    payload = json.loads(point.to_json())
    # Same names, different fundamentals -- the substitution the roster check
    # cannot see.
    other = pretium.Universe.random(len(UNIVERSE), seed=99)
    payload["universe"] = json.loads(other.to_json())
    with pytest.raises(pretium.ValidationError, match="fingerprint"):
        pretium.Checkpoint.from_json(json.dumps(payload))


def test_resuming_onto_a_supplied_roster_is_checked():
    engine, point = mark(days=2)
    # The right one works.
    assert point.resume(universe=UNIVERSE).prices() == engine.prices()
    # The same-named wrong one does not.
    other = pretium.Universe.random(len(UNIVERSE), seed=99)
    with pytest.raises(pretium.ValidationError, match="fingerprint"):
        point.resume(universe=other)


def test_an_older_checkpoint_without_a_fingerprint_still_loads():
    # Absent means "written before this existed", not "mismatched". Refusing
    # it would break every archive made last week to guard against a hazard
    # those archives do not have.
    engine, point = mark(days=2)
    payload = json.loads(point.to_json())
    del payload["universe_fingerprint"]
    restored = pretium.Checkpoint.from_json(json.dumps(payload))
    assert restored.resume().prices() == engine.prices()


# --------------------------------------------------------------------------
# Forking MID-DAY, which is where the snapshot was incomplete
# --------------------------------------------------------------------------


def _first(engine, factor, count):
    return struct.unpack("<%dd" % count, engine.attribution(factor))[0]


def test_a_mid_day_fork_continues_the_parent_exactly():
    """`branch` promises identical, not similar. Mid-day it delivered neither.

    The state snapshot carried the per-COMPANY columns and the generator
    position, and not the per-DAY accumulators that live beside them. A fork
    taken between two sessions of the same day lost the attribution
    accumulator and the market-open flag, so it re-opened the day on its next
    session, re-anchored `previous_close`, and priced differently from the
    parent it was supposed to be a copy of.

    Every existing test forked on a day boundary, where there is no per-day
    state to lose.
    """
    universe = pretium.Universe.random(6, seed=5)
    count = len(universe)
    parent = pretium.Engine(seed=1, universe=universe)
    parent.open_market()
    parent.run_session(9, 30, 3, 60)

    forks = pretium.branch(parent, 2, universe=universe, seed=1)
    assert _first(forks[0], "random_noise", count) == _first(
        parent, "random_noise", count), "the fork lost the day's attribution"

    parent.run_session(10, 30, 3, 60)
    for fork in forks:
        fork.run_session(10, 30, 3, 60)

    assert forks[0].prices() == parent.prices(), "the fork diverged in price"
    assert forks[1].prices() == parent.prices()
    assert _first(forks[0], "random_noise", count) == _first(
        parent, "random_noise", count)


def test_a_mid_day_fork_closes_the_day_the_same_way():
    """The deepest consequence, and the one a price check alone would miss.

    `close_market` feeds the day's accumulated random_noise to GARCH as the
    innovation. A fork that lost the accumulator closes on a different
    variance -- which does not show up in today's prices at all, only in
    tomorrow's.
    """
    universe = pretium.Universe.random(6, seed=5)
    count = len(universe)
    parent = pretium.Engine(seed=1, universe=universe)
    parent.open_market()
    parent.run_session(9, 30, 3, 60)
    fork = pretium.branch(parent, 1, universe=universe, seed=1)[0]

    parent.run_session(10, 30, 3, 60)
    fork.run_session(10, 30, 3, 60)
    parent.close_market()
    fork.close_market()

    assert fork.column("garch_variance") == parent.column("garch_variance")
    # And the variance actually moved, or this compared two untouched arrays.
    fresh = pretium.Engine(seed=1, universe=universe)
    assert fork.column("garch_variance") != fresh.column("garch_variance")


def test_a_snapshot_without_the_day_state_still_restores():
    # Written before the per-day accumulators were carried. Such a snapshot
    # described a day that had not started, so that is what it restores to --
    # refusing it would break every archived state for no gain.
    universe = pretium.Universe.random(6, seed=5)
    engine = pretium.Engine(seed=1, universe=universe)
    engine.open_market()
    engine.run_session(9, 30, 3, 60)
    snapshot = engine.state_snapshot()
    for key in ("attribution", "tick_components", "tick_fundamental",
                "tick_anchor", "market_open"):
        snapshot.pop(key)

    restored = pretium.Engine(seed=1, universe=universe)
    restored.restore_state(snapshot)
    assert restored.prices() == engine.prices()


def test_the_snapshot_carries_the_open_flag():
    universe = pretium.Universe.random(6, seed=5)
    engine = pretium.Engine(seed=1, universe=universe)
    assert engine.state_snapshot()["market_open"] is False
    engine.open_market()
    assert engine.state_snapshot()["market_open"] is True
    engine.close_market()
    assert engine.state_snapshot()["market_open"] is False


def test_a_mid_day_checkpoint_is_exact_because_it_replays():
    """The design argument in this module's docstring, checked.

    `branch` snapshots state and had to learn about the per-day accumulators
    the hard way. `Checkpoint` replays the order log, so there is no field to
    forget: every input is re-applied and the accumulators rebuild themselves.

    That is the reason the serialisable form is the log rather than the
    snapshot, and it is asserted here rather than argued.
    """
    universe = pretium.Universe.random(6, seed=5)
    count = len(universe)
    engine = pretium.Engine(seed=1, universe=universe)
    engine.open_market()
    engine.run_session(9, 30, 3, 60)

    resumed = pretium.Checkpoint.of(engine, universe=universe,
                                    seed=1).resume()
    assert resumed.prices() == engine.prices()
    assert _first(resumed, "random_noise", count) == _first(
        engine, "random_noise", count), "the replay lost the day's attribution"

    engine.run_session(10, 30, 3, 60)
    resumed.run_session(10, 30, 3, 60)
    engine.close_market()
    resumed.close_market()
    assert resumed.column("garch_variance") == engine.column("garch_variance")
