"""The agent's own noise floor, and the finding it keeps out of a paper.

`compare` puts two arms side by side and reports one trajectory each. For a
deterministic policy that is the whole answer. For a language model it is
half of one: the agent is the only stochastic component left in an
otherwise bit-identical experiment, and one pair of trajectories cannot
separate "the agent responded to the intervention" from "the agent answered
the same question two ways".

The measurement this exists to prevent, from a live run at temperature 0.
At the first post-fork decision the two prompts differed in 2 lines of 376.
The trajectories then diverged readably: control bought a dip, the shock arm
cut exposure "to manage downside risk". Resampling those two exact prompts
eight times each gave control 4 distinct answers with a net of 0.62 +/-
0.99, and the shock arm 1 answer in 8 calls at 0.00 +/- 0.00. The
between-arm gap sat inside control's own spread. The recorded split was one
of control's four available answers, and 71 publication checks passed
anyway.

Everything here is offline. No test calls a provider.
"""

from __future__ import annotations

import json

import pytest

import tradefloor as tf
from tradefloor.counterfactual import Resample, World, agree, resample
from tradefloor.integrations.callable import CallableAgentAdapter

SEED = 606
BASE_RATE = 0.04
SHOCK_RATE = 0.06


def universe():
    return list(tf.Universe.random(4, seed=11))


class Answers:
    """A decision function whose spread depends on the rate it is shown.

    Deterministic in the shape that matters -- it is a function of the
    payload and a call counter, not of a clock -- so the numbers below are
    the same on every run. It stands in for a model that is confident under
    one macro path and undecided under the other, which is the pattern the
    real resample found.
    """

    def __init__(self, *, spread: int = 4, refuse_every: int = 0) -> None:
        self.spread = spread
        self.refuse_every = refuse_every
        self.calls = 0
        self.seen: list[float] = []

    def __call__(self, payload):
        self.calls += 1
        rate = payload["macro"]["federal_funds_rate"]
        self.seen.append(rate)
        if self.refuse_every and self.calls % self.refuse_every == 0:
            return "not json at all"
        symbol = payload["assets"][0]["symbol"]
        if rate > BASE_RATE:
            # One answer, every time: a confident arm.
            return {"actions": [{"symbol": symbol, "side": "SELL",
                                 "quantity": 100}]}
        # `spread` different answers in rotation: an undecided arm.
        which = self.calls % self.spread
        if which == 0:
            return {"actions": []}
        return {"actions": [{"symbol": symbol, "side": "BUY",
                             "quantity": 100 * which}]}


#: One decision per simulated day, so the fork lands on a decision step.
#: A cadence that did not divide the day would put `fork_step` between two
#: decisions, and every test here would be asking about a step nobody
#: decided at.
STEPS_PER_DAY = 3


def forked(fn=None, days: int = 1):
    """A run, a fork, and an intervention -- the shape resample reads."""
    fn = fn or Answers()
    world = World(seed=SEED, universe=universe(),
                  agent=CallableAgentAdapter(fn, mode="live",
                                             every=STEPS_PER_DAY),
                  cash=1_000_000.0, steps_per_day=STEPS_PER_DAY,
                  ticks_per_step=30,
                  pins={"federal_funds_rate": BASE_RATE,
                        "corporate_bond_yield": 0.055})
    world.run(days=days)
    control, shock = world.fork("control", "shock")
    agreement = agree(control, shock)
    assert agreement.identical, "the arms must start identical"
    shock.intervene(federal_funds_rate=SHOCK_RATE)
    control.run(days=2)
    shock.run(days=2)
    return control, shock, fn


# --------------------------------------------------------------------------
# The measurement
# --------------------------------------------------------------------------

def test_it_reports_per_arm_noise_and_a_between_arm_separation():
    """Criterion 1, and the whole point: the gap is read against the
    spread, not on its own."""
    control, shock, _ = forked()
    probe = resample(control, shock, at=control.fork_step, n=8)

    assert isinstance(probe, Resample)
    assert probe.at == control.fork_step
    assert probe.n == 8
    assert set(probe.noise) == {"control", "shock"}
    assert probe.noise["control"]["samples"] == 8
    assert probe.noise["shock"]["samples"] == 8
    assert probe.noise["control"]["distinct"] > 1
    assert probe.noise["shock"]["distinct"] == 1
    assert probe.separation["net"] is not None


def test_it_runs_no_market():
    """N paired samples cost N calls, not N re-simulations. That is what
    makes the measurement affordable at the step it matters."""
    control, shock, _ = forked()
    before = (control.step, control.day, control.digest(),
              shock.digest(), len(control.trace))
    resample(control, shock, at=control.fork_step, n=6)
    assert (control.step, control.day, control.digest(),
            shock.digest(), len(control.trace)) == before


def test_a_resample_leaves_the_adapter_alone():
    """The adapter's state belongs to the RUN. A resample that appended to
    the price history or the record would rewrite the experiment it is
    measuring."""
    control, shock, _ = forked()
    before = (len(control.agent.history), len(control.agent.record),
              json.dumps(control.agent.state(), default=str))
    resample(control, shock, at=control.fork_step, n=6)
    assert (len(control.agent.history), len(control.agent.record),
            json.dumps(control.agent.state(), default=str)) == before


