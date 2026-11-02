"""The VIX's fear memory (`vix_fear_uptake`, `vix_fear_half_life`).

Under the identity the VIX reverts at `vix_mean_reversion` to a target that is
the variance read-back held against the anchor. Off zero, before each close a
memory `f` takes up a share `k` of the VIX's log excursion over that target
and decays at its half-life `H`,

    f' = 0.5^(1 / H) f + k ln(vix / (T exp(f)))

and the step reverts to `T exp(f')`, so a move the VIX holds is carried into
the target and leaves at the memory's own half-life.

These tests hold that it is off on every preset and silent in the digest at
0.0, that a model with it on carries the memory in the snapshot and both
state hashes and continues bit for bit from a restore, that it moves the VIX
and keeps the forecast's first step on the close's own law, and that the
dials are refused out of range or without their partners.
"""

import math

import pytest

import tradefloor as tf
from tradefloor import manifest

UNIVERSE = list(tf.Universe.random(6, seed=3))
ON = {"vix_fear_uptake": 0.3, "vix_fear_half_life": 8.0}


def engine(seed=7, preset="pt-v21", **dials):
    return tf.Engine(seed=seed, universe=UNIVERSE,
                     model=tf.ModelParams.from_preset(preset, **dials))


@pytest.mark.parametrize("preset", tf.preset_names())
def test_off_on_every_shipped_preset(preset):
    d = tf.ModelParams.from_preset(preset).to_dict()
    assert d["vix_fear_uptake"] == 0.0
    assert d["vix_fear_half_life"] == 0.0


def test_silent_in_the_digest_at_zero():
    silent = set(tf.ModelParams.digest_silent_at_zero())
    assert {"vix_fear_uptake", "vix_fear_half_life"} <= silent
    explicit = tf.ModelParams.from_preset("pt-v21", vix_fear_uptake=0.0,
                                          vix_fear_half_life=0.0)
    assert explicit.fingerprint == "pt-v21"
    assert tf.ModelParams.from_preset("pt-v21", **ON).fingerprint.startswith("custom-")


def test_off_no_memory_is_kept():
    e = engine()
    e.run_days(30, record=False)
    assert "vix_fear" not in e.state_snapshot()


def test_on_it_moves_the_vix_and_is_carried():
    off, on = engine(seed=11), engine(seed=11, **ON)
    for e in (off, on):
        e.run_days(200, record=False)
    snap = on.state_snapshot()
    assert "vix_fear" in snap
    assert math.isfinite(snap["vix_fear"]) and snap["vix_fear"] != 0.0
    assert on.macro_fields["vix"] != off.macro_fields["vix"]
    # The memory decays at its half-life, so it stays within the log range a
    # VIX between the floor and the ceiling allows.
    assert abs(snap["vix_fear"]) < math.log(181.3295 / 10.0)


def test_the_snapshot_and_a_restore_carry_the_memory():
    e = engine(**ON)
    e.run_days(120, record=False)
    snap = e.state_snapshot()
    assert manifest.state_hash(snap) == e.state_hash()
    moved = dict(snap, vix_fear=snap["vix_fear"] + 0.125)
    assert manifest.state_hash(moved) != e.state_hash()
    twin = engine(**ON)
    twin.restore_state(snap)
    assert twin.state_hash() == e.state_hash()
    for x in (e, twin):
        x.run_days(30, record=False, first_day=120)
    assert twin.state_hash() == e.state_hash()
    assert twin.macro_fields["vix"] == e.macro_fields["vix"]
    assert twin.prices() == e.prices()


def test_a_restored_memory_moves_the_continuation():
    e = engine(**ON)
    e.run_days(60, record=False)
    snap = e.state_snapshot()
    twin = engine(**ON)
    twin.restore_state(dict(snap, vix_fear=snap["vix_fear"] + 0.5))
    for x in (e, twin):
        x.run_days(1, record=False, first_day=60)
    # More fear in the target, so tonight's VIX closes higher.
    assert twin.state_snapshot()["economy"]["vix"] > e.state_snapshot()["economy"]["vix"]


def test_the_forecast_steps_the_memory():
    # The forecast's first VIX is the close's step at its means on the
    # advanced memory: with the memory on, it differs from the forecast the
    # same state gives with the memory written to its value after one more
    # take-up, and it stays finite at every horizon.
    dials = dict(ON, forecast_horizon_sessions=63.0)
    e = engine(**dials)
    e.run_days(150, record=False)
    f = e.forecast()["vix"]
    assert len(f) == 63 and all(math.isfinite(v) and v > 0.0 for v in f)
    snap = e.state_snapshot()
    twin = engine(**dials)
    twin.restore_state(dict(snap, vix_fear=snap["vix_fear"] + 0.25))
    twin.run_days(1, record=False, first_day=150)
    e.run_days(1, record=False, first_day=150)
    g, h = e.forecast()["vix"], twin.forecast()["vix"]
    # The memory decays: the two forecasts start apart and come together.
    assert h[0] > g[0]
    assert abs(h[-1] - g[-1]) < abs(h[0] - g[0])


@pytest.mark.parametrize("dials", [
    {"vix_fear_uptake": -0.1, "vix_fear_half_life": 8.0},
    {"vix_fear_uptake": 1.0, "vix_fear_half_life": 8.0},
    {"vix_fear_uptake": float("nan"), "vix_fear_half_life": 8.0},
    {"vix_fear_uptake": 0.3},                               # no half-life
    {"vix_fear_uptake": 0.3, "vix_fear_half_life": -1.0},
    {"vix_fear_uptake": 0.3, "vix_fear_half_life": 3000.0},
])
def test_the_ranges(dials):
    with pytest.raises(Exception):
        tf.ModelParams.from_preset("pt-v21", **dials)


def test_refused_without_the_identity():
    # pt-v18 runs no identity read-back, so there is no target to carry it.
    with pytest.raises(Exception):
        tf.ModelParams.from_preset("pt-v18", **ON)
    # The half-life alone is unread, and accepted.
    tf.ModelParams.from_preset("pt-v18", vix_fear_half_life=8.0)
