"""American options on the roster's names (pt-v22 phase 3).

Three dials, 0.0 on every shipped preset: the switch `options_names_listed`
and the names' surfaces' `surface_name_premium` and `surface_name_skew`.
A name option, its surface and its delivery read public state, draw
nothing and write only their own state, so a model with them on prints the
stock prices, closes and economy the same model prints with them off, and
an agent's exercise moves shares between accounts and no price. These tests
hold that, the listing and its symbols, the tree's quotes, rows AX1 and AX2
at every close, the delivery of shares and cash into a Portfolio (short
stock from an assignment included), the harness's tf.Exercise, the scan
margin with stock beside the options, and the state across a snapshot, a
fork and a replay.
"""
from __future__ import annotations

import math
import pathlib
import struct

import pytest

import tradefloor as tf
from tradefloor.manifest import state_hash
from tradefloor.portfolio import Portfolio, is_name_option
from tradefloor.render import TextRenderer
from tradefloor.replay import replay

SMALL = tf.Universe.random(10, seed=5)
UNIVERSE = tf.Universe.random(30, seed=111)
DIALS = ("options_names_listed", "surface_name_premium", "surface_name_skew", "option_position_limit")
BASE = dict(index_level_listed=1.0, vix_intraday_live=1.0, forecast_horizon_sessions=252.0,
            forecast_vix_dispersion=0.3, forecast_vix_dispersion_half_life=31.5,
            forecast_vix_dispersion_skew=0.81)
SURFACE = dict(surface_ssvi=1.0, surface_skew_physical=-0.8, surface_skew_physical_slope=0.3,
               surface_skew_premium=-1.0, surface_skew_premium_slope=0.5, surface_curvature=1.0,
               surface_curvature_exponent=0.5, surface_term_premium_short=-0.03,
               surface_term_premium_long=0.1, surface_earnings_weight=1.0)
ON = dict(BASE, **SURFACE, options_names_listed=1.0, surface_name_premium=0.1, surface_name_skew=0.5,
          option_dealer_spread=0.004, margin_scan_coverage=0.99)


def model(**dials):
    return tf.ModelParams.from_preset("pt-v21", **dials)


def _off(dials):
    # The spread is the dealer's, which quotes nothing with the switch off.
    return model(**{k: v for k, v in dials.items() if k not in DIALS + ("option_dealer_spread",)})


def names(engine, ticker=None):
    return [c for c in engine.contracts()
            if is_name_option(c["symbol"]) and (ticker is None or c["root"] == ticker)]


def day(engine):
    engine.open_market()
    engine.run_session(9, 30, 3, 390)
    engine.close_market()


def strike_near(engine, ticker, expiry, right, offset=0):
    """The listed `right` of `ticker` at `expiry` whose strike is `offset`
    grid steps from the one nearest the price."""
    rows = sorted((c for c in names(engine, ticker) if c["expiry"] == expiry and c["right"] == right),
                  key=lambda c: c["strike"])
    price = engine.prices_dict()[ticker] if hasattr(engine, "prices_dict") else _price(engine, ticker)
    i = min(range(len(rows)), key=lambda j: abs(rows[j]["strike"] - price))
    return rows[max(0, min(len(rows) - 1, i + offset))]


def _price(engine, ticker):
    values = struct.unpack("<%dd" % len(engine.tickers), engine.prices())
    return dict(zip(engine.tickers, values))[ticker]


def test_every_dial_is_off_on_every_shipped_preset_and_silent_in_the_digest():
    silent = set(tf.ModelParams.digest_silent_at_zero())
    for name in tf.preset_names():
        p = tf.ModelParams.from_preset(name)
        for dial in DIALS:
            assert getattr(p, dial) == 0.0, (name, dial)
    assert set(DIALS) <= silent
    assert model(**{d: 0.0 for d in DIALS}).fingerprint == "pt-v21"


