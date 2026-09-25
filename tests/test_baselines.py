"""The reference agents, and the Oracle as a measuring instrument.

Numbers asserted here were measured, not chosen. Where a test pins an ordering
or a ratio it is recording what the model actually does, so a change in the
model shows up as a failing test rather than as a quietly different leaderboard.
"""

import statistics
import struct

import pytest

import tradefloor
from tradefloor.baselines import (
    BuyAndHold,
    MeanReversion,
    Momentum,
    Oracle,
    RandomTrader,
    capture_ratio,
    rebalance,
    reference_agents,
)
from tradefloor.harness import Observation


UNIVERSE = tradefloor.Universe.random(40, seed=7)
SMALL = tradefloor.Universe.random(12, seed=3)


@pytest.fixture(scope="module")
def scores():
    # Seed 7, re-recorded at the 2026-08 era boundary (endogenous macro +
    # the avg_volume fix re-rolled every trajectory). On the previous
    # fixture seed (2026) momentum now outruns the oracle, 63,962 against
    # 21,473 -- a live demonstration that the oracle ceilings MISPRICING
    # capture, not trend capture, and of why `tradefloor.rank` exists: one
    # seed picks the top agent about half the time. The oracle tops 4 of 5
    # probed seeds (7, 11, 42, 99; not 2026); the fixture records one of
    # the typical ones.
    return tradefloor.evaluate(reference_agents(seed=3), seed=7,
                            universe=UNIVERSE, days=5)


#: The roster the Oracle's measurements below are made on, from 0.8.5.
ORACLE_UNIVERSE = tradefloor.Universe.random(20, seed=11)


@pytest.fixture(scope="module")
def long_scores():
    # The Oracle's own fixture, from 0.8.5: thirty days on a twenty-name
    # roster. On the default preset (pt-v20) the edge that hidden state
    # carries is market-wide and small against a day's market noise, so
    # over five days buy-and-hold's luck in a rising week beats it as often
    # as not; over thirty it is ahead of every price-only agent on 11 of 12
    # markets (rosters 3, 42 and 11, sim seeds 0-3) and positive on all 12.
    # Seed 0 here: oracle +38,457, buy_and_hold +15,602, random -64,948,
    # momentum -66,863, mean_reversion -73,014.
    return tradefloor.evaluate(reference_agents(seed=3), seed=0,
                            universe=ORACLE_UNIVERSE, days=30)


# --------------------------------------------------------------------------
# The reference set as a whole
# --------------------------------------------------------------------------


def test_every_reference_agent_runs_without_error(scores):
    assert set(scores) == {"buy_and_hold", "random", "momentum",
                           "mean_reversion", "oracle"}
    for card in scores.values():
        assert card.errors == [], card.errors
    # An error-free run of agents that never traded is not a passing run. The
    # emptiness above is satisfied just as well by every agent returning {}.
    for name, card in scores.items():
        assert card.trades > 0, name


def test_the_oracle_sets_the_ceiling(long_scores):
    scores = long_scores
    # Perfect information about mispricing beats every strategy that has to
    # infer it. If this ever fails, either the Oracle stopped reading the
    # truth column or a baseline started seeing something it should not.
    ceiling = scores["oracle"].pnl
    for name, card in scores.items():
        if name != "oracle":
            assert card.pnl < ceiling, name


