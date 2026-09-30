"""Ranking agents across many markets, because one market ranks them wrongly.

`evaluate` scores every agent against one seed. That is the right primitive,
it is what makes a comparison exact, since all agents see the identical market
but it is the wrong unit of judgement, and the difference is not small.

Measured on this build under pt-v19, with the reference agents over
``Universe.random(30, seed=11)``, ten days, sim seeds 0 through 11. Every
capture figure in this docstring is from pt-v19, the last preset where the
Oracle is a ceiling. The default, pt-v20, reports no capture and ranks each
agent on its P&L over buy-and-hold's instead (see "On pt-v20" below).

    pooled capture over 12 seeds        per-seed range      wins
        buy_and_hold     +0.095       [-0.776, +0.836]      9/12
        mean_reversion   -0.075       [-0.464, +0.273]      3/12
        random           -0.337       [-0.625, -0.248]      0/12
        momentum         -0.950       [-1.336, -0.477]      0/12

Until 0.8.5 this table was led by mean reversion at +0.947, winning 11 of
12. That lead was the harness: an agent's fills were held on every tick of
the step, so an agent that trades a lot was marked to many times its own
impact. With the fills applied once, nothing that sees only prices keeps
much of what the Oracle earns over ten days, and the winner is the agent
that trades least.

**A single seed names the pooled leader nine times in twelve here, and still
misreports the verdict.** The case for many seeds is not that one seed picks
the wrong winner, since it usually does not. It is that one seed cannot say
what the winner is WORTH: buy-and-hold's own capture runs from -0.776 to
+0.836 depending only on which market it drew, from losing three quarters of
what the Oracle made to keeping most of it, and that range is printed next
to the verdict for exactly that reason.

So a leaderboard from one call to `evaluate` is a measurement of the seed at
least as much as of the agents, and anything built on it (a benchmark, a
regression gate, an agent that tunes itself against this harness) inherits
that.

## And the aggregate can overstate too, so `separation` exists

That gap does NOT establish that buy-and-hold is the better agent. Paired
across the same twelve markets, buy-and-hold beats mean reversion on nine and
loses on three: `p = 0.15`, no separation worth the name. It wins more often
than it loses; twelve paired trials cannot call the ordering real, and no
aggregate of returns can tell those apart.

Mean reversion against random reads 10 to 2, `p = 0.039`, and momentum
against random 0 to 12, `p = 0.0005`, a clean sweep in random's favour. That
is what a real difference looks like here, and the contrast is the point:
orderings that appear on the same table, one of them meaningless.

A p-value also carries its seed window with it: the identical
buy-and-hold-versus-mean-reversion test over seeds 12 to 23 reads 4 to 8,
`p = 0.39`, the other way round, and there mean reversion leads the pooled
table by a hair (-0.093 against -0.098). Twelve paired seeds is a small
experiment, and even a clean sweep only reaches p = 0.0005, so one window's p
is a single draw of a noisy statistic, and the honest quote names the seeds.

Note that 10 to 2 is not `decisive`. That flag is reserved for a clean
sweep, the one verdict that needs no distributional assumption at all. A
small `p` and a clean sweep are different claims and the result reports both.

Quote a capture with its separation, or the ranking is just a prettier
version of the single-seed verdict.

## The aggregate pools; it does not average ratios

A capture ratio divides by what the reference earned in that market, which on
a short horizon can be almost nothing. Measured at three days on the same
universe, sim seeds 0-9: the Oracle's per-seed P&L spans $11.5k to $28.1k,
and against the thinnest denominator, 1.1% of the $1M book, momentum's ratio
is **-2.54** and buy-and-hold's +1.00. Values like those drag a median of ten
far enough to reorder the table: ranked by median of ratios, mean reversion
(+0.189) goes above buy-and-hold (+0.054), which the pooled figure reverses
(-0.035 against +0.042). Until 0.8.5, which stopped counting an agent's
fills on every tick of a step, the same grid read $15.5k to $34.1k and a
mean-reversion ratio of +1.50 on its best seed.

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

It needs more seeds than people expect. Six wins out of six paired seeds is
p = 0.031, and five out of five is p = 0.062, so below six seeds nothing can
separate at 0.05. At eight seeds 8 of 8 is p = 0.0078, but 7 of 8 is
p = 0.070 and 6 of 8 is p = 0.29. At twelve, 10 of 12 is p = 0.039. Pick the
seed count before the run, from the smallest split you would want to call
real.

## On pt-v20 there is no capture, and the table reads against buy-and-hold

A capture divides by the Oracle's P&L, and on pt-v20 that is not a ceiling:
market moves mostly stick, the Oracle made money in 10 of 14 test markets,
and its P&L follows the market's month (``baselines.ORACLE_NOT_A_CEILING``).
There `rank` reports no capture at all. :attr:`Ranking.capture_withheld`
gives the reason, no seed is listed as unmeasurable, and the table sorts on
each agent's mean P&L over buy-and-hold's in the same market, with the count
of seeds it came out ahead. The sign test is unchanged, since it never read
a capture. Every preset through pt-v19 ranks on pooled capture as before.

Buy-and-hold here is the entrant labelled ``buy_and_hold``. With no entrant
of that name, `rank` uses the one entrant that is a
:class:`~tradefloor.baselines.BuyAndHold` or a ``StrategySpec.hold()`` and
the report says which label it read. Pass ``benchmark=`` to name another.

## Agents are stateful, so this takes a factory

`Momentum` keeps a rolling window; `RandomTrader` advances a generator. Handing
the same instances to twelve seeds would carry seed 0's history into seed 1 and
score something that is not the agent. That failure is silent, because the
numbers look fine, so `rank` refuses a mapping of built agents rather than
accepting one and measuring the wrong thing.

A factory that hands back the same objects every time has the same problem,
so `rank` keeps the agents from the first call and refuses the run if a later
call returns any of them again, or if one call puts the same object under two
labels. On seeds 101, 202 and 303 of ``Universe.random(6, seed=1)``, three
days each, a shared momentum agent read a median P&L of -5,356 against
-2,163 for fresh ones.

A mapping whose values are all :class:`tradefloor.StrategySpec` is accepted
as it is. A spec is the instruction for building an agent, and
:func:`tradefloor.evaluate` builds it fresh on every seed.

## An agent whose code raised is ranked, and marked

`evaluate` scores an agent whose `act()` raised as though it placed no orders
on that step, and keeps the exception in `Scorecard.errors`. An agent that
raised on every step is scored as one that did nothing, and against
buy-and-hold in a falling market that can read as ahead. So each record keeps
the number of times the agent's code raised on each seed
(:attr:`AgentRecord.errors`), its refused orders (:attr:`AgentRecord.rejected`)
and its peak leverage (:attr:`AgentRecord.max_leverage`), and
:meth:`Ranking.report` names every agent that raised, with the count, the
seeds and the first exception. The agent stays in the table, so gate on
those fields if a raise should fail a run. An LLM integration's reply that
cannot be parsed into orders raises in `act()`, so it is counted here too.

## A tampered agent is not ranked

`evaluate` compares the engine's state hash around every call into agent
code (see :mod:`tradefloor.sandbox`). An agent that changed the market on any
seed is left out of the table, the win counts and the comparisons, and
:attr:`Ranking.tampered` and :meth:`Ranking.report` say which and where. Its
score is of a market it rewrote. An agent handed the live engine
(``trusted_agents=True``) or hidden state (``privileged = True``) is ranked,
and marked in the report, because nothing on its card can say it did not
use what it was given.
"""

