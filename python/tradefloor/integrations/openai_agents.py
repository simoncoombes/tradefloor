"""Run an OpenAI Agents SDK agent inside a Tradefloor market.

The OpenAI Agents SDK is OpenAI's open-source agent framework
(https://github.com/openai/openai-agents-python, MIT). This module is a
Tradefloor integration for it. There is no affiliation with or endorsement by
OpenAI, and nothing here is an official Agents SDK interface. Developed
against ``openai-agents`` 0.22.0.

    Tradefloor observation
            |
      OpenAIAgentsAdapter    serialize_observation -> brief + JSON payload
            |
      Runner.run             the user's real Agent, its tools, its guardrails
            |
      OpenAIAgentsAdapter    typed output -> parse_decision -> share deltas
            |
    Tradefloor execution

Everything except :meth:`OpenAIAgentsAdapter.ask` is
:class:`~tradefloor.integrations.common.FrameworkAdapter`: the price memory,
the cadence, the observation allowlist, the two-stage validation, the record
and the fork. This module is the part that reaches the SDK, and the reasons
it is shaped the way it is are below.

(``common.ReplayMixin`` packages the record-and-replay branch this module
writes out by hand. It arrived after this adapter was finished and tested,
and migrating working adapters onto it was judged churn rather than
correctness: the record join is already delivered by ``record_exchange``,
and a positional replay key is caught for every adapter by
``check_the_replay_key_derives_from_the_input`` whether or not it takes the
mixin. A NEW adapter should take the mixin; ``callable.py`` is the reference
for that shape.)

## The user's agent is used, never altered

An :class:`agents.Agent` is the user's: their instructions, their tools,
their model settings, their hooks, their guardrails. Evaluating a mutated
copy would rank an agent nobody wrote. So the only change this adapter makes
is to bind the decision contract as the output type, and it does that with
``Agent.clone(output_type=...)``, which is ``dataclasses.replace`` and
leaves the original untouched.

``clone`` is a SHALLOW copy, and that is a trap worth naming: an attribute
not passed arrives as the original agent's own list, so ``cloned.tools.
append(x)`` also appends to the user's agent. Anything this module ever adds
must be passed as a new list -- ``clone(tools=[*agent.tools, extra])`` --
never appended to the clone.

The instructions are the other half of not altering the agent. The standing
:data:`BRIEF` is sent as a message in the run, NOT written into
``instructions``: overwriting a user's system prompt would evaluate a
different agent, and the brief is a turn in the conversation rather than a
change to who the agent is.

## The decision contract is bound as the output type

``output_type`` is ``common.decision_model()``, the shared Pydantic
rendering of ``common.decision_schema()``, bound in strict mode. The
provider then constrains generation to the contract: the side enum, the
non-negative quantity, ``additionalProperties: false`` -- which is what
stops a model inventing ``order_type`` or ``limit_price``, fields this
market has no execution path for.

What binding it does NOT buy, measured rather than assumed: on
``openai-agents`` 0.22.0 there is no client-side retry when validation
fails. One model call, then ``ModelBehaviorError``. The value of the bound
type is provider-side constrained decoding, not a repair loop.

Which means the guarantee depends on WHICH PROVIDER is behind the agent,
and that is worth being exact about. The SDK is even-handed: it sends the
schema and the ``strict`` flag on both of its paths, the Responses API at
``models/openai_responses.py:2047`` and Chat Completions at
``models/chatcmpl_converter.py:117``. What differs is who receives it.
OpenAI implements strict structured outputs natively, so the contract is
enforced during generation. Route the same agent elsewhere -- through
``agents.extensions.models.litellm_model.LitellmModel`` to Anthropic, say,
which this adapter supports and which has no native equivalent -- and the
schema is TRANSLATED into whatever that provider offers. It still usually
works, and validation on this side is unchanged either way because
:func:`~tradefloor.integrations.common.parse_decision` runs regardless. But
"the provider constrains generation" is a claim about the native path, and
an agent pointed at a translating proxy is relying on the translation
rather than on the guarantee.

Validated output still goes through
:func:`~tradefloor.integrations.common.parse_decision`, which is the one
validator. Two validations of the same contract is deliberate: the SDK's
runs inside the framework where it can shape generation, and Tradefloor's
runs on this side of the wall where it decides what reaches the market.

## Errors carry their own step and day

The SDK redacts model text from its own error messages by default
(``OPENAI_AGENTS_DONT_LOG_MODEL_DATA``), so ``ModelBehaviorError`` arrives
saying "Invalid JSON when parsing model output" and nothing about which
decision point produced it. Every refusal raised here therefore states the
step and the day itself. Without that, a failed run says only that
something, somewhere, was invalid.

## Which SDK exceptions are outcomes and which are failures

``FrameworkAdapter.act`` wraps everything escaping the framework call in
:class:`~tradefloor.integrations.common.FrameworkError`, control-flow
exceptions included, because it cannot tell a signal from a crash. This
framework signals through exceptions, so :meth:`OpenAIAgentsAdapter._run`
sorts them here, on purpose, rather than letting the default decide.

Four are OUTCOMES -- the agent was asked and produced no executable
decision -- and become
:class:`~tradefloor.integrations.common.DecisionError`:

- ``ModelBehaviorError``: the output did not satisfy the bound contract.
- ``ModelRefusalError``: the model declined. A refusal is a thing the agent
  did.
- ``MaxTurnsExceeded``: the turn budget was spent without converging.
- the four guardrail tripwires: the user's own guardrail stopped the run.
  This is the case worth arguing, because a guardrail firing is a
  deliberate configured outcome and the tempting alternative is to treat it
  as a decision to trade nothing. It is NOT converted to a HOLD. An empty
  decision means the agent considered the market and declined, and it
  scores as ``trades=0`` beside an empty error column; a blocked run wearing
  that shape would record a considered choice nobody made. The same
  reasoning refuses an unwrapped envelope in
  :func:`~tradefloor.integrations.common.parse_decision`. A caller who wants
  a tripped guardrail to mean "sit this step out" can catch
  ``DecisionError`` and continue, which is a decision they take with the
  fact in hand rather than one this adapter takes for them.

Everything else -- a timeout, a transport failure, a misconfiguration --
is a FAILURE and is left to ``act``, which wraps it in
:class:`~tradefloor.integrations.common.FrameworkError` with the chain
intact. The two are scored differently by an experiment: the model said
something unusable, versus the call never completed.

## Async, and why the SDK's own sync entry point is not used

``Runner.run_sync`` raises a bare ``RuntimeError`` when a thread already has
a running event loop (``run.py:2222`` in 0.22.0), which is every Jupyter
cell. An adapter built on it works in a script and dies in the notebook the
same reader tries next. So this calls the ASYNC entry point, ``Runner.run``,
and bridges with :func:`~tradefloor.integrations.common.run_sync`, which is
the one supported crossing and behaves the same whether or not a loop is
already running.

## Tracing is off unless asked for

The SDK's tracing is ON by default and exports to OpenAI. It skips the
export when no ``OPENAI_API_KEY`` is set, but that is not a guarantee -- a
developer with a key in their shell running an evaluation WOULD ship trace
data. So every run this adapter starts passes ``tracing_disabled=True``
unless ``tracing=True`` was asked for, and it is set per run rather than
through the SDK's process-global switch, so it cannot alter tracing for
other code in the same process.

With ``tracing=True`` the run is named and stamped: the workflow name, the
``run_id`` as the trace group so every step of one experiment links
together, and the step, day, arm and decision schema version as trace
metadata.

## Replay

## What the replay key cannot see

The key is a digest of the input, so changing the brief or the observation
mapping makes every lookup miss and the replay refuses, naming the step.
What a key over the input cannot see is a change to something that never
enters it. The user's agent carries its OWN instructions, and those reach
the model as ``system_instructions`` rather than as part of the input --
so replacing the agent entirely leaves every recorded key intact. Measured
before this was guarded: an agent instructed "BUY EVERYTHING" and one
instructed "SELL EVERYTHING" produced byte-identical replay keys, and the
recording replayed under either as though nothing had changed.

That is a property of any adapter whose **instructions travel separately
from the keyed input**, not an Agents SDK quirk, and the other integrations
in this package share it.
:meth:`OpenAIAgentsAdapter._check_instructions` closes it by comparing the
recorded ``instructions_digest`` against the configured one, at
construction rather than at the first decision. The recorder stamps that
digest on its first write, so a recording arms the guard without anybody
remembering to set ``Transcript.meta``.

Live decisions cost money and a model answers differently every time.
``mode="replay"`` reads recorded responses keyed by
:func:`~tradefloor.integrations.common.digest` of the exact input, and never
reaches :meth:`OpenAIAgentsAdapter._run`, which is where the SDK is
imported -- so a replay needs no API key, no network and no
``openai-agents`` install. ``examples/integrations/openai_agents_agent.py``
records a run and replays it in the same script.
"""

