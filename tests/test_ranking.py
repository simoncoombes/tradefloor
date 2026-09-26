"""Ranking across seeds, and the two ways a ranking lies.

One seed ranks the seed. An aggregate median ranks the median, which is not
the same as ranking the agents -- mean-reversion's +0.064 pooled capture
against random's +0.019 is a 6-6 split when you pair it. Both failures are
measured here rather than described. (Before the 2026-08 era boundary the
demonstration pair was momentum against mean-reversion, +0.519 against
+0.242; the era re-roll moved which pair shows it, not the phenomenon.)
"""

import pytest

import tradefloor
from tradefloor.baselines import BuyAndHold, Momentum, reference_agents
from tradefloor.ranking import AgentRecord, _sign_test


UNIVERSE = tradefloor.Universe.random(20, seed=11)

#: The configuration the module's documented findings were measured on.
#: Deliberately larger than a unit test wants -- about ten seconds, built once
#: for the whole module -- because at twenty names and three days NOTHING in
#: the reference set separates, and a test suite that only ever ran the cheap
#: configuration could not tell a working sign test from one wired to return
#: "no difference".
HEADLINE = tradefloor.Universe.random(30, seed=11)


def make():
    return reference_agents(seed=3)


@pytest.fixture(scope="module")
def ranking():
    return tradefloor.rank(make, seeds=range(12), universe=HEADLINE, days=10,
                        workers=4)


#: The preset the capture tests run on. pt-v20, the default, reports no
#: capture (`baselines.ORACLE_NOT_A_CEILING`), so what a capture table does
#: is checked where the Oracle is a ceiling: pt-v19, the last such preset.
CEILING_PRESET = "pt-v19"


@pytest.fixture(scope="module")
def ceiling_ranking():
    # Smaller than `ranking`: these tests read the table's mechanics, not
    # a separation, and six seeds of five days is enough to order four
    # agents on distinct captures.
    return tradefloor.rank(make, seeds=range(6), universe=UNIVERSE, days=5,
                           workers=4, model=CEILING_PRESET)


# --------------------------------------------------------------------------
# The trap the API exists to close
# --------------------------------------------------------------------------


def test_passing_built_agents_is_refused():
    """Instances would carry seed 0's history into seed 1, invisibly.

    Momentum keeps a rolling window. Reusing it across seeds scores a
    momentum agent that has already seen a different market -- and the
    numbers look completely normal, which makes refusing better than
    documenting.
    """
    with pytest.raises(tradefloor.ValidationError, match="factory"):
        tradefloor.rank(reference_agents(seed=3), seeds=range(3),
                     universe=UNIVERSE, days=1)