def test_the_ordering_of_the_reference_set_is_the_measured_one(long_scores):
    ranked = [card.name for card in tradefloor.leaderboard(long_scores)]
    # FROM 0.8.5 on the Oracle's own thirty-day fixture: pt-v20 moved every
    # stock-specific shock into fair value, so the five-day fixture this
    # comment was written on no longer has an Oracle on top (it reads
    # buy_and_hold +2,786 against the oracle's -5,129 there). The history
    # below is the five-day fixture's under the presets it names.
    # The oracle on top is the robust part. The rest are separated by small
    # margins and have now swapped three times under model changes that left
    # everything else intact -- most recently when a stepped day stopped
    # re-opening the market at every step, which moved mean-reversion from
    # third to last on this seed (-1.09% against random's -0.15%).
    #
    # Pinned as a full order anyway, because a leaderboard IS an ordering and
    # this is the canary for it changing. If it fails on a deliberate change,
    # re-measure rather than assuming a regression -- and remember the order
    # belongs to THIS seed. `tradefloor.rank` exists because one seed picks the
    # top agent about half the time.
    #
    # Re-measured at the 2026-08 era boundary, on the fixture's new seed --
    # again when the RNG stream split joined that boundary, which re-dealt
    # every trajectory and dropped mean-reversion from third to last on
    # this seed (-38,788 against random's -3,653) -- again when the
    # GJR leverage recalibration joined the same boundary, which lifted
    # mean-reversion straight back to third (-1,758 against random's
    # -3,617 and buy_and_hold's -9,172) -- and again when the
    # market-factor variance process joined it, which swapped the bottom
    # pair: random now pays the widest noise floor (-6,125 against
    # buy_and_hold's -116, with mean_reversion third at +273). The oracle
    # and momentum have never swapped.
    #
    # Re-measured again at the 2026-08-26 era boundary that made pt-v10 the
    # default: oracle +72,530, momentum +17,219, mean_reversion +16,886,
    # random -6,881, buy_and_hold -8,121. The bottom pair swapped back, and
    # momentum and mean-reversion are now within 2% of each other, which is
    # the margin this comment keeps warning about.
    #
    # Re-measured again at the pt-v12 boundary (§114), where unpinning the
    # volume response to the size of a move re-dealt every trajectory:
    # oracle +7.455%, mean_reversion +1.777%, momentum +0.801%,
    # buy_and_hold -0.396%, random -0.923%. Momentum and mean-reversion
    # swapped for the fourth time, which is the margin this comment has been
    # warning about since it was written; the oracle has never moved.
    # Re-measured again at the 0.6.0 boundary that made pt-v16 the default:
    # oracle +6.290%, momentum +1.415%, mean_reversion +1.321%, random
    # -0.856%, buy_and_hold -1.521%. Momentum and mean-reversion swapped for
    # the fifth time, 0.094 points apart, and the bottom pair swapped with
    # them. Under pt-v14 the same seed read oracle +4.874%, mean_reversion
    # +1.801%, momentum +1.449%, buy_and_hold -0.588%, random -0.988%. The
    # oracle has still never moved.
    #
    # Re-measured again when the universe generator was reconciled to open a
    # drawn roster at its own fair value, which re-drew every generated
    # name's earnings and book value and so every trajectory: oracle
    # +7.132%, mean_reversion +2.450%, momentum +1.932%, random -0.735%,
    # buy_and_hold -1.103%. Momentum and mean-reversion swapped for the
    # sixth time, 0.518 points apart, which is the widest that pair has been
    # at a swap. The bottom pair held and the oracle has still never moved.
    #
    # Re-measured again at the 0.7.0 boundary that made pt-v18 the default:
    # oracle +7.185%, momentum +1.736%, mean_reversion +1.680%,
    # buy_and_hold -0.123%, random -0.848%. Momentum and mean-reversion
    # swapped for the SEVENTH time, 0.056 points apart -- the narrowest
    # margin at any swap so far, and the clearest reading yet of why this
    # comment keeps warning about that pair. The bottom pair swapped too.
    # The oracle has still never moved.
    #
    # Re-measured again at the 0.8.0 boundary that made pt-v19 the default:
    # oracle +7.148%, mean_reversion +2.305%, momentum +2.235%,
    # buy_and_hold -0.018%, random -1.103%. Momentum and mean-reversion
    # swapped for the EIGHTH time, 0.070 points apart, which is the second
    # narrowest margin at any swap and lands the pair back the way pt-v12
    # had them. The bottom pair held and the oracle has still never moved.
    #
    # What moved the whole board is worth naming, because it is the same
    # mechanism behind three other re-measurements at this boundary: under
    # `vix_level_identity` the VIX anchor is DERIVED from the roster rather
    # than read off a dial, and a derived anchor opens above where the VIX
    # settles. On this roster it opens at 18.66 against pt-v18's flat 15.98
    # and decays toward it over fifteen to twenty sessions. A five-day run
    # lives entirely inside that opening window, so every agent here is
    # graded on a market roughly a fifth more volatile than the one the
    # preset was certified on at 252 and 504 days.
    #
    # Re-measured again when `market::index_var` took the downside
    # transmission tilt and its lagged wire into the read-back: oracle
    # +7.527%, momentum +1.954%, mean_reversion +1.920%, buy_and_hold
    # -0.100%, random -1.146%. Momentum and mean-reversion swapped for the
    # NINTH time, 0.034 points apart -- narrower than the 0.056 that was
    # the record two boundaries ago, and about half of it. The bottom pair
    # held and the oracle has still never moved.
    #
    # The same mechanism as the paragraph above, one turn further on: the
    # derived anchor goes 20.17 to 22.23 on this roster, because the tilt
    # multiplies the read-back's market block by 1.891 on the session after
    # a down market factor and the unconditional variance now averages over
    # that coin. The opening VIX is 20.18 against the anchor's 22.23, so a
    # five-day run still lives inside the window the previous re-measurement
    # named, and the volatility it is graded on has risen again.
    # Re-measured again when pt-v19 took its final three dials -- the
    # excursion switch, the derived ceiling and the tape's GARCH
    # coefficients: oracle +7.451%, mean_reversion +2.569%, momentum
    # +0.677%, buy_and_hold -0.209%, random -1.209%. Momentum and
    # mean-reversion swapped for the TENTH time, and this one is not the
    # narrow margin every swap before it was: **1.892 points apart**,
    # against 0.518 at the widest previous swap. Momentum fell from +1.954%
    # while mean-reversion rose from +1.920%, so the pair separated rather
    # than crossing.
    #
    # That is the coefficients and not the ceiling. `market_vol_alpha` goes
    # 0.28035 to 0.1059 and `market_vol_beta` 0.69245 to 0.8787, so the
    # factor's variance persistence rises (alpha + beta 0.9728 -> 0.9846)
    # while its per-shock burstiness falls by nearly two thirds. A
    # five-session window on a smoother, more persistent variance path has
    # less of the tick-to-tick reversal momentum trades and more of the
    # drift-to-fair mean-reversion trades, which is the direction this pair
    # moved. The oracle has still never moved.
    # Re-measured again when pt-v19 took the tape's GJR triple: oracle
    # +7.667%, momentum +2.969%, mean_reversion +1.744%, buy_and_hold
    # -0.203%, random -1.012%. Momentum and mean-reversion swapped for the
    # ELEVENTH time, 1.225 points apart, and back to the order pt-v18 had.
    #
    # It is the same pair moving for the same reason as last time, in
    # reverse. The symmetric fit's alpha 0.1059 reacted to every shock;
    # the GJR's loads `alpha + gamma` = 0.1622 on a DOWN day and 0.0066 on
    # an up one. A five-session window therefore has its variance
    # concentrated behind the down moves, which is where momentum's
    # continuation signal lives and where mean-reversion's snap-back does
    # not. The oracle has still never moved, in eleven swaps.
    #
    # Re-measured again at pt-v19's fifth composition (2026-09-23: the
    # anchor form of the VIX law, the live down-day wire at 0.46, the macro
    # session clock and US cycle with the drawn opening, news priced within
    # minutes): oracle +7.664%, mean_reversion +2.663%, momentum +2.156%,
    # buy_and_hold +0.317%, random -1.124%. Momentum and mean-reversion
    # swapped for the TWELFTH time, 0.507 points apart; until this
    # re-measurement the pin read oracle, momentum, mean_reversion,
    # buy_and_hold, random. No one group of the composition does it alone:
    # each of the four groups added by itself to the fourth composition
    # leaves momentum ahead, and taking the lag wire, the macro group or the
    # news group back out of the fifth puts momentum ahead again. The
    # bottom pair held, buy_and_hold now just above flat, and the oracle has
    # still never moved, in twelve swaps.
    #
    # Re-measured at 0.8.5, when an agent's fills stopped being held on
    # every tick of the step and reached the market once (`fills=` on
    # `run_session`): oracle +4.894%, buy_and_hold +0.244%, momentum
    # -1.970%, random -2.127%, mean_reversion -2.647%. Until this
    # re-measurement the pin read oracle, mean_reversion, momentum,
    # buy_and_hold, random, at +7.664, +2.663, +2.156, +0.317 and -1.124.
    # This is not a thirteenth swap of the same kind. Every agent that
    # trades lost the tailwind of its own impact, held 65 times on the
    # prices it was marked at, and the two signal traders lost most because
    # they trade most: their `impact_bps` goes +19.94 and +25.79 to -0.83
    # and +2.20, the oracle's +206.34 to +2.97. Over five days neither
    # signal pays its costs, which is what a price-only rule should do.
    # The oracle has still never moved.
    #
    # Re-measured when pt-v20 became the default: oracle +0.703%,
    # buy_and_hold +0.279%, momentum -0.947%, random -1.766%,
    # mean_reversion -1.941%. The order held. The oracle's lead shrank from
    # 4.65 points to 0.42, because pt-v20 moves each name's own shocks into
    # its fair value and leaves little cross-sectional mispricing to trade.
    #
    # Then the Oracle was redesigned for pt-v20 (0.8.5): it trades what
    # still predicts returns there, the market-wide transient mispricing,
    # the herding term and the fair value's drift, and the ordering is read
    # on its thirty-day fixture: oracle +36,851, buy_and_hold +19,420,
    # random -66,082, momentum -71,641, mean_reversion -72,199. Then pt-v20's
    # dials were finalised (volume response 0.6, garch_beta 0.85) and the
    # bottom pair is momentum over mean reversion: oracle +38,457,
    # buy_and_hold +15,602, random -64,948, momentum -66,863,
    # mean_reversion -73,014. The top three have held throughout.
    assert ranked == ["oracle", "buy_and_hold", "random", "momentum",
                      "mean_reversion"]


