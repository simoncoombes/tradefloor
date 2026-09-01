"""The shared integration contract, and the reusable checks that enforce it.

`tradefloor.integrations.common` is what every framework adapter builds on:
the error family, the observation allowlist, the decision model and its
two-stage validation, transcripts and replay, adapter metadata, and the
`FrameworkAdapter` base. This file tests that layer directly, and it exports
`CONTRACT_CHECKS` -- a list of checks any adapter must pass -- so each
adapter's own test file proves the same contract without rewriting it:

```python
import test_integrations as contract

@pytest.mark.parametrize("check", contract.CONTRACT_CHECKS,
                         ids=lambda f: f.__name__)
def test_the_adapter_meets_the_shared_contract(check):
    check(lambda respond: MyAdapter(scripted=respond))
```

`make_agent(respond)` must return an adapter of the type under test whose
framework call is replaced, so that the adapter's raw output for a payload
is `respond(payload)` -- and an exception raised by `respond` propagates
from the framework-call site. That is the same seam `tests/test_finrobot.py`
cuts with its `Scripted` subclass: everything below the framework call runs
unmodified, because a double that reimplemented the validation would be
testing itself.

Nothing here needs a network, an API key, or any framework installed.

One harness property every adapter test must respect: ``tf.evaluate``
absorbs a DecisionError into ``card.errors`` and COMPLETES, so a test that
drives an adapter through it and asserts only on the scorecard passes over
a raising adapter. Assert ``not card.errors`` too -- that line is the one
doing the work, and its absence let a corrupted recording lose three of
five decisions while a notebook's narrative still read as intact.
"""

from __future__ import annotations

import json
import pathlib
import struct
import subprocess
import sys

import pytest

import tradefloor as tf
from tradefloor.counterfactual import World
from tradefloor.harness import Observation
from tradefloor.integrations import common as ci
from tradefloor.integrations.callable import CallableAgentAdapter, callable_agent

#: A two-name market, the same shape `tests/test_finrobot.py` uses: these are
#: unit tests, and a small roster keeps every assertion readable.
ROSTER = [
    ("TECH_A", "technology", 100.0, 0.30),
    ("DEFENSIVE_A", "consumer_staples", 50.0, 0.02),
]


def universe() -> list:
    return list(tf.Universe([
        tf.Instrument(ticker, sector, initial_price=price,
                      shares_outstanding=4.0e8, eps=3.0,
                      book_value_per_share=15.0, revenue_growth=growth,
                      avg_volume=5.0e6, beta=1.0, short_interest=4.0e6)
        for ticker, sector, price, growth in ROSTER
    ]))


def make_world(agent) -> World:
    return World(seed=7, universe=universe(), agent=agent,
                 cash=1_000_000.0,
                 pins={"federal_funds_rate": 0.04,
                       "corporate_bond_yield": 0.055})


def _observation(world: World, engine=None) -> Observation:
    """An Observation over a world's current state, as `World.run` builds
    one. Rebuilt here, not captured, so a test can substitute the engine."""
    prices = list(struct.unpack("<%dd" % len(world.engine.tickers),
                                world.engine.prices()))
    return Observation(world.step, world.day, world.engine.tickers, prices,
                       world.portfolio, engine or world.engine,
                       [i.avg_volume for i in world.universe],
                       world.steps_per_day)


def hold(payload):
    return {"actions": []}


def buy(payload):
    return {"actions": [{"symbol": "TECH_A", "side": "BUY",
                         "quantity": 2000}],
            "rationale": "contract"}


# -- the ground-truth boundary ----------------------------------------------

#: Everything on the engine that a trader in this market could not know.
#: `column` is on it because its fields include `mispricing_s` and
#: `garch_variance`; `truth` and `attribution` are the answer key outright;
#: the draw counters, the book snapshots and the session tapes are the
#: simulator's own bookkeeping. Every name here MUST be a real engine
#: attribute -- a test below asserts it -- because `fair_value` sat in this
#: list for a while and could never fire: it is a public module FUNCTION,
#: not an engine attribute, so an engine proxy structurally cannot seal it
#: (a caller who supplies full fundamentals supplies the means to
#: reconstruct it; see serialize_observation's docstring). A sealed name
#: that does not exist seals nothing, and the list rots silently.
SEALED = ("attribution", "truth", "session_mispricing_s",
          "column", "state_snapshot", "macro_table", "bars", "order_log",
          "model_params", "model", "draws_consumed", "draws_by_stream",
          "model_fingerprint", "book_table", "snapshot_book",
          "session_prices", "session_volumes")


class Sealed:
    """An engine that raises if the simulator's own knowledge is touched.

    Stronger than scanning serialized output for a leaked number: this fails
    on the ACCESS, so a future edit that reads `engine.attribution` and then
    rounds it, scales it or uses it to pick a word fails here too.
    """

    def __init__(self, engine):
        object.__setattr__(self, "_engine", engine)

    def __getattr__(self, name):
        if name in SEALED:
            raise AssertionError(
                f"the integration read engine.{name}, which is simulator "
                "ground truth. A framework must see only what a trader in "
                "this market could observe.")
        return getattr(object.__getattribute__(self, "_engine"), name)


# -- the reusable contract ---------------------------------------------------

#: The serialized payload, written down as a contract. A new key gives every
#: framework something new to see; that is a decision about the experiment,
#: so it should require editing this list.
PAYLOAD_KEYS = {"step", "day", "steps_per_day", "macro", "assets", "portfolio"}
ASSET_KEYS = {"symbol", "price", "return_1d", "return_5d", "volatility",
              "best_bid", "best_ask", "avg_daily_volume", "max_order_shares",
              "position", "fundamentals"}


def check_the_payload_reaches_the_framework(make_agent):
    """The adapter serializes the Observation it was handed, allowlist keys
    only, and the framework is shown that payload and nothing else."""
    seen = []

    def respond(payload):
        seen.append(payload)
        return {"actions": []}

    world = make_world(make_agent(respond))
    world.run(days=1)
    assert seen, "the framework was never consulted"
    payload = seen[0]
    assert set(payload) == PAYLOAD_KEYS
    assert set(payload["assets"][0]) == ASSET_KEYS
    assert set(payload["macro"]) == set(ci.OBSERVABLE_MACRO)
    assert [a["symbol"] for a in payload["assets"]] == [r[0] for r in ROSTER]
    assert payload["step"] == 0 and payload["day"] == 0


def check_a_valid_decision_is_executed(make_agent):
    world = make_world(make_agent(buy))
    world.run(days=1)
    assert world.portfolio.positions["TECH_A"].quantity > 0
    assert world.portfolio.cash < 1_000_000.0
    assert not world.rejected, world.rejected


def check_a_hold_produces_no_order(make_agent):
    world = make_world(make_agent(
        lambda payload: {"actions": [{"symbol": "TECH_A", "side": "HOLD"}]}))
    world.run(days=1)
    assert not world.portfolio.positions
    assert world.portfolio.cash == 1_000_000.0


def check_an_empty_action_list_is_a_no_op(make_agent):
    world = make_world(make_agent(hold))
    world.run(days=1)
    assert not world.portfolio.positions


def check_multiple_orders_execute_in_one_decision(make_agent):
    def respond(payload):
        return {"actions": [
            {"symbol": "TECH_A", "side": "BUY", "quantity": 2000},
            {"symbol": "DEFENSIVE_A", "side": "SELL", "quantity": 1500},
        ]}

    world = make_world(make_agent(respond))
    world.run(days=1)
    assert world.portfolio.positions["TECH_A"].quantity > 0
    assert world.portfolio.positions["DEFENSIVE_A"].quantity < 0


def check_invalid_output_is_refused(make_agent):
    world = make_world(make_agent(lambda payload: "no decision here at all"))
    with pytest.raises(ci.DecisionError):
        world.run(days=1)


def check_a_framework_envelope_is_refused_not_scored_as_hold(make_agent):
    """A mapping with no 'actions' key -- graph state carrying 'messages', a
    result wrapper, a different structured type -- must raise, never hold.
    trades=0 beside an empty errors list is exactly what a considered
    decline looks like, and a plumbing failure must not wear that shape.
    Unwrapping the envelope is the adapter's job, before parse_decision."""
    world = make_world(make_agent(
        lambda payload: {"messages": ["I have finished thinking."]}))
    with pytest.raises(ci.DecisionError, match="actions"):
        world.run(days=1)


