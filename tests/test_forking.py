"""The forking guarantees, stated as invariants and checked end to end.

`test_checkpoint.py` covers the checkpoint and branch surfaces one behaviour at
a time. This module asks the question a user actually has, which is longer than
any single behaviour:

    run a market, freeze it, fork the freeze, change ONE thing in the fork,
    resume both, and know that everything before the change was identical and
    everything after it is attributable to the change.

Every test here is an invariant on that chain. None of them assert on private
structure: they run the documented API and compare the state a user can read,
because the guarantee being protected is a user-visible one. A test that
pinned the shape of a snapshot dict would pass through the exact defects this
module was written after.

## The canonical scenario

One small market, defined once below and shared by everything here. Twelve
instruments, twenty-six ticks a day, a hundred days, with a fixed two-sided
order flow so the book, the fills and the price impact are exercised rather
than a pure price process. It runs in about a fifth of a second, the budget
lets these tests be ordinary CI tests rather than a nightly job.

## What was wrong when this was written

Four defects, all in the in-memory fork, all found by the tests below:

1. A mid-day fork ran the rest of the day with the day's endogenous news
   missing, and so priced DIFFERENTLY from the parent it was a copy of. Live
   on the shipped default preset.
2. A fork's order log was empty, so a `Checkpoint` taken on a fork replayed a
   market that began at day zero. Silently.
3. A mid-day fork lost the day's already-recorded ticks, so `record` wrote a
   day half as long as the parent's, well-formed and short.
4. A fork lost the previous close's pending jump, so the first row of its next
   recorded day attributed nothing to it.

All four had one cause: a fork was rebuilt by copying a hand-maintained list of
fields into a fresh engine, and the list was incomplete. It had been incomplete
five times before. The fix is that a fork is now a copy of the engine, so there
is no list to be incomplete.
"""

import json
import struct
import subprocess
import sys
import textwrap

import pytest

import tradefloor as tf
from tradefloor.manifest import market_digest
from tradefloor.scenario import Scenario

# --------------------------------------------------------------------------
# The canonical scenario
# --------------------------------------------------------------------------

#: Small enough to be fast, large enough that a per-company event with a 5%
#: intensity fires most days. The mid-day news divergence above was invisible
#: on the six-instrument universe the older tests use, because with six names
#: the day is usually newsless and a fork that loses the news loses nothing.
UNIVERSE = tf.Universe.random(12, seed=20260829)
SEED = 4242
TICKS_PER_DAY = 26
CHECKPOINT_DAY = 50
TOTAL_DAYS = 100

#: A fixed two-sided flow, so the order book, the fills and the impact term are
#: part of what these tests compare. Without it the market is a price process
#: and half of what a fork has to carry is never touched.
FLOW = {
    UNIVERSE[0].ticker: (250_000.0, 0.0),
    UNIVERSE[1].ticker: (0.0, 180_000.0),
}

#: Everything a fork has to reproduce that is not a price. `prices()` alone
#: passes on markets that differ in the state tomorrow's prices depend on,
#: which is how the mid-day snapshot defect survived: the fork closed on a
#: different GARCH variance and today's prints were identical.
STATE_COLUMNS = (
    "price", "previous_close", "previous_tick_price", "open", "high", "low",
    "volume", "avg_volume", "market_cap", "mispricing_s",
    "mispricing_s_prev_close", "mispricing_momentum", "last_daily_return",
    "maker_inventory", "garch_variance", "beta", "short_interest",
    "float_shares",
)


def day(engine, index, *, flow=FLOW, record=True):
    """One step of the canonical scenario."""
    engine.open_market()
    engine.run_session(9, 30, 3, TICKS_PER_DAY, order_flow=flow)
    if record:
        engine.record(index)
    engine.close_market()


def run(engine, days, *, first=0, flow=FLOW, record=True):
    for i in range(days):
        day(engine, first + i, flow=flow, record=record)
    return engine


def fresh(*, seed=SEED, macro=None, model=None):
    return tf.Engine(seed=seed, universe=UNIVERSE, macro_state=macro,
                     model=model)


def state(engine):
    """Everything comparable about a market, as one dict.

    Not a digest: a digest says two markets differ and this says WHERE, which
    is the difference between a failing test that sends you looking and one
    that tells you.
    """
    out = {f"column:{name}": engine.column(name) for name in STATE_COLUMNS}
    out.update({f"attribution:{f}": engine.attribution(f)
                for f in tf.Engine.FACTORS})
    out["prices"] = engine.prices()
    out["draws"] = engine.draws_consumed
    out["draws_by_stream"] = engine.draws_by_stream()
    out["digest"] = market_digest(engine)
    out["log_entries"] = len(engine.order_log)
    out["recorded_days"] = engine.recorded_days
    return out


def differences(left, right):
    """The keys on which two states disagree, for a failure message that
    names the field rather than saying two byte strings are unequal."""
    return sorted(k for k in left if left[k] != right[k])


def unpack(buf):
    return struct.unpack("<%dd" % len(UNIVERSE), buf)


def checkpoint_at(step, *, macro=None, model=None):
    """Run the canonical scenario to `step` and freeze it."""
    engine = fresh(macro=macro, model=model)
    run(engine, step)
    return engine, tf.Checkpoint.of(engine, universe=UNIVERSE, seed=SEED,
                                    macro=macro, label=f"day {step}")


# --------------------------------------------------------------------------
# 1. The same experiment twice
# --------------------------------------------------------------------------


def test_same_seed_reproduces():
    """The floor everything else stands on.

    Exact, not close. The library promises bit equality for a repeated run and
    a tolerance here would quietly accept the drift that every test below is
    trying to detect.
    """
    first = run(fresh(), TOTAL_DAYS)
    second = run(fresh(), TOTAL_DAYS)

    assert differences(state(first), state(second)) == []
    # And the histories match, not only the destinations. Two runs that
    # reached one state by different routes would be a different bug wearing
    # this test's pass.
    assert first.order_log == second.order_log


def test_same_seed_reproduces_the_whole_path_not_only_the_end():
    """Compared at every step, because a divergence that cancels is still a
    divergence and an end-state check would miss it."""
    a, b = fresh(), fresh()
    for i in range(TOTAL_DAYS):
        day(a, i)
        day(b, i)
        assert market_digest(a) == market_digest(b), f"diverged at step {i}"


def test_a_scored_run_reproduces_through_the_harness():
    """The same guarantee one level up, where a user actually reads results.

    Orders, fills, positions, cash and P&L are the harness's surface, not the
    engine's, so determinism has to be shown there too.
    """
    spec = tf.StrategySpec.momentum(lookback_days=1.0, top_k=3)
    first = tf.evaluate({"m": spec}, seed=SEED, universe=UNIVERSE, days=6)["m"]
    second = tf.evaluate({"m": spec}, seed=SEED, universe=UNIVERSE, days=6)["m"]

    for field in ("pnl", "return_pct", "trades", "turnover", "impact_bps",
                  "max_leverage", "rejected", "final_net_worth",
                  "strategy_fingerprint", "universe_fingerprint",
                  "model_fingerprint"):
        assert getattr(first, field) == getattr(second, field), field
    # A scorecard of a strategy that never traded would pass the loop above
    # by vacuum.
    assert first.trades > 0


