"""Run a PydanticAI agent inside a Tradefloor market, and record what it did.

PydanticAI is an open-source agent framework from the Pydantic team
(https://github.com/pydantic/pydantic-ai, MIT). This module is a Tradefloor
integration for it. There is no affiliation with or endorsement by Pydantic,
and nothing here is an official PydanticAI interface.

    Tradefloor observation
            |
     PydanticAIAdapter       serialize_observation -> JSON prompt
            |
        PydanticAI           the user's own Agent, over their own provider
            |
     PydanticAIAdapter       result.output -> parse_decision -> share deltas
            |
    Tradefloor execution

Everything except the middle box is `common.py`, shared with every other
adapter. What is specific to PydanticAI is how the observation is handed
over, how the answer shape is bound, and which of the framework's exceptions
means "the agent answered badly" rather than "the call never completed".

## You bring the agent

This adapter takes an agent you already built. It does not build one, and it
does not ask you to rebuild one:

```python
from pydantic_ai import Agent
from tradefloor.integrations.pydantic_ai import PydanticAIAdapter

pm = Agent("openai:gpt-5.2", deps_type=MyDeps, instructions="...")

@pm.tool
def house_limit(ctx: RunContext[MyDeps]) -> float:
    return ctx.deps.risk_limit

agent = PydanticAIAdapter(pm, deps=MyDeps(risk_limit=0.25))
scores = tf.evaluate({"pm": agent}, seed=4242, universe=roster)
```

Your `deps_type`, your tools, your `RunContext` usage, your instructions and
your toolsets all keep working, because the adapter changes none of them.

## What you give up, stated plainly

**The observation arrives as text, in the run's prompt.** Your tools cannot
query it programmatically: there is no `ctx.deps.observation`, and a tool
that wants the day's prices cannot ask for them. In exchange, nothing about
your existing agent has to change.

That trade was measured, not assumed. PydanticAI has exactly one dependency
slot -- `run(deps=...)`, read by every tool through `RunContext.deps` -- and
`Agent._get_deps` does no runtime type check, so an adapter that wrapped
your deps in a container of its own would hand your tools an object of the
wrong type and they would fail on the attribute access, one frame away from
any explanation. There is no second channel and no `RunContext.extras`. An
adapter cannot both carry its own payload in `deps` and leave yours intact,
so it carries nothing there and passes yours through untouched.

If a tool of yours genuinely needs the observation, put a mutable holder on
your own deps object and fill it from a decision rule you own; the adapter
never constructs deps, so it cannot collide with whatever you put there.

## The mandate is added, not substituted

`MANDATE` rides on the run's `instructions=` argument, which PydanticAI
APPENDS to the agent's own instructions rather than replacing them. Measured
against 2.36.0: an agent instructed "USER MANDATE." run with
`instructions="TRADEFLOOR RULES."` sends the model
`'USER MANDATE.\\n\\nTRADEFLOOR RULES.'`. The context-manager form,
`Agent.override(instructions=...)`, REPLACES instead -- including
instructions contributed by capabilities -- which is why it is not used
here. Your agent still knows what you told it; it additionally knows the
rules of this market.

## The answer shape is bound per run

`output_type=common.decision_model()` is passed to `run()`, which builds a
fresh output schema for that run and leaves the agent's own `output_type`
untouched. The point is not convenience: the model class is what PydanticAI
turns into the output tool's parameter schema, so the side enum and the
non-negative quantity are stated to the model BEFORE it answers, and a
violation is caught inside the framework's own retry loop -- fed back as the
pydantic error text and fixed within the turn -- instead of dying one layer
later in `parse_decision` and costing the decision point.

One case is refused rather than worked around. PydanticAI raises
`UserError("Cannot set a custom run output_type when the agent has output
validators")`, because a validator registered with `@agent.output_validator`
expects the agent's own output type. An agent with output validators
therefore cannot have its output type overridden, and the adapter says so
with the fix rather than letting the framework's message surface bare. Pass
`bind_output_type=False` and the agent's own output type is used; whatever
it produces still goes through `parse_decision`, which is total.

## Which exception means what

`common.FrameworkAdapter.act` wraps any non-Integration exception from
`ask()` in `FrameworkError` -- "the call never completed" -- and it wraps
CONTROL-FLOW exceptions too, which are not failures at all. PydanticAI has
two, and each is decided here rather than left to the default, because this
is where the framework knowledge is.

**`UsageLimitExceeded` becomes `UsageLimitReached`**, a `FrameworkError`
subclass defined in this module. A caller who set `request_limit` did so on
purpose and should be able to catch the stop by name rather than by
inspecting `__cause__` on a generic error. Being a `FrameworkError` it is
still in the shared family, so `act()` passes it through untouched and code
that catches `IntegrationError` or `FrameworkError` is unaffected. The
original exception stays on `__cause__`.

**`UnexpectedModelBehavior` becomes `DecisionError`.** It covers two
different things, and they land on the same side of the Tradefloor
boundary. One is a schema violation the retry budget could not fix; the
other is a model that answered in prose where the output tool was required.
Both produce the identical message, `"Exceeded maximum output retries (N)"`.
And both mean the same thing to an experiment: the agent was given N chances
to produce a valid decision and did not. Scoring that as an outage would let
an agent that reliably emits garbage read as a flaky network, which is the
one reading that would make the error columns useless.

A caller who needs to tell the two apart reads `__cause__.__cause__`:

    a `pydantic.ValidationError`  the model produced a decision-shaped
                                  answer with a bad field, and the error
                                  names the field
    anything else                 the model produced no decision at all

The message this adapter raises says which of the two it was, so the common
case needs no chain-walking.

`ContentFilterError` is the exception to the exception. It subclasses
`UnexpectedModelBehavior`, but the provider blocked the exchange and the
model never answered, so it is left alone for `act()` to file under
`FrameworkError`.

`RunCancelled` is deliberately NOT special-cased. It arises only if the
user's own tool calls `ctx.cancel()`, and it arrives as a `FrameworkError`
with the original on `__cause__`. That is a recorded choice, not an
oversight: giving it a type would be inventing surface for a case nothing
in this repo exercises, and it is one line to add when something does.

## The prompt is JSON, and that is deliberate

`render` JSON-dumps the allowlisted payload with sorted keys. The prose
block `finrobot.render` produces reads better to a human, but three things
argue for JSON here. The answer shape is already stated to the model by the
output schema, so the prompt only has to carry data. A hand-written
formatter is a second place the payload can be described, and it drifts. And
the render must work with no framework installed, because `digest(prompt)`
is the replay key and a replay needs nothing but the standard library.

## Replay

`mode="replay"` reads a recorded response for `digest(prompt)` and never
imports PydanticAI, so a reader without the extra installed can still run
the experiment. `mode="live"` calls the real agent and, given a `recorder`,
writes the same entries back. Change the observation mapping and the digest
changes, the key goes missing, and the replay raises naming the step --
which is the point, because replaying anyway would answer the new question
with a response given to the old one.

What a key over the input cannot see is a change to something that never
enters it. The mandate reaches the agent as `run(instructions=...)`, not as
part of the prompt, so editing it leaves every recorded key intact -- the run
completes, all fifteen digests match, and the decisions replayed were taken
under instructions nobody is running any more. That is a property of any
adapter whose **instructions travel separately from the keyed input**, not a
PydanticAI quirk, and the other integrations in this package share it.

`_check_instructions` refuses that, at construction rather than at the first
decision: everything it needs is known before the market opens. It compares
the recorded `instructions_digest` against the configured one and names both.
A transcript that records no digest is allowed through -- it never claimed a
mandate, so it is not known to be wrong, and refusing it would break
hand-written fixtures for no safety gain.

There is deliberately no version-only fallback layer. The FinRobot adapter
has one, for recordings made before its digest field existed; this adapter
stamps `instructions_digest` and `instructions_version` together, from one
`provenance()` call, so a transcript carrying the version without the digest
cannot be produced. A branch that cannot be reached is a branch that cannot
be tested, and this package has spent enough effort deleting guards that
could not fire.

## Async, and a trap worth knowing about

The framework's `Agent.run_sync` is not used. It calls
`loop.run_until_complete`, which raises `RuntimeError: This event loop is
already running` inside a notebook -- measured -- so this adapter calls the
async `Agent.run` and hands the coroutine to `common.run_sync`, the one
shared bridge.

The trap: when a loop IS already running, that bridge runs the coroutine on
a separate thread, and `concurrent.futures` does not propagate context
variables. `Agent.override(...)` is implemented with context variables, so
an override set around `World.run` inside a notebook does NOT reach the run.
This adapter never depends on that -- the model is passed per run through
`model=`, which is a plain argument -- but a test or notebook that sets a
model with `override` and then wonders why the real provider was called is
meeting this and not a bug in the adapter. Pass `model=` to the adapter
instead.
"""

