"""The option scan's own tail allowance (`margin_scan_option_tail`).

0.0 on every shipped preset, where the option scan takes `margin_scan_tail`
as the futures' margin does. Off zero it sizes the option scan's price move
and volatility shift in place of `margin_scan_tail`. The scan reads quotes
and writes nothing, so no price moves. These tests hold that, the branch at
0.0, and that the futures' margin keeps `margin_scan_tail`.
"""
from __future__ import annotations

import pytest

import tradefloor as tf
from tradefloor.portfolio import is_name_option

SMALL = tf.Universe.random(10, seed=5)
BASE = dict(index_level_listed=1.0, vix_intraday_live=1.0, forecast_horizon_sessions=252.0,
            forecast_vix_dispersion=0.3, forecast_vix_dispersion_half_life=31.5,
            forecast_vix_dispersion_skew=0.81)
SURFACE = dict(surface_ssvi=1.0, surface_skew_physical=-0.8, surface_skew_physical_slope=0.3,
               surface_skew_premium=-1.0, surface_skew_premium_slope=0.5, surface_curvature=1.0,
               surface_curvature_exponent=0.5, surface_term_premium_short=-0.03,
               surface_term_premium_long=0.1, surface_earnings_weight=1.0)
ON = dict(BASE, **SURFACE, options_names_listed=1.0, surface_name_premium=0.1, surface_name_skew=0.5,
          option_dealer_spread=0.004, futures_index_listed=1.0, basis_sd=3.753, basis_persistence=0.429,
          margin_scan_coverage=0.99, margin_scan_tail=1.258)


def model(**dials):
    return tf.ModelParams.from_preset("pt-v21", **dials)


def day(engine):
    engine.open_market()
    engine.run_session(9, 30, 3, 390)
    engine.close_market()


def short_strangle(engine):
    ticker = engine.tickers[3]
    opts = [c for c in engine.contracts() if is_name_option(c["symbol"]) and c["root"] == ticker]
    expiry = min(c["expiry"] for c in opts)
    rows = sorted(engine.chain(ticker, expiry), key=lambda r: r["strike"])
    price = float(dict(zip(engine.tickers, __import__("numpy").frombuffer(engine.prices(), "<f8")))[ticker])
    calls = [r for r in rows if r["right"] == "C" and r["strike"] > price]
    puts = [r for r in rows if r["right"] == "P" and r["strike"] < price]
    return [(calls[0]["symbol"], -1.0), (puts[-1]["symbol"], -1.0)]


def test_off_on_every_shipped_preset_and_silent_in_the_digest():
    silent = set(tf.ModelParams.digest_silent_at_zero())
    for name in tf.preset_names():
        assert tf.ModelParams.from_preset(name).margin_scan_option_tail == 0.0, name
    assert "margin_scan_option_tail" in silent


def test_refused_without_the_coverage_and_out_of_range():
    with pytest.raises(tf.ValidationError, match="margin_scan_coverage"):
        model(margin_scan_option_tail=0.8)
    for bad in (-0.1, 5.5):
        with pytest.raises(tf.ValidationError, match="margin_scan_option_tail"):
            model(margin_scan_coverage=0.99, margin_scan_option_tail=bad)


def test_at_zero_the_option_scan_takes_margin_scan_tail():
    zero = tf.Engine(seed=19, universe=SMALL, model=model(**ON))
    same = tf.Engine(seed=19, universe=SMALL, model=model(**ON, margin_scan_option_tail=1.258))
    for _ in range(3):
        day(zero)
        day(same)
    legs = short_strangle(zero)
    assert zero.option_margin(legs) == same.option_margin(legs) > 0.0


def test_the_option_tail_sizes_the_option_scan_alone_and_moves_no_price():
    off = tf.Engine(seed=19, universe=SMALL, model=model(**ON))
    on = tf.Engine(seed=19, universe=SMALL, model=model(**ON, margin_scan_option_tail=0.8))
    for _ in range(5):
        day(off)
        day(on)
        assert bytes(off.prices()) == bytes(on.prices())
        assert off.economy() == on.economy()
    legs = short_strangle(off)
    assert 0.0 < on.option_margin(legs) < off.option_margin(legs)
    # The futures' margin keeps margin_scan_tail.
    futs = [c["symbol"] for c in off.contracts() if not is_name_option(c["symbol"])]
    assert futs
    for s in futs:
        assert off.quote(s)["initial_margin"] == on.quote(s)["initial_margin"]
        assert off.quote(s)["mark"] == on.quote(s)["mark"]
