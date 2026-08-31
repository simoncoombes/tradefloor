"""The FinRobot integration, without FinRobot.

CI does not call an LLM. A live decision needs an API key, costs money and
answers differently every time, so a suite depending on one would be neither
reproducible nor free. What gets checked here is everything the integration is
responsible for: the observation mapping, the ground-truth boundary, the
validation of generated output, the execution of a validated decision, the
fork, and the replay of a genuine recorded run.

The recorded run carries the weight. `tests/fixtures/finrobot/` holds real
interactions from a real FinRobot agent, and
`test_the_recorded_run_replays_end_to_end` re-executes the whole experiment
against them. That beats a mock. Nobody wrote those responses to make the
parser happy; they are what the model said, code fences and trailing
sentences included, both of which the mandate asked it to leave out.

Nothing in this file imports `finrobot`, and one of the tests asserts the
adapter does not either until it is asked to go live.
"""

from __future__ import annotations

import copy
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
EXAMPLE = REPO / "examples" / "integrations" / "finrobot" / "rate_shock.py"

#: A two-name market, where the example uses four. The example runs the
#: experiment; these are unit tests, and a smaller roster keeps an assertion
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

    It replaces `_ask`, the ONE method that reaches outside the process.
    Everything below it runs unmodified: the observation mapping, the render,
    the parse, the validation, the execution, the fork. A double that
    reimplemented any of those would be testing itself.

    ``script`` is keyword-defaulted because `FinRobotAdapter.fork` rebuilds the
    agent as ``type(self)(**keyword_args)``, so a subclass with a required
    positional cannot be forked. That constraint is why the base class is
    keyword-only throughout.
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


class Recorded(fr.FinRobotAdapter):
    """A live-mode adapter whose answer is supplied at `_live`, not `_ask`.

    `Scripted` replaces `_ask`, and `_ask` is the method that decides what a
    live run records and what a replay reads back. A recording made through
    `Scripted` would therefore be the test's own recording, and replaying it
    would prove that the test agrees with itself.

    This one replaces `_live`, the single call that reaches the network, and
    leaves the real `_ask` to record. What a round trip through it exercises
    is the shipped recording path.
    """

    def __init__(self, script=None, **kwargs):
        kwargs.setdefault("mode", "live")
        kwargs.setdefault("llm_config", {"config_list": [{"model": "none"}]})
        super().__init__(**kwargs)
        self.script = script

    def _live(self, prompt):
        return self.script(prompt) if callable(self.script) else self.script


def one_observation(agent=None, *, days: int = 1) -> tuple[World, object]:
    """Run a world far enough to have an observation with some history."""
    agent = agent or Scripted(answer())
    world = World(seed=7, universe=universe(), agent=agent, cash=1_000_000.0,
                  pins={"federal_funds_rate": 0.04,
                        "corporate_bond_yield": 0.055})
    world.run(days=days)
    return world, agent


# -- the public surface -----------------------------------------------------

#: Every name this module publishes, and the type each one is. The integration
#: shipped in 0.6.0, so each of these is something a reader may already have
#: imported, and this set is the promise made to them. A refactor that renames
#: one, drops one, or turns a constant into a function has to fail here rather
#: than in somebody's script.
#:
#: The type matters beside the name. `MAX_PARTICIPATION` becoming a callable
#: would keep the name importable and break every caller multiplying by it,
#: and a name-only check would pass.
PUBLIC_SURFACE = {
    "AGENT_CONFIG": dict,
    "HISTORY_STEPS": int,
    "MANDATE": str,
    "MANDATE_VERSION": str,
    "MAX_PARTICIPATION": float,
    "OBSERVABLE_MACRO": tuple,
    "SIDES": tuple,
    "ENTRY_POINT": str,
    "FRAMEWORK_URL": str,
    "DECISION_FIELDS": tuple,
    "ACTION_FIELDS": tuple,
    "DecisionError": type,
    "Action": type,
    "Decision": type,
    "Transcript": type,
    "FinRobotAdapter": type,
    # Re-exported from the shared layer on purpose, not import artifacts.
    # `act` raises FrameworkError and `_finrobot` raises
    # MissingDependencyError, so a caller handling either should be able to
    # name it from the module that raised it.
    "FrameworkError": type,
    "IntegrationError": type,
    "MissingDependencyError": type,
}

#: The module-level functions, which are the integration's usable half: an
#: observation in, a validated share delta out. The shipped example drives
#: the adapter, but anybody evaluating their own agent reaches these directly.
PUBLIC_FUNCTIONS = ("observe", "render", "parse", "orders_from", "digest")

#: Attributes and methods each published class keeps. A class can gain
#: members without breaking anybody; losing or renaming one is what this
#: catches. Every class here but the adapter declares `__slots__`, so its
#: attributes are descriptors on the class and are visible without building
#: one.
PUBLIC_MEMBERS = {
    "Action": ("symbol", "side", "quantity", "as_dict", "signed"),
    "Decision": ("actions", "rationale", "as_dict"),
    "Transcript": ("meta", "entries", "record", "response_for", "as_dict",
                   "to_json", "from_json", "load", "save"),
    "FinRobotAdapter": ("act", "decision", "state", "fork", "provenance"),
}

#: What a constructed adapter carries. Read off an instance rather than the
#: class because `FinRobotAdapter` has no `__slots__` and these are set in
#: `__init__`. The example's `rate_shock.py` reads `agent.every`,
#: `agent.max_participation`, `agent.mode`, `agent.transcript`, `agent.arm`
#: and `agent.record` to build its provenance block and its per-arm artifact,
#: so all six are API whether or not they look like it.
PUBLIC_ATTRIBUTES = ("mode", "transcript", "recorder", "llm_config",
                     "fundamentals", "objective", "mandate", "agent_config",
                     "every", "max_participation", "arm", "history", "record",
                     "info")


def test_the_documented_import_path_still_works():
    """The line the example, the notebook and the README all print.

    Everything else in this file reaches the module as `from
    tradefloor.integrations import finrobot as fr`, which is not the form
    anybody outside this repository writes. Both have to keep working, and
    without this only one of them is ever exercised.
    """
    from tradefloor.integrations.finrobot import (  # noqa: F401
        AGENT_CONFIG, HISTORY_STEPS, MANDATE, MANDATE_VERSION,
        MAX_PARTICIPATION, OBSERVABLE_MACRO, SIDES, Action, Decision,
        DecisionError, FinRobotAdapter, Transcript, digest, observe,
        orders_from, parse, render)


def test_every_published_name_is_still_there_and_still_that_kind_of_thing():
    """The surface, pinned by name.

    New names may be added; that is additive and breaks nobody, so this
    asserts that none has GONE rather than that none has arrived. An exact
    comparison against `dir()` would fail on `import json` instead, which says
    nothing about what anybody imported.
    """
    missing = [name for name in PUBLIC_SURFACE if not hasattr(fr, name)]
    assert not missing, (
        f"names dropped from the published surface: {missing}. The "
        "integration shipped in 0.6.0; renaming one of these breaks a reader "
        "who already imported it.")
    for name, kind in sorted(PUBLIC_SURFACE.items()):
        value = getattr(fr, name)
        assert isinstance(value, kind), (
            f"{name} is a {type(value).__name__}, and was published as a "
            f"{kind.__name__}")

    for name in PUBLIC_FUNCTIONS:
        assert callable(getattr(fr, name, None)), f"{name} is not callable"

    for name, members in sorted(PUBLIC_MEMBERS.items()):
        published = getattr(fr, name)
        gone = [m for m in members if not hasattr(published, m)]
        assert not gone, f"{name} lost {gone}"

    agent = fr.FinRobotAdapter(mode="replay", transcript=fr.Transcript())
    gone = [a for a in PUBLIC_ATTRIBUTES if not hasattr(agent, a)]
    assert not gone, f"a constructed FinRobotAdapter lost {gone}"


def test_the_adapters_constructor_keywords_are_the_ones_callers_wrote():
    """Every argument is keyword-only, so every name is part of the API.

    A positional parameter can be renamed without breaking a caller. None of
    these can: `FinRobotAdapter(mode=..., transcript=...)` is how the example,
    the notebook and every reader builds one, and `fork` rebuilds the agent by
    passing all of them back by name.
    """
    import inspect
    parameters = inspect.signature(fr.FinRobotAdapter).parameters
    assert set(parameters) == {
        "mode", "transcript", "recorder", "llm_config", "fundamentals",
        "objective", "mandate", "agent_config", "every", "max_participation",
        "arm", "info"}
    assert all(p.kind is inspect.Parameter.KEYWORD_ONLY
               for p in parameters.values()), (
        "a positional argument here breaks FinRobotAdapter.fork, which "
        "rebuilds the agent as type(self)(**keywords)")


