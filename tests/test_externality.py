"""The cohort World, and the matrix measured off it.

Two things have to be true at once here and they pull in opposite
directions. The single-agent `World` is pinned by
`tests/test_counterfactual.py` and by every experiment already published
against it, so the cohort cannot be a rewrite of the run loop. And the
matrix is only worth reading if the arms it compares really are the same
market with one agent taken out, which is a claim about a fork, not about a
number.

So the assertions here are mostly about the arms rather than about the
result. `agree` before any day runs, the diagonal against `tca.analyse`
which is an independent implementation of the same measurement, and a cohort
of agents that never trade, where every entry has to be zero because there
was nothing for anyone to do to anyone.
"""

from __future__ import annotations

import pytest

import tradefloor as tf
from tradefloor.counterfactual import SOLO, World, agree, compare
from tradefloor.externality import Externality, externalities

SEED = 42
ROSTER_SEED = 99
NAMES = 8


def roster():
    return list(tf.Universe.random(NAMES, seed=ROSTER_SEED))


class Buyer:
    """Buys a fixed fraction of ADV in one name, once, at a chosen step.

    Small and deterministic. The cohort machinery is what is under test, so
    the agent is the smallest thing that puts a fill on the tape at a step
    a test can name.
    """

    def __init__(self, ticker_index: int = 0, at: int = 0,
                 shares: float = 200.0) -> None:
        self.ticker_index = ticker_index
        self.at = at
        self.shares = shares
        self.asked = 0

    def act(self, obs) -> dict[str, float]:
        self.asked += 1
        if obs.step != self.at:
            return {}
        return {obs.tickers[self.ticker_index]: self.shares}

    def state(self):
        return {"asked": self.asked}


class Idle:
    """Never trades, and counts the times it was asked."""

    def __init__(self) -> None:
        self.asked = 0

    def act(self, obs) -> dict[str, float]:
        self.asked += 1
        return {}

    def state(self):
        return {"asked": self.asked}


def cohort(**over) -> World:
    kwargs = dict(seed=SEED, universe=roster(), label="cohort",
                  agents={"alpha": Buyer(0, at=0), "beta": Buyer(1, at=1)})
    kwargs.update(over)
    return World(**kwargs)


# ---------------------------------------------------------------------------
# The two forms of World
# ---------------------------------------------------------------------------

def test_a_world_takes_one_agent_or_a_cohort_and_not_both():
    with pytest.raises(tf.ValidationError, match="exactly one"):
        World(seed=SEED, universe=roster(), agent=Idle(),
              agents={"a": Idle()})
    with pytest.raises(tf.ValidationError, match="exactly one"):
        World(seed=SEED, universe=roster())
    with pytest.raises(tf.ValidationError, match="nobody in it"):
        World(seed=SEED, universe=roster(), agents={})
    with pytest.raises(tf.ValidationError, match="non-empty strings"):
        World(seed=SEED, universe=roster(), agents={"": Idle()})
    with pytest.raises(tf.ValidationError, match="mapping"):
        World(seed=SEED, universe=roster(), agents=[Idle()])


def test_the_single_agent_form_is_a_one_element_cohort():
    agent = Idle()
    world = World(seed=SEED, universe=roster(), agent=agent)
    assert not world.is_cohort
    assert world.agent is agent
    assert world.agents == {SOLO: agent}
    assert list(world.portfolios) == [SOLO]
    assert world.portfolios[SOLO] is world.portfolio


def test_the_cohort_aliases_refuse_to_name_one_of_several():
    world = cohort()
    assert world.is_cohort
    assert list(world.agents) == ["alpha", "beta"]
    for read in (lambda: world.agent, lambda: world.portfolio):
        with pytest.raises(tf.ValidationError, match="names none of them"):
            read()
    with pytest.raises(tf.ValidationError, match="names none of them"):
        world.agent = Idle()
    with pytest.raises(tf.ValidationError, match="names none of them"):
        world.portfolio = tf.Portfolio()


