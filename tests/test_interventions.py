"""The scenario/intervention framework, from the YAML file to the fork.

`test_scenario.py` covers the macro-path half of `Scenario` -- pins, ramps,
serialised paths -- and every test there still passes unchanged. This module
covers the other half: a named collection of explicit interventions, read from
a file or written in Python, applied to a run, recorded in a manifest and
carried across a checkpoint.

The order below is the order a user meets them: the domain model, then the
document, then the registry, then application, then the reproducibility
guarantees, then the CLI.

## The guarantees these tests exist to hold

1. **A scenario is data.** The same experiment written in YAML and in Python
   resolves to the same object and the same fingerprint, and neither can
   reach anything the registry does not name.
2. **A relative operation does not compound.** A `hold` multiplying by 2.0 for
   twenty-five days means twice the level it found on its first day, held --
   not 2^25.
3. **A fork is independent.** Applying a scenario to one branch changes
   nothing about another, including the scenario's own recorded trail.
4. **A pending intervention survives a checkpoint**, because the writes it
   makes are recorded inputs and a replay re-executes them.
5. **The resolved scenario travels**, so a manifest replays the experiment
   after the YAML file is edited or deleted.
"""

import json
import pathlib
import struct
import subprocess
import sys

import pytest

import tradefloor as tf
from tradefloor.interventions import (
    TARGETS, UNSUPPORTED, Intervention, ScenarioValidationError,
)
from tradefloor.scenario import Scenario

REPO = pathlib.Path(__file__).resolve().parent.parent
SCENARIOS = REPO / "python" / "tradefloor" / "scenarios"

UNIVERSE = tf.Universe.random(10, seed=20260829)
SEED = 4242
TICKS = 26


def run(scenario, *, days=30, seed=SEED, universe=None):
    return tf.run_scenario(scenario, seed=seed,
                           universe=universe or list(UNIVERSE),
                           days=days, ticks_per_day=TICKS)


def prices(engine):
    return struct.unpack("<%dd" % len(engine.tickers), engine.prices())


def liquidity_scenario(*, at=5, duration=10, factor=0.4):
    return Scenario(name="thin").shock(
        "market.liquidity", operation="multiply", value=factor, at=at,
        duration=duration)


# ---------------------------------------------------------------------------
# The domain model
# ---------------------------------------------------------------------------


def test_an_intervention_carries_its_defaults():
    item = Intervention("macro.vix", operation="multiply", value=2.0, at=10)
    assert item.shape == "impulse"
    assert item.duration is None
    assert item.role == "shock"
    assert item.as_dict() == {
        "target": "macro.vix", "operation": "multiply", "value": 2.0,
        "at": 10, "duration": None, "shape": "impulse",
    }


def test_a_duration_makes_it_a_hold_without_being_told():
    item = Intervention("macro.vix", value=2.0, at=10, duration=5)
    assert item.shape == "hold"
    assert item.last_day == 14
    assert item.active_on(10) and item.active_on(14)
    assert not item.active_on(9) and not item.active_on(15)


def test_a_permanent_has_no_last_day():
    item = Intervention("macro.vix", value=2.0, at=10, shape="permanent")
    assert item.last_day is None
    assert item.active_on(10) and item.active_on(10_000)
    assert not item.active_on(9)


def test_a_one_day_ramp_is_an_impulse_by_construction():
    ramp = Intervention("macro.vix", value=2.0, at=3, duration=1, shape="ramp")
    assert ramp.active_on(3) and not ramp.active_on(4)


@pytest.mark.parametrize("kwargs,fragment", [
    (dict(target="macro.interest", value=1.0), "macro.policy_rate"),
    (dict(target="market.volatility", value=1.0), "macro.vix"),
    (dict(target="sector.energy.earnings", value=1.0), "per-sector"),
    (dict(target="simulator.internal.foo.bar", value=1.0), "Supported targets"),
    (dict(target="macro.vix", operation="divide", value=2.0), "vocabulary"),
    (dict(target="macro.vix", value=2.0, shape="temporary", duration=3), "hold"),
    (dict(target="macro.vix", value=2.0, shape="ramp"), "needs a duration"),
    (dict(target="macro.vix", value=2.0, shape="permanent", duration=3),
     "takes no duration"),
    (dict(target="macro.vix", value=2.0, at=-1), "cannot be negative"),
    (dict(target="macro.vix", value=2.0, at=1.5), "whole number"),
    (dict(target="macro.vix", value=2.0, duration=0), "at least one day"),
    (dict(target="macro.vix", operation="multiply", value=-1.0), "multiplier"),
    (dict(target="macro.vix", value=float("nan")), "finite"),
    (dict(target="macro.policy_rate", operation="set", value=5.0), "FRACTIONS"),
    (dict(target="macro.policy_rate", operation="add", value=2.0), "FRACTIONS"),
    (dict(target="macro.cycle", operation="add", value=1.0), "NAME"),
    (dict(target="macro.cycle", operation="set", value="slump"), "cycle phase"),
    (dict(target="macro.cycle", operation="set", value="contraction",
          duration=3, shape="ramp"), "cannot ramp"),
    (dict(target="macro.vix"), "needs a value"),
])
def test_a_bad_intervention_is_refused_by_name(kwargs, fragment):
    with pytest.raises(ScenarioValidationError) as exc:
        Intervention(**kwargs)
    assert fragment in str(exc.value)


