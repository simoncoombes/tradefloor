"""What every framework adapter shares, derived from the FinRobot integration.

`finrobot.py` came first, and it settled the questions every adapter meets:
what an agent may see, what a decision is, how framework output becomes share
deltas, and how a paid, non-deterministic call is recorded so the experiment
replays for free. This module is those answers with the FinRobot spelling
removed, so the second adapter and the tenth make the same choices without
re-litigating them.

    Tradefloor observation
            |
      the adapter            serialize_observation -> the framework's input
            |
      the framework          reasons, decides
            |
      the adapter            parse_decision -> orders_from -> share deltas
            |
    Tradefloor execution

## Ownership

The framework owns interpretation, the portfolio decision and a short
rationale. Tradefloor owns the market, the macro path, execution, the order
book, fills, accounting, checkpoints, forks, interventions and the
comparison. A framework MUST NOT mutate engine state, and every path from a
framework RESPONSE to the engine runs through :func:`parse_decision` and
:func:`orders_from` -- but that is validation, not confinement. ``act`` and
``ask`` hold the Observation, the Observation carries the live engine, and
the seam is not sandboxed: the adapter boundary is exactly an ordinary
agent's, no tighter. The serializer's allowlist and the contract checks
catch the accident of a cooperating author reading or writing what they
should not; nothing in this package restrains an adapter that reaches for
``obs.engine`` deliberately, and claiming otherwise would leave an author
believing in a property nobody enforces.

## The observation allowlist

:class:`~tradefloor.harness.Observation` carries ``.engine``, and the engine
knows the answer key: :func:`tradefloor.fair_value`, the nine-way factor
attribution of every price move, each company's ``mispricing_s``, and --
through a :class:`~tradefloor.Scenario` -- the macro path the run has not
reached yet. An agent reading any of those inverts the simulator, and the
experiment around it measures nothing.

So :func:`serialize_observation` names every field it emits, one at a time,
and reads nothing by reflection. Adding a field takes a deliberate edit here.
A denylist would go stale the first time the engine gained an attribute; an
allowlist survives that. ``tests/test_integrations.py`` runs the mapping
against an engine proxy that raises on the forbidden attributes, so a future
edit reaching for one fails on the access.

## The decision is market-sweep only

``Portfolio.execute`` sweeps the live book at whatever price it gives. There
is no resting order, no order type and no limit price anywhere at the agent
boundary, so the decision model carries a symbol, a side and a share count
and nothing else. A framework that emits ``order_type`` or ``limit_price``
is refused with a message naming the missing capability, and any other
field the contract does not define is refused the same way, because
silently dropping a field would execute an instruction the agent never
gave.

## Two-stage validation

:func:`parse_decision` is structural: it checks that the output is a
decision. :func:`orders_from` is market-shaped and needs the Observation: an
unknown symbol raises, an oversized order is clipped to the participation cap
with the clip returned as a note, and sub-one-share dust is dropped. The
split matters because the two failures mean different things -- the first is
the framework failing its output contract, the second is a well-formed
decision the market cannot take -- and an experiment scores them differently.

## Replay

A live framework decision costs money, and running one twice gives two
answers. Tradefloor's market is deterministic; a model behind an API is not.
:class:`Transcript` records every interaction keyed by :func:`digest` of the
exact input the framework was sent, and :func:`replay_response` reads it
back. Change the observation mapping and the digest changes, the key goes
missing, and the replay RAISES naming the step -- keyed by (arm, step) it
would answer the new question with a response given to the old one. Replay
needs no framework, no network and no API key.

This reproduces the AGENT. The market is already reproducible without it:
:func:`tradefloor.replay.replay` rebuilds an engine from its order log, and
nothing here duplicates that.
"""

from __future__ import annotations

import copy
import hashlib
import importlib
import json
import re
import statistics
from typing import Any, Literal, Sequence

from .._core import ValidationError
from ..counterfactual import MACRO_FIELDS

#: The macro fields an adapter may show its framework. Bound to
#: ``counterfactual.MACRO_FIELDS`` itself, so the two cannot drift apart over
#: what a macro experiment covers -- the library has already settled which
#: macro fields a run is ABOUT, and that set leaves out ``qe_pe_boost``, a
#: model coefficient no exchange publishes.
OBSERVABLE_MACRO = MACRO_FIELDS

#: The sides a decision may name. HOLD carries no quantity and produces no
#: order, so the agent can decline a decision period.
SIDES = ("BUY", "SELL", "HOLD")

#: Price rows kept for the recent-return and volatility lines. Five days at
#: the library's six steps a day: long enough for a realised-volatility number
#: to mean something, short enough to describe recent conditions.
HISTORY_STEPS = 30

#: Fraction of an instrument's average daily volume one order may take.
#: ``tradefloor.baselines.rebalance`` uses the same 2% for every shipped
#: baseline. Without a cap, one mis-scaled share count moves the price further
#: than the intervention does, and the comparison then measures market impact.
#: Requests above the cap are clipped and RECORDED as clipped. Being unable to
#: size a position says something about the agent, so the trace carries it.
MAX_PARTICIPATION = 0.02

#: Version of the decision contract: the shape :func:`decision_schema`
#: publishes and :func:`parse_decision` enforces. Recorded in adapter
#: metadata and in :meth:`FrameworkAdapter.state`, so a replayed or compared
#: run can say which contract its decisions were validated under.
DECISION_SCHEMA_VERSION = "1"


# -- errors -------------------------------------------------------------------


class IntegrationError(ValidationError):
    """Something went wrong between a framework and the market.

    The root of the adapter error family, and a subclass of
    :class:`~tradefloor.ValidationError` so a caller already catching the
    library's refusals catches these too. Raise one of the subclasses, which
    say WHERE it went wrong; this class exists so "any integration failure"
    is one except clause.
    """


class MissingDependencyError(IntegrationError, ImportError):
    """An optional dependency is not installed.

    Also an :class:`ImportError`, deliberately: the FinRobot adapter raised a
    plain ImportError for this case, and callers written against it -- and
    against the general Python convention for a missing module -- keep
    working. Raised by :func:`require`, which puts the exact pip command in
    the message, because "ModuleNotFoundError: No module named 'agents'"
    tells a user neither that the dependency is optional nor how to get it.
    """


class FrameworkError(IntegrationError):
    """The framework call itself failed.

    The network died, the provider refused, the framework raised out of its
    own plumbing. Distinct from :class:`DecisionError` because the framework
    never produced an output to judge -- an experiment scoring "the agent
    answered badly" should not count "the call never completed" in the same
    column. The original exception rides on ``__cause__``.
    """


class DecisionError(IntegrationError):
    """The framework returned something that is not an executable decision.

    Raised by :func:`parse_decision` for output that cannot be turned into a
    well-formed :class:`Decision`: no JSON, an unknown side, a negative or
    non-finite quantity, a symbol named twice, an order type the market does
    not have.

    It raises instead of repairing. A guess at what the model meant would be
    a second, unrecorded agent between the framework and the market, and
    every experiment run afterwards would measure that too.
    """


class ReplayMiss(DecisionError):
    """The recording holds no answer for this input.

    A subclass of :class:`DecisionError`, so every caller written to catch
    one and charge the agent a step keeps catching it. Its own class
    because that charge is wrong here, and wrong in a way that publishes.

    A model that answered badly is a fact about the AGENT. A recording that
    does not cover the question is a fact about the EXPERIMENT: the
    observation mapping, the instructions or the market moved since the
    recording was made. Counting the second as a refusal turns a
    misconfigured replay into an agent that refused every decision, and the
    run then completes, writes its artifacts and publishes that. Measured,
    before this class existed: two arms ran with a transcript that covered
    neither, reported twenty refusals each, and produced an empty series
    two hundred lines later.

    So :class:`~tradefloor.counterfactual.World` re-raises this under
    ``on_refusal="skip"`` while skipping everything else.
    """


class MarketRefusalError(DecisionError):
    """The decision was well-formed, and this market cannot take it.

    Raised by :func:`orders_from` for a symbol the market does not list. A
    subclass of :class:`DecisionError` rather than a sibling, because the
    FinRobot integration raises one class for both stages and callers written
    against that behaviour -- catch DecisionError, charge the agent a step --
    must keep catching everything a decision can fail with.
    """


def require(module: str, *, extra: str | None = None, pip: str | None = None,
            purpose: str = "", note: str = "") -> Any:
    """Import an optional dependency, or refuse with the command that fixes it.

    ``extra`` names the tradefloor extra that installs the module, and wins
    over ``pip``, which names a bare requirement for a dependency no extra
    carries. The point is that the error is actionable text: the extra, the
    exact pip command, and the original exception chained -- never a raw
    ModuleNotFoundError reaching the user of an adapter they installed on
    purpose and configured halfway.

    ``note`` is the sentence that goes AFTER the pip command: the version
    constraint or collision worth knowing before running it. It exists
    because the FinRobot adapter's hand-written refusal ends with one --
    "FinRobot supports Python 3.10 and 3.11 only, and Tradefloor needs 3.11
    or later, so the two overlap at 3.11 exactly" -- and that is the
    sentence that matters: without it, somebody on Python 3.12 runs the
    command and gets a resolver failure they cannot interpret. Every
    framework has a floor or a ceiling worth stating here.

    Call it INSIDE the function that needs the framework, never at module
    scope. The rule is set out in this subpackage's ``__init__``: replaying a
    recorded run, and ``import tradefloor`` itself, must never require the
    framework to be installed.
    """
    try:
        return importlib.import_module(module)
    except ImportError as exc:
        if extra:
            command = f'pip install "tradefloor[{extra}]"'
        else:
            command = f'pip install "{pip or module}"'
        need = purpose or f"this integration needs {module!r}"
        lines = [f"{need}, and it is not installed. It is an optional "
                 "dependency, so the core package does not carry it:",
                 f"    {command}"]
        if note:
            lines.append(note)
        lines.append(f"Original error: {exc}")
        raise MissingDependencyError("\n".join(lines)) from exc