def test_label_order_is_sorted_and_not_the_order_the_mapping_was_written():
    """The market cannot be a property of how the caller typed the call.

    Two callers naming one cohort get one market, which they would not if
    execution order came from a dict literal.
    """
    a = cohort(agents={"alpha": Buyer(0, at=0), "beta": Buyer(1, at=1)})
    b = cohort(agents={"beta": Buyer(1, at=1), "alpha": Buyer(0, at=0)})
    assert list(a.agents) == list(b.agents) == ["alpha", "beta"]
    a.run(days=2)
    b.run(days=2)
    assert a.digest() == b.digest()
    assert a.trace == b.trace


def test_every_agent_has_its_own_portfolio_and_its_own_capital():
    world = cohort(cash=250_000.0)
    assert {label: book.cash for label, book in world.portfolios.items()} == {
        "alpha": 250_000.0, "beta": 250_000.0}
    world.run(days=1)
    assert (world.portfolios["alpha"].positions.keys()
            != world.portfolios["beta"].positions.keys())


def test_a_cohort_trace_row_carries_the_agents_map_and_no_top_level_fields():
    world = cohort()
    world.run(days=1)
    row = world.trace[0]
    assert set(row) == {"step", "day", "step_of_day", "macro", "prices",
                        "agents"}
    assert set(row["agents"]) == {"alpha", "beta"}
    for fields in row["agents"].values():
        assert set(fields) == {"decision", "orders", "fills", "refused",
                               "unusable", "cash", "net_worth", "exposure",
                               "positions"}


def test_a_single_agent_trace_row_is_the_row_it_always_was():
    world = World(seed=SEED, universe=roster(), agent=Buyer(0, at=0))
    world.run(days=1)
    assert list(world.trace[0]) == [
        "step", "day", "step_of_day", "macro", "decision", "orders", "fills",
        "refused", "unusable", "prices", "cash", "net_worth", "exposure",
        "positions"]
    assert "agents" not in world.trace[0]


def test_the_cohort_flow_reaches_the_market_as_one_merged_order_flow():
    """Two buyers of half a size leave the market one buyer of the size does.

    Two sweeps of 10,000 shares take the levels one sweep of 20,000 takes,
    and two flows of 10,000 merged per ticker are one flow of 20,000, so
    the session is handed identical inputs and the market that comes out
    is identical to the bit. A cohort that passed one agent's flow and
    dropped the other's would land on the 10,000-share market, which the
    second assertion separates from the merged one.

    The size is above the clamp floor on the imbalance term, which
    ``tca.py`` documents at about 1.33 times the instrument's average
    minute volume. Below it the term is flat and 10,000 and 20,000 shares
    reach the price identically, so a smaller fixture would pass this test
    on a market that never read the flow.
    """
    cash = 10_000_000.0

    def solo(agent):
        world = World(seed=SEED, universe=roster(), agent=agent, cash=cash)
        world.run(days=1)
        return world.digest()

    both = cohort(cash=cash,
                  agents={"alpha": Buyer(0, at=0, shares=10_000.0),
                          "beta": Buyer(0, at=0, shares=10_000.0)})
    both.run(days=1)

    assert both.digest() == solo(Buyer(0, at=0, shares=20_000.0))
    assert both.digest() != solo(Buyer(0, at=0, shares=10_000.0))
    assert solo(Buyer(0, at=0, shares=10_000.0)) != solo(Idle())


