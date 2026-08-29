"""The counterfactual machinery, and the claims it lets an experiment make.

A fork is only worth anything if the arms really did start identical and
really are independent afterwards. Both are easy to believe and easy to get
wrong, and neither is visible in a result: two arms that quietly began from
different states produce a comparison that looks exactly like a valid one.

So the assertions here are mostly negative controls. It is not enough that
:func:`tradefloor.agree` says "identical" -- something has to check that it
would say otherwise if the arms were not, or the whole verification table is
nine rows of decoration.
"""

from __future__ import annotations

import copy
import json

import pytest

import tradefloor as tf
from tradefloor.counterfactual import World, agree, compare
from tradefloor.manifest import market_digest

SEED = 4242
WARMUP = 4
FORWARD = 4


class Ramp:
    """A deterministic agent that reads the policy rate and nothing else.

    Small on purpose. The demo's agent is the one being demonstrated; this one
    is the smallest thing that reacts to an intervention, so a failure here is
    a failure of the machinery rather than of a policy.
    """

    def __init__(self) -> None:
        self.base: float | None = None
        self.seen: list[float] = []

    def act(self, obs) -> dict[str, float]:
        rate = obs.engine.macro_state.federal_funds_rate
        if self.base is None:
            self.base = rate
        self.seen.append(rate)
        gross = 0.8 if rate <= self.base else 0.2
        return tf.baselines.rebalance(
            obs, {t: gross / len(obs.tickers) for t in obs.tickers},
            max_participation=0.02)

    def decision(self):
        return {"rate": self.seen[-1] if self.seen else None,
                "gross": 0.8 if (not self.seen or self.seen[-1] <= self.base)
                else 0.2}

    def state(self):
        return {"base": self.base, "seen": list(self.seen)}


def roster():
    return list(tf.Universe.random(4, seed=11))


def build(**over) -> World:
    kwargs = dict(seed=SEED, universe=roster(), agent=Ramp(),
                  pins={"federal_funds_rate": 0.04,
                        "corporate_bond_yield": 0.055},
                  cash=5_000_000.0, steps_per_day=3, ticks_per_step=30,
                  label="root")
    kwargs.update(over)
    return World(**kwargs)


def experiment(days_before: int = WARMUP, days_after: int = FORWARD):
    """The whole shape: history, checkpoint, fork, intervene, run both."""
    world = build()
    world.run(days=days_before)
    mark = world.checkpoint(label="before")
    control, shock = world.fork("control", "shock")
    agreement = agree(control, shock)
    shock.intervene(federal_funds_rate=0.06, corporate_bond_yield=0.075)
    control.run(days=days_after)
    shock.run(days=days_after)
    return world, mark, control, shock, agreement


# ---------------------------------------------------------------------------
# The shared history
# ---------------------------------------------------------------------------

def test_the_same_seed_gives_the_same_history_before_the_checkpoint():
    a, b = build(), build()
    a.run(days=WARMUP)
    b.run(days=WARMUP)
    assert a.trace == b.trace
    assert a.digest() == b.digest()
    assert a.engine.order_log == b.engine.order_log


def test_a_different_seed_gives_a_different_history():
    """The guard on the guard. If every seed gave the same market the test
    above would pass on a simulator that ignored its seed entirely."""
    a, b = build(), build(seed=SEED + 1)
    a.run(days=WARMUP)
    b.run(days=WARMUP)
    assert a.digest() != b.digest()


def test_a_checkpoint_resumes_to_the_state_the_fork_starts_from():
    """The two fork routes have to agree, or one of them is wrong.

    ``branch`` copies the engine's state snapshot; ``Checkpoint`` replays the
    order log. They are independent implementations of the same idea, and the
    log is the one a published result cites -- so if the in-memory fork does
    not land where the replay lands, the experiment is not the thing anyone
    else can reproduce.
    """
    world = build()
    world.run(days=WARMUP)
    (forked,) = world.fork("forked")
    replayed = world.checkpoint().resume()

    assert forked.engine.prices() == replayed.prices()
    for field in ("price", "previous_close", "mispricing_s",
                  "garch_variance", "maker_inventory", "avg_volume",
                  "market_cap", "volume"):
        assert forked.engine.column(field) == replayed.column(field), field
    snap_a = forked.engine.state_snapshot()
    snap_b = replayed.state_snapshot()
    assert snap_a["columns"] == snap_b["columns"]
    assert snap_a["economy"] == snap_b["economy"]
    assert snap_a["central_bank"] == snap_b["central_bank"]
    assert snap_a["day_count"] == snap_b["day_count"]


