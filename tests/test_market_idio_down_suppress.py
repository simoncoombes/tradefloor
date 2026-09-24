"""The variance-neutral down-tick reallocation, from the Python side.

`market_idio_down_suppress` suppresses a name's idiosyncratic shock by
`(1 - c)` on a down tick of the market factor and inflates it by
`sqrt(2 - (1 - c)^2)` on an up tick. The two squared scales sum to 2, so the
unconditional idiosyncratic variance is held exactly over an even split of
the half-lines and the mechanism raises the FACTOR's share on down ticks
without adding variance anywhere.

The tick-level algebra -- mean-neutrality pointwise, second-moment
neutrality over a symmetric factor, the bounds, and the size of the residual
at finite tick counts -- is proved in `rust/src/market/factors.rs`'s own
tests, where one tick can be addressed directly. What is left for this file
is the two claims that are only true of the ENGINE:

1. the dial is inert at 0.0 on every preset, bit for bit, over a run long
   enough that the branch is taken on both half-lines many thousands of
   times; and
2. it takes no draw at any value, on any stream, which is what makes the
   zero arm a control rather than a different random world.

Neither is a measurement of what the dial BUYS. That is
`programme/results/asymneut-registration.md` in the design repository and it
wants thirty seeds, not one.
"""

from __future__ import annotations

import struct

import pytest

import tradefloor as pt

UNIVERSE = pt.Universe.random(20, seed=11)

#: A run long enough that the factor has been down on tens of thousands of
#: ticks. The inertness claim is about a BRANCH, and a branch that is never
#: taken is not proved inert by a run that does not reach it.
DAYS = 20


def _run(model=None, *, seed: int = 77):
    kwargs = {} if model is None else {"model": model}
    engine = pt.Engine(seed=seed, universe=UNIVERSE, **kwargs)
    engine.run_days(DAYS)
    return engine


def _bits(engine) -> tuple:
    n = len(engine.tickers)
    return tuple(
        struct.unpack("<%dd" % n, engine.column(field))
        for field in ("price", "previous_close", "high", "low",
                      "mispricing_s", "garch_variance")
    )


@pytest.mark.parametrize("preset", ["pt-v1", "pt-v16", "pt-v18", "pt-v19"])
def test_the_dial_is_inert_at_zero_on_every_preset(preset: str) -> None:
    """Bit-identity, not approximate agreement.

    The branch is `== 0.0` and returns the shock untouched rather than
    multiplying it by a pair of ones, so this is an equality and not a
    tolerance. Run across presets because the thing that would break it is a
    preset whose factor spends its ticks on one side of zero, and four
    different variance processes is the cheapest way to not rely on one.
    """
    base = _bits(_run(preset))
    dialled = _bits(_run(pt.ModelParams.from_preset(
        preset, market_idio_down_suppress=0.0)))
    assert base == dialled


def test_the_dial_takes_no_draw_at_any_value() -> None:
    """The draw-schedule claim, on every stream and at every value.

    The transform reshapes a shock the tick has ALREADY taken, so there is
    no branch in it that a draw sits behind. That is not a convenience: a
    mechanism that needed a random number would have needed a stream of its
    own, which is a larger change than a reallocation, and this is the
    assertion that says this one did not need it.

    Asserted per stream rather than on the total, for the reason
    `test_model_params.market_state` gives: a total mixes two claims.
    """
    base = _run()
    counts = dict(base.draws_by_stream())
    total = base.draws_consumed
    for value in (0.05, 0.15, 0.40, 1.0):
        engine = _run(pt.ModelParams.from_preset(
            market_idio_down_suppress=value))
        assert dict(engine.draws_by_stream()) == counts, value
        assert engine.draws_consumed == total, value


def test_a_nonzero_value_moves_the_market_on_every_preset() -> None:
    """The counterproof to the inertness above: the wiring is live.

    A dial that reads inert at 0.0 because nothing is connected would pass
    the first test in this file and every preset check beside it. This is
    what makes that test a statement about the branch rather than about the
    wire.
    """
    for preset in ("pt-v16", "pt-v19"):
        base = _bits(_run(preset))
        dialled = _bits(_run(pt.ModelParams.from_preset(
            preset, market_idio_down_suppress=0.15)))
        assert base != dialled, preset


