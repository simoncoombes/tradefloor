"""The simulated rate indices: UST2Y, UST10Y and IGCORP.

What these hold, in order:

- the switch: a roster without the indices is the market it always was, and
  a roster with them leaves every equity price, draw and macro value
  bit-identical;
- the pricing formula, checked bit for bit against the documented identity
  on a live run, and the +200bp repricing on the day a shock lands;
- every packaged scenario's effect on the three indices;
- a 60/40 portfolio through each scenario;
- the machinery they share with the equities: book, fills, portfolio, tape,
  TCA, attribution, snapshot, fork, checkpoint, replay;
- cash interest, off by default;
- the known-answer digest for a session with the indices.
"""

from __future__ import annotations

import json
import math
import pathlib
import struct
import sys

import pytest

import tradefloor as tf
from tradefloor import manifest
from tradefloor.baselines import Balanced

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import known_answer  # noqa: E402

RATES = ("UST2Y", "UST10Y", "IGCORP")
SPECS = {s["ticker"]: s for s in tf.rate_specs()}


def f64(buf: bytes) -> list[float]:
    return list(struct.unpack("<%dd" % (len(buf) // 8), buf))


def universe(n: int = 12, seed: int = 101) -> tf.Universe:
    return tf.Universe.random(n, seed=seed, bonds=True)


def day(engine: tf.Engine, ticks: int = 390, fills=None) -> None:
    engine.open_market()
    engine.run_session(9, 30, 3, ticks, fills=fills)
    engine.close_market()


# -- the roster ---------------------------------------------------------------

def test_random_appends_the_three_indices_after_the_same_equities():
    plain = tf.Universe.random(10, seed=4)
    with_bonds = tf.Universe.random(10, seed=4, bonds=True)
    assert with_bonds.tickers() == plain.tickers() + list(RATES)
    assert with_bonds[:10] == plain[:10] or all(
        a.ticker == b.ticker and a.initial_price == b.initial_price
        for a, b in zip(with_bonds[:10], plain))
    assert plain.with_bonds().tickers() == with_bonds.tickers()
    assert with_bonds.equities().tickers() == plain.tickers()
    assert tf.RATE_TICKERS == RATES


def test_the_indices_say_what_they_are():
    for ticker, spec in SPECS.items():
        assert "Simulated" in spec["name"] and "not a real security" in spec["name"]
    assert (SPECS["UST2Y"]["duration"], SPECS["UST2Y"]["convexity"]) == (1.9, 4.6)
    assert (SPECS["UST10Y"]["duration"], SPECS["UST10Y"]["convexity"]) == (8.5, 84.0)
    assert (SPECS["IGCORP"]["duration"], SPECS["IGCORP"]["convexity"]) == (7.0, 100.0)


def test_a_universe_with_the_indices_round_trips_through_json():
    u = universe()
    back = tf.Universe.from_json(u.to_json())
    assert back.tickers() == u.tickers()
    assert back.fingerprint == u.fingerprint
    e1 = tf.Engine(seed=2, universe=u)
    e2 = tf.Engine(seed=2, universe=back)
    for _ in range(2):
        day(e1)
        day(e2)
    assert e1.prices() == e2.prices()


@pytest.mark.parametrize("build, message", [
    (lambda: tf.bonds() + list(tf.Universe.random(3, seed=1)), "must come after"),
    (lambda: list(tf.Universe.random(3, seed=1)) + tf.bonds(["UST10Y"]) * 2,
     "listed twice"),
    (lambda: tf.bonds(), "no equities"),
    (lambda: list(tf.Universe.random(3, seed=1)) + [
        tf.Instrument("UST10Y", "technology", initial_price=10.0,
                      shares_outstanding=1e6)] + tf.bonds(["UST10Y"]),
     "carries the ticker"),
])
def test_a_roster_the_engine_cannot_hold_is_refused(build, message):
    with pytest.raises(tf.ValidationError, match=message):
        tf.Engine(seed=1, universe=build())


def test_a_rate_instrument_is_one_of_the_priced_tickers_and_nothing_else():
    with pytest.raises(tf.ValidationError, match="not a rate instrument"):
        tf.Instrument("UST5Y", "rates", initial_price=100.0, shares_outstanding=1e8)
    with pytest.raises(tf.ValidationError, match="company field"):
        tf.Instrument("UST10Y", "rates", initial_price=100.0,
                      shares_outstanding=1e8, eps=1.0)
    with pytest.raises(tf.ValidationError, match="not a rate instrument"):
        tf.bonds(["UST30Y"])


def test_surfaces_that_run_equities_only_say_so():
    u = universe(6)
    with pytest.raises(tf.ValidationError, match="equities only"):
        tf.EngineBatch(seeds=[1, 2], universe=u)
    e = tf.Engine(seed=1, universe=u)
    with pytest.raises(tf.ValidationError, match="fixed when the engine is built"):
        e.list_instrument(tf.bonds(["UST2Y"])[0])
    with pytest.raises(tf.ValidationError, match="cannot be delisted"):
        e.delist(len(u) - 1)
    e.keep_explanations(0, 0)
    day(e)
    with pytest.raises(tf.ValidationError, match="rate index"):
        e.explain("UST10Y", 0)
    with pytest.raises(tf.ValidationError, match="rate index"):
        e.run_until(ticker="UST10Y", below=90.0)
    with pytest.raises(tf.ValidationError, match="rate index"):
        e.tick(10, 0, 3, news=[tf.News(ticker="IGCORP", price_impact=0.01)])


def test_listing_an_equity_keeps_the_indices_last():
    u = universe(4)
    e = tf.Engine(seed=1, universe=u)
    extra = tf.Universe.random(5, seed=9)[4]
    index = e.list_instrument(extra)
    assert index == 4
    assert e.tickers == u.tickers()[:4] + [extra.ticker] + list(RATES)
    assert len(f64(e.prices())) == len(e) == 8
    assert e.book("UST10Y").best_bid is not None


# -- the switch -----------------------------------------------------------------

def test_the_indices_move_no_equity_price_draw_or_macro_value():
    """The property that makes a 60/40 study clean: the equity sleeve trades
    in the market it would have traded in without bonds, even while an agent
    is trading the bonds."""
    plain = tf.Engine(seed=5, universe=tf.Universe.random(12, seed=101))
    bonded = tf.Engine(seed=5, universe=universe())
    for d in range(12):
        for e in (plain, bonded):
            e.open_market()
            for step in range(3):
                clock = tf.harness.session_clock((9, 30, 3), step, 130)
                fills = ({"UST10Y": (80_000.0, 0.0), "IGCORP": (0.0, 50_000.0)}
                         if e is bonded and step == 1 else None)
                e.run_session(*clock, 130, fills=fills)
            e.close_market()
        assert f64(bonded.prices())[:12] == f64(plain.prices())
    assert bonded.draws_by_stream() == plain.draws_by_stream()
    assert bonded.state_snapshot()["economy"] == plain.state_snapshot()["economy"]
    for factor in tf.Engine.FACTORS:
        assert f64(bonded.attribution(factor))[:12] == f64(plain.attribution(factor))


def test_without_the_indices_nothing_new_appears():
    e = tf.Engine(seed=5, universe=tf.Universe.random(6, seed=101))
    day(e)
    assert e.rate_instruments == []
    assert "rates" not in e.state_snapshot()
    assert "investment_grade" not in e.curve
    assert set(e.curve) == {"policy_rate", "treasury_2y", "treasury_10y", "corporate"}


def test_the_existing_known_answer_digests_did_not_move():
    baseline = json.loads((HERE / "known_answer.json").read_text(encoding="utf-8"))
    assert known_answer.simulation_digest() == baseline["simulationSha256"]
    assert known_answer.known_answer_digest() == baseline["sha256"]


def test_the_indices_known_answer_covers_them():
    """The digest itself is checked in `test_known_answer.py`, which the
    determinism workflow runs on every wheel target. This guards against it
    passing because it measures nothing: the buffer holds the three indices'
    columns on every day, and they moved."""
    buf = known_answer.bonds_buffer()
    assert len(buf) % 8 == 0 and len(buf) > 5 * 15 * 10 * 8
    values = struct.unpack(">%dd" % (len(buf) // 8), buf)
    assert len(set(values)) > len(values) // 4


# -- the formula ------------------------------------------------------------------

def repriced(level: float, duration: float, convexity: float, dy: float,
             carry_yield: float) -> float:
    """The documented identity, in the documented order of operations."""
    dy = min(dy, duration / convexity)
    total = carry_yield / 252.0 + (-duration * dy) + 0.5 * convexity * dy * dy
    return level * (1.0 + total)


def test_every_close_to_close_return_is_the_formula_to_the_bit():
    e = tf.Engine(seed=8, universe=universe())
    marks = [{r["ticker"]: (r["level"], r["yield"]) for r in e.rate_instruments}]
    first = True
    for d in range(40):
        e.open_market()
        now = {r["ticker"]: (r["level"], r["yield"]) for r in e.rate_instruments}
        for ticker in RATES:
            level, y = marks[-1][ticker]
            spec = SPECS[ticker]
            expected = repriced(level, spec["duration"], spec["convexity"],
                                now[ticker][1] - y, 0.0 if first else y)
            assert now[ticker][0] == expected, (d, ticker)
        first = False
        e.run_session(9, 30, 3, 390)
        e.close_market()
        # Untraded, the print is the level and the level does not move in a
        # session nobody pinned anything in.
        prices = dict(zip(e.tickers, f64(e.prices())))
        for r in e.rate_instruments:
            assert prices[r["ticker"]] == r["level"] == now[r["ticker"]][0]
        marks.append(now)


def test_the_components_sum_to_the_days_return():
    e = tf.Engine(seed=8, universe=universe())
    day(e)
    before = dict(zip(e.tickers, f64(e.prices())))
    day(e)
    after = dict(zip(e.tickers, f64(e.prices())))
    parts = {c: dict(zip(e.tickers, f64(e.rate_attribution(c))))
             for c in tf.Engine.RATE_COMPONENTS}
    for t in RATES:
        total = parts["carry"][t] + parts["duration"][t] + parts["convexity"][t]
        assert after[t] / before[t] - 1.0 == pytest.approx(total, abs=1e-15)
        assert parts["flow"][t] == 0.0
    for t in e.tickers[:12]:
        assert all(parts[c][t] == 0.0 for c in parts)


def test_a_200bp_parallel_shock_reprices_on_the_day_it_lands():
    e = tf.Engine(seed=8, universe=universe())
    day(e)
    fields = e.macro_fields
    marked = {r["ticker"]: r["yield"] for r in e.rate_instruments}
    before = dict(zip(e.tickers, f64(e.prices())))
    e.pin_macro(
        federal_funds_rate=fields["federal_funds_rate"] + 0.02,
        treasury_yield_2y=fields["treasury_yield_2y"] + 0.02,
        treasury_yield_10y=fields["treasury_yield_10y"] + 0.02,
        corporate_bond_yield=fields["corporate_bond_yield"] + 0.02,
    )
    day(e)
    after = dict(zip(e.tickers, f64(e.prices())))
    ret = {t: after[t] / before[t] - 1.0 for t in RATES}
    # A 10-year at duration 8.5 falls about 15 to 17 per cent, with
    # convexity: -8.5 * 0.02 + 0.5 * 84 * 0.02**2 = -15.32%, plus a day's
    # carry and whatever the close's own step added to the 10-year.
    assert -0.160 < ret["UST10Y"] < -0.150, ret
    assert -0.039 < ret["UST2Y"] < -0.035, ret
    assert -0.130 < ret["IGCORP"] < -0.115, ret
    # And the day's duration term is -D * dy to the bit.
    y10 = e.rate_instruments[1]["yield"]
    duration = f64(e.rate_attribution("duration"))[12 + 1]
    assert duration == -8.5 * (y10 - marked["UST10Y"])


def test_the_quadratic_stops_at_its_turning_point():
    e = tf.Engine(seed=8, universe=universe())
    day(e)
    before = f64(e.prices())[13]
    e.pin_macro(treasury_yield_10y=0.45)
    day(e)
    worst = f64(e.prices())[13] / before - 1.0
    assert worst == pytest.approx(-8.5**2 / (2 * 84.0), abs=2e-3)


def test_a_mid_day_pin_reaches_the_index_on_the_next_tick_and_no_sooner():
    e = tf.Engine(seed=8, universe=universe())
    day(e)
    e.open_market()
    e.run_session(9, 30, 3, 100)
    level = f64(e.prices())[13]
    e.pin_macro(treasury_yield_10y=e.macro_fields["treasury_yield_10y"] + 0.01)
    assert f64(e.prices())[13] == level, "a pin is not a print"
    e.run_session(11, 10, 3, 1)
    assert f64(e.prices())[13] < level * 0.93


def test_no_price_reveals_tomorrows_yield():
    """Two engines identical until one's close is given a different 10-year:
    every index price up to that close agrees to the bit.

    On pt-v19, where the close's macro step reaches prices at the next open.
    pt-v20 prices it the moment it is published since its graded arm
    (`macro_publication_repricing`, 2026-09-26), so there the two differ
    from the close on, by design; the first assertion is the one about
    tomorrow's yield and holds on both (checked below). Was the default."""
    b0 = tf.Engine(seed=8, universe=universe(), model="pt-v20")
    day(b0)
    b0.open_market()
    b0.run_session(9, 30, 3, 390)
    b1 = b0.fork(1)[0]
    b1.pin_macro(treasury_yield_10y=b1.macro_fields["treasury_yield_10y"] + 0.01)
    assert b0.prices() == b1.prices()
    a = tf.Engine(seed=8, universe=universe(), model="pt-v19")
    day(a)
    a.open_market()
    a.run_session(9, 30, 3, 390)
    b = a.fork(1)[0]
    b.pin_macro(treasury_yield_10y=b.macro_fields["treasury_yield_10y"] + 0.01)
    assert a.prices() == b.prices()
    a.close_market()
    b.close_market()
    # The yield reached the curve at the close and reaches the index at the
    # next open, not before.
    assert a.prices() == b.prices()
    a.open_market()
    b.open_market()
    assert f64(a.prices())[13] != f64(b.prices())[13]


# -- the packaged scenarios ------------------------------------------------------

def _scenario_run(scenario, days, seed=3, model=None):
    e = tf.Engine(seed=seed, universe=universe(8), model=model)
    closes = []
    for d in range(days):
        if scenario is not None:
            scenario.apply(e, d)
        day(e)
        closes.append(dict(zip(e.tickers, f64(e.prices()))))
    return closes


@pytest.mark.parametrize("name", tf.Scenario.available())
def test_every_packaged_scenario_moves_the_indices_the_way_it_says(name):
    scenario = tf.Scenario.load(name)
    corporate = [i for i in scenario.interventions
                 if i.target == "macro.corporate_yield"]
    first = min(i.at for i in (corporate or scenario.interventions))
    shocked = _scenario_run(scenario, first + 2)
    base = _scenario_run(None, first + 2)
    for closes in (shocked, base):
        assert all(math.isfinite(v) and v > 0 for row in closes for v in row.values())

    def move(t):
        return (shocked[first][t] / shocked[first - 1][t]
                - base[first][t] / base[first - 1][t])

    if name == "curve_shock":
        assert -0.0385 < move("UST2Y") < -0.0355
        assert -0.1560 < move("UST10Y") < -0.1510
        assert -0.1300 < move("IGCORP") < -0.1150
    elif corporate:
        # Every other packaged scenario that writes the corporate yield
        # widens credit, and the corporate index falls by about D * dy on
        # the day it lands. Nothing else moves the treasury indices that day.
        dy = corporate[0].value
        expected = -7.0 * dy + 50.0 * dy * dy
        assert expected - 0.01 < move("IGCORP") < expected + 0.005, (name, move("IGCORP"))
        assert abs(move("UST10Y")) < 1e-3 and abs(move("UST2Y")) < 1e-3
    else:
        pytest.fail(f"{name} writes no rate; say what it does to the indices")


def test_a_scenario_path_can_pin_the_treasury_curve():
    scenario = tf.Scenario(name="steepener").step(
        "treasury_yield_10y", before=0.04, after=0.05, at=3)
    closes = _scenario_run(scenario, 5)
    ten = [row["UST10Y"] for row in closes]
    # Day 3 prices a 100bp rise from the 4% held on days 0 to 2.
    assert ten[3] / ten[2] - 1.0 == pytest.approx(
        0.04 / 252 - 8.5 * 0.01 + 0.5 * 84.0 * 0.01**2, abs=5e-4)
    with pytest.raises(tf.ValidationError, match="FRACTIONS|fraction|plausible"):
        tf.Scenario().hold(treasury_yield_10y=4.5)


def test_the_rate_shock_reaches_the_2_year_at_that_evenings_close():
    """On pt-v19, whose 2-year is the formula (0.85 of the policy rate and
    0.15 of the 10-year), rewritten at every close: the shock reaches it
    whole that evening. Measured -3.45 per cent on day 51. pt-v20 gives
    the 2-year its own process; the test below measures that one."""
    scenario = tf.Scenario.load("rate_shock")
    shocked = _scenario_run(scenario, 53, model="pt-v19")
    base = _scenario_run(None, 53, model="pt-v19")
    day50 = shocked[50]["UST2Y"] / shocked[49]["UST2Y"] - base[50]["UST2Y"] / base[49]["UST2Y"]
    day51 = shocked[51]["UST2Y"] / shocked[50]["UST2Y"] - base[51]["UST2Y"] / base[50]["UST2Y"]
    assert abs(day50) < 1e-4
    assert -0.035 < day51 < -0.030


def test_the_rate_shock_reaches_pt_v20s_2_year_over_the_following_weeks():
    """pt-v20's 2-year (``treasury_2y_noise`` 0.022) closes 5 per cent of
    its gap to the formula each session between meetings, so the shock
    reaches it over the sessions to the next meeting rather than that
    evening, and the meeting re-anchors it to the formula. Nothing on day
    50; -0.16 per cent below the unshocked world on day 51, where pt-v19
    moves -3.45; a gap that widens every session to -0.61 on day 54; then
    the meeting at day 54's close takes it to -3.50 on day 55.

    RE-MEASURED 2026-09-26 on pt-v20's graded arm. At seed 3 the first
    meeting after the shock now falls at day 54's close (before, after day
    70), so the widening runs four sessions, not twenty. Was: -0.11 on day
    51, widening every session to -2.14 by day 70."""
    scenario = tf.Scenario.load("rate_shock")
    shocked = _scenario_run(scenario, 71, model="pt-v20")
    base = _scenario_run(None, 71, model="pt-v20")
    gap = [s["UST2Y"] / b["UST2Y"] - 1.0 for s, b in zip(shocked, base)]
    assert abs(gap[50]) < 1e-4
    assert -0.002 < gap[51] < -0.0005
    assert all(gap[d + 1] < gap[d] for d in range(51, 54))
    assert -0.008 < gap[54] < -0.004
    assert -0.040 < gap[55] < -0.030


def test_a_held_corporate_yield_holds_the_corporate_index():
    """rate_shock.yml holds the corporate yield 200bp up while the engine's
    10-year climbs toward the higher policy rate. The index must read the
    held yield, not the held yield plus the climb."""
    scenario = tf.Scenario.load("rate_shock")
    e = tf.Engine(seed=3, universe=universe(8))
    for d in range(70):
        scenario.apply(e, d)
        e.open_market()
        if d >= 50:
            assert e.curve["investment_grade"] == pytest.approx(
                e.curve["corporate"], abs=1e-15)
        e.run_session(9, 30, 3, 390)
        e.close_market()


# -- a 60/40 portfolio -------------------------------------------------------------

@pytest.mark.parametrize("name", [None, *tf.Scenario.available()])
def test_a_60_40_portfolio_runs_through_every_scenario(name):
    scenario = None if name is None else tf.Scenario.load(name)
    days = 56 if scenario is None else min(i.at for i in scenario.interventions) + 6
    held, banded = Balanced(band=None), Balanced(band=0.02)
    cards = tf.evaluate({"held": held, "banded": banded}, seed=4,
                        universe=universe(16), days=days, steps_per_day=1,
                        ticks_per_step=390, scenario=scenario)
    for card in cards.values():
        assert not card.errors and card.rejected == 0
        assert math.isfinite(card.pnl)
        assert card.max_leverage < 1.05
    # Nine-tenths bought on day zero and held: the bond sleeve starts at 40%.
    d0, equity, bonds = held.marks[1]
    assert 0.39 < bonds / (equity + bonds) < 0.41


def test_the_60_40_bond_sleeve_takes_the_duration_weighted_hit():
    scenario = tf.Scenario.load("curve_shock")
    agent = Balanced(band=None)
    tf.evaluate({"held": agent}, seed=4, universe=universe(16), days=52,
                steps_per_day=1, ticks_per_step=390, scenario=scenario)
    marks = {d: (eq, bd) for d, eq, bd in agent.marks}
    # Marks are taken at each day's first step, after the open, so the day-50
    # mark already carries the shock.
    sleeve = marks[50][1] / marks[49][1] - 1.0
    # 10% UST2Y, 20% UST10Y, 10% IGCORP: a quarter each of -3.7% and -12%,
    # half of -15.3%.
    expected = 0.25 * -0.0371 + 0.5 * -0.1532 + 0.25 * -0.12
    assert sleeve == pytest.approx(expected, abs=0.004)


def test_a_rebalancer_buys_bonds_after_a_shock_that_crosses_its_band():
    scenario = tf.Scenario.load("curve_shock")
    agent = Balanced(band=0.02)
    tf.evaluate({"banded": agent}, seed=4, universe=universe(16), days=53,
                steps_per_day=1, ticks_per_step=390, scenario=scenario)
    assert 50 in agent.rebalances


# -- shared machinery ---------------------------------------------------------------

def test_the_book_is_the_equity_ladder_one_cent_wide():
    e = tf.Engine(seed=1, universe=universe(4))
    e.open_market()
    for t in RATES:
        book = e.book(t)
        assert book.best_ask - book.best_bid == pytest.approx(0.01, abs=1e-9)
        assert len(book.price_levels("buy", 32)) == 10
    top = e.book("UST10Y").price_levels("sell", 1)[0].quantity
    assert top == math.floor(SPECS["UST10Y"]["avg_volume"] / 100)


def test_the_spread_widens_with_the_vix():
    e = tf.Engine(seed=1, universe=universe(4))
    e.pin_macro(vix=75.0)
    e.open_market()
    # 1 + (75 - 15) / 30 = 3 times the calm half-spread: 0.9bp for a
    # treasury and 1.2bp for the corporate index, which the cent grid quotes
    # two cents wide at a $100 level.
    for t in RATES:
        book = e.book(t)
        assert book.best_ask - book.best_bid == pytest.approx(0.02, abs=1e-9), t


def test_an_agents_fill_is_priced_by_the_book_and_its_impact_decays():
    e = tf.Engine(seed=1, universe=universe(4))
    day(e)
    e.open_market()
    level = f64(e.prices())[5]
    portfolio = tf.Portfolio(cash=1e8)
    fill = portfolio.execute(e, "UST10Y", 150_000)
    assert fill["price"] > level
    e.run_session(9, 30, 3, 1, fills=portfolio.pending_flow())
    portfolio.clear_flow()
    first = f64(e.prices())[5]
    e.run_session(9, 31, 3, 120)
    later = f64(e.prices())[5]
    assert first > later > level
    assert f64(e.column("maker_inventory"))[5] < 0
    # A round trip against its own footprint costs money.
    sale = portfolio.execute(e, "UST10Y", -150_000)
    assert sale["price"] < fill["price"]


def test_the_tape_carries_the_indices():
    e = tf.Engine(seed=1, universe=universe(4))
    for d in range(2):
        day(e)
        e.record(d)
    pa = pytest.importorskip("pyarrow")
    bars = pa.table(e.bars(grain="day")).to_pylist()
    ids = {row["instrument_id"] for row in bars}
    assert ids == set(range(7))
    closes = {row["instrument_id"]: row["close"] for row in bars if row["day"] == 1}
    assert closes[5] == f64(e.prices())[5]
    truth = pa.table(e.truth(day=1)).to_pylist()
    last = [r for r in truth if r["instrument_id"] == 5][-1]
    assert last["fundamental_value"] == last["anchor_price"] == f64(e.prices())[5]
    assert last["mispricing_s"] == 0.0


def test_tca_prices_a_bond_trade_against_the_untraded_world():
    class BuyTheTen:
        def act(self, obs):
            return {"UST10Y": 5_000} if obs.step == 0 else {}

    execution = tf.tca.analyse(BuyTheTen(), seed=2, universe=universe(6), days=1)
    assert 0.0 < execution.shortfall_bps("UST10Y") < 3.0
    assert execution.untouched_moved() == []


def test_the_liquidity_lever_thins_the_bond_books_too():
    e = tf.Engine(seed=1, universe=universe(4))
    depth = f64(e.column("avg_volume"))
    e.set_avg_volume([v * 0.4 for v in depth])
    e.open_market()
    top = e.book("UST10Y").price_levels("sell", 1)[0].quantity
    assert top == math.floor(SPECS["UST10Y"]["avg_volume"] * 0.4 / 100)


def test_attribution_is_as_wide_as_the_roster():
    e = tf.Engine(seed=1, universe=universe(4))
    day(e)
    for factor in tf.Engine.FACTORS:
        column = f64(e.attribution(factor))
        assert len(column) == len(e.tickers)
        assert column[4:] == [0.0, 0.0, 0.0]
    for part in ("market", "sector", "idio"):
        assert len(f64(e.noise_split(part))) == len(e.tickers)


# -- determinism, replay, fork -------------------------------------------------------

def _driven(engine, days=4, start=0):
    for d in range(start, start + days):
        if d == 2:
            fields = engine.macro_fields
            engine.pin_macro(treasury_yield_10y=fields["treasury_yield_10y"] + 0.005,
                             corporate_bond_yield=fields["corporate_bond_yield"] + 0.005)
        engine.open_market()
        engine.run_session(9, 30, 3, 200,
                           fills={"UST2Y": (40_000.0, 0.0)} if d == 1 else None)
        engine.run_session(12, 50, 3, 190)
        engine.close_market()
    return engine


def test_the_same_seed_gives_the_same_bonds():
    a = _driven(tf.Engine(seed=6, universe=universe()))
    b = _driven(tf.Engine(seed=6, universe=universe()))
    assert a.prices() == b.prices()
    assert a.state_hash() == b.state_hash()
    c = _driven(tf.Engine(seed=7, universe=universe()))
    assert f64(a.prices())[12:] != f64(c.prices())[12:]


def test_a_replayed_log_lands_on_the_same_state():
    u = universe()
    a = _driven(tf.Engine(seed=6, universe=u))
    b = tf.replay(a.order_log, seed=6, universe=u)
    assert b.state_hash() == a.state_hash()
    assert b.prices() == a.prices()


def test_a_fork_continues_as_its_parent_does_mid_day():
    a = _driven(tf.Engine(seed=6, universe=universe()), days=2)
    a.open_market()
    a.run_session(9, 30, 3, 100, fills={"IGCORP": (0.0, 70_000.0)})
    b = a.fork(1)[0]
    for e in (a, b):
        e.run_session(11, 10, 3, 290)
        e.close_market()
        _driven(e, days=2, start=2)
    assert a.state_hash() == b.state_hash()


def test_a_snapshot_restores_the_bonds_and_hashes_like_its_twin():
    u = universe()
    a = _driven(tf.Engine(seed=6, universe=u), days=2)
    a.open_market()
    a.run_session(9, 30, 3, 50, fills={"UST10Y": (30_000.0, 0.0)})
    snap = a.state_snapshot()
    assert "rates" in snap
    assert manifest.state_hash(snap) == a.state_hash()
    b = tf.Engine(seed=6, universe=u)
    b.restore_state(snap)
    assert b.state_hash() == a.state_hash()
    for e in (a, b):
        e.run_session(10, 20, 3, 340)
        e.close_market()
        _driven(e, days=2, start=2)
    assert a.prices() == b.prices()
    with pytest.raises(tf.ValidationError, match="carries none"):
        del snap["rates"]
        tf.Engine(seed=6, universe=u).restore_state(snap)


def test_a_checkpoint_of_a_bond_run_resumes_exactly():
    u = universe()
    a = _driven(tf.Engine(seed=6, universe=u), days=3)
    checkpoint = tf.Checkpoint.of(a, universe=u, seed=6)
    b = tf.Checkpoint.from_json(checkpoint.to_json()).resume()
    assert b.state_hash() == a.state_hash()


# -- cash --------------------------------------------------------------------------

def test_cash_earns_nothing_unless_asked():
    class Idle:
        def act(self, obs):
            return {}

    u = universe(4)
    off = tf.evaluate({"idle": Idle()}, seed=1, universe=u, days=5,
                      steps_per_day=1, ticks_per_step=390)["idle"]
    assert off.pnl == 0.0
    on = tf.evaluate({"idle": Idle()}, seed=1, universe=u, days=5,
                     steps_per_day=1, ticks_per_step=390,
                     cash_interest=True)["idle"]
    e = tf.Engine(seed=1, universe=u)
    rate = e.macro_fields["federal_funds_rate"]
    assert on.pnl == pytest.approx(1_000_000.0 * ((1 + rate / 252) ** 5 - 1), rel=1e-3)


def test_accrue_is_one_days_interest_at_the_policy_rate():
    e = tf.Engine(seed=1, universe=universe(4))
    p = tf.Portfolio(cash=252_000.0, cash_interest=True)
    rate = e.macro_fields["federal_funds_rate"]
    assert p.accrue(e) == pytest.approx(1000.0 * rate)
    assert p.interest == pytest.approx(1000.0 * rate)
    assert tf.Portfolio(cash=252_000.0).accrue(e) == 0.0
