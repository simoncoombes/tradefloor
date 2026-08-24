"""An MCP server, so an LLM agent can drive the simulator.

`pretium` is a Python library, which means its audience is a person writing
code. An MCP server adds a second audience that cannot write code against
it: a model that calls tools and reads back JSON. Everything here follows
from that one difference.

## The strategy surface is data, never a callable

`evaluate` accepts any object with an `act` method. A tool call cannot
carry one, and the alternative -- accepting a string of Python and running
it -- would make this server a remote code execution endpoint with a market
simulator attached. :class:`pretium.StrategySpec` already closed this gap
for citability, and its module docstring anticipated this exact use:

    It is also what an MCP server needs -- a tool cannot accept a callable
    -- and what stops callers inventing their own serialisation on the way
    to one.

So the grammar the spec expresses is the grammar this server exposes, and
what the spec cannot say, this server cannot run. Path dependence,
conditional logic and custom signals need a Python agent and the library.
That limit is stated in `describe_simulator` rather than discovered.

## Every result carries its own caveats

This is the part that is not ordinary MCP plumbing, and it is load-bearing.

A person calling `evaluate` has the docstring, the README and the realism
envelope page in reach. A model calling `evaluate_strategies` has the tool
result and nothing else, and it will summarise that result to a human who
has even less. The failure mode is not hypothetical -- it is the single
most repeated failure in this project's history: a correct number under a
sentence that inverted it. A design document described crisis severity as
arriving "through correlation" when that was backwards for one of two
parameters. A survey classifier printed `[MOVES]` for a parameter measured
to move things the wrong way. Every one had sound arithmetic and a wrong
connecting sentence, because **a number invites scepticism and a sentence
does not**.

A model handed `{"return_pct": 88.7}` will report that a strategy made
88.7%. So every tool here returns a `caveats` list beside its numbers, and
the caveats are **computed from the envelope and the measured facts at call
time** -- never retyped prose. That rule has already earned itself: while
this module was being written, `PRODUCT.md` and `README.md` were both found
still asserting a return autocorrelation of +0.219 and +0.249 from an
earlier preset, where the shipped `pt-v3` measures 0.0485 across the
README's own published method. Hardcoding a caveat is how a caveat becomes
false.

## What is deliberately not exposed

**Atlas.** A survey is thousands of simulations and runs for hours; a tool
call that cannot return inside a conversation is not a tool. Atlas stays a
library API, driven by `tools/calibration/atlas_survey.py`.

**Arbitrary model parameters.** `ModelParams` has 54 settable coefficients
and a preset fingerprint that makes a result citable. Letting a model
improvise coefficients produces markets nobody calibrated, reported with
the authority of a named preset. Presets are selectable by name; the
coefficients behind them are not.

**Anything that writes.** Every tool is read-only and pure: same arguments,
same bytes, on every platform.
"""

from __future__ import annotations

import json
import struct
from typing import Any, Literal

import pretium as pt
from pretium import baselines, envelope
from pretium.facts import REAL_MARKETS, band_distance

try:
    from mcp.server import MCPServer
except ImportError as exc:  # pragma: no cover - exercised by the install path
    raise ImportError(
        "The MCP server needs the `mcp` package, which pretium does not "
        "depend on by default:\n\n    pip install 'pretium[mcp]'\n"
    ) from exc


# -- limits ----------------------------------------------------------------
#
# A model will cheerfully ask for 500 days across 40 seeds because nothing in
# the request looks expensive. These caps exist so a tool call cannot become
# a compute job: the whole surface is meant to answer inside a conversation.
# Every one of them is a wall-clock decision, not a modelling one -- the
# library itself imposes none of them, and a caller who wants more should use
# the library.

MAX_DAYS = 60
MAX_UNIVERSE = 120
MAX_STRATEGIES = 8
MAX_SEEDS = 12

#: Named scenarios, with the constructor each one calls. Free-form scenario
#: building is a fluent API over 40-odd engine fields; exposing it whole would
#: let a model pin macro state to values no calibration ever saw and report
#: the result as a stress test. These three are the shipped presets.
SCENARIOS = ("rate_shock", "vix_shock", "vol_shock")


def _fail(msg: str) -> dict[str, Any]:
    """Refusals are results, not exceptions.

    An MCP exception reaches the model as a transport-level error with no
    room for guidance. A model that gets `{"error": "...", "how_to_fix":
    "..."}` back can correct itself on the next call, which is the whole
    loop this server is trying to support.
    """
    return {"ok": False, "error": msg}


