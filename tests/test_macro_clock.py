"""The macro clock (r13 audit): the anticipation's left-out drift
(`earnings_anticipation_drift_share`, `earnings_anticipation_drift_half_life`),
the risk-management cut (`fed_growth_cut`) and the drawn publication lag
(`cycle_publication_lag_draw`).

On pt-v20 as graded the published phase is a timing signal: the anticipated
earnings level carries a drift the true phase predicts, the first cut of an
easing marks the trough, and every turn is published exactly 252 sessions
late, so a lever-while-published-contraction rule beat holding in 0.97 of
90 histories. These dials take each of those away. The corporate spread's
blend (`corporate_spread_cycle`) is the fourth, merged from the phase
re-anchor and held by tests/test_cycle_nowcast.py.

These tests hold that each dial is off on every preset and inert at its
default, that the engine refuses values outside the domains, that each moves
what it is meant to move, and that the state the draw and the drift keep is
carried in the snapshot and both state hashes only while set. The recursions
themselves are held by the engine's unit tests
(`the_anticipation_drift_is_left_out_only_under_the_share`,
`the_drawn_publication_schedule_publishes_each_turn_in_order`) and the
branch's order by `the_growth_cut_fires_under_its_dial_and_before_lift_off`.
"""

import struct

import pytest

import tradefloor as tf
from tradefloor import manifest

UNIVERSE = list(tf.Universe.random(3, seed=3))
DIALS = ("earnings_anticipation_drift_share",
         "earnings_anticipation_drift_half_life",
         "fed_growth_cut", "cycle_publication_lag_draw")
DRIFT = {"earnings_anticipation_drift_share": 1.0,
         "earnings_anticipation_drift_half_life": 252.0}
DRAW = {"cycle_publication_lag_draw": 1.0}
# Seed 7 on this roster at 26 ticks a session turns four times in 480
# sessions (tests/test_cycle_nowcast.py), and the schedule publishes all of
# them within 480 + 441.
SEED, TICKS = 7, 26
PHASES = ("expansion", "peak", "contraction", "trough", "recovery")