def test_agents_do_not_take_each_others_liquidity_within_a_step():
    """`Portfolio.execute` reads the ladder and removes nothing.

    So label order decides which agent is asked first, which flow is summed
    first and whose rejection is written first, and no price. Two agents
    buying one name on one step meet the same levels and pay the same
    average, and the market is the same market whichever label sorts first.

    Without this the module could claim, as it did, that a later agent pays
    the levels an earlier one took. The behaviour was always right and the
    sentence was wrong, which is the failure a test states rather than a
    paragraph.
    """
    cash = 50_000_000.0
    both = cohort(cash=cash,
                  agents={"alpha": Buyer(0, at=0, shares=10_000.0),
                          "beta": Buyer(0, at=0, shares=10_000.0)})
    both.run(days=1)
    alpha = both.portfolios["alpha"].fills[0]
    beta = both.portfolios["beta"].fills[0]
    assert alpha["price"] == beta["price"]
    assert alpha["worst_price"] == beta["worst_price"]

    # And the ladder both of them swept is the ladder neither of them moved.
    fresh = tf.Engine(seed=SEED, universe=roster())
    fresh.open_market()
    ticker = fresh.tickers[0]
    before = _levels(fresh, ticker)
    tf.Portfolio(cash=cash).execute(fresh, ticker, 10_000.0)
    assert _levels(fresh, ticker) == before

    # Sorting order decides no price and no market.
    first = cohort(cash=cash,
                   agents={"aa": Buyer(0, at=0, shares=3_000.0),
                           "zz": Buyer(0, at=0, shares=9_000.0)})
    second = cohort(cash=cash,
                    agents={"aa": Buyer(0, at=0, shares=9_000.0),
                            "zz": Buyer(0, at=0, shares=3_000.0)})
    first.run(days=1)
    second.run(days=1)
    assert first.digest() == second.digest()
    assert (first.portfolios["aa"].fills[0]["price"]
            == second.portfolios["zz"].fills[0]["price"])


def _levels(engine, ticker, depth: int = 3):
    book = engine.book(ticker)
    return [(level.price, level.quantity)
            for side in ("buy", "sell")
            for level in book.price_levels(side, depth)]


def test_agents_are_asked_before_any_of_them_executes():
    """Simultaneous within a step: they see each other's impact and never
    each other's orders."""
    seen: list[tuple[str, float]] = []

    class Watcher:
        def __init__(self, name):
            self.name = name

        def act(self, obs):
            seen.append((self.name, obs.engine.book(obs.tickers[0]).mid_price))
            return {obs.tickers[0]: 400.0}

    world = cohort(agents={"alpha": Watcher("alpha"),
                           "beta": Watcher("beta")})
    world.run(days=1)
    first = seen[:2]
    assert [name for name, _ in first] == ["alpha", "beta"]
    assert first[0][1] == first[1][1], (
        "beta saw a mid alpha's fill had already moved, so the two were not "
        "asked before either executed")


# ---------------------------------------------------------------------------
# Removal
# ---------------------------------------------------------------------------

def test_without_freezes_one_agent_and_leaves_the_others_alone():
    world = cohort()
    world.run(days=1)
    arm = world.without("alpha")

    assert arm.frozen == ("alpha",)
    assert world.frozen == ()
    arm.run(days=1)

    asked_before = arm.agents["alpha"].asked
    assert asked_before == world.agents["alpha"].asked, (
        "the frozen agent was asked after the fork")
    assert arm.agents["beta"].asked > world.agents["beta"].asked
    for row in arm.trace[world.step:]:
        assert row["agents"]["alpha"]["orders"] == {}
        assert row["agents"]["alpha"]["fills"] == []
        assert row["agents"]["alpha"]["decision"] is None


def test_a_frozen_agent_keeps_its_positions_and_they_are_still_marked():
    world = cohort()
    world.run(days=1)
    held = {t: p.quantity
            for t, p in world.portfolios["alpha"].positions.items()}
    assert any(held.values()), "the fixture agent never traded"

    arm = world.without("alpha")
    arm.run(days=2)
    after = {t: p.quantity
             for t, p in arm.portfolios["alpha"].positions.items()}
    assert after == held
    assert arm.summary(agent="alpha")["final_net_worth"] != \
        world.summary(agent="alpha")["final_net_worth"]


def test_without_refuses_a_single_agent_world_and_an_unknown_label():
    solo = World(seed=SEED, universe=roster(), agent=Idle())
    with pytest.raises(tf.ValidationError, match="one agent built with"):
        solo.without("alpha")
    with pytest.raises(tf.ValidationError, match="no agent is labelled"):
        cohort().without("gamma")


def test_remove_is_without_and_acts_on_the_fork_it_returns():
    world = cohort()
    world.run(days=1)
    arm = world.remove("beta")
    assert arm.frozen == ("beta",)
    assert world.frozen == ()
    assert arm.label == "without beta"