def test_an_unknown_target_suggests_the_near_miss():
    with pytest.raises(ScenarioValidationError) as exc:
        Intervention("macro.polcy_rate", value=0.01)
    assert "Did you mean" in str(exc.value)
    assert "macro.policy_rate" in str(exc.value)


def test_every_unsupported_name_explains_itself_and_is_not_registered():
    for name, reason in UNSUPPORTED.items():
        assert name not in TARGETS, f"{name} is both unsupported and registered"
        assert len(reason) > 20, f"{name} has no explanation"
        with pytest.raises(ScenarioValidationError) as exc:
            Intervention(name, value=1.0)
        assert reason.split(".")[0][:24] in str(exc.value)


def test_the_registry_docstring_counts_the_registry():
    """A count in prose is a second place to remember.

    This exact defect was already live one module away: `test_mcp.py`
    asserted the pinnable macro field list was seven, and the assertion
    outlived the fact. A number written in a docstring cannot be asserted
    against itself, so it is asserted against the thing it describes.
    """
    import re

    from tradefloor import interventions

    words = {"ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
             "fourteen": 14, "fifteen": 15}
    source = pathlib.Path(interventions.__file__).read_text(encoding="utf-8")
    claims = re.findall(r"of the (\w+) targets|list of (\w+) names", source)
    found = [w for pair in claims for w in pair if w]
    assert found, "the registry docstring no longer states a count"
    for word in found:
        assert words.get(word) == len(TARGETS), (word, len(TARGETS))


def test_only_a_numeric_target_can_ramp():
    """A ramp interpolates, and one target's value is a NAME.

    `macro.cycle` set to "contraction" with shape="ramp" used to raise a bare
    TypeError from inside the run -- `unsupported operand type(s) for -:
    'str' and 'str'` -- on whichever day the ramp reached its second step.
    """
    numeric = [name for name, t in TARGETS.items() if t.numeric]
    assert len(numeric) == len(TARGETS) - 1
    for name in numeric:
        Intervention(name, operation="multiply", value=1.5, at=1, duration=3,
                     shape="ramp")


def test_every_registered_target_reads_and_writes_the_engine():
    """The registry's own claim, checked rather than trusted.

    A target whose `read` raised, or whose `write` did not change what `read`
    returns, would be a scenario entry that runs and measures nothing -- the
    exact failure the registry exists to prevent.
    """
    engine = tf.Engine(seed=1, universe=list(UNIVERSE))
    for name, target in sorted(TARGETS.items()):
        before = target.read(engine)
        if name == "macro.cycle":
            target.write(engine, "contraction")
            assert target.read(engine) == "contraction"
            continue
        if isinstance(before, tuple):
            after = tuple(v * 0.5 for v in before)
        else:
            after = (before * 0.5) if before else 1.0
        target.write(engine, after)
        assert target.read(engine) == pytest.approx(after), name


# ---------------------------------------------------------------------------
# The document
# ---------------------------------------------------------------------------


YAML = """
version: 1

scenario:
  name: probe
  description: >
    Example experimental assumptions.

  shocks:
    - target: macro.vix
      operation: multiply
      value: 2.0
      at: 5
      duration: 10

  transmission:
    - target: macro.inflation
      operation: add
      value: 0.015
      at: 8
"""


def python_twin():
    return (Scenario(name="probe",
                     description="Example experimental assumptions.")
            .shock("macro.vix", operation="multiply", value=2.0, at=5,
                   duration=10)
            .assume("macro.inflation", operation="add", value=0.015, at=8))


def test_yaml_and_python_resolve_to_the_same_scenario():
    from_yaml = Scenario.from_yaml(YAML)
    assert from_yaml.interventions == python_twin().interventions
    assert from_yaml.fingerprint == python_twin().fingerprint


def test_the_fingerprint_is_stable_across_formatting():
    reordered = YAML.replace("      operation: multiply\n", "").replace(
        "    - target: macro.vix\n",
        "    - target: macro.vix\n      operation: multiply\n")
    commented = YAML.replace("scenario:", "# a comment\nscenario:")
    assert Scenario.from_yaml(reordered).fingerprint == \
        Scenario.from_yaml(YAML).fingerprint
    assert Scenario.from_yaml(commented).fingerprint == \
        Scenario.from_yaml(YAML).fingerprint