# --------------------------------------------------------------------------
# 2. Restoring a checkpoint continues the run it froze
# --------------------------------------------------------------------------


def test_checkpoint_restore_matches_uninterrupted_run():
    """The most important test here.

    Run 0 to 100 in one go and record the state at every step from 50. Then
    freeze at 50, restore, and run 50 to 100 separately. The two continuations
    must agree at EVERY step, not only at the end: a restore that lands on the
    right market and then walks a different path is the failure this shape
    exists to catch, and comparing final states alone would let it through.
    """
    uninterrupted = fresh()
    run(uninterrupted, CHECKPOINT_DAY)
    frozen = tf.Checkpoint.of(uninterrupted, universe=UNIVERSE, seed=SEED)

    expected = []
    for i in range(CHECKPOINT_DAY, TOTAL_DAYS):
        day(uninterrupted, i)
        expected.append(state(uninterrupted))

    restored = frozen.resume()
    assert differences(state(restored), state(frozen.resume())) == []

    for offset, i in enumerate(range(CHECKPOINT_DAY, TOTAL_DAYS)):
        day(restored, i)
        gap = differences(state(restored), expected[offset])
        assert gap == [], f"step {i} diverged in {gap}"


def test_a_checkpoint_lands_on_the_state_it_froze():
    """Before continuing anywhere, the restored market must BE the frozen one.

    Checked across every column and both generators rather than on prices,
    because a market can print identical prices from a different variance and
    only tomorrow will say so.
    """
    engine, point = checkpoint_at(CHECKPOINT_DAY)
    restored = point.resume()
    assert differences(state(restored), state(engine)) == []
    assert [restored.draw_normal() for _ in range(8)] == \
           [engine.draw_normal() for _ in range(8)]


def test_a_checkpoint_carries_everything_a_continuation_needs():
    """The checkpoint's completeness, asked as a behaviour rather than as an
    inventory of its fields.

    A field list would be a test of the implementation and would pass while
    something outside the list went missing, which is the shape of every fork
    defect this project has had. Continuing for fifty steps and comparing the
    whole state at each one asks the question the list is a proxy for.
    """
    engine, point = checkpoint_at(CHECKPOINT_DAY)
    restored = point.resume()
    for i in range(CHECKPOINT_DAY, TOTAL_DAYS):
        day(engine, i)
        day(restored, i)
        gap = differences(state(restored), state(engine))
        assert gap == [], f"step {i} diverged in {gap}"


def test_there_are_no_outstanding_orders_for_a_checkpoint_to_lose():
    """Why a checkpoint carries no order book, asked rather than assumed.

    "What about resting orders?" is the obvious question about a checkpoint,
    and the answer here is that there are none: `engine.book()` is rebuilt per
    call from current state and is detached, so an order posted to it prices a
    fill and never joins the market. Pressure reaches the market only through
    `order_flow`, which IS in the log.

    Pinned because the day that stops being true, a checkpoint starts omitting
    state it has never had to carry, and the failure would be a market that
    restores with an empty book and looks entirely plausible.
    """
    engine = fresh()
    run(engine, 2)
    ticker = UNIVERSE[0].ticker
    before = engine.book(ticker).depth("buy")

    detached = engine.book(ticker)
    order = detached.post_limit("buy", (detached.best_bid or 1.0) * 0.5,
                                5_000.0, owner="me")
    assert engine.book(ticker).depth("buy") == before, (
        "posting to the book moved the engine's own depth; the book is no "
        "longer detached and a checkpoint now has resting orders to carry"
    )
    assert engine.book(ticker).cancel_order(order) is False

    # And the engine's state is untouched by any of it, so the checkpoint
    # taken after is the same one that would have been taken before.
    assert differences(
        state(tf.Checkpoint.of(engine, universe=UNIVERSE, seed=SEED).resume()),
        state(engine)) == []


def test_a_checkpoint_of_a_custom_model_resumes_under_that_model():
    """A run under non-default coefficients must not resume under the default.

    It would produce a plausible market that is not the one it froze, and
    nothing about the result would look wrong.
    """
    model = tf.ModelParams.from_preset("pt-v14", endogenous_news_intensity=0.2)
    engine, point = checkpoint_at(10, model=model)
    assert point.model is not None
    restored = point.resume()
    assert restored.model_fingerprint == engine.model_fingerprint
    assert differences(state(restored), state(engine)) == []


# --------------------------------------------------------------------------
# 3. A fork starts where its source stood
# --------------------------------------------------------------------------


def test_fork_initial_state_matches_source():
    engine, _ = checkpoint_at(CHECKPOINT_DAY)
    a, b, c = tf.branch(engine, 3)
    for fork in (a, b, c):
        assert differences(state(fork), state(engine)) == []


def test_fork_initial_state_matches_source_mid_day():
    """The same claim at the point where it used to be false.

    A fork taken between two sessions of one day carries per-day state that a
    fork taken on a day boundary does not have to: the attribution
    accumulators, the market-open flag, the day's recorded ticks and the day's
    endogenous news. Every one of those has been missing at some point.
    """
    engine = fresh()
    run(engine, 3)
    engine.open_market()
    engine.run_session(9, 30, 3, TICKS_PER_DAY, order_flow=FLOW)

    fork, = tf.branch(engine, 1)
    assert differences(state(fork), state(engine)) == []


def test_fork_does_not_mutate_source():
    """Driving a fork must leave the source untouched.

    The property that makes a fork an experiment rather than two runs that
    started similarly. Checked on the whole state, and after work heavy enough
    that a shared buffer anywhere would show.
    """
    engine, _ = checkpoint_at(CHECKPOINT_DAY)
    before = state(engine)
    before_log = list(engine.order_log)

    forks = tf.branch(engine, 3)
    for i, fork in enumerate(forks):
        Scenario.rate_shock(start=0.025, end=0.05 + 0.01 * i, over=5).apply(fork, 0)
        run(fork, 20, first=CHECKPOINT_DAY,
            flow={UNIVERSE[i].ticker: (400_000.0 * (i + 1), 0.0)})

    assert differences(state(engine), before) == []
    assert engine.order_log == before_log


def test_a_fork_shares_no_memory_with_another_fork():
    """Accidental sharing is what a shallow copy looks like from outside.

    Driving A must not move B, and B must still be able to reach the state A
    reached, which a shared buffer would make impossible rather than merely
    different.
    """
    engine, _ = checkpoint_at(20)
    a, b = tf.branch(engine, 2)
    before_b = state(b)

    run(a, 15, first=20, flow={UNIVERSE[2].ticker: (900_000.0, 0.0)})
    assert differences(state(b), before_b) == []

    run(b, 15, first=20, flow={UNIVERSE[2].ticker: (900_000.0, 0.0)})
    assert differences(state(b), state(a)) == []


# --------------------------------------------------------------------------
# 4. Control and intervention
# --------------------------------------------------------------------------