def test_a_resumed_checkpoint_continues_identically():
    """Restoring is not enough; the continuation has to match too.

    A state that restores correctly and then prices the next day differently
    is the exact defect `checkpoint.py` documents, invisible at the
    moment of the restore.
    """
    world = build()
    world.run(days=WARMUP)
    (forked,) = world.fork("forked")
    forked.run(days=FORWARD)

    resumed = world.checkpoint().resume()
    scenario = world.scenario()
    for day in range(WARMUP, WARMUP + FORWARD):
        scenario.apply(resumed, day)
        resumed.open_market()
        for step in range(world.steps_per_day):
            resumed.run_session(
                *tf.harness.session_clock((9, 30, 3), step,
                                          world.ticks_per_step),
                world.ticks_per_step)
        resumed.close_market()
    # The replay carries no agent, so its prices are the untraded ones. What
    # must match is that a fork with no orders and a replay with no orders
    # land in the same place; the traded case is covered by the arms being
    # bit-identical to each other above.
    (quiet,) = world.fork("quiet")
    quiet.agent = _Idle()
    quiet.run(days=FORWARD)
    assert quiet.engine.prices() == resumed.prices()


class _Idle:
    def act(self, obs):
        return {}


# ---------------------------------------------------------------------------
# The fork
# ---------------------------------------------------------------------------

def test_the_two_arms_are_identical_before_the_intervention():
    world = build()
    world.run(days=WARMUP)
    control, shock = world.fork("control", "shock")
    agreement = agree(control, shock)
    assert agreement.identical, agreement.differences
    assert len(agreement.checks) == 9
    assert not agreement.differences


def test_agree_reports_a_difference_when_there_is_one():
    """The negative control, and the reason the table above means anything.

    Nine rows that always say "identical" would verify nothing. Each of these
    perturbs one arm in one place and the check that owns it has to notice.
    """
    world = build()
    world.run(days=WARMUP)

    def perturbed(mutate):
        control, shock = world.fork("control", "shock")
        mutate(shock)
        return agree(control, shock)

    def move_the_market(arm):
        arm.engine.open_market()
        arm.engine.run_session(9, 30, 3, 30)
        arm.engine.close_market()

    assert "market columns" in perturbed(move_the_market).differences
    assert "prices" in perturbed(move_the_market).differences
    assert "generator state" in perturbed(move_the_market).differences

    def spend(arm):
        arm.portfolio.cash -= 1.0
    assert perturbed(spend).differences == ["portfolio"]

    def hike(arm):
        arm.engine.pin_macro(federal_funds_rate=0.06)
    assert set(perturbed(hike).differences) == {"macro chain",
                                                "whole engine state"}

    def teach(arm):
        arm.agent.base = 0.99
    assert perturbed(teach).differences == ["agent state"]

    def rewrite(arm):
        arm.trace = arm.trace[:-1]
    assert perturbed(rewrite).differences == ["shared history"]


def test_driving_one_arm_does_not_touch_the_other():
    """Independence in the strong sense. Shared memory anywhere between the
    arms would make the control a function of the treatment."""
    world = build()
    world.run(days=WARMUP)
    control, shock = world.fork("control", "shock")

    before = control.digest()
    before_worth = control.net_worth()
    before_trace = copy.deepcopy(control.trace)

    shock.intervene(federal_funds_rate=0.06, corporate_bond_yield=0.075)
    shock.run(days=FORWARD)

    assert control.digest() == before
    assert control.net_worth() == before_worth
    assert control.trace == before_trace
    assert control.pins == {"federal_funds_rate": 0.04,
                            "corporate_bond_yield": 0.055}
    assert control.interventions == []


def test_a_fork_refuses_an_open_market():
    world = build()
    world.run(days=1)
    world.engine.open_market()
    with pytest.raises(tf.ValidationError, match="market open"):
        world.fork("a", "b")
    with pytest.raises(tf.ValidationError, match="market open"):
        world.checkpoint()


