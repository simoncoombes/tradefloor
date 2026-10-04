"""The published VIX's stress premium (`vix_stress_premium`, `_knee`, `_cap`).

The engine's VIX state is damped against the anchor's slow memory, which the
loop needs for stability and which costs the quote its level in stress. The
premium lifts the PUBLISHED quote only: with `m` the read-back's log
deviation from the anchor's centre, kept at `vix_anchor_memory`'s rate (the
anchor's own memory in a free run, 0.0 on a pinned session),

    pi = cap * (1 - exp(-g * max(0, m - knee) / cap))
    quote = min(vix * exp(pi), vix_ceiling)

These tests hold that it is off on every preset and inert at 0.0, that the
quote is the formula and the memory the anchor's in a free run, that nothing
but the published VIX moves with it on, that a pin reads back and resets the
memory, that the snapshot and a restore carry it, and that the quote never
passes the ceiling.
"""

import math
import struct

import pytest

import tradefloor as tf
from tradefloor import manifest

UNIVERSE = list(tf.Universe.random(6, seed=3))
# The knee at 0.0, so the premium is live whenever the memory is above the
# anchor's centre, which a few hundred calm sessions reach; the proposed
# pt-v20 knee (0.8) needs a stress spell to open.
LIVE = {"vix_stress_premium": 2.0, "vix_stress_premium_knee": 0.0,
        "vix_stress_premium_cap": 0.25}


