"""Ranking across seeds, and the two ways a ranking lies.

One seed ranks the seed. An aggregate median ranks the median, which is not
the same as ranking the agents -- mean-reversion's +0.064 pooled capture
against random's +0.019 is a 6-6 split when you pair it. Both failures are
measured here rather than described. (Before the 2026-08 era boundary the
demonstration pair was momentum against mean-reversion, +0.519 against
+0.242; the era re-roll moved which pair shows it, not the phenomenon.)
"""

import pytest

import pretium
from pretium.baselines import BuyAndHold, Momentum, reference_agents
from pretium.ranking import AgentRecord, _sign_test


UNIVERSE = pretium.Universe.random(20, seed=11)

#: The configuration the module's documented findings were measured on.
#: Deliberately larger than a unit test wants -- about ten seconds, built once
#: for the whole module -- because at twenty names and three days NOTHING in
#: the reference set separates, and a test suite that only ever ran the cheap
#: configuration could not tell a working sign test from one wired to return
#: "no difference".
HEADLINE = pretium.Universe.random(30, seed=11)


def make():
    return reference_agents(seed=3)


@pytest.fixture(scope="module")
def ranking():
    return pretium.rank(make, seeds=range(12), universe=HEADLINE, days=10,
                        workers=4)


# --------------------------------------------------------------------------
# The trap the API exists to close
# --------------------------------------------------------------------------


def test_passing_built_agents_is_refused():
    """Instances would carry seed 0's history into seed 1, invisibly.

    Momentum keeps a rolling window. Reusing it across seeds scores a
    momentum agent that has already seen a different market -- and the
    numbers look completely normal, which is what makes refusing better than
    documenting.
    """
    with pytest.raises(pretium.ValidationError, match="factory"):
        pretium.rank(reference_agents(seed=3), seeds=range(3),
                     universe=UNIVERSE, days=1)


def test_the_factory_is_called_once_per_seed():
    calls = []

    def counting():
        calls.append(1)
        return {"buy_and_hold": BuyAndHold(), "momentum": Momentum()}

    pretium.rank(counting, seeds=range(4), universe=UNIVERSE, days=1)
    assert len(calls) == 4


def test_agents_really_are_fresh_each_seed():
    """Not just that the factory ran -- that its output was the thing used.

    A factory called four times whose results were discarded in favour of one
    cached mapping would pass the test above. This one holds every agent the
    ranking actually scored and asserts they are four distinct objects.
    """
    seen = []

    class Recording(BuyAndHold):
        def act(self, obs):
            # The OBJECT, not its id. CPython reuses the id of a freed object,
            # so collecting ids reported three distinct agents out of four --
            # a false failure that would have read as a real one.
            if self not in seen:
                seen.append(self)
            return super().act(obs)

    pretium.rank(lambda: {"a": Recording()}, seeds=range(4),
                 universe=UNIVERSE, days=1)
    assert len(seen) == 4


# --------------------------------------------------------------------------
# Determinism, including under threads
# --------------------------------------------------------------------------


def test_threading_does_not_change_the_answer():  # noqa: D401
    """The result that would make the whole module untrustworthy if it failed.

    Results are gathered in seed order rather than completion order, so a
    median over an even count picks the same element every run. Scheduling
    must not reach the numbers.
    """
    serial = pretium.rank(make, seeds=range(6), universe=UNIVERSE, days=2)
    threaded = pretium.rank(make, seeds=range(6), universe=UNIVERSE, days=2,
                            workers=4)
    assert serial.as_dict() == threaded.as_dict()


def test_a_ranking_is_reproducible():
    first = pretium.rank(make, seeds=range(4), universe=UNIVERSE, days=2)
    again = pretium.rank(make, seeds=range(4), universe=UNIVERSE, days=2)
    assert again.as_dict() == first.as_dict()


def test_repeated_seeds_are_refused():
    # The same market twice would weight it double in every median, quietly.
    with pytest.raises(pretium.ValidationError, match="distinct"):
        pretium.rank(make, seeds=[1, 2, 1], universe=UNIVERSE, days=1)


def test_no_seeds_is_refused():
    with pytest.raises(pretium.ValidationError):
        pretium.rank(make, seeds=[], universe=UNIVERSE, days=1)


def test_workers_must_be_positive():
    with pytest.raises(pretium.ValidationError):
        pretium.rank(make, seeds=range(2), universe=UNIVERSE, days=1, workers=0)


# --------------------------------------------------------------------------
# The sign test, checked against arithmetic rather than against itself
# --------------------------------------------------------------------------


