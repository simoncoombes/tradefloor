"""Oil futures (pt-v22 phase 1).

One switch, 0.0 on every shipped preset (`futures_oil_listed`). An oil
future reads the oil price and the forecast and draws nothing, so a model
with it on prints the stock prices, closes and economy the same model prints
with it off (NS3). These tests hold that, the calendar, the price against
the forecast, the settlement on the oil price at the expiry open, the book,
and the state across a snapshot, a fork and a replay.
"""
from __future__ import annotations

import math
import struct

import pytest

import tradefloor as tf
from tradefloor.manifest import state_hash
from tradefloor.replay import replay

UNIVERSE = tf.Universe.random(40, seed=111)
SMALL = tf.Universe.random(8, seed=5)
FORECAST = dict(forecast_vix_dispersion=0.3, forecast_vix_dispersion_half_life=31.5,
                forecast_vix_dispersion_skew=0.81, forecast_policy_shadow_discount=0.36,
                forecast_policy_persistence=0.6, forecast_policy_reversion=0.05,
                forecast_policy_neutral=1.48)
ON = dict(index_level_listed=1.0, vix_intraday_live=1.0, forecast_horizon_sessions=252.0,
          **FORECAST, futures_oil_listed=1.0)
#: With every phase 1 contract listed.
EVERY = dict(ON, futures_index_listed=1.0, basis_sd=3.753, basis_persistence=0.429,
             night_session_steps=8.0, futures_vix_listed=1.0, futures_vix_live_fast_share=0.254,
             futures_vix_live_fast_half_life=1.25, futures_vix_live_slow_half_life=30.0,
             futures_rates_listed=1.0)


def model(**dials):
    return tf.ModelParams.from_preset("pt-v21", **dials)


def _off(dials):
    return model(**{k: v for k, v in dials.items() if k != "futures_oil_listed"})


def oil_contracts(engine):
    return [c for c in engine.contracts() if c["root"] == "OIL"]


def day(engine):
    engine.open_market()
    engine.run_session(9, 30, 3, 390)
    engine.close_market()


def test_the_switch_is_off_on_every_shipped_preset_and_silent_in_the_digest():
    for name in tf.preset_names():
        assert tf.ModelParams.from_preset(name).futures_oil_listed == 0.0, name
    assert "futures_oil_listed" in set(tf.ModelParams.digest_silent_at_zero())
    assert model(futures_oil_listed=0.0).fingerprint == "pt-v21"


def test_off_nothing_is_listed_and_nothing_is_carried():
    engine = tf.Engine(seed=3, universe=SMALL, model=model(forecast_horizon_sessions=21.0))
    engine.run_days(20)
    assert oil_contracts(engine) == []
    assert "oil_futures" not in engine.state_snapshot()
    with pytest.raises(tf.ValidationError):
        engine.quote("OIL.F0035")


