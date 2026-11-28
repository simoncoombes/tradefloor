"""Run a Claude model through the Anthropic Python SDK inside a Tradefloor market.

The Anthropic Python SDK (https://github.com/anthropics/anthropic-sdk-python,
MIT) is the client for Anthropic's Messages API. This module is a Tradefloor
integration for it. There is no affiliation with or endorsement by Anthropic,
and nothing here is an official SDK interface. Developed against
``anthropic`` 2.14.0.

    Tradefloor observation
            |
      ClaudeAdapter          serialize_observation -> brief + JSON payload
            |
      messages.create        the user's client, model and system prompt,
            |                the decision schema as the output format
      ClaudeAdapter          JSON text -> parse_decision -> share deltas
            |
    Tradefloor execution

Everything except the two hooks below is
:class:`~tradefloor.integrations.common.FrameworkAdapter` and
:class:`~tradefloor.integrations.common.ReplayMixin`: the price memory, the
cadence, the observation allowlist, the two-stage validation, the record,
the replay key and the fork. ``callable.py`` is the reference for that
shape, and this module follows it.

## One request per decision

``prepare`` renders the request once. The user's ``system`` prompt is sent
as given, and :data:`BRIEF` and the JSON payload travel as two text blocks
of one user turn, so the brief never overwrites the user's instructions. The
replay key is the digest of the model and the messages. The system prompt and
the brief are checked separately through ``instructions_digest``, which
:class:`~tradefloor.integrations.common.ReplayMixin` compares at
construction.

## The decision schema is the output format

``call`` sends ``common.decision_schema()`` as
``output_config={"format": {"type": "json_schema", ...}}``. Forced tool use
is the older way to get JSON back, and current Claude models refuse
``tool_choice`` of type ``any`` or ``tool`` with a 400, so this module does
not use it. The schema goes through the SDK's own ``transform_schema`` first,
which moves the constraints the API does not accept (``minLength``,
``minimum``, ``exclusiveMinimum``) into the descriptions. Those constraints
are enforced again by :func:`~tradefloor.integrations.common.parse_decision`
on the way back, so nothing the schema promised is lost.

## The recorded response

``call`` returns the text of the response, the JSON decision, so the
transcript holds a string that the shared validator reads, as every other
adapter's does. A ``refusal`` stop, a ``max_tokens`` stop and a response with
no text block each raise
:class:`~tradefloor.integrations.common.DecisionError` inside ``call``,
naming the reason, so none of them is scored as a considered hold. A raised
call is not recorded, as on the OpenAI Agents adapter.

## What is not verified here

The tests drive the real SDK client through an HTTP transport that answers
locally, so the request the SDK builds and the response it parses are both
checked with no network. No request from this module has been sent to the
Anthropic API. Whether the API accepts the transformed schema is therefore
not verified.
"""

from __future__ import annotations

import json
from typing import Any

from .._core import ValidationError
from .common import (MAX_PARTICIPATION, AdapterInfo, DecisionError,
                     FrameworkAdapter, ReplayMixin, Transcript,
                     decision_schema, digest, require)

#: The PyPI distribution that installs the SDK, and the Tradefloor extra
#: that pulls it in: ``pip install "tradefloor[claude]"``. The extra was
#: there before this module, for ``examples/08-claude-agent.py``.
DISTRIBUTION = "anthropic"
EXTRA = "claude"

FRAMEWORK = "anthropic-sdk"
FRAMEWORK_URL = "https://github.com/anthropics/anthropic-sdk-python"
ENTRY_POINT = "anthropic.Anthropic.messages.create"

#: ``max_tokens`` when the caller names none. A decision is a short JSON
#: object, but adaptive thinking counts against the same limit, and a
#: response cut off by it is refused rather than parsed.
MAX_TOKENS = 16_000

#: Version of :data:`BRIEF`, recorded in ``AdapterInfo.instructions_version``.
#: A changed brief is a different experiment. ``instructions_digest`` tells
#: two briefs apart, and this number tells a reader which one without a diff.
BRIEF_VERSION = "1"

