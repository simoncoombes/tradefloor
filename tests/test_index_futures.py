"""Index futures and their night session (pt-v22 phase 1).

Two switches and two companions, each 0.0 on every shipped preset
(`futures_index_listed`, `night_session_steps`, `basis_sd`,
`basis_persistence`). A future reads the price index, the dividends and the
forecast and draws only on its own stream, so a model with every phase 1
switch on prints the stock prices, closes and economy the same model prints
with them off, across the night as well (NS3). These tests hold that, the
calendar and the symbols, the settlement on the opening prints (IF3), the
night's landing on the open (NS2 by construction), the basis's statistics,
the dividends in the carry, the books, and the futures' state across a fork,
a snapshot and a replay.
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

#: The forecast's derived dials, as tests/test_derivative_foundations.py
#: carries them.
FORECAST = dict(forecast_vix_dispersion=0.3, forecast_vix_dispersion_half_life=31.5,
                forecast_policy_shadow_discount=0.31, forecast_policy_persistence=0.62,
                forecast_policy_reversion=0.0475, forecast_policy_neutral=1.4)
#: The basis dials as fitted on pt-v21's held-out histories
#: (`tools/calibration/basis_dials.py derive`, seeds 8101 to 8124).
FUTURES = dict(futures_index_listed=1.0, basis_sd=3.753, basis_persistence=0.429,
               night_session_steps=8.0)
#: Every phase 1 switch built so far, on.
ON = dict(index_level_listed=1.0, vix_intraday_live=1.0, forecast_horizon_sessions=252.0,
          **FORECAST, **FUTURES)
NEW = ("futures_index_listed", "basis_sd", "basis_persistence", "night_session_steps")


def model(**dials):
    return tf.ModelParams.from_preset("pt-v21", **dials)


def floats(buf):
    return struct.unpack("<%dd" % (len(buf) // 8), buf)


def day(engine, night=True, steps=None):
    """One untraded day: the night before it (after the first), the open, a
    session and the close."""
    if night and engine.day_count:
        engine.run_night(steps)
    engine.open_market()
    engine.run_session(9, 30, 3, 390)
    engine.close_market()


# ── Off on every shipped preset ─────────────────────────────────────────────

def test_every_dial_is_off_on_every_shipped_preset_and_silent_in_the_digest():
    silent = set(tf.ModelParams.digest_silent_at_zero())
    for name in tf.preset_names():
        params = tf.ModelParams.from_preset(name)
        for dial in NEW:
            assert getattr(params, dial) == 0.0, (name, dial)
    for dial in NEW:
        assert dial in silent, dial
    assert model(**{d: 0.0 for d in NEW}).fingerprint == "pt-v21"
    assert model(**ON).fingerprint.startswith("custom-")


def test_off_nothing_is_listed_and_nothing_is_carried():
    engine = tf.Engine(seed=3, universe=SMALL, model="pt-v21")
    engine.run_days(2)
    assert engine.contracts() == []
    assert engine.settlements() == []
    assert engine.run_night(4) == 0
    with pytest.raises(tf.ValidationError, match="not a listed contract"):
        engine.quote("IDX.F0056")
    with pytest.raises(tf.ValidationError, match="no instrument"):
        engine.submit("a", "IDX.F0056", 1.0)
    snap = engine.state_snapshot()
    for key in ("futures", "derivatives_rng", "futures_book", "night_bridge"):
        assert key not in snap


def test_each_dial_is_refused_without_the_switch_it_reads():
    with pytest.raises(tf.ValidationError, match="index_level_listed"):
        model(futures_index_listed=1.0)
    with pytest.raises(tf.ValidationError, match="futures_index_listed"):
        model(index_level_listed=1.0, night_session_steps=4.0)
    with pytest.raises(tf.ValidationError, match="whole number"):
        model(index_level_listed=1.0, futures_index_listed=1.0, night_session_steps=2.5)
    with pytest.raises(tf.ValidationError, match="basis_persistence"):
        model(index_level_listed=1.0, futures_index_listed=1.0, basis_persistence=1.0)
    with pytest.raises(tf.ValidationError, match="a switch"):
        model(index_level_listed=1.0, futures_index_listed=0.5)


# ── NS3: no stock price moves ───────────────────────────────────────────────

def _untraded(params, seed, days=12, steps=5, pins=None):
    """Every print an untraded run makes: each step's tape, each close's
    prices, the macro state and four columns, in order, with the night
    walked between days (a no-op off) and any pins written between
    sessions."""
    engine = tf.Engine(seed=seed, universe=UNIVERSE, model=params)
    out = []
    ticks = 390 // steps
    for d in range(days):
        if d:
            engine.run_night(3)
            if pins and d in pins:
                engine.pin_macro(**pins[d])
            engine.run_night()
        engine.open_market()
        for k in range(steps):
            minutes = 30 + k * ticks
            engine.run_session(9 + minutes // 60, minutes % 60, 3, ticks)
            out.append(engine.session_prices())
        engine.close_market()
        out.append(engine.prices())
        out.append(repr(sorted(engine.economy().items())))
        for field in ("price", "volume", "mispricing_s", "garch_variance"):
            out.append(engine.column(field))
    return out, engine


@pytest.mark.parametrize("seed", [3, 17, 101, 9001])
def test_ns3_an_untraded_run_prints_the_same_prices_with_every_switch_on(seed):
    off, _ = _untraded(model(), seed)
    on, engine = _untraded(model(**ON), seed)
    assert len(off) == len(on)
    for i, (a, b) in enumerate(zip(off, on)):
        assert a == b, f"print {i} differs with the switches on"
    # And the futures ran: two listed, marked at every close, the night walked.
    assert len(engine.contracts()) == 2
    assert engine.quote(engine.contracts()[0]["symbol"])["mark"] is not None


def test_ns3_holds_across_a_pin_between_sessions_and_the_pin_moves_the_futures():
    pins = {4: dict(vix=38.0), 7: dict(federal_funds_rate=0.04, oil_price=95.0)}
    off, _ = _untraded(model(), 17, days=9, pins=pins)
    on, _ = _untraded(model(**ON), 17, days=9, pins=pins)
    assert off == on
    # The futures move when the pin is written, before any night step.
    engine = tf.Engine(seed=17, universe=UNIVERSE, model=model(**ON))
    for _ in range(4):
        day(engine)
    front = engine.contracts()[0]["symbol"]
    before = engine.quote(front)
    engine.pin_macro(vix=45.0)
    after = engine.quote(front)
    assert after["fair"] != before["fair"]
    assert after["mark"] == before["mark"]


def test_ns3_holds_when_agents_trade_the_futures_alone():
    """Futures flow writes nothing a stock price reads: a run whose agents
    trade only contracts prints the stocks of a run nobody trades."""
    off, _ = _untraded(model(), 101, days=6)
    engine = tf.Engine(seed=101, universe=UNIVERSE, model=model(**ON))
    on = []
    for d in range(6):
        if d:
            engine.run_night(4)
            engine.submit("night", engine.contracts()[0]["symbol"], 30.0)
            engine.run_night()
        engine.open_market()
        for k in range(5):
            front, back = (c["symbol"] for c in engine.contracts())
            engine.submit("a", front, 40.0 if k % 2 else -55.0)
            q = engine.quote(back)
            engine.submit("b", back, 10.0, limit_price=q["bid"] - 2 * q["tick"])
            minutes = 30 + k * 78
            engine.run_session(9 + minutes // 60, minutes % 60, 3, 78)
            on.append(engine.session_prices())
        engine.close_market()
        on.append(engine.prices())
        on.append(repr(sorted(engine.economy().items())))
        for field in ("price", "volume", "mispricing_s", "garch_variance"):
            on.append(engine.column(field))
    assert off == on
    assert engine.take_fills("a"), "the agents traded nothing"


# ── The calendar and the symbols ────────────────────────────────────────────

def test_the_front_two_quarterly_futures_are_listed_and_roll_at_expiry():
    engine = tf.Engine(seed=5, universe=SMALL, model=model(**ON))
    listed = engine.contracts()
    assert [c["symbol"] for c in listed] == ["IDX.F0056", "IDX.F0119"]
    assert [c["expiry"] for c in listed] == [56, 119]
    assert [c["roll"] for c in listed] == [50, 113]
    assert [c["front"] for c in listed] == [True, False]
    for c in listed:
        assert c["root"] == "IDX" and c["kind"] == "future"
        assert c["settlement"] == "opening_print_index"
        assert c["multiplier"] == 250.0 and c["tick"] == 0.05
    for _ in range(51):
        day(engine)
    # Session 51 is past the front's roll date.
    assert [c["front"] for c in engine.contracts()] == [False, True]
    for _ in range(5):
        day(engine)
    engine.run_night()
    engine.open_market()                      # session 56: the front settles
    assert [c["symbol"] for c in engine.contracts()] == ["IDX.F0119", "IDX.F0182"]
    assert engine.settlements(56)[0]["symbol"] == "IDX.F0056"
    assert engine.settlements(55) == []


def test_a_contract_symbol_round_trips_through_every_surface():
    engine = tf.Engine(seed=5, universe=SMALL, model=model(**ON))
    day(engine)
    for c in engine.contracts():
        symbol = c["symbol"]
        expiry = int(symbol.split(".F")[1])
        assert symbol == "IDX.F%04d" % expiry
        assert engine.quote(symbol)["symbol"] == symbol
        assert engine.book(symbol).best_bid is not None
    with pytest.raises(tf.ValidationError):
        engine.quote("IDX.F56")
    with pytest.raises(tf.ValidationError):
        engine.quote("IDX.F0057")
    with pytest.raises(tf.OrderError, match="not a listed contract"):
        engine.submit("a", "IDX.F0057", 1.0)


# ── IF3 and NS2: the open ───────────────────────────────────────────────────

def test_if3_the_future_settles_on_the_index_of_the_expiry_sessions_opening_prints():
    engine = tf.Engine(seed=9, universe=UNIVERSE, model=model(**ON))
    for _ in range(56):
        day(engine)
    engine.run_night()
    last = engine.quote("IDX.F0056")
    engine.open_market()
    opening = engine.index_level["level"]
    settled, = engine.settlements(56)
    assert settled["value"] == opening and settled["reference"] == opening
    assert abs(last["price"] - opening) / opening <= 1e-12
    assert abs(last["fair"] - opening) / opening <= 1e-12


def test_ns2_a_whole_night_lands_every_future_on_its_fair_value_at_the_open():
    engine = tf.Engine(seed=21, universe=UNIVERSE, model=model(**ON))
    for _ in range(10):
        day(engine)
    close = {c["symbol"]: engine.quote(c["symbol"]) for c in engine.contracts()}
    assert engine.run_night(3) == 3
    assert engine.run_night() == 5
    assert engine.run_night() == 0
    night = {s: engine.quote(s) for s in close}
    engine.open_market()
    for s, q in night.items():
        opened = engine.quote(s)
        assert q["fair"] == opened["fair"], s
        assert q["index"] == opened["index"], s
        assert q["mark"] == close[s]["mark"]
        # NS2's statistic, the 09:29 quote against fair value on the opening
        # index, is the basis alone.
        assert abs(q["price"] - opened["fair"] - q["basis_bp"] * opened["index"] / 1e4) < 1e-9


def test_the_night_is_refused_while_a_day_is_open():
    engine = tf.Engine(seed=21, universe=SMALL, model=model(**ON))
    day(engine)
    engine.open_market()
    with pytest.raises(tf.ValidationError, match="close_market"):
        engine.run_night()


# ── The price ───────────────────────────────────────────────────────────────

def test_the_quote_is_carry_fair_value_plus_the_basis():
    engine = tf.Engine(seed=4, universe=UNIVERSE, model=model(**ON))
    for _ in range(3):
        day(engine)
    engine.open_market()
    engine.run_session(9, 30, 3, 100)
    for c in engine.contracts():
        q = engine.quote(c["symbol"])
        tau = q["sessions_to_expiry"] / 252.0
        fair = (q["index"] - q["dividends"]) * math.exp(q["rate"] * tau)
        assert abs(fair - q["fair"]) <= 1e-12 * fair
        assert abs(q["price"] - (q["fair"] + q["basis_bp"] * q["index"] / 1e4)) <= 1e-9
        assert q["index"] == engine.index_level["level"]
        assert q["dividends"] > 0.0
        assert q["bid"] < q["price"] < q["ask"] + 1e-9
        assert round(q["ask"] - q["bid"], 6) == round(q["tick"], 6)
        assert q["initial_margin"] is None
    front, back = (engine.quote(c["symbol"]) for c in engine.contracts())
    assert back["dividends"] > front["dividends"]
    assert back["sessions_to_expiry"] - front["sessions_to_expiry"] == pytest.approx(63.0)


def test_the_basis_reads_its_dials_at_the_close():
    params = model(index_level_listed=1.0, futures_index_listed=1.0, basis_sd=4.0,
                   basis_persistence=0.6)
    engine = tf.Engine(seed=7, universe=SMALL, model=params)
    basis = []
    for _ in range(500):
        engine.run_days(1, record=False)
        q = engine.quote(engine.contracts()[-1]["symbol"])
        basis.append(1e4 * math.log(q["mark"] / q["fair"]))
    n = len(basis)
    mean = sum(basis) / n
    var = sum((b - mean) ** 2 for b in basis) / n
    lag = sum((a - mean) * (b - mean) for a, b in zip(basis, basis[1:])) / (n - 1) / var
    assert abs(math.sqrt(var) - 4.0) < 0.6, math.sqrt(var)
    assert abs(lag - 0.6) < 0.12, lag


# ── The book ────────────────────────────────────────────────────────────────

def test_an_agents_order_walks_the_book_and_its_flow_moves_the_basis():
    engine = tf.Engine(seed=8, universe=UNIVERSE, model=model(**dict(ON, basis_sd=0.0)))
    for _ in range(2):
        day(engine)
    engine.open_market()
    engine.run_session(9, 30, 3, 10)
    front = engine.contracts()[0]["symbol"]
    before = engine.quote(front)
    size = 0.05 * before["daily_volume"]
    report = engine.submit("a", front, size)
    assert report["filled"] == pytest.approx(size)
    assert report["average_price"] > before["ask"]
    assert {f["counterparty"] for f in report["fills"]} >= {"mm", "depth"}
    engine.run_session(9, 40, 3, 1)
    after = engine.quote(front)
    assert after["basis_bp"] > 0.5, after["basis_bp"]
    # A resting order fills when the book comes to it, and only then.
    bid = after["bid"]
    resting = engine.submit("b", front, -3.0, limit_price=bid + 20 * after["tick"])
    assert resting["resting"] == 3.0
    assert [o["ticker"] for o in engine.open_orders("b")] == [front]
    assert engine.cancel(resting["order_id"], agent="b")
    assert engine.open_orders("b") == []
    filled = engine.submit("c", front, -2.0, limit_price=bid + 0.5 * after["tick"])
    assert filled["resting"] == 2.0
    engine.submit("d", front, 2.0 * after["daily_volume"] * 0.01 + 50.0)
    engine.run_session(9, 41, 3, 1)
    fills = engine.take_fills("c")
    assert fills and fills[0]["ticker"] == front
    assert sum(f["quantity"] for f in fills) == pytest.approx(2.0)


# ── Fork, snapshot and replay ───────────────────────────────────────────────

def _traded_night_engine(seed=13):
    engine = tf.Engine(seed=seed, universe=SMALL, model=model(**ON))
    for _ in range(4):
        day(engine, steps=None)
        front = engine.contracts()[0]["symbol"]
        engine.submit("a", front, 12.0)
    engine.run_night(3)
    q = engine.quote(engine.contracts()[1]["symbol"])
    engine.submit("b", q["symbol"], -4.0, limit_price=q["ask"] + 30 * q["tick"])
    engine.submit("c", engine.contracts()[0]["symbol"], -7.0)
    return engine


def _go_on(engine):
    out = []
    engine.run_night()
    for _ in range(3):
        day(engine)
        out.append([engine.quote(c["symbol"]) for c in engine.contracts()])
        out.append(engine.prices())
        out.append(engine.state_hash())
    out.append(engine.take_fills())
    return out


def test_a_mid_night_snapshot_restores_and_continues_like_the_original():
    engine = _traded_night_engine()
    snap = engine.state_snapshot()
    for key in ("futures", "derivatives_rng", "futures_book", "night_bridge"):
        assert key in snap, key
    assert state_hash(snap) == engine.state_hash()
    restored = tf.Engine(seed=99, universe=SMALL, model=model(**ON))
    restored.restore_state(snap)
    assert restored.state_hash() == engine.state_hash()
    assert _go_on(restored) == _go_on(engine)


def test_a_mid_night_fork_continues_like_the_original():
    engine = _traded_night_engine(seed=14)
    fork, = engine.fork(1)
    assert fork.state_hash() == engine.state_hash()
    assert _go_on(fork) == _go_on(engine)


def test_a_snapshot_with_futures_is_refused_by_a_model_without_them():
    engine = _traded_night_engine()
    snap = engine.state_snapshot()
    off = tf.Engine(seed=99, universe=SMALL, model=model(index_level_listed=1.0))
    with pytest.raises(tf.ValidationError):
        off.restore_state(snap)


def test_the_order_log_replays_the_night_and_the_contract_orders():
    engine = _traded_night_engine(seed=15)
    _go_on(engine)
    again = replay(engine.order_log, seed=15, universe=SMALL, model=model(**ON))
    assert again.state_hash() == engine.state_hash()
    assert any(e["op"] == "run_night" for e in engine.order_log)
