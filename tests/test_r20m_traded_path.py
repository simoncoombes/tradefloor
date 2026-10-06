"""The traded path on R20M: order-flow laws, and the metaorder memory's checks.

R20M is the r21 vector on pt-v20 (`test_state_schema_r21.R20M`), here with
its prehistory cut to five sessions so each engine builds in a second. None
of what is checked here reads the prehistory.

Order flow (`order_flow_depth_law`, `order_flow_impact_law`, issues #182 and
#166). R20M prices an agent's fills through the linear law
(`fill_impact_coefficient` 0.15), so the order-flow channel carries only
flow sent with `flow_per_tick` or `tick(order_flow=...)`:

- with both laws on and the coefficient restated, an untraded run and a run
  where an agent trades through the book are the same to the bit, so no row
  that reads agents' orders (the metaorder rows, the book-path slope, the
  programme rows) can move;
- a session-long buy of a tenth of each name's daily volume moves every
  name, and its cost in daily sigma does not fall with dollar volume, where
  without the laws the thinnest names move hundreds of times as far as the
  deepest.

The metaorder memory (`impact_memory_*`) on R20M, as a parallel build that
used the same volume memory asked:

- one sweep of a fresh book costs the same with the memory on and off, so
  row C9 (`impact_curve.py`) cannot move;
- a block of 5% to 100% of a day's volume bought and sold back, either leg
  a block or twelve slices, loses on average, and none gains;
- the lift an order leaves falls from the close to the next close: no
  next-session rise for the decay rows to read;
- a fill between two agents feeds nothing: the memory stays empty and the
  price is the untraded twin's.
"""
from __future__ import annotations

import math
import pathlib
import statistics
import struct
import sys

import numpy as np
import pytest

import tradefloor as tf

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_state_schema_r21 import R20M as _R20M  # noqa: E402

R20M = dict(_R20M, market_prehistory_sessions=5.0)
#: Both order-flow laws on, the coefficient restated so the median certified
#: name moves about 0.15 of a daily sigma on the XN programme.
LAWS = dict(order_flow_depth_law=1.0, order_flow_impact_law=1.0, order_flow_coefficient=800.0)
MEMORY_OFF = dict(impact_memory_coefficient=0.0)

ROSTER = tf.Universe.random(12, seed=93001)


def f64(raw) -> np.ndarray:
    return np.frombuffer(raw, "<f8").copy()


def model(**over) -> tf.ModelParams:
    return tf.ModelParams.from_preset("pt-v20", **{**R20M, **over})


