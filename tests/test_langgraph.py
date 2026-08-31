"""The LangGraph adapter, against real graphs and against deterministic doubles.

The shared contract is enforced in `tests/test_integrations.py` and
parametrized in here over `CONTRACT_CHECKS`. What this file adds is
everything specific to driving a graph: the duck-typed boundary, the two
hooks, the config passthrough, the envelope unwrapping, and the errors a
graph raises.

Three tiers of subject, deliberately, because each catches something the
others cannot:

1. **Duck doubles** exposing `.invoke()` and nothing else. They are the
   fastest thing that can be wrong, they need no framework installed, and
   they prove the adapter really is duck-typed rather than quietly
   depending on a LangChain base class.
2. **A genuine compiled `StateGraph`**, with two nodes and custom
   reducers, built and invoked entirely offline. A double cannot tell you
   that the real class accepts what the adapter sends it.
3. **A genuine `create_react_agent`** over `GenericFakeChatModel`, which
   ships with `langchain-core` and needs no key. This is the
   `MessagesState` path -- what most users will actually bring -- and it
   is the case that would have shipped broken: before the shared parser
   learned to refuse an envelope, a graph returning `{"messages": [...]}`
   scored as an agent that considered the market and declined.

Nothing here needs a network or an API key. Tiers 2 and 3 need LangGraph,
which is installed in this repository's venv and skipped politely
otherwise; tier 1 and every refusal test run on a bare install.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import warnings
from typing import Annotated, Any, TypedDict

import pytest

import tradefloor as tf
from tradefloor.integrations import common as ci
from tradefloor.integrations.langgraph import (INSTRUCTIONS, INTERRUPT_KEY,
                                               GraphInterruptedError,
                                               LangGraphAdapter,
                                               default_input_builder,
                                               default_output_parser,
                                               langgraph_agent, render)

# The market helpers are shared with the contract file rather than copied:
# two rosters that drift apart would test two different markets under one
# name. Importable because the tests directory is flat and pytest puts it on
# sys.path.
import test_integrations as contract

REPO = pathlib.Path(__file__).resolve().parent.parent
EXAMPLE = REPO / "examples" / "integrations" / "langgraph" / "rate_shock.py"


# -- nothing phones home -----------------------------------------------------


#: Every environment variable that can turn LangSmith tracing on, in both
#: namespaces `langsmith.utils.get_env_var` searches. Unset is already off --
#: the check is `== "true"` against a default of `""` -- but a developer
#: machine may well have these set globally, and a suite that silently
#: uploaded a Tradefloor observation to somebody's LangSmith project would
#: be a bad way to find that out.
TRACING_VARS = ("LANGSMITH_TRACING", "LANGSMITH_TRACING_V2",
                "LANGCHAIN_TRACING", "LANGCHAIN_TRACING_V2",
                "LANGSMITH_API_KEY", "LANGCHAIN_API_KEY",
                "LANGSMITH_ENDPOINT", "LANGSMITH_PROJECT")


@pytest.fixture(autouse=True)
def no_tracing(monkeypatch):
    """Tracing off for every test in this file, whatever the machine says.

    Deleted rather than set to a falsy string: `langsmith.utils.get_env_var`
    is `lru_cache`d, so a value read once during a session would stick, and
    an absent variable is the state the library's own default assumes.
    """
    for name in TRACING_VARS:
        monkeypatch.delenv(name, raising=False)


def test_tracing_is_off_under_the_fixture():
    """The fixture is only worth having if it actually silences the client."""
    ls = pytest.importorskip("langsmith.utils")
    ls.get_env_var.cache_clear()
    assert not ls.tracing_is_enabled()


# -- tier 1: duck doubles ----------------------------------------------------


class DuckGraph:
    """A graph-shaped object with `invoke` and nothing else.

    No LangChain base class, no LangGraph import. `isinstance(DuckGraph(),
    Runnable)` is False -- measured -- which is exactly why the adapter
    duck-types instead. The signature mirrors the real `Runnable.invoke`,
    `(input, config=None, **kwargs)`, because that is the contract the
    adapter calls against and a double that took fewer arguments would be
    testing a call the adapter never makes.
    """

    def __init__(self, respond):
        self.respond = respond
        self.seen: list[tuple[Any, Any]] = []

    def invoke(self, state, config=None, **kwargs):
        self.seen.append((state, config))
        return self.respond(state["observation"])


class AsyncDuckGraph:
    """A graph-shaped object with only `ainvoke`, to exercise the bridge."""

    def __init__(self, respond):
        self.respond = respond

    async def ainvoke(self, state, config=None, **kwargs):
        return self.respond(state["observation"])


def make_agent(respond, **kwargs):
    """A LangGraphAdapter whose graph returns `respond(payload)`.

    The seam is the RUNNABLE, which is the honest place to cut it: the
    adapter's own input building, config assembly, envelope unwrapping,
    parsing and validation all run unmodified below it. A double that
    replaced `ask` would have skipped the half of this module worth
    testing.
    """
    return LangGraphAdapter(DuckGraph(respond), **kwargs)


@pytest.mark.parametrize("check", contract.CONTRACT_CHECKS,
                         ids=lambda f: f.__name__)
def test_the_adapter_meets_the_shared_contract(check):
    check(make_agent)


# -- the optional dependency and the module name -----------------------------


def test_the_adapter_imports_without_langgraph():
    """The whole reason the framework import lives inside a function.

    A subprocess, because this suite has LangGraph installed and has
    already imported it; the question is whether importing the adapter
    pulls it in, which only a fresh interpreter can answer.
    """
    code = ("import sys, tradefloor.integrations.langgraph; "
            "hit = [m for m in ('langgraph', 'langchain_core') "
            "if m in sys.modules]; "
            "assert not hit, f'importing the adapter pulled in {hit}'; "
            "print('ok')")
    done = subprocess.run([sys.executable, "-c", code],
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr


def test_the_module_name_does_not_shadow_the_package():
    """`tradefloor/integrations/langgraph.py` sits beside no package of its
    own name, and Python 3 has no implicit relative imports, so an absolute
    `import langgraph` inside it reaches the installed package. Pinned
    because a future refactor to `from . import langgraph` would break it
    silently, and because the failure would look like a LangGraph bug."""
    pytest.importorskip("langgraph")
    import langgraph as installed

    from tradefloor.integrations import langgraph as adapter

    assert adapter is not installed
    assert adapter.__name__ == "tradefloor.integrations.langgraph"
    assert installed.__name__ == "langgraph"
    # A namespace package: no __init__.py, so __file__ is None and __path__
    # is what identifies it.
    assert any("site-packages" in p for p in installed.__path__), \
        installed.__path__
    assert sys.modules["langgraph"] is installed


def test_the_real_adapter_and_the_framework_coexist_in_the_risky_order():
    """The adapter imported FIRST, then the framework, in a clean
    interpreter -- the order that would expose a shadowing bug, and the one
    the earlier synthetic check could not exercise because the real module
    did not exist yet.

    The failure mode is not an obvious ImportError. It is the adapter
    importing ITSELF where it meant the framework, which surfaces as a
    recursion or a missing attribute somewhere far away, so it is worth a
    subprocess and an explicit assertion rather than trust.
    """
    pytest.importorskip("langgraph")
    code = (
        "import sys;"
        "import tradefloor.integrations.langgraph as adapter;"
        "import langgraph;"
        "from langgraph.graph import StateGraph;"
        "assert adapter is not langgraph, 'the adapter shadowed the package';"
        "assert adapter.__name__ == 'tradefloor.integrations.langgraph';"
        "assert langgraph.__name__ == 'langgraph';"
        "assert StateGraph.__module__ == 'langgraph.graph.state';"
        "assert any('site-packages' in p for p in langgraph.__path__), "
        "langgraph.__path__;"
        # The adapter's own lazy import, exercised for real: this is the
        # call that would resolve to the wrong module if the name collided.
        "msg = adapter._user_message('hello');"
        "assert type(msg).__name__ == 'HumanMessage', type(msg);"
        "print('ok')")
    done = subprocess.run([sys.executable, "-c", code],
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stdout + done.stderr


def test_the_example_is_not_named_after_the_package():
    """`examples/integrations/langgraph.py` would be a real bug, not a
    style question: a script's own directory is `sys.path[0]`, so a file of
    that name there shadows the installed package for `python
    examples/integrations/langgraph.py` and the example could not import
    the framework it demonstrates. Verified by building one and watching it
    win. The package module is safe because it is only ever imported as
    part of a package, never as `sys.path[0]`."""
    assert not (EXAMPLE.parent / "langgraph.py").exists(), (
        "an example named langgraph.py shadows the langgraph package for "
        "its own process; keep the study name on the file")
    assert EXAMPLE.name == "rate_shock.py"
    assert EXAMPLE.parent.name == "langgraph"


# -- constructor refusals ----------------------------------------------------


def test_something_without_invoke_is_refused_with_a_sentence():
    with pytest.raises(tf.ValidationError) as excinfo:
        LangGraphAdapter(object())
    message = str(excinfo.value)
    assert "invoke()" in message
    assert "replay" in message, "the message should name the no-graph route"


def test_an_uncompiled_stategraph_is_refused_by_name():
    """The mistake that will actually be made. `StateGraph` is a builder
    with no `invoke`, and the generic refusal would not say the one word
    that fixes it."""
    pytest.importorskip("langgraph")
    from langgraph.graph import StateGraph

    with pytest.raises(tf.ValidationError, match="compile"):
        LangGraphAdapter(StateGraph(SimpleState))


def test_a_builder_shaped_object_is_refused_without_importing_langgraph():
    """The builder check is by SHAPE, not by isinstance, so it works -- and
    costs nothing -- on an install with no LangGraph."""
    class NotAGraph:
        def compile(self): ...
        def add_node(self, *a): ...

    with pytest.raises(tf.ValidationError, match="BUILDER"):
        LangGraphAdapter(NotAGraph())


def test_an_unknown_mode_is_refused():
    with pytest.raises(tf.ValidationError, match="mode"):
        LangGraphAdapter(DuckGraph(contract.hold), mode="sideways")


def test_replay_mode_refuses_without_a_transcript():
    with pytest.raises(tf.ValidationError, match="transcript"):
        LangGraphAdapter(mode="replay")


def test_replay_mode_needs_no_graph_at_all():
    """The point of replay: reproducing a recorded run must not require the
    framework, a network or a key."""
    agent = LangGraphAdapter(mode="replay", transcript=ci.Transcript())
    assert agent.runnable is None


def test_the_convenience_and_the_class_build_the_same_agent():
    graph = DuckGraph(contract.hold)
    assert isinstance(langgraph_agent(graph), LangGraphAdapter)
    assert langgraph_agent(graph, every=3).every == 3


# -- the default input builder -----------------------------------------------


def test_the_default_input_carries_both_shapes():
    """Both keys, always. A graph declaring only one gets what it needs and
    the other half is dropped by LangGraph before any node runs, which is
    what makes one default serve two schemas."""
    payload = {"step": 0, "assets": [{"symbol": "A", "price": 1.0}]}
    built = default_input_builder(payload)
    assert set(built) == {"observation", "messages"}
    assert built["observation"] is payload
    assert len(built["messages"]) == 1


def test_the_default_input_uses_a_real_human_message_when_available():
    """A graph that type-checks its messages should get the class it
    expects, not a look-alike dict."""
    pytest.importorskip("langchain_core")
    from langchain_core.messages import HumanMessage

    built = default_input_builder({"step": 0})
    assert isinstance(built["messages"][0], HumanMessage)
    assert "OBSERVATION" in built["messages"][0].content


def test_the_rendered_text_carries_the_instructions_and_the_payload():
    text = render({"step": 3, "assets": []})
    assert "actions" in text, "the graph must be told the decision shape"
    assert '"step": 3' in text


def test_the_render_is_stable_under_key_order():
    """The replay key is a digest of the payload, and an unordered dump
    would give the same market two keys."""
    assert render({"a": 1, "b": 2}) == render({"b": 2, "a": 1})


def test_a_custom_input_builder_replaces_the_default_entirely():
    seen = {}

    def build(payload):
        seen["payload"] = payload
        return {"observation": payload, "mine": True}

    graph = DuckGraph(lambda payload: {"actions": []})
    agent = LangGraphAdapter(graph, input_builder=build)
    world = contract.make_world(agent)
    world.run(days=1)
    assert seen["payload"]["step"] == 0
    state, _config = graph.seen[0]
    assert state["mine"] is True
    assert "messages" not in state, "the custom builder owns the whole input"


def test_the_input_builder_never_receives_the_observation():
    """The hook sees the payload and not the Observation, and that is the
    ground-truth boundary: a hook handed `obs` could read
    `obs.engine.attribution` and no allowlist test would see it."""
    got = []
    agent = LangGraphAdapter(
        DuckGraph(lambda payload: {"actions": []}),
        input_builder=lambda payload: got.append(payload) or {
            "observation": payload})
    contract.make_world(agent).run(days=1)
    assert set(got[0]) == contract.PAYLOAD_KEYS
    assert not hasattr(got[0], "engine")


# -- the default output parser -----------------------------------------------


DECISION = {"actions": [{"symbol": "TECH_A", "side": "BUY", "quantity": 10}],
            "rationale": "why"}


def test_a_state_carrying_actions_is_the_decision():
    assert ci.parse_decision(default_output_parser(DECISION)).actions


def test_the_actions_branch_extracts_rather_than_passing_the_state_through():
    """A graph returns its WHOLE state, so `actions` arrives beside
    `observation`, `notes` and whatever else the schema declares -- and
    `parse_decision` refuses unknown keys rather than dropping part of what
    the model said. Passing the state through turned a good decision into
    "unknown keys in the decision: notes, observation"."""
    state = {"observation": {"step": 0}, "notes": ["looked"],
             "actions": [{"symbol": "TECH_A", "side": "BUY", "quantity": 1}],
             "rationale": "why"}
    extracted = default_output_parser(state)
    assert set(extracted) == {"actions", "rationale"}
    decision = ci.parse_decision(extracted)
    assert decision.actions[0].signed() == 1.0
    assert decision.rationale == "why"


def test_a_real_graph_writing_actions_into_its_state_trades():
    """The same shape end to end, through a genuine compiled graph."""
    pytest.importorskip("langgraph")
    from langgraph.graph import END, START, StateGraph

    class ActionsState(TypedDict):
        observation: dict
        notes: Annotated[list, lambda a, b: a + b]
        actions: list
        rationale: str

    def decide(state):
        return {"actions": [{"symbol": "TECH_A", "side": "BUY",
                             "quantity": 2000}],
                "rationale": "state-level actions",
                "notes": ["decided"]}

    builder = StateGraph(ActionsState)
    builder.add_node("decide", decide)
    builder.add_edge(START, "decide")
    builder.add_edge("decide", END)

    world = contract.make_world(LangGraphAdapter(builder.compile()))
    world.run(days=1)
    assert world.portfolio.positions["TECH_A"].quantity > 0


def test_a_state_carrying_a_decision_key_is_unwrapped():
    parsed = default_output_parser({"observation": {}, "decision": DECISION})
    assert parsed is DECISION


def test_a_messages_state_is_unwrapped_to_the_last_message():
    parsed = default_output_parser({"messages": ["ignored",
                                                 json.dumps(DECISION)]})
    assert ci.parse_decision(parsed).actions[0].signed() == 10.0


def test_a_message_object_and_content_blocks_both_yield_their_text():
    """`AIMessage.content` is a string for most providers and a list of
    blocks for some. A parser that read only the string would work against
    one model and return nothing against the next."""
    class Message:
        def __init__(self, content):
            self.content = content

    text = json.dumps(DECISION)
    assert ci.parse_decision(
        default_output_parser({"messages": [Message(text)]})).actions
    assert ci.parse_decision(default_output_parser(
        {"messages": [Message([{"type": "text", "text": text}])]})).actions


def test_a_decision_object_and_a_string_pass_straight_through():
    decision = ci.parse_decision(DECISION)
    assert default_output_parser(decision) is decision
    assert default_output_parser("text") == "text"


def test_an_unreadable_state_is_refused_rather_than_held():
    """The trap this module exists to close. Returning an empty decision
    here would score a plumbing failure as a considered hold: trades=0, no
    errors, nothing in the scorecard to tell the two apart afterwards."""
    with pytest.raises(ci.DecisionError) as excinfo:
        default_output_parser({"observation": {}, "next": "end"})
    message = str(excinfo.value)
    assert "next" in message, "the refusal must name the keys it found"
    assert "output_parser" in message, "and how to fix it"


def test_a_message_that_carries_no_decision_is_refused_by_name():
    """A graph that talked instead of deciding. The refusal names the
    decision shape, because the shared parser's 'no JSON object' would send
    the reader looking for a bug in their prompt rather than their graph."""
    with pytest.raises(ci.DecisionError, match="actions"):
        default_output_parser({"messages": ["I have finished thinking."]})


def test_a_non_mapping_result_is_refused():
    with pytest.raises(ci.DecisionError, match="output_parser"):
        default_output_parser(42)


@pytest.mark.parametrize("state,expected,why", [
    ({"actions": None, "observation": {}}, "refuse",
     "an unwritten channel is not a decision"),
    ({"actions": []}, "empty",
     "an EMPTY list is how a decision declines, and must still work"),
    ({"actions": None, "decision": DECISION}, "decision",
     "a real decision beside an unwritten actions channel must survive"),
    ({"actions": DECISION["actions"]}, "decision", "the ordinary case"),
], ids=["null-actions", "empty-actions", "null-actions-real-decision",
        "normal"])
def test_an_unwritten_actions_channel_is_not_a_considered_decline(
        state, expected, why):
    """The silent hold this parser exists to prevent, in its subtlest form.

    `parse_decision` maps `{"actions": None}` to an empty decision, on the
    stated grounds that a present-but-null key means its author addressed
    the question and declined. Sound for a model's JSON, where something
    wrote `null` on purpose. FALSE for a graph state channel, where None is
    the UNWRITTEN DEFAULT: a node that failed, never ran, or swallowed an
    exception leaves exactly that.

    Selecting the branch on key presence therefore scored a broken graph at
    trades=0 with an empty error list -- indistinguishable from an agent
    that looked and declined. The third case is the worst: `actions` is
    examined before `decision`, so a graph that wrote a real decision into
    `decision` while leaving `actions` at its default had that decision
    silently discarded.
    """
    if expected == "refuse":
        with pytest.raises(ci.DecisionError):
            ci.parse_decision(default_output_parser(state))
        return
    decision = ci.parse_decision(default_output_parser(state))
    if expected == "empty":
        assert decision.actions == [], why
    else:
        assert len(decision.actions) == 1, why
        assert decision.actions[0].symbol == "TECH_A", why


def test_a_graph_leaving_actions_unwritten_is_scored_as_an_error_not_a_hold():
    """The same defect measured where it actually hurt: through a real
    market. trades=0 beside an empty errors list is what a considered
    decline looks like, and a graph whose decision node never wrote must
    not wear that shape."""
    broken = LangGraphAdapter(DuckGraph(
        lambda payload: {"actions": None, "observation": payload}))
    declining = make_agent(lambda payload: {"actions": []})

    scores = tf.evaluate({"broken": broken, "declining": declining},
                         seed=7, universe=contract.universe(), days=1)

    assert scores["broken"].errors, (
        "a graph that never wrote its decision scored silently")
    assert scores["broken"].trades == 0
    # The control: a genuine decline is still a clean no-op, so the test
    # above is detecting the defect and not merely detecting zero trades.
    assert not scores["declining"].errors
    assert scores["declining"].trades == 0


def test_a_custom_output_parser_replaces_the_default():
    """A graph returning ticker-to-shares is a shape the shared contract
    does not define, and this is how a user gets it accepted without the
    adapter inventing a fifth decision dialect."""
    def parse(result):
        return {"actions": [{"symbol": symbol,
                             "side": "BUY" if qty > 0 else "SELL",
                             "quantity": abs(qty)}
                            for symbol, qty in result["orders"].items()]}

    agent = LangGraphAdapter(
        DuckGraph(lambda payload: {"orders": {"TECH_A": 2000}}),
        output_parser=parse)
    world = contract.make_world(agent)
    world.run(days=1)
    assert world.portfolio.positions["TECH_A"].quantity > 0


# -- config passthrough ------------------------------------------------------


def test_the_config_carries_tradefloor_metadata():
    """`metadata` and not `run_name`: measured on langgraph 1.2.11, a node
    receives tags, metadata and recursion_limit from the invoking config
    and does NOT receive run_name, which the tracer consumes."""
    graph = DuckGraph(contract.hold)
    world = contract.make_world(LangGraphAdapter(graph, arm="treated"))
    world.run(days=1)
    _state, config = graph.seen[0]
    assert config["metadata"]["tradefloor_step"] == 0
    assert config["metadata"]["tradefloor_day"] == 0
    assert config["metadata"]["tradefloor_arm"] == "treated"
    assert config["metadata"]["tradefloor_decision_schema"] == \
        ci.DECISION_SCHEMA_VERSION
    assert "tradefloor" in config["tags"]


def test_a_caller_config_survives_beside_the_tradefloor_keys():
    graph = DuckGraph(contract.hold)
    agent = LangGraphAdapter(graph, config={
        "recursion_limit": 12,
        "tags": ["mine"],
        "metadata": {"experiment": "rate-shock"},
    })
    contract.make_world(agent).run(days=1)
    _state, config = graph.seen[0]
    assert config["recursion_limit"] == 12
    assert config["tags"] == ["mine", "tradefloor"]
    assert config["metadata"]["experiment"] == "rate-shock"
    assert config["metadata"]["tradefloor_step"] == 0


def test_the_thread_id_is_fresh_per_decision_by_default():
    """A reused thread accumulates the run's whole conversation, so a late
    step would not be running the experiment an early one ran."""
    graph = DuckGraph(contract.hold)
    contract.make_world(LangGraphAdapter(graph)).run(days=3)
    threads = [c["configurable"]["thread_id"] for _s, c in graph.seen]
    assert len(threads) == 3
    assert len(set(threads)) == 3, threads


def test_a_pinned_thread_id_is_used_for_every_decision():
    graph = DuckGraph(contract.hold)
    contract.make_world(LangGraphAdapter(graph, thread_id="fixed")).run(days=2)
    assert {c["configurable"]["thread_id"] for _s, c in graph.seen} == {"fixed"}


def test_a_callable_thread_id_is_given_the_observation():
    graph = DuckGraph(contract.hold)
    agent = LangGraphAdapter(graph, thread_id=lambda obs: f"day-{obs.day}")
    contract.make_world(agent).run(days=2)
    assert [c["configurable"]["thread_id"] for _s, c in graph.seen] == \
        ["day-0", "day-1"]


def test_a_thread_id_in_the_caller_config_wins():
    graph = DuckGraph(contract.hold)
    agent = LangGraphAdapter(
        graph, config={"configurable": {"thread_id": "theirs"}})
    contract.make_world(agent).run(days=1)
    assert graph.seen[0][1]["configurable"]["thread_id"] == "theirs"


def test_the_config_is_rebuilt_per_call_and_not_mutated_in_place():
    """Two decisions must not share a dict: the second would inherit the
    first's step, and a caller's config object must survive the run."""
    original = {"tags": ["mine"]}
    graph = DuckGraph(contract.hold)
    contract.make_world(LangGraphAdapter(graph, config=original)).run(days=2)
    first, second = graph.seen[0][1], graph.seen[1][1]
    assert first is not second
    assert first["metadata"]["tradefloor_step"] == 0
    assert second["metadata"]["tradefloor_step"] == 6
    assert original == {"tags": ["mine"]}, "the caller's config was mutated"


# -- async -------------------------------------------------------------------


def test_an_ainvoke_only_graph_runs_through_the_shared_bridge():
    world = contract.make_world(LangGraphAdapter(AsyncDuckGraph(contract.buy)))
    world.run(days=1)
    assert world.portfolio.positions["TECH_A"].quantity > 0


def test_an_ainvoke_only_graph_works_inside_a_running_event_loop():
    """The notebook case. `asyncio.run` would raise here; the shared bridge
    is why it does not."""
    import asyncio

    async def notebook_cell():
        world = contract.make_world(
            LangGraphAdapter(AsyncDuckGraph(contract.buy)))
        world.run(days=1)
        return world.portfolio.positions["TECH_A"].quantity

    assert asyncio.run(notebook_cell()) > 0


# -- errors ------------------------------------------------------------------


def test_a_graph_error_is_wrapped_with_the_original_on_the_chain():
    """LangGraph does not wrap a node's exception -- the original type and
    message come straight through with `__cause__` None -- so the wrapping
    is the adapter's, and the original has to survive it."""
    class Boom(RuntimeError):
        pass

    def explode(payload):
        raise Boom("the node failed")

    world = contract.make_world(make_agent(explode))
    with pytest.raises(ci.FrameworkError) as excinfo:
        world.run(days=1)
    assert isinstance(excinfo.value.__cause__, Boom)
    assert "the node failed" in str(excinfo.value)