from __future__ import annotations

import json
from typing import Any

from .._core import ValidationError
from ..render import JSONRenderer, Renderer
from .common import (DECISION_SCHEMA_VERSION, MAX_PARTICIPATION, AdapterInfo,
                     DecisionError, FrameworkAdapter, FrameworkError,
                     IntegrationError, Transcript, check_prior,
                     decision_model, digest, moment_of, refuse_replay_reask,
                     replay_response, require, run_sync,
                     stamp_resume_counts)

#: The rules of this market, appended to whatever the agent was already
#: instructed. It says what the agent is for and what the market can execute.
#: It says nothing about what is coming: the question an experiment asks is
#: whether the agent infers a changed world from the observation, and an arm
#: told it was the intervention arm would be answering something else.
#:
#: It deliberately does NOT restate the answer shape. `decision_model()` is
#: bound as the run's output type, so the field names, the side enum and the
#: non-negative quantity reach the model as schema, where they are enforced
#: rather than requested.
MANDATE = """\
You are managing a portfolio inside a simulated financial market.

The message is the entire observable state, as JSON. Use nothing else. You \
have no other data source and no browsing. The tickers are synthetic and are \
not real listed companies, so anything you know about real securities does \
not apply here.

Seek attractive risk-adjusted returns while controlling downside risk. You \
may buy, sell, resize or maintain positions. You are not required to trade: \
an empty action list means change nothing, and is a valid answer.

Every order is a market sweep of the live book. There are no limit prices \
and no order types. Quantities are SHARES, always positive -- the side \
carries the direction.

Two separate limits bind your orders, and you must respect BOTH.

`max_order_shares` on each asset is the participation cap: the most this \
MARKET will absorb from you in that asset in one step. \
`portfolio.buying_power` is the funding cap: the additional gross notional \
this BOOK can hold before `portfolio.max_leverage` refuses the trade, \
across all assets together. The funding cap is usually the binding one, and \
it is shared, so sizing each asset to its own participation cap will \
ordinarily ask for several times the equity you have. An order that exceeds \
the funding cap is REFUSED, not reduced: it buys nothing and costs you the \
decision.
"""