def run_sync(awaitable: Any) -> Any:
    """Run one coroutine to completion from Tradefloor's synchronous loop.

    The ONE supported bridge from ``act()`` to an async framework API, and
    it is shared because every framework needs it and the failure it guards
    against only shows up in a notebook. The frameworks' own synchronous
    entry points raise when called from a thread that already has a running
    event loop -- the OpenAI Agents SDK's ``Runner.run_sync`` raises a bare
    RuntimeError, and Jupyter runs everything inside a loop -- so an adapter
    built on them works in a script and dies in the notebook the same reader
    tries next. Call the framework's ASYNC entry point and hand the
    coroutine here instead.

    With no loop running in this thread, this is ``asyncio.run``. With one
    running, the coroutine runs on a separate thread with its own fresh
    loop, and this call BLOCKS until it finishes; the result comes back, and
    an exception comes back as the original exception object with its chain
    intact, so ``FrameworkAdapter.act`` still wraps the real error.

    What this does not buy, stated plainly: it does not make Tradefloor
    concurrent. One decision runs at a time and the market waits for it, as
    the run loop requires. And every call gets a FRESH event loop, so an
    object bound to a loop -- an aiohttp session opened outside, a
    framework client that caches its loop -- cannot be created once and
    awaited across calls. Create loop-bound resources inside the coroutine.
    """
    import asyncio
    import inspect as _inspect

    if not _inspect.isawaitable(awaitable):
        raise ValidationError(
            f"run_sync takes a coroutine, got {type(awaitable).__name__}. "
            "Call the framework's async entry point and pass what it "
            "returns, unawaited.")
    if _inspect.iscoroutine(awaitable):
        coro = awaitable
    else:
        # asyncio.run accepts only a coroutine, and futures or task-like
        # awaitables are bound to the loop that made them anyway.
        async def _await(a: Any) -> Any:
            return await a
        coro = _await(awaitable)

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    # A loop is already running in this thread -- a notebook, or a caller
    # driving the World from inside async code. Nesting is not an option and
    # neither is raising, so the coroutine gets its own thread and its own
    # loop. `Future.result()` re-raises the exception OBJECT raised inside,
    # `__cause__` and all, which is what keeps the chain honest across the
    # boundary.
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


# -- the decision model -------------------------------------------------------


class Action:
    """One validated instruction: a symbol, a side, and a share count.

    The constructor validates the two fields that carry direction, because
    :meth:`signed` returns 0.0 for any side it does not recognise -- so a
    hand-built ``Action("A", "SHORT", 5)`` handed straight to
    :func:`orders_from` would silently become a hold, and a negative
    quantity would flip the trade's sign. Refused here instead; case is
    normalised by :func:`parse_decision`, which is where lenient input
    belongs.
    """

    __slots__ = ("symbol", "side", "quantity")

    def __init__(self, symbol: str, side: str, quantity: float = 0.0) -> None:
        if side not in SIDES:
            raise DecisionError(
                f"side must be one of {', '.join(SIDES)}, got {side!r}. "
                "Action is built from validated input; parse_decision "
                "normalises case and refuses the rest.")
        quantity = float(quantity)
        if not quantity >= 0 or quantity == float("inf"):
            raise DecisionError(
                f"quantity must be a finite, non-negative share count, got "
                f"{quantity}. The side carries the direction.")
        self.symbol = symbol
        self.side = side
        self.quantity = float(quantity)

    def as_dict(self) -> dict[str, Any]:
        return {"symbol": self.symbol, "side": self.side,
                "quantity": self.quantity}

    def signed(self) -> float:
        """The share delta this action asks for. HOLD is zero."""
        if self.side == "BUY":
            return self.quantity
        if self.side == "SELL":
            return -self.quantity
        return 0.0

    def __eq__(self, other: Any) -> bool:
        return isinstance(other, Action) and self.as_dict() == other.as_dict()

    def __repr__(self) -> str:
        if self.side == "HOLD":
            return f"HOLD {self.symbol}"
        return f"{self.side} {self.quantity:,.0f} {self.symbol}"


class Decision:
    """What the framework decided at one decision point, after validation.

    ``rationale`` is a short comment that never affects execution. It is
    recorded -- :meth:`FrameworkAdapter.decision` publishes it, and the
    counterfactual comparison reads it -- but no character of it reaches
    :func:`orders_from`.
    """

    __slots__ = ("actions", "rationale")

    def __init__(self, actions: Sequence[Action], rationale: str = "") -> None:
        self.actions = list(actions)
        self.rationale = rationale

    def as_dict(self) -> dict[str, Any]:
        return {"actions": [a.as_dict() for a in self.actions],
                "rationale": self.rationale}

    def __eq__(self, other: Any) -> bool:
        return isinstance(other, Decision) and self.as_dict() == other.as_dict()

    def __repr__(self) -> str:
        if not self.actions:
            return "Decision(no change)"
        return "Decision(" + ", ".join(repr(a) for a in self.actions) + ")"


def decision_schema() -> dict[str, Any]:
    """The decision contract as a JSON Schema, for structured output.

    Hand this to whatever schema-binding mechanism a framework offers, and
    the framework physically cannot emit a shape :func:`parse_decision` would
    refuse. ``additionalProperties: false`` is load-bearing: it is what stops
    a model inventing ``order_type`` or ``limit_price``, fields this market
    has no execution path for.

    A fresh dictionary per call, because callers hand schemas to libraries
    that annotate them in place, and a shared module constant would let one
    framework's bookkeeping leak into the next adapter's contract.

    Versioned by ``DECISION_SCHEMA_VERSION``; validated output still goes
    through :func:`parse_decision`, which is the one validator.
    """
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "TradefloorDecision",
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "actions": {
                "type": "array",
                "description": "One instruction per symbol. Empty means "
                               "change nothing.",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "symbol": {"type": "string", "minLength": 1},
                        "side": {"enum": list(SIDES)},
                        "quantity": {
                            "type": "number",
                            "minimum": 0,
                            "description": "Shares, non-negative. The side "
                                           "carries the direction. Omit or "
                                           "zero for HOLD.",
                        },
                    },
                    "required": ["symbol", "side"],
                },
            },
            "rationale": {
                "type": "string",
                "description": "One or two sentences. Never affects "
                               "execution.",
            },
        },
        "required": ["actions"],
    }


#: Built once by :func:`decision_model` and reused, so two adapters asking
#: for the model get the SAME class and an isinstance check between them
#: means something.
_PYDANTIC_MODEL: list[Any] = []


def decision_model() -> Any:
    """A Pydantic model of the decision, built on demand.

    The canonical decision contract is stated ONCE, in
    :func:`decision_schema`, and this model reads every constraint out of
    that schema -- the side enum, the quantity floor, the symbol length, the
    field descriptions, which fields are required -- rather than repeating
    them. The first version repeated them, drifted immediately, and the
    drift was expensive in exactly the place a model is used: the
    constraints a bound model carries land in the schema the provider is
    shown, so an invalid decision becomes hard to GENERATE at all, and
    where a framework retries on validation failure the model's refusal is
    repaired inside the turn. Anything the model waves through instead dies
    one layer later in :func:`parse_decision` and costs the step. Whether a
    retry exists is framework-specific -- PydanticAI retries within the
    turn; the OpenAI Agents SDK makes one call and raises on the first
    invalid response -- so an adapter must not assume one.
    ``tests/test_integrations.py`` asserts the two renderings agree field
    by field, which is what actually protects this.

    On top of what a JSON Schema can express, the model enforces the two
    rules only code can state -- HOLD carries no quantity, no symbol named
    twice -- and normalises a lowercase side before the enum check, because
    case carries no meaning here and rejecting it would score a correct
    decision as a failure. Belt and braces is deliberate:
    :func:`parse_decision` stays total regardless of what any framework
    validated, so convert with ``parse_decision(instance.model_dump())``.

    Pydantic is imported here, on the first call, never at module scope;
    the plain-Python :class:`Action` and :class:`Decision` remain the
    canonical objects, because the core package depends on nothing.
    """
    if _PYDANTIC_MODEL:
        return _PYDANTIC_MODEL[0]
    pydantic = require("pydantic", pip="pydantic>=2",
                       purpose="the Pydantic decision model needs 'pydantic'")

    schema = decision_schema()
    action_schema = schema["properties"]["actions"]["items"]
    props = action_schema["properties"]
    Side = Literal[tuple(props["side"]["enum"])]

    class ActionModel(pydantic.BaseModel):
        """One instruction: a symbol, a side, a non-negative share count."""

        model_config = pydantic.ConfigDict(extra="forbid")

        symbol: str = pydantic.Field(
            min_length=props["symbol"]["minLength"])
        side: Side
        # allow_inf_nan=False, explicitly: pydantic's default admits an
        # INFINITE quantity through a field whose schema says minimum 0,
        # and the model whose entire job is making an invalid decision
        # hard to generate was accepting one parse_decision refuses.
        quantity: float = pydantic.Field(
            0.0, ge=props["quantity"]["minimum"], allow_inf_nan=False,
            description=props["quantity"]["description"])

        @pydantic.field_validator("side", mode="before")
        @classmethod
        def _case_is_meaningless(cls, value: Any) -> Any:
            return value.upper() if isinstance(value, str) else value

        @pydantic.model_validator(mode="after")
        def _hold_carries_no_quantity(self) -> "ActionModel":
            if self.side == "HOLD" and self.quantity:
                raise ValueError(
                    "HOLD means no trade; a quantity beside it does not say "
                    "whether the model wanted to buy it or to keep it")
            return self

    class DecisionModel(pydantic.BaseModel):
        """The decision contract, schema version %s.""" % (
            DECISION_SCHEMA_VERSION)

        model_config = pydantic.ConfigDict(extra="forbid")

        actions: list[ActionModel] = pydantic.Field(
            description=schema["properties"]["actions"]["description"])
        rationale: str = pydantic.Field(
            "", description=schema["properties"]["rationale"]["description"])

        @pydantic.model_validator(mode="after")
        def _symbols_named_once(self) -> "DecisionModel":
            seen = [action.symbol for action in self.actions]
            duplicated = sorted({s for s in seen if seen.count(s) > 1})
            if duplicated:
                raise ValueError(
                    f"symbols named more than once: {', '.join(duplicated)}")
            return self

    _PYDANTIC_MODEL.append(DecisionModel)
    return DecisionModel


