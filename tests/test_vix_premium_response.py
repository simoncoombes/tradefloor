"""The VIX futures premium's time-series response (`futures_vix_premium_response`).

0.0 on every shipped preset and left out of the digest there; refused without
`futures_vix_listed`. Off zero the premium's slope on the VIX moves from the
frozen level slope b(n) toward the response g(n) measured on CFE VX. It moves
only the VIX futures' own marks: the stocks, closes, economy and VIX are the
run's without it.
"""
from __future__ import annotations

import math

import pytest

import tradefloor as tf

from test_vix_futures import ON, SMALL, _untraded, day, model, premium, vix_contracts

GAMMA, TAU = -0.0716, 0.5
CENTRE = 19.3197


def response(n):
    return 0.0 if n <= 1 else GAMMA * (1 - math.exp(-(n - 1) / TAU))


def test_off_on_every_preset_silent_in_the_digest_and_refused_without_the_switch():
    assert "futures_vix_premium_response" in set(tf.ModelParams.digest_silent_at_zero())
    for name in tf.preset_names():
        assert tf.ModelParams.from_preset(name).futures_vix_premium_response == 0.0
    assert model(futures_vix_premium_response=0.0).fingerprint == "pt-v21"
    with pytest.raises(ValueError, match="futures_vix_listed"):
        model(futures_vix_premium_response=1.0)
    with pytest.raises(ValueError, match=r"\[0, 1\]"):
        model(**ON, futures_vix_premium_response=1.5)


def test_zero_is_the_frozen_premium_to_the_bit():
    a = tf.Engine(seed=7, universe=SMALL, model=model(**ON))
    b = tf.Engine(seed=7, universe=SMALL, model=model(**ON, futures_vix_premium_response=0.0))
    for _ in range(30):
        day(a); day(b)
        assert [a.quote(c["symbol"])["mark"] for c in vix_contracts(a)] == \
               [b.quote(c["symbol"])["mark"] for c in vix_contracts(b)]
    assert a.state_hash() == b.state_hash()


@pytest.mark.parametrize("w", [1.0, 0.5])
def test_the_close_marks_take_the_moved_slope(w):
    engine = tf.Engine(seed=7, universe=SMALL, model=model(**ON, futures_vix_premium_response=w))
    worst, s = 0.0, -1
    for _ in range(60):
        day(engine)
        s += 1
        f = engine.forecast()
        pub = engine.macro_fields["vix"]
        for c in vix_contracts(engine):
            n = c["expiry"] - s
            expected = pub if n == 1 else f["vix"][n - 2]
            b = 0.0 if n <= 1 else 0.36 * (1 - math.exp(-(n - 1) / 148.0))
            moved = premium(n, pub) + w * (response(n) - b) * (pub - CENTRE)
            worst = max(worst, abs(engine.quote(c["symbol"])["mark"] - (expected + moved)))
    assert worst < 1e-9, worst


def test_it_moves_no_stock_price_or_vix():
    off, _ = _untraded(model(**ON), 17, days=8)
    on, _ = _untraded(model(**ON, futures_vix_premium_response=1.0), 17, days=8)
    assert off == on
