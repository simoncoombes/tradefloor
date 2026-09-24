"""The VIX's own slow reversion toward the identity's anchor.

Under `vix_level_identity` the VIX reverts to the read-back of the index's
conditional variance and the variance reverts to a target that reads the
VIX. Neither reverts to a LEVEL, so the pair's only anchor is that its
static gain is under one: the closer that gain is to one, the longer the
pair wanders. `vix_anchor_reversion` is the third thing -- one term in the
VIX step, `+ kappa * (L * anchor - x)` -- and it ships at 0.0 on every
preset.

Three claims:

1. **Inert at 0.0.** Every shipped preset runs the arithmetic it ran, to
   the bit, with the dial written out explicitly.
2. **At kappa it pulls toward `L * anchor`.** Measured as the VIX path's
   mean distance from the anchor, which must shrink.
3. **The invariants.** A rate outside [0, 1) is refused, and so is any
   rate with the identity off, where there is no derived anchor.
"""
from __future__ import annotations

import math
import struct

import pytest

import tradefloor as pt

NAMES, DAYS, SEED, UNIVERSE_SEED = 12, 60, 3, 111

#: The DERIVED value: the kappa at which the linearised loop's slow pole
#: equals the tape's 0.9965 at `market_vol_vix_exponent` 1.83 and the
#: shipped `vix_mean_reversion` 0.27. See the dial's own docstring.
KAPPA = 0.046081

SHIPPED_PRESETS = (
    "pt-v1", "pt-v2", "pt-v3", "pt-v4", "pt-v5", "pt-v6", "pt-v7", "pt-v8",
    "pt-v9", "pt-v10", "pt-v11", "pt-v12", "pt-v13", "pt-v14", "pt-v15",
    "pt-v16", "pt-v18", "pt-v19",
)


def universe():
    return pt.Universe.random(NAMES, seed=UNIVERSE_SEED)


