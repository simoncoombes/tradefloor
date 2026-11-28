"""ClaudeAdapter: the shared contract, the request the SDK builds, and replay.

Nothing here calls the Anthropic API. Two seams are cut:

- The contract checks give the adapter a stand-in client whose
  ``messages.create`` answers from the payload with `respond(payload)`.
  Everything above the client runs unmodified.
- `test_the_sdk_builds_the_request_this_module_means` and its neighbours
  build a real ``anthropic.Anthropic`` client over an HTTP transport that
  answers locally, so the SDK's own request building and response parsing
  run, and the test reads the JSON body that would have been sent.

The live path needs the SDK for ``transform_schema``, so those tests skip
without it. Replaying the committed recording needs nothing installed.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import subprocess
import sys
from types import SimpleNamespace

import pytest

import test_integrations as contract
from tradefloor import ValidationError
from tradefloor.integrations import common as ci
from tradefloor.integrations.claude import (BRIEF, ClaudeAdapter,
                                            claude_agent, instructions_digest,
                                            payload_of)

HAVE_SDK = importlib.util.find_spec("anthropic") is not None
needs_sdk = pytest.mark.skipif(not HAVE_SDK,
                               reason='needs pip install "tradefloor[claude]"')

FIXTURE = (pathlib.Path(__file__).resolve().parent / "fixtures"
           / "claude_adapter" / "stand-in-two-days.json")

#: Not a Claude model id. A stand-in client answers, and every artefact that
#: carries this name says so.
STAND_IN = "stand-in-client"


class StandInClient:
    """``messages.create`` answered by `respond(payload)`, no network.

    A dict answer is sent as JSON text, a string verbatim, and an exception
    raised by `respond` propagates from the call site, which is the seam the
    contract checks expect. `stop_reason` scripts the end of the turn.
    """

    def __init__(self, respond, *, stop_reason: str = "end_turn"):
        self.requests: list[dict] = []
        self.messages = SimpleNamespace(create=self._create)
        self._respond = respond
        self._stop = stop_reason

    def _create(self, **request):
        self.requests.append(request)
        out = self._respond(payload_of(request))
        text = out if isinstance(out, str) else json.dumps(out)
        return SimpleNamespace(
            stop_reason=self._stop, stop_details=None,
            content=[SimpleNamespace(type="text", text=text)])


def make_agent(respond, **kwargs) -> ClaudeAdapter:
    """The adapter with its client stood in: the seam every check cuts."""
    kwargs.setdefault("model", STAND_IN)
    return ClaudeAdapter(StandInClient(respond), mode="live", **kwargs)


# -- the shared contract -----------------------------------------------------


@needs_sdk
@pytest.mark.parametrize("check", contract.CONTRACT_CHECKS,
                         ids=lambda f: f.__name__)
def test_the_adapter_meets_the_shared_contract(check):
    check(make_agent)


# -- construction and the optional dependency --------------------------------


def test_the_module_imports_without_the_sdk():
    """A replay must not import the SDK. A subprocess, because this suite
    may already have imported it."""
    source = ("import sys\n"
              "import tradefloor.integrations.claude as adapter\n"
              "assert 'anthropic' not in sys.modules\n"
              "assert adapter.ClaudeAdapter is not None\n")
    done = subprocess.run([sys.executable, "-c", source],
                          capture_output=True, text=True, timeout=300)
    assert done.returncode == 0, done.stderr


def test_replay_mode_is_the_default_and_needs_a_transcript():
    with pytest.raises(ValidationError, match="transcript"):
        ClaudeAdapter(model=STAND_IN)


def test_live_mode_needs_a_model_and_names_no_default():
    with pytest.raises(ValidationError, match="model="):
        ClaudeAdapter(StandInClient(contract.hold), mode="live")


def test_the_short_spelling_is_live():
    agent = claude_agent(StandInClient(contract.hold), model=STAND_IN)
    assert agent.mode == "live"


def test_the_metadata_names_the_sdk_the_model_and_the_instructions():
    agent = make_agent(contract.hold, system="Be careful.", effort="low")
    info = agent.info
    assert info.framework == "anthropic-sdk"
    assert info.provider == "anthropic" and info.model == STAND_IN
    assert info.instructions_digest == instructions_digest("Be careful.")
    assert info.instructions_digest != instructions_digest("")
    assert info.generation == {"max_tokens": 16_000, "effort": "low"}


# -- the request -------------------------------------------------------------


def test_the_brief_and_the_payload_travel_as_two_blocks_of_one_user_turn():
    client = StandInClient(contract.hold)
    agent = ClaudeAdapter(client, model=STAND_IN, mode="live",
                          system="You are a value investor.")
    pytest.importorskip("anthropic")
    contract.make_world(agent).run(days=1)
    request = client.requests[0]
    assert request["model"] == STAND_IN
    assert request["system"] == "You are a value investor."
    (turn,) = request["messages"]
    assert turn["role"] == "user"
    first, second = turn["content"]
    assert first["text"] == BRIEF
    assert json.loads(second["text"]) == agent.record[0]["payload"]
    assert "tool_choice" not in request and "tools" not in request


def _sdk_client(handler):
    """A real SDK client whose HTTP requests are answered by `handler`."""
    anthropic = pytest.importorskip("anthropic")
    httpx = pytest.importorskip("httpx2")
    # Not a credential: the transport never leaves the process.
    return anthropic.Anthropic(
        api_key="offline", max_retries=0,
        http_client=anthropic.DefaultHttpxClient(
            transport=httpx.MockTransport(handler)))


def _message(text, *, stop_reason="end_turn"):
    return {"id": "msg_offline", "type": "message", "role": "assistant",
            "model": STAND_IN, "stop_reason": stop_reason,
            "stop_sequence": None,
            "content": [{"type": "text", "text": text}],
            "usage": {"input_tokens": 1, "output_tokens": 1}}


@needs_sdk
def test_the_sdk_builds_the_request_this_module_means():
    """The JSON body the SDK would send, read at the HTTP layer."""
    import httpx2

    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx2.Response(200, json=_message(json.dumps(
            {"actions": [], "rationale": "nothing moves yet"})))

    agent = ClaudeAdapter(_sdk_client(handler), model=STAND_IN, mode="live",
                          effort="low")
    world = contract.make_world(agent)
    world.run(days=1)

    assert bodies, "no request reached the transport"
    body = bodies[0]
    assert body["model"] == STAND_IN and body["max_tokens"] == 16_000
    assert "system" not in body, "an empty system prompt is not sent"
    fmt = body["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    assert body["output_config"]["effort"] == "low"
    schema_text = json.dumps(fmt["schema"])
    for keyword in ("minLength", "exclusiveMinimum", "$schema"):
        assert f'"{keyword}"' not in schema_text, keyword
    assert fmt["schema"]["additionalProperties"] is False
    assert json.loads(body["messages"][0]["content"][1]["text"]) == \
        agent.record[0]["payload"]
    assert agent.record[0]["decision"]["actions"] == []


@needs_sdk
@pytest.mark.parametrize("stop,match", [("refusal", "refused"),
                                        ("max_tokens", "max_tokens")])
def test_a_refusal_or_a_cut_off_answer_is_a_decision_error(stop, match):
    """Neither is a considered hold, so neither may score as one."""
    import httpx2

    def handler(request):
        return httpx2.Response(200, json=_message("{", stop_reason=stop))

    agent = ClaudeAdapter(_sdk_client(handler), model=STAND_IN, mode="live")
    with pytest.raises(ci.DecisionError, match=match) as excinfo:
        contract.make_world(agent).run(days=1)
    assert "step 0, day 0" in str(excinfo.value)


@needs_sdk
def test_an_api_error_is_a_framework_error_not_a_decision_error():
    import httpx2

    def handler(request):
        return httpx2.Response(529, json={
            "type": "error",
            "error": {"type": "overloaded_error", "message": "busy"}})

    agent = ClaudeAdapter(_sdk_client(handler), model=STAND_IN, mode="live")
    with pytest.raises(ci.FrameworkError):
        contract.make_world(agent).run(days=1)


# -- record and replay -------------------------------------------------------


@needs_sdk
def test_a_recorded_run_replays_without_a_client():
    recorder = ci.Transcript()
    live = make_agent(contract.buy, recorder=recorder)
    contract.make_world(live).run(days=2)

    replay = ClaudeAdapter(model=STAND_IN, transcript=recorder)
    contract.make_world(replay).run(days=2)
    assert [r["decision"] for r in replay.record] == \
        [r["decision"] for r in live.record]


@needs_sdk
def test_a_replay_under_another_system_prompt_is_refused():
    recorder = ci.Transcript()
    contract.make_world(make_agent(contract.hold, recorder=recorder)).run(
        days=1)
    with pytest.raises(ValidationError, match="different instructions"):
        ClaudeAdapter(model=STAND_IN, system="A different agent.",
                      transcript=recorder)


@needs_sdk
def test_a_replay_under_another_model_misses():
    """The model is in the replay key: one model's answers are not
    another's."""
    recorder = ci.Transcript()
    contract.make_world(make_agent(contract.hold, recorder=recorder)).run(
        days=1)
    replay = ClaudeAdapter(model="another-model", transcript=recorder)
    with pytest.raises(ci.ReplayMiss):
        contract.make_world(replay).run(days=1)


