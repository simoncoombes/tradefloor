"""VIX futures (pt-v22 phase 1).

One switch and three companions, each 0.0 on every shipped preset
(`futures_vix_listed`, `futures_vix_live_fast_share`,
`futures_vix_live_fast_half_life`, `futures_vix_live_slow_half_life`). A VIX
future reads the published and live VIX and the forecast and draws nothing,
so a model with it on prints the stock prices, closes, economy and VIX the
same model prints with it off (NS3). These tests hold that, the calendar,
the close marks against the curve the VIX-law screen priced, the settlement
on the published VIX at the expiry open (VF7), the intraday price, the
books, and the state across a snapshot, a fork and a replay.
"""
from __future__ import annotations

import math

import pytest

import tradefloor as tf
from tradefloor.manifest import state_hash
from tradefloor.replay import replay

UNIVERSE = tf.Universe.random(40, seed=111)
SMALL = tf.Universe.random(8, seed=5)

#: The VIX futures' premium, frozen, as `derivatives::VIX_FUTURE` carries it.
PREMIUM = dict(centre=19.3197, k=0.26, p=0.4925, beta=0.36, tau=148.0)
#: The intraday loading as fitted on R22V3's held-out histories, rounded.
LOADING = dict(futures_vix_live_fast_share=0.27, futures_vix_live_fast_half_life=1.27,
               futures_vix_live_slow_half_life=28.6)
FORECAST = dict(forecast_vix_dispersion=0.3, forecast_vix_dispersion_half_life=31.5,
                forecast_vix_dispersion_skew=0.81, forecast_policy_shadow_discount=0.36,
                forecast_policy_persistence=0.6, forecast_policy_reversion=0.05,
                forecast_policy_neutral=1.48)
ON = dict(index_level_listed=1.0, vix_intraday_live=1.0, forecast_horizon_sessions=130.0,
          **FORECAST, futures_vix_listed=1.0, **LOADING)
#: With the index futures and their night as well.
EVERY = dict(ON, futures_index_listed=1.0, basis_sd=3.753, basis_persistence=0.429,
             night_session_steps=8.0)
NEW = ("futures_vix_listed", "futures_vix_live_fast_share", "futures_vix_live_fast_half_life",
       "futures_vix_live_slow_half_life")


def model(**dials):
    return tf.ModelParams.from_preset("pt-v21", **dials)


def premium(n, vix):
    if n <= 1:
        return 0.0
    p = PREMIUM
    return p["k"] * (n - 1) ** p["p"] + p["beta"] * (1 - math.exp(-(n - 1) / p["tau"])) * (vix - p["centre"])


def vix_contracts(engine):
    return [c for c in engine.contracts() if c["root"] == "VIX"]


def day(engine):
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


def test_off_nothing_is_listed_and_nothing_is_carried():
    engine = tf.Engine(seed=3, universe=SMALL, model=model(index_level_listed=1.0,
                                                           forecast_horizon_sessions=130.0))
    engine.run_days(20)
    assert vix_contracts(engine) == []
    assert engine.settlements() == []
    snap = engine.state_snapshot()
    assert "vix_futures" not in snap and "vix_futures_book" not in snap
    with pytest.raises(tf.ValidationError):
        engine.quote("VIX.F0035")


def test_the_switch_needs_a_forecast_to_its_sixth_contract_and_the_loading_needs_the_switch():
    with pytest.raises(tf.ValidationError, match="forecast_horizon_sessions"):
        model(forecast_horizon_sessions=125.0, futures_vix_listed=1.0)
    with pytest.raises(tf.ValidationError, match="forecast_horizon_sessions"):
        model(futures_vix_listed=1.0)
    for dial in NEW[1:]:
        with pytest.raises(tf.ValidationError, match="futures_vix_listed"):
            model(forecast_horizon_sessions=130.0, **{dial: 0.5})
    model(forecast_horizon_sessions=126.0, futures_vix_listed=1.0)


# ── NS3: nothing a price reads ──────────────────────────────────────────────

