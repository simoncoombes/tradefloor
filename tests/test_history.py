"""The pre-history is the same market run earlier, and reading it moves nothing.

These pin the identity the design rests on: N untraded sessions of
pre-history followed by T more reach, state hash for state hash, the market
an untraded run of N+T sessions reaches, forks of it are identical, and the
harness's own untraded baseline agrees on the prices.
"""

import pytest

import tradefloor as tf
from tradefloor.harness import _f64, _run_untraded
from tradefloor.sandbox import SandboxError
from tradefloor.history import (MAX_HISTORY_DAYS, History, check_history_days,
                                prehistory, run_untraded_day)

SEED = 2001
SPD, TPS = 6, 65


def _universe():
    return list(tf.Universe.random(6, seed=111))


def _engine(u):
    return tf.Engine(seed=SEED, universe=u, model="pt-v20")


@pytest.mark.parametrize("n,t", [(0, 2), (3, 2)])
def test_prehistory_then_run_equals_longer_untraded_run(n, t):
    u = _universe()
    e = _engine(u)
    hist = prehistory(e, n)
    assert len(hist) == n and hist.days == list(range(-n, 0))
    for _ in range(t):
        run_untraded_day(e, hist, SPD, TPS, (9, 30, 3))
    off = _engine(u)
    prehistory(off, n + t)
    assert e.state_hash() == off.state_hash()
    assert hist.days == list(range(-n, t))
    prices = list(_f64(e.prices()))
    assert prices == _run_untraded(SEED, u, None, n + t, SPD, TPS, 9, 30, 3,
                                   None, "pt-v20")


def test_forks_after_prehistory_are_identical():
    u = _universe()
    e = _engine(u)
    prehistory(e, 2)
    a, b = e.fork(2)
    ha, hb = History(a.tickers, SPD, 0), History(b.tickers, SPD, 0)
    run_untraded_day(a, ha, SPD, TPS, (9, 30, 3))
    run_untraded_day(b, hb, SPD, TPS, (9, 30, 3))
    assert a.state_hash() == b.state_hash()
    assert ha.closes() == hb.closes()


def test_bars_are_consistent_and_published_only():
    u = _universe()
    e = _engine(u)
    hist = prehistory(e, 2)
    for d in range(2):
        for i in range(len(u)):
            hi, lo = hist.rows("high")[d][i], hist.rows("low")[d][i]
            cl, op = hist.rows("close")[d][i], hist.rows("open")[d][i]
            assert lo <= cl <= hi and lo <= op <= hi
    assert len(hist.steps()) == 2 * SPD
    assert hist.window(3, 1) == hist.rows("open")[-2:]
    assert len(hist.macro("vix")) == 2
    with pytest.raises(SandboxError):
        hist.macro("qe_pe_boost")


@pytest.mark.parametrize("bad", [-1, MAX_HISTORY_DAYS + 1, 1.5, True, "252"])
def test_history_days_validated(bad):
    with pytest.raises(tf.ValidationError):
        check_history_days(bad)


# -- wired into the harness ------------------------------------------------
#
# The rows registered for pre-history (PH1-PH3) in their small form: the
# harness's fork of a pre-history is the market an untraded run of N+T days
# reaches, a run without one is the run it always was, and a lookback agent
# handed one trades from the first step.

from tradefloor.baselines import BuyAndHold, Momentum, reference_agents
from tradefloor.harness import Observation, leaderboard


class _Recorder:
    """Keeps every observation's history length and the engine it saw."""

    def __init__(self):
        self.seen = []

    def act(self, obs):
        h = obs.history
        self.seen.append((obs.day, obs.step,
                          None if h is None else (len(h), h.days[-1])))
        return {}


def test_evaluate_history_forks_are_the_longer_untraded_market():
    u = _universe()
    n, t = 3, 2
    agents = {"a": _Recorder(), "b": _Recorder()}
    cards = tf.evaluate(agents, seed=SEED, universe=u, days=t, model="pt-v20",
                        history_days=n)
    assert all(c.history_days == n for c in cards.values())
    assert all(c.as_dict()["history_days"] == n for c in cards.values())
    # The history ends at the day before the one being traded, at every step.
    for rec in agents.values():
        for day, _, shown in rec.seen:
            assert shown == (n + day, day - 1)
    # An untraded agent's market is the untraded run of n + t sessions: its
    # net worth is flat, and the baseline it was measured against matches.
    off = _run_untraded(SEED, u, None, n + t, SPD, TPS, 9, 30, 3, None,
                        "pt-v20")
    e = _engine(u)
    prehistory(e, n)
    base, a = e.fork(2)
    from tradefloor.harness import _advance_untraded
    assert _advance_untraded(base, t, SPD, TPS, 9, 30, 3) == off
    ref = _engine(u)
    prehistory(ref, n + t)
    assert a.state_hash() != ref.state_hash()      # a has not run yet
    _advance_untraded(a, t, SPD, TPS, 9, 30, 3)
    assert a.state_hash() == ref.state_hash()


def test_without_history_nothing_changes():
    u = _universe()
    rec = _Recorder()
    cards = tf.evaluate({"r": rec, "h": BuyAndHold()}, seed=SEED, universe=u,
                        days=2, model="pt-v20")
    assert all(shown is None for _, _, shown in rec.seen)
    assert all(c.history_days == 0 for c in cards.values())
    assert all("history_days" not in c.as_dict() for c in cards.values())
    assert "history" not in repr(cards["h"])


