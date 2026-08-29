"""The FinRobot integration, without FinRobot.

CI does not call an LLM. It cannot: a live decision needs an API key, costs
money and answers differently every time, so a suite that depended on one
would be neither reproducible nor free. What is checked here instead is
everything the integration is actually responsible for -- the observation
mapping, the ground-truth boundary, the validation of generated output, the
execution of a validated decision, the fork, and the replay of a genuine
recorded run.

The recorded run is the load-bearing part. `tests/fixtures/finrobot/` holds
real interactions from a real FinRobot agent, and
`test_the_recorded_run_replays_end_to_end` re-executes the whole experiment
against them. That is a stronger test than a mock: the responses were not
written to make the parser happy, they are what the model actually said,
including the code fences and the trailing sentences the mandate asked it not
to produce.

Nothing in this file imports `finrobot`, and one of the tests asserts that
the adapter does not either until it is asked to go live.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys

import pytest

import tradefloor as tf
from tradefloor.counterfactual import World, agree, compare
from tradefloor.integrations import finrobot as fr

REPO = pathlib.Path(__file__).resolve().parent.parent
FIXTURE = REPO / "tests" / "fixtures" / "finrobot" / "rate-shock.json"
EXAMPLE = REPO / "examples" / "finrobot" / "rate_shock.py"

#: A two-name market, deliberately not the example's four. The example is the
#: experiment; these are unit tests, and a smaller roster makes an assertion
#: about a rendered block readable.
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


class Scripted(fr.FinRobotAdapter):
    """A FinRobotAdapter whose answers are supplied rather than generated.

    It replaces `_ask`, which is the ONE method that reaches outside the
    process. Everything below it -- the observation mapping, the render, the
    parse, the validation, the execution, the fork -- is the shipped code
    running unmodified, which is the point: a double that reimplemented any of
    that would be testing itself.

    ``script`` is keyword-defaulted because `FinRobotAdapter.fork` rebuilds
    the agent as ``type(self)(**keyword_args)``, so a subclass with a
    required positional cannot be forked. That constraint is real and is why
    the base class is keyword-only throughout.
    """

    def __init__(self, script=None, **kwargs):
        kwargs.setdefault("mode", "live")
        kwargs.setdefault("llm_config", {"config_list": [{"model": "none"}]})
        super().__init__(**kwargs)
        self.script = script
        self.prompts: list[str] = []

    def _ask(self, prompt, key, obs):
        self.prompts.append(prompt)
        answer = self.script(obs) if callable(self.script) else self.script
        return answer

    def fork(self):
        twin = super().fork()
        twin.script = self.script
        twin.prompts = list(self.prompts)
        return twin


def answer(*actions, rationale="because") -> str:
    return json.dumps({"actions": list(actions), "rationale": rationale})


def act(symbol, side, quantity=None) -> dict:
    out = {"symbol": symbol, "side": side}
    if quantity is not None:
        out["quantity"] = quantity
    return out


def one_observation(agent=None, *, days: int = 1) -> tuple[World, object]:
    """Run a world far enough to have an observation with some history."""
    agent = agent or Scripted(answer())
    world = World(seed=7, universe=universe(), agent=agent, cash=1_000_000.0,
                  pins={"federal_funds_rate": 0.04,
                        "corporate_bond_yield": 0.055})
    world.run(days=days)
    return world, agent


# -- the optional dependency ------------------------------------------------


def test_the_adapter_imports_without_finrobot():
    """The whole reason the import is inside a method.

    A reader replaying a recorded run should never be asked to install a
    dependency tree whose current pins reach `torch`, and `import tradefloor`
    must not be breakable by a third party's packaging at all.
    """
    assert "finrobot" not in sys.modules, (
        "importing tradefloor.integrations.finrobot pulled in FinRobot. The "
        "import belongs inside FinRobotAdapter._finrobot, so that replay and "
        "`import tradefloor` never need it.")


def test_tradefloor_does_not_import_the_integrations_subpackage():
    """`import tradefloor` must not reach an adapter, so that a broken or
    absent third-party dependency cannot break the library."""
    source = (REPO / "python" / "tradefloor" / "__init__.py").read_text(
        encoding="utf-8")
    assert "integrations" not in source, (
        "tradefloor/__init__.py references the integrations subpackage. "
        "Adapters are reached by naming them, never by importing the "
        "package.")


def test_live_mode_without_finrobot_names_the_extra():
    if importlib.util.find_spec("finrobot") is not None:
        pytest.skip("finrobot is installed, so the refusal cannot be reached")
    agent = fr.FinRobotAdapter(mode="live",
                               llm_config={"config_list": [{"model": "x"}]})
    with pytest.raises(ImportError) as excinfo:
        agent._finrobot()
    message = str(excinfo.value)
    assert "tradefloor[finrobot]" in message
    assert "3.11" in message, "the message should name the Python overlap"


def test_replay_mode_refuses_without_a_transcript():
    with pytest.raises(tf.ValidationError, match="transcript"):
        fr.FinRobotAdapter(mode="replay")


def test_live_mode_refuses_without_an_llm_config():
    with pytest.raises(tf.ValidationError, match="llm_config"):
        fr.FinRobotAdapter(mode="live")


def test_an_unknown_mode_is_refused():
    with pytest.raises(tf.ValidationError, match="replay"):
        fr.FinRobotAdapter(mode="dry-run")


# -- the observation mapping ------------------------------------------------


#: The contract, written down. A new key in the payload is a new thing the
#: agent can see, which is a decision about the experiment and not a detail,
#: so it should require editing this list.
PAYLOAD_KEYS = {"step", "day", "steps_per_day", "macro", "assets", "portfolio"}
ASSET_KEYS = {"symbol", "price", "return_1d", "return_5d", "volatility",
              "best_bid", "best_ask", "avg_daily_volume", "max_order_shares",
              "position", "fundamentals"}


def test_the_payload_carries_exactly_the_allowlisted_keys():
    world, agent = one_observation(days=2)
    payload = fr.observe(_observation(world), history=agent.history)
    assert set(payload) == PAYLOAD_KEYS
    assert set(payload["assets"][0]) == ASSET_KEYS
    assert set(payload["macro"]) == set(fr.OBSERVABLE_MACRO)
    assert set(payload["portfolio"]) == {"cash", "net_worth", "gross_exposure"}


def test_the_macro_allowlist_is_the_librarys_own():
    """Not a copy of it. Two lists that agree today are two lists that can
    disagree after one edit, and the one that would silently widen is this."""
    from tradefloor.counterfactual import MACRO_FIELDS
    assert fr.OBSERVABLE_MACRO is MACRO_FIELDS
    assert "qe_pe_boost" not in fr.OBSERVABLE_MACRO, (
        "qe_pe_boost is a model coefficient, not a published macro number")


def test_returns_are_absent_rather_than_zero_before_they_are_observable():
    world, agent = one_observation(days=1)
    payload = fr.observe(_observation(world), history=agent.history[:1])
    asset = payload["assets"][0]
    assert asset["return_1d"] is None and asset["volatility"] is None
    assert "not available" in fr.render(payload)


def test_the_rendered_block_carries_the_market_and_the_book():
    world, agent = one_observation(days=3)
    payload = fr.observe(_observation(world), history=agent.history,
                         fundamentals={"TECH_A": {"revenue_growth": 0.30}})
    text = fr.render(payload, objective="Do the thing.")
    for expected in ("SIMULATED MARKET", "Macro", "federal_funds_rate",
                     "TECH_A", "DEFENSIVE_A", "Portfolio", "Positions:",
                     "revenue_growth", "Objective", "Do the thing."):
        assert expected in text, expected
    assert "max order this step" in text, (
        "the agent has to be told the execution constraint it is sized "
        "against, or its decisions are measuring a limit it cannot see")


def test_fundamentals_are_supplied_not_discovered():
    """The adapter must not go looking for company facts on the engine."""
    world, agent = one_observation(days=2)
    payload = fr.observe(_observation(world), history=agent.history)
    assert all(a["fundamentals"] == {} for a in payload["assets"])


# -- the ground-truth boundary ----------------------------------------------

#: Everything on the engine that a trader in this market could not know.
#: `column` is on the list because its fields include `mispricing_s` and
#: `garch_variance`; `truth` and `attribution` are the answer key outright.
SEALED = ("fair_value", "attribution", "truth", "session_mispricing_s",
          "column", "state_snapshot", "macro_table", "bars", "order_log",
          "model_params", "model")


class Sealed:
    """An engine that raises if the simulator's own knowledge is touched.

    Stronger than scanning the rendered text for a leaked number, and kept
    alongside that scan rather than instead of it. This one fails on the
    ACCESS, so a future edit that reads `engine.attribution` and then rounds
    it, scales it or uses it to pick a word fails here too.
    """

    def __init__(self, engine):
        object.__setattr__(self, "_engine", engine)

    def __getattr__(self, name):
        if name in SEALED:
            raise AssertionError(
                f"the FinRobot adapter read engine.{name}, which is "
                "simulator ground truth. FinRobot must see only what a "
                "trader in this market could observe.")
        return getattr(object.__getattribute__(self, "_engine"), name)


def _observation(world: World, engine=None):
    """An Observation over a world's current state, as `World.run` builds
    one. Rebuilt here rather than captured so a test can substitute the
    engine."""
    import struct

    from tradefloor.harness import Observation
    prices = list(struct.unpack("<%dd" % len(world.engine.tickers),
                                world.engine.prices()))
    return Observation(world.step, world.day, world.engine.tickers, prices,
                       world.portfolio, engine or world.engine,
                       [i.avg_volume for i in world.universe],
                       world.steps_per_day)


def test_the_mapping_never_touches_simulator_ground_truth():
    world, agent = one_observation(days=3)
    obs = _observation(world, engine=Sealed(world.engine))
    payload = fr.observe(obs, history=agent.history)
    fr.render(payload)              # raises through Sealed if it reached one
    assert payload["assets"], "the sealed run produced nothing to check"


def test_no_hidden_value_appears_in_the_text_finrobot_receives():
    """The complementary check: not whether the adapter ASKED, but whether
    the answer is in the block anyway, by some route nobody predicted."""
    world, agent = one_observation(days=4)
    payload = fr.observe(_observation(world), history=agent.history)
    text = fr.render(payload)

    hidden = []
    for instrument in world.universe:
        value = tf.fair_value(
            eps=instrument.eps, sector=instrument.sector,
            revenue_growth=instrument.revenue_growth,
            book_value_per_share=instrument.book_value_per_share,
            federal_funds_rate=0.04, corporate_bond_yield=0.055).fair_value
        hidden.append(value)
    for factor in ("momentum", "reversion", "company_news"):
        import struct
        blob = world.engine.attribution(factor)
        hidden += list(struct.unpack("<%dd" % (len(blob) // 8), blob))

    leaked = [v for v in hidden
              if v and abs(v) > 1e-9 and f"{v:.4f}" in text]
    assert not leaked, (
        f"values only the simulator knows appear in the FinRobot input: "
        f"{leaked}")


def test_the_published_state_carries_no_credential():
    agent = fr.FinRobotAdapter(
        mode="live",
        llm_config={"config_list": [{"model": "m", "api_key": "sk-secret"}]})
    published = json.dumps(agent.state())
    assert "sk-secret" not in published and "api_key" not in published, (
        "state() is printed by the fork agreement and written into artifacts")


# -- validating what the model returns --------------------------------------


def test_a_buy_is_accepted():
    decision = fr.parse(answer(act("TECH_A", "BUY", 100)))
    assert decision.actions == [fr.Action("TECH_A", "BUY", 100.0)]
    assert decision.actions[0].signed() == 100.0
    assert decision.rationale == "because"


def test_a_sell_is_accepted_and_carries_a_positive_quantity():
    decision = fr.parse(answer(act("TECH_A", "SELL", 20)))
    assert decision.actions[0].signed() == -20.0


def test_a_hold_is_accepted_and_produces_no_order():
    world, agent = one_observation(days=2)
    decision = fr.parse(answer(act("TECH_A", "HOLD")))
    orders, notes = fr.orders_from(decision, _observation(world))
    assert orders == {} and notes == []


def test_an_empty_action_list_means_change_nothing():
    decision = fr.parse(answer())
    assert decision.actions == []


def test_lowercase_sides_are_accepted():
    """Case is formatting, not meaning, and rejecting it would score a
    correct decision as a failure."""
    assert fr.parse(answer(act("TECH_A", "buy", 5))).actions[0].side == "BUY"


def test_a_fenced_answer_is_accepted():
    """Models add code fences and closing sentences whatever the mandate
    says, and this integration measures portfolio decisions rather than
    instruction compliance."""
    text = ('Here is my decision:\n```json\n'
            '{"actions": [{"symbol": "TECH_A", "side": "SELL", '
            '"quantity": 30}], "rationale": "rates"}\n```\nLet me know.')
    assert fr.parse(text).actions[0].signed() == -30.0


@pytest.mark.parametrize("text,match", [
    ("", "empty"),
    ("   ", "empty"),
    ("no json here at all", "no JSON object"),
    ('{"actions": [], ', "no JSON object"),
    ('{"actions": [1,]}', "does not parse"),
    ('[1, 2, 3]', "got a list"),
    ('{"actions": "TECH_A"}', "must be a list"),
    ('{"actions": [5]}', "not an object"),
    ('{"actions": [{"side": "BUY", "quantity": 1}]}', "no usable 'symbol'"),
    ('{"actions": [{"symbol": "TECH_A", "side": "SHORT", "quantity": 1}]}',
     "not one of"),
    ('{"actions": [{"symbol": "TECH_A", "side": "BUY", "quantity": -5}]}',
     "negative"),
    ('{"actions": [{"symbol": "TECH_A", "side": "BUY", "quantity": "lots"}]}',
     "not a number"),
    ('{"actions": [{"symbol": "TECH_A", "side": "HOLD", "quantity": 10}]}',
     "HOLD"),
    ('{"actions": [{"symbol": "A", "side": "BUY", "quantity": 1}, '
     '{"symbol": "A", "side": "SELL", "quantity": 1}]}', "more than once"),
    ('{"actions": [], "rationale": 5}', "must be a string"),
])
def test_invalid_output_is_refused_readably(text, match):
    with pytest.raises(fr.DecisionError, match=match):
        fr.parse(text)


def test_a_non_finite_quantity_is_refused():
    with pytest.raises(fr.DecisionError, match="non-finite"):
        fr.parse('{"actions": [{"symbol": "A", "side": "BUY", '
                 '"quantity": Infinity}]}')


def test_a_decision_error_is_a_validation_error():
    """So a caller already catching the library's refusals catches this."""
    assert issubclass(fr.DecisionError, tf.ValidationError)