def test_the_committed_recording_replays_with_nothing_installed():
    """`stand-in-two-days.json` was written by `StandInClient` answering
    `contract.buy`, on `contract.make_world` for two days. No model made it,
    and its meta says so."""
    transcript = ci.Transcript.load(FIXTURE)
    assert transcript.meta["model"] == STAND_IN
    assert "no model was called" in transcript.meta["extra"]["recorded_by"]
    replay = ClaudeAdapter(model=STAND_IN, transcript=transcript)
    contract.make_world(replay).run(days=2)
    assert len(replay.record) == len(transcript)
    assert all(r["decision"]["actions"] for r in replay.record)


def write_fixture() -> None:  # pragma: no cover - run by hand
    """Rewrite the committed recording. `python -c 'import
    test_claude_adapter as t; t.write_fixture()'` from `tests/`."""
    recorder = ci.Transcript()
    info = ci.AdapterInfo(
        framework="anthropic-sdk", provider="anthropic", model=STAND_IN,
        entry_point="anthropic.Anthropic.messages.create",
        framework_url="https://github.com/anthropics/anthropic-sdk-python",
        instructions_version="1", instructions_digest=instructions_digest(""),
        generation={"max_tokens": 16_000},
        extra={"recorded_by": "tests/test_claude_adapter.py StandInClient "
                              "answering contract.buy; no model was called"})
    agent = ClaudeAdapter(StandInClient(contract.buy), model=STAND_IN,
                          mode="live", recorder=recorder, info=info)
    contract.make_world(agent).run(days=2)
    FIXTURE.parent.mkdir(exist_ok=True)
    recorder.save(FIXTURE)