def test_the_supported_sides_are_the_three_the_mandate_names():
    """`SIDES`, `parse` and the mandate text have to agree. They are three
    statements of one contract, and a fourth side added to the tuple without
    the mandate asking for it would be a side no model ever returns."""
    assert fr.SIDES == ("BUY", "SELL", "HOLD")
    for side in fr.SIDES:
        assert f'"{side}"' in fr.MANDATE, (
            f"{side} is accepted but the mandate never offers it")


# -- the shared adapter contract --------------------------------------------


def _contract_agent(respond):
    """A FinRobotAdapter whose framework call is `respond(payload)`.

    The seam the shared checks need. FinRobot's own seam is `_ask`, which
    sits BELOW the render and the digest -- that is what makes replay keyed
    on the rendered prompt possible -- so the payload is rebuilt here exactly
    as `act` built it. Everything below `_ask` runs unmodified.
    """
    class Scripted(fr.FinRobotAdapter):
        def __init__(self, **kwargs):
            kwargs.setdefault("mode", "live")
            kwargs.setdefault("llm_config",
                              {"config_list": [{"model": "none"}]})
            super().__init__(**kwargs)

        def _ask(self, prompt, key, obs):
            payload = fr.observe(obs, history=self.history,
                                 fundamentals=self.fundamentals,
                                 max_participation=self.max_participation)
            out = respond(payload)
            return out if isinstance(out, str) else json.dumps(out)

    return Scripted()


try:
    import test_integrations as _contract
    _CHECKS = list(_contract.CONTRACT_CHECKS)
except ImportError:                                  # pragma: no cover
    _CHECKS = []

#: Checks FinRobot does not meet yet, and why. Empty, and it should stay
#: that way: an entry here means the integration and the shared layer answer
#: the same question differently, which is what makes a cross-adapter study
#: uncontrolled. Anything added needs a reason and a plan to remove it.
_NOT_YET: dict[str, str] = {}


@pytest.mark.skipif(not _CHECKS, reason="tests/test_integrations.py absent")
@pytest.mark.parametrize("check", _CHECKS, ids=lambda f: f.__name__)
def test_the_adapter_meets_the_shared_contract(check, request):
    """FinRobot against the checks every adapter must pass.

    This is what stops the integration and the shared layer answering the
    same question differently. Two adapters that disagree about whether a
    given model output was a decision make a cross-adapter study
    uncontrolled, and no scorecard says so -- measured, not argued: on one
    market and one seed, a response with no 'actions' key scored trades=0
    with an empty errors list here and three refusals through the shared
    validator.
    """
    reason = _NOT_YET.get(check.__name__)
    if reason:
        request.node.add_marker(pytest.mark.xfail(strict=True, reason=reason))
    check(_contract_agent)


# -- agreement with the shared layer ----------------------------------------
#
# `common.py` was derived FROM this module, and the two implementations are
# currently identical where they overlap. Nothing rebases one onto the other:
# the names here are released API and the duplication buys backward
# compatibility. What it costs is drift, and drift here is not a tidiness
# problem. If the shared serializer gains a field and this one does not, a
# FinRobot arm and a LangGraph arm of the same study are shown different
# markets, and a comparison across adapters silently stops being controlled.
#
# So the equivalence is asserted rather than assumed. These fail the moment
# somebody edits one side, which is the point at which somebody has to decide
# whether the other should follow.


#: Payload keys the two serializers deliberately differ on, and why. Empty,
#: and every entry needs a reason. Adding one is the decision this test
#: exists to force -- the alternative, which is what happens without it, is
#: that somebody deletes the test instead.
DELIBERATE_DIFFERENCES: dict[str, str] = {}


def test_the_serializer_agrees_with_the_shared_one():
    """`finrobot.observe` and `common.serialize_observation` must produce the
    same payload for the same observation.

    Not the same source -- the same OUTPUT, which is what an experiment
    depends on. A field added to one and not the other is a difference in
    what two frameworks are shown, and no test of either alone can see it.

    ## Why this is a test and not an alias

    The obvious fix is to make `observe` a thin wrapper over the shared
    serializer, and that would be wrong here. This module's replay key is the
    digest of the RENDERED prompt, and the shipped fixture's sixty responses
    are filed under those digests. Aliasing would couple the fixture's
    validity to every future edit of a serializer that is free to evolve, and
    the failure would surface at run time, in somebody else's example run, as
    "no recorded FinRobot response for step N" -- a message about a changed
    observation mapping shown to a person who changed no such thing.

    Measured, the exposure is narrower than it looks, and worth knowing
    precisely. `render` names its fields one at a time, so a key ADDED to the
    payload that render does not name -- per-asset, top-level, even a new
    macro field -- never reaches the text and the digest does not move. What
    moves it is a changed DERIVATION of a value render already prints
    (`_window_return`'s window, `_volatility`'s formula) or a change to the
    fundamentals pass-through, which render iterates whole. A renamed key
    raises KeyError, which is the loud case and needs no guard.

    So the maths is the exposure, not the field list -- and comparing values
    rather than keys is what catches it.
    """
    from tradefloor.integrations import common

    world, agent = one_observation(days=3)
    obs = _observation(world)
    facts = {"TECH_A": {"eps": 3.0, "sector": "technology"}}
    for history in ([], agent.history[:1], agent.history):
        mine = fr.observe(obs, history=history, fundamentals=facts)
        shared = common.serialize_observation(obs, history=history,
                                              fundamentals=facts)
        differing = sorted(k for k in set(mine) | set(shared)
                           if mine.get(k) != shared.get(k))
        unexplained = [k for k in differing if k not in DELIBERATE_DIFFERENCES]
        assert not unexplained, (
            f"the FinRobot serializer and the shared one disagree on "
            f"{unexplained}. One of them has been edited. Decide whether both "
            "should change -- and if they should NOT, add the key to "
            "DELIBERATE_DIFFERENCES with the reason, rather than deleting "
            "this test. A changed derivation here also moves the replay key, "
            "so the fixture may need re-recording.")

    # And the participation argument is honoured the same way by both.
    assert (fr.observe(obs, history=agent.history, max_participation=0.01)
            == common.serialize_observation(obs, history=agent.history,
                                            max_participation=0.01))
    assert fr.OBSERVABLE_MACRO is common.OBSERVABLE_MACRO
    assert fr.SIDES == common.SIDES
    assert fr.HISTORY_STEPS == common.HISTORY_STEPS
    assert fr.MAX_PARTICIPATION == common.MAX_PARTICIPATION


def test_a_recorded_transcript_reads_the_same_through_either_class():
    """The compatibility question a user's saved file asks.

    `finrobot.Transcript` and `common.Transcript` write the same JSON and
    read each other's files. If that ever stopped being true, a recording
    made by the shipped example would fail to load under the shared class,
    and the fixture in this repository is the first thing that would break.
    """
    from tradefloor.integrations import common

    mine = fr.Transcript.load(FIXTURE) if FIXTURE.exists() else None
    if mine is None:
        pytest.skip("no recorded FinRobot run to cross-read")
    shared = common.Transcript.load(FIXTURE)
    assert len(shared) == len(mine)
    assert shared.meta == mine.meta
    assert shared.entries == mine.entries
    assert shared.to_json() == mine.to_json(), (
        "the two Transcript classes no longer serialise identically, so a "
        "file written by one would not round-trip through the other")

    # The replay key is the same function under both names, which is what
    # makes a transcript portable between them at all.
    for entry in mine.entries[:5]:
        assert fr.digest(entry["prompt"]) == common.digest(entry["prompt"]), (
            "the two digest functions disagree on a recorded prompt, so a "
            "transcript keyed by one cannot be replayed through the other")
        assert fr.digest(entry["prompt"]) == entry["digest"], (
            "a recorded entry's stored digest no longer matches the digest "
            "of its own prompt, so this fixture cannot replay")


def test_the_two_action_types_are_not_interchangeable():
    """A trap worth knowing about, pinned so nobody rediscovers it.

    `Action.__eq__` is an isinstance check, so a `finrobot.Action` and a
    `common.Action` holding identical fields compare UNEQUAL. Anybody mixing
    the two modules -- comparing a FinRobot decision against one from another
    adapter -- has to compare `as_dict()`, not the objects.

    This is the strongest argument for eventually collapsing the two, and
    until that happens it is the strongest argument for knowing they are
    separate.
    """
    from tradefloor.integrations import common

    mine = fr.Action("TECH_A", "BUY", 5.0)
    shared = common.Action("TECH_A", "BUY", 5.0)
    assert mine.as_dict() == shared.as_dict()
    assert mine != shared, (
        "the two Action types now compare equal, which means one of them "
        "has changed how equality works; check what else that affects")
    assert fr.Decision([mine]).as_dict() == common.Decision([shared]).as_dict()


# -- the optional dependency ------------------------------------------------


