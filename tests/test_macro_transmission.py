"""Which macro fields reach a valuation, and through what.

This file exists because a worked example shipped with the answer backwards.
Notebook 09's transmission probe ran over two days, saw `federal_funds_rate`
and `vix` move nothing, and concluded they were disconnected. They are not.
The corporate bond yield is the rate equities discount off and it is
recomputed only at central-bank meetings, the first scheduled 45 days out, so
a short probe measures the meeting schedule and reports it as an absence.

`docs/scenario-recipes.md` has led with that trap since before the notebook
existed. Documentation did not prevent the error, so the shape is pinned here
as well, where a change breaks a test rather than quietly invalidating a page
of prose.

What is pinned is the *map*, not the coefficients: which field reaches a
valuation, through what, and on what schedule. The magnitudes are free to move
with calibration and the assertions are written to allow that.
"""

from __future__ import annotations

import json

import pyarrow as pa
import pyarrow.compute as pc
import pytest

import tradefloor as pt

#: The first scheduled central-bank meeting. Nothing rate-borne transmits
#: before it, and the boundary is sharp rather than gradual.
FIRST_MEETING_DAY = 45

BASE = {"vix": 15.0, "federal_funds_rate": 0.01625,
        "corporate_bond_yield": 0.0554, "qe_pe_boost": 0.0}


def _universe():
    probe = pt.Instrument("PROBE", "technology", initial_price=100.0,
                          shares_outstanding=1e9, eps=4.0,
                          book_value_per_share=20.0, revenue_growth=0.05,
                          avg_volume=5e6, beta=1.0, short_interest=1e6)
    u = pt.Universe([probe])
    u.extend(pt.Universe.random(4, seed=4))
    return u


def _run(days: int, drop: tuple[str, ...] = (), **over):
    fields = {k: v for k, v in {**BASE, **over}.items() if k not in drop}
    scen = pt.Scenario.from_json(json.dumps(
        {"schema": 1, "label": "probe", "days": days,
         "path": [dict(day=i, **fields) for i in range(days)]}))
    e = pt.Engine(seed=3, universe=_universe(), model="pt-v6")
    for i in range(days):
        scen.apply(e, i)
        e.run_days(1, first_day=i)
    return e


def _fundamental(days: int, drop: tuple[str, ...] = (), **over) -> float:
    e = _run(days, drop, **over)
    t = pa.table(e.truth())
    one = pc.filter(t, pc.equal(t["instrument_id"], 0))
    one = pc.filter(one, pc.equal(one["tick"], pc.max(one["tick"]))).to_pylist()
    return one[-1]["fundamental_value"]


def test_qe_pe_boost_is_a_direct_multiple() -> None:
    """The one channel that bypasses the discount rate, and it is linear.

    This is what makes it the only field able to express a re-rating: a 34%
    fall in a market is a multiple compressing, and nothing else in the macro
    surface can compress a multiple.
    """
    base = _fundamental(2)
    for boost in (-0.30, -0.10, 0.10, 0.30):
        got = _fundamental(2, qe_pe_boost=boost) / base - 1
        assert got == pytest.approx(boost, abs=0.01), (
            f"qe_pe_boost={boost} moved fundamental value by {got:+.3f}, "
            "expected one for one. It is documented as a direct multiple and "
            "notebook 09's whole argument rests on that being true."
        )


def test_nothing_rate_borne_transmits_before_the_first_meeting() -> None:
    """The trap, pinned at its boundary.

    Bit-identical through the meeting, materially different the day after. If
    this ever becomes gradual the docs and the notebook both need rewriting,
    because both state the boundary as sharp.
    """
    for days in (2, 30, FIRST_MEETING_DAY):
        lo = _fundamental(days, drop=("corporate_bond_yield",),
                          federal_funds_rate=0.005)
        hi = _fundamental(days, drop=("corporate_bond_yield",),
                          federal_funds_rate=0.10)
        assert lo == hi, (
            f"a 0.5% and a 10% policy rate gave different valuations after "
            f"{days} days, before the first meeting at day {FIRST_MEETING_DAY}"
        )

    after = FIRST_MEETING_DAY + 1
    lo = _fundamental(after, drop=("corporate_bond_yield",),
                      federal_funds_rate=0.005)
    hi = _fundamental(after, drop=("corporate_bond_yield",),
                      federal_funds_rate=0.10)
    assert hi < lo * 0.97, (
        f"the policy rate still had no effect on day {after}, one day after "
        f"the first meeting. Either the meeting moved or the rate channel "
        f"broke; {lo:.3f} against {hi:.3f}"
    )


def test_pinning_the_yield_severs_every_field_upstream_of_it() -> None:
    """Why the two-day probe's zeros looked plausible.

    `corporate_bond_yield` is the single discount channel. Pin it and the
    policy rate, the VIX and inflation are all cut off from valuation at once,
    however long the run. Notebook 09 pins it deliberately, to real data, so
    this is the regime that notebook actually operates in.
    """
    days = 90  # well past the first meeting, so a live channel would show
    base = _fundamental(days)
    for field, value in (("federal_funds_rate", 0.10),
                         ("vix", 82.69),
                         ("inflation_rate", 0.09)):
        assert _fundamental(days, **{field: value}) == base, (
            f"{field} moved fundamental value while corporate_bond_yield was "
            "pinned. It is documented as reaching valuation only through that "
            "yield, so either a new channel was added or the docs are stale."
        )


