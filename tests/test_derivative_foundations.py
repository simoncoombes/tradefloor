"""What derivatives read: the price index, the live VIX and the forecast.

Three switches, each 0.0 on every shipped preset (`index_level_listed`,
`vix_intraday_live`, `forecast_horizon_sessions`), and the forecast's derived
dials beside them. Each reads the engine's state and writes only its own
fields, so a model with all of them on prints the prices the same model
prints with them off. These tests hold that, the index against a hand
computation through a listing and a delisting, the live VIX against the
published one at the close, the snapshot and the state hash, and (with
`TRADEFLOOR_SLOW_TESTS=1`) the forecast's unbiasedness on fresh histories.
"""
from __future__ import annotations

import math
import os
import pathlib
import struct
import sys

import pytest

import tradefloor as tf
from tradefloor.manifest import state_hash

UNIVERSE = tf.Universe.random(40, seed=111)
SMALL = tf.Universe.random(8, seed=5)

SWITCHES = ("index_level_listed", "vix_intraday_live", "forecast_horizon_sessions")
#: The forecast's derived dials as fitted on pt-v21's held-out histories
#: (`tools/calibration/forecast_dials.py derive --seeds 3001,3040`, 2,000
#: sessions each, on `Universe.random(40, seed=111)`).
DERIVED = dict(forecast_vix_dispersion=0.3, forecast_vix_dispersion_half_life=31.5,
               forecast_policy_shadow_discount=0.31, forecast_policy_persistence=0.62,
               forecast_policy_reversion=0.0475, forecast_policy_neutral=1.4)
ON = dict(index_level_listed=1.0, vix_intraday_live=1.0, forecast_horizon_sessions=252.0,
          **DERIVED)
SLOW = bool(os.environ.get("TRADEFLOOR_SLOW_TESTS"))


def model(**dials):
    return tf.ModelParams.from_preset("pt-v21", **dials)