def test_evaluate_scores_a_raising_graph_instead_of_crashing():
    """One broken agent must not lose every other agent's result."""
    def explode(payload):
        raise RuntimeError("down")

    scores = tf.evaluate({"broken": make_agent(explode),
                          "fine": make_agent(contract.hold)},
                         seed=7, universe=contract.universe(), days=1)
    assert scores["broken"].errors and scores["broken"].trades == 0
    assert not scores["fine"].errors


# -- interrupts and recursion ------------------------------------------------


def test_an_interrupt_arrives_in_the_RESULT_not_as_an_exception():
    """The measurement the whole interrupt design rests on, pinned against
    the real framework. `interrupt()` never raises out of `invoke` -- with a
    checkpointer, without one, and from a subgraph, it comes back inside the
    state. LangGraph's own GraphInterrupt docstring says so: "Never raised
    directly, or surfaced to the user." If a future version changes that,
    this fails and the adapter's primary handler is in the wrong place."""
    pytest.importorskip("langgraph")
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import interrupt

    def pauser(state):
        return {"decision": {"actions": [], "rationale": interrupt("may I?")}}

    builder = StateGraph(SimpleState)
    builder.add_node("pauser", pauser)
    builder.add_edge(START, "pauser")
    builder.add_edge("pauser", END)

    for checkpointer, config in ((InMemorySaver(),
                                  {"configurable": {"thread_id": "i1"}}),
                                 (None, None)):
        graph = builder.compile(checkpointer=checkpointer)
        result = graph.invoke({"observation": {}}, config)
        assert INTERRUPT_KEY in result, (
            "an interrupt no longer arrives in the result; the adapter "
            "catches it in the output parser on that basis")
        assert result[INTERRUPT_KEY], result