def floats(raw):
    return struct.unpack("<%dd" % (len(raw) // 8), raw)


def engine(seed=SEED, **dials):
    return tf.Engine(seed=seed, universe=UNIVERSE,
                     model=tf.ModelParams.from_preset("pt-v20", **dials))


# pt-v21, the default from 0.10.0, sets these; the test after this one
# holds its values.
@pytest.mark.parametrize("preset", [p for p in tf.preset_names() if p != "pt-v21"])
def test_off_on_every_shipped_preset(preset):
    d = tf.ModelParams.from_preset(preset).to_dict()
    for name in DIALS:
        assert d[name] == 0.0, name


def test_pt_v21_ships_them_on():
    """pt-v21, the default from 0.10.0, ships them at the values its grade
    read."""
    d = tf.ModelParams.from_preset("pt-v21").to_dict()
    assert {n: d[n] for n in (
        "earnings_anticipation_drift_share",
        "earnings_anticipation_drift_half_life",
        "fed_growth_cut",
        "cycle_publication_lag_draw",
    )} == {
        "earnings_anticipation_drift_share": 0.9,
        "earnings_anticipation_drift_half_life": 252.0,
        "fed_growth_cut": 2.0,
        "cycle_publication_lag_draw": 1.0,
    }


def test_at_zero_nothing_moves_and_nothing_is_carried():
    a = engine()
    b = engine(**{name: 0.0 for name in DIALS})
    assert b.model.fingerprint == "pt-v20"
    for e in (a, b):
        e.run_days(20, record=False)
    assert floats(a.prices()) == floats(b.prices())
    assert a.state_hash() == b.state_hash()
    econ = b.state_snapshot()["economy"]
    for key in ("anticipation_drift", "anticipation_raw", "cycle_publication"):
        assert key not in econ
    # The fixed lag's history is kept as it was.
    assert len(econ["cycle_history"]) == 253


@pytest.mark.parametrize("name,value", [
    ("earnings_anticipation_drift_share", -0.1),
    ("earnings_anticipation_drift_share", 1.1),
    ("earnings_anticipation_drift_half_life", -1.0),
    ("earnings_anticipation_drift_half_life", 5041.0),
    ("fed_growth_cut", -5.1),
    ("fed_growth_cut", 5.1),
    ("cycle_publication_lag_draw", 0.5),
    ("cycle_publication_lag_draw", 2.0),
])
def test_the_domains(name, value):
    with pytest.raises(Exception):
        tf.ModelParams.from_preset("pt-v20", **{name: value})


def test_the_drift_moves_the_market():
    base = engine()
    base.run_days(20, record=False)
    e = engine(**DRIFT)
    e.run_days(20, record=False)
    assert floats(e.prices()) != floats(base.prices())


def rate_path(sessions, **dials):
    """Per close: the policy rate and true growth, and the prices at the end."""
    e = engine(**dials)
    rows = []
    for day in range(sessions):
        e.run_days(1, ticks_per_day=TICKS, record=False, first_day=day)
        econ = e.state_snapshot()["economy"]
        rows.append((econ["federal_funds_rate"], econ["gdp_growth"]))
    return rows, floats(e.prices())


def test_the_growth_cut_comes_first():
    # Seed 7: the ladder's first cut is at session 254, when unemployment
    # has risen; with the dial at 2.0 the bank cuts at the first meeting
    # growth is under 2 per cent, 131 sessions earlier, and every cut after
    # it is a quarter point.
    ctl, ctl_prices = rate_path(300)
    arm, arm_prices = rate_path(300, fed_growth_cut=2.0)
    first = lambda rows: next(d for d in range(1, len(rows)) if rows[d][0] < rows[d - 1][0])
    assert first(ctl) - first(arm) >= 60, (first(ctl), first(arm))
    d = first(arm)
    assert abs(arm[d - 1][0] - arm[d][0] - 0.25) < 1e-9
    assert arm[d - 1][1] < 2.0
    assert ctl_prices != arm_prices


def test_the_drift_leaves_the_anticipation_as_raw_less_d():
    e = engine(**DRIFT)
    econ = e.state_snapshot()["economy"]
    # Zeroed after the burn-in: the opening prices A - e.
    assert econ["anticipation_drift"] == 0.0
    assert e.earnings_anticipation == econ["anticipation_raw"]
    e.run_days(30, ticks_per_day=TICKS, record=False)
    econ = e.state_snapshot()["economy"]
    assert econ["anticipation_drift"] != 0.0
    assert e.earnings_anticipation == econ["anticipation_raw"] - econ["anticipation_drift"]


def published_path(sessions, **dials):
    """Per close: the true phase and the phase as published."""
    e = engine(**dials)
    rows = []
    for day in range(sessions):
        e.run_days(1, ticks_per_day=TICKS, record=False, first_day=day)
        rows.append((e.state_snapshot()["economy"]["cycle_phase"],
                     e.macro_fields["cycle"]))
    return rows


def test_the_drawn_lag_publishes_each_turn_late_and_in_order():
    fixed = published_path(900)
    drawn = published_path(900, **DRAW)
    # The true path is the fixed lag's: the schedule moves no draw, and on
    # pt-v20 nothing the economy reads comes from the published phase.
    assert [t for t, _ in fixed] == [t for t, _ in drawn]
    turns = [(c, drawn[c][0]) for c in range(1, len(drawn))
             if drawn[c][0] != drawn[c - 1][0]]
    assert len(turns) >= 4
    # The fixed lag publishes each turn exactly 252 closes after it.
    for c, (_, pub) in enumerate(fixed):
        if c >= 252:
            assert pub == fixed[c - 252][0]
    # The drawn lag: each announcement is of a turn made at least 84 closes
    # before, the announcements follow the turns' order, and they do not
    # all sit 252 closes late.
    announced = [(c, drawn[c][1]) for c in range(1, len(drawn))
                 if drawn[c][1] != drawn[c - 1][1]]
    assert len(announced) >= 3
    lags, k = [], 0
    for c, phase in announced:
        while k < len(turns) and turns[k][1] != phase:
            k += 1
        assert k < len(turns), (c, phase, turns)
        tau = turns[k][0]
        assert 84 <= c - tau, (c, tau, phase)
        lags.append(c - tau)
        k += 1
    assert any(lag != 252 for lag in lags), lags


def test_the_drawn_lag_is_a_function_of_the_seed():
    a = published_path(700, **DRAW)
    b = published_path(700, **DRAW)
    assert a == b


@pytest.mark.parametrize("dials,keys", [
    (DRIFT, ("anticipation_drift", "anticipation_raw")),
    (DRAW, ("cycle_publication",)),
    ({**DRIFT, **DRAW, "corporate_spread_cycle": 1.0, "fed_growth_cut": 2.0},
     ("anticipation_drift", "anticipation_raw", "cycle_publication")),
])
def test_the_state_is_carried_and_hashed_only_while_set(dials, keys):
    e = engine(**dials)
    e.run_days(300, ticks_per_day=TICKS, record=False)
    snap = e.state_snapshot()
    for key in keys:
        assert key in snap["economy"], key
    if "cycle_publication" in keys:
        pub = snap["economy"]["cycle_publication"]
        assert "cycle_history" not in snap["economy"]
        assert pub["closes"] == 300
        assert pub["pending_closes"] == sorted(pub["pending_closes"])
    # The Python twin of the state hash covers it, as the engine's does,
    # and a JSON round trip keeps it.
    assert manifest.state_hash(snap) == e.state_hash()
    back = manifest._snapshot_from_json(manifest._snapshot_to_json(snap))
    assert manifest.state_hash(back) == e.state_hash()
    edited = e.state_snapshot()
    if "anticipation_drift" in keys:
        edited["economy"]["anticipation_drift"] += 0.01
    else:
        edited["economy"]["cycle_publication"]["turns"] += 1
    assert manifest.state_hash(edited) != manifest.state_hash(snap)

    # A restore reproduces the market and the state.
    twin = engine(**dials)
    twin.restore_state(snap)
    assert twin.state_hash() == e.state_hash()
    for x in (e, twin):
        x.run_days(5, ticks_per_day=TICKS, record=False, first_day=300)
    assert floats(e.prices()) == floats(twin.prices())
    assert twin.state_hash() == e.state_hash()

    # Refused where the dial is off (the fingerprint dropped, so the
    # engine's own refusal is what is read).
    bare = dict(snap)
    bare.pop("model_fingerprint", None)
    with pytest.raises(Exception):
        engine().restore_state(bare)


@pytest.mark.parametrize("key, dial", [
    ("anticipation_drift", "earnings_anticipation_drift_share"),
    ("anticipation_raw", "earnings_anticipation_drift_share"),
    ("cycle_publication", "cycle_publication_lag_draw")])
def test_a_restore_without_the_state_is_refused_by_name(key, dial):
    # The versioned state contract: each key is required while its dial is
    # set, and the refusal names the dial.
    e = engine(**DRIFT, **DRAW)
    e.run_days(30, ticks_per_day=TICKS, record=False)
    snap = e.state_snapshot()
    del snap["economy"][key]
    with pytest.raises(tf.ValidationError, match=dial):
        engine(**DRIFT, **DRAW).restore_state(snap)


def test_one_of_the_drift_keys_alone_is_refused():
    e = engine(**DRIFT, **DRAW)
    e.run_days(30, ticks_per_day=TICKS, record=False)
    # One of the drift's two keys alone is refused.
    half = e.state_snapshot()
    del half["economy"]["anticipation_raw"]
    with pytest.raises(Exception):
        engine(**DRIFT, **DRAW).restore_state(half)
