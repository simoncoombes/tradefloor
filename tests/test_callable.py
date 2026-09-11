"""The callable adapter: a plain function held to the framework contract.

`CallableAgentAdapter` is the reference implementation of
`tradefloor.integrations.common`, so the shared contract itself is enforced
in `tests/test_integrations.py`, parametrized over `CONTRACT_CHECKS`. What
this file adds is the surface specific to wrapping a function: the shapes a
return value may take, the refusals for things a callable can do that a
framework cannot (return a coroutine, not be callable at all), determinism,
and the shipped example run end to end.

Nothing here needs a network, an API key, or any framework installed.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest

import tradefloor as tf
from tradefloor.counterfactual import World
from tradefloor.integrations import common as ci
from tradefloor.integrations.callable import CallableAgentAdapter, callable_agent

# The market helpers are shared with the contract file rather than copied:
# two rosters that drift apart would test two different markets under one
# name. Importable because the tests directory is flat and pytest puts it on
# the path, the same way `test_known_answer.py` imports `known_answer`.
import test_integrations as contract

REPO = pathlib.Path(__file__).resolve().parent.parent
EXAMPLE = REPO / "examples" / "integrations" / "callable" / "five_days.py"


def _load_example():
    import importlib.util
    spec = importlib.util.spec_from_file_location("callable_example", EXAMPLE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def buy(payload):
    return {"actions": [{"symbol": "TECH_A", "side": "BUY",
                         "quantity": 2000}],
            "rationale": "test"}


def run_world(fn, days=1, **kwargs):
    agent = callable_agent(fn, **kwargs)
    world = contract.make_world(agent)
    world.run(days=days)
    return world, agent


# -- construction ------------------------------------------------------------


def test_a_non_callable_is_refused_with_a_sentence():
    with pytest.raises(tf.ValidationError, match="callable"):
        CallableAgentAdapter("not a function")


def test_a_missing_fn_is_refused_the_same_way():
    """`fn` defaults to None so the refusal owns the message; a bare
    TypeError from the signature would not say what a valid fn looks
    like."""
    with pytest.raises(tf.ValidationError, match="callable"):
        CallableAgentAdapter()


def test_an_async_callable_trades_through_the_shared_bridge():
    """An async fn is supported: its coroutine goes through common.run_sync,
    the one bridge every adapter uses. Wrappers are covered too, because
    the RESULT is what is checked -- a partial carries a coroutine function
    past any constructor inspection."""
    async def decide(payload):
        return buy(payload)

    world, _ = run_world(decide)
    assert world.portfolio.positions["TECH_A"].quantity > 0


def test_an_async_callable_works_inside_a_running_event_loop():
    """The notebook case. A naive asyncio.run in the adapter works in a
    script and dies inside Jupyter's already-running loop; the shared
    bridge is what makes both work, so both are pinned."""
    import asyncio

    async def decide(payload):
        return buy(payload)

    async def notebook_cell():
        world, _ = run_world(decide)
        return world.portfolio.positions["TECH_A"].quantity

    assert asyncio.run(notebook_cell()) > 0


def test_an_exception_in_an_async_callable_keeps_its_chain():
    async def decide(payload):
        raise ConnectionError("provider unreachable")

    world = contract.make_world(callable_agent(decide))
    with pytest.raises(ci.FrameworkError) as excinfo:
        world.run(days=1)
    assert isinstance(excinfo.value.__cause__, ConnectionError)


def test_the_convenience_and_the_class_build_the_same_agent():
    agent = callable_agent(buy, every=3, arm="a")
    direct = CallableAgentAdapter(buy, every=3, arm="a")
    assert type(agent) is type(direct)
    assert agent.every == direct.every == 3
    assert agent.fn is direct.fn is buy


# -- the shapes a decision may take ------------------------------------------


def test_a_dict_decision_is_executed():
    world, agent = run_world(buy)
    assert world.portfolio.positions["TECH_A"].quantity > 0
    assert not world.rejected


def test_a_json_string_decision_is_executed():
    world, _ = run_world(lambda payload: json.dumps(buy(payload)))
    assert world.portfolio.positions["TECH_A"].quantity > 0


def test_a_decision_object_is_executed():
    world, _ = run_world(lambda payload: ci.Decision(
        [ci.Action("TECH_A", "BUY", 2000)], "typed"))
    assert world.portfolio.positions["TECH_A"].quantity > 0


def test_a_hold_and_an_empty_list_change_nothing():
    for fn in (lambda p: {"actions": [{"symbol": "TECH_A", "side": "HOLD"}]},
               lambda p: {"actions": []}):
        world, _ = run_world(fn)
        assert not world.portfolio.positions
        assert world.portfolio.cash == 1_000_000.0


def test_multiple_orders_execute_from_one_decision():
    def fn(payload):
        return {"actions": [
            {"symbol": "TECH_A", "side": "BUY", "quantity": 2000},
            {"symbol": "DEFENSIVE_A", "side": "SELL", "quantity": 1500}]}

    world, _ = run_world(fn)
    assert world.portfolio.positions["TECH_A"].quantity > 0
    assert world.portfolio.positions["DEFENSIVE_A"].quantity < 0


# -- refusals ----------------------------------------------------------------


def test_an_invalid_schema_is_refused():
    world = contract.make_world(callable_agent(
        lambda p: {"actions": [{"side": "BUY", "quantity": 1}]}))
    with pytest.raises(ci.DecisionError, match="symbol"):
        world.run(days=1)


def test_an_unlisted_symbol_is_refused():
    world = contract.make_world(callable_agent(
        lambda p: {"actions": [{"symbol": "NOT_LISTED", "side": "BUY",
                                "quantity": 10}]}))
    with pytest.raises(ci.MarketRefusalError, match="not listed"):
        world.run(days=1)


def test_a_negative_quantity_is_refused():
    world = contract.make_world(callable_agent(
        lambda p: {"actions": [{"symbol": "TECH_A", "side": "BUY",
                                "quantity": -5}]}))
    with pytest.raises(ci.DecisionError, match="negative"):
        world.run(days=1)


def test_an_exception_in_the_callable_propagates_with_its_chain():
    def fn(payload):
        raise KeyError("the rule read a field that is not there")

    world = contract.make_world(callable_agent(fn))
    with pytest.raises(ci.FrameworkError) as excinfo:
        world.run(days=1)
    assert isinstance(excinfo.value.__cause__, KeyError)


def test_evaluate_scores_a_raising_callable_instead_of_crashing():
    """The other harness. `evaluate` catches agent exceptions and records
    them, so a broken rule costs its own scorecard and nobody else's."""
    def fn(payload):
        raise RuntimeError("broken rule")

    scores = tf.evaluate(
        {"broken": callable_agent(fn), "idle": callable_agent(
            lambda p: {"actions": []})},
        seed=7, universe=contract.universe(), days=1)
    assert scores["broken"].errors
    assert "broken rule" in scores["broken"].errors[0]
    assert not scores["idle"].errors


