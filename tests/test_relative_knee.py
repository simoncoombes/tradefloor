"""The knee under a name's fair-value level (`fair_value_relative_knee`,
`fair_value_relative_half_life`).

At the close, a public, solvent, traded name whose fair-value level `v` sits
more than the knee below the roster's equal-weighted mean `v` is pulled
toward the knee: `v += (1 - 0.5 ** (1 / h)) * (mean - knee - v)`. No other
name moves, nothing is drawn, and no state is added. These tests hold that it
is off on every shipped preset, that the half-life is required with the knee,
that a run where no name reaches the knee is the knee-less run to the bit,
and that a name put past the knee is pulled by the formula at the close and
by nothing else.
"""

import struct

import pytest

import tradefloor as tf

UNIVERSE = list(tf.Universe.random(12, seed=3))


def floats(raw):
    return list(struct.unpack("<%dd" % (len(raw) // 8), raw))


def model(**extra):
    return tf.ModelParams.from_preset("pt-v20", **extra)


# pt-v21, the default from 0.10.0, sets these; the test after this one
# holds its values.
@pytest.mark.parametrize("preset", [p for p in tf.preset_names() if p != "pt-v21"])
def test_off_on_every_shipped_preset(preset):
    d = tf.ModelParams.from_preset(preset).to_dict()
    assert d["fair_value_relative_knee"] == 0.0
    assert d["fair_value_relative_half_life"] == 0.0


def test_pt_v21_ships_them_on():
    """pt-v21, the default from 0.10.0, ships them at the values its grade
    read."""
    d = tf.ModelParams.from_preset("pt-v21").to_dict()
    assert {n: d[n] for n in (
        "fair_value_relative_knee",
        "fair_value_relative_half_life",
    )} == {
        "fair_value_relative_knee": 4.0,
        "fair_value_relative_half_life": 63.0,
    }


@pytest.mark.parametrize("extra", [
    {"fair_value_relative_knee": 3.0},
    {"fair_value_relative_knee": -1.0, "fair_value_relative_half_life": 252.0},
    {"fair_value_relative_knee": 21.0, "fair_value_relative_half_life": 252.0},
    {"fair_value_relative_knee": 3.0, "fair_value_relative_half_life": -1.0},
    {"fair_value_relative_knee": 3.0, "fair_value_relative_half_life": 25201.0},
])
def test_refused_out_of_domain_and_without_its_half_life(extra):
    with pytest.raises(Exception):
        model(**extra)


def test_a_run_no_name_reaches_is_the_kneeless_run_to_the_bit():
    # Twenty sessions open no name anywhere near five log units below the
    # mean, so the close never pulls: the prices and the state are the ones
    # the knee-less build gives (the state hash carries the fingerprint, so
    # it is compared through the levels and the prices instead).
    runs = []
    for extra in ({}, {"fair_value_relative_knee": 5.0, "fair_value_relative_half_life": 252.0}):
        e = tf.Engine(seed=11, universe=UNIVERSE, model=model(**extra))
        e.run_days(20, record=False)
        snap = e.state_snapshot()
        runs.append((floats(e.prices()), floats(snap["fair_value_offset"])))
    assert runs[0] == runs[1]


def _pushed(knee, half_life, depth=6.0):
    """An engine one session in, with name 0's level put `depth` below
    everyone's, then one more session. Returns the levels after it."""
    extra = {} if knee == 0.0 else {"fair_value_relative_knee": knee,
                                    "fair_value_relative_half_life": half_life}
    e = tf.Engine(seed=11, universe=UNIVERSE, model=model(**extra))
    e.run_days(1, record=False)
    snap = e.state_snapshot()
    v = floats(snap["fair_value_offset"])
    v[0] -= depth
    snap["fair_value_offset"] = struct.pack("<%dd" % len(v), *v)
    e.restore_state(snap)
    e.run_days(1, record=False, first_day=1)
    return floats(e.state_snapshot()["fair_value_offset"])


def test_a_name_past_the_knee_is_pulled_by_the_formula_at_the_close():
    off = _pushed(0.0, 0.0)
    on = _pushed(3.0, 10.0)
    # Every other name's level is the knee-less run's.
    assert on[1:] == off[1:]
    # The session's news reached name 0 the same way in both; the close
    # then pulled it toward the knee under the mean of the levels it saw.
    mean = sum(off) / len(off)
    pull = 1.0 - 0.5 ** (1.0 / 10.0)
    assert off[0] < mean - 3.0
    assert on[0] == pytest.approx(off[0] + pull * (mean - 3.0 - off[0]), abs=1e-12)


def test_the_pull_halves_the_gap_in_a_half_life():
    # With nothing else moving the level much over a few sessions, the gap
    # below the knee closes by about half in `h` sessions.
    extra = {"fair_value_relative_knee": 2.0, "fair_value_relative_half_life": 5.0}
    e = tf.Engine(seed=11, universe=UNIVERSE, model=model(**extra))
    e.run_days(1, record=False)
    snap = e.state_snapshot()
    v = floats(snap["fair_value_offset"])
    v[0] -= 5.0
    snap["fair_value_offset"] = struct.pack("<%dd" % len(v), *v)
    e.restore_state(snap)

    def gap():
        w = floats(e.state_snapshot()["fair_value_offset"])
        return (sum(w) / len(w) - 2.0) - w[0]

    before = gap()
    e.run_days(5, record=False, first_day=1)
    assert gap() == pytest.approx(before / 2.0, rel=0.1)
