"""This market has never had news, and its news machinery has never fired.

News is caller-supplied: `SessionRequest.news` is a slice the engine never
filled, and the only populated path is `pretium.replay`, which feeds a
recorded log's news back in. So `company_news` contributed exactly zero in
every simulation the panel measures, 0 nonzero day-cells out of 30240 at
every pinned VIX (CALIBRATION-FOLLOWUPS.md §85), and `news_sector_weight`,
`news_market_weight` and the two peer weights could not move any certified
statistic.

That left the jump process as the only idiosyncratic shock, and a jump lands
on one name and reaches no other. Real earnings surprises transfer. What is
pinned here is that the mechanism ships inert, that switching it on makes
`company_news` non-zero, and that it reaches PEERS when peer transfer is on,
which is the point of routing it through the news pipeline rather than
inventing a second jump.
"""

from __future__ import annotations

import pyarrow as pa
import pytest

import pretium as pt


def _news_column(model, days: int = 20, n: int = 25) -> list[float]:
    e = pt.Engine(seed=17, universe=pt.Universe.random(n, seed=9), model=model)
    e.run_days(days)
    return pa.table(e.truth())["company_news"].to_pylist()


@pytest.mark.parametrize("preset", ["pt-v3", "pt-v10", "pt-v11"])
def test_it_ships_inert(preset: str) -> None:
    """At intensity zero the trajectory is the shipped one, bit for bit."""
    base = pt.Engine(seed=17, universe=pt.Universe.random(25, seed=9), model=preset)
    base.run_days(12)
    before = list(base.prices())

    m = pt.ModelParams.from_preset(preset, endogenous_news_intensity=0.0)
    after = pt.Engine(seed=17, universe=pt.Universe.random(25, seed=9), model=m)
    after.run_days(12)
    assert list(after.prices()) == before
    assert m.fingerprint == preset


def test_company_news_is_zero_on_every_shipped_preset() -> None:
    """The gap itself, asserted so it cannot be closed by accident.

    Every shipped preset leaves this channel silent. If a preset ever
    switches endogenous news on, this test must be updated deliberately
    rather than discovered failing.
    """
    for preset in ("pt-v3", "pt-v10", "pt-v11"):
        assert all(v == 0.0 for v in _news_column(preset)), preset


def test_switching_it_on_makes_company_news_fire() -> None:
    m = pt.ModelParams.from_preset(
        "pt-v10", endogenous_news_intensity=0.15, endogenous_news_sigma=0.04)
    col = _news_column(m)
    fired = [v for v in col if v != 0.0]
    assert fired, "endogenous news produced no company_news at all"


def test_it_reaches_peers_when_transfer_is_on() -> None:
    """The reason this routes through the news pipeline and is not a jump.

    A jump lands on one name. News with `news_peer_weight` above zero
    reaches other members of the announcer's sector, which is an endogenous
    contagion route this market has never had: sector co-movement today is a
    per-tick sector draw plus market beta and nothing that travels between
    members.
    """
    common = dict(endogenous_news_intensity=0.15, endogenous_news_sigma=0.04)
    off = pt.ModelParams.from_preset("pt-v10", **common)
    on = pt.ModelParams.from_preset("pt-v10", news_peer_weight=0.5,
                                    news_peer_weight_down=0.5, **common)
    assert _news_column(on) != _news_column(off)
    # And peer transfer alone, with no endogenous news, still changes
    # nothing: there is no announcer for it to transfer from.
    quiet = pt.ModelParams.from_preset("pt-v10", news_peer_weight=0.5,
                                       news_peer_weight_down=0.5)
    assert _news_column(quiet) == _news_column("pt-v10")


def test_neither_dial_does_anything_alone() -> None:
    """They are a pair, and each is inert without the other.

    Intensity alone generates events whose impact is `sigma * z` with sigma
    at its shipped 0.0, so every event carries zero and the news arm's
    truthy-or drops it. Sigma alone has no event to attach to. Worth pinning
    because "the dial is wired up" and "the dial does something" are
    different claims and only the second one matters.
    """
    base = _news_column("pt-v10")
    assert _news_column(
        pt.ModelParams.from_preset("pt-v10", endogenous_news_intensity=0.9)) == base
    assert _news_column(
        pt.ModelParams.from_preset("pt-v10", endogenous_news_sigma=0.05)) == base
    assert _news_column(
        pt.ModelParams.from_preset("pt-v10", endogenous_news_intensity=0.9,
                                   endogenous_news_sigma=0.05)) != base


def test_both_dials_are_settable_and_fingerprinted() -> None:
    for name, value in (("endogenous_news_intensity", 0.1),
                        ("endogenous_news_sigma", 0.03)):
        assert name in pt.ModelParams.settable()
        m = pt.ModelParams.from_preset("pt-v10", **{name: value})
        assert m.fingerprint.startswith("custom-")
        assert m.to_dict()[name] == value