def test_a_cohort_forks_whole():
    world = cohort()
    world.run(days=1)
    left, right = world.fork("left", "right")

    assert list(left.agents) == list(right.agents) == ["alpha", "beta"]
    for label in ("alpha", "beta"):
        assert left.agents[label] is not right.agents[label]
        assert left.agents[label] is not world.agents[label]
        assert left.portfolios[label] is not right.portfolios[label]
    left.run(days=1)
    assert right.digest() == world.digest()


# ---------------------------------------------------------------------------
# agree, summary and compare over a cohort
# ---------------------------------------------------------------------------

def test_agree_holds_between_the_full_cohort_and_each_arm_at_the_fork():
    world = cohort()
    world.run(days=2)
    (full,) = world.fork("full")
    for label in world.agents:
        agreement = agree(full, world.without(label))
        assert agreement.identical, agreement.differences
        assert len(agreement.checks) == 7 + 2 * len(world.agents)


def test_agree_names_the_agent_a_difference_belongs_to():
    world = cohort()
    world.run(days=1)
    (full,) = world.fork("full")
    arm = world.without("alpha")
    arm.portfolios["beta"].cash -= 1.0
    arm.agents["alpha"].asked = 999

    difference = agree(full, arm).differences
    assert difference == ["portfolio[beta]", "agent state[alpha]"]


def test_agree_reports_a_label_one_world_does_not_have():
    world = cohort()
    other = cohort(agents={"alpha": Buyer(0, at=0)})
    checks = dict((name, ok) for name, ok, _ in agree(world, other).checks)
    assert checks["portfolio[beta]"] is False
    assert checks["agent state[beta]"] is False


def test_a_single_agent_agreement_is_the_nine_checks_it_always_was():
    world = World(seed=SEED, universe=roster(), agent=Idle())
    world.run(days=1)
    control, shock = world.fork("control", "shock")
    agreement = agree(control, shock)
    assert [name for name, _, _ in agreement.checks] == [
        "market columns", "prices", "order book", "generator state",
        "macro chain", "whole engine state", "portfolio", "agent state",
        "shared history"]


def test_summary_and_compare_refuse_a_cohort_without_an_agent():
    world = cohort()
    world.run(days=1)
    control, shock = world.fork("control", "shock")
    control.run(days=1)
    shock.run(days=1)

    with pytest.raises(tf.ValidationError, match="reports one agent"):
        world.summary()
    with pytest.raises(tf.ValidationError, match="reports one agent"):
        compare(control, shock)
    with pytest.raises(tf.ValidationError, match="no agent is labelled"):
        compare(control, shock, agent="gamma")

    report = compare(control, shock, agent="alpha")
    assert report.control["agent"] == "alpha"
    assert "control:alpha" in report.render()


def test_compare_refuses_a_label_on_a_single_agent_world():
    world = World(seed=SEED, universe=roster(), agent=Idle())
    world.run(days=1)
    control, shock = world.fork("control", "shock")
    with pytest.raises(tf.ValidationError, match="carries no label"):
        compare(control, shock, agent="alpha")


def test_a_cohort_summary_reads_the_agent_it_names():
    world = cohort()
    world.run(days=2)
    alpha = world.summary(agent="alpha")
    beta = world.summary(agent="beta")
    assert alpha["trades"] == beta["trades"] == 1
    assert alpha["positions"] != beta["positions"]
    assert alpha["cash"] == world.portfolios["alpha"].cash
    assert alpha["final_net_worth"] == world.net_worth(agent="alpha")


# ---------------------------------------------------------------------------
# The reconstruction the diagonal rests on
# ---------------------------------------------------------------------------

def test_closing_and_opening_a_day_moves_no_price():
    """`_path` reads the price a step's session left as the next step's
    opening cross-section, and a day boundary sits between those two reads.

    If a close or an open ever moves a price, that reconstruction is wrong
    at every day boundary and the diagonal quietly stops being a shortfall.
    """
    world = World(seed=SEED, universe=roster(), agent=Idle())
    world.run(days=1)
    assert world.trace[-1]["prices"] == _prices(world.engine)
    world.engine.open_market()
    assert world.trace[-1]["prices"] == _prices(world.engine)
    world.engine.close_market()


