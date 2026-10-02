"""Every settable dial, moved off pt-v20, must survive a snapshot and restore.

A dial can switch on state that the snapshot does not carry. The 0.8.5
audit swept 109 dials by hand and found one, `garch_cascade_components`: at
3.0 the restored market was 1.4 per cent in log price off the original
twenty days after the cut, with the hashes equal at the cut. This test is
that sweep kept, so the next dial that adds state fails here rather than in
a user's resumed run.

For each dial it takes the first value `ModelParams.from_preset` accepts from
a short list of candidates, runs a small market to a day boundary and to the
middle of a session, restores a fresh engine from the snapshot, runs both on,
and requires the same state hash.

Slow, about six minutes on two workers (212 dials pass and 30 take no
candidate value), so it runs under TRADEFLOOR_SLOW_TESTS=1 as
the release check does.
"""

import os

import pytest

import tradefloor as tf

SLOW = pytest.mark.skipif(
    not (os.environ.get("TRADEFLOOR_SLOW_TESTS")
         or os.environ.get("PRETIUM_SLOW_TESTS")),
    reason="the dial sweep is slow; set TRADEFLOOR_SLOW_TESTS=1 to run",
)

UNIVERSE = tf.Universe.random(6, seed=111)
BASE = tf.ModelParams.from_preset("pt-v20").to_dict()
DIALS = sorted(name for name, value in BASE.items()
               if isinstance(value, (int, float)) and not isinstance(value, bool))


def _moved(name):
    value = BASE[name]
    candidates = ([1.0, 0.5, 2.0, 0.1, 3.0, 10.0] if not value else
                  [value * 1.5, value * 0.5, value * 1.1, value * 0.9,
                   value + 1.0, 0.0])
    for candidate in candidates:
        try:
            return tf.ModelParams.from_preset("pt-v20", **{name: candidate})
        except (tf.ValidationError, ValueError, TypeError):
            continue
    return None


@SLOW
@pytest.mark.parametrize("name", DIALS)
def test_a_moved_dial_restores_to_the_same_market(name):
    model = _moved(name)
    if model is None:
        pytest.skip(f"no candidate value for {name} is accepted")
    for mid_session in (False, True):
        original = tf.Engine(seed=4, universe=UNIVERSE, model=model)
        original.run_days(15, record=False)
        if mid_session:
            original.open_market()
            original.run_session(9, 30, 3, 120)
        restored = tf.Engine(seed=4, universe=UNIVERSE, model=model)
        restored.restore_state(original.state_snapshot())
        assert restored.state_hash() == original.state_hash()
        for engine in (original, restored):
            if mid_session:
                engine.run_session(11, 30, 3, 270)
                engine.close_market()
            engine.run_days(8, record=False)
        where = "mid-session" if mid_session else "at a day boundary"
        assert restored.state_hash() == original.state_hash(), (
            f"{name} moved off pt-v20: a restore {where} diverged from the "
            f"original within eight days, so the dial drives state the "
            f"snapshot does not carry")
