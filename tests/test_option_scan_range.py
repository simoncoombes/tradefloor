"""The single-name option scan at the portfolio-margin rule's range (`margin_option_scan_range`).

FINRA Rule 4210(g)(2)(F) takes a portfolio margin account's theoretical gains and losses at ten equidistant
valuation points over +/- 15% for any product that is, or is based on, an equity security. Off zero the dial puts
the single-name scan's price points there; 0.0, on every preset, keeps the scan's own size. The margin reads quotes
and writes nothing, so no price moves either way.
"""
from __future__ import annotations

import pytest

import tradefloor as tf
from test_name_options import ON, SMALL, _price, day, model, names, strike_near

RULE = 0.15


def _engines(**extra):
    a = tf.Engine(seed=19, universe=SMALL, model=model(**ON, **extra))
    b = tf.Engine(seed=19, universe=SMALL, model=model(**ON, **extra, margin_option_scan_range=RULE))
    for _ in range(3):
        day(a)
        day(b)
    return a, b


def test_the_range_is_silent_at_zero_and_refused_without_the_margin():
    assert "margin_option_scan_range" in set(tf.ModelParams.digest_silent_at_zero())
    for name in tf.preset_names():
        assert tf.ModelParams.from_preset(name).margin_option_scan_range == 0.0
    with pytest.raises(Exception, match="margin_scan_coverage"):
        model(**dict(ON, margin_scan_coverage=0.0), margin_option_scan_range=RULE)
    with pytest.raises(Exception, match=r"\[0, 1\]"):
        model(**ON, margin_option_scan_range=1.5)


def test_no_price_moves_and_the_state_is_the_same():
    scan, rule = _engines()
    assert scan.prices() == rule.prices()
    ticker = scan.tickers[3]
    expiry = sorted({c["expiry"] for c in names(scan, ticker)})[0]
    for c in names(scan, ticker):
        if c["expiry"] == expiry:
            qa, qb = scan.quote(c["symbol"]), rule.quote(c["symbol"])
            assert (qa["bid"], qa["ask"], qa["fair"]) == (qb["bid"], qb["ask"], qb["fair"])


def test_a_short_strangle_asks_its_loss_at_the_end_of_the_range():
    scan, rule = _engines()
    ticker = rule.tickers[3]
    expiry = sorted({c["expiry"] for c in names(rule, ticker)})[0]
    call = strike_near(rule, ticker, expiry, "C", 3)["symbol"]
    put = strike_near(rule, ticker, expiry, "P", -3)["symbol"]
    book = [(call, -1.0), (put, -1.0)]
    wide, own = rule.option_margin(book), scan.option_margin(book)
    assert wide > own > 0.0
    # At least what a 15% move takes from the leg it runs against, at the
    # options' intrinsic value: the price minus the call's strike.
    price = _price(rule, ticker)
    k_call = strike_near(rule, ticker, expiry, "C", 3)["strike"]
    assert wide >= 100.0 * (price * (1 + RULE) - k_call) - 1e-9
    # A long option still asks at most its value, and offsetting legs net.
    assert rule.option_margin([(call, 1.0)]) <= 100.0 * rule.quote(call)["fair"] * 1.2
    assert rule.option_margin([(call, -1.0), (call, 1.0)]) == 0.0


def test_stock_held_alone_asks_the_range_of_its_value():
    scan, rule = _engines()
    ticker = rule.tickers[3]
    price = _price(rule, ticker)
    assert rule.stock_margin([(ticker, 100.0)]) == pytest.approx(100.0 * RULE * price, rel=1e-12)
    assert rule.stock_margin([(ticker, -100.0)]) == rule.stock_margin([(ticker, 100.0)])
    assert scan.stock_margin([(ticker, 100.0)]) < rule.stock_margin([(ticker, 100.0)])


def test_the_floor_still_applies_beside_the_range():
    _, rule = _engines(margin_option_minimum=0.375)
    ticker = rule.tickers[3]
    expiry = sorted({c["expiry"] for c in names(rule, ticker)})[0]
    far = strike_near(rule, ticker, expiry, "C", 12)["symbol"]
    assert rule.option_margin([(far, -1.0)]) >= 0.375 * 100 - 1e-9