def test_an_unlisted_symbol_is_refused_rather_than_skipped():
    world, _ = one_observation(days=1)
    decision = fr.parse(answer(act("NOT_LISTED", "BUY", 10)))
    with pytest.raises(fr.DecisionError, match="not listed"):
        fr.orders_from(decision, _observation(world))


def test_an_oversized_order_is_clipped_and_the_clip_is_recorded():
    world, _ = one_observation(days=1)
    obs = _observation(world)
    cap = fr.MAX_PARTICIPATION * obs.avg_volume("TECH_A")
    decision = fr.parse(answer(act("TECH_A", "BUY", cap * 10)))
    orders, notes = fr.orders_from(decision, obs)
    assert orders["TECH_A"] == pytest.approx(cap)
    assert len(notes) == 1 and "clipped" in notes[0]


def test_sub_share_dust_produces_no_order():
    world, _ = one_observation(days=1)
    decision = fr.parse(answer(act("TECH_A", "BUY", 0.4)))
    orders, _notes = fr.orders_from(decision, _observation(world))
    assert orders == {}


# -- a validated decision reaches the market --------------------------------


def test_a_validated_decision_is_executed_by_tradefloor():
    agent = Scripted(answer(act("TECH_A", "BUY", 2_000)), every=6)
    world = World(seed=7, universe=universe(), agent=agent, cash=1_000_000.0,
                  pins={"federal_funds_rate": 0.04})
    world.run(days=2)

    assert world.portfolio.positions["TECH_A"].quantity > 0
    filled = [f for row in world.trace for f in row["fills"]]
    assert filled, "the decision never reached the book"
    assert world.portfolio.cash < 1_000_000.0
    assert not world.rejected, world.rejected