def _provenance(**extra: Any) -> dict[str, Any]:
    """What is needed to re-run this exact result somewhere else.

    Present on every successful result, because a number from a simulator
    without its seed and fingerprints is not a measurement -- it is an
    anecdote, and a model summarising it cannot tell the difference.
    """
    preset = pt.model_preset()
    return {
        "pretium_version": pt.__version__,
        "model_preset": preset["name"],
        "model_fingerprint": preset.get("fingerprint", ""),
        "spec_version": pt.SPEC_VERSION,
        **extra,
    }


# -- the caveat engine -----------------------------------------------------


def _statistic_line(name: str) -> str:
    """One measured statistic against its real-market band, as a sentence.

    Read from `envelope.CERTIFIED` and `facts.REAL_MARKETS` on every call.
    The numbers move when the preset moves; a sentence typed here would not.
    """
    measured = envelope.CERTIFIED[name]
    lo, hi = REAL_MARKETS[name]
    verdict = "in band" if band_distance(measured, lo, hi) == 0 else "OUT OF BAND"
    return (f"{name} measures {measured:.4g} against a real-market band of "
            f"{lo:g} to {hi:g} ({verdict}) at the certified "
            f"{envelope.CERTIFIED_HORIZON_DAYS}-day horizon")


def _caveats(*, days: int, n_seeds: int, signals: set[str],
             max_leverage: float | None, universe_size: int,
             scenario_magnitude: bool = False) -> list[str]:
    """The caveats this particular call earns.

    Computed, not selected from a list of stock warnings. Each branch below
    fires on a property of the request, so a result never carries a caveat
    that does not apply to it -- which is what keeps the ones it does carry
    worth reading.
    """
    out: list[str] = [
        "The price process is a known model, not a forecast. A strategy that "
        "does well here has done well against this model; that does not "
        "transfer to real returns.",
    ]

    # The envelope decides the horizon question, so the answer cannot drift
    # from what the envelope page says.
    v = envelope.check(horizon_days=days, scenario_magnitude=scenario_magnitude)
    if not v.inside:
        out.append("Outside the certified realism envelope: "
                   + "; ".join(v.reasons))

    # The envelope calls any horizon at or under the certified one "inside",
    # and it is right to -- its gaps are about running LONGER. But that
    # leaves the opposite risk unstated, and this server can only ever run
    # short: MAX_DAYS is well under the certified horizon because a
    # 252-day evaluation takes about 95 seconds and a tool call has to
    # answer inside a conversation.
    #
    # So every result here is a slice of a market whose realism was measured
    # over a year. That is not a violation, and it is not nothing either:
    # the statistics that make this market credible are annual ones.
    horizon = envelope.CERTIFIED_HORIZON_DAYS
    if days < horizon // 4:
        out.append(
            f"SHORT WINDOW: {days} trading days against a realism "
            f"certification measured over {horizon}. The statistics that "
            f"make this market credible -- volatility, autocorrelation, "
            f"cross-sectional co-movement -- are annual measurements, and "
            f"they are not established over a window this short. Treat a "
            f"result here as a sample of the market, not a description of "
            f"it."
        )

    if n_seeds == 1:
        out.append(
            "ONE SEED. A single seed measures that seed as much as the "
            "strategy -- the same market run under a different draw can "
            "reverse a ranking. Use `rank_strategies` across seeds before "
            "believing an ordering."
        )
    elif n_seeds < 6:
        out.append(f"{n_seeds} seeds is a small sample; the paired sign test "
                   f"needs more before a win rate means much.")

    if signals & {"momentum", "mean_reversion"}:
        out.append(
            "This strategy trades a return-continuation or reversal signal, "
            "so its edge depends on the simulator's return autocorrelation: "
            + _statistic_line("return_acf1") + "."
        )

    if "oracle" in signals:
        out.append(
            "The `oracle` signal is PRIVILEGED: it reads the simulator's own "
            "fair value, which no real trader can observe. It is a ceiling "
            "for measuring capture, never a strategy."
        )

    if max_leverage is None:
        out.append(
            "Leverage is unbounded. The book makes large trades expensive, "
            "but with no funding limit arbitrarily large size is always "
            "available and 'trade everything' wins on size rather than skill."
        )

    if universe_size < 30:
        out.append(f"A {universe_size}-name roster is small enough that one "
                   f"instrument's draw can dominate the result.")

    out.append(
        "The roster is sector-balanced, which no real index is, and the "
        "market is single-venue with zero latency and no strategic "
        "counterparties. See `describe_simulator` for the full list."
    )
    return out


