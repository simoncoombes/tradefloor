"""What an agent is handed, and what happens when it reaches past it.

An independent audit of 0.8.5 found that every harness handed agents the live
engine as ``obs.engine``. Two agents it wrote made the point: one forked the
engine and traded on the fork's next step (+11.4% in five days, four seeds of
four), and one rewrote the fundamentals of a name it held (+184%). Neither
scorecard showed an error or a flag. These tests reproduce both, and hold the
harness to refusing them by default and flagging them under the opt-in.
"""

from __future__ import annotations

import copy
import gc
import pickle
import struct

import pytest

import tradefloor as tf
from tradefloor.sandbox import (PUBLIC_COLUMNS, HiddenState, MarketView,
                                PortfolioView, SandboxError, TamperGuard)

U = tf.Universe.random(8, seed=111)


def _f64(buf):
    return list(struct.unpack("<%dd" % (len(buf) // 8), buf))


# -- the audit's two agents -----------------------------------------------


class Peek:
    """Forks the engine, runs the fork to the end of the step, buys what
    rises. Look-ahead."""

    @staticmethod
    def reach(obs):
        return obs.engine

    def act(self, obs):
        fork = self.reach(obs).fork(1)[0]
        k = obs.step_of_day
        hh, mm = divmod(9 * 60 + 30 + k * 65, 60)
        fork.run_session(hh, mm, 3, 65)
        later = _f64(fork.prices())
        best = max(range(len(later)), key=lambda i: later[i] / obs.prices[i])
        t = obs.tickers[best]
        want = 0.9 * obs.portfolio.net_worth(obs.engine) / obs.prices[best]
        orders = {x: -obs.position(x) for x in obs.tickers
                  if x != t and obs.position(x)}
        cap = 0.02 * obs.avg_volume(t)
        delta = max(-cap, min(cap, want - obs.position(t)))
        if abs(delta) >= 1:
            orders[t] = float(round(delta))
        return orders


class Mutate:
    """Buys one name, then multiplies its earnings tenfold."""

    def act(self, obs):
        if obs.step == 0:
            return {obs.tickers[0]: float(round(
                0.9 * obs.portfolio.net_worth(obs.engine) / obs.prices[0]))}
        if obs.step == 1:
            eps, bv, g = obs.engine.fundamentals()
            eps = list(eps)
            eps[0] = eps[0] * 10 if eps[0] > 0 else 1.0
            obs.engine.set_fundamentals(eps, list(bv), list(g))
        return {}


class Answer:
    """Reads the mispricing column: the answer key."""

    def act(self, obs):
        s = _f64(obs.engine.column("mispricing_s"))
        return {obs.tickers[min(range(len(s)), key=s.__getitem__)]: 10.0}


def _run(agents, **kw):
    return tf.evaluate(agents, seed=3, universe=U, days=2, **kw)


def test_look_ahead_through_fork_is_refused_by_default():
    card = _run({"peek": Peek()})["peek"]
    assert card.trades == 0 and card.pnl == 0.0
    assert card.errors and all("SandboxError" in e and "'fork'" in e
                               for e in card.errors)
    assert not card.trusted and not card.tampered


def test_look_ahead_under_the_opt_in_is_marked_trusted():
    cards = _run({"peek": Peek(), "hold": tf.StrategySpec.hold()},
                 trusted_agents=True)
    peek = cards["peek"]
    # The exploit works when the engine is handed over, which is what the
    # flag is for: the card cannot pass as a peer of a sandboxed one.
    assert peek.trades > 0 and peek.pnl > cards["hold"].pnl
    assert peek.trusted and cards["hold"].trusted
    assert "trusted" in repr(peek)


def test_rewriting_the_market_is_refused_by_default():
    card = _run({"mutate": Mutate()})["mutate"]
    assert not card.tampered
    assert any("SandboxError" in e and "'fundamentals'" in e
               for e in card.errors)
    assert card.return_pct < 50.0


def test_rewriting_the_market_under_the_opt_in_is_flagged_tampered():
    card = _run({"mutate": Mutate()}, trusted_agents=True)["mutate"]
    assert card.tampered
    assert any(e.startswith("step 1: tampered:") and "fundamentals" in e
               for e in card.errors)
    assert "TAMPERED" in repr(card)


def test_a_write_reached_around_the_view_is_still_flagged():
    """The view is a guard against accident, not a wall. The hash check is
    what catches a write however the agent reached the engine, here through
    the module's own table."""

    class Around:
        def act(self, obs):
            if obs.step == 2:
                engine = tf.sandbox._WRAPPED[obs.engine]
                engine.pin_macro(federal_funds_rate=0.2)
            return {}

    card = _run({"around": Around()})["around"]
    assert card.tampered and not card.trusted
    assert any("step 2: tampered:" in e and "state hash" in e
               for e in card.errors)


# -- no attribute of a view leads to what it wraps ----------------------------
#
# Until the last pre-release of 0.8.5 each view kept what it wrapped in a
# name-mangled slot. `dir(obs.engine)` listed `_MarketView__engine`, and one
# attribute access handed an agent the live engine. Two reviewers used it:
# one forked the engine at each step and traded on the fork (+4.2% to +8.2%
# in five days against +0.2% to +2.3% for buy-and-hold, four seeds of four),
# and one read `mispricing_s`. Neither card carried a flag, because a read
# changes no state and the tamper check compares state.


class ThroughTheSlot(Peek):
    """The first reviewer's agent: Peek, reaching the engine through the
    view's private slot."""

    @staticmethod
    def reach(obs):
        return obs.engine._MarketView__engine


class AnswerThroughTheSlot:
    """The second reviewer's read: the mispricing column, through the slot."""

    def act(self, obs):
        s = _f64(obs.engine._MarketView__engine.column("mispricing_s"))
        return {obs.tickers[min(range(len(s)), key=s.__getitem__)]: 10.0}


def test_look_ahead_through_the_private_slot_is_refused():
    card = _run({"slot": ThroughTheSlot()})["slot"]
    assert card.trades == 0 and card.pnl == 0.0
    assert card.errors and all("SandboxError" in e
                               and "_MarketView__engine" in e
                               for e in card.errors)
    assert not card.trusted and not card.tampered


def test_a_hidden_column_through_the_private_slot_is_refused():
    card = _run({"slot": AnswerThroughTheSlot()})["slot"]
    assert card.trades == 0
    assert card.errors and all("SandboxError" in e for e in card.errors)
    assert not card.uses_hidden_state


def _wrapped():
    engine = tf.Engine(seed=1, universe=U)
    engine.open_market()
    engine.run_session(9, 30, 3, 65)
    portfolio = tf.Portfolio(cash=1_000_000.0)
    views = {"market": MarketView(engine), "hidden": HiddenState(engine),
             "portfolio": PortfolioView(portfolio, engine)}
    return engine, portfolio, views


#: The slots each view used to have, spelled as an agent would reach them.
OLD_SLOTS = ("_MarketView__engine", "_HiddenState__raw",
             "_PortfolioView__portfolio", "_PortfolioView__engine")


@pytest.mark.parametrize("kind", ["market", "hidden", "portfolio"])
def test_no_attribute_of_a_view_leads_to_the_live_objects(kind):
    engine, portfolio, views = _wrapped()
    view = views[kind]
    live = (engine, portfolio)
    for name in dir(view):
        try:
            value = getattr(view, name)
        except Exception:
            continue
        assert not any(value is x for x in live), name
    for name in OLD_SLOTS:
        with pytest.raises(SandboxError):
            getattr(view, name)
        # No slot at all, so going under `__getattr__` finds nothing either.
        with pytest.raises(AttributeError):
            object.__getattribute__(view, name)
    assert not any(ref is x for ref in gc.get_referents(view) for x in live)
    # The view still reads the live objects, through the module's table.
    if kind == "portfolio":
        assert view.net_worth() == portfolio.net_worth(engine)
    else:
        assert view.prices() == engine.prices()


@pytest.mark.parametrize("kind", ["market", "hidden", "portfolio"])
def test_a_view_refuses_to_be_copied_or_pickled(kind):
    _, _, views = _wrapped()
    for how in (copy.copy, copy.deepcopy, pickle.dumps):
        with pytest.raises(SandboxError, match="cannot be copied or pickled"):
            how(views[kind])


def test_forking_a_world_whose_agent_keeps_the_view_says_what_to_do():
    class Keeps:
        def act(self, obs):
            self.view = obs.engine
            return {}

    world = tf.World(seed=2, universe=U, agent=Keeps())
    world.run(1)
    with pytest.raises(SandboxError, match=r"fork\(\) method"):
        world.fork("arm")


def test_the_portfolio_cannot_be_written_or_traded_directly():
    seen = {}

    class Rich:
        def act(self, obs):
            for name, attempt in (
                    ("cash", lambda: setattr(obs.portfolio, "cash", 1e12)),
                    ("execute", lambda: obs.portfolio.execute(
                        obs.engine, obs.tickers[0], 10.0))):
                try:
                    attempt()
                except SandboxError:
                    seen[name] = "refused"
            # A copy: editing it edits nothing the harness holds.
            obs.portfolio.positions.clear()
            obs.tickers.reverse()
            return {}

    card = _run({"rich": Rich()})["rich"]
    assert seen == {"cash": "refused", "execute": "refused"}
    assert not card.tampered and card.final_net_worth == 1_000_000.0


def test_a_portfolio_write_under_the_opt_in_is_flagged():
    class Rich:
        def act(self, obs):
            if obs.step == 0:
                obs.portfolio.cash += 1e9
            return {}

    card = _run({"rich": Rich()}, trusted_agents=True)["rich"]
    assert card.tampered
    assert any("portfolio changed" in e for e in card.errors)


def test_hidden_state_is_a_declared_capability():
    card = _run({"answer": Answer()})["answer"]
    assert any("mispricing_s" in e and "SandboxError" in e
               for e in card.errors)
    assert not card.uses_hidden_state

    class Declared(Answer):
        privileged = True

        def act(self, obs):
            assert isinstance(obs.hidden, HiddenState)
            s = _f64(obs.hidden.column("mispricing_s"))
            return {obs.tickers[min(range(len(s)), key=s.__getitem__)]: 10.0}

    card = _run({"declared": Declared()})["declared"]
    assert card.errors == [] and card.trades > 0
    assert card.uses_hidden_state and not card.trusted
    assert "hidden-state" in repr(card)


def test_the_oracle_reads_hidden_state_through_the_capability():
    cards = _run(tf.reference_agents(seed=1))
    assert cards["oracle"].uses_hidden_state
    assert cards["oracle"].errors == []
    assert cards["oracle"].explanation_accuracy == 1.0
    for name in ("buy_and_hold", "random", "momentum", "mean_reversion"):
        assert not cards[name].uses_hidden_state
        assert cards[name].errors == []


def test_every_reference_agent_scores_the_same_sandboxed_or_trusted():
    """The views read what the agents read before, so nothing moves."""
    fields = ("pnl", "trades", "turnover", "impact_bps", "max_leverage",
              "rejected", "explanations", "final_net_worth", "errors")
    for model in ("pt-v19", "pt-v20"):
        a = tf.evaluate(tf.reference_agents(seed=4), seed=5, universe=U,
                        days=2, model=model)
        b = tf.evaluate(tf.reference_agents(seed=4), seed=5, universe=U,
                        days=2, model=model, trusted_agents=True)
        for name in a:
            for f in fields:
                assert getattr(a[name], f) == getattr(b[name], f), (model,
                                                                    name, f)


def test_the_market_view_serves_what_a_trader_sees():
    engine = tf.Engine(seed=1, universe=U)
    engine.open_market()
    engine.run_session(9, 30, 3, 65)
    view = MarketView(engine)
    assert view.prices() == engine.prices()
    assert view.tickers == engine.tickers and len(view) == len(engine)
    for field in PUBLIC_COLUMNS:
        assert view.column(field) == engine.column(field)
    assert view.book(U[0].ticker).best_bid == engine.book(U[0].ticker).best_bid
    assert "qe_pe_boost" not in view.macro_fields
    assert (view.macro_state.federal_funds_rate
            == engine.macro_state.federal_funds_rate)
    for item in view.news():
        assert set(item) == {"ticker", "sector", "day"}
    assert view.model_fingerprint == engine.model_fingerprint


class History:
    """Asks for a daily history at the third day's open, as a reviewer of
    the pre-release did, at every grain."""

    ASKS = {"day": {"grain": "day"}, "minutes": {"minutes": 5},
            "tick": {}, "one day": {"day": 1, "grain": "day"}}

    def __init__(self):
        self.got = {}
        self.recorded = None

    def act(self, obs):
        if obs.day == 2 and obs.step_of_day == 0:
            import pyarrow as pa

            for label, kwargs in self.ASKS.items():
                try:
                    self.got[label] = pa.table(
                        obs.engine.bars(**kwargs)).to_pydict()
                except SandboxError as exc:
                    self.got[label] = str(exc)
            try:
                self.recorded = obs.engine.recorded_days
            except SandboxError:
                self.recorded = "not served"
        return {}


def test_bars_refuses_when_the_harness_records_nothing():
    """`evaluate` never records, and the engine's fallback was the last
    step's prints labelled day 0: on day 2, one bar per name labelled day 0
    covering the last 65 minutes of day 1, and no warning."""
    pytest.importorskip("pyarrow")
    agent = History()
    card = tf.evaluate({"h": agent}, seed=3, universe=U, days=3)["h"]
    assert card.errors == []
    assert set(agent.got) == set(History.ASKS)
    for label, got in agent.got.items():
        assert isinstance(got, str), (label, got)
        assert "never record" in got and "column('open')" in got
    assert agent.recorded == 0


def test_bars_serves_the_recorded_days_unchanged():
    pa = pytest.importorskip("pyarrow")
    agent = History()
    world = tf.World(seed=3, universe=U, agents={"h": agent})
    world.run(3, record=True)
    assert agent.recorded == 2
    engine = world.engine
    for label, kwargs in History.ASKS.items():
        if "day" in kwargs:
            want = pa.table(engine.bars(**kwargs))
        else:
            # Asked at day 2's open, so days 0 and 1 of the three.
            want = pa.concat_tables([pa.table(engine.bars(day=d, **kwargs))
                                     for d in (0, 1)])
        assert agent.got[label] == want.to_pydict(), label
    assert sorted(set(agent.got["day"]["day"])) == [0, 1]


def test_the_market_view_bars_is_the_engine_bars_once_recorded():
    pa = pytest.importorskip("pyarrow")
    engine = tf.Engine(seed=1, universe=U)
    engine.open_market()
    engine.run_session(9, 30, 3, 65)
    view = MarketView(engine)
    assert view.recorded_days == 0
    with pytest.raises(SandboxError, match="needs recorded days"):
        view.bars(grain="day")
    engine.record(0)
    assert view.recorded_days == 1
    for kwargs in ({}, {"grain": "day"}, {"minutes": 5}, {"day": 0}):
        assert pa.table(view.bars(**kwargs)).equals(
            pa.table(engine.bars(**kwargs))), kwargs


@pytest.mark.parametrize("name", [
    "fork", "set_fundamentals", "fundamentals", "set_avg_volume", "pin_macro",
    "tick", "run_session", "run_days", "open_market", "close_market", "truth",
    "prints", "attribution", "state_snapshot", "restore_state", "state_hash",
    "submit", "cancel", "patch_draws", "draw_uniform", "model_params",
    "session_news", "explain", "order_log",
])
def test_the_market_view_refuses_the_rest(name):
    view = MarketView(tf.Engine(seed=1, universe=U))
    assert not hasattr(view, name)
    with pytest.raises(SandboxError, match="trusted_agents=True"):
        getattr(view, name)


@pytest.mark.parametrize("field", ["mispricing_s", "mispricing_momentum",
                                   "mispricing_s_prev_close",
                                   "maker_inventory", "garch_variance"])
def test_the_market_view_refuses_hidden_columns(field):
    view = MarketView(tf.Engine(seed=1, universe=U))
    with pytest.raises(SandboxError):
        view.column(field)


def test_hidden_state_reads_and_never_writes():
    engine = tf.Engine(seed=1, universe=U)
    hidden = HiddenState(engine)
    assert hidden.column("mispricing_s") == engine.column("mispricing_s")
    assert hidden.model_params == dict(engine.model_params)
    assert hidden.economy() == engine.state_snapshot()["economy"]
    for name in ("fork", "set_fundamentals", "pin_macro", "state_snapshot",
                 "restore_state", "run_session", "tick", "submit"):
        assert not hasattr(hidden, name)
    with pytest.raises(SandboxError):
        hidden.anything = 1


def test_the_guard_reads_without_moving_the_market():
    engine = tf.Engine(seed=9, universe=U)
    untouched = tf.Engine(seed=9, universe=U)
    guard = TamperGuard(engine, ())
    for e in (engine, untouched):
        e.open_market()
    for step in range(3):
        with guard:
            pass
        assert not guard.tampered
        for e in (engine, untouched):
            e.run_session(*tf.harness.session_clock((9, 30, 3), step, 65), 65)
    assert engine.state_hash() == untouched.state_hash()
    assert engine.prices() == untouched.prices()


# -- the other harnesses ----------------------------------------------------


def test_world_sandboxes_and_records_tampering():
    seen = {}

    class Look:
        def act(self, obs):
            seen["engine"] = type(obs.engine).__name__
            seen["portfolio"] = type(obs.portfolio).__name__
            return {}

    # A World does not catch an agent's exception (it never has), so the
    # refused write ends the run, loudly, before anything reached the market.
    world = tf.World(seed=2, universe=U, agents={"look": Look(),
                                                 "mutate": Mutate()})
    with pytest.raises(SandboxError, match="'fundamentals'"):
        world.run(1)
    assert seen == {"engine": "MarketView", "portfolio": "PortfolioView"}
    assert world.tampered == {}

    honest = tf.World(seed=2, universe=U, agents={"look": Look()})
    honest.run(1)
    assert "tampered" not in honest.summary(agent="look")
    assert honest.manifest().agent_access is None

    trusted = tf.World(seed=2, universe=U, agents={"look": Look(),
                                                   "mutate": Mutate()},
                       trusted_agents=True)
    trusted.run(1)
    assert seen["engine"] == "Engine"
    assert list(trusted.tampered) == ["mutate"]
    summary = trusted.summary(agent="mutate")
    assert summary["trusted_agents"] and summary["tampered"]
    assert "tampered" not in trusted.summary(agent="look")
    access = trusted.manifest().agent_access
    assert access["trusted_agents"] is True
    assert list(access["tampered"]) == ["mutate"]
    arm, = trusted.fork("arm")
    assert arm.trusted_agents and arm.tampered == trusted.tampered


def test_world_market_is_the_same_sandboxed_or_trusted():
    a = tf.World(seed=4, universe=U, agent=tf.baselines.MeanReversion())
    b = tf.World(seed=4, universe=U, agent=tf.baselines.MeanReversion(),
                 trusted_agents=True)
    a.run(2)
    b.run(2)
    assert a.digest() == b.digest()
    assert a.summary()["pnl"] == b.summary()["pnl"]


def test_world_records_a_privileged_agent():
    world = tf.World(seed=4, universe=U, agent=tf.baselines.Oracle())
    world.run(1)
    assert world.summary()["uses_hidden_state"] is True
    assert world.manifest().agent_access == {"hidden_state": [""]}


def test_tca_refuses_a_tampering_agent():
    with pytest.raises(tf.ValidationError, match="changed the market"):
        tf.tca.analyse(Mutate(), seed=3, universe=U, days=1,
                       trusted_agents=True)
    # Sandboxed, the write is refused inside act and the analysis runs.
    tf.tca.analyse(tf.baselines.MeanReversion(), seed=3, universe=U, days=1)


def test_rank_excludes_a_tampering_agent_and_marks_trusted_rows():
    def entrants():
        agents = tf.reference_agents(seed=1)
        agents["mutate"] = Mutate()
        return agents

    ranking = tf.rank(entrants, seeds=[1, 2], universe=U, days=2,
                      trusted_agents=True)
    assert "mutate" not in ranking.records
    assert ranking.tampered == {"mutate": [1, 2]}
    report = ranking.report()
    assert "EXCLUDED mutate" in report
    assert "[trusted: live engine]" in report
    assert ranking.as_dict()["tampered"] == {"mutate": [1, 2]}

    plain = tf.rank(lambda: tf.reference_agents(seed=1), seeds=[1, 2],
                    universe=U, days=2)
    assert plain.tampered == {}
    assert "tampered" not in plain.as_dict()
    assert "[trusted" not in plain.report()


def test_the_integrations_payload_is_unchanged_by_the_view():
    from tradefloor.integrations.common import serialize_observation

    payloads = {}

    class Capture:
        def __init__(self, key):
            self.key = key

        def act(self, obs):
            payloads.setdefault(self.key, []).append(
                serialize_observation(obs))
            return {}

    tf.evaluate({"x": Capture("sandboxed")}, seed=6, universe=U, days=1)
    tf.evaluate({"x": Capture("trusted")}, seed=6, universe=U, days=1,
                trusted_agents=True)
    assert payloads["sandboxed"] == payloads["trusted"]
    assert len(payloads["sandboxed"]) == 6


# -- every route, against the probe of pt-v20 --------------------------------
#
# A probe on pt-v20 read three things through the live engine that the
# published business cycle is meant to hold back: the economy block of
# `state_snapshot()` (the true phase, the months in it, the phase's GDP
# target, the recession probability, the earnings cycle), the engine's
# `earnings_anticipation`, which jumps on the close of every true turn, and
# the fundamental, as log(price) less `mispricing_s`. Each route below is
# held to refusing all three, and the rest of the hidden state, while
# serving what a trader sees.

HIDDEN_ECONOMY = {"cycle_phase", "months_in_current_phase",
                  "phase_gdp_target", "recession_probability",
                  "earnings_cycle", "earnings_anticipation"}

#: Reads that must be refused, as (label, how).
REFUSED = (
    ("economy", lambda e: e.state_snapshot()["economy"]),
    ("anticipation", lambda e: e.earnings_anticipation),
    ("mispricing", lambda e: e.column("mispricing_s")),
    ("fundamentals", lambda e: e.fundamentals()),
    ("macro table", lambda e: e.macro_table()),
    ("model params", lambda e: e.model_params),
    ("fork", lambda e: e.fork(1)),
    ("write", lambda e: e.pin_macro(vix=80.0)),
)


def _allowed(engine, portfolio, *, view=True):
    """What a trader can read, all of which the view must serve."""
    ticker = engine.tickers[0]
    macro = engine.macro_fields
    assert not set(macro) & HIDDEN_ECONOMY
    if view:
        assert set(macro) <= tf.sandbox.PUBLISHED_MACRO
    assert {"cycle", "gdp_growth", "vix"} <= set(macro)
    return {
        "prices": engine.prices(),
        "volume": engine.column("volume"),
        "bid": engine.book(ticker).best_bid,
        "cycle": engine.macro_state.cycle,
        "macro": {k: macro[k] for k in tf.sandbox.PUBLISHED_MACRO
                  if k in macro},
        "curve": dict(engine.curve),
        "rates": engine.rate_instruments,
        "net_worth": portfolio.net_worth(engine),
        "cash": portfolio.cash,
    }


class Probe:
    """Tries every hidden read once, catching the refusal, then reads what
    a trader can. Never trades. ``live``: handed the live engine, so it
    reads and does not fork or write, which would change the market."""

    def __init__(self, live=False):
        self.live = live
        self.refused: dict[str, str] = {}
        self.read: dict[str, object] = {}
        self.allowed = None

    def act(self, obs):
        if self.allowed is None:
            for label, how in REFUSED:
                if self.live and label in ("fork", "write"):
                    continue
                try:
                    self.read[label] = how(obs.engine)
                except SandboxError as exc:
                    self.refused[label] = str(exc)
                except AttributeError:
                    # The live engine of a build without the dial:
                    # `earnings_anticipation` arrives with pt-v20's fixes.
                    self.read[label] = None
            self.allowed = _allowed(obs.engine, obs.portfolio,
                                    view=not self.live)
        return {}


def _held_to_the_view(probe):
    assert set(probe.refused) == {label for label, _ in REFUSED}, probe.read
    assert "true business-cycle phase" in probe.refused["economy"]
    assert "jumps" in probe.refused["anticipation"]
    assert "fundamental" in probe.refused["mispricing"]
    assert probe.allowed["prices"] and probe.allowed["net_worth"] > 0


def test_route_evaluate_refuses_the_probe():
    probe = Probe()
    card = tf.evaluate({"probe": probe}, seed=3, universe=U, days=1)["probe"]
    assert not card.errors
    _held_to_the_view(probe)
    # The opt-in hands the live engine back, and says so.
    trusted = Probe(live=True)
    card = tf.evaluate({"probe": trusted}, seed=3, universe=U, days=1,
                       trusted_agents=True)["probe"]
    assert card.trusted and not card.tampered and not card.errors
    economy = trusted.read["economy"]
    assert {"cycle_phase", "recession_probability"} <= set(economy)
    assert trusted.read["mispricing"]
    assert trusted.allowed == probe.allowed


def test_route_world_refuses_the_probe():
    probe = Probe()
    world = tf.World(seed=3, universe=U, agents={"probe": probe})
    world.run(1)
    _held_to_the_view(probe)
    trusted = Probe(live=True)
    tf.World(seed=3, universe=U, agents={"probe": trusted},
             trusted_agents=True).run(1)
    assert "cycle_phase" in trusted.read["economy"]


def test_route_tca_refuses_the_probe():
    probe = Probe()
    tf.tca.analyse(probe, seed=3, universe=U, days=1)
    _held_to_the_view(probe)


def test_route_rank_refuses_the_probe():
    probes = []

    def entrants():
        agents = tf.reference_agents(seed=1)
        probes.append(Probe())
        agents["probe"] = probes[-1]
        return agents

    tf.rank(entrants, seeds=[1, 2], universe=U, days=1)
    assert len(probes) == 2
    for probe in probes:
        _held_to_the_view(probe)


def test_route_hidden_state_is_declared_and_still_reads_nothing_it_was_not_given():
    """A privileged agent reads the economy by declaration, and its card
    says so. It still gets no fork, no writes and no anticipation getter."""

    class Declared(Probe):
        privileged = True

        def act(self, obs):
            self.economy = obs.hidden.economy()
            for name in ("fork", "pin_macro", "earnings_anticipation",
                         "state_snapshot"):
                assert not hasattr(obs.hidden, name)
            return super().act(obs)

    agent = Declared()
    card = tf.evaluate({"d": agent}, seed=3, universe=U, days=1)["d"]
    assert card.uses_hidden_state and not card.trusted
    assert "cycle_phase" in agent.economy
    _held_to_the_view(agent)       # obs.engine is the market view regardless


def test_route_gym_env_hands_training_code_the_view():
    pytest.importorskip("numpy")
    from tradefloor.gym import TradingEnv

    def run(trusted):
        env = TradingEnv(universe=U, seed=3, days=1, steps_per_day=3,
                         ticks_per_step=40, trusted_agents=trusted)
        obs, info = env.reset()
        trace = [obs.tobytes()]
        for k in range(3):
            action = [0.1 * ((i + k) % 3 - 1) for i in range(len(U))]
            obs, reward, *_ = env.step(action)
            trace.append((obs.tobytes(), reward))
        return env, info, trace

    env, info, sandboxed = run(False)
    assert "trusted" not in info
    assert isinstance(env.engine, MarketView)
    assert isinstance(env.portfolio, PortfolioView)
    assert env.engine is env.engine        # one view per episode
    for label, how in REFUSED:
        with pytest.raises(SandboxError):
            how(env.engine)
    with pytest.raises(SandboxError):
        env.portfolio.execute
    with pytest.raises(AttributeError):       # the env swaps it, not you
        env.engine = None
    _allowed(env.engine, env.portfolio)

    live, info, trusted = run(True)
    assert info["trusted"] is True
    assert isinstance(live.engine, tf.Engine)
    assert "cycle_phase" in live.engine.state_snapshot()["economy"]
    # The view is access control only: the same market, step for step.
    assert sandboxed == trusted
    assert env._engine.state_hash() == live.engine.state_hash()


def test_route_mcp_runs_strategies_sandboxed(monkeypatch):
    pytest.importorskip("mcp")
    from tradefloor import mcp

    calls = []
    real_evaluate, real_rank = tf.evaluate, tf.rank

    def evaluate(*args, **kwargs):
        calls.append(("evaluate", kwargs.get("trusted_agents")))
        return real_evaluate(*args, **kwargs)

    def rank(*args, **kwargs):
        calls.append(("rank", kwargs.get("trusted_agents")))
        return real_rank(*args, **kwargs)

    monkeypatch.setattr(tf, "evaluate", evaluate)
    monkeypatch.setattr(tf, "rank", rank)
    momentum = {"signal": {"kind": "momentum", "lookback_days": 1.0},
                "portfolio": {"top_k": 2}}
    oracle = {"signal": {"kind": "oracle"}, "portfolio": {"top_k": 2}}
    results = [
        mcp.evaluate_strategies({"m": momentum, "o": oracle}, days=1,
                                universe_size=8),
        mcp.rank_strategies({"m": momentum}, seeds=[1, 2], days=1,
                            universe_size=8),
        mcp.run_stress_scenario("rate_shock", universe_size=8, days=2),
    ]
    assert all(r.get("ok") for r in results), results
    assert calls and all(trusted is False for _, trusted in calls), calls
    rows = {r["name"]: r for r in results[0]["scores"]}
    assert rows["o"].get("uses_hidden_state") is True
    assert "uses_hidden_state" not in rows["m"]

    def keys(value):
        if isinstance(value, dict):
            for k, v in value.items():
                yield k
                yield from keys(v)
        elif isinstance(value, list):
            for v in value:
                yield from keys(v)

    for r in results:
        assert not set(keys(r)) & (HIDDEN_ECONOMY | {"mispricing_s"})