#: The standing brief, the first text block of every request.
#:
#: It says what only this market can say: the instruments are synthetic, the
#: payload is the only data, and two separate limits bound size. It says
#: nothing about what is coming, so both arms of a counterfactual receive the
#: same brief. The output shape is not described here, because the schema
#: is sent as the output format and a second statement of it in prose could
#: disagree with the first.
BRIEF = """\
You are trading a simulated market. Every instrument is synthetic: the \
tickers, the sectors and the fundamentals are generated, so anything you \
know about real listed companies does not apply here.

The next text block is the entire observation, as JSON. You have no other \
data source. Where a field is null it is unknown: a five-day return is null \
until five days have been observed, and null is not zero.

Seek attractive risk-adjusted returns while controlling downside risk. You \
may buy, sell, resize or keep positions, and you are not required to trade.

Two separate limits bound your size, and you must respect both. \
`max_order_shares` is what the market can absorb in one order; a larger \
request is clipped, and the clip is recorded. `portfolio.buying_power` is \
the additional gross notional the book can fund before the leverage cap \
refuses the order. Either can be the tighter one, so check both.
"""


def _installed_version() -> str:
    """The installed ``anthropic`` version, read from metadata, not imported.

    A replay reaches this and must not import the SDK, which may not be
    installed.
    """
    import importlib.metadata
    try:
        return importlib.metadata.version(DISTRIBUTION)
    except Exception:                                    # noqa: BLE001
        # An unknown version is not a reason to refuse a run.
        return ""


def instructions_digest(system: str, brief: str = BRIEF) -> str:
    """The digest of what the model is told before the payload.

    Both the user's system prompt and the brief, because a replay under
    either one changed answers a different question.
    """
    return digest({"system": system, "brief": brief,
                   "brief_version": BRIEF_VERSION})


def output_schema() -> dict[str, Any]:
    """``decision_schema()`` in the form the Messages API accepts.

    Uses the SDK's ``transform_schema``, so a live call needs an SDK release
    that exports it.
    """
    sdk = require(DISTRIBUTION, extra=EXTRA,
                  purpose="a live Claude decision needs the Anthropic SDK")
    transform = getattr(sdk, "transform_schema", None)
    if transform is None:
        raise ValidationError(
            f"anthropic {getattr(sdk, '__version__', '?')} does not export "
            "transform_schema, which this adapter uses to send the decision "
            "schema as the output format. Upgrade the SDK: "
            'pip install --upgrade anthropic')
    schema = dict(decision_schema())
    # Keywords that describe the document rather than constrain it. The API
    # takes the schema object itself, not a standalone JSON Schema file.
    schema.pop("$schema", None)
    schema.pop("title", None)
    return transform(schema)


def payload_of(request: dict[str, Any]) -> dict[str, Any]:
    """The serialized observation, read back from a request this adapter built.

    For a stand-in client in a test or an offline example: it receives the
    keyword arguments of ``messages.create`` and can decide from the same
    payload a model would see. ``request`` is those keyword arguments, or the
    recorded ``prompt`` of a transcript entry.
    """
    try:
        blocks = request["messages"][-1]["content"]
        return json.loads(blocks[-1]["text"])
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise ValidationError(
            "this request was not built by ClaudeAdapter, whose last user "
            "turn ends with the JSON observation as a text block: "
            f"{exc}") from exc


