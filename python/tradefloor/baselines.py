"""Reference agents, and a ceiling to measure the others against.

A leaderboard of one agent is not a measurement. `evaluate` will happily
report that your strategy made $47,000 and that number means nothing on its
own: it does not say whether the market simply went up, whether random trading
would have done as well, or whether $47,000 was most of what was available or
a tenth of it.

These are the reference points that make a score readable, from the bottom up:

- **BuyAndHold**: did the strategy beat owning the market? The null
  hypothesis, and the one most strategies quietly fail.
- **RandomTrader**: did it beat noise? A strategy that cannot beat coin flips
  is measuring its own transaction costs.
- **Momentum** and **MeanReversion**: did it beat the two simplest things
  anyone would try first?
- **Oracle**: how much was available *at all*?

## On pt-v20 the headline is buy-and-hold

The Oracle answers that last question only where hidden state predicts
returns, which is every preset through pt-v19. On pt-v20 market moves
mostly stick: each shock moves fair value for good, so even perfect
knowledge of the model's fair value leaves little edge. The Oracle made
money in 10 of 14 test markets there, and its P&L follows the
market's month. It stays in the reference set, but :func:`capture_ratio`
reports nothing on pt-v20 (:data:`ORACLE_NOT_A_CEILING` names it, with the
reason), and a score is read against buy-and-hold with
:func:`versus_buy_and_hold`. The rest of this docstring describes the
Oracle where it is a ceiling.

## The Oracle is a reference strategy, NOT an upper bound

This needs saying first because the name invites the opposite reading, and I
made that mistake in this file's own documentation for a week.

The Oracle sees the true mispricing. It does not follow that nothing can beat
it: the same information spent on a different rule beats it, and until 0.8.5
the reference agents did too. Counted on a fully stated grid, meaning the
reference agents over ``Universe.random(30, seed=11)``, sim seeds 0 through
11, ten days each, and a beat being a capture ratio above 1.0:

                          0.8.5      0.8.1
        mean_reversion    0/12       5/12
        buy_and_hold      0/12       0/12
        momentum          0/12       0/12
        random            0/12       0/12
        largest capture   0.84       1.58

The 0.8.1 column is the harness, not the signal. Every harness held an
agent's fills on every tick of the step, 65 times at six steps a day, so an
agent was marked to its own impact, and mean reversion, which buys what it
just pushed down and sells what it pushed up, collected the most. With the
fills applied once, no agent that sees only prices beats the Oracle on this
grid, and the largest capture is buy-and-hold's 0.84. The eras before 0.8.1
named other winners -- momentum at `pt-v10`, mean reversion at `pt-v12` --
under the same harness, so read their verdicts the same way.

The durable finding is about constraints, not about the winner's name.
The default Oracle is long the five most underpriced names and short the
five most overpriced (``top_k=5`` per side) at equal weight, gross 1.0,
capped at 2% of ADV, the same budget the trend baselines get. Perfect
information does not make that the best portfolio the same gross can buy:
spent on three names a side instead of five it earns more on 6 of the 8
markets below.

Two levers, measured on the same universe (median Oracle P&L across sim
seeds 0-7, ten days; "beaten" counts the seeds where the best reference
agent out-earned that configuration; 0.8.1 in brackets):

    top_k=5,  gross=1.0  (default)   median P&L  62,937 ( 68,090)   beaten 0/8 (3/8)
    top_k=15, gross=1.0              median P&L  44,245 ( 51,318)   beaten 1/8 (8/8)
    top_k=15, gross=2.0              median P&L  90,789 (123,500)   beaten 0/8 (0/8)

Spreading the same information across more names makes it WORSE, not better.
What makes it nearly unbeatable is doubling the gross exposure: capital,
not information. At equal constraints the Oracle is capital-limited like
everything else.

So read a capture ratio as **P&L relative to a perfectly-informed reference
portfolio under the same constraints**, not as a fraction of available alpha.
A ratio above 1.0 is a real result meaning the agent built a better portfolio
than top-k-by-mispricing. Look at it rather than explain it away.

## The Oracle cheats on purpose

Every other agent sees what a trader sees: prices, the book, its own
positions. The Oracle reads the mispricing directly out of the engine. It
knows, exactly and without estimation error, which instruments are trading
above and below fair value.

That is not a strategy and it is not competing. It is an *instrument*: it
measures how much alpha the market contains, which turns every other score
from a bare number into a fraction of what was achievable. An agent that
captures 60% of the Oracle's P&L is doing well; the same agent in a market
where the Oracle made twice as much is doing half as well as it looked.

Real markets cannot give you this. You cannot ask what perfect foresight would
have earned, because you never observe fair value. You only observe price,
and the difference between them is precisely the unobservable. Here it is a
column.

## And the ceiling is a real ceiling, not an infinite one

The Oracle cannot win by trading enormous size. Orders match against a real
book, so the price it gets moves as it consumes levels, and past some
participation the impact eats the edge that motivated the trade. That makes
the ceiling *economically* meaningful rather than merely informational: it is
the best a perfectly-informed trader could do given the liquidity that
actually exists, not the paper value of knowing everything.

Which means the Oracle's own scorecard is worth reading. If its `impact_bps`
is large, the market is thin and the headline mispricing was never harvestable
in the first place.

## Every one of these is expressible as data

The five classes here share one grammar (a signal, a concentration, an
exposure and a participation cap) and :class:`tradefloor.StrategySpec` writes it
down as versioned, hashable JSON, so a result built on these agents can cite
its strategy the way it already cites its seed and universe. Construct the
spec instead of the class when the result is going anywhere other people
will read: ``tf.StrategySpec.momentum()`` builds exactly ``Momentum()``.
"""

