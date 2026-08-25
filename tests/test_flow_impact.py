"""Counterfactual impact measurement.

The distinctive capability. In a real market you observe the price you got and
can never observe the price you would have got had you not traded, because
your trading is part of why that price happened. Here both worlds are
runnable.
"""

import pytest

import pretium

UNIVERSE = pretium.Universe.random(6, seed=3)
TRADED = UNIVERSE[0].ticker


# A day's counterfactual impact carries noise as well as push: the flow
# perturbs the traded name's own state (its book, its maker inventory, its
# variance path), and identical draws map through that perturbed state to a
# per-seed divergence of several bps either way. At this flow size the
# deterministic push is ALSO a few bps — `order_imbalance` floors at 0.2 for
# any realistic participation, so raising the flow does not raise the push —
# which makes single-seed sign assertions a seed lottery. They passed by luck
# before the 2026-08 market-factor recalibration re-rolled every trajectory
# (at the old sigma, seed 6 already read -4 bps for a buyer). The direction
# of the MECHANISM is asserted where it is visible: in the mean across seeds.
#
# Eight seeds sufficed until the market-factor variance process joined the
# era boundary. Its regimes rail violent days against the ±25% session
# breaker, and a railed close ERASES the counterfactual (both worlds print
# the bound, and `s` is re-derived from the clamped price in both) — so
# some seeds now read exactly 0 and the per-seed spread widened. Measured
# across seeds 1-32 at this flow: buy +1.72 mean (24 positive, 5 negative,
# 3 railed to zero, sd 2.98), sell -2.46 (24 negative). The first eight
# seeds happen to contain the worst of the draw (-0.57 buy mean), so the
# mean is taken where the mechanism is visible at this noise level:
# thirty-two seeds.
IMPACT_SEEDS = range(1, 33)


def _mean_impact_and_cost_bps(flow):
    impact = cost = 0.0
    for seed in IMPACT_SEEDS:
        cf = pretium.flow_impact(
            seed=seed, universe=UNIVERSE, order_flow={TRADED: flow}, ticks=390
        )
        impact += cf.impact_bps[cf.tickers.index(TRADED)]
        cost += cf.cost_bps(TRADED)
    return impact / len(IMPACT_SEEDS), cost / len(IMPACT_SEEDS)


def test_a_buyer_moves_the_price_up_and_pays_for_it():
    impact, cost = _mean_impact_and_cost_bps((6e6, 0.0))
    assert impact > 0, "buying pressure must lift the price"
    assert cost > 0, "and that lift is a cost to the buyer"


def test_a_seller_moves_the_price_down_and_also_pays():
    # cost_bps is signed so positive always means worse for the trader.
    # Reporting raw impact and leaving the caller to reason about direction is
    # how sign errors reach published numbers.
    impact, cost = _mean_impact_and_cost_bps((0.0, 6e6))
    assert impact < 0, "selling pressure must push the price down"
    assert cost > 0, "which is still a cost to the seller"


def test_impact_is_isolated_to_the_names_actually_traded():
    """The property that makes the measurement exact rather than a signal.

    Order flow consumes no draws, so adding it leaves the shared schedule
    byte-identical and untraded names follow exactly the path they would have
    followed. If that ever stopped being true, impact would be buried in a
    shifted market and this whole measurement would become an estimate.
    """
    cf = pretium.flow_impact(
        seed=5, universe=UNIVERSE, order_flow={TRADED: (6e6, 0.0)}, ticks=390
    )
    assert cf.untouched_moved() == []
    # And the traded name DID move. Without this the assertion above passes
    # when the two worlds are accidentally identical -- flow silently dropped,
    # say -- because then nothing moved and nothing is untouched-and-moved.
    # "No leak" and "no effect at all" look the same from one direction only.
    #
    # Seed 5, not the 42 this used to pin: since the factor-variance change,
    # a seed whose measurement day rails the session breaker reads exactly
    # zero impact WITH the flow fully applied (the rail erases the
    # counterfactual at the close; see IMPACT_SEEDS above), and 42 became
    # such a seed. The guard needs a rail-free seed to mean what it says.
    assert cf.impact_bps[cf.tickers.index(TRADED)] != 0.0


def test_order_flow_consumes_no_draws():
    # The mechanism behind the test above, asserted directly so a change to it
    # fails here with an obvious cause rather than there with a subtle one.
    def draws(flow):
        e = pretium.Engine(seed=42, universe=UNIVERSE)
        e.open_market()
        e.run_session(9, 30, 3, 390, order_flow=flow)
        return e.draws_consumed

    assert draws(None) == draws({TRADED: (6e6, 0.0)})


def test_a_roster_edit_is_not_a_counterfactual():
    # Contrast case. Adding an instrument DOES shift the draw schedule, so a
    # before/after comparison across roster sizes measures the schedule shift
    # rather than anything about trading.
    def draws(n):
        u = pretium.Universe.random(n, seed=3)
        e = pretium.Engine(seed=42, universe=u)
        e.open_market()
        e.run_session(9, 30, 3, 100)
        return e.draws_consumed

    assert draws(6) != draws(7)


def test_bigger_size_costs_more():
    # Emergent, not modelled: a larger order pushes further because it applies
    # more pressure, not because a coefficient scales with size.
    small = pretium.flow_impact(
        seed=42, universe=UNIVERSE, order_flow={TRADED: (1e6, 0.0)}, ticks=390
    )
    large = pretium.flow_impact(
        seed=42, universe=UNIVERSE, order_flow={TRADED: (8e6, 0.0)}, ticks=390
    )
    assert large.cost_bps(TRADED) >= small.cost_bps(TRADED)


def test_the_two_worlds_differ_only_by_the_flow():
    # Baseline must be exactly the market that would have happened. If it were
    # not, the subtraction would be measuring the difference between two
    # unrelated runs.
    cf = pretium.flow_impact(
        seed=7, universe=UNIVERSE, order_flow={TRADED: (2e6, 0.0)}, ticks=200
    )
    plain = pretium.Engine(seed=7, universe=UNIVERSE)
    plain.open_market()
    plain.run_session(9, 30, 3, 200)
    plain.close_market()

    import struct
    expected = list(struct.unpack("<%dd" % (len(plain.prices()) // 8), plain.prices()))
    assert cf.baseline == expected


def test_it_is_reproducible():
    a = pretium.flow_impact(seed=9, universe=UNIVERSE,
                               order_flow={TRADED: (3e6, 0.0)}, ticks=150)
    b = pretium.flow_impact(seed=9, universe=UNIVERSE,
                               order_flow={TRADED: (3e6, 0.0)}, ticks=150)
    assert a.impact == b.impact


def test_impact_bps_is_relative_so_names_are_comparable():
    # A penny on a $3 stock and a penny on a $600 stock are not the same
    # event, so currency impact alone is not comparable across a roster.
    cf = pretium.flow_impact(
        seed=42, universe=UNIVERSE, order_flow={TRADED: (6e6, 0.0)}, ticks=390
    )
    i = cf.tickers.index(TRADED)
    assert cf.impact_bps[i] == pytest.approx(
        cf.impact[i] / cf.baseline[i] * 10_000
    )


def test_no_trading_is_refused():
    with pytest.raises(pretium.ValidationError, match="nothing to measure"):
        pretium.flow_impact(seed=1, universe=UNIVERSE, order_flow={})
