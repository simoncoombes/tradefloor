"""What one badly-shaped answer costs, and what a dead run keeps.

Both halves come from the same live pilot: 60 planned decisions on a
24-name universe, and on call 36 the model returned a per-action
`rationale` field. `parse` refused it, correctly -- dropping an unknown
field executes a trade the agent conditioned on something it never got --
and the `DecisionError` went out through `World.run`, past the trace, past
the checkpoint and past every artifact the caller was about to write. 35
recorded interactions, 20 days of shared history and both arms of a fork,
gone to one malformed response at a measured rate of 1 in 35.

A run long enough to be interesting is a run long enough to hit that.

So: `on_refusal="skip"` makes bad output a measurement rather than a
traceback, and `prior=` lets the next attempt keep the answers the last one
paid for. They are separate features because they fail separately -- a
refusal policy does not survive a rate limit, and a resume does not survive
a model that cannot format an answer.
"""

from __future__ import annotations

import pytest

import tradefloor as tf
from tradefloor.counterfactual import Comparison, World, agree, compare
from tradefloor.integrations import common as ci
from tradefloor.integrations.callable import CallableAgentAdapter
from tradefloor.integrations.common import (DecisionError, FrameworkError,
                                            MarketRefusalError, Transcript)

SEED = 909


def universe():
    return list(tf.Universe.random(4, seed=11))


def make_world(agent, **over) -> World:
    kwargs = dict(seed=SEED, universe=universe(), agent=agent,
                  cash=1_000_000.0, steps_per_day=3, ticks_per_step=30,
                  pins={"federal_funds_rate": 0.04,
                        "corporate_bond_yield": 0.055})
    kwargs.update(over)
    return World(**kwargs)


class Scripted:
    """An agent that answers badly on the steps it is told to.

    Not a mock of the adapter layer: it raises the same `DecisionError`
    `parse_decision` raises, which is what a real framework returning a
    malformed response produces.
    """

    def __init__(self, bad, error=DecisionError) -> None:
        self.bad = set(bad)
        self.error = error
        self.asked: list[int] = []
        self.answered: list[int] = []

    def act(self, obs) -> dict[str, float]:
        self.asked.append(obs.step)
        if obs.step in self.bad:
            raise self.error(
                "action 0 carries rationale, which this market has no "
                f"execution path for (step {obs.step})")
        self.answered.append(obs.step)
        return {obs.tickers[0]: 100.0}

    def decision(self):
        return {"step": self.answered[-1] if self.answered else None}


# --------------------------------------------------------------------------
# The refusal policy
# --------------------------------------------------------------------------

def test_a_refusal_ends_the_run_by_default():
    """Criterion 3. Nothing changes for a caller who did not ask for it.

    A run that quietly skipped every decision would report a flat agent
    rather than a broken one, so the old behaviour keeps the default.
    """
    world = make_world(Scripted(bad={4}))
    with pytest.raises(DecisionError, match="step 4"):
        world.run(days=3)
    assert world.on_refusal == "raise"


def test_a_refusal_costs_a_step_under_skip():
    """Criterion 1. The run completes and the refused step trades nothing."""
    agent = Scripted(bad={4})
    world = make_world(agent, on_refusal="skip")
    world.run(days=3)

    assert world.day == 3
    assert len(world.trace) == 9
    assert 4 in agent.asked and 4 not in agent.answered
    refused_row = world.trace[4]
    assert refused_row["orders"] == {}
    assert refused_row["fills"] == []


def test_a_refused_step_records_no_decision():
    """The adapter's last decision is the one BEFORE the refused step.

    Reading it into the refused row would put a decision the agent did not
    take into the row recording it not taking one, and `compare` finds the
    divergence step by comparing exactly this field -- so a stale decision
    here moves the reported divergence.
    """
    world = make_world(Scripted(bad={4}), on_refusal="skip")
    world.run(days=2)
    assert world.trace[4]["decision"] is None
    assert world.trace[3]["decision"] == {"step": 3}
    assert world.trace[5]["decision"] == {"step": 5}


def test_the_refusal_is_countable_in_the_trace_and_the_summary():
    """Criterion 2. Behaving badly is a measurement, so it has a column."""
    world = make_world(Scripted(bad={1, 4, 7}), on_refusal="skip")
    world.run(days=3)

    unusable = [row["step"] for row in world.trace if row["unusable"]]
    assert unusable == [1, 4, 7]
    assert "rationale" in world.trace[1]["unusable"]
    assert world.summary()["unusable_responses"] == 3