def test_most_of_the_surface_transmits_the_day_you_move_it() -> None:
    """The counterweight to the meeting trap, and the reason it needs one.

    "Nothing transmits before day 45" is false and was believed anyway, by the
    author of notebook 09 and by a documentation heading that has since been
    narrowed. Only the two fields that work by steering the corporate bond
    yield wait for a meeting. The rest act immediately, and this pins that so
    the over-broad reading cannot quietly become true.
    """
    shock_day, read_day = 5, 25

    def prices(field=None, value=None):
        fields = dict(BASE)
        fields.setdefault("inflation_rate", 0.02)
        fields.setdefault("fear_greed_index", 50.0)
        path = []
        for i in range(read_day):
            row = dict(fields, day=i)
            if field is not None and i >= shock_day:
                row[field] = value
            path.append(row)
        scen = pt.Scenario.from_json(json.dumps(
            {"schema": 1, "label": "t", "days": read_day, "path": path}))
        e = pt.Engine(seed=9, universe=_universe(), model="pt-v6")
        for i in range(read_day):
            scen.apply(e, i)
            e.run_days(1, first_day=i)
        return list(e.prices())

    base = prices()
    for field, value in (("vix", 60.0),
                         ("qe_pe_boost", -0.30),
                         ("corporate_bond_yield", 0.114)):
        got = prices(field, value)
        assert got != base, (
            f"{field} moved on day {shock_day} and changed nothing by day "
            f"{read_day}. It is documented as transmitting immediately, so "
            "either it regressed or the docs are now wrong."
        )


@pytest.mark.parametrize("days", [30, 120])
def test_fear_greed_index_is_inert(days: int) -> None:
    """A field that is settable, validated, reported, and read by nothing.

    Pinned anywhere across its whole documented range it produces bit-identical
    prices. No pricing code in the engine reads it; it is computed as a
    diagnostic and exposed as though it were a lever.

    This is pinned as CURRENT BEHAVIOUR, not as desirable behaviour. Wiring
    sentiment to price would be a new mechanism needing calibration and an era
    boundary. Until then the honest thing is that the inertness is deliberate
    and tested rather than an accident nobody noticed.
    """
    lo = list(_run(days, fear_greed_index=0.0).prices())
    hi = list(_run(days, fear_greed_index=100.0).prices())
    assert lo == hi, (
        "fear_greed_index changed prices. That is arguably an improvement, "
        "but it contradicts the documented behaviour and notebook 09, both of "
        "which state it is inert. Update them together."
    )


def test_endogenous_inflation_never_reaches_its_own_crisis_regime() -> None:
    """A whole regime exists, is correct, and a default run cannot get to it.

    The central bank has a crisis cadence that pulls the next meeting in to
    21-30 days when a decision leaves it more than 2pp behind an inflation
    rate above 4%. It is not dead code: it fires in 22.0% of the 11,898 cases
    in the JS oracle corpus, and `economy_parity` pins it bit for bit.

    It is nonetheless unreachable from a default run, because the inflation
    process stalls well below the trigger. Measured over five seeds and five
    years, endogenous inflation peaks at 4.06% to 4.11% against a hard clamp
    of 6.0% and a real US CPI that reached 9.1% in June 2022. So the ceiling
    is not the clamp, it is the dynamics.

    WHICH part of the dynamics is measured, not guessed, because the obvious
    guess is wrong. `inflation_mean_rev_coeff` is 0.55 per month, which looks
    like it should pin inflation to the 2% target and implies an AR(1) of
    0.45. It does not: over eight seeds and five years the monthly series has
    AR(1) **+0.936**, against **+0.894** for real US CPI year-on-year over
    2020-21. The model's inflation is if anything slightly MORE persistent
    than the real thing, because the drivers around the reversion term carry
    their own persistence.

    The gap is dispersion, not persistence. The model's monthly inflation has
    sd 1.23 around a mean of 1.99% and spans -0.12% to 4.14%; real CPI spanned
    0.1% to 7.0% across 2020-21 alone. Reaching 9% from here is a 4.6 sigma
    excursion, so it never happens. A calibrated change should therefore go
    after the driver and shock magnitudes, and leave the mean reversion alone.

    This is recorded as a REALISM GAP rather than a defect. A 2022-style
    inflation shock has to be driven through a scenario today; the model will
    not produce one on its own. Raising it is an era boundary and belongs to
    a calibrated change, not to this test.

    The test fails if inflation ever comfortably clears the trigger, which
    would mean the gap has been closed and this docstring is stale.
    """
    import pyarrow as pa

    peaks = []
    for seed in (101, 102, 103):
        e = pt.Engine(seed=seed, universe=pt.Universe.random(12, seed=7),
                      model="pt-v6")
        e.run_days(756)  # three years
        peaks.append(max(r["inflation_rate"]
                         for r in pa.table(e.macro_table()).to_pylist()))

    assert max(peaks) < 0.05, (
        f"endogenous inflation reached {max(peaks):.2%}, clearing the 4% "
        "crisis trigger. If that is deliberate, the realism gap recorded here "
        "is closed and this test should be replaced by one asserting the "
        "crisis cadence now fires in default runs."
    )
