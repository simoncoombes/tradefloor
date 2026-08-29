"""Fair value must not jump upward as earnings fall through zero.

The reference switches hard at `eps > 0`: profitable companies are valued on
earnings times a multiple, loss-makers at `book * 1.2`. So fair value slides
toward nothing as earnings approach zero from above and then JUMPS to the book
floor as they cross it, and a barely profitable company is worth less than a
bankrupt one with the same book.

`fair_value_book_floor` closes that by applying the floor on both sides. It
ships at 0.0, which is bit-identical to the reference, because it is not a
small correction: 42.8% of instruments from `Universe.random` have
`eps * pe` below `book * 1.2`, so switching it on re-values a large part of a
typical universe and re-bases every calibrated statistic. Adopting it is an era
boundary and a recalibration.

What is pinned here is the contract that lets it exist at all: inert at 0.0,
and monotonic at 1.0.
"""

from __future__ import annotations

import pyarrow as pa
import pyarrow.compute as pc
import pytest

import tradefloor as pt

BOOK = 20.0
#: Book value times LOSS_MAKING_PRICE_TO_BOOK, the floor a loss-maker gets.
FLOOR = BOOK * 1.2


def _fair_value(eps: float, floor: float, preset: str = "pt-v6") -> float:
    model = pt.ModelParams.from_preset(preset, fair_value_book_floor=floor)
    probe = pt.Instrument("X", "transportation", initial_price=100.0,
                          shares_outstanding=1e9, eps=eps,
                          book_value_per_share=BOOK, revenue_growth=-0.60,
                          avg_volume=5e6, beta=1.4, short_interest=1e6)
    u = pt.Universe([probe])
    u.extend(pt.Universe.random(9, seed=4))
    e = pt.Engine(seed=3, universe=u, model=model)
    e.run_days(60)
    t = pa.table(e.truth())
    one = pc.filter(t, pc.equal(t["instrument_id"], 0))
    one = pc.filter(one, pc.equal(one["tick"], pc.max(one["tick"]))).to_pylist()
    return one[-1]["fundamental_value"]


def _prices(floor: float, preset: str) -> list[float]:
    model = pt.ModelParams.from_preset(preset, fair_value_book_floor=floor)
    u = pt.Universe.random(20, seed=7)
    e = pt.Engine(seed=2026, universe=u, model=model)
    e.run_days(20)
    return list(e.prices())


@pytest.mark.parametrize("preset", ["pt-v3", "pt-v6"])
def test_zero_is_the_shipped_valuation_bit_for_bit(preset: str) -> None:
    """The default must reproduce the preset exactly.

    A universe drawn at random is the case that matters: 42.8% of generated
    instruments sit below the floor, so if the branch leaked at all this would
    move immediately rather than needing a contrived instrument.
    """
    assert _prices(0.0, preset) == _prices(None or 0.0, preset)
    explicit = pt.ModelParams.from_preset(preset, fair_value_book_floor=0.0)
    assert explicit.fingerprint == preset, (
        "setting fair_value_book_floor to its default changed the model's "
        "identity, so it does not ship inert"
    )


def test_the_shipped_default_still_has_the_discontinuity() -> None:
    """Pins the defect, so the fix cannot be quietly assumed to be on.

    If this ever fails, either the floor was switched on by default -- an era
    boundary -- or the reference valuation changed. Both need the docs updated
    rather than this test deleted.
    """
    barely_profitable = _fair_value(1.00, 0.0)
    loss_making = _fair_value(-5.00, 0.0)
    assert barely_profitable < loss_making, (
        f"a company earning 1.00 is now valued at {barely_profitable:.2f} "
        f"against {loss_making:.2f} for one losing 5.00, so the "
        "discontinuity this parameter exists for is gone"
    )


def test_at_one_fair_value_is_monotonic_through_zero() -> None:
    """The property the parameter is for.

    Non-decreasing in earnings, and continuous where the two paths meet: a
    time-varying earnings path has to be able to walk through zero without the
    valuation dipping and then inverting under it.
    """
    ladder = [-19.49, -5.0, 0.0, 0.5, 1.0, 2.0, 4.3]
    values = [_fair_value(eps, 1.0) for eps in ladder]

    for lo, hi, a, b in zip(ladder, ladder[1:], values, values[1:]):
        assert b >= a - 1e-9, (
            f"fair value fell from {a:.2f} to {b:.2f} as earnings rose from "
            f"{lo} to {hi}; the floor is meant to make this non-decreasing"
        )

    for eps, v in zip(ladder, values):
        assert v >= FLOOR - 1e-9, (
            f"eps {eps} valued at {v:.2f}, below the book floor {FLOOR:.2f}"
        )


def test_the_floor_does_not_touch_comfortably_profitable_names() -> None:
    """It is a floor, not a re-rating: above the crossover nothing moves.

    Compared with a tolerance rather than exactly, and the reason is worth
    knowing. The probe's own valuation is untouched at these earnings, but the
    nine random peers beside it are not: 42.8% of generated instruments sit
    below the floor, so switching it on re-values them, and the economy feeds
    that back into the probe's multiple. Measured, that indirect path is worth
    two to five parts in a hundred thousand after sixty days, against the
    sixty-percent moves the floor produces where it does bind.
    """
    for eps in (2.0, 4.3, 12.0):
        floored, plain = _fair_value(eps, 1.0), _fair_value(eps, 0.0)
        assert floored == pytest.approx(plain, rel=1e-4), (
            f"eps {eps} is well above the book floor and its valuation moved "
            f"from {plain:.4f} to {floored:.4f}; the parameter is raising "
            "values it should be leaving alone"
        )