# -- determinism and state ---------------------------------------------------


def test_a_deterministic_callable_reproduces_its_run():
    """The property replays rest on: the market is seeded and the rule is a
    function of the payload, so two runs are the same run."""
    def fn(payload):
        symbol = payload["assets"][0]["symbol"]
        step = payload["step"]
        if step and step % 12 == 0:
            return {"actions": [{"symbol": symbol, "side": "BUY",
                                 "quantity": 500 + step}]}
        return {"actions": []}

    first, agent_a = run_world(fn, days=3)
    second, agent_b = run_world(fn, days=3)
    assert first.digest() == second.digest()
    assert agent_a.record == agent_b.record
    assert [row["orders"] for row in first.trace] \
        == [row["orders"] for row in second.trace]


def test_the_record_carries_every_decision_point_and_its_exchange():
    world, agent = run_world(buy, days=2)
    assert [e["step"] for e in agent.record] == [0, 6]
    entry = agent.record[0]
    assert set(entry) == {"arm", "step", "day", "payload", "digest",
                         "prompt", "response", "decision", "orders",
                         "clipped"}
    assert entry["decision"]["rationale"] == "test"
    # The exchange half: the exact input is the payload itself, keyed by
    # its canonical-JSON digest, and the response is what fn returned.
    assert entry["prompt"]["step"] == 0
    assert entry["digest"] == ci.digest(entry["prompt"])
    assert entry["response"]["actions"][0]["side"] == "BUY"