#: A JSON object anywhere in a text response. Models wrap answers in code
#: fences and add a closing sentence however firmly the instructions ask them
#: not to. Failing on a fence would score formatting compliance instead of
#: the portfolio decision. This finds the BRACES; ``json`` parses what is
#: inside.
_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


def parse_decision(raw: Any) -> Decision:
    """Turn framework output into a validated :class:`Decision`.

    Accepts the three shapes framework output arrives in: a
    :class:`Decision` already built (revalidated, because "already a
    Decision" says nothing about what was put in it), a mapping with
    ``actions`` and ``rationale`` keys -- which is also what a Pydantic
    model's ``model_dump()`` produces -- and a text response holding a JSON
    object, fences and trailing prose tolerated.

    Structural validation only: this checks that the answer is a decision.
    Whether the symbols exist and the sizes are executable belongs to
    :func:`orders_from`, which has the observation needed to answer it.

    It never unwraps a framework's envelope. A mapping without an
    ``actions`` key -- graph state, a result wrapper -- is refused rather
    than read as a hold; extracting the decision from whatever a framework
    returns is the adapter's job, in ``ask()``, before this is called.

    And it never drops a key it does not know, at either level. A
    ``stop_loss`` or a ``time_in_force`` silently discarded would leave the
    agent believing it has protection this market cannot give, and would
    let the dict path accept output the schema-bound model path refuses --
    two arms of one study running two contracts. The same inputs are driven
    through both paths by ``tests/test_integrations.py``, which asserts
    they agree on accept-versus-refuse.
    """
    if isinstance(raw, Decision):
        return _decision_from_mapping(raw.as_dict())
    if isinstance(raw, dict):
        return _decision_from_mapping(raw)
    if isinstance(raw, str):
        return _decision_from_text(raw)
    raise DecisionError(
        f"cannot read a decision out of a {type(raw).__name__}. An adapter "
        "returns a Decision, a dict with an 'actions' list, or a JSON string.")


def _no_duplicate_keys(pairs: list) -> dict[str, Any]:
    """`json.loads` hook that refuses an object naming a key twice.

    Standard JSON parsing keeps the LAST occurrence, so a response carrying
    '"actions": [], "actions": [...]' resolved to whichever the model
    emitted second, silently, and nothing recorded that a first statement
    existed. `_decision_from_mapping` refuses duplicate SYMBOLS one layer
    up for exactly this reason -- two instructions with no defined order --
    and the JSON layer owed the same principle to duplicate keys.
    """
    keys = [key for key, _ in pairs]
    duplicated = sorted({key for key in keys if keys.count(key) > 1})
    if duplicated:
        raise DecisionError(
            f"the JSON response names keys more than once: "
            f"{', '.join(duplicated)}. json parsing keeps the last "
            "occurrence, so which of the model's statements reached the "
            "market would depend on emission order, and nothing would "
            "record that another existed.")
    return dict(pairs)


def _decision_from_text(text: str) -> Decision:
    if not text.strip():
        raise DecisionError(
            "the framework returned an empty response, so there is no "
            "decision to validate. An empty answer is not the same as HOLD, "
            "and treating it as one would score a failed call as a "
            "considered choice.")

    # The exact contract first, so a compliant answer is parsed as written
    # and never through the brace search, which cannot tell a top-level list
    # from an object and would report the wrong thing about it.
    try:
        raw = json.loads(text.strip(), object_pairs_hook=_no_duplicate_keys)
    except json.JSONDecodeError:
        match = _OBJECT.search(text)
        if match is None:
            raise DecisionError(
                "no JSON object in the framework response. The decision "
                "contract is one object and nothing else; got "
                f"{text.strip()[:200]!r}") from None
        try:
            raw = json.loads(match.group(0),
                             object_pairs_hook=_no_duplicate_keys)
        except json.JSONDecodeError as exc:
            raise DecisionError(
                "the JSON object in the framework response does not parse: "
                f"{exc}. Got {match.group(0)[:200]!r}") from exc

    if not isinstance(raw, dict):
        raise DecisionError(
            "expected a JSON object with an 'actions' key, got a "
            f"{type(raw).__name__}")
    return _decision_from_mapping(raw)


#: The keys a decision and an action may carry. ``order_type`` and
#: ``limit_price`` sit on the action list so their SPECIFIC refusals below,
#: which explain the missing capability, fire instead of the generic
#: unknown-key one, which can only report it.
_DECISION_KEYS = frozenset(("actions", "rationale"))
_ACTION_KEYS = frozenset(("symbol", "side", "quantity", "order_type",
                          "limit_price"))


def _decision_from_mapping(raw: dict[str, Any]) -> Decision:
    """The one validator. Every input shape funnels through here."""
    # An ABSENT key is refused; an EMPTY list is accepted. The distinction is
    # load-bearing. A mapping with no 'actions' key at all is almost never a
    # decision -- it is a framework's own envelope: LangGraph graph state
    # carrying 'messages', a result wrapper, a different structured type from
    # a misconfigured run. Defaulting it to [] scored every one of those as a
    # considered HOLD: the run completed, the scorecard showed trades=0 and
    # an empty errors list, and nothing said otherwise. `{"actions": null}`
    # stays a no-op -- the key is present, so its author addressed the
    # question and declined it.
    if "actions" not in raw:
        keys = ", ".join(sorted(str(k) for k in raw)) or "none"
        raise DecisionError(
            f"no 'actions' key in the decision (keys present: {keys}). A "
            "decision that declines to trade says so with an EMPTY 'actions' "
            "list; a mapping without the key is usually the framework's own "
            "envelope -- graph state carrying 'messages', a result wrapper "
            "-- and treating it as HOLD would score a plumbing failure as a "
            "considered choice. Unwrap the envelope in the adapter's ask() "
            "or output parser before it reaches parse_decision.")
    unknown = sorted(str(k) for k in raw if k not in _DECISION_KEYS)
    if unknown:
        raise DecisionError(
            f"unknown keys in the decision: {', '.join(unknown)}. The "
            "contract is an 'actions' list and an optional 'rationale', "
            "nothing else. An unknown field is refused rather than dropped, "
            "because a dropped field leaves no record that part of what the "
            "model said never reached the market.")

    actions_raw = raw["actions"]
    if actions_raw is None:
        actions_raw = []
    if not isinstance(actions_raw, list):
        raise DecisionError(
            f"'actions' must be a list, got a {type(actions_raw).__name__}")

    actions: list[Action] = []
    for i, item in enumerate(actions_raw):
        if not isinstance(item, dict):
            raise DecisionError(
                f"action {i} is a {type(item).__name__}, not an object with "
                "'symbol' and 'side'")
        symbol = item.get("symbol")
        if not isinstance(symbol, str) or not symbol.strip():
            raise DecisionError(
                f"action {i} has no usable 'symbol': {item.get('symbol')!r}")
        side = item.get("side")
        if not isinstance(side, str) or side.upper() not in SIDES:
            raise DecisionError(
                f"action {i} names side {side!r}, which is not one of "
                f"{', '.join(SIDES)}")
        side = side.upper()

        # Refusals, never silent drops. Execution is a market sweep and
        # nothing else, so an order type, a limit price, a stop loss or any
        # field this contract does not define is an instruction the market
        # cannot follow. Ignoring one would execute a trade the agent
        # conditioned on a protection it never got, and the record would
        # show a position taken with no sign that half the instruction
        # evaporated. The two named fields get specific messages because
        # they can EXPLAIN the missing capability; everything else gets the
        # generic refusal after them.
        order_type = item.get("order_type")
        if order_type is not None and not (
                isinstance(order_type, str) and order_type.lower() == "market"):
            raise DecisionError(
                f"action {i} asks for order_type {order_type!r}. Tradefloor "
                "executes market sweeps only -- Portfolio.execute takes a "
                "signed share count and sweeps the live book, and there is "
                "no resting order at the agent boundary -- so only 'market' "
                "is accepted.")
        if item.get("limit_price") is not None:
            raise DecisionError(
                f"action {i} carries a limit_price, and this market has no "
                "limit orders at the agent boundary: Portfolio.execute "
                "sweeps the live book at whatever price it gives. Dropping "
                "the field silently would execute at market a trade the "
                "agent priced as protected, so it is refused instead.")
        unknown = sorted(str(k) for k in item if k not in _ACTION_KEYS)
        if unknown:
            raise DecisionError(
                f"action {i} carries unknown fields: {', '.join(unknown)}. "
                "Tradefloor executes market sweeps of signed share deltas -- "
                "there is no stop loss, no take profit and no time in force "
                "at the agent boundary -- so an unknown field cannot mean "
                "anything here, and dropping it silently would leave the "
                "agent believing it has protection this market cannot give.")

        quantity = item.get("quantity", 0)
        if quantity is None:
            quantity = 0
        if isinstance(quantity, bool) or not isinstance(quantity, (int, float)):
            raise DecisionError(
                f"action {i} has quantity {quantity!r}, which is not a number "
                "of shares")
        quantity = float(quantity)
        if quantity != quantity or quantity in (float("inf"), float("-inf")):
            raise DecisionError(
                f"action {i} has a non-finite quantity ({quantity})")
        if quantity < 0:
            raise DecisionError(
                f"action {i} has quantity {quantity}, which is negative. The "
                "side carries the direction, so a sell is SELL with a "
                "positive quantity; a negative one is ambiguous about which "
                "of the two the model meant.")
        if side == "HOLD" and quantity:
            raise DecisionError(
                f"action {i} is a HOLD carrying quantity {quantity}. HOLD "
                "means no trade; a quantity beside it does not say whether "
                "the model wanted to buy it or to keep it.")
        actions.append(Action(symbol.strip(), side, quantity))

    seen = [a.symbol for a in actions]
    duplicated = sorted({s for s in seen if seen.count(s) > 1})
    if duplicated:
        raise DecisionError(
            f"symbols named more than once: {', '.join(duplicated)}. Two "
            "instructions for one symbol have no defined order, so which one "
            "reaches the market would depend on dict iteration.")

    rationale = raw.get("rationale", "")
    if rationale is None:
        rationale = ""
    if not isinstance(rationale, str):
        raise DecisionError(
            f"'rationale' must be a string, got a {type(rationale).__name__}")
    return Decision(actions, rationale.strip())


