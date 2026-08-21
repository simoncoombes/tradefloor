"""A Gymnasium-compatible environment.

``gymnasium`` and ``numpy`` are OPTIONAL. The core package depends on neither,
and importing this module without them raises a message saying what to install
rather than an ImportError from three frames down. The environment also works
without gymnasium installed at all — it only subclasses ``gymnasium.Env`` when
it is present, so the duck-typed reset/step contract is usable on its own.

## Actions are target weights, not share counts

An action is a vector in ``[-1, 1]``, one entry per instrument, read as the
fraction of net worth to hold in that name. Negative is short.

Share counts would be the obvious alternative and they are wrong here: the
right number of shares depends on price, which varies by two orders of
magnitude across a generated roster, and on net worth, which changes every
step. A policy emitting share counts would have to learn the price scale of
each instrument before it could learn anything about trading. Weights are
scale-free, bounded, and mean the same thing on day one and day two hundred.

## The reward is P&L for the step

Change in net worth, not cumulative. Cumulative reward double-counts every
earlier step and makes the return depend on episode length rather than on
skill.

It is measured AFTER the market moves, so it includes the cost of the agent's
own footprint. An environment that rewarded the paper value of a position at
the price it was bought at would pay for trading rather than for being right.

## Episodes end; they do not reset in place

``reset`` builds a new engine, because that is what a reset IS here. A method
that rewound would either secretly reconstruct — fine, but then it is a
constructor — or try to restore mutable state and eventually miss a field: the
maker inventory, the Box-Muller spare, the GARCH state.
"""

from __future__ import annotations

from typing import Any, Sequence

from ._core import Engine, Instrument, Macro, OrderError, ValidationError
from .harness import session_clock
from .portfolio import Portfolio
from .universe_util import as_universe

# A name that is "a module or None", and a base class that is "Env or object",
# are both things a type checker is right to object to -- and both are the
# correct runtime shape for an optional dependency. Ignored with the reason
# rather than contorting the runtime to satisfy the checker.
try:  # pragma: no cover - exercised by the absence path, not the presence one
    import numpy as _np
except ImportError:  # pragma: no cover
    _np = None  # type: ignore[assignment]

try:  # pragma: no cover
    import gymnasium as _gym

    _Base: type = _gym.Env
except ImportError:  # pragma: no cover
    _gym = None  # type: ignore[assignment]
    _Base = object


def _require(module, name: str, extra: str):
    if module is None:
        raise ImportError(
            f"{name} is required for pretium.gym but is not installed. "
            f"Install it with: pip install {extra}"
        )
    return module


