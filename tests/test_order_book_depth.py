"""The agent-facing book: price for size, a shared book, and resting orders.

Two outside reviewers of 0.8.x, in their own words. A fund quant: "Beyond
about 2% of ADV per step there's no price for size, only a silent drop." An
academic studying interacting agents: "Cohort agents take no levels from
each other, resting limit orders never enter the book, and there is no
queue." `rust/src/agent_book.rs` carries the model that answers both; this
file holds it to its claims.

- **Off is off.** Every dial ships at 0.0 on every preset, an untraded
  market is the same to the bit at ANY setting of them, and with them off an
  agent trades as it did before they existed.
- **Price for size.** An order past the maker's ladder walks latent depth at
  worse prices instead of being cut off, and the price it pays follows the
  square-root law.
- **Consumption and refill.** What one order takes, the next meets gone: the
  maker's ladder until the next tick, the latent depth until it refills at
  its half-life.
- **Impact.** The temporary part decays with the refill; the permanent part
  lands once, in `s`, linear in size, and is attributed to the agent that
  caused it.
- **No arbitrage.** No round trip profits from its own impact, at any size,
  and the dial that makes the permanent law linear closes a manipulation the
  imbalance law's floor leaves open.
- **The queue.** A resting order waits behind the depth at its price, fills
  when the model's flow or another agent trades through it, partially when
  the flow is smaller than the queue, and leaves when it is cancelled.
- **Several agents.** A later agent meets the book an earlier one left,
  `externalities` sees it, and everything is deterministic under replay,
  fork and restore.
"""

from __future__ import annotations

import json
import math
import statistics
import struct

import pytest

import tradefloor as tf
from tradefloor import manifest
from tradefloor.counterfactual import World
from tradefloor.harness import session_clock

#: The seven dials, at the values suggested for pt-v20
#: (tools/calibration/impact_curve.py; the hand-off lists them).
LIVE = dict(book_depth_coefficient=0.75, book_depth_exponent=0.5,
            book_depth_reach=1.0, book_shared=1.0, book_refill_half_life=27.0,
            book_resting=1.0, fill_impact_coefficient=0.314)
DIALS = tuple(LIVE)
ROSTER = tf.Universe.random(20, seed=93001)

#: The last preset with every dial off, which the "off" arms below run on.
#: The default takes the book from pt-v20, so an engine built without a
#: model is no longer an engine without one.
OFF = "pt-v19"


