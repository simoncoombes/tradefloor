"""The metaorder memory (`impact_memory_coefficient` and its four shape dials).

Off zero, each name keeps a decaying memory of agents' net taker flow
against the house and its model price carries the square-root displacement
of it, so a
metaorder's impact reaches the tape, is concave in size and decays within
the day (`agent_book.rs`, "The metaorder memory"; measured by
`tools/calibration/metaorder_curve.py`). These tests hold:

- it is off on every shipped preset;
- at 0.0 its shape dials are read by nothing, even with agents trading;
- with no agent trading, setting every dial changes nothing: the memory is
  fed only by agents' flow, and the snapshot and state hash carry it only
  while it holds something;
- with agents trading it moves the tape, concavely in size;
- a single block on a fresh book costs exactly what it did (row C9);
- no round trip of `metaorder_curve.py`'s six strategies makes money
  against a twin that does not trade;
- a fill between two agents is not flow to the market with the memory on:
  a wash (one rests an ask inside the spread, the other lifts it) moves
  neither the memory nor the price;
- the invariants refuse bad values and missing companions;
- the memory survives a snapshot and restore, and forks carry it.
"""

import math
import struct
import sys
from pathlib import Path

import pytest

import tradefloor as tf
from tradefloor import manifest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "calibration"))

DIALS = ("impact_memory_coefficient", "impact_memory_half_life",
         "impact_memory_slow_half_life", "impact_memory_slow_weight",
         "impact_memory_crossover")
ON = dict(impact_memory_coefficient=0.5, impact_memory_half_life=45.0,
          impact_memory_slow_half_life=15600.0, impact_memory_slow_weight=0.1,
          impact_memory_crossover=0.001)
SHAPE_ONLY = {k: v for k, v in ON.items() if k != "impact_memory_coefficient"}
UNIVERSE = tf.Universe.random(8, seed=93001)
#: f64 per name in the snapshot's book "memory": fast, slow, booked, paid,
#: and the flow against the house waiting for the next tick.
WIDTH = 5