def check_a_framework_exception_is_surfaced_with_its_chain(make_agent):
    def respond(payload):
        raise RuntimeError("the framework exploded")

    world = make_world(make_agent(respond))
    with pytest.raises(ci.FrameworkError) as excinfo:
        world.run(days=1)
    assert "the framework exploded" in str(excinfo.value)
    assert isinstance(excinfo.value.__cause__, RuntimeError), (
        "the original exception must ride on __cause__, or the traceback "
        "a user debugs stops at the adapter")


def check_the_observation_is_not_mutated(make_agent):
    """`act` reads the Observation and touches NOTHING -- including the
    live engine the Observation carries. The package validates decisions;
    it does not sandbox this seam, so an adapter holds the same engine any
    agent holds and not touching it is an obligation this check enforces,
    not a property the harness provides. A subclass calling
    `obs.engine.pin_macro(...)` inside ask() once ran to completion with
    zero errors and moved the market, because the first version of this
    check snapshotted only the Observation's own fields."""
    from tradefloor.manifest import market_digest

    world = make_world(make_agent(hold))
    world.run(days=1)
    obs = _observation(world)
    before_prices = list(obs.prices)
    before_cash = obs.portfolio.cash
    before_positions = {t: p.quantity
                        for t, p in obs.portfolio.positions.items()}
    # The engine's own state, three ways, because no single view covers it:
    # the market digest (prices and the continuous internals), the order
    # log length (every COMMAND the engine accepts is logged, macro pins
    # included, so this catches a mutation of state the digest does not
    # fold in), and the observable macro fields (the demonstrated attack,
    # asserted directly so a failure names the field).
    before_digest = market_digest(world.engine)
    before_log = len(world.engine.order_log)
    before_macro = {field: getattr(world.engine.macro_state, field)
                    for field in ci.OBSERVABLE_MACRO}

    agent = make_agent(buy)
    orders = agent.act(obs)
    assert orders, "the decision produced no orders, so this checks nothing"
    assert list(obs.prices) == before_prices
    assert obs.portfolio.cash == before_cash
    assert {t: p.quantity for t, p in obs.portfolio.positions.items()} \
        == before_positions, "act() traded; execution belongs to the harness"
    assert market_digest(world.engine) == before_digest, (
        "act() changed the market's state")
    assert len(world.engine.order_log) == before_log, (
        "act() sent the engine a command; only the harness may")
    assert {field: getattr(world.engine.macro_state, field)
            for field in ci.OBSERVABLE_MACRO} == before_macro, (
        "act() moved the macro state the experiment is about")


def check_fork_preserves_type_and_copies_state(make_agent):
    agent = make_agent(buy)
    world = make_world(agent)
    world.run(days=2)
    twin = world.agent.fork()
    assert type(twin) is type(world.agent), (
        "fork() must build type(self), or a subclass forks into its base "
        "and the comparison runs two agents neither of which was under test")
    assert twin.state() == world.agent.state()
    assert twin.history is not world.agent.history
    twin.history.append([0.0, 0.0])
    assert len(twin.history) != len(world.agent.history)


def check_the_published_state_serializes_and_carries_no_credential(make_agent):
    """state() is printed by the fork agreement and written into artifacts,
    so it must serialise and carry no credential. The scan is the SAME rule
    AdapterInfo enforces at construction -- key-shaped names, anchored
    secret values -- because two credential rules that can disagree is the
    two-renderings problem again, in the guards. The first version was a
    bare substring scan, and "sk-" in "risk-adjusted" is True."""
    agent = make_agent(hold)
    world = make_world(agent)
    world.run(days=1)
    state = world.agent.state()
    json.dumps(state)
    ci._credential_free(state, "state()")   # raises ValidationError on a hit


def check_the_decision_hook_publishes_actions_and_rationale(make_agent):
    def respond(payload):
        return {"actions": [{"symbol": "TECH_A", "side": "HOLD"}],
                "rationale": "waiting"}

    world = make_world(make_agent(respond))
    world.run(days=1)
    published = world.agent.decision()
    assert published["rationale"] == "waiting"
    assert published["actions"][0]["side"] == "HOLD"
    assert "prompt" not in published and "response" not in published, (
        "compare() finds the first differing decision by comparing these "
        "dicts, so anything in here that varies for another reason reports "
        "a divergence that did not happen")


def check_metadata_is_captured_and_credential_free(make_agent):
    """The credential scan delegates to the AdapterInfo rule -- one rule,
    key-shaped and value-anchored, never a substring match that fires on
    "risk-adjusted" prose. It also covers the NAMED fields, which the
    constructor does not walk: a key pasted into `model` fails here."""
    agent = make_agent(hold)
    assert isinstance(agent.info, ci.AdapterInfo)
    assert agent.info.framework, "metadata must name the framework"
    assert agent.info.decision_schema_version == ci.DECISION_SCHEMA_VERSION
    published = agent.info.as_dict()
    json.dumps(published)
    ci._credential_free(published, "info")


def check_the_record_joins_the_decision_to_the_exchange(make_agent):
    """agent.record must let an artifact show the whole chain -- observation
    to exact framework input to raw response to validated action to order
    -- without hand-joining the transcript by digest. FinRobot's record did
    this from day one; the base once recorded a thin entry and the gap
    shipped into three adapters before anything noticed, because no check
    asserted what the record CARRIES, only that it exists."""
    world = make_world(make_agent(buy))
    world.run(days=1)
    assert world.agent.record, "no decision point was recorded"
    entry = world.agent.record[0]
    assert set(entry) == {"arm", "step", "day", "payload", "digest",
                          "prompt", "response", "decision", "orders",
                          "clipped"}, (
        f"record entry carries {sorted(entry)}")
    # `payload` is the OBSERVATION end of the chain this docstring names,
    # and it was the one link missing: the entry began at the rendered
    # input, so nothing joined a decision back to what the agent was
    # shown. `resample` needs it -- an adapter that builds its framework
    # input from the payload rather than from text has nothing to re-ask
    # without it.
    assert entry["payload"]["step"] == entry["step"]
    assert entry["digest"], "the record must carry the replay key"
    assert entry["response"], "the record must carry the raw response"
    json.dumps(entry)   # the record feeds artifacts; it must serialise


def check_the_replay_key_derives_from_the_input(make_agent):
    """The recorded digest must be a function of the INPUT, never of a
    position. An adapter keying its transcript on (arm, step) passes every
    other test it has, and its studies then answer new questions with
    recorded answers to old ones. Position cannot tell two markets apart
    and input can, so: the same decision points in two worlds that differ
    only in a pinned macro rate must carry disjoint keys, and an identical
    world must reproduce the identical keys."""
    def world_with_rate(agent, rate):
        return World(seed=7, universe=universe(), agent=agent,
                     cash=1_000_000.0,
                     pins={"federal_funds_rate": rate,
                           "corporate_bond_yield": 0.055})

    low, high, again = (make_agent(hold) for _ in range(3))
    world_with_rate(low, 0.04).run(days=1)
    world_with_rate(high, 0.06).run(days=1)
    world_with_rate(again, 0.04).run(days=1)

    low_keys = [entry["digest"] for entry in low.record]
    assert low_keys, "no decision point carried a key"
    assert set(low_keys).isdisjoint(
        entry["digest"] for entry in high.record), (
        "two different markets produced the same replay key, so the key is "
        "positional, not input-derived, and a replay would answer one "
        "market's question with the other's recorded response")
    assert [entry["digest"] for entry in again.record] == low_keys, (
        "an identical market must reproduce identical keys, or no "
        "recording can ever be replayed")


def check_the_state_carries_an_instructions_identity(make_agent):
    """Two arms built BY HAND -- the obvious way to compare two
    configurations of one framework -- must not run different instructions
    while agree() reports identical. The base publishes
    info.instructions_digest in state(); an adapter that overrides state()
    keeps an instructions identity in it (FinRobot's mandate_version serves
    the same purpose)."""
    state = make_agent(hold).state()
    assert any(key in state for key in ("instructions_digest",
                                        "mandate_version")), sorted(state)


def check_the_adapter_never_reaches_ground_truth(make_agent):
    """The serialization path runs against a sealed engine: reading any
    answer-key attribute raises on the access."""
    world = make_world(make_agent(hold))
    world.run(days=2)
    sealed = _observation(world, engine=Sealed(world.engine))
    agent = make_agent(buy)
    orders = agent.act(sealed)      # raises through Sealed if it reached one
    assert orders, "the sealed decision produced nothing to check"


