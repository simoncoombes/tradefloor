"""What a wrong argument or a broken agent is told.

One test per finding of the 0.8.5 error-message review. Each pins the
refusal or warning a user now gets, and each failed on 5b56d0b, where the
same call raised a Python internal ("'int' object is not iterable"), ran
silently to a zero score, or returned ``{}`` with no reason. Three pass
there by design, because they guard what must not change: an agent that
chose not to trade is not warned about, whole-share orders are not taken
for weights, and a day closed through the session still opens the next.

``act()`` returning something that is not an order mapping, and the
message for an infinite quantity, are fixed on the fix085/agent-orders
branch with their own tests, so they are not repeated here.

Every market here is five names, one day, two steps of five ticks, under
pt-v19, whose engine builds without pt-v20's 755-day macro burn-in. Nothing
tested here depends on the preset except the capture ratio, which pt-v20
withholds, and that test asks for pt-v20.
"""

from __future__ import annotations

import warnings

import pytest

import tradefloor as tf
from tradefloor import ValidationError

U = tf.Universe.random(5, seed=1)
TICKERS = [inst.ticker for inst in U]
MODEL = "pt-v19"
SMALL = dict(seed=7, universe=U, days=1, steps_per_day=2, ticks_per_step=5,
             model=MODEL)


class Idle:
    def act(self, obs):
        return {}


def _evaluate(agents, **overrides):
    return tf.evaluate(agents, **{**SMALL, **overrides})


def _messages(record):
    return [str(w.message) for w in record]


# -- agents that are not agents ----------------------------------------------


class Mine:
    def act(self, obs):
        return {}


@pytest.mark.parametrize("entry, words", [
    (Mine, "Agent 'a' is the class Mine, not an agent. Pass Mine() instead."),
    (object(), "Agent 'a' has no act(obs) method."),
    (lambda obs: {}, "is the function"),
    (tf.StrategySpec, "is the class StrategySpec, not a spec"),
    (tf.StrategySpec.momentum,
     "is the function StrategySpec.momentum. Call it to make a spec"),
])
def test_evaluate_refuses_a_non_agent_before_any_market_runs(entry, words):
    # Each of these ran to the end on 5b56d0b and scored pnl=0.00.
    with pytest.raises(ValidationError, match=r"(?s)" + _escape(words)):
        _evaluate({"a": entry})


def test_rank_refuses_a_non_agent_from_its_factory():
    with pytest.raises(ValidationError, match="is the class Mine"):
        tf.rank(lambda: {"a": Mine}, seeds=[1, 2], **{
            k: v for k, v in SMALL.items() if k != "seed"})


def test_world_names_the_class_it_was_given():
    # On 5b56d0b: "Mine.act() missing 1 required positional argument".
    with pytest.raises(ValidationError,
                       match=r"agent= is the class Mine, not an agent"):
        tf.World(seed=7, universe=U, agent=Mine, model=MODEL)
    with pytest.raises(ValidationError, match=r"Agent 'x' has no act"):
        tf.World(seed=7, universe=U, agents={"x": object()}, model=MODEL)


def _escape(text):
    import re
    return re.escape(text)


# -- an agent that failed everywhere ------------------------------------------


class Unknown:
    def act(self, obs):
        return {"ZZZZ": 100}


def test_an_agent_that_failed_on_every_step_is_warned_about():
    with pytest.warns(UserWarning, match=r"Agent 'a' failed on all 2 of its "
                                         r"steps, so its score is empty\. "
                                         r"First error: step 0: "):
        scores = _evaluate({"a": Unknown()})
    assert "errors=2" in repr(scores["a"])


def test_an_agent_that_chose_not_to_trade_is_not_warned_about():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        scores = _evaluate({"a": Idle()})
    assert "errors" not in repr(scores["a"])


# -- weights sent as shares ---------------------------------------------------


class Weights:
    def act(self, obs):
        return {t: 0.2 for t in obs.tickers}


class Shares:
    def act(self, obs):
        return {obs.tickers[0]: 10} if obs.step == 0 else {}


def test_portfolio_weights_are_warned_about_and_still_executed():
    with pytest.warns(UserWarning, match=r"asked for fractions of a share "
                                         r"\(0\.2 of AAA, .*not portfolio "
                                         r"weights"):
        card = _evaluate({"w": Weights()})["w"]
    # Warning only: the orders traded as they always did.
    assert card.trades == 10


def test_whole_share_orders_are_not_warned_about():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        _evaluate({"s": Shares()})


# -- a day that is already open -----------------------------------------------


def _mid_day():
    engine = tf.Engine(seed=7, universe=U, model=MODEL)
    engine.open_market()
    engine.run_session(9, 30, 3, 5)
    return engine


