"""Ranking agents across many markets, because one market ranks them wrongly.

`evaluate` scores every agent against one seed. That is the right primitive,
it is what makes a comparison exact, since all agents see the identical market
but it is the wrong unit of judgement, and the difference is not small.

Measured on this build, with the reference agents over
``Universe.random(30, seed=11)``, ten days, sim seeds 0 through 11:

    pooled capture over 12 seeds        per-seed range      wins
        momentum         +0.773       [+0.007, +2.834]      9/12
        buy_and_hold     +0.081       [-0.621, +0.592]      0/12
        mean_reversion   -0.010       [-1.863, +0.980]      3/12
        random           -0.037       [-0.305, +0.138]      0/12

**A single seed names the pooled leader nine times in twelve here, and still
misreports the verdict.** The case for many seeds is not that one seed picks
the wrong winner, since it usually does not. It is that one seed cannot say what
the winner is WORTH: momentum's own capture runs from +0.007 to +2.834
depending only on which market it drew, from a rounding error above nothing
to nearly triple the Oracle, and that range is printed next to the verdict for
exactly that reason.

So a leaderboard from one call to `evaluate` is a measurement of the seed at
least as much as of the agents, and anything built on it (a benchmark, a
regression gate, an agent that tunes itself against this harness) inherits
that.

## And the aggregate can overstate too, which is why `separation` exists

That gap does NOT establish that momentum is the better agent. Paired across
the same twelve markets, momentum beats mean-reversion on nine and loses on
three: `p = 0.15`, no separation worth the name. Momentum wins by MORE when
it wins; it does not win often enough for twelve paired trials to call the
ordering real, and no aggregate of returns can tell those apart.

Against random the same test reads 11 to 1, `p = 0.006`. That is what a real
difference looks like here, and the contrast is the point: two orderings that
appear on the same table, one of them meaningless.

A p-value also carries its seed window with it: the identical
momentum-versus-mean-reversion test over seeds 12 to 23 reads 10 to 2 at
`p = 0.039`. Twelve paired seeds is a small experiment, and even a clean
sweep only reaches p = 0.0005, so one window's p is a single draw of a noisy
statistic, and the honest quote names the seeds.

Note that even 11 to 1 is not `decisive`. That flag is reserved for a clean
sweep, the one verdict that needs no distributional assumption at all. A
small `p` and a clean sweep are different claims and the result reports both.

Quote a capture with its separation, or the ranking is just a prettier
version of the single-seed verdict.

## The aggregate pools; it does not average ratios

A capture ratio divides by what the reference earned in that market, which on
a short horizon can be almost nothing. Measured at three days on the same
universe, sim seeds 0-9: the Oracle's per-seed P&L spans $10.6k to $36.8k,
and against one thin denominator, 1.1% of the $1M book, mean-reversion's
ratio is **+3.85**. A single value like that drags a median of ten far
enough to reorder the whole table: ranked by median of ratios, momentum
drops below buy-and-hold, which the pooled figure reverses.

So the headline sums the numerators and the denominators instead. Each market
is weighted by the opportunity that actually existed in it, a seed with
nothing to earn contributes nothing to either sum, and the number answers a
question a reader has: of all the alpha the reference captured across these
markets, how much did this agent capture? The per-seed ratios are still
reported, because each is a true fact about its own seed and the spread is
the warning.

## What it reports, and why not a p-value on returns

`rank` returns the pooled capture across seeds, the per-seed range, and a
win count.
:meth:`Ranking.separation` answers "is A really better than B" with a sign
test: the number of seeds where A beat B, out of the paired seeds where both
were measurable. Paired, because both agents traded the *same* market on each
seed, which removes the market from the comparison entirely and is the whole
reason per-seed pairing is worth the runtime.

A sign test rather than a t-test on returns, because capture ratios across
seeds are neither normal nor independent of the market's dispersion, and a
p-value computed as though they were would be a precise-looking number built
on an assumption this library can measure to be false. Counting wins assumes
almost nothing.

## Agents are stateful, so this takes a factory

`Momentum` keeps a rolling window; `RandomTrader` advances a generator. Handing
the same instances to twelve seeds would carry seed 0's history into seed 1 and
score something that is not the agent. That failure is silent, because the
numbers look fine, so `rank` refuses a plain mapping rather than accepting one and
quietly measuring the wrong thing.
"""

from __future__ import annotations

import statistics
from typing import Any, Callable, Iterable, Sequence

from ._core import Instrument, Macro, ModelParams, ValidationError


