"""The VIX's level is the index's own conditional variance, or it is not.

`vix_level_identity` replaces a target that was about two thirds constants
-- a phase table shrunk toward 19, an offset cancelling the standing
excursion an asymmetric gain injects, an earnings bump -- and one third the
market FACTOR's sigma read through a conversion of 2105.1 VIX points per
unit of daily sigma where the identity is `100 * sqrt(252)` = 1587.5.

Two claims are asserted here and they are the whole change:

1. **Inert at 0.0.** Every preset before pt-v19 runs the arithmetic it ran.
2. **At 1.0 the level constants are NOT READ.** That is the criterion this
   programme judges a mechanism fix by -- which chosen dials it makes
   unnecessary -- and it is asserted the only way that cannot be argued
   with: two engines differing only in the dial produce the same
   trajectory to the bit.

Every retirement test has a MIRROR that shows the same dial moving the same
trajectory with the identity off. Without those, a harness that had lost
its grip on the model -- a preset that never reaches the code, a run too
short to differ -- would report every dial retired and pass.
"""
from __future__ import annotations

import math
import struct

import pytest

import tradefloor as pt

#: Small enough to run in the suite, large enough that the macro chain
#: reaches the market and the cycle constants have somewhere to bite.
NAMES, DAYS, SEED = 12, 40, 3
UNIVERSE_SEED = 111

#: The arm the design note registers its predictions on: pt-v18 with the C8
#: fear vector. Written out here rather than imported so the vector a test
#: runs is visible at the test.
C8 = {"vix_return_gain": 30.0, "vix_return_gain_up": 14.0,
      "vix_target_offset": -9.16, "vix_target_shock_cap": 150.0,
      "buyback_payout_share": 0.5, "jump_intensity_market": 0.031746,
      "jump_mean_market": -0.012, "jump_sigma_market": 0.02}


def universe():
    return pt.Universe.random(NAMES, seed=UNIVERSE_SEED)


def model(**over):
    d = pt.ModelParams.from_preset("pt-v18").to_dict()
    d.update(C8)
    d.update(over)
    return pt.ModelParams.from_dict(d)