def test_run_days_refuses_an_open_day_and_leaves_the_engine_alone():
    engine = _mid_day()
    before = engine.state_hash()
    with pytest.raises(ValidationError, match=r"the market is already open: "
                                              r"day 0 has run 5 ticks and has "
                                              r"not closed\..*close_market"):
        engine.run_days(1)
    assert engine.state_hash() == before
    # The way out it names works, and reaches the run that closed first.
    engine.close_market()
    engine.run_days(1)
    other = _mid_day()
    other.close_market()
    other.run_days(1)
    assert engine.state_hash() == other.state_hash()


def test_open_market_twice_is_refused():
    # On 5b56d0b the second call reopened the day and moved the state hash.
    engine = tf.Engine(seed=7, universe=U, model=MODEL)
    engine.open_market()
    before = engine.state_hash()
    with pytest.raises(ValidationError, match=r"day 0 is open and has not "
                                              r"closed\. Call close_market\(\)"):
        engine.open_market()
    assert engine.state_hash() == before


def test_a_day_closed_through_the_session_opens_the_next_as_before():
    # run_session(close_at_end=True) closes the day and leaves the session
    # flag set, and open_market() after it is how a hand loop reaches the
    # next day. That must not be taken for a day still open.
    engine = tf.Engine(seed=7, universe=U, model=MODEL)
    for _ in range(2):
        engine.open_market()
        engine.run_session(9, 30, 3, 5, close_at_end=True)
    engine.run_days(1, ticks_per_day=5)
    ops = [entry["op"] for entry in engine.order_log]
    assert ops.count("open_market") == 3  # two by hand, one by run_days


def test_world_fork_mid_day_says_how_to_close_the_day():
    world = tf.World(seed=7, universe=U, agent=Idle(), model=MODEL)
    world.engine.open_market()
    with pytest.raises(ValidationError,
                       match=r"world\.engine\.close_market\(\)"):
        world.fork("x")


# -- scenarios ------------------------------------------------------------------


def test_from_yaml_on_a_missing_file_says_the_file_is_missing(tmp_path):
    with pytest.raises(FileNotFoundError,
                       match=r"No file at 'no/such/file\.yml' \(looked in "):
        tf.Scenario.from_yaml("no/such/file.yml")
    # A real file and YAML text still read as before.
    import importlib.resources as resources
    shipped = resources.files("tradefloor") / "scenarios" / "liquidity_crisis.yml"
    path = tmp_path / "s.yml"
    path.write_text(shipped.read_text(encoding="utf-8"), encoding="utf-8")
    expected = tf.Scenario.load("liquidity_crisis").fingerprint
    assert tf.Scenario.from_yaml(str(path)).fingerprint == expected
    assert tf.Scenario.from_yaml(path.read_text()).fingerprint == expected


def test_a_scenario_name_is_refused_with_the_call_that_loads_it():
    with pytest.raises(ValidationError, match=r"got the string "
                                              r"'liquidity_crisis'\. Load it "
                                              r"first: tf\.Scenario\.load"):
        _evaluate({"a": Idle()}, scenario="liquidity_crisis")
    with pytest.raises(ValidationError, match="Load it first"):
        tf.run_scenario("liquidity_crisis", seed=7, universe=U, days=1,
                        model=MODEL)
    with pytest.raises(ValidationError, match=r"got the class Scenario"):
        _evaluate({"a": Idle()}, scenario=tf.Scenario)


# -- universes ------------------------------------------------------------------


@pytest.mark.parametrize("universe, words", [
    (None, "Got None."),
    (40, "Got the number 40. For 40 random companies use "
         "tf.Universe.random(40, seed=1)."),
    (TICKERS, "Got a list of ticker strings"),
    (["AAPL", "MSFT"], "Universe.from_edgar builds instruments"),
])
def test_a_wrong_universe_is_named_by_evaluate_and_engine(universe, words):
    head = "universe must be a list of instruments"
    with pytest.raises(ValidationError, match=_escape(head) + ".*"
                       + _escape(words)):
        _evaluate({"a": Idle()}, universe=universe)
    with pytest.raises(ValidationError, match=_escape(head) + ".*"
                       + _escape(words)):
        tf.Engine(seed=7, universe=universe, model=MODEL)


# -- other wrong arguments ---------------------------------------------------------


@pytest.mark.parametrize("overrides, words", [
    (dict(days=2.5), "days must be a whole number of 1 or more, got 2.5."),
    (dict(days="5"), "days must be a whole number of 1 or more, got the "
                     "string '5'."),
    (dict(days=0), "days must be 1 or more, got 0."),
    (dict(ticks_per_step=0), "ticks_per_step must be 1 or more, got 0."),
    (dict(cash="1e6"), "cash must be a number, got the string '1e6'."),
    (dict(start=(9, 30)), "start is (hour, minute, day_of_week), such as "
                          "(9, 30, 3); got (9, 30)."),
    (dict(macro={"vix": 30}), "macro must be a tf.Macro, got a dict. Use "
                              "tf.Macro(vix=30)."),
])
def test_evaluate_names_the_wrong_argument(overrides, words):
    with pytest.raises(ValidationError, match=_escape(words)):
        _evaluate({"a": Idle()}, **overrides)


