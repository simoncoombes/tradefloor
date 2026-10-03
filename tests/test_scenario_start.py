"""When a scenario starts, and what the record says about it.

The packaged scenarios first fire on day 50 (one on day 30). `at` counts
days from the first day a run loop applies the scenario, so an arm forked on
day 20 and handed `liquidity_crisis` fires it on day 70. An experiment that wants the
packaged file to fire on the first day after its fork has to be able to
say so without editing the file, because an edited copy has its own
fingerprint and the claim "we ran the scenario that ships with tradefloor"
stops being checkable.

These tests pin the two ways of saying it, `Scenario.starting_at(day)` and
`World.apply(scenario, at=day)`, what each records, and that a caller who
says neither gets exactly the timing they got before.
"""

from __future__ import annotations

import json
import struct

import pytest

import tradefloor as tf
from tradefloor.__main__ import main
from tradefloor.counterfactual import World
from tradefloor.manifest import RunManifest
from tradefloor.scenario import _pack

PACKAGED = "liquidity_crisis"
HISTORY = 3


class Idle:
    """An agent that never trades, so the arms differ only by the scenario."""

    def act(self, obs):
        return {}


def build() -> World:
    return World(seed=31, universe=list(tf.Universe.random(4, seed=11)),
                 agent=Idle(), steps_per_day=2, ticks_per_step=20,
                 label="root")


def forked():
    world = build()
    world.run(days=HISTORY)
    control, stress = world.fork("control", "stress")
    return control, stress


def depth(engine) -> list[float]:
    """Each name's quoted depth, the column `market.liquidity` scales."""
    raw = engine.column("avg_volume")
    return list(struct.unpack(f"<{len(raw) // 8}d", raw))


def first_days(scenario: tf.Scenario) -> list[int]:
    return [item.at for item in scenario.interventions]


# ---------------------------------------------------------------------------
# Starting a packaged scenario on the first day after a fork
# ---------------------------------------------------------------------------

def test_a_packaged_scenario_applied_at_zero_fires_on_the_first_day_after_the_fork():
    """The experiment the issue describes: fork, apply, and the crisis lands
    on the first post-fork day. Asserted on the market as well as on the
    record, because a firing the engine never felt would pass a record-only
    test."""
    control, stress = forked()
    fork_day = stress.day
    stress.apply(tf.Scenario.load(PACKAGED), at=0)
    control.run(days=1)
    stress.run(days=1)

    fired = {(f.day, f.target) for f in stress.firings}
    assert fired == {(fork_day, "market.liquidity"),
                     (fork_day, "macro.vix"),
                     (fork_day, "market.earnings"),
                     (fork_day, "macro.corporate_yield")}

    # Quoted depth is 40% of the control's on the fork day...
    ratio = [a / b for a, b in zip(depth(stress.engine),
                                   depth(control.engine))]
    assert ratio == pytest.approx([0.4] * 4)
    # ...and the macro the agent was shown that morning carries the VIX
    # three and a half times the control's.
    row = HISTORY * stress.steps_per_day
    assert stress.trace[row]["day"] == fork_day
    assert stress.trace[row]["macro"]["vix"] == pytest.approx(
        3.5 * control.trace[row]["macro"]["vix"])


def test_the_arms_are_bit_identical_until_the_day_the_scenario_fires():
    """A start two days after the fork: the arms must agree to the bit on
    both days before it and part on the day itself. Earlier would mean the
    scenario reached the market before it said; later, that it fired into a
    market that did not notice."""
    control, stress = forked()
    fork_day = stress.day
    stress.apply(tf.Scenario.load(PACKAGED), at=2)
    for day in range(fork_day, fork_day + 2):
        control.run(days=1)
        stress.run(days=1)
        assert stress.digest() == control.digest(), day
        assert stress.firings == ()
    control.run(days=1)
    stress.run(days=1)
    assert stress.digest() != control.digest()
    assert {f.day for f in stress.firings} == {fork_day + 2}


def test_starting_at_moves_every_day_by_one_amount():
    """The first firing moves to the day asked for and every other one keeps
    its distance from it: the earnings recovery is still 42 days after the
    onset."""
    packaged = tf.Scenario.load(PACKAGED)
    shifted = packaged.starting_at(0)
    assert first_days(packaged) == [50, 50, 50, 92, 50]
    assert first_days(shifted) == [0, 0, 0, 42, 0]
    assert first_days(packaged.starting_at(7)) == [7, 7, 7, 49, 7]
    # The packaged object is untouched, so it can be applied again.
    assert first_days(packaged) == [50, 50, 50, 92, 50]


