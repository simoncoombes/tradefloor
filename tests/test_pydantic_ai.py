"""The PydanticAI integration, without a provider.

CI does not call an LLM. A live decision needs an API key, costs money and
answers differently every time, so a suite depending on one would be neither
reproducible nor free. Everything here runs offline, and most of it runs the
REAL framework: a real `pydantic_ai.Agent`, its real output schema, its real
validation and its real retry loop, driven by `FunctionModel` and
`TestModel` -- the framework's own supported test doubles -- so what is
under test is the adapter and not a mock of one.

Two rails guard the "no provider" claim rather than assuming it.
`ALLOW_MODEL_REQUESTS = False` is set for every test in this module, which
makes any real provider call raise; and because that raise is a plain
`RuntimeError` rather than a framework exception, one test pins that too, so
a future `pytest.raises(AgentRunError)` cannot swallow it.

The shared contract lives in `tests/test_integrations.py` and is
parametrized over here rather than restated. What this file adds is the
surface specific to PydanticAI: how an existing user's agent survives
contact with the adapter, how the answer shape is bound, which framework
exception lands in which column, and the replay.

Without the extra installed this module skips whole, and the guard doing
that is load-bearing rather than tidy: see the comment on the
`importorskip` call. CI installs the extra by name and asserts it imports
before the batch runs, so the skip cannot quietly become a green lane.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import subprocess
import sys
from dataclasses import dataclass

import pytest

import tradefloor as tf
from tradefloor.integrations import common as ci
from tradefloor.integrations.pydantic_ai import (MANDATE, MANDATE_VERSION,
                                                 PydanticAIAdapter, render)

# The market helpers are shared with the contract file rather than copied:
# two rosters that drift apart would test two different markets under one
# name.
import test_integrations as contract

# Everything below needs the framework, so the whole module skips without it.
# At module scope and BEFORE the imports, because a bare `from pydantic_ai
# import ...` here is not a failure confined to this file: pytest interrupts
# the session on a collection error, so one missing optional extra stops all
# ~2,200 tests from running. `CONTRIBUTING.md` documents the development
# setup as maturin, pytest, pyarrow, numpy and pyyaml -- no frameworks -- so
# that is the setup a contributor following the repository's own
# instructions actually has.
pytest.importorskip("pydantic_ai",
                    reason="the PydanticAI adapter is an opt-in extra")

from pydantic_ai import Agent, RunContext, models  # noqa: E402
from pydantic_ai.messages import (ModelResponse, TextPart,  # noqa: E402
                                  ToolCallPart)
from pydantic_ai.models.function import AgentInfo, FunctionModel  # noqa: E402
from pydantic_ai.models.test import TestModel  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parent.parent
EXAMPLE = REPO / "examples" / "integrations" / "pydantic_ai_agent.py"


@pytest.fixture(autouse=True)
def no_provider_calls():
    """Every test in this module runs with model requests switched off.

    `TestModel` and `FunctionModel` are exempt by design, so this costs
    nothing here and turns any accidental real provider call into a loud
    failure instead of a bill. Restored afterwards because the flag is a
    module-level global and pytest shares the interpreter.
    """
    previous = models.ALLOW_MODEL_REQUESTS
    models.ALLOW_MODEL_REQUESTS = False
    try:
        yield
    finally:
        models.ALLOW_MODEL_REQUESTS = previous


# -- a user's own agent ------------------------------------------------------


@dataclass
class Desk:
    """A deps type a user declared before they had heard of Tradefloor."""

    name: str
    risk_limit: float


def user_agent(**kwargs):
    """An agent in the shape a PydanticAI user actually builds one.

    Its own `deps_type`, its own tool reading `RunContext`, its own
    instructions and its own output type. `defer_model_check=True` so
    construction needs no API key -- the model is resolved eagerly from a
    name otherwise, and `Agent("openai:gpt-5.2")` raises without one.
    """
    agent = Agent("openai:gpt-5.2", deps_type=Desk, defer_model_check=True,
                  instructions="USER INSTRUCTIONS.", **kwargs)

    @agent.tool
    def house_risk_limit(ctx: RunContext[Desk]) -> float:
        """Reads the user's own deps. If the adapter touched deps, this
        raises AttributeError instead of returning a number."""
        return ctx.deps.risk_limit

    return agent


BUY = {"actions": [{"symbol": "TECH_A", "side": "BUY", "quantity": 2000}],
       "rationale": "contract"}


def answering(decision, *, call_the_tool=False, seen=None):
    """A `FunctionModel` that answers with `decision` through the output tool.

    This is the honest offline double: everything below the wire runs for
    real, including the output schema the adapter bound and the validation
    of what comes back.
    """
    state = {"tool_called": False}

    def respond(messages, info: AgentInfo) -> ModelResponse:
        if seen is not None:
            seen.append(info)
        if call_the_tool and not state["tool_called"]:
            state["tool_called"] = True
            return ModelResponse(parts=[ToolCallPart("house_risk_limit", {})])
        return ModelResponse(
            parts=[ToolCallPart(info.output_tools[0].name, decision)])

    return FunctionModel(respond)


def run_world(model, *, days=1, agent=None, **kwargs):
    adapter = PydanticAIAdapter(agent or user_agent(),
                                deps=Desk("macro", 0.25), model=model,
                                **kwargs)
    world = contract.make_world(adapter)
    world.run(days=days)
    return world, adapter


# -- the shared contract -----------------------------------------------------


class Scripted(PydanticAIAdapter):
    """A PydanticAIAdapter whose framework call is supplied, not made.

    It replaces `_run`, the ONE method that reaches PydanticAI. Everything
    on either side runs unmodified: the render, the digest, the recording,
    the unwrap, the two-stage validation, the cadence and the fork. A double
    that reimplemented any of those would be testing itself.

    `respond` is keyword-defaulted because `fork()` rebuilds the twin as
    `type(self)(**self.fork_kwargs())`, so a subclass with a required
    positional argument could not be forked.
    """

    def __init__(self, respond=None, **kwargs):
        kwargs.setdefault("agent", user_agent())
        kwargs.setdefault("deps", Desk("macro", 0.25))
        super().__init__(**kwargs)
        self.respond = respond
        self.prompts: list[str] = []

    def _run(self, prompt, payload, obs):
        self.prompts.append(prompt)
        return self.respond(payload)

    def fork_kwargs(self):
        kwargs = super().fork_kwargs()
        kwargs["respond"] = self.respond
        return kwargs


@pytest.mark.parametrize("check", contract.CONTRACT_CHECKS,
                         ids=lambda f: f.__name__)
def test_the_adapter_meets_the_shared_contract(check):
    check(lambda respond: Scripted(respond))


# -- the optional dependency -------------------------------------------------


def test_the_adapter_module_imports_with_no_framework_installed():
    """The whole reason the import is inside `_run`. Replaying a recorded
    run, and `import tradefloor`, must never require PydanticAI -- and must
    not require pydantic either, since `decision_model()` is built on first
    request rather than on import. A subprocess, because this suite has
    already imported all of it."""
    banned = ("pydantic", "pydantic_ai", "openai", "httpx", "httpx2")
    code = ("import sys; "
            "import tradefloor.integrations.pydantic_ai; "
            f"hit = [m for m in {banned!r} if m in sys.modules]; "
            "assert not hit, f'the adapter module imported {hit}'; "
            "print('ok')")
    done = subprocess.run([sys.executable, "-c", code],
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr


def test_a_replay_adapter_can_be_built_with_no_framework_installed():
    """Constructing one must not import the framework either: the version
    that goes in the metadata is read through importlib.metadata, not by
    importing the package."""
    code = ("import sys; "
            "from tradefloor.integrations.common import Transcript; "
            "from tradefloor.integrations.pydantic_ai import "
            "PydanticAIAdapter; "
            "a = PydanticAIAdapter(mode='replay', transcript=Transcript()); "
            "assert 'pydantic_ai' not in sys.modules, 'construction "
            "imported the framework'; "
            "assert a.info.framework == 'pydantic-ai'; "
            "print('ok')")
    done = subprocess.run([sys.executable, "-c", code],
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr


def test_live_mode_without_the_framework_names_the_extra(monkeypatch):
    """`None` in sys.modules is how Python spells "this import fails", so
    this exercises the real `require` call inside `_run` rather than a
    stand-in for it."""
    monkeypatch.setitem(sys.modules, "pydantic_ai", None)
    adapter = PydanticAIAdapter(user_agent(), deps=Desk("macro", 0.25))
    world = contract.make_world(adapter)
    with pytest.raises(ci.MissingDependencyError) as excinfo:
        world.run(days=1)
    message = str(excinfo.value)
    assert 'pip install "tradefloor[pydantic-ai]"' in message
    assert isinstance(excinfo.value.__cause__, ImportError)


# -- construction ------------------------------------------------------------


def test_an_unknown_mode_is_refused():
    with pytest.raises(tf.ValidationError, match="replay"):
        PydanticAIAdapter(user_agent(), mode="dry-run")


def test_replay_mode_refuses_without_a_transcript():
    with pytest.raises(tf.ValidationError, match="transcript"):
        PydanticAIAdapter(mode="replay")


def test_live_mode_refuses_without_an_agent():
    """`agent` defaults to None so this refusal owns the message: a bare
    TypeError from the signature would not say what a valid agent is."""
    with pytest.raises(tf.ValidationError, match="pydantic_ai.Agent"):
        PydanticAIAdapter()


def test_live_mode_refuses_something_that_is_not_an_agent():
    with pytest.raises(tf.ValidationError, match="pydantic_ai.Agent"):
        PydanticAIAdapter("openai:gpt-5.2")


def test_a_request_limit_below_one_is_refused():
    with pytest.raises(tf.ValidationError, match="request_limit"):
        PydanticAIAdapter(user_agent(), request_limit=0)


# -- the user's agent keeps working ------------------------------------------


def test_the_users_deps_reach_the_users_tools_untouched():
    """The claim the whole adapter rests on. The observation goes in the
    prompt precisely so that this keeps working: the user's tool reads
    `ctx.deps.risk_limit` off the object the user passed, and an adapter
    that wrapped deps in a container of its own would fail here with an
    AttributeError."""
    seen = {}
    agent = Agent("openai:gpt-5.2", deps_type=Desk, defer_model_check=True)

    @agent.tool
    def house_risk_limit(ctx: RunContext[Desk]) -> float:
        seen["deps"] = ctx.deps
        return ctx.deps.risk_limit

    deps = Desk("macro", 0.25)
    adapter = PydanticAIAdapter(agent, deps=deps,
                                model=answering(BUY, call_the_tool=True))
    world = contract.make_world(adapter)
    world.run(days=1)

    assert seen["deps"] is deps, "the adapter did not pass deps through"
    assert world.portfolio.positions["TECH_A"].quantity > 0


def test_the_mandate_is_added_to_the_users_instructions_not_substituted():
    """`run(instructions=...)` appends; `Agent.override(instructions=...)`
    would replace, including anything a capability contributed. The user
    still knows what they told their agent."""
    seen = []
    run_world(answering(BUY, seen=seen))
    instructions = seen[0].instructions
    assert "USER INSTRUCTIONS." in instructions
    assert MANDATE.strip() in instructions
    assert instructions.index("USER INSTRUCTIONS.") < \
        instructions.index(MANDATE.strip()[:40]), (
            "the mandate must come after the user's own instructions")


def test_the_agents_own_output_type_is_not_mutated():
    """The output type is bound per RUN. The agent a user handed us is the
    same object afterwards, still answering its own shape."""
    agent = user_agent(output_type=str)
    run_world(answering(BUY), agent=agent)
    assert agent.output_type is str


def test_the_agents_own_model_is_not_mutated():
    agent = user_agent()
    run_world(answering(BUY), agent=agent)
    assert agent.model == "openai:gpt-5.2"


# -- the bound answer shape --------------------------------------------------


def test_the_bound_schema_states_the_market_rules_before_the_model_answers():
    """The payoff of binding `decision_model()` rather than a bare type: the
    side enum and the non-negative quantity are in the schema the model is
    SHOWN, so a provider that enforces its schema cannot emit a bad value at
    all, and one that does not is told exactly what was wrong."""
    seen = []
    run_world(answering(BUY, seen=seen))
    schema = seen[0].output_tools[0].parameters_json_schema
    action = schema["$defs"][next(iter(schema["$defs"]))]["properties"]
    assert action["side"]["enum"] == list(ci.SIDES)
    assert action["quantity"]["minimum"] == 0
    assert action["symbol"]["minLength"] == 1
    assert "actions" in schema["required"], (
        "an absent actions key must be impossible to emit, not merely "
        "refused after the fact")


def test_the_framework_retry_loop_fixes_a_bad_decision_within_the_turn():
    """The reason the constraints belong in the model and not only in
    `parse_decision`. A negative quantity is caught by the run's output
    schema, fed back to the model as the pydantic error, and corrected --
    inside the turn, at the cost of one retry rather than the decision
    point."""
    calls = {"n": 0}

    def flaky(messages, info: AgentInfo) -> ModelResponse:
        calls["n"] += 1
        if calls["n"] == 1:
            bad = {"actions": [{"symbol": "TECH_A", "side": "BUY",
                                "quantity": -2000}]}
            return ModelResponse(
                parts=[ToolCallPart(info.output_tools[0].name, bad)])
        return ModelResponse(
            parts=[ToolCallPart(info.output_tools[0].name, BUY)])

    world, adapter = run_world(FunctionModel(flaky))
    assert calls["n"] == 2, "the framework did not retry"
    assert world.portfolio.positions["TECH_A"].quantity > 0
    assert adapter.decision()["actions"][0]["quantity"] == 2000


def test_a_schema_violation_is_a_decision_error_carrying_its_cause():
    """When the retry budget cannot fix it, the run fails. That is the
    framework failing its OUTPUT contract, not the call failing to complete,
    so it must land in the DecisionError column -- and the pydantic error
    naming the field must still be reachable.

    Asserted on the __cause__ chain and never on the message: a text answer
    where the output tool was required produces the identical text, and a
    test that keyed on it would pass for the wrong reason (see the next
    test)."""
    always_bad = {"actions": [{"symbol": "TECH_A", "side": "SIDEWAYS",
                               "quantity": 1}]}

    def bad(messages, info: AgentInfo) -> ModelResponse:
        return ModelResponse(
            parts=[ToolCallPart(info.output_tools[0].name, always_bad)])

    with pytest.raises(ci.DecisionError) as excinfo:
        run_world(FunctionModel(bad))

    from pydantic import ValidationError as PydanticValidationError
    from pydantic_ai import UnexpectedModelBehavior

    assert isinstance(excinfo.value.__cause__, UnexpectedModelBehavior)
    assert isinstance(excinfo.value.__cause__.__cause__,
                      PydanticValidationError)
    assert "'BUY', 'SELL' or 'HOLD'" in str(excinfo.value.__cause__.__cause__)


def test_a_text_answer_produces_the_same_message_and_a_different_cause():
    """The reason the test above asserts on `__cause__`. A model that
    answered in prose where the output tool was required exhausts the same
    budget and produces the same sentence, and only the chain tells the two
    apart."""
    def prose(messages, info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart("I would rather not say.")])

    with pytest.raises(ci.DecisionError) as excinfo:
        run_world(FunctionModel(prose))

    from pydantic import ValidationError as PydanticValidationError
    assert "Exceeded maximum output retries" in str(excinfo.value.__cause__)
    assert not isinstance(excinfo.value.__cause__.__cause__,
                          PydanticValidationError)


def test_an_agent_with_output_validators_is_refused_with_the_fix():
    """PydanticAI forbids a per-run output_type on an agent carrying output
    validators. The framework's own message is accurate and says nothing
    about what to do, so the adapter names both the cause and the escape."""
    agent = user_agent()

    @agent.output_validator
    def _check(value):
        return value

    adapter = PydanticAIAdapter(agent, deps=Desk("macro", 0.25),
                                model=answering(BUY))
    world = contract.make_world(adapter)
    with pytest.raises(ci.FrameworkError) as excinfo:
        world.run(days=1)
    message = str(excinfo.value)
    assert "output_validator" in message
    assert "bind_output_type=False" in message
    assert "instead of returning a decision" not in message, (
        "the refusal must be a FrameworkError already, or act() wraps it a "
        "second time and the message describes the adapter's plumbing "
        "rather than the mistake")


def test_bind_output_type_false_lets_the_agents_own_type_stand():
    """The escape hatch, and proof that `parse_decision` stays total: the
    agent answers with its own type -- here a JSON string -- and the shared
    validator takes it from there."""
    agent = user_agent(output_type=str)

    @agent.output_validator
    def _check(value: str) -> str:
        return value

    def as_text(messages, info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart(json.dumps(BUY))])

    adapter = PydanticAIAdapter(agent, deps=Desk("macro", 0.25),
                                model=FunctionModel(as_text),
                                bind_output_type=False)
    world = contract.make_world(adapter)
    world.run(days=1)
    assert world.portfolio.positions["TECH_A"].quantity > 0


# -- the other error columns -------------------------------------------------


def test_a_usage_limit_is_catchable_by_name():
    """A budget stop is CONTROL FLOW: the caller set the limit on purpose.
    `act()` wraps every non-Integration exception, so left alone this would
    arrive as a generic FrameworkError reachable only through `__cause__`.
    It gets a name instead, and stays inside the shared family."""
    def loops(messages, info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[ToolCallPart("house_risk_limit", {})])

    from pydantic_ai import UsageLimitExceeded
    from tradefloor.integrations.pydantic_ai import UsageLimitReached

    with pytest.raises(UsageLimitReached) as excinfo:
        run_world(FunctionModel(loops), request_limit=3)

    assert isinstance(excinfo.value, ci.FrameworkError), (
        "the named type must stay inside the shared error family, or "
        "callers catching FrameworkError stop seeing it")
    assert isinstance(excinfo.value, ci.IntegrationError), (
        "it must be an IntegrationError, or act() wraps it a second time")
    assert isinstance(excinfo.value.__cause__, UsageLimitExceeded)
    assert "request budget of 3" in str(excinfo.value)


# The control-flow exceptions, raised straight through `ask()` rather than
# provoked from the framework. The shared contract check raises a bare
# RuntimeError, which SHOULD be wrapped; nothing in it raises a control-flow
# exception, so these pin the half the contract cannot see.
class Raising(Scripted):
    """A Scripted adapter whose framework call raises what it is given."""

    def __init__(self, error=None, **kwargs):
        # `respond` is popped rather than passed alongside, because
        # `fork_kwargs` puts one back and the constructor would then get the
        # argument twice. Raising never consults it.
        kwargs.pop("respond", None)
        super().__init__(respond=lambda payload: None, **kwargs)
        self.error = error

    def _run(self, prompt, payload, obs):
        raise self.error

    def fork_kwargs(self):
        kwargs = super().fork_kwargs()
        kwargs["error"] = self.error
        return kwargs


def _raise_through_ask(error):
    world = contract.make_world(Raising(error))
    world.run(days=1)


def test_a_usage_limit_raised_through_ask_keeps_its_name():
    from pydantic_ai import UsageLimitExceeded
    from tradefloor.integrations.pydantic_ai import UsageLimitReached

    with pytest.raises(UsageLimitReached) as excinfo:
        _raise_through_ask(UsageLimitExceeded("budget spent"))
    assert isinstance(excinfo.value.__cause__, UsageLimitExceeded)


def test_unexpected_model_behavior_raised_through_ask_is_a_decision_error():
    from pydantic_ai import UnexpectedModelBehavior

    with pytest.raises(ci.DecisionError) as excinfo:
        _raise_through_ask(
            UnexpectedModelBehavior("Exceeded maximum output retries (1)"))
    assert isinstance(excinfo.value.__cause__, UnexpectedModelBehavior)
    assert "no decision at all" in str(excinfo.value)


def test_a_content_filter_raised_through_ask_stays_a_framework_error():
    from pydantic_ai.exceptions import ContentFilterError

    with pytest.raises(ci.FrameworkError) as excinfo:
        _raise_through_ask(ContentFilterError("blocked"))
    from tradefloor.integrations.pydantic_ai import UsageLimitReached
    assert not isinstance(excinfo.value, UsageLimitReached)
    assert isinstance(excinfo.value.__cause__, ContentFilterError)


def test_a_bare_runtime_error_through_ask_is_still_wrapped():
    """The default must not have been broken by the special cases above."""
    with pytest.raises(ci.FrameworkError) as excinfo:
        _raise_through_ask(RuntimeError("the framework exploded"))
    assert isinstance(excinfo.value.__cause__, RuntimeError)
    assert "the framework exploded" in str(excinfo.value)


def test_a_run_cancelled_is_deliberately_not_special_cased():
    """A recorded choice rather than an oversight: `RunCancelled` arises only
    if the user's own tool calls `ctx.cancel()`, and nothing in this repo
    does. It arrives as a FrameworkError with the original on `__cause__`,
    which is one line to change when something needs it."""
    from pydantic_ai import RunCancelled

    with pytest.raises(ci.FrameworkError) as excinfo:
        _raise_through_ask(RunCancelled("a tool asked to stop"))
    assert isinstance(excinfo.value.__cause__, RunCancelled)


def test_the_two_kinds_of_bad_output_are_named_in_the_message():
    """`UnexpectedModelBehavior` covers a schema violation and a
    no-decision-at-all, with the identical framework message. The adapter
    reads the chain once and says which, so the common case needs no
    chain-walking."""
    bad_field = {"actions": [{"symbol": "TECH_A", "side": "SIDEWAYS",
                              "quantity": 1}]}

    def wrong_field(messages, info: AgentInfo) -> ModelResponse:
        return ModelResponse(
            parts=[ToolCallPart(info.output_tools[0].name, bad_field)])

    def prose(messages, info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart("I would rather not say.")])

    with pytest.raises(ci.DecisionError) as schema_failure:
        run_world(FunctionModel(wrong_field))
    with pytest.raises(ci.DecisionError) as no_decision:
        run_world(FunctionModel(prose))

    assert "did not satisfy the decision schema" in str(schema_failure.value)
    assert "actions.0.side" in str(schema_failure.value), (
        "the message must name the field, which is the whole point of "
        "reading the chain here")
    assert "no decision at all" in str(no_decision.value)


def test_a_content_filter_is_a_framework_failure_not_a_bad_decision():
    """`ContentFilterError` subclasses `UnexpectedModelBehavior`, so the
    naive translation would file a provider blocking the exchange as the
    agent answering badly. The model never answered at all."""
    from pydantic_ai.exceptions import ContentFilterError

    def blocked(messages, info: AgentInfo) -> ModelResponse:
        raise ContentFilterError("the provider blocked this exchange")

    with pytest.raises(ci.FrameworkError) as excinfo:
        run_world(FunctionModel(blocked))
    assert isinstance(excinfo.value.__cause__, ContentFilterError)


def test_a_provider_failure_keeps_its_chain_through_the_real_framework():
    """The contract check covers this through the scripted seam; this runs
    it through the real framework, where the exception has to survive
    PydanticAI's own plumbing and the async bridge as well."""
    def explode(messages, info: AgentInfo) -> ModelResponse:
        raise ConnectionError("provider unreachable")

    with pytest.raises(ci.FrameworkError) as excinfo:
        run_world(FunctionModel(explode))
    assert isinstance(excinfo.value.__cause__, ConnectionError)
    assert "provider unreachable" in str(excinfo.value)


