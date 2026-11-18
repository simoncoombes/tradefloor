"""The cycle's daily transition probability read as the one that compounds
to the monthly hazard (`cycle_hazard_compound`).

Every preset ships it at 0.0, which is left out of the digest, and every
known-answer digest is the one it was (tests/test_known_answer.py). Off, the
daily probability is the monthly hazard over the month's sessions, `h / m`;
on, it is `1 - (1 - h)^(1/m)`, so the month's sessions survived at it survive
the month with probability `1 - h`. The arithmetic is held to the bit beside
the code (`economy::cycle::compound_hazard`); these hold the dial's surface
and its engine: refused off its two values and without the monthly clock, at
0.0 the preset's own run, on a different cycle with the market stream's draws
unmoved, and a long run that cycles without failing.
"""

import struct

import pytest

import tradefloor as tf

UNIVERSE = list(tf.Universe.random(3, seed=3))
PRESET = "pt-v20"


def floats(raw):
    return struct.unpack("<%dd" % (len(raw) // 8), raw)


def engine(seed=7, preset=PRESET, **dials):
    return tf.Engine(seed=seed, universe=UNIVERSE,
                     model=tf.ModelParams.from_preset(preset, **dials))


@pytest.mark.parametrize("preset", tf.preset_names())
def test_off_on_every_shipped_preset(preset):
    assert tf.ModelParams.from_preset(preset).to_dict()["cycle_hazard_compound"] == 0.0


@pytest.mark.parametrize("value", [-1.0, 0.5, 2.0])
def test_a_switch_takes_two_values(value):
    with pytest.raises(tf.ValidationError, match="cycle_hazard_compound"):
        tf.ModelParams.from_preset(PRESET, cycle_hazard_compound=value)


@pytest.mark.parametrize("clock", [0.0, 0.5])
def test_refused_off_the_monthly_clock(clock):
    with pytest.raises(tf.ValidationError, match="cycle_hazard_per_month"):
        tf.ModelParams.from_preset(PRESET, cycle_hazard_compound=1.0,
                                   cycle_hazard_per_month=clock)


def test_on_is_a_different_model_and_zero_is_the_preset():
    on = tf.ModelParams.from_preset(PRESET, cycle_hazard_compound=1.0)
    assert on.fingerprint.startswith("custom-")
    assert tf.ModelParams.from_preset(PRESET, cycle_hazard_compound=0.0).fingerprint == PRESET
    assert "cycle_hazard_compound" in tf.ModelParams.digest_silent_at_zero()


def test_at_zero_it_is_the_presets_own_run():
    a, b = engine(), engine(cycle_hazard_compound=0.0)
    for x in (a, b):
        x.run_days(40, record=False)
    assert floats(a.prices()) == floats(b.prices())
    assert a.state_hash() == b.state_hash()
    assert a.draws_consumed == b.draws_consumed


def test_on_moves_the_cycle_and_not_the_market_stream():
    """The dial takes no draw: what moves is when phases end, and so the
    economy stream's state-dependent sites. The market stream's count is
    the preset's, session for session."""
    base, live = engine(), engine(cycle_hazard_compound=1.0)
    for day in range(30):
        for x in (base, live):
            x.run_days(1, record=False, first_day=day)
        assert dict(base.draws_by_stream())["market"] == dict(live.draws_by_stream())["market"]
    assert floats(base.prices()) != floats(live.prices())


def _phase_path(e, days):
    path = []
    for day in range(days):
        e.run_days(1, record=False, first_day=day)
        path.append(e.state_snapshot()["economy"]["cycle_phase"])
    return path


def _sojourns(path):
    runs, start = [], 0
    for t in range(1, len(path)):
        if path[t] != path[t - 1]:
            if start:
                runs.append(t - start)
            start = t
    return runs


ORDER = ["expansion", "peak", "contraction", "trough", "recovery"]


def _in_order(path):
    for a, b in zip(path, path[1:]):
        if a != b:
            assert ORDER.index(b) == (ORDER.index(a) + 1) % 5, (a, b)


def test_a_long_run_on_the_default_cycles_without_failing():
    """Two thousand sessions of the default preset with the switch on:
    no failure, and every phase change is the next phase in order."""
    path = _phase_path(engine(seed=5, preset=tf.model_preset()["name"],
                              cycle_hazard_compound=1.0), 2000)
    _in_order(path)


def test_its_phases_end_sooner():
    """Three thousand sessions on each of three pt-v20 seeds, both
    settings. The compound probability exceeds `h / m` at every hazard
    inside (0, 1), so the completed sojourns are shorter on average on the
    compound law (measured: 160 sessions over 35 sojourns against 174 over
    34). It is a statistic over a few dozen sojourns, so it is asserted
    loosely."""
    means = {}
    for compound in (0.0, 1.0):
        runs = []
        for seed in (9, 13, 17):
            path = _phase_path(engine(seed=seed, cycle_hazard_compound=compound), 3000)
            _in_order(path)
            runs += _sojourns(path)
        assert len(runs) >= 20, runs
        means[compound] = sum(runs) / len(runs)
    assert means[1.0] <= means[0.0] * 1.05, means


def test_a_restore_reproduces_the_run():
    """No state of its own: a snapshot taken mid-run and restored into a
    fresh engine with the switch on continues bit for bit."""
    e = engine(seed=42, cycle_hazard_compound=1.0)
    e.run_days(6, record=False)
    snap = e.state_snapshot()
    twin = engine(seed=42, cycle_hazard_compound=1.0)
    twin.restore_state(snap)
    assert twin.state_hash() == e.state_hash()
    for x in (e, twin):
        x.run_days(30, record=False, first_day=6)
    assert floats(e.prices()) == floats(twin.prices())
    assert e.state_hash() == twin.state_hash()