def orders_from(decision: Decision, obs: Any, *,
                max_participation: float = MAX_PARTICIPATION,
                ) -> tuple[dict[str, float], list[str]]:
    """Validated share deltas, plus a note for anything that was adjusted.

    The second half of validation, and the only thing that ever reaches
    execution. An unknown symbol raises: the model is trading an instrument
    this market does not list, and executing the remaining actions would
    execute half a plan the agent never made.

    A size above the participation cap does not raise. It is clipped to the
    cap and the clip comes back as a note. An oversized request is something
    the agent did, so the trace says so. See ``MAX_PARTICIPATION``. Below one
    share is dust: it generates a trade every step and turns turnover, which
    the comparison reports, into noise.
    """
    listed = list(obs.tickers)
    orders: dict[str, float] = {}
    notes: list[str] = []

    for action in decision.actions:
        if action.symbol not in listed:
            raise MarketRefusalError(
                f"{action.symbol!r} is not listed in this market. The "
                f"universe is {', '.join(listed)}.")
        delta = action.signed()
        if not delta:
            continue
        # A cap of zero IS a cap. `if cap > 0` once read "no volume to
        # participate in" as "no cap at all": with avg_volume or
        # max_participation at zero, a 1e12-share order passed through
        # whole with no note, while the serialized observation showed the
        # agent max_order_shares of exactly 0.0 -- the observation and the
        # enforcement stating opposite things. Clipped to zero, the order
        # falls out as dust below and the note says what happened.
        cap = max(0.0, max_participation * obs.avg_volume(action.symbol))
        if abs(delta) > cap:
            notes.append(
                f"{action.symbol}: asked for {abs(delta):,.0f} shares, "
                f"clipped to {cap:,.0f} ({max_participation:.1%} of average "
                "daily volume)")
            delta = cap if delta > 0 else -cap
        if abs(delta) >= 1.0:
            orders[action.symbol] = delta
    return orders, notes


# -- observation -> framework -------------------------------------------------


def serialize_observation(obs: Any, *,
                          history: Sequence[Sequence[float]] = (),
                          fundamentals: dict[str, dict[str, Any]] | None = None,
                          max_participation: float = MAX_PARTICIPATION,
                          ) -> dict[str, Any]:
    """The observable state, as a JSON-able payload. An allowlist.

    Every key below is written out by hand. Nothing is copied off the engine
    by reflection, and ``obs.engine`` is read for exactly two things: the
    macro fields in ``OBSERVABLE_MACRO``, and the marks that price the
    portfolio. See the module docstring for why that matters.

    ``history`` is the adapter's own record of the prices it has already been
    shown -- :attr:`FrameworkAdapter.history` -- so a recent return and a
    realised volatility come from the agent's memory, without asking the
    simulator for either. The derivation lives here rather than on the
    adapter because it is a pure function of the rows, and one implementation
    means every framework quotes the same numbers from the same window.

    Company fundamentals -- sector, EPS, book value, revenue growth, beta --
    do not come off the engine either. The caller supplies them as
    ``fundamentals``, keyed by ticker; an analyst reads all five off a
    filing, and keeping them out of the adapter leaves one less line to
    audit. Worth stating plainly rather than implying otherwise:
    :func:`tradefloor.fair_value` is a public function, so a caller who
    supplies the full set of valuation inputs has also supplied the means
    to reconstruct the model's anchor EXACTLY -- and, through
    ``log(price / fair_value)``, to land near the mispricing on top of it.
    Near, never on: a traded price carries microstructure the anchor does
    not, and the ratio form of that inversion is the wrong arithmetic
    outright (the engine applies ``fair_value * exp(s)``). How near depends
    on the roster and the moment, which is why no distance is quoted here;
    the measured ceiling lives in a test constant where it can be
    re-derived. Whether to hand an agent that much is the caller's decision
    about their own experiment; what this function guarantees is only that
    it never makes the decision for them.

    How the payload is RENDERED is the adapter's decision -- a chat framework
    wants prose, a graph framework wants the dict itself -- but what it may
    contain is settled here, so nothing outside the allowlist can appear in
    any framework's input by accident.

    Both size limits are stated, because an observation that names only one
    is a trap. ``max_order_shares`` is the participation cap on what this
    MARKET can absorb per order; ``portfolio.max_leverage`` and
    ``portfolio.buying_power`` are the funding cap on what this BOOK can
    hold. The binding one is usually the funding cap, and it used to be the
    one the payload hid -- see the comment at the portfolio block for what
    that cost.
    """
    macro_state = obs.engine.macro_state
    macro = {field: getattr(macro_state, field) for field in OBSERVABLE_MACRO}

    rows = [list(row) for row in history]
    facts = fundamentals or {}
    assets = []
    for i, ticker in enumerate(obs.tickers):
        book = obs.book(ticker)
        adv = obs.avg_volume(ticker)
        assets.append({
            "symbol": ticker,
            "price": obs.price(ticker),
            "return_1d": _window_return(rows, i, obs.steps_per_day),
            "return_5d": _window_return(rows, i, obs.steps_per_day * 5),
            "volatility": _volatility(rows, i),
            "best_bid": book.best_bid,
            "best_ask": book.best_ask,
            "avg_daily_volume": adv,
            "max_order_shares": max_participation * adv,
            "position": obs.position(ticker),
            "fundamentals": dict(facts.get(ticker, {})),
        })

    portfolio = obs.portfolio
    equity = portfolio.net_worth(obs.engine)
    limit = portfolio.max_leverage
    # The funding headroom: additional gross notional before the leverage
    # cap refuses the trade. Stated because the payload used to name ONE
    # size limit, max_order_shares, and it was not the binding one: four
    # independent agents -- two frontier models, a documentation example
    # and this module's own first example -- sized to the stated cap, were
    # refused at the leverage limit the payload never mentioned, and scored
    # zero trades. An agent that trusts the observation must not read as a
    # worse trader than one that second-guesses it. None means what it
    # says: no funding limit was configured, not a limit of zero.
    if limit is None:
        headroom = None
    else:
        headroom = max(0.0,
                       limit * equity - portfolio.gross_exposure(obs.engine))
    return {
        "step": obs.step,
        "day": obs.day,
        "steps_per_day": obs.steps_per_day,
        "macro": macro,
        "assets": assets,
        "portfolio": {
            "cash": portfolio.cash,
            "net_worth": equity,
            "gross_exposure": portfolio.leverage(obs.engine),
            "max_leverage": limit,
            "buying_power": headroom,
        },
    }