def test_the_fingerprint_moves_when_the_experiment_does():
    """Every edit below MUST move the hash, and every one must be reached.

    An earlier version of this caught a `ScenarioValidationError` and
    `continue`d, so a case that stopped parsing stopped being checked and the
    test still passed. A skip and a pass are the same colour.
    """
    base = Scenario.from_yaml(YAML)
    edits = {
        "value": YAML.replace("value: 2.0", "value: 2.5"),
        "at": YAML.replace("at: 5", "at: 6"),
        "duration": YAML.replace("duration: 10", "duration: 11"),
        "name": YAML.replace("name: probe", "name: probe2"),
        "operation": YAML.replace("operation: multiply", "operation: set"),
        "description": YAML.replace("Example experimental assumptions.",
                                    "Different assumptions."),
    }
    for label, text in edits.items():
        changed = Scenario.from_yaml(text)
        assert changed.fingerprint != base.fingerprint, label

    # And the role an intervention is filed under is part of the experiment,
    # which YAML cannot express as an edit to one line.
    same_values = Scenario(name=base.name, description=base.description)
    for item in base.interventions:
        same_values.intervene(Intervention(
            item.target, operation=item.operation, value=item.value,
            at=item.at, duration=item.duration, shape=item.shape,
            role="shock"))
    assert same_values.fingerprint != base.fingerprint


def test_declared_order_is_part_of_the_experiment():
    a = (Scenario(name="o").shock("macro.vix", value=2.0, at=1)
         .shock("macro.vix", operation="add", value=5.0, at=1))
    b = (Scenario(name="o").shock("macro.vix", operation="add", value=5.0, at=1)
         .shock("macro.vix", value=2.0, at=1))
    assert a.fingerprint != b.fingerprint


@pytest.mark.parametrize("text,fragment", [
    ("scenario:\n  name: x\n", "no `version`"),
    ("version: 2\nscenario:\n  name: x\n", "not one this build reads"),
    ("version: 1\n", "no `scenario:` block"),
    ("version: 1\nextra: 1\nscenario:\n  name: x\n", "unknown top-level"),
    ("version: 1\nscenario:\n  shocks:\n    - target: macro.vix\n"
     "      value: 2.0\n", "needs a `name`"),
    ("version: 1\nscenario:\n  name: x\n  interventions:\n"
     "    - target: macro.vix\n", "shocks:"),
    ("version: 1\nscenario:\n  name: x\n", "declares no interventions"),
    ("version: 1\nscenario:\n  name: x\n  shocks:\n"
     "    - target: macro.vix\n      value: 2.0\n      when: 5\n", "unknown key"),
    ("version: 1\nscenario:\n  name: x\n  shocks:\n"
     "    - value: 2.0\n", "names no target"),
    ("version: 1\nscenario:\n  name: x\n  shocks: 3\n", "must be a list"),
], ids=["no-version", "future-version", "no-block", "extra-top",
        "no-name", "interventions-key", "empty", "unknown-key",
        "no-target", "not-a-list"])
def test_a_bad_document_is_refused_by_name(text, fragment):
    with pytest.raises(ScenarioValidationError) as exc:
        Scenario.from_yaml(text)
    assert fragment in str(exc.value)


def test_absolute_timing_is_refused_rather_than_read_as_relative():
    text = ("version: 1\nscenario:\n  name: x\n  shocks:\n"
            "    - target: macro.vix\n      value: 2.0\n      at:\n"
            "        absolute: 55\n")
    with pytest.raises(ScenarioValidationError) as exc:
        Scenario.from_yaml(text)
    assert "RELATIVE" in str(exc.value)


def test_relative_timing_spelled_out_is_the_shorthand():
    long = ("version: 1\nscenario:\n  name: x\n  shocks:\n"
            "    - target: macro.vix\n      value: 2.0\n      at:\n"
            "        relative: 5\n")
    short = ("version: 1\nscenario:\n  name: x\n  shocks:\n"
             "    - target: macro.vix\n      value: 2.0\n      at: 5\n")
    assert Scenario.from_yaml(long).fingerprint == \
        Scenario.from_yaml(short).fingerprint


def test_yaml_cannot_construct_a_python_object():
    hostile = ("version: 1\nscenario:\n  name: x\n  shocks:\n"
               "    - target: !!python/object/apply:os.system ['echo pwned']\n")
    with pytest.raises(tf.ValidationError) as exc:
        Scenario.from_yaml(hostile)
    assert "tag" in str(exc.value)


# ---------------------------------------------------------------------------
# Applying it
# ---------------------------------------------------------------------------


def test_an_impulse_writes_once_and_the_chain_carries_it():
    scenario = Scenario(name="one").shock(
        "macro.vix", operation="multiply", value=2.0, at=3)
    run(scenario, days=8)
    assert [f.day for f in scenario.log] == [3]
    firing = scenario.log[0]
    assert firing.new == pytest.approx(firing.previous * 2.0)