#: One variable, moved once. A hiking cycle is the intervention with the
#: clearest sign: valuations discount off the corporate yield, so a rise
#: should price equities down and a result with the wrong sign is a broken
#: experiment rather than an interesting finding.
CONTROL = Scenario().hold(federal_funds_rate=0.025, corporate_bond_yield=0.045)
INTERVENTION = Scenario.rate_shock(start=0.025, end=0.06, over=10)
BASE_MACRO_KWARGS = dict(federal_funds_rate=0.025, corporate_bond_yield=0.045)


def _branch_pair(step=CHECKPOINT_DAY):
    macro = tf.Macro(**BASE_MACRO_KWARGS)
    engine, point = checkpoint_at(step, macro=macro)
    control, treated = tf.branch(engine, 2)
    return engine, point, control, treated


def _drive(engine, scenario, days, first):
    for i in range(days):
        scenario.apply(engine, i)
        day(engine, first + i)


def test_control_and_fork_match_before_intervention():
    """Same checkpoint, same agent, same history, same seeds.

    Both branches run the CONTROL path for ten steps, so nothing has been
    changed yet. They must be identical, which makes the divergence
    after the intervention attributable to the intervention.
    """
    _, _, control, treated = _branch_pair()
    _drive(control, CONTROL, 10, CHECKPOINT_DAY)
    _drive(treated, CONTROL, 10, CHECKPOINT_DAY)
    assert differences(state(control), state(treated)) == []


def test_intervention_causes_expected_divergence():
    """One changed variable, and it has to actually do something.

    Two failure modes matter here and they look alike from a distance: a fork
    that does not isolate, and an intervention that is silently inert. The
    first is caught by the test above; this catches the second, and checks the
    SIGN, because a rate rise that priced equities up would mean the scenario
    stopped reaching fair value.
    """
    _, _, control, treated = _branch_pair()
    _drive(control, CONTROL, 20, CHECKPOINT_DAY)
    _drive(treated, INTERVENTION, 20, CHECKPOINT_DAY)

    assert market_digest(control) != market_digest(treated)
    moves = [t / c - 1.0 for t, c in zip(unpack(treated.prices()),
                                         unpack(control.prices()))]
    assert any(m != 0.0 for m in moves), "the intervention did nothing"
    down = sum(1 for m in moves if m < 0)
    assert down > len(moves) // 2, f"a hike priced the market up: {moves}"


def test_divergence_begins_only_after_the_intervention():
    """The causal claim, localised to a step.

    Both branches are driven step by step and the first step at which their
    market digests differ is recorded. It must be a step at or after the one
    the intervention was applied on, and it must exist: a run that never
    diverged would pass a naive "identical before" assertion by doing nothing.
    """
    _, _, control, treated = _branch_pair()
    applied_at = 4

    first_divergence = None
    for i in range(20):
        CONTROL.apply(control, i)
        (INTERVENTION if i >= applied_at else CONTROL).apply(treated, i)
        day(control, CHECKPOINT_DAY + i)
        day(treated, CHECKPOINT_DAY + i)
        if first_divergence is None and market_digest(control) != market_digest(treated):
            first_divergence = i

    assert first_divergence is not None, "the intervention never diverged"
    assert first_divergence >= applied_at, (
        f"the branches diverged at step {first_divergence}, before the "
        f"intervention at step {applied_at}. Either the fork is not isolating "
        "or the scenario reached the control branch."
    )


def test_the_intervention_is_the_only_difference():
    """Re-running the treated branch from the same checkpoint reproduces it.

    Independently reproducible after the intervention, not merely different
    from the control. A fork whose divergence were noise rather than the
    intervention would fail here and pass everything above.
    """
    _, point, _, treated = _branch_pair()
    _drive(treated, INTERVENTION, 20, CHECKPOINT_DAY)

    again = point.resume()
    _drive(again, INTERVENTION, 20, CHECKPOINT_DAY)
    assert differences(state(again), state(treated)) == []


# --------------------------------------------------------------------------
# 5. Many forks
# --------------------------------------------------------------------------


def _three_interventions():
    return {
        "hike": Scenario.rate_shock(start=0.025, end=0.06, over=10),
        "cut": Scenario.rate_shock(start=0.025, end=0.005, over=10),
        "vol": Scenario.vix_shock(calm=15.0, peak=45.0, at=2, over=10),
    }


def test_multiple_forks_are_independent():
    """Three forks from one checkpoint, three different changes.

    Each must reproduce what it would have produced alone, which is a stronger
    claim than "they differ from each other": two branches that perturbed one
    another would still differ.
    """
    engine, point, *_ = _branch_pair()
    source_before = state(engine)
    paths = _three_interventions()

    forks = dict(zip(paths, tf.branch(engine, 3)))
    for name, fork in forks.items():
        _drive(fork, paths[name], 12, CHECKPOINT_DAY)

    # Nothing reached the source.
    assert differences(state(engine), source_before) == []

    # And each branch is what it would have been on its own.
    for name, path in paths.items():
        alone = point.resume()
        _drive(alone, path, 12, CHECKPOINT_DAY)
        gap = differences(state(alone), state(forks[name]))
        assert gap == [], f"fork {name!r} was perturbed: {gap}"

    # The three really are three different futures, or the loop above compared
    # a market with itself three times.
    digests = {market_digest(f) for f in forks.values()}
    assert len(digests) == 3


def test_branch_order_does_not_change_the_outcome():
    """Which fork is driven first must not matter.

    It would if the branches shared anything mutable, and the failure would be
    order-dependent, which is the kind that survives a test suite by being
    absent on the day it runs.
    """
    paths = _three_interventions()
    order_a = ["hike", "cut", "vol"]
    order_b = ["vol", "hike", "cut"]

    def outcomes(order):
        engine, _, *_ = _branch_pair(step=20)
        forks = dict(zip(order, tf.branch(engine, 3)))
        for name in order:
            _drive(forks[name], paths[name], 10, 20)
        return {name: market_digest(forks[name]) for name in order}

    assert outcomes(order_a) == outcomes(order_b)


# --------------------------------------------------------------------------
# 6. Forking a fork
# --------------------------------------------------------------------------


def test_nested_fork():
    """A fork of a fork inherits deterministically and stays independent.

    Supported, and worth pinning as supported: it was not, until a fork
    started carrying its parent's order log. A `Checkpoint` taken on a fork
    used to replay a market that began at day zero, so the second generation
    of any experiment was silently wrong.
    """
    engine, _ = checkpoint_at(20)
    child, = tf.branch(engine, 1)
    run(child, 10, first=20)

    grandchild_a, grandchild_b = tf.branch(child, 2)
    assert differences(state(grandchild_a), state(child)) == []
    assert differences(state(grandchild_b), state(child)) == []

    child_before = state(child)
    run(grandchild_a, 10, first=30,
        flow={UNIVERSE[3].ticker: (700_000.0, 0.0)})

    # Neither the parent nor the sibling moved.
    assert differences(state(child), child_before) == []
    assert differences(state(grandchild_b), child_before) == []

    # And the grandchild is reproducible from the generation above it.
    again, = tf.branch(child, 1)
    run(again, 10, first=30, flow={UNIVERSE[3].ticker: (700_000.0, 0.0)})
    assert differences(state(again), state(grandchild_a)) == []