class AgentRecord:
    """One agent's results across every seed in a ranking.

    ``captures`` and ``pnls`` are parallel to the ranking's ``seeds``, so a
    result can always be traced back to the market that produced it. A capture
    is ``None`` where it could not be measured -- see :class:`Ranking`.
    """

    __slots__ = ("name", "seeds", "captures", "pnls", "wins",
                 "reference_pnls")

    def __init__(self, name: str, seeds: list[int],
                 reference_pnls: list[float]) -> None:
        self.name = name
        self.seeds = seeds
        #: The reference's P&L per seed, shared with the ranking. Held here so
        #: a record can pool without reaching back for its parent.
        self.reference_pnls = reference_pnls
        self.captures: list[float | None] = []
        self.pnls: list[float] = []
        self.wins = 0

    @property
    def measured(self) -> list[float]:
        """The captures that exist, in seed order."""
        return [c for c in self.captures if c is not None]

    @property
    def pooled_capture(self) -> float | None:
        """The number to quote: total P&L over the reference's total P&L.

        Pooled rather than averaged, and the difference is not cosmetic. A
        per-seed ratio divides by whatever the reference happened to earn in
        that market, which on a short horizon can be almost nothing --
        measured at three days on the grid in this module's docstring, a
        seed where the reference earned 1.1% of capital produced a capture
        ratio of **+3.85**, and one such seed drags a median of ten far
        enough to reorder the table.

        Pooling weights each market by the opportunity that existed in it. A
        seed where nothing was there to earn contributes nearly nothing to the
        numerator AND nearly nothing to the denominator, so it cannot dominate.
        It also answers the question a reader actually has: across all these
        markets, what fraction of what the reference captured did this agent
        capture?

        Seeds where the reference lost money are excluded from both sums --
        see :attr:`Ranking.unmeasurable` -- because a negative denominator
        flips the sign of everything above it.
        """
        numerator = 0.0
        denominator = 0.0
        for pnl, reference in zip(self.pnls, self.reference_pnls):
            if reference > 0.0:
                numerator += pnl
                denominator += reference
        return numerator / denominator if denominator > 0.0 else None

    @property
    def median_capture(self) -> float | None:
        """The middle per-seed ratio. Prefer :attr:`pooled_capture`.

        Kept because a per-seed ratio is a true fact about its own seed and
        the distribution is worth seeing. But a median OF ratios inherits
        every explosion in the tail -- see :attr:`pooled_capture` -- so it is
        no longer what the table sorts on.
        """
        values = self.measured
        return statistics.median(values) if values else None

    @property
    def capture_range(self) -> tuple[float, float] | None:
        values = self.measured
        return (min(values), max(values)) if values else None

    @property
    def median_pnl(self) -> float:
        return statistics.median(self.pnls) if self.pnls else 0.0

    @property
    def win_rate(self) -> float:
        """Fraction of seeds this agent ranked first on, by P&L."""
        return self.wins / len(self.pnls) if self.pnls else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "seeds": list(self.seeds),
            "captures": list(self.captures),
            "pnls": list(self.pnls),
            "wins": self.wins,
            "pooled_capture": self.pooled_capture,
            "median_capture": self.median_capture,
            "median_pnl": self.median_pnl,
            "win_rate": self.win_rate,
        }

    def __repr__(self) -> str:
        pooled = self.pooled_capture
        shown = f"{pooled:+.3f}" if pooled is not None else "n/a"
        return (f"AgentRecord({self.name!r}, pooled_capture={shown}, "
                f"wins={self.wins}/{len(self.pnls)})")