def test_a_hold_does_not_compound():
    """The defect this shape exists to prevent.

    Recomputing `multiply` from the live value every day would give 2^n, and
    a twenty-five day crisis would end at a VIX of half a billion. The target
    is anchored to the level the field had on the hold's first day.
    """
    scenario = Scenario(name="held").shock(
        "macro.vix", operation="multiply", value=2.0, at=3, duration=6)
    run(scenario, days=12)
    written = [f.new for f in scenario.log]
    assert len(written) == 6
    assert written == [pytest.approx(written[0])] * 6
    assert written[0] == pytest.approx(scenario.log[0].previous * 2.0)


def test_a_hold_on_liquidity_puts_the_depth_back():
    """The defect this restore exists to close.

    Nothing in the engine writes `avg_volume` -- the shipped close policy is
    Hold -- so "release" could not mean "stop writing". A five-day hold
    thinned the book on day 2 and the book was still thin on day 14, under a
    scenario whose own description said the window ended on day 7.
    """
    engine = tf.Engine(seed=SEED, universe=list(UNIVERSE))
    before = engine.column("avg_volume")

    scenario = liquidity_scenario(at=2, duration=5, factor=0.25)
    inside = None
    for day in range(14):
        scenario.apply(engine, day)
        engine.open_market()
        engine.run_session(9, 30, 3, TICKS)
        engine.close_market()
        if day == 4:
            inside = engine.column("avg_volume")

    assert inside is not None and inside != before
    assert engine.column("avg_volume") == before
    releases = [f for f in scenario.log if f.operation == "release"]
    assert [f.day for f in releases] == [7]
    assert releases[0].new == pytest.approx(sum(
        struct.unpack("<%dd" % len(UNIVERSE), before)))


def test_overlapping_holds_restore_the_level_from_before_either_began():
    """The release is per TARGET, and this is why.

    Holds on days 1-4 and 3-6. Restoring what each one personally found puts
    back full depth on day 5 and then HALF depth on day 7 -- because half is
    what the second found while the first was running. The run ends thinned,
    with nothing raised.
    """
    scenario = (Scenario(name="two")
                .shock("market.liquidity", operation="multiply", value=0.5,
                       at=1, duration=4)
                .shock("market.liquidity", operation="multiply", value=0.5,
                       at=3, duration=4))
    engine = tf.Engine(seed=SEED, universe=list(UNIVERSE))
    before = engine.column("avg_volume")
    for day in range(12):
        scenario.apply(engine, day)
        engine.open_market()
        engine.run_session(9, 30, 3, TICKS)
        engine.close_market()

    releases = [f for f in scenario.log if f.operation == "release"]
    assert [f.day for f in releases] == [7], "one release, when the LAST ends"
    assert engine.column("avg_volume") == before


def test_a_release_is_a_logged_input_so_a_replay_reproduces_it():
    """A restore that a replay did not carry would be a fork of the market."""
    scenario = liquidity_scenario(at=2, duration=4, factor=0.3)
    engine = tf.Engine(seed=SEED, universe=list(UNIVERSE))
    for day in range(12):
        scenario.apply(engine, day)
        engine.open_market()
        engine.run_session(9, 30, 3, TICKS)
        engine.record(day)
        engine.close_market()

    writes = [e for e in engine.order_log if e["op"] == "set_avg_volume"]
    assert len(writes) == 5, "four inside the window and one release"
    resumed = tf.Checkpoint.of(engine, universe=list(UNIVERSE),
                               seed=SEED).resume()
    assert prices(resumed) == prices(engine)
    assert resumed.column("avg_volume") == engine.column("avg_volume")


def test_a_hold_on_a_self_restoring_target_writes_no_release():
    """A macro field needs no help: the chain moves it back on its own.

    Writing a restore there would be the library overriding the endogenous
    dynamics it exists to run.
    """
    scenario = Scenario(name="v").shock(
        "macro.vix", operation="multiply", value=2.0, at=2, duration=4)
    run(scenario, days=12)
    assert not [f for f in scenario.log if f.operation == "release"]


def test_a_relative_operation_cannot_write_a_value_the_target_cannot_mean():
    """`check` sees the multiplier; only the run sees the result.

    `add -500` on macro.vix wrote a VIX of -485 and the market traded a
    session against it, because (vix/15)^2 squares the sign away and nothing
    else looked. The result is now checked on the day it is written.
    """
    cases = [
        (dict(target="macro.vix", operation="add", value=-500.0),
         "positive VIX level"),
        (dict(target="macro.fear_greed", operation="multiply", value=1000.0),
         "sentiment range"),
        (dict(target="macro.policy_rate", operation="multiply", value=100.0),
         "plausible rate band"),
        (dict(target="market.liquidity", operation="add", value=-1e12),
         "positive share count"),
    ]
    for kwargs, fragment in cases:
        scenario = Scenario(name="bad").shock(at=1, **kwargs)
        with pytest.raises(ScenarioValidationError) as exc:
            run(scenario, days=4)
        message = str(exc.value)
        assert fragment in message, (kwargs, message)
        # The message has to name the live value it started from, or the
        # reader cannot tell a bad multiplier from a market that moved.
        assert "applied to" in message and "day 1" in message