from __future__ import annotations

from typing import Any

from .._core import ValidationError
from .common import (MAX_PARTICIPATION, AdapterInfo, DecisionError,
                     FrameworkAdapter, decision_model, digest,
                     replay_response, require, run_sync)

#: The PyPI distribution that installs the framework, and the Tradefloor
#: extra that pulls it in: ``pip install "tradefloor[openai-agents]"``.
DISTRIBUTION = "openai-agents"
EXTRA = "openai-agents"

#: Recorded in adapter metadata, so a transcript says what produced it and
#: where to go and read that thing. The entry point is the ASYNC one on
#: purpose -- see the module docstring on why the SDK's own sync entry point
#: is unusable from a notebook.
FRAMEWORK_URL = "https://github.com/openai/openai-agents-python"
ENTRY_POINT = "agents.Runner.run"

#: Version of :data:`BRIEF`, recorded beside every decision. Replaying a
#: transcript under a different brief produces a different experiment, and
#: this is how a reader notices without diffing prose.
#:
#: Bumped when a SHIPPED brief changes what the agent is told to do, which is
#: not a cosmetic bar: an earlier draft named only one of the two size limits
#: and changed the recorded behaviour of the same model on the same market.
#: Revisions before the first release stay at 1 -- claiming a 2 would imply a
#: 1 somebody could be holding -- and ``instructions_digest`` distinguishes
#: them meanwhile.
BRIEF_VERSION = "1"