def test_a_real_interrupting_graph_is_refused_with_the_reason():
    """The ruling: a market loop has nowhere to resume into, so an
    interrupting graph cannot be driven by one. Refused, never held."""
    pytest.importorskip("langgraph")
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import interrupt

    def pauser(state):
        return {"decision": {"actions": [], "rationale": interrupt("may I?")}}

    builder = StateGraph(SimpleState)
    builder.add_node("pauser", pauser)
    builder.add_edge(START, "pauser")
    builder.add_edge("pauser", END)

    agent = LangGraphAdapter(builder.compile(checkpointer=InMemorySaver()))
    with pytest.raises(GraphInterruptedError) as excinfo:
        contract.make_world(agent).run(days=1)
    message = str(excinfo.value)
    assert "may I?" in message, "the refusal must say what was asked"
    assert "resume" in message, "and why it cannot be honoured"


def test_an_interrupt_is_not_a_framework_error():
    """It is a DecisionError, catchable by name, because nothing FAILED --
    the graph did exactly what it was built to do. Wrapped in
    FrameworkError it would be indistinguishable from a provider outage and
    reachable only through __cause__."""
    assert issubclass(GraphInterruptedError, ci.DecisionError)
    assert not issubclass(GraphInterruptedError, ci.FrameworkError)
    assert issubclass(GraphInterruptedError, tf.ValidationError)