class ClaudeAdapter(ReplayMixin, FrameworkAdapter):
    """A Claude model, through the Anthropic SDK, as an agent.

    ```python
    import anthropic
    from tradefloor.integrations.claude import ClaudeAdapter
    from tradefloor.integrations import Transcript

    agent = ClaudeAdapter(anthropic.Anthropic(), model="claude-opus-5-5",
                          mode="live", recorder=Transcript())
    scores = tf.evaluate({"claude": agent}, seed=7, universe=roster)
    ```

    ``client`` is anything with ``messages.create`` that takes the Messages
    API's keyword arguments: an ``anthropic.Anthropic``, a client for another
    platform, or a stand-in. Left out in live mode, ``anthropic.Anthropic()``
    is built on the first call, which reads credentials from the environment.
    A replay never builds one.

    ``model`` is required in live mode and names no default, because a model
    id written into a library goes stale. It is also part of the replay key,
    so a recording made with one model does not answer for another.

    ``system`` is the user's own system prompt, sent unchanged. ``effort``,
    when given, is sent as ``output_config["effort"]``. ``mode`` defaults to
    "replay", as on every framework adapter, so a run never spends money
    unless somebody passed ``mode="live"``.
    """

    def __init__(self, client: Any = None, *, model: str = "",
                 system: str = "", max_tokens: int = MAX_TOKENS,
                 effort: str | None = None, name: str = "",
                 info: AdapterInfo | None = None, every: int = 6,
                 fundamentals: dict[str, dict[str, Any]] | None = None,
                 max_participation: float = MAX_PARTICIPATION,
                 arm: str = "", mode: str = "replay",
                 transcript: Transcript | None = None,
                 recorder: Transcript | None = None,
                 prior: Transcript | None = None) -> None:
        if mode == "live" and not model:
            raise ValidationError(
                "live mode needs model=, the Claude model id to call. There "
                "is no default, because a default model id goes stale.")
        if max_tokens < 1:
            raise ValidationError(
                f"max_tokens must be 1 or more, got {max_tokens}")
        generation: dict[str, Any] = {"max_tokens": int(max_tokens)}
        if effort is not None:
            generation["effort"] = effort
        super().__init__(
            mode=mode, transcript=transcript, recorder=recorder,
            prior=prior,
            info=info or AdapterInfo(
                framework=FRAMEWORK, framework_version=_installed_version(),
                provider="anthropic", model=model, agent_name=name,
                entry_point=ENTRY_POINT, framework_url=FRAMEWORK_URL,
                instructions_version=BRIEF_VERSION,
                instructions_digest=instructions_digest(system),
                generation=generation),
            every=every, fundamentals=fundamentals,
            max_participation=max_participation, arm=arm)
        self.client = client
        self.model = model
        self.system = system
        self.max_tokens = int(max_tokens)
        self.effort = effort
        self.name = name

    # -- the ReplayMixin hooks --------------------------------------------

    def prepare(self, obs: Any, payload: dict[str, Any]) -> tuple[Any, Any]:
        # Sorted keys, so the same payload renders to the same text and the
        # same replay key on every run.
        text = json.dumps(payload, sort_keys=True)
        messages = [{"role": "user", "content": [
            {"type": "text", "text": BRIEF},
            {"type": "text", "text": text},
        ]}]
        prompt = {"model": self.model, "system": self.system,
                  "messages": messages}
        # The model and the messages. The system prompt and the brief are
        # held by instructions_digest, which the mixin checks on replay.
        return {"model": self.model, "messages": messages}, prompt

    def call(self, obs: Any, prompt: dict[str, Any]) -> str:
        client = self.client
        if client is None:
            sdk = require(DISTRIBUTION, extra=EXTRA,
                          purpose="a live Claude decision needs the "
                                  "Anthropic SDK")
            client = self.client = sdk.Anthropic()
        output_config: dict[str, Any] = {
            "format": {"type": "json_schema", "schema": output_schema()}}
        if self.effort is not None:
            output_config["effort"] = self.effort
        request: dict[str, Any] = {
            "model": prompt["model"], "max_tokens": self.max_tokens,
            "messages": prompt["messages"], "output_config": output_config}
        if prompt.get("system"):
            request["system"] = prompt["system"]
        response = client.messages.create(**request)
        where = f"at step {obs.step}, day {obs.day}"
        stop = getattr(response, "stop_reason", None)
        if stop == "refusal":
            details = getattr(response, "stop_details", None)
            raise DecisionError(
                f"Claude refused {where} (stop_reason 'refusal', category "
                f"{getattr(details, 'category', None)!r}). A refusal is an "
                "error, not a decision to hold.")
        if stop == "max_tokens":
            raise DecisionError(
                f"Claude's answer {where} was cut off at "
                f"max_tokens={self.max_tokens} before the decision was "
                "complete. Raise max_tokens.")
        text = next((block.text for block in response.content
                     if getattr(block, "type", None) == "text"), None)
        if not text:
            raise DecisionError(
                f"Claude returned no text {where} (stop_reason {stop!r}), "
                "so there is no decision to read.")
        return text

    def fork_kwargs(self) -> dict[str, Any]:
        kwargs = super().fork_kwargs()
        # The client is SHARED: it holds an HTTP connection pool, and a copy
        # would open a second one for no gain.
        kwargs.update(client=self.client, model=self.model,
                      system=self.system, max_tokens=self.max_tokens,
                      effort=self.effort, name=self.name)
        return kwargs


def claude_agent(client: Any = None, **kwargs: Any) -> ClaudeAdapter:
    """A :class:`ClaudeAdapter`, live unless ``mode`` says otherwise.

    The short spelling for the common case:

    ```python
    agent = claude_agent(anthropic.Anthropic(), model="claude-opus-5-5")
    ```
    """
    kwargs.setdefault("mode", "live")
    return ClaudeAdapter(client, **kwargs)
