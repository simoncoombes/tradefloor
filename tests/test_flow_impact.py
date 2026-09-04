"""Counterfactual impact measurement.

The distinctive capability. In a real market you observe the price you got and
can never observe the price you would have got had you not traded, because
your trading is part of why that price happened. Here both worlds are
runnable.
"""

import math
import struct

import pytest

import tradefloor

UNIVERSE = tradefloor.Universe.random(6, seed=3)
TRADED = UNIVERSE[0].ticker


# A day's counterfactual impact carries noise as well as push: the flow
# perturbs the traded name's own state (its book, its maker inventory, its
# variance path), and identical draws map through that perturbed state to a
# per-seed divergence of several bps either way. At this flow size the
# deterministic push is ALSO a few bps -- `order_imbalance` is pinned at one
# of its clamps, so raising the flow does not raise the push --
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
        cf = tradefloor.flow_impact(
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
    cf = tradefloor.flow_impact(
        seed=1, universe=UNIVERSE, order_flow={TRADED: (6e6, 0.0)}, ticks=390
    )
    assert cf.untouched_moved() == []
    # And the traded name DID move. Without this the assertion above passes
    # when the two worlds are accidentally identical -- flow silently dropped,
    # say -- because then nothing moved and nothing is untouched-and-moved.
    # "No leak" and "no effect at all" look the same from one direction only.
    #
    # Seed 1, after 42 and then 5. The reason is the same each time and is
    # the point of the comment: a seed whose measurement day rails the
    # session breaker reads exactly zero impact WITH the flow fully applied,
    # because the rail erases the counterfactual at the close (see
    # IMPACT_SEEDS above). The 2026-08-26 era boundary to pt-v10 made seed 5
    # one of those, since that market is more volatile and rails more often.
    # Seed 1 reads +5.0 bps on the traded name and nothing on the rest.
    assert cf.impact_bps[cf.tickers.index(TRADED)] != 0.0


def test_order_flow_consumes_no_draws():
    # The mechanism behind the test above, asserted directly so a change to it
    # fails here with an obvious cause rather than there with a subtle one.
    def draws(flow):
        e = tradefloor.Engine(seed=42, universe=UNIVERSE)
        e.open_market()
        e.run_session(9, 30, 3, 390, order_flow=flow)
        return e.draws_consumed

    assert draws(None) == draws({TRADED: (6e6, 0.0)})


def test_a_roster_edit_is_not_a_counterfactual():
    # Contrast case. Adding an instrument DOES shift the draw schedule, so a
    # before/after comparison across roster sizes measures the schedule shift
    # rather than anything about trading.
    def draws(n):
        u = tradefloor.Universe.random(n, seed=3)
        e = tradefloor.Engine(seed=42, universe=u)
        e.open_market()
        e.run_session(9, 30, 3, 100)
        return e.draws_consumed

    assert draws(6) != draws(7)


def test_bigger_size_costs_more():
    # Emergent, not modelled: a larger order pushes further because it applies
    # more pressure, not because a coefficient scales with size.
    #
    # Measured on a THIN name, and at sizes three orders of magnitude apart,
    # because the size channel is narrow and bounded at both ends.
    # Under the SHIPPED law, `order_imbalance` multiplies the buy/sell ratio
    # by `max(0.2, min(total / avg_minute_volume, 10) * 0.15)`, so size
    # enters only through a multiplier that floors at 0.2 and saturates at
    # 1.5. The whole size effect is a factor of 7.5 and it is spent by ten
    # times the name's average minute volume. That is the default and it is
    # what this test measures; `order_flow_impact_law` replaces both clamps
    # with the measured law and widens the band without limit.
    #
    # This read `UNIVERSE[0]` at 1e6 against 8e6 shares until the universe
    # generator was reconciled to open a drawn roster at its own fair value.
    # That name is the roster's most liquid, its average minute volume is
    # 114,000 shares, and both sizes sat at the top of the multiplier's
    # range; the entire difference between them was worth about one price
    # tick on a 97 dollar share. The assertion was reading the rounding and
    # it happened to round the right way. It is not a size test unless the
    # sizes straddle the band, so it now straddles it.
    thin = min(UNIVERSE, key=lambda i: i.avg_volume)
    small = tradefloor.flow_impact(
        seed=42, universe=UNIVERSE, order_flow={thin.ticker: (1e1, 0.0)}, ticks=390
    )
    large = tradefloor.flow_impact(
        seed=42, universe=UNIVERSE, order_flow={thin.ticker: (1e4, 0.0)}, ticks=390
    )
    # A wide margin, not a tie-break: 137 bps against 1,327 here, and the
    # same ordering on seeds 7, 99, 123 and 2026.
    assert large.cost_bps(thin.ticker) > 2.0 * small.cost_bps(thin.ticker)


def test_order_size_stops_mattering_once_the_imbalance_multiplier_saturates():
    """The bound on the test above, asserted rather than left implicit.

    Impact is about PARTICIPATION and the SHIPPED participation term is
    capped, so beyond ten times a name's average minute volume the default
    is indifferent to size: a large order and an enormous one are the same
    order to it. Worth pinning, because a transaction-cost figure read off
    this surface at institutional size is reading a constant.

    A property of the default and not of the model. Issue #166 is this
    ceiling; `order_flow_impact_law` removes it, and the test below is the
    same three sizes under the measured law. This one stays because the
    default stays, and it will start failing on the day a preset turns the
    law on -- which is the notice anyone reading a cost figure needs.
    """
    thin = min(UNIVERSE, key=lambda i: i.avg_volume)
    costs = [
        tradefloor.flow_impact(
            seed=42, universe=UNIVERSE,
            order_flow={thin.ticker: (size, 0.0)}, ticks=390
        ).cost_bps(thin.ticker)
        for size in (1e4, 1e6, 1e8)
    ]
    assert costs[0] == costs[1] == costs[2], costs

    # And the ratio, not the net, is what the model reads. The same net
    # imbalance costs two orders of magnitude less when it arrives inside a
    # larger gross flow.
    lone = tradefloor.flow_impact(
        seed=42, universe=UNIVERSE,
        order_flow={thin.ticker: (1e3, 0.0)}, ticks=390
    ).cost_bps(thin.ticker)
    buried = tradefloor.flow_impact(
        seed=42, universe=UNIVERSE,
        order_flow={thin.ticker: (1e5, 9.9e4)}, ticks=390
    ).cost_bps(thin.ticker)
    assert lone > 100.0 * buried, (lone, buried)


SHIPPED = "pt-v16"


def _laws():
    return (tradefloor.ModelParams.from_preset(SHIPPED),
            tradefloor.ModelParams.from_preset(SHIPPED, order_flow_impact_law=1.0))


def _thin_and_its_minute():
    """The thinnest name and the denominator `order_imbalance` divides by.

    Derived from the roster rather than written as round numbers, so a size
    expressed in multiples of it lands where it is meant to whatever the
    generator gives this universe. The daily average over 390, floored at
    100 shares, which is the floor this name is actually on.
    """
    thin = min(UNIVERSE, key=lambda i: i.avg_volume)
    return thin, max(thin.avg_volume / 390.0, 100.0)


def _shock(model, ticker, size, seed=42, ticks=390):
    """A day's accumulated ``order_flow_impact`` for one name.

    The law's own output. It is read before the price breaker, the cent
    grid and the price path, none of which it passes through, so a ratio
    taken here is the law's ratio and not a session's. Every tick carries
    the same flow, so the day is the tick's shock times the intraday scale
    summed, and the scale is identical under both laws.
    """
    engine = tradefloor.Engine(seed=seed, universe=UNIVERSE, model=model)
    engine.open_market()
    engine.run_session(9, 30, 3, ticks, order_flow={ticker: (size, 0.0)})
    engine.close_market()
    raw = engine.attribution("order_flow_impact")
    values = struct.unpack("<%dd" % (len(raw) // 8), raw)
    return values[[i.ticker for i in UNIVERSE].index(ticker)]


def test_the_shipped_law_cannot_tell_three_orders_apart_and_the_measured_one_can():
    """Issue #166 as a deterministic contrast, with no statistic in the way.

    Under the shipped law the three sizes produce byte-identical runs. The
    participation term returns the same number for all three, so every
    price on every tick matches and they are literally the same order to
    the model. Under `order_flow_impact_law` they are three different runs.

    Asserted on the whole impact vector rather than on a cost, because a
    cost is a price difference, prices are on a cent grid, and a
    single-seed inequality on a noisy statistic is how the original size
    test came to compare one million shares against eight million and
    measure a rounding.
    """
    # The MOST liquid name, not the thinnest, and the reason is the test
    # below: on the thinnest name the corrected law moves the close past the
    # 25 per cent session breaker, the price rails, and two railed runs are
    # byte-identical for a reason that has nothing to do with the law. The
    # most liquid name is the one furthest from that bound, and a thousand
    # times ITS average minute volume is still deep in the tail because
    # participation is relative to the name.
    liquid = max(UNIVERSE, key=lambda i: i.avg_volume)
    minute = max(liquid.avg_volume / 390.0, 100.0)
    off, on = _laws()
    sizes = (10.0 * minute, 100.0 * minute, 1000.0 * minute)

    def runs(model):
        return [tradefloor.flow_impact(
            seed=42, universe=UNIVERSE, model=model,
            order_flow={liquid.ticker: (size, 0.0)}, ticks=390,
        ) for size in sizes]

    shipped = runs(off)
    assert (shipped[0].impact == shipped[1].impact == shipped[2].impact), (
        "the shipped law is supposed to be indifferent to size up here")

    measured = runs(on)
    # Checked before the inequalities, so a roster that starts railing fails
    # with the cause named rather than looking like the law stopped working.
    at = measured[0].tickers.index(liquid.ticker)
    for run in measured:
        move = abs(run.impact[at]) / run.baseline[at]
        assert move < 0.20, (
            "this name now moves %.1f per cent and the breaker is at 25, so "
            "these runs may be railing rather than following the law" % (move * 100))

    impacts = [run.impact for run in measured]
    assert impacts[0] != impacts[1]
    assert impacts[1] != impacts[2]
    assert impacts[0] != impacts[2]
    # The smallest sits exactly on the knee, where the two laws meet, so the
    # change is confined above it rather than applied everywhere.
    assert impacts[0] == shipped[0].impact


def test_the_measured_shock_follows_the_square_root_above_the_knee():
    """The exponent itself, through the public surface rather than derived.

    This is the assertion the pinned pair should have carried. It fails if
    the exponent above the knee is zero, which is what the `min` gave, and
    it fails at any other exponent including a merely higher ceiling.

    The multiplier above the knee is `1.5 * sqrt(participation / 10)` and a
    day's shock is proportional to it, so a hundredfold in size is a
    tenfold in the shock and a tenfold in size is a sqrt(10).

    The 1e-14 figure behind the tolerance is an EMULATION and not a
    measurement of this law. It was taken on a build that did not carry the
    dial, by scaling `order_flow_coefficient`, which multiplies the same
    product by the same factor and is therefore the same arithmetic. It
    establishes that the shock is proportional to the multiplier; it does
    not establish that the multiplier is right, which is what the assertion
    below is for. The tolerance is three orders looser than the figure.

    DELIBERATELY IN THE TAIL. Ten, a hundred and a thousand times the
    name's average minute volume are not typical orders, and that is the
    point: the shipped law is least wrong at typical size, so a test there
    would pass under both laws and be reading the body.
    """
    thin, minute = _thin_and_its_minute()
    off, on = _laws()
    multiples = (10.0, 100.0, 1000.0)
    shipped = [_shock(off, thin.ticker, m * minute) for m in multiples]
    measured = [_shock(on, thin.ticker, m * minute) for m in multiples]

    assert shipped[0] == shipped[1] == shipped[2], shipped
    assert measured[0] == shipped[0], (measured[0], shipped[0])
    assert measured[1] / measured[0] == pytest.approx(math.sqrt(10.0), rel=1e-11)
    assert measured[2] / measured[0] == pytest.approx(10.0, rel=1e-11)


def test_above_the_knee_the_session_breaker_binds_before_the_cost_law_does():
    """What the repair does NOT buy, pinned rather than left to be noticed.

    The corrected law keeps charging for size, and on the thinnest name it
    charges enough that the close rails the session breaker. So `cost_bps`
    stops following the shock well before the shock stops growing, and a
    reader comparing costs at institutional size on a thin name still sees
    a surface that barely responds -- for a different reason, which is
    worth naming.

    The breaker clamps the PRICE, so it takes the impact vector with it and
    not only the cost: two railed runs are byte-identical. Measured on the
    thinnest name, `price_breaker_fraction` is 0.25 and the close moves
    +13.412, +24.879 and +24.358 per cent at ten, a hundred and a thousand
    times its average minute volume, so the second is already at the bound
    and the third is the same place. `mispricing_cap` is not what binds:
    `s` sits at about three per cent of its 0.9 there.

    Asserted as the DISAGREEMENT between the two, not as either level, so
    it stays true whatever the breaker is set to and does not pin a
    measured result. The shipped law reaches the same place by a different
    route, so this is the one property the repair does not change.

    Found by EMULATION before any build carried the dial: scaling
    `order_flow_coefficient` by sqrt(10) and by 10 is the same arithmetic
    as the multiplier growing by those factors, and it gave 1341, 2488 and
    2436 basis points where the law predicts a factor of 3.162 at each
    step. The third is LOWER than the second. Those three numbers are the
    emulation's and not this law's, which is why none of them is asserted
    here.
    """
    thin, minute = _thin_and_its_minute()
    _, on = _laws()
    multiples = (10.0, 100.0, 1000.0)
    shock = [_shock(on, thin.ticker, m * minute) for m in multiples]
    cost = [tradefloor.flow_impact(
        seed=42, universe=UNIVERSE, model=on,
        order_flow={thin.ticker: (m * minute, 0.0)}, ticks=390,
    ).cost_bps(thin.ticker) for m in multiples]

    assert shock[0] < shock[1] < shock[2], shock
    assert not (cost[0] < cost[1] < cost[2]), (
        "the breaker is supposed to bind here; if the cost now tracks the "
        "shock all the way up, this bound has moved and the write-up that "
        "says the repair is capped above the knee is stale: %r" % (cost,))


def test_the_two_worlds_differ_only_by_the_flow():
    # Baseline must be exactly the market that would have happened. If it were
    # not, the subtraction would be measuring the difference between two
    # unrelated runs.
    cf = tradefloor.flow_impact(
        seed=7, universe=UNIVERSE, order_flow={TRADED: (2e6, 0.0)}, ticks=200
    )
    plain = tradefloor.Engine(seed=7, universe=UNIVERSE)
    plain.open_market()
    plain.run_session(9, 30, 3, 200)
    plain.close_market()

    import struct
    expected = list(struct.unpack("<%dd" % (len(plain.prices()) // 8), plain.prices()))
    assert cf.baseline == expected


def test_it_is_reproducible():
    a = tradefloor.flow_impact(seed=9, universe=UNIVERSE,
                               order_flow={TRADED: (3e6, 0.0)}, ticks=150)
    b = tradefloor.flow_impact(seed=9, universe=UNIVERSE,
                               order_flow={TRADED: (3e6, 0.0)}, ticks=150)
    assert a.impact == b.impact


def test_impact_bps_is_relative_so_names_are_comparable():
    # A penny on a $3 stock and a penny on a $600 stock are not the same
    # event, so currency impact alone is not comparable across a roster.
    cf = tradefloor.flow_impact(
        seed=42, universe=UNIVERSE, order_flow={TRADED: (6e6, 0.0)}, ticks=390
    )
    i = cf.tickers.index(TRADED)
    assert cf.impact_bps[i] == pytest.approx(
        cf.impact[i] / cf.baseline[i] * 10_000
    )


def test_no_trading_is_refused():
    with pytest.raises(tradefloor.ValidationError, match="nothing to measure"):
        tradefloor.flow_impact(seed=1, universe=UNIVERSE, order_flow={})
