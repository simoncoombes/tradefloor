"""Run a plain Python function as if it were a framework agent.

The reference implementation of the adapter contract in ``common.py``, and
the smallest thing that exercises all of it: the observation allowlist, the
two-stage validation, the cadence, the record, and the four methods both
harnesses look for. The framework adapters -- FinRobot, and the ones under
construction beside it -- are this shape with :meth:`ask` pointed at
something that costs money.

It is also useful in its own right, twice over. A decision RULE written as
one function gets, for free, everything an adapter provides: participation
clipping, dust dropping, the fork and state hooks that make it runnable in a
counterfactual, and a record of every decision it made. And an experiment
comparing a framework agent against a hand-written baseline wants both sides
going through the SAME validation path, or the comparison is partly
measuring two validators.

## The function receives the serialized payload, not the Observation

``fn`` is called with the :func:`~tradefloor.integrations.common.serialize_observation`
output -- a JSON-able dict -- and never with the Observation itself. The
Observation carries ``.engine``: a read-only market view by default, the
live engine, which knows the answer key, under ``trusted_agents=True``. A
function handed the Observation could read ``obs.engine.macro_state`` today
and, in a trusted run, ``obs.engine.attribution`` tomorrow, and nothing in
the allowlist test would see it. Handed the payload, the
function can only decide from what a framework would be shown, which is
what makes it an honest baseline for one. A policy that genuinely needs the
Observation is not an integration; it is a native agent, and it implements
``act`` directly.

## Record and replay, through the shared mixin

The adapter takes :class:`~tradefloor.integrations.common.ReplayMixin`, so
it is also the reference implementation of the replay skeleton: attach a
``recorder`` and every exchange is written down; pass ``mode="replay"``
with that transcript and the run reproduces without the function ever
being called. For a deterministic rule that buys nothing, which is why
``mode`` defaults to "live" here where a framework adapter defaults to
"replay" -- but a callable wrapping something non-deterministic (a local
model, say) gets record-once-replay-forever exactly as FinRobot does.

A replay needs no function at all, so ``fn`` may be left out in replay
mode:

```python
agent = CallableAgentAdapter(mode="replay",
                             transcript=Transcript.load("run.json"))
```

The key is the payload and nothing else, so a new system prompt inside
``fn`` does not change it. Put the prompt's digest in the adapter's
``AdapterInfo(instructions_digest=digest(PROMPT))`` when recording and when
replaying, and a replay under a different prompt is refused at
construction. A recorder whose ``meta`` does not name its instructions yet
is given the adapter's provenance on its first write, so the digest reaches
the file without a manual ``meta.update``.

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

Tradefloor's run loop is synchronous -- ``World.run`` and ``evaluate`` call
``act`` inline -- and an async ``fn`` is supported by handing its coroutine
to :func:`~tradefloor.integrations.common.run_sync`, the one bridge every
adapter uses. The bridge, not a local answer, because the failure it guards
against only shows up in a notebook: a naive ``asyncio.run`` works in a
script and dies inside Jupyter's already-running loop, and four adapters
solving that four ways is four chances to get it subtly wrong. What the
bridge does and does not buy is documented on ``run_sync`` itself; the short
version is that the market still waits for every decision, one at a time.
"""

from __future__ import annotations

import inspect
from typing import Any, Callable

from .._core import ValidationError
from .common import (MAX_PARTICIPATION, AdapterInfo, FrameworkAdapter,
                     ReplayMixin, Transcript, run_sync)


class CallableAgentAdapter(ReplayMixin, FrameworkAdapter):
    """A user function, in the shape :class:`World` and ``evaluate`` run.

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
    :class:`~tradefloor.integrations.common.DecisionError`, exactly as it
    would from a framework -- this adapter repairs nothing, because its
    other job is being the baseline a framework is compared against.

    ``fn`` may be None in replay mode, which never calls it. A live run
    needs it, and so does ``reask``.

    ``postprocess``, when given, is called as ``postprocess(raw, payload)``
    on whatever ``fn`` returned, in both modes, and ITS return is the
    decision. ``fn``'s return is then the raw model response, and that is
    what the transcript records and a replay hands back. See the module
    docstring for when to use it. Either function may be async.
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

    The convenience spelling, for the common case where the function is the
    whole configuration:

    ```python
    agent = callable_agent(momentum, every=6)
    ```

    Keyword arguments pass through to :class:`CallableAgentAdapter`.
    """
    return CallableAgentAdapter(fn, **kwargs)