def run(e, t: int, n: int, close: bool = False) -> int:
    """`n` ticks from `t` ticks after the 09:30 open, the clock advancing."""
    if n > 0:
        m = 9 * 60 + 30 + t
        e.run_session(m // 60, m % 60, 3, n, close_at_end=close)
    elif close:
        e.close_market()
    return t + n


def day(e, flow=None) -> None:
    e.open_market()
    if flow:
        e.run_session(9, 30, 3, 390, close_at_end=True, flow_per_tick=flow)
    else:
        e.run_session(9, 30, 3, 390, close_at_end=True)
    e.close_market()


def warmed(params, universe=ROSTER, seed=92001, days=3):
    e = tf.Engine(seed=seed, universe=universe, model=params)
    closes = [f64(e.prices())]
    for _ in range(days):
        day(e)
        closes.append(f64(e.prices()))
    return e, np.diff(np.log(np.array(closes)), axis=0).std(axis=0)


# -- order flow ------------------------------------------------------------------


def test_the_laws_leave_an_untraded_run_alone():
    runs = []
    for params in (model(), model(**LAWS)):
        e, _ = warmed(params, seed=7)
        snap = e.state_snapshot()
        rng = b"".join(struct.pack("<d", v) for v in snap["rng"])
        runs.append((snap["columns"], rng, e.draws_consumed))
    assert runs[0] == runs[1]


def test_the_laws_leave_a_run_with_agents_in_the_book_alone():
    """R20M's fills go through the linear law, not the order-flow channel,
    so a day of sliced buys, a block sale and a resting bid prints the same
    tape and the same fills with the laws on."""
    out = []
    for params in (model(), model(**LAWS)):
        e, _ = warmed(params, seed=8, days=1)
        e.open_market()
        t = 0
        for k in range(12):
            e.submit("a", ROSTER[k].ticker, 0.01 * ROSTER[k].avg_volume)
            e.submit("b", ROSTER[(k + 5) % 12].ticker, -0.02 * ROSTER[k].avg_volume)
            e.submit("c", ROSTER[k].ticker, 100, limit_price=round(e.book(ROSTER[k].ticker).best_bid, 2))
            t = run(e, t, 10)
        run(e, t, 390 - t, True)
        e.close_market()
        day(e)
        out.append((e.prices(), [(f["agent"], f["ticker"], f["quantity"], f["price"]) for f in e.take_fills()]))
    assert out[0] == out[1]


def _xn(params, seed=4, f=0.1):
    """A session-long buy of `f` of each name's daily volume against a twin,
    on the certified roster after twenty sessions: impact in daily sigma and
    each name's dollar volume."""
    u = tf.Universe.random(40, seed=111)
    e, sig = warmed(params, universe=u, seed=seed, days=20)
    flow = {x.ticker: (x.avg_volume * f / 390.0, 0.0) for x in u}
    x, ctl = e.fork(2)
    day(x, flow)
    day(ctl)
    px = f64(ctl.prices())
    imp = np.log(f64(x.prices()) / px) / sig
    dv = np.array([x.avg_volume for x in u]) * px
    return imp, dv


def _slope(imp, dv):
    ok = imp > 0
    x, y = np.log(dv[ok]), np.log(imp[ok])
    return float(((x - x.mean()) * (y - y.mean())).sum() / ((x - x.mean()) ** 2).sum())


def test_with_the_laws_cost_in_sigma_does_not_fall_with_dollar_volume():
    """XN1's statistic on one seed: within a third either side of flat, and
    every name moved. XN3's level near 0.15 of a sigma."""
    imp, dv = _xn(model(**LAWS))
    assert (imp > 0).all()
    assert -1 / 3 < _slope(imp, dv) < 1 / 3
    assert 0.05 < float(np.median(imp)) < 0.4


def test_without_the_laws_the_thinnest_names_pay_for_their_depth_twice():
    """The same programme on R20M as it ships: impact falls as depth, a
    slope near minus one, and the median name barely moves."""
    imp, dv = _xn(model())
    assert _slope(imp, dv) < -0.6
    assert float(np.median(imp)) < 0.06


# -- the metaorder memory ---------------------------------------------------------


def test_one_sweep_of_a_fresh_book_is_the_same_with_the_memory_on():
    """Row C9 reads the book an immediate order meets with nothing traded
    before it. The memory is empty then, so every level of every name's
    book, and every sweep, is the same to the bit."""
    (on, _), (off, _) = warmed(model()), warmed(model(**MEMORY_OFF))
    for e in (on, off):
        e.open_market()
        run(e, 0, 65)
    for i, t in enumerate(on.tickers):
        a, b = on.book(t), off.book(t)
        for side in ("buy", "sell"):
            levels = [[(x.price, x.quantity, x.orders) for x in book.price_levels(side, 4096)]
                      for book in (a, b)]
            assert levels[0] == levels[1], (t, side)
            for f in (0.001, 0.01, 0.1, 1.0):
                q = f * ROSTER[i].avg_volume
                x, y = a.sweep_cost(side, q), b.sweep_cost(side, q)
                assert (x.filled, x.average_price, x.worst_price) == \
                    (y.filled, y.average_price, y.worst_price), (t, side, f)


def _trip(base, i, f, buys, sells, gap=5):
    """Buy `f` of volume in `buys` slices, then sell it all back in `sells`,
    `gap` ticks apart, from fifteen ticks after the open. The gain against
    the untraded twin's mid at each slice, over the notional: a trader
    whose orders moved nothing gains zero."""
    tk = ROSTER[i].ticker
    q = f * ROSTER[i].avg_volume
    e, ctl = base.fork(2)
    e.open_market()
    ctl.open_market()
    t = run(e, 0, 15)
    run(ctl, 0, 15)
    ref = ctl.book(tk).mid_price
    gain, held = 0.0, 0.0
    for k, x in enumerate([q / buys] * buys + [None] * sells):
        mid = ctl.book(tk).mid_price
        if x is None:
            x = -held / (sells - (k - buys))
        r = e.submit("a", tk, x)
        sign = 1.0 if x > 0 else -1.0
        gain -= sign * r["filled"] * (r["average_price"] - mid)
        held += sign * r["filled"]
        run(e, t, gap)
        t = run(ctl, t, gap)
    return gain / (q * ref)


@pytest.fixture(scope="module")
def trips():
    base, _ = warmed(model())
    return [(f, legs, _trip(base, i, f, *legs))
            for i in range(len(ROSTER))
            for f in (0.05, 0.3, 1.0)
            for legs in ((1, 1), (1, 12), (12, 1), (12, 12))]


def test_no_round_trip_gains_from_its_own_impact(trips):
    """`fill_impact_coefficient` 0.15 with the memory on: every size and
    schedule loses on average, and no trip comes out ahead."""
    for f in (0.05, 0.3, 1.0):
        for legs in ((1, 1), (1, 12), (12, 1), (12, 12)):
            at = [g for x, lg, g in trips if x == f and lg == legs]
            assert statistics.fmean(at) < 0.0, (f, legs, at)
    assert max(g for *_, g in trips) < 0.0


def test_the_lift_falls_from_the_close_to_the_next_close():
    """A half-day buy of 20% of volume in 18 slices (the Q rows' half-day
    schedule). The crowd chases the day's move in `s`, which could lift the
    price further the session after an order; on R20M the lift falls from
    the close to the next close, and again to the one after, on the roster
    mean.

    What falls is the memory's own decay (the order-flow slot of
    `attribution`); the crowd's slot moves the other way. Twenty warm
    sessions, because the memory's displacement is marked at the name's
    live daily sigma: three sessions after a five-session prehistory the
    market's volatility is still rising, and the remaining displacement
    rises with it on some seeds."""
    base, sig = warmed(model(), days=20)
    rows = []
    for i in range(len(ROSTER)):
        tk = ROSTER[i].ticker
        q = 0.2 * ROSTER[i].avg_volume
        e, ctl = base.fork(2)
        e.open_market()
        ctl.open_market()
        t = run(e, 0, 15)
        run(ctl, 0, 15)
        for k in range(18):
            e.submit("m", tk, q / 18)
            run(e, t, 10)
            t = run(ctl, t, 10)
        run(e, t, 390 - t, True)
        run(ctl, t, 390 - t, True)
        e.close_market()
        ctl.close_market()
        row = []
        for _ in range(3):
            row.append(math.log(f64(e.prices())[i] / f64(ctl.prices())[i]) / sig[i])
            day(e)
            day(ctl)
        rows.append(row)
    close, next1, next2 = (statistics.fmean(r[k] for r in rows) for k in range(3))
    assert close > next1 > next2 > 0.0, (close, next1, next2)


def _wash(base, i, times=20, share=0.005):
    """Agent a rests an ask a cent inside the spread and agent b lifts it,
    `times` times a tick apart. Returns whether every one of b's fills was
    against a, and the fork and its untraded twin."""
    t = ROSTER[i].ticker
    q = round(share * ROSTER[i].avg_volume)
    x, ctl = base.fork(2)
    x.open_market()
    ctl.open_market()
    now = run(x, 0, 30)
    run(ctl, 0, 30)
    between = True
    for _ in range(times):
        book = x.book(t)
        px = round(book.best_ask - 0.01, 2)
        if px <= book.best_bid:
            px = book.best_ask
        x.submit("a", t, -q, limit_price=px)
        r = x.submit("b", t, q)
        between &= all(fl["counterparty"] == "a" for fl in r["fills"])
        now = run(x, now, 1)
    run(ctl, 30, now - 30)
    return between, x, ctl


def test_a_fill_between_two_agents_feeds_nothing():
    """On every name where the wash's fills were all between the two
    agents, the memory stays empty and the tape is the untraded twin's.
    (Where the touch is a cent wide the inside ask joins the maker's queue
    and part of b's buy is filled by the house; that is flow, and it is
    left out.)"""
    base, _ = warmed(model())
    checked = 0
    for i in range(len(ROSTER)):
        between, x, ctl = _wash(base, i)
        if not between:
            continue
        checked += 1
        assert x.prices() == ctl.prices(), i
        book = x.state_snapshot().get("book", {})
        assert "memory" not in book or not f64(book["memory"]).any(), i
    assert checked >= len(ROSTER) // 2


# -- the meta-order cost tool -----------------------------------------------------


def test_metaorder_cost_reads_a_sliced_order_on_r20m():
    """`tools/calibration/metaorder_cost.py` takes an arm's overrides as one
    comma-separated `--set`, and on R20M a day-long order of 10% of volume
    in 36 slices costs less than the same order as one block, and more than
    nothing."""
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools" / "calibration"))
    import metaorder_cost as mc

    text = ",".join(f"{k}={v!r}" for k, v in R20M.items())
    assert mc.parse_set([text, "impact_memory_coefficient=0.6"]) == \
        dict(R20M, impact_memory_coefficient=0.6)
    res = mc.measure("pt-v20", R20M, seeds=1, names=12, warm_days=5,
                     sizes=[0.1], slices=[1, 36], ranks=[0, 6])
    cost = {n: statistics.fmean(r["cost"] for r in res["rows"] if r["n"] == n) for n in (1, 36)}
    assert 0.0 < cost[36] < cost[1], cost
