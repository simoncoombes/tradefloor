"""`jump_momentum_share` must ship inert, and must be live only via a jump.

The mechanism exists to break the coupling §34 named and §37 reinstated: a
jump lands on `mispricing_s`, `mispricing_s_prev_close` stays behind, and at
the next close `momentum_theta` reads the jump as a re-rating and continues
it. Fattening the 504-day tail and adding 252-day return continuation are
the same write, so pushing one down pushes the other down with it.

The share advances the momentum reference with the jump, by the amount
herding must not see. What these tests pin is the safety contract around
that, which is what lets the mechanism exist at all:

  * At 1.0 it is EXACTLY the old behaviour, bit for bit, on a preset that
    actually fires jumps. Not close, identical, because a preset that moved
    by a rounding error would invalidate every result that cited it.
  * Below 1.0 it changes the trajectory ONLY when a jump fires. On a preset
    with zero jump intensity it must stay inert at every value, or it would
    be a second way to move a price that nobody asked for.
  * It moves the statistic it was built to move, in the direction claimed.
"""

from __future__ import annotations

import pytest

import tradefloor as pt

UNIVERSE_SEED = 7
NAMES = 20
DAYS = 12
SEED = 2026


def _prices(share: float | None, preset: str) -> list[float]:
    model = (
        preset
        if share is None
        else pt.ModelParams.from_preset(preset, jump_momentum_share=share)
    )
    u = pt.Universe.random(NAMES, seed=UNIVERSE_SEED)
    e = pt.Engine(seed=SEED, universe=u, model=model)
    e.run_days(DAYS)
    return list(e.prices())


def test_one_point_zero_is_the_shipped_trajectory_bit_for_bit() -> None:
    """The default value reproduces the preset exactly, on a jumping preset.

    pt-v4 is the preset that fires jumps, so it is the only one where this
    assertion has anything to catch. Running it on pt-v3, whose jump
    intensity is zero, would pass for the wrong reason.
    """
    baseline = _prices(None, "pt-v4")
    explicit = _prices(1.0, "pt-v4")
    assert explicit == baseline, (
        "setting jump_momentum_share to its default changed the trajectory; "
        "the mechanism does not ship inert and every pt-v4 result is invalid"
    )


@pytest.mark.parametrize("share", [0.0, 0.25, 0.5, 0.75])
def test_it_is_inert_without_a_jump_to_withhold(share: float) -> None:
    """No jumps, no effect, at any value.

    pt-v3 has zero jump intensity. The share governs what herding is allowed
    to continue OF A JUMP, so with no jump there is nothing to withhold and
    the trajectory must not move. If this fails the mechanism is touching
    something other than a jump.
    """
    assert _prices(share, "pt-v3") == _prices(None, "pt-v3"), (
        f"jump_momentum_share={share} moved a market with no jumps in it"
    )


def test_it_changes_the_trajectory_when_jumps_do_fire() -> None:
    """The converse of the two above, so they cannot both pass vacuously.

    Without this, a mechanism wired to nothing at all would satisfy every
    other test in this file.
    """
    assert _prices(0.0, "pt-v4") != _prices(1.0, "pt-v4"), (
        "jump_momentum_share had no effect on a preset that fires jumps, so "
        "it is not wired to anything"
    )


def test_the_fingerprint_moves_with_it() -> None:
    """A different market must not claim the same identity.

    The share is a settable coefficient, so a model carrying a non-default
    value is a different model and must not answer to pt-v4's name.

    It no longer answers to `custom-` either: pt-v4 at share 0.0 IS pt-v5
    now, and the fingerprint is taken over the parameters rather than over
    the table, so it resolves to the shipped name. That is the property
    worth pinning. A vector identical to a preset presenting as custom
    would be as wrong as a custom vector presenting as a preset.
    """
    shipped = pt.ModelParams.from_preset("pt-v4")
    altered = pt.ModelParams.from_preset("pt-v4", jump_momentum_share=0.0)
    assert altered.fingerprint != shipped.fingerprint
    assert shipped.fingerprint == "pt-v4"
    assert altered.fingerprint == "pt-v5"
    assert pt.ModelParams.from_preset("pt-v5").fingerprint == "pt-v5"

    # A value that is NOT a shipped preset still has to declare itself.
    odd = pt.ModelParams.from_preset("pt-v4", jump_momentum_share=0.3)
    assert odd.fingerprint.startswith("custom-")


def test_it_is_readable_settable_and_defaults_to_one() -> None:
    p = pt.ModelParams.from_preset("pt-v4")
    assert p.to_dict()["jump_momentum_share"] == 1.0
    assert "jump_momentum_share" in pt.ModelParams.settable()
    assert (
        pt.ModelParams.from_preset("pt-v4", jump_momentum_share=0.25)
        .to_dict()["jump_momentum_share"]
        == 0.25
    )


def test_lowering_it_reduces_return_autocorrelation() -> None:
    """The claim the mechanism is for, at the smallest honest scale.

    Six seeds is screening resolution and cannot certify anything; the
    thirty-seed confirmation lives in the calibration record. What this pins
    is the DIRECTION, so a later change that silently inverts the sign gets
    caught here rather than in a search that costs real money.
    """
    from tradefloor import facts

    u = pt.Universe.random(40, seed=111)
    seeds = (101, 102, 103, 104, 105, 106)

    def median_acf1(share: float) -> float:
        model = pt.ModelParams.from_preset("pt-v4", jump_momentum_share=share)
        vals = sorted(
            facts.measure(seed=s, universe=u, days=252, model=model)["return_acf1"]
            for s in seeds
        )
        return vals[len(vals) // 2]

    coupled = median_acf1(1.0)
    split = median_acf1(0.0)
    assert split < coupled, (
        f"decoupling the jump from herding did not lower return_acf1: "
        f"{coupled:.4f} coupled against {split:.4f} split. The mechanism's "
        "whole claim is that it does."
    )
