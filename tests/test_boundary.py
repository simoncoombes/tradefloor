"""Decision boundaries: the search, the floor, the bracket, the map.

Every claim `tradefloor.boundary` makes is stated here against agents
whose behaviour is set by construction. A threshold agent flips at a known
rate, so the bracket can be checked against the value it must contain. A
scripted agent's re-ask hook returns a controlled spread, so the floor is
known before the search measures it. The recorded FinRobot run is the one
real agent, and it is replayed: nothing in this file calls a provider, and
one test asserts the framework is never imported.
"""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import subprocess
import sys

import pytest

import tradefloor as tf
from tradefloor import boundary
from tradefloor.boundary import (COLUMNS, FLOOR_CALLS, STATUSES, BoundaryMap,
                                 Flip, Search, flip, macro_field_of,
                                 map_boundaries, search)
from tradefloor.counterfactual import World, resample
from tradefloor.integrations.callable import CallableAgentAdapter
from tradefloor.integrations.common import Transcript, refuse_replay_reask
from tradefloor.manifest import market_digest

REPO = pathlib.Path(__file__).resolve().parent.parent
FIXTURE = REPO / "tests" / "fixtures" / "finrobot" / "rate-shock.json"
STUDY = REPO / "examples" / "integrations" / "finrobot" / "rate_shock.py"
RUNNER = REPO / "tools" / "boundary" / "run.py"

SEED = 606
STEPS_PER_DAY = 3
WARMUP = 3
BASE_RATE = 0.04
#: The rate above which the threshold agent sells. Every bracket test
#: reads its answer against this number.
THRESHOLD = 0.05
VIX_THRESHOLD = 20.0


def universe():
    return list(tf.Universe.random(4, seed=11))


def _order(payload, side):
    symbol = payload["assets"][0]["symbol"]
    if side == "HOLD":
        return {"actions": [], "rationale": "hold"}
    return {"actions": [{"symbol": symbol, "side": side, "quantity": 100}],
            "rationale": f"{side} at {payload['macro']}"}


def threshold(payload):
    """Sells above THRESHOLD, buys at or below it."""
    rate = payload["macro"]["federal_funds_rate"]
    return _order(payload, "SELL" if rate > THRESHOLD else "BUY")


def vix_reader(payload):
    """Sells above VIX_THRESHOLD, buys at or below it."""
    return _order(payload,
                  "SELL" if payload["macro"]["vix"] > VIX_THRESHOLD else "BUY")


def refuses_high(payload):
    """A threshold agent that returns unusable output above 6%."""
    if payload["macro"]["federal_funds_rate"] > 0.06:
        return "not a decision"
    return threshold(payload)


class Scripted(CallableAgentAdapter):
    """A callable adapter whose floor is set by construction.

    ``spread`` is the number of distinct answers ``reask`` rotates
    through; zero re-asks the function itself, which is deterministic.
    ``reask_fn`` replaces both: it takes the recorded prompt and returns
    the raw answer, so a test can refuse or vary on re-ask alone.
    ``calls`` is shared across forks, so a test can count the floor's
    calls wherever they landed.
    """

    def __init__(self, fn=None, *, spread=0, reask_fn=None, calls=None,
                 **kwargs):
        super().__init__(fn, **kwargs)
        self.spread = spread
        self.reask_fn = reask_fn
        self.calls = [] if calls is None else calls

    def reask(self, entry):
        # The guard every real adapter applies: a recording holds one
        # answer per input, so a replaying agent has no floor to measure.
        refuse_replay_reask(self.mode, type(self).__name__)
        self.calls.append(entry["step"])
        if self.reask_fn is not None:
            return self.reask_fn(entry["prompt"])
        if not self.spread:
            return self.call(None, entry["prompt"])
        which = len(self.calls) % self.spread
        return _order(entry["prompt"], ("BUY", "SELL", "HOLD")[which % 3])

    def fork_kwargs(self):
        kwargs = super().fork_kwargs()
        kwargs.update(spread=self.spread, reask_fn=self.reask_fn,
                      calls=self.calls)
        return kwargs


def _above(prompt):
    return prompt["macro"]["federal_funds_rate"] > THRESHOLD


def _per_arm_counter():
    """Re-ask calls counted per arm, keyed by the rate the prompt shows."""
    seen = {}

    def count(prompt):
        rate = prompt["macro"]["federal_funds_rate"]
        seen[rate] = seen.get(rate, 0) + 1
        return seen[rate]

    return count


def size_change(payload):
    """Buys 100 at or below THRESHOLD and 1,000 above: one net either way."""
    symbol = payload["assets"][0]["symbol"]
    return {"actions": [{"symbol": symbol, "side": "BUY",
                         "quantity": 1000 if _above(payload) else 100}]}