def test_an_interrupt_wins_over_a_stale_decision_in_the_same_state():
    """A checkpointed thread can carry a decision written on an earlier
    step. Answering this step with the last step's decision would be worse
    than refusing, so the interrupt key is checked first."""
    with pytest.raises(GraphInterruptedError):
        default_output_parser({
            INTERRUPT_KEY: [type("I", (), {"value": "wait"})()],
            "decision": {"actions": [{"symbol": "TECH_A", "side": "BUY",
                                      "quantity": 1}]},
        })


def test_an_interrupt_RAISED_through_ask_gets_the_same_diagnosis():
    """The exception route measurement cannot reach -- a caller's own
    runnable, a nested construct, a future version that changes its mind.
    The sentence must be the same one, or the diagnosis would depend on
    which way the interrupt travelled."""
    pytest.importorskip("langgraph")
    from langgraph.errors import GraphInterrupt
    from langgraph.types import Interrupt

    def explode(payload):
        raise GraphInterrupt([Interrupt(value="from the exception route")])

    with pytest.raises(GraphInterruptedError) as excinfo:
        contract.make_world(make_agent(explode)).run(days=1)
    assert "resume" in str(excinfo.value)
    assert isinstance(excinfo.value.__cause__, GraphInterrupt)


def test_an_empty_interrupt_list_is_not_an_interrupt():
    """`{"__interrupt__": []}` is a graph that did not interrupt. Refusing
    it would turn a falsy key into a phantom pause."""
    parsed = default_output_parser(
        {INTERRUPT_KEY: [], "decision": {"actions": []}})
    assert ci.parse_decision(parsed).actions == []


