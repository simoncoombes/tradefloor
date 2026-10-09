"""Policy-rate and term-rate futures (pt-v22 phase 1).

One switch, 0.0 on every shipped preset (`futures_rates_listed`). A rate
future reads the policy rate each close sets and the forecast and draws
nothing, so a model with it on prints the stock prices, closes and economy
the same model prints with it off (NS3). These tests hold that, the
calendar, the settlement on the period's closing rates (RF3), the price
against its formula, the book, and the state across a snapshot, a fork and
a replay.
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

#: The rate futures' premium, frozen, as `derivatives::RATE_PREMIUM` carries it.
K, P, CAP = 1.5956, 1.683, 126.0
FORECAST = dict(forecast_vix_dispersion=0.3, forecast_vix_dispersion_half_life=31.5,
                forecast_vix_dispersion_skew=0.81, forecast_policy_shadow_discount=0.36,
                forecast_policy_persistence=0.6, forecast_policy_reversion=0.05,
                forecast_policy_neutral=1.48)
ON = dict(index_level_listed=1.0, vix_intraday_live=1.0, forecast_horizon_sessions=252.0,
          **FORECAST, futures_rates_listed=1.0)
#: With every other phase 1 contract listed as well.
EVERY = dict(ON, futures_index_listed=1.0, basis_sd=3.753, basis_persistence=0.429,
             night_session_steps=8.0, futures_vix_listed=1.0, futures_vix_live_fast_share=0.254,
             futures_vix_live_fast_half_life=1.25, futures_vix_live_slow_half_life=30.0)


def model(**dials):
    return tf.ModelParams.from_preset("pt-v21", **dials)


def premium(h):
    return 0.0 if h <= 0 else K * (min(h, CAP) / 21.0) ** P / 100.0


def rate_contracts(engine, root=None):
    return [c for c in engine.contracts() if c["root"] in ((root,) if root else ("FF", "TR3"))]


def policy_rate(engine):
    """The policy rate now, per cent."""
    return engine.economy()["federal_funds_rate"]


def day(engine):
    engine.open_market()
    engine.run_session(9, 30, 3, 390)
    engine.close_market()


def period_rate(root, rates):
    if root == "FF":
        return sum(rates) / len(rates)
    growth = 1.0
    for r in rates:
        growth *= 1.0 + r / 25200.0
    return (growth - 1.0) * 25200.0 / len(rates)


# ── Off on every shipped preset ─────────────────────────────────────────────

def test_the_switch_is_off_on_every_shipped_preset_and_silent_in_the_digest():
    for name in tf.preset_names():
        assert tf.ModelParams.from_preset(name).futures_rates_listed == 0.0, name
    assert "futures_rates_listed" in set(tf.ModelParams.digest_silent_at_zero())
    assert model(futures_rates_listed=0.0).fingerprint == "pt-v21"


def test_off_nothing_is_listed_and_nothing_is_carried():
    engine = tf.Engine(seed=3, universe=SMALL, model=model(forecast_horizon_sessions=21.0))
    engine.run_days(25)
    assert rate_contracts(engine) == []
    snap = engine.state_snapshot()
    assert "rate_futures" not in snap and "rate_futures_book" not in snap
    with pytest.raises(tf.ValidationError):
        engine.quote("FF.F0041")


# ── NS3 ─────────────────────────────────────────────────────────────────────

def _untraded(params, seed, days=12, steps=5, pins=None, trade=False):
    engine = tf.Engine(seed=seed, universe=UNIVERSE, model=params)
    out = []
    ticks = 390 // steps
    for d in range(days):
        if d and pins and d in pins:
            engine.pin_macro(**pins[d])
        engine.open_market()
        for k in range(steps):
            if trade and rate_contracts(engine):
                ff, tr = rate_contracts(engine, "FF")[0]["symbol"], rate_contracts(engine, "TR3")[1]["symbol"]
                engine.submit("a", ff, 200.0 if k % 2 else -300.0)
                q = engine.quote(tr)
                engine.submit("b", tr, 50.0, limit_price=q["bid"] - 2 * q["tick"])
            minutes = 30 + k * ticks
            engine.run_session(9 + minutes // 60, minutes % 60, 3, ticks)
            out.append(engine.session_prices())
        engine.close_market()
        out.append(engine.prices())
        out.append(repr(sorted(engine.economy().items())))
        for field in ("price", "volume", "mispricing_s", "garch_variance"):
            out.append(engine.column(field))
    return out, engine


def _off(dials):
    return model(**{k: v for k, v in dials.items() if k != "futures_rates_listed"})


@pytest.mark.parametrize("seed", [3, 17, 9001])
def test_ns3_an_untraded_run_prints_the_same_prices_with_the_rate_futures_on(seed):
    off, _ = _untraded(_off(ON), seed)
    on, engine = _untraded(model(**ON), seed)
    assert off == on
    assert len(rate_contracts(engine, "FF")) == 13 and len(rate_contracts(engine, "TR3")) == 8


def test_ns3_holds_across_settlements_with_every_phase_1_contract_on():
    off, _ = _untraded(_off(EVERY), 101, days=66, steps=2)
    on, engine = _untraded(model(**EVERY), 101, days=66, steps=2)
    assert off == on
    settled = [s["symbol"] for s in engine.settlements() if s["root"] in ("FF", "TR3")]
    assert settled == ["FF.F0020", "FF.F0041", "FF.F0062", "TR3.F0062"]


def test_ns3_holds_when_agents_trade_the_rate_futures_and_across_a_pin():
    pins = {4: dict(federal_funds_rate=0.06)}
    off, _ = _untraded(_off(ON), 17, days=8, pins=pins)
    on, engine = _untraded(model(**ON), 17, days=8, pins=pins, trade=True)
    assert off == on
    assert engine.take_fills("a"), "the agent traded nothing"


# ── The calendar and the settlement ─────────────────────────────────────────

def test_thirteen_monthly_and_eight_quarterly_are_listed_from_the_first_close():
    engine = tf.Engine(seed=5, universe=SMALL, model=model(**ON))
    assert rate_contracts(engine) == []
    day(engine)
    ff, tr = rate_contracts(engine, "FF"), rate_contracts(engine, "TR3")
    assert [c["expiry"] for c in ff] == [21 * j + 20 for j in range(13)]
    assert [c["expiry"] for c in tr] == [63 * q + 62 for q in range(8)]
    assert ff[0]["symbol"] == "FF.F0020" and tr[0]["symbol"] == "TR3.F0062"
    assert [c["front"] for c in ff] == [True] + [False] * 12
    assert ff[0]["settlement"] == "average_policy_rate_at_close"
    assert tr[0]["settlement"] == "compounded_policy_rate_at_close"


def test_rf3_each_settles_on_its_periods_closing_rates():
    engine = tf.Engine(seed=7, universe=SMALL, model=model(**ON))
    rates = []
    for _ in range(130):
        day(engine)
        rates.append(policy_rate(engine))
    settled = [s for s in engine.settlements() if s["root"] in ("FF", "TR3")]
    assert len([s for s in settled if s["root"] == "FF"]) == 6
    assert len([s for s in settled if s["root"] == "TR3"]) == 2
    for s in settled:
        length = 21 if s["root"] == "FF" else 63
        x = s["session"]
        expected = 100.0 - period_rate(s["root"], rates[x - length + 1: x + 1])
        assert abs(s["value"] - expected) < 1e-9, (s, expected)
        assert s["value"] == s["reference"]


def test_a_mark_is_100_less_the_realised_and_expected_rate_and_the_premium():
    engine = tf.Engine(seed=9, universe=SMALL, model=model(**ON))
    rates = []
    for _ in range(30):
        day(engine)
        rates.append(policy_rate(engine))
    s = len(rates) - 1
    f = engine.forecast()
    path = [100.0 * r for r in f["policy_rate"]]
    for c in rate_contracts(engine):
        length = 21 if c["root"] == "FF" else 63
        x = c["expiry"]
        period = [rates[k] if k <= s else path[min(k - s, len(path)) - 1] for k in range(x - length + 1, x + 1)]
        expected = period_rate(c["root"], period)
        q = engine.quote(c["symbol"])
        assert q["expected"] == pytest.approx(expected, abs=1e-12), c["symbol"]
        assert q["mark"] == pytest.approx(100.0 - expected - premium(x - s), abs=1e-12), c["symbol"]


def test_within_a_session_a_contract_holds_but_for_its_premiums_roll():
    engine = tf.Engine(seed=9, universe=SMALL, model=model(**ON))
    for _ in range(30):
        day(engine)
    sym = rate_contracts(engine, "FF")[3]["symbol"]
    mark = engine.quote(sym)["mark"]
    engine.open_market()
    engine.run_session(9, 30, 3, 195)
    q = engine.quote(sym)
    assert q["sessions_to_expiry"] == pytest.approx(q["expiry"] - 29 - 0.5)
    assert q["price"] == pytest.approx(100.0 - q["expected"] - premium(q["sessions_to_expiry"]), abs=1e-12)
    assert q["price"] > mark  # the premium has rolled down half a session


# ── The book ────────────────────────────────────────────────────────────────

def test_an_order_walks_the_makers_ladder_rests_and_marks_the_price():
    engine = tf.Engine(seed=11, universe=SMALL, model=model(**ON))
    for _ in range(5):
        day(engine)
    engine.open_market()
    engine.run_session(9, 30, 3, 5)
    sym = rate_contracts(engine, "FF")[0]["symbol"]
    q = engine.quote(sym)
    assert q["ask"] - q["bid"] == pytest.approx(q["tick"])
    book = engine.book(sym)
    assert len(book.price_levels("sell", 50)) == 10 and len(book.price_levels("buy", 50)) == 10
    r = engine.submit("a", sym, 1_000.0)
    assert r["filled"] == 1_000.0 and r["average_price"] > q["ask"]
    rest = engine.submit("a", sym, -100.0, limit_price=q["ask"] + 0.05)
    assert rest["resting"] == 100.0
    engine.run_session(9, 35, 3, 1)
    assert engine.quote(sym)["basis_bp"] < 0.0  # bought: the price up, the rate down
    assert engine.cancel(rest["order_id"])
    assert all(f["ticker"] == sym for f in engine.take_fills("a"))
    engine.run_session(9, 36, 3, 384)
    engine.close_market()
    with pytest.raises(tf.OrderError, match="session only"):
        engine.submit("a", sym, 1.0)


# ── State ───────────────────────────────────────────────────────────────────

def _traded_engine(seed=13):
    engine = tf.Engine(seed=seed, universe=SMALL, model=model(**EVERY))
    for _ in range(23):
        engine.open_market()
        engine.run_session(9, 30, 3, 390)
        engine.close_market()
        engine.run_night()
    engine.open_market()
    engine.run_session(9, 30, 3, 40)
    sym = rate_contracts(engine, "TR3")[0]["symbol"]
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
    for key in ("rate_futures", "rate_futures_book"):
        assert key in snap, key
    assert state_hash(snap) == engine.state_hash()
    restored = tf.Engine(seed=99, universe=SMALL, model=model(**EVERY))
    restored.restore_state(snap)
    assert restored.state_hash() == engine.state_hash()
    assert _go_on(restored) == _go_on(engine)


def test_the_state_hash_reads_the_rate_futures():
    engine = _traded_engine()
    snap = engine.state_snapshot()
    raw = snap["rate_futures"]
    last, = struct.unpack("<d", raw[-8:])
    changed = dict(snap, rate_futures=raw[:-8] + struct.pack("<d", math.nextafter(last, math.inf)))
    assert state_hash(changed) != state_hash(snap)


def test_a_mid_session_fork_continues_like_the_original():
    engine = _traded_engine(seed=14)
    fork, = engine.fork(1)
    assert fork.state_hash() == engine.state_hash()
    assert _go_on(fork) == _go_on(engine)


def test_a_snapshot_with_rate_futures_is_refused_by_a_model_without_them():
    snap = _traded_engine().state_snapshot()
    off = tf.Engine(seed=99, universe=SMALL, model=_off(EVERY))
    with pytest.raises(tf.ValidationError, match="futures_rates_listed"):
        off.restore_state(snap)


def test_the_order_log_replays_the_rate_futures_orders():
    engine = _traded_engine(seed=15)
    _go_on(engine)
    again = replay(engine.order_log, seed=15, universe=SMALL, model=model(**EVERY))
    assert again.state_hash() == engine.state_hash()