def test_a_fork_can_be_checkpointed_and_the_checkpoint_is_the_fork():
    """The composition that used to fail silently.

    A fork rebuilt from a state snapshot had an EMPTY order log, so a
    checkpoint taken on it recorded a history that began at the fork rather
    than at day zero, and resuming it produced a market with the right shape
    and the wrong past. Nothing raised.
    """
    engine, _ = checkpoint_at(20)
    fork, = tf.branch(engine, 1)
    run(fork, 10, first=20, flow={UNIVERSE[4].ticker: (300_000.0, 0.0)})

    point = tf.Checkpoint.of(fork, universe=UNIVERSE, seed=SEED)
    assert len(point.log) == len(fork.order_log)
    assert differences(state(point.resume()), state(fork)) == []

    # Specifically NOT a market that started at the fork.
    truncated = tf.replay(fork.order_log[len(engine.order_log):],
                          seed=SEED, universe=UNIVERSE)
    assert market_digest(truncated) != market_digest(fork)


# --------------------------------------------------------------------------
# 7. Persistence
# --------------------------------------------------------------------------


def test_checkpoint_serialization_roundtrip():
    engine, point = checkpoint_at(30)
    restored = tf.Checkpoint.from_json(point.to_json())

    assert restored.seed == point.seed
    assert restored.label == point.label
    assert restored.universe_fingerprint == point.universe_fingerprint
    assert restored.log == point.log
    assert differences(state(restored.resume()), state(engine)) == []


def test_a_checkpoint_survives_the_process(tmp_path):
    """Written by one interpreter, continued by another.

    Substantially stronger evidence than restoring an in-memory object: a
    second process shares no allocator, no module state and no warmed caches
    with the first, so a continuation that agrees here agrees for reasons
    that will still hold on someone else's machine next year.
    """
    engine, point = checkpoint_at(30)
    expected = fresh()
    run(expected, 30)
    run(expected, 15, first=30)

    path = tmp_path / "day30.json"
    path.write_text(point.to_json(), encoding="utf-8")

    script = textwrap.dedent(f"""
        import json, sys
        import tradefloor as tf
        from tradefloor.manifest import market_digest

        point = tf.Checkpoint.from_json(
            open({str(path)!r}, encoding="utf-8").read())
        engine = point.resume()
        for i in range(30, 45):
            engine.open_market()
            engine.run_session(9, 30, 3, {TICKS_PER_DAY},
                               order_flow={FLOW!r})
            engine.record(i)
            engine.close_market()
        print(json.dumps({{"digest": market_digest(engine),
                          "draws": engine.draws_consumed,
                          "prices": list(engine.prices())}}))
    """)
    completed = subprocess.run([sys.executable, "-c", script],
                               capture_output=True, text=True, timeout=300)
    assert completed.returncode == 0, completed.stderr
    got = json.loads(completed.stdout.strip().splitlines()[-1])

    assert got["digest"] == market_digest(expected)
    assert got["draws"] == expected.draws_consumed
    assert bytes(got["prices"]) == expected.prices()


def test_a_checkpoint_written_here_is_read_the_same_way_twice():
    """Serialisation is canonical, so two writes of one checkpoint are one
    string. A checkpoint whose bytes depended on dict ordering would give two
    archives of one experiment different names."""
    _, point = checkpoint_at(12)
    assert point.to_json() == point.to_json()
    assert tf.Checkpoint.from_json(point.to_json()).to_json() == point.to_json()


# --------------------------------------------------------------------------
# 8. Manifests and lineage
# --------------------------------------------------------------------------


def test_a_manifest_of_a_fork_reproduces_the_fork():
    """A branch of an experiment is publishable on its own terms.

    This is the composition that used to fail loudly and misleadingly: a
    manifest written on a fork carried the fork's empty log, `reproduce()`
    rebuilt a market that began at day zero, and the mismatch was reported as
    a suspected platform arithmetic difference between Windows and Windows.
    """
    engine, _ = checkpoint_at(20)
    fork, = tf.branch(engine, 1)
    _drive(fork, INTERVENTION, 10, 20)

    manifest = tf.RunManifest.of(fork, seed=SEED, universe=UNIVERSE,
                                 scenario=INTERVENTION, label="rate_shock")
    rebuilt = manifest.reproduce()
    assert market_digest(rebuilt) == market_digest(fork)


def test_manifest_records_fork_lineage():
    """Lineage is both derivable and, now, declared.

    A fork carries its parent's order log, so the manifests of two branches of
    one experiment share a prefix and the length of that prefix is where they
    parted. That reconstructs

        run -> checkpoint -> {control, treated}

    from the artefacts alone, by comparison, asserted here so it stays
    true.

    What used to be missing was a manifest saying so on its own: a reader
    holding ONE of the two could not tell it was a branch of anything.
    `derived_from` is that sentence, and the second half of this test is it.
    """
    engine, _ = checkpoint_at(20)
    control, treated = tf.branch(engine, 2)
    _drive(control, CONTROL, 10, 20)
    _drive(treated, INTERVENTION, 10, 20)

    left = tf.RunManifest.of(control, seed=SEED, universe=UNIVERSE,
                             scenario=CONTROL, label="control")
    right = tf.RunManifest.of(treated, seed=SEED, universe=UNIVERSE,
                              scenario=INTERVENTION, label="treated")

    parent_log = engine.order_log
    for name, manifest in (("control", left), ("treated", right)):
        assert manifest.order_log[:len(parent_log)] == parent_log, (
            f"the {name} branch does not carry its parent's history as a "
            "prefix, so a reader cannot recover where it was forked"
        )

    # The branches stay identical past the fork for as long as the two paths
    # pin the same values, which is a fact about the scenarios rather than
    # about lineage. What lineage needs is that neither branch diverges BEFORE
    # the fork.
    shared = 0
    for a, b in zip(left.order_log, right.order_log):
        if a != b:
            break
        shared += 1
    assert shared >= len(parent_log)

    # Both replay to their own result from that shared beginning.
    assert market_digest(left.reproduce()) == market_digest(control)
    assert market_digest(right.reproduce()) == market_digest(treated)

    # And declared, when the branch was recorded with the checkpoint it came
    # from. Both arms name the same parent, at the same entry.
    point = tf.Checkpoint.of(engine, universe=UNIVERSE, seed=SEED,
                             label=f"day {20}")
    declared_left = tf.RunManifest.of(control, seed=SEED, universe=UNIVERSE,
                                      scenario=CONTROL, label="control",
                                      derived_from=point)
    declared_right = tf.RunManifest.of(treated, seed=SEED, universe=UNIVERSE,
                                       scenario=INTERVENTION, label="treated",
                                       derived_from=point)

    for manifest in (declared_left, declared_right):
        recorded = manifest.derived_from
        assert recorded is not None
        assert recorded["checkpoint"] == point.fingerprint
        assert recorded["entries"] == len(parent_log)
        assert recorded["label"] == "day 20"
        # The claim survives the journey and can be checked by whoever
        # receives it, given the checkpoint.
        tf.RunManifest.from_json(manifest.to_json()).verify_lineage(point)

    assert declared_left.derived_from["checkpoint"] == \
        declared_right.derived_from["checkpoint"]