def floats(raw):
    return struct.unpack("<%dd" % (len(raw) // 8), raw)


def engine(seed=7, **dials):
    return tf.Engine(seed=seed, universe=UNIVERSE,
                     model=tf.ModelParams.from_preset("pt-v20", **dials))


def premium(memory, g, knee, cap):
    excess = memory - knee
    if not excess > 0.0:
        return 0.0
    return cap * (1.0 - math.exp(-g * excess / cap))


def table(e):
    import pyarrow as pa
    return pa.table(e.macro_table()).to_pydict()


@pytest.mark.parametrize("preset", tf.preset_names())
def test_off_on_every_shipped_preset(preset):
    d = tf.ModelParams.from_preset(preset).to_dict()
    assert d["vix_stress_premium"] == 0.0
    assert d["vix_stress_premium_knee"] == 0.0
    assert d["vix_stress_premium_cap"] == 0.0


def test_inert_at_zero_the_published_vix_is_the_state():
    e = engine()
    assert "vix_stress_memory" not in e.state_snapshot()
    for day in range(40):
        e.run_days(1, record=True, first_day=day)
        assert e.macro_fields["vix"] == e.state_snapshot()["economy"]["vix"]
        assert e.macro_state.vix == e.state_snapshot()["economy"]["vix"]
    assert "vix_stress_memory" not in e.state_snapshot()


def test_the_quote_is_the_formula_and_the_memory_is_the_anchors():
    g, knee, cap = (LIVE[k] for k in ("vix_stress_premium",
                                      "vix_stress_premium_knee",
                                      "vix_stress_premium_cap"))
    ceiling = tf.ModelParams.from_preset("pt-v20").to_dict()["vix_ceiling"]
    e = engine(**LIVE)
    live = 0
    quotes = [e.macro_fields["vix"]]
    for day in range(250):
        e.run_days(1, record=True, first_day=day)
        quotes.append(e.macro_fields["vix"])
        snap = e.state_snapshot()
        m = snap["vix_stress_memory"]
        assert m == snap["vix_anchor_slow"]
        state = snap["economy"]["vix"]
        pi = premium(m, g, knee, cap)
        want = state if pi == 0.0 else min(state * math.exp(pi), ceiling)
        assert e.macro_fields["vix"] == pytest.approx(want, rel=1e-12)
        assert e.macro_fields["vix"] >= state
        live += pi > 0.0
    assert live > 20
    # The macro table records a session's row before that session's macro
    # step, so it publishes the quote as the previous close left it.
    assert table(e)["vix"] == quotes[:-1]


def test_nothing_but_the_published_vix_moves():
    # 500 sessions: every price, every draw, every other macro series and
    # the whole state bar the memory are the premium-off run's, bit for bit.
    runs = []
    for dials in ({}, LIVE):
        e = engine(seed=11, **dials)
        e.run_days(500, record=True)
        runs.append(e)
    off, on = runs
    assert floats(off.prices()) == floats(on.prices())
    assert off.draws_consumed == on.draws_consumed
    a, b = table(off), table(on)
    for column in a:
        if column != "vix":
            assert a[column] == b[column], column
    lifted = sum(y > x for x, y in zip(a["vix"], b["vix"]))
    assert all(y >= x for x, y in zip(a["vix"], b["vix"]))
    assert lifted > 50
    sa, sb = off.state_snapshot(), on.state_snapshot()
    sb.pop("vix_stress_memory")
    sa.pop("model_fingerprint"), sb.pop("model_fingerprint")
    # By repr: the generator's raw words read as floats include NaNs.
    assert repr(sa) == repr(sb)


def test_a_pin_reads_back_and_resets_the_memory():
    e = engine(**LIVE)
    day = 0
    while e.state_snapshot()["vix_stress_memory"] <= 0.05:
        e.run_days(1, record=False, first_day=day)
        day += 1
        assert day < 500
    assert e.macro_fields["vix"] > e.state_snapshot()["economy"]["vix"]
    e.pin_macro(vix=45.0)
    assert e.macro_fields["vix"] == 45.0
    assert e.state_snapshot()["vix_stress_memory"] == 0.0
    e.run_days(1, record=True, first_day=day)
    snap = e.state_snapshot()
    assert snap["vix_stress_memory"] == 0.0
    assert e.macro_fields["vix"] == snap["economy"]["vix"]
    # The pinned session's row, recorded before its macro step, is the pin.
    assert table(e)["vix"][-1] == 45.0
    # Free again, it rebuilds from 0.0 at the anchor memory's rate.
    e.run_days(1, record=False, first_day=day + 1)
    assert e.state_snapshot()["vix_stress_memory"] != 0.0


def test_the_snapshot_and_a_restore_carry_the_memory():
    e = engine(**LIVE)
    e.run_days(120, record=False)
    snap = e.state_snapshot()
    assert snap["vix_stress_memory"] != 0.0
    assert manifest.state_hash(snap) == e.state_hash()
    moved = dict(snap, vix_stress_memory=snap["vix_stress_memory"] + 0.125)
    assert manifest.state_hash(moved) != e.state_hash()
    twin = engine(**LIVE)
    twin.restore_state(snap)
    assert twin.state_hash() == e.state_hash()
    assert twin.macro_fields["vix"] == e.macro_fields["vix"]
    for x in (e, twin):
        x.run_days(30, record=False, first_day=120)
    assert twin.state_hash() == e.state_hash()
    assert twin.macro_fields["vix"] == e.macro_fields["vix"]


def test_the_quote_never_passes_the_ceiling():
    dials = dict(LIVE, vix_stress_premium_cap=1.0)
    ceiling = tf.ModelParams.from_preset("pt-v20", **dials).to_dict()["vix_ceiling"]
    e = engine(**dials)
    e.run_days(5, record=False)
    e.pin_macro(vix=150.0)
    # A memory deep in stress, written through a restore: the premium is
    # all but the cap, 150 * e is past the ceiling, and the quote stops there.
    snap = dict(e.state_snapshot(), vix_stress_memory=2.0)
    e.restore_state(snap)
    assert e.state_snapshot()["economy"]["vix"] == 150.0
    assert e.macro_fields["vix"] == ceiling
    for day in range(5, 25):
        e.run_days(1, record=True, first_day=day)
        assert e.macro_fields["vix"] <= ceiling
    assert max(table(e)["vix"]) <= ceiling


@pytest.mark.parametrize("dials", [
    {"vix_stress_premium": -0.1, "vix_stress_premium_cap": 0.25},
    {"vix_stress_premium": 11.0, "vix_stress_premium_cap": 0.25},
    {"vix_stress_premium": 1.0},  # no cap
    {"vix_stress_premium": 1.0, "vix_stress_premium_cap": 1.5},
    {"vix_stress_premium": 1.0, "vix_stress_premium_cap": 0.25,
     "vix_stress_premium_knee": -0.1},
    {"vix_stress_premium": 1.0, "vix_stress_premium_cap": 0.25,
     "vix_stress_premium_knee": float("nan")},
])
def test_the_ranges(dials):
    with pytest.raises(Exception):
        tf.ModelParams.from_preset("pt-v20", **dials)


def test_refused_without_the_identity_and_the_anchor_memory():
    # pt-v18 runs neither the identity's read-back nor the anchor memory.
    with pytest.raises(Exception):
        tf.ModelParams.from_preset("pt-v18", vix_stress_premium=1.0,
                                   vix_stress_premium_cap=0.25)
    # The knee and the cap alone are unread, and accepted.
    tf.ModelParams.from_preset("pt-v18", vix_stress_premium_knee=0.5,
                               vix_stress_premium_cap=0.25)


def test_an_intervention_reads_the_state_not_the_quote():
    # `interventions` reads what `pin_macro` writes: a `multiply` of the VIX
    # scales the state, not the quote, which carries the premium on top.
    from tradefloor.interventions import true_macro_fields, true_macro_value
    e = engine(**LIVE)
    day = 0
    while e.macro_fields["vix"] <= e.state_snapshot()["economy"]["vix"]:
        e.run_days(1, record=False, first_day=day)
        day += 1
        assert day < 500
    state = e.state_snapshot()["economy"]["vix"]
    assert true_macro_value(e, "vix") == state
    assert true_macro_fields(e)["vix"] == state
    assert e.macro_fields["vix"] > state
