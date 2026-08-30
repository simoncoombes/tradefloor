"""This market has never had news, and its news machinery has never fired.

News is caller-supplied: `SessionRequest.news` is a slice the engine never
filled, and the only populated path is `tradefloor.replay`, which feeds a
recorded log's news back in. So `company_news` contributed exactly zero in
every simulation the panel measures, 0 nonzero day-cells out of 30240 at
every pinned VIX (CALIBRATION-FOLLOWUPS.md §85), and `news_sector_weight`,
`news_market_weight` and the two peer weights could not move any certified
statistic.

That left the jump process as the only idiosyncratic shock, and a jump lands
on one name and reaches no other. Real earnings surprises transfer. What is
pinned here is that the mechanism ships inert, that switching it on makes
`company_news` non-zero, and that it reaches PEERS when peer transfer is on,
and routing it through the news pipeline buys that, rather than
inventing a second jump.
"""

from __future__ import annotations

import pyarrow as pa
import pytest

import tradefloor as pt


def _news_column(model, days: int = 20, n: int = 25) -> list[float]:
    e = pt.Engine(seed=17, universe=pt.Universe.random(n, seed=9), model=model)
    e.run_days(days)
    return pa.table(e.truth())["company_news"].to_pylist()


@pytest.mark.parametrize("preset", ["pt-v3", "pt-v10", "pt-v11"])
def test_restating_a_presets_own_news_is_inert(preset: str) -> None:
    """Re-stating a preset's own news settings changes nothing, bit for bit.

    Written against each preset's OWN values rather than zero, as
    every preset carried until pt-v11 switched news on. A test that
    hardcodes the old default stops testing inertness the day a preset
    adopts the dial.
    """
    own = pt.ModelParams.from_preset(preset).to_dict()
    base = pt.Engine(seed=17, universe=pt.Universe.random(25, seed=9), model=preset)
    base.run_days(12)
    before = list(base.prices())

    m = pt.ModelParams.from_preset(
        preset,
        endogenous_news_intensity=own["endogenous_news_intensity"],
        endogenous_news_sigma=own["endogenous_news_sigma"])
    after = pt.Engine(seed=17, universe=pt.Universe.random(25, seed=9), model=m)
    after.run_days(12)
    assert list(after.prices()) == before
    assert m.fingerprint == preset


@pytest.mark.parametrize("preset", ["pt-v3", "pt-v10"])
def test_it_is_still_off_on_every_preset_before_pt_v11(preset: str) -> None:
    """The mechanism ships inert, and the presets that predate it stay so."""
    m = pt.ModelParams.from_preset(preset, endogenous_news_intensity=0.0)
    assert m.fingerprint == preset
    assert m.to_dict()["endogenous_news_intensity"] == 0.0


def test_company_news_fires_on_pt_v11_and_on_nothing_before_it() -> None:
    """The gap, and the preset that closed it.

    This test used to assert the channel was silent on EVERY preset, with a
    note that a preset switching news on must update it deliberately rather
    than discover it failing. pt-v11 switched it on, so this is that
    deliberate update: the channel is live there and silent everywhere
    earlier, which keeps the older presets reproducing.
    """
    for preset in ("pt-v3", "pt-v10"):
        assert all(v == 0.0 for v in _news_column(preset)), preset
    assert any(v != 0.0 for v in _news_column("pt-v11"))


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