def test_fork_refuses_duplicate_labels():
    world = build()
    world.run(days=1)
    with pytest.raises(tf.ValidationError, match="distinct"):
        world.fork("same", "same")


# ---------------------------------------------------------------------------
# The intervention
# ---------------------------------------------------------------------------

def test_only_the_intervened_fields_differ_after_the_intervention():
    """One changed variable means one changed variable.

    Read on the first step of the first day AFTER the intervention, which is
    the moment it becomes visible: everything else in the macro state has to
    be the value the control has.
    """
    world = build()
    world.run(days=WARMUP)
    control, shock = world.fork("control", "shock")
    shock.intervene(federal_funds_rate=0.06, corporate_bond_yield=0.075)
    control.run(days=1)
    shock.run(days=1)

    first = world.step
    a = control.trace[first]["macro"]
    b = shock.trace[first]["macro"]
    changed = {field for field in a if a[field] != b[field]}
    assert changed == {"federal_funds_rate", "corporate_bond_yield"}
    assert b["federal_funds_rate"] == pytest.approx(0.06)
    assert b["corporate_bond_yield"] == pytest.approx(0.075)


def test_the_intervention_is_recorded_in_the_engines_own_log():
    """Not only in the Python object. A checkpoint that did not carry the
    intervention would replay the control and call it the treatment."""
    _world, _mark, control, shock, _agreement = experiment()

    def rates(world):
        return [entry["fields"]["federal_funds_rate"]
                for entry in world.engine.order_log
                if entry["op"] == "pin_macro"]

    # Both arms carry the whole history, because a fork is a copy of the
    # engine: the shock arm's log holds the shared days at 0.04 AND its own
    # at 0.06. That is the point -- an arm whose log began at the fork would
    # replay into a market that never had a pre-shock history.
    assert set(rates(control)) == {0.04}
    assert len(rates(control)) == WARMUP + FORWARD

    assert set(rates(shock)) == {0.04, 0.06}
    assert set(rates(shock)[:WARMUP]) == {0.04}
    assert set(rates(shock)[WARMUP:]) == {0.06}
    assert len(rates(shock)[WARMUP:]) == FORWARD


def test_the_scenario_describes_the_intervention_as_data():
    _world, _mark, _control, shock, _agreement = experiment()
    path = json.loads(shock.scenario().to_json(WARMUP + FORWARD))["path"]
    assert path[WARMUP - 1]["federal_funds_rate"] == pytest.approx(0.04)
    assert path[WARMUP]["federal_funds_rate"] == pytest.approx(0.06)
    assert path[WARMUP]["corporate_bond_yield"] == pytest.approx(0.075)


def test_intervene_refuses_to_change_nothing():
    world = build()
    with pytest.raises(tf.ValidationError, match="changes nothing"):
        world.intervene()


# ---------------------------------------------------------------------------
# Reproducibility of the whole experiment
# ---------------------------------------------------------------------------

def test_the_whole_experiment_reruns_identically():
    """Both arms, twice, to the bit. If this fails the comparison is noise."""
    first = experiment()
    second = experiment()
    for a, b in ((first[2], second[2]), (first[3], second[3])):
        assert a.trace == b.trace
        assert a.digest() == b.digest()
        assert a.summary() == b.summary()


def test_the_arms_are_not_identical_to_each_other():
    """The other guard on the guard: an experiment where the intervention did
    nothing would pass every determinism test above."""
    _world, _mark, control, shock, _agreement = experiment()
    assert control.trace != shock.trace
    assert control.digest() != shock.digest()
    assert control.net_worth() != shock.net_worth()


# ---------------------------------------------------------------------------
# The comparison
# ---------------------------------------------------------------------------

def test_nothing_diverges_before_the_intervention():
    _world, _mark, control, shock, agreement = experiment()
    report = compare(control, shock, agreement=agreement)
    d = report.divergence

    assert d.intervention_step == WARMUP * control.steps_per_day
    for field in ("macro", "decision", "orders", "prices", "portfolio"):
        step = getattr(d, field)
        assert step is not None, f"{field} never diverged"
        assert step >= d.intervention_step, (
            f"{field} diverged at step {step}, before the intervention at "
            f"{d.intervention_step}. Something other than the intervention "
            "differs between the arms.")


