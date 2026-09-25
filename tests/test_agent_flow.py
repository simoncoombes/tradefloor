"""An agent's trades reach the market once, and the agent pays for them.

Until 0.8.5 every harness in the package handed an agent's fills to
``Engine.run_session`` as ``order_flow``, which the session held on EVERY
tick. One order was counted 65 times at six steps a day, and it landed only
after the agent had filled at the pre-trade book, so the agent collected its
own permanent impact instead of paying it. The spec mean-reversion rule beat
buy-and-hold on 20 of 20 markets of the published suite by a median of 42
points in 60 days on that alone, and a round trip of 1% of daily volume beat
a one-share control by 12 to 52 bp in 10 of 20 names (design repo,
``programme/meanrev-edge-ptv19-2026-09-24.md``).

The fix is an argument, ``fills``, that reaches the market once, on the
session's first tick, and a refusal of the old one. What this file holds:

- the contract: ``fills`` IS one tick of ``order_flow`` and then nothing;
- every entry point that trades for an agent uses it: ``tf.evaluate``, the
  gym environment, ``tca.analyse`` and counterfactual worlds and cohorts;
- the microstructure it buys: no round trip profits from its own impact,
  a one-share agent moves nothing it can see, and on every shipped preset a
  fill costs at least the permanent impact its own order leaves behind;
- the log: new sessions record ``fills`` and ``flow_per_tick``, and a log
  written before 0.8.5 still replays into the market it recorded.

Two presets carry an agent's trades two ways. On pt-v19 ``Portfolio.execute``
prices an order off a snapshot of the book and the harness hands the flow to
the next session as ``fills``. On pt-v20, the default, the agent-facing book
is live: the order executes in the engine's own book, the engine applies its
flow on the next tick under the linear law (``fill_impact_coefficient``), and
``fills`` carries nothing for it. The entry-point tests run on both, and the
tests that feed a snapshot-priced portfolio's ``pending_flow`` name pt-v19.
"""

from __future__ import annotations

import json
import math
import struct

import pytest

import tradefloor as tf
from tradefloor import Engine, Portfolio, ValidationError
from tradefloor.harness import session_clock

ROSTER = tf.Universe.random(20, seed=93001)
TICKS = 65
STEPS = 6

#: The preset without the agent-facing book and the one with it. An agent's
#: fills reach the market through ``fills`` on the first and through the
#: engine's book on the second.
SNAPSHOT_PRESET = "pt-v19"
BOOK_PRESET = "pt-v20"
FLOW_PRESETS = (SNAPSHOT_PRESET, BOOK_PRESET)


