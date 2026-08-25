"""Every gap must be reachable through `check`, not only through prose.

`pretium.envelope` exists so that a user does not have to remember a
documentation page to find out whether their question is one this simulator
can answer. A gap that appears in `GAPS` and in `certified()` but that no
argument to `check()` can ever surface defeats that: the programmatic
interface says `inside=True` while the page says otherwise.

That is not hypothetical. `macro-range` was added on 2026-08-25 recording that
the endogenous economy cannot reach its own crisis regimes, and for one
release it was invisible to `check`, because it names no statistics and there
was no flag for it. Someone asking "can I study an inflation regime here"
would have been told yes.

So the reachability is tested rather than remembered. A new gap now fails this
file until it is wired to something a caller can ask for.
"""

from __future__ import annotations

import pytest

from pretium import envelope as env

#: How a caller reaches each gap. Every entry is a kwargs dict for `check`
#: that MUST surface that gap, so adding a gap means adding the route to it.
ROUTES: dict[str, dict] = {
    "horizon": dict(horizon_days=504),
    "thin-tails": dict(horizon_days=504, statistics=("excess_kurtosis",)),
    "volume-change": dict(horizon_days=252, statistics=("volume_change_acf1",)),
    "decay-shape": dict(horizon_days=252, statistics=("abs_return_acf20",)),
    "scenario-magnitude": dict(horizon_days=252, scenario_magnitude=True),
    "roster-concentration": dict(horizon_days=252, sector_concentrated=True),
    "macro-range": dict(horizon_days=252, macro_regime=True),
}


def test_every_gap_has_a_route() -> None:
    """The guard on the table below, so a new gap cannot be forgotten here."""
    assert sorted(ROUTES) == sorted(g.id for g in env.GAPS), (
        "GAPS and ROUTES disagree. A gap with no route is invisible to "
        "`check`, which is the interface users actually call; a route with no "
        "gap is stale. Add the missing one rather than editing this list to "
        "match."
    )


@pytest.mark.parametrize("gap_id", sorted(ROUTES))
def test_the_route_surfaces_the_gap(gap_id: str) -> None:
    verdict = env.check(**ROUTES[gap_id])
    surfaced = [g.id for g in verdict.gaps]
    assert gap_id in surfaced, (
        f"check({ROUTES[gap_id]}) did not surface {gap_id!r}; it returned "
        f"{surfaced or 'no gaps at all'}. The gap is documented and "
        "unreachable, so a caller asking exactly the wrong question is told "
        "they are inside the envelope."
    )
    assert not verdict.inside, (
        f"{gap_id!r} was surfaced but the verdict still reads inside=True"
    )


@pytest.mark.parametrize("gap_id", sorted(ROUTES))
def test_the_reason_carries_a_measurement(gap_id: str) -> None:
    """A gap nobody can act on is trivia, and so is a reason with no number.

    Every refusal should tell the caller what was measured, not merely that
    something is wrong, because the next question is always "how wrong".
    """
    verdict = env.check(**ROUTES[gap_id])
    joined = " ".join(verdict.reasons)
    assert any(ch.isdigit() for ch in joined), (
        f"the reason given for {gap_id!r} contains no figure at all: {joined!r}"
    )


def test_a_plain_question_is_inside() -> None:
    """The converse, so the file cannot pass by refusing everything."""
    verdict = env.check(horizon_days=252)
    assert verdict.inside, verdict.reasons
    assert not verdict.gaps
