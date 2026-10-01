"""``history_days``: untraded days before day 0, and ``obs.history``.

A rule that needs 20 days of prices used to sit out the first 20 days of
every ``evaluate`` and ``rank`` run in cash while buy-and-hold was
invested, because every run started at day 0 with no history and
``obs.engine.bars()`` refuses when nothing is recorded (a 0.8.5 reviewer's
"no warm-up or pre-history for lookback strategies"). ``history_days=N``
on ``evaluate``, ``rank`` and ``World`` runs the market for N days first,
with nobody trading, and ``obs.history`` holds those days' bars and
published macro at the first decision.

Left at 0 it changes nothing: the same prices, the same scorecard and the
same scorecard dict. ``tests/test_known_answer*.py`` and
``tests/test_python_versions.py`` hold the digests.
"""
from __future__ import annotations

import struct

import pytest

import tradefloor as tf
from tradefloor import harness
from tradefloor.harness import session_clock
from tradefloor.sandbox import PUBLISHED_MACRO

U = tf.Universe.random(5, seed=21)
SEED = 13


def _f64(buf: bytes) -> list[float]:
    return list(struct.unpack("<%dd" % (len(buf) // 8), buf))


class Watcher:
    """Trades nothing; keeps what it was shown at each step."""

    def __init__(self) -> None:
        self.prices: dict[int, list[float]] = {}
        self.lengths: dict[int, int] = {}
        self.first_bars: list[dict] | None = None
        self.first_macro: list[dict] | None = None
        self.warmup: int | None = None

    def act(self, obs):
        self.prices[obs.step] = list(obs.prices)
        self.lengths[obs.step] = len(obs.history)
        if obs.step == 0:
            self.first_bars = obs.history.bars()
            self.first_macro = obs.history.macro()
            self.warmup = obs.history.warmup_days
        return {}


def _plain_run(days: int, *, record: bool = False):
    """The market run by hand, the harness's way, with nobody trading.

    Returns the engine, the prices at the start of every step, and the
    columns and macro each day showed before its close."""
    engine = tf.Engine(seed=SEED, universe=U)
    opens: list[list[float]] = []
    closes: list[dict] = []
    for day in range(days):
        engine.open_market()
        for step in range(6):
            opens.append(_f64(engine.prices()))
            engine.run_session(*session_clock((9, 30, 3), step, 65), 65)
        closes.append({
            "columns": {f: _f64(engine.column(f))
                        for f in ("open", "high", "low", "price", "volume")},
            "macro": dict(engine.macro_fields)})
        if record:
            engine.record(day)
        engine.close_market()
    return engine, opens, closes


# -- the warm-up ------------------------------------------------------------

def test_history_days_puts_the_warm_up_before_the_first_decision():
    agent = Watcher()
    card = tf.evaluate({"w": agent}, seed=SEED, universe=U, days=2,
                       history_days=4)["w"]
    assert card.errors == []
    assert agent.warmup == 4
    assert agent.lengths[0] == 4
    assert [row["day"] for row in agent.first_macro] == [-4, -3, -2, -1]
    assert sorted({row["day"] for row in agent.first_bars}) == [-4, -3, -2, -1]
    assert len(agent.first_bars) == 4 * len(U)
    # Each scored day joins after its close: day 1's first step sees day 0.
    assert agent.lengths[6] == 5
    assert agent.lengths[11] == 5
    assert card.history_days == 4
    assert card.as_dict()["history_days"] == 4
    assert "history_days=4" in repr(card)


def test_the_scored_days_continue_the_warmed_market():
    """With a warm-up of 3 the agent's day 0 is the seed's fourth day: the
    prices it is shown at every step are the ones a plain run of five days
    shows at its last ten steps."""
    agent = Watcher()
    tf.evaluate({"w": agent}, seed=SEED, universe=U, days=2, history_days=3)
    _, opens, _ = _plain_run(5)
    assert [agent.prices[s] for s in range(12)] == opens[18:]


def test_the_baseline_runs_the_same_warm_up(monkeypatch):
    """Impact is measured against the untraded run, which has to start from
    the warmed market too."""
    seen = []
    real = harness._run_untraded

    def spy(*args, engine=None, **kwargs):
        seen.append(_f64(engine.prices()))
        return real(*args, engine=engine, **kwargs)

    monkeypatch.setattr(harness, "_run_untraded", spy)
    tf.evaluate({"w": Watcher()}, seed=SEED, universe=U, days=1,
                history_days=3)
    _, opens, _ = _plain_run(4)
    assert seen == [opens[18]]


def test_warm_up_bars_are_what_a_recorded_run_reports():
    """Every field of a warm-up bar is the one ``Engine.bars(grain="day")``
    gives for the same day. The volume is the shares traded that day: since
    decision 1 a bar's volume is per bar at every grain, and the tick rows
    hold each minute's volume, not a running total."""
    pa = pytest.importorskip("pyarrow")
    agent = Watcher()
    tf.evaluate({"w": agent}, seed=SEED, universe=U, days=1, history_days=3)
    engine, _, _ = _plain_run(3, record=True)
    daily = pa.table(engine.bars(grain="day")).to_pylist()
    tickers = list(engine.tickers)
    by_key = {(row["day"] - 3, tickers[row["instrument_id"]]): row
              for row in daily}
    assert len(agent.first_bars) == len(by_key)
    for bar in agent.first_bars:
        want = by_key[(bar["day"], bar["ticker"])]
        for field in ("open", "high", "low", "close", "volume"):
            assert bar[field] == want[field], (field, bar, want)


def test_macro_history_is_the_published_figures_before_each_close():
    agent = Watcher()
    tf.evaluate({"w": agent}, seed=SEED, universe=U, days=1, history_days=3)
    _, _, closes = _plain_run(3)
    for row, day in zip(agent.first_macro, closes):
        assert set(row) - {"day"} <= PUBLISHED_MACRO
        assert "qe_pe_boost" not in row
        want = {k: v for k, v in day["macro"].items() if k in PUBLISHED_MACRO}
        assert {k: v for k, v in row.items() if k != "day"} == want


def test_bars_by_ticker_and_last():
    agent = Watcher()

    class Asker(Watcher):
        def act(self, obs):
            if obs.step == 0:
                name = obs.tickers[1]
                self.one = obs.history.bars(name, last=2)
                self.macro_last = obs.history.macro(last=1)
                self.one[0]["close"] = -1.0
                self.again = obs.history.bars(name, last=2)
                try:
                    obs.history.bars("NOPE")
                except tf.ValidationError as exc:
                    self.refusal = str(exc)
                try:
                    obs.history.bars(name, last=0)
                except tf.ValidationError as exc:
                    self.zero = str(exc)
            return {}

    agent = Asker()
    card = tf.evaluate({"a": agent}, seed=SEED, universe=U, days=1,
                       history_days=3)["a"]
    assert card.errors == []
    assert [row["day"] for row in agent.again] == [-2, -1]
    assert {row["ticker"] for row in agent.again} == {U[1].ticker}
    # A row handed out is a copy: changing it changed nothing held.
    assert agent.again[0]["close"] > 0
    assert [row["day"] for row in agent.macro_last] == [-1]
    assert "No ticker 'NOPE'" in agent.refusal
    assert "last must be 1 or more" in agent.zero


# -- off, nothing changes -----------------------------------------------------

def test_without_history_days_the_run_is_the_one_it_was():
    refs = tf.baselines.reference_agents()
    default = tf.evaluate({k: refs[k] for k in ("random", "buy_and_hold")},
                          seed=7, universe=U, days=2)
    refs = tf.baselines.reference_agents()
    zero = tf.evaluate({k: refs[k] for k in ("random", "buy_and_hold")},
                       seed=7, universe=U, days=2, history_days=0)
    for name in default:
        assert default[name].as_dict() == zero[name].as_dict()
        assert "history_days" not in default[name].as_dict()
        assert "history_days" not in repr(default[name])


def test_without_history_days_day_zero_is_the_seed_s_first_day():
    agent = Watcher()
    tf.evaluate({"w": agent}, seed=SEED, universe=U, days=2)
    _, opens, _ = _plain_run(2)
    assert [agent.prices[s] for s in range(12)] == opens
    # Empty at the first decision, and each day joins after its close.
    assert agent.first_bars == [] and agent.first_macro == []
    assert agent.warmup == 0
    assert agent.lengths[0] == 0 and agent.lengths[6] == 1


@pytest.mark.parametrize("bad", [-1, 2.0, True, "3"])
def test_history_days_must_be_a_whole_number_of_days(bad):
    with pytest.raises(tf.ValidationError, match="history_days"):
        tf.evaluate({"w": Watcher()}, seed=SEED, universe=U, days=1,
                    history_days=bad)
    with pytest.raises(tf.ValidationError, match="history_days"):
        tf.World(seed=SEED, universe=U, agent=Watcher(), history_days=bad)


# -- World, rank and the other routes -----------------------------------------

def test_world_runs_the_warm_up_when_it_is_built():
    agent = Watcher()
    world = tf.World(seed=SEED, universe=U, agent=agent, history_days=3)
    assert world.day == 0 and world.step == 0 and world.history_days == 3
    world.run(2)
    engine, opens, _ = _plain_run(5)
    assert [agent.prices[s] for s in range(12)] == opens[18:]
    assert world.engine.prices() == engine.prices()
    assert agent.lengths[0] == 3 and agent.lengths[6] == 4
    # The log holds the warm-up, so a manifest rebuilds the same market.
    assert world.manifest().reproduce().prices() == world.engine.prices()


def test_a_world_s_pins_start_on_the_first_scored_day():
    agent = Watcher()
    world = tf.World(seed=SEED, universe=U, agent=agent, history_days=2,
                     pins={"federal_funds_rate": 0.06})
    world.run(1)
    rates = {row["day"]: row["federal_funds_rate"]
             for row in world._history.macro()}
    assert rates[0] == pytest.approx(0.06)
    assert rates[-2] != pytest.approx(0.06)
    assert rates[-1] != pytest.approx(0.06)


def test_a_fork_carries_the_history_and_keeps_its_own():
    world = tf.World(seed=SEED, universe=U, agent=Watcher(), history_days=2)
    world.run(1)
    left, right = world.fork("left", "right")
    left.run(2)
    assert right.history_days == 2
    assert len(world._history) == 3
    assert len(right._history) == 3
    assert len(left._history) == 5
    assert left._history.macro()[:3] == world._history.macro()


def test_rank_passes_history_days_to_every_seed():
    warmups = []

    class Seen(Watcher):
        def act(self, obs):
            if obs.step == 0:
                warmups.append((obs.history.warmup_days, len(obs.history)))
            return {}

    ranking = tf.rank(lambda: {"seen": Seen(),
                               "buy_and_hold": tf.baselines.BuyAndHold()},
                      seeds=[1, 2], universe=U, days=1, history_days=3)
    assert warmups == [(3, 3), (3, 3)]
    assert ranking.seeds == [1, 2]


def test_tca_and_a_daily_spec_hand_on_the_history():
    from tradefloor.spec import _DailyCadence

    inner = Watcher()
    tf.evaluate({"d": _DailyCadence(inner)}, seed=SEED, universe=U, days=2,
                history_days=2)
    assert inner.warmup == 2 and inner.lengths[1] == 3

    agent = Watcher()
    tf.tca.analyse(agent, seed=SEED, universe=U, days=2)
    assert agent.lengths[0] == 0 and agent.lengths[6] == 1