def test_a_shifted_scenario_is_the_packaged_file_with_its_days_moved():
    """The shifted scenario fingerprints exactly as the hand-edited copy
    would, because it is the same experiment, and its record names the file
    it came from so a reader can check that rather than take it on trust."""
    packaged = tf.Scenario.load(PACKAGED)
    text = (_pack() / f"{PACKAGED}.yml").read_text()
    copy = tf.Scenario.from_yaml(
        text.replace("at: 50", "at: 0").replace("at: 92", "at: 42"))
    shifted = packaged.starting_at(0)

    assert shifted.fingerprint == copy.fingerprint != packaged.fingerprint
    assert shifted.origins == ({
        "name": PACKAGED,
        "fingerprint": packaged.fingerprint,
        "source": f"{PACKAGED}.yml",
        "shift": -50,
        "first_day": 0,
        "days": [0, 0, 0, 42, 0],
        "applied_on_day": None,
    },)
    # Shifting again is measured from the file, not from the last shift.
    again = shifted.starting_at(5)
    assert again.origins[0]["fingerprint"] == packaged.fingerprint
    assert again.origins[0]["shift"] == -45
    assert packaged.origins == ()


def test_the_world_records_the_packaged_fingerprint_and_the_effective_days():
    control, stress = forked()
    packaged = tf.Scenario.load(PACKAGED)
    stress.apply(packaged, at=0)
    stress.run(days=1)

    expected = {
        "name": PACKAGED,
        "fingerprint": packaged.fingerprint,
        "source": f"{PACKAGED}.yml",
        "shift": HISTORY - 50,
        "first_day": HISTORY,
        "days": [HISTORY, HISTORY, HISTORY, HISTORY + 42, HISTORY],
        "applied_on_day": HISTORY,
    }
    assert stress.scenario().origins == (expected,)

    # The manifest carries it, under the manifest's own scenario
    # fingerprint, and reads it back.
    manifest = stress.manifest(strategy="tests")
    doc = json.loads(manifest.to_json())
    assert doc["scenario"]["origins"] == [expected]
    assert RunManifest.from_json(manifest.to_json()).scenario.origins == \
        (expected,)
    # What the market saw is still recorded as it was: the effective days.
    assert [s["at"] for s in doc["scenario"]["shocks"]] == \
        [HISTORY, HISTORY, HISTORY, HISTORY + 42]


def test_the_raw_path_fires_on_the_first_day_of_its_own_loop():
    """`Scenario.apply` counts the loop's own days, so on a branched engine
    the first loop day is the first day after the branch."""
    engine = tf.Engine(seed=31, universe=list(tf.Universe.random(4, seed=11)))
    for _ in range(HISTORY):
        engine.open_market()
        engine.run_session(9, 30, 3, 20)
        engine.close_market()
    control, stress = tf.branch(engine, 2)
    scenario = tf.Scenario.load(PACKAGED).starting_at(0)
    for day in range(2):
        scenario.apply(stress, day)
        for arm in (control, stress):
            arm.open_market()
            arm.run_session(9, 30, 3, 20)
            arm.close_market()
    assert min(f.day for f in scenario.log) == 0
    assert {f.target for f in scenario.log if f.day == 0} == {
        "market.liquidity", "macro.vix", "market.earnings",
        "macro.corporate_yield"}
    ratio = [a / b for a, b in zip(depth(stress), depth(control))]
    assert ratio == pytest.approx([0.4] * 4)


def test_run_scenario_takes_a_shifted_scenario_and_refuses_one_past_its_end():
    universe = list(tf.Universe.random(4, seed=11))
    scenario = tf.Scenario.load(PACKAGED).starting_at(1)
    tf.run_scenario(scenario, seed=31, universe=universe, days=2,
                    ticks_per_day=20)
    assert {f.day for f in scenario.log} == {1}

    with pytest.raises(tf.ValidationError, match="day 5.*5 days"):
        tf.run_scenario(tf.Scenario.load(PACKAGED).starting_at(5), seed=31,
                        universe=universe, days=5, ticks_per_day=20)


# ---------------------------------------------------------------------------
# Callers who give no start are unaffected
# ---------------------------------------------------------------------------

def test_no_start_rebases_exactly_as_before():
    """`apply(scenario)` adds the world's day to every `at`, as it always
    has, and records the file it came from with that shift."""
    _control, stress = forked()
    packaged = tf.Scenario.load(PACKAGED)
    stress.apply(packaged)
    assert [item.at for item in stress.applied] == \
        [at + HISTORY for at in first_days(packaged)]
    assert stress.scenario().origins[0]["shift"] == HISTORY
    assert stress.scenario().origins[0]["fingerprint"] == packaged.fingerprint

    _control, explicit = forked()
    explicit.apply(packaged, at=None)
    assert explicit.applied == stress.applied


def test_a_scenario_never_applied_to_a_world_records_no_origin():
    """A world without `apply` writes the manifest it always wrote."""
    world = World(seed=31, universe=list(tf.Universe.random(4, seed=11)),
                  agent=Idle(), steps_per_day=2, ticks_per_step=20,
                  pins={"vix": 20.0})
    world.run(days=1)
    assert "origins" not in json.loads(world.scenario().to_json(1))