def symbol_change(payload):
    """Buys the second name above THRESHOLD, the first at or below it."""
    symbol = payload["assets"][1 if _above(payload) else 0]["symbol"]
    return {"actions": [{"symbol": symbol, "side": "BUY", "quantity": 100}]}


def build(fn=threshold, *, spread=0, days=WARMUP, agent=None, **over):
    agent = agent or Scripted(fn, spread=spread, mode="live",
                              every=STEPS_PER_DAY)
    kwargs = dict(seed=SEED, universe=universe(), agent=agent,
                  cash=1_000_000.0, steps_per_day=STEPS_PER_DAY,
                  ticks_per_step=30,
                  pins={"federal_funds_rate": BASE_RATE,
                        "corporate_bond_yield": 0.055},
                  label="root")
    kwargs.update(over)
    world = World(**kwargs)
    world.run(days=days)
    return world


def rate_search(world, **over):
    kwargs = dict(operation="set", bracket=(0.04, 0.08), steps=8)
    kwargs.update(over)
    return search(world, "macro.policy_rate", **kwargs)


# --------------------------------------------------------------------------
# The floor
# --------------------------------------------------------------------------

def test_a_flip_is_reported_only_when_the_gap_clears_the_floor():
    """The acceptance claim. The same threshold agent, two floors set by
    construction: a zero floor reports the flip, a wide one swallows it."""
    wide = rate_search(build(spread=3), steps=4)
    assert wide.status == "inside floor"
    assert wide.flip is None
    assert wide.floor is not None
    assert wide.separation is not None and wide.separation <= 1.0
    assert wide.decision_low != wide.decision_high, (
        "the bracket closed on two decisions; the floor is what refused it")
    assert any("sits inside the agent's own spread" in c for c in wide.caveats)
    assert any("within-arm stdev" in c for c in wide.caveats)

    zero = rate_search(build(spread=0), steps=4)
    assert zero.status == "flip"
    assert isinstance(zero.flip, Flip)
    assert zero.separation is None, "neither arm varied"
    assert zero.net_gap == -2.0, "BUY (+1) below, SELL (-1) above"
    assert any("the floor is zero" in c for c in zero.caveats)


def test_flip_returns_the_search_result_and_nothing_else():
    world = build()
    found = flip(world, "macro.policy_rate", operation="set",
                 bracket=(0.04, 0.08), steps=3)
    assert isinstance(found, Flip)
    assert flip(build(spread=3), "macro.policy_rate", operation="set",
                bracket=(0.04, 0.08), steps=3) is None


def test_the_floor_is_measured_on_the_two_bracketing_arms():
    """FLOOR_CALLS re-asks per arm, both at the decision step the bracket
    closed on, and none before the bracket closed."""
    world = build()
    found = rate_search(world, steps=3)
    calls = world.agent.calls
    assert len(calls) == 2 * FLOOR_CALLS
    assert set(calls) == {found.step}
    assert found.floor["n"] == FLOOR_CALLS
    assert set(found.floor["arms"].values()) == set(found.floor["noise"])
    labels = {p.label for p in found.probes}
    assert set(found.floor["noise"]) <= labels, "measured on probe arms"


def test_the_floor_is_unmeasurable_where_the_book_moves_with_the_target():
    """VIX widens the quoted spread on the day it moves, so the two arms'
    inputs differ in bid and ask lines and resample refuses them. The
    bracket still closes; no flip is reported; the refusal is quoted."""
    found = search(build(vix_reader), "macro.vix", operation="multiply",
                   bracket=(0.5, 3.0), steps=4)
    assert found.status == "floor unmeasurable"
    assert found.flip is None
    assert found.decision_low != found.decision_high
    assert "which no intervention touched" in found.caveats[0]
    # The bracket still closed on the threshold: the level the low arm
    # opened with sits at or below it and the high arm's above it.
    assert found.seen_low <= VIX_THRESHOLD < found.seen_high


def test_a_supplied_floor_is_used_and_declared():
    """A Resample handed in replaces the measurement, and the caveats say
    it was not measured on this search's arms."""
    noisy = build(spread=3)
    control, shock = noisy.fork("control", "shock")
    shock.intervene(federal_funds_rate=0.06)
    control.run(days=1)
    shock.run(days=1)
    supplied = resample(control, shock, at=control.fork_step, n=4)
    assert supplied.separation["net"] is not None

    found = rate_search(build(), steps=3, floor=supplied)
    assert found.status == "inside floor"
    assert found.floor == supplied.as_dict()
    assert any("supplied by the caller" in c for c in found.caveats)
    assert not build().agent.calls, "nothing was re-asked"


