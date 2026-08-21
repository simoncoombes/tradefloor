"""Reference agents, and a ceiling to measure the others against.

A leaderboard of one agent is not a measurement. `evaluate` will happily
report that your strategy made $47,000 and that number means nothing on its
own: it does not say whether the market simply went up, whether random trading
would have done as well, or whether $47,000 was most of what was available or
a tenth of it.

These are the reference points that make a score readable, from the bottom up:

- **BuyAndHold** — did the strategy beat owning the market? The null
  hypothesis, and the one most strategies quietly fail.
- **RandomTrader** — did it beat noise? A strategy that cannot beat coin flips
  is measuring its own transaction costs.
- **Momentum** and **MeanReversion** — did it beat the two simplest things
  anyone would try first?
- **Oracle** — how much was available *at all*?

## The Oracle is the interesting one, and it cheats on purpose

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
have earned, because you never observe fair value — you only observe price,
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
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # A type alias declared in the stub. It has no runtime existence in the
    # compiled extension, so importing it unguarded would break the package
    # for everyone who is not a type checker.
    from ._core import FactorName

from ._core import Engine, GameRng
from .harness import FACTOR_NAMES, Observation

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
    trade every step, and turnover — a scored metric — becomes noise.
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
    portfolio rather than a wish list. Measured before and after: the trend
    baselines went from 72 and 82 rejected trades to none.
    """
    per = gross / (2 * k)
    weights = {ticker: 0.0 for ticker in tickers}
    for i in longs:
        weights[tickers[i]] = per
    for i in shorts:
        weights[tickers[i]] = -per
    return weights


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
    signal — it is paying spread and impact to express a coin flip, and
    whatever P&L it shows is the market's drift minus its own costs.

    Draws from its own RNG stream rather than from the market's, so a random
    agent's decisions cannot shift the market's draw schedule. If it could,
    every agent would face a subtly different market and the same-seed
    comparison the harness exists to provide would be gone.
    """

    def __init__(self, *, seed: int = 0, gross: float = 0.5,
                 max_participation: float = 0.02):
        self.rng = GameRng(int(seed), RANDOM_AGENT_STREAM)
        self.gross = float(gross)
        self.max_participation = float(max_participation)

    def act(self, obs: Observation) -> dict[str, float]:
        raw = [self.rng.next_float() * 2.0 - 1.0 for _ in obs.tickers]
        total = sum(abs(x) for x in raw)
        if total == 0:
            return {}
        scale = self.gross / total
        return rebalance(obs, {t: x * scale for t, x in zip(obs.tickers, raw)},
                         max_participation=self.max_participation)


class _Trend:
    """Shared machinery for the two price-history baselines."""

    sign = 1.0

    def __init__(self, *, lookback: int = 6, top_k: int = 5, gross: float = 1.0,
                 max_participation: float = 0.02):
        if lookback < 1:
            raise ValueError("lookback must be >= 1")
        self.lookback = int(lookback)
        self.top_k = int(top_k)
        self.gross = float(gross)
        self.max_participation = float(max_participation)
        self._history: list[list[float]] = []

    def act(self, obs: Observation) -> dict[str, float]:
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


class Oracle:
    """Trades the true mispricing. Not a competitor — a measuring instrument.

    Reads ``mispricing_s`` straight out of the engine, so it knows without
    estimation error which instruments sit above and below fair value. Prices
    are ``fair_value * exp(s)``, so positive ``s`` is expensive: it goes short
    the highest ``s`` and long the lowest.

    Its P&L is the denominator that makes every other agent's readable. Report
    scores as a fraction of it, not as bare currency.

    Two honest caveats, both worth knowing before quoting a capture ratio:

    **It is a ceiling under a horizon, not the maximum possible.** Mispricing
    mean-reverts with a sixty-day half-life, so over a five-day evaluation most
    of the edge it can see has not yet converged. Over a longer run the same
    Oracle captures more. Quote the horizon with the ratio.

    **It is not an upper bound on every strategy.** A strategy trading a signal
    the Oracle ignores — order-flow pressure, a squeeze — can beat it, and a
    capture ratio above 1.0 is a finding rather than a bug. What it bounds is
    the *mispricing* trade specifically.
    """

    #: Marks an agent that sees past the observation wall. The harness does not
    #: enforce anything with it; it is here so a results table can label the
    #: row rather than presenting a privileged agent as a peer.
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
        self._engine: Engine | None = None

    def act(self, obs: Observation) -> dict[str, float]:
        self._engine = obs.engine
        s = _f64(obs.engine.column("mispricing_s"))
        k = min(self.top_k, len(s) // 2)
        if k < 1:
            return {}
        order = sorted(range(len(s)), key=lambda i: (s[i], obs.tickers[i]))
        cheap, dear = order[:k], order[-k:]
        return rebalance(obs, _book(obs.tickers, cheap, dear, self.gross, k),
                         max_participation=self.max_participation)

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
    """The standard set, ready to pass to :func:`pretium.evaluate`.

    ``seed`` only seeds the random baseline. It is deliberately separate from
    the market seed: reusing one number for both would couple the noise floor
    to the market it is measured in, and two markets could then differ for a
    reason that had nothing to do with the market.
    """
    return {
        "buy_and_hold": BuyAndHold(),
        "random": RandomTrader(seed=seed),
        "momentum": Momentum(),
        "mean_reversion": MeanReversion(),
        "oracle": Oracle(),
    }


def capture_ratio(scores: dict[str, Any], *, oracle: str = "oracle") -> dict[str, float]:
    """Each agent's P&L as a fraction of the Oracle's.

    The number worth reporting. Raw P&L is not comparable across markets —
    a seed with more dispersion pays every strategy more — and dividing by
    what perfect information earned in *that* market removes exactly that.

    Returns an empty mapping when the Oracle lost money or is absent: a ratio
    against a negative denominator flips sign and would rank the worst agent
    first. An empty result says "not measurable here", which is true and is
    better than a confidently wrong table.
    """
    if oracle not in scores:
        return {}
    ceiling = scores[oracle].pnl
    if ceiling <= 0:
        return {}
    return {
        name: card.pnl / ceiling
        for name, card in scores.items()
        if name != oracle
    }