def test_a_market_refusal_is_a_refusal_too():
    """`MarketRefusalError` subclasses `DecisionError` deliberately.

    A well-formed order in a symbol this market does not list fails at the
    second stage rather than the first, and a caller written to charge the
    agent a step must keep charging it for both.
    """
    world = make_world(Scripted(bad={4}, error=MarketRefusalError),
                       on_refusal="skip")
    world.run(days=2)
    assert world.summary()["unusable_responses"] == 1


def test_a_dead_framework_is_not_the_agent_behaving_badly():
    """A `FrameworkError` still ends the run, under either policy.

    The call never completed, so the agent produced nothing to judge.
    Counting a dropped connection as bad behaviour would put the network in
    a column labelled with the model's name.
    """
    world = make_world(Scripted(bad={4}, error=FrameworkError),
                       on_refusal="skip")
    with pytest.raises(FrameworkError):
        world.run(days=3)


def test_an_unknown_policy_is_refused_at_construction():
    with pytest.raises(tf.ValidationError, match="on_refusal"):
        make_world(Scripted(bad=()), on_refusal="continue")


def test_a_fork_carries_the_policy():
    """An arm that reverted to the default would die on output its sibling
    counted and continued past, and the surviving arm's column would be the
    only one anybody read."""
    world = make_world(Scripted(bad=()), on_refusal="skip")
    world.run(days=1)
    control, shock = world.fork("control", "shock")
    assert control.on_refusal == shock.on_refusal == "skip"


# --------------------------------------------------------------------------
# The two columns that must never be one column
# --------------------------------------------------------------------------

def test_the_two_refusal_counts_are_reported_apart():
    """Criterion 4, the one that decides whether this is worth having.

    An agent that cannot format an answer and a market that rejected an
    order are different failures with different remedies. A single column
    covering both would make an unusable agent read as an illiquid market.
    """
    world = make_world(Scripted(bad={1, 4}), on_refusal="skip")
    world.run(days=2)
    summary = world.summary()
    assert summary["unusable_responses"] == 2
    assert summary["refused"] == 0

    keys = [key for _, key, _ in Comparison.ROWS if key]
    assert keys.count("refused") == 1
    assert keys.count("unusable_responses") == 1


def test_the_comparison_renders_both_counts():
    """Two arms with different refusal counts are not comparable on
    turnover without the reader being told."""
    world = make_world(Scripted(bad=()), on_refusal="skip")
    world.run(days=2)
    control, shock = world.fork("control", "shock")
    agreement = agree(control, shock)
    shock.agent.bad = {8}
    shock.intervene(federal_funds_rate=0.06)
    control.run(days=1)
    shock.run(days=1)

    table = compare(control, shock, agreement=agreement)
    assert table.control["unusable_responses"] == 0
    assert table.treatment["unusable_responses"] == 1
    rendered = table.render()
    assert "unusable responses" in rendered
    assert "refused trades" in rendered
    assert "unusable_responses" in table.as_dict()["treatment"]


def test_the_count_is_windowed_to_the_fork_like_every_other_count():
    """A refusal in the shared history belongs to both arms and to neither.

    `summary()` measures from `fork_step` for the reason it always has: a
    figure that counts the prologue is mostly prologue in both columns.
    """
    world = make_world(Scripted(bad={1}), on_refusal="skip")
    world.run(days=1)
    control, shock = world.fork("control", "shock")
    control.run(days=1)
    assert world.summary()["unusable_responses"] == 1
    assert control.summary()["unusable_responses"] == 0
    assert control.summary(since=0)["unusable_responses"] == 1


# --------------------------------------------------------------------------
# Resuming a recording
# --------------------------------------------------------------------------