def check_a_recorded_decision_can_be_asked_again(make_agent):
    """`resample` asks one recorded input again, and the noise floor it
    reports is only meaningful if the re-ask changes nothing. An adapter
    that appended to the price history here would move the market memory
    the rest of the run was taken under, and the samples after the first
    would be answers to a different question."""
    world = make_world(make_agent(buy))
    world.run(days=1)
    agent = world.agent
    entry = agent.record[0]

    if getattr(agent, "mode", "live") == "replay":
        # A recording holds one answer per input, so re-asking it would
        # report a within-arm spread of zero -- the most convincing result
        # a resample can produce, and an artifact of the file.
        with pytest.raises(tf.ValidationError, match="noise floor of zero"):
            agent.reask(entry)
        return

    before = (len(agent.history), len(agent.record),
              json.dumps(agent.state(), default=str))
    again = agent.reask(entry)
    assert (len(agent.history), len(agent.record),
            json.dumps(agent.state(), default=str)) == before, (
        "reask must not touch adapter state; a resample happens after the "
        "run and the state belongs to the run")

    # Same shape as ask() returns, so parse_decision reads both.
    assert ci.parse_decision(again).actions


#: Every check above, in one list, so an adapter's test file can parametrize
#: over it and a failure names the clause that broke.
CONTRACT_CHECKS = [
    check_the_payload_reaches_the_framework,
    check_a_valid_decision_is_executed,
    check_a_hold_produces_no_order,
    check_an_empty_action_list_is_a_no_op,
    check_multiple_orders_execute_in_one_decision,
    check_invalid_output_is_refused,
    check_a_framework_envelope_is_refused_not_scored_as_hold,
    check_a_framework_exception_is_surfaced_with_its_chain,
    check_the_observation_is_not_mutated,
    check_fork_preserves_type_and_copies_state,
    check_the_published_state_serializes_and_carries_no_credential,
    check_the_decision_hook_publishes_actions_and_rationale,
    check_metadata_is_captured_and_credential_free,
    check_the_record_joins_the_decision_to_the_exchange,
    check_a_recorded_decision_can_be_asked_again,
    check_the_replay_key_derives_from_the_input,
    check_the_state_carries_an_instructions_identity,
    check_the_adapter_never_reaches_ground_truth,
]


def assert_adapter_contract(make_agent) -> None:
    """Run every contract check against one adapter type, in one call.

    The parametrized form above it is better in a test file -- a failure
    names the clause -- but a notebook or a quick script wants one line.
    """
    for check in CONTRACT_CHECKS:
        check(make_agent)


#: The reference adapter, held to its own contract. This is also what proves
#: the checks themselves run: a broken check that no adapter could fail
#: would fail here first.
@pytest.mark.parametrize("check", CONTRACT_CHECKS, ids=lambda f: f.__name__)
def test_the_callable_adapter_meets_the_shared_contract(check):
    check(lambda respond: callable_agent(respond))


# -- the committed recordings ------------------------------------------------

#: Every committed recording, discovered rather than listed, so a sixth
#: fixture arrives covered and a renamed one cannot quietly leave.
_FIXTURES = sorted(
    pathlib.Path(__file__).resolve().parent.joinpath("fixtures")
    .glob("*/*.json"))


def test_there_are_committed_recordings_to_check():
    """Guards the guard: a glob matching nothing would make the test below
    pass by vacuum, and the suite would report the recordings healthy
    while reading none of them -- which is exactly the state an audit
    found two of five fixtures in."""
    assert len(_FIXTURES) >= 5, [p.as_posix() for p in _FIXTURES]


@pytest.mark.parametrize("path", _FIXTURES, ids=lambda p: p.parent.name)
def test_a_committed_recording_is_valid_without_its_framework(path):
    """Replay is the one path this package guarantees works with no
    framework installed, and it was the path with the least coverage in
    exactly that configuration: two of the five committed fixtures were
    read by no test at all, a corrupted response in either left the
    default suite green, and the per-adapter fixture tests that closed it
    sit behind importorskip on the framework -- a gate stricter than the
    code, since replay imports nothing. So the shared layer proves what it
    claims, here, on a bare install: every fixture loads as a Transcript,
    its meta carries what a replay cannot reconstruct, every entry has
    exactly the allowed keys and a unique digest, every recorded response
    still parses through the shared validator, and no credential shape
    appears anywhere.

    What this deliberately does NOT do is re-run each adapter's world.
    The seeds and rosters belong to the examples, and a sixth place that
    knows five worlds is a sixth place to keep in sync -- the digest-keyed
    end-to-end replay stays with each adapter's own tests, which need no
    framework either and should not hide behind one."""
    text = path.read_text(encoding="utf-8")
    transcript = ci.Transcript.from_json(text)
    assert len(transcript) > 0, "an empty recording records nothing"
    for field in ("framework", "decision_every_steps", "max_participation"):
        assert transcript.meta.get(field) not in (None, ""), (
            f"{path.parent.name} meta is missing {field}")

    allowed = {"arm", "step", "day", "digest", "prompt", "response"}
    digests = []
    for entry in transcript.entries:
        assert set(entry) <= allowed, set(entry) - allowed
        assert entry.get("digest"), (
            "an entry without a key can never be replayed")
        digests.append(entry["digest"])
        assert isinstance(entry.get("response"), str), (
            "a recorded response has to be text; a null one cannot be "
            "replayed and is not the same thing as a refusal")

    # How many recorded responses the shared validator refuses, checked
    # against what the recording says it should be.
    #
    # This was a bare `parse_decision` on every entry, which reads a
    # corrupted file and a model that answered badly as the same failure.
    # They are not: `parse` is strict on purpose, so a long enough real
    # recording contains output it refuses, and forbidding that forbids
    # committing an honest recording. One measured case, in a recording of
    # 80 decisions: a per-action `rationale` field, which the study around
    # it counted as a refusal and a lost step.
    #
    # A recording that declares nothing must still parse cleanly, so the
    # five fixtures committed before this are unchanged by it, and a
    # corrupted response in any of them still fails here.
    refused = 0
    for entry in transcript.entries:
        try:
            ci.parse_decision(entry["response"])
        except ci.DecisionError:
            refused += 1
    declared = transcript.meta.get("unparseable_responses", 0)
    assert refused == declared, (
        f"{path.parent.name} has {refused} responses the validator "
        f"refuses and declares {declared}. A recording may hold output a "
        "real model produced, and it has to say how much: a change in "
        "this count is either a corrupted file or a different run."
    )
    assert len(digests) == len(set(digests)), "duplicate replay keys"

    for secret in ("sk-ant-", "sk-proj-", "lsv2_pt-", "pylf_v1_", "api_key",
                   "Bearer ", "Authorization"):
        assert secret not in text, f"{path.parent.name} contains {secret!r}"


# -- import hygiene ----------------------------------------------------------