def f64(buf: bytes) -> list[float]:
    return list(struct.unpack("<%dd" % (len(buf) // 8), buf))


def live(**over) -> tf.ModelParams:
    return tf.ModelParams.from_preset(**{**LIVE, **over})


def warmed(model=None, universe=ROSTER, seed=92001) -> tf.Engine:
    """An engine one untraded day in, with the next day open and one step
    run, so the maker has quoted around a real print."""
    e = tf.Engine(seed=seed, universe=universe, model=model)
    e.open_market()
    e.run_session(9, 30, 3, 390)
    e.close_market()
    e.open_market()
    e.run_session(9, 30, 3, 65)
    return e


def index_by_volume(universe, which: str) -> int:
    pick = min if which == "thinnest" else max
    return pick(range(len(universe)), key=lambda k: universe[k].avg_volume)


def taken(engine: tf.Engine, index: int) -> list[float]:
    """The book's consumed-depth row for one name, from the snapshot: maker
    bid, maker ask, latent bid, latent ask, and the maker's pending
    inventory."""
    raw = engine.state_snapshot()["book"]["taken"]
    row = f64(raw)[5 * index: 5 * index + 5]
    return row


# -- off is off ------------------------------------------------------------------


#: The presets that take the book: pt-v20 carries the values the hand-off
#: suggested (design repository, programme/ptv20-registration.md). Written
#: out, so a preset added without a decision about the book fails below.
BOOK_ON = {"pt-v20": dict(book_depth_coefficient=0.75, book_depth_exponent=0.5,
                          book_depth_reach=1.0, book_shared=1.0,
                          book_refill_half_life=27.0, book_resting=1.0,
                          fill_impact_coefficient=0.314)}


@pytest.mark.parametrize("preset", tuple(tf.preset_names()))
def test_every_dial_ships_off(preset):
    values = tf.ModelParams.from_preset(preset).to_dict()
    if preset in BOOK_ON:
        assert {name: values[name] for name in DIALS} == BOOK_ON[preset], preset
        return
    assert all(values[name] == 0.0 for name in DIALS), preset


def test_an_untraded_market_is_the_same_at_any_setting_of_the_dials():
    """The dials are read only on an agent's path. The model's own flow
    settles through the maker's ladder at any setting, so three untraded
    days are the same market to the bit, every column and every draw."""
    runs = []
    for model in (None, live()):
        e = tf.Engine(seed=7, universe=ROSTER, model=model)
        for _ in range(3):
            e.open_market()
            for k in range(6):
                e.run_session(*session_clock((9, 30, 3), k, 65), 65)
            e.close_market()
        snap = e.state_snapshot()
        # The generator states as bits: a spare that is absent reads as a
        # NaN, and a NaN is never equal to itself.
        rng = b"".join(struct.pack("<d", v) for v in snap["rng"])
        runs.append((snap["columns"], rng, e.draws_consumed))
    assert runs[0] == runs[1]


def test_with_the_dials_off_an_agent_trades_as_it_always_did():
    """No dial on, which is pt-v19: `Portfolio.execute` prices off a
    snapshot of the book, nothing reaches the engine's book, the log carries
    no order and the snapshot no book, so every traded run is the run it
    was."""
    e = warmed(OFF)
    assert not e.book_live
    p = tf.Portfolio(cash=1e7)
    fill = p.execute(e, ROSTER[0].ticker, 1_000)
    assert fill["quantity"] == 1_000
    assert p.pending_flow() == {ROSTER[0].ticker: (1_000.0, 0.0)}
    assert not any(x["op"] in ("submit", "take_fills") for x in e.order_log)
    assert "book" not in e.state_snapshot()


def test_the_depth_alone_leaves_execution_where_it_was():
    """The latent depth on and the book not shared, on pt-v19: the
    portfolio still prices off a snapshot, and the snapshot now has depth to
    price size against. The flow still travels through `fills`."""
    e = warmed(tf.ModelParams.from_preset(OFF, book_depth_coefficient=0.75))
    assert not e.book_live
    i = index_by_volume(ROSTER, "thinnest")
    t = ROSTER[i].ticker
    q = round(0.3 * ROSTER[i].avg_volume)
    p = tf.Portfolio(cash=1e9)
    fill = p.execute(e, t, q)
    assert fill["quantity"] == q and not fill["partial"]
    assert p.pending_flow() == {t: (float(q), 0.0)}


# -- price for size --------------------------------------------------------------


def test_an_order_past_the_ladder_walks_deeper_levels_instead_of_being_cut_off():
    """Off (pt-v19), the maker's ten levels hold a few percent of daily
    volume and a larger order fills what they hold. On, the same order fills
    in full, at an average that rises with size."""
    off, on = warmed(OFF), warmed(live())
    for which in ("thinnest", "thickest"):
        i = index_by_volume(ROSTER, which)
        t = ROSTER[i].ticker
        v = ROSTER[i].avg_volume
        cut = off.book(t).sweep_cost("buy", round(0.3 * v))
        assert cut.filled < 0.2 * v, (which, cut.filled / v)
        last = 0.0
        for f in (0.01, 0.03, 0.1, 0.3, 1.0):
            q = round(f * v)
            c = on.book(t).sweep_cost("buy", q)
            assert c.filled == q, (which, f, c.filled)
            assert c.average_price > last, (which, f)
            last = c.average_price


def test_the_price_of_size_follows_the_square_root_law():
    """Past the ladder the book IS the law: the worst price reached, measured
    from the touch, grows as the square root of size. Fitted on every name
    of the roster from 10% to 100% of daily volume, where the latent pool
    holds nearly all the depth, the exponent is 0.5 to within the grid; and
    quadrupling the size doubles the distance. (Below that the maker's
    ladder adds depth near the touch, which only makes size cheaper.)"""
    e = warmed(live())
    exponents = []
    for i, t in enumerate(e.tickers):
        v = ROSTER[i].avg_volume
        book = e.book(t)
        touch = book.best_ask
        xs, ys = [], []
        for f in (0.1, 0.25, 0.5, 1.0):
            c = book.sweep_cost("buy", round(f * v))
            xs.append(math.log(f))
            ys.append(math.log(c.worst_price / touch - 1.0))
        mx, my = statistics.fmean(xs), statistics.fmean(ys)
        exponents.append(sum((x - mx) * (y - my) for x, y in zip(xs, ys))
                         / sum((x - mx) ** 2 for x in xs))
        near = book.sweep_cost("buy", round(0.25 * v)).worst_price / touch - 1.0
        far = book.sweep_cost("buy", round(1.0 * v)).worst_price / touch - 1.0
        assert 1.7 < far / near < 2.3, (t, far / near)
    assert 0.45 < statistics.median(exponents) < 0.58, exponents


def test_the_exponent_and_the_coefficient_are_dials():
    e05, e06 = warmed(live()), warmed(live(book_depth_exponent=0.6))
    e_hi = warmed(live(book_depth_coefficient=1.5))
    i = index_by_volume(ROSTER, "thinnest")
    t, v = ROSTER[i].ticker, ROSTER[i].avg_volume
    at = lambda e, f: e.book(t).sweep_cost("buy", round(f * v)).worst_price  # noqa: E731
    touch = e05.book(t).best_ask
    # The coefficient scales the distance from the touch; the exponent
    # bends it (a larger exponent is cheaper below the reach, where f < 1).
    assert at(e_hi, 0.5) - touch > 1.8 * (at(e05, 0.5) - touch)
    assert at(e06, 0.1) < at(e05, 0.1)


# -- consumption and refill -----------------------------------------------------


def test_a_second_agent_meets_the_book_the_first_left():
    """Shared: the second buy of the same size in the same instant pays
    more than the first, and in a fork without the first it pays what the
    first paid, to the bit."""
    base = warmed(live())
    t, v = ROSTER[0].ticker, ROSTER[0].avg_volume
    q = round(0.05 * v)
    (both,) = base.fork(1)
    a = both.submit("a", t, q)
    b = both.submit("b", t, q)
    assert b["average_price"] > a["average_price"]
    assert b["reference"] > a["reference"], "the ask a took lifted the mid"
    (alone,) = base.fork(1)
    solo = alone.submit("b", t, q)
    assert solo["average_price"] == a["average_price"]


def test_off_the_shared_book_nothing_is_consumed():
    base = warmed(live(book_shared=0.0, book_refill_half_life=0.0))
    assert base.book_live, "resting orders still execute in the engine"
    t, v = ROSTER[0].ticker, ROSTER[0].avg_volume
    a = base.submit("a", t, round(0.05 * v))
    b = base.submit("b", t, round(0.05 * v))
    assert a["average_price"] == b["average_price"]


def test_the_ladder_refills_at_the_next_tick_and_the_depth_at_its_half_life():
    """One buy of 30% of daily volume takes the maker's ask and latent
    depth behind it. The maker re-quotes at the next tick, so its row is
    zero from then; the latent row decays by exactly half every 27 ticks."""
    e = warmed(live())
    i = index_by_volume(ROSTER, "thickest")
    e.submit("a", ROSTER[i].ticker, round(0.3 * ROSTER[i].avg_volume))
    row0 = taken(e, i)
    assert row0[1] > 0 and row0[3] > 0 and row0[0] == 0 and row0[2] == 0
    assert row0[4] < 0, "the maker sold, so its pending inventory is short"
    e.run_session(10, 35, 3, 1)
    row1 = taken(e, i)
    assert row1[1] == 0.0 and row1[4] == 0.0
    e.run_session(10, 36, 3, 27)
    row28 = taken(e, i)
    assert row28[3] / row1[3] == pytest.approx(0.5, rel=1e-12)


def test_a_large_orders_temporary_impact_decays_and_its_permanent_impact_stays():
    """Two forks, one with a buy of 10% of daily volume and one without,
    run the same ticks on the same draws. The extra a second buy pays over
    its own book's mid decays with the refill; the difference in `s` is
    the linear law's, and it is still there two steps later."""
    base = warmed(live())
    i = index_by_volume(ROSTER, "thickest")
    t, v = ROSTER[i].ticker, ROSTER[i].avg_volume
    q = round(0.1 * v)
    extra, perm = {}, {}
    for k in (1, 30, 130):
        out = []
        for trade in (True, False):
            (e,) = base.fork(1)
            if trade:
                e.submit("a", t, q)
            e.run_session(10, 35, 3, k)
            book = e.book(t)
            c = book.sweep_cost("buy", q)
            out.append((c.average_price / book.mid_price - 1.0,
                        f64(e.column("mispricing_s"))[i]))
        extra[k] = (out[0][0] - out[1][0]) * 1e4
        perm[k] = (out[0][1] - out[1][1]) * 1e4
    assert extra[1] > extra[30] > extra[130]
    assert extra[130] < 0.25 * extra[1], extra
    assert perm[1] > 0.5 and abs(perm[130] / perm[1] - 1.0) < 0.01, perm


# -- impact, and who caused it ---------------------------------------------------


def test_the_permanent_impact_is_linear_and_attributed_to_each_agent():
    """Under `fill_impact_coefficient` each agent's permanent impact is
    `gamma sigma net / V`: twice the size is twice the impact, two agents'
    impacts add, and the name's order-flow attribution on the tick they
    land is their sum."""
    e = warmed(live())
    e.close_market()
    e.open_market()
    t = ROSTER[3].ticker
    q = round(0.02 * ROSTER[3].avg_volume)
    e.submit("a", t, q)
    e.submit("b", t, 2 * q)
    e.submit("c", t, -q)
    e.run_session(9, 30, 3, 1)
    rows = {r["agent"]: r for r in e.take_impacts()}
    assert rows["b"]["permanent"] == pytest.approx(2 * rows["a"]["permanent"], rel=1e-12)
    assert rows["c"]["permanent"] == pytest.approx(-rows["a"]["permanent"], rel=1e-12)
    flow = f64(e.attribution("order_flow_impact"))[3]
    assert flow == pytest.approx(sum(r["permanent"] for r in rows.values()), rel=1e-12)


def test_under_the_imbalance_law_the_tick_is_shared_pro_rata():
    e = warmed(live(fill_impact_coefficient=0.0))
    e.close_market()
    e.open_market()
    t = ROSTER[3].ticker
    q = round(0.02 * ROSTER[3].avg_volume)
    e.submit("a", t, q)
    e.submit("b", t, 3 * q)
    e.run_session(9, 30, 3, 1)
    rows = {r["agent"]: r for r in e.take_impacts()}
    assert rows["b"]["permanent"] == pytest.approx(3 * rows["a"]["permanent"], rel=1e-12)
    flow = f64(e.attribution("order_flow_impact"))[3]
    assert flow == pytest.approx(rows["a"]["permanent"] + rows["b"]["permanent"], rel=1e-9)


@pytest.mark.parametrize("f", (0.001, 0.01, 0.1, 0.3, 1.0))
def test_a_fill_pays_at_least_its_own_permanent_impact(f):
    """The fair-pricing condition of `test_agent_flow.py`, at the suggested
    dials and at every size the depth reaches: a buy's premium over the mid
    is never below the change its own flow makes to `s`."""
    base = warmed(live())
    for i, t in enumerate(base.tickers):
        (e,) = base.fork(1)
        q = max(1.0, round(f * ROSTER[i].avg_volume))
        r = e.submit("a", t, q)
        e.run_session(10, 35, 3, 1)
        (row,) = e.take_impacts()
        premium = math.log(r["average_price"] / r["reference"])
        assert premium >= row["permanent"], (t, f, premium, row["permanent"])


# -- no arbitrage -----------------------------------------------------------------

SIZES = (0.0, 0.01, 0.02, 0.05, 0.1, 0.3, 1.0)   # of daily volume; 0.0 is one share


def test_a_round_trip_in_one_instant_always_loses():
    """Buy, then sell the same shares before any tick: the sell meets a bid
    the buy never touched, so the trip pays the spread and both walks.
    Every name, every size, one share to a whole day's volume."""
    base = warmed(live())
    for i, t in enumerate(base.tickers):
        for f in SIZES:
            (e,) = base.fork(1)
            q = 1.0 if f == 0 else max(1.0, round(f * ROSTER[i].avg_volume))
            buy = e.submit("a", t, q)
            sell = e.submit("a", t, -buy["filled"])
            assert sell["filled"] == buy["filled"]
            pnl = sell["average_price"] - buy["average_price"]
            assert pnl < 0, (t, f, pnl)


def _round_trip(base: tf.Engine, i: int, q: float):
    """Buy at a step, sell at the next, in the engine's book; and the same
    trip priced off the untouched market's book without sending anything.
    Returns the gain from the market feeling the trip, its cost against the
    untouched prints and the trip's permanent impact, all in bp."""
    t = base.tickers[i]
    (bare,) = base.fork(1)
    p0 = f64(bare.prices())[i]
    c0 = bare.book(t).sweep_cost("buy", q)
    bare.run_session(10, 35, 3, 65)
    p1 = f64(bare.prices())[i]
    c1 = bare.book(t).sweep_cost("sell", c0.filled)
    (fed,) = base.fork(1)
    r0 = fed.submit("a", t, q)
    fed.run_session(10, 35, 3, 65)
    r1 = fed.submit("a", t, -r0["filled"])
    if not (c0.filled == r0["filled"] == r1["filled"] == c1.filled):
        return None
    notional = c0.filled * c0.average_price
    bare_pnl = c0.filled * (c1.average_price - c0.average_price)
    fed_pnl = (sum(f["quantity"] * f["price"] for f in r1["fills"])
               - sum(f["quantity"] * f["price"] for f in r0["fills"]))
    cost = ((c0.average_price - p0) + (p1 - c1.average_price)) * c0.filled
    permanent = sum(r["permanent"] for r in fed.take_impacts()
                    if r["bought"] > 0)
    return ((fed_pnl - bare_pnl) / notional * 1e4, cost / notional * 1e4,
            permanent * 1e4)


def _trips(model) -> list[tuple[float, float, float, float]]:
    rows = []
    for useed in (93001, 111):
        u = tf.Universe.random(20, seed=useed)
        base = warmed(model, universe=u, seed=useed + 7)
        for i in range(len(u)):
            for f in SIZES:
                q = 1.0 if f == 0 else max(1.0, round(f * u[i].avg_volume))
                r = _round_trip(base, i, q)
                if r is not None:
                    rows.append((f, *r))
    return rows


@pytest.fixture(scope="module")
def trips():
    return _trips(live())


def test_no_round_trip_profits_from_its_own_impact(trips):
    """Buy, sell a step later, on every name of two 20-name rosters at one
    share and at 1% to 100% of daily volume: 280 round trips, each against
    the same trip priced off the untouched market.

    Two statements, because the gain has two parts.

    What the trip leaves the market systematically never reaches its cost,
    in any single trip: its permanent impact, the change to `s` its own
    fills made, is below what the trip paid the book. The maker's skew from
    the inventory the buy left it is the other systematic part, and it is
    bounded by a fraction of a half-spread.

    The gain as realised also carries the tape's noise. A moved model price
    changes which way the settlement's draws fall, and the maker quotes
    around the print those draws leave, so two markets a basis point apart
    in `s` print tens of bp apart a step later, either way. So a single
    trip can come out ahead of its cost by chance, and some do at the small
    sizes, where the cost is a spread. On average they never do: at every
    size the mean gain is under half the mean cost.

    Measured on this build, mean gain against mean cost in bp: 0.05 against
    16 at one share, 4.4 against 20 at 1% of daily volume, 7.6 against 42
    at 5%, 7.2 against 59 at 10%, 64 against 183 at a whole day's volume.
    16 of the 280 trips gained more than they paid, all at 10% or less;
    the permanent part never did."""
    for f, gain, cost, permanent in trips:
        assert permanent < cost, (f, permanent, cost)
    for f in SIZES:
        at = [r for r in trips if r[0] == f]
        assert len(at) >= 30, (f, len(at))
        gain = statistics.fmean(r[1] for r in at)
        cost = statistics.fmean(r[2] for r in at)
        assert gain < 0.5 * cost, (f, gain, cost)
    assert all(r[0] <= 0.1 for r in trips if r[1] > r[2])


def test_the_test_can_fail():
    """Non-vacuity. At the dial's ceiling, a permanent impact of five sigma
    per day's volume, the trips at a tenth of volume and more profit from
    their own impact on average. A test the manipulable market passed would
    prove nothing."""
    rows = _trips(live(fill_impact_coefficient=5.0))
    for f in (0.1, 0.3, 1.0):
        at = [r for r in rows if r[0] == f]
        assert statistics.fmean(r[1] for r in at) > statistics.fmean(r[2] for r in at)


def test_the_linear_law_makes_impact_independent_of_how_an_order_is_split():
    """Sixty one-share buys, one a step, against one buy of sixty shares at
    the first of the same sixty steps, on the thinnest name.

    Under the imbalance law a one-share order carries the floor of 0.2, so
    the sixty small buys lift `s` many times more than the one block of the
    same size: splitting an order multiplies its permanent impact, which is
    the concave permanent law Huberman and Stanzl (2004) show admits a
    manipulation (buy in small pieces, sell in one). Under the linear law
    the two lift it by the same amount, to within what `s`'s own herding
    and reversion do differently to a lift that arrives at once and one
    that arrives over ten days, because impact is additive in size.
    Measured: 65 times the block's lift under the imbalance law, 1.09
    times under the linear one."""
    i = index_by_volume(ROSTER, "thinnest")
    t = ROSTER[i].ticker
    ratio = {}
    for law, gamma in (("imbalance", 0.0), ("linear", 0.314)):
        base = warmed(live(fill_impact_coefficient=gamma))
        lift = {}
        for schedule in ("pieces", "block", "none"):
            (e,) = base.fork(1)
            for step in range(60):
                if schedule == "pieces":
                    e.submit("a", t, 1.0)
                elif schedule == "block" and step == 0:
                    e.submit("a", t, 60.0)
                e.run_session(10, 35, 3, 65)
            lift[schedule] = f64(e.column("mispricing_s"))[i]
        ratio[law] = (lift["pieces"] - lift["none"]) / (lift["block"] - lift["none"])
    assert ratio["imbalance"] > 5.0, ratio
    assert abs(ratio["linear"] - 1.0) < 0.15, ratio


# -- the queue --------------------------------------------------------------------


def _touch_order(e, t, agent, size, side="sell"):
    book = e.book(t)
    price = book.best_ask if side == "sell" else book.best_bid
    return e.submit(agent, t, -size if side == "sell" else size,
                    limit_price=price), price


def test_a_resting_order_queues_behind_the_maker_and_fills_partially():
    """A sell resting at the maker's best ask waits behind everything
    already at that price. A buy of that much plus 200 takes all of it and
    200 of the agent's, at the agent's price, and both sides of that fill
    are reported."""
    e = warmed(live())
    t = ROSTER[0].ticker
    r, price = _touch_order(e, t, "b", 500)
    assert r["mode"] == "queue" and r["resting"] == 500
    level = e.book(t).price_levels("sell", 1)[0]
    assert level.price == price and level.orders >= 2
    ahead = level.quantity - 500
    buy = e.submit("a", t, ahead + 200, limit_price=price)
    assert buy["fills"][-1]["counterparty"] == "b", "the agent is last in the queue"
    from_b = [f for f in buy["fills"] if f["counterparty"] == "b"]
    assert sum(f["quantity"] for f in from_b) == 200
    assert all(f["price"] == price for f in from_b)
    (order,) = e.open_orders("b")
    assert order["remaining"] == 300
    (fill,) = e.take_fills("b")
    assert fill["liquidity"] == "maker" and fill["counterparty"] == "a"
    assert fill["quantity"] == 200 and fill["side"] == "sell"


def test_at_an_equal_price_the_earlier_order_fills_first():
    e = warmed(live())
    t = ROSTER[0].ticker
    _, price = _touch_order(e, t, "b", 300)
    _touch_order(e, t, "c", 300)
    level = e.book(t).price_levels("sell", 1)[0]
    e.submit("a", t, level.quantity - 300, limit_price=price)
    assert e.open_orders("b") == []
    assert e.open_orders("c")[0]["remaining"] == 300
    e.submit("a", t, 100, limit_price=price)
    assert e.open_orders("c")[0]["remaining"] == 200


def widest(engine: tf.Engine) -> int:
    """The name whose quoted spread is widest in cents: the one with room
    for an order strictly inside it."""
    def cents(i):
        book = engine.book(engine.tickers[i])
        return book.best_ask - book.best_bid
    return max(range(len(engine.tickers)), key=cents)


def test_the_models_flow_fills_a_resting_order_at_its_price_in_parts():
    """A buy resting a cent inside the spread is the best bid. The model's
    own sell flow reaches it first, one slice at a time, at its price."""
    e = warmed(live())
    i = widest(e)
    t = e.tickers[i]
    book = e.book(t)
    assert book.best_ask - book.best_bid >= 0.03
    price = round(book.best_bid + 0.01, 2)
    size = round(0.01 * ROSTER[i].avg_volume)
    r = e.submit("a", t, size, limit_price=price)
    assert r["filled"] == 0 and r["resting"] == size
    fills = []
    for k in range(65):
        e.run_session(10, 35 + k % 25, 3, 1)
        fills += e.take_fills("a")
        if fills:
            break
    assert fills, "the flow never reached a bid inside the spread"
    first = fills[0]
    assert first["counterparty"] == "flow" and first["liquidity"] == "maker"
    assert first["price"] == price and first["side"] == "buy"
    assert first["quantity"] < size, "the first slice filled it only in part"


def test_a_standing_bid_at_the_ask_takes_the_makers_requote_and_pays_for_it():
    """A buy left resting at the price the maker asks crosses the maker's
    re-quote at the next tick. That is taking liquidity: the fills are the
    agent's as taker, against the maker, and their flow reaches `s` on the
    tick after, attributed to the agent."""
    e = warmed(live())
    i = index_by_volume(ROSTER, "thickest")
    t = ROSTER[i].ticker
    ask = e.book(t).best_ask
    first = e.submit("a", t, round(0.05 * ROSTER[i].avg_volume), limit_price=ask)
    assert first["resting"] > 0
    e.take_fills()            # the order's own fills on arrival
    e.run_session(10, 35, 3, 1)
    e.take_impacts()          # ... whose flow the first tick applied
    crossed = [f for f in e.take_fills("a") if f["liquidity"] == "taker"]
    assert crossed and all(f["counterparty"] == "mm" and f["price"] <= ask
                           for f in crossed)
    e.run_session(10, 36, 3, 1)
    impacts = [r for r in e.take_impacts("a") if r["ticker"] == t]
    assert impacts and impacts[-1]["bought"] == sum(f["quantity"] for f in crossed)


def test_a_cancelled_order_leaves_the_book():
    e = warmed(live())
    t = ROSTER[0].ticker
    r, price = _touch_order(e, t, "b", 500)
    assert e.cancel(r["order_id"], agent="b")
    assert e.open_orders() == []
    assert all(l.orders == 1 or l.price != price
               for l in e.book(t).price_levels("sell", 3))
    level = e.book(t).price_levels("sell", 1)[0]
    buy = e.submit("a", t, level.quantity + 10, limit_price=price)
    assert all(f["counterparty"] != "b" for f in buy["fills"])
    assert not e.cancel(r["order_id"])


def test_off_a_limit_waits_for_the_traded_range_outside_the_book():
    """`book_resting` off: the hosted service's convention. The remainder
    is not in the book, so the book is what it would be without it; it
    fills in full, at its limit, on the first tick whose print reaches it,
    and not before."""
    base = warmed(live(book_resting=0.0))
    i = index_by_volume(ROSTER, "thickest")
    t = ROSTER[i].ticker
    limit = round(f64(base.prices())[i] * 0.9995, 2)
    (e,) = base.fork(1)
    r = e.submit("a", t, 1_000, limit_price=limit)
    assert r["mode"] == "range" and r["filled"] == 0 and r["resting"] == 1_000
    assert e.book(t).depth("buy") == base.book(t).depth("buy")
    for k in range(390):
        e.run_session(10, 35 + k % 25, 3, 1)
        fills = e.take_fills("a")
        reached = f64(e.prices())[i] <= limit
        if not reached:
            assert fills == [] and e.open_orders("a")[0]["remaining"] == 1_000
            continue
        (fill,) = fills
        assert fill["price"] == limit and fill["quantity"] == 1_000
        assert fill["liquidity"] == "range" and fill["counterparty"] == "range"
        assert e.open_orders("a") == []
        break
    else:
        pytest.fail("no print reached a limit five bp under the market in a day")


def test_two_agents_cross_at_the_resting_price():
    """A buy rests above the maker's bid; a sell with a lower limit trades
    against it at the RESTING price, the seller's price improvement, and
    each side's fill names the other."""
    e = warmed(live())
    t = ROSTER[0].ticker
    book = e.book(t)
    price = round(book.best_bid + 0.02, 2)
    e.submit("a", t, 400, limit_price=price)
    sell = e.submit("b", t, -250, limit_price=round(book.best_bid - 0.05, 2))
    assert sell["fills"][0]["counterparty"] == "a"
    assert sell["fills"][0]["price"] == price
    (af,) = e.take_fills("a")
    assert af["side"] == "buy" and af["quantity"] == 250 and af["counterparty"] == "b"


# -- several agents ---------------------------------------------------------------


def test_submit_many_orders_a_step_by_agent_then_list_position():
    base = warmed(live())
    t, q = ROSTER[0].ticker, round(0.03 * ROSTER[0].avg_volume)
    (x,) = base.fork(1)
    (y,) = base.fork(1)
    out_x = x.submit_many([{"agent": "b", "ticker": t, "quantity": q},
                           {"agent": "a", "ticker": t, "quantity": q},
                           {"agent": "a", "ticker": t, "quantity": -q // 2}])
    out_y = y.submit_many([{"agent": "a", "ticker": t, "quantity": q},
                           {"agent": "a", "ticker": t, "quantity": -q // 2},
                           {"agent": "b", "ticker": t, "quantity": q}])
    assert [r["agent"] for r in out_x] == ["a", "a", "b"]
    assert x.state_hash() == y.state_hash()


class Buyer:
    def __init__(self, ticker, shares):
        self.ticker, self.shares = ticker, shares

    def act(self, obs):
        return {self.ticker: self.shares}

    def fork(self):
        return Buyer(self.ticker, self.shares)


def test_a_cohort_takes_levels_from_each_other():
    """Two agents buying one name every step. Off the shared book (pt-v19)
    they fill at the same price; on it the second in label order pays more, and each
    one's permanent impact is its own."""
    u = tf.Universe.random(8, seed=99)
    t, q = u[0].ticker, round(0.03 * u[0].avg_volume)
    prices = {}
    for name, model in (("off", OFF), ("live", live())):
        w = World(seed=42, universe=u, model=model, cash=1e9, max_leverage=None,
                  agents={"a": Buyer(t, q), "b": Buyer(t, q)})
        w.run(days=1)
        row = w.trace[0]["agents"]
        prices[name] = (row["a"]["fills"][0]["price"], row["b"]["fills"][0]["price"])
    assert prices["off"][0] == prices["off"][1]
    assert prices["live"][1] > prices["live"][0]


def test_externalities_show_agents_taking_levels_from_each_other():
    """`levels[a][b]`: what b's execution cost against each step's opening
    mid changes by when a stops trading. Off the shared book (pt-v19) it
    is zero; on it, a (first in the arrival order) makes b's fills dearer by
    the levels it takes, and b barely reaches a, which met the book first."""
    u = tf.Universe.random(8, seed=99)
    t, q = u[0].ticker, round(0.03 * u[0].avg_volume)
    out = {}
    for name, model in (("off", OFF), ("live", live())):
        w = World(seed=42, universe=u, model=model, cash=1e9, max_leverage=None,
                  agents={"a": Buyer(t, q), "b": Buyer(t, q)})
        out[name] = tf.externalities(w, days=1)
    off, on = out["off"], out["live"]
    assert not off.live and on.live
    assert off.levels["a"]["b"] == 0.0 and off.levels["b"]["a"] == 0.0
    # 33,036 against 3,548 (9.3x) since pt-v20's volume response went to 0.6;
    # about 10x before. The claim is the asymmetry, not its exact size.
    assert on.levels["a"]["b"] > 5 * abs(on.levels["b"]["a"]) > 0
    assert "levels taken" in on.render() and "levels taken" not in off.render()
    json.dumps(on.as_dict())


class Quoter:
    """Joins the best bid on its first step, then waits."""

    def __init__(self, ticker, shares, done=False):
        self.ticker, self.shares, self.done = ticker, shares, done

    def act(self, obs):
        if self.done:
            return {}
        self.done = True
        bid = obs.engine.book(self.ticker).best_bid
        return {self.ticker: tf.Limit(self.shares, bid)}

    def fork(self):
        return Quoter(self.ticker, self.shares, self.done)


def test_a_world_agent_can_rest_a_limit_order_and_is_filled_by_the_market():
    """An agent's `act()` returns a `tf.Limit` joining the bid. It rests,
    the market's flow fills it during the day's sessions, the fills reach
    the portfolio and the trace rows record them under `book_fills`."""
    u = tf.Universe.random(8, seed=99)
    probe = warmed(live(), universe=u, seed=42)
    t = u[widest(probe)].ticker
    w = World(seed=42, universe=u, model=live(), agent=Quoter(t, 2_000))
    w.run(days=2)
    first = w.trace[0]
    assert first["fills"][0]["limit"] and first["fills"][0]["resting"] == 2_000
    got = [f for row in w.trace for f in row.get("book_fills", [])]
    assert got and all(f["liquidity"] in ("maker", "taker") for f in got)
    held = w.portfolio.positions[t].quantity
    assert held == sum(f["quantity"] for f in got)


def test_a_removed_agents_waiting_orders_go_with_it():
    u = tf.Universe.random(8, seed=99)
    t = u[0].ticker
    w = World(seed=42, universe=u, model=live(),
              agents={"q": Quoter(t, 500), "b": Buyer(t, 10)})
    w.run(days=1)
    w.engine.submit("q", t, 1, limit_price=1.0)   # a far bid, left waiting
    w.portfolios["q"]._in_book = True
    arm = w.without("q")
    assert arm.engine.open_orders("q") == []
    assert w.engine.open_orders("q")


# -- determinism ------------------------------------------------------------------


def _scripted(engine: tf.Engine) -> tf.Engine:
    """A fixed day of orders: takers, resting orders, a cancel, several
    agents, sessions between them."""
    t0, t1 = engine.tickers[0], engine.tickers[5]
    engine.submit("a", t0, 4_000)
    r = engine.submit("b", t0, -1_500, limit_price=engine.book(t0).best_ask)
    engine.submit("c", t1, 2_000, limit_price=engine.book(t1).best_bid)
    engine.run_session(10, 35, 3, 65)
    engine.take_fills("c")
    engine.cancel(r["order_id"], agent="b")
    engine.submit_many([{"agent": "b", "ticker": t1, "quantity": -800},
                        {"agent": "a", "ticker": t0, "quantity": -4_000}])
    engine.run_session(11, 40, 3, 65)
    return engine


def test_the_same_seeds_and_orders_give_the_same_state_hash():
    one = _scripted(warmed(live()))
    two = _scripted(warmed(live()))
    assert one.state_hash() == two.state_hash()
    other = warmed(live())
    other.submit("a", other.tickers[0], 4_001)
    assert other.state_hash() != warmed(live()).state_hash()


def test_a_replay_reproduces_the_book():
    e = _scripted(warmed(live()))
    again = tf.replay(json.loads(json.dumps(e.order_log)), seed=92001,
                      universe=ROSTER, model=live())
    assert again.state_hash() == e.state_hash()
    assert again.open_orders() == e.open_orders()
    assert again.take_fills() == e.take_fills()


def test_a_fork_carries_the_book_and_continues_like_the_parent():
    e = warmed(live())
    e.submit("b", e.tickers[0], -1_500, limit_price=e.book(e.tickers[0]).best_ask)
    e.submit("a", e.tickers[2], 3_000)
    (f,) = e.fork(1)
    assert f.state_hash() == e.state_hash()
    for x in (e, f):
        x.run_session(10, 35, 3, 65)
        x.submit("c", x.tickers[0], 2_000)
    assert f.state_hash() == e.state_hash()


def test_a_snapshot_carries_the_book_and_restores_it():
    """The snapshot carries the book once it has been used, the Python twin
    of the state hash covers it byte for byte, and an engine restored from
    it continues exactly as the one it was taken from."""
    e = _scripted(warmed(live()))
    snap = e.state_snapshot()
    assert "book" in snap
    assert manifest.state_hash(snap) == e.state_hash()
    fresh = tf.Engine(seed=92001, universe=ROSTER, model=live())
    fresh.restore_state(snap)
    assert fresh.state_hash() == e.state_hash()
    for x in (e, fresh):
        x.run_session(12, 45, 3, 65)
        x.submit("a", x.tickers[0], 500)
    assert fresh.state_hash() == e.state_hash()
    # And a restore from a snapshot without a book clears one.
    clean = warmed(live()).state_snapshot()
    fresh.restore_state(clean)
    assert fresh.open_orders() == [] and "book" not in fresh.state_snapshot()


def test_refused_orders_change_nothing():
    e = warmed(live())
    before = (e.state_hash(), len(e.order_log))
    for bad in (dict(agent="mm", ticker=e.tickers[0], quantity=5),
                dict(agent="a", ticker="NOPE", quantity=5),
                dict(agent="a", ticker=e.tickers[0], quantity=0.0),
                dict(agent="a", ticker=e.tickers[0], quantity=5, limit_price=-1.0)):
        with pytest.raises((tf.ValidationError, tf.OrderError)):
            e.submit(bad.pop("agent"), bad.pop("ticker"), bad.pop("quantity"), **bad)
    assert (e.state_hash(), len(e.order_log)) == before


# -- every harness, on a live book -----------------------------------------------


class BuyOnce:
    def __init__(self, ticker, shares, done=False):
        self.ticker, self.shares, self.done = ticker, shares, done

    def act(self, obs):
        if self.done:
            return {}
        self.done = True
        return {self.ticker: self.shares}

    def fork(self):
        return BuyOnce(self.ticker, self.shares, self.done)


def test_evaluate_trades_through_the_live_book_and_counts_the_flow_once():
    """`tf.evaluate` on a live book: the agent's order executes in the
    engine under its portfolio's owner, the harness's `fills=` carries
    nothing for it, and the engine applies the flow once, on the next tick.
    A buy of 30% of daily volume fills in full, where the ladder alone, on
    pt-v19, would have cut it off."""
    i = index_by_volume(ROSTER, "thickest")
    t, q = ROSTER[i].ticker, round(0.3 * ROSTER[i].avg_volume)
    live_card = tf.evaluate({"a": BuyOnce(t, q)}, seed=92001, universe=ROSTER,
                            days=1, model=live(), max_leverage=None,
                            cash=1e12)["a"]
    off_card = tf.evaluate({"a": BuyOnce(t, q)}, seed=92001, universe=ROSTER,
                           days=1, model=OFF, max_leverage=None,
                           cash=1e12)["a"]
    assert live_card.trades == off_card.trades == 1
    assert live_card.turnover > 2 * off_card.turnover, "size was priced, not cut"
    # The dials on the default's base are pt-v20 itself. On pt-v19's base,
    # the default until 0.8.5, they fingerprinted as "custom-".
    assert live_card.model_fingerprint == "pt-v20"


def test_a_counterfactual_world_on_a_live_book_logs_its_orders_and_replays():
    w = World(seed=92001, universe=ROSTER, model=live(),
              agent=BuyOnce(ROSTER[4].ticker, 5_000))
    w.run(days=1)
    ops = [x["op"] for x in w.engine.order_log]
    assert "submit" in ops
    sessions = [x for x in w.engine.order_log if x["op"] == "run_session"]
    assert all(not x["fills"] for x in sessions), "the engine applied the flow"
    again = tf.replay(json.loads(json.dumps(w.engine.order_log)), seed=92001,
                      universe=ROSTER, model=live())
    assert again.state_hash() == w.engine.state_hash()