def test_a_declared_parent_is_checked_when_it_is_claimed():
    """`derived_from` is a claim about history, so it is tested at the point
    it is made rather than believed and carried."""
    engine, _ = checkpoint_at(20)
    fork, = tf.branch(engine, 1)
    run(fork, 5, first=20)
    later = tf.Checkpoint.of(fork, universe=UNIVERSE, seed=SEED)

    # A checkpoint taken AFTER the run cannot be where it started.
    with pytest.raises(tf.ValidationError, match="cannot have started"):
        tf.RunManifest.of(engine, seed=SEED, universe=UNIVERSE,
                          derived_from=later)

    # A checkpoint of a DIFFERENT MARKET is refused on identity, and it has
    # to be: the log records inputs, so a run of the same sessions on another
    # seed carries a log that compares equal entry for entry. Nothing about
    # the history distinguishes them.
    other = tf.Engine(seed=SEED + 1, universe=UNIVERSE)
    run(other, 20)
    elsewhere = tf.Checkpoint.of(other, universe=UNIVERSE, seed=SEED + 1)
    assert elsewhere.log == tf.Checkpoint.of(engine, universe=UNIVERSE,
                                             seed=SEED).log
    with pytest.raises(tf.ValidationError, match="did not branch from it"):
        tf.RunManifest.of(fork, seed=SEED, universe=UNIVERSE,
                          derived_from=elsewhere)

    # And a checkpoint of the same seed on another roster, which shares every
    # ticker with this one and no fundamentals.
    swapped = tf.Universe.random(len(UNIVERSE), seed=99)
    assert [i.ticker for i in swapped] == [i.ticker for i in UNIVERSE]
    stranger = tf.Engine(seed=SEED, universe=swapped)
    run(stranger, 20)
    with pytest.raises(tf.ValidationError, match="different roster"):
        tf.RunManifest.of(fork, seed=SEED, universe=UNIVERSE,
                          derived_from=tf.Checkpoint.of(
                              stranger, universe=swapped, seed=SEED))


def test_verifying_lineage_needs_the_checkpoint_it_names():
    """The declaration names a digest; testing it needs the artefact."""
    engine, point = checkpoint_at(20)
    fork, = tf.branch(engine, 1)
    run(fork, 5, first=20)
    manifest = tf.RunManifest.of(fork, seed=SEED, universe=UNIVERSE,
                                 derived_from=point)

    manifest.verify_lineage(point)

    # The same market frozen under a different label is a different starting
    # state to cite, and the digest says so.
    relabelled = tf.Checkpoint.of(engine, universe=UNIVERSE, seed=SEED,
                                  label="something else")
    with pytest.raises(tf.ValidationError, match="branched from checkpoint"):
        manifest.verify_lineage(relabelled)

    # A manifest that never claimed a parent says that, rather than passing.
    plain = tf.RunManifest.of(fork, seed=SEED, universe=UNIVERSE)
    assert plain.derived_from is None
    with pytest.raises(tf.ValidationError, match="declares no parent"):
        plain.verify_lineage(point)


def test_a_checkpoint_fingerprint_is_content_not_formatting():
    """Two people holding the same checkpoint compute the same identity, and
    a checkpoint that travelled and arrived changed computes a different one."""
    _, point = checkpoint_at(10)
    assert point.fingerprint == point.fingerprint
    assert tf.Checkpoint.from_json(point.to_json()).fingerprint == \
        point.fingerprint

    _, longer = checkpoint_at(11)
    assert longer.fingerprint != point.fingerprint


# --------------------------------------------------------------------------
# 9. Regressions: the four defects this module was written after
# --------------------------------------------------------------------------


def test_a_mid_day_fork_keeps_the_days_endogenous_news():
    """The defect that made a fork price differently from its parent.

    The day's endogenous news is generated once in `open_market` and read by
    every tick of that day, so it is per-DAY state. A fork rebuilt from a
    state snapshot did not carry it and ran the rest of the day with the news
    missing. Live on the shipped default preset, and invisible on a small
    universe because with six names most days are newsless.

    The intensity is raised here so the day is never newsless: the point is to
    exercise the path, not to rediscover it by luck.
    """
    model = tf.ModelParams.from_preset("pt-v14", endogenous_news_intensity=0.9)
    parent = fresh(model=model)
    parent.open_market()
    parent.run_session(9, 30, 3, TICKS_PER_DAY, order_flow=FLOW)

    fork, = tf.branch(parent, 1)
    parent.run_session(10, 30, 3, TICKS_PER_DAY, order_flow=FLOW)
    fork.run_session(10, 30, 3, TICKS_PER_DAY, order_flow=FLOW)

    gap = differences(state(fork), state(parent))
    assert gap == [], f"the fork lost the day's news: {gap}"

    # And the news was actually there, or this compared two newsless days.
    quiet = tf.ModelParams.from_preset("pt-v14", endogenous_news_intensity=0.0)
    newsless = fresh(model=quiet)
    newsless.open_market()
    newsless.run_session(9, 30, 3, TICKS_PER_DAY, order_flow=FLOW)
    newsless.run_session(10, 30, 3, TICKS_PER_DAY, order_flow=FLOW)
    assert newsless.prices() != parent.prices()


def test_a_fork_carries_its_parents_order_log():
    engine, _ = checkpoint_at(10)
    fork, = tf.branch(engine, 1)
    assert fork.order_log == engine.order_log


def test_a_mid_day_fork_keeps_the_days_recorded_ticks():
    """A fork used to record a day half as long as its parent's.

    Well-formed, self-consistent and short, with nothing to indicate it. The
    same failure the day buffer exists to prevent, one level up.
    """
    parent = fresh()
    parent.open_market()
    parent.run_session(9, 30, 3, TICKS_PER_DAY, order_flow=FLOW)
    fork, = tf.branch(parent, 1)

    for engine in (parent, fork):
        engine.run_session(10, 30, 3, TICKS_PER_DAY, order_flow=FLOW)
        engine.record(0)
        engine.close_market()

    pyarrow = pytest.importorskip("pyarrow")
    parent_rows = pyarrow.table(parent.truth(day=0)).num_rows
    fork_rows = pyarrow.table(fork.truth(day=0)).num_rows
    assert fork_rows == parent_rows == 2 * TICKS_PER_DAY * len(UNIVERSE)


def test_a_fork_keeps_the_previous_closes_pending_jump():
    """The jump a close queues is observed on the first row of the NEXT day.

    A fork taken on the day boundary between them used to lose it, so its tape
    attributed nothing to a move that happened. Prices were identical, which
    is why only the tape shows it.
    """
    pyarrow = pytest.importorskip("pyarrow")
    compute = pytest.importorskip("pyarrow.compute")

    # Seeds differ in whether a jump fires at all, so search for one that does
    # rather than assume. A test that silently found no jump would pass by
    # comparing two zero columns.
    for seed in range(1, 40):
        parent = tf.Engine(seed=seed, universe=UNIVERSE)
        run(parent, 4)
        fork, = tf.branch(parent, 1)
        day(parent, 4)
        day(fork, 4)

        def jump_column(engine):
            table = pyarrow.table(engine.truth(day=4))
            table = table.filter(compute.equal(table.column("day"), 4))
            return table.column("jump").to_pylist()

        parent_jumps = jump_column(parent)
        if any(v != 0.0 for v in parent_jumps):
            assert jump_column(fork) == parent_jumps
            return
    pytest.fail("no seed in range queued a jump; the test proved nothing")


