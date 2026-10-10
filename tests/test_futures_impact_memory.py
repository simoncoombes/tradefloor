"""The index futures' metaorder memory (`futures_impact_memory`, pt-v22).

0.0 on every shipped preset, where the spec's linear mark stays: agents'
net flow against the house moves the basis by `0.15 sigma flow / V` and
decays at a 30-step half-life, so a day's TWAP of 10 per cent of the
contract's volume costs about half a tick (IF4 read 0.005 daily sigma
against Q6's 0.10 to 0.21). Off zero, each contract keeps the equity
book's metaorder memory and the house is priced on its curve on both sides
(the Alfonsi-Fruth-Schied book under the maker's ladder). These tests hold
the switch inert off, inert untraded on, the square-root shape traded, no
profitable round trip, and the memory across a roll, a fork and a snapshot.
"""
from __future__ import annotations

import math

import pytest

import tradefloor as tf

UNIVERSE = tf.Universe.random(40, seed=111)
SMALL = tf.Universe.random(8, seed=5)
FORECAST = dict(forecast_vix_dispersion=0.3, forecast_vix_dispersion_half_life=31.5,
                forecast_policy_shadow_discount=0.31, forecast_policy_persistence=0.62,
                forecast_policy_reversion=0.0475, forecast_policy_neutral=1.4)
ON = dict(index_level_listed=1.0, vix_intraday_live=1.0, forecast_horizon_sessions=252.0, **FORECAST,
          futures_index_listed=1.0, basis_sd=3.753, basis_persistence=0.429, night_session_steps=8.0)
#: The equity memory the futures read (R22V3's, the pt-v21 lineage's).
MEMORY = dict(impact_memory_coefficient=0.65, impact_memory_half_life=12.0, impact_memory_slow_half_life=780.0,
              impact_memory_slow_weight=0.1, impact_memory_crossover=0.001)
Y = 0.65


def model(**dials):
    return tf.ModelParams.from_preset("pt-v21", **dials)


def day(engine):
    if engine.day_count:
        engine.run_night()
    engine.open_market()
    engine.run_session(9, 30, 3, 390)
    engine.close_market()


def test_off_on_every_shipped_preset_silent_in_the_digest_and_refused_without_futures():
    for name in tf.preset_names():
        assert tf.ModelParams.from_preset(name).futures_impact_memory == 0.0, name
    assert "futures_impact_memory" in set(tf.ModelParams.digest_silent_at_zero())
    assert model(futures_impact_memory=0.0).fingerprint == "pt-v21"
    with pytest.raises(tf.ValidationError):
        model(futures_impact_memory=0.65)
    with pytest.raises(tf.ValidationError):
        model(**ON, futures_impact_memory=-0.1)


@pytest.mark.parametrize("seed", [3, 4])
def test_untraded_the_switch_changes_nothing(seed):
    """It reads only agents' futures flow: an untraded run's prices,
    quotes and state are the run without it (all but the model's
    fingerprint, which the state hash folds in; compared by repr, since
    the generators' words hold NaN)."""
    out = []
    for y in (0.0, Y):
        e = tf.Engine(seed=seed, universe=SMALL, model=model(**ON, **MEMORY, futures_impact_memory=y))
        rows = []
        for _ in range(4):
            day(e)
            snap = e.state_snapshot()
            snap.pop("model_fingerprint")
            rows += [e.prices(), [e.quote(c["symbol"]) for c in e.contracts()], repr(sorted(snap.items()))]
        out.append(rows)
    assert out[0] == out[1]


def _warm(y, seed=8, **extra):
    e = tf.Engine(seed=seed, universe=UNIVERSE, model=model(**dict(ON, basis_sd=0.0), **MEMORY,
                                                            futures_impact_memory=y, **extra))
    for _ in range(3):
        day(e)
    e.run_night()
    e.open_market()
    e.run_session(9, 30, 3, 15)
    return e