from __future__ import annotations

import re
import statistics
from typing import Any, Callable, Iterable, Mapping, Sequence

from ._core import Instrument, Macro, ModelParams, ValidationError
from ._core import check_seed

#: How `evaluate` writes what went wrong on a scorecard. A step where
#: ``act()`` raised reads ``step 3: KeyError: 'x'``, and ``explain()``
#: raising reads ``day 0 explain: KeyError: 'x'``. A refused order reads
#: ``step 3: <reason>`` and is counted in ``Scorecard.rejected`` as well. A
#: tamper line carries ``tampered:`` and is left out here, since a tampered
#: agent is not ranked at all.
_TAMPER_LINE = re.compile(r"^(?:step \d+|day \d+ explain): tampered: ")
_EXPLAIN_LINE = re.compile(r"^day \d+ explain: ")
_ACT_RAISED_LINE = re.compile(r"^step \d+: [A-Za-z_][\w.]*: ")


def _raised(card: Any) -> tuple[int, int, str | None]:
    """How many times an agent's ``act()`` and ``explain()`` raised on one
    scorecard, and the first line that says so.

    Counted by subtraction rather than by reading each line: every refused
    order wrote exactly one step line and added one to ``rejected``, so the
    step lines left over are the steps where ``act()`` raised, whatever the
    refusal's text happens to look like.
    """
    lines = [line for line in card.errors if not _TAMPER_LINE.match(line)]
    in_explain = sum(1 for line in lines if _EXPLAIN_LINE.match(line))
    in_act = max(0, len(lines) - in_explain - card.rejected)
    first = next((line for line in lines
                  if (in_explain and _EXPLAIN_LINE.match(line))
                  or (in_act and _ACT_RAISED_LINE.match(line))), None)
    return in_act, in_explain, first