def test_identical_inputs_never_report_a_flip():
    """An agent that answers differently on byte-identical inputs is noise,
    whatever the recorded decisions say, and no floor is asked for."""
    seen = []

    def flaky(payload):
        seen.append(1)
        return _order(payload, "BUY" if len(seen) % 2 else "SELL")

    world = build(flaky)
    found = search(world, "commodity.oil", operation="multiply",
                   bracket=(0.6, 1.5), steps=3)
    assert found.status == "unseen"
    assert found.flip is None
    assert found.decision_low != found.decision_high
    assert found.floor is None
    assert world.agent.calls == [], "no re-ask was spent on it"
    assert any("byte-identical" in c for c in found.caveats)


# --------------------------------------------------------------------------
# Floors that were never measured
# --------------------------------------------------------------------------

def test_an_arm_whose_re_asks_all_refuse_has_no_floor():
    """resample averages an arm over the answers that parsed, so an arm
    refusing every re-ask reads as a stable arm with no spread. That is
    no floor, and no flip is reported over it."""
    def refuse_above(prompt):
        return "not a decision" if _above(prompt) else threshold(prompt)

    agent = Scripted(threshold, reask_fn=refuse_above, mode="live",
                     every=STEPS_PER_DAY)
    found = rate_search(build(agent=agent), steps=3)
    assert found.status == "floor unmeasurable"
    assert found.flip is None
    assert found.floor is not None, "the resample ran and is carried"
    high = found.floor["arms"]["treatment"]
    assert found.floor["noise"][high]["parsed"] == 0
    assert found.floor["noise"][high]["refusals"] == FLOOR_CALLS
    assert found.floor["separation"]["net"] is None
    assert found.floor_gap != 0.0, "the number that used to pass as a gap"
    assert f"arm {high!r} returned 0 executable decision(s)" in \
        found.caveats[0]
    assert f"in {FLOOR_CALLS} calls ({FLOOR_CALLS} refused)" in \
        found.caveats[0]
    assert any("counted as refusals" in c for c in found.caveats)
    assert len(agent.calls) == 2 * FLOOR_CALLS


def test_two_arms_that_refuse_everything_are_not_inside_a_floor():
    agent = Scripted(threshold, reask_fn=lambda prompt: "no", mode="live",
                     every=STEPS_PER_DAY)
    found = rate_search(build(agent=agent), steps=2)
    assert found.status == "floor unmeasurable"
    assert found.floor_gap == 0.0
    assert not any("inside the agent's own spread" in c
               for c in found.caveats)
    assert "returned 0 executable decision(s)" in found.caveats[0]


def test_one_parsed_answer_is_not_a_floor_of_zero():
    """A single sample has a population stdev of zero by construction."""
    count = _per_arm_counter()

    def once_above(prompt):
        if _above(prompt) and count(prompt) > 1:
            return "no"
        return threshold(prompt)

    agent = Scripted(threshold, reask_fn=once_above, mode="live",
                     every=STEPS_PER_DAY)
    found = rate_search(build(agent=agent), steps=2)
    assert found.status == "floor unmeasurable"
    high = found.floor["arms"]["treatment"]
    assert found.floor["noise"][high]["parsed"] == 1
    assert "returned 1 executable decision(s)" in found.caveats[0]
    assert f"({FLOOR_CALLS - 1} refused)" in found.caveats[0]


def test_refusals_beside_enough_parsed_answers_are_declared_not_fatal():
    """Two refusals in eight per arm leave six parsed answers, a measured
    zero floor, and a reported flip that says what was refused."""
    count = _per_arm_counter()

    def refuse_some(prompt):
        return "no" if count(prompt) % 3 == 0 else threshold(prompt)

    agent = Scripted(threshold, reask_fn=refuse_some, mode="live",
                     every=STEPS_PER_DAY)
    found = rate_search(build(agent=agent), steps=2)
    assert found.status == "flip"
    for arm in found.floor["noise"].values():
        assert arm["parsed"] == FLOOR_CALLS - 2
        assert arm["refusals"] == 2
    assert found.separation is None
    assert found.floor_gap == found.net_gap == -2.0
    assert any(f"4 of the {2 * FLOOR_CALLS} floor calls" in c
               for c in found.flip.caveats)
    assert any("the floor is zero" in c for c in found.flip.caveats)


@pytest.mark.parametrize("fn, low, high", [
    (size_change, "BUY 100 AAA", "BUY 1,000 AAA"),
    (symbol_change, "BUY 100 AAA", "BUY 100 AAB"),
])
def test_a_change_the_net_cannot_see_is_named_not_swallowed(fn, low, high):
    """A change of quantity or of symbol closes a bracket and is invisible
    to net, the measure the floor is computed on. It is its own status,
    apart from a measured change that was too small."""
    world = build(fn)
    found = rate_search(world, steps=2)
    assert found.status == "same net"
    assert found.flip is None
    assert found.net_gap == 0.0
    assert boundary.describe_shape(found.shape_low) == low
    assert boundary.describe_shape(found.shape_high) == high
    assert found.floor is None and found.floor_gap is None
    assert world.agent.calls == [], "no re-ask was spent on it"
    assert any("agree in net" in c for c in found.caveats)
    assert not any("inside the agent's own spread" in c
               for c in found.caveats)