def test_the_adapter_imports_without_finrobot():
    """The whole reason the import is inside a method.

    Replaying a recorded run should never require a dependency tree whose
    current pins reach `torch`, and no third party's packaging should be able
    to break `import tradefloor`.
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
    assert isinstance(excinfo.value.__cause__, ImportError), (
        "the refusal is raised `from exc`, so the original import failure "
        "stays in the chain. Somebody whose finrobot install is broken rather "
        "than absent needs to see which module could not be found, and a "
        "message about the extra alone would send them to reinstall a package "
        "they already have.")

    from tradefloor.integrations import common

    # It is BOTH, and both halves earn their place. ImportError is what a
    # caller written before the shared layer catches, and what Python
    # convention says a missing module raises. IntegrationError is what stops
    # `act` wrapping this in a FrameworkError and burying the pip command.
    assert isinstance(excinfo.value, ImportError)
    assert isinstance(excinfo.value, common.MissingDependencyError)
    assert isinstance(excinfo.value, common.IntegrationError)
    assert "3.11 exactly" in message, (
        "`common.require` cannot carry this sentence, which is why the "
        "refusal is still raised by hand here. If require() gains a way to "
        "append a note, this can move onto it -- but not before.")


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


#: The contract, written down. A new key in the payload gives the agent
#: something new to see. That is a decision about the experiment, so it should
#: require editing this list.
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
    # `max_leverage` and `buying_power` state the constraint that actually
    # refuses a trade. The payload already named a participation cap worth
    # several times equity; without these an agent sizing to what it was told
    # scores zero fills and reads as a bad agent when it was misled.
    assert set(payload["portfolio"]) == {"cash", "net_worth", "gross_exposure",
                                         "max_leverage", "buying_power"}


def test_the_macro_allowlist_is_the_librarys_own():
    """The same object, not a copy. Two lists agreeing today can disagree
    after one edit, and a widening here would go unnoticed."""
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


def test_the_observation_arrives_at_finrobot_with_its_numbers_intact():
    """The mapping is a rename, not a transformation.

    Every number the agent is shown has to be the one the market published.
    A rounded price, a stale position or an average volume read off the wrong
    index would leave the ground-truth tests passing -- nothing forbidden
    would have leaked -- while the agent reasoned about a book it does not
    hold.
    """
    world, agent = one_observation(days=3)
    obs = _observation(world)
    payload = fr.observe(obs, history=agent.history)

    assert [a["symbol"] for a in payload["assets"]] == list(obs.tickers)
    for asset in payload["assets"]:
        ticker = asset["symbol"]
        book = obs.book(ticker)
        assert asset["price"] == obs.price(ticker)
        assert asset["position"] == obs.position(ticker)
        assert asset["avg_daily_volume"] == obs.avg_volume(ticker)
        assert asset["best_bid"] == book.best_bid
        assert asset["best_ask"] == book.best_ask
    assert payload["step"] == obs.step and payload["day"] == obs.day
    assert payload["steps_per_day"] == obs.steps_per_day
    assert payload["portfolio"]["cash"] == obs.portfolio.cash


def test_the_mapping_leaves_the_observation_and_the_history_alone():
    """`observe` reads. Anything it wrote would be a second agent.

    `World.run` hands one `Observation` to the agent and then executes
    against the same portfolio object, so a mapping that sorted the tickers
    in place, or normalised a price, would change what Tradefloor executes
    afterwards. The history is the adapter's own memory and is handed in by
    reference, so it gets the same guarantee.
    """
    world, agent = one_observation(days=3)
    obs = _observation(world)
    before = (list(obs.tickers), list(obs.prices), obs.step, obs.day,
              obs.portfolio.cash,
              {t: p.quantity for t, p in obs.portfolio.positions.items()})
    history = [list(row) for row in agent.history]
    kept = copy.deepcopy(history)
    facts = {"TECH_A": {"eps": 3.0}}

    payload = fr.observe(obs, history=history, fundamentals=facts)
    fr.render(payload)

    assert (list(obs.tickers), list(obs.prices), obs.step, obs.day,
            obs.portfolio.cash,
            {t: p.quantity for t, p in obs.portfolio.positions.items()}
            ) == before, "observe() changed the observation it was handed"
    assert history == kept, "observe() changed the price history it was handed"

    # And the payload holds copies, so an agent-side edit cannot reach back
    # into the caller's fundamentals table between decisions.
    payload["assets"][0]["fundamentals"]["eps"] = 999
    assert facts["TECH_A"]["eps"] == 3.0


def test_the_participation_cap_the_agent_is_shown_is_the_one_it_is_held_to():
    """One number, computed twice, in the mapping and in the execution.

    `observe` tells the agent its maximum order and `orders_from` enforces it.
    They take the cap by the same argument for that reason: an agent sized
    against a limit it was not held to would have its clipped orders read as
    decisions it made.
    """
    world, _ = one_observation(days=2)
    obs = _observation(world)
    payload = fr.observe(obs, history=[], max_participation=0.01)
    shown = payload["assets"][0]["max_order_shares"]
    assert shown == 0.01 * obs.avg_volume("TECH_A")

    decision = fr.parse(answer(act("TECH_A", "BUY", shown * 4)))
    orders, notes = fr.orders_from(decision, obs, max_participation=0.01)
    assert orders["TECH_A"] == pytest.approx(shown)
    assert len(notes) == 1 and "1.0%" in notes[0]


# -- the ground-truth boundary ----------------------------------------------

#: Everything on the engine that a trader in this market could not know.
#: `column` is on the list because its fields include `mispricing_s` and
#: `garch_variance`; `truth` and `attribution` are the answer key outright.
#: `fair_value` was on this list and could never fire: it is a module-level
#: function, not an engine attribute, so `hasattr(engine, "fair_value")` is
#: False and the proxy below would never have been asked for it. One entry of
#: the guard was decorative for as long as the guard existed. Every name here
#: is now pinned by `test_every_sealed_name_is_a_real_engine_attribute`, so a
#: rename upstream disarms the entry loudly instead of silently.
SEALED = ("attribution", "truth", "session_mispricing_s",
          "column", "state_snapshot", "macro_table", "bars", "order_log",
          "model_params", "model",
          # Added after an adversarial review of the shared layer found them
          # reachable and unsealed. Each verified present on a live engine
          # before being listed, which is what the phantom above did not get.
          "draws_consumed", "draws_by_stream", "model_fingerprint",
          "book_table", "snapshot_book", "session_prices", "session_volumes")


class Sealed:
    """An engine that raises if the simulator's own knowledge is touched.

    Stronger than scanning the rendered text for a leaked number, and kept
    alongside that scan. This one fails on the ACCESS, so a future edit that
    reads `engine.attribution` and then rounds it, scales it or uses it to
    pick a word fails here too.
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
    one. Rebuilt here, not captured, so a test can substitute the engine."""
    import struct

    from tradefloor.harness import Observation
    prices = list(struct.unpack("<%dd" % len(world.engine.tickers),
                                world.engine.prices()))
    return Observation(world.step, world.day, world.engine.tickers, prices,
                       world.portfolio, engine or world.engine,
                       [i.avg_volume for i in world.universe],
                       world.steps_per_day)


def test_every_sealed_name_is_a_real_engine_attribute():
    """The guard that guards the guard.

    A name in `SEALED` that the engine does not have is a clause the proxy
    can never reach: `__getattr__` only fires for an attribute somebody asks
    for, and nobody asks for one that does not exist. `fair_value` sat here
    for exactly that reason -- it is a module-level function -- so one
    eleventh of this defence was decorative and every run reported it green.

    That is the same shape as a skipped test reporting success, and the fix
    is the same: check the subject still exists rather than trusting the
    list's intent.
    """
    world, _ = one_observation(days=1)
    missing = [name for name in SEALED if not hasattr(world.engine, name)]
    assert not missing, (
        f"{missing} are listed as sealed but are not engine attributes, so "
        "the proxy can never be asked for them and those entries guard "
        "nothing. Either the engine renamed them -- in which case find the "
        "new name -- or they never belonged here.")
    assert len(set(SEALED)) == len(SEALED), "a name is listed twice"


def test_the_valuation_is_reconstructible_from_what_the_caller_supplies():
    """The limit of the ground-truth boundary, written down.

    `tradefloor.fair_value` is public, and every argument it needs is either
    a fundamental the caller is invited to supply or a macro field in
    `OBSERVABLE_MACRO`. So supplying full fundamentals hands the agent the
    means to reconstruct the model's own anchor, without reading one engine
    attribute -- which is why neither `Sealed` nor any allowlist of engine
    reads can see it happen.

    This asserts the claim the module docstring makes, in both directions. If
    `fair_value` ever gains a required argument that is neither supplied nor
    observable, the reconstruction stops being possible and the docstring
    starts over-warning; this fails and says so.
    """
    import inspect

    required = {name for name, p in
                inspect.signature(tf.fair_value).parameters.items()
                if p.default is inspect.Parameter.empty}
    supplied = {"sector", "eps", "book_value_per_share", "revenue_growth",
                "beta"}
    observable = set(fr.OBSERVABLE_MACRO)
    assert required <= supplied | observable, (
        f"fair_value now requires {sorted(required - supplied - observable)}, "
        "which the caller cannot supply and the agent cannot observe. The "
        "module docstring's warning about reconstruction is now too strong.")

    # The anchor reconstructs EXACTLY. It is a pure function of six inputs,
    # so there is nothing approximate about this half.
    world, agent = one_observation(days=2)
    facts = {"TECH_A": {"sector": "technology", "eps": 3.0,
                        "book_value_per_share": 15.0, "revenue_growth": 0.30}}
    payload = fr.observe(_observation(world), history=agent.history,
                         fundamentals=facts)
    asset = payload["assets"][0]
    f, macro = asset["fundamentals"], payload["macro"]
    reconstructed = tf.fair_value(
        eps=f["eps"], sector=f["sector"], revenue_growth=f["revenue_growth"],
        book_value_per_share=f["book_value_per_share"],
        federal_funds_rate=macro["federal_funds_rate"],
        corporate_bond_yield=macro["corporate_bond_yield"]).fair_value
    direct = tf.fair_value(
        eps=3.0, sector="technology", revenue_growth=0.30,
        book_value_per_share=15.0, federal_funds_rate=0.04,
        corporate_bond_yield=0.055).fair_value
    assert reconstructed == direct, (
        "fair_value is a pure function of six inputs and the payload carries "
        "all six, so this is an equality and not an approximation")


#: How close `log(price / fair_value)` lands to the engine's `mispricing_s`.
#: Measured, not chosen: the error is roster- and moment-dependent, so this
#: is a ceiling generous enough not to be flaky and tight enough to fail if
#: the relationship stops holding at all.
#:
#: It lives here rather than in a docstring for a reason. Three people
#: measured this claim and produced three numbers -- "exact", "a tenth of a
#: percentage point", "8.5 percentage points" -- because two of the three
#: inverted it as `price / fair_value - 1`. The engine applies the
#: mispricing as `fair_value * exp(s)`, so the ratio form is the wrong
#: arithmetic and both derived figures were artefacts. A number in prose
#: cannot be re-derived by the next reader; this one can.
MISPRICING_TOLERANCE = 0.05


def test_the_state_variable_is_approximable_but_not_recoverable():
    """The other half, and the half that is NOT exact.

    `mispricing_s` is a state variable, not a ratio that can be read off a
    price. The traded price carries microstructure on top of the anchor, so
    even the correct inversion lands near rather than on it.
    """
    import math
    import struct

    world, _agent = one_observation(days=4)
    engine = world.engine
    n = len(engine.tickers)
    prices = list(struct.unpack("<%dd" % n, engine.prices()))
    truth = list(struct.unpack("<%dd" % n, engine.column("mispricing_s")))

    facts = {"TECH_A": {"sector": "technology", "eps": 3.0,
                        "book_value_per_share": 15.0, "revenue_growth": 0.30}}
    payload = fr.observe(_observation(world), history=[], fundamentals=facts)
    macro = payload["macro"]
    f = facts["TECH_A"]
    value = tf.fair_value(
        eps=f["eps"], sector=f["sector"], revenue_growth=f["revenue_growth"],
        book_value_per_share=f["book_value_per_share"],
        federal_funds_rate=macro["federal_funds_rate"],
        corporate_bond_yield=macro["corporate_bond_yield"]).fair_value

    approximation = math.log(prices[0] / value)
    error = abs(approximation - truth[0])
    assert error < MISPRICING_TOLERANCE, (
        f"log(price/fair_value) is {error:.6f} from mispricing_s, past the "
        "measured tolerance. Either the engine changed how it applies the "
        "mispricing, or the approximation no longer holds at all.")
    # And it is genuinely an approximation, not a hidden equality. If this
    # ever fails, the state variable became recoverable and the module
    # docstring's careful distinction has collapsed.
    assert error > 0.0, (
        "log(price/fair_value) now equals mispricing_s exactly, which the "
        "docstring says it does not. Re-read that paragraph before relaxing "
        "this.")


def test_the_mapping_never_touches_simulator_ground_truth():
    world, agent = one_observation(days=3)
    obs = _observation(world, engine=Sealed(world.engine))
    payload = fr.observe(obs, history=agent.history)
    fr.render(payload)              # raises through Sealed if it reached one
    assert payload["assets"], "the sealed run produced nothing to check"


def test_no_hidden_value_appears_in_the_text_finrobot_receives():
    """The complementary check. The proxy above catches the adapter asking;
    this catches the answer arriving in the block by a route nobody
    predicted."""
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


class _NotJsonable:
    """Stands in for the objects a real `llm_config` holds.

    AutoGen documents `http_client` and a `filter_dict` callable as members,
    so a config carrying something `json` cannot serialise is ordinary rather
    than exotic.
    """

    def __repr__(self):
        return "<a client object>"


@pytest.mark.parametrize("config", [
    # nested inside config_list, which is where a client usually sits
    {"config_list": [{"model": "gpt-4o", "api_key": "k",
                      "http_client": _NotJsonable()}]},
    # and at the top level, which AutoGen also allows
    {"config_list": [{"model": "gpt-4o"}], "http_client": _NotJsonable()},
    # a callable, which is what filter_dict is
    {"config_list": [{"model": "gpt-4o"}], "filter_dict": lambda d: True},
    # and under a generation key, which is lifted by name
    {"config_list": [{"model": "m"}], "temperature": _NotJsonable()},
])
def test_a_config_that_is_not_json_still_builds_an_adapter(config):
    """The regression this test exists for.

    Recording `config_digest` meant digesting the caller's `llm_config`, and
    the shared digest falls back to `json.dumps` for anything that is not a
    string. A config holding a client object then raised `TypeError` out of
    the constructor -- on input the released adapter accepted, and as an
    exception `except ValidationError` does not catch, which is what every
    docstring here tells a caller to write.

    A digest has to be stable and one-way. It does not have to round-trip,
    so the unserialisable parts become their type name and construction
    proceeds.
    """
    agent = fr.FinRobotAdapter(mode="live", llm_config=config)
    assert agent.info.config_digest, "the config left no trace"
    assert len(agent.info.config_digest) == 16
    # And the record still serialises, which is what a fixture needs.
    assert json.dumps(agent.provenance())


def test_the_config_digest_still_tells_two_configs_apart():
    """Rendering the unserialisable parts must not collapse them together.

    A digest that returned the same value for every config would satisfy the
    test above and record nothing, which is the failure mode of a fix that
    only stops an exception.
    """
    def built(config):
        return fr.FinRobotAdapter(mode="live",
                                  llm_config=config).info.config_digest

    a = built({"config_list": [{"model": "gpt-4o"}], "temperature": 0.0})
    b = built({"config_list": [{"model": "gpt-4o"}], "temperature": 1.0})
    c = built({"config_list": [{"model": "other"}], "temperature": 0.0})
    d = built({"config_list": [{"model": "gpt-4o"}], "temperature": 0.0,
               "http_client": _NotJsonable()})
    assert len({a, b, c, d}) == 4, "the digest stopped discriminating"


def test_a_non_string_mandate_does_not_break_construction():
    """`digest` is str-only, and the mandate is a caller-supplied argument.

    Before the metadata was recorded, a non-string mandate reached FinRobot
    and failed there, in live mode. Digesting it at construction turned that
    into an `AttributeError` from `__init__`, which is both earlier and
    uncatchable as a `ValidationError`.
    """
    agent = fr.FinRobotAdapter(
        mode="live", llm_config={"config_list": [{"model": "m"}]},
        mandate=_NotJsonable())
    assert agent.info.instructions_digest


def test_the_mandate_digest_is_unchanged_for_a_real_mandate():
    """The `str()` above must not move the shipped fixture's key.

    `str()` on a string is that string, so the stamped digest still matches.
    If this ever fails, every recorded transcript stops replaying.
    """
    assert fr.digest(str(fr.MANDATE)) == fr.digest(fr.MANDATE)


def test_the_adapter_metadata_carries_no_credential():
    """`info` is written into a committed fixture, so it is the second place
    a key could escape after `state()`.

    The provider, the model and the generation parameters are lifted out of
    `llm_config` BY NAME. The config object itself never reaches this record.
    What stands in for it is `config_digest`, which is enough to prove two
    arms ran the same configuration and cannot be read back into one.
    """
    agent = fr.FinRobotAdapter(
        mode="live",
        llm_config={"config_list": [{"model": "m", "api_type": "anthropic",
                                     "api_key": "sk-ant-SECRET"}],
                    "temperature": 0.0})
    published = json.dumps(agent.info.as_dict())
    for secret in ("api_key", "sk-", "Bearer ", "SECRET"):
        assert secret not in published, f"the metadata leaked {secret!r}"

    # The named values ARE lifted, so the record still says what ran.
    assert agent.info.provider == "anthropic"
    assert agent.info.model == "m"
    assert agent.info.generation == {"temperature": 0.0}
    assert agent.info.config_digest, (
        "the config has to leave a one-way trace, or two arms cannot be "
        "shown to have run the same configuration")
    assert json.dumps(agent.provenance())          # serialises for a fixture


@pytest.mark.skipif(not FIXTURE.exists(), reason="no recorded FinRobot run")
def test_every_recorded_meta_field_still_has_somewhere_to_live():
    """The shipped fixture's `meta` is the specification for this.

    `AdapterInfo` has fixed fields on purpose -- that is what leaves nowhere
    to put a credential -- and the risk of fixed fields is that a run stops
    being able to record something it needs. The recorded run is the concrete
    statement of what a FinRobot experiment must carry, so every key in it
    has to map somewhere in `provenance()`, whether by name, in `generation`
    or in `extra`.
    """
    meta = json.loads(FIXTURE.read_text(encoding="utf-8"))["meta"]
    agent = fr.FinRobotAdapter(
        mode="live", llm_config={"config_list": [{"model": "m"}],
                                 "temperature": 0.0})
    published = agent.provenance()
    housed = dict(published)
    housed.update(published.get("generation") or {})
    housed.update(published.get("extra") or {})

    homeless = sorted(k for k in meta if k not in housed)
    assert not homeless, (
        f"the recorded run carries {homeless}, and provenance() has nowhere "
        "to put them. A fixed-field record that cannot hold what a real run "
        "needs stops being provenance.")


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


def test_several_instructions_in_one_answer_are_all_kept_in_order():
    """A portfolio decision is usually more than one trade.

    Every other validation test names one symbol, which leaves the loop over
    `actions` covered only at length one. Order is asserted with it: the
    example prints the actions of a decision side by side across the two arms,
    and a mapping that reordered them would report a divergence in wording
    that the agent did not produce.
    """
    decision = fr.parse(answer(act("TECH_A", "BUY", 100),
                               act("DEFENSIVE_A", "SELL", 40),
                               act("OTHER", "HOLD")))
    assert [(a.symbol, a.side, a.quantity) for a in decision.actions] == [
        ("TECH_A", "BUY", 100.0),
        ("DEFENSIVE_A", "SELL", 40.0),
        ("OTHER", "HOLD", 0.0)]
    assert [a.signed() for a in decision.actions] == [100.0, -40.0, 0.0]


def test_an_empty_action_list_means_change_nothing():
    decision = fr.parse(answer())
    assert decision.actions == []


@pytest.mark.parametrize("text,rationale", [
    ('{"actions": null, "rationale": "none"}', "none"),    # an explicit null
    ('{"actions": []}', ""),                               # no 'rationale' key
    ('{"actions": [], "rationale": null}', ""),
])
def test_the_null_and_the_empty_read_as_the_documented_no_op(text, rationale):
    """A present 'actions' key that is empty or null means change nothing.

    The key being THERE is what makes this a decision: whoever wrote it was
    answering the question the mandate asked and declined to trade. Refusing
    it would score a decision to change nothing -- which the mandate
    explicitly offers -- as a malformed answer. An ABSENT key is the
    opposite, and the test below covers it.
    """
    decision = fr.parse(text)
    assert decision.actions == []
    assert decision.rationale == rationale


@pytest.mark.parametrize("text,keys", [
    ('{"rationale": "nothing to do"}', "rationale"),
    ('{"recommendation": "trim TECH_A", "confidence": 0.8}',
     "confidence, recommendation"),
    ('Here is my view:\n{"analysis": "rates are rising"}\nHope that helps.',
     "analysis"),
])
def test_an_answer_with_no_actions_key_is_refused_not_read_as_a_hold(text,
                                                                     keys):
    """The behaviour change, pinned. A JSON object with no 'actions' key used
    to parse as an empty decision, and that was a defect in released code.

    A model half-following the mandate writes what it would say to a person,
    and the brace search finds it. Read as a hold, the run completes with
    `trades=0` and an empty `errors` list -- indistinguishable from an agent
    that considered the market and declined. The refusal names the keys it
    did get, because the usual cause is a model answering in prose shape and
    the reader needs to see which shape.
    """
    with pytest.raises(fr.DecisionError, match="no 'actions' key") as excinfo:
        fr.parse(text)
    assert keys in str(excinfo.value), (
        "the refusal has to name the keys it got, or a reader cannot tell "
        "which of the many ways to miss the contract this was")


@pytest.mark.parametrize("text,match", [
    ('{"actions": [], "confidence": 0.8}', "confidence"),
    ('{"actions": [], "rationale": "x", "notes": "more"}', "notes"),
    ('{"actions": [{"symbol": "A", "side": "BUY", "quantity": 5, '
     '"stop_loss": 95.0}]}', "stop_loss"),
    ('{"actions": [{"symbol": "A", "side": "BUY", "quantity": 5, '
     '"time_in_force": "gtc"}]}', "time_in_force"),
    ('{"actions": [{"symbol": "A", "side": "BUY", "quantity": 5, '
     '"order_type": "limit"}]}', "market sweeps only"),
    ('{"actions": [{"symbol": "A", "side": "BUY", "quantity": 5, '
     '"limit_price": 99.5}]}', "no limit orders"),
])
def test_a_field_the_market_cannot_honour_is_refused_by_name(text, match):
    """Silently dropping an unknown field executes an instruction the agent
    did not give.

    A model that writes `stop_loss` believes it has protection and sizes
    accordingly. Dropping the field buys at market with none, and the trace
    then records a decision nobody made. `order_type` and `limit_price` get
    refusals naming the missing capability because they are what a model
    reaches for first; everything else is refused as unreadable rather than
    ignored.
    """
    with pytest.raises(fr.DecisionError, match=match):
        fr.parse(text)


def test_a_market_order_type_is_tolerated_because_it_is_what_happens_anyway():
    """`order_type: "market"` states the only thing this market does, so it
    is redundant rather than wrong. Refusing it would fail a model for
    describing the execution it was going to get."""
    decision = fr.parse('{"actions": [{"symbol": "A", "side": "BUY", '
                        '"quantity": 5, "order_type": "market", '
                        '"limit_price": null}]}')
    assert decision.actions[0].signed() == 5.0


def test_the_refusal_matches_the_shared_validator():
    """The whole point of the change: one answer, both adapters.

    `tests/test_integrations.py` holds the shared layer to this rule. If the
    two ever disagree again, a study that scores a FinRobot arm against
    another framework's is no longer controlled, and neither scorecard says
    so.
    """
    from tradefloor.integrations import common

    for text in ('{"rationale": "x"}', '{"recommendation": "sell"}'):
        with pytest.raises(fr.DecisionError):
            fr.parse(text)
        with pytest.raises(common.DecisionError):
            common.parse_decision(text)
    # And they still agree on the shapes that ARE a decision.
    for text in ('{"actions": []}', '{"actions": null}',
                 '{"actions": [{"symbol": "A", "side": "BUY", '
                 '"quantity": 3}]}'):
        assert (fr.parse(text).as_dict()
                == common.parse_decision(text).as_dict())


def test_a_quantity_of_null_is_read_as_none():
    """`"quantity": null` beside a HOLD is what the mandate asks for when it
    says the field is omitted or zero, and some models write it that way."""
    decision = fr.parse(
        '{"actions": [{"symbol": "TECH_A", "side": "HOLD", '
        '"quantity": null}]}')
    assert decision.actions[0].quantity == 0.0
    assert decision.actions[0].signed() == 0.0


def test_a_padded_symbol_is_trimmed_rather_than_refused():
    """Whitespace around a ticker is a formatting slip, not a decision to
    trade something else. `orders_from` matches against the listed universe by
    string, so an untrimmed symbol would be refused as unlisted."""
    decision = fr.parse(answer(act("  TECH_A  ", "BUY", 5)))
    assert decision.actions[0].symbol == "TECH_A"


def test_lowercase_sides_are_accepted():
    """Case carries no meaning here. Rejecting it would score a correct
    decision as a failure."""
    assert fr.parse(answer(act("TECH_A", "buy", 5))).actions[0].side == "BUY"


def test_a_fenced_answer_is_accepted():
    """Models add code fences and closing sentences whatever the mandate
    says. This integration measures portfolio decisions, not instruction
    compliance."""
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


def test_a_decision_error_is_caught_by_both_names():
    """`DecisionError` derives from the shared adapter error, and that has to
    ADD a way to catch it rather than replace one.

    Two clauses have to keep working: the one written before the shared layer
    existed, `except tradefloor.ValidationError`, and the one written against
    it, `except common.DecisionError`. The first is the compatibility
    promise -- the integration shipped in 0.6.0 and callers already catch
    FinRobot's refusals that way -- and the second is the whole point of
    moving the base.
    """
    from tradefloor.integrations import common

    assert issubclass(fr.DecisionError, common.DecisionError)
    assert issubclass(fr.DecisionError, common.IntegrationError)
    assert issubclass(fr.DecisionError, tf.ValidationError)

    for catch in (tf.ValidationError, common.IntegrationError,
                  common.DecisionError, fr.DecisionError):
        with pytest.raises(catch):
            fr.parse("not a decision at all")

    # And not the other way round. The shared error must not start catching
    # things only FinRobot raises, or a caller handling one adapter would
    # silently be handling all of them.
    assert not issubclass(common.DecisionError, fr.DecisionError)


def test_importing_the_adapter_still_needs_no_framework_after_the_rebase():
    """`finrobot` now imports `common` at module scope, and that is only safe
    because `common` imports nothing but the standard library and Tradefloor.

    The subpackage's rule is that reaching an adapter must never require a
    third party. A shared module that grew a framework import at module scope
    would break that for every adapter at once, and the failure would look
    like FinRobot's fault.
    """
    source = (REPO / "python" / "tradefloor" / "integrations"
              / "common.py").read_text(encoding="utf-8")
    import ast
    imported = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            # Relative imports are first-party Tradefloor and always fine.
            if node.level == 0:
                imported.add(node.module.split(".")[0])
    allowed = {"copy", "hashlib", "importlib", "json", "re", "statistics",
               "typing", "asyncio", "inspect", "concurrent", "pathlib",
               "__future__"}
    assert imported <= allowed, (
        f"common.py imports {sorted(imported - allowed)} at module scope. "
        "finrobot.py imports common at module scope, so anything common "
        "imports is something `import tradefloor.integrations.finrobot` now "
        "requires.")


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


def test_a_zero_cap_is_a_cap_and_not_an_absence_of_one():
    """`if cap > 0` read "no volume to participate in" as "no cap at all".

    With `avg_volume` or `max_participation` at zero, an arbitrarily large
    order passed through whole and unnoted, while the payload showed the
    agent `max_order_shares: 0.0` -- the observation and the enforcement
    stating opposite things about the same limit.
    """
    world, _ = one_observation(days=1)
    obs = _observation(world)
    decision = fr.parse(answer(act("TECH_A", "BUY", 1e12)))

    orders, notes = fr.orders_from(decision, obs, max_participation=0.0)
    assert orders == {}, "a zero cap let a trillion shares through"
    assert len(notes) == 1 and "clipped to 0" in notes[0], (
        "being clipped to nothing is something the agent did, so the trace "
        f"has to carry it; got {notes}")

    # And the payload agrees with the enforcement, which is the actual claim.
    payload = fr.observe(obs, history=[], max_participation=0.0)
    assert payload["assets"][0]["max_order_shares"] == 0.0


def test_a_json_object_naming_a_key_twice_is_refused():
    """`json.loads` keeps the LAST occurrence, silently.

    `parse` already refuses a SYMBOL named twice, because two instructions
    with no defined order cannot both reach the market. The JSON layer owed
    the same principle to duplicate keys, and did not enforce it: which of
    the model's two statements counted depended on emission order and
    nothing recorded that another existed.
    """
    with pytest.raises(fr.DecisionError, match="more than once"):
        fr.parse('{"actions": [], "actions": '
                 '[{"symbol": "TECH_A", "side": "BUY", "quantity": 500}]}')
    # Inside an action object too, not only at the top level.
    with pytest.raises(fr.DecisionError, match="more than once"):
        fr.parse('{"actions": [{"symbol": "A", "symbol": "B", '
                 '"side": "BUY", "quantity": 5}]}')
    # And through the brace-search path, which is a second call site.
    with pytest.raises(fr.DecisionError, match="more than once"):
        fr.parse('Here you go:\n```json\n{"rationale": "a", '
                 '"rationale": "b", "actions": []}\n```')


@pytest.mark.parametrize("symbol,side,quantity,match", [
    ("A", "SHORT", 5, "side must be one of"),
    ("A", "buy", 5, "side must be one of"),      # case belongs to parse
    ("A", "SELL", -5, "non-negative"),
    ("A", "BUY", float("inf"), "finite"),
    ("A", "BUY", float("nan"), "non-negative"),
])
def test_an_action_refuses_what_it_cannot_mean(symbol, side, quantity, match):
    """`Action` validated nothing, one layer below where `parse` refuses.

    `Action("A", "SHORT", -5)` constructed, and `signed()` returns 0.0 for an
    unrecognised side -- so an unknown side became a silent HOLD and a
    negative SELL became a sign-flipped BUY. Anybody building an Action
    directly, which the class being public invites, got a decision the
    market executed differently from how it read.
    """
    with pytest.raises(fr.DecisionError, match=match):
        fr.Action(symbol, side, quantity)


def test_a_valid_action_still_constructs_every_way_it_used_to():
    """The compatibility half. Validation must refuse only the unmeanable."""
    assert fr.Action("A", "BUY", 5).signed() == 5.0
    assert fr.Action("A", "SELL", 5).signed() == -5.0
    assert fr.Action("A", "HOLD").signed() == 0.0        # default quantity
    assert fr.Action("A", "BUY", 0).quantity == 0.0
    assert fr.Action("A", "BUY", "5").quantity == 5.0    # coerced, as before


def test_a_recorded_null_response_gets_its_own_diagnosis():
    """Two failures, opposite remedies, one message between them.

    `response_for` returns None both for a missing entry and for an entry
    whose response is null. Telling the second "the inputs changed, re-record
    with --live --record" sends a reader to spend sixty live calls
    re-recording an interaction that is sitting right there.
    """
    world, _ = one_observation(days=2)
    obs = _observation(world)
    prompt = fr.render(fr.observe(obs, history=[list(obs.prices)]))

    transcript = fr.Transcript()
    transcript.record({"digest": fr.digest(prompt), "step": obs.step,
                       "response": None})
    agent = fr.FinRobotAdapter(mode="replay", transcript=transcript, every=1)
    with pytest.raises(fr.DecisionError) as excinfo:
        agent.act(obs)
    message = str(excinfo.value)
    assert "has no response" in message
    assert "nothing has drifted" in message, (
        "the reader has to be told the recording is fine, or they will "
        "re-record it")
    assert "--live --record" not in message, (
        "that is the OTHER failure's remedy and sends sixty live calls at "
        "an interaction that is already recorded")


def test_entry_for_and_response_for_answer_different_questions():
    transcript = fr.Transcript()
    transcript.record({"digest": "abc", "response": None, "step": 0})
    assert transcript.response_for("abc") is None
    assert transcript.response_for("missing") is None
    assert transcript.entry_for("abc") is not None, (
        "the entry exists; only its response is null")
    assert transcript.entry_for("missing") is None


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


def test_a_decision_naming_several_symbols_reaches_the_market_as_several():
    """Both directions in one decision, executed together.

    `orders_from` returns a dict keyed by symbol, so a decision that bought
    one name and sold another has to arrive as two entries with opposite
    signs. The single-order test above cannot tell a working loop from one
    that keeps the last action it saw.
    """
    agent = Scripted(answer(act("TECH_A", "BUY", 3_000),
                            act("DEFENSIVE_A", "SELL", 2_000)), every=6)
    world = World(seed=7, universe=universe(), agent=agent, cash=1_000_000.0,
                  pins={"federal_funds_rate": 0.04})
    world.run(days=2)

    assert agent.record[0]["orders"] == {"TECH_A": 3_000.0,
                                         "DEFENSIVE_A": -2_000.0}
    assert world.portfolio.positions["TECH_A"].quantity > 0
    assert world.portfolio.positions["DEFENSIVE_A"].quantity < 0
    assert not world.rejected, world.rejected


def test_a_decision_to_change_nothing_sends_nothing_and_is_still_recorded():
    """The no-op, end to end.

    An agent that decided to leave the book alone and one that failed to
    answer both send no orders, and the difference between them is the whole
    reason `parse` refuses an empty response. This is the first half: the
    decision is published, the record carries it, and the market sees no
    order.
    """
    agent = Scripted(answer(rationale="the book is where I want it"), every=6)
    world = World(seed=7, universe=universe(), agent=agent, cash=1_000_000.0)
    world.run(days=2)

    assert all(entry["orders"] == {} for entry in agent.record)
    assert agent.decision()["actions"] == []
    assert agent.decision()["rationale"] == "the book is where I want it"
    assert not any(row["fills"] for row in world.trace)
    assert world.portfolio.cash == 1_000_000.0


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


# -- when FinRobot itself fails ---------------------------------------------
#
# `_live` is the one method that reaches outside the process, and the tests
# below drive the REAL one. They replace `_finrobot`, which returns the
# assembled `SingleAssistant`, with a stand-in of the same shape: a
# `.user_proxy` with `initiate_chat`, an `.assistant`, and `.reset()`. What
# runs is the shipped call sequence and the shipped error handling.


class _Assistant:
    """The two agents `SingleAssistant` assembles, and what it does with them.

    Narrow on purpose. Everything the adapter touches on a real one is here
    and nothing else is, so a change to `_live` that reached for another
    attribute fails on this rather than passing against a permissive mock.
    """

    def __init__(self, reply):
        self.reply = reply
        self.assistant = object()
        self.resets = 0
        self.user_proxy = self

    def initiate_chat(self, assistant, **kwargs):
        return self.reply(**kwargs)

    def reset(self):
        self.resets += 1


class _Chat:
    def __init__(self, history):
        self.chat_history = history


def _driven_by(reply):
    """A live adapter whose FinRobot is `reply`, with everything else real."""

    class Driven(fr.FinRobotAdapter):
        def _finrobot(self):
            if self._assistant is None:
                self._assistant = _Assistant(reply)
            return self._assistant

    return Driven(mode="live",
                  llm_config={"config_list": [{"model": "none"}]}, every=1)


def test_the_response_is_the_last_message_of_the_conversation():
    """`SingleAssistant.chat` prints and returns nothing, so `_live` drives
    the same two agents itself and keeps the result. What it keeps is the
    assistant's reply, and the agents are reset afterwards so the next
    decision starts from the mandate rather than from this one."""
    world, _ = one_observation(days=1)
    agent = _driven_by(lambda **kwargs: _Chat(
        [{"role": "user", "content": kwargs["message"]},
         {"role": "assistant", "content": answer(act("TECH_A", "BUY", 25))}]))
    orders = agent.act(_observation(world))
    assert orders == {"TECH_A": 25.0}
    assert agent._assistant.resets == 1, (
        "the conversation has to be reset between decisions, or the next "
        "prompt is answered in the context of this one")


def test_an_error_from_the_framework_arrives_typed_with_its_chain_intact():
    """A rate limit, an auth failure, a provider outage.

    These are a different thing from a bad decision and the error type has to
    say which: the agent never answered, so scoring it as "the agent decided
    badly" would put a network outage in the same column as a malformed
    portfolio. `FrameworkError` is that distinction, and it is what the
    shared contract check requires of every adapter.

    The original exception rides on `__cause__`. That is the compatibility
    half: a caller who was catching the provider's own exception can still
    reach it, and the traceback they debug still ends at the provider rather
    than at this adapter.
    """
    class ProviderError(RuntimeError):
        pass

    def explode(**_kwargs):
        raise ProviderError("429 rate limited")

    agent = _driven_by(explode)
    world = World(seed=7, universe=universe(), agent=agent, cash=1_000_000.0)
    with pytest.raises(fr.FrameworkError) as excinfo:
        world.run(days=1)
    assert "429 rate limited" in str(excinfo.value)
    assert "ProviderError" in str(excinfo.value), (
        "the message should name the exception type the framework raised")
    assert isinstance(excinfo.value.__cause__, ProviderError), (
        "the original must ride on __cause__, or the traceback a user debugs "
        "stops at the adapter")
    assert isinstance(excinfo.value, tf.ValidationError), (
        "FrameworkError reaches ValidationError through IntegrationError, so "
        "a caller catching the library's refusals still catches this")


def test_the_adapters_own_refusals_are_not_wrapped_as_framework_failures():
    """`act` re-raises the adapter family untouched.

    A replay miss and a missing install are already actionable, and already
    say what to do. Wrapping either in a `FrameworkError` would bury the
    digest, or the pip command, one level down in a message about the
    framework having raised -- when the framework was never called.
    """
    world, _ = one_observation(days=2)
    obs = _observation(world)
    replayer = fr.FinRobotAdapter(mode="replay", transcript=fr.Transcript(),
                                  every=1)
    with pytest.raises(fr.DecisionError) as excinfo:
        replayer.act(obs)
    assert not isinstance(excinfo.value, fr.FrameworkError)
    assert "--live --record" in str(excinfo.value)


def test_only_one_refusal_in_the_adapter_throws_away_what_caused_it():
    """`raise ... from None` erases the chain, and the chain is the diagnosis.

    Where the adapter raises its own exception on top of somebody else's, the
    original has to stay attached: the person reading the traceback is usually
    debugging the third party, and a message about the extra shown to somebody
    whose install is merely broken sends them to reinstall what they have.

    One use is deliberate. In `parse`, the strict `json.loads` failing is not
    the error -- the adapter goes on to search the text for braces, and
    reporting the first failure would name a step that was expected to fail.
    Every other refusal in the module carries `from exc`, and a second name in
    this list means a new one does not.
    """
    import ast
    source = (REPO / "python" / "tradefloor" / "integrations"
              / "finrobot.py").read_text(encoding="utf-8")
    dropped = []
    for function in ast.walk(ast.parse(source)):
        if not isinstance(function, ast.FunctionDef):
            continue
        for handler in ast.walk(function):
            if not isinstance(handler, ast.ExceptHandler):
                continue
            for node in ast.walk(handler):
                if (isinstance(node, ast.Raise)
                        and isinstance(node.cause, ast.Constant)
                        and node.cause.value is None):
                    dropped.append(function.name)
    assert sorted(set(dropped)) == ["parse"], (
        f"these refusals drop the exception chain: {sorted(set(dropped))}")


def test_an_empty_conversation_is_refused_rather_than_read_as_a_hold():
    """A call that produced no message did not produce a decision.

    The same rule as an empty response string, one layer out: `chat_history`
    can come back empty when the provider returns nothing at all, and an
    adapter treating that as no change would score a failed call as a
    considered choice.
    """
    world, _ = one_observation(days=1)
    agent = _driven_by(lambda **_kwargs: _Chat([]))
    with pytest.raises(fr.DecisionError, match="empty conversation"):
        agent.act(_observation(world))


def test_a_message_with_no_content_is_refused_by_the_parser():
    """`.get("content") or ""` turns a missing or null content into the empty
    string, which `parse` then refuses by name. Two layers, and the second is
    the one with the readable message."""
    world, _ = one_observation(days=1)
    agent = _driven_by(lambda **_kwargs: _Chat([{"role": "assistant"}]))
    with pytest.raises(fr.DecisionError, match="empty"):
        agent.act(_observation(world))


# -- fork and intervention --------------------------------------------------


def test_both_arms_start_identical_with_a_finrobot_agent():
    """The claim the experiment rests on, with this adapter in the loop.

    `tests/test_counterfactual.py` already proves `agree` for the market and
    the portfolio. The AGENT half is what this adds. An adapter whose
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
    """`fork` builds `type(self)`. Hard-coding the base class let the run
    complete and compare two agents, neither of them the one under test."""
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
        "the agent reads the rate, so its first post-fork decision should "
        "be the first one to differ")
    assert report.divergence.orders == fork_step
    assert report.control["label"] == "control"
    assert report.treatment["turnover"] > 0