def test_the_agent_is_asked_on_the_cadence_and_not_every_step():
    agent = Scripted(answer(), every=6)
    world = World(seed=7, universe=universe(), agent=agent, cash=1_000_000.0)
    world.run(days=3)
    assert world.step == 18
    assert len(agent.record) == 3, [e["step"] for e in agent.record]
    assert [e["step"] for e in agent.record] == [0, 6, 12]


def test_the_decision_hook_publishes_actions_and_rationale():
    agent = Scripted(answer(act("TECH_A", "HOLD"), rationale="waiting"))
    world = World(seed=7, universe=universe(), agent=agent, cash=1_000_000.0)
    world.run(days=1)
    published = agent.decision()
    assert published["rationale"] == "waiting"
    assert published["actions"][0]["side"] == "HOLD"
    assert "prompt" not in published and "response" not in published, (
        "compare() finds the first differing decision by comparing these "
        "dicts, so anything in here that varies for another reason reports "
        "a divergence that did not happen")


# -- fork and intervention --------------------------------------------------


def test_both_arms_start_identical_with_a_finrobot_agent():
    """The claim the experiment rests on, with this adapter in the loop.

    `tests/test_counterfactual.py` already proves `agree` for the market and
    the portfolio. What is new here is the AGENT half of it: an adapter whose
    `state()` under-reported would let the arms differ while the agreement
    passed.
    """
    agent = Scripted(answer(act("TECH_A", "BUY", 5_000)))
    world = World(seed=7, universe=universe(), agent=agent, cash=1_000_000.0,
                  pins={"federal_funds_rate": 0.04,
                        "corporate_bond_yield": 0.055})
    world.run(days=4)
    control, shock = world.fork("control", "shock")
    agreement = agree(control, shock)
    assert agreement.identical, agreement.differences
    assert control.agent.state() == shock.agent.state()