def _untraded(params, seed, days=12, steps=5, pins=None, trade=False):
    """Every print a run makes: each step's tape, each close's prices, the
    macro state and four columns, with pins written between sessions and,
    with `trade`, agents trading the VIX futures alone."""
    engine = tf.Engine(seed=seed, universe=UNIVERSE, model=params)
    out = []
    ticks = 390 // steps
    for d in range(days):
        if d and pins and d in pins:
            engine.pin_macro(**pins[d])
        engine.open_market()
        for k in range(steps):
            if trade and vix_contracts(engine):
                front, second = (c["symbol"] for c in vix_contracts(engine)[:2])
                engine.submit("a", front, 300.0 if k % 2 else -450.0)
                q = engine.quote(second)
                engine.submit("b", second, 40.0, limit_price=q["bid"] - 2 * q["tick"])
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
def test_ns3_an_untraded_run_prints_the_same_prices_with_the_vix_futures_on(seed):
    base = {k: v for k, v in ON.items() if k not in NEW}
    off, _ = _untraded(model(**base), seed)
    on, engine = _untraded(model(**ON), seed)
    assert off == on
    assert len(vix_contracts(engine)) == 6
    assert engine.quote(vix_contracts(engine)[0]["symbol"])["mark"] is not None


def test_ns3_holds_across_an_expiry_and_with_the_index_futures_on():
    base = {k: v for k, v in EVERY.items() if k not in NEW}
    off, _ = _untraded(model(**base), 101, days=40, steps=2)
    on, engine = _untraded(model(**EVERY), 101, days=40, steps=2)
    assert off == on
    assert [s["symbol"] for s in engine.settlements() if s["root"] == "VIX"] == ["VIX.F0014", "VIX.F0035"]


def test_ns3_holds_when_agents_trade_the_vix_futures_alone():
    base = {k: v for k, v in ON.items() if k not in NEW}
    off, _ = _untraded(model(**base), 17, days=8)
    on, engine = _untraded(model(**ON), 17, days=8, trade=True)
    assert off == on
    assert engine.take_fills("a"), "the agent traded nothing"


def test_ns3_holds_across_a_pin_and_the_pin_moves_the_vix_futures_at_once():
    pins = {4: dict(vix=38.0)}
    base = {k: v for k, v in ON.items() if k not in NEW}
    off, _ = _untraded(model(**base), 17, days=7, pins=pins)
    on, _ = _untraded(model(**ON), 17, days=7, pins=pins)
    assert off == on
    engine = tf.Engine(seed=17, universe=UNIVERSE, model=model(**ON))
    for _ in range(4):
        day(engine)
    front = vix_contracts(engine)[0]["symbol"]
    before = engine.quote(front)
    engine.pin_macro(vix=38.0)
    after = engine.quote(front)
    assert after["fair"] > before["fair"]
    assert after["mark"] == before["mark"]


# ── The calendar, the marks and the settlement ──────────────────────────────

def test_six_monthly_contracts_are_listed_from_the_first_close_and_settle_at_expiry():
    engine = tf.Engine(seed=5, universe=SMALL, model=model(**ON))
    assert vix_contracts(engine) == []
    day(engine)
    listed = vix_contracts(engine)
    assert [c["expiry"] for c in listed] == [14, 35, 56, 77, 98, 119]
    assert [c["symbol"] for c in listed][:2] == ["VIX.F0014", "VIX.F0035"]
    assert all(c["settlement"] == "published_vix_at_open" and c["multiplier"] == 1000.0 for c in listed)
    assert [c["front"] for c in listed] == [True] + [False] * 5
    for _ in range(9):
        day(engine)
    # Session 10's close: the next session is 8 before F0014's expiry, past its roll.
    assert [c["front"] for c in vix_contracts(engine)][:2] == [False, True]
    for _ in range(4):
        day(engine)
    assert vix_contracts(engine)[0]["expiry"] == 14
    published = engine.macro_fields["vix"]
    engine.open_market()
    settled, = (s for s in engine.settlements(14) if s["root"] == "VIX")
    assert settled["value"] == published and settled["reference"] == published
    assert [c["expiry"] for c in vix_contracts(engine)] == [35, 56, 77, 98, 119, 140]