def test_each_agent_grows_its_own_history():
    # After day zero each agent's market is its own; the bars it is shown
    # must be the ones its own trading printed.
    u = _universe()
    seen = {}

    class Heavy:
        def act(self, obs):
            seen.setdefault("heavy", obs.history)
            return {obs.tickers[0]: 5_000.0} if obs.day == 0 else {}

    class Quiet:
        def act(self, obs):
            seen.setdefault("quiet", obs.history)
            return {}

    tf.evaluate({"heavy": Heavy(), "quiet": Quiet()}, seed=SEED, universe=u,
                days=2, model="pt-v20", history_days=2)
    heavy, quiet = seen["heavy"], seen["quiet"]
    assert heavy is not quiet
    assert heavy.days == quiet.days == [-2, -1, 0, 1]
    assert heavy.closes()[:2] == quiet.closes()[:2]
    assert heavy.closes()[2] != quiet.closes()[2]


def test_lookback_agent_trades_from_the_first_step_with_history():
    u = _universe()
    cold = tf.evaluate({"m": Momentum(lookback_days=2)}, seed=SEED,
                       universe=u, days=2, model="pt-v20")["m"]
    warm = tf.evaluate({"m": Momentum(lookback_days=2)}, seed=SEED,
                       universe=u, days=2, model="pt-v20",
                       history_days=2)["m"]
    assert cold.trades == 0            # a 12-step window never fills in 12
    assert warm.trades > 0


def test_window_is_what_the_agent_would_have_recorded():
    # The prefilled window equals the rows a cold agent collects over the
    # same sessions: the step prices at the harness cadence, the day's
    # opening prices at a daily one.
    u = _universe()
    e = _engine(u)
    hist = prehistory(e, 3)
    rows = []

    class Collect:
        def act(self, obs):
            rows.append(list(obs.prices))
            return {}

    tf.evaluate({"c": Collect()}, seed=SEED, universe=u, days=3,
                model="pt-v20")
    assert hist.window(18, SPD) == rows
    assert hist.window(3, 1) == rows[::SPD]


def test_spec_agents_and_daily_cadence_see_history():
    u = _universe()
    spec = tf.StrategySpec.momentum(lookback_days=3.0, top_k=2)
    daily = tf.StrategySpec.momentum(lookback_days=3.0, top_k=2,
                                     cadence="daily")
    blend = tf.StrategySpec.blend([
        {"kind": "momentum", "weight": 0.5, "lookback_days": 3.0},
        {"kind": "mean_reversion", "weight": 0.5, "lookback_days": 1.0}],
        top_k=2)
    for s in (spec, daily, blend):
        cold = tf.evaluate({"s": s}, seed=SEED, universe=u, days=2,
                           model="pt-v20")["s"]
        warm = tf.evaluate({"s": s}, seed=SEED, universe=u, days=2,
                           model="pt-v20", history_days=3)["s"]
        assert cold.trades == 0 and warm.trades > 0, s.to_json()


def test_cards_with_different_history_are_not_ranked_together():
    u = _universe()
    a = tf.evaluate({"x": BuyAndHold()}, seed=SEED, universe=u, days=1,
                    model="pt-v20")
    b = tf.evaluate({"y": BuyAndHold()}, seed=SEED, universe=u, days=1,
                    model="pt-v20", history_days=1)
    leaderboard({**a})
    leaderboard({**b})
    with pytest.raises(tf.ValidationError, match="history_days"):
        leaderboard({**a, **b})


def test_rank_records_history_and_refuses_bad_values():
    u = _universe()
    r = tf.rank(lambda: {"h": BuyAndHold(), "oracle": BuyAndHold()},
                seeds=[SEED, SEED + 1], universe=u, days=1, model="pt-v20",
                history_days=1)
    assert r.history_days == 1 and r.as_dict()["history_days"] == 1
    assert "pre-history" in r.report()
    off = tf.rank(lambda: {"h": BuyAndHold(), "oracle": BuyAndHold()},
                  seeds=[SEED, SEED + 1], universe=u, days=1,
                  model="pt-v20")
    assert off.history_days == 0 and "history_days" not in off.as_dict()
    with pytest.raises(tf.ValidationError):
        tf.rank(lambda: {"h": BuyAndHold()}, seeds=[SEED], universe=u,
                days=1, history_days=-1)
    with pytest.raises(tf.ValidationError):
        tf.evaluate({"h": BuyAndHold()}, seed=SEED, universe=u, days=1,
                    history_days=MAX_HISTORY_DAYS + 1)


def test_observation_built_by_hand_has_no_history():
    obs = Observation(0, 0, ["A"], [1.0], None, None, [1.0])
    assert obs.history is None


def test_gym_reset_with_history():
    pytest.importorskip("numpy")
    from tradefloor.gym import TradingEnv
    u = _universe()
    env = TradingEnv(universe=u, seed=SEED, days=2, steps_per_day=SPD,
                     ticks_per_step=TPS, model="pt-v20")
    obs0, info = env.reset()
    assert env.history is None and "history_days" not in info
    obs, info = env.reset(options={"history_days": 2})
    assert info["history_days"] == 2 and env.history.days == [-2, -1]
    # The episode's market is the one an untraded run of two days reached.
    ref = _engine(u)
    prehistory(ref, 2)
    import numpy as np
    for _ in range(SPD):
        env.step(np.zeros(len(u)))
    assert env.history.days == [-2, -1, 0]
    assert len(env.history.steps()) == 3 * SPD
    with pytest.raises(tf.ValidationError):
        env.reset(options={"history_days": -3})