def test_random_trading_is_close_to_flat_over_a_short_run(scores):
    # The noise floor really is a floor: a coin flip neither makes nor loses
    # much over five days, it just pays costs. Any strategy near this number
    # is measuring its own transaction costs.
    #
    # The bound was 0.5% before the market-factor variance process, and 1.0%
    # from there to 0.7.0, because a random book carries market beta that no
    # longer diversifies away -- the correlated share of every name is a
    # third of its variance, so forty coin-flip positions keep a net exposure
    # the factor's regimes move.
    #
    # 1.25% since 0.8.0, and the cause is NOT costs. Measured across twelve
    # seeds (1, 2, 3, 5, 7, 11, 13, 17, 42, 99, 101, 2026) at this boundary:
    # trade count is unchanged at about 1194, impact per trade FELL from a
    # mean 8.89 bps to 7.47, and buy-and-hold is essentially unchanged
    # seed-for-seed, so the market's direction did not move either. What
    # moved is the mean: -0.305% to -0.423%, with the spread of |return|
    # going 0.386% to 0.487% and its worst seed 0.869% to 1.103%. That is
    # variance drag on an undiversified book in a window roughly a fifth
    # more volatile -- the same mechanism that raised this bound once
    # before, arriving by a different route. Under `vix_level_identity` the
    # VIX anchor is DERIVED from the roster rather than read off a dial, and
    # a derived anchor opens above where the VIX settles: 18.66 on this
    # roster against pt-v18's flat 15.98, decaying toward it over fifteen to
    # twenty sessions. A five-day run sits entirely inside that window.
    #
    # 1.25 and not the measured 1.103: a floor asserted at its own worst
    # observed seed is a floor that fails on the thirteenth seed anybody
    # tries. The floor's meaning is relative anyway -- an order of magnitude
    # under the oracle on the same seed and horizon, which the ratio below
    # measures properly across seeds.
    #
    # 2.5 since 0.8.5, and this time it IS costs. An agent's fills now reach
    # the market once instead of on every tick of the step, so a random
    # book no longer marks its own positions up by the impact it made. On
    # the same twelve seeds the mean goes -0.495% to -1.522% and the worst
    # seed 1.133% to 2.200% (seed 11), with the trade count unchanged at
    # 1,195. 2.5 is that worst seed plus the same margin 1.25 gave 1.103.
    assert abs(scores["random"].return_pct) < 2.5

    # The RATIO is measured across seeds, not on the fixture's one.
    #
    # It was a single-seed assertion until the 2026-08-28 boundary and passed
    # because seed 7 suited pt-v12. It was never a single-seed property:
    # across seeds 7, 11, 42, 99, 3 and 5 the ratio exceeds 0.2 on one seed
    # for pt-v12 (seed 11) and two for pt-v14 (seeds 7 and 11) -- while
    # pt-v14's MEDIAN is the better of the two, 0.090 against 0.126. A
    # fixture seed decided which preset looked worse.
    #
    # What the claim actually is: random trading sits well below perfect
    # foresight typically, not on every draw. Five days is short enough that
    # one seed's oracle can have little mispricing to capture.
    #
    # Re-measured at 0.8.5, when fills stopped being held on every tick of
    # the step. Both halves of the ratio moved against it: the oracle no
    # longer collects its own impact (its five-day return on these six
    # seeds falls from a median 5.30% to 3.68%) and random no longer
    # offsets its costs with it. The ratios go 0.147, 0.308, 0.084, 0.112,
    # 0.126, 0.005 (median 0.119) to 0.435, 1.375, 0.370, 0.481, 0.435,
    # 0.216 (median 0.435). Over five days a coin flip now costs about
    # four tenths of what perfect information earns, and on seed 11, where
    # the oracle makes only 1.6%, more than all of it. So the claim is
    # restated at what the market does: random sits below the oracle's
    # gain at the median and on all but one of the six seeds.
    #
    # From 0.8.5 the ratio is read over thirty days on the Oracle's roster.
    # On pt-v20 the Oracle's edge is market-wide and small against five days
    # of market noise, so a five-day denominator is as often negative as not
    # (ratios 3.16 at the median on the six seeds above). Over thirty days
    # on sim seeds 0-5 random loses 5.1 to 9.7 per cent to costs while the
    # Oracle makes 2.8 to 16.7, every seed. So the claim that survives is the
    # ordering, on every seed, rather than a ratio of two numbers of opposite
    # sign.
    for seed in range(6):
        sc = tradefloor.evaluate(reference_agents(seed=3), seed=seed,
                              universe=ORACLE_UNIVERSE, days=30)
        assert sc["oracle"].pnl > 0, seed
        assert sc["random"].pnl < sc["oracle"].pnl, seed