def test_a_scenario_reaches_the_agent_on_the_day_it_fires_and_not_before():
    """`World.apply` drives an arm from a scenario document, and the adapter
    has to work under one exactly as it does under `intervene`.

    Both halves matter. A scenario declares a macro PATH, and the whole path
    is known to the engine from the moment it is applied, so an adapter
    reading anything but today's macro state would hand the agent a number
    the market has not reached. That is the same ground-truth leak as fair
    value, arriving by a different route, and no allowlist of field NAMES
    catches it -- `corporate_bond_yield` is on the list; next week's is still
    the answer key.
    """
    seen = []

    def script(obs):
        seen.append((obs.day, obs.engine.macro_state.corporate_bond_yield))
        return answer()

    agent = Scripted(script, every=6)
    world = World(seed=7, universe=universe(), agent=agent, cash=1_000_000.0,
                  pins={"federal_funds_rate": 0.04,
                        "corporate_bond_yield": 0.055})
    world.apply(tf.Scenario(name="widen").shock(
        "macro.corporate_yield", operation="add", value=0.02, at=2))
    world.run(days=4)

    assert [day for day, _ in seen] == [0, 1, 2, 3]
    before = [yield_ for day, yield_ in seen if day < 2]
    assert all(y == pytest.approx(0.055) for y in before), (
        "the agent was shown a yield the run had not reached yet")
    on_the_day = dict(seen)[2]
    assert on_the_day == pytest.approx(0.075)

    # And what it was SHOWN is what the engine held, not something derived.
    rendered = [p for p in agent.prompts if "0.0750" in p]
    assert len(rendered) == 1, (
        "the shocked yield should appear in exactly the one prompt built on "
        "the day it fired")