def test_a_recursion_trip_stays_a_framework_error_but_names_the_knob():
    """The opposite ruling to the interrupt: a graph that ran out of steps
    without answering genuinely failed, so the classification stands. Only
    the message changes, because the base's does not tell anyone that
    recursion_limit is theirs to raise."""
    pytest.importorskip("langgraph")
    from langgraph.errors import GraphRecursionError
    from langgraph.graph import START, StateGraph

    class Looping(TypedDict):
        observation: dict
        n: int

    builder = StateGraph(Looping)
    builder.add_node("bump", lambda state: {"n": state.get("n", 0) + 1})
    builder.add_edge(START, "bump")
    builder.add_edge("bump", "bump")

    agent = LangGraphAdapter(builder.compile(),
                             config={"recursion_limit": 4})
    with pytest.raises(ci.FrameworkError) as excinfo:
        contract.make_world(agent).run(days=1)
    message = str(excinfo.value)
    assert "recursion_limit" in message, "the message must name the knob"
    assert isinstance(excinfo.value.__cause__, GraphRecursionError)
    assert not isinstance(excinfo.value, GraphInterruptedError)


def test_an_unknown_exception_is_left_for_the_base_to_wrap():
    """_reraise_known must recognise two things and pass everything else
    through, or an ordinary bug would be mislabelled as a control signal."""
    def explode(payload):
        raise ValueError("an ordinary bug")

    with pytest.raises(ci.FrameworkError) as excinfo:
        contract.make_world(make_agent(explode)).run(days=1)
    assert isinstance(excinfo.value.__cause__, ValueError)
    assert not isinstance(excinfo.value, GraphInterruptedError)


# -- forking -----------------------------------------------------------------


def test_a_fork_shares_the_graph_and_keeps_the_hooks():
    """The graph is shared, not copied: it may hold a checkpointer and an
    HTTP client. The hooks are the policy, and two arms disagreeing about
    how the observation is presented would not be a comparison."""
    def parse(result):
        return result

    graph = DuckGraph(contract.buy)
    agent = LangGraphAdapter(graph, output_parser=parse, thread_id="pinned",
                             config={"tags": ["mine"]}, arm="control")
    twin = agent.fork()
    assert type(twin) is LangGraphAdapter
    assert twin.runnable is graph
    assert twin.output_parser is parse
    assert twin.input_builder is agent.input_builder
    assert twin.thread_id == "pinned"
    assert twin.config == {"tags": ["mine"]}
    assert twin.arm == "control"


def test_a_fork_keeps_a_subclass():
    class Louder(LangGraphAdapter):
        pass

    assert type(Louder(DuckGraph(contract.hold)).fork()) is Louder


# -- metadata ----------------------------------------------------------------


def test_the_metadata_names_langgraph_and_its_version():
    """A card that cannot say which LangGraph produced it is not
    re-runnable, which is the whole point of recording it."""
    pytest.importorskip("langgraph")
    import importlib.metadata

    info = LangGraphAdapter(DuckGraph(contract.hold)).info
    assert info.framework == "langgraph"
    assert info.framework_version == importlib.metadata.version("langgraph")
    assert info.instructions_digest == ci.digest(INSTRUCTIONS)


def test_the_config_is_digested_never_stored_in_the_metadata():
    """A RunnableConfig can carry callbacks and a LangSmith key, and
    AdapterInfo is printed."""
    agent = LangGraphAdapter(
        DuckGraph(contract.hold),
        config={"configurable": {"api_key": "sk-secret-value"}})
    published = json.dumps(agent.info.as_dict())
    assert "sk-secret-value" not in published
    assert agent.info.config_digest


def test_the_published_state_carries_no_config():
    agent = LangGraphAdapter(DuckGraph(contract.hold),
                             config={"metadata": {"token": "sk-secret"}})
    contract.make_world(agent).run(days=1)
    assert "sk-secret" not in json.dumps(agent.state())


# -- replay ------------------------------------------------------------------


def test_a_live_run_records_and_replays_without_a_graph():
    """The recording is keyed on the rendered text -- instructions and
    payload both -- so a replay reproduces the run with no graph, no
    LangGraph and no network."""
    live = LangGraphAdapter(DuckGraph(lambda payload: DECISION))
    recorder = ci.Transcript(meta=live.provenance())
    live.recorder = recorder
    first = contract.make_world(live)
    first.run(days=2)
    assert len(recorder) == 2

    replayed = LangGraphAdapter(mode="replay", transcript=recorder)
    second = contract.make_world(replayed)
    second.run(days=2)
    assert replayed.record[0]["decision"] == live.record[0]["decision"]
    assert second.portfolio.positions["TECH_A"].quantity == \
        pytest.approx(first.portfolio.positions["TECH_A"].quantity)


def test_the_record_digest_joins_to_the_transcript():
    """The record and the transcript must join on the digest without a
    hand-written mapping, or an artifact showing what the graph SAID beside
    what it TRADED has to reconcile two files by eye."""
    recorder = ci.Transcript()
    agent = LangGraphAdapter(DuckGraph(lambda payload: DECISION),
                             recorder=recorder)
    contract.make_world(agent).run(days=1)

    entry = agent.record[0]
    assert entry["digest"], "the record must carry the replay key"
    assert recorder.response_for(entry["digest"]) is not None, (
        "the record's digest does not resolve in the transcript it was "
        "recorded beside")
    assert entry["prompt"].startswith(INSTRUCTIONS[:40])
    assert "OBSERVATION" in entry["prompt"]


def test_the_record_carries_the_exchange_in_replay_mode_too():
    """A replayed run's record is as complete as a live one's: the prompt
    is rebuilt from the payload either way, so an artifact made from a
    replay shows the same chain."""
    recorder = ci.Transcript()
    live = LangGraphAdapter(DuckGraph(lambda payload: DECISION),
                            recorder=recorder)
    contract.make_world(live).run(days=1)

    replayed = LangGraphAdapter(mode="replay", transcript=recorder)
    contract.make_world(replayed).run(days=1)
    assert replayed.record[0]["digest"] == live.record[0]["digest"]
    assert replayed.record[0]["prompt"] == live.record[0]["prompt"]


def test_the_provenance_carries_the_cadence_and_the_cap():
    """What a recorder should write into `Transcript.meta`: the framework
    identity plus the two Tradefloor settings a transcript alone cannot
    reconstruct."""
    agent = LangGraphAdapter(DuckGraph(contract.hold), every=3)
    meta = agent.provenance()
    assert meta["framework"] == "langgraph"
    assert meta["decision_every_steps"] == 3
    assert meta["max_participation"] == ci.MAX_PARTICIPATION
    json.dumps(meta)


def test_changed_instructions_cannot_be_replayed_against_an_old_recording():
    """The mandate-drift guard, and it is the replay KEY rather than a check
    beside it.

    `digest(prompt)` is taken over text that BEGINS with the instructions,
    so editing them moves every digest and the first lookup misses. That is
    the strongest place to put this: it cannot be forgotten, disabled, or
    left unarmed, because it is the lookup itself. A recording made under
    one mandate can never answer a question asked under another.
    """
    recorder = ci.Transcript()
    live = LangGraphAdapter(DuckGraph(lambda payload: DECISION),
                            recorder=recorder)
    contract.make_world(live).run(days=2)

    same = LangGraphAdapter(mode="replay", transcript=recorder)
    contract.make_world(same).run(days=2)
    assert len(same.record) == 2, "the unchanged mandate must still replay"

    drifted = LangGraphAdapter(
        mode="replay", transcript=recorder,
        instructions=INSTRUCTIONS + "\n\nAlso: never trade on Fridays.")
    with pytest.raises(ci.DecisionError, match="step 0"):
        contract.make_world(drifted).run(days=1)