def test_random_trading_bleeds_over_a_longer_run():
    # And over sixty days the costs compound into a real loss. This is why
    # "beat random" is a weaker bar than it sounds over short horizons and a
    # meaningful one over long ones.
    #
    # A claim about an average, pinned on one seed, so the seed matters: at
    # the stream-split re-deal, seed 2026 -- used here previously -- became
    # the lucky one, +22,938 while seeds 1, 2, 3, 7, 11, 42 and 99 all bleed
    # (mean -45,437 across the eight). Moved to the fixture's seed 7
    # (-50,496) rather than weakened to a tolerance: a coin flip that PROFITS
    # on the pinned seed would still deserve a failure here.
    long_run = tradefloor.evaluate({"random": RandomTrader(seed=3)}, seed=7,
                                universe=UNIVERSE, days=60)
    assert long_run["random"].pnl < 0


def test_a_capture_ratio_is_meaningless_without_its_horizon():
    """The measurement that says why the ratio must be quoted with a horizon.

    Mispricing mean-reverts on a sixty-day half-life, so a five-day evaluation
    sees only the beginning of the convergence the Oracle is trading -- and
    momentum, whose signal is the price trend that convergence produces, sees
    even less of it. Measured on this seed: momentum captures 81% of the
    reference over five days and 110% over sixty.

    The same agent, the same market, the same Oracle. Only the horizon
    changed, and the headline number moved by a third.

    Re-measured four times now: after the harness began advancing its clock
    within the day (27% and 94% before that change); at the 2026-08 era
    boundary, which also moved the test off seed 2026; when the
    market-factor variance process joined the same boundary; and at the
    pt-v3 boundary, which broke the DIRECTION the earlier three preserved.

    Under pt-v1 the far ratio exceeded the near one on 4 of 4 probe seeds,
    because momentum rode a +0.243 return autocorrelation that compounded
    with horizon. pt-v3 takes that to +0.084 and the sign becomes seed
    luck: gaps of -0.193, -0.144, +0.030, +0.159 on seeds 7, 11, 42, 99.

    So the direction was never the finding -- it was a property of a
    momentum edge this model no longer has. The finding is the MAGNITUDE,
    and it is stronger than before: the same agent in the same market
    against the same Oracle moves its headline number by at least 0.11, and
    by as much as 0.28, on horizon alone. A capture ratio quoted without its
    horizon is meaningless in either direction, and this test is
    named for.
    """
    # From 0.8.5 on the Oracle's roster, sim seeds 0-3. On pt-v20 a five-day
    # Oracle can lose money (seeds 1 and 3 here: -2,909 and -12,102), and a
    # ratio against a negative denominator is not a ratio, which is itself
    # the horizon warning at its strongest; the gap is read where both ends
    # are measurable. The Oracle's own P&L grows with the horizon on every
    # seed: 1,631 to 52,632, -2,909 to 9,082, 9,639 to 72,346 and -12,102
    # to 298,219.
    gaps = []
    for seed in range(4):
        short = tradefloor.evaluate({"oracle": Oracle(), "momentum": Momentum()},
                                 seed=seed, universe=ORACLE_UNIVERSE, days=5)
        long = tradefloor.evaluate({"oracle": Oracle(), "momentum": Momentum()},
                                seed=seed, universe=ORACLE_UNIVERSE, days=60)
        assert long["oracle"].pnl > short["oracle"].pnl
        near = capture_ratio(short).get("momentum")
        far = capture_ratio(long).get("momentum")
        if near is not None and far is not None:
            gaps.append(far - near)
    assert len(gaps) >= 2, gaps
    # The MEDIAN gap, not the worst one. Any single seed can come out quiet
    # -- seed 42 reads 0.030 here -- and a threshold pinned to the weakest
    # seed is a threshold fitted to whichever vector happened to ship.
    assert statistics.median(abs(g) for g in gaps) > 0.10, gaps
    # A gap worth the warning, rather than a threshold on the near value. That
    # was `< 0.55` against a measured 0.677 once the clock started advancing:
    # a test calibrated to a number rather than to the effect it names. The
    # signed form of this assertion went the same way at pt-v3 -- it was
    # measuring momentum's compounding edge, not the horizon sensitivity it
    # was named for -- so what is asserted is the worst gap across the four
    # probe seeds, in whichever direction that seed took it.
    typical = statistics.median(abs(g) for g in gaps)
    assert typical > 0.10, (
        f"the horizon moved capture by only {typical:.3f} at the median probe "
        "seed; the caveat this test exists to justify is no longer warranted"
    )