def test_an_agent_that_ignores_the_rate_never_diverges_in_its_decisions():
    """The negative case. A comparison has to be able to report that the
    intervention moved the market and left the agent unmoved, which is a
    finding in its own right."""
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


def test_a_transcript_round_trips_through_a_file(tmp_path):
    """`save` and `load`, which is how a recording actually travels.

    `to_json`/`from_json` are checked above and are not the same thing: `save`
    writes the parent directory into existence, and a `--live --record` run
    pointed at a fixture path that does not exist yet is the shipped case.
    """
    transcript = fr.Transcript(meta={"framework": "FinRobot"})
    transcript.record({"digest": "abc", "response": "hello", "step": 0})
    path = tmp_path / "not-yet" / "rate-shock.json"
    transcript.save(path)

    again = fr.Transcript.load(path)
    assert again.meta == {"framework": "FinRobot"}
    assert again.response_for("abc") == "hello"
    assert len(again) == 1


def test_re_recording_one_prompt_replaces_the_answer_to_it():
    """Last write wins, and it has to.

    A replay is keyed by the prompt, so an identical prompt must have exactly
    one answer available. Two entries under one digest would make the replayed
    decision depend on which the lookup happened to reach.
    """
    transcript = fr.Transcript()
    transcript.record({"digest": "abc", "response": "first", "step": 0})
    transcript.record({"digest": "abc", "response": "second", "step": 6})
    assert transcript.response_for("abc") == "second"
    assert len(transcript) == 2, (
        "both interactions stay in the record -- the run really did ask "
        "twice, and an audit of the transcript should see that")


