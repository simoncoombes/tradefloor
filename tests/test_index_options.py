"""Index options, their surface and their dealer (pt-v22 phase 2).

Eleven dials, 0.0 on every shipped preset: the switches `surface_ssvi`,
`options_index_listed` and `option_dealer_spread`, and the surface's
companions. The surface and the options read public state, draw nothing and
write only their own state, so a model with them on prints the stock prices,
closes and economy the same model prints with them off. These tests hold
that, rows SV1 (the strip's VIX is the published VIX) and SV2 (no static
arbitrage on any quoted chain) at every close, the dealer, the settlement on
the opening prints, the scan margin, accounts, and the state across a
snapshot, a fork and a replay.
"""
from __future__ import annotations

import math
import struct

import pytest

import tradefloor as tf
from tradefloor.manifest import state_hash
from tradefloor.portfolio import Portfolio, is_option_symbol
from tradefloor.replay import replay

SMALL = tf.Universe.random(10, seed=5)
UNIVERSE = tf.Universe.random(30, seed=111)
DIALS = ("options_index_listed", "surface_ssvi", "surface_skew_physical", "surface_skew_physical_slope",
         "surface_skew_premium", "surface_curvature", "surface_curvature_exponent",
         "surface_term_premium_short", "surface_term_premium_long", "surface_earnings_weight",
         "option_dealer_spread")
BASE = dict(index_level_listed=1.0, vix_intraday_live=1.0, forecast_horizon_sessions=252.0,
            forecast_vix_dispersion=0.3, forecast_vix_dispersion_half_life=31.5,
            forecast_vix_dispersion_skew=0.81)
SURFACE = dict(surface_ssvi=1.0, surface_skew_physical=-0.8, surface_skew_physical_slope=0.3,
               surface_skew_premium=-1.0, surface_curvature=1.0, surface_curvature_exponent=0.5,
               surface_term_premium_short=-0.03, surface_term_premium_long=0.1,
               surface_earnings_weight=1.0)
ON = dict(BASE, **SURFACE, options_index_listed=1.0, option_dealer_spread=0.004,
          margin_scan_coverage=0.99)


def model(**dials):
    return tf.ModelParams.from_preset("pt-v21", **dials)


def _off(dials):
    return model(**{k: v for k, v in dials.items() if k not in DIALS})


def options(engine):
    return [c for c in engine.contracts() if c["kind"] == "option"]


def day(engine):
    engine.open_market()
    engine.run_session(9, 30, 3, 390)
    engine.close_market()


def atm(engine, expiry, right="C"):
    rows = [r for r in engine.chain("IDX", expiry) if r["right"] == right]
    return min(rows, key=lambda r: abs(r["strike"] - r["forward"]))


def test_every_dial_is_off_on_every_shipped_preset_and_silent_in_the_digest():
    silent = set(tf.ModelParams.digest_silent_at_zero())
    for name in tf.preset_names():
        p = tf.ModelParams.from_preset(name)
        for dial in DIALS:
            assert getattr(p, dial) == 0.0, (name, dial)
    assert set(DIALS) <= silent
    assert model(**{d: 0.0 for d in DIALS}).fingerprint == "pt-v21"


@pytest.mark.parametrize("dials, words", [
    (dict(surface_ssvi=1.0), "index_level_listed"),
    (dict(index_level_listed=1.0, surface_curvature=1.0), "surface_ssvi"),
    (dict(index_level_listed=1.0, surface_ssvi=1.0, surface_curvature=2.0), "surface_curvature"),
    (dict(index_level_listed=1.0, surface_ssvi=1.0, surface_curvature_exponent=0.6), "surface_curvature_exponent"),
    (dict(index_level_listed=1.0, surface_ssvi=1.0, surface_earnings_weight=1.0), "forecast_horizon_sessions"),
    (dict(index_level_listed=1.0, surface_ssvi=1.0, options_index_listed=1.0), "option_dealer_spread"),
    (dict(index_level_listed=1.0, option_dealer_spread=0.004), "options_index_listed"),
])
def test_a_dial_without_what_it_reads_is_refused(dials, words):
    with pytest.raises(tf.ValidationError, match=words):
        model(**dials)