def test_a_forked_adapter_is_independent():
    agent = Scripted(answer(act("TECH_A", "BUY", 5_000)))
    world = World(seed=7, universe=universe(), agent=agent, cash=1_000_000.0)
    world.run(days=2)
    control, shock = world.fork("control", "shock")

    assert control.agent is not shock.agent
    assert control.agent.history is not shock.agent.history
    control.agent.history.append([0.0, 0.0])
    assert len(control.agent.history) != len(shock.agent.history)


def test_a_fork_keeps_the_subclass():
    """`fork` builds `type(self)`, not the base class. The version that did
    not completed the run and compared two agents that were not the one under
    test."""
    agent = Scripted(answer())
    world = World(seed=7, universe=universe(), agent=agent, cash=1_000_000.0)
    world.run(days=1)
    control, _shock = world.fork("control", "shock")
    assert isinstance(control.agent, Scripted)


def test_the_intervention_reaches_one_arm_only():
    agent = Scripted(answer(act("TECH_A", "BUY", 5_000)))
    world = World(seed=7, universe=universe(), agent=agent, cash=1_000_000.0,
                  pins={"federal_funds_rate": 0.04,
                        "corporate_bond_yield": 0.055})
    world.run(days=3)
    control, shock = world.fork("control", "+200bps")
    shock.intervene(federal_funds_rate=0.06, corporate_bond_yield=0.075)
    control.run(days=2)
    shock.run(days=2)

    assert control.engine.macro_state.federal_funds_rate == pytest.approx(0.04)
    assert shock.engine.macro_state.federal_funds_rate == pytest.approx(0.06)
    assert not control.interventions
    assert len(shock.interventions) == 1


