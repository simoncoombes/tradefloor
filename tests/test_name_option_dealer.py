"""The single-name options' dealer shaped on the tape (pt-v22): its
inventory response and half-life, and its spread in moneyness and tenor.

Each dial is 0.0 on every shipped preset, where the dealer is as it was:
a response of half an at-the-money half-spread per 20 contracts that halves
in 15 open ticks, and a half-spread in volatility, `1 + 0.25 |z|` times the
at-the-money one, flat in tenor. Off zero:

- `option_name_inventory_slope` moves an expiry's volatility by that share
  of its at-the-money volatility per at-the-money-equivalent contract the
  dealer is short;
- `option_name_inventory_half_life` halves the pressure in that many
  sessions of open ticks;
- `option_name_spread_otm` and `option_name_spread_itm` quote the
  half-spread as a share of the price: the at-the-money share times a factor
  of the absolute delta, `otm` at 0.25 and `itm` at 0.75;
- `option_name_spread_tenor_exponent` scales the half-spread by
  `(sessions / 21)` to its power.

They price options only: no stock price moves.
"""
from __future__ import annotations

import struct

import pytest

import tradefloor as tf
from tradefloor.portfolio import is_name_option

UNIVERSE = tf.Universe.random(30, seed=111)
ON = dict(index_level_listed=1.0, vix_intraday_live=1.0, forecast_horizon_sessions=252.0,
          forecast_vix_dispersion=0.3, forecast_vix_dispersion_half_life=31.5, forecast_vix_dispersion_skew=0.81,
          surface_ssvi=1.0, surface_skew_physical=-0.8, surface_skew_physical_slope=0.3, surface_skew_premium=-1.0,
          surface_skew_premium_slope=0.5, surface_curvature=1.0, surface_curvature_exponent=0.5,
          surface_term_premium_short=-0.03, surface_term_premium_long=0.1, surface_earnings_weight=1.0,
          options_names_listed=1.0, surface_name_premium=0.1, surface_name_skew=0.5, option_dealer_spread=0.02,
          margin_scan_coverage=0.99)
DIALS = ("option_name_inventory_slope", "option_name_inventory_half_life", "option_name_spread_otm",
         "option_name_spread_itm", "option_name_spread_tenor_exponent")


def model(**dials):
    return tf.ModelParams.from_preset("pt-v21", **dials)


def engine(params, days=3, ticks=15):
    e = tf.Engine(seed=7, universe=UNIVERSE, model=params)
    for _ in range(days):
        e.open_market(); e.run_session(9, 30, 3, 390); e.close_market()
    e.open_market(); e.run_session(9, 30, 3, ticks)
    return e


def price(e, ticker):
    return dict(zip(e.tickers, struct.unpack("<%dd" % len(e.tickers), e.prices())))[ticker]


def big_name(e):
    names = sorted({c["root"] for c in e.contracts() if is_name_option(c["symbol"])})
    return max(names, key=lambda t: price(e, t))


def chain(e, ticker, expiry):
    return sorted(e.chain(ticker, expiry), key=lambda r: (r["right"], r["strike"]))


def expiries(e, ticker):
    return sorted({c["expiry"] for c in e.contracts() if c["root"] == ticker})


def test_every_dial_is_off_on_every_shipped_preset_and_silent_in_the_digest():
    silent = set(tf.ModelParams.digest_silent_at_zero())
    for name in DIALS:
        assert name in silent
        for preset in tf.preset_names():
            assert getattr(tf.ModelParams.from_preset(preset), name) == 0.0
    assert model(**ON).fingerprint == model(**ON, **{d: 0.0 for d in DIALS}).fingerprint


def test_each_is_refused_without_the_names_and_out_of_its_range():
    off = {k: v for k, v in ON.items() if k not in ("options_names_listed", "surface_name_premium",
                                                     "surface_name_skew", "option_dealer_spread")}
    for name in DIALS:
        with pytest.raises(tf.ValidationError, match=name):
            model(**off, **{name: 0.5 if "exponent" in name else 1.0e-4})
    for name, bad in (("option_name_inventory_slope", 0.02), ("option_name_inventory_half_life", -1.0),
                      ("option_name_spread_otm", 6.0), ("option_name_spread_tenor_exponent", 1.5)):
        with pytest.raises(tf.ValidationError, match=name):
            model(**ON, **{name: bad})