@pytest.mark.parametrize("dials, words", [
    (dict(BASE, options_names_listed=1.0), "surface_ssvi"),
    (dict(index_level_listed=1.0, surface_ssvi=1.0, options_names_listed=1.0), "forecast_horizon_sessions"),
    (dict(BASE, surface_ssvi=1.0, surface_name_premium=0.1), "options_names_listed"),
    (dict(BASE, surface_ssvi=1.0, options_names_listed=1.0, surface_name_skew=1.5), "surface_name_skew"),
    (dict(BASE, surface_ssvi=1.0, options_names_listed=1.0, surface_name_premium=-0.6), "surface_name_premium"),
    (dict(BASE, surface_ssvi=1.0, options_names_listed=0.5), "switch"),
])
def test_a_dial_without_what_it_reads_is_refused(dials, words):
    with pytest.raises(tf.ValidationError, match=words):
        model(**dials)


def test_the_spread_dial_reads_the_name_options_without_the_index_options():
    p = model(**ON)
    assert p.options_index_listed == 0.0 and p.option_dealer_spread == 0.004


def test_off_nothing_is_listed_and_nothing_is_carried():
    engine = tf.Engine(seed=3, universe=SMALL, model=_off(ON))
    for _ in range(3):
        day(engine)
    assert names(engine) == [] and engine.name_option_roots() == []
    assert not engine.lists_name_options
    snap = engine.state_snapshot()
    assert not {"name_options", "name_options_book", "name_options_exercise"} & set(snap)
    with pytest.raises(tf.ValidationError):
        engine.surface(engine.tickers[0])
    with pytest.raises(tf.OrderError, match="options_names_listed"):
        engine.exercise("a", f"{engine.tickers[0]}.O0014.C10.00", 1.0)
    with pytest.raises(tf.ValidationError):
        engine.exercise_violations()


def test_every_name_lists_its_chains_from_the_first_close():
    engine = tf.Engine(seed=5, universe=SMALL, model=model(**ON))
    assert engine.lists_name_options and names(engine) == []
    day(engine)
    assert engine.name_option_roots() == list(engine.tickers)
    listed = names(engine)
    assert {c["expiry"] for c in listed} == {14, 35, 56, 119, 182, 245}
    for c in listed:
        assert c["settlement"] == "physical_at_close" and c["multiplier"] == 100.0
        assert c["kind"] == "option" and c["root"] in engine.tickers
        parsed = tf.contracts.parse(c["symbol"])
        assert parsed["root"] == c["root"]
        assert tf.contracts.format(parsed["root"], parsed["expiry"], parsed["right"],
                                   parsed["strike"]) == c["symbol"]
    # Ten strikes either side of each name's price, on its price tier.
    for ticker in engine.tickers:
        price = _price(engine, ticker)
        step = 0.5 if price < 5 else 1.0 if price < 50 else 2.5 if price < 100 else 5.0 if price < 200 else 10.0
        strikes = sorted({c["strike"] for c in names(engine, ticker) if c["expiry"] == 14})
        gaps = {round(b - a, 2) for a, b in zip(strikes, strikes[1:])}
        assert gaps == {step}, (ticker, price, gaps)
        assert strikes[-1] >= price + 9.5 * step