def test_agents_not_in_a_dict_are_refused_with_the_shape_wanted():
    for agents in (Idle(), [Idle()]):
        with pytest.raises(ValidationError, match=_escape(
                "agents must be a dict of name to agent, such as "
                "{'mine': MyAgent()}")):
            tf.evaluate(agents, **SMALL)


def test_rank_names_a_seed_count_passed_as_seeds():
    with pytest.raises(ValidationError, match=_escape(
            "seeds must be a list of seeds, such as range(12); got the "
            "number 5. For 5 seeds use range(5).")):
        tf.rank(lambda: {"a": Idle()}, seeds=5, universe=U, days=1,
                model=MODEL)


def test_engine_positional_arguments_show_the_keyword_call():
    with pytest.raises(TypeError, match=_escape(
            "Engine takes keyword arguments: Engine(seed=7, "
            "universe=universe).")):
        tf.Engine(7, U)
    # Left out is a TypeError that shows the call, as it was when both
    # were required; None is a wrong value and names it.
    with pytest.raises(TypeError, match="Engine needs a seed"):
        tf.Engine(universe=U)
    with pytest.raises(TypeError, match="Engine needs a universe"):
        tf.Engine(seed=7)
    with pytest.raises(ValidationError, match=r"0 to 2\*\*64 - 1"):
        tf.Engine(seed=None, universe=U)
    # The signature a reader sees is the keyword-only one.
    assert tf.Engine.__text_signature__ == \
        "(*, seed, universe, macro_state=None, model=None, population=None)"


def test_negative_counts_are_refused_in_words():
    # On 5b56d0b both were "OverflowError: can't convert negative int to
    # unsigned".
    with pytest.raises(ValidationError, match=_escape(
            "run_days: days must be 1 or more, got -1")):
        tf.Engine(seed=7, universe=U, model=MODEL).run_days(-1)
    with pytest.raises(ValidationError, match=_escape(
            "Universe.random needs a number of companies of 1 or more, "
            "got -3")):
        tf.Universe.random(-3)
    with pytest.raises(ValidationError, match=_escape(
            "Universe.random makes at most 17,576 companies")):
        tf.Universe.random(10**9)


# -- tickers ------------------------------------------------------------------------


class Asks:
    def __init__(self, ticker):
        self.ticker = ticker

    def act(self, obs):
        obs.price(self.ticker)
        return {}


def test_obs_price_names_an_unknown_ticker_and_the_one_meant():
    with pytest.warns(UserWarning, match="failed on all 2"):
        card = _evaluate({"a": Asks("aaa")})["a"]
    assert card.errors[0] == (
        "step 0: ValidationError: No ticker 'aaa' in this market. Did you "
        "mean 'AAA'? obs.tickers lists all 5.")
    with pytest.warns(UserWarning, match="failed on all 2"):
        card = _evaluate({"a": Asks("ZZZZ")})["a"]
    assert card.errors[0] == (
        "step 0: ValidationError: No ticker 'ZZZZ' in this market. "
        "obs.tickers lists all 5.")


def test_duplicate_tickers_are_refused():
    roster = list(U)
    with pytest.raises(ValidationError, match=_escape(
            'ticker "AAA" appears twice in the universe (positions 0 and 5)')):
        tf.Engine(seed=7, universe=roster + [roster[0]], model=MODEL)
    engine = tf.Engine(seed=7, universe=roster, model=MODEL)
    with pytest.raises(ValidationError, match=r'ticker "AAB" is already '
                                              r'listed, at position 1'):
        engine.list_instrument(roster[1])


# -- specs, fills and comparisons -------------------------------------------------


def test_top_k_beyond_the_universe_is_warned_about():
    with pytest.warns(UserWarning, match=_escape(
            "top_k=50 asks for 50 names on each side, but a 5-name universe "
            "has room for 2, so the run used 2.")):
        _evaluate({"m": tf.StrategySpec.momentum(lookback_days=1.0,
                                                  top_k=50)})
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        _evaluate({"m": tf.StrategySpec.momentum(lookback_days=1.0,
                                                  top_k=2)})


class Big:
    def act(self, obs):
        return {obs.tickers[0]: 1e12} if obs.step == 0 else {}


