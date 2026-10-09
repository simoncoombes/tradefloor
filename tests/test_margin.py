"""The listed contracts' initial margin (pt-v22 phase 1).

Two dials, 0.0 on every shipped preset (`margin_scan_coverage`,
`margin_scan_tail`). The margin reads the contracts' marks and writes only
its own state, so no price moves. These tests hold that, the margin against
its recursion, and its state across a snapshot.
"""
from __future__ import annotations

import math
import struct
from statistics import NormalDist

import pytest

import tradefloor as tf
from tradefloor.manifest import state_hash

SMALL = tf.Universe.random(8, seed=5)
HALF_LIFE = 7.0
COVERAGE, TAIL = 0.99, 1.7
CONTRACTS = dict(index_level_listed=1.0, vix_intraday_live=1.0, forecast_horizon_sessions=252.0,
                 forecast_vix_dispersion=0.3, forecast_vix_dispersion_half_life=31.5,
                 forecast_vix_dispersion_skew=0.81, forecast_policy_shadow_discount=0.36,
                 forecast_policy_persistence=0.6, forecast_policy_reversion=0.05,
                 forecast_policy_neutral=1.48, futures_index_listed=1.0, basis_sd=3.753,
                 basis_persistence=0.429, futures_vix_listed=1.0,
                 futures_vix_live_fast_share=0.713, futures_vix_live_fast_half_life=5.96,
                 futures_vix_live_slow_half_life=71.7, futures_rates_listed=1.0,
                 futures_oil_listed=1.0)
ON = dict(CONTRACTS, margin_scan_coverage=COVERAGE, margin_scan_tail=TAIL)


def model(**dials):
    return tf.ModelParams.from_preset("pt-v21", **dials)


def day(engine):
    engine.open_market()
    engine.run_session(9, 30, 3, 390)
    engine.close_market()


def test_both_dials_are_off_on_every_shipped_preset_and_silent_in_the_digest():
    silent = set(tf.ModelParams.digest_silent_at_zero())
    for name in tf.preset_names():
        p = tf.ModelParams.from_preset(name)
        assert p.margin_scan_coverage == 0.0 and p.margin_scan_tail == 0.0, name
    assert {"margin_scan_coverage", "margin_scan_tail"} <= silent


def test_off_no_quote_carries_a_margin_and_nothing_is_carried():
    engine = tf.Engine(seed=3, universe=SMALL, model=model(**CONTRACTS))
    for _ in range(3):
        day(engine)
    for c in engine.contracts():
        q = engine.quote(c["symbol"])
        assert q["initial_margin"] is None and q["maintenance_margin"] is None
    assert "margin" not in engine.state_snapshot()


def test_the_allowance_needs_the_coverage_and_the_coverage_is_a_share():
    with pytest.raises(tf.ValidationError, match="margin_scan_coverage"):
        model(margin_scan_tail=1.5)
    for bad in (0.5, 1.0, -0.1):
        with pytest.raises(tf.ValidationError, match="margin_scan_coverage"):
            model(margin_scan_coverage=bad)


def test_the_margin_moves_no_price():
    off = tf.Engine(seed=17, universe=SMALL, model=model(**CONTRACTS))
    on = tf.Engine(seed=17, universe=SMALL, model=model(**ON))
    for _ in range(30):
        day(off)
        day(on)
        assert bytes(off.prices()) == bytes(on.prices())
        assert off.economy() == on.economy()
        for c in off.contracts():
            assert off.quote(c["symbol"])["mark"] == on.quote(c["symbol"])["mark"]


def test_the_margin_is_the_multiplier_times_z_t_sigma_and_sigma_follows_its_marks():
    """Every contract's margin, read back to its variance, follows
    var' = d var + (1 - d) (mark' - mark)^2 from one close to the next, and
    maintenance is initial over 1.1."""
    z = NormalDist().inv_cdf(0.5 + 0.5 * COVERAGE)
    decay = 0.5 ** (1.0 / HALF_LIFE)
    engine = tf.Engine(seed=7, universe=SMALL, model=model(**ON))
    prev, checked = {}, 0
    for _ in range(60):
        day(engine)
        now = {}
        for c in engine.contracts():
            q = engine.quote(c["symbol"])
            assert q["initial_margin"] is not None, c["symbol"]
            assert q["maintenance_margin"] == pytest.approx(q["initial_margin"] / 1.1, rel=1e-15)
            var = (q["initial_margin"] / (q["multiplier"] * z * TAIL)) ** 2
            now[c["symbol"]] = (q["mark"], var)
            if c["symbol"] in prev:
                mark0, var0 = prev[c["symbol"]]
                want = decay * var0 + (1 - decay) * (q["mark"] - mark0) ** 2
                assert var == pytest.approx(want, rel=1e-9, abs=1e-18), c["symbol"]
                checked += 1
        prev = now
    assert checked > 1000


def test_a_newly_listed_contract_takes_its_familys_front_variance():
    engine = tf.Engine(seed=9, universe=SMALL, model=model(**ON))
    for _ in range(14):
        day(engine)
    engine.open_market()  # VIX.F0014 and OIL.F0014 settle; F0140 and F0266 are listed
    engine.run_session(9, 30, 3, 390)
    engine.close_market()
    vix = [c["symbol"] for c in engine.contracts() if c["root"] == "VIX"]
    assert vix[-1] == "VIX.F0140"
    front, new = engine.quote(vix[0]), engine.quote(vix[-1])
    # Seeded at its first close with the front's variance as that close left it.
    assert new["initial_margin"] == front["initial_margin"] > 0.0


def test_a_snapshot_restores_the_margin_and_the_hash_reads_it():
    engine = tf.Engine(seed=13, universe=SMALL, model=model(**ON))
    for _ in range(20):
        day(engine)
    snap = engine.state_snapshot()
    assert "margin" in snap
    assert state_hash(snap) == engine.state_hash()
    raw = snap["margin"]
    last, = struct.unpack("<d", raw[-8 * 2:-8])
    changed = dict(snap, margin=raw[:-16] + struct.pack("<d", math.nextafter(last, math.inf)) + raw[-8:])
    assert state_hash(changed) != state_hash(snap)
    restored = tf.Engine(seed=99, universe=SMALL, model=model(**ON))
    restored.restore_state(snap)
    for e in (engine, restored):
        for _ in range(10):
            day(e)
    assert restored.state_hash() == engine.state_hash()
    margins = lambda e: [e.quote(c["symbol"])["initial_margin"] for c in e.contracts()]
    assert margins(restored) == margins(engine)
    off = tf.Engine(seed=99, universe=SMALL, model=model(**CONTRACTS))
    with pytest.raises(tf.ValidationError, match="margin_scan_coverage"):
        off.restore_state(snap)