def test_the_reference_agents_stay_inside_the_leverage_limit(scores):
    # A baseline that fights the leverage cap is not measuring its signal, it
    # is measuring the cap. This failed before `_book` targeted the whole
    # roster: the trend agents never unwound names that dropped out of their
    # top-k, so gross exposure ratcheted up and 72 and 82 trades were refused.
    for card in scores.values():
        assert card.rejected == 0, (card.name, card.rejected)
        assert card.max_leverage <= 2.0


# --------------------------------------------------------------------------
# The Oracle's explanation is a self-test of the scorer
# --------------------------------------------------------------------------


def test_the_oracle_explains_itself_perfectly(scores):
    # Correct by construction: it reads the attribution the scorer checks
    # against. A value below 1.0 means the explanation scorer is broken, not
    # that the Oracle guessed wrong.
    assert scores["oracle"].explanation_accuracy == 1.0
    assert len(scores["oracle"].explanations) == 5


def test_an_agent_without_explain_is_not_scored_on_it(scores):
    # None, not zero. An agent that made no claim did not make a wrong one.
    assert scores["momentum"].explanation_accuracy is None


def test_the_oracle_explains_nothing_before_it_has_acted():
    assert Oracle().explain(0) is None


# --------------------------------------------------------------------------
# Determinism
# --------------------------------------------------------------------------


def test_the_whole_evaluation_is_reproducible():
    a = tradefloor.evaluate(reference_agents(seed=3), seed=11, universe=SMALL, days=3)
    b = tradefloor.evaluate(reference_agents(seed=3), seed=11, universe=SMALL, days=3)
    assert {k: v.pnl for k, v in a.items()} == {k: v.pnl for k, v in b.items()}


def test_the_random_baseline_is_seeded_not_random():
    a = tradefloor.evaluate({"r": RandomTrader(seed=5)}, seed=11, universe=SMALL, days=3)
    b = tradefloor.evaluate({"r": RandomTrader(seed=5)}, seed=11, universe=SMALL, days=3)
    c = tradefloor.evaluate({"r": RandomTrader(seed=6)}, seed=11, universe=SMALL, days=3)
    assert a["r"].pnl == b["r"].pnl
    assert a["r"].pnl != c["r"].pnl


def test_no_agent_perturbs_the_market_s_draw_schedule():
    """The property the whole same-seed comparison rests on.

    Order flow consumes zero RNG draws, so every agent -- and nobody at all --
    faces the identical sequence of market events. If trading shifted the draw
    schedule, two agents would be running two different experiments and the
    leaderboard would be comparing markets rather than strategies.

    Measured across the full reference set plus an untraded run: 103,740 draws
    in every case.
    """
    counts = set()
    for agent in list(reference_agents(seed=3).values()) + [None]:
        engine = tradefloor.Engine(seed=99, universe=SMALL)
        portfolio = tradefloor.Portfolio(cash=1_000_000.0, max_leverage=2.0)
        adv = [i.avg_volume for i in SMALL]
        for day in range(3):
            engine.open_market()
            for step in range(4):
                if agent is not None:
                    prices = list(struct.unpack("<%dd" % len(SMALL),
                                                engine.prices()))
                    obs = Observation(step, day, list(engine.tickers), prices,
                                      portfolio, engine, adv)
                    for ticker, quantity in agent.act(obs).items():
                        try:
                            portfolio.execute(engine, ticker, quantity)
                        except (tradefloor.OrderError, tradefloor.ValidationError):
                            pass
                engine.run_session(9, 30, 3, 65,
                                   fills=portfolio.pending_flow())
                portfolio.clear_flow()
            engine.close_market()
        counts.add(engine.draws_consumed)
    assert len(counts) == 1, counts