def test_the_factory_is_called_once_per_seed():
    calls = []

    def counting():
        calls.append(1)
        return {"buy_and_hold": BuyAndHold(), "momentum": Momentum()}

    tradefloor.rank(counting, seeds=range(4), universe=UNIVERSE, days=1)
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

    tradefloor.rank(lambda: {"a": Recording()}, seeds=range(4),
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
    serial = tradefloor.rank(make, seeds=range(6), universe=UNIVERSE, days=2)
    threaded = tradefloor.rank(make, seeds=range(6), universe=UNIVERSE, days=2,
                            workers=4)
    assert serial.as_dict() == threaded.as_dict()


def test_a_ranking_is_reproducible():
    first = tradefloor.rank(make, seeds=range(4), universe=UNIVERSE, days=2)
    again = tradefloor.rank(make, seeds=range(4), universe=UNIVERSE, days=2)
    assert again.as_dict() == first.as_dict()


def test_repeated_seeds_are_refused():
    # The same market twice would weight it double in every median, quietly.
    with pytest.raises(tradefloor.ValidationError, match="distinct"):
        tradefloor.rank(make, seeds=[1, 2, 1], universe=UNIVERSE, days=1)


def test_no_seeds_is_refused():
    with pytest.raises(tradefloor.ValidationError):
        tradefloor.rank(make, seeds=[], universe=UNIVERSE, days=1)


def test_workers_must_be_positive():
    with pytest.raises(tradefloor.ValidationError):
        tradefloor.rank(make, seeds=range(2), universe=UNIVERSE, days=1, workers=0)


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

    Re-measured at 0.8.5, when an agent's fills stopped being held on every
    tick of the step, and the pairs changed again. Under 0.8.1 mean
    reversion led on pooled capture at +0.947 and swept random 12 to 0;
    that lead was its own impact, collected on every tick. Now the table
    reads buy-and-hold +0.095, mean reversion -0.075, random -0.337 and
    momentum -0.950. The strong pair is still mean reversion against
    random, 10 to 2 at p = 0.039. The weak pair is buy-and-hold against
    mean reversion: buy-and-hold is ahead on pooled capture and wins 9 of
    12 paired seeds, p = 0.146, which the sign test does not confirm.

    Re-measured when pt-v20 became the default (0.8.5), which moved every
    stock-specific shock into fair value and took the price-only edge away,
    as C4b now grades: mean reversion and random split 6 to 6 (p = 1.0).
    The pairs are re-dealt. The strong pair is buy-and-hold against random,
    11 to 1 at p = 0.0063. The weak pair is mean reversion against
    momentum: mean reversion is ahead on pooled capture (-0.907 against
    -1.035) and wins 8 of 12 paired seeds, p = 0.388, which the sign test
    does not confirm. Two of the twelve seeds are unmeasurable, where the
    Oracle lost money over the ten days.

    Re-measured on pt-v20's graded arm, where the Oracle is no ceiling and
    the table reads each agent's mean P&L over buy-and-hold's in place of
    a capture. The strong pair holds, buy-and-hold against random 11 to 1
    at p = 0.0063. The weak pair is still mean reversion against momentum:
    mean reversion trails buy-and-hold by less (-31,797 a seed against
    -35,129) and wins 7 of 12 paired seeds, p = 0.77. No seed is
    unmeasurable, since nothing divides by the Oracle. On pt-v19 the same
    grid reads the pooled captures in this module's docstring.

    Asserted as the CONTRAST rather than as two fixed p-values, because the
    counts belong to these seeds. What must hold is that the sign test can
    tell the two situations apart at all.
    """
    strong = ranking.separation("buy_and_hold", "random")
    weak = ranking.separation("mean_reversion", "momentum")
    assert strong["p_value"] < 0.05, (
        f"buy-and-hold did not separate from random: {strong}"
    )
    assert not weak["decisive"]
    assert weak["p_value"] > strong["p_value"], (
        "the sign test gave mean-reversion-vs-momentum at least as much "
        "confidence as buy-and-hold-vs-random; it is not discriminating"
    )
    # And the ordering the aggregate suggests is the one the sign test
    # refuses to confirm, and reporting both exists for that.
    table = {r.name: r.mean_excess_pnl for r in ranking.table()}
    assert table["buy_and_hold"] > table["random"]
    assert table["mean_reversion"] > table["momentum"]


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
    with pytest.raises(tradefloor.ValidationError, match="not in this ranking"):
        ranking.separation("momentum", "nonesuch")


# --------------------------------------------------------------------------
# The table, and what it does with what it cannot measure
# --------------------------------------------------------------------------


def test_the_table_is_ordered_by_pooled_capture(ceiling_ranking):
    assert ceiling_ranking.capture_withheld is None
    pooled = [r.pooled_capture for r in ceiling_ranking.table()]
    assert None not in pooled
    assert pooled == sorted(pooled, reverse=True)


def test_the_table_can_still_be_asked_for_the_median(ceiling_ranking):
    medians = [r.median_capture
               for r in ceiling_ranking.table(by="median_capture")]
    assert None not in medians
    assert medians == sorted(medians, reverse=True)


def test_the_capture_ranking_is_unchanged_where_the_oracle_is_a_ceiling(
        ceiling_ranking):
    """On pt-v19 a ranking reads as it did before pt-v20 withheld the
    capture: the same keys, the pooled capture in the report, and no
    buy-and-hold field in its place. Since 0.8.5 each record also carries
    what the agent's code did on each seed, in both forms."""
    payload = ceiling_ranking.as_dict()
    assert "capture_withheld" not in payload
    assert "unmeasurable_seeds" in payload
    for record in payload["agents"].values():
        assert set(record) == {"name", "seeds", "captures", "pnls", "wins",
                               "pooled_capture", "median_capture",
                               "median_pnl", "win_rate", "errors",
                               "rejected", "max_leverage"}
    assert "capture " in ceiling_ranking.report()
    assert "buy-and-hold" not in ceiling_ranking.report()


# --------------------------------------------------------------------------
# pt-v20: no capture, and the table reads against buy-and-hold
# --------------------------------------------------------------------------


def test_on_pt_v20_no_capture_is_reported_and_the_reason_is(ranking):
    """The default's Oracle is not a ceiling, so the ranking carries no
    capture at all: every one None, none in `as_dict`, no seed counted as
    unmeasurable (the Oracle may well have made money), and the reason in
    `capture_withheld` and the report."""
    from tradefloor.baselines import ORACLE_NOT_A_CEILING

    assert ranking.model_fingerprint == "pt-v20"
    assert ranking.capture_withheld == ORACLE_NOT_A_CEILING["pt-v20"]
    assert ranking.unmeasurable == []
    for record in ranking.records.values():
        assert record.captures == [None] * len(ranking.seeds)
        assert record.pooled_capture is None
        assert record.median_capture is None
    payload = ranking.as_dict()
    assert payload["capture_withheld"] == ranking.capture_withheld
    assert "unmeasurable_seeds" not in payload
    for record in payload["agents"].values():
        assert not {"captures", "pooled_capture",
                    "median_capture"} & set(record)
    assert "No capture ratio on pt-v20" in ranking.report()
    assert "capture +" not in ranking.report()


def test_on_pt_v20_the_table_is_ordered_against_buy_and_hold(ranking):
    """The headline is each agent's P&L less buy-and-hold's, per seed and
    averaged, checked here by arithmetic on the recorded P&Ls."""
    benchmark = ranking.records["buy_and_hold"].pnls
    for record in ranking.records.values():
        excess = [p - b for p, b in zip(record.pnls, benchmark)]
        assert record.excess_pnls == pytest.approx(excess)
        assert record.mean_excess_pnl == pytest.approx(
            sum(excess) / len(excess))
        assert record.seeds_ahead == sum(1 for e in excess if e > 0)
    assert ranking.records["buy_and_hold"].mean_excess_pnl == 0.0
    ordered = [r.mean_excess_pnl for r in ranking.table()]
    assert ordered == sorted(ordered, reverse=True)
    # Asking for the capture falls back to median P&L, as with no
    # reference at all, rather than sorting on None.
    medians = [r.median_pnl for r in ranking.table(by="pooled_capture")]
    assert medians == sorted(medians, reverse=True)


def test_the_oracle_is_not_a_contender(ranking):
    # It is the denominator. Ranking it against the agents it normalises
    # would put it first by construction and mean nothing.
    assert "oracle" not in ranking.records


def test_ranking_by_an_unknown_key_is_refused(ranking):
    with pytest.raises(tradefloor.ValidationError):
        ranking.table(by="sharpe")


def test_an_absent_reference_leaves_capture_unmeasurable_and_says_so():
    """No oracle means no capture, and the table must not pretend otherwise.

    It falls back to median P&L so the ranking still ranks, and every capture
    reads None rather than 0.0 -- which would have sorted a lossmaking agent
    above one that lost more.
    """
    # On `CEILING_PRESET`: on pt-v20 no capture is reported with or
    # without the Oracle, and the reason is the preset's, not a lost seed.
    ranking = tradefloor.rank(make, seeds=range(4), universe=UNIVERSE, days=2,
                           oracle="not_present", model=CEILING_PRESET)
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


# --------------------------------------------------------------------------
# An agent whose code raised is ranked, and named
# --------------------------------------------------------------------------


class _Raises:
    """Raises on every call to act(), as a one-character bug would."""

    def act(self, obs):
        raise KeyError("x")


class _ExplainRaises(BuyAndHold):
    """Trades like buy-and-hold, and its explain() raises every day."""

    def explain(self, day):
        raise ValueError("no factor")


class _Refused:
    """Orders a ticker that does not exist on every step. Refused, not
    raised: the harness counts these in `Scorecard.rejected`."""

    def act(self, obs):
        return {"NOT_A_TICKER": 10.0}


#: The market the raising-agent report was reported on: three seeds, two
#: days, a 20-name roster.
RAISING_UNIVERSE = tradefloor.Universe.random(20, seed=111)


def test_an_agent_that_raises_every_step_is_ranked_and_named():
    """An agent whose act() raises on every step trades nothing and is
    scored as though it chose to. The row stays, marked, and the report
    says how often it raised and what the first exception was. Until 0.8.5
    it read "ahead 2/3" against buy-and-hold with nothing to say it had
    never run."""
    ranking = tradefloor.rank(
        lambda: {"broken": _Raises(), "buy_and_hold": BuyAndHold()},
        seeds=range(3), universe=RAISING_UNIVERSE, days=2)
    record = ranking.records["broken"]
    steps = 2 * 6
    assert record.errors == [steps, steps, steps]
    assert record.seeds_with_errors == 3
    assert record.raised_in_act
    assert record.first_error == "seed 0, step 0: KeyError: 'x'"
    assert ranking.records["buy_and_hold"].errors == [0, 0, 0]

    report = ranking.report()
    assert "[raised: see below]" in report
    assert (f"RAISED broken: {3 * steps} errors on 3 of 3 seeds, the first "
            "on seed 0, step 0: KeyError: 'x'.") in report
    assert "act() raised as a step with no orders" in report
    assert "raised on 3/3 seeds" in repr(record)
    assert ranking.as_dict()["agents"]["broken"]["errors"] == [steps] * 3
    assert (ranking.as_dict()["agents"]["broken"]["first_error"]
            == record.first_error)


def test_a_refused_order_is_not_counted_as_a_raise():
    """A refused order is a line in `Scorecard.errors` too, and it is not
    the agent's code raising. It goes in `rejected`."""
    ranking = tradefloor.rank(
        lambda: {"refused": _Refused(), "buy_and_hold": BuyAndHold()},
        seeds=range(2), universe=RAISING_UNIVERSE, days=1)
    record = ranking.records["refused"]
    assert record.errors == [0, 0]
    assert record.rejected == [6, 6]
    assert record.first_error is None
    assert "RAISED" not in ranking.report()
    assert "[raised" not in ranking.report()


def test_an_explain_that_raises_is_named_and_does_not_cost_orders():
    ranking = tradefloor.rank(
        lambda: {"explains": _ExplainRaises(), "buy_and_hold": BuyAndHold()},
        seeds=range(2), universe=RAISING_UNIVERSE, days=2)
    record = ranking.records["explains"]
    assert record.errors == [2, 2]
    assert not record.raised_in_act
    assert record.first_error == "seed 0, day 0 explain: ValueError: no factor"
    # It trades exactly as buy-and-hold does, since only explain() raised.
    assert record.pnls == ranking.records["buy_and_hold"].pnls
    assert "Only explain() raised, so its P&L is unaffected" in (
        ranking.report())


def test_each_record_keeps_the_scorecards_rejected_and_leverage_per_seed():
    """What a regression gate reads, besides P&L. Parallel to `seeds`, and
    equal to what `evaluate` put on each seed's scorecard."""
    ranking = tradefloor.rank(make, seeds=[4, 9], universe=UNIVERSE, days=1)
    for index, seed in enumerate(ranking.seeds):
        cards = tradefloor.evaluate(make(), seed=seed, universe=UNIVERSE,
                                    days=1)
        for name, record in ranking.records.items():
            assert record.rejected[index] == cards[name].rejected
            assert record.max_leverage[index] == cards[name].max_leverage
            assert record.errors[index] == 0
    payload = ranking.as_dict()["agents"]["momentum"]
    assert payload["rejected"] == ranking.records["momentum"].rejected
    assert payload["max_leverage"] == ranking.records["momentum"].max_leverage


# --------------------------------------------------------------------------
# Shared instances, and specs
# --------------------------------------------------------------------------


def test_a_factory_that_returns_shared_instances_is_refused():
    """`lambda: shared` passed the old check, which only asked whether the
    argument was callable. On seeds 101, 202 and 303 of a six-name roster a
    shared momentum agent read a median P&L of -5,356 against -2,163 for
    fresh ones, and nothing said so."""
    shared = {"mom": tradefloor.StrategySpec.momentum().build()}
    with pytest.raises(tradefloor.ValidationError,
                       match="returned the same 'mom' object for seed 202"):
        tradefloor.rank(lambda: shared, seeds=[101, 202, 303],
                        universe=UNIVERSE, days=1)


def test_a_fresh_mapping_around_a_shared_agent_is_refused():
    agent = Momentum()
    with pytest.raises(tradefloor.ValidationError, match="same 'mom' object"):
        tradefloor.rank(lambda: {"mom": agent,
                                 "buy_and_hold": BuyAndHold()},
                        seeds=range(3), universe=UNIVERSE, days=1, workers=2)


def test_one_object_under_two_labels_is_refused():
    agent = Momentum()
    with pytest.raises(tradefloor.ValidationError,
                       match="one object under two labels, 'a' and 'b'"):
        tradefloor.rank(lambda: {"a": agent, "b": agent}, seeds=range(2),
                        universe=UNIVERSE, days=1)


def test_a_factory_must_return_a_mapping():
    with pytest.raises(tradefloor.ValidationError, match="returned a list"):
        tradefloor.rank(lambda: [Momentum()], seeds=range(2),
                        universe=UNIVERSE, days=1)


def test_a_mapping_of_specs_is_accepted_as_it_is():
    """`evaluate` builds a spec fresh on every seed, so a mapping of specs
    carries no state between seeds and needs no factory. It ranks exactly
    as the same specs behind a factory do."""
    specs = {"momentum": tradefloor.StrategySpec.momentum(),
             "buy_and_hold": tradefloor.StrategySpec.hold()}
    direct = tradefloor.rank(specs, seeds=range(3), universe=UNIVERSE, days=1)
    behind = tradefloor.rank(lambda: dict(specs), seeds=range(3),
                             universe=UNIVERSE, days=1)
    assert direct.as_dict() == behind.as_dict()


def test_a_mapping_with_one_built_agent_is_still_refused_and_named():
    mixed = {"momentum": tradefloor.StrategySpec.momentum(),
             "hold": BuyAndHold()}
    with pytest.raises(tradefloor.ValidationError,
                       match="Built here: 'hold'"):
        tradefloor.rank(mixed, seeds=range(2), universe=UNIVERSE, days=1)


# --------------------------------------------------------------------------
# Which entrant is buy-and-hold
# --------------------------------------------------------------------------


def test_buy_and_hold_under_another_label_is_found_and_named():
    """A BuyAndHold labelled 'bh' read "no buy-and-hold to compare" on
    every row. It is now the benchmark, and the report says which label
    it read."""
    ranking = tradefloor.rank(
        lambda: {"mine": Momentum(), "bh": BuyAndHold()},
        seeds=[1, 2, 3], universe=UNIVERSE, days=2)
    assert ranking.benchmark == "bh"
    mine, bh = ranking.records["mine"], ranking.records["bh"]
    assert mine.excess_pnls == pytest.approx(
        [a - b for a, b in zip(mine.pnls, bh.pnls)])
    report = ranking.report()
    assert "no buy-and-hold to compare" not in report
    assert "bh                the benchmark" in report
    assert "The benchmark is 'bh', the one buy-and-hold entrant." in report
    assert ranking.as_dict()["benchmark"] == "bh"


def test_a_hold_spec_is_found_as_the_benchmark():
    ranking = tradefloor.rank(
        {"mom": tradefloor.StrategySpec.momentum(),
         "hold": tradefloor.StrategySpec.hold()},
        seeds=[1, 2], universe=UNIVERSE, days=1)
    assert ranking.benchmark == "hold"


def test_the_benchmark_can_be_named():
    ranking = tradefloor.rank(
        lambda: {"mine": Momentum(), "buy_and_hold": BuyAndHold()},
        seeds=[1, 2], universe=UNIVERSE, days=1, benchmark="mine")
    assert ranking.benchmark == "mine"
    assert ranking.records["mine"].excess_pnls == [0.0, 0.0]
    with pytest.raises(tradefloor.ValidationError,
                       match="benchmark='nope' is not one of the entrants"):
        tradefloor.rank(lambda: {"mine": Momentum()}, seeds=[1, 2],
                        universe=UNIVERSE, days=1, benchmark="nope")


def test_no_benchmark_says_how_to_name_one():
    ranking = tradefloor.rank(lambda: {"mine": Momentum()}, seeds=[1, 2],
                              universe=UNIVERSE, days=1)
    assert ranking.benchmark is None
    assert "rank(..., benchmark='<label>')" in ranking.report()

    two = tradefloor.rank(
        lambda: {"mine": Momentum(), "a": BuyAndHold(), "b": BuyAndHold()},
        seeds=[1, 2], universe=UNIVERSE, days=1)
    assert two.benchmark is None
    assert "2 entrants hold the market ('a', 'b')" in two.report()


class _TamperingHold(BuyAndHold):
    """Buys the market, then rewrites the first name's earnings. Handed
    the live engine only under `trusted_agents=True`."""

    def act(self, obs):
        if obs.step == 1:
            eps, bv, g = obs.engine.fundamentals()
            eps = list(eps)
            eps[0] = eps[0] * 10 if eps[0] > 0 else 1.0
            obs.engine.set_fundamentals(eps, list(bv), list(g))
        return super().act(obs)


def test_a_benchmark_that_changed_the_market_is_refused():
    """It is left out of the table like any tampered agent, and every other
    row would be measured against a market it rewrote."""
    with pytest.raises(tradefloor.ValidationError,
                       match="the benchmark 'bh' changed the market"):
        tradefloor.rank(lambda: {"mine": Momentum(), "bh": _TamperingHold()},
                        seeds=[1, 2], universe=UNIVERSE, days=1,
                        trusted_agents=True)


def test_the_withheld_capture_reason_is_printed_only_when_the_oracle_ran():
    """On pt-v20 the reason no capture is reported is about the Oracle. A
    ranking with no Oracle entered never had a capture to withhold, so the
    paragraph is left out."""
    from tradefloor.baselines import ORACLE_NOT_A_CEILING

    reason = ORACLE_NOT_A_CEILING["pt-v20"]
    without = tradefloor.rank(
        lambda: {"mine": Momentum(), "buy_and_hold": BuyAndHold()},
        seeds=[1, 2], universe=UNIVERSE, days=1)
    assert without.capture_withheld == reason
    assert reason not in without.report()
    with_oracle = tradefloor.rank(make, seeds=[1, 2], universe=UNIVERSE,
                                  days=1)
    assert reason in with_oracle.report()


# --------------------------------------------------------------------------
# The docstring's seed-count guidance
# --------------------------------------------------------------------------


def test_the_seed_counts_in_the_docstring_are_the_sign_tests():
    import tradefloor.ranking as module

    doc = " ".join(module.__doc__.split())
    for wins, losses, quoted in ((6, 0, "0.031"), (5, 0, "0.062"),
                                 (8, 0, "0.0078"), (7, 1, "0.070"),
                                 (6, 2, "0.29"), (10, 2, "0.039")):
        assert f"{_sign_test(wins, losses):.2g}" == f"{float(quoted):.2g}"
        assert f"p = {quoted}" in doc
    assert _sign_test(5, 0) > 0.05