def _f64s(b):
    return struct.unpack("<%dd" % (len(b) // 8), b)


def trajectory(m, days=DAYS, seed=SEED, uni=None):
    """The VIX path and every close, as exact bits.

    Prices as well as the VIX, because a dial can be unread by the VIX's
    own target and still reach the market some other way -- through the
    variance couplings the anchor feeds, for instance -- and a test that
    watched only the VIX would call that retired.
    """
    engine = pt.Engine(seed=seed, universe=uni if uni is not None else universe(),
                       model=m)
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


def same_to_the_bit(a, b):
    return bits(a[0]) == bits(b[0]) and bits(a[1]) == bits(b[1])


# ── 1. Inert at the default ──────────────────────────────────────────────

#: Every preset that ships, in the spelling test_earnings_nominal_growth
#: uses. pt-v17 is absent because the recomposition era reserves the number.
SHIPPED_PRESETS = (
    "pt-v1", "pt-v2", "pt-v3", "pt-v4", "pt-v5", "pt-v6", "pt-v7", "pt-v8",
    "pt-v9", "pt-v10", "pt-v11", "pt-v12", "pt-v13", "pt-v14", "pt-v15",
    "pt-v16", "pt-v18", "pt-v19",
)

#: The presets that switch the identity ON, by name and on purpose. pt-v19
#: is the first (composed 2026-09-10, `params.rs::pt_v19`, provenance in
#: `provenance.DIAL_PROVENANCE["vix_level_identity"]`). A preset that is
#: not in this tuple and reads 1.0 turned it on by accident.
IDENTITY_ON = ("pt-v19",)


def test_every_shipped_preset_leaves_the_identity_off_unless_named_here():
    """A preset that turned it on by accident would move every trajectory
    it has, and -- while it is not the default -- nothing but this would
    say so, because the known-answer digest only watches the default."""
    for name in SHIPPED_PRESETS:
        d = pt.ModelParams.from_preset(name).to_dict()
        expected = 1.0 if name in IDENTITY_ON else 0.0
        assert d["vix_level_identity"] == expected, name
        # And the premium ships at the value it was MEASURED at, on every
        # one of them, so a preset cannot acquire a different premium
        # without saying so.
        assert d["vix_variance_premium"] == 0.252, name
    assert set(IDENTITY_ON) <= set(SHIPPED_PRESETS)


def test_the_premium_is_not_read_while_the_identity_is_off():
    """The dial the perturbation registry declares inert, shown inert.

    `vix_variance_premium` ships at the MEASURED 0.252 rather than at a
    value that makes it inert, because the branch that reads it is what
    makes it inert. So the claim has to be checked rather than assumed.
    """
    a = trajectory(model(vix_variance_premium=0.252))
    b = trajectory(model(vix_variance_premium=1.75))
    assert same_to_the_bit(a, b)


def test_the_premium_moves_the_level_once_the_identity_is_on():
    """The mirror. Without this the test above passes on a dial that is
    wired to nothing at all."""
    a = trajectory(model(vix_level_identity=1.0, vix_variance_premium=0.252))
    b = trajectory(model(vix_level_identity=1.0, vix_variance_premium=1.75))
    assert not same_to_the_bit(a, b)
    # And in the direction the identity gives it: the premium multiplies
    # the whole level.
    assert sum(b[0]) / len(b[0]) > sum(a[0]) / len(a[0])


# ── 2. What the identity retires ─────────────────────────────────────────

#: Each entry is (dial, value A, value B). Under the identity the two must
#: produce the same trajectory to the bit; with it off they must not.
RETIRED = [
    # The read-back's own anchor: derived from the roster's unconditional
    # variance, so the dial is not read at any of its four coupling sites.
    ("market_vol_vix_anchor", 15.98426471, 24.0),
    # The phase table's amplitude, and with it the table: the business
    # cycle reaches the VIX through the variance processes or not at all.
    ("vix_cycle_amplitude", 0.85, 0.20),
    # The offset, which cancelled the excursion an asymmetric gain injects.
    # Under the identity that is a closed form computed daily.
    ("vix_target_offset", -9.16, 0.0),
    # The blend weight: the WHOLE target is the read-back, so there is
    # nothing to blend it with.
    ("vix_realised_vol_weight", 0.3, 0.9),
    # Which return the spike reads: the identity always reads the session,
    # because the zero-mean correction is sized for a session.
    ("vix_return_source", 1.0, 0.0),
]


@pytest.mark.parametrize("dial,a,b", RETIRED, ids=[r[0] for r in RETIRED])
def test_the_identity_does_not_read_the_level_constants(dial, a, b):
    ta = trajectory(model(vix_level_identity=1.0, **{dial: a}))
    tb = trajectory(model(vix_level_identity=1.0, **{dial: b}))
    assert same_to_the_bit(ta, tb), (
        f"{dial} still moves the market under the identity")


@pytest.mark.parametrize("dial,a,b", RETIRED, ids=[r[0] for r in RETIRED])
def test_each_of_those_does_move_the_market_with_the_identity_off(dial, a, b):
    """THE MIRROR, and it is not decoration.

    Every one of these dials is live on the shipped path. A harness that
    had stopped reaching the model -- a universe too small, a run too
    short, a preset that never applied -- would report all five retired and
    pass the test above. This is the check that the instrument works.
    """
    ta = trajectory(model(**{dial: a}))
    tb = trajectory(model(**{dial: b}))
    assert not same_to_the_bit(ta, tb), (
        f"{dial} moves nothing even with the identity off; the harness is "
        f"not reaching the model")


def test_the_business_cycle_does_not_set_the_level_under_the_identity():
    """The phase TABLE itself, which is five constants in the code and not
    a dial, so it cannot be shown retired by moving a value.

    Shown instead by its consequence: with the identity on, the VIX's
    target is a function of the index's variance alone, so two runs whose
    cycle phases differ must still agree wherever their variance does. The
    reachable version of that is the amplitude test above plus this: at
    amplitude 0.0 -- the table collapsed to its own mean, every phase the
    same 19 -- the identity's trajectory is unchanged, where the shipped
    path's is not.
    """
    flat = trajectory(model(vix_level_identity=1.0, vix_cycle_amplitude=0.0))
    full = trajectory(model(vix_level_identity=1.0, vix_cycle_amplitude=1.0))
    assert same_to_the_bit(flat, full)
    assert not same_to_the_bit(
        trajectory(model(vix_cycle_amplitude=0.0)),
        trajectory(model(vix_cycle_amplitude=1.0)))


# ── 3. The anchor is derived, and it is the identity's own fixed point ───

def test_the_anchor_is_the_dial_while_the_identity_is_off():
    for name in ("pt-v1", "pt-v16", "pt-v18"):
        m = pt.ModelParams.from_preset(name)
        e = pt.Engine(seed=1, universe=universe(), model=m)
        assert e.vix_anchor == m.to_dict()["market_vol_vix_anchor"], name


def test_the_derived_anchor_is_a_level_the_identity_could_return():
    """`anchor = (1 + pi) 100 sqrt(252 V_uncond)`, so the anchor must sit
    where a VIX priced off a baseline-variance index would sit.

    Asserted as a RANGE around the model's own realised level rather than
    against a number, because the number is roster-dependent and pinning it
    would be pinning a measurement in a test. What is asserted is the
    property: the derived anchor is far above the dial it replaces (which
    is the finding -- the two constants never agreed), and the model's own
    mean VIX under the identity lands near it, which is what "the loop is
    consistent at the unconditional point" means.
    """
    e = pt.Engine(seed=1, universe=universe(),
                  model=model(vix_level_identity=1.0))
    anchor = e.vix_anchor
    assert anchor > 15.98426471, "the derived anchor is not above the dial"
    assert 10.0 < anchor < 60.0, anchor
    vix, _, _ = trajectory(model(vix_level_identity=1.0), days=120)
    mean = sum(vix) / len(vix)
    assert 0.6 * anchor < mean < 1.6 * anchor, (mean, anchor)


def test_the_anchor_follows_the_roster_and_not_only_the_model():
    """A VIX prices its OWN index, so the anchor is a property of the pair.

    Two rosters, one model. Under the identity the anchors must differ;
    with it off they must be the same number, because there the anchor is
    a coefficient.
    """
    small = pt.Universe.random(8, seed=7)
    large = pt.Universe.random(40, seed=111)
    on = model(vix_level_identity=1.0)
    a = pt.Engine(seed=1, universe=small, model=on).vix_anchor
    b = pt.Engine(seed=1, universe=large, model=on).vix_anchor
    assert a != b
    off = model()
    assert (pt.Engine(seed=1, universe=small, model=off).vix_anchor
            == pt.Engine(seed=1, universe=large, model=off).vix_anchor)


# ── 4. The level it produces ─────────────────────────────────────────────

def test_the_identity_raises_the_vix_toward_its_own_index():
    """The finding, as a test: the shipped level sits BELOW the volatility
    its own index realises, where a real VIX sits above it.

    Real, per 252-session window over 1990-2025: median VIX / RV 1.252,
    IQR 1.128 to 1.398, and 92.5 per cent of windows above 1.0. The
    shipped model reads about 0.88. This asserts the DIRECTION and the
    crossing of 1.0, not the value: the value is a measurement and belongs
    in a record, not pinned here.
    """
    def ratio(m):
        engine = pt.Engine(seed=SEED, universe=universe(), model=m)
        vix, rets = [], []
        for _ in range(150):
            engine.open_market()
            engine.run_session(9, 30, 3, 390)
            engine.close_market()
            snap = engine.state_snapshot()
            vix.append(snap["economy"]["vix"])
            col = snap["columns"]
            acc = wsum = 0.0
            for pr, pc, mc in zip(_f64s(col["price"]),
                                  _f64s(col["previous_close"]),
                                  _f64s(col["market_cap"])):
                if pc > 0.0 and pr > 0.0:
                    w = mc / pr * pc
                    acc += (pr - pc) / pc * 100.0 * w
                    wsum += w
            rets.append(acc / wsum if wsum else 0.0)
        mu = sum(rets) / len(rets)
        sd = math.sqrt(sum((r - mu) ** 2 for r in rets) / len(rets))
        return (sum(vix) / len(vix)) / (sd * math.sqrt(252.0))

    off, on = ratio(model()), ratio(model(vix_level_identity=1.0))
    assert off < 1.0, off
    assert on > off, (off, on)
    assert on > 1.0, on
