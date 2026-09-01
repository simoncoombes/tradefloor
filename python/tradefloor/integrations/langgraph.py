"""Run a LangGraph graph inside a Tradefloor market, and record what it did.

LangGraph is an open-source framework for stateful, multi-actor agent
workflows from LangChain (https://github.com/langchain-ai/langgraph,
MIT). This module is a Tradefloor integration for LangGraph. There is no
affiliation with or endorsement by LangChain, and nothing here is an
official LangGraph interface.

    Tradefloor observation
            |
      LangGraphAdapter       allowlist -> payload -> graph input
            |
        LangGraph            the user's real compiled graph
            |
      LangGraphAdapter       unwrap -> parse -> validate -> share deltas
            |
    Tradefloor execution

LangGraph owns the workflow: which nodes run, in what order, with what
model behind them. Tradefloor owns the market, the macro path, execution,
the order book, fills, accounting, checkpoints, forks, interventions and
the comparison. The division and the shared machinery are described in
:mod:`tradefloor.integrations.common`; this module is the LangGraph half.

## We duck-type on ``.invoke``, and that is deliberate

A user may hand us a ``CompiledStateGraph``, a graph built by
``langgraph.prebuilt.create_react_agent``, or any LangChain ``Runnable``.
Measured against langgraph 1.2.11, every one of those has the ancestry
``CompiledStateGraph -> Pregel -> PregelProtocol -> Runnable``, so an
``isinstance`` check against ``Runnable`` would catch all three.

It is still the wrong check. ``Runnable`` is a nominal ABC with no
``__subclasshook__``, so ``isinstance(obj, Runnable)`` is ``False`` for a
plain object with a perfectly good ``invoke`` -- which is exactly the
deterministic double a test wants to pass, and exactly what
``tests/test_langgraph.py`` uses. Importing ``Runnable`` to run the check
would also drag ``langchain_core`` onto the import path to reject the one
caller who does not need it. So the check is
``callable(getattr(obj, "invoke", None))``, and the only class named by
name is ``StateGraph`` -- duck-checked, never imported -- because an
uncompiled builder has no ``invoke`` at all and the one-word fix is worth
saying out loud.

## The two hooks, and why a fixed state schema would be wrong

``input_builder`` and ``output_parser`` are not conveniences. A graph's
state schema belongs to whoever wrote the graph, and three measured
behaviours of langgraph 1.2.11 say what happens when an adapter guesses it:

- **An undeclared key is dropped before the node runs.** Sending
  ``{"observation": ...}`` to a graph whose schema does not declare
  ``observation`` does not raise; the node simply never sees it. So a
  fixed input shape fails silently, which is the worst way to fail.
- **A graph keyed on ``messages`` fed ``{"observation": ...}`` dies inside
  the user's own node**, with ``IndexError: list index out of range`` from
  ``state["messages"][-1]``. There is no schema validation at the graph
  boundary for a ``TypedDict`` state, so the traceback points at a
  stranger's code and says nothing about the real cause.
- **A missing key is a bare ``KeyError`` from the node.**

The default input therefore carries BOTH shapes -- ``observation`` and
``messages`` -- because a graph that declares only one gets what it needs
and the unused half is dropped harmlessly. That is what makes the default
work for a graph written for this harness AND for the message-shaped
graphs ``create_react_agent`` produces, with no hook at all. Anything else
needs an ``input_builder``, and that is a property of LangGraph, not a gap
here.

## Unwrapping the envelope is this module's job

A graph returns its whole state. ``{"messages": [...]}`` is not a
decision, and
:func:`~tradefloor.integrations.common.parse_decision` refuses it by
design: a mapping with no ``actions`` key used to validate as an empty
action list, so a graph whose plumbing was wrong scored as an agent that
considered the market and declined -- ``trades=0``, no errors, nothing to
distinguish it from a real hold. :func:`default_output_parser` extracts
the decision from the three envelopes a graph realistically returns, and
REFUSES anything it does not recognise rather than falling through to a
hold.

## An interrupting graph is refused, and that is a decision

``interrupt()`` is how a LangGraph graph asks a human a question and waits.
This adapter refuses it, with a message saying so, and the refusal is not a
gap to be filled later.

An interrupt means "pause here, resume later", and resuming needs the run
to still be where it stopped. A Tradefloor market has no such place:
``act`` is called at one decision point, the market advances the moment it
returns, and the book, the macro path and the variance process move with
it. There is nothing to resume INTO. A decision answered an hour later
would be about a market that no longer exists, and scoring the pause as a
hold would record a paused workflow as a considered choice -- the same
silent-hold failure the envelope rules exist to stop.

Where it is caught is worth knowing, because it is not where you would
guess. Measured on langgraph 1.2.11, ``GraphInterrupt`` never escapes
``invoke``: with a checkpointer, without one, raised directly by a node,
and from a subgraph, all four come back as ``{"__interrupt__":
[Interrupt(...)]}`` inside the returned state. LangGraph's own docstring
agrees -- "Never raised directly, or surfaced to the user". So the primary
handler is in :func:`default_output_parser`, not an ``except`` clause;
:func:`_reraise_known` covers the exception route as well, so the
diagnosis reads the same either way. Both raise
:class:`GraphInterruptedError`, which a caller can catch by name.

``GraphRecursionError`` is treated as the opposite: a genuine failure, so
it stays a
:class:`~tradefloor.integrations.common.FrameworkError`. Only its message
is rewritten, to name ``recursion_limit`` as the knob the caller owns.

## Two things called a checkpoint

LangGraph has checkpointers too, and they are a different thing from
Tradefloor's. A LangGraph checkpoint is WORKFLOW state: dumped from a
graph compiled with an ``InMemorySaver`` (the current class;
``MemorySaver`` is a compatibility alias), it holds the graph's channel
values and which node runs next, and nothing else. A Tradefloor
checkpoint is simulated MARKET state: prices, the order book, the macro
path, the variance process, the RNG.

Neither reconstructs the other, and the failure is quiet in both
directions. Restoring a LangGraph thread into a fresh market replays a
deliberation against a market that never produced it; restoring a
Tradefloor checkpoint says nothing about what the agent had been
thinking. Keep them paired by run, and do not treat either as a save file
for the pair. ``thread_id`` is addressed through
``config["configurable"]["thread_id"]``, and this adapter derives a fresh
one per decision by default -- see :class:`LangGraphAdapter`.

## The module name does not shadow the package

This file is ``tradefloor/integrations/langgraph.py`` and the third-party
package is ``langgraph``. Python 3 has no implicit relative imports, so
``import langgraph`` here resolves absolutely to the installed package;
verified by building a module of exactly this name in exactly this
position and confirming that ``langgraph.__path__`` pointed into
site-packages while both modules sat under distinct ``sys.modules`` keys.
The framework is imported inside the function that needs it regardless,
which is this subpackage's rule and is what keeps ``import tradefloor``
free of it.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from .._core import ValidationError
from .common import (DECISION_SCHEMA_VERSION, MAX_PARTICIPATION, AdapterInfo,
                     Decision, DecisionError, FrameworkAdapter,
                     FrameworkError, MissingDependencyError, Transcript,
                     check_prior, digest, parse_decision,
                     replay_response, require, run_sync,
                     stamp_resume_counts)

#: What the graph is told to produce, when the default input builder renders
#: the prompt text. Short on purpose: the decision contract is stated once,
#: in :func:`~tradefloor.integrations.common.decision_schema`, and a graph
#: that wants the machine-readable form should bind
#: :func:`~tradefloor.integrations.common.decision_model` as its output type
#: rather than be told the schema twice in prose.
INSTRUCTIONS = (
    "You are managing a portfolio in a simulated market. You are given the "
    "observable state as JSON: prices, recent returns, realised volatility, "
    "the top of the book, your positions and your cash.\n"
    "\n"
    "Reply with ONE JSON object and nothing else:\n"
    '  {"actions": [{"symbol": "TICKER", "side": "BUY|SELL|HOLD", '
    '"quantity": <shares>}], "rationale": "one line"}\n'
    "\n"
    "The side carries the direction, so a sell is SELL with a POSITIVE "
    "quantity. HOLD carries no quantity. Name a symbol at most once. To "
    "change nothing, return an empty actions list -- that is a decision, "
    "and it is not the same as returning no actions key.\n"
    "Orders execute as market sweeps against the live book. There are no "
    "limit prices and no order types. Do not exceed max_order_shares for a "
    "name; a larger request is clipped and the clip is recorded against you."
)

#: The key an interrupted graph puts its pending questions under. Hard-coded
#: rather than imported from ``langgraph.constants``, which still exports
#: ``INTERRUPT`` but warns on the import: "This constant is now private and
#: should not be used directly. Deprecated in LangGraph V1.0 to be removed in
#: V2.0." A private constant is not a thing to depend on, and the string is
#: part of the invoke RESULT that users see, so it is public in the only
#: sense that matters here.
INTERRUPT_KEY = "__interrupt__"


class GraphInterruptedError(DecisionError):
    """The graph paused for input instead of deciding.

    A distinct class so a caller can catch this exactly, rather than
    string-matching a message or reaching through ``__cause__``. It is a
    :class:`~tradefloor.integrations.common.DecisionError` because at the
    Tradefloor boundary the fact that matters is that no decision was
    produced -- not a
    :class:`~tradefloor.integrations.common.FrameworkError`, because
    nothing failed: an interrupting graph did exactly what it was built to
    do. See :func:`default_output_parser` for why it cannot be honoured.
    """


#: The state keys the default input builder writes. Named here because
#: :func:`default_input_builder` and the documentation must not drift, and
#: because a user writing their own graph wants to know what to declare.
DEFAULT_INPUT_KEYS = ("observation", "messages")


def render(payload: dict[str, Any], *, instructions: str = INSTRUCTIONS,
           ) -> str:
    """The payload as the text a language model reads.

    JSON rather than prose, and sorted, for two reasons. It is what a graph
    built around a chat model handles best without a second parser between
    it and the facts, and a canonical ordering makes the text a stable
    replay key -- an unordered dump would produce a different digest for
    the same market on a different run.

    Rendered from the PAYLOAD only, never from the Observation. The
    Observation carries ``.engine``, which knows fair value, the factor
    attribution and the macro path the run has not reached; the payload is
    the allowlisted view, and rendering from it is what makes the
    ground-truth boundary checkable by a test that cannot see inside this
    function.
    """
    body = json.dumps(payload, indent=2, sort_keys=True)
    return f"{instructions}\n\nOBSERVATION\n{body}" if instructions else body


def _user_message(text: str) -> Any:
    """One user-turn message, for the ``messages`` half of the default input.

    A real ``langchain_core.messages.HumanMessage`` when LangGraph is
    installed, because a graph that type-checks its messages should get the
    class it expects. On a bare install -- which is the duck-typed
    ``.invoke`` object a test uses, where there is no graph to satisfy --
    it falls back to ``{"role": "user", "content": ...}``, the shape
    ``add_messages`` coerces to exactly the same ``HumanMessage`` when
    there is one. :func:`~tradefloor.integrations.common.require` is still
    the thing that tries the import, so anyone who reaches this in a
    traceback gets the pip command rather than a bare ImportError.
    """
    try:
        messages = require(
            "langchain_core.messages", extra="langgraph",
            purpose="the default input builder's 'messages' key needs "
                    "'langchain_core', which LangGraph installs")
    except MissingDependencyError:
        return {"role": "user", "content": text}
    return messages.HumanMessage(content=text)


def default_input_builder(payload: dict[str, Any], *,
                          instructions: str = INSTRUCTIONS,
                          ) -> dict[str, Any]:
    """The graph input for one decision: both shapes, so either graph works.

    ```python
    {"observation": <the serialized payload>,
     "messages": [HumanMessage(content=<the rendered text>)]}
    ```

    Both keys, always, and the module docstring says why: a key a graph's
    schema does not declare is dropped before any node runs, so the unused
    half costs nothing, while a graph handed only the half it does not read
    fails deep inside its own nodes. A graph declaring ``observation`` gets
    the structured payload; a graph declaring ``messages`` -- the shape
    ``MessagesState`` and ``create_react_agent`` use, which is most of them
    -- gets the same facts as text. Neither is told about the other.

    Takes the payload and not the Observation, for the reason
    :func:`render` gives.
    """
    return {"observation": payload,
            "messages": [_user_message(render(payload,
                                              instructions=instructions))]}


def _text_of(message: Any) -> str:
    """The text of one message, whether it is an object, a dict, or blocks.

    ``AIMessage.content`` is a string for most providers and a list of
    content blocks for some. Both arrive here, and a parser that handled
    only the string would work against one model and return nothing against
    the next -- which, before the shared parser learned to refuse an
    envelope, scored as a considered hold.
    """
    if isinstance(message, str):
        return message
    content = getattr(message, "content", None)
    if content is None and isinstance(message, dict):
        content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "\n".join(parts)
    return ""


def _interrupt_message(payload: Any) -> str:
    """Why an interrupting graph cannot be driven by a market loop.

    The refusal is deliberate and it is not a limitation that can be
    lifted by trying harder. ``interrupt()`` means "pause here, a human
    will answer, resume later", and resuming requires the run to still be
    where it stopped. Tradefloor has no such place: ``act`` is called at
    one decision point, the market advances the moment it returns, and the
    book, the macro path and the variance process have all moved on. There
    is nothing to resume INTO. Answering the interrupt later would deliver
    a decision made about a market that no longer exists, which is worse
    than no decision, and scoring it as a hold would record a paused
    workflow as a considered choice.

    So it refuses, and says which question went unanswered, because the
    fix is a design change in the caller's graph and they need to know
    which node to look at.
    """
    asked = ""
    if isinstance(payload, dict):
        pending = payload.get(INTERRUPT_KEY) or ()
        values = []
        for item in pending if isinstance(pending, (list, tuple)) else ():
            value = getattr(item, "value", item)
            values.append(str(value))
        asked = "; ".join(v for v in values if v)
    return (
        "the graph interrupted instead of deciding"
        + (f", asking: {asked[:200]}" if asked else "")
        + ". A LangGraph interrupt means 'pause here, resume later', and a "
        "Tradefloor market loop has nowhere to resume into: the agent is "
        "asked at one decision point and the market advances the moment "
        "act() returns, so by the time a human answered, the book and the "
        "macro path would have moved. Answering later would decide about a "
        "market that no longer exists. Remove the interrupt from the path "
        "this adapter drives -- run the human-in-the-loop graph separately "
        "and give Tradefloor a graph that decides -- or handle the "
        "interrupt in your own output_parser if you have a resume story "
        "this adapter does not know about.")


def default_output_parser(result: Any) -> Any:
    """Pull the decision out of whatever the graph returned.

    A graph returns its whole state, so this is an unwrapping step and
    nothing more: it hands
    :func:`~tradefloor.integrations.common.parse_decision` something that
    function accepts and lets the shared validator do the deciding. The
    ladder, first match wins:

    1. a :class:`~tradefloor.integrations.common.Decision`, or a string --
       passed straight through
    2. a mapping carrying ``actions`` -- it IS the decision already
    3. a mapping carrying ``decision`` -- the natural shape for a graph
       written for this harness, which puts its answer in a state key
    4. a mapping carrying ``messages`` -- the ``MessagesState`` shape; the
       last message's text is taken, fences and trailing prose included,
       because the shared text parser tolerates both

    Anything else RAISES. That is the whole point of this function. A
    mapping this cannot read is a plumbing failure -- a graph that never
    reached its decision node, a state key spelled differently -- and
    returning an empty decision for one would score it as an agent that
    considered the market and declined, with ``trades=0`` and an empty
    error list. There is nothing in a scorecard that would tell the two
    apart afterwards, so the refusal has to happen here.
    """
    if isinstance(result, (Decision, str)):
        return result
    if isinstance(result, dict):
        # Checked FIRST, and checked here rather than in an except clause,
        # because this is where an interrupt actually arrives. Measured on
        # langgraph 1.2.11, `interrupt()` never raises out of `invoke`: with
        # a checkpointer, without one, raised directly by a node, and from a
        # subgraph, all four return `{"__interrupt__": [Interrupt(...)]}` as
        # part of the state. LangGraph's own `GraphInterrupt` docstring says
        # so -- "Never raised directly, or surfaced to the user".
        #
        # It is checked before `decision` because a checkpointed thread can
        # carry a decision written on an earlier step, and answering this
        # step with the last step's decision would be worse than refusing.
        if result.get(INTERRUPT_KEY):
            raise GraphInterruptedError(_interrupt_message(result))
        # `is not None`, NOT `"actions" in result`, and the difference is a
        # silent hold. `parse_decision` maps `{"actions": None}` to an empty
        # decision, on the stated grounds that a present-but-null key means
        # its author addressed the question and declined. That reasoning is
        # sound for a model's JSON output, where something wrote `null` on
        # purpose. It is FALSE for a graph state channel, where `None` is
        # the unwritten default: a node that failed, never ran, or swallowed
        # an exception leaves exactly that, and reading it as a considered
        # decline scores the failure at trades=0 with an empty error list --
        # indistinguishable from an agent that looked and declined.
        #
        # It is the same argument this module's docstring makes about
        # UNDECLARED keys, which I failed to extend to declared-but-unwritten
        # ones. The worst case is the third: with a presence check, a graph
        # that wrote a real decision into `decision` while leaving `actions`
        # at its default had that decision silently discarded, because
        # `actions` is examined first.
        if result.get("actions") is not None:
            # The decision FIELDS, extracted -- not the whole state. A graph
            # returns everything its schema declares, so a state carrying
            # `actions` almost always carries `observation`, `notes` and the
            # rest beside it, and `parse_decision` refuses unknown keys (it
            # will not silently drop part of what the model said). Passing
            # the state through therefore turned a perfectly good decision
            # into "unknown keys in the decision: notes, observation".
            # Extracting here is the same unwrapping job as the `messages`
            # branch, one level shallower.
            decision = {"actions": result["actions"]}
            if "rationale" in result:
                decision["rationale"] = result["rationale"]
            return decision
        if "decision" in result and result["decision"] is not None:
            return result["decision"]
        messages = result.get("messages")
        if isinstance(messages, (list, tuple)) and messages:
            text = _text_of(messages[-1])
            # Parsed HERE rather than handed back for `act` to parse, so
            # that a message carrying no decision is reported as what it is
            # -- a graph that talked instead of deciding -- rather than as
            # the shared parser's "no JSON object", which does not mention
            # the envelope the text came out of and leaves the reader
            # looking for a bug in their prompt rather than their graph.
            # parse_decision revalidates a Decision, so parsing twice is
            # free and this stays the one validator.
            try:
                return parse_decision(text)
            except DecisionError as exc:
                raise DecisionError(
                    "the graph's last message carried no decision. A "
                    "decision is a JSON object with an 'actions' list, and "
                    "an empty list is how one declines to trade. The "
                    f"message was {text.strip()[:200]!r}. Underlying: {exc}"
                ) from exc
        keys = ", ".join(sorted(str(k) for k in result)) or "none"
        raise DecisionError(
            f"the graph returned a state this parser cannot read a decision "
            f"out of (keys present: {keys}). It looks for 'actions', then "
            "'decision', then the last entry of 'messages'. Returning an "
            "empty decision instead would score a plumbing failure as a "
            "considered hold -- trades=0, no errors, indistinguishable from "
            "an agent that looked and declined. Write the decision into one "
            "of those keys, or pass output_parser= to read your own.")
    raise DecisionError(
        f"the graph returned a {type(result).__name__}, which carries no "
        "decision this parser can read. A graph returns its state, normally "
        "a dict; pass output_parser= if yours returns something else.")


class LangGraphAdapter(FrameworkAdapter):
    """A LangGraph graph, in the shape ``evaluate`` and ``World`` run.

    ```python
    from langgraph.graph import END, START, StateGraph

    builder = StateGraph(MyState)
    ...
    graph = builder.compile()

    agent = LangGraphAdapter(graph)
    scores = tf.evaluate({"graph": agent}, seed=7, universe=roster)
    ```

    ``runnable`` is anything with an ``invoke`` method -- a
    ``CompiledStateGraph``, a ``create_react_agent`` agent, any LangChain
    ``Runnable``, or a plain object of your own. An object with only
    ``ainvoke`` is driven through
    :func:`~tradefloor.integrations.common.run_sync`, the one supported
    bridge. It is called as ``runnable.invoke(graph_input, config)``, two
    positional arguments, which is the form the ``Runnable`` ABC
    guarantees; an object whose ``invoke`` takes only one argument is the
    single shape that will not work here.

    ``input_builder`` takes the serialized payload and returns the graph
    input; ``output_parser`` takes what the graph returned and returns
    anything
    :func:`~tradefloor.integrations.common.parse_decision` accepts. Both
    default to the module-level functions of the same name, and the
    defaults are built so that a graph ignoring them still works -- see the
    module docstring for the three measured reasons a fixed state schema
    would not.

    Neither hook is given the Observation. They see the payload and the
    graph's own output, which is what keeps the ground-truth boundary
    something a test can check: a hook handed ``obs`` could read
    ``obs.engine.attribution`` and no allowlist test would see it.

    ``config`` is a ``RunnableConfig`` merged into the one this adapter
    builds. Tradefloor run identity goes into ``config["metadata"]``,
    because that is the key measured to arrive intact at a node --
    ``run_name`` is consumed by the tracer and does not -- and a
    ``"tradefloor"`` tag goes into ``config["tags"]``. Your keys survive;
    the Tradefloor ones are added beside them.

    ``thread_id`` addresses a LangGraph checkpointer and defaults to a
    fresh id per decision, ``tradefloor-<arm>-d<day>-s<step>``. That choice
    matters only if you compiled with a checkpointer, and then it is the
    one a comparison harness wants: a single reused thread would accumulate
    the whole run's conversation, so step 20 would not be running the
    experiment step 1 ran. Pass a string to pin one, or a callable taking
    the Observation to derive your own.

    ``mode`` is ``"live"`` or ``"replay"``. Replay reads a recorded
    :class:`~tradefloor.integrations.common.Transcript` and needs no graph,
    no LangGraph install and no network -- worth having even though a local
    graph is free, because a graph with a language model in it is neither
    free nor deterministic, and a recorded run is how such an experiment
    stays reproducible.
    """

    def __init__(self, runnable: Any = None, *, mode: str = "live",
                 transcript: Transcript | None = None,
                 recorder: Transcript | None = None,
                 prior: Transcript | None = None,
                 input_builder: Callable[[dict[str, Any]], Any] | None = None,
                 output_parser: Callable[[Any], Any] | None = None,
                 instructions: str = INSTRUCTIONS,
                 config: dict[str, Any] | None = None,
                 thread_id: str | Callable[[Any], str] | None = None,
                 info: AdapterInfo | None = None, every: int = 6,
                 fundamentals: dict[str, dict[str, Any]] | None = None,
                 max_participation: float = MAX_PARTICIPATION,
                 arm: str = "") -> None:
        if mode not in ("live", "replay"):
            raise ValidationError(
                f"mode must be 'live' or 'replay', got {mode!r}")
        if mode == "replay" and transcript is None:
            raise ValidationError(
                "replay mode needs a transcript to replay. Load one with "
                "Transcript.load(path), or pass mode='live' with a graph.")
        if mode == "live":
            _check_runnable(runnable)

        super().__init__(
            info=info or _describe(runnable, instructions, config),
            every=every, fundamentals=fundamentals,
            max_participation=max_participation, arm=arm)
        self.runnable = runnable
        self.mode = mode
        self.transcript = transcript
        self.recorder = recorder
        self.prior = check_prior(
            prior, mode=mode, recorder=recorder,
            instructions_digest=self.info.instructions_digest)
        self.input_builder = input_builder or default_input_builder
        self.output_parser = output_parser or default_output_parser
        self.instructions = instructions
        self.config = dict(config) if config else None
        self.thread_id = thread_id

    # -- the one framework-specific method --------------------------------

    def ask(self, obs: Any, payload: dict[str, Any]) -> Any:
        """One graph run for this payload, unwrapped to a decision.

        The exchange declared to the record, and the replay key, are both
        the RENDERED TEXT rather than the graph input. Three reasons, and
        they point the same way: the graph input may hold ``HumanMessage``
        objects, which have no canonical JSON form to digest or to write
        into a transcript; it varies with a caller's ``input_builder``,
        which does not change what the market showed the agent; and a
        transcript has to be readable and diffable by a person, which a
        dict of message objects is not. The text already carries both
        things that determine the answer -- the instructions and the
        payload -- so a recording survives a change to how the input is
        assembled and correctly misses when the market or the mandate
        moved.
        """
        prompt = render(payload, instructions=self.instructions)
        key = digest(prompt)
        self.record_exchange(prompt, key=key)

        if self.mode == "replay":
            return replay_response(self.transcript, key, step=obs.step,
                                   day=obs.day)

        graph_input = self.input_builder(payload)
        config = self.build_config(obs)
        try:
            result = self.call_or_resume(
                key, lambda: self._invoke(graph_input, config))
        except Exception as exc:                          # noqa: BLE001
            _reraise_known(exc)
            raise
        parsed = self.output_parser(result)

        if self.recorder is not None:
            # Stamp provenance on the FIRST write, so a transcript describes
            # itself whoever built the recorder. Self-arming on purpose: a
            # guard that only works when somebody remembered to set `meta`
            # is off in exactly the runs nobody was careful about.
            #
            # This is provenance, NOT the drift guard. What actually stops a
            # recording being replayed under changed instructions is the key
            # itself: `digest(prompt)` over text that BEGINS with the
            # instructions, so editing them moves every digest and every
            # lookup misses at step 0. That cannot be forgotten, because it
            # is the lookup rather than a check beside it.
            if not self.recorder.meta.get("framework"):
                self.recorder.meta.update(self.provenance())
            # Recorded as text, because a replay feeds the recorded response
            # straight back to parse_decision and a string is the one shape
            # that survives a JSON round trip unchanged.
            self.recorder.record({
                "arm": self.arm, "step": obs.step, "day": obs.day,
                "digest": key, "prompt": prompt,
                "response": parsed if isinstance(parsed, str)
                else json.dumps(_as_jsonable(parsed)),
            })
            stamp_resume_counts(self.recorder, self.prior)
        return parsed

    def _invoke(self, graph_input: Any, config: dict[str, Any]) -> Any:
        """Call the runnable, preferring the synchronous entry point.

        ``invoke`` first, because it is the only method a duck-typed object
        is guaranteed to have and because calling it from inside a running
        event loop is measurably fine -- Tradefloor's loop is synchronous
        and never runs one anyway. ``ainvoke`` is the fallback for an
        object that has only that, and it goes through the shared
        :func:`~tradefloor.integrations.common.run_sync` bridge rather than
        ``asyncio.run``, which raises inside a notebook's loop.
        """
        invoke = getattr(self.runnable, "invoke", None)
        if callable(invoke):
            return invoke(graph_input, config)
        ainvoke = getattr(self.runnable, "ainvoke", None)
        if callable(ainvoke):
            return run_sync(ainvoke(graph_input, config))
        # Unreachable through the constructor, which checks; reachable if
        # `runnable` was replaced on the instance afterwards.
        _check_runnable(self.runnable)
        raise ValidationError("unreachable")     # pragma: no cover

    # -- configuration ----------------------------------------------------

    def build_config(self, obs: Any) -> dict[str, Any]:
        """The ``RunnableConfig`` for this decision.

        The caller's ``config`` with Tradefloor's run identity merged into
        ``metadata``, a ``"tradefloor"`` tag appended, and a ``thread_id``
        under ``configurable``. Nothing the caller set is overwritten
        except by their own later edit: their metadata keys survive beside
        ours, and a ``thread_id`` they pinned in ``config`` wins over the
        derived one.

        ``metadata`` and not ``run_name``: measured on langgraph 1.2.11, a
        node receives ``tags``, ``metadata`` and ``recursion_limit`` from
        the config it was invoked with, and does NOT receive ``run_name``,
        which the tracer consumes. Metadata is therefore the only one of
        the three that a graph can actually read, and the only honest place
        to put facts a node might branch on.
        """
        config: dict[str, Any] = dict(self.config) if self.config else {}

        metadata = dict(config.get("metadata") or {})
        metadata.setdefault("tradefloor_step", obs.step)
        metadata.setdefault("tradefloor_day", obs.day)
        metadata.setdefault("tradefloor_arm", self.arm)
        metadata.setdefault("tradefloor_decision_schema",
                            DECISION_SCHEMA_VERSION)
        config["metadata"] = metadata

        tags = list(config.get("tags") or [])
        if "tradefloor" not in tags:
            tags.append("tradefloor")
        config["tags"] = tags

        configurable = dict(config.get("configurable") or {})
        configurable.setdefault("thread_id", self._thread_id(obs))
        config["configurable"] = configurable
        return config

    def _thread_id(self, obs: Any) -> str:
        if callable(self.thread_id):
            return str(self.thread_id(obs))
        if self.thread_id:
            return str(self.thread_id)
        # Fresh per decision. See the class docstring: a reused thread
        # accumulates the run's whole conversation, and then a late step is
        # not running the experiment an early one ran.
        return f"tradefloor-{self.arm or 'main'}-d{obs.day}-s{obs.step}"

    # -- forking ----------------------------------------------------------

    def fork_kwargs(self) -> dict[str, Any]:
        """Extends the base with everything this constructor added.

        The graph, the transcript and the recorder are SHARED, not copied.
        A compiled graph may hold a checkpointer and, with a model behind
        it, an HTTP client; deep-copying one is wasteful at best and a
        shared socket at worst. Both directions want the sharing anyway --
        a replay of one arm must read the same recorded run as the other,
        and a live recording of both arms belongs in one file. The hooks
        are the policy, and two arms disagreeing about how the observation
        is presented would not be a comparison.
        """
        kwargs = super().fork_kwargs()
        kwargs.update({
            "runnable": self.runnable,
            "mode": self.mode,
            "transcript": self.transcript,
            "recorder": self.recorder,
            "prior": self.prior,
            "input_builder": self.input_builder,
            "output_parser": self.output_parser,
            "instructions": self.instructions,
            "config": self.config,
            "thread_id": self.thread_id,
        })
        return kwargs


def langgraph_agent(runnable: Any, **kwargs: Any) -> LangGraphAdapter:
    """A :class:`LangGraphAdapter` around ``runnable``.

    The convenience spelling, matching
    :func:`~tradefloor.integrations.callable.callable_agent`, for the
    common case where the graph is the whole configuration:

    ```python
    agent = langgraph_agent(graph, every=6)
    ```
    """
    return LangGraphAdapter(runnable, **kwargs)


# -- helpers ------------------------------------------------------------------


def _reraise_known(exc: Exception) -> None:
    """Re-raise the two LangGraph exceptions worth naming; return otherwise.

    ``FrameworkAdapter.act`` wraps anything that escapes ``ask`` in a
    :class:`~tradefloor.integrations.common.FrameworkError`, which is right
    for a failure and wrong for a control-flow signal. Narrowing the base's
    handler would need a per-framework exempt list in shared code, so the
    knowledge lives here, where the framework is known.

    Measured on langgraph 1.2.11, ``GraphInterrupt`` does NOT escape
    ``invoke`` -- an interrupt comes back in the result, and
    :func:`default_output_parser` is what meets it. This clause exists for
    the paths that measurement cannot cover: a caller's own runnable, a
    nested construct, a future version that changes its mind. It costs a
    lazy import taken only on an exception, and it means the diagnosis is
    the same sentence either way instead of depending on which route the
    interrupt took.

    ``GraphRecursionError`` stays a
    :class:`~tradefloor.integrations.common.FrameworkError`, because unlike
    an interrupt it IS a failure: the graph ran out of steps without
    reaching an answer. Only the message changes, to name the knob -- the
    base's "raised GraphRecursionError instead of returning a decision" is
    accurate and does not tell anyone that ``recursion_limit`` is theirs to
    raise.
    """
    try:
        from langgraph.errors import GraphInterrupt, GraphRecursionError
    except ImportError:
        # A duck-typed runnable on an install with no LangGraph. Nothing to
        # recognise; `act` wraps it as it would any other framework failure.
        return

    if isinstance(exc, GraphInterrupt):
        raise GraphInterruptedError(
            _interrupt_message({INTERRUPT_KEY: getattr(exc, "args", ())
                                and exc.args[0]})) from exc
    if isinstance(exc, GraphRecursionError):
        raise FrameworkError(
            f"the graph hit its recursion limit without reaching a "
            f"decision: {exc}. That is a graph or configuration problem, "
            "not a market one -- either the graph loops without a stop "
            "condition, or the limit is too low for the work it does. "
            "Raise it with config={'recursion_limit': N} on the adapter, "
            "which merges into every run.") from exc


def _check_runnable(runnable: Any) -> None:
    """Refuse a non-runnable at construction, with the fix in the message.

    Duck-typed, never ``isinstance``: see the module docstring. The
    ``StateGraph`` case is named because it is the mistake that will
    actually be made -- ``compile()`` is easy to forget, an uncompiled
    builder has no ``invoke`` at all, and the generic message would not
    say the one word that fixes it. It is recognised by shape rather than
    by import so that the check costs nothing, and works, when LangGraph
    is not installed.
    """
    if callable(getattr(runnable, "invoke", None)):
        return
    if callable(getattr(runnable, "ainvoke", None)):
        return
    if (callable(getattr(runnable, "compile", None))
            and callable(getattr(runnable, "add_node", None))):
        raise ValidationError(
            f"{type(runnable).__name__} is a graph BUILDER, not a runnable "
            "graph: it has no invoke(). Compile it first, and pass the "
            "result:\n"
            "    agent = LangGraphAdapter(builder.compile())")
    raise ValidationError(
        f"LangGraphAdapter needs something with an invoke() method, got "
        f"{type(runnable).__name__}. Pass a compiled LangGraph graph, an "
        "agent from langgraph.prebuilt, any LangChain Runnable, or any "
        "object exposing invoke(input, config=None). For a run with no "
        "graph at all, pass mode='replay' with a transcript.")


def _describe(runnable: Any, instructions: str,
              config: dict[str, Any] | None) -> AdapterInfo:
    """What ran, for the scorecard and the transcript.

    The installed LangGraph version is read from package METADATA rather
    than by importing the package, so describing an adapter never pulls the
    framework onto the import path. A run whose card cannot say which
    LangGraph produced it is not re-runnable, which is the whole point of
    recording it.

    The config is DIGESTED, never stored. A ``RunnableConfig`` can carry
    callbacks and a LangSmith key, and this object is printed.
    """
    import importlib.metadata

    try:
        version = importlib.metadata.version("langgraph")
    except Exception:                                        # noqa: BLE001
        # Not installed, or installed without metadata -- a duck-typed
        # runnable, most likely. An empty string is the honest answer; a
        # guess would put a version in a citation that nothing ran.
        version = ""

    name = getattr(runnable, "name", "")
    return AdapterInfo(
        framework="langgraph", framework_version=version,
        agent_name=name if isinstance(name, str) else "",
        instructions_digest=digest(instructions) if instructions else "",
        config_digest=digest(_as_jsonable(config)) if config else "")


def _as_jsonable(value: Any) -> Any:
    """A JSON-able rendering of ``value``, for digesting and recording.

    A ``RunnableConfig`` may hold callbacks and a decision may arrive as a
    :class:`~tradefloor.integrations.common.Decision`; neither is JSON. The
    unrepresentable parts become their type name, which is enough to make
    two configs comparable by digest and keeps an object that cannot be
    serialised from raising in the middle of a run.
    """
    if isinstance(value, Decision):
        return value.as_dict()
    if isinstance(value, dict):
        return {str(k): _as_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_as_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return f"<{type(value).__name__}>"