def f64(raw):
    return struct.unpack("<%dd" % (len(raw) // 8), raw)


def warmed(over=None, seed=2011):
    model = tf.ModelParams.from_preset("pt-v20", **(over or {}))
    e = tf.Engine(seed=seed, universe=UNIVERSE, model=model)
    e.open_market()
    e.run_session(9, 30, 3, 390, close_at_end=True)
    e.close_market()
    e.open_market()
    e.run_session(9, 30, 3, 30)
    return e


def sliced(e, i, f, slices=12, gap=5, start=30):
    """Buy `f` of daily volume in `slices` slices, `gap` ticks apart."""
    t = e.tickers[i]
    q = f * UNIVERSE[i].avg_volume / slices
    now = start
    for _ in range(slices):
        e.submit("me", t, q)
        m = 9 * 60 + 30 + now
        e.run_session(m // 60, m % 60, 3, gap)
        now += gap
    return now


def displacement(over, f, i=2):
    base = warmed(over)
    x, ctl = base.fork(2)
    now = sliced(x, i, f)
    m = 9 * 60 + 30 + 30
    ctl.run_session(m // 60, m % 60, 3, now - 30)
    s = f64(x.column("mispricing_s"))[i] - f64(ctl.column("mispricing_s"))[i]
    return s, math.log(f64(x.prices())[i] / f64(ctl.prices())[i])


@pytest.mark.parametrize("preset", tf.preset_names())
def test_off_on_every_shipped_preset(preset):
    d = tf.ModelParams.from_preset(preset).to_dict()
    assert all(d[k] == 0.0 for k in DIALS)


def test_shape_dials_are_inert_while_the_coefficient_is_zero():
    """Agents trade; the four shape dials at their values, the coefficient
    at 0.0: the same fills, prices and state as the preset."""
    a, b = warmed(), warmed(SHAPE_ONLY)
    for e in (a, b):
        sliced(e, 2, 0.1)
    assert a.prices() == b.prices()
    assert a.take_fills() == b.take_fills()
    assert "memory" not in b.state_snapshot()["book"]
    assert all("transient" not in r for r in b.take_impacts())


def test_setting_every_dial_changes_nothing_without_agents():
    """The memory is fed only by agents' flow: an untraded run is the same
    market to the bit, and its state hashes the same."""
    a, b = warmed(), warmed(ON)
    for e in (a, b):
        e.run_session(10, 0, 3, 200)
    assert a.prices() == b.prices()
    snap = b.state_snapshot()
    assert "memory" not in snap.get("book", {})
    assert manifest.state_hash(snap) == b.state_hash()


def test_with_agents_it_puts_the_order_on_the_tape_concavely():
    """Without the memory, `s` moves linearly in size (gamma alone) and a
    1% order barely reaches the print; with it, the displacement is
    concave (ten times the size, about sqrt(10) = 3.2 times as far) and on
    the print."""
    off = [displacement({}, f) for f in (0.01, 0.1)]
    on = [displacement(ON, f) for f in (0.01, 0.1)]
    assert off[1][0] / off[0][0] == pytest.approx(10.0, rel=0.05)
    assert 2.0 < on[1][0] / on[0][0] < 5.0
    assert on[0][0] > 3 * off[0][0] and on[1][0] > off[1][0]
    assert on[0][1] > on[0][0] / 2 and on[1][1] > on[1][0] / 2


def test_the_memory_decays_and_is_carried_by_the_snapshot():
    e = warmed(ON)
    sliced(e, 2, 0.1)
    snap = e.state_snapshot()
    assert "memory" in snap["book"]
    assert manifest.state_hash(snap) == e.state_hash()
    rows = f64(snap["book"]["memory"])
    assert len(rows) == WIDTH * len(UNIVERSE)
    fast = rows[WIDTH * 2]
    assert fast > 0.0
    assert any("transient" in r for r in e.take_impacts())
    # Restore into a fresh engine: the same state, and the same future.
    twin = tf.Engine(seed=2011, universe=UNIVERSE,
                     model=tf.ModelParams.from_preset("pt-v20", **ON))
    twin.restore_state(e.state_snapshot())
    assert twin.state_hash() == e.state_hash()
    for x in (e, twin):
        x.run_session(10, 30, 3, 120)
    assert twin.prices() == e.prices()
    after = f64(e.state_snapshot()["book"]["memory"])[WIDTH * 2]
    assert 0.0 < after < fast


def test_a_checkpoint_carries_the_memory():
    e = warmed(ON)
    sliced(e, 2, 0.1)
    checkpoint = tf.Checkpoint.of(e, universe=UNIVERSE, seed=2011)
    b = tf.Checkpoint.from_json(checkpoint.to_json()).resume()
    assert b.state_hash() == e.state_hash()


def test_a_single_block_on_a_fresh_book_costs_what_it_did():
    """C9's order: one immediate block with no memory. The bound acts only
    against a memory's lean, so the book this order meets is unchanged."""
    a, b = warmed(), warmed(ON)
    for i in range(len(UNIVERSE)):
        t = UNIVERSE[i].ticker
        for f in (0.001, 0.01, 0.1, 0.3):
            q = f * UNIVERSE[i].avg_volume
            x, y = a.book(t).sweep_cost("buy", q), b.book(t).sweep_cost("buy", q)
            assert x.filled == y.filled
            assert x.average_price == y.average_price


def test_no_round_trip_makes_money():
    """`metaorder_curve.py trips` on one seed and a few names: the best
    strategy, against the same orders at the twin's mid, still loses."""
    import metaorder_curve as mc
    params = tf.ModelParams.from_preset("pt-v20", **ON)
    rows = mc.round_trips(2012, params, n_names=4)
    assert len(rows) > 40
    assert max(r["edge_bp"] for r in rows) < 0.0


def wash(e, i, times=20, share=0.005):
    """Agent a rests an ask a cent inside the spread and agent b lifts it,
    `times` times, one tick apart; returns the pair's cash."""
    t = e.tickers[i]
    q = round(share * UNIVERSE[i].avg_volume)
    cash = 0.0
    now = 30
    for _ in range(times):
        book = e.book(t)
        px = round(book.best_ask - 0.01, 2)
        if px <= book.best_bid:
            px = book.best_ask
        e.submit("a", t, -q, limit_price=px)
        r = e.submit("b", t, q)
        assert r["filled"] == q and all(f["counterparty"] == "a" for f in r["fills"])
        cash += q * px - r["filled"] * r["average_price"]
        m = 9 * 60 + 30 + now
        e.run_session(m // 60, m % 60, 3, 1)
        now += 1
    return cash


@pytest.mark.parametrize("on", [False, True])
def test_a_wash_between_two_agents_moves_nothing_with_the_memory_on(on):
    """Off, the taker's side of a fill between two agents is flow as it
    always was, and the linear law moves the price on it. On, it is not
    flow to the market: the memory stays empty and the price is the price
    of a twin nobody traded on, so the pair walks nothing for its zero."""
    base = warmed(ON if on else None)
    i = 3
    x, ctl = base.fork(2)
    assert wash(x, i) == pytest.approx(0.0, abs=1e-6)
    ctl.run_session(10, 0, 3, 20)
    moved = f64(x.prices())[i] != f64(ctl.prices())[i]
    if on:
        assert not moved
        assert x.prices() == ctl.prices()
        book = x.state_snapshot()["book"]
        assert "memory" not in book or not any(f64(book["memory"]))
        assert all(r["transient"] == 0.0 for r in x.take_impacts() if "transient" in r)
    else:
        assert moved


def test_a_fill_against_the_house_still_feeds_the_memory():
    """The same buys taken from the house (no resting ask to lift) do move
    the memory and the price."""
    e = warmed(ON)
    x, ctl = e.fork(2)
    t = x.tickers[3]
    q = round(0.005 * UNIVERSE[3].avg_volume)
    for k in range(20):
        x.submit("b", t, q)
        for y in (x, ctl):
            m = 9 * 60 + 30 + 30 + k
            y.run_session(m // 60, m % 60, 3, 1)
    assert f64(x.prices())[3] > f64(ctl.prices())[3]
    assert f64(x.state_snapshot()["book"]["memory"])[WIDTH * 3] > 0.0


@pytest.mark.parametrize("over", [
    dict(impact_memory_coefficient=-0.1, impact_memory_half_life=90.0),
    dict(impact_memory_coefficient=0.9, impact_memory_half_life=90.0),   # above book_depth_coefficient 0.75
    dict(impact_memory_coefficient=0.5),                                  # no half-life
    dict(impact_memory_coefficient=0.5, impact_memory_half_life=40000.0),
    dict(impact_memory_coefficient=0.5, impact_memory_half_life=90.0, impact_memory_slow_half_life=60.0),
    dict(impact_memory_coefficient=0.5, impact_memory_half_life=90.0, impact_memory_slow_weight=0.1),
    dict(impact_memory_coefficient=0.5, impact_memory_half_life=90.0, impact_memory_slow_half_life=15600.0,
         impact_memory_slow_weight=1.0),
    dict(impact_memory_coefficient=0.5, impact_memory_half_life=90.0, impact_memory_crossover=0.1),
    dict(impact_memory_coefficient=0.5, impact_memory_half_life=90.0, book_shared=0.0,
         book_refill_half_life=0.0),
])
def test_the_invariants_refuse(over):
    with pytest.raises(Exception):
        tf.ModelParams.from_preset("pt-v20", **over)


def test_a_valid_vector_is_accepted():
    tf.ModelParams.from_preset("pt-v20", **ON)


def test_pt_v20s_preset_known_answer_holds_with_every_dial_set(monkeypatch):
    """pt-v20's row of `known_answer_presets.json`, run with the memory's
    five dials set: no agent trades there, so it is the registered digest."""
    import hashlib
    import json
    import types

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import known_answer_presets as K

    orig = tf.Engine

    def engine(**kw):
        kw["model"] = tf.ModelParams.from_preset(kw["model"], **ON)
        return orig(**kw)

    shim = types.SimpleNamespace(**{k: getattr(tf, k) for k in dir(tf) if not k.startswith("__")})
    shim.Engine = engine
    monkeypatch.setattr(K, "tradefloor", shim)
    baseline = json.loads((Path(__file__).resolve().parent / "known_answer_presets.json").read_text())
    got = hashlib.sha256(K.preset_buffer("pt-v20")).hexdigest()
    assert got == baseline["presets"]["pt-v20"]