def test_importing_tradefloor_does_not_import_the_integrations():
    """`import tradefloor` must never touch this subpackage, so a broken or
    absent third-party dependency cannot break the library. A subprocess,
    because this suite has already imported both."""
    code = ("import sys, tradefloor; "
            "assert 'tradefloor.integrations' not in sys.modules, "
            "'import tradefloor pulled in the integrations subpackage'; "
            "print('ok')")
    done = subprocess.run([sys.executable, "-c", code],
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr


def test_the_shared_layer_imports_with_no_framework_installed():
    """`common` must import on a bare install: it is on the path of REPLAYING
    a recorded run, which is exactly the situation where no framework is
    present. Pydantic included -- the model is built on first request, not
    on import."""
    banned = ("pydantic", "agents", "pydantic_ai", "langgraph", "finrobot",
              "autogen", "openai", "langchain")
    code = ("import sys; "
            "import tradefloor.integrations.common; "
            "import tradefloor.integrations.callable; "
            f"hit = [m for m in {banned!r} if m in sys.modules]; "
            "assert not hit, f'shared layer imported {hit}'; "
            "print('ok')")
    done = subprocess.run([sys.executable, "-c", code],
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr


def test_a_missing_dependency_names_the_extra_and_the_command():
    with pytest.raises(ci.MissingDependencyError) as excinfo:
        ci.require("module_that_does_not_exist_anywhere",
                   extra="openai-agents")
    message = str(excinfo.value)
    assert 'pip install "tradefloor[openai-agents]"' in message
    assert "optional" in message
    assert isinstance(excinfo.value.__cause__, ImportError), (
        "the original ImportError must be chained, or the real module "
        "that failed -- often a dependency of the framework, not the "
        "framework itself -- is lost")


def test_a_missing_dependency_without_an_extra_names_the_requirement():
    with pytest.raises(ci.MissingDependencyError) as excinfo:
        ci.require("module_that_does_not_exist_anywhere",
                   pip="somepackage>=2")
    assert 'pip install "somepackage>=2"' in str(excinfo.value)


def test_a_dependency_note_lands_after_the_pip_command():
    """The note is the sentence that matters: a version constraint or
    collision the user hits AFTER running the command. FinRobot's is the
    reference case -- without "the two overlap at 3.11 exactly", somebody
    on 3.12 gets a resolver failure they cannot interpret."""
    with pytest.raises(ci.MissingDependencyError) as excinfo:
        ci.require("module_that_does_not_exist_anywhere", extra="finrobot",
                   note="FinRobot supports Python 3.10 and 3.11 only, and "
                        "Tradefloor needs 3.11 or later, so the two overlap "
                        "at 3.11 exactly.")
    message = str(excinfo.value)
    command = message.index('pip install "tradefloor[finrobot]"')
    assert command < message.index("overlap at 3.11"), (
        "the note belongs after the command it qualifies")
    assert message.index("overlap at 3.11") < message.index("Original error")


def test_require_returns_the_module_when_it_is_installed():
    import json as stdlib_json
    assert ci.require("json") is stdlib_json


# -- the error family --------------------------------------------------------


def test_every_integration_error_is_a_validation_error():
    """So a caller already catching the library's refusals catches these."""
    for exc in (ci.IntegrationError, ci.MissingDependencyError,
                ci.FrameworkError, ci.DecisionError, ci.MarketRefusalError):
        assert issubclass(exc, tf.ValidationError), exc


def test_the_error_family_distinguishes_the_four_failures():
    assert issubclass(ci.MissingDependencyError, ImportError), (
        "the FinRobot adapter raised ImportError for a missing framework, "
        "and callers written against that must keep working")
    assert issubclass(ci.MarketRefusalError, ci.DecisionError), (
        "catch DecisionError, catch everything a decision can fail with -- "
        "the FinRobot integration raises one class for both stages")
    assert not issubclass(ci.FrameworkError, ci.DecisionError)
    assert not issubclass(ci.DecisionError, ci.FrameworkError)


# -- the observation serializer ----------------------------------------------


def test_the_serializer_emits_exactly_the_allowlisted_keys():
    agent = callable_agent(hold)
    world = make_world(agent)
    world.run(days=2)
    payload = ci.serialize_observation(_observation(world),
                                       history=agent.history)
    assert set(payload) == PAYLOAD_KEYS
    assert set(payload["assets"][0]) == ASSET_KEYS
    assert set(payload["portfolio"]) == {"cash", "net_worth",
                                         "gross_exposure", "max_leverage",
                                         "buying_power"}


def test_the_payload_states_the_binding_size_limit():
    """max_order_shares is the participation cap, and it is not usually the
    binding one. Measured four times over -- two frontier models, a
    documentation example, this module's own first example -- an agent
    that sized to the only limit the payload named was refused at the
    leverage limit the payload hid, scored zero trades, and read as a bad
    agent when it was misled by the observation. Both limits are stated
    now, so an agent can take the minimum instead of deriving the
    harness's own rule."""
    world = make_world(callable_agent(hold))
    world.run(days=1)
    book = ci.serialize_observation(_observation(world))["portfolio"]
    assert book["max_leverage"] == 2.0
    # Nothing is held, so the headroom is the full limit times equity.
    assert book["buying_power"] == pytest.approx(2.0 * book["net_worth"])

    # And the stated numbers are sufficient: a max_order_shares-sized order
    # would blow the cap, a buying_power-sized one cannot.
    asset = ci.serialize_observation(_observation(world))["assets"][0]
    stated_cap_notional = asset["max_order_shares"] * asset["price"]
    assert stated_cap_notional > book["buying_power"], (
        "the trap this test pins requires the participation cap to exceed "
        "the funding cap; if the roster changed, resize it")


def test_an_unconstrained_portfolio_is_stated_honestly():
    """max_leverage=None means no funding limit was configured. The payload
    says null rather than inventing a number, because a made-up ceiling is
    the same defect in the other direction."""
    world = World(seed=7, universe=universe(), agent=callable_agent(hold),
                  cash=1_000_000.0, max_leverage=None)
    world.run(days=1)
    book = ci.serialize_observation(_observation(world))["portfolio"]
    assert book["max_leverage"] is None
    assert book["buying_power"] is None


def test_the_macro_allowlist_is_the_librarys_own():
    """The same object, not a copy. Two lists agreeing today can disagree
    after one edit, and a widening here would go unnoticed."""
    from tradefloor.counterfactual import MACRO_FIELDS
    assert ci.OBSERVABLE_MACRO is MACRO_FIELDS
    assert "qe_pe_boost" not in ci.OBSERVABLE_MACRO


def test_the_sealed_list_names_real_engine_attributes():
    """A sealed name that does not exist on the engine seals nothing.
    `fair_value` sat in this list and could never fire -- it is a module
    function, not an engine attribute -- which is the tell that the list
    was written from intent rather than from the engine's surface. This
    pins every entry to the surface, so the list cannot rot silently
    again."""
    world = make_world(callable_agent(hold))
    missing = [name for name in SEALED if not hasattr(world.engine, name)]
    assert not missing, (
        f"SEALED names attributes the engine does not have: {missing}. "
        "They seal nothing; fix the name or remove it.")


def test_the_serializer_never_touches_ground_truth():
    agent = callable_agent(hold)
    world = make_world(agent)
    world.run(days=2)
    obs = _observation(world, engine=Sealed(world.engine))
    payload = ci.serialize_observation(obs, history=agent.history)
    assert payload["assets"], "the sealed run produced nothing to check"


def test_returns_are_absent_rather_than_zero_before_they_are_observable():
    world = make_world(callable_agent(hold))
    world.run(days=1)
    payload = ci.serialize_observation(_observation(world), history=[])
    asset = payload["assets"][0]
    assert asset["return_1d"] is None and asset["volatility"] is None


def test_fundamentals_are_supplied_not_discovered():
    world = make_world(callable_agent(hold))
    world.run(days=1)
    obs = _observation(world)
    bare = ci.serialize_observation(obs)
    assert all(a["fundamentals"] == {} for a in bare["assets"])
    given = ci.serialize_observation(
        obs, fundamentals={"TECH_A": {"revenue_growth": 0.30}})
    assert given["assets"][0]["fundamentals"] == {"revenue_growth": 0.30}


# -- the decision model ------------------------------------------------------


def test_action_is_a_validating_constructor():
    """signed() returns 0.0 for a side it does not recognise, so a
    hand-built Action("A", "SHORT", 5) handed to the public orders_from
    silently became a hold, and a negative quantity flipped the trade's
    sign through the same arithmetic. Every documented path revalidates
    through parse_decision; the constructor now refuses what only a
    hand-builder could reach."""
    with pytest.raises(ci.DecisionError, match="side"):
        ci.Action("A", "SHORT", 5)
    with pytest.raises(ci.DecisionError, match="non-negative"):
        ci.Action("A", "SELL", -5)
    with pytest.raises(ci.DecisionError, match="finite"):
        ci.Action("A", "BUY", float("inf"))
    assert ci.Action("A", "HOLD").signed() == 0.0
    assert ci.Action("A", "SELL", 5).signed() == -5.0


def test_a_decision_parses_from_all_three_shapes():
    as_dict = {"actions": [{"symbol": "A", "side": "buy", "quantity": 5}],
               "rationale": "why"}
    from_dict = ci.parse_decision(as_dict)
    from_text = ci.parse_decision(json.dumps(as_dict))
    from_decision = ci.parse_decision(from_dict)
    assert from_dict == from_text == from_decision
    assert from_dict.actions[0].side == "BUY"
    assert from_dict.actions[0].signed() == 5.0


def test_a_fenced_answer_is_accepted():
    """Models add code fences and closing sentences whatever the
    instructions say. Adapters measure portfolio decisions, not instruction
    compliance."""
    text = ('Here is my decision:\n```json\n'
            '{"actions": [{"symbol": "A", "side": "SELL", '
            '"quantity": 30}], "rationale": "rates"}\n```\nLet me know.')
    assert ci.parse_decision(text).actions[0].signed() == -30.0


@pytest.mark.parametrize("raw,match", [
    ("", "empty"),
    ("no json here at all", "no JSON object"),
    ("[1, 2, 3]", "got a list"),
    ({"actions": "TECH_A"}, "must be a list"),
    ({"actions": [5]}, "not an object"),
    ({"actions": [{"side": "BUY", "quantity": 1}]}, "no usable 'symbol'"),
    ({"actions": [{"symbol": "A", "side": "SHORT", "quantity": 1}]},
     "not one of"),
    ({"actions": [{"symbol": "A", "side": "BUY", "quantity": -5}]},
     "negative"),
    ({"actions": [{"symbol": "A", "side": "BUY", "quantity": "lots"}]},
     "not a number"),
    ({"actions": [{"symbol": "A", "side": "HOLD", "quantity": 10}]}, "HOLD"),
    ({"actions": [{"symbol": "A", "side": "BUY", "quantity": 1},
                  {"symbol": "A", "side": "SELL", "quantity": 1}]},
     "more than once"),
    ({"actions": [], "rationale": 5}, "must be a string"),
    (42, "cannot read a decision"),
    (None, "cannot read a decision"),
    ({}, "no 'actions' key"),
    ({"messages": ["done"], "next": "end"}, "no 'actions' key"),
    ('{"rationale": "thinking"}', "no 'actions' key"),
    ({"actions": [{"symbol": "A", "side": "BUY", "quantity": 1,
                   "stop_loss": 90}]}, "unknown fields"),
    ({"actions": [{"symbol": "A", "side": "BUY", "quantity": 1,
                   "time_in_force": "GTC"}]}, "no stop loss"),
    ({"actions": [], "confidence": 0.9}, "unknown keys"),
])
def test_invalid_output_is_refused_readably(raw, match):
    with pytest.raises(ci.DecisionError, match=match):
        ci.parse_decision(raw)


def test_an_absent_actions_key_is_refused_and_an_empty_list_is_not():
    """The silent-hold trap, pinned. A framework envelope with no 'actions'
    key -- LangGraph state is `{"messages": [...]}` -- used to validate as
    a considered HOLD: trades=0, errors empty, indistinguishable from an
    agent that declined. Absent now raises, naming the keys it got; empty
    stays a valid, deliberate no-op; null counts as present-and-declined."""
    with pytest.raises(ci.DecisionError) as excinfo:
        ci.parse_decision({"messages": ["step complete"]})
    message = str(excinfo.value)
    assert "messages" in message, "the refusal must name the keys it found"
    assert "empty" in message.lower(), (
        "the refusal must say how a decision declines to trade")
    assert ci.parse_decision({"actions": []}).actions == []
    assert ci.parse_decision({"actions": None}).actions == []


def test_an_order_type_other_than_market_is_refused_by_name():
    """Tradefloor executes market sweeps only. A limit instruction silently
    downgraded to market would execute a trade the agent priced as
    protected, so the refusal has to name the missing capability."""
    with pytest.raises(ci.DecisionError, match="market sweeps only"):
        ci.parse_decision({"actions": [{"symbol": "A", "side": "BUY",
                                        "quantity": 1,
                                        "order_type": "limit"}]})
    accepted = ci.parse_decision(
        {"actions": [{"symbol": "A", "side": "BUY", "quantity": 1,
                      "order_type": "MARKET"}]})
    assert accepted.actions[0].quantity == 1.0


def test_a_limit_price_is_refused_by_name():
    with pytest.raises(ci.DecisionError, match="no limit orders"):
        ci.parse_decision({"actions": [{"symbol": "A", "side": "BUY",
                                        "quantity": 1,
                                        "limit_price": 99.5}]})


def test_the_json_schema_matches_the_validator():
    schema = ci.decision_schema()
    action = schema["properties"]["actions"]["items"]
    assert action["properties"]["side"]["enum"] == list(ci.SIDES)
    assert action["additionalProperties"] is False, (
        "additionalProperties: false is what stops a schema-bound model "
        "emitting order_type or limit_price")
    assert schema["additionalProperties"] is False
    assert action["properties"]["quantity"]["minimum"] == 0


def test_the_schema_is_a_fresh_copy_per_call():
    """Callers hand schemas to libraries that annotate them in place; one
    framework's bookkeeping must not leak into the next adapter's
    contract."""
    first = ci.decision_schema()
    first["properties"]["actions"]["items"]["properties"]["side"]["enum"] \
        .append("SHORT")
    assert "SHORT" not in (ci.decision_schema()["properties"]["actions"]
                           ["items"]["properties"]["side"]["enum"])


def test_the_pydantic_model_is_built_on_demand_and_round_trips():
    pydantic = pytest.importorskip("pydantic")
    model = ci.decision_model()
    assert ci.decision_model() is model, (
        "two adapters asking for the model must get the same class, or an "
        "isinstance check between them means nothing")
    instance = model(actions=[{"symbol": "A", "side": "buy", "quantity": 3}],
                     rationale="why")
    decision = ci.parse_decision(instance.model_dump())
    assert decision.actions == [ci.Action("A", "BUY", 3.0)]
    with pytest.raises(pydantic.ValidationError):
        model(actions=[{"symbol": "A", "side": "BUY", "quantity": 1,
                        "order_type": "limit"}])


#: One corpus, driven through BOTH validation paths. Schema equality was
#: necessary and not sufficient: parse_decision once silently dropped a
#: stop_loss the model refused, the schemas still agreed, and the schema
#: comparison passed -- so a LangGraph adapter on the dict path and a
#: PydanticAI adapter binding the model would have validated the same
#: output differently, two arms of one study running two contracts.
#: Behavioural agreement is asserted here, on the inputs themselves. The
#: three tolerated spellings excluded from the corpus are pinned in
#: `test_the_documented_asymmetries_between_the_two_paths`.
_ACT = {"symbol": "A", "side": "BUY", "quantity": 5}
DECISION_CORPUS = [
    ("valid-buy", {"actions": [dict(_ACT)]}, True),
    ("valid-lowercase-side",
     {"actions": [{"symbol": "A", "side": "sell", "quantity": 5}]}, True),
    ("valid-hold", {"actions": [{"symbol": "A", "side": "HOLD"}]}, True),
    ("valid-empty-actions", {"actions": []}, True),
    ("valid-rationale", {"actions": [], "rationale": "waiting"}, True),
    ("negative-quantity",
     {"actions": [{"symbol": "A", "side": "BUY", "quantity": -5}]}, False),
    ("unknown-side",
     {"actions": [{"symbol": "A", "side": "SHORT", "quantity": 5}]}, False),
    ("empty-symbol",
     {"actions": [{"symbol": "", "side": "BUY", "quantity": 5}]}, False),
    ("hold-with-quantity",
     {"actions": [{"symbol": "A", "side": "HOLD", "quantity": 10}]}, False),
    ("duplicate-symbols",
     {"actions": [{"symbol": "A", "side": "BUY", "quantity": 1},
                  {"symbol": "A", "side": "SELL", "quantity": 1}]}, False),
    ("missing-actions", {"rationale": "an envelope with no actions"}, False),
    ("framework-envelope", {"messages": ["I am done thinking."]}, False),
    ("stop-loss", {"actions": [dict(_ACT, stop_loss=90.0)]}, False),
    ("take-profit", {"actions": [dict(_ACT, take_profit=120.0)]}, False),
    ("time-in-force", {"actions": [dict(_ACT, time_in_force="GTC")]}, False),
    ("limit-order-type", {"actions": [dict(_ACT, order_type="limit")]},
     False),
    ("limit-price", {"actions": [dict(_ACT, limit_price=99.0)]}, False),
    ("unknown-top-level-key", {"actions": [], "confidence": 0.9}, False),
    # Pydantic's allow_inf_nan default admitted an INFINITE quantity
    # through a field whose schema said minimum 0; parse_decision refused
    # it all along. Both non-finite spellings are pinned on both paths.
    ("infinite-quantity",
     {"actions": [dict(_ACT, quantity=float("inf"))]}, False),
    ("nan-quantity",
     {"actions": [dict(_ACT, quantity=float("nan"))]}, False),
]


@pytest.mark.parametrize(
    "raw,accepted", [(raw, ok) for _, raw, ok in DECISION_CORPUS],
    ids=[name for name, _, _ in DECISION_CORPUS])
def test_the_two_validation_paths_agree_on_accept_versus_refuse(raw,
                                                                accepted):
    pydantic = pytest.importorskip("pydantic")

    try:
        ci.parse_decision(raw)
        dict_path = True
    except ci.DecisionError:
        dict_path = False
    try:
        ci.decision_model()(**raw)
        model_path = True
    except pydantic.ValidationError:
        model_path = False

    assert dict_path == accepted, "parse_decision disagrees with the corpus"
    assert model_path == accepted, "the model disagrees with the corpus"


def test_the_documented_asymmetries_between_the_two_paths():
    """Three spellings are tolerated on the dict path and unemittable on the
    model path, deliberately, and this pins both halves so a drift in
    either direction fails a test.

    `order_type` of "market" or null, `limit_price` of null, and `actions`
    of null all STATE THE DEFAULT: a market sweep, no limit, no trade. The
    dict path serves unconstrained LLM text, where refusing a redundant
    statement of the only execution mode that exists would score a correct
    decision as a failure. The model path forbids the keys outright because
    a schema-bound framework can always emit the constrained spelling, and
    `additionalProperties: false` is what makes order_type unemittable at
    the provider. No intent is dropped and no arms can diverge on it: every
    tolerated spelling EXECUTES identically to its constrained form."""
    pydantic = pytest.importorskip("pydantic")
    model = ci.decision_model()

    market = {"actions": [dict(_ACT, order_type="MARKET")]}
    nulls = {"actions": [dict(_ACT, order_type=None, limit_price=None)]}
    assert ci.parse_decision(market).actions[0].quantity == 5.0
    assert ci.parse_decision(nulls).actions[0].quantity == 5.0
    assert ci.parse_decision({"actions": None}).actions == []

    for tolerated in (market, nulls, {"actions": None}):
        with pytest.raises(pydantic.ValidationError):
            model(**tolerated)


def test_the_two_renderings_of_the_contract_agree():
    """decision_schema() is the canonical statement and the Pydantic model
    derives its constraints FROM it -- this asserts the derivation held, on
    every field, its type, its constraints and its required-ness. The first
    model repeated the constraints by hand and drifted on all of them:
    quantity=-5 validated, HOLD with a quantity validated, duplicate
    symbols validated, and a framework binding the model accepted decisions
    that then died in parse_decision -- after its retry loop closed, where
    it had one, and on the single call where it did not."""
    pytest.importorskip("pydantic")
    hand = ci.decision_schema()
    hand_action = hand["properties"]["actions"]["items"]
    rendered = ci.decision_model().model_json_schema()
    ref = rendered["properties"]["actions"]["items"]["$ref"]
    action = rendered["$defs"][ref.rsplit("/", 1)[-1]]

    assert rendered["additionalProperties"] is False
    assert action["additionalProperties"] is False
    assert set(rendered["required"]) == set(hand["required"])
    assert set(action["required"]) == set(hand_action["required"])
    assert set(action["properties"]) == set(hand_action["properties"])

    side = action["properties"]["side"]
    assert side.get("enum", side.get("const")) == \
        hand_action["properties"]["side"]["enum"]
    quantity = action["properties"]["quantity"]
    assert quantity["minimum"] == \
        hand_action["properties"]["quantity"]["minimum"]
    assert quantity["type"] == "number"
    assert quantity["description"] == \
        hand_action["properties"]["quantity"]["description"]
    symbol = action["properties"]["symbol"]
    assert symbol["minLength"] == \
        hand_action["properties"]["symbol"]["minLength"]
    assert rendered["properties"]["actions"]["description"] == \
        hand["properties"]["actions"]["description"]
    assert rendered["properties"]["rationale"]["description"] == \
        hand["properties"]["rationale"]["description"]


# -- the event-loop bridge ---------------------------------------------------


def test_run_sync_from_a_plain_synchronous_context():
    async def answer():
        return 41 + 1

    assert ci.run_sync(answer()) == 42


def test_run_sync_from_inside_a_running_event_loop():
    """The notebook case, and the reason the bridge exists: the frameworks'
    own sync entry points raise here, so an adapter built on them works in
    a script and dies in Jupyter."""
    import asyncio

    async def answer():
        return "from the loop"

    async def outer():
        return ci.run_sync(answer())

    assert asyncio.run(outer()) == "from the loop"


def test_run_sync_preserves_the_exception_chain():
    async def boom():
        try:
            raise KeyError("inner")
        except KeyError as exc:
            raise ValueError("outer") from exc

    with pytest.raises(ValueError, match="outer") as excinfo:
        ci.run_sync(boom())
    assert isinstance(excinfo.value.__cause__, KeyError)


def test_run_sync_preserves_the_chain_across_the_thread_boundary():
    """The running-loop path crosses a thread; the exception must arrive as
    the original object, chain intact, so FrameworkAdapter.act still wraps
    the real error."""
    import asyncio

    async def boom():
        raise RuntimeError("threaded failure") from KeyError("root")

    async def outer():
        with pytest.raises(RuntimeError, match="threaded") as excinfo:
            ci.run_sync(boom())
        return isinstance(excinfo.value.__cause__, KeyError)

    assert asyncio.run(outer())


def test_run_sync_refuses_a_non_awaitable_with_a_sentence():
    with pytest.raises(tf.ValidationError, match="coroutine"):
        ci.run_sync(42)


# -- market-stage validation -------------------------------------------------


def test_an_unlisted_symbol_is_refused_rather_than_skipped():
    world = make_world(callable_agent(hold))
    world.run(days=1)
    decision = ci.parse_decision(
        {"actions": [{"symbol": "NOT_LISTED", "side": "BUY",
                      "quantity": 10}]})
    with pytest.raises(ci.MarketRefusalError, match="not listed"):
        ci.orders_from(decision, _observation(world))


def test_an_oversized_order_is_clipped_and_the_clip_is_recorded():
    world = make_world(callable_agent(hold))
    world.run(days=1)
    obs = _observation(world)
    cap = ci.MAX_PARTICIPATION * obs.avg_volume("TECH_A")
    decision = ci.parse_decision(
        {"actions": [{"symbol": "TECH_A", "side": "BUY",
                      "quantity": cap * 10}]})
    orders, notes = ci.orders_from(decision, obs)
    assert orders["TECH_A"] == pytest.approx(cap)
    assert len(notes) == 1 and "clipped" in notes[0]


def test_sub_share_dust_produces_no_order():
    world = make_world(callable_agent(hold))
    world.run(days=1)
    decision = ci.parse_decision(
        {"actions": [{"symbol": "TECH_A", "side": "BUY", "quantity": 0.4}]})
    orders, _notes = ci.orders_from(decision, _observation(world))
    assert orders == {}


def test_a_zero_cap_means_no_order_not_no_cap():
    """`if cap > 0` once read a zero cap as NO cap: with avg_volume or
    max_participation at zero, a 1e12-share order passed through whole
    with no clip note, while serialize_observation showed the agent
    max_order_shares of exactly 0.0 -- the observation and the enforcement
    stating opposite things, and the function accepting the exact order it
    exists to refuse. A zero cap now clips to zero, the order falls out as
    dust, and the note says so."""
    world = make_world(callable_agent(hold))
    world.run(days=1)
    obs = _observation(world)
    decision = ci.parse_decision(
        {"actions": [{"symbol": "TECH_A", "side": "BUY",
                      "quantity": 1e12}]})
    orders, notes = ci.orders_from(decision, obs, max_participation=0.0)
    assert orders == {}
    assert len(notes) == 1 and "clipped to 0" in notes[0], notes


@pytest.mark.parametrize("text", [
    '{"actions": [], '
    '"actions": [{"symbol": "A", "side": "BUY", "quantity": 9999}]}',
    '{"actions": [{"symbol": "A", "side": "BUY", "quantity": 9999}], '
    '"actions": []}',
], ids=["hold-then-buy", "buy-then-hold"])
def test_duplicate_json_keys_are_refused_not_resolved_by_order(text):
    """json.loads keeps the LAST occurrence, so which of the model's two
    statements reached the market depended on emission order, and nothing
    recorded that another existed. The mapping layer refuses duplicate
    SYMBOLS for exactly this reason; the JSON layer now applies the same
    principle to keys, in both orderings."""
    with pytest.raises(ci.DecisionError, match="more than once"):
        ci.parse_decision(text)


# -- recording and replay ----------------------------------------------------


def test_the_digest_keys_on_the_exact_input():
    assert ci.digest("a") == ci.digest("a")
    assert ci.digest("a") != ci.digest("a ")


def test_the_digest_of_a_payload_ignores_key_order():
    assert ci.digest({"b": 1, "a": 2}) == ci.digest({"a": 2, "b": 1})


def test_jsonable_renders_what_json_cannot():
    """Promoted from two byte-identical adapter copies. Digesting a
    framework config raised TypeError out of json.dumps -- not a
    ValidationError, so a caller catching the library's refusals did not
    catch it -- on configurations released adapters accepted. The
    unrepresentable parts become their type name, because a digest has to
    be stable and one-way, never round-trip."""
    config = {"model": "m", "client": object(), "callbacks": [print],
              "nested": {"filter": len, "keep": 1}, 5: "int-key"}
    rendered = ci.jsonable(config)
    json.dumps(rendered)                    # the whole point
    assert rendered["client"] == "<object>"
    assert rendered["callbacks"][0].startswith("<")
    assert rendered["nested"]["keep"] == 1
    assert rendered["5"] == "int-key"
    assert ci.digest(rendered)              # and it digests
    assert ci.jsonable(ci.Decision([ci.Action("A", "BUY", 1)])) == {
        "actions": [{"symbol": "A", "side": "BUY", "quantity": 1.0}],
        "rationale": ""}


def test_a_transcript_round_trips():
    transcript = ci.Transcript(meta={"framework": "x"})
    transcript.record({"digest": "abc", "response": "hello", "step": 0})
    again = ci.Transcript.from_json(transcript.to_json())
    assert again.meta == {"framework": "x"}
    assert again.response_for("abc") == "hello"
    assert again.response_for("missing") is None


def test_replay_returns_the_recorded_response():
    transcript = ci.Transcript()
    transcript.record({"digest": "abc", "response": "the answer"})
    assert ci.replay_response(transcript, "abc", step=6, day=1) \
        == "the answer"


def test_a_missing_recording_raises_and_says_which_step():
    with pytest.raises(ci.DecisionError) as excinfo:
        ci.replay_response(ci.Transcript(), "missing", step=12, day=2)
    message = str(excinfo.value)
    assert "step 12" in message
    assert "Re-record" in message, (
        "the message has to say how to fix it, because the usual cause is "
        "an intentional change to the observation mapping")


def test_a_recorded_null_response_is_not_diagnosed_as_missing():
    """response_for returns None both for 'no entry' and 'entry recorded
    with a null response' -- two situations with OPPOSITE remedies. The
    missing-key message told the user their inputs had changed and to
    re-record the run, about an entry sitting right there; that spends
    money rediscovering a file they already have."""
    transcript = ci.Transcript()
    transcript.record({"digest": "k", "response": None, "step": 3})
    with pytest.raises(ci.DecisionError) as excinfo:
        ci.replay_response(transcript, "k", step=3, day=0)
    message = str(excinfo.value)
    assert "null response" in message
    assert "this interaction" in message, (
        "the remedy is re-recording ONE interaction, and the message must "
        "say so")
    assert "none for this input" not in message, (
        "the missing-key diagnosis is the wrong one here")


# -- adapter metadata --------------------------------------------------------


def test_adapter_info_digests_a_config_rather_than_holding_it():
    """The named fields have no place for a key, and a digest of a config
    that contains one is one-way."""
    config = {"model": "m", "api_key": "sk-secret-value"}
    info = ci.AdapterInfo(framework="example", framework_version="1.2",
                          provider="prov", model="m", agent_name="agent",
                          instructions_digest=ci.digest("mandate"),
                          config_digest=ci.digest(config))
    published = json.dumps(info.as_dict())
    assert "sk-secret-value" not in published
    assert "api_key" not in published
    assert set(info.as_dict()) == {
        "framework", "framework_version", "provider", "model", "agent_name",
        "entry_point", "mode", "framework_url", "instructions_version",
        "instructions_digest", "config_digest", "generation", "extra",
        "decision_schema_version"}


def test_adapter_info_carries_a_full_recorded_runs_provenance():
    """The shipped FinRobot fixture's meta holds twelve keys, and the first
    AdapterInfo had a home for four of them. Every one must have a place
    now -- a named field, the generation mapping, the extra mapping, or the
    adapter's provenance() -- without loss."""
    info = ci.AdapterInfo(
        framework="FinRobot", framework_version="0.1.5",
        provider="anthropic", model="claude-sonnet-5",
        entry_point="SingleAssistant", mode="replay",
        framework_url="https://github.com/AI4Finance-Foundation/FinRobot",
        instructions_version="1",
        generation={"temperature": 0.2},
        extra={"recorded": True, "mandate_version": "1"})
    published = info.as_dict()
    assert published["entry_point"] == "SingleAssistant"
    assert published["mode"] == "replay"
    assert published["framework_url"].endswith("FinRobot")
    assert published["generation"]["temperature"] == 0.2
    assert published["extra"] == {"recorded": True, "mandate_version": "1"}
    # The remaining two fixture keys are the adapter's own settings and
    # ride provenance(), tested below. The mappings are copies: mutating
    # the published dict must not reach the info.
    published["extra"]["recorded"] = False
    assert info.extra["recorded"] is True


def test_the_adapter_assembles_transcript_provenance():
    """provenance() is the one dictionary a recorder writes into
    Transcript.meta: the info plus the Tradefloor-side settings a replay
    cannot reconstruct -- the cadence and the participation cap."""
    agent = callable_agent(hold, every=3)
    provenance = agent.provenance()
    assert provenance["decision_every_steps"] == 3
    assert provenance["max_participation"] == ci.MAX_PARTICIPATION
    assert provenance["framework"] == "callable"
    assert provenance["decision_schema_version"] == ci.DECISION_SCHEMA_VERSION


@pytest.mark.parametrize("bad", [
    {"api_key": "value"},
    {"auth_token": "value"},
    {"Authorization": "value"},
    {"nested": {"client_secret": "value"}},
    {"params": [{"session_cookie": "value"}]},
], ids=["api-key", "token", "authorization", "nested", "in-a-list"])
def test_adapter_info_refuses_credential_shaped_keys(bad):
    """The open mappings moved the no-credentials boundary from shape to
    validation, so the validation is tested on the shapes configs actually
    take, nesting included."""
    with pytest.raises(tf.ValidationError, match="credential"):
        ci.AdapterInfo(framework="x", extra=bad)
    with pytest.raises(tf.ValidationError, match="credential"):
        ci.AdapterInfo(framework="x", generation=bad)


def test_adapter_info_refuses_a_secret_shaped_value():
    with pytest.raises(tf.ValidationError, match="secret"):
        ci.AdapterInfo(framework="x", extra={"note": "sk-proj-abc123"})


def test_the_key_scan_matches_the_head_segment_not_a_substring():
    """'token' in 'max_tokens' is True, and max_tokens is the second most
    common generation parameter in existence -- `generation` exists to
    carry exactly those, so a substring scan refuses the field's primary
    use case, and a guard that fires on that gets worked around. English
    compounds are head-final: api_key IS a key, token_budget is a budget
    ABOUT tokens, cookie_policy is a policy. So the scan compares the
    FINAL segment, exactly and in the singular. Both directions pinned so
    the anchoring cannot silently regress in either."""
    accepted = ci.AdapterInfo(
        framework="x",
        generation={"temperature": 0.0, "max_tokens": 512,
                    "max_completion_tokens": 512, "top_p": 0.9},
        extra={"token_budget": 1000, "cookie_policy": "none"})
    assert accepted.generation["max_tokens"] == 512
    assert accepted.extra["cookie_policy"] == "none"

    for bad in ("api_key", "auth_token", "Authorization", "session_secret",
                "apiKey", "X-Api-Key", "apikey", "access-token", "auth"):
        with pytest.raises(tf.ValidationError, match="credential"):
            ci.AdapterInfo(framework="x", extra={bad: "value"})
        with pytest.raises(tf.ValidationError, match="credential"):
            ci.AdapterInfo(framework="x", generation={bad: "value"})


def test_the_credential_scan_is_anchored_not_a_substring_match():
    """A bare "sk-" substring matches "risk-adjusted" and "task-based", and
    a credential check that cries wolf on prose gets weakened or deleted,
    which costs more than the hole it closes. Both contract checks
    delegate to this one rule, so pinning it here pins them: prose passes,
    a real key fails -- even one EMBEDDED mid-sentence, which the earlier
    startswith anchor let through."""
    prose = "risk-adjusted sizing over a brisk-moving, task-based book"
    info = ci.AdapterInfo(framework="x", extra={"note": prose})
    assert info.extra["note"] == prose

    for value in ("sk-proj-abc123",
                  "the config carried sk-live-abc123 by mistake",
                  "Authorization: Bearer abc123"):
        with pytest.raises(tf.ValidationError, match="secret"):
            ci.AdapterInfo(framework="x", extra={"note": value})


def test_adapter_info_refuses_unserialisable_metadata():
    with pytest.raises(tf.ValidationError, match="JSON"):
        ci.AdapterInfo(framework="x", extra={"engine": object()})


def test_adapter_info_stamps_the_schema_version_itself():
    info = ci.AdapterInfo(framework="example")
    assert info.decision_schema_version == ci.DECISION_SCHEMA_VERSION


def test_the_credential_scan_cannot_be_bypassed_after_construction():
    """The scan once ran on a SHALLOW copy, so nested structures stayed
    shared with the caller's object: build a config, hand it over, keep
    using it -- the completely normal shape -- and anything added
    afterwards was unscanned and reached provenance(), which is what gets
    written into committed transcript meta. The stored form is now a deep
    copy taken before validation, and the published form is a deep copy of
    the stored one, so neither direction hands out a handle."""
    cfg = {"client": {"note": "clean"}}
    info = ci.AdapterInfo(framework="x", extra=cfg)

    cfg["client"]["note"] = "sk-proj-INSERTED-AFTER-THE-SCAN"
    assert info.extra["client"]["note"] == "clean"
    assert "INSERTED" not in json.dumps(info.as_dict())

    published = info.as_dict()
    published["extra"]["client"]["note"] = "sk-proj-VIA-AS-DICT"
    assert info.extra["client"]["note"] == "clean"


def test_the_reference_string_cites_the_run():
    """`RunManifest.of(strategy=...)` takes a reference string for an agent
    that is not a StrategySpec; this is that string, so it has to carry
    what a reader needs to find the setup."""
    info = ci.AdapterInfo(framework="example", framework_version="1.2",
                          provider="prov", model="m")
    reference = info.reference()
    assert "example 1.2" in reference
    assert "prov m" in reference
    assert "decision schema" in reference


# -- the base adapter --------------------------------------------------------


def test_the_base_adapter_refuses_a_bad_cadence():
    with pytest.raises(tf.ValidationError, match="every"):
        callable_agent(hold, every=0)


def test_the_base_adapter_asks_on_the_cadence_and_not_every_step():
    calls = []

    def respond(payload):
        calls.append(payload["step"])
        return {"actions": []}

    world = make_world(callable_agent(respond, every=6))
    world.run(days=3)
    assert world.step == 18
    assert calls == [0, 6, 12]


def test_the_price_memory_is_bounded():
    agent = callable_agent(hold, every=6)
    world = make_world(agent)
    world.run(days=7)       # 42 steps, above HISTORY_STEPS
    assert len(agent.history) == ci.HISTORY_STEPS


def test_ask_is_abstract_on_the_base():
    adapter = ci.FrameworkAdapter()
    with pytest.raises(NotImplementedError, match="ask"):
        adapter.ask(None, {})


def test_hand_built_arms_with_different_instructions_do_not_agree():
    """The hole state() closes: a forked pair shares one info, but two arms
    built by hand can run different instructions, and before the digest
    entered state() they agreed on every check."""
    def arm(mandate):
        return callable_agent(hold, info=ci.AdapterInfo(
            framework="callable", instructions_digest=ci.digest(mandate)))

    control, treatment = arm("mandate v1"), arm("mandate v2")
    assert control.state() != treatment.state()
    assert arm("mandate v1").state() == control.state(), (
        "identical instructions must still agree, or every honest fork "
        "starts reporting divergence")


# -- the replay mixin --------------------------------------------------------


def test_replay_mode_refuses_without_a_transcript():
    with pytest.raises(tf.ValidationError, match="transcript"):
        callable_agent(hold, mode="replay")


def test_an_unknown_mode_is_refused():
    with pytest.raises(tf.ValidationError, match="replay"):
        callable_agent(hold, mode="dry-run")


def test_the_mixin_hooks_are_both_required():
    """Both hooks raise on the base, so an adapter that forgets one fails
    on the first decision rather than silently keying or calling wrong."""
    class Bare(ci.ReplayMixin, ci.FrameworkAdapter):
        pass

    agent = Bare(mode="live")
    with pytest.raises(NotImplementedError, match="prepare"):
        agent.prepare(None, {})
    with pytest.raises(NotImplementedError, match="call"):
        agent.call(None, {})


def test_a_live_run_records_and_then_replays_identically():
    """The property the mixin exists for: record once, replay forever. The
    replay calls no framework -- the function under the adapter EXPLODES if
    touched -- and reproduces the same orders on the same market."""
    recorder = ci.Transcript(meta={"framework": "callable"})
    live = callable_agent(buy, recorder=recorder)
    first = make_world(live)
    first.run(days=2)
    assert len(recorder) == 2
    assert set(recorder.entries[0]) == {"arm", "step", "day", "digest",
                                        "prompt", "response"}

    def exploding(payload):
        raise AssertionError("replay must not call the framework")

    replayer = callable_agent(
        exploding, mode="replay",
        transcript=ci.Transcript.from_json(recorder.to_json()))
    second = make_world(replayer)
    second.run(days=2)
    assert second.digest() == first.digest()
    assert [e["orders"] for e in replayer.record] \
        == [e["orders"] for e in live.record]


def test_a_replay_miss_refuses_and_names_the_step():
    transcript = ci.Transcript()
    transcript.record({"digest": "not-this-input", "response": "{}"})
    world = make_world(callable_agent(buy, mode="replay",
                                      transcript=transcript))
    with pytest.raises(ci.DecisionError, match="step 0"):
        world.run(days=1)


def test_key_material_and_sent_prompt_are_separable():
    """The LangGraph shape: key on {payload, instructions} while sending a
    rendered prompt. The two are separate outputs of prepare() on purpose
    -- here, changing the INSTRUCTIONS alone changes the replay key while
    the sent prompt stays identical, which is exactly the property a
    hash-what-you-send hook could not express."""
    class Rendered(CallableAgentAdapter):
        instructions = "mandate v1"

        def prepare(self, obs, payload):
            material = {"payload": payload,
                        "instructions": self.instructions}
            return material, f"RENDERED step {payload['step']}"

        def call(self, obs, prompt):
            return buy(prompt)

    first = Rendered(buy)
    make_world(first).run(days=1)
    entry = first.record[0]
    assert entry["prompt"] == "RENDERED step 0"
    assert entry["digest"] != ci.digest(entry["prompt"])

    class Retold(Rendered):
        instructions = "mandate v2"

    second = Retold(buy)
    make_world(second).run(days=1)
    assert second.record[0]["prompt"] == entry["prompt"]
    assert second.record[0]["digest"] != entry["digest"]


def test_a_fork_shares_the_transcript_and_the_recorder():
    """Both directions of the sharing matter: a replay of one arm must read
    the same recorded run as its sibling, and a live recording of both
    arms belongs in one file."""
    recorder = ci.Transcript()
    world = make_world(callable_agent(buy, recorder=recorder))
    world.run(days=2)
    control, shock = world.fork("control", "shock")
    assert control.agent.recorder is shock.agent.recorder is recorder
    assert control.agent.mode == "live"
    assert type(control.agent) is CallableAgentAdapter


def test_a_custom_exchange_key_is_honoured():
    """An adapter that computed its own transcript digest records THAT key,
    so the record joins to the transcript it actually wrote."""
    class Keyed(CallableAgentAdapter):
        def ask(self, obs, payload):
            self.record_exchange("the exact rendered input", key="cafe1234")
            return self.fn(payload)

    world = make_world(Keyed(buy))
    world.run(days=1)
    entry = world.agent.record[0]
    assert entry["digest"] == "cafe1234"
    assert entry["prompt"] == "the exact rendered input"
    assert entry["response"]["actions"][0]["symbol"] == "TECH_A"