def test_an_oversized_order_is_clipped_and_noted_in_the_record():
    def fn(payload):
        cap = payload["assets"][0]["max_order_shares"]
        return {"actions": [{"symbol": payload["assets"][0]["symbol"],
                             "side": "BUY", "quantity": cap * 10}]}

    # max_leverage=None: the point here is the participation clip, and the
    # leverage refusal would otherwise fire first on an order this size.
    agent = callable_agent(fn)
    world = World(seed=7, universe=contract.universe(), agent=agent,
                  cash=1_000_000.0, max_leverage=None)
    world.run(days=1)
    assert agent.record[0]["clipped"], "the clip must be recorded"
    assert "clipped" in agent.record[0]["clipped"][0]


def test_a_fork_keeps_the_function_and_the_subclass():
    class Tilted(CallableAgentAdapter):
        pass

    agent = Tilted(buy)
    world = contract.make_world(agent)
    world.run(days=2)
    control, shock = world.fork("control", "shock")
    assert isinstance(control.agent, Tilted)
    assert control.agent.fn is agent.fn, (
        "the function is the policy; a fork that lost it would run a "
        "different agent in both arms")
    agreement = tf.agree(control, shock)
    assert agreement.identical, agreement.differences


def test_the_metadata_names_the_function():
    agent = callable_agent(buy)
    assert agent.info.framework == "callable"
    assert agent.info.agent_name == "buy"
    named = callable_agent(buy, name="my_rule")
    assert named.info.agent_name == "my_rule"


# -- the recorded model run --------------------------------------------------

FIXTURE = REPO / "tests" / "fixtures" / "callable" / "five-days.json"

needs_fixture = pytest.mark.skipif(
    not FIXTURE.exists(),
    reason="no recorded callable-model run at tests/fixtures/callable/")


@needs_fixture
def test_the_fixture_records_what_a_replay_cannot_reconstruct():
    transcript = ci.Transcript.load(FIXTURE)
    assert len(transcript) > 0
    for field in ("framework", "provider", "model", "instructions_digest",
                  "decision_every_steps", "decision_schema_version",
                  "recorded_utc"):
        assert transcript.meta.get(field), f"meta is missing {field}"
    assert transcript.meta["framework"] == "callable"


@needs_fixture
def test_the_fixture_carries_no_credential_or_provider_metadata():
    text = FIXTURE.read_text(encoding="utf-8")
    # Prefixes and header names, not bare fragments -- `sk-` on its own
    # matched "risk-adjusted", which this fixture's mandate contains.
    for secret in ("sk-ant-", "sk-proj-", "lsv2_pt-", "pylf_v1_", "api_key",
                   "api-key", "Bearer ", "Authorization",
                   "anthropic-version", "request_id"):
        assert secret not in text, f"the fixture contains {secret!r}"

    entries = json.loads(text)["entries"]
    allowed = {"arm", "step", "day", "digest", "prompt", "response"}
    for entry in entries:
        extra = set(entry) - allowed
        assert not extra, f"unexpected fields in a recorded entry: {extra}"