class TradingEnv(_Base):
    """A market as a reinforcement-learning environment.

    Observation: prices, holdings as fractions of net worth, and cash as a
    fraction of net worth — all ``float64``, C-contiguous, which is what a
    ``Box`` space wants.

    Prices are given as log returns since the previous step rather than as
    levels. A level of 512.44 tells a policy nothing without knowing what it
    was before, and the range across a generated roster spans two orders of
    magnitude; returns are stationary and comparable across instruments.
    """

    metadata: dict[str, Any] = {"render_modes": []}

    def __init__(
        self,
        *,
        universe: Sequence[Instrument],
        seed: int = 0,
        macro: Macro | None = None,
        days: int = 5,
        steps_per_day: int = 6,
        ticks_per_step: int = 65,
        cash: float = 1_000_000.0,
        max_leverage: float | None = 2.0,
        start: tuple[int, int, int] = (9, 30, 3),
    ) -> None:
        _require(_np, "numpy", "numpy")

        self.universe = as_universe(universe)
        self.base_seed = int(seed)
        self.macro = macro
        self.days = int(days)
        self.steps_per_day = int(steps_per_day)
        self.ticks_per_step = int(ticks_per_step)
        self.starting_cash = float(cash)
        self.max_leverage = max_leverage
        self.start = start

        if self.days < 1 or self.steps_per_day < 1 or self.ticks_per_step < 1:
            raise ValidationError("days, steps_per_day and ticks_per_step must be >= 1")

        self.n = len(self.universe)
        self.max_steps = self.days * self.steps_per_day

        if _gym is not None:
            from gymnasium import spaces

            self.action_space = spaces.Box(
                low=-1.0, high=1.0, shape=(self.n,), dtype=_np.float64
            )
            # returns (n) + holdings (n) + cash fraction (1)
            #
            # Unbounded, and gymnasium's env_checker warns about it. The
            # warning is right to ask and the answer is that no finite bound
            # is true. A step is 65 ticks; the circuit breaker caps each tick
            # at 25% of the previous close, so a step's log return is bounded
            # only by 1.25**65 -- a number no policy should be told is the
            # range. Cash as a fraction of net worth can go negative when
            # levered and above one when net short.
            #
            # A bound the environment can exceed is worse than infinity, not
            # better: wrappers that normalise against the space would silently
            # emit out-of-range observations, and a clipping wrapper would
            # discard real information. Infinity is the honest declaration.
            self.observation_space = spaces.Box(
                low=-_np.inf, high=_np.inf, shape=(2 * self.n + 1,), dtype=_np.float64
            )

        self.engine: Engine | None = None
        self.portfolio: Portfolio | None = None
        self._step = 0
        self._prev_prices = None
        self._prev_worth = 0.0

    # -- gym API ----------------------------------------------------------

    def reset(self, *, seed: int | None = None, options: dict | None = None):
        """Start a fresh episode.

        ``seed`` selects the market. Passing a different one gives a different
        market, which is the point: an agent trained on one seed and evaluated
        on another is being tested rather than recalled.
        """
        if _gym is not None:
            # Gymnasium keeps its own generator on the base class and its API
            # checker enforces that reset seeds it. This environment does not
            # use `self.np_random` -- all randomness lives in the engine's
            # PCG32 stream -- but skipping the call would make a conforming
            # env fail conformance, and would leave a second, unseeded
            # generator sitting on the object for any wrapper that reaches
            # for it.
            super().reset(seed=seed)

        episode_seed = self.base_seed if seed is None else int(seed)
        self.engine = Engine(seed=episode_seed, universe=self.universe,
                             macro_state=self.macro)
        self.portfolio = Portfolio(cash=self.starting_cash,
                                   max_leverage=self.max_leverage)
        self._step = 0
        self.engine.open_market()
        self._prev_prices = self._prices()
        self._prev_worth = self.portfolio.net_worth(self.engine)
        return self._observe(), {"seed": episode_seed}

    def step(self, action):
        if self.engine is None or self.portfolio is None:
            raise ValidationError("call reset() before step()")

        action = _np.asarray(action, dtype=_np.float64).reshape(-1)
        if action.shape[0] != self.n:
            raise ValidationError(
                f"action has {action.shape[0]} entries, expected {self.n}"
            )
        if not _np.all(_np.isfinite(action)):
            raise ValidationError("action contains non-finite values")
        # Clipped rather than rejected. A policy emitting 1.3 early in training
        # is normal, and killing the episode for it would make the environment
        # teach optimiser hygiene instead of trading.
        action = _np.clip(action, -1.0, 1.0)

        rejected = self._rebalance(action)

        # The clock advances within the day, so an episode traverses trading
        # days rather than replaying each one's opening minutes. See
        # `harness.session_clock` for the measurement.
        self.engine.run_session(
            *session_clock(self.start, self._step % self.steps_per_day,
                           self.ticks_per_step),
            self.ticks_per_step,
            order_flow=self.portfolio.pending_flow())
        self.portfolio.clear_flow()

        self._step += 1
        if self._step % self.steps_per_day == 0:
            self.engine.close_market()
            if self._step < self.max_steps:
                self.engine.open_market()

        worth = self.portfolio.net_worth(self.engine)
        # Reward is the step's P&L, measured AFTER the market moved, so it
        # includes the cost of the agent's own footprint.
        reward = worth - self._prev_worth
        self._prev_worth = worth

        terminated = worth <= 0.0     # insolvent: the episode is genuinely over
        truncated = self._step >= self.max_steps and not terminated

        info = {
            "net_worth": worth,
            "cash": self.portfolio.cash,
            "leverage": self.portfolio.leverage(self.engine),
            "rejected": rejected,
            "step": self._step,
        }
        return self._observe(), float(reward), bool(terminated), bool(truncated), info

    # -- internals --------------------------------------------------------

    def _prices(self):
        # Asserted rather than assumed: these helpers are only reachable after
        # reset(), but nothing enforced that across a method boundary, and a
        # helper called early would have failed on None with a worse message.
        assert self.engine is not None, "engine not built - call reset()"
        import struct
        return _np.array(
            struct.unpack("<%dd" % self.n, self.engine.prices()), dtype=_np.float64
        )

    def _rebalance(self, weights) -> int:
        """Trade towards the target weights. Returns how many trades were refused."""
        assert self.engine is not None and self.portfolio is not None
        prices = self._prices()
        worth = self.portfolio.net_worth(self.engine)
        rejected = 0
        for i, ticker in enumerate(self.engine.tickers):
            if prices[i] <= 0:
                continue
            target = weights[i] * worth / prices[i]
            delta = target - self.portfolio.positions.get(
                ticker, _Zero
            ).quantity
            # A threshold, so floating-point dust does not generate a trade
            # every step. One share is the smallest unit anyone would act on.
            if abs(delta) < 1.0:
                continue
            try:
                self.portfolio.execute(self.engine, ticker, delta)
            except (OrderError, ValidationError):
                # A refused trade is information, not a failure. Being unable
                # to reach a target -- because the book is thin or leverage is
                # capped -- is a fact about the action, and the agent should
                # experience it rather than have the episode die.
                rejected += 1
        return rejected

    def _observe(self):
        assert self.engine is not None and self.portfolio is not None
        prices = self._prices()
        # Log returns, not levels: a level says nothing without its history,
        # and the range across a roster spans two orders of magnitude.
        with _np.errstate(divide="ignore", invalid="ignore"):
            returns = _np.log(prices / self._prev_prices)
        returns = _np.nan_to_num(returns, nan=0.0, posinf=0.0, neginf=0.0)
        self._prev_prices = prices

        worth = self.portfolio.net_worth(self.engine)
        denom = worth if worth > 0 else 1.0
        holdings = _np.array(
            [
                self.portfolio.positions.get(t, _Zero).quantity * p / denom
                for t, p in zip(self.engine.tickers, prices)
            ],
            dtype=_np.float64,
        )
        cash_fraction = _np.array([self.portfolio.cash / denom], dtype=_np.float64)
        return _np.ascontiguousarray(
            _np.concatenate([returns, holdings, cash_fraction]), dtype=_np.float64
        )

    def render(self):  # pragma: no cover - no visual mode
        return None

    def close(self):  # pragma: no cover
        self.engine = None
        self.portfolio = None


class _ZeroPosition:
    """Stand-in for an unheld instrument, so lookups need no branch."""

    __slots__ = ()
    quantity = 0.0


_Zero = _ZeroPosition()
