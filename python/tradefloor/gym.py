"""A Gymnasium-compatible environment.

``gymnasium`` and ``numpy`` are OPTIONAL. The core package depends on neither,
and importing this module without them raises a message saying what to install
rather than an ImportError from three frames down. The environment also works
without gymnasium installed at all, since it only subclasses ``gymnasium.Env`` when
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

The absolute weights can add up to more than ``max_leverage`` allows, and
``[1, 1, 1, 1, 1]`` asks for 5x under the default 2x cap. Such an action is
scaled down, every weight by the same factor, until its gross exposure is
``max_leverage / (1 + 0.01 * max_leverage)``, which is 1.96x under a 2x cap.
The gap below the cap leaves room for fills that cost up to 1% of what they
buy, so all five names are filled at 0.39 each and the step's ``info`` says
``scaled=True``. An action already under that level is traded as given.
When fills cost more than 1% the last trades can still meet the cap, which
refuses them, and ``info["rejected"]`` counts them.
Before 0.8.5 the env traded names in roster order until the cap refused
one, so ``[1, 1, 1, 1, 1]`` bought the first name at 1x and refused the
other four.

The step also trades every position it is shrinking before any it is
growing, so moving 1.9x out of one name and into another sells the first and
then buys the second. Before 0.8.5 the purchase came first whenever the new
name was earlier in the roster, and the cap refused it.

## The reward is P&L for the step

The reward is the step's change in net worth in dollars, the same units as
``cash``. It is not cumulative. Cumulative reward double-counts every
earlier step and makes the return depend on episode length rather than on
skill.

It is measured AFTER the market moves, so it includes the cost of the agent's
own footprint. An environment that rewarded the paper value of a position at
the price it was bought at would pay for trading rather than for being right.

## The env holds the market; training code gets a view of it

The observation is an array and carries nothing but returns, holdings and
cash. The env object is another matter: training code holds it, and until
0.8.5 ``env.engine`` was the live engine, with the true business-cycle
phase in ``state_snapshot()["economy"]``, the mispricing among its columns
and ``fork`` to run the market ahead. ``env.engine`` and ``env.portfolio``
are now the read-only :class:`~tradefloor.sandbox.MarketView` and
:class:`~tradefloor.sandbox.PortfolioView` every harness hands an agent,
and ``trusted_agents=True`` gives back the live objects, as it does for
:func:`tradefloor.evaluate`. The env itself steps the live engine either
way, so the market, the rewards and the observations are the same bytes
with the view or without it. See :mod:`tradefloor.sandbox`.

## Episodes end; they do not reset in place

``reset`` builds a new engine, because that is what a reset IS here. A method
that rewound would either secretly reconstruct, which is fine but then it is
a constructor, or try to restore mutable state and eventually miss a field: the
maker inventory, the Box-Muller spare, the GARCH state.

Which market the new engine runs follows the Gymnasium convention. The
first ``reset()`` runs the constructor's ``seed``. ``reset(seed=n)`` runs
seed ``n``. Every later ``reset()`` without a seed draws the next episode's
seed from the env's generator, seeded by the constructor's seed or by the
latest ``reset(seed=n)``, so
``for episode in range(1000): env.reset()`` meets 1000 markets and meets the
same 1000 on every run. ``info["seed"]`` is the seed the episode ran, and
``reset(seed=info["seed"])`` replays it. Until 0.8.5 a ``reset()`` without a
seed always replayed the constructor's market, even straight after
``reset(seed=99)``.
"""

from __future__ import annotations

from typing import Any, Sequence

from ._core import check_seed
from ._core import (Engine, Instrument, Macro, ModelParams, OrderError,
                    ValidationError)
from .harness import session_clock
from .portfolio import Portfolio
from .sandbox import MarketView, PortfolioView
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
            f"{name} is required for tradefloor.gym but is not installed. "
            f"Install it with: pip install {extra}"
        )
    return module