# --------------------------------------------------------------------------
# Individual agents
# --------------------------------------------------------------------------


def test_buy_and_hold_trades_once_and_stops(scores):
    # Not rebalanced, deliberately: a rebalanced equal-weight portfolio is a
    # mean-reversion strategy in disguise and would stop being the null
    # hypothesis.
    assert scores["buy_and_hold"].trades == len(UNIVERSE)


def test_a_trend_agent_holds_cash_through_its_warm_up():
    agent = Momentum(lookback=3)
    engine = tradefloor.Engine(seed=1, universe=SMALL)
    portfolio = tradefloor.Portfolio()
    prices = list(struct.unpack("<%dd" % len(SMALL), engine.prices()))
    adv = [i.avg_volume for i in SMALL]
    for step in range(3):
        obs = Observation(step, 0, list(engine.tickers), prices, portfolio,
                          engine, adv)
        # Guessing during warm-up would make the first few steps measure the
        # guess rather than the signal.
        assert agent.act(obs) == {}
    obs = Observation(3, 0, list(engine.tickers), prices, portfolio, engine, adv)
    agent.act(obs)  # the fourth observation completes the lookback


def test_momentum_and_reversion_are_exact_opposites():
    # Driven with synthetic prices rather than a live engine, so the returns
    # are known: AAA..AAF rise by increasing amounts, AAG..AAL fall. Whoever
    # momentum goes long, reversion must go short.
    engine = tradefloor.Engine(seed=4, universe=SMALL)
    portfolio = tradefloor.Portfolio()
    adv = [i.avg_volume for i in SMALL]
    tickers = list(engine.tickers)
    first = [100.0] * len(tickers)
    second = [100.0 + (i - len(tickers) / 2) for i in range(len(tickers))]

    up, down = Momentum(lookback=1, top_k=3), MeanReversion(lookback=1, top_k=3)
    warm = Observation(0, 0, tickers, first, portfolio, engine, adv)
    assert up.act(warm) == {} and down.act(warm) == {}

    # Exactly one further observation: a third would append a duplicate, make
    # every return zero, and quietly turn the assertion into a test of the
    # alphabetical tie-break.
    obs = Observation(1, 0, tickers, second, portfolio, engine, adv)
    winners = {t for t, q in up.act(obs).items() if q > 0}
    shorted = {t for t, q in down.act(obs).items() if q < 0}
    assert winners == {"AAJ", "AAK", "AAL"}
    assert winners == shorted


def test_a_lookback_below_one_is_refused():
    with pytest.raises(ValueError, match="lookback"):
        Momentum(lookback=0)


def test_only_the_oracle_is_marked_privileged():
    assert Oracle.privileged is True
    for agent in (BuyAndHold(), RandomTrader(), Momentum(), MeanReversion()):
        assert not getattr(agent, "privileged", False)


# --------------------------------------------------------------------------
# rebalance
# --------------------------------------------------------------------------


def test_rebalance_caps_at_the_participation_limit():
    engine = tradefloor.Engine(seed=1, universe=SMALL)
    portfolio = tradefloor.Portfolio(cash=1e12)   # enough to want far too much
    prices = list(struct.unpack("<%dd" % len(SMALL), engine.prices()))
    adv = [i.avg_volume for i in SMALL]
    obs = Observation(0, 0, list(engine.tickers), prices, portfolio, engine, adv)
    orders = rebalance(obs, {t: 1.0 for t in obs.tickers}, max_participation=0.01)
    for ticker, quantity in orders.items():
        assert obs.participation(ticker, quantity) <= 0.01 + 1e-9


def test_rebalance_ignores_dust():
    engine = tradefloor.Engine(seed=1, universe=SMALL)
    portfolio = tradefloor.Portfolio()
    prices = list(struct.unpack("<%dd" % len(SMALL), engine.prices()))
    adv = [i.avg_volume for i in SMALL]
    obs = Observation(0, 0, list(engine.tickers), prices, portfolio, engine, adv)
    assert rebalance(obs, {t: 0.0 for t in obs.tickers}) == {}


# --------------------------------------------------------------------------
# capture_ratio
# --------------------------------------------------------------------------


def test_capture_ratio_is_a_fraction_of_the_ceiling(long_scores):
    scores = long_scores
    ratios = capture_ratio(scores)
    assert "oracle" not in ratios
    assert ratios["momentum"] == pytest.approx(
        scores["momentum"].pnl / scores["oracle"].pnl)
    # Every agent below the ceiling on this fixture, and buy-and-hold a
    # fraction of it. The fraction was momentum's until 0.8.5 (0.281); with
    # its fills applied once it loses money over these five days (-0.402),
    # and a negative capture is a loss, not a fraction.
    assert all(r < 1.0 for r in ratios.values()), ratios
    assert 0.0 < ratios["buy_and_hold"] < 1.0


def test_capture_ratio_declines_to_answer_when_the_oracle_lost_money():
    # A ratio against a negative denominator flips sign and ranks the worst
    # agent first. Saying "not measurable here" is true; a confidently wrong
    # table is not.
    class Card:
        def __init__(self, pnl):
            self.pnl = pnl

    assert capture_ratio({"oracle": Card(-1.0), "a": Card(-2.0)}) == {}
    assert capture_ratio({"a": Card(1.0)}) == {}


