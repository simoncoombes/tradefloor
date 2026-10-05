"""Run a plain Python function as if it were a framework agent.

This is the reference implementation of the adapter contract in ``common.py``,
and the smallest adapter that exercises all of it: the observation allowlist,
the two-stage validation, the cadence, the record, and the four methods both
harnesses look for. FinRobot and the other framework adapters have the same
shape, with :meth:`ask` calling something that costs money.

It has two uses of its own. A decision rule written as one function gets
everything an adapter provides: participation clipping, dust dropping, the
fork and state hooks that make it runnable in a counterfactual, and a record
of every decision it made. And an experiment comparing a framework agent
against a hand-written baseline needs both sides to go through the same
validation path, or the comparison partly measures the difference between two
validators.

## The function receives the serialized payload, not the Observation

``fn`` is called with the
:func:`~tradefloor.integrations.common.serialize_observation` output, a
JSON-able dict, and never with the Observation itself. The Observation carries
``.engine``, which is a read-only market view by default and, under
``trusted_agents=True``, the live engine, which knows the answer key. A
function handed the Observation could read ``obs.engine.macro_state``, or
``obs.engine.attribution`` in a trusted run, and nothing in the allowlist test
would see it. Handed the payload, the function can only decide from what a
framework would be shown, so it is a fair baseline for one. A policy that
needs the Observation should be a native agent that implements ``act``
directly.

## Record and replay, through the shared mixin

The adapter uses :class:`~tradefloor.integrations.common.ReplayMixin`, so it
is also the reference implementation of the replay skeleton. Attach a
``recorder`` and every exchange is written down. Pass ``mode="replay"`` with
that transcript and the run reproduces without calling the function. A
deterministic rule gains nothing from this, which is why ``mode`` defaults to
"live" here while a framework adapter defaults to "replay". A callable
wrapping something non-deterministic, such as a local model, can be recorded
once and replayed any number of times, as FinRobot is.

A replay needs no function at all, so ``fn`` may be left out in replay
mode:

```python
agent = CallableAgentAdapter(mode="replay",
                             transcript=Transcript.load("run.json"))
```

The replay key is computed from the payload alone, so a new system prompt
inside ``fn`` does not change it. Put the prompt's digest in the adapter's
``AdapterInfo(instructions_digest=digest(PROMPT))`` when recording and when
replaying, and a replay under a different prompt is refused at construction.
Replaying a transcript that names a prompt digest with an adapter that names
none (no ``AdapterInfo``, or one without ``instructions_digest``) warns that
this check is off. A recorder whose ``meta`` does not name its instructions
yet is given the adapter's provenance on its first write, so the digest
reaches the file without a manual ``meta.update``.

## Code that runs after the model answers

Whatever ``fn`` returns is what gets recorded, and a replay hands that back
without running ``fn``. Code inside ``fn`` after the model call (parsing a
tool call, a risk check, sizing) therefore runs live and never on replay.
Split it out as ``postprocess``:

```python
def ask_model(payload):
    return call_my_model(json.dumps(payload))      # recorded, skipped on replay

def size(raw, payload):
    decision = parse_my_tool_call(raw)
    return cap_to_buying_power(decision, payload)  # runs in both modes

agent = CallableAgentAdapter(ask_model, postprocess=size, mode="live",
                             recorder=Transcript())
```

``fn``'s return, the raw response, is what the transcript and the record
hold. ``postprocess(raw, payload)`` then runs in both modes and returns the
decision. Without ``postprocess`` nothing changes, and recordings made
before it existed replay as they did.

## Async goes through the one shared bridge

Tradefloor's run loop is synchronous (``World.run`` and ``evaluate`` call
``act`` inline). An async ``fn`` is supported by handing its coroutine to
:func:`~tradefloor.integrations.common.run_sync`, the bridge every adapter
uses. The shared bridge matters because the failure it guards against only
shows up in a notebook. A plain ``asyncio.run`` works in a script and fails
inside Jupyter's already-running loop, and four adapters solving that four
different ways would be four chances to get it wrong. ``run_sync`` documents
what the bridge does and does not do. In short, the market still waits for
every decision, one at a time.
"""

from __future__ import annotations

import inspect
import warnings
from typing import Any, Callable

from .._core import ValidationError
from .common import (MAX_PARTICIPATION, AdapterInfo, FrameworkAdapter,
                     ReplayMixin, Transcript, run_sync)


