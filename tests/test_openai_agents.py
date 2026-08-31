"""The OpenAI Agents SDK adapter, without an API key and without a network.

CI does not call an LLM. A live decision needs a key, costs money and answers
differently every time, so a suite depending on one would be neither
reproducible nor free.

What replaces it is not a mock of this adapter. The SDK ships its own
deterministic model double -- `agents.testing.ScriptedModel`, a real
`agents.models.interface.Model` -- and `RunConfig(model=...)` takes a Model
instance ahead of the provider lookup, so a scripted model runs the WHOLE SDK
offline: the turn loop, the tool calls, the output-schema validation, the
guardrails, the refusal path. The double sits at the framework's own model
boundary rather than at this adapter's seam, so everything between the two is
the real thing, and the double is strictly LESS capable than a real model
rather than more. A stub that reimplemented the SDK's validation would be
testing itself.

`scripted.last_call` is what the ground-truth assertions read: it is the
exact system instructions, input and tool list the model received, so
`test_no_hidden_value_reaches_the_model` inspects what was actually sent
rather than what this file believes was sent.

Tracing is turned off three ways at import -- the env var, the SDK's global
switch, and `tracing_disabled=True` on every run the adapter starts -- so
nothing here can phone home even on a developer machine with OPENAI_API_KEY
exported.

The shared contract is not re-tested here. `tests/test_integrations.py`
exports `CONTRACT_CHECKS`, and the parametrized test below runs every one of
them against this adapter. What this file adds is the surface specific to the
SDK: the user's agent surviving untouched, its tools and guardrails still
running, which SDK exceptions are outcomes and which are failures, the
replay path, and the shipped example.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import sys

import pytest

import tradefloor as tf
from tradefloor.integrations import common as ci
from tradefloor.integrations.openai_agents import (BRIEF, BRIEF_VERSION,
                                                   OpenAIAgentsAdapter,
                                                   openai_agent, payload_of)

# The market helpers are shared with the contract file rather than copied:
# two rosters that drift apart would test two different markets under one
# name. Importable because the tests directory is flat and pytest puts it on
# the path.
import test_integrations as contract

REPO = pathlib.Path(__file__).resolve().parent.parent
EXAMPLE = REPO / "examples" / "integrations" / "openai_agents_agent.py"

# Off before anything imports the SDK: `TraceProvider` reads the env var once,
# on first use, and caches it.
os.environ["OPENAI_AGENTS_DISABLE_TRACING"] = "1"

try:
    import agents as sdk
except ImportError:                                      # pragma: no cover
    sdk = None
else:
    sdk.set_tracing_disabled(True)

#: The SDK is an optional extra, so most of this file skips without it.
#:
#: The handful of tests left UNMARKED are the ones that assert the adapter
#: works without it -- the lazy import, the subpackage isolation, the
#: constructor refusals, the brief, the public surface. Marking those would
#: make them pass by being skipped, which is the one thing they exist to
#: rule out.
#:
#: Everything that reaches `sdk.Agent` needs the marker, and two tests added
#: late did not have it: with the extras absent they failed rather than
#: skipped, so a contributor following CONTRIBUTING.md -- which documents a
#: dev setup with no framework installed -- would have seen a red suite for
#: a dependency they were never asked to install.
needs_sdk = pytest.mark.skipif(
    sdk is None, reason="openai-agents is an optional extra")

#: What a credential actually looks like, rather than a substring that
#: collides with English.
#:
#: When this was written the shared contract checks tested membership --
#: `assert "sk-" not in json.dumps(...)` -- and `"sk-" in "risk-adjusted"`
#: is True. `BRIEF` says "risk-adjusted returns", so scanning a transcript,
#: which carries the brief, failed on ordinary prose. The checks have since
#: been anchored and now delegate to the one validator on `AdapterInfo`, so
#: the collision is gone; this stays because an anchored pattern is the
#: right thing to scan prose with either way, and because a guard that
#: cries wolf is a guard people learn to delete.
CREDENTIAL = re.compile(
    r"\bsk-[A-Za-z0-9_-]{8,}|api[_-]?key|Bearer\s|Authorization", re.I)

#: The user agent's own system prompt. Named, because a test asserts it
#: survives the run unchanged, and a literal repeated in three places is
#: a literal that eventually differs in one of them.
INSTRUCTIONS = "You are a disciplined value investor."


# -- the offline model -------------------------------------------------------


def scripted(respond, *, turns: int = 64):
    """A `ScriptedModel` that answers each call by applying `respond`.

    `respond` receives the serialized observation payload -- recovered from
    the call itself with `payload_of`, so the assertion that the payload
    reached the model is made by the plumbing rather than trusted -- and
    returns whatever the model should say. A dict is JSON-dumped; a string is
    sent verbatim, which is how a malformed answer is scripted.

    `turns` is generous rather than exact because the contract checks run
    worlds of different lengths through one adapter, and a script that ran
    out would fail as `UnexpectedModelCall` instead of as the thing under
    test. Tests that care about the exact call count read `model.calls`.
    """
    from agents.testing import ModelStep, ScriptedModel, assistant_message

    def answer(call):
        out = respond(payload_of(call))
        text = out if isinstance(out, str) else json.dumps(out)
        return [assistant_message(text)]

    return ScriptedModel([ModelStep.respond(answer) for _ in range(turns)])


def user_agent(**kwargs):
    """A plausible user agent: their name, their instructions, no
    output type."""
    kwargs.setdefault("name", "Portfolio Manager")
    kwargs.setdefault("instructions", INSTRUCTIONS)
    return sdk.Agent(**kwargs)


def make_agent(respond, *, agent=None, **kwargs) -> OpenAIAgentsAdapter:
    """The adapter with its model scripted -- the seam every check cuts."""
    return OpenAIAgentsAdapter(agent=agent or user_agent(), mode="live",
                               model=scripted(respond), **kwargs)


def answer(*actions, rationale="because") -> dict:
    return {"actions": list(actions), "rationale": rationale}


def act(symbol, side, quantity=0) -> dict:
    return {"symbol": symbol, "side": side, "quantity": quantity}


def run_world(respond, *, days=1, **kwargs):
    agent = make_agent(respond, **kwargs)
    world = contract.make_world(agent)
    world.run(days=days)
    return world, agent


# -- the shared contract -----------------------------------------------------


@needs_sdk
@pytest.mark.parametrize("check", contract.CONTRACT_CHECKS,
                         ids=lambda f: f.__name__)
def test_the_adapter_meets_the_shared_contract(check):
    """Every guarantee in `tests/test_integrations.py`, against this adapter.

    The seam is the SDK's model boundary rather than `ask`, so each of these
    runs the real turn loop and the real output validation on the way past.
    """
    check(make_agent)


# -- the optional dependency -------------------------------------------------


def test_the_module_imports_without_the_sdk():
    """The whole reason the import is inside `_run`.

    Checked in a subprocess rather than against `sys.modules`, because the
    SDK IS installed in this environment and an earlier test importing it
    would make a `sys.modules` check pass for the wrong reason. `import
    agents` costs 4.5 seconds and pulls 37 packages including a whole ASGI
    server stack, and replaying a recorded run must pay none of that.
    """
    source = (
        "import sys\n"
        "import tradefloor.integrations.openai_agents as adapter\n"
        "assert 'agents' not in sys.modules, sorted(\n"
        "    m for m in sys.modules if m.startswith('agents'))\n"
        "assert adapter.OpenAIAgentsAdapter is not None\n"
    )
    done = subprocess.run([sys.executable, "-c", source],
                          capture_output=True, text=True, timeout=300)
    assert done.returncode == 0, done.stdout + done.stderr


def test_tradefloor_does_not_import_the_integrations_subpackage():
    """`import tradefloor` must not reach an adapter, so that a broken or
    absent third-party dependency cannot break the library."""
    source = (REPO / "python" / "tradefloor" / "__init__.py").read_text(
        encoding="utf-8")
    assert "integrations" not in source


def test_live_mode_without_the_sdk_names_the_extra(monkeypatch):
    """A user who configured an adapter halfway meets the pip command, not a
    raw ModuleNotFoundError from four frames down."""
    import importlib

    real = importlib.import_module

    def refuse(name, *args, **kwargs):
        if name == "agents":
            raise ImportError("No module named 'agents'")
        return real(name, *args, **kwargs)

    monkeypatch.setattr(importlib, "import_module", refuse)
    adapter = OpenAIAgentsAdapter(agent=object(), mode="live")
    with pytest.raises(ci.MissingDependencyError, match="openai-agents"):
        adapter._run([], _Step(0, 0))

    with pytest.raises(ci.MissingDependencyError, match="pip install"):
        adapter._run([], _Step(0, 0))


class _Step:
    """The two attributes `_run` reads off an Observation, for error text."""

    def __init__(self, step, day):
        self.step = step
        self.day = day


# -- construction ------------------------------------------------------------


def test_replay_mode_refuses_without_a_transcript():
    with pytest.raises(tf.ValidationError, match="transcript"):
        OpenAIAgentsAdapter()


def test_live_mode_refuses_without_an_agent():
    with pytest.raises(tf.ValidationError, match="agents.Agent"):
        OpenAIAgentsAdapter(mode="live")


def test_an_unknown_mode_is_refused():
    with pytest.raises(tf.ValidationError, match="replay"):
        OpenAIAgentsAdapter(mode="dry-run")


def test_a_zero_turn_budget_is_refused():
    """A decision needs at least one model call, and `max_turns=0` would
    raise MaxTurnsExceeded at every decision point instead of saying so."""
    with pytest.raises(tf.ValidationError, match="max_turns"):
        OpenAIAgentsAdapter(agent=object(), mode="live", max_turns=0)


@needs_sdk
def test_the_agent_is_taken_positionally_like_every_other_adapter():
    """`OpenAIAgentsAdapter(llm)` is the first line anybody writes, and it
    used to raise TypeError because this constructor alone was keyword-only.

    Nothing offline caught it: every test in this file, and the docstring
    example, passed the agent by keyword, so the suite agreed with the code
    instead of checking it. It surfaced the first time somebody wrote
    live-model code against all four adapters side by side.

    Both spellings are pinned. `agent=` has to keep working because `fork()`
    rebuilds the twin as `type(self)(**fork_kwargs())`, which passes
    keywords.
    """
    import inspect

    from tradefloor.integrations.callable import CallableAgentAdapter
    from tradefloor.integrations.openai_agents import OpenAIAgentsAdapter as A

    llm = user_agent()
    positional = A(llm, mode="live", model=scripted(lambda p: answer()))
    keyword = A(agent=llm, mode="live", model=scripted(lambda p: answer()))
    assert positional.agent is llm and keyword.agent is llm

    # The convention, read off the reference adapter rather than asserted
    # from memory: framework object first and positional, everything after
    # it keyword-only.
    for cls, first in ((A, "agent"), (CallableAgentAdapter, "fn")):
        params = list(inspect.signature(cls.__init__).parameters.values())
        assert params[1].name == first, params
        assert params[1].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
        assert params[1].default is None, (
            "the framework object must default to None so fork() can pass "
            "it by keyword")
        assert all(p.kind is inspect.Parameter.KEYWORD_ONLY
                   for p in params[2:]), [p.name for p in params[2:]]


@needs_sdk
def test_a_positionally_built_adapter_runs_and_forks():
    """The signature change must not have broken the fork, which rebuilds
    the twin from `fork_kwargs()` by keyword."""
    adapter = OpenAIAgentsAdapter(user_agent(), mode="live",
                                  model=scripted(lambda p: answer()))
    world = contract.make_world(adapter)
    world.run(days=1)
    twin = adapter.fork()
    assert type(twin) is OpenAIAgentsAdapter
    assert twin.agent is adapter.agent
    assert twin.state() == adapter.state()


@needs_sdk
def test_the_convenience_spelling_defaults_to_live():
    agent = openai_agent(user_agent(), model=scripted(lambda p: answer()))
    assert agent.mode == "live"
    assert isinstance(agent, OpenAIAgentsAdapter)


# -- the user's agent is not altered -----------------------------------------


@needs_sdk
def test_the_users_agent_is_not_mutated_by_a_run():
    """The adapter binds the decision contract on a CLONE. The original keeps
    the output type it had, which for almost every agent is none."""
    from agents import function_tool

    @function_tool
    def note(text: str) -> str:
        """Record a note."""
        return f"noted: {text}"

    original = user_agent(tools=[note])
    before = (original.output_type, list(original.tools),
              original.instructions, original.name)

    world, adapter = run_world(lambda p: answer(), agent=original)

    assert original.output_type is None, (
        "the adapter set output_type on the user's own agent")
    assert (original.output_type, list(original.tools),
            original.instructions, original.name) == before
    assert adapter._bound is not original
    assert adapter._bound.instructions == original.instructions
    assert adapter._bound.tools == original.tools


@needs_sdk
def test_the_clone_is_built_once_and_reused():
    """`clone` per decision point would be waste, and two clones would be two
    agents where the experiment claims one."""
    world, adapter = run_world(lambda p: answer(), days=3)
    first = adapter._bound
    assert first is not None
    assert len(adapter.record) == 3
    assert adapter._bound is first


@needs_sdk
def test_an_agent_that_already_declares_an_output_type_is_refused():
    """It was built to return something specific. Overriding that silently
    would discard a contract its author relies on elsewhere."""
    import pydantic

    class Theirs(pydantic.BaseModel):
        verdict: str

    adapter = make_agent(lambda p: answer(),
                         agent=user_agent(output_type=Theirs))
    with pytest.raises(tf.ValidationError, match="output_type"):
        contract.make_world(adapter).run(days=1)


@needs_sdk
def test_an_agent_already_carrying_the_shared_model_is_accepted():
    """Setting it yourself is the documented way to opt in, so it must not be
    refused as if it were somebody else's contract."""
    agent = user_agent(output_type=ci.decision_model())
    world, adapter = run_world(lambda p: answer(act("TECH_A", "BUY", 2000)),
                               agent=agent)
    assert world.portfolio.positions["TECH_A"].quantity > 0