def _prices(engine):
    import struct
    return list(struct.unpack("<%dd" % len(engine.tickers), engine.prices()))


# ---------------------------------------------------------------------------
# The matrix
# ---------------------------------------------------------------------------

def test_the_diagonal_is_the_tca_shortfall_on_a_one_agent_cohort():
    """Bit for bit against an independent implementation of the measurement.

    `tca.analyse` builds two engines from one seed, runs the agent in one
    and nobody in the other, and prices each fill against the baseline path
    at the same step index. A one-agent cohort's world-without-a IS the
    nobody-trades world, so the two numbers are the same number computed
    two ways, and any disagreement is this module reconstructing the
    baseline path wrongly.

    Measured on Universe.random(8, seed=99) at seed 42, three days, the
    default six steps a day and 65 ticks a step, one 200-share buy of the
    first name at step 0.
    """
    def agent():
        return Buyer(0, at=0, shares=200.0)

    reference = tf.tca.analyse(agent(), seed=SEED, universe=roster(), days=3)
    world = World(seed=SEED, universe=roster(), agents={"alpha": agent()})
    result = externalities(world, days=3)

    assert reference.shortfall() != 0.0, "the fixture agent never traded"
    assert result.diagonal["alpha"] == reference.shortfall()
    assert result.diagonal_bps["alpha"] == reference.shortfall_bps()
    assert result.matrix["alpha"]["alpha"] == reference.shortfall()


class Roundtrip:
    """Buys before the fork, buys again after it, then sells."""

    def act(self, obs) -> dict[str, float]:
        ticker = obs.tickers[0]
        if obs.step in (2, 7):
            return {ticker: 900.0}
        if obs.step == 13:
            return {ticker: -900.0}
        return {}


def test_the_diagonal_leaves_out_the_fills_the_arms_share():
    """A fork that lands mid-run has fills on both sides of it.

    Those before it happened in a history both arms share, so their cost is
    the shared history's rather than an externality, and pricing them
    against a baseline path that starts at the fork is arithmetic on two
    unrelated steps. `tca.Execution._reference` guards only the upper end of
    the path, so a pre-fork fill carries a negative index, reads a real
    price off the end of the list and returns a wrong number in silence.

    The expected value here is built from the arm's own price path rather
    than from `externalities`, so the test can fail honestly.
    """
    world = World(seed=SEED, universe=roster(), agents={"alpha": Roundtrip()})
    world.run(days=1)
    fork = world.step
    assert [f["step"] for f in world.portfolios["alpha"].fills] == [2], (
        "the fixture leaves no fill before the fork, so the filter this "
        "test is about would be a no-op")

    result = externalities(world, days=3)

    (full,) = world.fork("full")
    arm = world.without("alpha")
    full.run(days=3)
    arm.run(days=3)
    baseline = [_prices(world.engine)] + [list(row["prices"])
                                          for row in arm.trace[fork:]]
    tickers = full.engine.tickers
    expected = sum(
        fill["quantity"] * (fill["price"]
                            - baseline[fill["step"] - fork][
                                tickers.index(fill["ticker"])])
        for fill in full.portfolios["alpha"].fills if fill["step"] >= fork)

    assert result.diagonal["alpha"] == expected
    # And the number a missing filter would produce is a different number,
    # so the assertion above is not passing on an equality that holds either
    # way.
    wrong = sum(
        fill["quantity"] * (fill["price"]
                            - baseline[fill["step"] - fork][
                                tickers.index(fill["ticker"])])
        for fill in full.portfolios["alpha"].fills)
    assert wrong != expected


def test_two_agents_that_never_trade_give_a_zero_matrix():
    world = World(seed=SEED, universe=roster(),
                  agents={"alpha": Idle(), "beta": Idle()})
    result = externalities(world, days=2)

    assert result.labels == ("alpha", "beta")
    for a in result.labels:
        for b in result.labels:
            assert result.matrix[a][b] == 0.0, (a, b)
    assert result.diagonal == {"alpha": 0.0, "beta": 0.0}
    assert result.trades == {"alpha": 0, "beta": 0}
    assert any("separable" in line for line in result.caveats())