class UsageLimitReached(FrameworkError):
    """The run stopped because it hit the request budget, not because it
    failed.

    A `FrameworkError` subclass so the shared family still holds -- `act()`
    re-raises an `IntegrationError` untouched, and anything catching
    `FrameworkError` or `IntegrationError` keeps working -- and a named type
    so a caller who set `request_limit` on purpose can catch the stop by
    name instead of matching on a message or walking `__cause__`.

    PydanticAI's own `UsageLimitExceeded` is on `__cause__`, with the budget
    it exceeded in its message.
    """


#: Version of `MANDATE`, recorded beside every decision. Replaying a run
#: under a different mandate produces a different experiment; this is how a
#: reader notices, and it is what `AdapterInfo.instructions_version` carries.
#: Bump it when the text above changes meaning, not when it changes wording.
#:
#: 2: names the funding cap beside the participation cap. Version 1 called
#:    `max_order_shares` "the most this market will absorb" and said nothing
#:    about `buying_power`, which is the limit that usually binds. Four
#:    independent agents sized to the stated cap and were refused at the
#:    unstated one; a mandate that names one of two limits is a trap, and a
#:    run recorded under version 1 is measuring the trap, not the agent.
MANDATE_VERSION = "2"

#: Requests one decision may cost. A decision is normally two model requests
#: -- one round of tool calls, one final answer -- and eight leaves room for
#: an agent with several tools without leaving a runaway loop uncapped. A
#: metered call with no ceiling is how an experiment produces a bill instead
#: of a result. ``None`` removes the cap.
REQUEST_LIMIT = 8