#: The standing brief, sent as the first message of every run.
#:
#: Deliberately short, because the user's agent already has instructions and
#: this is not the place to restate them. It says the three things only this
#: market can say: the tickers are synthetic so real-world knowledge does not
#: apply, there is no data source beyond the payload, and size is bounded by
#: what the book can absorb. It says nothing about what is coming, so both
#: arms of a counterfactual receive an identical brief and the experiment
#: measures whether the agent inferred a changed world rather than whether it
#: was told about one.
#:
#: The output shape is NOT described here. ``decision_model()`` is bound as
#: the output type, so the schema is injected by the SDK and asking for it in
#: prose as well would be two statements of one contract, free to disagree.
BRIEF = """\
You are trading a simulated market. Every instrument is synthetic: the \
tickers, the sectors and the fundamentals are generated, so anything you \
know about real listed companies does not apply here.

The message after this one is the entire observation, as JSON. You have no \
other data source and no browsing. Where a field is null it is genuinely \
unknown -- a five-day return is null until five days have been observed -- \
and null is not zero.

Seek attractive risk-adjusted returns while controlling downside risk. You \
may buy, sell, resize or maintain positions, and you are not required to \
trade: leaving the book alone is a decision.

Two separate limits bound your size and you must respect both. \
`max_order_shares` is what this market can absorb in one order without your \
own trade moving the price against you; a larger request is clipped, and the \
clip is recorded against you rather than silently applied. \
`portfolio.buying_power` is the additional gross notional this book can fund \
before the leverage cap refuses the order outright. They are unrelated \
numbers and either can be the tighter one, so check both rather than \
assuming which binds -- both are in the observation. An order well inside \
`max_order_shares` can still be refused for funding, and a refused order is \
recorded as an error rather than as a considered decision not to trade.
"""


def payload_of(call: Any) -> dict[str, Any]:
    """The serialized observation, recovered from a recorded model call.

    A helper for running this adapter offline. Scripting a model -- with
    ``agents.testing.ScriptedModel``, or any other
    :class:`agents.models.interface.Model` -- means answering a call whose
    input is whatever this module chose to send, and that layout is this
    module's business rather than the caller's. This reads it back, so a
    scripted model can decide from the same payload a real one would see:

    ```python
    from agents.testing import ModelStep, ScriptedModel, assistant_message

    def answer(call):
        payload = payload_of(call)
        ...
        return [assistant_message(json.dumps(decision))]

    model = ScriptedModel([ModelStep.respond(answer)] * 8)
    ```

    Takes anything carrying the SDK's ``input`` -- a ``ModelCall``, or the
    input list itself -- because the two are equally natural to have in hand
    at the point a scripted model answers.
    """
    import json

    items = getattr(call, "input", call)
    if isinstance(items, str):
        raise ValidationError(
            "this model call carries a bare string input, so it did not come "
            "from OpenAIAgentsAdapter, which always sends the brief and the "
            "payload as two separate messages.")
    if not items:
        raise ValidationError("this model call carries no input to read.")
    last = items[-1]
    content = last.get("content") if isinstance(last, dict) else getattr(
        last, "content", None)
    if not isinstance(content, str):
        raise ValidationError(
            f"the last input item carries {type(content).__name__} content, "
            "not the JSON payload OpenAIAgentsAdapter sends.")
    try:
        return json.loads(content)
    except ValueError as exc:
        raise ValidationError(
            "the last input item is not the JSON observation payload: "
            f"{exc}. Read it from a call this adapter made.") from exc