def _untraded(params, seed, days=12, steps=5, pins=None, trade=False):
    engine = tf.Engine(seed=seed, universe=UNIVERSE, model=params)
    out = []
    ticks = 390 // steps
    for d in range(days):
        if d and pins and d in pins:
            engine.pin_macro(**pins[d])
        engine.open_market()
        for k in range(steps):
            if trade and oil_contracts(engine):
                front, back = oil_contracts(engine)[0]["symbol"], oil_contracts(engine)[3]["symbol"]
                engine.submit("a", front, 200.0 if k % 2 else -300.0)
                q = engine.quote(back)
                engine.submit("b", back, 30.0, limit_price=q["bid"] - 2 * q["tick"])
            minutes = 30 + k * ticks
            engine.run_session(9 + minutes // 60, minutes % 60, 3, ticks)
            out.append(engine.session_prices())
        engine.close_market()
        out.append(engine.prices())
        out.append(repr(sorted(engine.economy().items())))
        for field in ("price", "volume", "mispricing_s", "garch_variance"):
            out.append(engine.column(field))
    return out, engine


@pytest.mark.parametrize("seed", [3, 17, 9001])
def test_ns3_an_untraded_run_prints_the_same_prices_with_the_oil_futures_on(seed):
    off, _ = _untraded(_off(ON), seed)
    on, engine = _untraded(model(**ON), seed)
    assert off == on
    assert len(oil_contracts(engine)) == 12


def test_ns3_holds_across_an_expiry_with_every_phase_1_contract_on():
    off, _ = _untraded(_off(EVERY), 101, days=40, steps=2)
    on, engine = _untraded(model(**EVERY), 101, days=40, steps=2)
    assert off == on
    assert [s["symbol"] for s in engine.settlements() if s["root"] == "OIL"] == ["OIL.F0014", "OIL.F0035"]


def test_ns3_holds_when_agents_trade_the_oil_futures_and_across_a_pin():
    pins = {4: dict(oil_price=95.0)}
    off, _ = _untraded(_off(ON), 17, days=8, pins=pins)
    on, engine = _untraded(model(**ON), 17, days=8, pins=pins, trade=True)
    assert off == on
    assert engine.take_fills("a"), "the agent traded nothing"


def test_twelve_monthly_contracts_are_listed_from_the_first_close_and_settle_at_expiry():
    engine = tf.Engine(seed=5, universe=SMALL, model=model(**ON))
    assert oil_contracts(engine) == []
    day(engine)
    listed = oil_contracts(engine)
    assert [c["expiry"] for c in listed] == [21 * m + 14 for m in range(12)]
    assert listed[0]["symbol"] == "OIL.F0014" and listed[0]["settlement"] == "oil_price_at_open"
    assert listed[0]["multiplier"] == 1000.0 and listed[0]["tick"] == 0.01
    for _ in range(13):
        day(engine)
    price = engine.economy()["oil_price"]
    engine.open_market()
    settled, = (s for s in engine.settlements(14) if s["root"] == "OIL")
    assert settled["value"] == price == settled["reference"]
    assert oil_contracts(engine)[0]["expiry"] == 35


def test_a_mark_is_the_forecasts_expected_oil_price_at_settlement():
    engine = tf.Engine(seed=7, universe=SMALL, model=model(**ON))
    worst, horizons = 0.0, set()
    for s in range(120):
        day(engine)
        f, now = engine.forecast(), engine.economy()["oil_price"]
        for c in oil_contracts(engine):
            n = c["expiry"] - s
            horizons.add(n)
            expected = now if n == 1 else f["oil"][min(n - 1, len(f["oil"])) - 1]
            q = engine.quote(c["symbol"])
            worst = max(worst, abs(q["mark"] - expected))
            assert q["premium"] == 0.0 and q["expected"] == q["fair"]
    assert worst == 0.0
    assert min(horizons) == 1 and max(horizons) == 252


def test_within_a_session_a_contract_holds_its_price_and_a_pin_moves_it():
    engine = tf.Engine(seed=9, universe=SMALL, model=model(**ON))
    for _ in range(10):
        day(engine)
    sym = oil_contracts(engine)[2]["symbol"]
    mark = engine.quote(sym)["mark"]
    engine.open_market()
    engine.run_session(9, 30, 3, 200)
    assert engine.quote(sym)["price"] == mark
    engine.run_session(12, 50, 3, 190)
    engine.close_market()
    before = engine.quote(sym)["price"]
    engine.pin_macro(oil_price=engine.economy()["oil_price"] + 20.0)
    assert engine.quote(sym)["price"] > before


def test_an_order_walks_the_makers_ladder_rests_and_marks_the_price():
    engine = tf.Engine(seed=11, universe=SMALL, model=model(**ON))
    for _ in range(5):
        day(engine)
    engine.open_market()
    engine.run_session(9, 30, 3, 5)
    sym = oil_contracts(engine)[0]["symbol"]
    q = engine.quote(sym)
    assert q["ask"] - q["bid"] == pytest.approx(q["tick"])
    book = engine.book(sym)
    assert len(book.price_levels("sell", 50)) == 10 and len(book.price_levels("buy", 50)) == 10
    r = engine.submit("a", sym, 2_000.0)
    assert r["filled"] == 2_000.0 and r["average_price"] > q["ask"]
    rest = engine.submit("a", sym, -100.0, limit_price=q["ask"] + 1.0)
    assert rest["resting"] == 100.0
    engine.run_session(9, 35, 3, 1)
    assert engine.quote(sym)["basis_bp"] > 0.0
    assert engine.cancel(rest["order_id"])
    assert all(f["ticker"] == sym for f in engine.take_fills("a"))
    engine.run_session(9, 36, 3, 384)
    engine.close_market()
    with pytest.raises(tf.OrderError, match="session only"):
        engine.submit("a", sym, 1.0)


def _traded_engine(seed=13):
    engine = tf.Engine(seed=seed, universe=SMALL, model=model(**EVERY))
    for _ in range(16):
        engine.open_market()
        engine.run_session(9, 30, 3, 390)
        engine.close_market()
        engine.run_night()
    engine.open_market()
    engine.run_session(9, 30, 3, 40)
    sym = oil_contracts(engine)[0]["symbol"]
    engine.submit("a", sym, 500.0)
    q = engine.quote(sym)
    engine.submit("b", sym, 30.0, limit_price=q["bid"] - 3 * q["tick"])
    engine.run_session(9, 70, 3, 10)
    return engine


def _go_on(engine):
    out = []
    engine.run_session(10, 20, 3, 340)
    out.append([engine.quote(c["symbol"]) for c in engine.contracts()])
    engine.close_market()
    for _ in range(25):
        engine.run_night()
        engine.open_market()
        engine.run_session(9, 30, 3, 390)
        engine.close_market()
        out.append([engine.quote(c["symbol"])["mark"] for c in engine.contracts()])
    out.append(engine.settlements())
    out.append(engine.state_hash())
    return out


def test_a_mid_session_snapshot_restores_and_continues_like_the_original():
    engine = _traded_engine()
    snap = engine.state_snapshot()
    for key in ("oil_futures", "oil_futures_book"):
        assert key in snap, key
    assert state_hash(snap) == engine.state_hash()
    restored = tf.Engine(seed=99, universe=SMALL, model=model(**EVERY))
    restored.restore_state(snap)
    assert restored.state_hash() == engine.state_hash()
    assert _go_on(restored) == _go_on(engine)


def test_the_state_hash_reads_the_oil_futures():
    engine = _traded_engine()
    snap = engine.state_snapshot()
    raw = snap["oil_futures"]
    last, = struct.unpack("<d", raw[-8:])
    changed = dict(snap, oil_futures=raw[:-8] + struct.pack("<d", math.nextafter(last, math.inf)))
    assert state_hash(changed) != state_hash(snap)


def test_a_mid_session_fork_continues_like_the_original():
    engine = _traded_engine(seed=14)
    fork, = engine.fork(1)
    assert fork.state_hash() == engine.state_hash()
    assert _go_on(fork) == _go_on(engine)


def test_a_snapshot_with_oil_futures_is_refused_by_a_model_without_them():
    snap = _traded_engine().state_snapshot()
    off = tf.Engine(seed=99, universe=SMALL, model=_off(EVERY))
    with pytest.raises(tf.ValidationError, match="futures_oil_listed"):
        off.restore_state(snap)


def test_the_order_log_replays_the_oil_futures_orders():
    engine = _traded_engine(seed=15)
    _go_on(engine)
    again = replay(engine.order_log, seed=15, universe=SMALL, model=model(**EVERY))
    assert again.state_hash() == engine.state_hash()
