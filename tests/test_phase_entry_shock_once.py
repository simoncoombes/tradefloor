"""The growth shock on entering a phase, fired once (`phase_entry_shock_once`).

Every preset ships it at 0.0, which is left out of the digest, and every
known-answer digest is the one it was (tests/test_known_answer.py). Off, the
step's gate holds at a phase's age 0.0 and again at exactly one increment, so
the shock (and its uniform) fires on the phase's first two sessions; that
legacy behaviour is pinned here and, with the draw counts, beside the code
(`economy::daily::phase_entry_gate`). On, it fires on the first session
alone.
"""

import struct

import pytest

import tradefloor as tf

UNIVERSE = list(tf.Universe.random(3, seed=3))
PRESET = "pt-v20"
#: The fixed entry shocks (`economy::daily::phase_entry_growth_shock`); a
#: contraction's is drawn and a peak's is zero, so they are not read here.
FIXED = {"trough": -0.5, "recovery": 1.0, "expansion": 0.5}
#: pt-v20's macro month in sessions; growth's monthly and quarterly steps
#: run on a game day that is a multiple of it.
MONTH = 21


def floats(raw):
    return struct.unpack("<%dd" % (len(raw) // 8), raw)


def engine(seed=11, **dials):
    return tf.Engine(seed=seed, universe=UNIVERSE,
                     model=tf.ModelParams.from_preset(PRESET, **dials))


@pytest.mark.parametrize("preset", tf.preset_names())
def test_off_on_every_shipped_preset(preset):
    assert tf.ModelParams.from_preset(preset).to_dict()["phase_entry_shock_once"] == 0.0


@pytest.mark.parametrize("value", [-1.0, 0.5, 2.0])
def test_a_switch_takes_two_values(value):
    with pytest.raises(tf.ValidationError, match="phase_entry_shock_once"):
        tf.ModelParams.from_preset(PRESET, phase_entry_shock_once=value)


def test_on_is_a_different_model_and_zero_is_the_preset():
    on = tf.ModelParams.from_preset(PRESET, phase_entry_shock_once=1.0)
    assert on.fingerprint.startswith("custom-")
    assert tf.ModelParams.from_preset(PRESET, phase_entry_shock_once=0.0).fingerprint == PRESET
    assert "phase_entry_shock_once" in tf.ModelParams.digest_silent_at_zero()


def test_at_zero_it_is_the_presets_own_run():
    a, b = engine(), engine(phase_entry_shock_once=0.0)
    for x in (a, b):
        x.run_days(40, record=False)
    assert floats(a.prices()) == floats(b.prices())
    assert a.state_hash() == b.state_hash()
    assert a.draws_consumed == b.draws_consumed


def _entries(once, days=2500):
    """For each entry into a phase with a fixed shock, growth's change on
    the three sessions after the transition, skipping a session whose step
    also runs growth's monthly or quarterly update."""
    e = engine(phase_entry_shock_once=once)
    rows = []
    prev = e.state_snapshot()["economy"]
    for day in range(days):
        e.run_days(1, record=False, first_day=day)
        now = e.state_snapshot()["economy"]
        rows.append((day, prev["cycle_phase"], now["cycle_phase"],
                     now["gdp_growth"] - prev["gdp_growth"]))
        prev = now
    out = []
    for i, (_, before, after, _) in enumerate(rows[:-3]):
        if before == after or after not in FIXED:
            continue
        window = rows[i + 1:i + 4]
        assert all(r[1] == r[2] == after for r in window), "a phase shorter than three sessions"
        out.append((after, [None if (r[0] + 1) % MONTH == 0 else r[3] for r in window]))
    return out, e


@pytest.mark.parametrize("once,sessions", [(0.0, 2), (1.0, 1)])
def test_the_shock_fires_on_the_first_sessions_of_a_phase(once, sessions):
    """Off, the first TWO sessions of a phase each take the shock whole;
    on, the first alone. Every other session's growth stands still off a
    release day, so the third session is the control."""
    entries, _ = _entries(once)
    assert len(entries) >= 2, entries
    for phase, moves in entries:
        for k, move in enumerate(moves):
            if move is None:
                continue
            want = FIXED[phase] if k < sessions else 0.0
            assert move == pytest.approx(want, abs=1e-12), (phase, k + 1, moves)


def test_on_takes_fewer_economy_draws_and_none_from_the_market():
    """At seed 42 the burn-in lives through phase entries, and each takes its
    uniform once instead of twice, which is the switch's own mechanism (and
    moves which state-dependent sites fire after it): measured, 9041 economy
    draws at construction against 9137. The market stream's count is the
    preset's, session for session."""
    base, live = engine(seed=42), engine(seed=42, phase_entry_shock_once=1.0)
    assert dict(live.draws_by_stream())["economy"] < dict(base.draws_by_stream())["economy"]
    for day in range(30):
        for x in (base, live):
            x.run_days(1, record=False, first_day=day)
        assert dict(base.draws_by_stream())["market"] == dict(live.draws_by_stream())["market"]


def test_a_restore_reproduces_the_run():
    """No state of its own: a snapshot taken mid-run and restored into a
    fresh engine with the switch on continues bit for bit."""
    e = engine(seed=42, phase_entry_shock_once=1.0)
    e.run_days(6, record=False)
    snap = e.state_snapshot()
    twin = engine(seed=42, phase_entry_shock_once=1.0)
    twin.restore_state(snap)
    assert twin.state_hash() == e.state_hash()
    for x in (e, twin):
        x.run_days(30, record=False, first_day=6)
    assert floats(e.prices()) == floats(twin.prices())
    assert e.state_hash() == twin.state_hash()