def test_sign_test_matches_the_binomial_by_hand():
    # 12-0 out of 12: two tails of 1/4096 each.
    assert _sign_test(12, 0) == pytest.approx(2.0 / 4096)
    assert _sign_test(0, 12) == pytest.approx(2.0 / 4096)
    # An even split is as unsurprising as it gets.
    assert _sign_test(6, 6) == pytest.approx(1.0)
    # 5-1 out of 6: 2 * (C(6,0) + C(6,1)) / 64 = 2 * 7/64.
    assert _sign_test(5, 1) == pytest.approx(2.0 * 7 / 64)
    # 1-0 proves nothing: 2 * 1/2 = 1.0, capped.
    assert _sign_test(1, 0) == pytest.approx(1.0)


def test_sign_test_is_none_with_nothing_to_compare():
    # Not 1.0, and not 0.0. No paired seeds is "no answer", and reporting a
    # number would let a caller act on a comparison that never happened.
    assert _sign_test(0, 0) is None


def test_sign_test_never_exceeds_one():
    for a in range(6):
        for b in range(6):
            p = _sign_test(a, b)
            if p is not None:
                assert 0.0 < p <= 1.0


# --------------------------------------------------------------------------
# Separation says what the median cannot
# --------------------------------------------------------------------------


def test_a_real_difference_separates_and_a_median_gap_may_not(ranking):
    """The finding this module was built around.

    Momentum ranks above random on pooled capture AND wins 11 of 12 paired
    seeds, p = 0.0063. Mean-reversion also ranks above random on the pooled
    aggregate (+0.064 against +0.019), and THAT ordering is not established
    at all -- 6-6, p = 1.0. It wins bigger, not more often.

    Re-measured three times: once when a stepped day stopped re-opening the
    market at every step (which had inflated momentum's edge through the
    `previous_close` reset), again at the 2026-08 era boundary, and again at
    the pt-v3 boundary -- which INVERTED the leaderboard and so swapped
    which pair is which.

    Under pt-v1 momentum was the strong pair, because the model's return
    autocorrelation was +0.243 and momentum was mechanically profitable
    against it. pt-v3 takes that to +0.084, and the ranking rearranges to
    match: mean-reversion now dominates on pooled capture (+0.785 against
    momentum's +0.087) and wins 12 of 12 paired seeds against random,
    p = 0.0005, while momentum -- still ordered above random on the pooled
    aggregate -- wins only 9 of 12, p = 0.146, and is not established at all.

    That inversion is the calibration landing rather than a regression: with
    returns no longer autocorrelated, the exploitable process is the
    mispricing one, which is much closer to real equities, where momentum is
    a weak and contested effect rather than a free lunch.

    Asserted as the CONTRAST rather than as two fixed p-values, because the
    counts belong to these seeds. What must hold is that the sign test can
    tell the two situations apart at all.
    """
    strong = ranking.separation("mean_reversion", "random")
    weak = ranking.separation("momentum", "random")
    assert strong["p_value"] < 0.05, (
        f"momentum did not separate from random: {strong}"
    )
    assert not weak["decisive"]
    assert weak["p_value"] > strong["p_value"], (
        "the sign test gave mean-reversion-vs-random at least as much "
        "confidence as momentum-vs-random; it is not discriminating"
    )
    # And the ordering the aggregate suggests is the one the sign test
    # refuses to confirm -- which is the entire point of reporting both.
    table = {r.name: r.pooled_capture for r in ranking.table()}
    assert table["mean_reversion"] > table["random"]


def test_separation_is_symmetric_in_its_verdict(ranking):
    forward = ranking.separation("momentum", "buy_and_hold")
    backward = ranking.separation("buy_and_hold", "momentum")
    assert forward["wins_a"] == backward["wins_b"]
    assert forward["p_value"] == backward["p_value"]
    assert forward["decisive"] == backward["decisive"]


def test_every_pairing_accounts_for_every_seed(ranking):
    for other in ("mean_reversion", "random", "buy_and_hold"):
        result = ranking.separation("momentum", other)
        assert (result["wins_a"] + result["wins_b"] + result["ties"]
                == len(ranking.seeds))


def test_separation_refuses_an_unknown_agent(ranking):
    with pytest.raises(pretium.ValidationError, match="not in this ranking"):
        ranking.separation("momentum", "nonesuch")


# --------------------------------------------------------------------------
# The table, and what it does with what it cannot measure
# --------------------------------------------------------------------------


def test_the_table_is_ordered_by_pooled_capture(ranking):
    pooled = [r.pooled_capture for r in ranking.table()]
    assert pooled == sorted(pooled, reverse=True)


def test_the_table_can_still_be_asked_for_the_median(ranking):
    medians = [r.median_capture for r in ranking.table(by="median_capture")]
    assert medians == sorted(medians, reverse=True)