def test_a_real_provider_under_the_flag_raises_a_plain_runtime_error(
        monkeypatch):
    """The rail this module runs on, pinned. `ALLOW_MODEL_REQUESTS = False`
    raises `RuntimeError` and NOT an `AgentRunError`, so a future test that
    guarded with `pytest.raises(AgentRunError)` would let a real provider
    call through.

    A dummy key is set because the flag is checked when the request is MADE:
    without one, model construction fails first and the flag is never
    reached, which would make this test pass for the wrong reason.
    """
    from pydantic_ai import AgentRunError

    monkeypatch.setenv("OPENAI_API_KEY", "sk-not-a-real-key")
    agent = Agent("openai:gpt-5.2", defer_model_check=True)
    adapter = PydanticAIAdapter(agent)
    world = contract.make_world(adapter)
    with pytest.raises(ci.FrameworkError) as excinfo:
        world.run(days=1)
    cause = excinfo.value.__cause__
    assert isinstance(cause, RuntimeError)
    assert not isinstance(cause, AgentRunError), (
        "ALLOW_MODEL_REQUESTS raises a plain RuntimeError; a guard written "
        "for the framework's exception family would not catch it")
    assert "ALLOW_MODEL_REQUESTS" in str(cause)


def test_a_missing_api_key_is_refused_before_any_request(monkeypatch):
    """The other half of the pair above: with no key at all the framework
    refuses during model construction, as a `UserError`, and the adapter
    turns that into one actionable FrameworkError rather than letting it
    arrive double-wrapped."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    adapter = PydanticAIAdapter(Agent("openai:gpt-5.2",
                                      defer_model_check=True))
    world = contract.make_world(adapter)
    with pytest.raises(ci.FrameworkError) as excinfo:
        world.run(days=1)
    assert "OPENAI_API_KEY" in str(excinfo.value)
    assert "instead of returning a decision" not in str(excinfo.value)


# -- what the agent is shown -------------------------------------------------


def test_the_prompt_is_the_payload_and_nothing_else():
    """The agent is shown the serialized observation and no commentary. The
    allowlist test upstream says what may be in the payload; this says the
    prompt is that payload and not a hand-written description of it, which
    is what keeps the two from drifting."""
    agent = Scripted(lambda payload: {"actions": []})
    world = contract.make_world(agent)
    world.run(days=1)

    parsed = json.loads(agent.prompts[0])
    assert set(parsed) == contract.PAYLOAD_KEYS
    assert set(parsed["assets"][0]) == contract.ASSET_KEYS
    assert set(parsed["macro"]) == set(ci.OBSERVABLE_MACRO)


def test_the_render_is_stable_under_key_order():
    """The replay key is a hash of these exact bytes, so two runs that show
    the agent the same market must produce the same string whatever order
    the payload was built in."""
    one = {"day": 1, "step": 6, "assets": [{"symbol": "A", "price": 1.0}]}
    two = {"assets": [{"price": 1.0, "symbol": "A"}], "step": 6, "day": 1}
    assert render(one) == render(two)
    assert ci.digest(render(one)) == ci.digest(render(two))


def test_a_payload_value_that_is_not_json_raises_rather_than_stringifying():
    """A value quietly rendered as its repr would change the replay key
    without changing anything a reader could see."""
    with pytest.raises(TypeError, match="allowlist"):
        render({"assets": [{"symbol": object()}]})


def test_the_real_framework_path_never_reaches_ground_truth():
    """The contract check runs this through the scripted seam. This runs the
    whole real path -- render, the Agent, the output schema, the unwrap --
    against a sealed engine, so a future edit that reached for the answer
    key anywhere along it fails on the access."""
    world, adapter = run_world(answering(BUY), days=2)
    sealed = contract._observation(world, engine=contract.Sealed(world.engine))
    probe = PydanticAIAdapter(user_agent(), deps=Desk("macro", 0.25),
                              model=answering(BUY), every=1)
    probe.history = [list(row) for row in adapter.history]
    orders = probe.act(sealed)
    assert orders, "the sealed decision produced nothing to check"


# -- async -------------------------------------------------------------------


def test_the_adapter_works_inside_a_running_event_loop():
    """The notebook case. `Agent.run_sync` calls `loop.run_until_complete`
    and raises `RuntimeError: This event loop is already running` here; the
    adapter calls the async entry point through the shared bridge, so both a
    script and a notebook work."""
    async def notebook_cell():
        world, _ = run_world(answering(BUY))
        return world.portfolio.positions["TECH_A"].quantity

    assert asyncio.run(notebook_cell()) > 0


def test_the_frameworks_own_sync_entry_point_would_have_failed_here():
    """Pins the reason for the rule, so that "just call run_sync" cannot be
    reintroduced as a simplification."""
    async def notebook_cell():
        agent = Agent("test", output_type=str)
        with pytest.raises(RuntimeError, match="already running"):
            agent.run_sync("x", model=TestModel())

    asyncio.run(notebook_cell())


def test_an_override_set_outside_does_not_cross_the_bridge():
    """A documented trap, pinned so it stays documented rather than becoming
    a surprise. `Agent.override` is built on context variables, and the
    shared bridge runs the coroutine on another thread when a loop is
    already running -- and `concurrent.futures` does not propagate context.
    The adapter never relies on it: the model is a per-run argument. A user
    reaching for `override` in a notebook is meeting this, not a bug."""
    agent = Agent("test", output_type=str)
    inside = {}

    async def notebook_cell():
        with agent.override(model=TestModel(custom_output_text="overridden")):
            def look(messages, info: AgentInfo) -> ModelResponse:
                inside["reached"] = True
                return ModelResponse(parts=[TextPart("not overridden")])

            return ci.run_sync(agent.run("x", model=FunctionModel(look)))

    result = asyncio.run(notebook_cell())
    assert inside.get("reached"), "the per-run model did not take effect"
    assert result.output == "not overridden"


# -- recording and replay ----------------------------------------------------


def test_the_recorder_writes_the_conventional_entry_shape():
    recorder = ci.Transcript(meta={"note": "test"})
    world, adapter = run_world(answering(BUY), recorder=recorder)
    assert len(recorder) == 1
    entry = recorder.entries[0]
    assert set(entry) == {"arm", "step", "day", "digest", "prompt", "response"}
    assert entry["digest"] == ci.digest(entry["prompt"])
    assert json.loads(entry["response"])["actions"][0]["symbol"] == "TECH_A"


def test_a_text_output_agent_records_and_replays_without_double_encoding():
    """The `bind_output_type=False` path writes a recording too, and its
    output is already JSON TEXT. Encoding it again would store a JSON string
    containing JSON, and the replay would parse one level, find a `str`
    where a decision was expected, and fail -- at replay time, against a
    recording that looked fine when it was written."""
    agent = user_agent(output_type=str)

    @agent.output_validator
    def _check(value: str) -> str:
        return value

    def as_text(messages, info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart(json.dumps(BUY))])

    recorder = ci.Transcript()
    live = PydanticAIAdapter(agent, deps=Desk("macro", 0.25),
                             model=FunctionModel(as_text),
                             bind_output_type=False, recorder=recorder)
    contract.make_world(live).run(days=1)

    assert json.loads(recorder.entries[0]["response"])["actions"], (
        "the recorded response must be the decision, not a JSON string "
        "wrapping one")

    replayed = PydanticAIAdapter(mode="replay", transcript=recorder)
    world = contract.make_world(replayed)
    world.run(days=1)
    assert world.portfolio.positions["TECH_A"].quantity > 0


def test_replay_reproduces_a_recorded_decision_without_the_framework():
    """A recorded run replays with nothing imported. The key is the digest
    of the exact input, so this also proves the render is deterministic
    across two separate adapters."""
    recorder = ci.Transcript()
    live_world, _ = run_world(answering(BUY), recorder=recorder)

    replayed = PydanticAIAdapter(mode="replay", transcript=recorder)
    world = contract.make_world(replayed)
    world.run(days=1)

    assert world.portfolio.positions["TECH_A"].quantity == \
        live_world.portfolio.positions["TECH_A"].quantity
    assert replayed.decision() == _decision_of(live_world)


def _decision_of(world):
    return world.agent.decision()


def test_the_mandate_names_both_size_limits():
    """A mandate that names the participation cap and not the funding cap is
    a trap: sizing each asset to its own `max_order_shares` ordinarily asks
    for several times the equity available, and the order is REFUSED rather
    than reduced. Four independent agents made exactly that mistake, which
    is why the payload gained `buying_power` and why version 2 of this
    mandate names it."""
    assert "max_order_shares" in MANDATE
    assert "buying_power" in MANDATE
    assert "max_leverage" in MANDATE
    assert MANDATE_VERSION == "2", (
        "the mandate changed meaning, so the version must move with it -- a "
        "transcript recorded under the old text is a different experiment")


def test_a_recording_stamps_what_it_ran_under_without_being_asked():
    """The replay guard can only refuse a mismatch if the recording says
    what it ran under. Stamped on first write rather than left to the
    caller, because a guard that arms only when someone remembers to set
    `meta` is off in exactly the runs nobody was careful about."""
    recorder = ci.Transcript()
    run_world(answering(BUY), recorder=recorder)
    assert recorder.meta["instructions_digest"] == ci.digest(MANDATE)
    assert recorder.meta["instructions_version"] == MANDATE_VERSION
    assert recorder.meta["framework"] == "pydantic-ai"


def test_an_explicit_meta_is_not_overwritten_by_the_stamp():
    recorder = ci.Transcript(meta={"instructions_digest": "deadbeef",
                                   "note": "mine"})
    run_world(answering(BUY), recorder=recorder)
    assert recorder.meta["instructions_digest"] == "deadbeef"
    assert recorder.meta["note"] == "mine"


def test_a_replay_under_different_instructions_is_refused_at_construction():
    """The hole this closes: the replay key is a digest of the OBSERVATION
    and the mandate travels separately, so editing the mandate leaves every
    key intact. Every lookup hits, the run completes, and old answers are
    served to a new question. Nothing about the key would notice.

    Refused when the adapter is BUILT, not when it first decides. Everything
    the check needs is known before the market opens, and a replay that
    refuses twenty simulated days in has already spent the reader's time on
    a fault that was knowable at construction.
    """
    recorder = ci.Transcript()
    run_world(answering(BUY), recorder=recorder)

    with pytest.raises(ci.DecisionError) as excinfo:
        PydanticAIAdapter(mode="replay", transcript=recorder,
                          instructions="A DIFFERENT MANDATE.")

    message = str(excinfo.value)
    assert ci.digest(MANDATE) in message, "the message must name both digests"
    assert ci.digest("A DIFFERENT MANDATE.") in message
    # The message has to say what to DO, not only that something differs.
    assert "instructions=" in message and "re-record" in message


def test_a_forked_arm_is_checked_too():
    """`fork()` rebuilds the twin through the same constructor, so both arms
    of a comparison get the check rather than only the one built by hand."""
    recorder = ci.Transcript()
    _, live = run_world(answering(BUY), recorder=recorder)

    replayed = PydanticAIAdapter(mode="replay", transcript=recorder,
                                 instructions=MANDATE)
    twin = replayed.fork()
    assert type(twin) is PydanticAIAdapter

    replayed.instructions = "A DIFFERENT MANDATE."
    with pytest.raises(ci.DecisionError):
        replayed.fork()


def test_the_keys_alone_would_not_have_noticed():
    """Proves the guard is load-bearing rather than belt-and-braces: with
    the check neutered, a mandate change replays clean and silently wrong.

    Neutered by SUBCLASSING rather than by assigning over the attribute,
    because the check now runs inside `__init__` -- an instance patched
    afterwards would already have passed it, and this test would then be
    proving nothing.
    """
    class Unguarded(PydanticAIAdapter):
        def _check_instructions(self):
            pass

    recorder = ci.Transcript()
    live_world, _ = run_world(answering(BUY), recorder=recorder)

    replayed = Unguarded(mode="replay", transcript=recorder,
                         instructions="A DIFFERENT MANDATE.")
    world = contract.make_world(replayed)
    world.run(days=1)

    assert world.portfolio.positions["TECH_A"].quantity == \
        live_world.portfolio.positions["TECH_A"].quantity, (
            "without the guard the run completes and looks correct, which "
            "is exactly why printing the version and hoping is not enough")


def test_a_transcript_with_no_recorded_digest_still_replays():
    """Only a MISMATCH is refused. Transcripts predate this check, and
    refusing one that never claimed a mandate would break recordings that
    are not known to be wrong."""
    recorder = ci.Transcript()
    run_world(answering(BUY), recorder=recorder)
    recorder.meta.pop("instructions_digest")

    replayed = PydanticAIAdapter(mode="replay", transcript=recorder,
                                 instructions="A DIFFERENT MANDATE.")
    world = contract.make_world(replayed)
    world.run(days=1)
    assert world.portfolio.positions["TECH_A"].quantity > 0


def test_a_missing_recording_raises_and_says_which_step():
    replayed = PydanticAIAdapter(mode="replay", transcript=ci.Transcript())
    world = contract.make_world(replayed)
    with pytest.raises(ci.DecisionError) as excinfo:
        world.run(days=1)
    message = str(excinfo.value)
    assert "step 0" in message and "digest" in message


def test_a_recorded_run_round_trips_through_a_file(tmp_path):
    recorder = ci.Transcript(meta={"framework": "pydantic-ai"})
    run_world(answering(BUY), recorder=recorder)
    path = tmp_path / "run.json"
    recorder.save(path)

    replayed = PydanticAIAdapter(mode="replay",
                                 transcript=ci.Transcript.load(path))
    world = contract.make_world(replayed)
    world.run(days=1)
    assert world.portfolio.positions["TECH_A"].quantity > 0


def test_a_transcript_carries_no_credential():
    """The file gets committed as a fixture, so what goes in it matters."""
    recorder = ci.Transcript()
    run_world(answering(BUY), recorder=recorder)
    published = recorder.to_json()
    for secret in ("api_key", "sk-", "Bearer ", "Authorization"):
        assert secret not in published


# -- fork and state ----------------------------------------------------------


def test_a_fork_shares_the_agent_and_copies_the_decision_state():
    """Both arms must ask the SAME agent -- it is the thing under test --
    while the price memory and the last decision are copied, because those
    are what could make two arms diverge for a reason other than the
    intervention."""
    _, adapter = run_world(answering(BUY), days=2)
    twin = adapter.fork()

    assert type(twin) is PydanticAIAdapter
    assert twin.agent is adapter.agent
    assert twin.deps is adapter.deps
    assert twin.model is adapter.model
    assert twin.state() == adapter.state()
    assert twin.history is not adapter.history


def test_a_fork_keeps_the_subclass():
    agent = Scripted(lambda payload: {"actions": []})
    world = contract.make_world(agent)
    world.run(days=1)
    twin = world.agent.fork()
    assert type(twin) is Scripted
    assert twin.respond is world.agent.respond


def test_the_published_state_carries_no_credential():
    """`state()` is printed by the fork agreement and written into
    artifacts, and a user's deps object is exactly the kind of thing that
    holds a key. It stays out, and so does the agent."""
    @dataclass
    class Secretive:
        api_key: str

    agent = Agent("openai:gpt-5.2", deps_type=Secretive,
                  defer_model_check=True)
    adapter = PydanticAIAdapter(agent, deps=Secretive("sk-not-a-real-key"),
                                model=answering(BUY))
    published = json.dumps(adapter.state())
    assert "sk-not-a-real-key" not in published
    assert "api_key" not in published


def test_the_state_names_the_instructions_by_digest_not_by_text():
    """Two arms running different mandates is a different experiment, and
    `agree` compares these dictionaries. The digest is what makes that
    visible without printing the whole mandate into every artifact."""
    adapter = PydanticAIAdapter(user_agent(), model=answering(BUY))
    other = PydanticAIAdapter(user_agent(), model=answering(BUY),
                              instructions="A DIFFERENT MANDATE.")
    assert adapter.state()["instructions_digest"] == ci.digest(MANDATE)
    assert adapter.state()["instructions_digest"] != \
        other.state()["instructions_digest"]


def test_a_replayed_run_records_the_same_chain_a_live_one_does():
    """`record_exchange` is called before the live/replay branch on purpose.
    An artifact showing what the model SAID beside what it TRADED must work
    off a replayed run too, or the recording cannot be audited against the
    run that used it."""
    recorder = ci.Transcript()
    run_world(answering(BUY), recorder=recorder)

    replayed = PydanticAIAdapter(mode="replay", transcript=recorder)
    contract.make_world(replayed).run(days=1)

    entry = replayed.record[0]
    assert set(entry) == {"arm", "step", "day", "digest", "prompt",
                          "response", "decision", "orders", "clipped"}
    assert entry["digest"] == recorder.entries[0]["digest"], (
        "a replayed record must join to the recording it replayed")


def test_the_provenance_names_the_entry_point_and_the_mandate_version():
    """What `Transcript.meta` should carry. The entry point matters to
    anyone reproducing the run -- the async `run`, not `run_sync` -- and the
    mandate version is how a reader notices a transcript replayed under
    different instructions."""
    adapter = PydanticAIAdapter(user_agent(), model=TestModel())
    provenance = adapter.provenance()
    assert provenance["entry_point"] == "pydantic_ai.Agent.run"
    assert provenance["instructions_version"] == MANDATE_VERSION
    assert provenance["framework_url"].endswith("pydantic-ai")
    json.dumps(provenance)      # it goes in a file


def test_the_metadata_names_the_framework_and_the_version():
    adapter = PydanticAIAdapter(user_agent(), model=TestModel())
    assert adapter.info.framework == "pydantic-ai"
    assert adapter.info.framework_version, "the installed version is recorded"
    assert adapter.info.decision_schema_version == ci.DECISION_SCHEMA_VERSION
    assert "pydantic-ai" in adapter.info.reference()


# -- the shipped example -----------------------------------------------------


# -- the committed recording -------------------------------------------------
#
# The fixture tests live in `tests/test_pydantic_ai_replay.py`, NOT here.
# This module guards itself with `pytest.importorskip("pydantic_ai")`, so
# everything in it skips on an install without the extra -- and replaying a
# recording needs no framework at all. Gating that coverage behind an import
# it does not use would leave the committed artefact unread for exactly the
# contributor the importorskip was written for. See that file's docstring.


def _load_example():
    """The shipped example, imported as the notebook imports it."""
    import importlib

    sys.path.insert(0, str(EXAMPLE.parent))
    try:
        return importlib.import_module("pydantic_ai_agent")
    finally:
        sys.path.remove(str(EXAMPLE.parent))


def test_live_mode_requires_an_explicit_opt_in(monkeypatch):
    """A credential existing in the environment is not consent to spend it.

    Without this gate, a developer with a key exported who ran the slow
    suite would re-execute every live notebook and spend real money, having
    asked for neither -- and the replay-identity cell would then read False
    against the committed recording, so the surprise bill would arrive
    dressed as a test failure. Live needs the opt-in ON TOP of the key and
    the SDK; replay is the default even when live is possible.

    The convention is shared across all four integration examples, so this
    matches `tests/test_callable.py::test_live_mode_requires_an_explicit_opt_in`
    deliberately rather than inventing a second spelling.
    """
    example = _load_example()
    monkeypatch.setenv(example.LIVE_KEY_VAR, "set-but-not-consent")
    monkeypatch.delenv(example.LIVE_OPT_IN_VAR, raising=False)
    assert not example.can_run_live(), (
        "a key alone must never enable live calls")

    monkeypatch.setenv(example.LIVE_OPT_IN_VAR, "1")
    if example.have_framework():
        assert example.can_run_live(), (
            "opt-in plus key plus SDK is exactly the live condition")

    monkeypatch.delenv(example.LIVE_KEY_VAR)
    assert not example.can_run_live(), "the opt-in alone is not enough either"


def test_the_spend_preview_cannot_disagree_with_the_call():
    """The token ceiling the notebook quotes before spending is the one the
    agent is actually configured with. Two literals would drift, and the
    drift would be invisible until a bill."""
    example = _load_example()
    agent = example.build_agent()
    assert agent.model_settings["max_tokens"] == example.LIVE_MAX_TOKENS


def test_the_example_runs_end_to_end():
    """The example is AST-parsed by `tests/test_examples.py` and never run
    by it, so this runs it. It asserts its own gates and exits non-zero if
    any fails; the return code is the verdict."""
    if not EXAMPLE.exists():
        pytest.fail(f"{EXAMPLE.name} is missing from examples/integrations/")
    done = subprocess.run([sys.executable, str(EXAMPLE)],
                          capture_output=True, text=True, timeout=300)
    assert done.returncode == 0, done.stdout[-3000:] + done.stderr[-3000:]
    assert "decisions" in done.stdout