def test_a_rate_reading_agent_diverges_at_the_first_decision_after_the_fork():
    """The behavioural-divergence detection, with an agent whose response to
    the rate is known, so the expected step is known too."""

    def script(obs):
        rate = obs.engine.macro_state.federal_funds_rate
        if rate > 0.05:
            return answer(act("TECH_A", "SELL", 3_000), rationale="tighter")
        return answer(act("TECH_A", "BUY", 3_000), rationale="steady")

    agent = Scripted(script)
    world = World(seed=7, universe=universe(), agent=agent, cash=1_000_000.0,
                  pins={"federal_funds_rate": 0.04,
                        "corporate_bond_yield": 0.055})
    world.run(days=3)
    fork_step = world.step
    control, shock = world.fork("control", "+200bps")
    agreement = agree(control, shock)
    shock.intervene(federal_funds_rate=0.06, corporate_bond_yield=0.075)
    control.run(days=3)
    shock.run(days=3)

    report = compare(control, shock, agreement=agreement)
    assert report.divergence.intervention_step == fork_step
    assert report.divergence.decision == fork_step, (
        "the agent reads the rate, so its first post-fork decision is the "
        "first one that should differ")
    assert report.divergence.orders == fork_step
    assert report.control["label"] == "control"
    assert report.treatment["turnover"] > 0