def test_a_snapshot_carries_the_dormant_engine_dials():
    """Two engine-level states are inert under every shipped preset and were
    left out of the snapshot for that reason.

    That is the position the common log-volume state was in before
    pt-v10 turned it on, at which point a restored engine started trading
    different volume and printing different prices. Turning the dial on here
    proves they are carried now rather than waiting for a preset to find out.
    """
    model = tf.ModelParams.from_preset(
        "pt-v14", volume_idio_sigma=0.4, volume_idio_persistence=0.9,
        volume_idio_variance_gain=0.5, universe_stress_weight=0.5,
        universe_stress_decay=0.9)

    parent = fresh(model=model)
    run(parent, 6)

    target = fresh(model=model)
    target.restore_state(parent.state_snapshot())
    # A snapshot carries market state and not history, so the log, the
    # recorded tape and the cumulative draw counters (and the digest, which
    # includes the draw count) are expected to differ. Everything that drives
    # the market must not.
    assert differences(state(target), state(parent)) == [
        "digest", "draws", "draws_by_stream", "log_entries", "recorded_days",
    ]

    # And continuing from the restore stays on the parent's path, which is
    # what the dials being carried actually buys.
    run(parent, 6, first=6)
    run(target, 6, first=6)
    assert target.prices() == parent.prices()
    assert target.column("volume") == parent.column("volume")


#: A market with nothing dormant in it.
#:
#: A snapshot that forgets a field is invisible while that field is inert,
#: and every latent omission this project has had was found the hard way: the
#: common log-volume state was missing and harmless until pt-v10 turned it on.
#: So the guard below runs on a model where nothing is off, in a market where
#: nothing is quiet.
#:
#: Three deliberate departures from the shipped preset, each because a dial
#: that never fires is a dial the guard cannot see:
#:
#: - every parameter shipped at zero, at 0.05. Measured to keep prices finite
#:   and positive over this horizon.
#: - endogenous news at 0.9 rather than the shipped 0.05, so every day HAS
#:   news. At 0.05 across twelve names a given day is newsless about half the
#:   time, which is exactly how the missing-news defect hid from the suite.
#: - universe stress with weight and decay, under a crisis VIX, because the
#:   stress term ratchets on the VIX above a threshold and stays at zero in a
#:   calm market however large its weight.
def _nothing_dormant():
    shipped = tf.ModelParams.from_preset().to_dict()
    dormant = {name: 0.05 for name in tf.ModelParams.settable()
               if float(shipped[name]) == 0.0}
    assert dormant, "no dial ships at zero; this model is not testing anything"
    dormant.update(endogenous_news_intensity=0.9,
                   universe_stress_weight=0.5,
                   universe_stress_decay=0.9)
    return tf.ModelParams.from_preset(**dormant)


#: A crisis, so the stress term has something to remember.
CRISIS = tf.Macro(vix=45.0, federal_funds_rate=0.05, cycle="contraction")

#: What a snapshot claims to carry: the market, not the history. The log, the
#: recorded tape, the recorded day count and the draw counters are documented
#: omissions and are excluded rather than silently passing.
def market_only(engine):
    skip = {"draws", "draws_by_stream", "digest", "log_entries",
            "recorded_days"}
    return {k: v for k, v in state(engine).items() if k not in skip}


def tape_from(engine, first_day, last_day):
    """The recorded tape over days both engines have.

    A restored engine starts with an EMPTY tape -- that is one of the
    snapshot's documented omissions -- so comparing whole tapes against a fork
    would report a difference that is by design and hide the ones that are
    not. Only the days recorded after the restore are comparable, and those
    are the ones that say whether the two engines are running the same market.

    The tape is worth comparing at all because two of the fields a snapshot
    carries (`tick_fundamental` and `tick_anchor`) reach nothing else: they
    are the labelled-dataset output, which is the library's headline product,
    and a fork that lost them would print identical prices.
    """
    pyarrow = pytest.importorskip("pyarrow")
    out = {}
    for d in range(first_day, last_day):
        table = pyarrow.table(engine.truth(day=d))
        for column in table.column_names:
            out[f"tape:{d}:{column}"] = table.column(column).to_pylist()
    return out


def _mid_day_parent(model, macro):
    """A parent stopped between two sessions of one day.

    Mid-day on purpose: every field the snapshot's list has lost was per-day
    state, and an engine stopped on a day boundary has none of it to lose.
    """
    engine = tf.Engine(seed=SEED, universe=UNIVERSE, macro_state=macro,
                       model=model)
    run(engine, 4)
    engine.open_market()
    engine.run_session(9, 30, 3, TICKS_PER_DAY, order_flow=FLOW)
    return engine


#: The day the parent was interrupted on, and how far both engines run after
#: it. Twenty-five days because the central bank's meeting calendar and the
#: day counter that drives it are state whose effect is weeks away, not
#: tomorrow, and a shorter run cannot see them.
SPLIT_DAY = 4
CONTINUE_DAYS = 25


def _continue(engine):
    """Finish the open day, then run on."""
    engine.run_session(10, 30, 3, TICKS_PER_DAY, order_flow=FLOW)
    engine.record(SPLIT_DAY)
    engine.close_market()
    run(engine, CONTINUE_DAYS, first=SPLIT_DAY + 1)
    return engine


def _split_day_tail(engine):
    """The split day's tape, restricted to the session both engines ran.

    The split day is half recorded when the snapshot is taken and a restored
    engine does not carry the day's accumulated ticks -- one of the omissions
    the snapshot documents -- so the fork's day-4 tape is two sessions and the
    restored engine's is one. The LAST session is the comparable part, and it
    is the most informative rows in the whole comparison: it is the first
    thing either engine does after the restore, so a field that is missing
    shows up there before anything has had a chance to wash out.
    """
    pyarrow = pytest.importorskip("pyarrow")
    rows = TICKS_PER_DAY * len(UNIVERSE)
    table = pyarrow.table(engine.truth(day=SPLIT_DAY))
    # Every column but `tick`, which is the row's INDEX within the day and so
    # counts from the start of whatever each engine recorded: the fork's day
    # is two sessions and the restored engine's is one, so the same session
    # is numbered 26-51 in one and 0-25 in the other. That is the labelling
    # consequence of the day buffer the snapshot does not carry, not a
    # divergence, and comparing it would make this guard fail always and
    # therefore mean nothing.
    return {f"split:{c}": table.column(c).to_pylist()[-rows:]
            for c in table.column_names if c != "tick"}


def _diverged(left, right):
    """Where two continuations disagree: market state, the session they both
    recorded on the split day, and every whole day after it."""
    first = SPLIT_DAY + 1
    last = first + CONTINUE_DAYS

    def everything(engine):
        return {**market_only(engine), **_split_day_tail(engine),
                **tape_from(engine, first, last)}

    return differences(everything(left), everything(right))


