"""A deterministic agent that trades the rate regime, not the price chart.

Used by ``counterfactual.py``. It exists to make one experiment
answerable -- does the same agent behave differently when rates move? -- and it
is written to be read, not to win. Every number in it is a declared constant,
there is no fitting, no randomness and no lookup of anything the simulator
knows and a trader would not.

## Why not a moving average

A crossover rule reacts to the rate shock only after the shock has moved
prices, so its divergence is the market's, borrowed. It would show that the
market changed, which the price chart already shows. This agent reads the
policy rate directly, so a change in its behaviour is a change in ITS
behaviour, and the experiment measures the thing it claims to measure.

## The policy, in three lines

    tightening      = policy rate now  -  policy rate when it started
    gross exposure  = base, cut by RISK_OFF_PER_100BP for each 100bp of it
    weights         = equal, tilted away from duration in proportion to it

Plus a no-trade band, so the agent rebalances when its portfolio has actually
drifted rather than every time the arithmetic moves by a share.

## Duration means what this model means by it

Rate sensitivity in Tradefloor's valuation is
``1 - (discount - neutral) * RATE_PE_SENSITIVITY * (1 + growth * SCALE)``:
revenue growth IS the duration term. So the agent ranks its roster by revenue
growth, which is public company information a real analyst reads off a
filing, and which here is exactly the coefficient that decides how much a
hike costs each name.

That correspondence is honest for this market and does not transfer. A real
utility is a long-duration bond proxy; in this model, with one percent revenue
growth, it is the least rate-sensitive thing on the roster. The agent is right
about the world it is in, which is the only claim being made.

## What it observes, and what it cannot

    market      realised volatility of its own recent price history
    macro       the policy rate, from engine.macro_state
    portfolio   its current weights, against its targets
    execution   size capped as a fraction of each name's daily volume

It never reads ``mispricing_s``, fair value, or the factor attribution. Those
are what the simulator knows and a trader does not, and an agent that read
them would be inverting the model rather than trading in it.

## Replacing it

``World`` calls ``act(obs)`` and, if they exist, ``decision()`` and
``state()``. Anything with those three methods drops in unchanged -- a
FinRobot adapter, an LLM policy, somebody else's framework behind a shim. The
experiment around it does not move.
"""

from __future__ import annotations

import statistics
from typing import Any

from tradefloor.baselines import rebalance

#: Gross exposure, as a multiple of net worth, when rates are where the agent
#: found them. Not one: the five percent it never invests is the reserve that
#: keeps a rebalance from paying its own impact out of borrowed cash. And not
#: two: the demo is about the CHANGE in exposure, and a levered starting point
#: would make the cut look smaller than it is.
BASE_GROSS = 0.95

#: Fraction of BASE_GROSS shed per 100bp of tightening. At +200bp the agent
#: targets 0.4x. Chosen to be legible rather than optimal; nothing here is
#: fitted, and a different number would change the size of the response
#: without changing anything the experiment demonstrates.
RISK_OFF_PER_100BP = 0.30

#: Gross exposure never goes below this, however far rates rise. A policy that
#: can reach zero stops being a portfolio and starts being a switch, and a
#: switch has no composition to compare.
GROSS_FLOOR = 0.20

#: How hard the tilt leans away from duration, per 100bp. At +200bp the
#: longest-duration name on a growth-0.35-to-0.01 roster ends up holding about
#: two thirds of what the shortest does.
DURATION_TILT_PER_100BP = 0.35

#: Further exposure haircut per unit of volatility excess. This is the
#: second, slower channel: the shock moves prices, the prices move realised
#: volatility, and the agent de-risks again for a reason it OBSERVED rather
#: than one it was told.
VOL_HAIRCUT = 0.40