class PydanticAIAdapter(FrameworkAdapter):
    """A PydanticAI agent, in the shape :class:`World` and ``evaluate`` run.

    ```python
    agent = PydanticAIAdapter(my_agent, deps=my_deps, every=6)
    world = World(seed=4242, universe=roster, agent=agent, pins=PINS)
    ```

    ``agent`` is your own :class:`pydantic_ai.Agent`, already built. See the
    module docstring for what the adapter does and does not touch.

    ``mode`` is ``"live"`` or ``"replay"``. Replay imports nothing from
    PydanticAI and needs no API key; ``"live"`` imports it inside
    :meth:`_run` and names the extra if that import fails.

    ``model`` overrides the model for every run this adapter makes, as a
    plain per-run argument rather than through :meth:`Agent.override`. That
    is what makes an offline test work in a notebook as well as a script:
    ``override`` is built on context variables, and the shared async bridge
    crosses a thread boundary where those do not propagate. Pass
    ``TestModel()`` or ``FunctionModel(...)`` here for an offline run.

    ``deps`` is handed to ``run(deps=...)`` verbatim. The adapter never
    constructs, wraps or inspects it.

    ``every`` is the decision cadence in steps, and the two arms of a
    comparison MUST run the same one; :meth:`fork` copies it.
    """

    def __init__(self, agent: Any = None, *, deps: Any = None,
                 mode: str = "live", model: Any = None,
                 transcript: Transcript | None = None,
                 recorder: Transcript | None = None,
                 prior: Transcript | None = None,
                 instructions: str = MANDATE,
                 bind_output_type: bool = True,
                 request_limit: int | None = REQUEST_LIMIT,
                 renderer: Renderer | None = None,
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
                "Transcript.load(path), or pass mode='live' to call the "
                "agent.")
        # `agent` defaults to None rather than being required so this refusal
        # owns the message: a bare TypeError from the signature would not say
        # what a valid agent is, and the mistake it catches -- reaching for
        # the adapter before building an Agent -- is the common one.
        if mode == "live" and not hasattr(agent, "run"):
            raise ValidationError(
                "live mode needs a pydantic_ai.Agent -- an object with an "
                f"async run() -- got {type(agent).__name__}. Build the agent "
                "first: Agent('openai:gpt-5.2', deps_type=..., "
                "instructions=...), then hand it here. Replay mode needs no "
                "agent at all.")
        if request_limit is not None and request_limit < 1:
            raise ValidationError(
                f"request_limit must be >= 1 request, got {request_limit}. "
                "Pass None to remove the cap.")

        super().__init__(
            info=info or AdapterInfo(
                framework="pydantic-ai",
                framework_version=_installed_version(),
                framework_url="https://github.com/pydantic/pydantic-ai",
                # The entry point matters to anyone reproducing this: the
                # async `run` and not `run_sync`, because the synchronous
                # one cannot be called from inside a running event loop.
                entry_point="pydantic_ai.Agent.run",
                agent_name=getattr(agent, "name", None) or "",
                model=_model_name(model, agent),
                provider=_provider_name(model, agent),
                mode=mode,
                # The version names THIS module's MANDATE, so it is only
                # claimed when that is what is being run. Stamping it beside
                # someone else's instructions would label their text with a
                # version it has nothing to do with, and the mismatch
                # message would then report two identical versions with
                # different digests -- true, and useless.
                instructions_version=(MANDATE_VERSION
                                      if instructions == MANDATE else ""),
                instructions_digest=digest(instructions),
                generation={"request_limit": request_limit,
                            "bind_output_type": bind_output_type}),
            every=every, fundamentals=fundamentals,
            max_participation=max_participation, arm=arm)

        self.agent = agent
        self.deps = deps
        self.mode = mode
        self.model = model
        self.transcript = transcript
        self.recorder = recorder
        self.prior = check_prior(
            prior, mode=mode, recorder=recorder,
            instructions_digest=self.info.instructions_digest)
        self.instructions = instructions
        self.bind_output_type = bind_output_type
        self.request_limit = request_limit
        #: What turns the payload into the text half of the record and the
        #: replay key -- `run(instructions=...)` carries `self.instructions`
        #: separately and never passes through this. Defaults to
        #: :class:`~tradefloor.render.JSONRenderer`, which reproduces this
        #: adapter's own historical `render(payload)` character for
        #: character.
        self.renderer: Renderer = renderer if renderer is not None else JSONRenderer()

        # Here rather than at the first decision. Everything this needs is
        # known before the market opens, and a replay that refuses twenty
        # simulated days in has already spent the reader's time on a fault
        # that was knowable at construction. `fork()` rebuilds a twin through
        # this same constructor, so both arms of a comparison are checked.
        if mode == "replay":
            self._check_instructions()

    # -- the one framework-specific method ---------------------------------

    def ask(self, obs: Any, payload: dict[str, Any]) -> Any:
        """One decision for this payload, as a dict `parse_decision` accepts.

        The envelope is unwrapped here, not by `parse_decision`: PydanticAI
        returns an `AgentRunResult`, and what the market gets to see is
        `result.output`, converted to a plain mapping. Handing the wrapper
        onwards would present a result object with no `actions` key, which
        `parse_decision` refuses -- correctly, but one layer too late to say
        anything useful about why.

        Exceptions are classified here too, and not in :meth:`_run`, so the
        classification survives a subclass replacing the framework call: a
        test double raising `UsageLimitExceeded` gets the same treatment as
        the real framework raising it, which is the only way the rule can be
        tested without a provider. `act()` wraps everything it is handed,
        control-flow exceptions included, so anything that should not read as
        an outage has to be named before it gets there. See the module
        docstring for the reasoning on each.
        """
        prompt = self.renderer.render(payload)
        key = digest(prompt)
        # Declared before the branch, so a replayed run's record carries the
        # same chain a live one does: observation, exact input, response,
        # validated action, order. A replay whose record omitted the input
        # could not be audited against the recording it came from.
        self.record_exchange(prompt, key=key)

        if self.mode == "replay":
            # No framework import on this path, by construction: a recorded
            # run must replay with nothing installed. The instructions were
            # checked at construction, so nothing here can refuse except a
            # missing recording.
            return replay_response(self.transcript, key,
                                   step=obs.step, day=obs.day)

        try:
            output = self.call_or_resume(
                key, lambda: self._run(prompt, payload, obs))
        except IntegrationError:
            # Already one of ours -- MissingDependencyError from `require`,
            # or a refusal a subclass raised deliberately.
            raise
        except Exception as exc:
            translated = _translate(exc, obs, self)
            if translated is None:
                raise           # not ours to classify; act() wraps it
            raise translated from exc

        if self.recorder is not None:
            # Stamp the provenance on first write rather than leaving it to
            # the caller. `_check_instructions` can only refuse a mismatched
            # replay if the recording says what it ran under, and a guard
            # that arms itself only when someone remembers to set `meta` is
            # a guard that is off in exactly the runs nobody was careful
            # about. An explicit `meta` is respected: keys already there win.
            if "instructions_digest" not in self.recorder.meta:
                for field, value in self.provenance().items():
                    self.recorder.meta.setdefault(field, value)
            self.recorder.record({
                "arm": self.arm, "step": obs.step, "day": obs.day,
                "digest": key, "prompt": prompt,
                "response": _recordable(output),
            })
            stamp_resume_counts(self.recorder, self.prior)
        return output

    def _check_instructions(self) -> None:
        """Refuse a replay whose recording ran under other instructions.

        Called from the constructor, not from `act()`: a replay that refuses
        twenty simulated days in has already spent the reader's time on a
        fault knowable before the market opened.

        What a key over the input cannot see is a change to something that
        never enters it. The replay key is a digest of the OBSERVATION, and
        the instructions **travel separately from the keyed input** -- as
        `run(instructions=...)` here, as an agent profile or a system prompt
        elsewhere. So editing the mandate leaves every key intact, every
        lookup hits, and the run completes: the decisions replayed were taken
        under instructions nobody is running any more, and nothing says so.
        Any adapter with that property has this hole; the FinRobot
        integration is the other instance in this package.

        Only a MISMATCH is refused. A transcript recording no digest never
        claimed a mandate, so it is not known to be wrong, and refusing it
        would break hand-written fixtures for no safety gain. There is no
        version-only middle case: this adapter stamps digest and version
        together from one `provenance()` call, so a recording with the
        version and not the digest cannot exist, and a branch that cannot be
        reached cannot be tested.
        """
        recorded = (self.transcript.meta or {}).get("instructions_digest")
        current = digest(self.instructions)
        if recorded and recorded != current:
            raise DecisionError(
                "this transcript was recorded under different instructions: "
                f"the recording carries instructions_digest {recorded} and "
                f"this adapter is configured with {current} "
                f"(recorded under mandate version "
                f"{(self.transcript.meta or {}).get('instructions_version') or 'custom'}"
                f", configured with "
                f"{MANDATE_VERSION if self.instructions == MANDATE else 'custom'}"
                ").\n"
                "The replay key is a digest of the OBSERVATION, so every "
                "lookup would still hit and the run would complete -- "
                "answering the new instructions with decisions made under "
                "the old ones, which is a different experiment wearing this "
                "one's results.\n"
                "Either pass the original mandate, "
                "`PydanticAIAdapter(..., instructions=<the recorded text>)`, "
                "or re-record the run live against the mandate you want.")

    # -- PydanticAI ---------------------------------------------------------

    def reask(self, entry: Any) -> Any:
        """One more answer to a recorded input, changing nothing."""
        refuse_replay_reask(self.mode, type(self).__name__)
        return self._run(entry.get("prompt"), entry.get("payload") or {},
                         moment_of(entry))

    def _run(self, prompt: str, payload: dict[str, Any], obs: Any) -> Any:
        """One real PydanticAI run. The only method that reaches the
        framework, and the seam a test replaces.

        The framework is imported HERE rather than at module scope, so that
        replay -- and `import tradefloor` -- never need it installed.
        """
        pydantic_ai = require(
            "pydantic_ai", extra="pydantic-ai",
            purpose="live mode runs a real pydantic_ai.Agent")

        kwargs: dict[str, Any] = {
            "deps": self.deps,
            "instructions": self.instructions,
            "model": self.model,
            # Identifiers for anyone who has instrumentation switched on.
            # They cost nothing when it is off, which is the default, and
            # they are the difference between a trace of forty anonymous
            # runs and a trace you can line up against a scorecard.
            "run_id": (f"tradefloor-{self.arm or 'run'}"
                       f"-day{payload['day']}-step{payload['step']}"),
            "metadata": {
                "tradefloor.arm": self.arm,
                "tradefloor.day": payload["day"],
                "tradefloor.step": payload["step"],
                "tradefloor.decision_schema_version": DECISION_SCHEMA_VERSION,
            },
        }
        if self.bind_output_type:
            kwargs["output_type"] = decision_model()
        if self.request_limit is not None:
            kwargs["usage_limits"] = pydantic_ai.usage.UsageLimits(
                request_limit=self.request_limit)

        # Exceptions are classified by `ask()`, not here, so that the
        # classification applies to whatever this call raises however it is
        # invoked -- including a test double that replaces this method.
        return _unwrap(run_sync(self.agent.run(prompt, **kwargs)).output)

    # -- the rest of the adapter contract -----------------------------------

    def state(self) -> dict[str, Any]:
        """What a fork has to agree on, plus what would change the question.

        The base publishes the price memory, the last decision, the cadence
        and the instructions digest. Added here: the mode and whether the
        shared decision model was bound. Both change what the agent was
        asked, so two arms that disagreed on either would not be a
        controlled comparison, and `agree()` should say so.

        The agent, the deps and the model stay out. Deps in particular: it
        is the user's own object, it may hold a client or a key, and this
        dictionary is printed by the fork agreement and written into
        artifacts.
        """
        published = super().state()
        published["mode"] = self.mode
        published["bind_output_type"] = self.bind_output_type
        return published

    def provenance(self) -> dict[str, Any]:
        """The base's provenance, plus which renderer produced the text."""
        out = super().provenance()
        out["renderer"] = self.renderer.key()
        return out

    def fork_kwargs(self) -> dict[str, Any]:
        """The constructor arguments a fork is rebuilt with.

        The agent, the deps, the model, the transcript and the recorder are
        SHARED rather than copied. Every one of them is configuration both
        arms must agree on, and copying an Agent -- which may hold an HTTP
        client -- is wasteful at best and a shared socket at worst. A live
        recording of both arms belongs in one file, and a replay of one arm
        must read the same recorded run as the other.
        """
        kwargs = super().fork_kwargs()
        kwargs.update(agent=self.agent, deps=self.deps, mode=self.mode,
                      model=self.model, transcript=self.transcript,
                      recorder=self.recorder, prior=self.prior,
                      instructions=self.instructions,
                      bind_output_type=self.bind_output_type,
                      request_limit=self.request_limit,
                      renderer=self.renderer)
        return kwargs

    def __repr__(self) -> str:
        return (f"PydanticAIAdapter(mode={self.mode!r}, arm={self.arm!r}, "
                f"every={self.every}, decisions={len(self.record)})")