def test_the_two_gaps_are_told_apart():
    """The recorded gap and the resampled-means gap differ whenever the
    agent is not deterministic, and both are named where they appear."""
    count = _per_arm_counter()

    def hold_sometimes(prompt):
        # The high arm answers SELL, SELL, HOLD, HOLD...: a mean net of
        # -0.5 against a recorded SELL of -1.
        if _above(prompt) and count(prompt) > 2:
            return _order(prompt, "HOLD")
        return threshold(prompt)

    agent = Scripted(threshold, reask_fn=hold_sometimes, mode="live",
                     every=STEPS_PER_DAY)
    found = rate_search(build(agent=agent), steps=2)
    assert found.net_gap == -2.0, "recorded: BUY (+1) to SELL (-1)"
    assert found.floor_gap == pytest.approx(-1.25), "means: +1 to -0.25"
    assert found.floor_gap != found.net_gap
    assert found.separation == pytest.approx(
        abs(found.floor_gap) / found.floor["separation"]["floor_net"])
    text = (found.flip or found).render()
    assert "recorded" in text and "resampled" in text
    row = found.row()
    assert row["net_gap"] == -2.0 and row["floor_gap"] == found.floor_gap


# --------------------------------------------------------------------------
# The bracket
# --------------------------------------------------------------------------

def test_bisection_closes_on_the_threshold_within_steps():
    """Set operation: the closed bracket contains the threshold, is
    2**steps narrower than the one given, and cost 2 + steps probes."""
    found = rate_search(build(), steps=8)
    assert found.status == "flip"
    assert found.low <= THRESHOLD < found.high
    assert found.high - found.low == pytest.approx(0.04 / 2 ** 8)
    assert len(found.probes) == 2 + 8
    assert all(p.outcome == "decided" for p in found.probes)
    assert [p.seen for p in found.probes] == [p.value for p in found.probes]
    assert found.flip.low == found.low and found.flip.high == found.high


def test_bisection_closes_in_relative_units_too():
    """Multiply: the threshold at 5% is a multiplier of 1.25 on 4%."""
    found = search(build(), "macro.policy_rate", operation="multiply",
                   bracket=(1.0, 2.0), steps=6)
    assert found.status == "flip"
    assert found.low <= THRESHOLD / BASE_RATE < found.high
    assert found.seen_low == pytest.approx(found.low * BASE_RATE)
    added = search(build(), "macro.policy_rate", operation="add",
                   bracket=(0.0, 0.03), steps=5)
    assert added.status == "flip"
    assert added.low <= THRESHOLD - BASE_RATE < added.high


def test_bisection_stops_at_the_targets_resolution():
    """A rate prints to a basis point. Once both ends print alike the
    search stops, however many steps remain, and the caveat says so."""
    found = rate_search(build(), steps=20)
    assert found.status == "flip"
    assert len(found.probes) - 2 < 20
    assert found.high - found.low < 0.0001
    assert any("at the target's resolution" in c for c in found.caveats)


def test_a_bracket_whose_ends_agree_reports_no_flip():
    found = rate_search(build(), bracket=(0.02, 0.045))
    assert found.status == "no flip"
    assert found.flip is None
    assert [p.outcome for p in found.probes] == ["decided", "decided"]
    assert found.decision_low["actions"] == found.decision_high["actions"]


def test_a_target_the_agent_is_not_shown_is_said_to_be_unseen():
    """Oil is not in the observation, so both ends send the same input,
    and the no-flip verdict says the bracket could not have flipped it."""
    found = search(build(), "commodity.oil", operation="multiply",
                   bracket=(0.6, 1.5), steps=3)
    assert found.status == "no flip"
    assert any("byte-identical inputs" in c for c in found.caveats)


def test_the_two_arms_are_agreed_before_either_is_written_to():
    found = rate_search(build(), steps=2)
    assert found.agreement is not None
    assert found.agreement.identical
    assert len(found.agreement.checks) == 9
    assert found.flip.agreement is found.agreement


def test_the_callers_world_is_left_where_it_was():
    world = build()
    before = (world.day, world.step, world.digest(), len(world.trace),
              len(world.agent.record), list(world.interventions))
    rate_search(world, steps=3)
    rate_search(world, steps=2, at=world.day + 2)
    assert (world.day, world.step, world.digest(), len(world.trace),
            len(world.agent.record), list(world.interventions)) == before