def test_the_comparison_measures_the_window_after_the_fork():
    _world, _mark, control, shock, _agreement = experiment()
    report = compare(control, shock)
    fork = WARMUP * control.steps_per_day
    assert report.control["measured_from_step"] == fork
    assert report.treatment["measured_from_step"] == fork
    # Whole-run counts are strictly larger, which is the thing the window
    # exists to keep out of the table.
    assert (control.summary(since=0)["turnover"]
            > report.control["turnover"] > 0)


def test_compare_refuses_arms_that_do_not_line_up():
    a = build(steps_per_day=3)
    b = build(steps_per_day=4)
    a.run(days=1)
    b.run(days=1)
    with pytest.raises(tf.ValidationError, match="step for step"):
        compare(a, b)


def test_a_manifest_of_each_arm_reproduces_it():
    _world, _mark, control, shock, _agreement = experiment()
    for arm in (control, shock):
        doc = arm.manifest(strategy="tests/test_counterfactual.py::Ramp")
        restored = tf.RunManifest.from_json(doc.to_json())
        engine = restored.reproduce()
        assert engine.prices() == arm.engine.prices()


# ---------------------------------------------------------------------------
# Two rough edges in the library, pinned where they are worked around
# ---------------------------------------------------------------------------

def test_a_state_snapshot_does_not_compare_equal_to_itself():
    """Pinned as CURRENT BEHAVIOUR, and the reason `agree` compares bitwise.

    ``state_snapshot()`` returns the generator position as f64s carrying u64
    bit patterns, and some of those patterns are NaN, which never compares
    equal to itself. So ``==`` on two snapshots of the SAME engine is False,
    and the obvious way to verify a fork reports a difference that is not
    there -- a false negative, on the one check an experiment most needs.

    If this test starts failing, the snapshot has stopped carrying raw
    patterns and `_bits` in `counterfactual.py` can go.
    """
    world = build()
    world.run(days=2)
    snapshot = world.engine.state_snapshot()
    assert snapshot != world.engine.state_snapshot()
    assert any(value != value for value in snapshot["rng"])


def test_a_branch_carries_the_draw_counter_and_the_log():
    """The fork is a copy, so both come with it.

    This asserted the opposite until `Engine.fork` replaced the
    rebuild-from-a-field-list branch. A fresh engine restored from a state
    snapshot started at zero draws with an empty log; `market_digest` folds
    the draw counter in, so a branched engine's digest differed from its
    parent's while every column was identical, and a RunManifest written from
    a branch recorded a different `result.digest` than one written from the
    checkpoint replay of the same state. Two engines on one market, two
    digests. A copy has none of that, and it closes the whole class rather
    than the three cases that had been found.
    """
    world = build()
    world.run(days=2)
    (forked,) = world.fork("forked")

    assert world.engine.draws_consumed > 0
    assert forked.engine.draws_consumed == world.engine.draws_consumed
    assert forked.engine.order_log == world.engine.order_log

    assert forked.engine.prices() == world.engine.prices()
    assert (forked.engine.state_snapshot()["columns"]
            == world.engine.state_snapshot()["columns"])
    assert market_digest(forked.engine) == market_digest(world.engine)

    # And the replay route agrees with both, which it did not before.
    replayed = world.checkpoint().resume()
    assert replayed.draws_consumed == world.engine.draws_consumed
    assert market_digest(replayed) == market_digest(world.engine)


def test_a_forked_arms_log_holds_the_shared_history_exactly_once():
    """The regression the two fixes made possible between them.

    `World` carried its parent's log across a fork because a branched engine's
    began empty; `Engine.fork` then started copying it. Each was right alone,
    and together the shared history was in the log twice -- so a manifest
    built from a forked arm replayed the first days over again,
    reproducibly, into a market nobody ran. Which is the defect the World-side
    workaround was written to fix, arriving from the other direction.
    """
    world = build()
    world.run(days=2)
    before = len(world.order_log)
    assert before > 0

    control, shock = world.fork("control", "shock")
    assert len(control.order_log) == before, (
        f"the fork's log holds {len(control.order_log)} entries against the "
        f"parent's {before}; the shared history is in it more than once")

    control.run(days=1)
    assert len(control.order_log) > before
    assert control.order_log[:before] == world.order_log
    assert len(shock.order_log) == before, "the arms are not independent"