def test_the_replay_key_is_the_input():
    """Two different observations must not collide, and the same one must."""
    assert fr.digest("a") == fr.digest("a")
    assert fr.digest("a") != fr.digest("a ")


def test_replay_reproduces_a_recorded_decision_without_a_network():
    world, agent = one_observation(days=2)
    obs = _observation(world)
    # `act` appends the prices it is shown before rendering, so the prompt a
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


def test_a_recorded_run_replays_into_the_same_experiment():
    """Record, then replay, and get the same run back.

    The claim `--live --record` makes, and the one the shipped fixture rests
    on. It is checked here on a run this test recorded itself, so a change
    that broke the round trip is caught even when the fixture is absent -- and
    it is checked through the real `_ask`, which is the method that writes to
    the recorder and reads back from the transcript.

    Same decisions, same orders, same market. The digests match too, which is
    the stronger statement: the replay found each answer by rebuilding the
    exact text the recording was made from.
    """
    script = answer(act("TECH_A", "BUY", 3_000),
                    act("DEFENSIVE_A", "SELL", 1_000))
    recorder = fr.Transcript(meta={"framework": "FinRobot"})
    live = Recorded(script, every=6, recorder=recorder, arm="shared")
    recorded_world = World(seed=7, universe=universe(), agent=live,
                           cash=1_000_000.0, pins={"federal_funds_rate": 0.04})
    recorded_world.run(days=3)
    assert len(recorder) == 3

    replayer = fr.FinRobotAdapter(mode="replay", transcript=recorder, every=6,
                                  arm="shared")
    replayed_world = World(seed=7, universe=universe(), agent=replayer,
                           cash=1_000_000.0, pins={"federal_funds_rate": 0.04})
    replayed_world.run(days=3)

    assert [e["digest"] for e in replayer.record] == \
        [e["digest"] for e in live.record]
    assert [e["decision"] for e in replayer.record] == \
        [e["decision"] for e in live.record]
    assert [e["orders"] for e in replayer.record] == \
        [e["orders"] for e in live.record]
    assert replayed_world.net_worth() == recorded_world.net_worth()