def f64(buf: bytes) -> list[float]:
    return list(struct.unpack("<%dd" % (len(buf) // 8), buf))


def thinnest(universe) -> int:
    return min(range(len(universe)), key=lambda k: universe[k].avg_volume)


def flow_attribution(engine: Engine, index: int) -> float:
    return f64(engine.attribution("order_flow_impact"))[index]


# -- the contract --------------------------------------------------------------


def test_run_session_refuses_order_flow_and_logs_nothing():
    """The old argument is refused with the two that replace it named, and
    the refused call leaves no trace in the log: a log holding a call that
    never ran would replay into a different market."""
    e = Engine(seed=1, universe=ROSTER)
    e.open_market()
    before = len(e.order_log)
    with pytest.raises(ValidationError) as caught:
        e.run_session(9, 30, 3, TICKS, order_flow={ROSTER[0].ticker: (1.0, 0.0)})
    message = str(caught.value)
    assert "fills=" in message and "flow_per_tick=" in message
    assert len(e.order_log) == before


@pytest.mark.parametrize("preset", FLOW_PRESETS)
def test_fills_are_one_tick_of_order_flow_then_nothing(preset):
    """The whole contract, as the market the tick loop already defines: a
    session carrying ``fills`` is the same market, to the bit, as a one-tick
    session carrying them and the rest of the session without.

    On pt-v19 it is also one tick carrying them as ``order_flow``. On pt-v20
    the linear law prices them as agent flow through the engine's book
    instead, which a raw tick's ``order_flow`` does not reach, so that
    spelling is the imbalance law's alone."""
    t = ROSTER[thinnest(ROSTER)].ticker
    fills = {t: (1_000.0, 0.0), ROSTER[3].ticker: (0.0, 25_000.0)}

    once = Engine(seed=92001, universe=ROSTER, model=preset)
    once.open_market()
    once.run_session(10, 35, 3, TICKS, fills=fills)

    split = Engine(seed=92001, universe=ROSTER, model=preset)
    split.open_market()
    split.run_session(10, 35, 3, 1, fills=fills)
    split.run_session(10, 36, 3, TICKS - 1)

    assert once.prices() == split.prices()
    assert once.draws_consumed == split.draws_consumed

    if preset == SNAPSHOT_PRESET:
        spelled = Engine(seed=92001, universe=ROSTER, model=preset)
        spelled.open_market()
        spelled.tick(10, 35, 3, order_flow=fills)
        spelled.run_session(10, 36, 3, TICKS - 1)
        assert once.prices() == spelled.prices()
        assert once.draws_consumed == spelled.draws_consumed


#: What 1,000 shares of the thinnest name, passed as ``fills``, moves ``s``
#: by. pt-v19 prices it by the imbalance law, one tick of order flow; pt-v20
#: by the linear law, ``gamma sigma q / V`` with ``gamma`` 0.314.
ONCE = {SNAPSHOT_PRESET: 6.730769e-4, BOOK_PRESET: 5.401555e-4}

#: What the same 1,000 shares moves ``s`` by as ONE tick of order flow, the
#: imbalance law's, which a standing ``flow_per_tick`` applies on every tick
#: under either preset.
ONE_TICK_OF_FLOW = 6.730769e-4


@pytest.mark.parametrize("preset", FLOW_PRESETS)
def test_a_step_counts_an_order_once_where_a_standing_flow_counts_it_65_times(preset):
    """Measured on the thinnest name of the suite's first roster (average
    volume 10,337 shares): 1,000 shares passed as ``fills`` moves ``s`` by
    6.73 bp on pt-v19 and 5.40 bp on pt-v20, the same over a one-tick
    session as over a 65-tick step, and by 437.5 bp held for a 65-tick step,
    which is what every harness did."""
    i = thinnest(ROSTER)
    t = ROSTER[i].ticker

    def impact(ticks=TICKS, **kwargs) -> float:
        e = Engine(seed=92001, universe=ROSTER, model=preset)
        e.open_market()
        e.run_session(9, 30, 3, ticks, **kwargs)
        return flow_attribution(e, i)

    once = impact(fills={t: (1_000.0, 0.0)})
    held = impact(flow_per_tick={t: (1_000.0, 0.0)})
    assert once == pytest.approx(ONCE[preset], rel=1e-6)
    assert impact(1, fills={t: (1_000.0, 0.0)}) == once
    assert held == pytest.approx(65.0 * ONE_TICK_OF_FLOW, rel=1e-6)


def test_an_untraded_step_is_unchanged():
    """No fills, no change: the stepped day an evaluation's baseline runs is
    bit-identical to one 390-tick session, as `session_clock` says it is.
    The known-answer digest checks the same property for the untraded run
    as a whole."""
    stepped = Engine(seed=7, universe=ROSTER)
    stepped.open_market()
    for k in range(STEPS):
        stepped.run_session(*session_clock((9, 30, 3), k, TICKS), TICKS,
                            fills={})
    whole = Engine(seed=7, universe=ROSTER)
    whole.open_market()
    whole.run_session(9, 30, 3, STEPS * TICKS)
    assert stepped.prices() == whole.prices()


# -- every entry point that trades for an agent --------------------------------


class BuyOnceThenHold:
    """Buys a fixed size of one name at the first step, then does nothing."""

    def __init__(self, ticker: str, shares: float) -> None:
        self.ticker, self.shares, self.done = ticker, shares, False

    def act(self, obs):
        if self.done:
            return {}
        self.done = True
        return {self.ticker: self.shares}

    def fork(self):
        twin = BuyOnceThenHold(self.ticker, self.shares)
        twin.done = self.done
        return twin


def reference_net_worth(ticker: str, shares: float, *, seed: int, days: int,
                        model: str, standing: bool = False) -> float:
    """The same trade, stepped by hand with the flow applied through
    `Engine.tick` on the minute after the fill. ``standing`` holds it on
    every tick of the step instead, which is what the harnesses did.

    On a live book the engine applies the flow itself and the portfolio
    holds none, so the step runs as it is. ``standing`` then holds the
    filled shares on every tick on top of that."""
    e = Engine(seed=seed, universe=ROSTER, model=model)
    p = Portfolio(cash=1_000_000.0, max_leverage=2.0)
    step = 0
    for _ in range(days):
        e.open_market()
        for k in range(STEPS):
            if step == 0:
                fill = p.execute(e, ticker, shares)
            flow = p.pending_flow()
            p.clear_flow()
            if e.book_live:
                assert flow == {}, "the engine's book carries the flow"
                flow = ({ticker: (fill["quantity"], 0.0)}
                        if standing and step == 0 else {})
            h, m, d = session_clock((9, 30, 3), k, TICKS)
            if flow and not standing:
                e.tick(h, m, d, order_flow=flow)
                e.run_session(*session_clock((h, m, d), 1, 1), TICKS - 1)
            else:
                e.run_session(h, m, d, TICKS,
                              flow_per_tick=flow if standing else None)
            step += 1
        e.close_market()
    return p.net_worth(e)


@pytest.fixture(scope="module")
def trade():
    i = thinnest(ROSTER)
    return ROSTER[i].ticker, 800.0


@pytest.fixture(scope="module")
def expected(trade):
    """The reference net worth on each preset."""
    ticker, shares = trade
    out = {}
    for preset in FLOW_PRESETS:
        once = reference_net_worth(ticker, shares, seed=92001, days=2,
                                   model=preset)
        held = reference_net_worth(ticker, shares, seed=92001, days=2,
                                   model=preset, standing=True)
        # The two must differ, or none of the equalities below could tell a
        # harness that applies the flow once from one that holds it.
        assert once != held, preset
        out[preset] = once
    return out


def applied_once_in_the_book(engine: Engine, ticker: str,
                             bought: dict[str, float]) -> None:
    """On a live book: no session carried the agents' fills, and the engine
    applied each agent's flow once, on the first tick of the day."""
    sessions = [x for x in engine.order_log if x["op"] == "run_session"]
    assert all(not x["fills"] and not x["flow_per_tick"] for x in sessions)
    rows = engine.take_impacts()
    assert sorted((r["agent"], r["ticker"], r["bought"], r["sold"]) for r in rows) == (
        sorted((agent, ticker, q, 0.0) for agent, q in bought.items()))
    assert all(r["day"] == 0 and r["tick"] == 0 for r in rows)


@pytest.mark.parametrize("preset", FLOW_PRESETS)
def test_evaluate_applies_the_flow_once(trade, expected, preset):
    ticker, shares = trade
    card = tf.evaluate({"a": BuyOnceThenHold(ticker, shares)}, seed=92001,
                       universe=ROSTER, days=2, model=preset)["a"]
    assert card.final_net_worth == expected[preset]


@pytest.mark.parametrize("preset", FLOW_PRESETS)
def test_the_gym_environment_applies_the_flow_once(trade, preset):
    np = pytest.importorskip("numpy")
    from tradefloor.gym import TradingEnv
    ticker, _ = trade
    # Trusted: the test reads the engine's order log, which training code
    # holding the env sees only through the opt-in.
    env = TradingEnv(universe=ROSTER, seed=92001, days=1, model=preset,
                     trusted_agents=True)
    env.reset()
    action = np.zeros(len(ROSTER))
    action[ROSTER.tickers().index(ticker)] = 0.001
    env.step(action)
    if env.engine.book_live:
        (order,) = [x for x in env.engine.order_log if x["op"] == "submit"]
        applied_once_in_the_book(env.engine, ticker,
                                 {order["agent"]: order["quantity"]})
        return
    sessions = [x for x in env.engine.order_log if x["op"] == "run_session"]
    assert sessions[0]["fills"], "the step's trade reached the session"
    assert sessions[0]["flow_per_tick"] == {}


@pytest.mark.parametrize("preset", FLOW_PRESETS)
def test_tca_applies_the_flow_once(trade, expected, preset):
    from tradefloor import tca
    ticker, shares = trade
    execution = tca.analyse(BuyOnceThenHold(ticker, shares), seed=92001,
                            universe=ROSTER, days=2, model=preset)
    final = dict(zip(execution.tickers, execution.actual_path[-1]))
    held = execution.portfolio.positions[ticker].quantity
    assert execution.portfolio.cash + held * final[ticker] == expected[preset]


@pytest.mark.parametrize("preset", FLOW_PRESETS)
def test_a_counterfactual_world_applies_the_flow_once(trade, expected, preset):
    from tradefloor.counterfactual import World
    ticker, shares = trade
    world = World(seed=92001, universe=ROSTER, model=preset,
                  agent=BuyOnceThenHold(ticker, shares))
    world.run(days=2)
    assert world.trace[-1]["net_worth"] == expected[preset]
    if world.engine.book_live:
        applied_once_in_the_book(world.engine, ticker, {
            world.portfolio.owner: world.portfolio.fills[0]["quantity"]})
        return
    sessions = [x for x in world.engine.order_log if x["op"] == "run_session"]
    assert sessions[0]["fills"] and not sessions[0]["flow_per_tick"]
    assert all(not x["fills"] for x in sessions[1:])


@pytest.mark.parametrize("preset", FLOW_PRESETS)
def test_a_cohort_sends_its_merged_fills_once(trade, preset):
    """Two agents buying the same name in one step: the market sees their
    sum, once, on the step's first tick. On pt-v19 each fills what the
    unconsumed ladder holds (800 shares of this name fill 513 each), so the
    sum is read off their fills rather than off what they asked for. On
    pt-v20 each fills 800 in the shared book and the engine applies each
    agent's flow once."""
    from tradefloor.counterfactual import World
    ticker, shares = trade
    world = World(seed=92001, universe=ROSTER, model=preset,
                  agents={"a": BuyOnceThenHold(ticker, shares),
                          "b": BuyOnceThenHold(ticker, shares)})
    world.run(days=1)
    filled = {name: sum(f["quantity"] for f in p.fills)
              for name, p in world.portfolios.items()}
    assert all(q > 0 for q in filled.values())
    if world.engine.book_live:
        applied_once_in_the_book(world.engine, ticker, filled)
        return
    sessions = [x for x in world.engine.order_log if x["op"] == "run_session"]
    assert sessions[0]["fills"][ticker] == [sum(filled.values()), 0.0]
    assert all(not x["fills"] and not x["flow_per_tick"] for x in sessions[1:])


# -- the microstructure it buys ------------------------------------------------


def round_trip(universe, seed: int, index: int, shares: float, *,
               feed: str | None) -> dict:
    """Buy at the first step of day 1, flatten at the second, after one
    untraded day. ``feed`` is how the flow reaches the market: ``fills``,
    ``flow_per_tick`` (the pre-0.8.5 harness) or None (not at all).

    On pt-v19, whose portfolio prices off a snapshot and holds the flow for
    the harness to feed. On pt-v20 the engine's book applies the flow itself
    and ``feed`` has nothing to carry; ``test_order_book_depth.py`` runs the
    same round trips through that book."""
    e = Engine(seed=seed, universe=universe, model=SNAPSHOT_PRESET)
    p = Portfolio(cash=1e10)
    t = e.tickers[index]
    e.open_market()
    e.run_session(9, 30, 3, 390)
    e.close_market()
    e.open_market()
    prints = []
    for k in range(2):
        prints.append(f64(e.prices())[index])
        p.execute(e, t, shares if k == 0 else -p.positions[t].quantity)
        flow = p.pending_flow()
        p.clear_flow()
        clock = session_clock((9, 30, 3), k, TICKS)
        e.run_session(*clock, TICKS, **({feed: flow} if feed else {}))
    buy, sell = p.fills
    notional = abs(buy["notional"])
    cost = ((buy["price"] - prints[0]) * buy["quantity"]
            + (prints[1] - sell["price"]) * abs(sell["quantity"]))
    return {"pnl": p.cash - 1e10, "notional": notional,
            "cost_bp": cost / notional * 1e4, "next_print": prints[1]}


ROUND_TRIP_ROSTERS = (93001, 93002, 71)
ROUND_TRIP_SIZES = (0.0, 0.01, 0.02)     # of daily volume; 0.0 is one share


def _gains(feed: str) -> list[dict]:
    rows = []
    for useed in ROUND_TRIP_ROSTERS:
        u = tf.Universe.random(20, seed=useed)
        for i in range(len(u)):
            for frac in ROUND_TRIP_SIZES:
                shares = 1.0 if frac == 0 else max(1.0, round(frac * u[i].avg_volume))
                fed = round_trip(u, useed + 7, i, shares, feed=feed)
                bare = round_trip(u, useed + 7, i, shares, feed=None)
                rows.append({
                    "frac": frac,
                    # What feeding its own flow did to the round trip's P&L.
                    "gain_bp": (fed["pnl"] - bare["pnl"]) / fed["notional"] * 1e4,
                    # What the round trip paid the book, against the prints.
                    "cost_bp": bare["cost_bp"],
                    "self_bp": (fed["next_print"] - bare["next_print"])
                               / bare["next_print"] * 1e4,
                })
    return rows


@pytest.fixture(scope="module")
def once_rows():
    return _gains("fills")


@pytest.fixture(scope="module")
def held_rows():
    return _gains("flow_per_tick")


def test_no_round_trip_profits_from_its_own_impact(once_rows):
    """Buy, then sell a step later, on every name of three 20-name rosters
    at one share, 1% and 2% of daily volume on pt-v19, whose harness feeds
    the flow: 180 round trips. What feeding its own flow adds to the P&L
    never covers what the book charged for the trip, so the trip is a loss
    before the market's own move in every case.

    Measured 2026-09-24 on 400 trips (five rosters, four sizes): no trip's
    own-impact gain exceeded its cost, and the largest gain, +31.7 bp on a
    2% trip, came with a larger cost. Case by case the gain is noisy, from
    -46 to +32 bp, because a moved model price changes which way the
    settlement draws fall; its median is 0.0 at every size."""
    over = [r for r in once_rows if r["gain_bp"] > r["cost_bp"] + 1e-9]
    assert not over, over[:5]


def test_the_pre_fix_harness_fails_the_same_test(held_rows):
    """Non-vacuity, on pt-v19 with the round trips above. Holding the flow
    for the step, as the harness did, pays for itself in a quarter of the
    trips: measured on 400, 17% of one-share trips and 26% of 2% trips
    gained more from their own impact than they paid, by up to 232 bp. A
    test that the old harness passed would prove nothing."""
    over = [r for r in held_rows if r["gain_bp"] > r["cost_bp"]]
    assert len(over) >= len(held_rows) // 10


def test_a_one_share_agent_sees_no_impact_of_its_own(once_rows, held_rows):
    """One share moves the print the agent meets at its next step by
    nothing on average: measured on the 60 one-share trips here, on
    pt-v19, a mean within 2 bp of zero, where holding the flow for the step
    made it +9 bp on the 100 of the investigation's grid. Case by case it is
    the same settlement noise the round trips carry."""
    once = [r["self_bp"] for r in once_rows if r["frac"] == 0.0]
    held = [r["self_bp"] for r in held_rows if r["frac"] == 0.0]
    assert abs(sum(once) / len(once)) < 2.0
    assert sum(held) / len(held) > 5.0


def test_one_share_moves_the_model_price_by_under_a_basis_point():
    """Exactly, through the attribution rather than the print: the 0.2
    floor on a small order's imbalance gives one share at most 0.9 bp of
    `s` in the thinnest names, and nothing in the liquid ones."""
    for useed in ROUND_TRIP_ROSTERS:
        u = tf.Universe.random(20, seed=useed)
        e = Engine(seed=useed, universe=u)
        e.open_market()
        e.run_session(9, 30, 3, TICKS,
                      fills={t: (1.0, 0.0) for t in e.tickers})
        moved = f64(e.attribution("order_flow_impact"))
        assert max(moved) < 1e-4, max(moved)


PRESETS = tuple(tf.preset_names())


@pytest.mark.parametrize("preset", PRESETS)
def test_a_fill_pays_at_least_its_own_permanent_impact(preset):
    """The fair-pricing condition, on every shipped preset.

    An order fills against the book standing at the step boundary and its
    permanent impact reaches the market on the next tick. That is free of a
    round-trip profit only while the book charges at least the permanent
    impact an order causes: a buy's fill must sit at or above the book's
    mid times ``exp`` of the ``s`` its own flow adds, a sell's at or below.

    Against the MID, not the last print. The print sits at whichever touch
    last traded, so a small sell against a print at the bid pays nothing
    over it, and did here: 0.0 against an impact of 0.0015 bp on pt-v2. The
    round trip still pays the spread on its other leg, which is what
    `test_no_round_trip_profits_from_its_own_impact` measures end to end.

    Checked on every name of two rosters, both sides, at 0.1%, 1%, 2% and
    5% of daily volume (the book rarely holds more). ``s`` is read from the
    attribution, so the comparison carries no settlement noise. A preset
    that recalibrates impact upward fails here, and then the fill has to
    start charging the impact it causes, or the harvest this file exists to
    stop comes back."""
    for useed in (93001, 111):
        u = tf.Universe.random(20, seed=useed)
        for frac in (0.001, 0.01, 0.02, 0.05):
            for side in ("buy", "sell"):
                e = Engine(seed=5, universe=u, model=preset)
                e.open_market()
                e.run_session(9, 30, 3, TICKS)
                sizes, charged = {}, {}
                for i, t in enumerate(e.tickers):
                    book = e.book(t)
                    want = max(1.0, round(frac * u[i].avg_volume))
                    cost = book.sweep_cost(side, want)
                    if cost is None or cost.filled <= 0:
                        continue
                    mid = (book.best_bid + book.best_ask) / 2.0
                    sizes[t] = ((cost.filled, 0.0) if side == "buy"
                                else (0.0, cost.filled))
                    sign = 1.0 if side == "buy" else -1.0
                    charged[i] = sign * (cost.average_price / mid - 1.0)
                e.run_session(10, 35, 3, 1, fills=sizes)
                moved = f64(e.attribution("order_flow_impact"))
                for i, premium in charged.items():
                    impact = abs(moved[i])
                    # log(1 + premium) is the premium in `s`'s units.
                    assert math.log1p(premium) >= impact, (
                        preset, useed, frac, side, e.tickers[i], premium, impact)


# -- the log ---------------------------------------------------------------------


def test_a_session_logs_fills_and_its_per_tick_flow_under_their_names():
    e = Engine(seed=3, universe=ROSTER)
    e.open_market()
    e.run_session(9, 30, 3, TICKS, fills={ROSTER[0].ticker: (10.0, 0.0)},
                  flow_per_tick={ROSTER[1].ticker: (0.0, 5.0)})
    entry = [x for x in e.order_log if x["op"] == "run_session"][0]
    assert entry["fills"] == {ROSTER[0].ticker: [10.0, 0.0]}
    assert entry["flow_per_tick"] == {ROSTER[1].ticker: [0.0, 5.0]}
    assert "order_flow" not in entry
    # A log is data: it survives JSON and replays to the same market.
    log = json.loads(json.dumps(e.order_log))
    again = tf.replay(log, seed=3, universe=ROSTER)
    assert again.prices() == e.prices()


def test_a_log_written_before_0_9_0_replays_into_the_market_it_recorded():
    """A 0.8.x log names a session's flow ``order_flow`` and meant it on
    every tick. Replayed now it must still mean that, or an archived traded
    run replays into a different market under the same seed."""
    t = ROSTER[0].ticker
    e = Engine(seed=4, universe=ROSTER)
    e.open_market()
    e.run_session(9, 30, 3, TICKS, flow_per_tick={t: (2e5, 0.0)})
    e.close_market()
    old = json.loads(json.dumps(e.order_log))
    for entry in old:
        if entry["op"] == "run_session":
            entry["order_flow"] = entry.pop("flow_per_tick")
            entry.pop("fills")
    again = tf.replay(old, seed=4, universe=ROSTER)
    assert again.prices() == e.prices()