# -- shared argument handling ----------------------------------------------


def _build_universe(size: int, seed: int) -> Any:
    if not 2 <= size <= MAX_UNIVERSE:
        raise ValueError(f"universe_size must be 2..{MAX_UNIVERSE}, got {size}")
    return pt.Universe.random(size, seed=seed)


#: Names of strategies whose `spec_version` this server supplied. Reported in
#: the result rather than kept quiet -- see `_normalise`.
_ASSUMED = "spec_version_assumed"


def _normalise(doc: Any) -> tuple[str, bool]:
    """Wire form to spec JSON, defaulting a MISSING version but never a wrong one.

    `StrategySpec.from_json` requires `spec_version`, and it is right to:
    a spec document without one is unversioned, and reading a NEWER version
    on a best-effort basis would produce a strategy nobody specified while
    claiming, via its fingerprint, to be what was written.

    That reasoning is about documents being READ BACK. This server is an
    authoring surface -- a model composes a spec here and runs it in the same
    breath -- and a model will omit the field essentially every time, turning
    a mandatory version into a guaranteed wasted round trip.

    So: a document with NO version is stamped with the current one, exactly
    as `StrategySpec.momentum()` does when it constructs at today's version.
    A document that names a version keeps it, so the newer-than-understood
    refusal still fires. The stamping is reported in the result and the
    canonical form shows what was assumed, because a version silently
    supplied is a claim about meaning made on the caller's behalf.
    """
    if isinstance(doc, str):
        doc = json.loads(doc)
    if not isinstance(doc, dict):
        raise ValueError(f"a spec must be an object, got {type(doc).__name__}")
    if "spec_version" not in doc:
        return json.dumps({"spec_version": pt.SPEC_VERSION, **doc}), True
    return json.dumps(doc), False