def test_the_recorder_stamps_its_own_provenance_on_the_first_write():
    """Self-arming, because a guard that works only when somebody remembered
    to set `meta` is off in exactly the runs nobody was careful about. This
    is provenance rather than the drift guard -- the digest above is what
    stops a stale replay -- but a transcript should describe itself whoever
    built the recorder."""
    recorder = ci.Transcript()
    assert recorder.meta == {}

    agent = LangGraphAdapter(DuckGraph(lambda payload: DECISION),
                             recorder=recorder)
    contract.make_world(agent).run(days=1)

    assert recorder.meta["framework"] == "langgraph"
    assert recorder.meta["instructions_digest"] == ci.digest(INSTRUCTIONS)
    assert recorder.meta["decision_every_steps"] == agent.every
    json.dumps(recorder.meta)


def test_stamping_never_overwrites_a_caller_supplied_meta():
    """The shipped example sets richer meta before the run -- provider,
    model, the experiment's own constants. Stamping must not clobber it."""
    recorder = ci.Transcript(meta={"framework": "langgraph",
                                   "model": "claude-opus-5",
                                   "shock_bps": 200})
    agent = LangGraphAdapter(DuckGraph(lambda payload: DECISION),
                             recorder=recorder)
    contract.make_world(agent).run(days=1)
    assert recorder.meta["model"] == "claude-opus-5"
    assert recorder.meta["shock_bps"] == 200


def test_a_replay_miss_names_the_step():
    agent = LangGraphAdapter(mode="replay", transcript=ci.Transcript())
    with pytest.raises(ci.DecisionError, match="step 0"):
        contract.make_world(agent).run(days=1)


# -- tier 2: a genuine compiled StateGraph -----------------------------------


class SimpleState(TypedDict):
    """A state schema of our own, with reducers, so the real merge runs."""

    observation: dict
    notes: Annotated[list, lambda a, b: a + b]
    decision: dict


def _cheapest_faller(state: SimpleState) -> dict[str, Any]:
    """A deterministic node. No model, no network."""
    payload = state["observation"]
    actions = [{"symbol": asset["symbol"], "side": "BUY", "quantity": 2000}
               for asset in payload["assets"]
               if asset["symbol"] == "TECH_A"]
    return {"decision": {"actions": actions, "rationale": "graph decided"},
            "notes": ["decided at step %d" % payload["step"]]}


def _look(state: SimpleState) -> dict[str, Any]:
    return {"notes": ["saw %d assets" % len(state["observation"]["assets"])]}


def real_graph():
    from langgraph.graph import END, START, StateGraph

    builder = StateGraph(SimpleState)
    builder.add_node("look", _look)
    builder.add_node("decide", _cheapest_faller)
    builder.add_edge(START, "look")
    builder.add_edge("look", "decide")
    builder.add_edge("decide", END)
    return builder.compile()


def test_a_real_compiled_stategraph_trades_a_market():
    """The test a double cannot stand in for: the genuine class, accepting
    what the adapter actually sends it, running offline."""
    pytest.importorskip("langgraph")
    agent = LangGraphAdapter(real_graph())
    world = contract.make_world(agent)
    world.run(days=2)
    assert world.portfolio.positions["TECH_A"].quantity > 0
    assert agent.record[0]["decision"]["rationale"] == "graph decided"
    assert not world.rejected


def test_a_real_graph_ignores_the_half_of_the_default_input_it_never_declared():
    """`SimpleState` declares no `messages` key, so LangGraph drops it
    before any node runs -- which is exactly what lets one default input
    serve both schemas."""
    pytest.importorskip("langgraph")
    graph = real_graph()
    payload = {"step": 0, "assets": [{"symbol": "TECH_A"}]}
    out = graph.invoke(default_input_builder(payload))
    assert "messages" not in out
    assert out["notes"] == ["saw 1 assets", "decided at step 0"]


def test_a_real_graph_with_a_checkpointer_accepts_the_derived_thread_id():
    """The thread_id is inert without a checkpointer and correct with one;
    both paths run the same adapter code, so both are worth pinning."""
    pytest.importorskip("langgraph")
    from langgraph.checkpoint.memory import InMemorySaver, MemorySaver
    from langgraph.graph import END, START, StateGraph

    assert MemorySaver is InMemorySaver, (
        "InMemorySaver is the current name; MemorySaver is the alias")

    builder = StateGraph(SimpleState)
    builder.add_node("decide", _cheapest_faller)
    builder.add_edge(START, "decide")
    builder.add_edge("decide", END)
    graph = builder.compile(checkpointer=InMemorySaver())

    agent = LangGraphAdapter(graph)
    world = contract.make_world(agent)
    world.run(days=1)
    assert world.portfolio.positions["TECH_A"].quantity > 0


def test_a_langgraph_checkpoint_holds_no_market_state():
    """The two-checkpoint claim, measured rather than asserted. A LangGraph
    checkpoint is workflow state; a Tradefloor checkpoint is market state;
    neither reconstructs the other."""
    pytest.importorskip("langgraph")
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import END, START, StateGraph

    builder = StateGraph(SimpleState)
    builder.add_node("decide", _cheapest_faller)
    builder.add_edge(START, "decide")
    builder.add_edge("decide", END)
    graph = builder.compile(checkpointer=InMemorySaver())

    config = {"configurable": {"thread_id": "t1"}}
    graph.invoke(default_input_builder({"step": 0, "assets": []}), config)
    snapshot = graph.get_state(config)

    assert set(snapshot.values) <= {"observation", "notes", "decision"}
    dumped = json.dumps(snapshot.values, default=str)
    for market_only in ("engine", "order_book", "garch", "mispricing_s",
                        "fair_value", "rng"):
        assert market_only not in dumped, (
            f"a LangGraph checkpoint carried {market_only}; the "
            "documentation says it holds workflow state only")


def test_a_real_graph_error_reaches_the_scorecard_with_its_type():
    pytest.importorskip("langgraph")
    from langgraph.graph import END, START, StateGraph

    class Boom(RuntimeError):
        pass

    def explode(state: SimpleState) -> dict:
        raise Boom("node down")

    builder = StateGraph(SimpleState)
    builder.add_node("explode", explode)
    builder.add_edge(START, "explode")
    builder.add_edge("explode", END)

    world = contract.make_world(LangGraphAdapter(builder.compile()))
    with pytest.raises(ci.FrameworkError) as excinfo:
        world.run(days=1)
    assert isinstance(excinfo.value.__cause__, Boom)


def test_a_real_recursion_limit_trip_is_reported_as_a_framework_error():
    pytest.importorskip("langgraph")
    from langgraph.errors import GraphRecursionError
    from langgraph.graph import START, StateGraph

    class Looping(TypedDict):
        observation: dict
        n: int

    builder = StateGraph(Looping)
    builder.add_node("bump", lambda state: {"n": state.get("n", 0) + 1})
    builder.add_edge(START, "bump")
    builder.add_edge("bump", "bump")

    agent = LangGraphAdapter(builder.compile(),
                             config={"recursion_limit": 4})
    with pytest.raises(ci.FrameworkError) as excinfo:
        contract.make_world(agent).run(days=1)
    assert isinstance(excinfo.value.__cause__, GraphRecursionError)


# -- tier 3: a genuine prebuilt agent over a fake model ----------------------