def _twap(e, f, n=36, gap=10):
    """A TWAP of f of the front's daily volume, priced against a twin's
    mid: implementation shortfall over the index's daily sigma proxy (bp)."""
    x, ctl = e.fork(2)
    sym = e.contracts()[0]["symbol"]
    v = e.quote(sym)["daily_volume"]
    num = den = 0.0
    t = 15
    for k in range(n):
        mid = ctl.book(sym).mid_price
        r = x.submit("me", sym, f * v / n)
        num += r["filled"] * (r["average_price"] - mid)
        den += r["filled"] * mid
        for y in (x, ctl):
            m = 9 * 60 + 30 + t
            y.run_session(m // 60, m % 60, 3, gap)
        t += gap
    return 1e4 * num / den


def test_traded_the_cost_follows_a_square_root_and_exceeds_the_linear_marks():
    off, on = _warm(0.0), _warm(Y)
    lin = [_twap(off, f) for f in (0.01, 0.1)]
    sq = [_twap(on, f) for f in (0.01, 0.1)]
    assert sq[1] > 5 * lin[1], (lin, sq)
    expo = math.log(sq[1] / sq[0]) / math.log(10.0)
    assert 0.3 < expo < 0.7, (sq, expo)


def _round_trip(e, legs):
    """Flat legs [(ticks before, contracts)], P&L against the twin's mid,
    bp of what was bought."""
    x, ctl = e.fork(2)
    sym = e.contracts()[0]["symbol"]
    pnl = bought = 0.0
    t = 15
    for wait, q in legs:
        for y in (x, ctl):
            if wait:
                m = 9 * 60 + 30 + t
                y.run_session(m // 60, m % 60, 3, wait)
        t += wait
        ref = ctl.book(sym).mid_price
        r = x.submit("me", sym, q)
        f = r["filled"] * (1 if q > 0 else -1)
        pnl += f * (ref - r["average_price"])
        bought += max(f, 0.0) * r["average_price"]
    return 1e4 * pnl / bought


@pytest.mark.parametrize("f", [0.01, 0.03, 0.1])
def test_no_round_trip_pays(f):
    e = _warm(Y)
    q = f * e.quote(e.contracts()[0]["symbol"])["daily_volume"]
    plans = {
        "block, block back": [(0, q), (1, -q)],
        "block, six slices back": [(0, q)] + [(1, -q / 6)] * 6,
        "hour TWAP, block back": [(5 if k else 0, q / 12) for k in range(12)] + [(1, -q)],
        "alternating blocks": [(1 if k else 0, q if k % 2 == 0 else -q) for k in range(20)],
    }
    for name, legs in plans.items():
        assert _round_trip(e, legs) < 0.0, name


def test_selling_back_meets_the_memorys_curve():
    """Against the lean the bids are the memory's path down, not the
    maker's ladder around the displaced price."""
    e = _warm(Y)
    sym = e.contracts()[0]["symbol"]
    q = 0.05 * e.quote(sym)["daily_volume"]
    e.submit("a", sym, q)
    e.run_session(9, 45, 3, 1)
    bids = e.book(sym).price_levels("buy", 50)
    depth_to = lambda px: sum(l.quantity for l in bids if l.price >= px)  # noqa: E731
    mid = e.book(sym).mid_price
    # the maker's ten levels alone would hold 3% of the volume within ten ticks
    assert depth_to(mid - 10 * 0.05) < 0.03 * e.quote(sym)["daily_volume"]


def test_the_memory_rides_a_fork_and_a_snapshot():
    e = _warm(Y, seed=13)
    sym = e.contracts()[0]["symbol"]
    e.submit("a", sym, 0.03 * e.quote(sym)["daily_volume"])
    e.run_session(9, 45, 3, 2)
    snap = e.state_snapshot()
    assert "futures_book" in snap
    fork, = e.fork(1)
    restored = tf.Engine(seed=99, universe=UNIVERSE, model=e.model)
    restored.restore_state(snap)
    out = []
    for x in (e, fork, restored):
        x.run_session(9, 47, 3, 200)
        x.close_market()
        day(x)
        out.append(([x.quote(c["symbol"]) for c in x.contracts()], x.state_hash()))
    assert out[0] == out[1] == out[2]


def test_the_memory_follows_its_contract_through_the_roll():
    e = tf.Engine(seed=21, universe=SMALL, model=model(**ON, **MEMORY, futures_impact_memory=Y))
    day(e)
    seen = set()
    for _ in range(80):
        cs = e.contracts()
        e.open_market()
        back = e.contracts()[1]["symbol"]
        e.submit("a", back, 5.0)
        e.run_session(9, 30, 3, 390)
        e.close_market()
        seen.add(tuple(c["symbol"] for c in cs))
        e.run_night()
        for c in e.contracts():
            assert math.isfinite(e.quote(c["symbol"])["basis_bp"])
    assert len(seen) > 1, "no roll in 80 sessions"