def test_an_agent_that_ignores_the_rate_never_diverges_in_its_decisions():
    """The negative case, which is a finding rather than a gap: a comparison
    has to be able to report that the intervention changed the market and not
    the agent."""
    agent = Scripted(answer(act("TECH_A", "BUY", 2_000)))
    world = World(seed=7, universe=universe(), agent=agent, cash=1_000_000.0,
                  pins={"federal_funds_rate": 0.04,
                        "corporate_bond_yield": 0.055})
    world.run(days=3)
    control, shock = world.fork("control", "+200bps")
    shock.intervene(federal_funds_rate=0.06, corporate_bond_yield=0.075)
    control.run(days=2)
    shock.run(days=2)

    report = compare(control, shock)
    assert report.divergence.decision is None
    assert report.divergence.prices is not None, (
        "the intervention still moved the market")


# -- recording and replay ---------------------------------------------------


def test_a_transcript_round_trips():
    transcript = fr.Transcript(meta={"model": "m"})
    transcript.record({"digest": "abc", "response": "hello", "step": 0})
    again = fr.Transcript.from_json(transcript.to_json())
    assert again.meta == {"model": "m"}
    assert again.response_for("abc") == "hello"
    assert again.response_for("missing") is None


def test_the_replay_key_is_the_input():
    """Two different observations must not collide, and the same one must."""
    assert fr.digest("a") == fr.digest("a")
    assert fr.digest("a") != fr.digest("a ")


def test_replay_reproduces_a_recorded_decision_without_a_network():
    world, agent = one_observation(days=2)
    obs = _observation(world)
    # `act` appends the prices it is shown before it renders, so the prompt a
    # replay looks up is built from the history INCLUDING this step.
    history = agent.history + [list(obs.prices)]
    prompt = fr.render(fr.observe(obs, history=history))

    transcript = fr.Transcript()
    transcript.record({"digest": fr.digest(prompt), "step": obs.step,
                       "response": answer(act("TECH_A", "BUY", 10))})
    replayer = fr.FinRobotAdapter(mode="replay", transcript=transcript,
                                  every=1)
    replayer.history = [list(row) for row in agent.history]
    orders = replayer.act(obs)
    assert orders == {"TECH_A": 10.0}