#: The two windows behind that excess, in steps. Six steps is a day, so this
#: is two days of recent volatility against ten days of background.
#:
#: A ratio of two measured windows, not a level against a remembered baseline.
#: The remembered-baseline version was written first and was wrong: whatever
#: the market happened to be doing in the agent's first two days became the
#: definition of calm, and a quiet opening made every later step read as a
#: crisis. A ratio has no such reference point to get wrong -- it sits at
#: 1.0 whenever recent volatility matches the background, whatever that is.
VOL_FAST = 12
VOL_SLOW = 60

#: Rebalance only when some name's weight has drifted this far from target.
#: Without a band, floating-point drift generates an order every step and
#: turnover stops meaning anything. With one, "the agent traded" is an event.
REBALANCE_BAND = 0.01

#: Share of an instrument's average daily volume one order may take. Impact
#: scales with participation, so an uncapped rebalance in a thin name pays
#: for the whole move itself.
MAX_PARTICIPATION = 0.02


class MacroAwareAgent:
    """Cuts risk when the policy rate rises, and cuts duration hardest.

    Deterministic: given the same observations it makes the same decisions,
    with no RNG anywhere in it. That is what lets the experiment attribute a
    behavioural change to the intervention rather than to the agent.

    ``duration`` maps ticker to the revenue growth the agent believes the
    company has -- public information, supplied at construction because an
    ``Observation`` carries prices and positions, not fundamentals. Anything
    the agent knows, it was given or it measured.
    """

    def __init__(self, *, duration: dict[str, float],
                 base_gross: float = BASE_GROSS,
                 risk_off_per_100bp: float = RISK_OFF_PER_100BP,
                 gross_floor: float = GROSS_FLOOR,
                 duration_tilt_per_100bp: float = DURATION_TILT_PER_100BP,
                 vol_haircut: float = VOL_HAIRCUT,
                 vol_fast: int = VOL_FAST,
                 vol_slow: int = VOL_SLOW,
                 rebalance_band: float = REBALANCE_BAND,
                 max_participation: float = MAX_PARTICIPATION) -> None:
        self.duration = dict(duration)
        self.base_gross = float(base_gross)
        self.risk_off_per_100bp = float(risk_off_per_100bp)
        self.gross_floor = float(gross_floor)
        self.duration_tilt_per_100bp = float(duration_tilt_per_100bp)
        self.vol_haircut = float(vol_haircut)
        self.vol_fast = int(vol_fast)
        self.vol_slow = int(vol_slow)
        self.rebalance_band = float(rebalance_band)
        self.max_participation = float(max_participation)

        # Learned on the first observation, not passed in: the agent's
        # reference point is the world it woke up in. Passing it would let a
        # caller put the two arms of an experiment on different baselines
        # without noticing, which is exactly the confound a fork removes.
        self.baseline_rate: float | None = None
        self.history: list[list[float]] = []
        self._decision: dict[str, Any] | None = None

    # -- the policy -------------------------------------------------------

    def act(self, obs) -> dict[str, float]:
        rate = obs.engine.macro_state.federal_funds_rate
        if self.baseline_rate is None:
            self.baseline_rate = rate

        self.history.append(list(obs.prices))
        if len(self.history) > self.vol_slow + 1:
            self.history.pop(0)

        # Observe -> assess the regime. Tightening only: this agent has no
        # view on cuts, and pretending to one would be a second policy nobody
        # asked it to have.
        tightening = max(0.0, rate - self.baseline_rate)
        hundreds = tightening / 0.01

        gross = self.base_gross * (1.0 - self.risk_off_per_100bp * hundreds)
        vol_excess = self._vol_excess()
        gross *= 1.0 - min(1.0, self.vol_haircut * vol_excess)
        gross = max(self.gross_floor, min(self.base_gross, gross))

        # Act -> the target book. Every ticker is named, including the ones
        # it wants nothing in: a target that omits a name is a wish list, and
        # the position it forgot to unwind stays on.
        raw = {t: 1.0 / (1.0 + self.duration_tilt_per_100bp
                         * self.duration.get(t, 0.0) * hundreds)
               for t in obs.tickers}
        total = sum(raw.values())
        weights = {t: gross * value / total for t, value in raw.items()}

        self._decision = {
            "rate": rate,
            "tightening_bps": round(tightening * 10_000, 6),
            "vol_excess": round(vol_excess, 12),
            "gross": round(gross, 12),
            "weights": {t: round(w, 12) for t, w in sorted(weights.items())},
        }

        if not self._drifted(obs, weights):
            return {}
        return rebalance(obs, weights,
                         max_participation=self.max_participation)

    def _vol_excess(self) -> float:
        """How much busier the last two days are than the last ten.

        Zero until there is a full slow window to compare against, and zero
        whenever recent volatility is at or below the background. It is the
        market condition the agent can actually see: no fair value, no
        attribution, just the prices it was shown.
        """
        if len(self.history) <= self.vol_slow:
            return 0.0
        fast = self._vol(self.vol_fast)
        slow = self._vol(self.vol_slow)
        if not fast or not slow:
            return 0.0
        return max(0.0, fast / slow - 1.0)

    def _vol(self, window: int) -> float | None:
        """Cross-sectional mean of per-name step-return standard deviation."""
        rows = self.history[-(window + 1):]
        if len(rows) < 3:
            return None
        sigmas = []
        for i in range(len(rows[0])):
            series = [row[i] for row in rows]
            returns = [b / a - 1.0 for a, b in zip(series, series[1:])
                       if a > 0]
            if len(returns) >= 2:
                sigmas.append(statistics.pstdev(returns))
        return sum(sigmas) / len(sigmas) if sigmas else None

    def _drifted(self, obs, weights: dict[str, float]) -> bool:
        """True when some name is further than the band from its target."""
        worth = obs.portfolio.net_worth(obs.engine)
        if worth <= 0:
            return False
        for ticker, target in weights.items():
            held = obs.position(ticker) * obs.price(ticker) / worth
            if abs(held - target) >= self.rebalance_band:
                return True
        return False

    # -- the two hooks World looks for ------------------------------------

    def decision(self) -> dict[str, Any] | None:
        """What the last :meth:`act` concluded, before it became orders.

        Recorded by ``World`` on every step, which is what lets the
        comparison say when the agent's TARGET changed as distinct from when
        its orders did. They are not the same step: a target can move inside
        the rebalance band and produce no trade at all.
        """
        return self._decision

    def state(self) -> dict[str, Any]:
        """Everything this agent carries between steps.

        Read by :func:`tradefloor.agree` to check that two forked arms really
        did start from the same agent. An agent that reported only part of its
        state would let the verification pass while the arms differed.
        """
        return {
            "baseline_rate": self.baseline_rate,
            "history": [list(row) for row in self.history],
            "decision": self._decision,
        }

    def policy(self) -> str:
        """The rule, as the demo prints it. One place, so it cannot drift."""
        return (
            f"tightening   = policy rate - rate at start\n"
            f"gross        = {self.base_gross:.2f} "
            f"- {self.risk_off_per_100bp:.2f} per 100bp, "
            f"floor {self.gross_floor:.2f}\n"
            f"             then x (1 - {self.vol_haircut:.2f} x vol excess), "
            f"where excess is\n"
            f"             {self.vol_fast}-step volatility / "
            f"{self.vol_slow}-step volatility - 1\n"
            f"weight_i     = gross x (1 / (1 + "
            f"{self.duration_tilt_per_100bp:.2f} x growth_i x 100bp)), "
            f"normalised\n"
            f"trade        when any weight is {self.rebalance_band:.0%} from "
            f"target; size capped at {self.max_participation:.0%} of ADV"
        )

    def __repr__(self) -> str:
        return (f"MacroAwareAgent({len(self.duration)} names, "
                f"base_gross={self.base_gross}, "
                f"baseline_rate={self.baseline_rate})")