def test_a_later_day_runs_a_private_fork_forward():
    world = build()
    found = rate_search(world, steps=2, at=world.day + 2)
    assert found.status == "flip"
    assert found.day == world.day + 2
    assert found.step == (world.day + 2) * STEPS_PER_DAY
    assert world.day == WARMUP


# --------------------------------------------------------------------------
# Manifests
# --------------------------------------------------------------------------

def test_every_reported_boundary_carries_two_manifests_that_reproduce():
    found = rate_search(build(), steps=3).flip
    assert len(found.manifests) == 2
    low, high = found.manifests
    for manifest, label in ((low, "low"), (high, "high")):
        assert isinstance(manifest, tf.RunManifest)
        rebuilt = tf.RunManifest.from_json(manifest.to_json()).reproduce()
        assert market_digest(rebuilt) == manifest.result["digest"], label
        assert manifest.scenario is not None
        assert manifest.strategy_reference, "the agent's own citation"
    assert low.result["digest"] != high.result["digest"]
    # Lineage: both logs carry the shared history as a prefix and part
    # where the probe day's pin differs.
    parted = next(i for i, (a, b) in enumerate(zip(low.order_log,
                                                    high.order_log))
                  if a != b)
    assert parted > 0
    assert low.order_log[:parted] == high.order_log[:parted]
    assert low.label != high.label
    fields = {item for m in (low, high) for item in m.scenario.fields}
    assert "federal_funds_rate" in fields


def test_a_flip_serialises_with_its_manifests():
    found = rate_search(build(), steps=2)
    payload = json.loads(json.dumps(found.as_dict()))
    assert payload["status"] == "flip"
    assert len(payload["flip"]["manifests"]) == 2
    assert payload["flip"]["floor"]["n"] == FLOOR_CALLS
    assert payload["flip"]["floor_gap"] == payload["flip"]["net_gap"]
    assert [p["outcome"] for p in payload["probes"]] == ["decided"] * 4
    text = found.flip.render()
    assert "net gap, recorded" in text and "net gap, resampled" in text


# --------------------------------------------------------------------------
# Probes that do not decide
# --------------------------------------------------------------------------

def test_a_replaying_agent_closes_its_bracket_and_cannot_report_a_flip():
    """Record a live search, replay it: every probe is reached, the
    bracket closes on the same decisions, and the floor is refused
    because a recording holds one answer per input."""
    recorder = Transcript()
    live_agent = Scripted(threshold, mode="live", every=STEPS_PER_DAY,
                          recorder=recorder)
    live = rate_search(build(agent=live_agent), steps=3)
    assert live.status == "flip"
    assert len(recorder) >= WARMUP + len(live.probes)

    replayer = Scripted(threshold, mode="replay", transcript=recorder,
                        every=STEPS_PER_DAY)
    replayed = rate_search(build(agent=replayer), steps=3)
    assert replayed.status == "floor unmeasurable"
    assert replayed.flip is None
    assert "replaying" in replayed.caveats[0]
    assert [p.value for p in replayed.probes] == [p.value for p in live.probes]
    assert [p.shape for p in replayed.probes] == [p.shape for p in live.probes]
    assert not replayer.calls, "a replay was never re-asked"

    # A fresh adapter: an instance carries its price history across
    # worlds, and a reused one would render a different step-zero input.
    fresh = Scripted(threshold, mode="replay", transcript=recorder,
                     every=STEPS_PER_DAY)
    outside = rate_search(build(agent=fresh), bracket=(0.041, 0.08),
                          steps=1)
    assert outside.status == "unreachable"
    assert outside.probes[0].outcome == "unreachable"
    assert "ReplayMiss" in outside.probes[0].detail
    assert outside.unreachable == outside.probes[:1]


def test_a_replay_miss_is_unreachable_even_where_refusals_are_skipped():
    """`on_refusal="skip"` skips a bad answer and re-raises a missing one;
    the search keeps that distinction. The recording covers the shared
    history and no probe."""
    recorder = Transcript()
    build(agent=Scripted(threshold, mode="live", every=STEPS_PER_DAY,
                         recorder=recorder))
    replayer = Scripted(threshold, mode="replay", transcript=recorder,
                        every=STEPS_PER_DAY)
    world = build(agent=replayer, on_refusal="skip")
    found = rate_search(world, steps=2)
    assert found.status == "unreachable"
    assert found.probes[0].outcome == "unreachable"
    assert "ReplayMiss" in found.probes[0].detail
    later = rate_search(world, steps=2, at=world.day + 1)
    assert later.status == "unreachable"
    assert later.probes == []
    assert "could not be run from day" in later.caveats[0]