# -- the observation, as the agent sees it -----------------------------------


def render(payload: dict[str, Any]) -> str:
    """The payload as the text the agent receives.

    Generated from ``payload`` alone, so nothing outside the allowlist can
    appear here by accident, and with the standard library alone, so a
    replay -- which keys on ``digest`` of this string -- needs no framework
    installed.

    Sorted keys and a fixed indent, because the replay key is a hash of
    these exact bytes. Two runs that show the agent the same market must
    produce the same string, and a dict whose insertion order differed would
    otherwise miss its own recording.
    """
    return json.dumps(payload, indent=2, sort_keys=True, default=_jsonable)


def _jsonable(value: Any) -> Any:
    """Last resort for a payload value `json` cannot encode.

    The serializer emits floats, strings, None and containers of those, so
    this should never fire. It raises rather than stringifying, because a
    value silently rendered as its repr would change the replay key without
    changing anything a reader could see.
    """
    raise TypeError(
        f"the observation payload carried a {type(value).__name__}, which "
        "does not serialize to JSON. serialize_observation emits numbers, "
        "strings, None and containers of those; a new field that does not "
        "is a bug in the allowlist, not something to render as its repr.")


# -- helpers ------------------------------------------------------------------


def _translate(exc: Exception, obs: Any, adapter: Any) -> Exception | None:
    """What one of PydanticAI's exceptions means at the Tradefloor boundary.

    Returns the exception to raise instead, or ``None`` to let this one
    through for :meth:`FrameworkAdapter.act` to wrap in a ``FrameworkError``.
    The reasoning for each case is in the module docstring; this is where it
    is enforced.

    The framework is read out of ``sys.modules`` rather than imported. If it
    was never imported, nothing it defines can have been raised, and forcing
    an import here would break the guarantee that a replay -- or a subclass
    that never calls the framework -- needs nothing installed.
    """
    import sys

    pydantic_ai = sys.modules.get("pydantic_ai")
    if pydantic_ai is None:
        return None
    errors = pydantic_ai.exceptions

    if isinstance(exc, errors.ContentFilterError):
        # A subclass of UnexpectedModelBehavior, and the one member of that
        # family that is NOT an output-contract failure: the provider
        # blocked the exchange, so the model never answered.
        return None
    if isinstance(exc, errors.UsageLimitExceeded):
        return UsageLimitReached(
            f"the run at step {obs.step} (day {obs.day}) stopped on its "
            f"request budget of {adapter.request_limit}: {exc}. Raise "
            "request_limit, or pass None to remove the cap.")
    if isinstance(exc, errors.UnexpectedModelBehavior):
        return DecisionError(
            f"the agent did not produce a valid decision at step "
            f"{obs.step} (day {obs.day}): {_why(exc)}. {exc}")
    if isinstance(exc, errors.UserError):
        return _user_error(exc, adapter.bind_output_type)
    return None


