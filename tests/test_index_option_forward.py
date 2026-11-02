"""The index options' forward from the listed future
(`options_index_forward_from_future`, pt-v22).

Off, on every shipped preset, the dealer prices every index option off the
carry `(S - PV(D)) e^{r tau}`, while the listed index future of the same
expiry trades at that carry plus a basis. A conversion against the future
then locks the basis whenever it is wider than the spreads. On, an expiry
with a future of its own takes the forward from that future's book mid, so
the chain's mids meet put-call parity against the future exactly; an expiry
without one keeps the carry. It moves no price a stock or the index prints.
"""
from __future__ import annotations

import math

import pytest

import tradefloor as tf

UNIVERSE = tf.Universe.random(30, seed=111)
ON = dict(index_level_listed=1.0, vix_intraday_live=1.0, forecast_horizon_sessions=252.0,
          forecast_vix_dispersion=0.3, forecast_vix_dispersion_half_life=31.5, forecast_vix_dispersion_skew=0.81,
          surface_ssvi=1.0, surface_skew_physical=-0.8, surface_skew_physical_slope=0.3, surface_skew_premium=-1.0,
          surface_skew_premium_slope=0.5, surface_curvature=1.0, surface_curvature_exponent=0.5,
          options_index_listed=1.0, futures_index_listed=1.0, basis_sd=3.753, basis_persistence=0.429)


def model(**dials):
    return tf.ModelParams.from_preset("pt-v21", **dials)


def run(params, seed=7, days=8, steps=3):
    e = tf.Engine(seed=seed, universe=UNIVERSE, model=params)
    out = []
    for _ in range(days):
        e.open_market()
        ticks = 390 // steps
        for k in range(steps):
            minutes = 30 + k * ticks
            e.run_session(9 + minutes // 60, minutes % 60, 3, ticks)
            out.append(e.session_prices())
        e.close_market()
        out.append(e.prices())
        out.append(repr(sorted(e.economy().items())))
    return out, e


def test_off_it_is_silent_and_refused_without_the_options_and_the_futures():
    assert model().options_index_forward_from_future == 0.0
    assert model(**ON).fingerprint == model(**ON, options_index_forward_from_future=0.0).fingerprint
    for drop in ("options_index_listed", "futures_index_listed"):
        with pytest.raises(Exception):
            model(**{**ON, drop: 0.0}, options_index_forward_from_future=1.0)
    with pytest.raises(Exception):
        model(**ON, options_index_forward_from_future=0.5)


def test_the_same_prices_print_with_it_on():
    off, _ = run(model(**ON))
    on, _ = run(model(**ON, options_index_forward_from_future=1.0))
    assert off == on


def _mid_session(params):
    e = tf.Engine(seed=7, universe=UNIVERSE, model=params)
    for _ in range(5):
        e.open_market(); e.run_session(9, 30, 3, 390); e.close_market()
    e.open_market(); e.run_session(9, 30, 3, 15)
    return e


def test_an_expiry_with_its_own_future_prices_off_the_futures_mid_and_meets_parity_against_it():
    off = _mid_session(model(**ON))
    on = _mid_session(model(**ON, options_index_forward_from_future=1.0))
    futures = {c["expiry"]: c["symbol"] for c in on.contracts() if c["root"] == "IDX" and c["kind"] == "future"}
    expiries = sorted({c["expiry"] for c in on.contracts() if c["root"] == "IDX" and c["kind"] == "option"})
    shared = [x for x in expiries if x in futures]
    assert shared and [x for x in expiries if x not in futures]
    for x in expiries:
        chain_on, chain_off = on.chain("IDX", x), off.chain("IDX", x)
        if x not in futures:
            assert [r["mid"] for r in chain_on] == [r["mid"] for r in chain_off]
            continue
        q = on.quote(futures[x])
        F = q["mid"]
        assert F is not None and chain_on[0]["forward"] == F
        assert abs(F - chain_off[0]["forward"]) > 1e-9          # the basis the carry missed
        calls = {r["strike"]: r for r in chain_on if r["right"] == "C"}
        for p in (r for r in chain_on if r["right"] == "P"):
            c = calls[p["strike"]]
            df = math.exp(-c["rate"] * c["sessions_to_expiry"] / 252.0)
            assert c["mid"] - p["mid"] == pytest.approx(df * (F - p["strike"]), abs=1e-9 * F)