def test_a_probe_on_the_day_a_pin_begins_is_refused_by_name():
    """A world's construction pins begin on day 0, and a probe is a pin
    on the same field; two beginning the same day would be refused at
    run time, so the search refuses first and names the pin."""
    with pytest.raises(tf.ValidationError, match="construction pin"):
        rate_search(build(days=0), steps=1)
    world = build()
    world.intervene(federal_funds_rate=0.045)
    with pytest.raises(tf.ValidationError, match="intervene\\(\\) on day"):
        rate_search(world, steps=1)
    assert search(world, "commodity.oil", operation="multiply",
                  bracket=(0.6, 1.5), steps=1).status == "no flip"


@pytest.mark.parametrize("policy", ["raise", "skip"])
def test_an_unusable_answer_ends_the_search_as_unusable(policy):
    found = rate_search(build(refuses_high, on_refusal=policy), steps=3)
    assert found.status == "unusable"
    assert found.flip is None
    assert found.probes[0].outcome == "decided"
    assert found.probes[1].outcome == "unusable"
    assert "DecisionError" in found.probes[1].detail


# --------------------------------------------------------------------------
# Refusals
# --------------------------------------------------------------------------

def test_a_name_valued_target_is_refused():
    with pytest.raises(tf.ValidationError, match="no midpoint"):
        search(build(), "macro.cycle", operation="set", bracket=(0, 1))


def test_a_bracket_that_moves_nothing_is_refused():
    with pytest.raises(tf.ValidationError, match="moves nothing"):
        search(build(), "macro.qe_pe_boost", operation="multiply",
               bracket=(0.5, 2.0))


def test_a_bracket_end_outside_the_domain_is_refused_before_any_probe():
    world = build()
    steps_before = world.step
    with pytest.raises(tf.ValidationError, match="outside the sentiment"):
        search(world, "macro.fear_greed", operation="multiply",
               bracket=(0.5, 2.0))
    assert world.step == steps_before


@pytest.mark.parametrize("kwargs, match", [
    (dict(bracket=(0.08, 0.04)), "low < high"),
    (dict(bracket=(0.04,)), "two numbers"),
    (dict(operation="scale", bracket=(1, 2)), "unknown operation"),
    (dict(bracket=(0.04, 0.08), steps=-1), "whole number"),
    (dict(bracket=(0.04, 0.08), floor={"net": 1}), "Resample"),
    (dict(bracket=(0.04, 0.08), at=0), "cannot rewind"),
])
def test_bad_arguments_are_refused_by_name(kwargs, match):
    with pytest.raises(tf.ValidationError, match=match):
        search(build(), "macro.policy_rate", **kwargs)


def test_an_unknown_target_is_refused_with_the_registry():
    with pytest.raises(tf.ValidationError, match="unknown intervention"):
        search(build(), "macro.interest_rate", bracket=(1, 2))


def test_a_native_decision_without_actions_is_refused_with_advice():
    class Ramp:
        def act(self, obs):
            return {}

        def decision(self):
            return {"gross": 0.5}

    with pytest.raises(tf.ValidationError, match="took no decision"):
        rate_search(build(agent=Ramp()), steps=1)


# --------------------------------------------------------------------------
# Targets
# --------------------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(tf.TARGETS))
def test_the_derived_macro_field_is_the_one_the_target_writes(name):
    """Derived by writing, so it holds by construction; checked anyway."""
    target = tf.TARGETS[name]
    field = macro_field_of(target)
    engine = tf.Engine(seed=3, universe=list(tf.Universe.random(2, seed=1)))
    if name == "market.liquidity":
        assert field is None
        return
    assert field in engine.macro_fields
    value = "contraction" if name == "macro.cycle" else (
        target.read(engine) + 0.005 if name != "macro.fear_greed" else 60.0)
    target.write(engine, value)
    # The engine carries rates in percent and hands them back as
    # fractions, so a written level comes back to the last bit or so.
    assert engine.macro_fields[field] == pytest.approx(value)
    assert target.read(engine) == pytest.approx(value)


# --------------------------------------------------------------------------
# The map
# --------------------------------------------------------------------------