def test_a_neutral_value_is_settable():
    """Both of these were refused, and both are the field's own default.

    `qe_pe_boost` opens at 0.0 and `fear_greed` runs from 0, so a check that
    demanded a positive `set` refused turning the boost off and refused the
    most extreme fear the index can express.
    """
    Intervention("macro.qe_pe_boost", operation="set", value=0.0, at=1)
    Intervention("macro.fear_greed", operation="set", value=0.0, at=1)
    with pytest.raises(ScenarioValidationError):
        Intervention("macro.fear_greed", operation="set", value=140.0, at=1)


def test_a_hold_releases_the_field_when_it_ends():
    scenario = Scenario(name="held").shock(
        "macro.vix", operation="multiply", value=3.0, at=2, duration=3)
    engine = run(scenario, days=10)
    assert [f.day for f in scenario.log] == [2, 3, 4]
    # Five days after release the chain has moved it off the held level.
    assert engine.macro_fields["vix"] != pytest.approx(scenario.log[-1].new)


def test_a_ramp_reaches_its_target_on_its_last_active_day():
    scenario = Scenario(name="ramped").shock(
        "macro.vix", operation="multiply", value=2.0, at=2, duration=4,
        shape="ramp")
    run(scenario, days=10)
    written = [f.new for f in scenario.log]
    start = scenario.log[0].previous
    assert len(written) == 4
    assert written[-1] == pytest.approx(start * 2.0)
    assert written == sorted(written)


def test_a_permanent_writes_every_day_to_the_end():
    scenario = Scenario(name="forever").shock(
        "macro.corporate_yield", operation="add", value=0.02, at=4,
        shape="permanent")
    run(scenario, days=12)
    assert [f.day for f in scenario.log] == list(range(4, 12))


def test_shocks_fire_before_transmission_whatever_the_declared_order():
    scenario = (Scenario(name="order")
                .assume("macro.vix", operation="add", value=1.0, at=2)
                .shock("macro.vix", operation="multiply", value=2.0, at=2))
    run(scenario, days=5)
    assert [f.role for f in scenario.log] == ["shock", "transmission"]
    shock, assumption = scenario.log
    assert assumption.previous == pytest.approx(shock.new)


def test_two_interventions_on_one_target_compose_in_declared_order():
    scenario = (Scenario(name="compose")
                .shock("macro.vix", operation="multiply", value=2.0, at=2)
                .shock("macro.vix", operation="add", value=5.0, at=2))
    run(scenario, days=5)
    first, second = scenario.log
    assert second.previous == pytest.approx(first.new)
    assert second.new == pytest.approx(first.new + 5.0)


def test_the_audit_trail_carries_real_values_not_the_recipe():
    scenario = Scenario(name="oil").shock(
        "commodity.oil", operation="multiply", value=1.40, at=3)
    run(scenario, days=6)
    entry = scenario.log[0].as_dict()
    assert entry["target"] == "commodity.oil"
    assert entry["operation"] == "multiply"
    assert entry["value"] == 1.40
    assert entry["new"] == pytest.approx(entry["previous"] * 1.40)
    assert entry["previous"] != 1.40
    assert json.loads(json.dumps(entry)) == entry


def test_a_liquidity_intervention_thins_the_book_and_nothing_else_does():
    thin = liquidity_scenario(at=2, duration=6, factor=0.4)
    control = thin.without_interventions()
    a, b = run(thin, days=5), run(control, days=5)
    before = struct.unpack("<%dd" % len(UNIVERSE), b.column("avg_volume"))
    after = struct.unpack("<%dd" % len(UNIVERSE), a.column("avg_volume"))
    assert after == pytest.approx(tuple(v * 0.4 for v in before))


def test_a_scenario_that_never_fires_is_refused_rather_than_reported_as_zero():
    scenario = Scenario(name="late").shock("macro.vix", value=2.0, at=99)
    with pytest.raises(tf.ValidationError) as exc:
        tf.scenario.compare(scenario, seed=SEED, universe=list(UNIVERSE),
                            days=10)
    assert "no intervention" in str(exc.value)


def test_compare_differences_the_scenario_against_itself_without_it():
    scenario = Scenario(name="rates").shock(
        "macro.corporate_yield", operation="add", value=0.02, at=2,
        shape="permanent")
    result = tf.scenario.compare(scenario, seed=SEED, universe=list(UNIVERSE),
                                 days=25, ticks_per_day=TICKS)
    assert result["exact"] is True
    assert result["scenario_fingerprint"] == scenario.fingerprint
    assert result["interventions"], "the firing trail should reach the result"
    assert result["median_pct"] < 0


