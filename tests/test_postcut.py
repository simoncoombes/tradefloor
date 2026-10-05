"""The stress hold and the priced policy path (`fed_stress_hold`,
`treasury_path_pricing`, `treasury_path_half_life`).

The meeting's arithmetic (a rise held inside the hold under the inflation
gate, the put giving nothing back, a cut standing, the priced path's move of
the 10-year on the day) is held on a hand-built economy in
rust/tests/postcut.rs. These hold the engine around it: every preset ships
the three at 0.0 and every digest is the one it was; out-of-range values are
refused; the snapshot and both state hashes carry the hold's clock and the
path's forecast only while their dials are set, and a restore reproduces the
run; the clock restarts on a close whose published VIX is at or over
`fed_stress_vix` and ages a session otherwise; the forecast moves by a
meeting's rate change and decays at its half-life.
"""

import struct

import pytest

import tradefloor as tf
from tradefloor import manifest

UNIVERSE = list(tf.Universe.random(12, seed=3))
DIALS = ("fed_stress_hold", "treasury_path_pricing", "treasury_path_half_life",
         "treasury_policy_damping")
HOLD = {"fed_stress_hold": 63.0}
PATH = {"treasury_path_pricing": 1.0, "treasury_path_half_life": 63.0}


def floats(raw):
    return struct.unpack("<%dd" % (len(raw) // 8), raw)


def engine(seed=7, **dials):
    return tf.Engine(seed=seed, universe=UNIVERSE,
                     model=tf.ModelParams.from_preset("pt-v20", **dials))


# pt-v21, the default from 0.10.0, sets these; the test after this one
# holds its values.
@pytest.mark.parametrize("preset", [p for p in tf.preset_names() if p != "pt-v21"])
def test_off_on_every_shipped_preset(preset):
    values = tf.ModelParams.from_preset(preset).to_dict()
    assert all(values[name] == 0.0 for name in DIALS)


def test_pt_v21_ships_them_on():
    """pt-v21, the default from 0.10.0, ships them at the values its grade
    read."""
    d = tf.ModelParams.from_preset("pt-v21").to_dict()
    assert {n: d[n] for n in (
        "fed_stress_hold",
        "treasury_path_pricing",
        "treasury_path_half_life",
        "treasury_policy_damping",
    )} == {
        "fed_stress_hold": 42.0,
        "treasury_path_pricing": 1.0,
        "treasury_path_half_life": 63.0,
        "treasury_policy_damping": 0.5,
    }


def test_the_default_fingerprint_does_not_carry_them_at_zero():
    # Left at 0.0 they are out of the digest, so a custom vector that leaves
    # them there keeps the fingerprint it had before they existed.
    base = tf.ModelParams.from_preset("pt-v20", fed_put_gain=5.0, fed_put_half_life=126.0)
    same = tf.ModelParams.from_preset("pt-v20", fed_put_gain=5.0, fed_put_half_life=126.0,
                                      fed_stress_hold=0.0, treasury_path_pricing=0.0,
                                      treasury_path_half_life=0.0)
    assert base.fingerprint == same.fingerprint
    assert tf.ModelParams.from_preset("pt-v20", **HOLD).fingerprint != \
        tf.ModelParams.from_preset("pt-v20").fingerprint


@pytest.mark.parametrize("dials", [
    {"fed_stress_hold": -1.0},
    {"fed_stress_hold": 505.0},
    {"treasury_path_pricing": -0.1, "treasury_path_half_life": 63.0},
    {"treasury_path_pricing": 3.5, "treasury_path_half_life": 63.0},
    {"treasury_path_half_life": 600.0},
    {"treasury_policy_damping": -0.1},
    {"treasury_policy_damping": 0.95},
    # A forecast that never decays would price every change for ever.
    {"treasury_path_pricing": 1.0},
])
def test_out_of_range_is_refused(dials):
    with pytest.raises(Exception):
        tf.ModelParams.from_preset("pt-v20", **dials)


def test_the_state_is_carried_only_while_set():
    keys = {"fed_stress_hold_age", "treasury_policy_path"}
    assert not keys & set(engine().state_snapshot())
    # The half-life alone writes nothing.
    assert not keys & set(engine(treasury_path_half_life=63.0).state_snapshot())
    assert "fed_stress_hold_age" in engine(**HOLD).state_snapshot()
    assert "treasury_policy_path" in engine(**PATH).state_snapshot()
    assert "treasury_policy_path" not in engine(**HOLD).state_snapshot()


def test_the_hold_clock_restarts_on_a_stressed_close_and_ages_otherwise():
    e = engine(**HOLD)
    assert e.state_snapshot()["fed_stress_hold_age"] >= 1e9
    for day in range(3):
        e.pin_macro(vix=45.0)
        e.run_days(1, record=False, first_day=day)
    # The published VIX at the close sits at or over 30 under the pin.
    assert e.macro_fields["vix"] >= 30.0
    assert e.state_snapshot()["fed_stress_hold_age"] == 0.0
    ages = []
    for day in range(3, 13):
        e.run_days(1, record=False, first_day=day)
        ages.append((e.macro_fields["vix"], e.state_snapshot()["fed_stress_hold_age"]))
    last = 0.0
    for vix, age in ages:
        assert age == (0.0 if vix >= 30.0 else last + 1.0)
        last = age


# A stressed VIX pinned for three sessions makes the next meeting cut.
STRESS = {"fed_stress_cut": 0.25}


def test_the_forecast_moves_with_the_rate_and_decays_at_its_half_life():
    e = engine(**PATH, **STRESS)
    snap = e.state_snapshot()
    rate = snap["economy"]["federal_funds_rate"]
    path = snap["treasury_policy_path"]
    decay = 0.5 ** (1.0 / 63.0)
    moved = 0
    for day in range(120):
        if day < 3:
            e.pin_macro(vix=45.0)
        e.run_days(1, record=False, first_day=day)
        snap = e.state_snapshot()
        now_rate = snap["economy"]["federal_funds_rate"]
        expected = path * decay + (now_rate - rate)
        assert snap["treasury_policy_path"] == pytest.approx(expected, abs=1e-12)
        moved += now_rate != rate
        rate, path = now_rate, snap["treasury_policy_path"]
    assert moved > 0


def test_the_hashes_agree_and_a_restore_reproduces_the_run():
    dials = {**HOLD, **PATH, "fed_stress_cut": 0.25}
    e = engine(**dials)
    for day in range(30):
        if day < 3:
            e.pin_macro(vix=45.0)
        e.run_days(1, record=False, first_day=day)
    snap = e.state_snapshot()
    assert snap["treasury_policy_path"] != 0.0
    assert manifest.state_hash(snap) == e.state_hash()
    for name in ("fed_stress_hold_age", "treasury_policy_path"):
        moved = {**snap, name: snap[name] + 1.0}
        assert manifest.state_hash(moved) != e.state_hash(), name
    twin = engine(**dials)
    twin.restore_state(snap)
    assert twin.state_hash() == e.state_hash()
    for x in (e, twin):
        x.run_days(20, record=False, first_day=30)
    assert floats(e.prices()) == floats(twin.prices())
    assert e.state_hash() == twin.state_hash()


def test_a_snapshot_with_either_is_refused_by_a_model_without_it():
    snap = engine(**HOLD, **PATH).state_snapshot()
    with pytest.raises(Exception):
        engine().restore_state(snap)


def test_the_priced_path_moves_the_ten_year_after_a_cut():
    # The same seed with the path priced or not: the rate path is the
    # bank's, which reads no yield, so it is the same until the curve's
    # different path reaches the economy through prices; the 10-year parts
    # at the first change by the forecast's share of it.
    runs = {}
    for dials in ({}, PATH):
        e = engine(**dials, **STRESS)
        rows = []
        for day in range(90):
            if day < 3:
                e.pin_macro(vix=45.0)
            e.run_days(1, record=False, first_day=day)
            ec = e.state_snapshot()["economy"]
            rows.append((ec["federal_funds_rate"], ec["treasury_yield_10y"]))
        runs[bool(dials)] = rows
    first = next(i for i in range(1, 90) if runs[False][i][0] != runs[False][i - 1][0])
    assert runs[True][first][0] == runs[False][first][0]
    change = runs[False][first][0] - runs[False][first - 1][0]
    gap = runs[True][first][1] - runs[False][first][1]
    assert gap == pytest.approx(change, abs=0.02 + 0.05 * abs(change))


def test_the_damping_pulls_the_ten_year_toward_neutral_and_takes_no_state():
    # The 10-year's anchor reads the rate pulled toward 2.5 by the share,
    # and the daily pull closes 5 per cent of the gap: with the rate above
    # 2.5 the damped 10-year sits under the undamped one after 40 sessions.
    ten = {}
    for d in (0.0, 0.5):
        e = engine(treasury_policy_damping=d)
        assert "treasury_policy_path" not in e.state_snapshot()
        e.run_days(40, record=False)
        snap = e.state_snapshot()["economy"]
        ten[d] = (snap["federal_funds_rate"], snap["treasury_yield_10y"])
    rate = ten[0.0][0]
    gap = ten[0.0][1] - ten[0.5][1]
    assert gap * (rate - 2.5) > 0
    assert abs(gap) == pytest.approx(0.5 * abs(rate - 2.5) * (1 - 0.95 ** 40), rel=0.5)