class TradingEnv(_Base):
    """A market as a reinforcement-learning environment.

    Observation: prices, holdings as fractions of net worth, and cash as a
    fraction of net worth. All ``float64`` and C-contiguous, as a ``Box``
    space wants.

    Prices are given as log returns since the previous step rather than as
    levels. A level of 512.44 tells a policy nothing without knowing what it
    was before, and the range across a generated roster spans two orders of
    magnitude; returns are stationary and comparable across instruments.

    Action: a target weight in ``[-1, 1]`` per instrument, scaled down as a
    whole when the weights ask for more than ``max_leverage`` allows. Reward:
    the step's change in net worth, in dollars. The module docstring has the
    details of both.
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
        model: str | ModelParams | None = None,
        trusted_agents: bool = False,
    ) -> None:
        _require(_np, "numpy", "numpy")

        self.universe = as_universe(universe)
        self.base_seed = check_seed(seed)
        self.macro = macro
        # The coefficient set every episode runs -- a preset name or a
        # ModelParams, fixed at construction like the universe. Per-episode
        # models would make a policy's replay buffer a mixture of markets
        # with nothing in the observation to tell them apart; train against
        # a different model by building a different env.
        self.model = model
        self.days = int(days)
        self.steps_per_day = int(steps_per_day)
        self.ticks_per_step = int(ticks_per_step)
        self.starting_cash = float(cash)
        self.max_leverage = max_leverage
        self.start = start
        #: ``env.engine`` and ``env.portfolio`` are the live objects rather
        #: than read-only views. See the module docstring.
        self.trusted_agents = bool(trusted_agents)

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

        # The live market, which only the env steps. `engine` and
        # `portfolio` below are what anything holding the env sees.
        self._engine: Engine | None = None
        self._portfolio: Portfolio | None = None
        self._shown: tuple[Any, Any] = (None, None)
        self._step = 0
        self._prev_prices = None
        self._prev_worth = 0.0
        # Whether a reset has seeded the generator that draws the seed of
        # each reset() given none. Until one has, reset() runs `base_seed`.
        self._seeded = False
        # That generator when gymnasium is absent. With it, the generator is
        # gymnasium's own `np_random`. See `_seed_generator`.
        self._fallback_rng = None

    @property
    def engine(self) -> Any:
        """This episode's market, as a read-only
        :class:`~tradefloor.sandbox.MarketView`; the live engine under
        ``trusted_agents=True``. None before :meth:`reset`."""
        return self._engine if self.trusted_agents else self._shown[0]

    @property
    def portfolio(self) -> Any:
        """This episode's book, as a read-only
        :class:`~tradefloor.sandbox.PortfolioView`; the live portfolio
        under ``trusted_agents=True``. None before :meth:`reset`."""
        return self._portfolio if self.trusted_agents else self._shown[1]

    # -- gym API ----------------------------------------------------------

    def reset(self, *, seed: int | None = None, options: dict | None = None):
        """Start a fresh episode.

        ``seed`` selects the market. Passing a different one gives a different
        market, deliberately: an agent trained on one seed and evaluated on
        another is being tested rather than recalled. Any integer from 0 to
        ``2**64 - 1``, checked before Gymnasium's own generator sees it, so a
        refusal names the engine's range rather than numpy's.

        With ``seed=None`` the first reset runs the constructor's seed, and
        each later one runs a new seed below ``2**32`` drawn from the env's
        generator. ``reset(seed=n)`` reseeds that generator, so the markets
        that follow it are the same on every run. ``info["seed"]`` is the
        seed the episode ran.
        """
        if seed is not None:
            episode_seed = check_seed(seed)
            self._seed_generator(episode_seed)
        elif not self._seeded:
            episode_seed = self.base_seed
            self._seed_generator(episode_seed)
        else:
            episode_seed = self._draw_seed()
        self._seeded = True

        engine = Engine(seed=episode_seed, universe=self.universe,
                        macro_state=self.macro, model=self.model)
        portfolio = Portfolio(cash=self.starting_cash,
                              max_leverage=self.max_leverage)
        self._engine, self._portfolio = engine, portfolio
        # Built once per episode, so `env.engine` is one object for the
        # episode and a new one after the next reset, as it always was.
        self._shown = (MarketView(engine), PortfolioView(portfolio, engine))
        self._step = 0
        engine.open_market()
        self._prev_prices = self._prices()
        self._prev_worth = portfolio.net_worth(engine)
        # The info dict names the episode's market: the seed that drew it
        # and the model that priced it, so a training log can cite both.
        info = {"seed": episode_seed,
                "model_fingerprint": engine.model_fingerprint}
        if self.trusted_agents:
            # Only when set, so every sandboxed info dict is the one it was.
            info["trusted"] = True
        return self._observe(), info

    def step(self, action):
        if self._engine is None or self._portfolio is None:
            raise ValidationError("call reset() before step()")
        engine, portfolio = self._engine, self._portfolio

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
        action, scaled = self._within_cap(action)

        rejected = self._rebalance(action)

        # The clock advances within the day, so an episode traverses trading
        # days rather than replaying each one's opening minutes. See
        # `harness.session_clock` for the measurement.
        engine.run_session(
            *session_clock(self.start, self._step % self.steps_per_day,
                           self.ticks_per_step),
            self.ticks_per_step,
            fills=portfolio.pending_flow())
        portfolio.clear_flow()

        self._step += 1
        if self._step % self.steps_per_day == 0:
            engine.close_market()
            if self._step < self.max_steps:
                engine.open_market()

        worth = portfolio.net_worth(engine)
        # Reward is the step's P&L in dollars, measured AFTER the market
        # moved, so it includes the cost of the agent's own footprint.
        reward = worth - self._prev_worth
        self._prev_worth = worth

        terminated = worth <= 0.0     # insolvent: the episode is genuinely over
        truncated = self._step >= self.max_steps and not terminated

        info = {
            "net_worth": worth,
            "cash": portfolio.cash,
            "leverage": portfolio.leverage(engine),
            "rejected": rejected,
            # True when the action's weights asked for more than the
            # leverage cap and were scaled down to fit. See `_within_cap`.
            "scaled": scaled,
            "step": self._step,
        }
        return self._observe(), float(reward), bool(terminated), bool(truncated), info

    # -- internals --------------------------------------------------------

    def _seed_generator(self, seed: int) -> None:
        """Seed the generator that draws the seed of a reset given none."""
        if _gym is not None:
            # Gymnasium keeps its own generator on the base class and its API
            # checker enforces that reset seeds it. That generator is the one
            # `_draw_seed` reads; the market itself draws nothing from it,
            # since all of its randomness lives in the engine's PCG32 stream.
            super().reset(seed=seed)
        else:
            # Built the way gymnasium's `seeding.np_random` builds its
            # generator, so the drawn seeds are the same with gymnasium
            # installed or without it.
            self._fallback_rng = _np.random.Generator(
                _np.random.PCG64(_np.random.SeedSequence(seed)))

    def _draw_seed(self) -> int:
        """The next episode's seed, drawn from the seeded generator."""
        rng = self.np_random if _gym is not None else self._fallback_rng
        # Below 2**32: plenty of markets for any training run, and short
        # enough to read in a log and to survive a JSON round trip through
        # JavaScript, whose numbers lose integers above 2**53.
        return int(rng.integers(2**32))

    def _within_cap(self, weights):
        """The target weights, scaled down if they ask for more than the cap.

        Returns the weights and whether they were scaled. An action whose
        absolute weights sum past the cap is scaled as a whole to a gross of
        ``max_leverage / (1 + _FILL_COST * max_leverage)``: the most a book
        can hold at the cap after paying fills that cost ``_FILL_COST`` of
        what they buy. Scaling every weight by one factor keeps the action's
        proportions, so the position a policy gets does not depend on which
        name comes first in the roster.
        """
        if self.max_leverage is None:
            return weights, False
        room = self.max_leverage / (1.0 + _FILL_COST * self.max_leverage)
        gross = float(_np.abs(weights).sum())
        if gross <= room:
            return weights, False
        return weights * (room / gross), True

    def _prices(self):
        # Asserted rather than assumed: these helpers are only reachable after
        # reset(), but nothing enforced that across a method boundary, and a
        # helper called early would have failed on None with a worse message.
        assert self._engine is not None, "engine not built - call reset()"
        import struct
        return _np.array(
            struct.unpack("<%dd" % self.n, self._engine.prices()), dtype=_np.float64
        )

    def _rebalance(self, weights) -> int:
        """Trade towards the target weights. Returns how many trades were refused."""
        assert self._engine is not None and self._portfolio is not None
        prices = self._prices()
        worth = self._portfolio.net_worth(self._engine)
        shrinking, growing = [], []
        for i, ticker in enumerate(self._engine.tickers):
            if prices[i] <= 0:
                continue
            target = weights[i] * worth / prices[i]
            held = self._portfolio.positions.get(ticker, _Zero).quantity
            delta = target - held
            # A threshold, so floating-point dust does not generate a trade
            # every step. One share is the smallest unit anyone would act on.
            if abs(delta) < 1.0:
                continue
            (shrinking if abs(target) < abs(held) else growing).append(
                (ticker, delta))
        # Every trade that shrinks a position goes before any that grows one.
        # In roster order, a move from one name at 1.9x into another at 1.9x
        # bought first when the new name came first, and the cap refused it.
        rejected = 0
        for ticker, delta in shrinking + growing:
            try:
                self._portfolio.execute(self._engine, ticker, delta)
            except (OrderError, ValidationError):
                # A refused trade is information, not a failure. Being unable
                # to reach a target -- because the book is thin or leverage is
                # capped -- is a fact about the action, and the agent should
                # experience it rather than have the episode die.
                rejected += 1
        return rejected

    def _observe(self):
        assert self._engine is not None and self._portfolio is not None
        prices = self._prices()
        # Log returns, not levels: a level says nothing without its history,
        # and the range across a roster spans two orders of magnitude.
        with _np.errstate(divide="ignore", invalid="ignore"):
            returns = _np.log(prices / self._prev_prices)
        returns = _np.nan_to_num(returns, nan=0.0, posinf=0.0, neginf=0.0)
        self._prev_prices = prices

        worth = self._portfolio.net_worth(self._engine)
        denom = worth if worth > 0 else 1.0
        holdings = _np.array(
            [
                self._portfolio.positions.get(t, _Zero).quantity * p / denom
                for t, p in zip(self._engine.tickers, prices)
            ],
            dtype=_np.float64,
        )
        cash_fraction = _np.array([self._portfolio.cash / denom], dtype=_np.float64)
        return _np.ascontiguousarray(
            _np.concatenate([returns, holdings, cash_fraction]), dtype=_np.float64
        )

    def render(self):  # pragma: no cover - no visual mode
        return None

    def close(self):  # pragma: no cover
        self._engine = None
        self._portfolio = None
        self._shown = (None, None)


#: What `TradingEnv._within_cap` allows a fill to cost, as a fraction of
#: what it buys, when it scales an over-cap action. Measured on 540 trades
#: from flat (3 to 20 names, six random rosters each, all-ones and random
#: signed actions, the default 2x cap), fills cost at most 0.40% of what they
#: bought at the default 1,000,000 of cash, 0.88% at 10,000,000 and 1.0% at
#: 100,000,000, where the thinnest books filled only part of each order. The
#: cap refused none of the 540.
_FILL_COST = 0.01


class _ZeroPosition:
    """Stand-in for an unheld instrument, so lookups need no branch."""

    __slots__ = ()
    quantity = 0.0


_Zero = _ZeroPosition()