def test_the_divergence_trace_lands_on_the_day_the_shock_fires():
    """The check that a scenario did not leak before it said it did.

    The baseline is the same world without the interventions, so the two
    markets are bit-identical until the first one fires. `first_divergence`
    earlier than `at` would mean the scenario reached the market early;
    later would mean it fired into a market that did not notice.
    """
    for at in (2, 7, 13):
        scenario = Scenario(name="rates").shock(
            "macro.corporate_yield", operation="add", value=0.02, at=at,
            shape="permanent")
        result = tf.scenario.compare(scenario, seed=SEED,
                                     universe=list(UNIVERSE), days=20,
                                     ticks_per_day=TICKS, trace=True)
        assert result["first_divergence"] == at


def test_tracing_does_not_change_the_comparison():
    """Two day loops, and the trace is only worth having if they are one.

    `_run_in_lockstep` is a second copy of `run_scenario`'s loop, which is
    exactly the kind of duplication that drifts. If it ever applies the
    scenario at a different point, or records on a different day, this fails.
    """
    scenario = Scenario.from_yaml(YAML)
    args = dict(seed=SEED, universe=list(UNIVERSE), days=25,
                ticks_per_day=TICKS)
    plain = tf.scenario.compare(scenario, **args)
    traced = tf.scenario.compare(scenario, trace=True, **args)
    assert plain["first_divergence"] is None
    assert traced["first_divergence"] is not None
    for key in plain:
        if key != "first_divergence":
            assert plain[key] == traced[key], key


# ---------------------------------------------------------------------------
# Reproducibility
# ---------------------------------------------------------------------------


def test_same_seed_same_scenario_reproduces_exactly():
    def once():
        scenario = Scenario.from_yaml(YAML)
        engine = run(scenario, days=20)
        return prices(engine), [f.as_dict() for f in scenario.log]

    first, second = once(), once()
    assert first == second


def test_a_yaml_scenario_and_its_python_twin_produce_the_same_market():
    a = run(Scenario.from_yaml(YAML), days=20)
    b = run(python_twin(), days=20)
    assert prices(a) == prices(b)


def test_applying_a_scenario_to_one_fork_does_not_touch_another():
    """The strongest fork guarantee, and the one worth being explicit about.

    Not "the two branches differ afterwards" -- that would pass on a fork
    that shared nothing but also did nothing. The control branch must be
    BIT-IDENTICAL to a branch that was never forked from, and the scenario
    must not have written a single value into it.
    """
    engine = tf.Engine(seed=SEED, universe=list(UNIVERSE))
    for _ in range(10):
        engine.open_market()
        engine.run_session(9, 30, 3, TICKS)
        engine.close_market()

    control, stress = tf.branch(engine, 2)
    lonely, = tf.branch(engine, 1)

    scenario = liquidity_scenario(at=0, duration=5)
    inside_window = None
    for day in range(10):
        scenario.apply(stress, day)
        for branch in (control, stress, lonely):
            branch.open_market()
            branch.run_session(9, 30, 3, TICKS)
            branch.close_market()
        if day == 3:
            inside_window = (stress.column("avg_volume"),
                             control.column("avg_volume"))

    assert prices(control) == prices(lonely)
    assert prices(stress) != prices(control)
    assert control.column("avg_volume") == lonely.column("avg_volume")
    # Thin INSIDE the window. Asserting this at the end of the run instead is
    # what an earlier version of this test did, and it passed for the wrong
    # reason: the column was never restored, so it read as different because
    # of a defect rather than because of the shock.
    assert inside_window is not None and inside_window[0] != inside_window[1]
    # And back afterwards -- the window was days 0-4, and this is day 9.
    assert stress.column("avg_volume") == control.column("avg_volume")
    # And the scenario's own trail belongs to the branch it was applied to.
    assert {f.target for f in scenario.log} == {"market.liquidity"}


def test_a_run_that_joins_a_hold_part_way_through_is_refused():
    """The reachable version of the anchor mistake.

    A hold is anchored to the level its target had on its first day. A run
    that starts after that day has nothing to anchor to, and anchoring it to
    whatever the field happens to hold would silently run a different
    experiment.
    """
    engine = tf.Engine(seed=1, universe=list(UNIVERSE))
    scenario = Scenario(name="held").shock(
        "macro.vix", operation="multiply", value=2.0, at=3, duration=5)
    with pytest.raises(ScenarioValidationError) as exc:
        scenario.apply(engine, 5)
    assert "never applied day 3" in str(exc.value)


def test_a_second_run_starts_a_fresh_trail():
    """The clock, not the engine, is what identifies a run."""
    scenario = Scenario(name="one").shock(
        "macro.vix", operation="multiply", value=2.0, at=2)
    first = run(scenario, days=6)
    trail = [f.as_dict() for f in scenario.log]
    second = run(scenario, days=6)
    assert [f.as_dict() for f in scenario.log] == trail
    assert prices(first) == prices(second)