def floats(buf):
    return struct.unpack("<%dd" % (len(buf) // 8), buf)


# ── Off on every shipped preset ─────────────────────────────────────────────

def test_every_switch_is_off_on_every_shipped_preset():
    for name in tf.preset_names():
        params = tf.ModelParams.from_preset(name)
        for dial in SWITCHES + tuple(k for k in DERIVED if k != "forecast_policy_neutral"):
            assert getattr(params, dial) == 0.0, (name, dial)
        assert params.forecast_policy_neutral == 2.5, name


def test_at_zero_the_switches_leave_the_fingerprint_alone():
    silent = set(tf.ModelParams.digest_silent_at_zero())
    for dial in SWITCHES + tuple(k for k in DERIVED if k != "forecast_policy_neutral"):
        assert dial in silent, dial
    assert tf.ModelParams.digest_silent_at_default()["forecast_policy_neutral"] == 2.5
    explicit = model(**{d: 0.0 for d in SWITCHES}, forecast_policy_neutral=2.5)
    assert explicit.fingerprint == "pt-v21"
    assert model(**ON).fingerprint.startswith("custom-")


def test_off_there_is_nothing_to_read_and_nothing_carried():
    engine = tf.Engine(seed=3, universe=SMALL, model="pt-v21")
    engine.run_days(2)
    assert engine.index_level is None
    assert engine.live_vix is None
    assert engine.forecast() is None
    snap = engine.state_snapshot()
    for key in ("index_divisor", "vix_live", "forecast"):
        assert key not in snap


# ── The switches move no price ──────────────────────────────────────────────

def _untraded(params, seed, days=12, steps=5):
    """Every print an untraded run makes: each step's tape, each close's
    prices, the macro state and every column, in order."""
    engine = tf.Engine(seed=seed, universe=UNIVERSE, model=params)
    out = []
    ticks = 390 // steps
    for _ in range(days):
        engine.open_market()
        for k in range(steps):
            minutes = 30 + k * ticks
            engine.run_session(9 + minutes // 60, minutes % 60, 3, ticks)
            out.append(engine.session_prices())
        engine.close_market()
        out.append(engine.prices())
        out.append(repr(sorted(engine.economy().items())))
        for field in ("price", "volume", "mispricing_s", "garch_variance"):
            out.append(engine.column(field))
    return out


@pytest.mark.parametrize("seed", [3, 17, 101, 9001])
def test_an_untraded_run_prints_the_same_prices_with_every_switch_on(seed):
    off = _untraded(model(), seed)
    on = _untraded(model(**ON), seed)
    assert len(off) == len(on)
    for i, (a, b) in enumerate(zip(off, on)):
        assert a == b, f"print {i} differs with the switches on"


# ── The index ───────────────────────────────────────────────────────────────

def _cap(engine, shares):
    prices = floats(engine.prices())[:len(shares)]
    return sum(p * s for p, s in zip(prices, shares))


def test_the_index_is_cap_over_divisor_by_hand_through_a_delisting_and_a_listing():
    instruments = list(SMALL)
    shares = [i.shares_outstanding for i in instruments]
    engine = tf.Engine(seed=7, universe=SMALL, model=model(index_level_listed=1.0))
    assert engine.index_level == {"level": 1000.0, "close": None, "divisor": None,
                                  "constituents": len(instruments)}
    engine.open_market()
    divisor = _cap(engine, shares) / 1000.0
    index = engine.index_level
    assert index["divisor"] == divisor
    assert math.isclose(index["level"], 1000.0, rel_tol=1e-12)
    engine.run_session(9, 30, 3, 390)
    level = _cap(engine, shares) / divisor
    assert math.isclose(engine.index_level["level"], level, rel_tol=1e-12)
    engine.close_market()
    assert math.isclose(engine.index_level["close"], level, rel_tol=1e-12)

    # Delist the third name between sessions: the level holds, the divisor
    # takes the survivors' capitalisation at the prices standing.
    standing = _cap(engine, shares) / divisor
    engine.delist(2)
    shares = shares[:2] + shares[3:]
    divisor = _cap(engine, shares) / standing
    index = engine.index_level
    assert math.isclose(index["divisor"], divisor, rel_tol=1e-12)
    assert math.isclose(index["level"], standing, rel_tol=1e-12)
    assert index["constituents"] == len(instruments) - 1

    # List a new name: the level holds again.
    new = instruments[2]
    engine.list_instrument(tf.Instrument(
        "NEWCO", new.sector, initial_price=new.initial_price,
        shares_outstanding=new.shares_outstanding, eps=new.eps,
        book_value_per_share=new.book_value_per_share, revenue_growth=new.revenue_growth,
        avg_volume=new.avg_volume, beta=new.beta))
    shares = shares + [new.shares_outstanding]
    divisor = _cap(engine, shares) / standing
    assert math.isclose(engine.index_level["level"], standing, rel_tol=1e-12)
    assert math.isclose(engine.index_level["divisor"], divisor, rel_tol=1e-12)

    # The next session prices the new roster on the new divisor.
    engine.open_market()
    engine.run_session(9, 30, 3, 390)
    assert math.isclose(engine.index_level["level"], _cap(engine, shares) / divisor,
                        rel_tol=1e-12)
    engine.close_market()


# ── The live VIX ────────────────────────────────────────────────────────────

def test_the_live_vix_is_the_published_vix_at_the_close_and_moves_inside_the_session():
    engine = tf.Engine(seed=5, universe=UNIVERSE, model=model(vix_intraday_live=1.0))
    engine.run_days(3)
    assert engine.live_vix == engine.macro_fields["vix"]
    moves, returns, gaps = [], [], []
    for _ in range(30):
        engine.open_market()
        before = engine.live_vix
        level = floats(engine.prices())
        for k in range(6):
            minutes = 30 + k * 65
            engine.run_session(9 + minutes // 60, minutes % 60, 3, 65)
            now = floats(engine.prices())
            moves.append(engine.live_vix - before)
            returns.append(sum(math.log(a / b) for a, b in zip(now, level)) / len(now))
            before, level = engine.live_vix, now
            # Within the session the published VIX is the last close's.
            assert engine.macro_fields["vix"] != engine.live_vix
        last = engine.live_vix
        engine.close_market()
        assert engine.live_vix == engine.macro_fields["vix"]
        gaps.append(engine.macro_fields["vix"] - last)
    # The live VIX falls as the market rises within the session.
    n = len(moves)
    mx, my = sum(returns) / n, sum(moves) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(returns, moves))
    var_x = sum((x - mx) ** 2 for x in returns)
    var_y = sum((y - my) ** 2 for y in moves)
    assert cov / math.sqrt(var_x * var_y) < -0.3
    # The last minute's projection is the close but for the close's draws.
    assert abs(sum(gaps) / len(gaps)) < 0.25
    assert max(abs(g) for g in gaps) < 3.0


# ── The forecast ────────────────────────────────────────────────────────────

def test_the_forecast_runs_its_horizon_from_public_state():
    engine = tf.Engine(seed=9, universe=UNIVERSE, model=model(**ON))
    assert engine.forecast() is None
    engine.run_days(4)
    f = engine.forecast()
    assert f["horizon"] == 252 and f["day"] == 4
    for key in ("vix", "index_variance", "policy_rate", "oil"):
        assert len(f[key]) == 252 and all(math.isfinite(v) for v in f[key])
    assert set(f["name_variance"]) == {i.ticker for i in UNIVERSE}
    assert all(len(v) == 252 for v in f["name_variance"].values())
    # The policy path starts from the rate standing and the VIX from near the
    # published one.
    rate = engine.economy()["federal_funds_rate"] / 100.0
    assert abs(f["policy_rate"][0] - rate) < 0.01
    assert abs(f["vix"][0] - engine.macro_fields["vix"]) < 0.25 * engine.macro_fields["vix"]
    # A pin re-reads it on the pinned state.
    engine.pin_macro(vix=45.0)
    assert engine.forecast()["vix"][0] > f["vix"][0]


# ── Snapshot and state hash ─────────────────────────────────────────────────

def test_the_keys_are_carried_hashed_and_mirrored_and_a_restore_continues():
    params = model(**ON)
    engine = tf.Engine(seed=13, universe=SMALL, model=params)
    engine.run_days(3)
    engine.open_market()
    engine.run_session(9, 30, 3, 40)
    snap = engine.state_snapshot()
    for key in ("index_divisor", "vix_live", "forecast"):
        assert key in snap, key
    assert state_hash(snap) == engine.state_hash()
    copy = tf.Engine(seed=99, universe=SMALL, model=params)
    copy.restore_state(snap)
    assert copy.state_hash() == engine.state_hash()
    for e in (engine, copy):
        e.run_session(10, 10, 3, 350)
        e.close_market()
        e.run_days(2)
    assert copy.state_hash() == engine.state_hash()
    assert copy.prices() == engine.prices()
    assert copy.forecast() == engine.forecast()
    assert copy.index_level == engine.index_level


@pytest.mark.parametrize("key, dials", [
    ("index_divisor", dict(index_level_listed=1.0)),
    ("vix_live", dict(vix_intraday_live=1.0)),
    ("forecast", dict(forecast_horizon_sessions=21.0)),
])
def test_a_key_is_refused_by_name_on_a_model_without_its_dial(key, dials):
    engine = tf.Engine(seed=13, universe=SMALL, model=model(**dials))
    engine.run_days(2)
    engine.open_market()
    engine.run_session(9, 30, 3, 40)
    snap = engine.state_snapshot()
    assert key in snap
    with pytest.raises(tf.ValidationError, match=key):
        tf.Engine(seed=13, universe=SMALL, model="pt-v21").restore_state(snap)


# ── Unbiasedness on fresh histories (RF5, VF8, OF4 at construction level) ──

@pytest.mark.skipif(not SLOW, reason="sixteen forty-name histories of 2,000 sessions; "
                    "set TRADEFLOOR_SLOW_TESTS=1")
def test_the_forecast_is_unbiased_on_fresh_histories():
    """Mean realised less forecast, per history, then across histories: zero
    within two standard errors for the VIX (VF8), oil (OF4) and the policy
    rate (RF5) at 21, 63 and 126 sessions, on 16 fresh histories of 2,000
    sessions that were never used to fit the derived dials. The runs are
    `tools/calibration/forecast_dials.py check`'s.

    Long histories because the policy rate's errors are skewed: the ladder
    hikes in small steps and cuts in large ones, so most short samples read a
    positive mean error that a rare cut would take back. On 12 histories of
    1,000 sessions the policy rate read +0.041 and +0.095 points at 63 and 126
    sessions, 2.2 standard errors each.
    """
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools" / "calibration"))
    import forecast_dials  # noqa: PLC0415

    histories = forecast_dials.run(range(7001, 7017), 2000, DERIVED, "pt-v21",
                                   os.cpu_count() or 4)
    failures = []
    for series in ("vix", "oil", "rate"):
        for h in (21, 63, 126):
            means = [sum(e) / len(e) for e in
                     (forecast_dials.errors(rows, h, series) for rows in histories)]
            mean = sum(means) / len(means)
            sd = math.sqrt(sum((v - mean) ** 2 for v in means) / (len(means) - 1))
            se = sd / math.sqrt(len(means))
            if abs(mean) > 2.0 * se:
                failures.append(f"{series} at {h}: {mean:+.4g} against 2 se {2 * se:.4g}")
    assert not failures, failures