# --------------------------------------------------------------------------
# Horizons, cadence, and the cost of trading often
# --------------------------------------------------------------------------


def test_lookback_is_counted_in_steps_not_days():
    # The agent sees one observation per decision step, so six is six STEPS.
    # It equals a day only when steps_per_day is six -- which is the harness
    # default, so the default agent is a one-day trader by two defaults
    # happening to match rather than by contract.
    agent = Momentum(lookback=3)
    engine = tradefloor.Engine(seed=1, universe=SMALL)
    portfolio = tradefloor.Portfolio()
    prices = list(struct.unpack("<%dd" % len(SMALL), engine.prices()))
    adv = [i.avg_volume for i in SMALL]
    for step in range(3):
        assert agent.act(Observation(step, 0, list(engine.tickers), prices,
                                     portfolio, engine, adv, 6)) == {}
    # The fourth observation completes three steps of history, regardless of
    # how many days that is.
    agent.act(Observation(3, 0, list(engine.tickers), prices, portfolio,
                          engine, adv, 6))


def test_lookback_days_holds_a_horizon_across_cadences():
    """Say what you mean, and it survives a change to steps_per_day.

    Resolved from the observation rather than at construction, because the
    agent cannot know the harness's cadence until it is handed one.
    """
    for steps_per_day, expected in ((3, 3), (6, 6), (12, 12)):
        agent = Momentum(lookback_days=1.0)
        engine = tradefloor.Engine(seed=1, universe=SMALL)
        portfolio = tradefloor.Portfolio()
        prices = list(struct.unpack("<%dd" % len(SMALL), engine.prices()))
        adv = [i.avg_volume for i in SMALL]
        agent.act(Observation(0, 0, list(engine.tickers), prices, portfolio,
                              engine, adv, steps_per_day))
        assert agent.lookback == expected