def test_copy_is_what_makes_two_runs_from_one_recipe_work():
    a = tf.Engine(seed=1, universe=list(UNIVERSE))
    b = tf.Engine(seed=1, universe=list(UNIVERSE))
    recipe = Scenario(name="held").shock(
        "macro.vix", operation="multiply", value=2.0, at=0, duration=3)
    left, right = recipe.copy(), recipe.copy()
    for day in range(3):
        left.apply(a, day)
        right.apply(b, day)
    assert [f.as_dict() for f in left.log] == [f.as_dict() for f in right.log]


def test_a_checkpoint_replays_a_pending_intervention():
    """An intervention is an INPUT, so the order log carries it.

    A checkpoint taken mid-window and resumed must reproduce the writes the
    original made, without the scenario object being present at all -- which
    is the whole reason `set_avg_volume` and `pin_macro` are logged.
    """
    scenario = liquidity_scenario(at=3, duration=6)
    engine = tf.Engine(seed=SEED, universe=list(UNIVERSE))
    for day in range(6):
        scenario.apply(engine, day)
        engine.open_market()
        engine.run_session(9, 30, 3, TICKS)
        engine.close_market()

    mark = tf.Checkpoint.of(engine, universe=list(UNIVERSE), seed=SEED)
    assert any(entry["op"] == "set_avg_volume" for entry in mark.log)

    resumed = mark.resume()
    assert prices(resumed) == prices(engine)
    assert resumed.column("avg_volume") == engine.column("avg_volume")

    # And the checkpoint survives a round trip through JSON.
    reread = tf.Checkpoint.from_json(mark.to_json()).resume()
    assert prices(reread) == prices(engine)


def test_a_checkpoint_and_an_uninterrupted_run_agree_to_the_last_day():
    scenario = Scenario(name="rates").shock(
        "macro.corporate_yield", operation="add", value=0.02, at=2,
        shape="permanent")

    straight = tf.Engine(seed=SEED, universe=list(UNIVERSE))
    for day in range(12):
        scenario.apply(straight, day)
        straight.open_market()
        straight.run_session(9, 30, 3, TICKS)
        straight.close_market()

    twin = Scenario(name="rates").shock(
        "macro.corporate_yield", operation="add", value=0.02, at=2,
        shape="permanent")
    part = tf.Engine(seed=SEED, universe=list(UNIVERSE))
    for day in range(6):
        twin.apply(part, day)
        part.open_market()
        part.run_session(9, 30, 3, TICKS)
        part.close_market()
    resumed = tf.Checkpoint.of(part, universe=list(UNIVERSE),
                               seed=SEED).resume()
    for day in range(6, 12):
        twin.apply(resumed, day)
        resumed.open_market()
        resumed.run_session(9, 30, 3, TICKS)
        resumed.close_market()

    assert prices(resumed) == prices(straight)


def test_the_manifest_records_the_resolved_scenario_not_the_filename():
    scenario = Scenario.load("liquidity_crisis")
    engine = tf.Engine(seed=SEED, universe=list(UNIVERSE))
    for day in range(60):
        scenario.apply(engine, day)
        engine.open_market()
        engine.run_session(9, 30, 3, TICKS)
        engine.record(day)
        engine.close_market()

    manifest = tf.RunManifest.of(engine, seed=SEED, universe=list(UNIVERSE),
                                 scenario=scenario)
    doc = json.loads(manifest.to_json())["scenario"]
    assert doc["name"] == "liquidity_crisis"
    assert doc["fingerprint"] == scenario.fingerprint
    assert doc["source"] == "liquidity_crisis.yml"
    assert [s["target"] for s in doc["shocks"]] == [
        "market.liquidity", "macro.vix"]
    assert doc["transmission"][0]["target"] == "macro.corporate_yield"

    # It survives the round trip, and it reproduces.
    back = tf.RunManifest.from_json(manifest.to_json())
    assert back.scenario.fingerprint == scenario.fingerprint
    assert back.scenario.interventions == scenario.interventions
    assert prices(back.reproduce()) == prices(engine)


def test_an_edited_manifest_scenario_is_refused():
    scenario = Scenario.from_yaml(YAML)
    doc = json.loads(scenario.to_json())
    doc["shocks"][0]["value"] = 9.0
    with pytest.raises(tf.ValidationError) as exc:
        Scenario.from_json(json.dumps(doc))
    assert "disagree" in str(exc.value)


def test_a_pins_only_scenario_still_serialises_as_schema_one():
    """The compatibility promise: nothing already published moves."""
    old = Scenario.rate_shock(start=0.025, end=0.05, over=10)
    doc = json.loads(old.to_json(20))
    assert doc["schema"] == 1
    assert set(doc) == {"schema", "label", "days", "path"}
    assert Scenario.from_json(old.to_json(20)).table(20) == old.table(20)


def test_a_scenario_with_both_pins_and_interventions_round_trips():
    mixed = (Scenario(name="both").hold(vix=15.0)
             .shock("macro.vix", operation="multiply", value=2.0, at=3))
    doc = json.loads(mixed.to_json(10))
    assert doc["schema"] == 2
    assert doc["path"][0]["vix"] == 15.0
    back = Scenario.from_json(mixed.to_json(10))
    assert back.interventions == mixed.interventions
    assert back.table(10) == mixed.table(10)