def test_the_close_marks_are_the_screens_curve_and_vf7_holds_to_the_bit():
    """At the close of session s a contract n = expiry - s sessions out is
    marked at the forecast's expected published VIX after n - 1 closes (the
    published VIX at n = 1) plus the frozen premium, as the pt-v22 VIX-law
    screen priced it."""
    engine = tf.Engine(seed=7, universe=SMALL, model=model(**ON))
    published, worst, horizons = [], 0.0, set()
    for _ in range(150):
        day(engine)
        f = engine.forecast()
        pub = engine.macro_fields["vix"]
        published.append(pub)
        s = len(published) - 1
        for c in vix_contracts(engine):
            n = c["expiry"] - s
            horizons.add(n)
            expected = pub if n == 1 else f["vix"][n - 2]
            q = engine.quote(c["symbol"])
            worst = max(worst, abs(q["mark"] - (expected + premium(n, pub))))
            assert q["mark"] == q["price"]
    assert worst < 1e-12, worst
    assert min(horizons) == 1 and max(horizons) == 126
    settled = [s for s in engine.settlements() if s["root"] == "VIX"]
    assert len(settled) == 7
    for s in settled:
        assert s["value"] == published[s["session"] - 1] == s["reference"]


# ── Within a session ────────────────────────────────────────────────────────