@needs_sdk
def test_a_non_agent_object_is_refused_by_name():
    adapter = OpenAIAgentsAdapter(agent=object(), mode="live",
                                  model=scripted(lambda p: answer()))
    with pytest.raises(tf.ValidationError, match="clone"):
        contract.make_world(adapter).run(days=1)


# -- what the model is actually sent -----------------------------------------


@needs_sdk
def test_the_model_receives_the_brief_and_the_payload_as_two_messages():
    model = scripted(lambda p: answer())
    adapter = OpenAIAgentsAdapter(agent=user_agent(), mode="live", model=model)
    contract.make_world(adapter).run(days=1)

    call = model.last_call
    assert call.system_instructions == INSTRUCTIONS, (
        "the user's own instructions must be the system prompt; the brief is "
        "a message, not a replacement for who the agent is")
    assert [item["role"] for item in call.input] == ["user", "user"]
    assert call.input[0]["content"] == BRIEF
    assert json.loads(call.input[1]["content"]) == payload_of(call)


@needs_sdk
def test_the_decision_contract_is_bound_as_the_output_type():
    model = scripted(lambda p: answer())
    adapter = OpenAIAgentsAdapter(agent=user_agent(), mode="live", model=model)
    contract.make_world(adapter).run(days=1)

    schema = model.last_call.output_schema
    assert schema is not None, "no output type was bound"
    assert schema.is_strict_json_schema(), (
        "strict mode is what makes the provider constrain generation to the "
        "contract")
    rendered = json.dumps(schema.json_schema())
    assert "actions" in rendered and "BUY" in rendered
    assert "order_type" not in rendered and "limit_price" not in rendered