def test_a_future_schema_is_refused_rather_than_read_partially():
    doc = json.loads(Scenario.from_yaml(YAML).to_json())
    doc["schema"] = 99
    with pytest.raises(tf.ValidationError) as exc:
        Scenario.from_json(json.dumps(doc))
    assert "newer than this version" in str(exc.value)


def test_evaluate_takes_a_scenario_and_scores_under_it():
    spec = tf.StrategySpec.momentum(lookback_days=1.0, top_k=3)
    thin = liquidity_scenario(at=0, duration=20, factor=0.3)
    plain = tf.evaluate({"m": spec}, seed=SEED, universe=list(UNIVERSE),
                        days=6)["m"]
    shocked = tf.evaluate({"m": spec}, seed=SEED, universe=list(UNIVERSE),
                          days=6, scenario=thin)["m"]
    assert shocked.impact_bps != plain.impact_bps
    assert thin.log, "the scenario should have fired inside evaluate"


# ---------------------------------------------------------------------------
# The shipped scenario pack
# ---------------------------------------------------------------------------


SHIPPED = sorted(SCENARIOS.glob("*.yml"))


def test_the_pack_is_not_empty():
    assert len(SHIPPED) >= 3


@pytest.mark.parametrize("path", SHIPPED, ids=lambda p: p.stem)
def test_every_shipped_scenario_loads_and_runs(path):
    scenario = Scenario.from_yaml(str(path))
    assert scenario.name == path.stem
    assert scenario.interventions
    assert scenario.fingerprint.startswith("sha256:")
    engine = run(scenario, days=60)
    assert scenario.log, f"{path.name} fired nothing in sixty days"
    assert len(engine.tickers) == len(UNIVERSE)


@pytest.mark.parametrize("path", SHIPPED, ids=lambda p: p.stem)
def test_every_shipped_scenario_says_it_is_not_a_forecast(path):
    """Section 41 of the brief, as a test rather than as a convention."""
    text = path.read_text(encoding="utf-8").lower()
    assert "not a forecast" in text
    assert "example experimental assumptions" in text


@pytest.mark.parametrize("path", SHIPPED, ids=lambda p: p.stem)
def test_no_shipped_scenario_names_a_political_actor(path):
    text = path.read_text(encoding="utf-8").lower()
    # Named actors and party labels, not the words "election" or "candidate"
    # -- policy_regime_shift.yml uses both, in a comment explaining why it
    # contains neither a person nor a party.
    for banned in ("trump", "biden", "obama", "republican", "democrat",
                   "labour", "tory", "president "):
        assert banned not in text, f"{path.name} names {banned!r}"


# ---------------------------------------------------------------------------
# The command line
# ---------------------------------------------------------------------------


def cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "tradefloor", *args],
        capture_output=True, text=True, cwd=str(REPO),
    )


def test_validate_reports_the_fingerprint_and_exits_zero():
    result = cli("scenario", "validate", "liquidity_crisis")
    assert result.returncode == 0, result.stderr
    assert "Scenario valid." in result.stdout
    assert Scenario.load("liquidity_crisis").fingerprint in result.stdout


def test_validate_accepts_the_whole_pack_at_once():
    result = cli("scenario", "validate", *[p.stem for p in SHIPPED])
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count("Scenario valid.") == len(SHIPPED)


def test_validate_fails_loudly_on_a_bad_file(tmp_path):
    bad = tmp_path / "bad.yml"
    bad.write_text("version: 1\nscenario:\n  name: x\n  shocks:\n"
                   "    - target: macro.interest\n      value: 0.01\n",
                   encoding="utf-8")
    result = cli("scenario", "validate", str(bad))
    assert result.returncode == 1
    assert "Scenario invalid." in result.stdout
    assert "macro.policy_rate" in result.stdout


def test_show_separates_shocks_from_assumptions():
    result = cli("scenario", "show", "oil_price_spike")
    assert result.returncode == 0, result.stderr
    assert "Exogenous shocks" in result.stdout
    assert "Assumed transmission" in result.stdout
    assert result.stdout.index("Exogenous shocks") < \
        result.stdout.index("Assumed transmission")
    assert "ASSUMPTIONS" in result.stdout


def test_diff_names_the_targets_that_differ():
    result = cli("scenario", "diff", "rate_shock", "recession")
    assert result.returncode == 0, result.stderr
    assert "SCENARIO DIFF" in result.stdout
    assert "macro.cycle" in result.stdout
    assert "A: unchanged" in result.stdout


def test_targets_lists_the_registry_and_the_refusals():
    result = cli("scenario", "targets")
    assert result.returncode == 0, result.stderr
    for name in TARGETS:
        assert name in result.stdout
    assert "NOT SUPPORTED" in result.stdout
    assert "execution.market_impact" in result.stdout