class Ranking:
    """Agent results over a set of seeds, and the comparisons worth making."""

    __slots__ = ("records", "seeds", "unmeasurable", "universe_fingerprint",
                 "oracle", "reference_pnls", "model_fingerprint")

    def __init__(self, records: dict[str, AgentRecord], seeds: list[int],
                 unmeasurable: list[int], universe_fingerprint: str,
                 oracle: str, reference_pnls: list[float],
                 model_fingerprint: str = "") -> None:
        self.records = records
        self.seeds = seeds
        #: What the reference earned on each seed, parallel to ``seeds``. This
        #: is the denominator, and it varies several-fold across seeds --
        #: measured $10.6k to $36.8k over ten three-day seeds on the grid in
        #: this module's docstring -- which is exactly why the headline
        #: number pools rather than averages ratios.
        self.reference_pnls = reference_pnls
        #: Seeds where capture was not measurable because the reference did
        #: not make money. Reported rather than dropped: a median over eight
        #: of twelve seeds that presents itself as twelve is the kind of quiet
        #: omission this library exists to not do.
        self.unmeasurable = unmeasurable
        self.universe_fingerprint = universe_fingerprint
        self.oracle = oracle
        #: The model every seed ran under -- one value, because ranking
        #: agents across different models would compare markets, not
        #: agents. A shipped preset's name or custom-XXXXXXXX.
        self.model_fingerprint = model_fingerprint

    def table(self, by: str = "pooled_capture") -> list[AgentRecord]:
        """Records sorted best-first, ties broken on name.

        Sorts on pooled capture rather than the median of per-seed ratios;
        :attr:`AgentRecord.pooled_capture` explains why, with the measurement.

        Falls back to median P&L when capture was never measurable, so a
        ranking without a working reference still ranks rather than raising.
        """
        if by not in ("pooled_capture", "median_capture", "median_pnl",
                      "win_rate"):
            raise ValidationError(f"cannot rank by {by!r}")
        if by in ("pooled_capture", "median_capture") and not any(
            getattr(r, by) is not None for r in self.records.values()
        ):
            by = "median_pnl"

        def key(record: AgentRecord):
            value = getattr(record, by)
            # An unmeasurable agent sorts last rather than crashing the sort
            # or silently reading as zero, which would rank it above every
            # agent that lost money.
            return (0, record.name) if value is None else (-value, record.name)

        measurable = [r for r in self.records.values()
                      if getattr(r, by) is not None]
        missing = [r for r in self.records.values()
                   if getattr(r, by) is None]
        return (sorted(measurable, key=key)
                + sorted(missing, key=lambda r: r.name))

    def separation(self, a: str, b: str) -> dict[str, Any]:
        """Is ``a`` really better than ``b``? A paired sign test.

        Both agents traded the same market on each seed, so comparing them
        seed by seed removes the market from the question. Returns the win
        counts and a ``decisive`` flag, which is true only when one agent won
        on every paired seed -- the strongest claim a sign test can make and
        the only one that needs no distributional assumption at all.

        ``p_value`` is the two-sided probability of a split at least this
        lopsided if the two were coin-flip equal. Exact rather than
        approximate, being a binomial tail on a handful of trials.
        """
        for name in (a, b):
            if name not in self.records:
                raise ValidationError(
                    f"{name!r} is not in this ranking. Have: "
                    + ", ".join(sorted(self.records))
                )
        left, right = self.records[a], self.records[b]
        wins_a = wins_b = ties = 0
        for pa, pb in zip(left.pnls, right.pnls):
            if pa > pb:
                wins_a += 1
            elif pb > pa:
                wins_b += 1
            else:
                ties += 1
        paired = wins_a + wins_b
        return {
            "a": a,
            "b": b,
            "wins_a": wins_a,
            "wins_b": wins_b,
            "ties": ties,
            "paired_seeds": paired,
            "decisive": paired > 0 and (wins_a == paired or wins_b == paired),
            "p_value": _sign_test(wins_a, wins_b),
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "seeds": list(self.seeds),
            "unmeasurable_seeds": list(self.unmeasurable),
            "universe_fingerprint": self.universe_fingerprint,
            "model_fingerprint": self.model_fingerprint,
            "oracle": self.oracle,
            "reference_pnls": list(self.reference_pnls),
            "agents": {n: r.as_dict() for n, r in self.records.items()},
        }

    def report(self) -> str:
        """A few lines fit to print, including what could not be measured."""
        lines = [
            f"{len(self.seeds)} seeds on universe "
            f"{self.universe_fingerprint[:12]}..."
            # The model is part of the citation whenever one was recorded;
            # a custom-XXXXXXXX here is what stops a modified-model ranking
            # reading as a benchmark table.
            + (f" under model {self.model_fingerprint}"
               if self.model_fingerprint else "")
        ]
        for record in self.table():
            pooled = record.pooled_capture
            span = record.capture_range
            if pooled is None or span is None:
                lines.append(f"  {record.name:16s}  capture unmeasurable  "
                             f"median pnl {record.median_pnl:+12,.0f}")
                continue
            # The pooled figure is the verdict; the per-seed span is shown
            # beside it because a wide one is the warning that a single seed
            # would have said something else entirely.
            lines.append(
                f"  {record.name:16s}  capture {pooled:+.3f}  "
                f"per-seed [{span[0]:+.3f}, {span[1]:+.3f}]  "
                f"wins {record.wins}/{len(record.pnls)}"
            )
        if self.unmeasurable:
            shown = ", ".join(str(s) for s in self.unmeasurable[:8])
            more = ", ..." if len(self.unmeasurable) > 8 else ""
            lines.append(
                f"  capture unmeasurable on {len(self.unmeasurable)} seed(s) "
                f"({shown}{more}): the reference lost money there, so a ratio "
                "against it would flip sign."
            )
        return "\n".join(lines)

    def __repr__(self) -> str:
        return (f"Ranking({len(self.records)} agents, {len(self.seeds)} seeds, "
                f"{len(self.unmeasurable)} unmeasurable)")