def test_off_nothing_is_listed_and_nothing_is_carried():
    engine = tf.Engine(seed=3, universe=SMALL, model=model(**BASE))
    for _ in range(3):
        day(engine)
    assert options(engine) == []
    snap = engine.state_snapshot()
    assert "options" not in snap and "options_book" not in snap
    with pytest.raises(tf.ValidationError):
        engine.surface("IDX")
    with pytest.raises(tf.ValidationError):
        engine.chain("IDX", 14)
    with pytest.raises(tf.ValidationError):
        engine.quote("IDX.O0014.C1000.00")
    assert engine.option_margin([("IDX.O0014.C1000.00", -1.0)]) is None


def _run(params, seed, days=10, steps=3, trade=False):
    engine = tf.Engine(seed=seed, universe=UNIVERSE, model=params)
    out = []
    ticks = 390 // steps
    for d in range(days):
        engine.open_market()
        for k in range(steps):
            if trade:
                expiry = options(engine)[0]["expiry"]
                call = atm(engine, expiry)
                engine.submit("a", call["symbol"], 40.0 if k % 2 else -60.0)
                put = atm(engine, expiry, "P")
                engine.submit("b", put["symbol"], 10.0, limit_price=max(0.05, put["bid"] or 0.05))
                engine.surface("IDX").strip_vix(21.0)
            minutes = 30 + k * ticks
            engine.run_session(9 + minutes // 60, minutes % 60, 3, ticks)
            out.append(engine.session_prices())
        engine.close_market()
        out.append(engine.prices())
        out.append(repr(sorted(engine.economy().items())))
        for field in ("price", "volume", "mispricing_s", "garch_variance"):
            out.append(engine.column(field))
    return out, engine


@pytest.mark.parametrize("seed", [3, 17])
def test_an_untraded_run_prints_the_same_prices_with_options_on(seed):
    off, _ = _run(_off(ON), seed)
    on, engine = _run(model(**ON), seed)
    assert off == on
    assert len(options(engine)) > 600


def test_the_same_prices_when_agents_trade_options_and_read_the_surface():
    off, _ = _run(_off(ON), 9, days=6)
    on, engine = _run(model(**ON), 9, days=6, trade=True)
    assert off == on
    assert engine.take_fills("a"), "the agent traded nothing"


def test_sv1_and_sv2_hold_at_every_close_and_within_a_session():
    engine = tf.Engine(seed=21, universe=SMALL, model=model(**ON))
    worst = 0.0
    for d in range(45):
        day(engine)
        s = engine.surface("IDX")
        worst = max(worst, abs(s.strip_vix(21.0) - engine.macro_state.vix))
        assert s.check() is None
        assert s.reached
        assert engine.option_arbitrage() == [], d
    # SV1: the Cboe formula on the 21-session strip less the published VIX.
    assert worst <= 1e-6
    engine.open_market()
    engine.run_session(9, 30, 3, 123)
    s = engine.surface("IDX")
    assert abs(s.strip_vix(21.0) - engine.live_vix) <= 1e-6
    assert engine.option_arbitrage() == []


def test_the_surface_reads_the_skew_asked_and_the_term_premia():
    engine = tf.Engine(seed=23, universe=SMALL, model=model(**ON))
    for _ in range(3):
        day(engine)
    s = engine.surface("IDX")
    vix = engine.macro_state.vix
    target = -0.8 + 0.3 * math.log(vix / 20.0) - 1.0
    assert s.skewness_target == pytest.approx(target, abs=1e-12)
    assert s.strip_skew(21.0) == pytest.approx(100.0 - 10.0 * target, abs=1e-5)
    assert s.skew == pytest.approx(s.strip_skew(21.0), abs=1e-6)
    # A flatter curvature reads less skew from the same skewness asked.
    flat = tf.Engine(seed=23, universe=SMALL, model=model(**dict(ON, surface_curvature=0.0)))
    for _ in range(3):
        day(flat)
    f = flat.surface("IDX")
    assert f.skewness_target is None and f.rho == 0.0
    assert f.strip_skew(21.0) == pytest.approx(100.0, abs=0.05)
    # The forward is the futures' carry; total variance rises with the tenor.
    assert s.forward(0.0) + s.dividends(0.0) == pytest.approx(engine.index_level["level"])
    for k in (-0.2, 0.0, 0.1):
        tv = [s.total_variance(k, t) for t in (6.0, 21.0, 63.0, 126.0, 252.0)]
        assert tv == sorted(tv)


def test_the_chain_settles_on_the_opening_prints_and_lists_the_next_expiry():
    engine = tf.Engine(seed=5, universe=SMALL, model=model(**ON))
    expiries = sorted({c["expiry"] for c in options(engine)})
    assert expiries == [14, 35, 56, 77, 98, 119, 182, 245]
    first = [c for c in options(engine) if c["expiry"] == 14]
    assert all(c["settlement"] == "opening_print_index" and c["multiplier"] == 100.0 for c in first)
    assert all(is_option_symbol(c["symbol"]) for c in first)
    for _ in range(14):
        day(engine)
    engine.open_market()
    opening = engine.index_level["level"]
    settled = [s for s in engine.settlements(14) if s["kind"] == "option"]
    # Every strike listed for the expiry, the range widened since its listing.
    assert len(settled) >= len(first)
    for s in settled:
        assert s["reference"] == opening
        strike = float(s["symbol"].split(".")[-2][1:] + "." + s["symbol"].split(".")[-1])
        right = s["symbol"].split(".")[-2][0]
        assert s["value"] == (max(0.0, opening - strike) if right == "C" else max(0.0, strike - opening))
    assert sorted({c["expiry"] for c in options(engine)}) == [35, 56, 77, 98, 119, 140, 182, 245]


def test_the_dealer_quotes_a_ladder_fills_and_moves_its_volatility():
    engine = tf.Engine(seed=7, universe=SMALL, model=model(**ON))
    for _ in range(3):
        day(engine)
    engine.open_market()
    engine.run_session(9, 30, 3, 10)
    expiry = options(engine)[8]["expiry"]
    call = atm(engine, expiry)
    q = engine.quote(call["symbol"])
    assert q["bid"] < q["mid"] < q["ask"]
    assert q["iv"] == q["surface_iv"] and q["open_interest"] == 0.0
    assert q["initial_margin"] > 0.0
    book = engine.book(call["symbol"])
    assert len(book.price_levels("sell", 50)) == 10
    r = engine.submit("a", call["symbol"], 80.0)
    assert r["filled"] == 80.0 and r["fills"][0]["price"] == q["ask"]
    after = engine.quote(call["symbol"])
    assert after["iv"] > q["iv"] and after["open_interest"] == 80.0
    put = engine.quote(call["symbol"].replace(".C", ".P"))
    assert put["iv"] == after["iv"], "the strike's put moved with its call"
    # A limit under the ask waits outside any book until the quote crosses.
    w = engine.submit("b", call["symbol"], -5.0, limit_price=after["ask"] + 5.0)
    assert w["resting"] == 5.0
    assert [o["order_id"] for o in engine.open_orders("b")] == [w["order_id"]]
    assert engine.cancel(w["order_id"])
    engine.run_session(9, 41, 3, 349)
    engine.close_market()
    with pytest.raises(tf.OrderError, match="session only"):
        engine.submit("a", call["symbol"], 1.0)


def test_the_scan_margins_shorts_nets_spreads_and_widens_with_the_coverage():
    engine = tf.Engine(seed=9, universe=SMALL, model=model(**ON))
    for _ in range(3):
        day(engine)
    expiry = options(engine)[0]["expiry"]
    rows = [r for r in engine.chain("IDX", expiry) if r["right"] == "P"]
    i = min(range(len(rows)), key=lambda j: abs(rows[j]["strike"] - rows[j]["forward"]))
    short, wing = rows[i]["symbol"], rows[i - 2]["symbol"]
    one = engine.option_margin([(short, -1.0)])
    assert one == engine.quote(short)["initial_margin"]
    spread = engine.option_margin([(short, -1.0), (wing, 1.0)])
    assert 0.0 < spread < one
    assert spread <= 100.0 * (rows[i]["strike"] - rows[i - 2]["strike"]) + 1e-9
    assert engine.option_margin([(short, -1.0), (short, 1.0)]) == 0.0
    wider = tf.Engine(seed=9, universe=SMALL, model=model(**dict(ON, margin_scan_coverage=0.999)))
    for _ in range(3):
        day(wider)
    assert wider.option_margin([(short, -1.0)]) > one


def test_a_portfolio_pays_the_premium_marks_at_mid_and_is_paid_at_expiry():
    engine = tf.Engine(seed=11, universe=SMALL, model=model(**ON))
    p = Portfolio(cash=1_000_000.0, owner="a")
    for _ in range(10):
        day(engine)
    engine.open_market()
    p.settle_open(engine)
    engine.run_session(9, 30, 3, 10)
    call = atm(engine, 14)
    fill = p.execute(engine, call["symbol"], 3.0)
    assert fill["quantity"] == 3.0
    assert p.cash == pytest.approx(1_000_000.0 - 300.0 * fill["price"])
    assert p.options[call["symbol"]].quantity == 3.0
    mid = engine.quote(call["symbol"])["mid"]
    assert p.net_worth(engine) == pytest.approx(p.cash + 300.0 * mid)
    assert p.margin_requirement(engine) == pytest.approx(engine.option_margin([(call["symbol"], 3.0)]))
    engine.run_session(9, 40, 3, 380)
    engine.close_market()
    p.settle_close(engine)
    for _ in range(3):
        engine.open_market()
        p.settle_open(engine)
        engine.run_session(9, 30, 3, 390)
        engine.close_market()
        p.settle_close(engine)
    engine.open_market()
    p.settle_open(engine)
    assert call["symbol"] not in p.options
    paid, = (s for s in p.settled if s["symbol"] == call["symbol"])
    opening = engine.index_level["level"]
    assert paid["price"] == max(0.0, opening - call["strike"])
    assert paid["cash"] == pytest.approx(300.0 * paid["price"])


def _traded_engine(seed=13):
    engine = tf.Engine(seed=seed, universe=SMALL, model=model(**ON))
    for _ in range(5):
        day(engine)
    engine.open_market()
    engine.run_session(9, 30, 3, 40)
    expiry = options(engine)[1]["expiry"]
    engine.submit("a", atm(engine, expiry)["symbol"], 30.0)
    put = atm(engine, expiry, "P")
    engine.submit("b", put["symbol"], 10.0, limit_price=put["bid"])
    engine.run_session(9, 70, 3, 10)
    return engine


def _go_on(engine):
    out = []
    engine.run_session(10, 20, 3, 340)
    out.append([engine.quote(c["symbol"]) for c in options(engine)[::37]])
    engine.close_market()
    for _ in range(12):
        day(engine)
        out.append([engine.quote(c["symbol"])["mid"] for c in options(engine)[::53]])
    out.append([s for s in engine.settlements() if s["kind"] == "option"][::17])
    out.append(engine.state_hash())
    return out


def test_a_mid_session_snapshot_restores_and_continues_like_the_original():
    engine = _traded_engine()
    snap = engine.state_snapshot()
    for key in ("options", "options_book"):
        assert key in snap, key
    assert state_hash(snap) == engine.state_hash()
    restored = tf.Engine(seed=99, universe=SMALL, model=model(**ON))
    restored.restore_state(snap)
    assert restored.state_hash() == engine.state_hash()
    assert _go_on(restored) == _go_on(engine)


def test_the_state_hash_reads_the_options():
    snap = _traded_engine().state_snapshot()
    raw = snap["options"]
    last, = struct.unpack("<d", raw[-8:])
    changed = dict(snap, options=raw[:-8] + struct.pack("<d", math.nextafter(last, math.inf)))
    assert state_hash(changed) != state_hash(snap)


def test_a_mid_session_fork_continues_like_the_original():
    engine = _traded_engine(seed=14)
    fork, = engine.fork(1)
    assert fork.state_hash() == engine.state_hash()
    assert _go_on(fork) == _go_on(engine)


def test_a_snapshot_with_options_is_refused_by_a_model_without_them():
    snap = _traded_engine().state_snapshot()
    off = tf.Engine(seed=99, universe=SMALL, model=_off(ON))
    with pytest.raises(tf.ValidationError, match="surface_ssvi"):
        off.restore_state(snap)


def test_the_order_log_replays_the_option_orders():
    engine = _traded_engine(seed=15)
    _go_on(engine)
    again = replay(engine.order_log, seed=15, universe=SMALL, model=model(**ON))
    assert again.state_hash() == engine.state_hash()