def _f64s(b):
    return struct.unpack("<%dd" % (len(b) // 8), b)


def trajectory(model, days=DAYS, seed=SEED):
    """The VIX path and every close, as exact bits, plus the anchor."""
    engine = pt.Engine(seed=seed, universe=universe(), model=model)
    vix, prices = [], []
    for _ in range(days):
        engine.open_market()
        engine.run_session(9, 30, 3, 390)
        engine.close_market()
        snap = engine.state_snapshot()
        vix.append(snap["economy"]["vix"])
        prices.extend(_f64s(snap["columns"]["price"]))
    return vix, prices, engine.vix_anchor


def bits(xs):
    return [struct.pack("<d", x) for x in xs]


def pull_target(model, days=DAYS, seed=SEED):
    """The VIX path and, day by day, the level the reversion pulls it to.

    `L * anchor`, with `L` the slow regime level's multiplier as the engine
    forms it (`Engine::vix_level_multiplier`, engine.rs): `exp(log_level -
    var / 2)`, `var` the level's stationary variance at the APPLIED sigma
    (`vix_level_sigma` over `vix_level_loop_gain` where the gain is set), and
    exactly 1.0 with the level off or at zero. No accessor exposes it, so it
    is spelled out here from the snapshot's `vix_log_level` and the dials.
    """
    d = model.to_dict()
    sigma = d["vix_level_sigma"]
    if d["vix_level_loop_gain"] != 0.0:
        sigma = sigma / d["vix_level_loop_gain"]
    phi = d["vix_level_persistence"]
    var = sigma * sigma / (1.0 - phi * phi) if sigma != 0.0 else 0.0
    engine = pt.Engine(seed=seed, universe=universe(), model=model)
    vix, target = [], []
    for _ in range(days):
        engine.open_market()
        engine.run_session(9, 30, 3, 390)
        engine.close_market()
        snap = engine.state_snapshot()
        vix.append(snap["economy"]["vix"])
        log_level = snap["vix_log_level"]
        level = (1.0 if sigma == 0.0 or log_level == 0.0
                 else math.exp(log_level - 0.5 * var))
        target.append(level * engine.vix_anchor)
    return vix, target, engine.vix_anchor


# ── 1. Inert at 0.0 ──────────────────────────────────────────────────────

@pytest.mark.parametrize("name", SHIPPED_PRESETS)
def test_every_shipped_preset_leaves_the_reversion_off(name):
    assert pt.ModelParams.from_preset(name).to_dict()["vix_anchor_reversion"] == 0.0


def test_writing_the_zero_out_changes_no_bit_of_any_trajectory():
    """`x + 0.0` is not a no-op on a negative zero, so the zero arm is a
    BRANCH in `daily.rs` rather than an addition of nothing. This is the
    branch, read at the engine: prices as well as the VIX, because a dial
    can be unread by the VIX's own step and still reach the market.
    """
    for name in ("pt-v18", "pt-v19"):
        was = trajectory(pt.ModelParams.from_preset(name))
        now = trajectory(pt.ModelParams.from_preset(name, vix_anchor_reversion=0.0))
        assert bits(was[0]) == bits(now[0]), name
        assert bits(was[1]) == bits(now[1]), name


def test_the_dial_is_not_inert_at_the_derived_value():
    """The counterproof: without it, the test above would pass on a wheel
    that had never heard of the dial."""
    was = trajectory(pt.ModelParams.from_preset("pt-v19"))
    now = trajectory(pt.ModelParams.from_preset("pt-v19", vix_anchor_reversion=KAPPA))
    assert bits(was[0]) != bits(now[0])


# ── 2. It pulls toward L * anchor ────────────────────────────────────────

def test_the_reversion_pulls_the_vix_toward_the_anchor():
    """The VIX's mean distance from `L * anchor`, over a run, must shrink.

    Asserted as a DIRECTION over several seeds rather than a value: the
    size is the closed form and the Rust test
    `the_anchor_reversion_moves_the_vix_toward_the_level_times_the_anchor`
    pins it exactly on a day whose variance is pinned. Here the whole loop
    is running, and what is claimed is only that the term does its name.

    The distance is taken to the term's own target, `L * anchor` on each
    day, and no longer to the bare anchor. The bare anchor stood in for it
    while the slow level was narrow; pt-v19's fifth composition (2026-09-23)
    widened the level (sigma 0.0181 on a loop gain of 1.79, against 0.0173
    on 2.4684) and put the VIX's own target on the anchor form, and against
    the bare anchor the term then read closer on 4 of 6 seeds while it
    moved the VIX toward `L * anchor` on all six. The fourth composition
    reads 6 of 6 either way.
    """
    closer = 0
    for seed in range(1, 7):
        off = pull_target(pt.ModelParams.from_preset("pt-v19"), days=120, seed=seed)
        on = pull_target(pt.ModelParams.from_preset("pt-v19", vix_anchor_reversion=KAPPA),
                         days=120, seed=seed)
        assert off[2] == on[2], "the anchor is derived and the dial must not move it"
        d_off = sum(abs(v - t) for v, t in zip(off[0], off[1])) / len(off[0])
        d_on = sum(abs(v - t) for v, t in zip(on[0], on[1])) / len(on[0])
        closer += d_on < d_off
    assert closer >= 5, f"the reversion moved the VIX toward L * anchor on {closer} of 6"


# ── 3. The invariants ────────────────────────────────────────────────────

@pytest.mark.parametrize("rate", [-0.5, -1e-9, 1.0, 1.5, 12.0])
def test_a_rate_outside_the_unit_interval_is_refused(rate):
    with pytest.raises(Exception) as e:
        pt.ModelParams.from_preset("pt-v19", vix_anchor_reversion=rate)
    assert "vix_anchor_reversion" in str(e.value)


def test_the_reversion_is_refused_with_the_identity_off():
    """Off the identity `derive_vix_anchor` returns the DIAL
    `market_vol_vix_anchor` and the VIX's target is the phase table, so
    the term would pull a VIX on one scale toward a level on another. The
    same refusal `vix_level_sigma` carries."""
    with pytest.raises(Exception) as e:
        pt.ModelParams.from_preset("pt-v18", vix_anchor_reversion=KAPPA)
    assert "vix_level_identity" in str(e.value)
    # And it is admissible the moment the identity is on.
    pt.ModelParams.from_preset("pt-v18", vix_anchor_reversion=KAPPA,
                               vix_level_identity=1.0)