def test_trading_more_often_loses_money_on_the_same_signal():
    """The impact model making "trade more" unprofitable, on its own.

    Horizon held at exactly one day; only the rebalance frequency changes.
    Measured on seed 2026, 40 instruments, 30 days, on pt-v20:

        3 steps/day    -8.65%    (pt-v19: -13.24%)
        6 steps/day   -12.82%    (pt-v19: -27.36%)
       12 steps/day   -23.69%    (pt-v19: -46.53%)

    Re-measured after a stepped day stopped re-opening the market at every
    step (then +49.71, +32.70 and +0.18), and again when pt-v20 became the
    default. The numbers moved and the shape did not: this asserts the
    ORDERING, and the ordering is the mechanism.

    The spread narrows on pt-v20, from 33.3 points to 15.0, and the tape is
    why. pt-v20 centres the quote on the model price, which takes out the
    one-step reversal pt-v19's prints carried (a 65-minute lag-one
    autocorrelation of -0.187), and a momentum rule that rebalances every
    step bought into that reversal each time. pt-v20 with
    `quote_model_weight` and `closing_auction` back at 0.0 reads -21.45,
    -33.67 and -51.03, a spread of 29.6. What is left on pt-v20 is the cost
    of crossing the book, which still grows with the rebalance count.

    Nothing charges a fee. The orders simply cross a real spread and consume
    real depth four times as often. This is the same mechanism that makes
    "trade bigger" unprofitable, and an agent cannot win here by
    turning the dial up.
    """
    returns = []
    for steps_per_day in (3, 6, 12):
        scores = tradefloor.evaluate(
            {"m": Momentum(lookback_days=1.0)}, seed=2026, universe=UNIVERSE,
            days=30, steps_per_day=steps_per_day,
            ticks_per_step=390 // steps_per_day)
        returns.append(scores["m"].return_pct)
    # The ORDERING is the mechanism and is asserted strictly.
    assert returns[0] > returns[1] > returns[2]
    # The size matters too -- a monotone decline of a basis point would be
    # technically ordered and mean nothing -- but it is asserted as a spread
    # rather than as two absolute thresholds. Those were `> 50.0` and `< 0.0`
    # against measured values of +49.71 and +0.18: a knife edge that failed on
    # a change to something else entirely, which is a test measuring its own
    # calibration rather than the model.
    #
    # 11.0 on pt-v20, which measures 15.0 (pt-v19: 25.0 against 33.3), about
    # the same share of the measurement the old bound was.
    assert returns[0] - returns[2] > 11.0, (
        f"quadrupling the trade rate cost only {returns[0] - returns[2]:.1f} "
        "points; the impact model has stopped biting"
    )


def test_the_observation_carries_the_cadence():
    # Without it, "a one-day lookback" is unwriteable except by hard-coding
    # the harness default and hoping nobody changes it.
    seen = []

    class Probe:
        def act(self, obs):
            seen.append(obs.steps_per_day)
            return {}

    tradefloor.evaluate({"p": Probe()}, seed=1, universe=SMALL, days=2,
                     steps_per_day=4, ticks_per_step=20)
    assert set(seen) == {4}


def test_a_nonsense_lookback_days_is_refused():
    with pytest.raises(ValueError, match="lookback_days"):
        Momentum(lookback_days=0.0)
    with pytest.raises(ValueError, match="lookback_days"):
        MeanReversion(lookback_days=-1.0)


# --------------------------------------------------------------------------
# The Oracle is a reference, not a maximum
# --------------------------------------------------------------------------


def test_no_reference_agent_beats_the_oracle_once_it_pays_its_own_impact():
    """What the reference agents can do against perfect information.

    Until 0.8.5 this test was `the Oracle is beaten only by agents that
    trade a signal`, and on this grid the signal traders did beat it: 4 of
    16 agent-market pairs under 0.8.1, and buy-and-hold and random never.
    The beats were the harness, not the signal. Every harness held an
    agent's fills on every tick of the step, so the busiest traders
    collected the most of their own impact, and mean reversion, which buys
    the names it just pushed down and sells the ones it pushed up, collected
    it into its own signal. With the fills applied once, nothing beats the
    Oracle here: 0 of 16 for the signal traders and 0 of 16 for the rest.

    Pinned at zero because zero is the finding. A price-only rule that
    out-earns a perfectly informed reference under the same constraints
    should now be a surprise worth reading, and the first thing to check
    is whether its own flow is reaching the market more than once.
    """
    signal_traders = 0
    non_traders = 0
    measurable = 0
    for useed in (3, 42):
        universe = tradefloor.Universe.random(20, seed=useed)
        for seed in range(4):
            scores = tradefloor.evaluate(reference_agents(seed=3), seed=seed,
                                      universe=universe, days=30)
            ratios = capture_ratio(scores)
            if not ratios:
                continue
            measurable += 1
            signal_traders += sum(
                ratios[n] > 1.0 for n in ("mean_reversion", "momentum")
                if n in ratios)
            non_traders += sum(
                ratios[n] > 1.0 for n in ("buy_and_hold", "random")
                if n in ratios)
    assert measurable == 8, "the oracle lost money somewhere; nothing was measured there"
    # From 0.8.5 (pt-v20, the Oracle trading the market-wide state): one of
    # the eight is buy-and-hold's, on roster 3 at sim seed 0, where the
    # market rose 2.2 per cent in the month and the Oracle, holding a
    # smaller net long, made 1.4. A market-wide edge of a few basis points a
    # day does not out-run a lucky month; it wins the other seven.
    assert non_traders <= 1, f"an agent trading no signal beat the Oracle {non_traders} times"
    assert signal_traders == 0, (
        f"a price-only signal beat the Oracle {signal_traders} times in 16; "
        "check that no harness applies an agent's fills more than once"
    )


def test_an_agent_can_beat_the_oracle():
    """Pinned so nobody "fixes" it into an upper bound.

    The Oracle sees the true mispricing, and it does not follow that nothing
    can beat it. It gets the same gross exposure and participation cap as
    every other baseline and spends them on a naive rule -- equal weight
    across the top_k most mispriced names -- so an agent whose selection suits
    the constraint better out-earns it.

    Measured across eight seeds until 0.8.5: momentum beat it twice and
    mean-reversion once. Those beats were the reference agents collecting
    their own impact, and with an agent's fills applied once none of them
    beats it on these eight markets. The point survives with the same
    information spent differently: three names a side instead of five, at
    the same gross and participation cap, out-earns the default on 6 of the
    8 (ratios 1.04, 1.20, 1.16, 1.10, 1.20, 1.00, 1.18, 0.94). Asserted as
    existing rather than as a count, because the count is a property of
    the seeds.
    """
    universe = tradefloor.Universe.random(30, seed=11)
    beaten = 0
    for seed in range(8):
        scores = tradefloor.evaluate({"oracle": Oracle(),
                                      "narrower": Oracle(top_k=3)},
                                     seed=seed, universe=universe, days=10)
        if scores["narrower"].pnl > scores["oracle"].pnl:
            beaten += 1
    assert beaten > 0, (
        "nothing beat the Oracle in eight seeds -- either the baselines got "
        "worse or the Oracle stopped being a same-constraints reference"
    )


def test_the_oracle_is_capital_limited_not_information_limited():
    """What actually raises the ceiling is gross exposure, not information.

    Spreading the SAME perfect information across three times as many names
    makes it worse -- each position shrinks and turnover rises. Doubling the
    gross makes it dominate. That is the evidence for calling it a reference
    portfolio rather than a maximum.
    """
    universe = tradefloor.Universe.random(30, seed=11)

    def median_pnl(make_oracle):
        pnls = []
        for seed in range(4):
            scores = tradefloor.evaluate({"o": make_oracle()}, seed=seed,
                                      universe=universe, days=10)
            pnls.append(scores["o"].pnl)
        return statistics.median(pnls)

    narrow = median_pnl(lambda: Oracle())
    wide = median_pnl(lambda: Oracle(top_k=15))
    levered = median_pnl(lambda: Oracle(top_k=15, gross=2.0))

    assert wide < narrow, "spreading the same information should dilute it"
    assert levered > narrow, "gross exposure is what raises the ceiling"


def test_capture_ratio_reports_above_one_rather_than_clamping():
    # A ratio above 1.0 is a finding about portfolio construction. Clamping it
    # to 1.0 would hide the most interesting result the harness can produce.
    class Card:
        def __init__(self, pnl):
            self.pnl = pnl

    ratios = capture_ratio({"oracle": Card(100.0), "better": Card(150.0)})
    assert ratios["better"] == pytest.approx(1.5)
