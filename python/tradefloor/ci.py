"""A baseline and a candidate agent on the same seeds, for a pull request.

    tradefloor ci compare \\
        --baseline base/bot.py:make_agent \\
        --candidate head/bot.py:make_agent \\
        --seeds 1-12 --days 20 --max-drawdown 15

Both agents trade every seed, each on its own copy of that seed's market, so
the two runs of a seed differ only by the agent. The report gives each agent's
median return, worst drawdown, impact, refused orders and errors, the number
of seeds each one won, and the paired sign test :func:`tradefloor.rank` uses.

The command exits 0 when every threshold holds, 1 when one fails and 2 when
the arguments cannot be read. A run with no threshold always exits 0, so the
report can be adopted before a rule is agreed.

Nothing here calls a network. An agent that calls a paid model does so in its
own code, and the cost of that is the caller's to bound: this module counts
decisions and does not see tokens.

The agent files are imported, which runs them. Treat a pull request's agent
as code with the permissions of the job that runs it, and keep secrets out of
jobs that run a fork's code. `.github/workflows/evaluate.yml` runs with a
read-only token for that reason.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import statistics
import sys
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from . import __version__
from . import _core
from .harness import evaluate
from .ranking import _sign_test
from .scenario import Scenario

#: The schema of :meth:`Report.as_dict`. A consumer that stores reports
#: should refuse a number it does not know.
REPORT_VERSION = 1

_NAMES = ("baseline", "candidate")


class Threshold:
    """One rule a report is checked against, and its failure text."""

    __slots__ = ("name", "limit")

    #: name -> (what it reads, the comparison that FAILS it)
    KINDS = {
        "max_drawdown": "candidate's worst drawdown over the seeds, percent",
        "max_drawdown_increase": "candidate's worst drawdown minus the baseline's, percentage points",
        "min_median_return": "candidate's median return, percent",
        "min_median_return_delta": "candidate's median return minus the baseline's, percentage points",
        "max_errors": "errors and refused orders the candidate produced, all seeds",
    }

    def __init__(self, name: str, limit: float) -> None:
        if name not in self.KINDS:
            raise ValueError(
                f"unknown threshold {name!r}; the thresholds are "
                f"{', '.join(sorted(self.KINDS))}")
        self.name = name
        self.limit = float(limit)


class Report:
    """What :func:`compare` measured. Plain data, no engine attached."""

    def __init__(self, *, seeds: list[int], days: int, rows: list[dict[str, Any]],
                 scenario: str | None, universe_fingerprint: str,
                 model_fingerprint: str) -> None:
        self.seeds = seeds
        self.days = days
        #: One row per seed: ``{"seed": n, "baseline": {...}, "candidate": {...}}``.
        self.rows = rows
        self.scenario = scenario
        self.universe_fingerprint = universe_fingerprint
        self.model_fingerprint = model_fingerprint

    # -- aggregates ---------------------------------------------------------

    def _column(self, who: str, field: str) -> list[float]:
        return [row[who][field] for row in self.rows]

    def summary(self, who: str) -> dict[str, Any]:
        """Medians and worst cases for ``"baseline"`` or ``"candidate"``."""
        if who not in _NAMES:
            raise ValueError(f"who is 'baseline' or 'candidate', not {who!r}")
        returns = self._column(who, "return_pct")
        return {
            "median_return_pct": statistics.median(returns),
            "worst_return_pct": min(returns),
            "profitable_seeds": sum(1 for value in returns if value > 0),
            "worst_drawdown_pct": max(self._column(who, "max_drawdown_pct")),
            "median_impact_bps": statistics.median(self._column(who, "impact_bps")),
            "trades": sum(self._column(who, "trades")),
            "rejected": sum(self._column(who, "rejected")),
            "errors": sum(self._column(who, "errors")),
            "tampered_seeds": sum(1 for row in self.rows if row[who]["tampered"]),
        }

    def paired(self) -> dict[str, Any]:
        """Seeds each agent won on net worth, and the two-sided sign test.

        ``p_value`` is None when every seed tied, since a sign test has
        nothing to read then.
        """
        wins_c = sum(1 for row in self.rows
                     if row["candidate"]["pnl"] > row["baseline"]["pnl"])
        wins_b = sum(1 for row in self.rows
                     if row["baseline"]["pnl"] > row["candidate"]["pnl"])
        return {
            "candidate_wins": wins_c,
            "baseline_wins": wins_b,
            "ties": len(self.rows) - wins_c - wins_b,
            "p_value": _sign_test(wins_c, wins_b),
        }

    # -- thresholds ---------------------------------------------------------

    def check(self, thresholds: Iterable[Threshold]) -> list[str]:
        """The thresholds that failed, one sentence each. Empty means pass."""
        base, cand = self.summary("baseline"), self.summary("candidate")
        failures = []
        for rule in thresholds:
            if rule.name == "max_drawdown":
                value, bad = cand["worst_drawdown_pct"], lambda v, lim: v > lim
            elif rule.name == "max_drawdown_increase":
                value = cand["worst_drawdown_pct"] - base["worst_drawdown_pct"]
                bad = lambda v, lim: v > lim
            elif rule.name == "min_median_return":
                value, bad = cand["median_return_pct"], lambda v, lim: v < lim
            elif rule.name == "min_median_return_delta":
                value = cand["median_return_pct"] - base["median_return_pct"]
                bad = lambda v, lim: v < lim
            else:
                value = cand["errors"] + cand["rejected"]
                bad = lambda v, lim: v > lim
            if bad(value, rule.limit):
                failures.append(
                    f"{rule.name}: {value:.2f} against a limit of {rule.limit:.2f} "
                    f"({Threshold.KINDS[rule.name]})")
        # A tampered run is not a measurement, whatever the thresholds say.
        if cand["tampered_seeds"]:
            failures.append(
                f"tampered: the candidate changed market state on "
                f"{cand['tampered_seeds']} of {len(self.rows)} seeds")
        return failures

    # -- output -------------------------------------------------------------

    def as_dict(self, thresholds: Iterable[Threshold] = ()) -> dict[str, Any]:
        thresholds = list(thresholds)
        failures = self.check(thresholds)
        return {
            "report_version": REPORT_VERSION,
            "tradefloor": __version__,
            "seeds": self.seeds,
            "days": self.days,
            "scenario": self.scenario,
            "universe_fingerprint": self.universe_fingerprint,
            "model_fingerprint": self.model_fingerprint,
            "baseline": self.summary("baseline"),
            "candidate": self.summary("candidate"),
            "paired": self.paired(),
            "thresholds": {rule.name: rule.limit for rule in thresholds},
            "failures": failures,
            "passed": not failures,
            "per_seed": self.rows,
        }

    def markdown(self, thresholds: Iterable[Threshold] = ()) -> str:
        """The report as a GitHub-flavoured Markdown summary."""
        thresholds = list(thresholds)
        failures = self.check(thresholds)
        base, cand = self.summary("baseline"), self.summary("candidate")
        paired = self.paired()
        n = len(self.rows)
        verdict = "passed" if not failures else "FAILED"
        if not thresholds and not failures:
            verdict = "reported, no thresholds set"
        lines = [
            f"### tradefloor evaluation: {verdict}",
            "",
            f"{n} seeds, {self.days} trading days"
            + (f", scenario `{self.scenario}`" if self.scenario else "")
            + f", tradefloor {__version__}. Each agent trades its own copy of "
              "each seed's market.",
            "",
            "| | baseline | candidate |",
            "|---|---:|---:|",
        ]
        for label, key, fmt in (
            ("Median return, %", "median_return_pct", "{:+.2f}"),
            ("Worst return, %", "worst_return_pct", "{:+.2f}"),
            ("Profitable seeds", "profitable_seeds", "{} of " + str(n)),
            ("Worst drawdown, %", "worst_drawdown_pct", "{:.2f}"),
            ("Median impact, bp", "median_impact_bps", "{:+.2f}"),
            ("Trades", "trades", "{}"),
            ("Refused orders", "rejected", "{}"),
            ("Errors", "errors", "{}"),
        ):
            lines.append(f"| {label} | {fmt.format(base[key])} | {fmt.format(cand[key])} |")
        p = paired["p_value"]
        lines += [
            "",
            f"Seeds won on final net worth: candidate {paired['candidate_wins']}, "
            f"baseline {paired['baseline_wins']}, ties {paired['ties']}. "
            + ("Every seed tied, so the sign test has nothing to read."
               if p is None else f"Two-sided sign test p = {p:.4f}."),
        ]
        # Six wins from six is the least a sign test can call at 0.05, so a
        # small run says so itself instead of leaving the reader to know it.
        if n < 6:
            lines.append(
                f"With {n} seeds no split can reach p < 0.05; the test needs six or more.")
        if failures:
            lines += ["", "Failed thresholds:", ""]
            lines += [f"- {failure}" for failure in failures]
        lines += [
            "",
            "These are simulated markets. A result here ranks two versions of "
            "an agent against each other and predicts no real return.",
            "",
        ]
        return "\n".join(lines)


def compare(
    baseline: Callable[[], Any],
    candidate: Callable[[], Any],
    *,
    seeds: Iterable[int],
    universe: Sequence[Any],
    days: int = 20,
    scenario: Any = None,
    cash: float = 1_000_000.0,
    model: Any = None,
) -> Report:
    """Run both agents on every seed and return the paired :class:`Report`.

    ``baseline`` and ``candidate`` are zero-argument callables that return a
    fresh agent. They are called once per seed, because an agent that carried
    state from one seed into the next would make the seeds dependent and the
    sign test wrong.
    """
    seeds = [int(seed) for seed in seeds]
    if not seeds:
        raise ValueError("seeds is empty; give at least one seed")
    if len(set(seeds)) != len(seeds):
        raise ValueError("seeds repeats a value; a repeated seed is the same "
                         "market twice and would be counted as two")
    rows, universe_fp, model_fp = [], "", ""
    for seed in seeds:
        cards = evaluate(
            {"baseline": baseline(), "candidate": candidate()},
            seed=seed, universe=universe, days=days, scenario=scenario,
            cash=cash, model=model)
        row: dict[str, Any] = {"seed": seed}
        for who in _NAMES:
            card = cards[who]
            row[who] = {
                "pnl": card.pnl,
                "return_pct": card.return_pct,
                "max_drawdown_pct": card.max_drawdown_pct,
                "impact_bps": card.impact_bps,
                "trades": card.trades,
                "rejected": card.rejected,
                "errors": len(card.errors),
                "first_error": card.errors[0] if card.errors else None,
                "tampered": card.tampered,
                "ruined": card.ruined,
            }
            universe_fp, model_fp = card.universe_fingerprint, card.model_fingerprint
        rows.append(row)
    name = getattr(scenario, "name", None) if scenario is not None else None
    return Report(seeds=seeds, days=days, rows=rows, scenario=name,
                  universe_fingerprint=universe_fp, model_fingerprint=model_fp)


# -- command line -----------------------------------------------------------


def parse_seeds(text: str) -> list[int]:
    """``"1-12"``, ``"3,5,8"`` or a mix: ``"1-4,9"``."""
    seeds: list[int] = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            low, high = part.split("-", 1)
            first, last = int(low), int(high)
            if last < first:
                raise ValueError(f"seed range {part!r} runs backwards")
            seeds.extend(range(first, last + 1))
        else:
            seeds.append(int(part))
    if not seeds:
        raise ValueError("no seeds given")
    return seeds


def load_factory(target: str, label: str) -> Callable[[], Any]:
    """``path/to/file.py:name`` -> the callable ``name`` in that file.

    The two files usually have the same name in two checkouts, so each is
    imported under its own module name and neither shadows the other.
    """
    path_text, sep, attribute = target.rpartition(":")
    if not sep or not path_text or not attribute:
        raise ValueError(f"{label} is {target!r}; expected FILE.py:NAME")
    path = Path(path_text)
    if not path.is_file():
        raise ValueError(f"{label}: no file at {path}")
    spec = importlib.util.spec_from_file_location(f"_tradefloor_ci_{label}", path)
    if spec is None or spec.loader is None:
        raise ValueError(f"{label}: {path} cannot be imported as Python")
    module = importlib.util.module_from_spec(spec)
    # A file that imports its neighbours expects its own directory on the path.
    sys.path.insert(0, str(path.parent.resolve()))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    try:
        factory = getattr(module, attribute)
    except AttributeError:
        raise ValueError(f"{label}: {path} has no {attribute!r}") from None
    if not callable(factory):
        raise ValueError(f"{label}: {attribute!r} in {path} is not callable")
    return factory


def add_parser(top: Any) -> None:
    """Attach ``ci compare`` to the ``tradefloor`` command tree."""
    ci = top.add_parser("ci", help="compare two versions of an agent on the same seeds")
    sub = ci.add_subparsers(dest="command", required=True)
    cmd = sub.add_parser(
        "compare", help="run a baseline and a candidate, report, apply thresholds")
    cmd.add_argument("--baseline", required=True, metavar="FILE.py:NAME",
                     help="a zero-argument callable returning the current agent")
    cmd.add_argument("--candidate", required=True, metavar="FILE.py:NAME",
                     help="the same for the proposed agent")
    cmd.add_argument("--seeds", default="1-12",
                     help="market seeds, as 1-12 or 3,5,8 (default 1-12)")
    cmd.add_argument("--days", type=int, default=20,
                     help="trading days per seed (default 20)")
    cmd.add_argument("--companies", type=int, default=20,
                     help="generated companies in the market (default 20)")
    cmd.add_argument("--universe-seed", type=int, default=7,
                     help="seed of the generated roster (default 7)")
    cmd.add_argument("--scenario", default=None,
                     help="a name from `tradefloor scenario list`, or a path")
    cmd.add_argument("--cash", type=float, default=1_000_000.0)
    for name, text in Threshold.KINDS.items():
        cmd.add_argument("--" + name.replace("_", "-"), type=float, default=None,
                         metavar="LIMIT", help=f"fail on the {text}")
    cmd.add_argument("--json", default=None, metavar="PATH",
                     help="write the full report as JSON")
    cmd.add_argument("--markdown", default=None, metavar="PATH",
                     help="append the Markdown summary to PATH, for example "
                          "$GITHUB_STEP_SUMMARY")


def run(args: argparse.Namespace) -> int:
    try:
        seeds = parse_seeds(args.seeds)
        baseline = load_factory(args.baseline, "baseline")
        candidate = load_factory(args.candidate, "candidate")
        scenario = None
        if args.scenario:
            scenario = (Scenario.load(args.scenario)
                        if args.scenario in Scenario.available()
                        else Scenario.from_yaml(args.scenario))
        thresholds = [Threshold(name, getattr(args, name))
                      for name in Threshold.KINDS if getattr(args, name) is not None]
        if args.days < 1 or args.companies < 1:
            raise ValueError("--days and --companies are at least 1")
    except (ValueError, OSError) as exc:
        print(f"tradefloor ci: {exc}", file=sys.stderr)
        return 2
    universe = _core.random_instruments(args.companies, seed=args.universe_seed)
    report = compare(baseline, candidate, seeds=seeds, universe=universe,
                     days=args.days, scenario=scenario, cash=args.cash)
    text = report.markdown(thresholds)
    print(text)
    if args.markdown:
        with open(args.markdown, "a", encoding="utf-8") as handle:
            handle.write(text + "\n")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(report.as_dict(thresholds), handle, indent=2, sort_keys=True)
            handle.write("\n")
    return 1 if report.check(thresholds) else 0