def test_the_stock_prices_are_the_same_with_every_dial_on():
    dials = dict(option_name_inventory_slope=1.4e-4, option_name_inventory_half_life=11.4, option_name_spread_otm=1.43,
                 option_name_spread_itm=0.72, option_name_spread_tenor_exponent=-0.16)
    a, b = engine(model(**ON)), engine(model(**ON, **dials))
    assert a.prices() == b.prices()
    t = big_name(a)
    x = expiries(a, t)[0]
    assert [r["mid"] for r in chain(a, t, x)] == [r["mid"] for r in chain(b, t, x)]


def test_the_moneyness_shape_quotes_a_share_of_the_price_by_delta():
    # against the same dealer quoting a flat share of the price (1 and 1)
    flat = engine(model(**ON, option_name_spread_otm=1.0, option_name_spread_itm=1.0))
    shaped = engine(model(**ON, option_name_spread_otm=1.5, option_name_spread_itm=0.6))
    t = big_name(flat)
    x = expiries(flat, t)[1]
    checked = 0
    for a in chain(flat, t, x):
        b = next(r for r in chain(shaped, t, x) if r["symbol"] == a["symbol"])
        if not (a["bid"] and b["bid"] and a["mid"] > 2.0):
            continue
        d = min(0.75, max(0.25, abs(a["delta"])))
        m = 1.5 + (1.0 - 1.5) * (d - 0.25) / 0.25 if d < 0.5 else 1.0 + (0.6 - 1.0) * (d - 0.5) / 0.25
        tick = a["tick"]
        # the ladder rounds each side away from the mid, by under a tick
        assert (b["ask"] - b["bid"]) == pytest.approx(m * (a["ask"] - a["bid"]), abs=2.0 * tick * (1.0 + m))
        checked += 1
    assert checked > 5


def test_the_tenor_exponent_scales_the_half_spread():
    flat = engine(model(**ON))
    tilted = engine(model(**ON, option_name_spread_tenor_exponent=-0.5))
    t = big_name(flat)
    for x in expiries(flat, t)[:3]:
        a = min((r for r in chain(flat, t, x) if r["right"] == "C"), key=lambda r: abs(r["delta"] - 0.5))
        b = next(r for r in chain(tilted, t, x) if r["symbol"] == a["symbol"])
        factor = (max(a["sessions_to_expiry"], 1.0) / 21.0) ** -0.5
        assert (b["ask"] - b["bid"]) == pytest.approx((a["ask"] - a["bid"]) * factor, abs=0.11)


def _pressure_move(params, contracts=40, wait=0):
    """The dealer's shift on the traded at-the-money call's expiry, as a
    share of its surface volatility: (quoted iv - surface iv) / surface iv."""
    e = engine(params)
    t = big_name(e)
    x = expiries(e, t)[1]
    row = min((r for r in chain(e, t, x) if r["right"] == "C"), key=lambda r: abs(r["delta"] - 0.5))
    e.submit("a", row["symbol"], float(contracts))
    if wait:
        e.run_session(9, 45, 3, wait)
    q = e.quote(row["symbol"])
    return (q["iv"] - q["surface_iv"]) / q["surface_iv"], row


def test_the_inventory_slope_moves_the_volatility_by_its_share_per_contract():
    move, row = _pressure_move(model(**ON, option_name_inventory_slope=2.0e-4), contracts=40)
    assert move == pytest.approx(40 * 2.0e-4, rel=0.2)
    base, _ = _pressure_move(model(**ON), contracts=40)
    assert base > 3 * move          # the constant response is steeper


def test_the_half_life_keeps_the_pressure_for_sessions():
    quick, _ = _pressure_move(model(**ON), contracts=40, wait=60)
    quick0, _ = _pressure_move(model(**ON), contracts=40)
    slow, _ = _pressure_move(model(**ON, option_name_inventory_half_life=10.0), contracts=40, wait=60)
    slow0, _ = _pressure_move(model(**ON, option_name_inventory_half_life=10.0), contracts=40)
    assert quick == pytest.approx(quick0 * 0.5 ** (60 / 15), rel=0.1)
    assert slow == pytest.approx(slow0 * 0.5 ** (60 / (10 * 390)), rel=0.02)