def test_stability_is_visible_where_the_mean_is_not():
    """Four distinct answers against one is a difference in decision
    stability rather than direction. `compare` cannot see it; it is only
    observable because the input was byte-identical N times."""
    control, shock, _ = forked()
    probe = resample(control, shock, at=control.fork_step, n=8)
    assert probe.noise["shock"]["modal_share"] == 1.0
    assert probe.noise["control"]["modal_share"] < 1.0
    assert probe.noise["control"]["bought"]
    assert probe.noise["shock"]["sold"]


# --------------------------------------------------------------------------
# The two failure paths that decide whether this is a measurement
# --------------------------------------------------------------------------

def test_it_refuses_inputs_that_differ_beyond_the_intervention():
    """Criterion 2. Two arms whose inputs differ for a second reason are
    not a controlled resample, and today that difference is silent."""
    control, shock, _ = forked()
    # A second decision point: by now the market has answered the
    # intervention and the arms differ in every price.
    later = control.agent.record[-1]["step"]
    assert later > control.fork_step
    with pytest.raises(tf.ValidationError) as raised:
        resample(control, shock, at=later, n=4)
    message = str(raised.value)
    assert "which no intervention touched" in message
    assert "federal_funds_rate" in message, "it must name what DID move"


def test_the_refusal_names_the_field_that_moved():
    """An error a reader can act on names the field, not the offset."""
    control, shock, _ = forked()
    later = control.agent.record[-1]["step"]
    with pytest.raises(tf.ValidationError, match="price|assets|positions"):
        resample(control, shock, at=later, n=4)


def test_a_zero_variance_arm_reports_zero_and_never_infinity():
    """Criterion 3. A ratio over zero is undefined, not large, and `inf`
    printed in a published table reads as an overwhelming result."""
    control, shock, _ = forked(Answers(spread=1))
    probe = resample(control, shock, at=control.fork_step, n=6)

    assert probe.noise["control"]["stdev_net"] == 0.0
    assert probe.noise["shock"]["stdev_net"] == 0.0
    assert probe.separation["net"] is None
    assert probe.separation["floor_net"] == 0.0
    assert probe.separation["gap_net"] != 0.0, "the arms did differ"
    assert "undefined" in probe.render()
    json.dumps(probe.as_dict())        # None survives; inf would not


def test_refusals_are_counted_and_never_resampled_away():
    """Criterion 4. An agent that returns unusable output on three of
    twenty calls is a finding; sampling until twenty parse would hide
    it."""
    control, shock, fn = forked()
    fn.refuse_every = 3        # armed after the run, not during it
    calls_before = fn.calls
    probe = resample(control, shock, at=control.fork_step, n=6)

    total = sum(probe.noise[arm]["refusals"] for arm in probe.noise)
    parsed = sum(probe.noise[arm]["parsed"] for arm in probe.noise)
    assert total > 0
    assert parsed + total == 12
    assert fn.calls - calls_before == 12, "exactly n calls per arm"


# --------------------------------------------------------------------------
# The artifact
# --------------------------------------------------------------------------

def test_as_dict_is_json_and_carries_no_credential():
    """Criterion 5, matching what `Comparison.as_dict` already guarantees."""
    control, shock, _ = forked()
    doc = resample(control, shock, at=control.fork_step, n=4).as_dict()
    text = json.dumps(doc)
    for banned in ("api_key", "api-key", "secret", "token", "authorization",
                   "sk-"):
        assert banned not in text.lower()
    assert doc["identical_inputs"] is False   # the shock reached the input
    assert doc["intervened_fields"] == ["federal_funds_rate"]
    assert doc["n"] == 4


def test_the_diff_is_reported_for_the_write_up():
    """A reader has to be able to see that the arms differed in the
    intervention and in nothing else."""
    control, shock, _ = forked()
    probe = resample(control, shock, at=control.fork_step, n=4)
    assert probe.differing_lines, "the intervention DID move a field"
    joined = json.dumps(probe.differing_lines)
    assert "federal_funds_rate" in joined
    assert "0.04" in joined and "0.06" in joined
    assert "and nowhere else" in probe.render()


def test_identical_inputs_says_the_intervention_never_arrived():
    """A gap between two arms that were asked the same question is agent
    noise and nothing else, and a reader about to attribute it to the
    intervention should be told so on the face of the result."""
    world = World(seed=SEED, universe=universe(),
                  agent=CallableAgentAdapter(Answers(), mode="live",
                                             every=STEPS_PER_DAY),
                  cash=1_000_000.0, steps_per_day=STEPS_PER_DAY,
                  ticks_per_step=30,
                  pins={"federal_funds_rate": BASE_RATE})
    world.run(days=1)
    control, shock = world.fork("control", "shock")
    control.run(days=1)
    shock.run(days=1)

    probe = resample(control, shock, at=control.fork_step, n=4)
    assert probe.identical_inputs is True
    assert probe.differing_lines == []
    assert probe.intervened_fields == []
    assert "had not reached the agent" in probe.render()