def _specs_from(
    strategies: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Turn the wire form into specs, naming the offender on failure.

    A model authoring a spec gets the grammar wrong in specific ways, and
    `ValidationError` already says which. Wrapping it with the strategy's
    name is the difference between a fixable error and a retry loop.
    """
    if not strategies:
        raise ValueError("no strategies given")
    if len(strategies) > MAX_STRATEGIES:
        raise ValueError(
            f"at most {MAX_STRATEGIES} strategies per call, got "
            f"{len(strategies)}")
    built: dict[str, Any] = {}
    assumed: list[str] = []
    for name, doc in strategies.items():
        try:
            text, was_assumed = _normalise(doc)
            built[name] = pt.StrategySpec.from_json(text)
        except Exception as exc:
            raise ValueError(f"strategy {name!r}: {exc}") from exc
        if was_assumed:
            assumed.append(name)
    return built, assumed


def _signals_in(specs: dict[str, Any]) -> set[str]:
    """Every signal kind any spec uses, blend components included.

    A blend that contains `oracle` is as privileged as a bare oracle, and a
    caveat that missed that would be exactly the inverted sentence this
    module exists to prevent.
    """
    found: set[str] = set()

    def walk(sig: Any) -> None:
        if not isinstance(sig, dict):
            return
        kind = sig.get("kind")
        if kind:
            found.add(kind)
        for comp in sig.get("components", ()) or ():
            walk(comp)

    for spec in specs.values():
        walk(spec.as_dict().get("signal"))
    return found


def _scorecard_row(s: Any) -> dict[str, Any]:
    return {
        "name": s.name,
        "return_pct": round(s.return_pct, 4),
        "pnl": round(s.pnl, 2),
        "final_net_worth": round(s.final_net_worth, 2),
        "trades": s.trades,
        "turnover": round(s.turnover, 2),
        "impact_bps": round(s.impact_bps, 4),
        "max_leverage": round(s.max_leverage, 4),
        "rejected": s.rejected,
        "errors": list(s.errors),
        "strategy_fingerprint": s.strategy_fingerprint,
    }


# -- the server ------------------------------------------------------------

INSTRUCTIONS = """\
`pretium` is a deterministic equity market simulator with a real limit order
book. Orders match against real depth, so trading moves the price.

Start with `describe_simulator`. It reports what this market is certified to
reproduce and what it is not, and everything else is easier to read after it.

Strategies are DATA, not code: build one with `validate_strategy`, then run it
with `evaluate_strategies`. There is no way to submit Python here.

Every result carries a `caveats` list. It is computed for that specific call
and is part of the result, not decoration -- a summary of a pretium result
that drops the caveats is a misreport.

A single seed measures the seed as much as the strategy. `rank_strategies` is
the honest version of `evaluate_strategies`.
"""

server = MCPServer(
    name="pretium",
    title="pretium market simulator",
    version=pt.__version__,
    instructions=INSTRUCTIONS,
)


@server.tool(
    description="What this simulator is, what it is certified to reproduce, "
                "and what it cannot do. Call this first."
)
def describe_simulator() -> dict[str, Any]:
    """Orientation, computed from the shipped envelope rather than prose."""
    cert = envelope.certified()
    in_band, out_band = [], []
    for name in REAL_MARKETS:
        lo, hi = REAL_MARKETS[name]
        target = in_band if band_distance(
            envelope.CERTIFIED[name], lo, hi) == 0 else out_band
        target.append(name)

    return {
        "ok": True,
        "what_it_is": (
            "A deterministic equity market simulator: prices, a limit order "
            "book, fills and macro state run forward from a seed, a roster "
            "and a macro state. Orders match against real depth, so trading "
            "moves the price. Bit-identical across platforms."
        ),
        "certified": {
            "preset": cert["preset"],
            "horizon_days": cert["certified_horizon_days"],
            "statistics_in_band": in_band,
            "statistics_out_of_band": out_band,
            "detail": [_statistic_line(n) for n in REAL_MARKETS],
        },
        "known_gaps": [
            {"id": g["id"], "forbids": g["forbids"]} for g in cert["gaps"]
        ],
        "structural_limitations": [
            "Single venue: no fragmentation, no NBBO, no routing.",
            "Zero latency; orders arrive instantly.",
            "No strategic counterparties -- you trade against a market maker "
            "and aggregate flow, not agents that adapt to you.",
            "The roster is sector-balanced, which no real index is.",
            "Good results do not predict real returns. The price process "
            "comes from a known model.",
        ],
        "strategy_grammar": {
            "signal_kinds": list(pt.spec.SIGNAL_KINDS),
            "cadences": list(pt.spec.CADENCES),
            "cannot_express": [
                "path dependence (stop losses, drawdown limits, anything "
                "reading its own P&L history)",
                "conditional logic ('momentum in calm markets, reversion in "
                "stress')",
                "custom signals",
            ],
            "escape_hatch": (
                "Write a Python agent against the library. The cost is that "
                "the result is not citable as a spec."
            ),
        },
        "baselines": list(baselines.reference_agents(seed=0)),
        "scenarios": list(SCENARIOS),
        "factors": list(pt.Engine.FACTORS),
        "limits": {
            "max_days": MAX_DAYS, "max_universe": MAX_UNIVERSE,
            "max_strategies": MAX_STRATEGIES, "max_seeds": MAX_SEEDS,
            "note": "Limits of this MCP server, so a call answers inside a "
                    "conversation. The library imposes none of them.",
            "measured_cost": "40 names with the baselines: ~0.5s at 5 days, "
                             "~20s at 60, ~95s at 252. max_days is set below "
                             "the certified 252-day horizon for this reason, "
                             "so every result here is a SHORT WINDOW on a "
                             "market whose realism is an annual measurement.",
        },
        "not_exposed_here": {
            "atlas": "Response-surface surveys run for hours. Library only.",
            "model_coefficients": "Presets are selectable by name; the 54 "
                                  "coefficients behind them are not, because "
                                  "improvised coefficients produce a market "
                                  "nobody calibrated.",
        },
        "provenance": _provenance(),
    }


@server.tool(
    description="Ask whether a question falls inside the realism envelope "
                "BEFORE running it. Names the measurement behind any refusal."
)
def check_envelope(
    horizon_days: int,
    statistics: list[str] | None = None,
    sector_concentrated: bool = False,
    scenario_magnitude: bool = False,
) -> dict[str, Any]:
    """The honesty gate. Cheap, and worth calling before an expensive run."""
    try:
        v = envelope.check(
            horizon_days=horizon_days,
            statistics=statistics or (),
            sector_concentrated=sector_concentrated,
            scenario_magnitude=scenario_magnitude,
        )
    except pt.ValidationError as exc:
        return _fail(
            f"{exc}. Known statistics: {sorted(REAL_MARKETS)}. An unknown "
            f"name is refused rather than ignored, because silently dropping "
            f"one would grant a certification nobody measured."
        )
    return {
        "ok": True,
        "inside": v.inside,
        "reasons": list(v.reasons),
        "gaps": [{"id": g.id, "forbids": g.forbids, "detail": g.detail}
                 for g in v.gaps],
        "provenance": _provenance(),
    }


@server.tool(
    description="Parse and fingerprint a strategy spec WITHOUT running it. "
                "Use this to iterate on a spec cheaply; grammar errors come "
                "back naming what was wrong."
)
def validate_strategy(spec: dict[str, Any]) -> dict[str, Any]:
    """The authoring loop.

    Separate from `evaluate_strategies` because a model gets the grammar
    wrong several times before it gets it right, and each of those attempts
    should cost a parse rather than a simulation.
    """
    try:
        specs, assumed = _specs_from({"spec": spec})
        built = specs["spec"]
    except ValueError as exc:
        return _fail(
            f"{exc}\n\nSignal kinds: {list(pt.spec.SIGNAL_KINDS)}. "
            f"Cadences: {list(pt.spec.CADENCES)}. "
            f"A minimal spec: "
            f'{{"signal": {{"kind": "momentum", "lookback_days": 1.0}}, '
            f'"portfolio": {{"top_k": 5, "gross": 1.0}}}}'
        )
    return {
        "ok": True,
        "canonical": built.as_dict(),
        "fingerprint": built.fingerprint,
        "signals_used": sorted(_signals_in({"spec": built})),
        _ASSUMED: bool(assumed),
        "note": ("The fingerprint identifies this strategy in a result. Two "
                 "specs that build the same agent share one -- blend weights "
                 "are normalised, so 1.2/0.8 and 0.6/0.4 are the same "
                 "strategy."),
        "provenance": _provenance(),
    }


@server.tool(
    description="Run strategies against one identical market and score them. "
                "Fast, and the right first look -- but it is ONE seed; use "
                "rank_strategies before believing an ordering."
)
def evaluate_strategies(
    strategies: dict[str, Any],
    seed: int = 7,
    universe_size: int = 40,
    universe_seed: int = 111,
    days: int = 5,
    steps_per_day: int = 6,
    cash: float = 1_000_000.0,
    max_leverage: float | None = 2.0,
    include_baselines: bool = True,
) -> dict[str, Any]:
    """The headline tool.

    `include_baselines` defaults to True on purpose. A return of +4% means
    nothing without knowing what buy-and-hold did on the same market, and a
    model handed a bare number will report the bare number.
    """
    if not 1 <= days <= MAX_DAYS:
        return _fail(f"days must be 1..{MAX_DAYS}, got {days}")
    try:
        specs, assumed = _specs_from(strategies)
        universe = _build_universe(universe_size, universe_seed)
    except ValueError as exc:
        return _fail(str(exc))

    entrants: dict[str, Any] = dict(specs)
    if include_baselines:
        for name, agent in baselines.reference_agents(seed=seed).items():
            entrants.setdefault(name, agent)

    try:
        scores = pt.evaluate(
            entrants, seed=seed, universe=universe, days=days,
            steps_per_day=steps_per_day, cash=cash, max_leverage=max_leverage,
        )
    except pt.ValidationError as exc:
        return _fail(str(exc))

    rows = [_scorecard_row(s) for s in scores.values()]
    rows.sort(key=lambda r: r["return_pct"], reverse=True)

    result: dict[str, Any] = {
        "ok": True,
        "scores": rows,
        "ranking_note": "Sorted by return on this ONE market draw.",
    }
    if include_baselines and "oracle" in scores:
        result["capture_ratio"] = {
            k: round(v, 4) for k, v in pt.capture_ratio(scores).items()
        }
        result["capture_note"] = (
            "Fraction of the oracle's P&L captured. The oracle reads the "
            "simulator's own fair value, so this is a ceiling: 1.0 is "
            "perfect foresight, not a target."
        )

    result["caveats"] = _caveats(
        days=days, n_seeds=1, signals=_signals_in(specs),
        max_leverage=max_leverage, universe_size=universe_size,
    )
    result["provenance"] = _provenance(
        seed=seed,
        universe={"size": universe_size, "seed": universe_seed},
        universe_fingerprint=next(iter(scores.values())).universe_fingerprint
        if scores else "",
        days=days, steps_per_day=steps_per_day,
    )
    return result


@server.tool(
    description="Score strategies across MANY seeds and rank them with a "
                "paired sign test. Slower than evaluate_strategies and the "
                "only version whose ordering is worth believing."
)
def rank_strategies(
    strategies: dict[str, Any],
    seeds: list[int] | None = None,
    universe_size: int = 40,
    universe_seed: int = 111,
    days: int = 5,
    steps_per_day: int = 6,
    max_leverage: float | None = 2.0,
) -> dict[str, Any]:
    """The honest version.

    `rank` takes a factory rather than instances because agents are stateful
    and a reused instance carries one market's history into the next with no
    visible symptom. Specs are immune -- they are rebuilt per seed -- which
    is why this server only ever passes specs.
    """
    seeds = seeds or [1, 2, 3, 4, 5, 6]
    if not 2 <= len(seeds) <= MAX_SEEDS:
        return _fail(f"seeds must be 2..{MAX_SEEDS} values, got {len(seeds)}")
    if not 1 <= days <= MAX_DAYS:
        return _fail(f"days must be 1..{MAX_DAYS}, got {days}")
    try:
        specs, assumed = _specs_from(strategies)
        universe = _build_universe(universe_size, universe_seed)
    except ValueError as exc:
        return _fail(str(exc))

    def make_agents() -> dict[str, Any]:
        entrants: dict[str, Any] = dict(specs)
        for name, agent in baselines.reference_agents(seed=0).items():
            entrants.setdefault(name, agent)
        return entrants

    try:
        ranking = pt.rank(
            make_agents, seeds=seeds, universe=universe, days=days,
            steps_per_day=steps_per_day, max_leverage=max_leverage,
        )
    except pt.ValidationError as exc:
        return _fail(str(exc))

    # `table()` is the RANKED order; `records` is a dict, and iterating it
    # would present insertion order as if it were a ranking.
    records = [
        {
            "name": r.name,
            # The library says to quote this one: total P&L over the
            # reference's total, rather than a median of per-seed ratios
            # whose denominators differ.
            "pooled_capture": round(r.pooled_capture, 4)
            if r.pooled_capture is not None else None,
            "median_capture": round(r.median_capture, 4)
            if r.median_capture is not None else None,
            "median_pnl": round(r.median_pnl, 2),
            "seeds_first": r.wins,
            "seeds_measured": len(r.measured),
        }
        for r in ranking.table()
    ]

    # The question anyone actually has is "is A really better than B", and a
    # league table cannot answer it -- so run the paired sign test between
    # the submitted strategies and every entrant that beat or trailed them.
    order = [r["name"] for r in records]
    tests = []
    for name in specs:
        for other in order:
            if other == name:
                continue
            try:
                tests.append(ranking.separation(name, other))
            except Exception:  # a pair the test cannot form is not an error
                continue

    return {
        "ok": True,
        "records": records,
        "seeds": list(ranking.seeds),
        "paired_sign_tests": tests,
        "unmeasurable": list(ranking.unmeasurable),
        "report": ranking.report(),
        "reading_note": (
            "QUOTE `pooled_capture`: total P&L over the reference's total. "
            "`seeds_first` counts seeds where this entrant ranked FIRST among "
            "ALL entrants, baselines included -- so it is not a head-to-head "
            "record, and two entrants that behave identically will split it "
            "arbitrarily on a tie. For 'is A better than B', read "
            "`paired_sign_tests`: both traded the SAME market on each seed, "
            "so the pairing removes the market from the question. `decisive` "
            "is true only when one won on every paired seed. `unmeasurable` "
            "names entrants the test could not separate -- a real answer, "
            "not a gap."
        ),
        "caveats": _caveats(
            days=days, n_seeds=len(seeds), signals=_signals_in(specs),
            max_leverage=max_leverage, universe_size=universe_size,
        ),
        "provenance": _provenance(
            seeds=list(seeds), days=days, steps_per_day=steps_per_day,
            universe={"size": universe_size, "seed": universe_seed},
            universe_fingerprint=ranking.universe_fingerprint,
        ),
    }


@server.tool(
    description="Run strategies through a macro stress scenario (rate_shock, "
                "vix_shock or vol_shock) and compare against the same market "
                "with no shock."
)
def run_stress_scenario(
    scenario: Literal["rate_shock", "vix_shock", "vol_shock"],
    strategies: dict[str, Any] | None = None,
    seed: int = 7,
    universe_size: int = 40,
    universe_seed: int = 111,
    days: int = 20,
    peak_day: int | None = None,
) -> dict[str, Any]:
    """Stress testing, always paired against the unshocked control.

    A scenario result on its own is unreadable: a -3% return under a rate
    shock could be the shock or could be the market. Running the identical
    seed with and without the scenario is the counterfactual the simulator
    exists to provide, so this tool always returns both.
    """
    if scenario not in SCENARIOS:
        return _fail(f"unknown scenario {scenario!r}; known: {list(SCENARIOS)}")
    if not 1 <= days <= MAX_DAYS:
        return _fail(f"days must be 1..{MAX_DAYS}, got {days}")
    try:
        specs, assumed = (_specs_from(strategies) if strategies else ({}, []))
        universe = _build_universe(universe_size, universe_seed)
    except ValueError as exc:
        return _fail(str(exc))

    kwargs: dict[str, Any] = {}
    if peak_day is not None:
        kwargs["peak_day" if scenario != "rate_shock" else "over"] = peak_day
    try:
        built = getattr(pt.Scenario, scenario)(**kwargs)
    except Exception as exc:
        return _fail(f"building {scenario}: {exc}")

    # The same entrants twice: once shocked, once not. Baselines are rebuilt
    # per call rather than shared between the two runs, because a reference
    # agent is stateful and reusing one would carry the shocked market's
    # history into the control -- which would corrupt the very difference
    # this tool exists to report, with no visible symptom.
    def entrants() -> dict[str, Any]:
        out: dict[str, Any] = dict(specs)
        for name, agent in baselines.reference_agents(seed=seed).items():
            out.setdefault(name, agent)
        return out

    try:
        shocked = pt.evaluate(entrants(), seed=seed, universe=universe,
                              days=days, scenario=built)
        control = pt.evaluate(entrants(), seed=seed, universe=universe,
                              days=days)
    except pt.ValidationError as exc:
        return _fail(str(exc))

    rows = []
    for name, s in shocked.items():
        base = control.get(name)
        # Differenced on the ROUNDED figures, so a reader who subtracts the
        # two numbers shown gets the number shown. Rounding the exact
        # difference instead would leave the result disagreeing with its own
        # arithmetic by up to 1e-4 -- small, invisible, and the sort of thing
        # a model reports as a discrepancy.
        hi = round(s.return_pct, 4)
        lo = round(base.return_pct, 4) if base else None
        rows.append({
            "name": name,
            "return_pct_shocked": hi,
            "return_pct_control": lo,
            "difference": round(hi - lo, 4) if lo is not None else None,
        })
    rows.sort(key=lambda r: (r["difference"] is None, r["difference"] or 0.0))

    caveats = _caveats(
        days=days, n_seeds=1, signals=_signals_in(specs),
        max_leverage=2.0, universe_size=universe_size,
        scenario_magnitude=True,
    )
    caveats.insert(1, (
        "Scenario MAGNITUDE is outside the envelope: the direction of a "
        "shock's effect is certified, the size of it is not. Read these "
        "differences as sign and ordering, not as a calibrated loss."
    ))
    return {
        "ok": True,
        "scenario": scenario,
        "scenario_table": built.table(days),
        "comparison": rows,
        "reading_note": (
            "`difference` is shocked minus control on the IDENTICAL seed, so "
            "the market draw cancels and what is left is the scenario."
        ),
        "caveats": caveats,
        "provenance": _provenance(
            seed=seed, days=days, scenario=scenario,
            universe={"size": universe_size, "seed": universe_seed},
        ),
    }


@server.tool(
    description="Why did a price move? Returns the seven factor "
                "contributions that SUM to the move -- ground truth the "
                "simulator can give because it computed the reasons."
)
def explain_price_move(
    ticker: str | None = None,
    seed: int = 3,
    universe_size: int = 40,
    universe_seed: int = 111,
    day: int = 1,
    top_n: int = 10,
) -> dict[str, Any]:
    """The labelled-dataset output, and the thing no historical data has.

    You can observe that a stock fell. You cannot observe that 60% of the
    fall was order-flow pressure and the rest was noise -- unless something
    computed it, which is exactly what happened here.
    """
    if not 1 <= day <= MAX_DAYS:
        return _fail(f"day must be 1..{MAX_DAYS}, got {day}")
    try:
        universe = _build_universe(universe_size, universe_seed)
    except ValueError as exc:
        return _fail(str(exc))

    engine = pt.Engine(universe=universe, seed=seed)
    engine.run_days(day)
    tickers = list(engine.tickers)

    cols: dict[str, tuple[float, ...]] = {}
    for factor in pt.Engine.FACTORS:
        raw = engine.attribution(factor)
        cols[factor] = struct.unpack(f"<{len(raw) // 8}d", raw)

    rows = []
    for i, tk in enumerate(tickers):
        parts = {f: cols[f][i] for f in pt.Engine.FACTORS}
        total = sum(parts.values())
        # Rounded for readability, but the residual is measured on the
        # ROUNDED values that are actually returned. Reporting the model's
        # ~1e-16 residual next to figures rounded to 1e-10 would be a
        # sentence contradicting its own numbers -- the exact failure this
        # module exists to prevent. What is returned is what is checked.
        shown = {f: round(v, 12) for f, v in parts.items()}
        shown_total = round(total, 12)
        rows.append({
            "ticker": tk,
            "total_log_move": shown_total,
            "factors": shown,
            "residual": abs(sum(shown.values()) - shown_total),
            "largest_factor": max(parts, key=lambda f: abs(parts[f])),
        })

    if ticker is not None:
        rows = [r for r in rows if r["ticker"] == ticker]
        if not rows:
            return _fail(f"unknown ticker {ticker!r}; roster starts "
                         f"{tickers[:8]}")
    else:
        rows.sort(key=lambda r: abs(r["total_log_move"]), reverse=True)
        rows = rows[:max(1, min(top_n, len(rows)))]

    return {
        "ok": True,
        "day": day,
        "rows": rows,
        "factors": list(pt.Engine.FACTORS),
        "reading_note": (
            "The seven factors SUM to `total_log_move`. Each row carries "
            "its own `residual` -- the measured disagreement in the figures "
            "as returned, so the claim is checkable rather than asserted. "
            "Contributions accumulate per day and reset at market open, so "
            "this is the named day only."
        ),
        "caveats": [
            "This is the simulator's own bookkeeping, not an inference. It "
            "is exact for this model and says nothing about why a real "
            "stock moved.",
            "No agent traded in this run, so `order_flow_impact` reflects "
            "background flow only. Use `evaluate_strategies` to see a "
            "strategy's own footprint.",
        ],
        "provenance": _provenance(
            seed=seed, day=day,
            universe={"size": universe_size, "seed": universe_seed},
            model_fingerprint=engine.model_fingerprint,
        ),
    }


@server.tool(
    description="Inspect the roster a run would use: tickers, sectors and "
                "the fingerprint that identifies it in a result."
)
def describe_universe(size: int = 40, seed: int = 111,
                      limit: int = 20) -> dict[str, Any]:
    """Rosters are generated from `(size, seed)`, so this is how a model
    learns which tickers exist before writing a spec that names them."""
    try:
        universe = _build_universe(size, seed)
    except ValueError as exc:
        return _fail(str(exc))

    shown = list(universe)[:max(1, min(limit, len(universe)))]
    sectors: dict[str, int] = {}
    for inst in universe:
        sectors[inst.sector] = sectors.get(inst.sector, 0) + 1

    return {
        "ok": True,
        "size": len(universe),
        "sector_counts": sectors,
        "instruments": [
            {"ticker": i.ticker, "sector": i.sector,
             "initial_price": round(i.initial_price, 4),
             "beta": round(i.beta, 4),
             "avg_volume": round(i.avg_volume, 1)}
            for i in shown
        ],
        "truncated": len(universe) - len(shown),
        "caveats": [
            "Roster ORDER is contractual: the engine draws in index order, "
            "so a reordered universe is a different market from the same "
            "seed.",
            "The generated cross-section is sector-BALANCED, which no real "
            "index is. That is a named gap in the realism envelope.",
        ],
        "provenance": _provenance(universe={"size": size, "seed": seed}),
    }


def main() -> None:
    """Entry point for `pretium-mcp`. Speaks MCP over stdio."""
    server.run("stdio")


if __name__ == "__main__":  # pragma: no cover
    main()