class Counting:
    """A decision function that says how many times it was really called."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, payload):
        self.calls += 1
        return {"actions": [], "rationale": f"call {self.calls}"}


def record_a_run(days: int = 2):
    fn = Counting()
    recorder = Transcript()
    world = make_world(CallableAgentAdapter(fn, mode="live",
                                            recorder=recorder))
    world.run(days=days)
    return recorder, fn


def test_a_prior_answers_the_calls_it_covers():
    """Criterion 5. A resumed run makes no provider call for what it holds.

    The market is deterministic, so the second attempt reaches the same
    prompts and computes the same digests. Every recorded answer is still
    an answer to the question being asked.
    """
    prior, first = record_a_run(days=2)
    covered = first.calls
    assert covered > 0

    second_fn = Counting()
    resumed = Transcript()
    world = make_world(CallableAgentAdapter(
        second_fn, mode="live", recorder=resumed, prior=prior))
    world.run(days=3)

    assert len(resumed.entries) > covered
    assert second_fn.calls == len(resumed.entries) - covered


def test_a_resume_reaches_the_same_answers_the_first_run_gave():
    """The point of the resume: the same run, not a similar one."""
    prior, _ = record_a_run(days=2)
    resumed = Transcript()
    world = make_world(CallableAgentAdapter(
        Counting(), mode="live", recorder=resumed, prior=prior))
    world.run(days=2)
    assert ([e["response"] for e in resumed.entries]
            == [e["response"] for e in prior.entries])


def test_the_recording_says_how_much_of_it_was_resumed():
    """Criterion 7. A file stitched from two sessions says so on its face.

    Without this it claims to be one live run, and a reader counting
    entries cannot tell how many were paid for today.
    """
    prior, first = record_a_run(days=2)
    resumed = Transcript()
    world = make_world(CallableAgentAdapter(
        Counting(), mode="live", recorder=resumed, prior=prior))
    world.run(days=3)
    assert resumed.meta["replayed_from_prior"] == first.calls
    assert (resumed.meta["replayed_from_prior"]
            + resumed.meta["called_live"] == len(resumed.entries))
    assert resumed.meta["called_live"] > 0


def test_the_resume_count_is_not_accumulated_across_forks():
    """A fork shares one recorder and gives each arm its own adapter.

    A counter living on an adapter would split across the arms, and each
    half would understate the file it describes. This is why the count is
    derived from the two transcripts instead.
    """
    prior, _ = record_a_run(days=2)
    resumed = Transcript()
    world = make_world(CallableAgentAdapter(
        Counting(), mode="live", recorder=resumed, prior=prior))
    world.run(days=1)
    control, shock = world.fork("control", "shock")
    control.run(days=2)
    shock.run(days=2)

    assert control.agent.recorder is shock.agent.recorder is resumed
    assert control.agent.prior is shock.agent.prior is prior
    assert (resumed.meta["replayed_from_prior"]
            + resumed.meta["called_live"] == len(resumed.entries))
    assert resumed.meta["replayed_from_prior"] > 0


def test_a_prior_recorded_under_other_instructions_is_refused():
    """Criterion 6, refused before the run starts.

    The instructions do not travel in the input the key is computed over,
    so every recorded key would still match and the run would complete --
    answering the instructions you have now with decisions taken under the
    ones you had then. Nothing in the output would say the question
    changed.
    """
    prior, _ = record_a_run(days=1)
    prior.meta["instructions_digest"] = "a digest from another mandate"
    with pytest.raises(tf.ValidationError, match="different instructions"):
        CallableAgentAdapter(Counting(), mode="live",
                             recorder=Transcript(), prior=prior,
                             info=ci.AdapterInfo(
                                 framework="callable",
                                 instructions_digest="the current one"))


def test_a_prior_with_no_recorded_instructions_is_allowed():
    """It cannot be checked, and refusing it breaks every recording made
    before the digest was stamped for no gain -- those runs are no worse
    off than they were."""
    prior, _ = record_a_run(days=1)
    prior.meta.pop("instructions_digest", None)
    CallableAgentAdapter(Counting(), mode="live", recorder=Transcript(),
                         prior=prior)


def test_a_prior_outside_live_mode_is_refused():
    """Replay calls no provider, so there is nothing for a resume to save."""
    prior, _ = record_a_run(days=1)
    with pytest.raises(tf.ValidationError, match="resuming a live run"):
        CallableAgentAdapter(Counting(), mode="replay", transcript=prior,
                             prior=prior)


def test_a_prior_with_no_recorder_is_refused():
    """The resumed run would keep nothing, which is the loss it exists to
    stop."""
    prior, _ = record_a_run(days=1)
    with pytest.raises(tf.ValidationError, match="needs a recorder"):
        CallableAgentAdapter(Counting(), mode="live", prior=prior)


def test_every_adapter_takes_prior():
    """It is part of the shared contract, so no adapter can be the one that
    silently lacks it -- and `fork` rebuilds every adapter by passing all of
    its keywords back by name."""
    import inspect

    from tradefloor.integrations import (finrobot, langgraph, openai_agents,
                                         pydantic_ai)

    for cls in (CallableAgentAdapter, finrobot.FinRobotAdapter,
                langgraph.LangGraphAdapter, openai_agents.OpenAIAgentsAdapter,
                pydantic_ai.PydanticAIAdapter):
        parameters = inspect.signature(cls).parameters
        assert "prior" in parameters, f"{cls.__name__} takes no prior="
        assert (parameters["prior"].kind
                is inspect.Parameter.KEYWORD_ONLY), cls.__name__
