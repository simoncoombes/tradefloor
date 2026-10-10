"""The stress memory's own rate (`vix_stress_premium_memory`).

The published VIX's stress memory steps at `vix_anchor_memory`'s rate unless
this dial is set. These tests hold that it is 0.0 on every preset and silent in
the digest, that 0.0 and the anchor's own rate are the shipped arithmetic bit
for bit (quote, state, prices, forecast and snapshot), that off zero only the
published VIX and its stress memory move, and that the memory then steps at
the dial's rate.
"""

import struct

import pytest

import tradefloor as tf

UNIVERSE = list(tf.Universe.random(6, seed=3))
LIVE = {"vix_stress_premium": 2.0, "vix_stress_premium_knee": 0.0,
        "vix_stress_premium_cap": 0.25, "forecast_horizon_sessions": 21.0}


def engine(**dials):
    return tf.Engine(seed=7, universe=UNIVERSE,
                     model=tf.ModelParams.from_preset("pt-v20", **dials))


def record(e, days=120):
    out = []
    for _ in range(days):
        e.run_days(1)
        out.append((e.macro_fields["vix"], e.economy()["vix"],
                    struct.unpack("<6d", e.prices()[:48]),
                    tuple(e.forecast()["vix"]),
                    e.state_snapshot().get("vix_stress_memory")))
    return out


@pytest.mark.parametrize("preset", tf.preset_names())
def test_off_on_every_shipped_preset(preset):
    assert tf.ModelParams.from_preset(preset).to_dict()["vix_stress_premium_memory"] == 0.0


def test_silent_in_the_digest_at_zero():
    assert "vix_stress_premium_memory" in set(tf.ModelParams.digest_silent_at_zero())
    explicit = tf.ModelParams.from_preset("pt-v21", vix_stress_premium_memory=0.0)
    assert explicit.fingerprint == "pt-v21"
    assert tf.ModelParams.from_preset(
        "pt-v21", vix_stress_premium_memory=0.05).fingerprint.startswith("custom-")


def test_zero_and_the_anchors_rate_are_the_shipped_arithmetic():
    rate = tf.ModelParams.from_preset("pt-v20").to_dict()["vix_anchor_memory"]
    assert rate > 0.0
    base = record(engine(**LIVE))
    assert record(engine(**LIVE, vix_stress_premium_memory=rate)) == base


def test_off_zero_only_the_quote_moves():
    base = record(engine(**LIVE))
    fast = record(engine(**LIVE, vix_stress_premium_memory=0.2))
    assert [r[1] for r in fast] == [r[1] for r in base]
    assert [r[2] for r in fast] == [r[2] for r in base]
    assert [r[0] for r in fast] != [r[0] for r in base]
    assert [r[4] for r in fast] != [r[4] for r in base]


def test_out_of_range_is_refused():
    for v in (-0.1, 1.5, float("nan")):
        with pytest.raises(ValueError):
            tf.ModelParams.from_preset("pt-v20", vix_stress_premium_memory=v)


# The anchor's pull undone above the knee (`vix_stress_premium_undo`).

def test_undo_is_off_on_every_preset_and_silent():
    for preset in tf.preset_names():
        assert tf.ModelParams.from_preset(preset).to_dict()["vix_stress_premium_undo"] == 0.0
    assert "vix_stress_premium_undo" in set(tf.ModelParams.digest_silent_at_zero())
    assert tf.ModelParams.from_preset("pt-v21", vix_stress_premium_undo=0.0).fingerprint == "pt-v21"


def test_undo_at_zero_is_the_hinge():
    assert record(engine(**LIVE, vix_stress_premium_undo=0.0)) == record(engine(**LIVE))


def test_undo_is_the_anchor_weight_above_the_knee_and_moves_only_the_quote():
    import math
    base = record(engine(**LIVE))
    e = engine(**LIVE, vix_stress_premium_undo=1.0)
    w = e.params().to_dict()["vix_anchor_weight"] if hasattr(e, "params") else \
        tf.ModelParams.from_preset("pt-v20").to_dict()["vix_anchor_weight"]
    assert w > 0.0
    moved = 0
    for i in range(120):
        e.run_days(1)
        m = e.state_snapshot()["vix_stress_memory"]
        state = e.economy()["vix"]
        want = state * math.exp(w * max(0.0, m - LIVE["vix_stress_premium_knee"]))
        assert e.macro_fields["vix"] == pytest.approx(want, rel=1e-12)
        assert state == base[i][1]
        assert struct.unpack("<6d", e.prices()[:48]) == base[i][2]
        moved += e.macro_fields["vix"] != base[i][0]
    assert moved > 0