def test_the_contract_settling_on_tonights_vix_ends_the_session_on_the_live_vix():
    engine = tf.Engine(seed=9, universe=SMALL, model=model(**ON))
    for _ in range(34):
        day(engine)
    front = vix_contracts(engine)[0]
    assert front["expiry"] == 35
    engine.open_market()
    first = None
    for k in range(390):
        engine.run_session(9 + (30 + k) // 60, (30 + k) % 60, 3, 1)
        q = engine.quote(front["symbol"])
        assert q["loading"] == 1.0
        assert q["index"] == engine.live_vix
        first = first if first is not None else q["premium"]
    assert first > 0.0
    assert q["premium"] == 0.0
    assert q["price"] == pytest.approx(engine.live_vix, abs=1e-12)
    engine.close_market()
    assert engine.quote(front["symbol"])["mark"] == engine.macro_fields["vix"]


def test_the_intraday_loading_is_the_two_parts_and_zero_dials_hold_the_deferred_marks():
    engine = tf.Engine(seed=9, universe=SMALL, model=model(**ON))
    for _ in range(20):
        day(engine)
    engine.open_market()
    engine.run_session(9, 30, 3, 60)
    w, h1, h2 = (LOADING[k] for k in NEW[1:])
    for c in vix_contracts(engine):
        q = engine.quote(c["symbol"])
        m = c["expiry"] - 20
        lam = w * 0.5 ** ((m - 1) / h1) + (1 - w) * 0.5 ** ((m - 1) / h2)
        assert q["loading"] == pytest.approx(lam, rel=1e-12), (m, q["loading"], lam)
    # With the loading's dials at 0.0 only the contract that settles on
    # tonight's VIX follows the live VIX: the rest move by their premium's
    # roll alone.
    still = tf.Engine(seed=9, universe=SMALL, model=model(**{k: v for k, v in ON.items() if k not in NEW[1:]}))
    for _ in range(20):
        day(still)
    still.open_market()
    marks = {c["symbol"]: still.quote(c["symbol"])["mark"] for c in vix_contracts(still)}
    still.run_session(9, 30, 3, 200)
    for c in vix_contracts(still):
        q = still.quote(c["symbol"])
        assert q["loading"] == 0.0
        expected_plus_roll = q["expected"] + q["premium"]
        assert q["price"] == pytest.approx(expected_plus_roll, abs=1e-12)
        assert abs(q["price"] - marks[c["symbol"]]) < 0.1


# ── The book ────────────────────────────────────────────────────────────────

def test_an_agents_order_walks_the_book_rests_and_its_flow_marks_the_price():
    engine = tf.Engine(seed=11, universe=SMALL, model=model(**ON))
    for _ in range(5):
        day(engine)
    engine.open_market()
    engine.run_session(9, 30, 3, 5)
    front, deferred = vix_contracts(engine)[0]["symbol"], vix_contracts(engine)[4]["symbol"]
    q = engine.quote(front)
    assert q["ask"] - q["bid"] == pytest.approx(q["tick"])
    big = engine.submit("a", front, 20_000.0)
    assert big["filled"] == 20_000.0 and big["average_price"] > q["ask"]
    rest = engine.submit("a", front, -50.0, limit_price=q["ask"] + 2.0)
    assert rest["resting"] == 50.0
    assert [o["ticker"] for o in engine.open_orders("a")] == [front]
    engine.run_session(9, 35, 3, 1)
    assert engine.quote(front)["basis_bp"] > 0.0
    # A deferred contract quotes the maker's ladder alone: ten levels a side.
    book = engine.book(deferred)
    assert len(book.price_levels("sell", 50)) == 10 and len(book.price_levels("buy", 50)) == 10
    assert len(engine.book(front).price_levels("sell", 50)) > 10
    assert engine.cancel(rest["order_id"])
    fills = engine.take_fills("a")
    assert fills and all(f["ticker"] == front for f in fills)
    engine.run_session(9, 36, 3, 384)
    engine.close_market()
    with pytest.raises(tf.OrderError, match="session only"):
        engine.submit("a", front, 1.0)


# ── State ───────────────────────────────────────────────────────────────────

def _traded_engine(seed=13):
    """An engine mid-session, its VIX futures traded and one settled."""
    engine = tf.Engine(seed=seed, universe=SMALL, model=model(**EVERY))
    for _ in range(16):
        engine.open_market()
        engine.run_session(9, 30, 3, 390)
        engine.close_market()
        engine.run_night()
    engine.open_market()
    engine.run_session(9, 30, 3, 40)
    front = vix_contracts(engine)[0]["symbol"]
    engine.submit("a", front, 500.0)
    q = engine.quote(front)
    engine.submit("b", front, 30.0, limit_price=q["bid"] - 3 * q["tick"])
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
    for key in ("vix_futures", "vix_futures_book"):
        assert key in snap, key
    assert state_hash(snap) == engine.state_hash()
    restored = tf.Engine(seed=99, universe=SMALL, model=model(**EVERY))
    restored.restore_state(snap)
    assert restored.state_hash() == engine.state_hash()
    assert _go_on(restored) == _go_on(engine)


def test_the_state_hash_reads_the_vix_futures():
    import struct
    engine = _traded_engine()
    snap = engine.state_snapshot()
    raw = snap["vix_futures"]
    # The last number is the last settlement's value: move it by one ulp.
    last, = struct.unpack("<d", raw[-8:])
    changed = dict(snap, vix_futures=raw[:-8] + struct.pack("<d", math.nextafter(last, math.inf)))
    assert state_hash(changed) != state_hash(snap)


def test_a_mid_session_fork_continues_like_the_original():
    engine = _traded_engine(seed=14)
    fork, = engine.fork(1)
    assert fork.state_hash() == engine.state_hash()
    assert _go_on(fork) == _go_on(engine)


def test_a_snapshot_with_vix_futures_is_refused_by_a_model_without_them():
    engine = _traded_engine()
    snap = engine.state_snapshot()
    off = tf.Engine(seed=99, universe=SMALL, model=model(**{k: v for k, v in EVERY.items() if k not in NEW}))
    with pytest.raises(tf.ValidationError, match="futures_vix_listed"):
        off.restore_state(snap)


def test_the_order_log_replays_the_vix_futures_orders():
    engine = _traded_engine(seed=15)
    _go_on(engine)
    again = replay(engine.order_log, seed=15, universe=SMALL, model=model(**EVERY))
    assert again.state_hash() == engine.state_hash()