# ---------------------------------------------------------------------------
# A window that crosses one call to `run`
# ---------------------------------------------------------------------------

def thin() -> tf.Scenario:
    """Depth held at 40% on days 1-4 after the apply, and the VIX ramped to
    double over days 1-6: one window that needs its first day's level to be
    put back, and one that needs it on every day it writes."""
    return (tf.Scenario(name="thin")
            .shock("market.liquidity", operation="multiply", value=0.4, at=1,
                   duration=4)
            .shock("macro.vix", operation="multiply", value=2.0, at=1,
                   shape="ramp", duration=6))


def test_a_window_survives_running_the_world_a_day_at_a_time():
    """Ten days as one call or as ten calls is the same run.

    `run` used to build a fresh scenario on every call, so a hold or a ramp
    that began in one call had no anchor in the next, and the second call
    raised.
    """
    _c1, whole = forked()
    _c2, daily = forked()
    for arm in (whole, daily):
        arm.apply(thin())
    whole.run(days=10)
    for _ in range(10):
        daily.run(days=1)
    assert daily.digest() == whole.digest()
    assert [f.as_dict() for f in daily.firings] == \
        [f.as_dict() for f in whole.firings]
    released = [f for f in daily.firings if f.operation == "release"]
    assert [(f.day, f.target) for f in released] == \
        [(HISTORY + 5, "market.liquidity")]


def test_a_window_that_ends_on_a_call_boundary_is_still_released():
    """The quiet half of the same defect. The release that writes quoted
    depth back needs the level recorded on the window's first day, and a
    fresh scenario had none, so depth stayed at 40% for the rest of the run
    and nothing raised."""
    control, stress = forked()
    stress.apply(tf.Scenario(name="thin").shock(
        "market.liquidity", operation="multiply", value=0.4, at=1,
        duration=4))
    # Days 3-7 cover the window (days 4-7); the release is due on day 8.
    stress.run(days=5)
    stress.run(days=2)
    control.run(days=7)
    assert stress.engine.column("avg_volume") == \
        control.engine.column("avg_volume")


def test_a_fork_inside_a_window_continues_it():
    _c, stress = forked()
    stress.apply(thin())
    stress.run(days=3)
    straight, = stress.fork("straight")
    branched, = stress.fork("branched")
    straight.run(days=6)
    branched.run(days=2)
    branched.run(days=4)
    assert branched.digest() == straight.digest()
    def trail(world):
        # Without the label, which is the arm's own name.
        return [{k: v for k, v in f.as_dict().items() if k != "scenario"}
                for f in world.firings]

    assert trail(branched) == trail(straight)
    # The firings before the fork are part of the arm's history.
    assert branched.firings[0].day == HISTORY + 1


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [-1, 1.5, True, "0"])
def test_a_start_that_is_not_a_day_is_refused(bad):
    _control, stress = forked()
    with pytest.raises(tf.ValidationError, match="at"):
        stress.apply(tf.Scenario.load(PACKAGED), at=bad)
    with pytest.raises(tf.ValidationError):
        tf.Scenario.load(PACKAGED).starting_at(bad)
    assert stress.applied == []


def test_a_scenario_with_pins_cannot_be_started_later():
    """A pin is a whole path from day zero, so "start it on day 5" has no
    meaning for one, and a world's pins belong to the world."""
    scenario = (tf.Scenario(name="mixed").hold(vix=20.0)
                .shock("macro.vix", operation="multiply", value=2.0, at=3))
    with pytest.raises(tf.ValidationError, match="pin"):
        scenario.starting_at(0)
    _control, stress = forked()
    with pytest.raises(tf.ValidationError, match="pin"):
        stress.apply(scenario)
    assert stress.applied == []


def test_a_scenario_with_nothing_to_start_is_refused():
    with pytest.raises(tf.ValidationError, match="no interventions"):
        tf.Scenario(name="empty").starting_at(0)


# ---------------------------------------------------------------------------
# The command line
# ---------------------------------------------------------------------------

def test_scenario_show_takes_a_start(capsys):
    assert main(["scenario", "show", PACKAGED, "--start", "0"]) == 0
    out = capsys.readouterr().out
    packaged = tf.Scenario.load(PACKAGED)
    assert packaged.starting_at(0).fingerprint in out
    assert packaged.fingerprint in out
    assert "day 0-24" in out and "day 42..125 ramp" in out

    assert main(["scenario", "show", PACKAGED]) == 0
    assert "day 50-74" in capsys.readouterr().out

    assert main(["scenario", "show", PACKAGED, "--start", "-1"]) == 1
    assert "negative" in capsys.readouterr().out