def test_the_oracle_is_not_a_contender(ranking):
    # It is the denominator. Ranking it against the agents it normalises
    # would put it first by construction and mean nothing.
    assert "oracle" not in ranking.records


def test_ranking_by_an_unknown_key_is_refused(ranking):
    with pytest.raises(pretium.ValidationError):
        ranking.table(by="sharpe")


def test_an_absent_reference_leaves_capture_unmeasurable_and_says_so():
    """No oracle means no capture, and the table must not pretend otherwise.

    It falls back to median P&L so the ranking still ranks, and every capture
    reads None rather than 0.0 -- which would have sorted a lossmaking agent
    above one that lost more.
    """
    ranking = pretium.rank(make, seeds=range(4), universe=UNIVERSE, days=2,
                           oracle="not_present")
    assert ranking.unmeasurable == [0, 1, 2, 3]
    assert all(r.median_capture is None for r in ranking.records.values())
    assert all(r.pooled_capture is None for r in ranking.records.values())
    assert "unmeasurable" in ranking.report()
    ordered = ranking.table()
    assert [r.median_pnl for r in ordered] == sorted(
        (r.median_pnl for r in ordered), reverse=True)


def test_wins_are_counted_once_per_seed(ranking):
    assert sum(r.wins for r in ranking.records.values()) == len(ranking.seeds)


def test_every_record_has_one_entry_per_seed(ranking):
    for record in ranking.records.values():
        assert len(record.pnls) == len(ranking.seeds)
        assert len(record.captures) == len(ranking.seeds)


# --------------------------------------------------------------------------
# Provenance and shape
# --------------------------------------------------------------------------


def test_a_ranking_names_the_market_it_came_from(ranking):
    assert ranking.universe_fingerprint == HEADLINE.fingerprint


def test_as_dict_is_json_shaped(ranking):
    import json

    payload = ranking.as_dict()
    assert json.loads(json.dumps(payload)) == payload


def test_the_report_mentions_every_agent(ranking):
    text = ranking.report()
    for name in ranking.records:
        assert name in text


# --------------------------------------------------------------------------
# Non-vacuity: the numbers must actually vary
# --------------------------------------------------------------------------


def test_the_seeds_really_are_different_markets(ranking):
    """If every seed produced the same P&L, every test above would pass while
    measuring one market twelve times."""
    for record in ranking.records.values():
        if record.name == "buy_and_hold":
            assert len(set(record.pnls)) > 1, "every seed paid the same"


# --------------------------------------------------------------------------
# Pooling, checked by arithmetic on a constructed record
# --------------------------------------------------------------------------


def _record(pnls, reference_pnls):
    record = AgentRecord("a", list(range(len(pnls))), list(reference_pnls))
    record.pnls = list(pnls)
    record.captures = [p / r if r > 0 else None
                       for p, r in zip(pnls, reference_pnls)]
    return record


def test_pooling_survives_a_seed_the_median_cannot():
    """The defect that made pooling the headline, reproduced exactly.

    Two markets. In the first the reference earned 100 and the agent 10 -- a
    capture of 0.1. In the second the reference earned almost nothing, 1, and
    the agent 14, which is a capture of 14.0 and a true statement about that
    seed. A median of the two ratios is 7.05, and it would rank this agent
    above one that captured 0.9 in both markets.

    Pooled, it is 24/101 = 0.238: the second market weighs what it was worth.
    """
    record = _record([10.0, 14.0], [100.0, 1.0])
    assert record.median_capture == pytest.approx(7.05)
    assert record.pooled_capture == pytest.approx(24.0 / 101.0)

    steady = _record([90.0, 0.9], [100.0, 1.0])
    assert steady.pooled_capture > record.pooled_capture
    assert steady.median_capture < record.median_capture, (
        "the median did not misrank these; the demonstration is vacuous"
    )


def test_pooling_excludes_seeds_where_the_reference_lost():
    # A negative denominator in the sum would drag the total toward zero and
    # could flip the sign of a perfectly good agent.
    record = _record([10.0, 50.0], [100.0, -80.0])
    assert record.pooled_capture == pytest.approx(0.1)


def test_pooling_is_unmeasurable_when_no_seed_had_opportunity():
    record = _record([10.0, 50.0], [0.0, -80.0])
    assert record.pooled_capture is None


def test_pooling_equals_the_ratio_when_every_seed_is_identical():
    # The sanity anchor: with a flat denominator, pooling and averaging agree,
    # so pooling is not quietly changing the answer in the easy case.
    record = _record([25.0, 75.0], [100.0, 100.0])
    assert record.pooled_capture == pytest.approx(0.5)
    assert record.median_capture == pytest.approx(0.5)