def _why(exc: Exception) -> str:
    """Which kind of bad output an `UnexpectedModelBehavior` was.

    The framework reports both with the same sentence -- "Exceeded maximum
    output retries (N)" -- and only the chain tells them apart, so the
    reading is done once, here, and put in the message. A caller wanting it
    programmatically reads `__cause__.__cause__` and checks for a
    `pydantic.ValidationError`, which is the same test this makes.
    """
    from pydantic import ValidationError as _PydanticValidationError

    if isinstance(exc.__cause__, _PydanticValidationError):
        fields = ", ".join(
            ".".join(str(part) for part in error["loc"])
            for error in exc.__cause__.errors()) or "an unnamed field"
        return ("its answer did not satisfy the decision schema after every "
                f"retry (rejected on {fields})")
    return ("it produced no decision at all after every retry -- an answer "
            "the output schema could not read as a decision")


def _recordable(output: Any) -> str:
    """One decision, as the string a transcript stores.

    A transcript entry's ``response`` is always text, so that
    ``replay_response`` can hand it straight back and every adapter's
    recordings have one shape. With the shared model bound the output is a
    mapping and JSON-encoding it is right.

    A string is passed through UNCHANGED, and that branch is the whole
    reason this is a function. Under ``bind_output_type=False`` a
    text-output agent returns JSON text; encoding it again would store a
    JSON string CONTAINING JSON, and the replay would parse one level, get a
    `str` where a decision was expected, and fail -- at replay time, against
    a recording that looked fine when it was written.
    """
    if isinstance(output, str):
        return output
    return json.dumps(output, sort_keys=True)