def _study():
    spec = importlib.util.spec_from_file_location("rate_shock_study", STUDY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _recorded_world():
    from tradefloor.integrations.finrobot import FinRobotAdapter
    from tradefloor.integrations.finrobot import Transcript as Recording

    study = _study()
    agent = FinRobotAdapter(
        mode="replay", transcript=Recording.load(FIXTURE),
        fundamentals=study.FUNDAMENTALS, objective=study.OBJECTIVE,
        every=study.DECISION_EVERY, arm="shared")
    world = World(seed=study.SEED, universe=list(study.universe()),
                  agent=agent, pins=study.BASE_PINS, cash=study.CASH,
                  steps_per_day=study.STEPS_PER_DAY,
                  ticks_per_step=study.TICKS_PER_STEP, label="shared")
    world.run(days=study.WARMUP_DAYS)
    return world, study


needs_fixture = pytest.mark.skipif(
    not FIXTURE.exists(),
    reason="no recorded FinRobot run at tests/fixtures/finrobot/")


@needs_fixture
def test_the_map_runs_against_the_recorded_finrobot_agent_without_a_provider():
    """Exact replay. A target the prompt shows misses the recording at the
    first probe and is named unreachable; a target it does not show
    replays the recorded control decision at both ends. A scenario the
    recording does not reach is unreachable for every target. FinRobot
    itself is never imported."""
    from tradefloor.integrations.finrobot import OBSERVABLE_MACRO

    world, study = _recorded_world()
    # A multiplier moves nothing at zero and takes sentiment past 100 from
    # where the recorded world holds it, so those two are refused by the
    # bracket rather than searched; they are covered by their own tests.
    targets = [name for name, t in tf.TARGETS.items()
               if t.numeric and name not in ("macro.qe_pe_boost",
                                             "macro.fear_greed")]
    shown = {name for name in targets
             if macro_field_of(tf.TARGETS[name]) in OBSERVABLE_MACRO}
    shown.add("market.liquidity")

    atlas = map_boundaries(world, targets=targets,
                           scenarios=[None, "rate_shock"],
                           operation="multiply", bracket=(0.5, 1.4),
                           steps=2)
    assert "finrobot" not in sys.modules
    assert world.agent.llm_config is None
    assert isinstance(atlas, BoundaryMap)
    assert atlas.flips == []
    assert len(atlas.searches) == 2 * len(targets)

    first_step = study.WARMUP_DAYS * study.STEPS_PER_DAY
    for found in atlas.searches:
        if found.scenario == "rate_shock":
            assert found.status == "unreachable"
            assert found.probes == []
            assert "could not be run to its first shock day" in (
                found.caveats[0])
            continue
        assert found.scenario == "shared"
        if found.target in shown:
            assert found.status == "unreachable", found.target
            assert found.probes[0].outcome == "unreachable"
            assert f"no recorded FinRobot response for step {first_step}" \
                in found.probes[0].detail
            assert found.unreachable == found.probes
        else:
            assert found.status == "no flip", found.target
            assert len(found.probes) == 2
            assert any("byte-identical" in c for c in found.caveats)
            recorded = world.agent.transcript.entries
            control = next(e for e in recorded if e["step"] == first_step)
            assert found.decision_low["step"] == first_step
            assert found.step == first_step
            assert control["arm"] == "control"
    assert len(atlas.unreachable) == len(shown) + len(targets)
    assert any("outside the agent's recording" in c for c in atlas.caveats)
    assert any("replaying a recording of 60" in c
               for s in atlas.searches for c in s.caveats)


def test_the_map_forks_each_scenario_at_its_first_shock_day():
    """The probe day is the shock day, a target the scenario writes that
    day is shadowed, and None means the world as it stands."""
    custom = tf.Scenario(name="oil").shock("commodity.oil",
                                           operation="multiply",
                                           value=1.4, at=2)
    world = build()
    atlas = map_boundaries(
        world, targets=["macro.policy_rate", "macro.corporate_yield",
                        "commodity.oil"],
        scenarios=["rate_shock", None, custom],
        operation="multiply", bracket=(1.0, 2.0), steps=2)
    by = {(s.scenario, s.target): s for s in atlas.searches}
    assert len(by) == 9

    shocked = by[("rate_shock", "macro.policy_rate")]
    assert shocked.status == "shadowed"
    assert shocked.day == WARMUP + 50
    assert "macro.policy_rate" in shocked.caveats[0]
    assert by[("rate_shock", "macro.corporate_yield")].status == "shadowed"
    assert by[("rate_shock", "commodity.oil")].status == "no flip"
    assert by[("rate_shock", "commodity.oil")].day == WARMUP + 50

    assert by[("root", "macro.policy_rate")].status == "flip"
    assert by[("root", "macro.policy_rate")].day == WARMUP
    assert by[("oil", "commodity.oil")].status == "shadowed"
    assert by[("oil", "commodity.oil")].day == WARMUP + 2
    assert by[("oil", "macro.policy_rate")].status == "flip"
    assert len(atlas.flips) == 2
    assert world.day == WARMUP, "the caller's world did not move"


def test_the_map_refuses_what_a_single_search_would_and_carries_on():
    atlas = map_boundaries(build(), targets=["macro.qe_pe_boost",
                                             "macro.policy_rate"],
                           scenarios=[None], operation="multiply",
                           bracket=(1.0, 2.0), steps=1)
    assert [s.status for s in atlas.searches] == ["refused", "flip"]
    assert "moves nothing" in atlas.searches[0].caveats[0]


def test_the_map_takes_a_bracket_per_target():
    atlas = map_boundaries(
        build(), targets=["macro.policy_rate", "commodity.oil"],
        scenarios=[None], operation="set",
        bracket={"macro.policy_rate": (0.04, 0.08),
                 "commodity.oil": (50.0, 100.0)}, steps=1)
    assert [s.status for s in atlas.searches] == ["flip", "no flip"]
    with pytest.raises(tf.ValidationError, match="names no pair"):
        map_boundaries(build(), targets=["macro.policy_rate"],
                       scenarios=[None], operation="set",
                       bracket={"commodity.oil": (50.0, 100.0)})


@pytest.mark.parametrize("kwargs, match", [
    (dict(at=3, bracket=(1, 2)), "first shock day"),
    (dict(floor=None, bracket=(1, 2)), "supplied floor"),
    (dict(), "needs bracket"),
    (dict(bracket=(1, 2), n=3), "unknown keyword"),
])
def test_the_map_refuses_arguments_it_cannot_honour(kwargs, match):
    with pytest.raises(tf.ValidationError, match=match):
        map_boundaries(build(), targets=["macro.policy_rate"],
                       scenarios=[None], **kwargs)


def test_the_map_table_has_the_declared_columns():
    pa = pytest.importorskip("pyarrow")
    atlas = map_boundaries(build(), targets=["macro.policy_rate",
                                             "commodity.oil",
                                             "macro.qe_pe_boost"],
                           scenarios=[None], operation="multiply",
                           bracket=(1.0, 2.0), steps=2)
    table = atlas.table()
    assert isinstance(table, pa.Table)
    assert table.column_names == list(COLUMNS)
    assert table.num_rows == len(atlas.searches) == 3
    rows = table.to_pylist()
    assert [row["status"] for row in rows] == ["flip", "no flip", "refused"]
    assert set(row["status"] for row in rows) <= set(STATUSES)
    assert rows[0]["reported"] is True and rows[1]["reported"] is False
    assert rows[0]["decision_low"] != rows[0]["decision_high"]
    assert rows[0]["net_gap"] == rows[0]["floor_gap"] == -2.0
    assert rows[0]["floor"] == 0.0 and rows[0]["separation"] is None
    assert rows[1]["floor_gap"] is None
    assert rows[0]["probes"] == 4 and rows[0]["unreachable"] == 0
    assert isinstance(rows[0]["caveats"], list) and rows[0]["caveats"]
    assert rows[2]["low"] is None


def test_the_map_renders_and_serialises():
    atlas = map_boundaries(build(), targets=["macro.policy_rate"],
                           scenarios=[None], operation="multiply",
                           bracket=(1.0, 2.0), steps=1)
    text = atlas.render()
    assert "flip" in text and "macro.policy_rate" in text
    assert atlas.caveats[0].startswith("1 flip(s) reported across 1")
    payload = json.loads(json.dumps(atlas.as_dict()))
    assert payload["counts"] == {"flip": 1}
    assert payload["searches"][0]["flip"]["manifests"]


# --------------------------------------------------------------------------
# The runner
# --------------------------------------------------------------------------

def _env():
    env = dict(os.environ)
    package = str(pathlib.Path(tf.__file__).resolve().parent.parent)
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in (package, env.get("PYTHONPATH", "")) if p)
    return env