def _window_return(rows: Sequence[Sequence[float]], i: int,
                   steps: int) -> float | None:
    """Return over the last ``steps`` observations, or None if unseen.

    None, for a window the agent has not lived through yet. Zero would claim
    the price did not move, and on day one that describes the record and not
    the market. From the number alone the agent cannot tell the two apart.

    The longest window is one step short of its label, KNOWINGLY. A
    five-day return needs ``steps + 1`` rows -- 31 at six steps a day --
    and ``HISTORY_STEPS`` keeps 30, so once the buffer is full "return_5d"
    spans 29 intervals: 4.83 days, permanently, labelled five. The honest
    fix is ``HISTORY_STEPS = steps_per_day * 5 + 1``, and it is
    deliberately not made: this value is rendered into every adapter's
    recorded input, so correcting the arithmetic moves every committed
    replay digest across all five fixture sets, replacing recorded
    evidence to relabel a diagnostic. The number is the same for every
    agent and both arms of any comparison; only its name overstates it by
    a sixth of a day. Fix the arithmetic in the pass that next re-records
    the fixtures for cause, and not before.

    The window is load-bearing beyond this file. The value it produces is
    rendered into FinRobot's prompt, and FinRobot's recorded replay keys
    are digests of that prompt -- so tuning the window moves every key in
    the committed transcript at ``tests/fixtures/finrobot/``, with no key
    change anywhere here to make that visible, and the shipped example and
    notebook stop replaying. Adding a FIELD to the payload is safe (the
    render names its fields one at a time); changing a rendered VALUE is
    what invalidates a recording. FinRobot keeps its own byte-identical
    copy deliberately, and, in ``tests/test_finrobot.py``,
    ``test_the_serializer_agrees_with_the_shared_one`` compares the values
    -- which is what fails first if either side is tuned.
    """
    if len(rows) < 2:
        return None
    window = rows[-(steps + 1):]
    if len(window) < 2:
        return None
    first, last = window[0][i], window[-1][i]
    return None if first <= 0 else (last / first) - 1.0


def _volatility(rows: Sequence[Sequence[float]], i: int) -> float | None:
    """Realised volatility of the step returns held in ``rows``.

    The formula is load-bearing the same way :func:`_window_return`'s
    window is: its value is rendered into FinRobot's prompt, whose digest
    is the recorded replay key, so changing the derivation invalidates the
    committed transcript with nothing here to say so. In
    ``tests/test_finrobot.py``,
    ``test_the_serializer_agrees_with_the_shared_one`` is what fails first.
    """
    if len(rows) < 3:
        return None
    steps = [rows[k][i] / rows[k - 1][i] - 1.0
             for k in range(1, len(rows)) if rows[k - 1][i] > 0]
    return statistics.pstdev(steps) if len(steps) >= 2 else None


# -- recording and replay -----------------------------------------------------


def jsonable(value: Any) -> Any:
    """A JSON-able rendering of ``value``, for digesting and recording.

    A framework config is somebody else's object graph -- HTTP clients,
    callbacks, filter callables -- and digesting one directly raised
    ``TypeError`` out of ``json.dumps`` on configurations released adapters
    accepted; ``TypeError`` is not a :class:`~tradefloor.ValidationError`,
    so a caller catching the library's refusals did not catch it either.
    The unrepresentable parts become their type name: a digest has to be
    stable and one-way, never round-trip, and two configs differing only in
    which client object they hold are the same configuration for what a
    digest records.

    Promoted from two byte-identical private copies in the LangGraph and
    FinRobot adapters, by the argument that produced :class:`ReplayMixin`:
    a helper that exists twice is two chances to drift, and this one feeds
    ``config_digest``, which the fork agreement compares.
    """
    if isinstance(value, Decision):
        return value.as_dict()
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return f"<{type(value).__name__}>"


def digest(data: Any) -> str:
    """The replay key: SHA-256 of the exact input the framework was sent.

    A string or bytes is hashed as-is, which is the FinRobot behaviour and
    the right one for a rendered prompt. Anything else is hashed as canonical
    JSON -- sorted keys, no whitespace -- so two dicts that mean the same
    thing produce the same key whatever order they were built in.

    Sixteen hex characters: ample for the few dozen decision points of one
    experiment, and short enough to compare by eye in an error message.

    A rule for every caller, learned three separate times in one review: if
    you compute a fingerprint, something must FAIL when it differs, and a
    test must prove that something fails. A digest that is recorded and
    never compared is provenance theatre -- it decorates the artifact
    while two different setups replay as one.
    """
    if isinstance(data, bytes):
        blob = data
    elif isinstance(data, str):
        blob = data.encode("utf-8")
    else:
        blob = json.dumps(data, sort_keys=True,
                          separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


class Transcript:
    """Recorded framework interactions, keyed by the input that produced them.

    A file of these lets an experiment reproduce a real agent run with no API
    key, no network and no framework install. It holds what re-executing and
    auditing the experiment need -- the input, the raw response, and the
    step it happened at -- and nothing else. No API keys, no account
    identifiers, no request IDs, no provider headers. The conventional entry
    fields are ``arm``, ``step``, ``day``, ``digest``, ``prompt`` and
    ``response``; a committed fixture should carry no others.

    ``meta`` records what the run cannot reconstruct: the framework and its
    version, the provider and model, the generation parameters, and the
    digests of the instructions the agent ran under
    (:meth:`AdapterInfo.as_dict` is the intended shape). Replaying a
    transcript under different instructions produces a different experiment,
    and ``meta`` is how a reader notices.
    """

    __slots__ = ("meta", "entries", "_by_digest")

    def __init__(self, meta: dict[str, Any] | None = None,
                 entries: Sequence[dict[str, Any]] = ()) -> None:
        self.meta = dict(meta or {})
        self.entries = [dict(e) for e in entries]
        self._by_digest = {e["digest"]: e for e in self.entries}

    def record(self, entry: dict[str, Any]) -> None:
        self.entries.append(dict(entry))
        # Last write wins. An identical input must have one answer
        # available, and re-recording one re-runs the same question.
        self._by_digest[entry["digest"]] = self.entries[-1]

    def response_for(self, key: str) -> str | None:
        entry = self._by_digest.get(key)
        return None if entry is None else entry["response"]

    def entry_for(self, key: str) -> dict[str, Any] | None:
        """The whole recorded entry, or None only when none exists.

        Distinct from :meth:`response_for`, which returns None BOTH for a
        missing entry and for an entry whose recorded response is null --
        two situations with opposite remedies, which
        :func:`replay_response` has to tell apart.
        """
        return self._by_digest.get(key)

    def __len__(self) -> int:
        return len(self.entries)

    def as_dict(self) -> dict[str, Any]:
        return {"meta": self.meta, "entries": self.entries}

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), indent=2) + "\n"

    @classmethod
    def from_json(cls, text: str) -> "Transcript":
        raw = json.loads(text)
        return cls(meta=raw.get("meta", {}), entries=raw.get("entries", []))

    @classmethod
    def load(cls, path: Any) -> "Transcript":
        import pathlib
        return cls.from_json(pathlib.Path(path).read_text(encoding="utf-8"))

    def save(self, path: Any) -> None:
        """Write the recording. The bytes do not depend on the platform.

        `write_bytes` rather than `write_text`, so a recording made on
        Windows and one made on Linux from the same transcript are the
        same file. Recordings get committed, diffed and hashed, and text
        mode would answer all three differently per machine.
        """
        import pathlib
        target = pathlib.Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(self.to_json().encode("utf-8"))


def replay_response(transcript: Transcript, key: str, *, step: int,
                    day: int) -> Any:
    """The recorded response for ``key``, or a refusal naming the step.

    The one lookup every replaying adapter performs, centralised so the
    error message -- the part a user actually meets -- is written once and
    says the same thing everywhere: which step missed, why a miss means the
    inputs changed, and that replaying anyway would answer this question
    with a response given to a different one.

    Both failures raise :class:`ReplayMiss`, which is a
    :class:`DecisionError` a skipping caller must NOT skip: a recording
    that cannot answer is a broken experiment rather than a badly behaved
    agent.

    A missing ENTRY and a recorded NULL are told apart, because their
    remedies are opposite: a missing key means the inputs changed and the
    run needs re-recording; a null response means the recording is right
    there and holds no answer -- the live call likely failed mid-run and
    the failure was written down -- and sending the user off to re-record
    the whole run would spend money to rediscover a file they already
    have.
    """
    entry = transcript.entry_for(key)
    if entry is None:
        raise ReplayMiss(
            f"no recorded response for step {step} (day {day}, digest "
            f"{key}). The transcript holds {len(transcript)} interactions, "
            "none for this input. A replay is keyed by the exact input the "
            "framework was sent, so this means the observation mapping, the "
            "instructions or the market configuration has changed since the "
            "recording -- replaying anyway would answer this question with a "
            "response given to a different one. Re-record the run live.")
    response = entry.get("response")
    if response is None:
        raise ReplayMiss(
            f"the recorded entry for step {step} (day {day}, digest {key}) "
            "holds a null response. The recording exists -- the inputs have "
            "not changed -- but this interaction captured no answer, which "
            "usually means the live call failed mid-run and the failure was "
            "recorded. Re-record this interaction; the rest of the "
            "transcript is fine.")
    return response


# -- adapter metadata ---------------------------------------------------------


#: Words that, as the HEAD of a key name, mean the entry is a credential.
#: Head-segment matching, not substring matching: "token" in "max_tokens"
#: is True, and max_tokens is the second most common generation parameter
#: in existence -- a scan that refuses it refuses the ``generation``
#: field's primary use case, and a credential check that fires on that
#: gets worked around, which costs more than the hole it closes. English
#: compounds are head-final: ``api_key`` IS a key, ``token_budget`` is a
#: budget ABOUT tokens, ``cookie_policy`` is a policy. So the FINAL
#: segment is compared, exactly and in the singular ("tokens" counts
#: tokens; "credentials" holds them, so the plural is listed where it is
#: itself the secret). The joined spellings are here because segmentation
#: cannot split them, and a suffix match would refuse "monkey".
#: "session_key" still costs somebody a rename -- that trade stands, for a
#: name whose head they chose.
_SECRET_WORDS = frozenset((
    "key", "token", "secret", "password", "passwd", "credential",
    "credentials", "bearer", "cookie", "auth", "authorization",
    "apikey", "authtoken", "accesstoken",
))