def test_a_partial_fill_is_on_the_card():
    card = _evaluate({"b": Big()}, max_leverage=None, cash=1e15)["b"]
    assert len(card.partial_fills) == 1
    line = card.partial_fills[0]
    assert line.startswith("step 0: asked to buy 1,000,000,000,000 AAA; the "
                           "book held ")
    assert line.endswith(", and the rest did not fill.")
    assert "partial_fills=1" in repr(card)
    # Not an error: ranking counts errors as raises.
    assert card.errors == []


def test_empty_comparisons_come_with_a_reason():
    scores = _evaluate({"a": Idle()})
    with pytest.warns(UserWarning, match=r"No 'buy_and_hold' in these "
                                         r"scores"):
        assert tf.versus_buy_and_hold(scores) == {}
    scores = _evaluate({"a": Idle(), "buy_and_hold": Idle()})
    with pytest.warns(UserWarning, match=r"Did you mean "
                                         r"reference='buy_and_hold'\?"):
        assert tf.versus_buy_and_hold(scores, reference="buy-and-hold") == {}
    # pt-v20, the default, withholds the capture ratio.
    scores = _evaluate({"a": Idle()}, model=None)
    with pytest.warns(UserWarning, match=r"No capture ratio on pt-v20.*"
                                         r"tf\.versus_buy_and_hold"):
        assert tf.capture_ratio(scores) == {}


# -- macro ---------------------------------------------------------------------


def test_a_negative_vix_is_refused():
    with pytest.raises(ValidationError, match=_escape(
            "vix cannot be negative, got -5")):
        tf.Engine(seed=7, universe=U, model=MODEL).pin_macro(vix=-5)
    with pytest.raises(ValidationError, match="vix cannot be negative"):
        tf.Macro(vix=-5)


# -- extras --------------------------------------------------------------------


def test_gym_names_the_rl_extra(monkeypatch):
    import tradefloor.gym as gym

    monkeypatch.setattr(gym, "_np", None)
    with pytest.raises(ImportError, match=_escape(
            "pip install 'tradefloor[rl]'")):
        gym.TradingEnv(seed=7, universe=U, model=MODEL)


def test_gym_without_gymnasium_names_the_extra_for_its_spaces(monkeypatch):
    import tradefloor.gym as gym

    monkeypatch.setattr(gym, "_gym", None)
    env = gym.TradingEnv(seed=7, universe=U, model=MODEL)
    with pytest.raises(AttributeError, match=r"no action_space without "
                                             r"gymnasium.*tradefloor\[rl\]"):
        env.action_space
    with pytest.raises(AttributeError, match="no attribute 'nothing'"):
        env.nothing


def test_arrow_stream_repr_says_how_to_read_it():
    engine = tf.Engine(seed=7, universe=U, model=MODEL)
    engine.run_days(1, ticks_per_day=5, record=True)
    shown = repr(engine.bars())
    assert "pyarrow.table(stream)" in shown
    assert "polars.from_arrow(stream)" in shown


# -- checkpoints -------------------------------------------------------------------


def test_checkpoint_verify_catches_the_wrong_seed():
    engine = tf.Engine(seed=7, universe=U, model=MODEL)
    engine.run_days(1, ticks_per_day=5)
    with pytest.raises(ValidationError, match=r"replaying its log under seed "
                                              r"8 gives a different market"):
        tf.Checkpoint.of(engine, universe=U, seed=8, verify=True)
    right = tf.Checkpoint.of(engine, universe=U, seed=7, verify=True)
    assert right.resume().state_hash() == engine.state_hash()


# -- nits ---------------------------------------------------------------------------


def test_small_wording_gaps():
    scores = _evaluate({"a": Idle()})
    with pytest.raises(ValidationError, match=_escape(
            "leaderboard ranks by pnl, return_pct, impact_bps or turnover; "
            "got 'return'.")):
        tf.leaderboard(scores, by="return")
    with pytest.raises(ValidationError, match=_escape(
            'Did you mean "pt-v20"? Preset names are lower case.')):
        tf.Engine(seed=7, universe=U, model="PT-V20")
    with pytest.raises(ValidationError, match=_escape(
            'Did you mean "technology"? Sector names are lower case.')):
        tf.Instrument("XYZ", "Technology", initial_price=10.0,
                      shares_outstanding=1e6)


def test_counts_name_the_argument_in_tca_and_gym():
    # On 5b56d0b both said "days, steps_per_day and ticks_per_step must be
    # >= 1" without saying which one or what came.
    from tradefloor import tca
    with pytest.raises(ValidationError, match=_escape(
            "steps_per_day must be 1 or more, got 0.")):
        tca.analyse(Idle(), seed=7, universe=U, steps_per_day=0, model=MODEL)
    import tradefloor.gym as gym
    with pytest.raises(ValidationError, match=_escape(
            "ticks_per_step must be 1 or more, got 0.")):
        gym.TradingEnv(seed=7, universe=U, ticks_per_step=0, model=MODEL)