@pytest.mark.parametrize("lively", [False, True],
                         ids=["shipped-default", "nothing-dormant"])
def test_a_restored_snapshot_continues_like_a_copy(lively):
    """The drift guard for `state_snapshot`, asked as behaviour.

    `branch` copies the engine, so a fork is complete by construction and is
    the reference. `state_snapshot` is a hand-written list of fields, and that
    list has been wrong six times: the per-day accumulators, the market-open
    flag, the market factor's variance, the common log-volume state, the day
    counter, and the day's endogenous news.

    Nothing in the library uses the snapshot any more, which removes the
    pressure that kept catching those and leaves the drift free to continue.
    So this compares the two mechanisms directly and CONTINUES them: a field
    that only matters tomorrow -- the GARCH variance was exactly that -- shows
    up nowhere in today's prices, and a state comparison alone would pass.
    """
    model = _nothing_dormant() if lively else None
    macro = CRISIS if lively else None

    parent = _mid_day_parent(model, macro)
    reference, = tf.branch(parent, 1)
    restored = tf.Engine(seed=SEED, universe=UNIVERSE, macro_state=macro,
                         model=model)
    restored.restore_state(parent.state_snapshot())

    gap = differences(market_only(restored), market_only(parent))
    assert gap == [], (
        f"a snapshot no longer restores the market it captured: {gap}. "
        "state_snapshot is a written list of fields and the engine has grown "
        "past it again."
    )

    _continue(reference)
    _continue(restored)
    gap = _diverged(restored, reference)
    assert gap == [], (
        f"a restored snapshot diverged from a copy: {gap}. Something the "
        "engine carries drives the market and is not in the snapshot."
    )


#: The keys `restore_state` requires outright, refusing rather than silently
#: restoring half a market. Each has its own test; dropping one here would
#: measure the refusal, not the field.
REQUIRED_SNAPSHOT_KEYS = ("columns", "rng", "tickers", "tick_components")

#: Snapshot fields this scenario cannot reach, each with the condition its
#: effect waits on. Same shape as `PERTURBATIONS` in `test_model_params.py`,
#: and for the same reason: a field that moves nothing WITHOUT a named reason
#: is a field the guard below is not guarding, and the difference between
#: those two cases is the whole value of the check.
UNREACHED_SNAPSHOT_FIELDS = {
    "draw_counts":
        "the address counters behind tradefloor.noise. A generator restored "
        "without them continues from counts of zero, so a patch written "
        "against the source lands elsewhere or nowhere; with no overlay "
        "installed nothing reads them, and the trajectory is the same. "
        "test_noise.py pins both halves.",
    "draw_overlay":
        "the substitutions installed by tradefloor.noise. This scenario "
        "installs none, so there is nothing to drop; test_noise.py restores "
        "a snapshot with one installed and asserts the continuation keeps it.",
    "model_fingerprint":
        "not state. It is the guard that refuses a snapshot restored onto an "
        "engine running other coefficients, which has its own test; dropping "
        "it removes a check rather than a value.",
    "tick_fundamental":
        "per-tick scratch. The valuation is recomputed every tick before "
        "anything reads it, so a continuation never depends on the value "
        "carried. It is in the snapshot for the tape's current row.",
    "tick_anchor":
        "per-tick scratch, exactly as tick_fundamental.",
    "universe_stress":
        "the remembered stress contributes only where it EXCEEDS the instant "
        "stress -- market/tick.rs computes max(remembered - instant, 0) -- so "
        "it needs a market whose VIX was high and is now held low. This "
        "scenario keeps the VIX in crisis throughout, which makes "
        "every OTHER dial live.",
    "nominal_output_base":
        "the run's opening gdp times cpi, read once at construction. Both "
        "engines here are built from the same macro state, and Macro cannot "
        "set either level, so their bases are the same number and dropping "
        "the key restores to it. What reaches this field is a snapshot whose "
        "economy carries output away from the restoring engine's own, which "
        "test_earnings_nominal_growth.py restores and then prices against.",
    "central_bank":
        "the meeting calendar runs off day_count, which IS restored, so both "
        "engines schedule the same meetings. A difference needs a run that "
        "crosses a meeting whose decision depends on carried bank state "
        "rather than on the calendar.",
}


def test_the_drift_guard_notices_every_field_the_snapshot_carries():
    """Guards the guard, and it needs guarding.

    A test that compares two engines proves nothing unless the comparison can
    fail. Written first without the crisis macro and with the shipped news
    rate, this same guard saw only six of the fourteen optional fields -- it
    would have passed a snapshot that had lost the day's news, which is the
    defect the whole forking pass was about.

    So each field is dropped from the snapshot in turn and the divergence must
    be caught, or the field must appear in `UNREACHED_SNAPSHOT_FIELDS` with
    the condition its effect waits on. Asserted as an equality in both
    directions: a NEW field that nothing reaches fails here, and a field that
    becomes reachable fails here too, which is the prompt to delete its note
    rather than let a stale excuse accumulate.
    """
    model = _nothing_dormant()
    carried = [k for k in _mid_day_parent(model, CRISIS).state_snapshot()
               if k not in REQUIRED_SNAPSHOT_KEYS]
    assert len(carried) >= 10, carried

    invisible = []
    for key in carried:
        parent = _mid_day_parent(model, CRISIS)
        reference, = tf.branch(parent, 1)
        damaged = parent.state_snapshot()
        damaged.pop(key)
        restored = tf.Engine(seed=SEED, universe=UNIVERSE, macro_state=CRISIS,
                             model=model)
        restored.restore_state(damaged)
        _continue(reference)
        _continue(restored)
        if not _diverged(restored, reference):
            invisible.append(key)

    unnamed = sorted(set(invisible) - set(UNREACHED_SNAPSHOT_FIELDS))
    assert unnamed == [], (
        f"dropping {unnamed} from a snapshot changed nothing this scenario "
        "can see, so the guard above is not guarding those fields. Either "
        "give the scenario a market that exercises them, or add them to "
        "UNREACHED_SNAPSHOT_FIELDS with the condition their effect waits on."
    )
    stale = sorted(set(UNREACHED_SNAPSHOT_FIELDS) - set(invisible))
    assert stale == [], (
        f"{stale} are recorded as out of this scenario's reach and the "
        "scenario now reaches them. Delete the note: an excuse that is no "
        "longer true is how a list of known gaps stops being read."
    )


def test_a_snapshot_does_not_carry_history_and_says_so():
    """The documented limit of `state_snapshot`, pinned.

    It carries market state, not history: no order log, no recorded tape. A
    reader who assumes otherwise gets an engine that cannot be checkpointed,
    so `branch` copies the engine instead of rebuilding one from
    this. Asserted so the boundary stays where the docstring puts it.
    """
    engine, _ = checkpoint_at(5)
    target = fresh()
    target.restore_state(engine.state_snapshot())

    assert target.prices() == engine.prices()
    assert target.order_log == []
    assert target.recorded_days == 0


# --------------------------------------------------------------------------
# 9b. Every shipped preset, not just the default
# --------------------------------------------------------------------------