class CallableAgentAdapter(ReplayMixin, FrameworkAdapter):
    """A user function run as an agent by :class:`World` and ``evaluate``.

    ```python
    def momentum(payload):
        orders = []
        for asset in payload["assets"]:
            if asset["return_5d"] and asset["return_5d"] < -0.02:
                orders.append({"symbol": asset["symbol"], "side": "BUY",
                               "quantity": asset["max_order_shares"]})
        return {"actions": orders, "rationale": "buy what fell"}

    agent = CallableAgentAdapter(momentum)
    scores = tf.evaluate({"momentum": agent}, seed=7, universe=roster)
    ```

    ``fn`` takes the serialized observation payload and returns anything
    :func:`~tradefloor.integrations.common.parse_decision` accepts: a
    :class:`~tradefloor.integrations.common.Decision`, a dict with an
    ``actions`` list, or a JSON string. Invalid output raises
    :class:`~tradefloor.integrations.common.DecisionError`, as it would from a
    framework. This adapter repairs nothing, because it is also the baseline a
    framework is compared against.

    ``fn`` may be None in replay mode, which never calls it. A live run
    needs it, and so does ``reask``.

    ``postprocess``, when given, is called as ``postprocess(raw, payload)`` on
    whatever ``fn`` returned, in both modes, and its return is the decision.
    ``fn``'s return is then the raw model response, and that is what the
    transcript records and a replay hands back. See the module docstring for
    when to use it. Either function may be async.
    """

    def __init__(self, fn: Callable[[dict[str, Any]], Any] | None = None, *,
                 name: str = "", info: AdapterInfo | None = None,
                 every: int = 6,
                 fundamentals: dict[str, dict[str, Any]] | None = None,
                 max_participation: float = MAX_PARTICIPATION,
                 arm: str = "", mode: str = "live",
                 transcript: Transcript | None = None,
                 recorder: Transcript | None = None,
                 prior: Transcript | None = None,
                 postprocess: Callable[[Any, dict[str, Any]], Any] | None
                 = None) -> None:
        # `fn` defaults to None rather than being required, so the refusal
        # below owns the message; a bare TypeError from the signature would
        # not say what a valid `fn` looks like. A replay never calls it, so
        # replay mode accepts None, as OpenAIAgentsAdapter accepts no agent
        # there; it was refused until 0.8.5, and users passed a function
        # that raised if called. Anything else that is not callable is
        # still refused in either mode.
        if not (callable(fn) or (fn is None and mode == "replay")):
            raise ValidationError(
                f"CallableAgentAdapter wraps a callable, got "
                f"{type(fn).__name__}. Pass a function taking the serialized "
                "observation payload and returning a decision. Only "
                "mode='replay' can run without one.")
        if postprocess is not None and not callable(postprocess):
            raise ValidationError(
                f"postprocess must be a function taking (raw, payload), got "
                f"{type(postprocess).__name__}.")
        # `mode` defaults to "live", unlike a framework adapter's "replay":
        # calling a local function is free and deterministic callables are
        # the common case, so the recording machinery is opt-in here and
        # the default just runs the function.
        super().__init__(
            mode=mode, transcript=transcript, recorder=recorder,
            prior=prior,
            info=info or AdapterInfo(
                framework="callable",
                agent_name=name or getattr(fn, "__name__", "")),
            every=every, fundamentals=fundamentals,
            max_participation=max_participation, arm=arm)
        self.fn = fn
        self.name = name
        self.postprocess = postprocess
        recorded = ((transcript.meta or {}).get("instructions_digest")
                    if mode == "replay" and transcript is not None else None)
        if recorded and not self.info.instructions_digest:
            warnings.warn(
                f"this transcript was recorded under instructions digest "
                f"{recorded}, and this adapter names no instructions, so the "
                "check that refuses a replay under a changed prompt is off. "
                "Pass info=AdapterInfo(framework='callable', "
                "instructions_digest=digest(PROMPT)) with the prompt fn "
                "sends to turn it on.", UserWarning, stacklevel=2)

    def prepare(self, obs: Any, payload: dict[str, Any]) -> tuple[Any, Any]:
        # The payload IS both the key material and the input: nothing is
        # rendered between the serializer and the function, so the replay
        # key is the canonical-JSON digest of exactly what fn receives.
        return payload, payload

    def call(self, obs: Any, prompt: dict[str, Any]) -> Any:
        if self.fn is None:
            raise ValidationError(
                "this CallableAgentAdapter was built for replay with no "
                "function, so it has nothing to call. Build it with the "
                "function to make a live call or a reask.")
        return _settled(self.fn(prompt))

    def interpret(self, response: Any, payload: dict[str, Any]) -> Any:
        if self.postprocess is None:
            return response
        return _settled(self.postprocess(response, payload))

    def fork_kwargs(self) -> dict[str, Any]:
        kwargs = super().fork_kwargs()
        # The function itself is SHARED, not copied. It is the policy, the
        # thing both arms must agree on, and a deep copy of a closure over a
        # client or a file handle is exactly the hazard fork() exists to
        # avoid. The same goes for postprocess.
        kwargs["fn"] = self.fn
        kwargs["name"] = self.name
        kwargs["postprocess"] = self.postprocess
        return kwargs


def _settled(out: Any) -> Any:
    """``out``, or what it resolves to if a user function returned a coroutine.

    The RESULT is checked rather than the function: a partial or a wrapper
    carries a coroutine function past any constructor check. run_sync is
    the one shared bridge -- see the module docstring -- and it works
    whether or not this thread already has a running event loop.
    """
    return run_sync(out) if inspect.iscoroutine(out) else out


def callable_agent(fn: Callable[[dict[str, Any]], Any] | None = None,
                   **kwargs: Any) -> CallableAgentAdapter:
    """A :class:`CallableAgentAdapter` around ``fn``.

    Shorthand for the common case where the function is the whole
    configuration:

    ```python
    agent = callable_agent(momentum, every=6)
    ```

    Keyword arguments pass through to :class:`CallableAgentAdapter`.
    """
    return CallableAgentAdapter(fn, **kwargs)