def test_a_recording_holds_the_interaction_and_not_the_conclusion():
    """What the recorder writes is the input and the raw answer.

    The parsed decision, the orders and the clip notes are all derived, and
    re-deriving them on replay is the point: a transcript carrying them would
    replay a validation that ran under the code of the day it was recorded.
    `tests/fixtures/finrobot/` is asserted against this same field list, so
    the recorder and the shipped fixture cannot drift apart.
    """
    recorder = fr.Transcript()
    agent = Recorded(answer(act("TECH_A", "BUY", 100)), every=6,
                     recorder=recorder, arm="control")
    world = World(seed=7, universe=universe(), agent=agent, cash=1_000_000.0)
    world.run(days=1)

    entry = recorder.entries[0]
    assert set(entry) == {"arm", "step", "day", "digest", "prompt", "response"}
    assert entry["arm"] == "control"
    assert entry["digest"] == fr.digest(entry["prompt"])
    assert "SIMULATED MARKET" in entry["prompt"]


def test_the_price_memory_is_bounded():
    """`history` is the agent's own record and grows a row a step.

    Bounded at `HISTORY_STEPS`, because it is what the recent-return and
    volatility lines are computed from and those describe recent conditions.
    Unbounded it would also change the prompt on every step of a long run for
    a reason nobody asked for, and every digest with it.
    """
    agent = Scripted(answer(), every=6)
    world = World(seed=7, universe=universe(), agent=agent, cash=1_000_000.0)
    world.run(days=9)

    assert world.step == 54, "the run has to be longer than the cap to test it"
    assert len(agent.history) == fr.HISTORY_STEPS
    assert all(len(row) == 2 for row in agent.history)