def fake_model(replies):
    """A chat model that says exactly what it is told, with no network.

    `GenericFakeChatModel` ships with `langchain-core`, so this needs no
    provider package and no API key.
    """
    from langchain_core.language_models.fake_chat_models import (
        GenericFakeChatModel)
    from langchain_core.messages import AIMessage

    return GenericFakeChatModel(messages=iter([AIMessage(content=r)
                                               for r in replies]))


def prebuilt_agent(model, tools=()):
    """A prebuilt agent, from whichever constructor this install provides.

    `langgraph.prebuilt.create_react_agent` is deprecated in LangGraph 1.x
    -- it points at `langchain.agents.create_agent`, a separate
    distribution the `langgraph` extra does not install -- and is removed
    in V2. The extra is `langgraph>=1.2` with no ceiling, which is the
    subpackage's deliberate policy, and the ADAPTER does not import either
    constructor: it is indifferent to which one built the graph, because
    both produce a `CompiledStateGraph` and both are duck-typed the same
    way. Only this test knows the difference.

    So it resolves at runtime: prefer the current import, fall back to the
    deprecated one, and FAIL rather than skip if neither exists. A skip
    would report green while the tier-3 guarantee -- the messages path, the
    case that would otherwise have shipped broken -- went unverified, and a
    test whose subject moved is exactly the kind that reports green
    forever.
    """
    try:
        from langchain.agents import create_agent
    except ImportError:
        pass
    else:
        return create_agent(model, tools=list(tools))

    try:
        from langgraph.prebuilt import create_react_agent
    except ImportError:
        pytest.fail(
            "neither langchain.agents.create_agent nor "
            "langgraph.prebuilt.create_react_agent is importable, so the "
            "messages-path tier cannot run. If LangGraph V2 has removed the "
            "prebuilt constructor, point this helper at the current one -- "
            "do NOT skip it. This is the tier that would otherwise ship "
            "broken.")

    with warnings.catch_warnings():
        # The deprecation is understood and handled by the branch above;
        # printing it on every run trains people to ignore warnings.
        warnings.filterwarnings("ignore",
                                message="create_react_agent has been moved")
        return create_react_agent(model, tools=list(tools))


def test_a_real_prebuilt_agent_trades_a_market_on_the_messages_path():
    """The case that would have shipped broken.

    `create_react_agent` returns `{"messages": [...]}` -- its whole state,
    not a decision. Before the shared parser learned to refuse an envelope,
    that scored as an agent which considered the market and declined:
    trades=0, no errors, nothing to distinguish it from a real hold. This
    runs the genuine prebuilt agent over a fake model and asserts it
    actually traded.
    """
    pytest.importorskip("langgraph")

    reply = json.dumps({"actions": [{"symbol": "TECH_A", "side": "BUY",
                                     "quantity": 2000}],
                        "rationale": "fake model"})
    agent = LangGraphAdapter(prebuilt_agent(fake_model([reply])))
    world = contract.make_world(agent)
    world.run(days=1)

    assert world.portfolio.positions["TECH_A"].quantity > 0, (
        "the prebuilt agent's messages envelope was not unwrapped")
    assert agent.record[0]["decision"]["rationale"] == "fake model"
    assert not world.rejected


def test_a_prebuilt_agent_that_only_talks_is_refused_not_scored_as_a_hold():
    """The same path, failing. A model that answers in prose has produced
    no decision, and the run must say so rather than record a hold."""
    pytest.importorskip("langgraph")

    agent = LangGraphAdapter(
        prebuilt_agent(fake_model(["I would rather not say."])))
    with pytest.raises(ci.DecisionError, match="actions"):
        contract.make_world(agent).run(days=1)


def test_a_prebuilt_agent_is_a_compiled_graph_like_any_other():
    pytest.importorskip("langgraph")
    from langgraph.graph.state import CompiledStateGraph

    built = prebuilt_agent(fake_model(["{}"]))
    assert isinstance(built, CompiledStateGraph)
    assert callable(getattr(built, "invoke", None))


def test_a_duck_object_is_not_a_runnable_and_is_accepted_anyway():
    """The measurement the whole design rests on: `Runnable` is a nominal
    ABC, so a working duck fails `isinstance` -- which is why the adapter
    does not use one."""
    pytest.importorskip("langchain_core")
    from langchain_core.runnables import Runnable

    duck = DuckGraph(contract.hold)
    assert not isinstance(duck, Runnable)
    assert callable(getattr(duck, "invoke", None))
    contract.make_world(LangGraphAdapter(duck)).run(days=1)


# -- the recorded run and the notebook ---------------------------------------


FIXTURE = REPO / "tests" / "fixtures" / "langgraph" / "rate-shock.json"
NOTEBOOK = REPO / "examples" / "integrations" / "langgraph" / "rate_shock.ipynb"

#: Every credential prefix that could plausibly reach a recording: Anthropic,
#: OpenAI, LangSmith personal, and LangSmith service. `AdapterInfo` has
#: nowhere to put one by construction, but a fixture is committed once and
#: read forever, so this is checked rather than trusted.
SECRET_PREFIXES = ("sk-ant-", "sk-proj-", "sk-", "lsv2_pt_", "lsv2_sk_",
                   "pylf_v1_", "api_key", "Authorization", "Bearer ")