from __future__ import annotations

import copy
import math
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # A type alias declared in the stub. It has no runtime existence in the
    # compiled extension, so importing it unguarded would break the package
    # for everyone who is not a type checker.
    from ._core import FactorName

from ._core import Engine, GameRng, ValidationError, check_seed
from ._core import rate_specs as _rate_specs
from .harness import FACTOR_NAMES, Observation
from .sandbox import economy_of, hidden_state

# The stream the random baseline draws on. Distinct from the market stream, so
# a random agent's decisions cannot perturb the market it is trading in --
# which would make it a different experiment per agent and destroy the
# same-seed comparison the harness is built on.
RANDOM_AGENT_STREAM = 71


def _f64(buf: bytes) -> list[float]:
    import struct

    return list(struct.unpack("<%dd" % (len(buf) // 8), buf))


def rebalance(
    obs: Observation,
    weights: dict[str, float],
    *,
    max_participation: float = 0.02,
) -> dict[str, float]:
    """Turn target weights into the share deltas that reach them.

    Shared by every baseline, because getting this wrong is the usual way a
    reference agent stops being a reference. Three things it handles:

    **Weights, not share counts.** A target of "8% of net worth in AAA" means
    the same thing on day one and day two hundred; "buy 5,000 shares" does
    not, and across a generated roster whose prices span two orders of
    magnitude it does not even mean the same thing across instruments.

    **Participation capping.** Trades are clipped to a fraction of the
    instrument's average daily volume. Impact scales with participation, so an
    uncapped rebalance in a thin name pays for the whole move itself and the
    resulting P&L measures the cap that was missing rather than the signal.

    **A one-share threshold.** Without it, floating-point dust generates a
    trade every step, and turnover, a scored metric, becomes noise.
    """
    worth = obs.portfolio.net_worth(obs.engine)
    orders: dict[str, float] = {}
    for ticker, weight in weights.items():
        price = obs.price(ticker)
        if price <= 0:
            continue
        delta = weight * worth / price - obs.position(ticker)
        cap = max_participation * obs.avg_volume(ticker)
        if cap > 0:
            delta = max(-cap, min(cap, delta))
        if abs(delta) >= 1.0:
            orders[ticker] = delta
    return orders


def _book(tickers, longs, shorts, gross, k):
    """Target weights for the WHOLE roster, not just the selected names.

    The omission is subtle and it matters: an agent that only sends orders for
    its current top-k never unwinds the names that have dropped out, so gross
    exposure ratchets up every step until the leverage cap starts refusing
    trades. Its P&L then measures the accumulation rather than the signal, and
    `gross` stops meaning gross.

    Naming every ticker -- zero for the ones not selected -- makes the target a
    portfolio rather than a wish list. Measured before and after when this
    fix landed: the trend baselines went from 72 and 82 rejected trades to
    none.
    """
    per = gross / (2 * k)
    weights = {ticker: 0.0 for ticker in tickers}
    for i in longs:
        weights[tickers[i]] = per
    for i in shorts:
        weights[tickers[i]] = -per
    return weights


#: The simulated rate indices' tickers, for telling them from equities.
RATE_TICKERS = frozenset(spec["ticker"] for spec in _rate_specs())


class BuyAndHold:
    """Equal weight across the roster, bought once and left alone.

    The null hypothesis. Most of what looks like skill in a rising market is
    this, and a strategy that does not beat it has not earned its turnover.

    It trades on the first observation only. Not rebalanced, deliberately: a
    rebalanced equal-weight portfolio is a mean-reversion strategy wearing a
    passive label, and it would stop being the null hypothesis.
    """

    def __init__(self, *, leverage: float = 1.0, max_participation: float = 0.05):
        self.leverage = float(leverage)
        self.max_participation = float(max_participation)
        self._done = False

    def act(self, obs: Observation) -> dict[str, float]:
        if self._done:
            return {}
        self._done = True
        weight = self.leverage / len(obs.tickers)
        return rebalance(obs, {t: weight for t in obs.tickers},
                         max_participation=self.max_participation)


class RandomTrader:
    """Uniformly random target weights, redrawn every step.

    The noise floor. A strategy that does not beat this is not trading on a
    signal. It is paying spread and impact to express a coin flip, and
    whatever P&L it shows is the market's drift minus its own costs.

    Draws from its own RNG stream rather than from the market's, so a random
    agent's decisions cannot shift the market's draw schedule. If it could,
    every agent would face a subtly different market and the same-seed
    comparison the harness exists to provide would be gone.
    """

    def __init__(self, *, seed: int = 0, gross: float = 0.5,
                 max_participation: float = 0.02):
        self.rng = GameRng(check_seed(seed), RANDOM_AGENT_STREAM)
        self.gross = float(gross)
        self.max_participation = float(max_participation)

    def fork(self) -> "RandomTrader":
        """An independent copy that will draw what this one would draw next.

        `World.fork` calls it. The generator is copied at its position, so
        both arms flip the same coins from here and neither moves the
        other's stream. Until 0.8.5 there was no hook and the world fell
        back to `copy.deepcopy`, which raised on the generator, so a world
        holding this baseline could not be forked.
        """
        twin = copy.copy(self)
        twin.rng = copy.copy(self.rng)
        return twin

    def __deepcopy__(self, memo: dict) -> "RandomTrader":
        # Wrappers that deep-copy their inner agent (the daily-cadence agent a
        # spec builds, for one) reach this rather than the generator. The
        # twin goes in `memo` so a second reference to this agent inside the
        # same copy, a bound method say, resolves to the same twin.
        twin = self.fork()
        memo[id(self)] = twin
        return twin

    def act(self, obs: Observation) -> dict[str, float]:
        raw = [self.rng.next_float() * 2.0 - 1.0 for _ in obs.tickers]
        total = sum(abs(x) for x in raw)
        if total == 0:
            return {}
        scale = self.gross / total
        return rebalance(obs, {t: x * scale for t, x in zip(obs.tickers, raw)},
                         max_participation=self.max_participation)


class _Trend:
    """Shared machinery for the two price-history baselines.

    ## `lookback` is in STEPS, not days

    The agent sees one observation per decision step, so a lookback of six is
    six steps. It equals one day only when ``steps_per_day`` is six, which is
    the harness default -- so the default agent is a one-day trader by a
    coincidence of two defaults matching, not by contract. Change
    ``steps_per_day`` and the same number means a different horizon.

    Pass ``lookback_days`` instead to say what you mean. It is converted using
    ``obs.steps_per_day`` on the first observation, so it holds whatever the
    harness is configured to do.

    ## Rebalancing more often costs more than the signal is worth

    Measured on this build, via ``evaluate({'m': Momentum(lookback_days=1.0)},
    seed=2026, universe=Universe.random(40, seed=7), days=30,
    steps_per_day=S, ticks_per_step=T)`` and reading ``return_pct``, holding
    the horizon at one day and varying only how often the agent rebalances.
    ``T`` approximates a 390-tick day per cadence (3x130 and 6x65 are
    exact; 12 does not divide 390, so that row runs 12x32 = 384 ticks):

        3 steps/day x 130 ticks, lookback 3      -8.65%
        6 steps/day x  65 ticks, lookback 6     -12.82%
       12 steps/day x  32 ticks, lookback 12    -23.69%

    The same signal over the same horizon loses half as much again when
    traded twice as often, and nearly three times as much when traded four
    times as often. Nothing charges a fee: the orders simply cross a real
    spread and consume real depth more times. This is the impact model making
    "trade more" expensive on its own, which is the same mechanism that makes
    "trade bigger" expensive. (Re-measured on 0.8.5 under `pt-v20`; `pt-v19`
    read -13.24/-27.36/-46.53 on the same build, where the tape's one-step
    reversal charged every rebalance as well. Earlier docstrings read
    +37.55/+9.79/-27.46 on pretium 0.3.0 under `pt-v12`, +103.13/+59.33/
    +24.08 pre-GJR and +97.45/+33.84/+0.10 before the pt-v12 era boundary.
    Re-measure after any engine change rather than carrying these forward.)
    """

    sign = 1.0

    def __init__(self, *, lookback: int = 6, top_k: int = 5, gross: float = 1.0,
                 max_participation: float = 0.02,
                 lookback_days: float | None = None):
        if lookback < 1:
            raise ValueError("lookback must be >= 1")
        if lookback_days is not None and lookback_days <= 0:
            raise ValueError("lookback_days must be positive")
        self.lookback_days = lookback_days
        self.lookback = int(lookback)
        self.top_k = int(top_k)
        self.gross = float(gross)
        self.max_participation = float(max_participation)
        self._history: list[list[float]] = []

    def act(self, obs: Observation) -> dict[str, float]:
        if self.lookback_days is not None:
            # Resolved from the observation rather than at construction,
            # because the agent does not know the harness's cadence until it
            # is handed one. Rounded up: a lookback of zero steps would
            # compare a price with itself and trade on nothing.
            steps = self.lookback_days * getattr(obs, "steps_per_day", 1)
            self.lookback = max(1, int(round(steps)))
            self.lookback_days = None
        self._history.append(list(obs.prices))
        if len(self._history) <= self.lookback:
            # No signal yet. Holding cash is the honest answer; guessing would
            # make the first few steps measure the guess.
            return {}
        past = self._history[-(self.lookback + 1)]
        now = self._history[-1]
        returns = [
            (now[i] / past[i] - 1.0) if past[i] > 0 else 0.0
            for i in range(len(now))
        ]
        return self._weights_from(obs, returns)

    def _weights_from(self, obs, returns):
        k = min(self.top_k, len(returns) // 2)
        if k < 1:
            return {}
        order = sorted(range(len(returns)),
                       key=lambda i: (self.sign * returns[i], obs.tickers[i]))
        # Ties break on ticker so the selection is total. Without it the choice
        # between two identically-performing names would depend on sort
        # stability, and a baseline has to be reproducible to be a baseline.
        longs, shorts = order[:k], order[-k:]
        return rebalance(obs, _book(obs.tickers, longs, shorts, self.gross, k),
                         max_participation=self.max_participation)


class Momentum(_Trend):
    """Long the recent winners, short the recent losers."""

    sign = -1.0


class MeanReversion(_Trend):
    """Long the recent losers, short the recent winners."""

    sign = 1.0


#: The bond sleeve :class:`Balanced` holds by default, as weights of the
#: whole portfolio. Its duration is (0.10 * 1.9 + 0.20 * 8.5 + 0.10 * 7.0) /
#: 0.40 = 6.5 years, about the US aggregate bond index's.
DEFAULT_BOND_SLEEVE = {"UST2Y": 0.10, "UST10Y": 0.20, "IGCORP": 0.10}


class Balanced:
    """A fixed-weight equity and bond portfolio with a drift band: 60/40.

    Holds ``equity`` of net worth across every equity in the roster, equally
    weighted, and ``bonds`` (ticker to weight, :data:`DEFAULT_BOND_SLEEVE` by
    default) in the simulated rate indices. It buys the targets on its first
    observation and then checks once a day, at the day's first step: if the
    equity sleeve has drifted more than ``band`` from its target, or any bond
    weight more than ``band`` from its own, it trades every holding back to
    target. ``band=None`` never rebalances, which is the buy-and-hold 60/40.

    Needs a roster with the rate indices it names (``Universe.random(...,
    bonds=True)``). Not in :func:`reference_agents`: it is a portfolio policy
    to study under a scenario, not a signal to rank.

    ``rebalances`` lists the days it traded back to target, and ``marks``
    records, at each day's first step, the day and the equity and bond
    sleeves' values, so a caller can read what each sleeve did.
    """

    def __init__(self, *, equity: float = 0.6,
                 bonds: dict[str, float] | None = None,
                 band: float | None = 0.05,
                 max_participation: float = 0.05):
        self.equity = float(equity)
        self.bonds = dict(DEFAULT_BOND_SLEEVE if bonds is None else bonds)
        if self.equity < 0 or any(w < 0 for w in self.bonds.values()):
            raise ValueError("weights must not be negative")
        if self.equity + sum(self.bonds.values()) > 1.0 + 1e-12:
            raise ValueError("equity and bond weights sum to more than 1")
        self.band = None if band is None else float(band)
        self.max_participation = float(max_participation)
        self.rebalances: list[int] = []
        self.marks: list[tuple[int, float, float]] = []
        self._started = False

    def _targets(self, obs: Observation) -> dict[str, float]:
        missing = [t for t in self.bonds if t not in obs.tickers]
        if missing:
            raise ValueError(
                f"Balanced holds {', '.join(missing)}, which this roster does "
                "not list. Build it with Universe.random(..., bonds=True) or "
                "Universe.with_bonds().")
        equities = [t for t in obs.tickers if t not in RATE_TICKERS]
        each = self.equity / len(equities) if equities else 0.0
        targets = {t: each for t in equities}
        targets.update(self.bonds)
        return targets

    def act(self, obs: Observation) -> dict[str, float]:
        targets = self._targets(obs)
        if not obs.is_first_step_of_day:
            return {}
        worth = obs.portfolio.net_worth(obs.engine)
        held = {t: obs.position(t) * obs.price(t) for t in obs.tickers}
        equity_value = sum(v for t, v in held.items() if t not in RATE_TICKERS)
        bond_value = sum(v for t, v in held.items() if t in RATE_TICKERS)
        self.marks.append((obs.day, equity_value, bond_value))
        if not self._started:
            self._started = True
            return rebalance(obs, targets,
                             max_participation=self.max_participation)
        if self.band is None or worth <= 0:
            return {}
        drift = abs(equity_value / worth - self.equity)
        for ticker, weight in self.bonds.items():
            drift = max(drift, abs(held[ticker] / worth - weight))
        if drift <= self.band:
            return {}
        self.rebalances.append(obs.day)
        return rebalance(obs, targets, max_participation=self.max_participation)


class Oracle:
    """Trades the true mispricing. A measuring instrument, not a competitor.

    **Two rules, picked from the preset's dials** (:meth:`cross_sectional`),
    never from its name. Where every stock-specific move is mispricing
    (every preset through pt-v19), it trades the cross-section of ``s`` as
    described below. Where the preset moves part of each shock into fair
    value or cycles aggregate earnings (pt-v20), the cross-section of ``s``
    is small: its spread falls from 0.30 to 0.015, its rank IC against the
    next day's return from -0.44 to -0.03, and the cross-sectional rule
    loses money on 3 of 8 seeds. There it trades each name's expected
    return over the next session from state no trader sees (see
    :meth:`expected_returns` and :meth:`_act_on_expected_returns`): the
    market-wide transient mispricing and herding, the fair value's drift and
    the earnings cycle's pull, as a net position plus a residual
    cross-sectional book, once a day.

    **On pt-v20 as graded it trades close to no edge.** The graded arm
    moves every shock into fair value for good, the market's plain loading
    included (``fair_value_market_share`` 1.0 with
    ``fair_value_market_linear``), and opens the market-wide mispricing at
    ``opening_market_sigma`` 0.001, so there is almost no transient
    mispricing left for hidden state to know: the index's next-day return
    correlates with the predicted common return at 0.11, and the
    cross-sectional rank IC is 0.014. The rule is net long most days and
    its P&L takes the sign of the market's month. Over 30 days
    on rosters ``Universe.random(20, seed=3 / 42 / 11)`` it is positive on 10
    of 14 markets (sim seeds 0-3, 0-3, 0-5) and on 30 of 48 over sim seeds
    0-15. It lost on sim seed 3 on all three rosters, a month the index fell
    about 11 per cent in log terms, 10 points of it in the names' permanent
    fair-value offsets, and on seed 4 on the third; buy-and-hold lost more
    in each. A fuller model does no better: the terms :meth:`expected_returns`
    leaves out (the crowd's lean on ``s``, the anticipated earnings' drift
    in place of the cycle's pull, the volatility discount's approach to its
    target) were added and re-measured on the same 48 markets, and moved the
    count to between 26 and 30 with the mean still near zero. With the
    opening dispersion at the 0.10 the rule was first measured on, seed 3
    on roster 3 pays it +152,102 against buy-and-hold's -175,280: that
    dispersion was its edge. So the Oracle stays a reference agent on
    pt-v20 but is not a ceiling there: :func:`capture_ratio` reports
    nothing on it (:data:`ORACLE_NOT_A_CEILING`), and a score is read
    against buy-and-hold (:func:`versus_buy_and_hold`). Measure the Oracle
    as a ceiling on pt-v19.

    The rest of this docstring describes the cross-sectional rule and was
    measured under pt-v19.

    Reads ``mispricing_s`` straight out of the engine, so it knows without
    estimation error which instruments sit above and below fair value. Prices
    are ``fair_value * exp(s)``, so positive ``s`` is expensive: it goes short
    the highest ``s`` and long the lowest.

    Its P&L is the denominator that makes every other agent's readable. Report
    scores as a fraction of it, not as bare currency.

    It is a REFERENCE, not a maximum -- see this module's docstring. It gets
    the same gross exposure and participation cap as every other baseline,
    and spends them on a naive rule: equal weight, long the ``top_k`` most
    underpriced names and short the ``top_k`` most overpriced. A different
    rule on the same information beats it -- ``top_k=3`` on 6 of 8 markets
    on the module docstring's grid -- and that is a result rather than a
    fault. Until 0.8.5 mean reversion beat it in 5 of 12 of them too; that
    was the harness applying its fills on every tick of the step, and none
    of the price-only agents beats it there now.

    Three further caveats, all worth knowing before quoting a capture ratio:

    **Its height is a CHOICE, and ``top_k`` is a real lever on it.**
    Re-measured on 0.8.5 under ``pt-v19`` at sim seed 2026 over thirty
    days, holding gross exposure and the participation cap fixed at the values
    every other baseline gets:

        top_k                    1      2      3      5      8     12
        random(20, seed=11)   214k   211k   181k   150k   135k   130k
        random(20, seed=7)    235k   198k   179k   178k   158k   127k

    On this build the curve falls with ``top_k`` on both rosters: the best
    configuration measured is ``top_k=1``, at 1.43x and 1.32x the default,
    and ``top_k=12`` is the worst at 0.87x and 0.71x. That shape is new and
    belongs to this build. Under 0.8.1, which held every agent's fills on
    all 65 ticks of a step, the same rows read 227k/219k/184k/194k/154k/154k
    and 249k/225k/210k/252k/197k/152k, where the default was the best on
    the second roster; under ``pt-v12`` before that, 196k/213k/180k/181k/
    164k/150k and 209k/176k/193k/269k/204k/170k; and before the ``pt-v12``
    era boundary ``top_k=1`` was worth multiples of the default. Nothing
    here has ranked the same way twice. Do not carry the numbers above to a
    different universe or build; re-measure.

    What follows either way is that **a capture ratio is quoted against a
    configuration, not against a universal quantity**. Two ratios computed
    with different ``top_k`` are not comparable, and neither is comparable to
    a published number that did not state it.

    **It is a reference under a horizon.** Mispricing mean-reverts with a
    sixty-day half-life, so over a five-day evaluation most of the edge it can
    see has not yet converged. Over a longer run the same Oracle captures
    more. Quote the horizon with the ratio.

    **It is not an upper bound on any strategy.** On the grid stated in the
    module docstring the same information on three names a side beats it
    on 6 of 8 markets, and fifteen names a side at twice the gross earns
    1.44x its median. No
    price-only reference agent beats it there since 0.8.5; the ones that
    did before were marked to their own impact. That a better rule under
    the same constraints CAN out-earn revealed information has held in
    every era measured. A capture ratio above 1.0 is a finding about
    portfolio construction rather than about information, and from a
    price-only agent it is also a reason to check that its fills reach the
    market once.
    """

    #: Marks an agent that sees past the observation wall, and is how it gets
    #: there: the harness hands an agent that declares it ``obs.hidden``, a
    #: read-only :class:`~tradefloor.sandbox.HiddenState`, and records
    #: ``uses_hidden_state`` on its scorecard so a results table can label
    #: the row rather than presenting a privileged agent as a peer. No agent
    #: gets the live engine unless the run passed ``trusted_agents=True``.
    privileged = True

    def __init__(self, *, top_k: int = 5, gross: float = 1.0,
                 max_participation: float = 0.02):
        self.top_k = int(top_k)
        self.gross = float(gross)
        self.max_participation = float(max_participation)
        # `explain` is handed a day and no observation, so the engine has to be
        # remembered from the last `act`. Held here rather than threaded
        # through the protocol, which would complicate every agent that does
        # not explain itself.
        self._engine: Any = None

    def fork(self) -> "Oracle":
        """An independent copy for another arm of a forked world.

        The engine the Oracle remembers for `explain` belongs to the world
        it was reading, and a fork runs a different engine, so the copy
        forgets it and picks its own up at its first `act`. A fork happens
        between days, and `explain` is asked only after a day's steps, so
        nothing reads the gap. Until 0.8.5 there was no hook, and the
        world's fallback `copy.deepcopy` raised on the engine.
        """
        twin = copy.copy(self)
        twin._engine = None
        return twin

    def __deepcopy__(self, memo: dict) -> "Oracle":
        # In `memo` for the reason `RandomTrader.__deepcopy__` gives: the
        # daily-cadence wrapper holds this agent AND its bound `explain`,
        # and both must land on one twin.
        twin = self.fork()
        memo[id(self)] = twin
        return twin

    @staticmethod
    def cross_sectional(model: dict[str, Any]) -> bool:
        """Whether the preset's own dials put the edge in the cross-section.

        True when no part of a name's shocks moves its fair value for good
        (``fair_value_news_share`` and ``fair_value_market_share`` 0.0) and
        aggregate earnings have no cycle (``earnings_cycle_depth`` 0.0):
        every preset through pt-v19. Then every stock-specific move is
        mispricing that reverts, and the spread of ``s`` across names is
        the edge. Read from the dials, never from the preset's name.
        """
        return (model.get("fair_value_news_share", 0.0) == 0.0
                and model.get("fair_value_market_share", 0.0) == 0.0
                and model.get("earnings_cycle_depth", 0.0) == 0.0)

    def act(self, obs: Observation) -> dict[str, float]:
        truth = hidden_state(obs)
        self._engine = truth
        model = dict(truth.model_params)
        if not self.cross_sectional(model):
            return self._act_on_expected_returns(obs, model)
        s = _f64(truth.column("mispricing_s"))
        k = min(self.top_k, len(s) // 2)
        if k < 1:
            return {}
        order = sorted(range(len(s)), key=lambda i: (s[i], obs.tickers[i]))
        cheap, dear = order[:k], order[-k:]
        return rebalance(obs, _book(obs.tickers, cheap, dear, self.gross, k),
                         max_participation=self.max_participation)

    def expected_returns(self, engine: Any,
                         model: dict[str, Any]) -> tuple[dict[int, float], float]:
        """Each equity's expected log return over the next session, from state
        no trader can see, split into a per-name part and a common drift.

        The per-name part is the mispricing's own law over a day: reversion
        ``(phi^390 - 1) s`` and the herding term ``theta * momentum``, which
        carry the market-wide transient mispricing and whatever
        cross-sectional residual is left. The common drift is the fair
        value's: nominal output growth, the buyback yield where the preset
        counts buybacks, and the pull of the aggregate earnings cycle toward
        its phase's level. Returns ``({index: per-name part}, drift)``.

        ``engine`` is the live engine or the read-only
        :class:`~tradefloor.sandbox.HiddenState` a privileged agent is handed.
        """
        s = _f64(engine.column("mispricing_s"))
        mom = _f64(engine.column("mispricing_momentum"))
        phi = model["s_phi_tick"] ** 390
        theta = model["momentum_theta"]
        tickers = engine.tickers
        own = {i: (phi - 1.0) * s[i] + theta * mom[i]
               for i in range(len(s)) if tickers[i] not in RATE_TICKERS}
        macro = engine.macro_fields
        economy = economy_of(engine)
        # The TRUE growth, which output compounds: `macro_fields` reports
        # the published quarterly figure under `gdp_publication_lag`. The
        # core's percent over 100 is `macro_fields`' own figure at 0.0.
        drift = (economy["gdp_growth"] / 100.0 + macro["inflation_rate"]) / 252.0
        if model.get("market_pe_buybacks", 0.0) != 0.0 and economy["market_pe"] > 0:
            drift += model["buyback_payout_share"] / economy["market_pe"] / 252.0
        depth = model.get("earnings_cycle_depth", 0.0)
        if depth != 0.0:
            down = economy["cycle_phase"] in ("contraction", "trough")
            target = -depth if down else depth * model["earnings_cycle_upside"]
            pull = 1.0 - 0.5 ** (1.0 / model["earnings_cycle_half_life"])
            drift += pull * (target - economy["earnings_cycle"])
        return own, drift

    def _act_on_expected_returns(self, obs: Observation,
                                 model: dict[str, Any]) -> dict[str, float]:
        """The rule for a preset whose edge is not in the cross-section.

        Once a day, at the open. Each equity's expected return (see
        :meth:`expected_returns`) is a common part, the mean across names
        plus the fair value's drift, and a residual. The residual is traded
        as the cross-sectional rule is, long the ``top_k`` highest and short
        the ``top_k`` lowest; the common part as a net position spread
        equally over every equity, long or short with its sign. The gross is
        split between the two in proportion to what each earns per unit of
        gross: ``|common|`` against half the residual spread between the two
        books. Net exposure stays inside ``gross``.
        """
        if obs.step_of_day != 0:
            return {}
        own, drift = self.expected_returns(hidden_state(obs), model)
        if not own:
            return {}
        names = sorted(own)
        centre = math.fsum(own.values()) / len(own)
        common = centre + drift
        residual = {i: own[i] - centre for i in names}
        k = min(self.top_k, len(names) // 2)
        order = sorted(names, key=lambda i: (residual[i], obs.tickers[i]))
        longs, shorts = (order[-k:], order[:k]) if k else ([], [])
        spread = ((math.fsum(residual[i] for i in longs) / k
                   - math.fsum(residual[i] for i in shorts) / k) / 2.0
                  if k else 0.0)
        total = abs(common) + spread
        share = abs(common) / total if total > 0 else 0.0
        weights = {ticker: 0.0 for ticker in obs.tickers}
        if k:
            per = (1.0 - share) * self.gross / (2 * k)
            for i in longs:
                weights[obs.tickers[i]] += per
            for i in shorts:
                weights[obs.tickers[i]] -= per
        net = math.copysign(share * self.gross / len(names), common)
        for i in names:
            weights[obs.tickers[i]] += net
        return rebalance(obs, weights, max_participation=self.max_participation)

    def explain(self, day: int) -> str | None:
        """The factor that actually dominated. Correct by construction.

        Which makes it a self-test of the scoring machinery rather than a
        claim about the Oracle: if this does not score close to 1.0, the
        explanation scorer is broken, not the agent.

        Computed here rather than by calling the scorer's own
        ``_dominant_factor``. Sharing the function would make the test a
        tautology -- the scorer agreeing with itself -- where two independent
        implementations agreeing is evidence.
        """
        if self._engine is None:
            return None
        best: FactorName = FACTOR_NAMES[0]
        largest = -1.0
        for factor in FACTOR_NAMES:
            total = 0.0
            for value in _f64(self._engine.attribution(factor)):
                total += abs(value)
            if total > largest:
                best, largest = factor, total
        return best


def reference_agents(*, seed: int = 0) -> dict[str, Any]:
    """The standard set, ready to pass to :func:`tradefloor.evaluate`.

    ``seed`` only seeds the random baseline. It is deliberately separate from
    the market seed: reusing one number for both would couple the noise floor
    to the market it is measured in, and two markets could then differ for a
    reason that had nothing to do with the market. Any integer from 0 to
    ``2**64 - 1``.
    """
    return {
        "buy_and_hold": BuyAndHold(),
        "random": RandomTrader(seed=seed),
        "momentum": Momentum(),
        "mean_reversion": MeanReversion(),
        "oracle": Oracle(),
    }


#: Shipped presets on which the Oracle stays a reference agent but is not a
#: ceiling, each with the reason a result gives for reporting no capture
#: ratio. A capture ratio reads the Oracle's P&L as what was there to earn,
#: and that holds only where hidden state predicts returns: on every preset
#: through pt-v19 each shock is mispricing that reverts, and the Oracle
#: trades it. pt-v20 moves each shock into fair value for good, so the
#: Oracle's P&L follows the market's month (see :class:`Oracle`).
#:
#: Keyed by the name a scorecard records in ``model_fingerprint``. A custom
#: model (``custom-XXXXXXXX``) is not in it, whatever preset it was built
#: from: a card carries its model's name and not its dials, so the ratio is
#: reported there as before, and a study on a modified pt-v20 should read
#: :func:`versus_buy_and_hold` instead.
ORACLE_NOT_A_CEILING: dict[str, str] = {
    "pt-v20": (
        "No capture ratio on pt-v20. Market moves there mostly stick: each "
        "shock moves fair value for good, so even perfect knowledge of the "
        "model's fair value leaves little edge. The Oracle made money in "
        "10 of 14 test markets and its P&L follows the market's "
        "month, so a fraction of it would measure the month, not the "
        "agent. Compare against buy-and-hold instead."
    ),
}


def _model_name(model: Any) -> str:
    """The preset name or custom fingerprint a model runs under.

    ``None`` is the shipped default, a string is taken as a preset name, and
    anything else is read for its ``fingerprint``: a
    :class:`tradefloor.ModelParams`.
    """
    if model is None:
        from ._core import ModelParams
        return ModelParams.from_preset().fingerprint
    if isinstance(model, str):
        return model
    return str(getattr(model, "fingerprint", ""))


def oracle_is_ceiling(model: Any = None) -> bool:
    """Whether a capture ratio is reported under ``model``.

    ``model`` is what :func:`tradefloor.evaluate` takes (a preset name, a
    :class:`tradefloor.ModelParams`, or ``None`` for the default), or a
    scorecard's ``model_fingerprint``. False only for the presets named in
    :data:`ORACLE_NOT_A_CEILING`.
    """
    return _model_name(model) not in ORACLE_NOT_A_CEILING


def capture_withheld(scores: dict[str, Any], *,
                     oracle: str = "oracle") -> str | None:
    """Why no capture ratio is reported for ``scores``, or None if one is.

    Read from the scorecards' ``model_fingerprint``: the Oracle's card, or
    any card when the Oracle did not run. A card without the field (a
    stand-in built by hand) reads as a ceiling, as it did before.
    """
    card = scores.get(oracle)
    if card is None and scores:
        card = next(iter(scores.values()))
    name = getattr(card, "model_fingerprint", "") if card is not None else ""
    return ORACLE_NOT_A_CEILING.get(name)


def versus_buy_and_hold(scores: dict[str, Any], *,
                        reference: str = "buy_and_hold") -> dict[str, float]:
    """Each agent's P&L less buy-and-hold's in the same market.

    The comparison to quote where the Oracle is not a ceiling
    (:data:`ORACLE_NOT_A_CEILING`), and a useful one everywhere: did the
    strategy earn more than owning the market did? In currency, because
    every agent in one evaluation starts with the same cash. The Oracle is
    included; it is a reference agent like the others, and its card says
    ``uses_hidden_state``.

    A tampered agent (``Scorecard.tampered``) is left out, since its P&L
    is of a market it rewrote. A tampered reference raises
    :class:`ValidationError`, since every excess would be measured against
    it. :func:`tradefloor.rank` leaves tampered agents out the same way.

    Returns an empty mapping when buy-and-hold did not run.
    """
    if reference not in scores:
        return {}
    _refuse_tampered(scores[reference], reference, "buy-and-hold reference")
    base = scores[reference].pnl
    return {
        name: card.pnl - base
        for name, card in scores.items()
        if name != reference and not getattr(card, "tampered", False)
    }


def _refuse_tampered(card: Any, name: str, role: str) -> None:
    if getattr(card, "tampered", False):
        raise ValidationError(
            f"the {role} {name!r} tampered with its market (see its "
            "Scorecard.errors), so no comparison against it means anything. "
            "Run the evaluation again with an honest reference.")


def capture_ratio(scores: dict[str, Any], *, oracle: str = "oracle") -> dict[str, float]:
    """Each agent's P&L as a fraction of the Oracle's.

    The number worth reporting. Raw P&L is not comparable across markets,
    since a seed with more dispersion pays every strategy more, and dividing by
    what a perfectly-informed reference earned in *that* market removes
    exactly that.

    A ratio ABOVE 1.0 is legal. The Oracle is not an upper bound: it holds
    the same gross exposure as everyone else and spends it on a naive
    equal-weight rule, so a better portfolio under the same constraint
    out-earns it, and ``Oracle(top_k=3)`` does on 6 of the 8 markets this
    module's docstring measures. From a price-only agent it no longer
    occurs on that grid: 0 of 48 agent-market pairs since 0.8.5, where 0.8.1
    counted 5 of mean reversion's 12, all of them its own impact counted on
    every tick of a step. Treat a ratio above 1.0 as a finding about
    portfolio construction, not as a broken denominator, and from a
    price-only agent check first that its fills reach the market once.

    The ratio is also only comparable across runs that used the SAME Oracle
    configuration; ``top_k`` moves the denominator substantially. See
    :class:`Oracle`.

    Returns an empty mapping when the Oracle lost money or is absent: a ratio
    against a negative denominator flips sign and would rank the worst agent
    first. An empty result says "not measurable here", which is true and is
    better than a confidently wrong table.

    Returns an empty mapping, too, on a preset where the Oracle is not a
    ceiling (:data:`ORACLE_NOT_A_CEILING`, which names pt-v20), whatever
    the Oracle earned. :func:`capture_withheld` gives the reason, and
    :func:`versus_buy_and_hold` the comparison to quote there.

    A tampered agent is left out and a tampered Oracle is refused with a
    :class:`ValidationError`, as :func:`versus_buy_and_hold` does.
    """
    if capture_withheld(scores, oracle=oracle) is not None:
        return {}
    if oracle not in scores:
        return {}
    _refuse_tampered(scores[oracle], oracle, "Oracle")
    ceiling = scores[oracle].pnl
    if ceiling <= 0:
        return {}
    return {
        name: card.pnl / ceiling
        for name, card in scores.items()
        if name != oracle and not getattr(card, "tampered", False)
    }