@needs_fixture
def test_the_recorded_responses_are_a_real_models_and_still_validate():
    """Nobody hand-authored these. The recorded text is what the model
    said, so parsing it tests the validator against genuine output rather
    than against a fixture written to suit it."""
    entries = json.loads(FIXTURE.read_text(encoding="utf-8"))["entries"]
    decisions = [ci.parse_decision(e["response"]) for e in entries]
    assert decisions, "the fixture is empty"
    sides = {a.side for d in decisions for a in d.actions}
    assert sides <= set(ci.SIDES) and sides


@needs_fixture
def test_the_recorded_run_replays_end_to_end():
    """The recorded model run, replayed through evaluate() with a function
    that EXPLODES if called: no network, no key, no fn -- and the market
    must accept every recorded decision without a rejection, or the
    recording describes a run that cannot have happened."""
    example = _load_example()

    def exploding(payload):
        raise AssertionError("replay called the function")

    agent = callable_agent(exploding, mode="replay",
                           transcript=ci.Transcript.load(FIXTURE))
    card = tf.evaluate({"llm": agent}, seed=example.SEED,
                       universe=example.universe(),
                       days=example.DAYS)["llm"]
    assert len(agent.record) == example.DAYS
    assert card.trades > 0
    assert card.rejected == 0, card.errors
    assert not card.errors, card.errors


def test_live_mode_requires_an_explicit_opt_in(monkeypatch):
    """A credential existing in the environment is not consent to spend it.

    Without this gate, a developer with a key exported who ran the slow
    suite would re-execute every live notebook and spend real money,
    having asked for neither -- and the replay-identity cell would then
    read False against the committed recording, so the surprise bill would
    arrive dressed as a test failure. Live needs the opt-in ON TOP of the
    key and the SDK; replay is the default even when live is possible."""
    example = _load_example()
    monkeypatch.setenv(example.LIVE_KEY_VAR, "set-but-not-consent")
    monkeypatch.delenv(example.LIVE_OPT_IN_VAR, raising=False)
    assert not example.can_run_live(), (
        "a key alone must never enable live calls")

    monkeypatch.setenv(example.LIVE_OPT_IN_VAR, "1")
    import importlib.util
    if importlib.util.find_spec("anthropic") is not None:
        assert example.can_run_live(), (
            "opt-in plus key plus SDK is exactly the live condition")

    monkeypatch.delenv(example.LIVE_KEY_VAR)
    assert not example.can_run_live(), "the opt-in alone is not enough either"


def test_can_run_live_answers_no_when_the_probe_itself_raises(monkeypatch):
    """Under an import blocker -- a restricted sandbox, a broken parent
    package -- find_spec RAISES for the module instead of returning None,
    and an unguarded probe turns "the framework is absent" into a crash
    inside the very test written to prove it can be absent. A helper whose
    entire job is answering "can we?" must never be the thing that fails:
    a raised question is a no."""
    import importlib.util

    example = _load_example()
    monkeypatch.setenv(example.LIVE_KEY_VAR, "set-but-not-consent")
    monkeypatch.setenv(example.LIVE_OPT_IN_VAR, "1")

    def blocked(name, *args, **kwargs):
        raise ModuleNotFoundError(f"import of {name!r} is blocked here")

    monkeypatch.setattr(importlib.util, "find_spec", blocked)
    assert example.can_run_live() is False


# -- the example -------------------------------------------------------------


def test_the_example_runs_end_to_end():
    """The shipped example, run whole, and NOT behind the slow flag: it
    finishes in about a second, and it is the template the framework
    examples copy, so it rotting would propagate. It asserts its own gates
    and exits non-zero if any fails; the return code is the verdict."""
    if not EXAMPLE.exists():
        pytest.fail(f"{EXAMPLE.name} is missing from examples/integrations/")
    done = subprocess.run([sys.executable, str(EXAMPLE)],
                          capture_output=True, text=True, timeout=300)
    assert done.returncode == 0, done.stdout[-3000:] + done.stderr[-3000:]
    assert "decisions" in done.stdout