class AgentRecord:
    """One agent's results across every seed in a ranking.

    ``captures`` and ``pnls`` are parallel to the ranking's ``seeds``, so a
    result can always be traced back to the market that produced it. A capture
    is ``None`` where it could not be measured -- see :class:`Ranking`.

    ``errors``, ``rejected`` and ``max_leverage`` are parallel to ``seeds``
    too, read off each seed's scorecard: how many times the agent's own code
    raised, how many of its orders were refused, and its peak leverage.
    """

    __slots__ = ("name", "seeds", "captures", "pnls", "wins",
                 "reference_pnls", "benchmark_pnls", "capture_withheld",
                 "trusted", "uses_hidden_state", "errors", "rejected",
                 "max_leverage", "first_error", "raised_in_act")

    def __init__(self, name: str, seeds: list[int],
                 reference_pnls: list[float],
                 benchmark_pnls: list[float | None] | None = None,
                 capture_withheld: str | None = None) -> None:
        self.name = name
        self.seeds = seeds
        #: The reference's P&L per seed, shared with the ranking. Held here so
        #: a record can pool without reaching back for its parent.
        self.reference_pnls = reference_pnls
        #: Buy-and-hold's P&L per seed, shared with the ranking, ``None`` on
        #: a seed where it did not run. The comparison to quote where the
        #: Oracle is not a ceiling.
        self.benchmark_pnls: list[float | None] = (
            benchmark_pnls if benchmark_pnls is not None else [])
        #: Why no capture is reported, on a preset where the Oracle is not a
        #: ceiling (``baselines.ORACLE_NOT_A_CEILING``); None elsewhere.
        self.capture_withheld = capture_withheld
        self.captures: list[float | None] = []
        self.pnls: list[float] = []
        self.wins = 0
        #: Handed the live engine on these runs (``trusted_agents=True``).
        self.trusted = False
        #: Declared ``privileged = True`` and was handed hidden state.
        self.uses_hidden_state = False
        #: Per seed, how many times this agent's ``act()`` or ``explain()``
        #: raised. A step where ``act()`` raised traded nothing, and the
        #: P&L counts it that way.
        self.errors: list[int] = []
        #: Per seed, how many of its orders were refused
        #: (``Scorecard.rejected``).
        self.rejected: list[int] = []
        #: Per seed, its peak leverage (``Scorecard.max_leverage``).
        self.max_leverage: list[float] = []
        #: The first exception line, prefixed with its seed, or None.
        self.first_error: str | None = None
        #: Whether any of those raises came from ``act()``, which costs
        #: orders, rather than only from ``explain()``, which does not.
        self.raised_in_act = False

    @property
    def seeds_with_errors(self) -> int:
        """How many seeds this agent's code raised on at least once."""
        return sum(1 for count in self.errors if count)

    @property
    def marks(self) -> str:
        """The report's label for how this agent saw the market, if not as
        every other agent did, and whether its code raised."""
        return "".join(tag for tag, on in (
            ("  [trusted: live engine]", self.trusted),
            ("  [hidden state]", self.uses_hidden_state),
            ("  [raised: see below]", self.seeds_with_errors > 0)) if on)

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
        seed where the reference earned 1.1% of capital produced capture
        ratios of **-2.54** and +1.00, and seeds like it drag a median of
        ten far enough to reorder the table.

        Pooling weights each market by the opportunity that existed in it. A
        seed where nothing was there to earn contributes nearly nothing to the
        numerator AND nearly nothing to the denominator, so it cannot dominate.
        It also answers the question a reader actually has: across all these
        markets, what fraction of what the reference captured did this agent
        capture?

        Seeds where the reference lost money are excluded from both sums --
        see :attr:`Ranking.unmeasurable` -- because a negative denominator
        flips the sign of everything above it.

        None on a preset where the Oracle is not a ceiling
        (:attr:`capture_withheld`); read :attr:`mean_excess_pnl` there.
        """
        if self.capture_withheld is not None:
            return None
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

    @property
    def excess_pnls(self) -> list[float | None]:
        """P&L less buy-and-hold's, per seed; None where it did not run."""
        return [None if base is None else pnl - base
                for pnl, base in zip(self.pnls, self.benchmark_pnls)]

    @property
    def mean_excess_pnl(self) -> float | None:
        """Mean P&L over buy-and-hold's across the seeds where both ran.

        The headline where the Oracle is not a ceiling. A difference in
        currency needs no pooling: every agent starts each seed with the
        same cash, so a seed where the market moved a lot weighs no more
        here than its difference does. None when buy-and-hold never ran.
        """
        values = [v for v in self.excess_pnls if v is not None]
        return statistics.fmean(values) if values else None

    @property
    def seeds_ahead(self) -> int | None:
        """Seeds where this agent earned more than buy-and-hold did."""
        values = [v for v in self.excess_pnls if v is not None]
        return sum(1 for v in values if v > 0.0) if values else None

    def as_dict(self) -> dict[str, Any]:
        # The same in both forms: what the agent's own code did on each
        # seed, and how it saw the market.
        conduct = {
            "errors": list(self.errors),
            "rejected": list(self.rejected),
            "max_leverage": list(self.max_leverage),
            **({"first_error": self.first_error}
               if self.first_error is not None else {}),
            **({"trusted": True} if self.trusted else {}),
            **({"uses_hidden_state": True} if self.uses_hidden_state
               else {}),
        }
        if self.capture_withheld is not None:
            # No capture keys at all, rather than None: a missing ratio
            # cannot be read as a measured zero or sorted as one.
            return {
                "name": self.name,
                "seeds": list(self.seeds),
                "pnls": list(self.pnls),
                "excess_pnls": self.excess_pnls,
                "mean_excess_pnl": self.mean_excess_pnl,
                "seeds_ahead": self.seeds_ahead,
                "wins": self.wins,
                "median_pnl": self.median_pnl,
                "win_rate": self.win_rate,
                **conduct,
            }
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
            **conduct,
        }

    def __repr__(self) -> str:
        raised = (f", raised on {self.seeds_with_errors}/{len(self.errors)} "
                  "seeds" if self.seeds_with_errors else "")
        if self.capture_withheld is not None:
            excess = self.mean_excess_pnl
            shown = f"{excess:+,.0f}" if excess is not None else "n/a"
            return (f"AgentRecord({self.name!r}, mean_excess_pnl={shown}, "
                    f"wins={self.wins}/{len(self.pnls)}{raised})")
        pooled = self.pooled_capture
        shown = f"{pooled:+.3f}" if pooled is not None else "n/a"
        return (f"AgentRecord({self.name!r}, pooled_capture={shown}, "
                f"wins={self.wins}/{len(self.pnls)}{raised})")