#: Splits a key into segments at underscores, hyphens, other punctuation
#: and camelCase boundaries, so "X-Api-Key", "apiKey" and "api_key" all
#: expose the same head.
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_SEGMENTS = re.compile(r"[^A-Za-z0-9]+")


def check_prior(prior: "Transcript | None", *, mode: str,
                recorder: "Transcript | None",
                instructions_digest: str) -> "Transcript | None":
    """Validate a ``prior`` recording and hand it back, or refuse it.

    Every adapter that records calls this from its constructor, so a bad
    resume is a construction error rather than something discovered forty
    paid calls into a run.

    ``prior`` is a recording an earlier live run produced. Any live run can
    die -- a rate limit, a dropped connection, a keyboard interrupt -- and
    without a resume the second attempt re-asks every question it already
    holds an answer to. On a digest hit the recorded answer is reused; on a
    miss the provider is called. Same keying as the replay path, different
    source.

    Three ways it is refused: outside live mode, where no provider is
    called and the argument means nothing; with no recorder, where the
    resumed run keeps nothing; and under changed instructions, which
    :func:`refuse_changed_instructions` explains.
    """
    if prior is None:
        return None
    if mode != "live":
        raise ValidationError(
            "prior is for resuming a live run: it is consulted before the "
            "provider is called, and replay mode calls no provider. To "
            "replay a recording, pass it as transcript=.")
    if recorder is None:
        raise ValidationError(
            "prior needs a recorder to resume into. Without one the resumed "
            "run keeps nothing, which is the loss prior exists to stop.")
    refuse_changed_instructions(prior, instructions_digest)
    return prior


def stamp_resume_counts(recorder: "Transcript | None",
                        prior: "Transcript | None") -> None:
    """Record how much of ``recorder`` was resumed rather than called.

    A recording stitched from two sessions has to say so on its face.
    Without this it claims to be one live run, and a reader counting
    entries cannot tell how many of them were paid for today.

    DERIVED from the two transcripts rather than counted as it goes. A fork
    shares one recorder between arms and gives each arm its own adapter, so
    a counter living on an adapter would split across the arms and each
    half would understate the file it describes.
    """
    if recorder is None or prior is None:
        return
    resumed = sum(1 for entry in recorder.entries
                  if prior.entry_for(entry.get("digest", "")))
    recorder.meta["replayed_from_prior"] = resumed
    recorder.meta["called_live"] = len(recorder.entries) - resumed


def refuse_changed_instructions(prior: "Transcript | None",
                                current: str) -> None:
    """Refuse a resume whose instructions are not the recorded ones.

    The transcript key is a digest of the INPUT, and an adapter's
    instructions do not travel in that input -- they reach the framework as
    a system prompt, an agent profile or a constructor argument. So editing
    them leaves every recorded key intact: the resume completes, every
    digest matches, and the answers it reuses were given under instructions
    nobody is running any more. Nothing in the output says the question
    changed.

    ``current`` is :attr:`AdapterInfo.instructions_digest`, which is
    already a digest and therefore cannot carry a credential.

    A prior carrying no ``instructions_digest`` in its meta is allowed
    through. It cannot be checked, and refusing it would break every
    recording made before this existed for no gain -- those runs are no
    worse off than they were. An adapter that leaves
    ``instructions_digest`` empty is in the same position, and the fix
    there is to stamp one.
    """
    if prior is None:
        return
    recorded = (prior.meta or {}).get("instructions_digest")
    if not recorded or not current or recorded == current:
        return
    raise ValidationError(
        f"this prior transcript was recorded under different instructions "
        f"(recorded {recorded}, current {current}). The instructions do not "
        "travel in the input the resume is keyed on, so every recorded key "
        "would still match and the run would complete -- answering the "
        "instructions you have now with decisions taken under the ones you "
        "had then. Restore the instructions, or start a fresh recording "
        "without prior=.")


def _head_of(key: Any) -> str:
    """The final segment of a key name, lowercased. Empty if there is none."""
    spaced = _CAMEL_BOUNDARY.sub("_", str(key))
    segments = [s for s in _SEGMENTS.split(spaced) if s]
    return segments[-1].lower() if segments else ""

#: Values that ARE a secret, anchored rather than substring-matched. The
#: word boundary is load-bearing: a bare "sk-" scan matched "risk-adjusted"
#: and "task-based", and a credential check that cries wolf on prose gets
#: weakened or deleted, which costs more than the hole it closes. Anchored,
#: it still catches a key EMBEDDED in a sentence, which a startswith check
#: did not.
_SECRET_VALUE = re.compile(r"\bsk-[A-Za-z0-9_-]{4,}|Bearer\s+[A-Za-z0-9]")


def _credential_free(mapping: dict[str, Any], where: str) -> dict[str, Any]:
    """A DEEP copy of ``mapping``, refused if anything in it smells like a
    secret.

    The open mappings on :class:`AdapterInfo` moved the no-credentials
    boundary from shape to validation, so the validation has to be real:
    key names are checked recursively, string values against anchored
    secret shapes, and the whole thing must be JSON-serialisable, because
    it is written into transcripts and printed by the fork agreement.

    Deep, not shallow, and that is the boundary itself: the first version
    took ``dict(mapping)``, which shares every NESTED structure with the
    caller's object -- so building a config, handing it over and mutating
    it afterwards (the completely normal shape) put unscanned values into
    what :meth:`FrameworkAdapter.provenance` writes into committed
    transcript meta. A scan-once-share-forever guard is a guard against
    the past only. The copy is a JSON round-trip rather than
    ``copy.deepcopy``, so what is stored is byte-for-byte the form a
    transcript will hold.
    """
    try:
        blob = json.dumps(dict(mapping))
    except (TypeError, ValueError) as exc:
        raise ValidationError(
            f"AdapterInfo {where} must be JSON-serialisable -- it is "
            f"written into transcripts and manifests -- and is not: "
            f"{exc}") from exc
    out = json.loads(blob)

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                head = _head_of(key)
                if head in _SECRET_WORDS:
                    raise ValidationError(
                        f"AdapterInfo {where} carries {key!r}, which names "
                        f"a credential (its final segment is {head!r}). "
                        "This metadata is printed and written into "
                        "artifacts; record digest(config) instead of the "
                        "config.")
                walk(value)
        elif isinstance(node, (list, tuple)):
            for value in node:
                walk(value)
        elif isinstance(node, str):
            if _SECRET_VALUE.search(node):
                raise ValidationError(
                    f"AdapterInfo {where} carries a value that looks like a "
                    "secret. This metadata is printed and written into "
                    "artifacts; record a digest instead.")

    walk(out)
    return out


class AdapterInfo:
    """What ran: the framework, the model behind it, and the exact setup.

    Named fields for what every adapter has, because a replayed run has to
    be READABLE: the framework and its version, the provider and model, the
    ``entry_point`` driven inside the framework (``SingleAssistant`` and
    ``MultiAssistantWithLeader`` are different experiments), the ``mode``
    (a recording of a replay is a different document from a recording of a
    live run), the upstream ``framework_url`` for attribution, the
    instructions version and digest, and ``generation`` -- the parameters
    like temperature that most make an LLM run irreproducible, kept as a
    mapping because every framework has them and none share the spelling.

    ``extra`` is the open, JSON-able mapping for provenance that is
    genuinely per-framework. The first version had fixed slots only, on the
    argument that an open dict would eventually be handed a whole config
    carrying an API key -- and then the shipped FinRobot fixture's meta
    needed twelve keys and four of them had a home. The boundary moved from
    shape to VALIDATION: ``generation`` and ``extra`` are refused at
    construction if any key or value in them looks like a credential, so
    the guarantee survives the flexibility. ``tests/test_integrations.py``
    holds the tests.

    It rides existing structures rather than replacing them:
    :meth:`as_dict` (or :meth:`FrameworkAdapter.provenance`, which adds the
    Tradefloor-side settings) belongs in ``Transcript.meta``, and
    :meth:`reference` is a citation string for
    ``RunManifest.of(strategy=...)``, which takes a reference string for
    any agent that is not a :class:`~tradefloor.StrategySpec` -- an LLM
    adapter is not one, and forcing it into one would fingerprint a
    strategy that never existed.
    """

    __slots__ = ("framework", "framework_version", "provider", "model",
                 "agent_name", "entry_point", "mode", "framework_url",
                 "instructions_version", "instructions_digest",
                 "config_digest", "generation", "extra",
                 "decision_schema_version")

    def __init__(self, *, framework: str, framework_version: str = "",
                 provider: str = "", model: str = "", agent_name: str = "",
                 entry_point: str = "", mode: str = "",
                 framework_url: str = "", instructions_version: str = "",
                 instructions_digest: str = "", config_digest: str = "",
                 generation: dict[str, Any] | None = None,
                 extra: dict[str, Any] | None = None) -> None:
        self.framework = framework
        self.framework_version = framework_version
        self.provider = provider
        self.model = model
        self.agent_name = agent_name
        self.entry_point = entry_point
        self.mode = mode
        self.framework_url = framework_url
        self.instructions_version = instructions_version
        self.instructions_digest = instructions_digest
        self.config_digest = config_digest
        self.generation = _credential_free(generation or {}, "generation")
        self.extra = _credential_free(extra or {}, "extra")
        # Stamped, not passed: the schema an adapter validates against is
        # this module's, so letting a caller claim another version would
        # record a contract that never ran.
        self.decision_schema_version = DECISION_SCHEMA_VERSION

    def as_dict(self) -> dict[str, Any]:
        # Deep copies of the mappings, for the same reason the validator
        # deep-copies on the way in: a shallow copy hands the caller a
        # handle on the STORED nested structures, and a mutation through it
        # would bypass the credential scan that ran at construction.
        out = {}
        for slot in self.__slots__:
            value = getattr(self, slot)
            out[slot] = copy.deepcopy(value) if isinstance(value, dict) \
                else value
        return out

    def reference(self) -> str:
        """A one-line citation, for ``RunManifest.of(strategy=...)``."""
        parts = [" ".join(p for p in (self.framework, self.framework_version)
                          if p)]
        if self.model:
            parts.append(" ".join(p for p in (self.provider, self.model)
                                  if p))
        if self.entry_point:
            parts.append(f"via {self.entry_point}")
        if self.agent_name:
            parts.append(f"agent {self.agent_name}")
        if self.config_digest:
            parts.append(f"config {self.config_digest}")
        parts.append(f"decision schema {self.decision_schema_version}")
        return ", ".join(parts)

    def __eq__(self, other: Any) -> bool:
        return (isinstance(other, AdapterInfo)
                and self.as_dict() == other.as_dict())

    def __repr__(self) -> str:
        return f"AdapterInfo({self.reference()!r})"


