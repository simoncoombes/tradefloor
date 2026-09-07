"""`Engine.market_variance_target()` — the targets the last close read.

The getter reports `(fast, slow)` and the SLOW element is `None` whenever
the preset has no slow component. That is the whole design question §4.1
left open, and it was ruled rather than guessed: `factor_vol.rs`'s
`close_day_at` computes the fast target unconditionally before its early
return at `market_vol_slow_weight == 0.0`, and computes the slow target
only after it. So on the single-component branch there is no slow target
in existence to report.

The rejected alternative was `(target, target)`, which would have been
correct on every preset that ships today — because every preset with
`market_vol_slow_weight` 0.0 also ships `market_vol_slow_vix_damp` 0.0,
at which the slow target evaluates to the fast one. **That is a
coincidence of the current presets, not an invariant**, and
`test_the_none_is_structural_not_a_coincidence_of_shipped_presets` below
is the test that says so: it builds the preset that breaks the
coincidence and shows the two targets would differ.
"""
from __future__ import annotations

import math

import pytest

import tradefloor as tf


def _run(model, days=3, size=8, seed=5):
    e = tf.Engine(seed=seed, universe=tf.Universe.random(size, seed=11), model=model)
    for _ in range(days):
        e.open_market()
        e.run_session(9, 30, 3, 390)
        e.close_market()
    return e


def test_absent_before_any_close():
    e = tf.Engine(seed=1, universe=tf.Universe.random(4, seed=11))
    assert e.market_variance_target() is None


def test_two_components_report_both_targets():
    d = tf.ModelParams.from_preset("pt-v16").to_dict()
    assert d["market_vol_slow_weight"] != 0.0, "pt-v16 is the two-component case"
    got = _run(tf.ModelParams.from_preset("pt-v16")).market_variance_target()
    assert got is not None
    fast, slow = got
    assert isinstance(fast, float) and fast > 0.0
    assert slow is not None and slow > 0.0


def test_one_component_reports_no_slow_target():
    d = tf.ModelParams.from_preset("pt-v1").to_dict()
    assert d["market_vol_slow_weight"] == 0.0, "pt-v1 is the one-component case"
    got = _run(tf.ModelParams.from_preset("pt-v1")).market_variance_target()
    assert got is not None
    fast, slow = got
    assert fast > 0.0
    assert slow is None, (
        "there is no slow component on this branch, so there is no slow "
        "target to report"
    )


def test_the_none_is_structural_not_a_coincidence_of_shipped_presets():
    """THE TEST THE RULING EXISTS FOR.

    `(target, target)` was rejected because it rests on every
    `market_vol_slow_weight` 0.0 preset also shipping
    `market_vol_slow_vix_damp` 0.0. Every shipped preset does, so the
    rejected form would pass every test above. This builds the preset that
    breaks the coincidence — no slow component, but a non-zero damp — and
    shows the two targets would NOT be equal there, so reporting the fast
    one twice would be reporting a number the model never computed.

    It cannot assert the slow value (there is none). It asserts the
    premise: that `(target, target)` would have been wrong, on a preset
    nothing prevents somebody writing tomorrow.
    """
    d = tf.ModelParams.from_preset("pt-v1").to_dict()
    d["market_vol_slow_weight"] = 0.0        # no slow component
    d["market_vol_slow_vix_damp"] = 0.5      # but a damp that would move its target
    one = tf.ModelParams.from_dict(d)
    got = _run(one).market_variance_target()
    assert got is not None and got[1] is None, "still no slow component"

    # The same dials WITH a slow component: the two targets differ, which
    # is what makes `(target, target)` a fabrication rather than a synonym.
    d["market_vol_slow_weight"] = 0.35
    two = _run(tf.ModelParams.from_dict(d)).market_variance_target()
    assert two is not None and two[1] is not None
    fast, slow = two
    assert not math.isclose(fast, slow, rel_tol=1e-12), (
        f"the damp did not separate the targets ({fast} vs {slow}); if this "
        "ever holds, the coincidence the ruling guards against has become "
        "an invariant and the ruling should be revisited"
    )


def test_a_fork_carries_the_reading_and_a_restore_does_not():
    e = _run(tf.ModelParams.from_preset("pt-v16"))
    before = e.market_variance_target()
    assert before is not None

    forked = e.fork(1)[0]
    assert forked.market_variance_target() == before, "Engine derives Clone"

    fresh = tf.Engine(seed=5, universe=tf.Universe.random(8, seed=11),
                      model=tf.ModelParams.from_preset("pt-v16"))
    fresh.restore_state(e.state_snapshot())
    assert fresh.market_variance_target() is None, (
        "the snapshot carries no such key, which is what keeps the "
        "diagnostic out of the state hash"
    )


def test_reading_it_moves_no_state():
    a = _run(tf.ModelParams.from_preset("pt-v16"))
    h1 = a.state_hash()
    for _ in range(5):
        a.market_variance_target()
    assert a.state_hash() == h1, "a read is a read"


@pytest.mark.parametrize("preset", ["pt-v1", "pt-v16", "pt-v18"])
def test_the_fast_target_is_positive_and_finite_on_every_shipped_preset(preset):
    got = _run(tf.ModelParams.from_preset(preset)).market_variance_target()
    assert got is not None
    fast, _ = got
    assert math.isfinite(fast) and fast > 0.0