def experiment_module():
    """The shipped example.

    Loaded by location under a unique name, not by bare module name.
    Three integrations name their study `rate_shock.py`, and `sys.modules`
    caches by name -- so importing the bare name would hand whichever
    integration ran first to whoever asked second, silently, with every
    attribute answered by the wrong experiment. Demonstrated: importing
    langgraph's and then pydantic_ai's returns the same object.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "example_langgraph_rate_shock", EXAMPLE)
    module = importlib.util.module_from_spec(spec)
    # Registered before execution: dataclasses resolve their own types
    # through sys.modules[cls.__module__], and a module absent from it
    # raises inside dataclasses rather than anywhere near this line.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_live_mode_requires_an_explicit_opt_in(monkeypatch):
    """A credential existing in the environment is not consent to spend it.

    Without this gate, a developer with a key exported who ran the slow
    suite would re-execute every live notebook and spend real money,
    having asked for neither -- and the replay-identity cell would then
    read False against the committed recording, so the surprise bill would
    arrive dressed as a test failure. Live needs the opt-in ON TOP of the
    key and the framework; replay is the default even when live is
    possible.
    """
    experiment = experiment_module()
    monkeypatch.setenv(experiment.LIVE_KEY_VAR, "set-but-not-consent")
    monkeypatch.delenv(experiment.LIVE_OPT_IN_VAR, raising=False)
    assert not experiment.can_run_live(), (
        "a key alone must never enable live calls")

    monkeypatch.setenv(experiment.LIVE_OPT_IN_VAR, "1")
    if experiment.have_live_packages():
        assert experiment.can_run_live(), (
            "opt-in plus key plus framework is exactly the live condition")

    monkeypatch.delenv(experiment.LIVE_KEY_VAR)
    assert not experiment.can_run_live(), "the opt-in alone is not enough either"


def test_the_missing_requirement_is_named_in_gate_order(monkeypatch):
    """The notebook prints this, so it has to be a recipe rather than a
    complaint. Checked in the order the gate applies, because telling
    someone about the key when the opt-in is also missing sends them round
    the loop twice."""
    experiment = experiment_module()
    monkeypatch.delenv(experiment.LIVE_OPT_IN_VAR, raising=False)
    monkeypatch.delenv(experiment.LIVE_KEY_VAR, raising=False)
    missing = experiment.live_requirements()
    assert missing.startswith(experiment.LIVE_OPT_IN_VAR), missing
    assert experiment.LIVE_KEY_VAR in missing


def test_a_blocked_find_spec_reads_as_absent_rather_than_raising(monkeypatch):
    """`find_spec` RAISES whatever a `sys.meta_path` finder raises, rather
    than returning None. This suite installs such a finder two tests below
    to prove the replay path never imports the framework, so an unguarded
    check would turn "LangGraph is absent" into a crash inside the test
    written to confirm it can be absent."""
    experiment = experiment_module()
    import importlib.util

    def explode(name, *a, **k):
        raise ImportError(f"{name} is blocked")

    monkeypatch.setattr(importlib.util, "find_spec", explode)
    assert experiment.have_live_packages() is False
    monkeypatch.setenv(experiment.LIVE_OPT_IN_VAR, "1")
    monkeypatch.setenv(experiment.LIVE_KEY_VAR, "k")
    assert experiment.can_run_live() is False


def test_the_live_call_count_states_the_fork_arithmetic():
    """A reader looking at a FORK cannot guess the cost: the shared history
    is decided once and both arms inherit it, then each arm decides for
    itself. The notebook prints this before the run cell spends anything."""
    experiment = experiment_module()
    shared, per_arm, total = experiment.live_call_count()
    assert shared == experiment.WARMUP_DAYS
    assert per_arm == experiment.BRANCH_DAYS
    assert total == shared + 2 * per_arm
    assert total == len(ci.Transcript.load(FIXTURE)), (
        "the previewed call count and the recording disagree")


def test_the_fixture_carries_no_credential():
    """A recording is committed once and read forever. `Transcript` excludes
    credentials by construction, and this checks the artefact anyway."""
    if not FIXTURE.exists():
        pytest.fail(f"{FIXTURE} is missing; run langgraph/rate_shock.py --record")
    text = FIXTURE.read_text(encoding="utf-8")
    for prefix in SECRET_PREFIXES:
        assert prefix not in text, f"the fixture contains {prefix!r}"


def test_the_fixture_names_the_run_that_produced_it():
    """A recording whose card cannot say what produced it is not
    re-runnable, which is the whole point of recording it."""
    transcript = ci.Transcript.load(FIXTURE)
    meta = transcript.meta
    assert meta["framework"] == "langgraph"
    assert meta["framework_version"]
    assert meta["provider"] == "anthropic" and meta["model"]
    assert meta["seed"] and meta["warmup_days"] and meta["branch_days"]
    assert meta["decision_every_steps"] == 6


def test_the_fixture_labels_both_arms():
    """`fork` copies the agent, so it cannot know which branch it became.
    Without the labels the file cannot be read a branch at a time, and every
    entry claims to be shared history."""
    entries = ci.Transcript.load(FIXTURE).as_dict()["entries"]
    arms = {e["arm"] for e in entries}
    assert arms == {"shared", "control", "+200bps"}, arms


def test_the_recorded_run_replays_end_to_end():
    """The claim the notebook makes, tested: the whole forked experiment
    reproduces from the recording, with no graph object and no network."""
    experiment = experiment_module()
    agent = experiment.replay_agent(ci.Transcript.load(FIXTURE))
    _world, _mark, control, shock = experiment.run_experiment(agent)

    expected = experiment.WARMUP_DAYS + experiment.BRANCH_DAYS
    assert len(control.agent.record) == expected
    assert len(shock.agent.record) == expected
    # `World.rejected` is the list of orders the MARKET refused, and empty is
    # the claim the notebook makes about sizing: the graph reads
    # `buying_power` and stays inside both caps. Not `errors` -- `World` has
    # none, and it does not need one here: unlike `evaluate`, `World.run`
    # does not catch what `act` raises, so a run that returns at all is
    # already proof the adapter never raised.
    assert control.rejected == [] and shock.rejected == [], (
        control.rejected, shock.rejected)


def test_the_recorded_arms_diverge_at_the_shock():
    """The experiment's result, pinned. If a future edit to the observation
    mapping quietly stopped the agent seeing the policy rate, the notebook
    would still run and its story would be gone."""
    experiment = experiment_module()
    agent = experiment.replay_agent(ci.Transcript.load(FIXTURE))
    _world, _mark, control, shock = experiment.run_experiment(agent)

    fork_step = experiment.WARMUP_DAYS * experiment.STEPS_PER_DAY
    assert control.fork_step == fork_step
    diverged = experiment.action_divergence(control, shock)
    assert diverged is not None, (
        "the two arms never traded differently; the notebook's experiment "
        "has no result and should be re-recorded or told as a flat run")
    assert diverged >= fork_step, (
        "the arms differed BEFORE the intervention, which means the fork "
        "was not clean")


def test_replay_needs_neither_the_framework_nor_a_key():
    """The strongest version of the claim, and the one that makes the
    notebook readable by someone who has never installed LangGraph.

    A subprocess with an import blocker in front of `sys.meta_path`, so
    reaching for the framework RAISES rather than quietly succeeding
    because this suite already imported it. The key is removed from the
    environment for the same reason.
    """
    code = f"""
import sys
BANNED = ("langgraph", "langchain_core", "langchain_anthropic", "anthropic")


class Blocked:
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in BANNED:
            raise ImportError("%s is blocked for this test" % name)
        return None


sys.meta_path.insert(0, Blocked())
sys.path.insert(0, r"{REPO / 'examples' / 'integrations' / 'langgraph'}")

import rate_shock as experiment
from tradefloor.integrations import common as ci

agent = experiment.replay_agent(ci.Transcript.load(experiment.FIXTURE))
world, mark, control, shock = experiment.run_experiment(agent)
assert len(control.agent.record) == experiment.WARMUP_DAYS + experiment.BRANCH_DAYS
assert control.rejected == [] and shock.rejected == []
hit = [m for m in BANNED if m in sys.modules]
assert not hit, "the replay path imported %s" % hit
print("ok")
"""
    env = dict(os.environ)
    for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        env.pop(name, None)
    done = subprocess.run([sys.executable, "-c", code], capture_output=True,
                          text=True, timeout=600, env=env)
    assert done.returncode == 0, done.stdout[-3000:] + done.stderr[-3000:]


def test_the_notebook_replays_the_committed_fixture():
    """The notebook must not have been executed against a recording that is
    no longer the committed one. Its own cells assert nothing, so this
    checks the join: the notebook names the fixture, and the fixture is the
    one the experiment module points at."""
    nbformat = pytest.importorskip("nbformat")
    if not NOTEBOOK.exists():
        pytest.fail(f"{NOTEBOOK.name} is missing from examples/integrations/")
    nb = nbformat.read(NOTEBOOK, as_version=4)
    source = "\n".join(c["source"] for c in nb.cells)
    assert "experiment.FIXTURE" in source, (
        "the notebook should load the fixture the experiment module names, "
        "not a path of its own")
    assert "import rate_shock as experiment" in source

    experiment = experiment_module()
    assert experiment.FIXTURE == FIXTURE


# -- the shipped example -----------------------------------------------------


def test_the_example_runs_end_to_end():
    """The shipped example, run whole. It asserts its own gates and exits
    non-zero if any fails; the return code is the verdict. Not behind the
    slow flag, because it is offline and finishes in seconds -- and because
    `tests/test_examples.py` only AST-parses scripts, so if this file does
    not run it, nothing does."""
    pytest.importorskip("langgraph")
    if not EXAMPLE.exists():
        pytest.fail(f"{EXAMPLE.name} is missing from examples/integrations/")
    done = subprocess.run([sys.executable, str(EXAMPLE)],
                          capture_output=True, text=True, timeout=300)
    assert done.returncode == 0, done.stdout[-3000:] + done.stderr[-3000:]
    assert "decisions" in done.stdout