def test_the_dial_is_on_the_settable_surface_and_ships_at_zero() -> None:
    # The registries a new dial has to reach, checked here so that a dial
    # wired into the engine and into nothing else fails on its own file
    # rather than three files away.
    assert "market_idio_down_suppress" in pt.ModelParams.settable()
    for name in ("pt-v1", "pt-v16", "pt-v18", "pt-v19"):
        values = pt.ModelParams.from_preset(name).to_dict()
        assert values["market_idio_down_suppress"] == 0.0, name


def _window_variances(model, *, seed: int = 5, days: int = 252):
    """`(cross-sectional variance, index variance)` over a 252-day window.

    The cross-sectional variance -- the mean over sessions of the variance
    of that session's log returns ACROSS names -- is the quantity the
    neutrality identity is about. The common factor cancels out of it (up to
    the roster's beta dispersion), so it reads the idiosyncratic leg at
    roughly `names x sessions` degrees of freedom rather than `sessions`,
    which is what makes a one-seed reading of it worth asserting on where a
    one-seed reading of index volatility is not.
    """
    import math

    engine = pt.Engine(seed=seed, universe=UNIVERSE,
                       **({} if model is None else {"model": model}))
    n = len(engine.tickers)
    previous = None
    cross: list[float] = []
    index: list[float] = []
    for day in range(days):
        engine.run_days(1, first_day=day)
        prices = struct.unpack("<%dd" % n, engine.column("price"))
        if previous is not None:
            rets = [math.log(a / b) for a, b in zip(prices, previous)]
            mean = sum(rets) / len(rets)
            index.append(mean)
            cross.append(sum((r - mean) ** 2 for r in rets) / (len(rets) - 1))
        previous = prices
    mean_index = sum(index) / len(index)
    index_var = sum((r - mean_index) ** 2 for r in index) / (len(index) - 1)
    return sum(cross) / len(cross), index_var


def test_the_reallocation_holds_the_idiosyncratic_budget_in_the_engine():
    """What the neutrality looks like from outside the tick. MEASURED.

    PROTOCOL: `Universe.random(20, seed=11)`, engine seed 5, 252 sessions,
    the default preset against the same preset at `market_idio_down_suppress`
    0.15 and 1.00. The two arms are paired by construction and not by
    arrangement -- the dial takes no draw, so both arms consume the same
    normals in the same order and the shocks being compared are literally
    the same numbers under two scalings.

    The per-tick identity is exact in EXPECTATION and not in a realisation:
    a finite run realises a binomial fraction of down ticks rather than
    exactly half, which is worth `|1 - (1-c)^2| / sqrt(N)` of the
    idiosyncratic variance -- 0.09 per cent over a window this long at
    `c` = 0.15. On top of that the engine has feedback the tick algebra does
    not cover: the per-name GJR GARCH innovation is the day's squared noise
    and this dial moves that conditionally on the day's direction, so a
    level can drift through the variance recursion.

    MEASURED, seeds 5, 6 and 7 at `c` in {0.05, 0.15, 0.40, 1.00}: the
    cross-sectional variance moves by -3.7 to +2.7 per cent and the index's
    annualised volatility by -1.5 to +0.8 per cent, both UNSIGNED and
    non-monotone in `c`. For scale, the multiplying tilt this replaces pays
    +9.5 points of annualised volatility per unit. The bars below are set
    above the measured spread rather than at it, because this is a guard
    against a wiring error that leaks a large multiple of the idiosyncratic
    budget -- a missing `sqrt`, one half-line scaled and not the other --
    and not a certification of neutrality, which one seed cannot give.
    """
    import math

    base_cross, base_index = _window_variances(None)
    assert base_cross > 0.0 and base_index > 0.0
    for value, cross_bar, vol_bar in ((0.15, 0.05, 0.03), (1.0, 0.06, 0.04)):
        cross, index = _window_variances(
            pt.ModelParams.from_preset(market_idio_down_suppress=value))
        assert abs(cross / base_cross - 1.0) < cross_bar, (
            f"c={value}: the idiosyncratic budget moved from {base_cross} "
            f"to {cross}, further than a reallocation of it can")
        assert abs(math.sqrt(index / base_index) - 1.0) < vol_bar, (
            f"c={value}: index volatility moved by "
            f"{math.sqrt(index / base_index) - 1.0}, and this dial adds no "
            "variance to the factor at all")