def test_the_runner_help_renders():
    proc = subprocess.run([sys.executable, str(RUNNER), "--help"],
                          capture_output=True, text=True, timeout=120,
                          env=_env())
    assert proc.returncode == 0, proc.stderr[-1200:]
    assert "usage:" in proc.stdout


@needs_fixture
def test_the_runner_replays_the_recording_and_writes_the_map(tmp_path):
    proc = subprocess.run(
        [sys.executable, str(RUNNER), "--scenarios", "none",
         "--targets", "commodity.oil", "macro.policy_rate",
         "--steps", "1", "--out", str(tmp_path)],
        capture_output=True, text=True, timeout=300, env=_env())
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "replay of" in proc.stdout
    written = json.loads((tmp_path / "boundary-map.json")
                         .read_text(encoding="utf-8"))
    assert written["counts"] == {"no flip": 1, "unreachable": 1}
    statuses = {s["target"]: s["status"] for s in written["searches"]}
    assert statuses == {"commodity.oil": "no flip",
                        "macro.policy_rate": "unreachable"}


def test_the_runner_refuses_recording_a_replay():
    proc = subprocess.run(
        [sys.executable, str(RUNNER), "--record", "x.json"],
        capture_output=True, text=True, timeout=120, env=_env())
    assert proc.returncode != 0
    assert "--record needs --live" in proc.stderr