def _run(params, seed, days=8, steps=3, trade=False):
    engine = tf.Engine(seed=seed, universe=UNIVERSE, model=params)
    out = []
    ticks = 390 // steps
    for d in range(days):
        engine.open_market()
        for k in range(steps):
            if trade and d:
                ticker = engine.tickers[(d + k) % 7]
                expiry = sorted({c["expiry"] for c in names(engine, ticker)})[k % 3]
                call = strike_near(engine, ticker, expiry, "C", -2)
                engine.submit("a", call["symbol"], 6.0 if k % 2 else -4.0)
                put = strike_near(engine, ticker, expiry, "P", 1)
                engine.submit("b", put["symbol"], -5.0)
                if k == 1:
                    # Just bought: exercise one at the close.
                    engine.exercise("a", call["symbol"], 1.0)
                engine.surface(ticker).iv(0.0, 21.0)
            minutes = 30 + k * ticks
            engine.run_session(9 + minutes // 60, minutes % 60, 3, ticks)
            out.append(engine.session_prices())
        engine.close_market()
        out.append(engine.prices())
        out.append(repr(sorted(engine.economy().items())))
        for field in ("price", "volume", "mispricing_s", "garch_variance"):
            out.append(engine.column(field))
        if trade:
            assert engine.exercise_violations() == [], d
    return out, engine


def test_an_untraded_run_prints_the_same_prices_with_name_options_on():
    off, _ = _run(_off(ON), 3)
    on, engine = _run(model(**ON), 3)
    assert off == on
    assert len(names(engine)) > 30 * 6 * 2 * 15


def test_the_same_prices_when_agents_trade_and_exercise_name_options():
    off, _ = _run(_off(ON), 9, days=6)
    on, engine = _run(model(**ON), 9, days=6, trade=True)
    assert off == on
    assert engine.take_fills("a") and engine.take_fills("b"), "the agents traded nothing"
    assert any(s["event"] == "exercise" and s["agent"] == "a" for s in engine.settlements())


def test_a_quote_is_a_tree_on_the_names_surface():
    engine = tf.Engine(seed=7, universe=SMALL, model=model(**ON))
    for _ in range(3):
        day(engine)
    engine.open_market()
    engine.run_session(9, 30, 3, 30)
    ticker = engine.tickers[2]
    s = engine.surface(ticker)
    assert s.root == ticker and s.rho == pytest.approx(0.5 * engine.surface("IDX").rho)
    assert s.check() is None
    expiry = sorted({c["expiry"] for c in names(engine, ticker)})[1]
    rows = engine.chain(ticker, expiry)
    price = _price(engine, ticker)
    for r in rows:
        assert r["root"] == ticker and r["multiplier"] == 100.0 and r["index"] == price
        intrinsic = max(0.0, price - r["strike"]) if r["right"] == "C" else max(0.0, r["strike"] - price)
        assert r["mid"] >= intrinsic - 1e-12
        assert r["ask"] > r["mid"] and (r["bid"] is None or r["bid"] < r["mid"])
        k = math.log(r["strike"] / r["forward"])
        assert r["surface_iv"] == pytest.approx(s.iv(k, r["sessions_to_expiry"]), rel=1e-12)
    q = engine.quote(rows[len(rows) // 2]["symbol"])
    assert q["initial_margin"] > 0.0
    summary = engine.option_expiries(ticker)
    assert [x["expiry"] for x in summary] == sorted({c["expiry"] for c in names(engine, ticker)})
    assert all(x["root"] == ticker for x in summary)


def test_an_agent_exercises_and_the_portfolio_takes_the_shares_at_the_strike():
    engine = tf.Engine(seed=11, universe=SMALL, model=model(**ON))
    p = Portfolio(cash=1_000_000.0, owner="a")
    for _ in range(2):
        day(engine)
    ticker = engine.tickers[1]
    engine.open_market()
    p.settle_open(engine)
    engine.run_session(9, 30, 3, 10)
    expiry = sorted({c["expiry"] for c in names(engine, ticker)})[1]
    call = strike_near(engine, ticker, expiry, "C", -3)
    fill = p.execute(engine, call["symbol"], 4.0)
    assert fill["quantity"] == 4.0
    assert p.exercise(engine, call["symbol"], 3.0) == 3.0
    with pytest.raises(tf.OrderError):
        p.exercise(engine, call["symbol"], 2.0)
    assert engine.exercise_instructions("a") == [{"agent": "a", "symbol": call["symbol"], "quantity": 3.0}]
    engine.run_session(9, 40, 3, 380)
    cash = p.cash
    engine.close_market()
    p.settle_close(engine)
    row, = (s for s in engine.settlements(engine.day_count - 1) if s["agent"] == "a")
    assert (row["event"], row["quantity"], row["shares"]) == ("exercise", 3.0, 300.0)
    assert row["cash"] == -300.0 * call["strike"]
    # AX2 in the account: the shares and the cash the row says, to the cent.
    assert p.positions[ticker].quantity == 300.0
    assert p.positions[ticker].avg_cost == call["strike"]
    assert p.cash == cash + row["cash"]
    assert p.options[call["symbol"]].quantity == 1.0
    delivered, = p.deliveries
    assert delivered["event"] == "exercise" and delivered["shares"] == 300.0
    assert p.fills[-1]["ticker"] == call["symbol"], "a delivery is not a fill"
    assert engine.exercise_violations() == []


def test_an_assignment_at_expiry_leaves_the_writer_short_the_stock():
    engine = tf.Engine(seed=13, universe=SMALL, model=model(**ON))
    p = Portfolio(cash=1_000_000.0, owner="w")
    day(engine)
    for _ in range(13):
        engine.open_market()
        p.settle_open(engine)
        engine.run_session(9, 30, 3, 390)
        engine.close_market()
        p.settle_close(engine)
    ticker = engine.tickers[0]
    engine.open_market()
    p.settle_open(engine)
    engine.run_session(9, 30, 3, 10)
    deep = strike_near(engine, ticker, 14, "C", -6)
    sold = p.execute(engine, deep["symbol"], -5.0)
    assert sold["quantity"] == -5.0
    engine.run_session(9, 40, 3, 380)
    engine.close_market()
    close = engine.settlements(14)
    p.settle_close(engine)
    reference = next(s["reference"] for s in close if s["symbol"] == deep["symbol"] and s["event"] == "settlement")
    assert reference >= deep["strike"] + 0.01
    row, = (s for s in close if s["agent"] == "w")
    assert (row["event"], row["quantity"], row["shares"]) == ("assignment", 5.0, -500.0)
    assert p.positions[ticker].quantity == -500.0
    assert deep["symbol"] not in p.options
    # The dealer exercised the other side, and the close's rows sum to nothing.
    rows = [s for s in close if s["symbol"] == deep["symbol"] and s["event"] != "settlement"]
    assert sum(s["shares"] for s in rows) == 0.0 and sum(s["cash"] for s in rows) == 0.0
    assert any(s["agent"] == "mm" and s["event"] == "exercise" for s in rows)
    assert engine.exercise_violations() == []
    # The short is a holding like any other: the next order trades it.
    engine.open_market()
    p.settle_open(engine)
    p.execute(engine, ticker, 500.0)
    assert p.positions[ticker].quantity == pytest.approx(0.0, abs=1e-9)


def test_the_harness_takes_tf_exercise_and_scores_the_delivered_stock():
    class Agent:
        name = "caller"

        def __init__(self):
            self.symbol = None

        def act(self, obs):
            engine = obs.engine
            if obs.day == 1 and self.symbol is None:
                ticker = obs.tickers[0]
                expiry = sorted({e["expiry"] for e in engine.option_expiries(ticker)})[1]
                rows = [r for r in engine.chain(ticker, expiry) if r["right"] == "C"]
                self.symbol = rows[2]["symbol"]
                return {self.symbol: 2}
            if obs.day == 2 and obs.step == 6:
                return {self.symbol: tf.Exercise(2)}
            return None

    agent = Agent()
    card = tf.evaluate({"caller": agent}, seed=17, universe=SMALL, days=4, steps_per_day=3,
                       model=model(**ON))["caller"]
    assert card.errors == []
    assert card.trades == 1, "the exercise is not a trade"
    assert card.final_net_worth == pytest.approx(card.equity_curve[-1])
    assert card.time_in_market > 0.0


def test_a_covered_call_margins_net_and_a_naked_one_does_not():
    engine = tf.Engine(seed=19, universe=SMALL, model=model(**ON))
    for _ in range(3):
        day(engine)
    ticker = engine.tickers[3]
    expiry = sorted({c["expiry"] for c in names(engine, ticker)})[0]
    call = strike_near(engine, ticker, expiry, "C", 1)["symbol"]
    naked = engine.option_margin([(call, -1.0)])
    assert naked > 0.0 and naked == engine.quote(call)["initial_margin"]
    # Covered, the call asks only what the stock cannot offset: the scan's
    # volatility shift with the stock unmoved.
    covered = engine.option_margin([(call, -1.0), (ticker, 100.0)])
    assert 0.0 <= covered < 0.25 * naked
    assert engine.option_margin([(ticker, 100.0)]) == 0.0
    assert engine.option_margin([(ticker, -100.0)]) == 0.0
    # Ten naked calls ask ten times one.
    assert engine.option_margin([(call, -10.0)]) == pytest.approx(10.0 * naked)


def test_stock_beside_short_calls_is_credited_once():
    # +100 shares and 3 short calls are, move for move, 1 short put and 2
    # short calls at the strike (C - P moves with the stock): one worst loss,
    # on the rise, and the stock's gain there credited once.
    engine = tf.Engine(seed=19, universe=SMALL, model=model(**ON))
    for _ in range(3):
        day(engine)
    ticker = engine.tickers[3]
    expiry = sorted({c["expiry"] for c in names(engine, ticker)})[0]
    call, put = strike_near(engine, ticker, expiry, "C"), strike_near(engine, ticker, expiry, "P")
    assert call["strike"] == put["strike"]
    combo = engine.option_margin([(call["symbol"], -3.0), (ticker, 100.0)])
    synthetic = engine.option_margin([(put["symbol"], -1.0), (call["symbol"], -2.0)])
    assert synthetic > 0.0
    assert combo == pytest.approx(synthetic, rel=1e-9)


def test_an_exercise_is_refused_where_it_cannot_be_made():
    engine = tf.Engine(seed=21, universe=SMALL, model=model(**ON))
    for _ in range(2):
        day(engine)
    engine.open_market()
    ticker = engine.tickers[0]
    call = strike_near(engine, ticker, 14, "C")["symbol"]
    with pytest.raises(tf.OrderError, match="holds 0"):
        engine.exercise("a", call, 1.0)
    with pytest.raises(tf.OrderError, match="greater than zero"):
        engine.exercise("a", call, 0.0)
    with pytest.raises(tf.OrderError, match="not a listed"):
        engine.exercise("a", f"{ticker}.O0015.C1.00", 1.0)
    with pytest.raises(tf.ValidationError):
        tf.Exercise(0)
    with pytest.raises(tf.ValidationError):
        tf.Exercise("2")
    assert tf.Exercise(2) == tf.Exercise(2.0) and repr(tf.Exercise(2)) == "Exercise(2)"
    engine.submit("a", call, 3.0)
    engine.exercise("a", call, 2.0)
    assert engine.cancel_exercise("a", call) == 2.0
    assert engine.exercise_instructions() == []


def test_the_position_limit_refuses_a_position_past_the_dealers_depth():
    engine = tf.Engine(seed=43, universe=SMALL, model=model(**dict(ON, option_position_limit=0.1)))
    day(engine)
    engine.open_market()
    ticker = engine.tickers[0]
    call = strike_near(engine, ticker, 14, "C")["symbol"]
    assert engine.position_limit(call) == 20.0 and engine.quote(call)["position_limit"] == 20.0
    engine.submit("a", call, 20.0)
    with pytest.raises(tf.OrderError, match="position limit"):
        engine.submit("a", call, 1.0)
    engine.submit("a", call, -40.0)
    with pytest.raises(tf.OrderError, match="position limit"):
        engine.submit("a", call, -1.0)
    off = tf.Engine(seed=43, universe=SMALL, model=model(**ON))
    day(off)
    assert off.position_limit(call) is None and "position_limit" not in off.quote(call)
    with pytest.raises(tf.ValidationError, match="option_position_limit"):
        model(**dict(BASE, option_position_limit=1.0))


def test_the_settlements_table_carries_the_exercises():
    pa = pytest.importorskip("pyarrow")
    engine = _traded_engine(seed=23)
    engine.run_session(10, 20, 3, 340)
    engine.close_market()
    rows = engine.settlements()
    table = pa.table(engine.settlements_table())
    assert table.column_names[-5:] == ["event", "agent", "quantity", "shares", "cash"]
    assert table.to_pylist() == [{k: s[k] for k in table.column_names} for s in rows]
    assert any(r["event"] == "exercise" for r in rows)


def _traded_engine(seed=29):
    engine = tf.Engine(seed=seed, universe=SMALL, model=model(**ON))
    for _ in range(4):
        day(engine)
    engine.open_market()
    engine.run_session(9, 30, 3, 40)
    ticker = engine.tickers[4]
    expiry = sorted({c["expiry"] for c in names(engine, ticker)})[1]
    call = strike_near(engine, ticker, expiry, "C", -2)["symbol"]
    engine.submit("a", call, 8.0)
    engine.submit("b", call, -3.0)
    put = engine.quote(strike_near(engine, ticker, expiry, "P", 2)["symbol"])
    engine.submit("b", put["symbol"], 4.0, limit_price=put["bid"] or 0.01)
    engine.exercise("a", call, 2.0)
    engine.run_session(9, 70, 3, 10)
    return engine


def _go_on(engine):
    out = []
    engine.run_session(10, 20, 3, 340)
    ticker = engine.tickers[4]
    out.append([engine.quote(c["symbol"]) for c in names(engine, ticker)[::29]])
    engine.close_market()
    for _ in range(12):
        day(engine)
        out.append([engine.quote(c["symbol"])["mid"] for c in names(engine, ticker)[::31]])
    out.append([s for s in engine.settlements() if s["event"] != "settlement"])
    out.append(engine.state_hash())
    return out


def test_a_mid_session_snapshot_restores_and_continues_like_the_original():
    engine = _traded_engine()
    snap = engine.state_snapshot()
    for key in ("name_options", "name_options_book", "name_options_exercise"):
        assert key in snap, key
    assert state_hash(snap) == engine.state_hash()
    restored = tf.Engine(seed=99, universe=SMALL, model=model(**ON))
    restored.restore_state(snap)
    assert restored.state_hash() == engine.state_hash()
    assert _go_on(restored) == _go_on(engine)


def test_the_state_hash_reads_the_name_options():
    snap = _traded_engine().state_snapshot()
    raw = snap["name_options"]
    last, = struct.unpack("<d", raw[-8:])
    changed = dict(snap, name_options=raw[:-8] + struct.pack("<d", math.nextafter(last, math.inf)))
    assert state_hash(changed) != state_hash(snap)
    ex = snap["name_options_exercise"]
    agent, symbol, quantity = ex["instructions"][0]
    moved = dict(snap, name_options_exercise=dict(ex, instructions=[(agent, symbol, quantity + 1.0)]))
    assert state_hash(moved) != state_hash(snap)


def test_a_mid_session_fork_continues_like_the_original():
    engine = _traded_engine(seed=31)
    fork, = engine.fork(1)
    assert fork.state_hash() == engine.state_hash()
    assert _go_on(fork) == _go_on(engine)


def test_a_snapshot_with_name_options_is_refused_by_a_model_without_them():
    snap = _traded_engine().state_snapshot()
    off = tf.Engine(seed=99, universe=SMALL, model=_off(ON))
    with pytest.raises(tf.ValidationError, match="options_names_listed"):
        off.restore_state(snap)


def test_the_order_log_replays_the_exercises():
    engine = _traded_engine(seed=37)
    _go_on(engine)
    assert any(e["op"] == "exercise" for e in engine.order_log)
    again = replay(engine.order_log, seed=37, universe=SMALL, model=model(**ON))
    assert again.state_hash() == engine.state_hash()


def test_a_delisted_names_options_are_paid_in_cash():
    engine = _traded_engine(seed=41)
    engine.run_session(10, 20, 3, 340)
    engine.close_market()
    ticker = engine.tickers[4]
    held = {f["symbol"] for f in engine.settlements() if f["agent"] in ("a", "b")}
    index = engine.tickers.index(ticker)
    engine.delist(index)
    rows = [s for s in engine.settlements() if s["root"] == ticker and s["event"] == "settlement" and s["agent"]]
    assert {r["symbol"] for r in rows if r["agent"] != "mm"} <= held | {r["symbol"] for r in rows}
    for symbol in {r["symbol"] for r in rows}:
        assert sum(r["quantity"] for r in rows if r["symbol"] == symbol) == pytest.approx(0.0)
    assert ticker not in engine.name_option_roots()
    assert names(engine, ticker) == []
    day(engine)
    assert engine.exercise_violations() == []


# -- the MCP server -----------------------------------------------------------


@pytest.fixture
def name_sessions(monkeypatch):
    """MCP sessions opened from here on run pt-v21 with the phase 3 switch
    on: what a library caller gets from `tf.Engine(model=...)`, which the
    server's preset argument cannot name."""
    pytest.importorskip("mcp", reason="the MCP server is an opt-in extra")
    mcp = pytest.importorskip("tradefloor.mcp")
    params = model(**ON)

    def fresh(self, *, to_restore=False):
        opening = {"macro_state": tf.Macro()} if to_restore else {}
        return tf.Engine(seed=self.seed, universe=self.roster, model=params, **opening)

    monkeypatch.setattr(mcp._Session, "_fresh_engine", fresh)
    yield mcp
    for sid in list(mcp._sessions):
        mcp.close_session(sid)


def test_mcp_reads_a_names_chain_trades_it_and_exercises_it(name_sessions):
    mcp = name_sessions
    r = mcp.open_session(universe_size=8)
    assert r["ok"], r.get("error")
    sid = r["session_id"]
    mcp.session_step(sid, days=1)
    engine = mcp._sessions[sid].engine
    ticker = engine.tickers[2]
    chain = mcp.option_chain(sid, root=ticker, strikes=3)
    assert chain["ok"] and chain["root"] == ticker
    assert chain["name_options"] == engine.name_option_roots()
    assert [e["expiry"] for e in chain["expiries"]] == [e["expiry"] for e in engine.option_expiries(ticker)]
    assert chain["multiplier"] == 100.0 and len(chain["chain"]) == 12
    refused = mcp.option_chain(sid, root="NOPE")
    assert refused["ok"] is False and ticker in refused["error"]
    call = min((row for row in chain["chain"] if row["right"] == "C"), key=lambda row: row["strike"])["symbol"]
    bought = mcp.session_step(sid, orders={call: 2})
    assert bought["ok"], bought.get("error")
    me = bought["agents"]["me"]
    assert me["options"][call]["quantity"] == 2.0
    assert bought["market"]["name_options_listed"] == len(engine.name_option_roots())
    asked = mcp.session_step(sid, orders={call: {"exercise": 2}})
    assert asked["ok"], asked.get("error")
    assert asked["agents"]["me"]["orders_sent"] == {call: {"exercise": 2.0}}
    done = mcp.session_step(sid, days=1)
    me = done["agents"]["me"]
    assert call not in me["options"]
    assert me["positions"][ticker]["quantity"] == 200.0
    assert me["deliveries"][0]["event"] == "exercise"
    bad = mcp.session_step(sid, orders={call: {"exercise": 0}})
    assert bad["ok"] is False


def test_describe_simulator_names_the_single_name_options():
    pytest.importorskip("mcp", reason="the MCP server is an opt-in extra")
    mcp = pytest.importorskip("tradefloor.mcp")
    names_family = mcp.describe_simulator()["instruments"]["name_options"]
    assert names_family["listed_by"] == "options_names_listed"
    assert "exercise" in names_family["how"]


# -- the framework payload and decision ---------------------------------------


class _Capture:
    """Records the payloads it is shown; trades what `decide` says."""

    def __init__(self, decide=None):
        self.payloads = []
        self.decide = decide

    def __call__(self, payload):
        self.payloads.append(payload)
        return self.decide(payload) if self.decide else {"actions": []}


def _buy_then_exercise(payload):
    """Day 1: buy two of the first name's second-expiry call two strikes
    below the money. Day 2: exercise them, and try to exercise an index
    symbol that is no name's."""
    if payload["step"] % payload["steps_per_day"]:
        return {"actions": []}
    names = payload["options"]["names"]
    if not names["expiries"]:
        return {"actions": []}  # nothing lists before the first close
    ticker = sorted(names["iv_30d"])[0]
    price = next(a["price"] for a in payload["assets"] if a["symbol"] == ticker)
    step = 1.0 if price < 50 else 2.5
    strike = max(step, (round(price / step) - 2) * step)
    symbol = tf.contracts.format(ticker, names["expiries"][1], "C", strike)
    if payload["day"] == 1:
        return {"actions": [{"symbol": symbol, "side": "BUY", "quantity": 2}]}
    if payload["day"] == 2:
        return {"actions": [{"symbol": symbol, "side": "EXERCISE", "quantity": 2},
                            {"symbol": "IDX.O0035.C1000.00", "side": "EXERCISE", "quantity": 1}]}
    return {"actions": []}


def test_a_framework_sees_the_names_and_exercises_through_a_decision():
    from tradefloor.integrations.callable import CallableAgentAdapter

    seen = _Capture(_buy_then_exercise)
    cards = tf.evaluate({"a": CallableAgentAdapter(seen, every=6)}, seed=3, universe=SMALL,
                        model=model(**ON), days=4)
    card = cards["a"]
    assert card.trades == 1, card.errors
    refused, = card.errors
    assert "IDX.O0035.C1000.00" in refused and "not listed" in refused
    later = seen.payloads[-1]
    options = later["options"]
    assert options["expiries"] == []
    assert set(options["names"]) == {"expiries", "iv_30d"}
    assert sorted(options["names"]["iv_30d"]) == sorted(a["symbol"] for a in later["assets"])
    assert all(0.0 < v < 3.0 for v in options["names"]["iv_30d"].values())
    assert options["positions"] == [], "exercised: the call is gone"
    support = (pathlib.Path(__file__).resolve().parent.parent / "docs" / "SUPPORT.md").read_text(encoding="utf-8")
    for key in ("names", "iv_30d", "EXERCISE"):
        assert f"`{key}`" in support, key
    text = TextRenderer().render(later)
    assert "Single-name options" in text and "EXERCISE" in text


def test_the_decision_contract_takes_exercise():
    from tradefloor.integrations import common as ci

    assert "EXERCISE" in ci.SIDES
    assert ci.decision_schema()["properties"]["actions"]["items"]["properties"]["side"]["enum"] == list(ci.SIDES)
    ok = ci.parse_decision({"actions": [{"symbol": "AAA.O0035.C5.00", "side": "exercise", "quantity": 3}]})
    action, = ok.actions
    assert (action.side, action.quantity, action.signed()) == ("EXERCISE", 3.0, 0.0)
    assert repr(action) == "EXERCISE 3 AAA.O0035.C5.00"
    for bad in ({"symbol": "AAA.O0035.C5.00", "side": "EXERCISE"},
                {"symbol": "AAA.O0035.C5.00", "side": "EXERCISE", "quantity": 1, "order_type": "market"},
                {"symbol": "AAA.O0035.C5.00", "side": "EXERCISE", "quantity": 1, "limit_price": 2.0}):
        assert ci.parse_decision({"actions": [bad]}).refused, bad
    assert ci.recorded_orders({"X": tf.Exercise(2)}) == {"X": {"exercise": 2.0}}