def test_the_matrix_is_the_pnl_the_removal_changed():
    """The off-diagonal against the arms it was computed from.

    Recomputing it from the same two summaries would test nothing. This
    forks and runs the removal arm a second time, independently of
    `externalities`, and requires the same number, which is also a check
    that the run is reproducible from the fork.
    """
    world = cohort()
    world.run(days=1)
    result = externalities(world, days=2)

    (full,) = world.fork("full")
    full.run(days=2)
    arm = world.without("alpha")
    arm.run(days=2)

    expected = (arm.summary(agent="beta")["pnl_since"]
                - full.summary(agent="beta")["pnl_since"])
    assert result.matrix["alpha"]["beta"] == expected
    assert result.cohort_pnl["beta"] == full.summary(agent="beta")["pnl_since"]


def test_removing_an_agent_that_traded_moves_the_other_agents_pnl():
    """The guard on the guard. A matrix of zeros passes every structural
    test above, so something has to check that a real removal registers."""
    world = cohort(agents={"alpha": Buyer(0, at=0, shares=3_000.0),
                           "beta": Buyer(0, at=1, shares=3_000.0)})
    result = externalities(world, days=2)
    assert result.matrix["alpha"]["beta"] != 0.0
    assert result.matrix["beta"]["alpha"] != 0.0


def test_externalities_leaves_the_world_it_measures_where_it_found_it():
    world = cohort()
    world.run(days=1)
    before = (world.day, world.step, world.digest(), len(world.trace))
    first = externalities(world, days=2)
    second = externalities(world, days=2)

    assert (world.day, world.step, world.digest(), len(world.trace)) == before
    assert first.matrix == second.matrix
    assert first.diagonal == second.diagonal


def test_the_result_carries_the_agreement_of_every_arm():
    world = cohort()
    world.run(days=1)
    result = externalities(world, days=1)

    assert result.agreement.identical
    assert [name for name, _, _ in result.agreement.checks] == [
        "without alpha", "without beta"]
    assert all("checks identical" in detail
               for _, _, detail in result.agreement.checks)


def test_externalities_refuses_what_it_cannot_measure():
    solo = World(seed=SEED, universe=roster(), agent=Idle())
    with pytest.raises(tf.ValidationError, match="one agent built with"):
        externalities(solo, days=1)
    with pytest.raises(tf.ValidationError, match="takes a World"):
        externalities("cohort", days=1)
    with pytest.raises(tf.ValidationError, match="at least 1"):
        externalities(cohort(), days=0)


def test_the_result_renders_and_serialises_without_retyped_prose():
    world = cohort()
    world.run(days=1)
    result = externalities(world, days=2)

    text = result.render()
    assert "remove alpha" in text
    assert "day 1" in text, "the render does not say when the removal starts"
    for line in result.caveats():
        assert line in text
    doc = result.as_dict()
    assert doc["caveats"] == result.caveats()
    assert doc["labels"] == ["alpha", "beta"]
    assert doc["matrix"]["alpha"]["beta"] == result.matrix["alpha"]["beta"]
    assert isinstance(repr(result), str)


def test_effect_on_is_the_column_of_the_matrix():
    world = cohort()
    world.run(days=1)
    result = externalities(world, days=1)
    assert result.effect_on("beta") == {
        "alpha": result.matrix["alpha"]["beta"]}
    with pytest.raises(tf.ValidationError, match="no agent is labelled"):
        result.effect_on("gamma")


def test_the_table_has_the_declared_columns():
    pytest.importorskip("pyarrow")
    world = cohort()
    world.run(days=1)
    result = externalities(world, days=1)
    table = result.table()

    assert table.column_names == list(Externality.COLUMNS)
    assert table.num_rows == len(result.labels) ** 2
    rows = table.to_pydict()
    assert rows["kind"] == ["shortfall", "externality",
                            "externality", "shortfall"]
    assert rows["removed"] == ["alpha", "alpha", "beta", "beta"]
    assert rows["affected"] == ["alpha", "beta", "alpha", "beta"]
    assert rows["value"][0] == result.diagonal["alpha"]
    assert rows["value"][1] == result.matrix["alpha"]["beta"]
