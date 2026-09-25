"""What an agent is handed, and what happens when it reaches past it.

An independent audit of 0.8.5 found that every harness handed agents the live
engine as ``obs.engine``. Two agents it wrote made the point: one forked the
engine and traded on the fork's next step (+11.4% in five days, four seeds of
four), and one rewrote the fundamentals of a name it held (+184%). Neither
scorecard showed an error or a flag. These tests reproduce both, and hold the
harness to refusing them by default and flagging them under the opt-in.
"""

from __future__ import annotations

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

    def act(self, obs):
        fork = obs.engine.fork(1)[0]
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
    what catches a write however the agent reached the engine."""

    class Around:
        def act(self, obs):
            if obs.step == 2:
                engine = obs.engine._MarketView__engine
                engine.pin_macro(federal_funds_rate=0.2)
            return {}

    card = _run({"around": Around()})["around"]
    assert card.tampered and not card.trusted
    assert any("step 2: tampered:" in e and "state hash" in e
               for e in card.errors)


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