def test_a_missing_recording_raises_and_says_which_step():
    world, agent = one_observation(days=2)
    obs = _observation(world)
    replayer = fr.FinRobotAdapter(mode="replay", transcript=fr.Transcript(),
                                  every=1)
    with pytest.raises(fr.DecisionError) as excinfo:
        replayer.act(obs)
    message = str(excinfo.value)
    assert f"step {obs.step}" in message
    assert "--live --record" in message, (
        "the message has to say how to fix it, because the usual cause is "
        "an intentional change to the observation mapping")


# -- the recorded run --------------------------------------------------------


needs_fixture = pytest.mark.skipif(
    not FIXTURE.exists(),
    reason="no recorded FinRobot run at tests/fixtures/finrobot/")


@needs_fixture
def test_the_fixture_records_what_a_replay_cannot_reconstruct():
    transcript = fr.Transcript.load(FIXTURE)
    assert len(transcript) > 0
    for field in ("framework", "framework_version", "provider", "model",
                  "mandate_version", "entry_point"):
        assert transcript.meta.get(field), f"meta is missing {field}"
    assert transcript.meta["framework"] == "FinRobot"


@needs_fixture
def test_the_fixture_carries_no_credential_or_provider_metadata():
    text = FIXTURE.read_text(encoding="utf-8")
    # Prefixes and header names, not bare fragments. `sk-` on its own matched
    # "risk-adjusted" in the mandate, which is the failure mode of a secret
    # scanner nobody trusts after the first false positive.
    for secret in ("sk-ant-", "sk-proj-", "api_key", "api-key", "Bearer ",
                   "Authorization", "anthropic-version", "request_id"):
        assert secret not in text, f"the fixture contains {secret!r}"

    entries = json.loads(text)["entries"]
    allowed = {"arm", "step", "day", "digest", "prompt", "response"}
    for entry in entries:
        extra = set(entry) - allowed
        assert not extra, f"unexpected fields in a recorded entry: {extra}"


@needs_fixture
def test_the_recorded_responses_are_a_real_models_and_still_validate():
    """Not hand-authored. The recorded text is what the model said, fences
    and all, so parsing it is a real test of the parser rather than of a
    fixture written to suit it."""
    entries = json.loads(FIXTURE.read_text(encoding="utf-8"))["entries"]
    decisions = [fr.parse(e["response"]) for e in entries]
    assert decisions, "the fixture is empty"
    sides = {a.side for d in decisions for a in d.actions}
    assert sides, "no recorded decision named an action"
    assert sides <= set(fr.SIDES)


@needs_fixture
def test_the_recorded_run_replays_end_to_end(tmp_path):
    """The whole experiment, from the shipped fixture, with no key.

    This is the test the integration exists to pass: shared history, a
    checkpoint, a fork proved identical, one intervention, both arms
    continued, and a comparison that finds where they came apart.
    """
    example = _load_example()
    result = example.main(live=False, out=tmp_path / "artifacts",
                          fixture=FIXTURE)

    divergence = result["divergence"]
    fork_step = example.WARMUP_DAYS * example.STEPS_PER_DAY
    assert divergence["intervention_step"] == fork_step
    assert divergence["macro"] == fork_step
    assert divergence["decision"] is not None, (
        "the agent saw a different policy rate and decided identically, "
        "which would be a finding, but not one this recording shows")
    assert divergence["decision"] >= fork_step, (
        "a decision cannot diverge before the intervention that caused it")
    assert result["control"]["label"] == "control"
    assert result["treatment"]["label"] == "+200bps"
    assert result["provenance"]["framework"] == "FinRobot"
    assert result["provenance"]["mode"] == "replay"


def _load_example():
    spec = importlib.util.spec_from_file_location("finrobot_rate_shock",
                                                  EXAMPLE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