def _sign_test(wins_a: int, wins_b: int) -> float | None:
    """Two-sided exact binomial tail for a paired sign test.

    Written out rather than pulled from scipy, which this library does not
    depend on. Exact, so it stays honest at the tiny trial counts a sweep of
    eight or twelve seeds actually produces -- where a normal approximation
    would report a confident p-value on four observations.
    """
    n = wins_a + wins_b
    if n == 0:
        return None
    k = min(wins_a, wins_b)
    total = 0.0
    coefficient = 1.0
    for i in range(k + 1):
        if i:
            coefficient = coefficient * (n - i + 1) / i
        total += coefficient
    return min(1.0, 2.0 * total / (2.0 ** n))


def _factory_or_refuse(make_agents: Any) -> Callable[[], dict[str, Any]]:
    if callable(make_agents):
        return make_agents
    raise ValidationError(
        "rank() needs a factory that BUILDS agents, not built agents. Agents "
        "are stateful -- Momentum keeps a rolling window, RandomTrader "
        "advances a generator -- so reusing instances would carry the first "
        "seed's history into the second and score something that is not the "
        "agent, with no visible symptom. Pass a callable: "
        "rank(lambda: reference_agents(seed=3), seeds=range(12), ...)"
    )


def rank(
    make_agents: Callable[[], dict[str, Any]],
    *,
    seeds: Iterable[int],
    universe: Sequence[Instrument],
    macro: Macro | None = None,
    days: int = 5,
    steps_per_day: int = 6,
    ticks_per_step: int = 65,
    cash: float = 1_000_000.0,
    max_leverage: float | None = 2.0,
    start: tuple[int, int, int] = (9, 30, 3),
    scenario: Any = None,
    oracle: str = "oracle",
    workers: int = 1,
    model: str | ModelParams | None = None,
) -> Ranking:
    """Score agents on many seeds and rank them on the aggregate.

    ``make_agents`` is called once per seed and must return a fresh mapping;
    see this module's docstring for why instances are refused.

    Every agent still meets an identical market within each seed, so the
    per-seed comparison stays exact. What changes is that the verdict is taken
    across seeds, where it is a property of the agents rather than of one draw.

    ``model`` selects the coefficient set every evaluation runs, either a
    preset name or a :class:`tradefloor.ModelParams`, one model for the whole
    ranking, agents and seeds alike, because a verdict taken across models
    would rank markets rather than agents. The :class:`Ranking` records
    ``model_fingerprint``, as does every scorecard under it.

    ```python
    ranking = tf.rank(lambda: reference_agents(seed=3), seeds=range(12),
                      universe=u, days=10)
    print(ranking.report())
    ranking.separation("momentum", "mean_reversion")
    ```
    """
    from .baselines import capture_ratio
    from .harness import evaluate
    from .universe_util import as_universe, fingerprint_of

    factory = _factory_or_refuse(make_agents)
    seed_list = [int(s) for s in seeds]
    if not seed_list:
        raise ValidationError("no seeds given")
    if len(set(seed_list)) != len(seed_list):
        # Repeating a seed would double-count one market and quietly weight
        # every median toward it.
        raise ValidationError("seeds must be distinct; a repeated seed would "
                              "weight that market twice in every median")
    if workers < 1:
        raise ValidationError(f"workers must be at least 1, got {workers}")

    roster = as_universe(universe)
    kwargs: dict[str, Any] = dict(
        universe=roster, macro=macro, days=days, steps_per_day=steps_per_day,
        ticks_per_step=ticks_per_step, cash=cash, max_leverage=max_leverage,
        start=start, scenario=scenario, model=model,
    )

    def one(seed: int):
        return seed, evaluate(factory(), seed=seed, **kwargs)

    if workers == 1:
        results = [one(seed) for seed in seed_list]
    else:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(one, seed) for seed in seed_list]
            # Collected in SEED order, never completion order, so a median
            # over an even count picks the same element on every run.
            results = [f.result() for f in futures]

    records: dict[str, AgentRecord] = {}
    unmeasurable: list[int] = []
    reference_pnls: list[float] = []
    for seed, scores in results:
        ratios = capture_ratio(scores, oracle=oracle)
        reference = scores[oracle].pnl if oracle in scores else 0.0
        reference_pnls.append(reference)
        if not ratios:
            unmeasurable.append(seed)
        contenders = {n: c for n, c in scores.items() if n != oracle}
        winner = (max(contenders, key=lambda n: (contenders[n].pnl, n))
                  if contenders else None)
        for name, card in contenders.items():
            record = records.setdefault(
                name, AgentRecord(name, seed_list, reference_pnls))
            record.captures.append(ratios.get(name))
            record.pnls.append(card.pnl)
            if name == winner:
                record.wins += 1

    # Read off a scorecard rather than recomputed here, so the recorded
    # name is the one the evaluations actually ran under.
    model_fingerprint = next(iter(results[0][1].values())).model_fingerprint
    return Ranking(records, seed_list, unmeasurable, fingerprint_of(roster),
                   oracle, reference_pnls, model_fingerprint)