def test_render_puts_the_gap_against_the_noise_floor():
    control, shock, _ = forked()
    rendered = resample(control, shock, at=control.fork_step, n=8).render()
    assert "distinct answers" in rendered
    assert "noise floor" in rendered
    assert "control" in rendered and "shock" in rendered


# --------------------------------------------------------------------------
# Refusals a caller should meet early
# --------------------------------------------------------------------------

def test_one_call_per_arm_is_refused():
    control, shock, _ = forked()
    with pytest.raises(tf.ValidationError, match="at least 2"):
        resample(control, shock, at=control.fork_step, n=1)


def test_a_step_the_agent_did_not_decide_at_is_refused():
    control, shock, _ = forked()
    with pytest.raises(tf.ValidationError, match="took no decision at step"):
        resample(control, shock, at=control.fork_step + 1, n=4)


def test_a_world_that_never_forked_is_told_to_name_a_step():
    control, shock, _ = forked()
    control.fork_step = shock.fork_step = None
    with pytest.raises(tf.ValidationError, match="needs a step"):
        resample(control, shock, n=4)


def test_an_agent_with_no_reask_is_refused():
    """A plain policy has no framework to ask again."""
    class Flat:
        def act(self, obs):
            return {}

    world = World(seed=SEED, universe=universe(), agent=Flat(),
                  cash=1_000_000.0, steps_per_day=STEPS_PER_DAY,
                  ticks_per_step=30,
                  pins={"federal_funds_rate": BASE_RATE})
    world.run(days=1)
    control, shock = world.fork("control", "shock")
    shock.intervene(federal_funds_rate=SHOCK_RATE)
    with pytest.raises(tf.ValidationError, match="no decision record"):
        resample(control, shock, at=control.fork_step, n=4)


def test_the_default_step_is_the_fork():
    """The first decision the intervention could have reached."""
    control, shock, _ = forked()
    assert resample(control, shock, n=4).at == shock.fork_step


def test_a_replayed_run_cannot_be_resampled():
    """A recording holds one answer per input.

    Re-asking it N times hands back that answer N times and reports a
    within-arm spread of zero, which is the most confident-looking result a
    resample can produce and is a property of the file rather than of the
    agent. The noise floor is measured live or it is not measured.
    """
    from tradefloor.integrations.common import Transcript

    recorder = Transcript()
    world = World(seed=SEED, universe=universe(),
                  agent=CallableAgentAdapter(Answers(), mode="live",
                                             every=STEPS_PER_DAY,
                                             recorder=recorder),
                  cash=1_000_000.0, steps_per_day=STEPS_PER_DAY,
                  ticks_per_step=30,
                  pins={"federal_funds_rate": BASE_RATE})
    world.run(days=1)

    def replaying(label):
        arm = World(seed=SEED, universe=universe(),
                    agent=CallableAgentAdapter(Answers(), mode="replay",
                                               every=STEPS_PER_DAY,
                                               transcript=recorder),
                    cash=1_000_000.0, steps_per_day=STEPS_PER_DAY,
                    ticks_per_step=30, label=label,
                    pins={"federal_funds_rate": BASE_RATE})
        return arm.run(days=1)

    control, shock = replaying("control"), replaying("shock")
    with pytest.raises(tf.ValidationError, match="noise floor of zero"):
        resample(control, shock, at=0, n=4)


def test_a_stray_field_cannot_ride_in_on_an_intervened_one():
    """Ownership is decided per line, not per diff block.

    `difflib` returns one replace opcode for a run of adjacent changed
    lines, so a changed rate and a changed price land in the same block.
    Asking whether the BLOCK mentions an intervened field would let the
    price through on the rate's ticket, and letting an uncontrolled
    difference through is the one failure this check exists to stop.
    """
    from tradefloor.counterfactual import _diff

    control = chr(10).join(['  "federal_funds_rate": 0.04,',
                            '  "price": 10.0,',
                            '  "held": 5'])
    shocked = chr(10).join(['  "federal_funds_rate": 0.06,',
                            '  "price": 11.0,',
                            '  "held": 5'])
    with pytest.raises(tf.ValidationError, match="price"):
        _diff({"prompt": control}, {"prompt": shocked},
              ["federal_funds_rate"])


def test_two_arms_under_one_label_are_refused():
    """One arm's numbers would land on top of the other's, and the result
    would describe one arm twice under two column headings."""
    control, shock, _ = forked()
    shock.label = control.label
    with pytest.raises(tf.ValidationError, match="both arms are labelled"):
        resample(control, shock, at=control.fork_step, n=4)


def test_the_label_check_runs_before_anything_is_paid_for():
    """A refusal after 2N provider calls is a bill, not a guard."""
    control, shock, fn = forked()
    shock.label = control.label
    before = fn.calls
    with pytest.raises(tf.ValidationError):
        resample(control, shock, at=control.fork_step, n=8)
    assert fn.calls == before


def test_resample_is_reachable_beside_compare():
    """Criterion 7."""
    assert tf.resample is resample
    assert tf.Resample is Resample
    assert "resample" in tf.__all__ and "Resample" in tf.__all__