# -- the adapter base ---------------------------------------------------------


class FrameworkAdapter:
    """The shape every framework adapter takes, so all of them fit both
    harnesses.

    Implements the four methods :class:`~tradefloor.counterfactual.World`
    looks for -- :meth:`act`, and the optional :meth:`decision`,
    :meth:`state` and :meth:`fork` -- and :meth:`act` alone is what
    :func:`tradefloor.evaluate` needs. A subclass implements :meth:`ask`,
    the ONE method that reaches its framework, and everything on either side
    of that call -- the price memory, the cadence, the serialisation, the
    two-stage validation, the record -- runs shared code that
    ``tests/test_integrations.py`` checks once for everybody.

    ``every`` is the decision cadence in steps. At the library's six steps a
    day, the default of six gives one decision per simulated day; that
    matches how often a portfolio manager decides, and it keeps a metered
    framework's bill proportional to the experiment instead of to the tick
    rate. The two arms of a comparison MUST run the same cadence, and
    :meth:`fork` copies it.

    Constructors are keyword-only all the way down, because :meth:`fork`
    rebuilds the twin as ``type(self)(**self.fork_kwargs())`` -- a subclass
    with a required positional argument could not be forked, and the failure
    would surface two arms into an experiment instead of here.
    """

    def __init__(self, *, info: AdapterInfo | None = None, every: int = 6,
                 fundamentals: dict[str, dict[str, Any]] | None = None,
                 max_participation: float = MAX_PARTICIPATION,
                 arm: str = "") -> None:
        if every < 1:
            raise ValidationError(f"every must be >= 1 step, got {every}")
        self.info = info or AdapterInfo(framework=type(self).__name__)
        self.every = int(every)
        self.fundamentals = dict(fundamentals or {})
        self.max_participation = float(max_participation)
        self.arm = arm
        #: A recording an earlier live run produced, consulted before the
        #: provider. Set by an adapter that records; see :func:`check_prior`
        #: and :meth:`call_or_resume`. None on an adapter with no live path.
        self.prior: "Transcript | None" = None

        #: Prices this adapter has been shown, oldest first. The agent's own
        #: memory, and the only reason it can quote a return or a volatility
        #: without asking the simulator for one. It lives on the adapter
        #: rather than in the serializer because it is state a fork has to
        #: copy and :func:`tradefloor.agree` has to compare.
        self.history: list[list[float]] = []
        #: Every decision point, for examples and notebooks: the exchange
        #: (digest, exact input, raw response) joined to the decision it
        #: produced and the orders that reached the market. Not state:
        #: :meth:`state` publishes the parts a fork has to agree on.
        self.record: list[dict[str, Any]] = []
        self._decision: dict[str, Any] | None = None
        #: Staged by :meth:`record_exchange` during one ask(), consumed by
        #: :meth:`act` into the record entry, cleared before the next.
        self._exchange: dict[str, Any] | None = None

    # -- the one framework-specific method --------------------------------

    def ask(self, obs: Any, payload: dict[str, Any]) -> Any:
        """One framework decision for this payload. Subclasses implement this.

        ``payload`` is the :func:`serialize_observation` output; render it
        however the framework reads best. ``obs`` is passed for ``step`` and
        ``day`` in error messages and transcript entries -- do NOT read
        market state off it here, because anything the framework should see
        is already in the payload and the allowlist test cannot see what
        this method reads.

        Call :meth:`record_exchange` with the exact input you sent the
        framework -- and the transcript key, if you recorded one -- so the
        record joins the exchange to the decision it produced.

        Return the decision in any shape :func:`parse_decision` accepts: a
        :class:`Decision`, a dict, or a JSON string. Raise
        :class:`MissingDependencyError` (via :func:`require`) if the
        framework is not installed; any other exception is wrapped in
        :class:`FrameworkError` by :meth:`act` with the chain preserved --
        INCLUDING exceptions a framework raises as control flow rather than
        as failure, a human-in-the-loop interrupt among them. ``act`` cannot
        tell a signal from a crash, so if your framework communicates
        through exceptions, catch them in here and handle them
        deliberately; only what escapes this method is wrapped.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not implement ask(). An adapter "
            "subclasses FrameworkAdapter and implements ask(obs, payload), "
            "the one method that reaches its framework.")

    def call_or_resume(self, key: str, live: Any) -> Any:
        """The answer recorded for ``key`` in :attr:`prior`, or ``live()``.

        Wrap the live call in every adapter's ``ask``. A resumed answer
        costs nothing and raises nothing, so an adapter that catches
        framework exceptions around the live call keeps catching exactly
        what it did.

        The market is deterministic, so a resumed run reaches the same
        prompts and computes the same digests, and a recorded answer is
        still an answer to the question being asked. That is the same
        property the replay path rests on; this changes which source is
        consulted first, and a miss falls through to the provider.
        """
        if self.prior is not None:
            entry = self.prior.entry_for(key)
            if entry is not None:
                return entry.get("response")
        return live()

    def record_exchange(self, prompt: Any, *, key: str | None = None) -> None:
        """Declare the exact framework input behind the decision under way.

        Call it inside :meth:`ask`. ``prompt`` is whatever the framework was
        actually given -- rendered text, a message list, the payload itself
        -- and ``key`` is the transcript digest if the adapter computed one;
        left out, it is :func:`digest` of the prompt, which is the same
        value a recorder keyed by the same input would use.

        This exists because the record entry has to carry the whole chain:
        observation to input to response to validated action to order. The
        first version recorded only the validated half, the transcript held
        the other half keyed by digest, and nothing joined them -- an
        artifact wanting to show what the model SAID beside what it TRADED
        had to hand-join two files. The gap shipped into three adapters
        before anything noticed, so now a contract check asserts the join.
        """
        self._exchange = {"digest": key or digest(prompt), "prompt": prompt}

    # -- the agent protocol -----------------------------------------------

    def act(self, obs: Any) -> dict[str, float]:
        """Share deltas for this step. Empty on the steps between decisions.

        The market advances every step; the framework is asked every
        ``every`` steps. On the steps in between, this records the prices it
        saw and returns nothing. A human manager watches the book
        continuously and revisits it on a schedule.

        The Observation is read, never written: the same object is what the
        harness executes against after this returns.
        """
        self.history.append(list(obs.prices))
        if len(self.history) > HISTORY_STEPS:
            self.history.pop(0)

        if obs.step % self.every:
            return {}

        payload = serialize_observation(
            obs, history=self.history, fundamentals=self.fundamentals,
            max_participation=self.max_participation)
        self._exchange = None
        try:
            raw = self.ask(obs, payload)
        except IntegrationError:
            # Already one of ours, already actionable. Wrapping it again
            # would bury the pip command or the parse detail one level down.
            raise
        except Exception as exc:
            raise FrameworkError(
                f"{self.info.framework or type(self).__name__} raised "
                f"{type(exc).__name__} instead of returning a decision: "
                f"{exc}") from exc

        decision = parse_decision(raw)
        orders, notes = orders_from(
            decision, obs, max_participation=self.max_participation)

        self._decision = {"step": obs.step, **decision.as_dict()}
        entry: dict[str, Any] = {"arm": self.arm, "step": obs.step,
                                 "day": obs.day}
        # The exchange, when ask() declared one, in the same keys the
        # FinRobot record and the Transcript use, so the three join without
        # translation. The response is always in hand -- it is what ask()
        # returned -- normalised to the JSON-able form the record needs.
        if self._exchange is not None:
            entry["digest"] = self._exchange["digest"]
            entry["prompt"] = self._exchange["prompt"]
        entry["response"] = raw.as_dict() if isinstance(raw, Decision) else raw
        entry["decision"] = decision.as_dict()
        entry["orders"] = dict(orders)
        entry["clipped"] = notes
        self.record.append(entry)
        return orders

    def decision(self) -> dict[str, Any] | None:
        """The last validated decision, as ``World`` records it every step.

        The actions and the rationale. The raw framework response and the
        arm stay out: :func:`~tradefloor.counterfactual.compare` finds the
        first step at which two arms' decisions differ by comparing these
        dictionaries, so a field varying for any other reason would report a
        divergence that never happened.
        """
        return self._decision

    def state(self) -> dict[str, Any]:
        """What a fork has to agree on, for :func:`tradefloor.agree`.

        The price memory and the last decision: everything surviving from
        one step to the next that could make two arms behave differently for
        some reason other than the intervention. Framework configs stay out
        of this dictionary and out of any subclass's additions to it -- a
        config carries an API key, and this dictionary gets printed.

        ``instructions_digest`` is in, and it is here for the arms a fork
        does not build. A forked pair shares one ``info`` and cannot
        disagree on it; two arms built BY HAND -- the obvious way to compare
        two configurations of the same framework -- could run different
        instructions while ``agree()`` reported identical on every check,
        confirming a controlled comparison that was not one. A schema
        version cannot notice that; the digest of the instructions can, and
        being a digest it cannot carry a credential.
        """
        return {
            "history": [list(row) for row in self.history],
            "decision": copy.deepcopy(self._decision),
            "every": self.every,
            "instructions_digest": self.info.instructions_digest,
            "decision_schema_version": DECISION_SCHEMA_VERSION,
        }

    def provenance(self) -> dict[str, Any]:
        """What ``Transcript.meta`` should carry about this adapter.

        :meth:`AdapterInfo.as_dict` plus the Tradefloor-side settings the
        transcript alone cannot reconstruct: the decision cadence, without
        which an agent asked once a day and one asked every step read as
        the same agent, and the participation cap, which decides what
        "clipped" means in the record. They live here rather than on
        :class:`AdapterInfo` because they are this adapter's settings, not
        the framework's identity -- but a recording needs both halves, so
        this is the one dictionary a recorder should write.
        """
        out = self.info.as_dict()
        out["decision_every_steps"] = self.every
        out["max_participation"] = self.max_participation
        return out

    def fork_kwargs(self) -> dict[str, Any]:
        """The constructor arguments a fork is rebuilt with.

        A subclass that adds constructor arguments extends this rather than
        overriding :meth:`fork`, so the copy semantics -- what is shared,
        what is deep-copied -- stay in one place.
        """
        return {"info": self.info, "every": self.every,
                "fundamentals": self.fundamentals,
                "max_participation": self.max_participation,
                "arm": self.arm}

    def fork(self) -> "FrameworkAdapter":
        """An independent copy, for :meth:`World.fork`.

        Written out instead of left to ``copy.deepcopy``: a live adapter
        holds a framework agent holding an HTTP client, and copying one is
        wasteful at best and a shared socket at worst.

        ``type(self)``, so a subclass forks into its own type. Hard-coding
        the class name broke this in the FinRobot adapter's history: a
        subclass overriding how a decision is obtained kept the override
        through the shared history and lost it in both arms. The run then
        completes, and the comparison it prints is between two agents
        neither of which was the one under test.
        """
        twin = type(self)(**self.fork_kwargs())
        twin.history = [list(row) for row in self.history]
        twin.record = copy.deepcopy(self.record)
        twin._decision = copy.deepcopy(self._decision)
        return twin

    def __repr__(self) -> str:
        return (f"{type(self).__name__}(framework={self.info.framework!r}, "
                f"arm={self.arm!r}, every={self.every}, "
                f"decisions={len(self.record)})")


class ReplayMixin:
    """The record-and-replay control flow, shared so it cannot drift.

    :func:`digest`, :class:`Transcript` and :func:`replay_response` were
    shared from the start, and the branch that USES them was left to each
    adapter. That was backwards. The leaves are the parts nobody gets
    wrong; the control flow is where the drift-dangerous decisions live --
    which input the key is computed over, which branch runs, what gets
    recorded. An adapter written in six months that keys its transcript on
    (arm, step) instead of on the input passes every test it has, and its
    studies then answer new questions with recorded answers to old ones;
    sharing the lookup cannot prevent that, because the lookup sits
    downstream of the choice that goes wrong. Measured across the first
    four adapters, the ask() bodies were 18 to 33 percent line-similar and
    their control flow was identical -- the signature of a skeleton that
    wants one home.

    Use it beside the base, mixin first:

    ```python
    class MyAdapter(ReplayMixin, FrameworkAdapter):
        def prepare(self, obs, payload): ...
        def call(self, obs, prompt): ...
    ```

    The mixin owns ``mode``, ``transcript`` and ``recorder``, implements
    :meth:`FrameworkAdapter.ask`, stages the exchange into the record, and
    shares the transcript AND the recorder across :meth:`fork` -- a replay
    of one arm must read the same recorded run as its sibling, and a live
    recording of both arms belongs in one file. Do not override ``ask`` on
    an adapter that takes the mixin; the skeleton is the point of it.

    Two hooks, BOTH required, so neither is silently forgotten:

    - :meth:`prepare` turns the payload into ``(key_material, prompt)``:
      what the replay key is computed over, and what the framework is
      actually sent. Usually they are the same object -- a rendered prompt
      hashes as itself -- but they are two return values because they are
      two decisions: an adapter may key on ``{payload, instructions}``
      while sending a rendered prompt, and a hook that assumed "hash what
      you send" would force it to key on something it did not choose.
      Rendering happens here, ONCE; nothing above or below renders again.
    - :meth:`call` performs one live framework interaction and returns the
      raw response. Replay mode never reaches it, which is what makes a
      replayed run need no framework, no network and no API key.
    """

    def __init__(self, *, mode: str = "replay",
                 transcript: Transcript | None = None,
                 recorder: Transcript | None = None,
                 prior: Transcript | None = None, **kwargs: Any) -> None:
        if mode not in ("replay", "live"):
            raise ValidationError(
                f"mode must be 'replay' or 'live', got {mode!r}")
        if mode == "replay" and transcript is None:
            raise ValidationError(
                "replay mode needs a transcript to replay. Load one with "
                "Transcript.load(path), or pass mode='live' to call the "
                "framework.")
        super().__init__(**kwargs)
        self.mode = mode
        self.transcript = transcript
        self.recorder = recorder
        self.prior = check_prior(
            prior, mode=mode, recorder=recorder,
            instructions_digest=self.info.instructions_digest)

    # -- the two framework-specific hooks ---------------------------------

    def prepare(self, obs: Any, payload: dict[str, Any]) -> tuple[Any, Any]:
        """``(key_material, prompt)`` for one decision. Subclasses implement.

        ``key_material`` is what the replay key is computed over;
        ``prompt`` is what the framework is sent. Return the same object
        twice when they coincide, which is the common case. See the class
        docstring for why they are allowed to differ.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not implement prepare(). Return "
            "(key_material, prompt): what the replay key is computed over, "
            "and what the framework is actually sent -- usually the same "
            "object twice. Render here, once.")

    def call(self, obs: Any, prompt: Any) -> Any:
        """One live framework interaction. Subclasses implement.

        Replay mode never reaches this method. Import the framework in
        here through :func:`require`, so a replayed run does not need it.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not implement call(). Perform one "
            "live framework interaction with the prepared prompt and "
            "return the raw response; replay mode never reaches this "
            "method.")

    # -- the shared skeleton ----------------------------------------------

    def ask(self, obs: Any, payload: dict[str, Any]) -> Any:
        """One decision, replayed or live, recorded either way.

        The key is a digest of the INPUT, never of a position: change the
        observation mapping or the instructions and the key goes missing
        and the replay REFUSES, naming the step, instead of answering the
        new question with a response given to the old one.
        """
        key_material, prompt = self.prepare(obs, payload)
        key = digest(key_material)
        self.record_exchange(prompt, key=key)
        if self.mode == "replay":
            return replay_response(self.transcript, key,
                                   step=obs.step, day=obs.day)
        # A `prior` recording is consulted first. The market is
        # deterministic, so a resumed run reaches the same prompts and the
        # same digests, and a recorded answer is still an answer to the
        # question being asked. Same keying as the replay path; different
        # source, and a miss falls through to the provider.
        resumed = None if self.prior is None else self.prior.entry_for(key)
        response = (self.call(obs, prompt) if resumed is None
                    else resumed.get("response"))
        if self.recorder is not None:
            # The response is recorded AS RETURNED, never re-serialised.
            # Replay hands back exactly what was recorded, so live and
            # replay must return the same shape -- a JSON string
            # json.dumps'd here would replay one parse level short, against
            # a recording that looked fine when it was written.
            self.recorder.record({
                "arm": self.arm, "step": obs.step, "day": obs.day,
                "digest": key, "prompt": prompt, "response": response,
            })
            stamp_resume_counts(self.recorder, self.prior)
        return response

    def fork_kwargs(self) -> dict[str, Any]:
        kwargs = super().fork_kwargs()
        # SHARED, not copied, in both directions on purpose: a replay of
        # one arm must read the same recorded run as its sibling, and a
        # live recording of both arms belongs in one file. `prior` shares
        # for the same reason -- a resume that only one arm consulted
        # would pay for one arm and not the other.
        kwargs.update(mode=self.mode, transcript=self.transcript,
                      recorder=self.recorder, prior=self.prior)
        return kwargs