def _installed_version() -> str:
    """The installed ``openai-agents`` version, without importing it.

    ``importlib.metadata`` reads the distribution's metadata off disk, so
    adapter metadata can name the version in replay mode, where the package
    may not be installed at all and importing it is exactly what this
    integration promises not to do.
    """
    import importlib.metadata
    try:
        return importlib.metadata.version(DISTRIBUTION)
    except Exception:                                    # noqa: BLE001
        # PackageNotFoundError normally, but metadata reading has failed in
        # stranger ways on frozen and vendored installs, and an unknown
        # version is not a reason to refuse a run.
        return ""


def _instructions_digest(instructions: Any) -> str:
    """A digest of what the agent was told, for :class:`AdapterInfo`.

    Two arms of a controlled comparison must have run the same instructions,
    and this is how a reader checks that without the instructions themselves
    being copied into artifacts. A callable is digested by its qualified name
    rather than its text: dynamic instructions are computed per run from a
    context this has no access to, and hashing the name at least separates
    two different generators.
    """
    if instructions is None:
        return ""
    if isinstance(instructions, str):
        return digest(instructions)
    name = getattr(instructions, "__qualname__", None) or repr(instructions)
    return digest(f"callable:{name}")


def _model_name(model: Any) -> str:
    """The model, as a name that can be written into a transcript.

    A string is the model name and is recorded as given. Anything else is a
    :class:`agents.models.interface.Model` instance, which may hold a
    configured HTTP client and therefore an API key, so nothing is read off
    it wholesale. There is nowhere in :class:`AdapterInfo` to put a
    credential and this is one of the reasons.

    The class name alone was not enough. Two ``LitellmModel`` instances
    pointed at different providers rendered identically, so a field whose
    job is saying what produced a recording said the same thing about two
    different models -- the same defect the reviewer found in the shared
    ``jsonable`` helper, in this module's own spelling of it. A ``.model``
    attribute holding a STRING is the SDK's own convention for the model
    name and is safe to name: it is a model identifier, not a secret, and
    ``AdapterInfo``'s recursive validator refuses anything key-shaped that
    somehow arrives here anyway.
    """
    if isinstance(model, str):
        return model
    if model is None:
        return ""
    named = getattr(model, "model", None)
    if isinstance(named, str) and named:
        return f"{type(model).__name__}:{named}"
    return type(model).__name__