@needs_sdk
def test_no_hidden_value_reaches_the_model():
    """Read off the recorded call, not off what this file thinks was sent.

    Ground truth is checked two ways, because they fail differently. The
    contract's `Sealed` proxy fails on the ACCESS, catching a read that is
    then rounded or reworded; this catches a number that arrived by some
    route the seal does not cover.
    """
    import struct

    model = scripted(lambda p: answer())
    adapter = OpenAIAgentsAdapter(agent=user_agent(), mode="live", model=model)
    world = contract.make_world(adapter)
    world.run(days=2)

    sent = json.dumps([model.last_call.system_instructions,
                       model.last_call.input])

    hidden = []
    for instrument in world.universe:
        hidden.append(tf.fair_value(
            eps=instrument.eps, sector=instrument.sector,
            revenue_growth=instrument.revenue_growth,
            book_value_per_share=instrument.book_value_per_share,
            federal_funds_rate=0.04,
            corporate_bond_yield=0.055).fair_value)
    for factor in ("momentum", "reversion", "company_news"):
        blob = world.engine.attribution(factor)
        hidden += list(struct.unpack("<%dd" % (len(blob) // 8), blob))

    leaked = [v for v in hidden
              if v and abs(v) > 1e-9 and f"{v:.4f}" in sent]
    assert not leaked, (
        f"values only the simulator knows reached the model: {leaked}")
    for forbidden in ("fair_value", "mispricing", "attribution",
                      "crowd_lean"):
        assert forbidden not in sent.lower(), (
            f"the model was sent the word {forbidden!r}, which names "
            "simulator ground truth")


@needs_sdk
def test_the_payload_is_recoverable_from_the_call():
    """`payload_of` is public because a user scripting an offline model needs
    it, and the input layout is this module's business rather than theirs."""
    seen = []
    model = scripted(lambda p: seen.append(p) or answer())
    adapter = OpenAIAgentsAdapter(agent=user_agent(), mode="live", model=model)
    contract.make_world(adapter).run(days=1)

    assert seen and set(seen[0]) == contract.PAYLOAD_KEYS
    assert payload_of(model.last_call) == seen[0]
    assert payload_of(model.last_call.input) == seen[0], (
        "the input list alone must work too; that is what a caller building "
        "one by hand has")


@needs_sdk
def test_payload_of_refuses_something_that_is_not_ours():
    with pytest.raises(tf.ValidationError, match="two separate messages"):
        payload_of("a bare string")
    with pytest.raises(tf.ValidationError, match="no input"):
        payload_of([])
    with pytest.raises(tf.ValidationError, match="JSON"):
        payload_of([{"role": "user", "content": "not json"}])


# -- tracing -----------------------------------------------------------------


@needs_sdk
def test_tracing_is_off_on_every_run_by_default():
    """The SDK's tracing is ON by default and exports to OpenAI. An adapter
    that inherited that default would ship trace data from any machine with a
    key in its shell."""
    from agents.models.interface import ModelTracing

    model = scripted(lambda p: answer())
    adapter = OpenAIAgentsAdapter(agent=user_agent(), mode="live", model=model)
    contract.make_world(adapter).run(days=1)

    assert model.last_call.tracing is ModelTracing.DISABLED


@needs_sdk
def test_tracing_can_be_turned_on_and_carries_the_run_metadata():
    """A user who wants their own tracing gets it, with the run identified."""
    adapter = OpenAIAgentsAdapter(agent=user_agent(), mode="live",
                                  model=scripted(lambda p: answer()),
                                  tracing=True, run_id="seed-4242", arm="base")
    config = adapter._run_config(sdk, _Step(12, 2))
    assert config.tracing_disabled is False
    assert config.workflow_name == "tradefloor"
    assert config.group_id == "seed-4242"
    assert config.trace_metadata["step"] == 12
    assert config.trace_metadata["day"] == 2
    assert config.trace_metadata["arm"] == "base"


# -- the user's own agent machinery still runs -------------------------------


@needs_sdk
def test_the_users_tools_run_inside_the_decision():
    """The double is at the model boundary, so the SDK's real turn loop runs:
    the tool is called, its output goes back to the model, and the second
    turn produces the decision. An `ask`-level stub would have skipped all of
    it."""
    from agents import function_tool
    from agents.testing import (ModelStep, ScriptedModel, assistant_message,
                                function_call)

    called = []

    @function_tool
    def depth(symbol: str) -> str:
        """How much of this name the book can absorb."""
        called.append(symbol)
        return f"{symbol}: thin"

    model = ScriptedModel([
        [function_call("depth", {"symbol": "TECH_A"}, call_id="c1")],
        ModelStep.respond(lambda call: [assistant_message(json.dumps(
            answer(act("TECH_A", "BUY", 2000))))]),
    ])
    adapter = OpenAIAgentsAdapter(agent=user_agent(tools=[depth]),
                                  mode="live", model=model)
    world = contract.make_world(adapter)
    world.run(days=1)

    assert called == ["TECH_A"], "the user's tool never ran"
    assert world.portfolio.positions["TECH_A"].quantity > 0
    assert len(model.calls) == 2
    model.assert_complete()


@needs_sdk
def test_a_tripped_guardrail_is_a_decision_error_naming_the_step():
    """The guardrail is the agent's own, so this is the agent declining --
    a decision point that produced nothing, not a broken framework."""
    from agents import GuardrailFunctionOutput, output_guardrail

    @output_guardrail
    def no_buying(ctx, agent, output):
        return GuardrailFunctionOutput(
            output_info={"actions": len(output.actions)},
            tripwire_triggered=any(a.side == "BUY" for a in output.actions))

    adapter = make_agent(lambda p: answer(act("TECH_A", "BUY", 2000)),
                         agent=user_agent(output_guardrails=[no_buying]))
    with pytest.raises(ci.DecisionError, match="guardrail") as excinfo:
        contract.make_world(adapter).run(days=1)
    assert "step 0" in str(excinfo.value) and "day 0" in str(excinfo.value)
    assert isinstance(excinfo.value.__cause__,
                      sdk.OutputGuardrailTripwireTriggered)


@needs_sdk
def test_an_input_guardrail_tripwire_is_a_decision_error_too():
    """The other half of the guardrail pair, and it fires on a different
    path -- before the model is called at all, on the first agent in the
    chain -- so a handler written only against the output side would miss
    it and let it fall through as a FrameworkError."""
    from agents import GuardrailFunctionOutput, input_guardrail

    @input_guardrail
    def refuse_mondays(ctx, agent, user_input):
        return GuardrailFunctionOutput(output_info={},
                                       tripwire_triggered=True)

    adapter = make_agent(lambda p: answer(),
                         agent=user_agent(input_guardrails=[refuse_mondays]))
    with pytest.raises(ci.DecisionError, match="guardrail") as excinfo:
        contract.make_world(adapter).run(days=1)
    assert "step 0" in str(excinfo.value)
    assert isinstance(excinfo.value.__cause__,
                      sdk.InputGuardrailTripwireTriggered)


@needs_sdk
def test_a_tripped_guardrail_is_not_quietly_scored_as_a_hold():
    """The axis decision, pinned. A guardrail firing is a configured
    OUTCOME, and the tempting move is to turn it into an empty decision --
    the agent sat this step out. That is wrong, and it is wrong in the
    expensive direction: an empty decision scores as `trades=0` beside an
    empty error column, which is exactly what a considered decline looks
    like, so a blocked run would be indistinguishable from a deliberate
    one in the scorecard.

    A caller who WANTS a tripped guardrail to mean "sit this step out" can
    catch DecisionError and continue. That is a choice they make with the
    fact in hand, rather than one this adapter makes for them silently.
    """
    from agents import GuardrailFunctionOutput, output_guardrail

    @output_guardrail
    def refuse_everything(ctx, agent, output):
        return GuardrailFunctionOutput(output_info={},
                                       tripwire_triggered=True)

    blocked = user_agent(output_guardrails=[refuse_everything])
    adapter = make_agent(lambda p: answer(act("TECH_A", "BUY", 2000)),
                         agent=blocked)
    world = contract.make_world(adapter)
    with pytest.raises(ci.DecisionError):
        world.run(days=1)
    assert not adapter.record, (
        "the blocked step was recorded as a decision; a scorecard could "
        "not tell it from a considered HOLD")
    assert adapter.decision() is None
    assert not world.portfolio.positions


@needs_sdk
def test_a_guardrail_trip_is_not_a_framework_error():
    """`act` wraps everything escaping the framework call in FrameworkError,
    control-flow exceptions included, because it cannot tell a signal from a
    crash. This framework signals through exceptions, so `call` sorts them
    rather than letting the default decide. A caller catching DecisionError
    must not have to reach through `__cause__` to find a guardrail."""
    from agents import GuardrailFunctionOutput, output_guardrail

    @output_guardrail
    def refuse_everything(ctx, agent, output):
        return GuardrailFunctionOutput(output_info={},
                                       tripwire_triggered=True)

    blocked = user_agent(output_guardrails=[refuse_everything])
    adapter = make_agent(lambda p: answer(), agent=blocked)
    with pytest.raises(ci.IntegrationError) as excinfo:
        contract.make_world(adapter).run(days=1)
    assert not isinstance(excinfo.value, ci.FrameworkError), (
        "a configured guardrail arrived as a transport failure")
    assert isinstance(excinfo.value, ci.DecisionError)


# -- the four SDK failures that mean "no decision" ---------------------------


@needs_sdk
def test_a_refusal_is_a_decision_error_carrying_the_refusal_text():
    """A refusal is not a HOLD. Scoring it as one would record a considered
    choice the agent never made."""
    from agents.testing import ScriptedModel
    from openai.types.responses import (ResponseOutputMessage,
                                        ResponseOutputRefusal)

    refusal = ResponseOutputMessage(
        id="m1", type="message", role="assistant", status="completed",
        content=[ResponseOutputRefusal(type="refusal",
                                       refusal="I will not trade.")])
    adapter = OpenAIAgentsAdapter(agent=user_agent(), mode="live",
                                  model=ScriptedModel([[refusal]]))
    with pytest.raises(ci.DecisionError, match="refused") as excinfo:
        contract.make_world(adapter).run(days=1)
    assert "I will not trade." in str(excinfo.value)
    assert "step 0" in str(excinfo.value) and "day 0" in str(excinfo.value)
    assert isinstance(excinfo.value.__cause__, sdk.ModelRefusalError)


@needs_sdk
def test_an_exhausted_turn_budget_is_a_decision_error_naming_the_step():
    from agents import function_tool
    from agents.testing import ScriptedModel, function_call

    @function_tool
    def spin() -> str:
        """A tool that teaches the model nothing."""
        return "nothing"

    model = ScriptedModel([
        [function_call("spin", {}, call_id="c1")],
        [function_call("spin", {}, call_id="c2")],
        [function_call("spin", {}, call_id="c3")],
    ])
    adapter = OpenAIAgentsAdapter(agent=user_agent(tools=[spin]), mode="live",
                                  model=model, max_turns=2)
    with pytest.raises(ci.DecisionError, match="turn budget") as excinfo:
        contract.make_world(adapter).run(days=1)
    assert "step 0" in str(excinfo.value)
    assert "max_turns above 2" in str(excinfo.value)
    assert isinstance(excinfo.value.__cause__, sdk.MaxTurnsExceeded)


@needs_sdk
def test_a_schema_failure_is_a_decision_error_that_names_the_contract():
    """The SDK redacts model text from its own error, so the message this
    adapter raises has to carry the step, the day and the contract itself --
    otherwise a failed run says only that something, somewhere, was
    invalid."""
    adapter = make_agent(lambda p: "I have thought about it at length.")
    with pytest.raises(ci.DecisionError, match="actions") as excinfo:
        contract.make_world(adapter).run(days=1)
    message = str(excinfo.value)
    assert "step 0" in message and "day 0" in message
    assert "OPENAI_AGENTS_DONT_LOG_MODEL_DATA" in message, (
        "the message must say how to see the redacted text")
    assert isinstance(excinfo.value.__cause__, sdk.ModelBehaviorError)


@needs_sdk
def test_a_framework_envelope_cannot_score_as_a_considered_hold():
    """Also covered by the shared contract; kept because this is the failure
    that silently produces `trades=0` and an empty errors list, which is
    exactly what a deliberate decline looks like."""
    adapter = make_agent(lambda p: {"messages": ["I have finished thinking."]})
    with pytest.raises(ci.DecisionError, match="actions"):
        contract.make_world(adapter).run(days=1)


@needs_sdk
def test_a_transport_failure_is_a_framework_error_not_a_decision_error():
    """The two mean different things and an experiment scores them
    differently: the model said something unusable, versus the call never
    completed."""
    def explode(payload):
        raise ConnectionError("connection reset by peer")

    adapter = make_agent(explode)
    with pytest.raises(ci.FrameworkError) as excinfo:
        contract.make_world(adapter).run(days=1)
    assert isinstance(excinfo.value.__cause__, ConnectionError)


# -- the contract survives the SDK's own validation --------------------------


@needs_sdk
def test_the_bound_model_refuses_a_hold_carrying_a_quantity():
    """The rule lives in `decision_model`, and this proves it survives the
    SDK's strict-schema rendering rather than being dropped on the way."""
    adapter = make_agent(lambda p: answer(act("TECH_A", "HOLD", 500)))
    with pytest.raises(ci.DecisionError):
        contract.make_world(adapter).run(days=1)


@needs_sdk
def test_the_bound_model_refuses_a_negative_quantity():
    adapter = make_agent(lambda p: answer(act("TECH_A", "BUY", -2000)))
    with pytest.raises(ci.DecisionError):
        contract.make_world(adapter).run(days=1)


@needs_sdk
def test_an_unlisted_symbol_is_a_market_refusal_not_a_decision_error():
    """Well-formed output the market cannot take. The SDK validated it
    happily, because the SDK does not know what is listed here."""
    adapter = make_agent(lambda p: answer(act("NOT_LISTED", "BUY", 100)))
    with pytest.raises(ci.MarketRefusalError, match="NOT_LISTED"):
        contract.make_world(adapter).run(days=1)


@needs_sdk
def test_an_oversized_order_is_clipped_and_the_clip_is_recorded():
    world, adapter = run_world(
        lambda p: answer(act("TECH_A", "BUY", 10_000_000)))
    entry = adapter.record[0]
    assert entry["clipped"], "an oversized order left no note"
    assert entry["orders"]["TECH_A"] == pytest.approx(0.02 * 5.0e6)


# -- async ------------------------------------------------------------------


@needs_sdk
def test_a_decision_works_from_inside_a_running_event_loop():
    """The reason `Runner.run_sync` is not used. It raises a bare
    RuntimeError when a loop is already running, which is every Jupyter cell,
    and an adapter built on it works in a script and dies in the notebook the
    same reader tries next."""
    import asyncio

    async def drive():
        return run_world(
            lambda p: answer(act("TECH_A", "BUY", 2000)))
        return world, adapter

    world, adapter = asyncio.run(drive())
    assert world.portfolio.positions["TECH_A"].quantity > 0
    assert len(adapter.record) == 1


@needs_sdk
def test_the_sdk_sync_entry_point_is_not_reachable_from_the_adapter():
    """A guard on the rule rather than on one call site: nothing in the
    module may name `run_sync` on the SDK's Runner."""
    source = (REPO / "python" / "tradefloor" / "integrations"
              / "openai_agents.py").read_text(encoding="utf-8")
    assert "Runner.run_sync" not in source.replace(
        "``Runner.run_sync``", "").replace("`Runner.run_sync`", ""), (
        "the adapter calls Runner.run_sync somewhere outside a docstring")
    assert "Runner.run(" in source


# -- fork and state ----------------------------------------------------------


@needs_sdk
def test_a_fork_shares_the_agent_and_rebuilds_the_binding():
    """The agent is shared because this module never mutates it, and copying
    one would clone whatever HTTP client its model settings hold. `_bound` is
    derived, so a twin rebuilds it rather than inheriting a stale clone."""
    world, adapter = run_world(lambda p: answer(), days=2)
    twin = adapter.fork()

    assert twin.agent is adapter.agent
    assert twin.model is adapter.model
    assert twin.brief == adapter.brief
    assert twin.max_turns == adapter.max_turns
    assert twin._bound is None, "the fork inherited a built clone"
    assert twin.state() == adapter.state()
    # The mixin's half: a replay of one arm must read the same recorded run
    # as its sibling, and a live recording of both belongs in one file.
    assert twin.mode == adapter.mode
    assert twin.transcript is adapter.transcript
    assert twin.recorder is adapter.recorder


@needs_sdk
def test_a_fork_keeps_the_subclass():
    class Tweaked(OpenAIAgentsAdapter):
        pass

    adapter = Tweaked(agent=user_agent(), mode="live",
                      model=scripted(lambda p: answer()))
    assert type(adapter.fork()) is Tweaked


@needs_sdk
def test_the_published_state_names_what_changes_a_decision():
    """Two arms differing in the brief or the turn budget are not running the
    controlled comparison they claim to be, and `agree` compares `state()`."""
    world, adapter = run_world(lambda p: answer())
    published = adapter.state()
    assert published["brief_digest"] == ci.digest(BRIEF)
    assert published["max_turns"] == 6
    assert published["decision_schema_version"] == ci.DECISION_SCHEMA_VERSION


@needs_sdk
def test_the_published_state_carries_no_agent_and_no_credential():
    """`state()` is printed by the fork agreement and written into artifacts.
    The agent's model settings can hold an API key."""
    world, adapter = run_world(lambda p: answer())
    published = json.dumps(adapter.state())
    assert not CREDENTIAL.search(published), CREDENTIAL.search(published)
    assert "Portfolio Manager" not in published
    assert set(adapter.state()) == {
        "history", "decision", "every", "instructions_digest",
        "decision_schema_version", "max_turns", "brief_digest"}


@needs_sdk
def test_both_arms_start_identical():
    from tradefloor.counterfactual import agree

    adapter = make_agent(lambda p: answer())
    world = contract.make_world(adapter)
    world.run(days=1)
    base, twin = world.fork("base", "twin")
    agree(base, twin)


# -- metadata ---------------------------------------------------------------


@needs_sdk
def test_the_metadata_names_the_framework_the_version_and_the_agent():
    adapter = make_agent(lambda p: answer(),
                         agent=user_agent(model="gpt-5.6-luna"))
    info = adapter.info
    assert info.framework == "openai-agents"
    assert info.framework_version == sdk.__version__
    assert info.model == "gpt-5.6-luna"
    assert info.provider == "openai"
    assert info.agent_name == "Portfolio Manager"
    assert info.instructions_digest
    assert info.decision_schema_version == ci.DECISION_SCHEMA_VERSION
    assert "openai-agents" in info.reference()


@needs_sdk
def test_every_metadata_field_is_populated():
    """`AdapterInfo` was widened because the original fields could not
    describe a replayed run. A shipped fixture with half of them empty
    undercuts that in the one artefact a reader opens, so every field this
    adapter can know is filled.

    `entry_point` is the ASYNC entry point, because that is what a
    reproduction has to call -- `Runner.run_sync` raises inside the notebook
    the fixture is replayed from.
    """
    adapter = make_agent(lambda p: answer(),
                         agent=user_agent(model="gpt-5.2"),
                         recorder=ci.Transcript())
    info = adapter.info.as_dict()

    empty = [k for k, v in info.items() if v in ("", {}, None)]
    assert not empty, f"metadata fields left empty: {empty}"
    assert info["entry_point"] == "agents.Runner.run"
    assert info["framework_url"].startswith("https://github.com/openai/")
    assert info["mode"] == "live"
    assert info["instructions_version"] == BRIEF_VERSION
    assert info["generation"] == {"max_turns": 6, "tracing": False}
    assert info["extra"] == {"recorded": True}
    json.dumps(info)   # it is written into transcripts; it must serialise


@needs_sdk
def test_a_custom_brief_does_not_claim_the_shipped_version():
    """Stamping BRIEF_VERSION beside somebody else's text would label it
    with a version it has nothing to do with, and a later mismatch would
    report two identical versions with different digests -- true, and
    useless."""
    mine = make_agent(lambda p: answer())
    theirs = make_agent(lambda p: answer(), brief="Trade well.")
    assert mine.info.instructions_version == BRIEF_VERSION
    assert theirs.info.instructions_version == ""
    assert mine.info.instructions_digest != theirs.info.instructions_digest


@needs_sdk
def test_the_instructions_identity_covers_the_agent_and_the_brief():
    """Both halves change what the agent was told, so both must move the
    digest. Covering only the brief would let two arms hand-built with
    different agent instructions publish the same identity and `agree()`
    call them identical -- the exact hole this field exists to close."""
    base = make_agent(lambda p: answer())
    other_agent = make_agent(lambda p: answer(),
                             agent=user_agent(instructions="Be reckless."))
    other_brief = make_agent(lambda p: answer(), brief="Trade well.")

    assert (base.info.instructions_digest
            != other_agent.info.instructions_digest)
    assert (base.info.instructions_digest
            != other_brief.info.instructions_digest)
    assert base.state()["instructions_digest"] == base.info.instructions_digest


@needs_sdk
def test_the_metadata_records_a_model_instance_by_class_not_by_value():
    """A `Model` instance can hold a configured client and therefore a key.
    There is nowhere in AdapterInfo to put a credential, and this is one of
    the reasons."""
    model = scripted(lambda p: answer())
    adapter = OpenAIAgentsAdapter(agent=user_agent(model=model), mode="live",
                                  model=model)
    assert adapter.info.model == "ScriptedModel"
    assert adapter.info.provider == ""


def test_the_version_is_read_without_importing_the_sdk():
    """`importlib.metadata` reads the distribution off disk, so replay mode
    can name the version it was recorded under without paying the import.

    The subprocess is the claim: it reads the version and then asserts
    `agents` never entered `sys.modules`.

    The in-process check compares two independent invocations rather than
    comparing against `sdk.__version__`. Those are different facts -- whether
    the package IMPORTS, and whether the distribution is INSTALLED -- and a
    test that couples them goes red under any harness that separates the
    two, which is exactly what a `sys.meta_path` blocker does: it intercepts
    the import and leaves the dist-info on disk. That reported a false
    failure in the bare-install measurement every other adapter was being
    held to. Comparing the two invocations tests the same property and holds
    with the SDK present, under a blocker, and on a true uninstall, where
    both return "".
    """
    from tradefloor.integrations import openai_agents as module

    source = (
        "import sys\n"
        "from tradefloor.integrations.openai_agents import (\n"
        "    _installed_version)\n"
        "version = _installed_version()\n"
        "assert 'agents' not in sys.modules, 'that import read it'\n"
        "print(version)\n"
    )
    done = subprocess.run([sys.executable, "-c", source],
                          capture_output=True, text=True, timeout=300)
    assert done.returncode == 0, done.stdout + done.stderr
    assert module._installed_version() == done.stdout.strip()
    if sdk is not None:
        assert done.stdout.strip() == sdk.__version__


@needs_sdk
def test_a_dynamic_instructions_callable_is_digested_by_name():
    """Dynamic instructions are computed per run from a context this has no
    access to. Hashing the name at least separates two different generators,
    where hashing nothing would make them look identical."""
    one = make_agent(lambda p: answer(),
                     agent=user_agent(instructions=lambda ctx, a: "a"))
    other = make_agent(lambda p: answer(),
                       agent=user_agent(instructions=_named_instructions))
    assert one.info.instructions_digest
    assert one.info.instructions_digest != other.info.instructions_digest


def _named_instructions(ctx, agent):
    return "a different generator"


# -- replay -----------------------------------------------------------------


@needs_sdk
def test_a_recorded_run_replays_to_the_same_decisions():
    recorder = ci.Transcript(meta={})
    live = make_agent(lambda p: answer(act("TECH_A", "BUY", 2000)),
                      recorder=recorder)
    recorder.meta.update(live.provenance())
    first = contract.make_world(live)
    first.run(days=2)

    replayed = OpenAIAgentsAdapter(mode="replay", transcript=recorder)
    second = contract.make_world(replayed)
    second.run(days=2)

    assert len(recorder) == 2
    assert [e["decision"] for e in replayed.record] == \
           [e["decision"] for e in live.record]
    assert second.portfolio.positions["TECH_A"].quantity == \
           pytest.approx(first.portfolio.positions["TECH_A"].quantity)
    assert [e["digest"] for e in replayed.record] == \
           [e["digest"] for e in live.record], (
        "the replay keyed off different inputs, so the two records cannot "
        "be compared step by step")
    assert [e["prompt"] for e in replayed.record] == \
           [e["prompt"] for e in live.record]


@needs_sdk
def test_a_replay_needs_no_sdk_and_no_key(tmp_path):
    """The point of the whole replay path: a reader without the extra
    installed can still re-execute somebody's experiment."""
    recorder = ci.Transcript(meta={})
    live = make_agent(lambda p: answer(act("TECH_A", "BUY", 2000)),
                      recorder=recorder)
    contract.make_world(live).run(days=2)
    path = tmp_path / "run.json"
    recorder.save(path)

    source = (
        "import sys\n"
        "import test_integrations as contract\n"
        "from tradefloor.integrations.common import Transcript\n"
        "from tradefloor.integrations.openai_agents import (\n"
        "    OpenAIAgentsAdapter)\n"
        f"transcript = Transcript.load({str(path)!r})\n"
        "adapter = OpenAIAgentsAdapter(mode='replay', transcript=transcript)\n"
        "world = contract.make_world(adapter)\n"
        "world.run(days=2)\n"
        "assert len(adapter.record) == 2, adapter.record\n"
        "assert 'agents' not in sys.modules, 'the replay imported the SDK'\n"
    )
    env = dict(os.environ)
    env.pop("OPENAI_API_KEY", None)
    done = subprocess.run([sys.executable, "-c", source],
                          capture_output=True, text=True, timeout=300,
                          cwd=str(REPO / "tests"), env=env)
    assert done.returncode == 0, done.stdout + done.stderr


@needs_sdk
def test_the_recorded_response_round_trips_one_level_deep(tmp_path):
    """The encoding trap, pinned. `ask` returns a JSON STRING and the
    recorder writes it verbatim, so a replay hands `parse_decision` exactly
    what the live path handed it.

    Double-encoding is the failure this guards: a `json.dumps` of a response
    that is already JSON text stores a string containing a string, the write
    looks perfectly correct on disk, and the replay parses one level, gets a
    `str` back where a decision should be, and fails -- at replay time,
    against a recording made days earlier.
    """
    recorder = ci.Transcript(meta={})
    live = make_agent(lambda p: answer(act("TECH_A", "BUY", 2000)),
                      recorder=recorder)
    contract.make_world(live).run(days=1)

    path = tmp_path / "run.json"
    recorder.save(path)
    reloaded = ci.Transcript.load(path)
    stored = reloaded.entries[0]["response"]

    assert isinstance(stored, str), (
        "the recorded response is not text, so the replay path and the live "
        "path do not hand parse_decision the same thing")
    once = json.loads(stored)
    assert isinstance(once, dict) and "actions" in once, (
        f"one json.loads left {type(once).__name__}, so the response was "
        "encoded twice")
    assert ci.parse_decision(stored).as_dict() == live.record[0]["decision"]


@needs_sdk
def test_a_missing_recording_raises_and_says_which_step():
    """A replay is keyed by the exact input. A changed brief means the key
    goes missing, and answering anyway would answer this question with a
    response given to a different one."""
    recorder = ci.Transcript(meta={})
    live = make_agent(lambda p: answer(), recorder=recorder)
    contract.make_world(live).run(days=1)

    moved = OpenAIAgentsAdapter(mode="replay", transcript=recorder,
                                brief=BRIEF + "\nAlso: be bold.")
    with pytest.raises(ci.DecisionError, match="no recorded response"):
        contract.make_world(moved).run(days=1)


@needs_sdk
def test_the_transcript_carries_no_credential_and_no_provider_metadata():
    recorder = ci.Transcript(meta={})
    live = make_agent(lambda p: answer(), recorder=recorder)
    recorder.meta.update(live.provenance())
    contract.make_world(live).run(days=1)

    published = recorder.to_json()
    assert not CREDENTIAL.search(published), CREDENTIAL.search(published)
    assert "openai.com" not in published, (
        "a provider endpoint reached the file")
    assert set(recorder.entries[0]) == {"arm", "step", "day", "digest",
                                        "prompt", "response"}


@needs_sdk
def test_the_replay_key_is_the_input_the_model_was_sent():
    recorder = ci.Transcript(meta={})
    live = make_agent(lambda p: answer(), recorder=recorder)
    contract.make_world(live).run(days=1)

    entry = recorder.entries[0]
    assert ci.digest(entry["prompt"]) == entry["digest"]
    assert entry["prompt"][0]["content"] == BRIEF


# -- the live gate -----------------------------------------------------------


def _load_example():
    import importlib.util
    spec = importlib.util.spec_from_file_location("openai_example", EXAMPLE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_live_mode_requires_an_explicit_opt_in(monkeypatch):
    """A credential existing in the environment is not consent to spend it.

    Without this gate, a developer with a key exported who ran the slow
    suite would re-execute every live notebook and spend real money, having
    asked for neither -- and the replay-identity cell would then read False
    against the committed recording, so the surprise bill would arrive
    dressed as a test failure. Live needs the opt-in ON TOP of the key and
    the SDK; replay is the default even when live is possible."""
    example = _load_example()
    monkeypatch.setenv(example.LIVE_KEY_VAR, "set-but-not-consent")
    monkeypatch.delenv(example.LIVE_OPT_IN_VAR, raising=False)
    assert not example.can_run_live(), (
        "a key alone must never enable live calls")

    monkeypatch.setenv(example.LIVE_OPT_IN_VAR, "1")
    if sdk is not None:
        assert example.can_run_live(), (
            "opt-in plus key plus SDK is exactly the live condition")

    monkeypatch.delenv(example.LIVE_KEY_VAR)
    assert not example.can_run_live(), "the opt-in alone is not enough either"


def test_the_missing_conditions_read_as_a_recipe(monkeypatch):
    """The notebook prints this, so it has to name what to do rather than
    only what is wrong -- and in the order somebody would fix it."""
    example = _load_example()
    monkeypatch.delenv(example.LIVE_OPT_IN_VAR, raising=False)
    monkeypatch.delenv(example.LIVE_KEY_VAR, raising=False)
    missing = example.missing_for_live()
    assert missing[0] == f"{example.LIVE_OPT_IN_VAR}=1", missing
    assert example.LIVE_KEY_VAR in missing

    monkeypatch.setenv(example.LIVE_OPT_IN_VAR, "1")
    monkeypatch.setenv(example.LIVE_KEY_VAR, "x")
    assert example.missing_for_live() == ([] if sdk is not None
                                          else missing[2:])


def test_the_fixture_is_found_by_marker_not_by_counting_parents():
    """`parents[2]` was correct at this file's depth and would break
    silently the next time `examples/` gains a level -- which has already
    happened once on this branch."""
    example = _load_example()
    assert example.FIXTURE.exists(), example.FIXTURE
    assert example.FIXTURE == (REPO / "tests" / "fixtures" / "openai_agents"
                               / "five-days.json")

    # Structural, not a source scan: the first draft of this test asserted
    # "parents[2]" was absent from the file and matched the docstring
    # explaining why it is not used -- a substring check standing in for
    # the structural one, which is the exact defect this branch keeps
    # finding. What matters is that the root is FOUND, so check that.
    root = example._repo_root()
    assert (root / "pyproject.toml").exists(), root
    assert root == REPO, (root, REPO)


# -- the shipped example -----------------------------------------------------


@needs_sdk
def test_the_example_runs_end_to_end():
    """Example scripts are AST-parsed by `tests/test_examples.py` and not
    executed, so the file that owns an example runs it. It asserts its own
    gates and exits non-zero if any fails; the return code is the verdict."""
    if not EXAMPLE.exists():
        pytest.fail(f"{EXAMPLE.name} is missing from examples/integrations/")
    env = dict(os.environ)
    env.pop("OPENAI_API_KEY", None)
    done = subprocess.run([sys.executable, str(EXAMPLE)],
                          capture_output=True, text=True, timeout=300,
                          env=env)
    assert done.returncode == 0, done.stdout[-3000:] + done.stderr[-3000:]
    assert "decisions" in done.stdout


# -- the public surface ------------------------------------------------------

#: Every name this module publishes, and the type each one is. A refactor
#: that renames one, drops one, or turns a constant into a function has to
#: fail here rather than in somebody's script. The type matters beside the
#: name: `BRIEF` becoming a callable would keep the name importable and break
#: every caller comparing against it.
PUBLIC_SURFACE = {
    "BRIEF": str,
    "DISTRIBUTION": str,
    "EXTRA": str,
    "OpenAIAgentsAdapter": type,
    "openai_agent": type(lambda: None),
    "payload_of": type(lambda: None),
}

PUBLIC_MEMBERS = ("act", "ask", "decision", "state", "fork", "fork_kwargs",
                  "provenance", "input_items")


@needs_sdk
def test_the_replay_key_is_the_input_and_not_a_position():
    """The property `ask` has to hold, checked end to end.

    `common.ReplayMixin` packages this branch and a new adapter should take
    it; this one was finished and tested before it existed, and migrating
    working code is churn rather than correctness. Note that the mixin would
    not make this test redundant: it computes the key from whatever
    `prepare` hands it, so it cannot save an adapter that hands it a
    position either.

    The key must be a digest of the INPUT: two worlds that differ produce
    disjoint keys, two identical worlds reproduce them, and the arm label
    moves nothing. Keyed on (arm, step) both halves would look fine and
    every study would answer new questions with old recorded answers.

    `contract.check_the_replay_key_derives_from_the_input` asserts the same
    property for every adapter; this keeps the reason beside the branch that
    has to hold it.
    """
    from tradefloor.counterfactual import World

    def keys(rate, arm=""):
        recorder = ci.Transcript(meta={})
        adapter = make_agent(lambda p: answer(), recorder=recorder, arm=arm)
        World(seed=7, universe=contract.universe(), agent=adapter,
              cash=1_000_000.0,
              pins={"federal_funds_rate": rate,
                    "corporate_bond_yield": 0.055}).run(days=2)
        return [e["digest"] for e in recorder.entries]

    base = keys(0.04)
    assert len(base) == 2 and all(base)
    assert base == keys(0.04), "identical inputs did not reproduce the key"
    assert not set(base) & set(keys(0.09)), (
        "a different market produced the same replay keys, so a replay would "
        "answer the new question with the old run's answers")
    assert base == keys(0.04, arm="shocked"), (
        "the key moved with the arm label, which is a position and not an "
        "input")


@needs_sdk
def test_the_exchange_is_declared_so_the_record_joins_the_transcript():
    """`record_exchange` is what puts `digest`/`prompt`/`response` in the
    record beside the decision they produced. Without it an artifact showing
    what the model SAID beside what it TRADED has to hand-join two files by
    digest."""
    recorder = ci.Transcript(meta={})
    world, adapter = run_world(lambda p: answer(act("TECH_A", "BUY", 2000)),
                               recorder=recorder)
    entry = adapter.record[0]
    assert entry["digest"] == recorder.entries[0]["digest"]
    assert entry["prompt"] == recorder.entries[0]["prompt"]
    assert entry["response"] == recorder.entries[0]["response"]
    assert entry["decision"]["actions"][0]["symbol"] == "TECH_A"


def test_the_public_surface_is_what_it_says():
    from tradefloor.integrations import openai_agents as module

    for name, kind in PUBLIC_SURFACE.items():
        assert hasattr(module, name), f"{name} is gone from the module"
        assert isinstance(getattr(module, name), kind), (
            f"{name} is a {type(getattr(module, name)).__name__}, not a "
            f"{kind.__name__}")
    for member in PUBLIC_MEMBERS:
        assert hasattr(module.OpenAIAgentsAdapter, member)


def test_the_brief_names_no_ground_truth_and_no_arm():
    """Both arms of a counterfactual receive an identical brief, and it says
    nothing about what is coming. An arm told it was the rate-shock arm would
    be answering a different question."""
    lowered = BRIEF.lower()
    for forbidden in ("fair value", "mispricing", "attribution", "shock",
                      "rate rise", "scenario", "arm"):
        assert forbidden not in lowered
    assert "max_order_shares" in BRIEF and "buying_power" in BRIEF, (
        "the brief must name BOTH size limits. It once named only "
        "max_order_shares -- the participation cap -- while the binding one "
        "is usually the funding cap, and an agent told to size against the "
        "wrong limit is refused at a limit nothing pointed it at. That cost "
        "a live recording an order.")