#: Every preset a user can still select. Named rather than discovered, so a new
#: one has to be added here on purpose -- and a preset that changes what a fork
#: has to carry is the thing that would otherwise ship unchecked.
SHIPPED_PRESETS = ("pt-v1", "pt-v4", "pt-v6", "pt-v8", "pt-v10", "pt-v12",
                   "pt-v14", "pt-v15")


@pytest.mark.parametrize("preset", SHIPPED_PRESETS)
def test_the_fork_guarantees_hold_on_every_shipped_preset(preset):
    """A preset is a different market, and can be a different set of live state.

    The news defect was inert before `pt-v11` and live after it; the common
    log-volume state was inert before `pt-v10` and live after it. So "the fork
    is exact" is a claim about a preset, not about the library, and testing it
    only on the default would keep missing the same class of defect one preset
    at a time.

    Deliberately small -- eight steps, a mid-day fork, a restore and a
    checkpoint of a fork -- because it runs eight times.
    """
    parent = fresh(model=preset)
    run(parent, 4)

    # A mid-day fork continues the parent exactly.
    parent.open_market()
    parent.run_session(9, 30, 3, TICKS_PER_DAY, order_flow=FLOW)
    fork, = tf.branch(parent, 1)
    parent.run_session(10, 30, 3, TICKS_PER_DAY, order_flow=FLOW)
    fork.run_session(10, 30, 3, TICKS_PER_DAY, order_flow=FLOW)
    assert differences(state(fork), state(parent)) == [], preset
    for engine in (parent, fork):
        engine.record(4)
        engine.close_market()

    # A checkpoint restores and then continues exactly.
    point = tf.Checkpoint.of(parent, universe=UNIVERSE, seed=SEED)
    restored = point.resume()
    assert market_digest(restored) == market_digest(parent), preset
    for i in range(5, 9):
        day(parent, i)
        day(restored, i)
    assert differences(state(restored), state(parent)) == [], preset

    # And a fork can be checkpointed on its own terms.
    child, = tf.branch(parent, 1)
    run(child, 3, first=9)
    assert market_digest(
        tf.Checkpoint.of(child, universe=UNIVERSE, seed=SEED).resume()
    ) == market_digest(child), preset


# --------------------------------------------------------------------------
# 10. What the errors say
# --------------------------------------------------------------------------


def test_a_fork_count_below_one_is_refused():
    engine, point = checkpoint_at(2)
    with pytest.raises(tf.ValidationError, match="at least 1"):
        tf.branch(engine, 0)
    with pytest.raises(tf.ValidationError, match="at least 1"):
        point.branch(0)
    with pytest.raises(tf.ValidationError, match="at least 1"):
        engine.fork(-3)


def test_forking_onto_another_roster_is_refused_by_name():
    """The error has to say WHICH roster, or a caller reads it as a bug in the
    library rather than a mistake in their own call."""
    engine, _ = checkpoint_at(2)
    smaller = tf.Universe.random(4, seed=20260829)
    with pytest.raises(tf.ValidationError, match="not the same roster"):
        tf.branch(engine, 2, universe=smaller, seed=SEED)

    reordered = tf.Universe(list(UNIVERSE)[::-1])
    with pytest.raises(tf.ValidationError, match="ordered differently"):
        tf.branch(engine, 2, universe=reordered, seed=SEED)


def test_a_corrupted_checkpoint_says_what_is_missing():
    """A truncated archive must not arrive as a KeyError.

    `KeyError: 'seed'` tells a developer that a dictionary lacked a key. It
    does not tell them they are holding a damaged checkpoint, which is the
    fact they need.
    """
    _, point = checkpoint_at(3)
    for field in ("seed", "universe", "log"):
        payload = json.loads(point.to_json())
        del payload[field]
        with pytest.raises(tf.ValidationError, match=field):
            tf.Checkpoint.from_json(json.dumps(payload))


def test_a_checkpoint_that_is_not_a_checkpoint_says_so():
    with pytest.raises(tf.ValidationError, match="not a checkpoint"):
        tf.Checkpoint.from_json("[1, 2, 3]")
    with pytest.raises(tf.ValidationError, match="not JSON"):
        tf.Checkpoint.from_json("{not json at all")


def test_a_checkpoint_from_another_era_is_refused_by_version():
    """A build that moved a trajectory must not replay an old checkpoint.

    This is the failure with no symptom. A replay is the original run
    re-executed, so a release that changed the arithmetic does not resume an
    old checkpoint into something visibly broken: it resumes into a market of
    the right size and the right shape that never existed. `RunManifest` has
    checked this since 0.2 and a checkpoint carried no version at all.

    The error has to name BOTH versions, because "these differ" leaves a
    developer with nothing to do and "written by 0.4.3, this is 0.5.0" tells
    them what to install.
    """
    _, point = checkpoint_at(3)
    payload = json.loads(point.to_json())
    assert payload["tradefloor_version"] == tf.__version__
    payload["era"] = "0" * 64
    payload["tradefloor_version"] = "0.4.3"

    stale = tf.Checkpoint.from_json(json.dumps(payload))
    with pytest.raises(tf.ValidationError) as raised:
        stale.resume()
    message = str(raised.value)
    assert "0.4.3" in message and tf.__version__ in message
    assert "000000000000" in message


def test_a_checkpoint_without_an_era_still_resumes():
    """Absent means "written before this was recorded", not "mismatched".

    Every archive made before this release has no era. Refusing them would
    break them to guard against a hazard nothing can measure for them, which
    is the same reading the universe fingerprint already takes.
    """
    engine, point = checkpoint_at(5)
    payload = json.loads(point.to_json())
    del payload["era"]
    del payload["tradefloor_version"]
    restored = tf.Checkpoint.from_json(json.dumps(payload))
    assert differences(state(restored.resume()), state(engine)) == []


def test_a_newer_schema_is_refused_by_name():
    _, point = checkpoint_at(3)
    payload = json.loads(point.to_json())
    payload["schema"] = 99
    with pytest.raises(tf.ValidationError, match="newer"):
        tf.Checkpoint.from_json(json.dumps(payload))


def test_resuming_onto_the_wrong_universe_is_refused():
    _, point = checkpoint_at(3)
    other = tf.Universe.random(len(UNIVERSE), seed=99)
    # Same tickers, different fundamentals: the substitution names cannot see.
    assert [i.ticker for i in other] == [i.ticker for i in UNIVERSE]
    with pytest.raises(tf.ValidationError, match="fingerprint"):
        point.resume(universe=other)


def test_an_unknown_log_operation_is_refused_by_name():
    """A replay that skipped an entry would produce a market the log does not
    describe, and would look like a successful replay."""
    _, point = checkpoint_at(3)
    point.log[1]["op"] = "teleport"
    with pytest.raises(tf.ValidationError, match="teleport"):
        point.resume()


def test_an_invalid_intervention_is_refused_before_it_runs():
    """A scenario that drives a field nothing reads would be an experiment
    with no treatment, and the result would look like a null finding."""
    with pytest.raises(tf.ValidationError):
        Scenario().hold(not_a_macro_field=1.0)