class OpenAIAgentsAdapter(FrameworkAdapter):
    """An OpenAI Agents SDK agent, in the shape ``World`` and ``evaluate`` run.

    ```python
    from agents import Agent
    from tradefloor.integrations.openai_agents import OpenAIAgentsAdapter

    pm = Agent(name="Portfolio Manager",
               instructions="You are a disciplined value investor.",
               model="gpt-5.6-luna")

    agent = OpenAIAgentsAdapter(pm, mode="live", recorder=Transcript())
    scores = tf.evaluate({"pm": agent}, seed=4242, universe=roster, days=5)
    ```

    ``agent`` is the user's :class:`agents.Agent`, taken positionally as
    every adapter here takes its framework object, and never modified; see
    the module docstring. ``mode`` is ``"replay"`` or ``"live"``, and defaults
    to replay for the same reason ``FinRobotAdapter`` does: the mode that
    costs nothing and needs nothing installed is the one to reach by
    accident.

    ``model`` is an :class:`agents.models.interface.Model` instance handed to
    ``RunConfig(model=...)``, which the SDK honours ahead of the agent's own
    model and ahead of its provider lookup. It is how a run happens with no
    network -- pass ``agents.testing.ScriptedModel`` -- and it is what the
    test suite and the shipped example use. Leave it ``None`` and the user's
    agent talks to whatever it was configured to talk to.

    ``max_turns`` bounds one decision. A turn is one model call, so an agent
    with tools spends several per decision and this is what stops a tool loop
    turning one decision point into an open-ended bill. Six is enough for a
    handful of tool round-trips and far short of the SDK's own default of
    ten, which was chosen for interactive use rather than for a loop that
    runs at every cadence step of every arm of an experiment.
    """

    # `agent` is positional-or-keyword and everything after it is
    # keyword-only, which is the shape every adapter in this subpackage
    # takes: `CallableAgentAdapter(fn)`, `PydanticAIAdapter(agent)`,
    # `LangGraphAdapter(runnable)`. This one was keyword-only at first, and
    # `OpenAIAgentsAdapter(llm)` -- the first line anybody writes -- raised
    # TypeError, which no offline test caught because every test passed the
    # agent by keyword and so agreed with the code instead of checking it.
    # It still defaults to None so that `fork()`, which rebuilds the twin as
    # `type(self)(**fork_kwargs())`, can pass it by keyword.
    def __init__(self, agent: Any = None, *, mode: str = "replay",
                 transcript: Any = None, recorder: Any = None,
                 model: Any = None, brief: str = BRIEF, max_turns: int = 6,
                 tracing: bool = False, run_id: str = "",
                 info: AdapterInfo | None = None, every: int = 6,
                 fundamentals: dict[str, dict[str, Any]] | None = None,
                 max_participation: float = MAX_PARTICIPATION,
                 arm: str = "") -> None:
        if mode not in ("replay", "live"):
            raise ValidationError(
                f"mode must be 'replay' or 'live', got {mode!r}")
        if mode == "replay" and transcript is None:
            raise ValidationError(
                "replay mode needs a transcript to replay. Load one with "
                "Transcript.load(path), or pass mode='live' with an agent to "
                "call the SDK.")
        if mode == "live" and agent is None:
            raise ValidationError(
                "live mode needs an agent -- the agents.Agent whose decisions "
                "are being evaluated. Build one with agents.Agent(name=...), "
                "or pass mode='replay' with a transcript.")
        if max_turns < 1:
            raise ValidationError(
                f"max_turns must be >= 1, got {max_turns}. One turn is one "
                "model call, and a decision needs at least one.")

        super().__init__(
            info=info or AdapterInfo(
                framework=DISTRIBUTION,
                framework_version=_installed_version(),
                framework_url=FRAMEWORK_URL,
                # The entry point matters to anyone reproducing a recording,
                # and it is the ASYNC one: `Runner.run_sync` raises inside a
                # running event loop, so a reproduction built on it would
                # work in a script and die in the notebook this fixture is
                # replayed from.
                entry_point=ENTRY_POINT,
                provider="openai" if isinstance(
                    getattr(agent, "model", None), str) else "",
                model=_model_name(getattr(agent, "model", None)),
                agent_name=getattr(agent, "name", "") or "",
                mode=mode,
                # Claimed only when the brief IS this module's. Stamping the
                # version beside somebody else's text would label it with a
                # version it has nothing to do with, and a later mismatch
                # would report two identical versions with different
                # digests -- true, and useless.
                instructions_version=(BRIEF_VERSION if brief == BRIEF
                                      else ""),
                # BOTH halves of what the agent was told: the user's own
                # system prompt and the standing brief. Digesting only the
                # brief would reopen the hole this field exists to close --
                # two arms hand-built with different agent instructions
                # would publish the same identity and ``agree()`` would
                # call them identical. Digesting only the agent's
                # instructions would miss a changed brief, which is exactly
                # what moved this module's own recorded behaviour once.
                instructions_digest=digest({
                    "agent": _instructions_digest(
                        getattr(agent, "instructions", None)),
                    "brief": digest(brief),
                }),
                # The run controls, which change what a decision can be
                # without changing what the agent is. Neither can hold a
                # credential.
                generation={"max_turns": max_turns, "tracing": bool(tracing)},
                # The configuration that changes what a decision is, hashed
                # rather than stored. Two arms proving they ran the same
                # setup is what this is for, and a stored config would
                # eventually be a stored API key.
                config_digest=digest({
                    "brief": brief, "every": every, "max_turns": max_turns,
                    "max_participation": max_participation,
                    "model": _model_name(getattr(agent, "model", None)),
                }),
                extra={"recorded": recorder is not None}),
            every=every, fundamentals=fundamentals,
            max_participation=max_participation, arm=arm)

        self.agent = agent
        self.mode = mode
        self.transcript = transcript
        self.recorder = recorder
        self.model = model
        self.brief = brief
        self.max_turns = int(max_turns)
        self.tracing = bool(tracing)
        self.run_id = run_id
        #: The cloned agent carrying the bound output type, built on the
        #: first live call and reused. Derived, so it is rebuilt rather than
        #: copied by a fork, and it is not in `fork_kwargs`.
        self._bound: Any = None

        self._check_instructions()

    def _check_instructions(self) -> None:
        """Refuse a replay whose recording ran under other instructions.

        Called from the constructor rather than from :meth:`act`: everything
        this needs is known before the market opens, and a replay that
        refuses twenty simulated days in has already spent the reader's
        time on a fault that was knowable at the start.

        What a key over the input cannot see is a change to something that
        never enters it. The replay key is a digest of the brief and the
        observation, and the user's agent carries its OWN instructions,
        which reach the model as ``system_instructions`` and never appear
        in the keyed input. So swapping the agent entirely leaves every
        recorded key intact: every lookup hits, the run completes, and the
        decisions replayed were taken by an agent nobody is running any
        more. Measured before this guard existed -- an agent instructed
        "BUY EVERYTHING" and one instructed "SELL EVERYTHING" produced
        byte-identical replay keys. That is a property of any adapter whose
        **instructions travel separately from the keyed input**, not an
        Agents SDK quirk, and the other integrations in this package share
        it.

        The brief is the other half and needs no guard here: it IS in the
        keyed input, so editing it makes every key miss and the replay
        refuses on its own. It is folded into ``instructions_digest``
        anyway, which only makes this check fire earlier and more
        specifically than the missing-key one would.

        Only a MISMATCH is refused, and only when an agent was actually
        configured. A transcript that records no digest never claimed
        anything, so it is not known to be wrong, and refusing it would
        break hand-written fixtures for no safety gain. Replay WITHOUT an
        agent is the ordinary path -- it needs no SDK and no key -- and it
        asserts nothing about whose instructions produced the recording, so
        there is nothing to contradict.
        """
        if self.transcript is None or self.agent is None:
            return
        recorded = (self.transcript.meta or {}).get("instructions_digest")
        if not recorded or recorded == self.info.instructions_digest:
            return
        was = (self.transcript.meta or {}).get("instructions_version")
        raise DecisionError(
            "this transcript was recorded under different instructions: it "
            f"carries instructions_digest {recorded} and this adapter is "
            f"configured with {self.info.instructions_digest} (recorded "
            f"under brief version {was or 'custom'}, configured with "
            f"{self.info.instructions_version or 'custom'}).\n"
            "The digest covers the agent's own instructions AND the standing "
            "brief. The agent's instructions never enter the replay key -- "
            "they reach the model as system_instructions -- so every lookup "
            "would still hit and the run would complete, answering for this "
            "agent with decisions another one made.\n"
            "Either pass the agent the recording was made with, or drop the "
            "agent argument to replay it as a recording rather than as this "
            "agent's run: OpenAIAgentsAdapter(mode='replay', "
            "transcript=...).")

    # -- the one framework-specific method --------------------------------

    def ask(self, obs: Any, payload: dict[str, Any]) -> Any:
        """One decision for this payload, from the SDK or from a recording.

        Both modes go through the same input construction, so a replay is
        keyed by the exact input a live run would have sent. Change the brief
        or the observation mapping and the digest changes, the recorded key
        goes missing, and the replay RAISES naming the step instead of
        answering this question with a response given to a different one.
        The key is a digest of the INPUT and never of a position: keyed by
        (arm, step) this would pass every test it has and its studies would
        answer new questions with old recorded answers.

        The exchange is declared before either branch, so the record carries
        the same input and the same key whether the response came from the
        SDK or from a recording -- which is what lets a replayed record be
        compared with the live one it came from.

        The response is a JSON STRING, one level deep, in both branches. That
        matters: the recorder writes it verbatim and a replay hands the same
        string to ``parse_decision``, so the two paths parse identical input.
        A response that had itself been ``json.dumps``-ed would round-trip
        into a string of a string and fail at REPLAY time, against a
        recording that looked correct when it was written.
        """
        items = self.input_items(payload)
        key = digest(items)
        self.record_exchange(items, key=key)

        if self.mode == "replay":
            return replay_response(self.transcript, key, step=obs.step,
                                   day=obs.day)

        response = self._run(items, obs)
        if self.recorder is not None:
            # Stamp the provenance on the first write rather than leaving it
            # to the caller. `_check_instructions` can only refuse a
            # mismatched replay if the recording says what it ran under, and
            # a guard that arms itself only when somebody remembers to set
            # `meta` is off in exactly the runs nobody was careful about.
            # Every recording this adapter makes now carries the digest to
            # compare against tomorrow. An explicit `meta` is respected:
            # keys already there win, so a caller who set their own
            # provenance keeps it.
            if "instructions_digest" not in self.recorder.meta:
                for field, value in self.provenance().items():
                    self.recorder.meta.setdefault(field, value)
            self.recorder.record({
                "arm": self.arm, "step": obs.step, "day": obs.day,
                "digest": key, "prompt": items, "response": response,
            })
        return response

    # -- observation -> the SDK -------------------------------------------

    def input_items(self, payload: dict[str, Any]) -> list[dict[str, str]]:
        """The run input: the brief, then the payload as JSON.

        Two messages rather than one concatenated string, and the payload
        message is JSON and nothing else. That keeps the observation exactly
        recoverable -- :func:`payload_of` reads it straight back, so a
        scripted model decides from the same dict a real one is shown --
        and it keeps the brief separable from the data for anyone reading a
        transcript.

        Sorted keys, so the digest that keys a replay depends on the
        observation and not on the order a dict happened to be built in.
        """
        import json

        return [
            {"role": "user", "content": self.brief},
            {"role": "user",
             "content": json.dumps(payload, sort_keys=True, indent=2,
                                   default=float)},
        ]

    # -- the SDK -----------------------------------------------------------

    def _bind(self) -> Any:
        """The user's agent with the decision contract bound, built once.

        ``clone`` rather than assignment, so the user's own agent keeps the
        output type it had -- which for almost every agent is none at all.
        An agent that already declares one is REFUSED rather than
        overridden: it was built to return something specific, and quietly
        replacing that would discard a contract its author is relying on
        elsewhere.
        """
        if self._bound is not None:
            return self._bound

        model = decision_model()
        declared = getattr(self.agent, "output_type", None)
        if declared is not None and declared is not model:
            raise ValidationError(
                f"the agent {getattr(self.agent, 'name', '')!r} already "
                f"declares output_type={declared!r}, and this adapter binds "
                "the Tradefloor decision contract as the output type. "
                "Overriding it would discard a contract the agent was built "
                "around. Pass an agent without an output_type, or set it to "
                "tradefloor.integrations.common.decision_model() yourself.")
        if not hasattr(self.agent, "clone"):
            raise ValidationError(
                f"agent must be an agents.Agent, got "
                f"{type(self.agent).__name__}, which has no clone(). The "
                "adapter clones the agent to bind the decision contract "
                "without altering the original.")

        self._bound = self.agent.clone(output_type=model)
        return self._bound

    def _run_config(self, sdk: Any, obs: Any) -> Any:
        """The per-run configuration: the model override and tracing.

        Per run rather than through the SDK's process-global switches, so
        nothing here changes the behaviour of other code sharing the
        process. See the module docstring on why tracing defaults off.
        """
        if not self.tracing:
            return sdk.RunConfig(model=self.model, tracing_disabled=True)
        return sdk.RunConfig(
            model=self.model,
            tracing_disabled=False,
            workflow_name="tradefloor",
            group_id=self.run_id or None,
            trace_metadata={
                "step": obs.step, "day": obs.day, "arm": self.arm,
                "run_id": self.run_id,
                "decision_schema_version":
                    self.info.decision_schema_version,
            })

    def _run(self, items: list[dict[str, str]], obs: Any) -> str:
        """One real SDK run, returned as the decision in JSON.

        The framework is imported HERE and nowhere earlier, so replaying a
        recorded run -- and ``import tradefloor`` -- never need the SDK
        installed. See this subpackage's ``__init__`` for the rule.
        """
        sdk = require(
            "agents", extra=EXTRA,
            purpose="live mode needs the OpenAI Agents SDK")
        bound = self._bind()
        where = f"step {obs.step} (day {obs.day})"

        try:
            result = run_sync(sdk.Runner.run(
                bound, items, max_turns=self.max_turns,
                run_config=self._run_config(sdk, obs)))
        except sdk.ModelRefusalError as exc:
            raise DecisionError(
                f"the model refused to produce a decision at {where}: "
                f"{exc.refusal}. A refusal is not a HOLD -- scoring it as one "
                "would record a considered choice the agent never made."
            ) from exc
        except sdk.MaxTurnsExceeded as exc:
            raise DecisionError(
                f"the agent used its whole turn budget at {where} without "
                f"producing a decision ({exc}). A turn is one model call; "
                f"raise max_turns above {self.max_turns} if the agent needs "
                "more tool round-trips, or look at why it is not converging."
            ) from exc
        except (sdk.InputGuardrailTripwireTriggered,
                sdk.OutputGuardrailTripwireTriggered,
                sdk.ToolInputGuardrailTripwireTriggered,
                sdk.ToolOutputGuardrailTripwireTriggered) as exc:
            raise DecisionError(
                f"a guardrail on the agent stopped the run at {where}: "
                f"{exc}. The guardrail is the agent's own, so this is the "
                "agent declining to answer rather than the framework "
                "failing, and the decision point produced nothing."
            ) from exc
        except sdk.ModelBehaviorError as exc:
            raise DecisionError(
                f"the SDK rejected the model's output at {where}: {exc}. The "
                "bound output type is the Tradefloor decision contract -- a "
                "JSON object with an 'actions' list, each action naming a "
                "symbol, a side and a non-negative quantity -- and the SDK "
                "validates against it before this adapter sees anything. The "
                "SDK redacts model text from its own errors by default; set "
                "OPENAI_AGENTS_DONT_LOG_MODEL_DATA=0 to see what was "
                "actually returned.") from exc

        return self._output_of(result, where)

    def _output_of(self, result: Any, where: str) -> str:
        """The run's final output as JSON, or a refusal naming why not.

        Unwrapping the framework's envelope is the adapter's job:
        ``RunResult`` is the envelope and ``final_output`` is the decision.
        It is normally an instance of ``decision_model()``, because that is
        what was bound -- but a run that HANDS OFF ends on a different
        agent, with that agent's output type, and the bound contract does
        not follow the handoff. That case is refused here rather than
        allowed to reach ``parse_decision`` as some unrelated object.
        """
        import json

        final = result.final_output
        model = decision_model()
        if isinstance(final, model):
            return final.model_dump_json()
        if isinstance(final, str):
            # A plain-text final output, which means the bound type was not
            # in force. parse_decision still gets its chance -- it reads a
            # JSON object out of prose -- and refuses readably if there is
            # no decision in there.
            return final
        if isinstance(final, dict):
            return json.dumps(final)

        ended_on = getattr(getattr(result, "last_agent", None), "name", "")
        raise DecisionError(
            f"the run at {where} ended with {type(final).__name__} instead of "
            f"a decision, on agent {ended_on!r}. The decision contract is "
            "bound to the agent this adapter was given; a handoff ends on a "
            "different agent carrying its own output type, and that agent's "
            "answer is not a Tradefloor decision. Evaluate the sub-agent "
            "directly, or give the handoff target the same output type.")

    # -- fork ---------------------------------------------------------------

    def fork_kwargs(self) -> dict[str, Any]:
        """The constructor arguments a fork is rebuilt with.

        The user's ``agent`` is SHARED, not copied, and so are the transcript
        and the recorder. Sharing the agent is safe precisely because this
        module never mutates it, and copying one would clone whatever HTTP
        client its model settings hold -- wasteful at best, a shared socket
        at worst. Both directions want the transcript shared too: a replay of
        one arm must read the same recorded run as the other, and a live
        recording of both arms belongs in one file.

        ``_bound`` is deliberately absent. It is derived from ``agent``, so
        the twin rebuilds it on its first live call and the two cannot
        disagree about what was bound.
        """
        kwargs = super().fork_kwargs()
        kwargs.update({
            "agent": self.agent, "mode": self.mode,
            "transcript": self.transcript, "recorder": self.recorder,
            "model": self.model, "brief": self.brief,
            "max_turns": self.max_turns, "tracing": self.tracing,
            "run_id": self.run_id,
        })
        return kwargs

    def state(self) -> dict[str, Any]:
        """What a fork has to agree on, plus what this adapter adds to it.

        The base publishes the price memory, the last decision and the
        cadence. The two additions are the turn budget and the digest of the
        brief: both change what a decision can be, so two arms differing in
        either are not running the controlled comparison they claim to be.

        The agent, the model and the transcript stay out. The agent's model
        settings can hold an API key, and this dictionary is printed by the
        fork agreement and written into artifacts.
        """
        published = super().state()
        published["max_turns"] = self.max_turns
        published["brief_digest"] = digest(self.brief)
        return published

    def __repr__(self) -> str:
        return (f"OpenAIAgentsAdapter(mode={self.mode!r}, "
                f"agent={getattr(self.agent, 'name', None)!r}, "
                f"arm={self.arm!r}, every={self.every}, "
                f"decisions={len(self.record)})")


def openai_agent(agent: Any, **kwargs: Any) -> OpenAIAgentsAdapter:
    """A live :class:`OpenAIAgentsAdapter` around ``agent``.

    The convenience spelling for the common case, where the agent is the
    whole configuration and the run is live:

    ```python
    agent = openai_agent(pm, recorder=Transcript())
    ```

    Keyword arguments pass through, so ``mode`` can still be overridden.
    """
    kwargs.setdefault("mode", "live")
    return OpenAIAgentsAdapter(agent=agent, **kwargs)