class Ranking:
    """Agent results over a set of seeds, and the comparisons worth making."""

    __slots__ = ("records", "seeds", "unmeasurable", "universe_fingerprint",
                 "oracle", "reference_pnls", "model_fingerprint",
                 "capture_withheld", "tampered", "benchmark",
                 "benchmark_note", "oracle_entered")

    def __init__(self, records: dict[str, AgentRecord], seeds: list[int],
                 unmeasurable: list[int], universe_fingerprint: str,
                 oracle: str, reference_pnls: list[float],
                 model_fingerprint: str = "",
                 capture_withheld: str | None = None,
                 tampered: dict[str, list[int]] | None = None,
                 benchmark: str | None = "buy_and_hold",
                 benchmark_note: str | None = None,
                 oracle_entered: bool = True) -> None:
        self.records = records
        self.seeds = seeds
        #: What the reference earned on each seed, parallel to ``seeds``. This
        #: is the denominator, and it varies several-fold across seeds --
        #: measured $11.5k to $28.1k over ten three-day seeds on the grid in
        #: this module's docstring -- so the headline number pools rather
        #: than averages ratios.
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
        #: Why no capture is reported, where the model is a preset on which
        #: the Oracle is not a ceiling (``baselines.ORACLE_NOT_A_CEILING``,
        #: pt-v20); None elsewhere. When set, no seed is counted in
        #: :attr:`unmeasurable`, every capture is None, and the table sorts
        #: on :attr:`AgentRecord.mean_excess_pnl`, P&L over buy-and-hold's.
        self.capture_withheld = capture_withheld
        #: Agents left out because their code changed the market, each with
        #: the seeds it did so on. Not in :attr:`records`: nothing they
        #: scored is a score.
        self.tampered: dict[str, list[int]] = dict(tampered or {})
        #: The label whose P&L :attr:`AgentRecord.excess_pnls` subtracts, or
        #: None when no entrant could serve as buy-and-hold.
        self.benchmark = benchmark
        #: What the report says about how :attr:`benchmark` was chosen, when
        #: it was not the label ``buy_and_hold``, or why there is none.
        self.benchmark_note = benchmark_note
        #: Whether the :attr:`oracle` label was among the entrants. Without
        #: it there is no capture to withhold or to measure, so the report
        #: leaves out pt-v20's reason for withholding one and, on earlier
        #: presets, says no Oracle ran rather than that it lost money.
        self.oracle_entered = oracle_entered

    def table(self, by: str | None = None) -> list[AgentRecord]:
        """Records sorted best-first, ties broken on name.

        By default on pooled capture rather than the median of per-seed
        ratios (:attr:`AgentRecord.pooled_capture` explains why, with the
        measurement), and on :attr:`AgentRecord.mean_excess_pnl` where the
        Oracle is not a ceiling (:attr:`capture_withheld`).

        Falls back to median P&L when the key was never measurable, so a
        ranking without a working reference still ranks rather than raising.
        """
        if by is None:
            by = ("mean_excess_pnl" if self.capture_withheld is not None
                  else "pooled_capture")
        if by not in ("pooled_capture", "median_capture", "median_pnl",
                      "win_rate", "mean_excess_pnl"):
            raise ValidationError(f"cannot rank by {by!r}")
        if by in ("pooled_capture", "median_capture",
                  "mean_excess_pnl") and not any(
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
        if self.capture_withheld is not None:
            return {
                "seeds": list(self.seeds),
                "capture_withheld": self.capture_withheld,
                "universe_fingerprint": self.universe_fingerprint,
                "model_fingerprint": self.model_fingerprint,
                "oracle": self.oracle,
                "benchmark": self.benchmark,
                "reference_pnls": list(self.reference_pnls),
                "agents": {n: r.as_dict() for n, r in self.records.items()},
                **({"tampered": {n: list(s) for n, s in self.tampered.items()}}
                   if self.tampered else {}),
            }
        return {
            "seeds": list(self.seeds),
            "unmeasurable_seeds": list(self.unmeasurable),
            "universe_fingerprint": self.universe_fingerprint,
            "model_fingerprint": self.model_fingerprint,
            "oracle": self.oracle,
            "reference_pnls": list(self.reference_pnls),
            "agents": {n: r.as_dict() for n, r in self.records.items()},
            **({"tampered": {n: list(s) for n, s in self.tampered.items()}}
               if self.tampered else {}),
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
        if self.capture_withheld is not None:
            for record in self.table():
                excess, ahead = record.mean_excess_pnl, record.seeds_ahead
                if record.name == self.benchmark:
                    lines.append(f"  {record.name:16s}  the benchmark  "
                                 f"median pnl {record.median_pnl:+12,.0f}  "
                                 f"wins {record.wins}/{len(record.pnls)}"
                                 f"{record.marks}")
                    continue
                if excess is None or ahead is None:
                    lines.append(f"  {record.name:16s}  no buy-and-hold to "
                                 f"compare  median pnl "
                                 f"{record.median_pnl:+12,.0f}{record.marks}")
                    continue
                measured = sum(1 for v in record.excess_pnls if v is not None)
                lines.append(
                    f"  {record.name:16s}  vs buy-and-hold "
                    f"{excess:+12,.0f} a seed  ahead {ahead}/{measured}  "
                    f"wins {record.wins}/{len(record.pnls)}{record.marks}"
                )
            if self.benchmark_note:
                lines.append(f"  {self.benchmark_note}")
            lines.extend(self._excluded_lines())
            lines.extend(self._error_lines())
            # The reason is about the Oracle, so it is printed only where
            # one ran. Without one there was never a capture to withhold.
            if self.oracle_entered:
                lines.append(f"  {self.capture_withheld}")
            return "\n".join(lines)
        for record in self.table():
            pooled = record.pooled_capture
            span = record.capture_range
            if pooled is None or span is None:
                lines.append(f"  {record.name:16s}  capture unmeasurable  "
                             f"median pnl {record.median_pnl:+12,.0f}"
                             f"{record.marks}")
                continue
            # The pooled figure is the verdict; the per-seed span is shown
            # beside it because a wide one is the warning that a single seed
            # would have said something else entirely.
            lines.append(
                f"  {record.name:16s}  capture {pooled:+.3f}  "
                f"per-seed [{span[0]:+.3f}, {span[1]:+.3f}]  "
                f"wins {record.wins}/{len(record.pnls)}{record.marks}"
            )
        lines.extend(self._excluded_lines())
        lines.extend(self._error_lines())
        if self.unmeasurable and not self.oracle_entered:
            lines.append(
                f"  capture unmeasurable on every seed: no entrant is "
                f"labelled {self.oracle!r}, so there is nothing to divide "
                "by. Enter tf.baselines.Oracle() under that label, or name "
                "yours with rank(..., oracle='<label>').")
        elif self.unmeasurable:
            shown = ", ".join(str(s) for s in self.unmeasurable[:8])
            more = ", ..." if len(self.unmeasurable) > 8 else ""
            lines.append(
                f"  capture unmeasurable on {len(self.unmeasurable)} seed(s) "
                f"({shown}{more}): the reference lost money there, so a ratio "
                "against it would flip sign."
            )
        return "\n".join(lines)

    def _excluded_lines(self) -> list[str]:
        """One line per agent left out for changing the market, in either
        form of the report: with a capture, or against buy-and-hold where
        the capture is withheld."""
        return [
            f"  EXCLUDED {name}: its code changed the market during "
            f"act() on seed(s) {', '.join(str(s) for s in seeds)}, so "
            "its score is not a score. See Scorecard.errors."
            for name, seeds in sorted(self.tampered.items())]

    def _error_lines(self) -> list[str]:
        """One line per ranked agent whose own code raised, in either form
        of the report. The agent keeps its row; this says what the row
        is a score of."""
        lines = []
        for name, record in sorted(self.records.items()):
            hit = record.seeds_with_errors
            if not hit:
                continue
            total = sum(record.errors)
            count = f"{total} error" + ("" if total == 1 else "s")
            first = (f", the first on {record.first_error}"
                     if record.first_error else "")
            cost = ("Its score counts every step where act() raised as a "
                    "step with no orders" if record.raised_in_act else
                    "Only explain() raised, so its P&L is unaffected")
            lines.append(
                f"  RAISED {name}: {count} on {hit} of "
                f"{len(record.errors)} seeds{first}. {cost}. "
                "See Scorecard.errors.")
        return lines

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


#: Why a built agent cannot be handed to more than one seed. Shared by every
#: refusal below, which differ only in what they caught.
_STATEFUL = (
    "Agents are stateful (Momentum keeps a rolling window, RandomTrader "
    "advances a generator), so reusing instances would carry the first "
    "seed's history into the second and score something that is not the "
    "agent, with no visible symptom.")


def _factory_or_refuse(make_agents: Any) -> Callable[[], dict[str, Any]]:
    from .spec import StrategySpec

    if callable(make_agents):
        return make_agents
    built: list[str] = []
    if isinstance(make_agents, Mapping):
        built = [str(name) for name, entry in make_agents.items()
                 if not isinstance(entry, StrategySpec)]
        if make_agents and not built:
            # Specs carry no state, and evaluate() builds each one fresh on
            # every seed, so the mapping itself is a factory already.
            specs = dict(make_agents)
            return lambda: dict(specs)
    raise ValidationError(
        "rank() needs a factory that BUILDS agents, not built agents. "
        + _STATEFUL + " Pass a callable: "
        "rank(lambda: reference_agents(seed=3), seeds=range(12), ...), or a "
        "mapping whose values are all StrategySpec, which evaluate() builds "
        "fresh on every seed."
        + (f" Built here: {', '.join(repr(n) for n in built)}."
           if built else "")
    )


def _entrants(factory: Callable[[], Any]) -> Mapping[str, Any]:
    """One call to the factory, which must return a mapping."""
    entrants = factory()
    if not isinstance(entrants, Mapping):
        raise ValidationError(
            "rank()'s factory must return a mapping of label to agent, "
            f"such as {{'mine': Mine()}}, and returned a "
            f"{type(entrants).__name__}.")
    return entrants


def _refuse_shared(entrants: Mapping[str, Any],
                   first: Mapping[str, Any] | None, *, first_seed: int,
                   seed: int) -> None:
    """Refuse a factory call that handed back an object it handed out
    before, or one object under two labels.

    ``first`` is a copy of the first call's mapping, or None when this IS
    the first call. The copy is held for the whole ranking so its objects
    stay alive: an identity check against an object that has been freed
    could match a new one that happens to reuse its address. A copy rather
    than the mapping itself, because a factory that returns one dict every
    time could change what that dict holds between calls.
    """
    from .spec import StrategySpec

    seen: dict[int, str] = {}
    earlier = {id(entry) for entry in (first or {}).values()
               if not isinstance(entry, StrategySpec)}
    for name, entry in entrants.items():
        if isinstance(entry, StrategySpec):
            continue
        if id(entry) in earlier:
            raise ValidationError(
                f"rank() needs a factory that BUILDS agents, and the one "
                f"given returned the same {name!r} object for seed {seed} "
                f"that it returned for seed {first_seed}. " + _STATEFUL
                + " Build each agent inside the factory, as in "
                "rank(lambda: {'mine': Mine()}, ...).")
        if id(entry) in seen:
            raise ValidationError(
                f"rank() was handed one object under two labels, "
                f"{seen[id(entry)]!r} and {name!r}. evaluate() runs one "
                "label's market after the other's, so the second would "
                "start with the first's history. " + _STATEFUL)
        seen[id(entry)] = name


def _is_buy_and_hold(entry: Any) -> bool:
    """A :class:`~tradefloor.baselines.BuyAndHold`, a hold spec, or an
    agent built from one."""
    from .baselines import BuyAndHold
    from .spec import StrategySpec

    spec = entry if isinstance(entry, StrategySpec) else getattr(
        entry, "spec", None)
    if isinstance(spec, StrategySpec):
        return spec.signal["kind"] == "hold"
    return isinstance(entry, BuyAndHold)


def _resolve_benchmark(benchmark: str, entrants: Mapping[str, Any],
                       oracle: str) -> tuple[str | None, str | None]:
    """The label to read buy-and-hold's P&L from, and what the report
    should say about how it was chosen."""
    if benchmark in entrants:
        return benchmark, None
    if benchmark != "buy_and_hold":
        raise ValidationError(
            f"benchmark={benchmark!r} is not one of the entrants: "
            + ", ".join(repr(n) for n in sorted(entrants)) + ".")
    held = sorted(name for name, entry in entrants.items()
                  if name != oracle and _is_buy_and_hold(entry))
    if len(held) == 1:
        return held[0], (
            f"The benchmark is {held[0]!r}, the one buy-and-hold entrant. "
            "No entrant is labelled 'buy_and_hold'.")
    if held:
        return None, (
            f"No benchmark: {len(held)} entrants hold the market ("
            + ", ".join(repr(n) for n in held) + ") and none is labelled "
            "'buy_and_hold'. Pick one with rank(..., benchmark='<label>').")
    return None, (
        "No benchmark: no entrant is labelled 'buy_and_hold' or is a "
        "BuyAndHold. Enter tf.baselines.BuyAndHold(), or name another "
        "entrant with rank(..., benchmark='<label>').")


def rank(
    make_agents: Callable[[], dict[str, Any]] | Mapping[str, Any],
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
    trusted_agents: bool = False,
    benchmark: str = "buy_and_hold",
) -> Ranking:
    """Score agents on many seeds and rank them on the aggregate.

    ``make_agents`` is called once per seed and must return a fresh mapping;
    see this module's docstring for why instances are refused. A later call
    that returns an object from the first call is refused too. A mapping
    whose values are all :class:`tradefloor.StrategySpec` can be passed
    as it is, since :func:`tradefloor.evaluate` builds a spec fresh on
    every seed.

    ``benchmark`` is the label of the buy-and-hold entrant that
    :attr:`AgentRecord.excess_pnls` subtracts, and the headline on pt-v20.
    Left at ``"buy_and_hold"`` with no entrant of that name, the one entrant
    that is a :class:`~tradefloor.baselines.BuyAndHold` or a
    ``StrategySpec.hold()`` is used, and the report names it. Any other
    label must be an entrant.

    An agent whose code raised stays in the table, and the report names it
    with the count and the first exception. :attr:`AgentRecord.errors`,
    :attr:`~AgentRecord.rejected` and :attr:`~AgentRecord.max_leverage` hold
    the per-seed counts to gate on.

    Every agent still meets an identical market within each seed, so the
    per-seed comparison stays exact. What changes is that the verdict is taken
    across seeds, where it is a property of the agents rather than of one draw.

    ``model`` selects the coefficient set every evaluation runs, either a
    preset name or a :class:`tradefloor.ModelParams`, one model for the whole
    ranking, agents and seeds alike, because a verdict taken across models
    would rank markets rather than agents. The :class:`Ranking` records
    ``model_fingerprint``, as does every scorecard under it.

    ``trusted_agents`` is passed to every :func:`tradefloor.evaluate`; see
    there and :mod:`tradefloor.sandbox`. Every row is then marked as having
    had the live engine. An agent whose code changed the market on any seed
    is left out of the table and named in :attr:`Ranking.tampered`.

    ```python
    ranking = tf.rank(lambda: reference_agents(seed=3), seeds=range(12),
                      universe=u, days=10)
    print(ranking.report())
    ranking.separation("momentum", "mean_reversion")
    ```
    """
    from .baselines import capture_ratio, capture_withheld
    from .harness import evaluate
    from .universe_util import as_universe, fingerprint_of

    factory = _factory_or_refuse(make_agents)
    seed_list = [check_seed(s) for s in seeds]
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
        trusted_agents=trusted_agents,
    )

    # The first seed's agents are built here, before any worker starts, and
    # held until the end: every later call is checked against them, and
    # they tell the benchmark lookup what each entrant is.
    first = _entrants(factory)
    _refuse_shared(first, None, first_seed=seed_list[0], seed=seed_list[0])
    held = dict(first)
    benchmark_label, benchmark_note = _resolve_benchmark(benchmark, held,
                                                         oracle)

    def one(index: int, seed: int):
        if index == 0:
            entrants = first
        else:
            entrants = _entrants(factory)
            _refuse_shared(entrants, held, first_seed=seed_list[0],
                           seed=seed)
        return seed, evaluate(entrants, seed=seed, **kwargs)

    if workers == 1:
        results = [one(index, seed) for index, seed in enumerate(seed_list)]
    else:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(one, index, seed)
                       for index, seed in enumerate(seed_list)]
            # Collected in SEED order, never completion order, so a median
            # over an even count picks the same element on every run.
            try:
                results = [f.result() for f in futures]
            except BaseException:
                # A refusal on one seed ends the ranking, so the seeds
                # that have not started yet are not run for nothing.
                for future in futures:
                    future.cancel()
                raise

    # Found across every seed first: an agent that tampered on one seed is
    # out of every seed, so its other seeds cannot take a win from anybody.
    tampered: dict[str, list[int]] = {}
    for seed, scores in results:
        for name, card in scores.items():
            if card.tampered:
                tampered.setdefault(name, []).append(seed)
    if oracle in tampered:
        raise ValidationError(
            f"the reference {oracle!r} changed the market during act() on "
            f"seed(s) {tampered[oracle]}, so no capture here means "
            "anything. See Scorecard.errors.")
    if benchmark_label is not None and benchmark_label in tampered:
        raise ValidationError(
            f"the benchmark {benchmark_label!r} changed the market during "
            f"act() on seed(s) {tampered[benchmark_label]}, so no P&L "
            "measured against it means anything. See Scorecard.errors.")

    records: dict[str, AgentRecord] = {}
    unmeasurable: list[int] = []
    reference_pnls: list[float] = []
    benchmark_pnls: list[float | None] = []
    # One model for every seed, so the first seed's answer is every seed's.
    withheld = capture_withheld(results[0][1], oracle=oracle)
    oracle_entered = any(oracle in scores for _, scores in results)
    for seed, scores in results:
        ratios = capture_ratio(scores, oracle=oracle)
        reference = scores[oracle].pnl if oracle in scores else 0.0
        reference_pnls.append(reference)
        benchmark_pnls.append(scores[benchmark_label].pnl
                              if benchmark_label in scores else None)
        # A withheld capture is not an unmeasurable one: the reference may
        # well have made money. The reason is on the ranking instead.
        if not ratios and withheld is None:
            unmeasurable.append(seed)
        contenders = {n: c for n, c in scores.items()
                      if n != oracle and n not in tampered}
        winner = (max(contenders, key=lambda n: (contenders[n].pnl, n))
                  if contenders else None)
        for name, card in contenders.items():
            record = records.setdefault(
                name, AgentRecord(name, seed_list, reference_pnls,
                                  benchmark_pnls, withheld))
            record.captures.append(ratios.get(name))
            record.pnls.append(card.pnl)
            record.trusted = record.trusted or card.trusted
            record.uses_hidden_state = (record.uses_hidden_state
                                        or card.uses_hidden_state)
            in_act, in_explain, first_line = _raised(card)
            record.errors.append(in_act + in_explain)
            record.rejected.append(card.rejected)
            record.max_leverage.append(card.max_leverage)
            if first_line is not None and record.first_error is None:
                record.first_error = f"seed {seed}, {first_line}"
            record.raised_in_act = record.raised_in_act or in_act > 0
            if name == winner:
                record.wins += 1

    # Read off a scorecard rather than recomputed here, so the recorded
    # name is the one the evaluations actually ran under.
    model_fingerprint = next(iter(results[0][1].values())).model_fingerprint
    return Ranking(records, seed_list, unmeasurable, fingerprint_of(roster),
                   oracle, reference_pnls, model_fingerprint, withheld,
                   tampered, benchmark_label, benchmark_note, oracle_entered)
