"""The basis's night share (`basis_night_share`, pt-v22).

0.0 on every shipped preset, where the basis noise decays evenly over the
session's ticks and the night's steps. Off zero the night carries that share
of a day's decay. These tests hold it off on every preset, refused without a
night session, the close's basis and its close-to-close persistence
unchanged, the morning's carry of a close's deviation lower, and the stock
prices untouched.
"""
from __future__ import annotations

import numpy as np
import pytest

import tradefloor as tf

SMALL = tf.Universe.random(8, seed=5)
ON = dict(index_level_listed=1.0, forecast_horizon_sessions=252.0, futures_index_listed=1.0,
          basis_sd=3.753, basis_persistence=0.429, night_session_steps=10.0)


def model(**dials):
    return tf.ModelParams.from_preset("pt-v21", **dials)


def test_off_on_every_shipped_preset_silent_in_the_digest_and_refused_without_a_night():
    for name in tf.preset_names():
        assert tf.ModelParams.from_preset(name).basis_night_share == 0.0, name
    assert "basis_night_share" in set(tf.ModelParams.digest_silent_at_zero())
    assert model(basis_night_share=0.0).fingerprint == "pt-v21"
    with pytest.raises(tf.ValidationError):
        model(**dict(ON, night_session_steps=0.0), basis_night_share=0.76)
    for bad in (-0.1, 1.1):
        with pytest.raises(tf.ValidationError):
            model(**ON, basis_night_share=bad)


def _run(share, seed, days):
    e = tf.Engine(seed=seed, universe=SMALL, model=model(**ON, basis_night_share=share))
    close, morning, prices = [], [], []
    for d in range(days):
        if d:
            e.run_night()
        e.open_market()
        sym = e.contracts()[0]["symbol"] if e.contracts() else None
        morning.append(float(e.quote(sym)["basis_bp"]) if sym else np.nan)
        e.run_session(9, 30, 3, 390)
        e.close_market()
        sym = e.contracts()[0]["symbol"]
        close.append(float(e.quote(sym)["basis_bp"]))
        prices.append(e.prices())
    return np.array(close), np.array(morning), prices


def test_the_close_keeps_its_sd_and_persistence_and_the_morning_carries_less():
    c0, m0, p0 = _run(0.0, 11, 600)
    c1, m1, p1 = _run(0.76, 11, 600)
    assert p0 == p1, "a stock price moved"
    s0, s1 = c0[10:].std(), c1[10:].std()
    assert abs(s1 / s0 - 1) < 0.15, (s0, s1)
    r0 = np.corrcoef(c0[10:-1], c0[11:])[0, 1]
    r1 = np.corrcoef(c1[10:-1], c1[11:])[0, 1]
    assert abs(r1 - r0) < 0.15, (r0, r1)
    k0 = np.corrcoef(c0[10:-1], m0[11:])[0, 1]
    k1 = np.corrcoef(c1[10:-1], m1[11:])[0, 1]
    assert k0 > 0.9 and k1 < 0.75, (k0, k1)