def _unwrap(output: Any) -> Any:
    """The decision inside whatever the run returned.

    With the shared model bound this is always a pydantic instance, and
    ``model_dump()`` is the documented conversion. The other branches exist
    for ``bind_output_type=False``, where the output is whatever the user's
    own agent produces: a model of their own, a mapping, or the plain string
    a default `Agent` returns. Each of those is a shape ``parse_decision``
    already accepts, so they are passed through rather than guessed at.
    """
    dump = getattr(output, "model_dump", None)
    if callable(dump):
        return dump()
    return output


def _user_error(exc: Exception, bind_output_type: bool) -> Exception:
    """A PydanticAI `UserError`, re-raised as something actionable.

    A `FrameworkError` and not a bare `ValidationError`, for a mechanical
    reason worth stating: `act()` re-raises an `IntegrationError` unchanged
    and wraps anything else, so returning a plain `ValidationError` here
    would arrive wrapped a second time, under the sentence "pydantic-ai
    raised ValidationError instead of returning a decision" -- which
    describes the adapter's own plumbing rather than the mistake. The column
    is right either way; only the message a user reads differs.

    One case is common enough to name: an agent carrying
    `@agent.output_validator` cannot have its output type overridden per
    run, because a validator expects the agent's own output type. The
    framework's own message is accurate and says nothing about what to do
    next.
    """
    if bind_output_type and "output validator" in str(exc):
        return FrameworkError(
            "this agent has output validators registered with "
            "@agent.output_validator, and PydanticAI does not allow a "
            "per-run output_type on an agent that has them. The adapter "
            "binds the shared decision model as the run's output type so "
            "the market's rules are enforced inside the framework's retry "
            "loop.\n"
            "Either remove the output validator, or construct the adapter "
            "with bind_output_type=False and let your agent's own output "
            "type stand -- whatever it produces still goes through "
            f"parse_decision. Original error: {exc}")
    # Anything else is a wiring mistake -- no model set, no API key, a bad
    # run_id -- and the framework's own message is the useful one.
    return FrameworkError(f"PydanticAI refused the run: {exc}")


