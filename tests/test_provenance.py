"""Every result says which market it came from.

A number without its market is not a finding. "Momentum returned 40%",
"kurtosis is +4.8", "the shortfall was 16 bps" are each only checkable against
the roster they were measured on — and a seed does not identify a roster,
because the same seed over a different universe is a different market.

Tickers do not identify one either. They are generated positionally, so
`Universe.random(20, seed=7)` and `Universe.random(20, seed=99)` share every
name and share no earnings. The fingerprint is the only thing that does.
"""

import pytest

import pretium
from pretium.baselines import BuyAndHold

UNIVERSE = pretium.Universe.random(20, seed=7)
#: Same size, same tickers, different fundamentals. The pair a name-based
#: check cannot tell apart.
DECOY = pretium.Universe.random(20, seed=99)


class Buyer:
    def act(self, obs):
        ticker = obs.tickers[0]
        return {ticker: 0.005 * obs.avg_volume(ticker)} if obs.step == 0 else {}


def test_the_decoy_really_is_indistinguishable_by_name():
    # Otherwise every test below is checking something easier than it looks.
    assert [i.ticker for i in DECOY] == [i.ticker for i in UNIVERSE]
    assert [i.eps for i in DECOY] != [i.eps for i in UNIVERSE]


def test_a_scorecard_names_its_market():
    scores = pretium.evaluate({"hold": BuyAndHold()}, seed=2026,
                              universe=UNIVERSE, days=2)
    card = scores["hold"]
    assert card.universe_fingerprint == UNIVERSE.fingerprint
    assert card.seed == 2026
    assert "universe_fingerprint" in card.as_dict()


def test_facts_name_their_market():
    facts = pretium.facts.measure(seed=3, universe=UNIVERSE, days=60)
    assert facts["universe_fingerprint"] == UNIVERSE.fingerprint
    assert facts["seed"] == 3


def test_an_execution_names_its_market():
    execution = pretium.tca.analyse(Buyer(), seed=2026, universe=UNIVERSE,
                                    days=1)
    assert execution.universe_fingerprint == UNIVERSE.fingerprint
    assert "universe_fingerprint" in execution.as_dict()


def test_the_three_agree_on_one_universe():
    # One definition, three call sites. Two places computing "the universe's
    # identity" slightly differently would be worse than one computing it
    # wrongly, because only one of those is findable.
    scores = pretium.evaluate({"hold": BuyAndHold()}, seed=1,
                              universe=UNIVERSE, days=2)
    facts = pretium.facts.measure(seed=1, universe=UNIVERSE, days=60)
    execution = pretium.tca.analyse(Buyer(), seed=1, universe=UNIVERSE, days=1)
    assert {
        scores["hold"].universe_fingerprint,
        facts["universe_fingerprint"],
        execution.universe_fingerprint,
    } == {UNIVERSE.fingerprint}


@pytest.mark.parametrize("produce", [
    lambda u: pretium.evaluate({"hold": BuyAndHold()}, seed=2026, universe=u,
                               days=2)["hold"].universe_fingerprint,
    lambda u: pretium.facts.measure(seed=3, universe=u, days=60)[
        "universe_fingerprint"],
    lambda u: pretium.tca.analyse(Buyer(), seed=2026, universe=u,
                                  days=1).universe_fingerprint,
])
def test_each_result_distinguishes_the_decoy(produce):
    # The check that matters. If the stamp were the ticker list, or the roster
    # length, or anything else a decoy shares, this would fail.
    assert produce(UNIVERSE) != produce(DECOY)


def test_a_plain_list_is_fingerprinted_the_same_as_a_universe():
    # These functions accept any sequence of instruments, so the stamp must
    # not depend on the caller having wrapped it in a Universe.
    plain = list(UNIVERSE)
    facts = pretium.facts.measure(seed=3, universe=plain, days=60)
    assert facts["universe_fingerprint"] == UNIVERSE.fingerprint


def test_a_sweep_row_names_its_market():
    """The case where provenance matters most.

    A hundred rows keyed only by seed look interchangeable. Two sweeps over
    different rosters merge into one table that is wrong in no visible way --
    same seeds, same tickers, same shape, different markets.
    """
    rows = pretium.run_many(seeds=[1, 2, 3], universe=UNIVERSE, days=1,
                            ticks=40, collect="summary")
    assert all(r["universe_fingerprint"] == UNIVERSE.fingerprint for r in rows)


def test_sweep_rows_distinguish_the_decoy_where_tickers_do_not():
    ours = pretium.run_many(seeds=[1], universe=UNIVERSE, days=1, ticks=40,
                            collect="summary")[0]
    theirs = pretium.run_many(seeds=[1], universe=DECOY, days=1, ticks=40,
                              collect="summary")[0]
    # The row already carried `tickers`, and they are equal — which is exactly
    # why that field could never have served as identity.
    assert ours["tickers"] == theirs["tickers"]
    assert ours["universe_fingerprint"] != theirs["universe_fingerprint"]


def test_the_stamp_survives_the_worker_boundary():
    # Workers rebuild the universe from JSON, so a fingerprint computed inside
    # a worker could in principle differ from one computed outside. It is
    # hashed once in the parent and carried, and this asserts the parallel and
    # serial paths agree.
    serial = pretium.run_many(seeds=[1, 2, 3, 4], universe=UNIVERSE, days=1,
                              ticks=40, workers=1, collect="summary")
    threaded = pretium.run_many(seeds=[1, 2, 3, 4], universe=UNIVERSE, days=1,
                                ticks=40, workers=4, collect="summary")
    assert [r["universe_fingerprint"] for r in serial] == \
           [r["universe_fingerprint"] for r in threaded]
    assert serial[0]["universe_fingerprint"] == UNIVERSE.fingerprint