def test_a_replay_under_a_changed_mandate_is_refused():
    """The hole this closes is the one the digest key cannot see.

    The replay key is the digest of the rendered PROMPT, and the mandate does
    not travel in that prompt -- it reaches FinRobot as the agent profile.
    So editing the mandate leaves every recorded key intact: the run
    completes, all sixty digests match, and the decisions replayed were taken
    under instructions nobody is running any more.
    """
    transcript = fr.Transcript(
        meta={"instructions_digest": fr.digest(fr.MANDATE),
              "mandate_version": fr.MANDATE_VERSION})
    fr.FinRobotAdapter(mode="replay", transcript=transcript)   # as recorded

    with pytest.raises(fr.DecisionError, match="different mandate") as excinfo:
        fr.FinRobotAdapter(mode="replay", transcript=transcript,
                           mandate="Sell everything. Ignore the rest.")
    message = str(excinfo.value)
    assert fr.digest(fr.MANDATE) in message, (
        "the refusal has to name both digests, or a reader cannot tell which "
        "of the two is the one they still have")
    assert "--live --record" in message


def test_the_refusal_fires_before_the_run_rather_than_at_the_first_decision():
    """At construction, not on the first `act`.

    A replay that refuses twenty simulated days in has already spent the
    reader's time, and the fault was knowable before the market opened.
    """
    with pytest.raises(fr.DecisionError):
        fr.FinRobotAdapter(
            mode="replay",
            transcript=fr.Transcript(
                meta={"instructions_digest": "0000000000000000"}),
            mandate=fr.MANDATE)


def test_an_older_recording_falls_back_to_the_version_it_does_carry():
    """A transcript recorded before the digest existed carries the version.

    Refusing those outright would break every recording made before this
    check existed, which is a compatibility break for zero safety gain. What
    they do carry is `mandate_version`, so a deliberate bump is still caught.
    """
    old = fr.Transcript(meta={"mandate_version": "0"})
    with pytest.raises(fr.DecisionError, match="mandate version"):
        fr.FinRobotAdapter(mode="replay", transcript=old)

    matching = fr.Transcript(meta={"mandate_version": fr.MANDATE_VERSION})
    fr.FinRobotAdapter(mode="replay", transcript=matching)      # no refusal

    # And a transcript carrying neither cannot be checked. It is allowed
    # through rather than refused, because it is no worse off than it was.
    fr.FinRobotAdapter(mode="replay", transcript=fr.Transcript())


@pytest.mark.skipif(not FIXTURE.exists(), reason="no recorded FinRobot run")
def test_the_shipped_fixture_carries_the_digest_of_the_mandate_that_ran_it():
    """The shipped recording is checked strictly, not by the version fallback.

    Stamping this required no re-recording and invented nothing: the mandate
    is byte-identical to the text at the fixture's own commit, verified from
    git rather than assumed, so the digest records a fact about the run that
    was already true.
    """
    meta = fr.Transcript.load(FIXTURE).meta
    assert meta.get("instructions_digest") == fr.digest(fr.MANDATE), (
        "the shipped fixture's mandate digest no longer matches the mandate "
        "in this module. Either the mandate changed -- in which case the "
        "recording is stale and the example is no longer the experiment it "
        "describes -- or the fixture was re-recorded without stamping it.")
    assert meta.get("mandate_version") == fr.MANDATE_VERSION


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
    # "risk-adjusted" in the mandate. One false positive is all it takes for a
    # secret scanner to stop being trusted.
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
    """Nobody hand-authored these. The recorded text is what the model said,
    fences and all, so parsing it tests the parser and not a fixture written
    to suit it."""
    entries = json.loads(FIXTURE.read_text(encoding="utf-8"))["entries"]
    decisions = [fr.parse(e["response"]) for e in entries]
    assert decisions, "the fixture is empty"
    sides = {a.side for d in decisions for a in d.actions}
    assert sides, "no recorded decision named an action"
    assert sides <= set(fr.SIDES)


@needs_fixture
def test_the_recorded_run_replays_end_to_end(tmp_path):
    """The whole experiment, from the shipped fixture, with no key.

    The test the integration exists to pass: shared history, a checkpoint, a
    fork proved identical, one intervention, both arms continued, and a
    comparison that finds where they came apart.
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