def _installed_version() -> str:
    """The installed pydantic-ai version, or "" if it is not installed.

    Read through `importlib.metadata` rather than by importing the package,
    so constructing a replay-mode adapter still needs nothing installed.
    """
    import importlib.metadata as metadata

    for distribution in ("pydantic-ai-slim", "pydantic-ai"):
        try:
            return metadata.version(distribution)
        except metadata.PackageNotFoundError:
            continue
    return ""


def _provider_name(model: Any, agent: Any) -> str:
    """The provider half of PydanticAI's `provider:name` model spelling.

    Metadata only, and never a credential: `"anthropic:claude-opus-5"` gives
    `"anthropic"`. A `Model` object is asked for its `system`, which is what
    the framework calls the same thing. Worth recording because "which
    provider answered" is the one provenance question a model name alone
    cannot settle -- the same name can be reached through a gateway, a
    proxy, or a different vendor's compatible endpoint.
    """
    chosen = model if model is not None else getattr(agent, "model", None)
    if isinstance(chosen, str):
        return chosen.split(":", 1)[0] if ":" in chosen else ""
    if chosen is None:
        return ""
    return str(getattr(chosen, "system", "") or "")


def _model_name(model: Any, agent: Any) -> str:
    """A printable name for whatever model this adapter will use.

    Metadata only, and it never touches a credential: the run-level override
    if there is one, otherwise whatever the agent reports. A `Model` object
    gets its `model_name`; a string is already the name.
    """
    chosen = model if model is not None else getattr(agent, "model", None)
    if chosen is None:
        return ""
    if isinstance(chosen, str):
        return chosen
    return str(getattr(chosen, "model_name", "") or type(chosen).__name__)
