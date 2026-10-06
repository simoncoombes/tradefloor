"""An MCP server, so an LLM agent can drive the simulator.

`tradefloor` is a Python library, written for a person writing code. An
MCP server adds a second audience that cannot write code against it, a
model that calls tools and reads back JSON. The design below follows from
that difference.

## The strategy surface is data, never a callable

`evaluate` accepts any object with an `act` method. A tool call cannot carry
one, and accepting a string of Python and running it would make this server
a remote code execution endpoint with a market simulator attached.
:class:`tradefloor.StrategySpec` already closed this gap for citability, and
its module docstring anticipated this exact use:

    It is also what an MCP server needs -- a tool cannot accept a callable
    -- and what stops callers inventing their own serialisation on the way
    to one.

So this server exposes the spec's grammar, and cannot run anything the
spec cannot express. Path dependence, conditional logic and custom signals
need a Python agent and the library. `describe_simulator` states that
limit.

## Every result carries its own caveats

A person calling `evaluate` has the docstring, the README and the realism
envelope page in reach. A model calling `evaluate_strategies` has the tool
result and nothing else, and it will summarise that result to a human who
has even less. The most repeated failure in this project's history is a
correct number under a sentence that inverted it. A design document
described crisis severity as arriving "through correlation" when that was
backwards for one of two parameters. A survey classifier printed `[MOVES]`
for a parameter measured to move things the wrong way. Every one had sound
arithmetic and a wrong connecting sentence, because **a number invites
scepticism and a sentence does not**.

A model handed `{"return_pct": 88.7}` will report that a strategy made
88.7%. So every tool here returns a `caveats` list beside its numbers, and
the caveats are **computed from the envelope and the measured facts at call
time**, never retyped prose. While this module was being written, the
product brief and `README.md` were both found still asserting a return
autocorrelation of +0.219 and +0.249 from an earlier preset, where the
shipped `pt-v3` measures 0.0485 across the README's own published method.
A hardcoded caveat goes stale the same way.

## What is deliberately not exposed

**Atlas.** A survey is thousands of simulations and runs for hours, which
is too long for a tool call inside a conversation. Atlas stays a library
API, driven by `tools/calibration/atlas_survey.py`.

**Arbitrary model parameters.** `ModelParams` has over 200 settable
coefficients and a preset fingerprint that makes a result citable. Letting
a model improvise coefficients produces markets nobody calibrated, reported
with the authority of a named preset. A run tool takes a shipped preset by
name and nothing finer. The default runs when none is named, and it is the
only preset the realism envelope certifies, so a result under another one
carries a caveat that says so and quotes that preset's own measured record
(`tf.preset_record`), and its provenance names the preset. A population of
background traders is taken the same way, a shipped one by name. A
population of your own participants needs the library.

**Anything that writes, outside a session.** Every tool but the session
tools and `start_job` is read-only and pure, and the same arguments give the
same bytes on every platform. `start_job` adds a job to this process's
memory. The session tools keep a market in this process between calls, and
the sessions block further down says why each choice was made.
`session_step` advances one, `session_fork` copies one, `session_rewind`
puts one back to a checkpoint and `close_session` frees one. The same calls
from the same open give the same bytes, apart from the session id.

**The live engine, to a strategy.** A strategy here is data, and it runs
through `evaluate` and `rank` with `trusted_agents=False`, stated at each
call. It is handed the read-only market view every sandboxed agent gets
(see `tradefloor.sandbox`), so it cannot read the true business-cycle
phase, the economy block, the mispricing or the fundamentals, fork the
market or write it. The `oracle` signal is the one declared exception, and
its row says `uses_hidden_state`. There is no opt-in, because a tool call
cannot carry the code that would need one. `explain_price_move` and
`explain` answer the experimenter about a run of their own, after it has
run. No strategy runs inside them, so they hand no agent anything.

A session hands its caller what `MarketView` and `PortfolioView` serve and
nothing else, so a model trading by hand sees what a sandboxed agent sees.
It cannot stop look-ahead, because the caller can fork, rewind or reopen
the market from the same seed. So a session's P&L is never offered
as a strategy's score, and every session result says so.
"""

from __future__ import annotations

import copy
import functools
import inspect
import json
import math
import secrets
import struct
import threading
import time
import traceback
import typing
from concurrent.futures import ThreadPoolExecutor
from typing import Annotated, Any, Literal

import tradefloor as tf
from tradefloor import baselines, envelope
from tradefloor import population as _population
from tradefloor._arith import ordered_sum
from tradefloor._core import OrderError, check_seed
from tradefloor.facts import REAL_MARKETS
from tradefloor.harness import (History, Observation, _f64, _unknown_ticker,
                                session_clock)
from tradefloor.portfolio import check_order, order_items
from tradefloor.sandbox import (HiddenState, MarketView, PortfolioView,
                                TamperGuard, declares_hidden_state)

try:
    from mcp.server import MCPServer
    from mcp.types import ToolAnnotations
except ImportError as exc:  # pragma: no cover - exercised by the install path
    raise ImportError(
        "The MCP server needs the `mcp` package, which tradefloor does not "
        "depend on by default:\n\n    pip install 'tradefloor[mcp]'\n"
    ) from exc

# pydantic arrives with `mcp`, so it is imported after the check above. It
# carries the parameter descriptions into each tool's input schema, and it
# checks a background job's arguments the way the SDK checks a direct call's.
from pydantic import Field, TypeAdapter  # noqa: E402
from pydantic import ValidationError as _SchemaError  # noqa: E402


# -- limits ----------------------------------------------------------------
#
# A model will cheerfully ask for 500 days across 40 seeds because nothing in
# the request looks expensive. These caps exist so a tool call cannot become
# a compute job: the whole surface is meant to answer inside a conversation.
# Every one of them is a wall-clock decision, not a modelling one -- the
# library itself imposes none of them, and a caller who wants more should use
# the library.

MAX_DAYS = 60

#: The day cap for a BACKGROUND job, set to the certified horizon itself.
#:
#: This is the point of having jobs at all. `MAX_DAYS` is 60 because a
#: 252-day evaluation takes about 95 seconds and would block a tool call --
#: which meant every result this server could produce was a SHORT WINDOW on
#: a market whose realism is certified annually. A job is not merely a
#: convenience for slow work; it is what lets the answer reach the horizon
#: the certification is actually about.
MAX_DAYS_ASYNC = envelope.CERTIFIED_HORIZON_DAYS

#: Set on the worker thread so the tool it calls knows it may run long.
#: Thread-local rather than a context variable because `ThreadPoolExecutor`
#: does not propagate a context, and a cap that silently failed to apply
#: would be worse than no cap.
_local = threading.local()


def _day_cap() -> int:
    return MAX_DAYS_ASYNC if getattr(_local, "async_job", False) else MAX_DAYS


MAX_UNIVERSE = 120
MAX_STRATEGIES = 8
MAX_SEEDS = 12

#: Steps per simulated day on `evaluate_strategies` and `rank_strategies`.
#:
#: Every step here is 65 ticks, a minute each (`evaluate`'s default), so six
#: steps are the whole 390-minute session and each step past the sixth runs
#: 65 minutes after the close. 22 steps is 1,430 minutes, the most that fits
#: in a 24-hour day. A run costs time in proportion to days times steps, and
#: until this cap only days were bounded: `steps_per_day=10**9` on a one-day
#: call ran until the server was killed, and two such jobs held both job
#: workers, so every later job was refused.
MAX_STEPS_PER_DAY = 22

#: The steps per day the day caps were measured at, and the tools' default.
DEFAULT_STEPS_PER_DAY = 6


def _steps_refusal(days: int, steps_per_day: int,
                   day_cap: int) -> dict[str, Any] | None:
    """A `_fail` for a steps_per_day the server will not run, else None.

    Days times steps may not exceed `day_cap` days at the default six steps,
    so a longer day buys fewer of them and a run costs at most what the day
    cap already allowed.
    """
    if not 1 <= steps_per_day <= MAX_STEPS_PER_DAY:
        return _fail(
            f"steps_per_day must be 1..{MAX_STEPS_PER_DAY}, got "
            f"{steps_per_day}. A step is 65 minutes, so "
            f"{DEFAULT_STEPS_PER_DAY} steps are the whole trading session and "
            f"{MAX_STEPS_PER_DAY} fill a 24-hour day.")
    budget = day_cap * DEFAULT_STEPS_PER_DAY
    if days * steps_per_day > budget:
        return _fail(
            f"days x steps_per_day must be at most {budget} "
            f"({day_cap} days at {DEFAULT_STEPS_PER_DAY} steps), got {days} x "
            f"{steps_per_day} = {days * steps_per_day}. Ask for fewer days or "
            f"fewer steps"
            + ("." if day_cap > MAX_DAYS else
               f", or use `start_job`, which allows "
               f"{MAX_DAYS_ASYNC * DEFAULT_STEPS_PER_DAY}."))
    return None


#: The seeds `rank_strategies` runs when none are given, and the count the
#: job estimate assumes for it.
DEFAULT_SEEDS = (1, 2, 3, 4, 5, 6)

#: The scenario CONSTRUCTORS reachable by name, each timed by `peak_day`.
#:
#: An earlier draft of this module exposed ONLY preset shapes, on the stated
#: grounds that free-form building was "a fluent API over 40-odd engine
#: fields" and would let a model pin macro state nobody calibrated. Both
#: halves were wrong. A scenario pins a SHORT, VALIDATED list of macro fields
#: (`_macro_fields` reads it from the engine, so no count is written here);
#: unknown names are refused with the valid list; and `Scenario` already
#: round-trips through `to_json`/`from_json`, so it is data in exactly the
#: way a `StrategySpec` is data.
#:
#: The reasoning was also inconsistent with this server's whole design. The
#: caveat engine exists so that questions can be ALLOWED AND LABELLED rather
#: than forbidden -- and the envelope already carries a `scenario_magnitude`
#: gap for precisely the risk being invoked. Forbidding here while labelling
#: everywhere else was a rule with no principle behind it.
#:
#: Keyed by the name this server takes, valued by the `Scenario` class
#: method it calls and the keyword `peak_day` fills. `rate_ramp` calls
#: `Scenario.rate_shock`, because `rate_shock` is also a shipped document
#: (`scenarios/rate_shock.yml`) and one name can reach only one of the two.
#: Until 0.8.5 the document won, so the constructor was listed and could not
#: be called, and `peak_day` was passed to `vix_shock` under its own name,
#: which `vix_shock` does not take.
CONSTRUCTORS: dict[str, tuple[str, str]] = {
    "vix_shock": ("vix_shock", "at"),
    "rate_ramp": ("rate_shock", "over"),
}

#: Names still accepted and no longer listed. `vol_shock` is the library's
#: deprecated alias of `vix_shock`, and it runs `vix_shock` here.
_CONSTRUCTOR_ALIASES = {"vol_shock": "vix_shock"}

SCENARIOS = tuple(CONSTRUCTORS)

#: The macro inputs whose extreme states the endogenous economy does not
#: reach on its own, so a scenario driving one of them earns the envelope's
#: `macro-range` gap (`envelope.check(macro_regime=True)`). Pinned fields by
#: `Scenario` name, intervention targets by registry name.
_REGIME_FIELDS = frozenset({"inflation_rate", "gdp_growth", "cycle"})
_REGIME_TARGETS = frozenset({"macro.inflation", "macro.growth", "macro.cycle"})


def _packaged() -> tuple[str, ...]:
    """The scenario files that ship inside the wheel, by name.

    Read from the package rather than listed, so the pack and this server
    cannot disagree about what exists. Distinct from `CONSTRUCTORS` above,
    which are shapes built from arguments. These are documents: a
    named collection of explicit interventions somebody wrote down, with a
    fingerprint, which is what makes one citable.
    """
    return tf.Scenario.available()

#: The macro fields a scenario may pin. Read from the engine rather than
#: typed, so this cannot drift from what `Scenario` actually accepts.
def _macro_fields() -> list[str]:
    try:
        tf.Scenario("probe").hold(__definitely_not_a_field__=0.0)
    except tf.ValidationError as exc:
        tail = str(exc).split("Valid:")[-1]
        return [f.strip() for f in tail.split(",") if f.strip()]
    return []


def _fail(msg: str) -> dict[str, Any]:
    """Refusals are results, not exceptions.

    An MCP exception reaches the model as a transport-level error with no
    room for guidance. A model that gets `{"error": "...", "how_to_fix":
    "..."}` back can correct itself on the next call, which is the whole
    loop this server is trying to support.
    """
    return {"ok": False, "error": msg}


#: How much of an unexpected exception's text a result carries.
_CRASH_TEXT = 600


def _guarded(fn: Any) -> Any:
    """Turn an exception that escapes a tool into a `_fail` result.

    The SDK sends a crash to the client as `Error executing tool <name>` and
    nothing else, which leaves a model nothing to correct. Every input this
    module expects is refused by name before it gets here. This catches the
    ones nobody expected, a list where a mapping belongs or a string where a
    number does, and returns Python's own message so the next call can fix
    the argument. Only the exception's type and message are sent, never a
    traceback.

    `functools.wraps` keeps the signature, so the SDK builds the same input
    schema from the wrapper as from the function.
    """
    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            text = f"{type(exc).__name__}: {exc}"
            if len(text) > _CRASH_TEXT:
                text = text[:_CRASH_TEXT] + "..."
            return _fail(
                f"{fn.__name__} could not run on these arguments ({text}). "
                f"The argument checks did not catch this input, so the "
                f"message is Python's own. Check each argument against the "
                f"tool's input schema.")
    return wrapper


def _seed_refusal(**named: Any) -> dict[str, Any] | None:
    """A `_fail` for the first argument that is not a seed, else None.

    Checked before any engine is built, so a seed outside 0 to 2**64 - 1
    comes back as a result naming the range rather than as an exception
    from inside a run. A list is checked entry by entry.
    """
    for name, value in named.items():
        for item in (value if isinstance(value, (list, tuple)) else [value]):
            try:
                check_seed(item, name)
            except tf.ValidationError as exc:
                return _fail(str(exc))
    return None


def _whole(name: str, value: Any) -> int:
    """``value`` as an int, or ValueError naming the argument.

    A direct tool call is type-checked against its signature before it gets
    here. A job's arguments and a universe document are not: they arrive as
    raw JSON, so ``"abc"``, ``2.5`` or a list would otherwise raise from
    inside the tool, or run with a float where a count belongs.
    """
    try:
        whole = int(value)
        exact = whole == value or (isinstance(value, str)
                                   and value.strip() == str(whole))
    except (TypeError, ValueError, OverflowError):
        exact = False
    if not exact:
        raise ValueError(f"{name} must be a whole number, got "
                         f"{repr(value)[:80]}")
    return whole


def _provenance(preset: str | None = None, population: Any = None,
                **extra: Any) -> dict[str, Any]:
    """What is needed to re-run this exact result somewhere else.

    Present on every successful result, because a number from a simulator
    without its seed and fingerprints is not a measurement -- it is an
    anecdote, and a model summarising it cannot tell the difference.

    `model_fingerprint` is the run's preset's `ModelParams.fingerprint`,
    the value a scorecard and `Engine.model_fingerprint` record. Until 0.8.5
    it was read from `tf.model_preset()`, which carries no fingerprint, so
    every result here sent an empty string. Results from 0.9.1 and earlier
    also carried `pretium_version`, the package's name before 0.5.0, with
    the same value; 0.10.0 dropped it.

    `preset` is the run's preset after `_preset_choice`: None for the
    shipped default, whose provenance is the one every result carried
    before the run tools took a preset. Another preset is named under the
    same two keys, and `certified_preset` names the default beside it, so
    the result says which preset the certification it is outside of is.

    `population` is the run's `tf.Population` after `_population_choice`,
    None for isolated mode, whose provenance is the one every result
    carried before the run tools took a population. A populated run names
    it and its fingerprint, the value a scorecard records.
    """
    name = tf.model_preset()["name"] if preset is None else preset
    return {
        "tradefloor_version": tf.__version__,
        "model_preset": name,
        "model_fingerprint": _preset_fingerprint(name),
        **({} if preset is None else {"certified_preset": envelope.PRESET}),
        **({} if population is None else {
            "population": population.name,
            "population_fingerprint": population.fingerprint}),
        "spec_version": tf.SPEC_VERSION,
        **extra,
    }


@functools.lru_cache(maxsize=None)
def _preset_fingerprint(name: str) -> str:
    return tf.ModelParams.from_preset(name).fingerprint


# -- presets ---------------------------------------------------------------
#
# The run tools take a `preset`, a shipped preset's name. The realism
# envelope certifies one of them, `envelope.PRESET`, the shipped default:
# every band verdict, gap and measured figure the caveats quote is that
# preset's. Another preset is a different market, run on request and
# labelled as outside the certification, with its own measured record
# (`tf.preset_record`) quoted beside it. Coefficients stay out of reach: a
# preset is a named, fingerprinted vector somebody measured, and an
# improvised vector is not.


def _preset_choice(preset: Any) -> tuple[str | None, str | None]:
    """(the preset to run, or None for the default; a refusal, or None).

    The default named explicitly runs as the default, so a call that names
    it returns the bytes a call that omits it returns.
    """
    if preset is None:
        return None, None
    names = list(tf.preset_names())
    if not isinstance(preset, str) or preset not in names:
        return None, (
            f"unknown preset {preset!r}. The shipped presets are {names}. "
            f"Omit preset for the default, {tf.model_preset()['name']}, the "
            f"one the realism envelope certifies.")
    if preset == tf.model_preset()["name"]:
        return None, None
    return preset, None


def _preset_record(name: str) -> dict[str, Any] | None:
    """A preset's measured record, or None when it has none."""
    try:
        return tf.preset_record(name)
    except LookupError:
        return None


def _preset_summary(name: str) -> dict[str, Any]:
    """What a preset's measured record says about it, for a result.

    Read from the record that ships in the wheel. `of` is the rows the
    record grades: the ones in band and the ones it misses. A row the band
    basis has no band for, or that the run could not read, is in neither.
    """
    record = _preset_record(name)
    out: dict[str, Any] = {
        "name": name,
        "fingerprint": _preset_fingerprint(name),
        "certified": name == envelope.PRESET,
    }
    if record is None:
        out["record"] = None
        return out
    in_band, misses = record["in_band"], record["misses"]
    method = record["measured"]["method"]
    out["default_since"] = record.get("default_since")
    out["record"] = {
        horizon: {"in_band": in_band[horizon],
                  "of": in_band[horizon] + len(misses[horizon]),
                  "misses": list(misses[horizon])}
        for horizon in ("252", "504") if horizon in in_band
    }
    out["measured_on"] = (
        f"{method['roster']}, seeds {method['train_seeds']}, tradefloor "
        f"{record['measured']['tradefloor_version']}")
    return out


def _preset_caveat(name: str) -> str:
    """The caveat a result under a preset other than the certified one earns.

    Computed from the preset's own record, so a count here is the count the
    record carries.
    """
    summary = _preset_summary(name)
    head = (f"PRESET {name}: the realism certification, and every band, gap "
            f"and measured figure this server quotes from it, describe "
            f"{envelope.PRESET}, so a run on {name} is outside the certified "
            f"envelope.")
    rows = (summary["record"] or {}).get("252")
    if rows is None:
        return head + f" {name} has no measured record."
    missing = (f", missing {', '.join(rows['misses'])}" if rows["misses"]
               else "")
    return (head + f" {name}'s own record (tf.preset_record({name!r})) holds "
            f"{rows['in_band']} of {rows['of']} panel rows in their "
            f"real-market bands at 252 days{missing}, measured on "
            f"{summary['measured_on']}. That record measures the preset's "
            f"statistics; the gaps, level rows and crisis rows the envelope "
            f"certifies were measured on {envelope.PRESET} only.")


def _record_value(name: str, statistic: str) -> float | None:
    """A preset record's 252-day median for one statistic, or None."""
    record = _preset_record(name)
    value = (record or {}).get("panel_252", {}).get(statistic)
    return float(value) if isinstance(value, (int, float)) else None


# -- populations -----------------------------------------------------------
#
# The run tools take a `population`, a shipped population's name. Without
# one a run is in isolated mode, as every run was before populations
# existed: each strategy meets the same market to the bit. With one, a
# population of background traders shares each strategy's market and reacts
# to its trading, so the run asks whether an edge survives other traders. A
# populated result is reproducible, and it says in a caveat that strategies
# in it no longer meet identical markets, with what populated mode was
# measured to do (`tradefloor.population.MEASURED`). `rank_strategies` stays
# isolated: its paired test needs every strategy on the same market.


def _population_choice(population: Any) -> tuple[Any, str | None]:
    """(the run's `tf.Population`, or None for isolated; a refusal, or None)."""
    if population is None:
        return None, None
    names = list(_population.SHIPPED)
    if not isinstance(population, str) or population not in names:
        return None, (
            f"unknown population {population!r}. The shipped populations "
            f"are {names}. Omit population for isolated mode, the default, "
            f"where every strategy meets the same market.")
    return tf.Population.named(population), None


#: Why `rank_strategies` refuses a population, as its refusal says it.
_RANK_ISOLATED = (
    "rank_strategies runs in isolated mode only, so it takes no population. "
    "Its paired sign test rests on common random numbers: on each seed every "
    "strategy meets the same market draw, so pairing them takes the market "
    "out of the comparison. A population reacts to what each strategy does, "
    "so under one the strategies would meet different markets and the test "
    "would compare strategy and reaction together. To ask whether an edge "
    "survives other traders, pass population to evaluate_strategies or "
    "run_stress_scenario.")


def _population_summary(pop: Any) -> dict[str, Any]:
    """A population as `describe_simulator` lists it."""
    return {
        "name": pop.name,
        "fingerprint": pop.fingerprint,
        "participants": [{"name": p.name, "kind": p.kind}
                         for p in pop.participants],
    }


def _population_caveat(pop: Any) -> str:
    """The caveat a populated result earns, from the measured record."""
    m = _population.MEASURED
    d = m["edge_decay_signal"]
    return (
        f"POPULATION {pop.name} ({pop.fingerprint}): this run is in "
        f"populated mode, with {len(pop.participants)} background traders "
        f"sharing each strategy's market and reacting to its trading. The "
        f"result is reproducible, but strategies here no longer face "
        f"identical markets, so a difference between two of them mixes "
        f"strategy and reaction; rank_strategies, which runs isolated, "
        f"compares strategies. Measured on {m['model']}, with the "
        f"{m['population']} population ({m['method']}): "
        f"{m['edge_decay']} (the one-day reversal's frictionless return "
        f"over {d['sessions']} sessions falls from {d['isolated']:.1%} "
        f"isolated to {d['populated']:.1%} populated); a predictable "
        f"programme costs about "
        f"{m['programme_cost_excess']:.1%} more than in isolated mode, far "
        f"below the {m['programme_cost_excess_reported']:.0%} "
        f"{m['programme_cost_source']} report from real markets, because "
        f"impact here is mostly transient; {m['crowded_exit']}. Populated "
        f"mode moves return_acf1 by about {m['return_acf1_shift']:+.3f} and "
        f"takes about {m['runtime_ratio']:.1f} times as long to run, and the "
        f"realism certification was measured without a population.")


# -- the caveat engine -----------------------------------------------------


#: Draw addresses per node in a tool result. The tree holds thousands
#: and a model reads the count, so the rest is weight on the wire.
ADDRESS_SAMPLE = 4


def _trim_addresses(tree: dict[str, Any], keep: int) -> tuple[int, int]:
    """Trim each node's address list in place, and report the counts.

    Returns how many addresses survived and how many were dropped, so the
    result can say what it is showing instead of appearing to show
    everything.
    """
    shown = dropped = 0
    addresses = tree.get("addresses") or []
    if len(addresses) > keep:
        tree["addresses"] = addresses[:keep]
        shown, dropped = keep, len(addresses) - keep
    else:
        shown = len(addresses)
    for child in tree["children"]:
        a, b = _trim_addresses(child, keep)
        shown += a
        dropped += b
    return shown, dropped


def _nodes(tree: dict[str, Any]) -> int:
    """How many nodes an explanation tree holds, counted over the tree.

    Reported beside the misses, so "no node missed" is readable as a
    number of nodes rather than as an empty list that could equally mean
    nothing was replayed.
    """
    return 1 + ordered_sum(_nodes(child) for child in tree["children"])


def _statistic_line(name: str, cert: dict[str, Any] | None = None) -> str:
    """One measured statistic against its real-market band, as a sentence.

    Read from `envelope.certified()` on every call: the measured value, the
    band and the verdict are the envelope's own, on the envelope's own band
    basis. The numbers move when the preset moves; a sentence typed here
    would not.

    Until 0.8.5 the band came from `facts.REAL_MARKETS`, the 2015-2025
    decade table, while `describe_simulator` served the verdicts of the
    default basis beside it. The two named different bands for the same
    row, and `crisis_sector_dispersion`, which the decade table does not
    carry, had no line at all.
    """
    cert = cert if cert is not None else envelope.certified()
    row = cert["statistics"].get(name)
    horizon = cert["certified_horizon_days"]
    if row is None or row.get("measured") is None:
        return (f"{name} is graded and its certified value has not been "
                f"measured yet")
    measured, band, in_band = row["measured"], row.get("band"), row["in_band"]
    if band is None or in_band is None:
        return (f"{name} measures {measured:.4g} at the certified "
                f"{horizon}-day horizon, and the {cert['band_basis']} band "
                f"basis has no band to grade it on")
    verdict = "in band" if in_band else "OUT OF BAND"
    return (f"{name} measures {measured:.4g} against a real-market band of "
            f"{band[0]:g} to {band[1]:g} ({verdict}, {cert['band_basis']} "
            f"basis) at the certified {horizon}-day horizon")


#: Sentences in `envelope.check`'s reasons that tell a LIBRARY caller what
#: to do, with the text a caller of this server can act on instead. The
#: roster refusal says to pass the mix's name as `sector_concentrated`, and
#: no run tool here takes that argument; `check_envelope` does. Replaced by
#: exact match, so a reworded library sentence passes through unchanged
#: rather than being half-rewritten, and `test_mcp.py` catches the drift.
_MCP_ADVICE: tuple[tuple[str, str], ...] = (
    ("If your roster is one of them, pass its name as `sector_concentrated`. "
     "Otherwise measure your own roster, since no other mix was measured",
     "To ask about one of those mixes, call `check_envelope` with its name "
     "as `sector_concentrated`. No tool here measures a roster of your own. "
     "That needs the library and tools/calibration/roster_shapes.py in the "
     "repository"),
)


def _for_this_server(reason: str) -> str:
    for library, here in _MCP_ADVICE:
        reason = reason.replace(library, here)
    return reason


def _drives_regime(sc: Any) -> bool:
    """Whether a scenario pins or shocks inflation, growth or the cycle."""
    return (bool(_REGIME_FIELDS & set(sc.fields))
            or any(item.target in _REGIME_TARGETS
                   for item in sc.interventions))


def _caveats(*, days: int, n_seeds: int, signals: set[str],
             max_leverage: float | None, universe_size: int,
             scenario_magnitude: bool = False,
             sector_concentrated: bool = False,
             macro_regime: bool = False,
             preset: str | None = None,
             population: Any = None) -> list[str]:
    """The caveats this particular call earns.

    Computed, not selected from a list of stock warnings. Each branch below
    fires on a property of the request, so a result never carries a caveat
    that does not apply to it, which keeps the ones it does carry worth
    reading.

    `preset` is the run's preset after `_preset_choice`, None for the
    default. A run under the default carries the caveats it always carried.
    `population` is the run's population after `_population_choice`, None
    for isolated mode, which likewise carries the caveats it always carried.
    """
    out: list[str] = [
        "The price process is a known model, not a forecast. A strategy that "
        "does well here has done well against this model; that does not "
        "transfer to real returns.",
    ]
    if preset is not None:
        out.append(_preset_caveat(preset))
    if population is not None:
        out.append(_population_caveat(population))

    # The envelope decides the horizon question, so the answer cannot drift
    # from what the envelope page says.
    v = envelope.check(horizon_days=days,
                       scenario_magnitude=scenario_magnitude,
                       sector_concentrated=sector_concentrated,
                       macro_regime=macro_regime, preset=preset)
    if not v.inside:
        out.append("Outside the certified realism envelope: "
                   + "; ".join(_for_this_server(r) for r in v.reasons))

    # The envelope calls any horizon at or under the certified one "inside",
    # and it is right to -- its gaps are about running LONGER. But that
    # leaves the opposite risk unstated, and this server can only ever run
    # short: MAX_DAYS is well under the certified horizon because a
    # 252-day evaluation takes about 95 seconds and a tool call has to
    # answer inside a conversation.
    #
    # So every result here is a slice of a market whose realism was measured
    # over a year. No violation, and no free pass either: the statistics
    # that make this market credible are annual ones.
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
        own = None if preset is None else _record_value(preset,
                                                        "return_acf1")
        out.append(
            "This strategy trades a return-continuation or reversal signal, "
            "so its edge depends on the simulator's return autocorrelation: "
            + ("" if preset is None else
               f"this run is on {preset}, whose record measures it at "
               + (f"{own:.4g}" if own is not None else "no value")
               + f" over 252 days, and on the certified {envelope.PRESET} ")
            + _statistic_line("return_acf1") + "."
        )

    if "oracle" in signals:
        # Whether the Oracle is a ceiling is the run's preset's answer.
        out.append(
            "The `oracle` signal is PRIVILEGED: it reads the simulator's own "
            "fair value, which no real trader can observe. "
            + ("It is a ceiling for measuring capture, never a strategy."
               if baselines.oracle_is_ceiling(preset) else
               "It is a reference agent, never a strategy, and on this preset "
               "not a ceiling either: market moves mostly stick, so knowing "
               "fair value leaves little edge.")
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

    if sector_concentrated:
        out.append(
            "This roster is sector-CONCENTRATED by request. That is a named "
            "envelope gap rather than a setting: the certification was "
            "measured on a balanced roster, so the realism statistics are "
            "not established for this one. It is the honest way to ask the "
            "question, and the answer is uncertified."
        )
    else:
        out.append(
            "The roster is sector-BALANCED, which no real index is, and that "
            "is a named gap in the envelope. Pass `universe_sectors`, or a "
            "`universe` from `build_universe` with `sectors`, to "
            "concentrate it."
        )
    if population is None:
        out.append(
            "The market is single-venue with zero latency and no strategic "
            "counterparties. See `describe_simulator` for the full list."
        )
    else:
        out.append(
            "The market is single-venue with zero latency. The only traders "
            "in it that react to a strategy's trading are the population's, "
            "and they follow fixed rules. See `describe_simulator` for the "
            "full list."
        )
    return out


# -- shared argument handling ----------------------------------------------


#: The pool a concentrated roster is selected from. `Universe.random` is
#: prefix-stable -- random(20, seed)[:10] == random(10, seed) -- so drawing
#: one fixed pool and filtering it is deterministic in (size, seed, sectors)
#: without depending on how many names happened to match.
_POOL = MAX_UNIVERSE * 4


def _build_universe(size: int, seed: int,
                    sectors: list[str] | None = None) -> tuple[Any, bool]:
    """A roster, and whether it is sector-concentrated.

    Concentration is not a convenience feature. "Certification was measured
    on a sector-balanced roster, which no real index is" is one of the six
    NAMED gaps in the realism envelope, and `envelope.check` already takes
    `sector_concentrated` as an argument. A server that could not build a
    concentrated roster could not ask about a gap its own product
    documents -- so this exists to make that gap reachable, and the second
    return value is what tells the caveat engine to say so.
    """
    if not 2 <= size <= MAX_UNIVERSE:
        raise ValueError(f"universe_size must be 2..{MAX_UNIVERSE}, got {size}")
    if not sectors:
        return tf.Universe.random(size, seed=seed), False

    known = set(tf.sectors())
    unknown = sorted(set(sectors) - known)
    if unknown:
        raise ValueError(
            f"unknown sector(s) {unknown}; known sectors are "
            f"{sorted(known)}")
    want = set(sectors)
    pool = tf.Universe.random(_POOL, seed=seed)
    chosen = [i for i in pool if i.sector in want][:size]
    if len(chosen) < size:
        raise ValueError(
            f"only {len(chosen)} names in {sorted(want)} within a pool of "
            f"{_POOL}; ask for fewer than {size} or add a sector")
    # Roster ORDER is contractual -- the engine draws in index order -- and
    # pool order is preserved here, so the same request is the same market.
    return tf.Universe(chosen), True


#: The Instrument fields a hand-authored roster may set. Anything else is
#: refused by name, because a silently ignored field would produce a roster
#: the caller did not describe.
_INSTRUMENT_FIELDS = ("ticker", "sector", "initial_price",
                      "shares_outstanding", "eps", "book_value_per_share",
                      "revenue_growth", "avg_volume", "beta",
                      "short_interest")


def _refuse_unvalued(i: int, inst: Any) -> None:
    """Refuse an authored row the model can value only at its floor.

    The valuation reads a positive `eps` times a sector P/E, or, for a
    loss-making company, `book_value_per_share` times a price-to-book
    multiple. A row with neither (both are optional on `Instrument`) is
    valued at the fair-value floor of one cent, and the mispricing pulls its
    price toward that floor every day, as far as the circuit breaker lets
    it. Measured on a two-name roster (AAA technology at 50, BBB energy at
    30, nothing else set, seed 7): buy-and-hold lost 26% on day one and the
    prices kept falling, while the same rows with an `eps` held near their
    opening prices. The result read as an ordinary day, so the row is
    refused here with the fix, and the library is left as it is.

    The floor is read from `tf.fair_value` with nothing to value on, rather
    than typed, and the row is valued the same way.
    """
    floor = tf.fair_value(sector=inst.sector).fair_value
    value = tf.fair_value(eps=inst.eps, sector=inst.sector,
                          revenue_growth=inst.revenue_growth,
                          book_value_per_share=inst.book_value_per_share
                          ).fair_value
    if value > floor:
        return
    given = ", ".join(f"{k}={getattr(inst, k)!r}"
                      for k in ("eps", "book_value_per_share")
                      if getattr(inst, k) is not None) or "no eps or book value"
    raise ValueError(
        f"instrument {i} ({inst.ticker}): with {given} the model has nothing "
        f"to value it on, so its fair value is the {floor:g} floor and its "
        f"price falls toward that floor every day, as far as the circuit "
        f"breaker allows. Give a positive `eps` (earnings per share, which "
        f"the valuation multiplies by a sector P/E), or, for a loss-making "
        f"company, a positive `book_value_per_share`.")


def _resolve_universe(doc: Any) -> tuple[Any, bool, dict[str, Any]]:
    """A universe document to (roster, concentrated, canonical document).

    Two forms. `{"size": n, "seed": s, "sectors": [...]}` generates one;
    `{"instruments": [...]}` builds one from explicit rows. The canonical
    document comes back so a result's provenance records the roster as it
    was ASKED FOR, not merely its fingerprint -- a fingerprint identifies a
    roster to someone who already has it, and a document reconstructs it.
    """
    if isinstance(doc, str):
        doc = json.loads(doc)
    if not isinstance(doc, dict):
        raise ValueError(f"a universe must be an object, got "
                         f"{type(doc).__name__}")

    rows = doc.get("instruments")
    if rows:
        # Checked by shape here because the rows reach `tf.Instrument`, whose
        # binding raises TypeError on a wrong type, and a refusal is a
        # result: an exception reaches the model as a bare transport error.
        if not isinstance(rows, list):
            raise ValueError(f"instruments must be a list of objects, got "
                             f"{type(rows).__name__}")
        if not 2 <= len(rows) <= MAX_UNIVERSE:
            raise ValueError(
                f"instruments must number 2..{MAX_UNIVERSE}, got {len(rows)}")
        built = []
        for i, row in enumerate(rows):
            if not isinstance(row, dict):
                raise ValueError(f"instrument {i} must be an object, got "
                                 f"{type(row).__name__}")
            unknown = sorted(set(row) - set(_INSTRUMENT_FIELDS))
            if unknown:
                raise ValueError(
                    f"instrument {i}: unknown field(s) {unknown}; allowed "
                    f"{list(_INSTRUMENT_FIELDS)}")
            for req in ("ticker", "sector", "initial_price",
                        "shares_outstanding"):
                if req not in row:
                    raise ValueError(f"instrument {i}: missing {req!r}")
            kw = {k: v for k, v in row.items()
                  if k not in ("ticker", "sector")}
            try:
                inst = tf.Instrument(row["ticker"], row["sector"], **kw)
            except (tf.ValidationError, TypeError) as exc:
                # The library's messages name the trap -- short_interest is a
                # SHARE COUNT, not a fraction -- so pass them through whole.
                raise ValueError(f"instrument {i} ({row['ticker']}): "
                                 f"{exc}") from exc
            _refuse_unvalued(i, inst)
            built.append(inst)
        universe = tf.Universe(built)
        counts = {}
        for inst in universe:
            counts[inst.sector] = counts.get(inst.sector, 0) + 1
        # A hand-authored roster counts as concentrated unless it spans most
        # of the sector space -- the gap is about the CROSS-SECTION, and a
        # caller who picked the names picked the cross-section.
        concentrated = len(counts) < max(2, len(tf.sectors()) // 2)
        return universe, concentrated, {"instruments": rows}

    unknown = sorted(set(doc) - {"size", "seed", "sectors", "instruments"})
    if unknown:
        raise ValueError(
            f"a universe document takes size, seed and sectors, or "
            f"instruments; {unknown} is not one of them")
    size = _whole("universe size", doc.get("size", 40))
    seed = check_seed(doc.get("seed", 111), "universe seed")
    sectors = doc.get("sectors")
    if sectors is not None and (
            not isinstance(sectors, list)
            or not all(isinstance(s, str) for s in sectors)):
        raise ValueError(
            f"sectors must be a list of sector ids, for example "
            f"[\"technology\", \"energy\"], got {repr(sectors)[:80]}")
    universe, concentrated = _build_universe(size, seed, sectors)
    return universe, concentrated, {"size": size, "seed": seed,
                                    "sectors": sectors}


#: Names of strategies whose `spec_version` this server supplied. Reported in
#: the result rather than kept quiet -- see `_normalise`.
_ASSUMED = "spec_version_assumed"


def _normalise(doc: Any) -> tuple[str, bool]:
    """Wire form to spec JSON, defaulting a MISSING version but never a wrong one.

    `StrategySpec.from_json` requires `spec_version`, correctly: a spec
    document without one is unversioned, and reading a NEWER version
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
        return json.dumps({"spec_version": tf.SPEC_VERSION, **doc}), True
    return json.dumps(doc), False


def _baseline_names() -> tuple[str, ...]:
    """The entrant names the run tools add beside a caller's strategies."""
    return tuple(baselines.reference_agents(seed=0))


def _specs_from(
    strategies: dict[str, Any],
    reserved: tuple[str, ...] = (),
) -> tuple[dict[str, Any], list[str]]:
    """Turn the wire form into specs, naming the offender on failure.

    A model authoring a spec gets the grammar wrong in specific ways, and
    `ValidationError` already says which. Wrapping it with the strategy's
    name is the difference between a fixable error and a retry loop.

    `reserved` names the baselines the calling tool adds to the same
    market. A strategy under one of those names is refused. The baselines
    were added with `setdefault`, so until 0.8.5 a strategy called
    `buy_and_hold` replaced the real buy-and-hold with no message, every
    `versus_buy_and_hold` figure was then measured against the caller's own
    strategy, and a strategy called `oracle` vanished from a ranking whose
    table leaves the oracle row out.
    """
    if not strategies:
        raise ValueError("no strategies given")
    if not isinstance(strategies, dict):
        raise ValueError(
            f"strategies must be an object of name to spec, got "
            f"{type(strategies).__name__}")
    if len(strategies) > MAX_STRATEGIES:
        raise ValueError(
            f"at most {MAX_STRATEGIES} strategies per call, got "
            f"{len(strategies)}")
    taken = sorted(set(strategies) & set(reserved))
    if taken:
        raise ValueError(
            f"strategy name(s) {taken} belong to baselines this call runs on "
            f"the same market ({', '.join(reserved)}), and a strategy under "
            f"that name would replace the baseline in every comparison. "
            f"Rename it, for example 'my_{taken[0]}'.")
    built: dict[str, Any] = {}
    assumed: list[str] = []
    for name, doc in strategies.items():
        try:
            text, was_assumed = _normalise(doc)
            built[name] = tf.StrategySpec.from_json(text)
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
        # Only when set, so every ordinary row is the row it was. A strategy
        # here is data and runs sandboxed, so `trusted` never appears; the
        # oracle, and a blend holding one, read hidden state by declaration.
        **({"uses_hidden_state": True} if s.uses_hidden_state else {}),
        **({"tampered": True} if s.tampered else {}),
        # Only when any were paid, for the same reason: a model without
        # dividends pays none and its rows are the rows they were.
        **({"dividends": round(s.dividends, 2)}
           if getattr(s, "dividends", 0.0) else {}),
    }


# -- scenario timing -------------------------------------------------------


#: How far ahead a macro path is read for the days it changes. Four
#: certified horizons, which covers every shipped document's timing (the
#: latest, `recession`, starts its last event on day 680) and a path is a
#: few closures, so reading it is cheap.
_PATH_SCAN_DAYS = 4 * envelope.CERTIFIED_HORIZON_DAYS


def _path_episodes(sc: Any, days: int) -> list[tuple[int, int, str]]:
    """When a scenario's macro PATH moves: (first day, last day, what).

    One episode per field per run of consecutive days on which its pinned
    value changes, so a ten-day ramp is one episode and a step is an
    episode of one day. Day 0 is left out, since a pin holds its field from
    the first day and what a reader needs is when the path MOVES. Read from
    `Scenario.at` rather than from the pins, so a constructor that builds
    its path in one closure (`vix_shock` spikes on its own `at`) is read the
    same way as a ramp.
    """
    if not sc.fields:
        return []
    moves: dict[str, list[int]] = {field: [] for field in sc.fields}
    before = sc.at(0)
    for day in range(1, max(days, _PATH_SCAN_DAYS)):
        now = sc.at(day)
        for field in sc.fields:
            if now[field] != before[field]:
                moves[field].append(day)
        before = now
    out: list[tuple[int, int, str]] = []
    for field, changed in moves.items():
        run: list[int] = []
        for day in changed + [None]:
            if run and (day is None or day != run[-1] + 1):
                out.append((run[0], run[-1],
                            f"{field} moves on days {run[0]} to {run[-1]}"
                            if len(run) > 1 else
                            f"{field} changes on day {run[0]}"))
                run = []
            if day is not None:
                run.append(day)
    return sorted(out)


def _events(sc: Any, days: int) -> list[tuple[int, int | None, str]]:
    """What can make shocked and control differ: (start, last day, what).

    The control keeps a scenario's pins whenever it carries interventions
    (see `run_stress_scenario`), so for such a scenario only the
    interventions count, each to its `last_day` (None for a permanent
    one). For a pure macro path the control runs no scenario at all, and
    the path's episodes are the events.
    """
    if sc.interventions:
        return sorted(((item.at, item.last_day,
                        " ".join(item.describe().split()))
                       for item in sc.interventions), key=lambda e: e[0])
    return [(a, b, what) for a, b, what in _path_episodes(sc, days)]


def _run_length_advice(day: int) -> str:
    """What run length reaches `day`, and which tool can run it."""
    need = day + 1
    if need <= MAX_DAYS:
        return f"Run at least {need} days."
    if need <= MAX_DAYS_ASYNC:
        return (f"Run at least {need} days, which is past the {MAX_DAYS}-day "
                f"cap on a direct call, so use `start_job` (up to "
                f"{MAX_DAYS_ASYNC} days).")
    return (f"That needs {need} days, past the {MAX_DAYS_ASYNC}-day cap on a "
            f"job, so no call here reaches it. `tf.run_scenario` in the "
            f"library has no cap.")


def _listed(events: list[tuple[int, int | None, str]], cap: int = 6) -> str:
    shown = "; ".join(what for _, _, what in events[:cap])
    more = f"; and {len(events) - cap} more" if len(events) > cap else ""
    return shown + more


def _timing(sc: Any, name: str, days: int) -> tuple[str | None, list[str]]:
    """(a refusal or None, caveats) for running `sc` over `days` days.

    Refused when nothing in the scenario happens inside the run: a day-50
    shock in a 20-day run left shocked and control identical, and until
    0.8.5 every shipped document, run at the default 20 days, came back
    `ok` with a difference of 0.0 for every entrant and nothing saying the
    shock never fired. When some events start after the run, or are still
    in force when it ends, the result carries a caveat naming them.
    """
    events = _events(sc, days)
    if not events:
        return None, []
    inside = [e for e in events if e[0] < days]
    after = [e for e in events if e[0] >= days]
    if not inside:
        first, _, what = after[0]
        return (
            f"{name}: its first event is on day {first} ({what}), and a "
            f"{days}-day run covers days 0 to {days - 1}, so nothing in the "
            f"scenario would happen and every difference would read 0.0. "
            f"{_run_length_advice(first)}"), []
    caveats = []
    if after:
        caveats.append(
            f"{len(after)} of the scenario's {len(events)} events "
            f"{'starts' if len(after) == 1 else 'start'} after this "
            f"{days}-day run ends and never "
            f"{'happens' if len(after) == 1 else 'happen'} here: "
            f"{_listed(after)}. {_run_length_advice(after[-1][0])}")
    cut = [e for e in inside if e[1] is not None and e[1] >= days]
    if cut:
        caveats.append(
            f"The run ends on day {days - 1} while "
            + ("1 event is" if len(cut) == 1 else f"{len(cut)} events are")
            + f" still under way, so the result covers only the start: "
            f"{_listed(cut)}. {_run_length_advice(max(e[1] for e in cut))}")
    return None, caveats


# -- tool parameters -------------------------------------------------------
#
# Each parameter carries a description into the tool's input schema. Until
# 0.8.5 none did, and a model had to guess the grammars (a strategy spec, a
# universe document, a scenario), the caps and the sector spelling. Built
# at import from the module's own constants and the library's lists, so a
# cap or a signal kind named here is the one enforced.

_SPEC_EXAMPLE = ('{"signal": {"kind": "momentum", "lookback_days": 1.0}, '
                 '"portfolio": {"top_k": 5, "gross": 1.0}}')

StrategiesArg = Annotated[dict[str, Any], Field(description=(
    f"Strategies to run, keyed by a name you choose. Each value is a "
    f"strategy spec, for example {_SPEC_EXAMPLE}. Signal kinds: "
    f"{', '.join(tf.spec.SIGNAL_KINDS)}. At most {MAX_STRATEGIES}. The "
    f"baseline names ({', '.join(_baseline_names())}) are taken. Check a "
    f"spec with validate_strategy before running it."))]
SpecArg = Annotated[dict[str, Any], Field(description=(
    f"One strategy spec, for example {_SPEC_EXAMPLE}. `spec_version` may be "
    f"left out and is then set to the current version."))]
SeedArg = Annotated[int, Field(description=(
    "Simulation seed, an integer from 0 to 2**64 - 1. The same seed and "
    "arguments give the same result."))]
SeedsArg = Annotated[list[int] | None, Field(description=(
    f"Simulation seeds, 2 to {MAX_SEEDS} of them. Every entrant trades the "
    f"same market on each seed. Omit for {list(DEFAULT_SEEDS)}."))]
UniverseSizeArg = Annotated[int, Field(description=(
    f"Names in a generated roster, 2 to {MAX_UNIVERSE}. Ignored when "
    f"`universe` is given."))]
UniverseSeedArg = Annotated[int, Field(description=(
    "Seed that generates the roster, separate from the simulation seed. "
    "Ignored when `universe` is given."))]
SectorsArg = Annotated[list[str] | None, Field(description=(
    f"Lowercase sector ids to concentrate a generated roster on, for "
    f"example [\"technology\", \"energy\"]. The ids: "
    f"{', '.join(tf.sectors())}. A concentrated roster is a named envelope "
    f"gap, and the result says so."))]
UniverseArg = Annotated[dict[str, Any] | None, Field(description=(
    "A roster document, usually the `universe` field of a build_universe "
    "result. Either {\"size\": n, \"seed\": s, \"sectors\": [...]} or "
    "{\"instruments\": [...]}. When given it replaces universe_size, "
    "universe_seed and universe_sectors."))]
DaysArg = Annotated[int, Field(description=(
    f"Trading days to run: 1 to {MAX_DAYS} in a direct call, up to "
    f"{MAX_DAYS_ASYNC} (the certified horizon) through start_job."))]
StepsPerDayArg = Annotated[int, Field(description=(
    f"Decision points per trading day, 1 to {MAX_STEPS_PER_DAY}. Each entrant "
    f"is asked for orders at each one. A step is 65 minutes, so "
    f"{DEFAULT_STEPS_PER_DAY} cover the trading session, and days times steps "
    f"may be at most {MAX_DAYS * DEFAULT_STEPS_PER_DAY} in a direct call."))]
CashArg = Annotated[float, Field(description=(
    "Starting cash for each entrant, in currency."))]
LeverageArg = Annotated[float | None, Field(description=(
    "Cap on gross exposure as a multiple of net worth. null removes the "
    "cap, and the result then warns that trading size alone can win."))]
BaselinesArg = Annotated[bool, Field(description=(
    f"Add the baseline agents ({', '.join(_baseline_names())}) to the same "
    f"market. On by default, because a return means little without "
    f"buy-and-hold's beside it."))]
ScenarioArg = Annotated[str | dict[str, Any], Field(description=(
    "A shipped document by name (list_scenarios lists them with the day "
    "each one's first event falls on, and the run must be longer than "
    f"that), a constructor by name ({', '.join(CONSTRUCTORS)}, timed with "
    "peak_day), or a scenario document from build_scenario."))]


def _constructor_default(name: str) -> Any:
    """The library's default for the keyword `peak_day` fills, or None."""
    method, keyword = CONSTRUCTORS[name]
    raw = vars(tf.Scenario).get(method)
    try:
        return inspect.signature(getattr(raw, "_func", raw)
                                 ).parameters[keyword].default
    except (KeyError, TypeError, ValueError):
        return None


PeakDayArg = Annotated[int | None, Field(description=(
    f"Constructors only. vix_shock: the day the VIX jumps to its peak "
    f"(default {_constructor_default('vix_shock')}). rate_ramp: the day the "
    f"policy rate and corporate yield reach their end level (default "
    f"{_constructor_default('rate_ramp')}). A vix_shock peak must fall "
    f"inside the run."))]
TickerArg = Annotated[str | None, Field(description=(
    "One ticker from the roster. Omit to take the largest moves "
    "(explain_price_move) or the first name (explain)."))]
DayArg = Annotated[int, Field(description=(
    f"The trading day to explain, 1 to {MAX_DAYS}."))]
PresetArg = Annotated[str | None, Field(description=(
    f"A shipped model preset to run, by name: "
    f"{', '.join(tf.preset_names())}. Omit for the shipped default, "
    f"{tf.model_preset()['name']}, the only preset the realism envelope "
    f"certifies; a result under any other carries a caveat saying it is "
    f"outside the certification, with that preset's own measured record. "
    f"describe_simulator lists every preset's record."))]
PopulationArg = Annotated[str | None, Field(description=(
    f"A shipped population of background traders to run beside, by name: "
    f"{', '.join(_population.SHIPPED)}. They share each strategy's market "
    f"and react to its trading (populated mode), so a result says whether "
    f"an edge survives other traders, and strategies no longer meet "
    f"identical markets. Omit for isolated mode, the default. "
    f"describe_simulator lists each population's traders."))]
RankPopulationArg = Annotated[str | None, Field(description=(
    "rank_strategies refuses any population, because its paired test needs "
    "every strategy on the same market. Pass population to "
    "evaluate_strategies or run_stress_scenario instead."))]


# -- the server ------------------------------------------------------------

INSTRUCTIONS = """\
`tradefloor` is a deterministic equity market simulator with a real limit order
book. Orders match against real depth, so trading moves the price.

Start with `describe_simulator`. It reports what this market is certified to
reproduce and what it is not, and everything else is easier to read after it.

Strategies are data. Build one with `validate_strategy`, then run it with
`evaluate_strategies`. There is no way to submit Python here.

Every result carries a `caveats` list. It is computed for that specific call
and is part of the result. A summary of a tradefloor result that drops the
caveats misreports it.

A single seed measures the seed as much as the strategy. `rank_strategies`
runs the same evaluation across many seeds.

To trade step by step yourself, open a session with `open_session` and advance
it with `session_step`. A session can be forked and rewound, so its P&L is not
a strategy's score.
"""

# Every tool says what it does to the world. Most only read: each builds
# its own engine, runs it and returns what it measured, and the same
# arguments give the same result. start_job, open_session and session_fork
# add a job or a session to this server's memory, so they are not
# read-only, but they change nothing else. session_step advances a session
# and keeps the state it left as a checkpoint, so it only adds, apart from
# the oldest checkpoint past MAX_CHECKPOINTS; session_rewind drops the
# checkpoints after the one it returns to and close_session drops a
# session, so those two are destructive.
_READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                             idempotentHint=True, openWorldHint=False)
_STARTS_JOB = ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                              idempotentHint=False, openWorldHint=False)

server = MCPServer(
    name="tradefloor",
    title="tradefloor market simulator",
    version=tf.__version__,
    instructions=INSTRUCTIONS,
)


def _measured_cost() -> str:
    """The cost line `describe_simulator` serves.

    Computed from the estimate `start_job` reports, so the two cannot
    disagree. The figures it replaced (0.5s at 5 days, 20s at 60, 95s at
    252) carried no date and came from 0.1.0's preset.
    """
    one = {"strategies": {"m": {}}}
    figures = ", ".join(
        f"{_estimate_seconds('evaluate_strategies', {**one, 'days': d}):.0f}s"
        f" at {d} days" for d in (5, MAX_DAYS, MAX_DAYS_ASYNC))
    return (
        f"One strategy and the baselines on 40 names: about {figures}. CPU "
        f"time measured on pt-v20 on 2026-09-26; a loaded machine takes "
        f"longer. Most of a short run is start-up: each entrant trades its "
        f"own copy of the market, one more copy runs untraded, and each copy "
        f"costs about the same to start whatever the roster. max_days is set "
        f"below the certified {MAX_DAYS_ASYNC}-day horizon for this reason, "
        f"so every direct result here is a SHORT WINDOW on a market whose "
        f"realism is an annual measurement.")


@server.tool(
    title='Describe the simulator',
    description=(
        "Describe what this simulator is, what its realism checks certify, "
        "what it cannot do, the caps on every tool and how long a run "
        "takes. It also lists every shipped preset with its measured "
        "record, says which one the certification covers, lists the shipped "
        "populations of background traders with what they were measured to "
        "do, and describes market sessions. Call it first, before any other "
        "tool. It takes "
        "no arguments, runs no market and returns the same text on every "
        "call."),
    annotations=_READ_ONLY,
)
@_guarded
def describe_simulator() -> dict[str, Any]:
    """Orientation, computed from the shipped envelope rather than prose."""
    cert = envelope.certified()
    # READ THE ENVELOPE'S OWN VERDICTS, do not recompute them. This walked
    # `REAL_MARKETS` and graded each row against the shipped decade pair,
    # which made this surface a band path of its own: `certified` could be
    # on one basis and the served split on another, and the two disagreed
    # on `sector_excess_corr` the day the default basis moved. A row the
    # basis has no band for is UNREADABLE and is served as neither in nor
    # out, because calling it out would publish a verdict nobody reached.
    in_band, out_band, unreadable = [], [], []
    for name, row in cert["statistics"].items():
        if row["in_band"] is None:
            unreadable.append(name)
        elif row["in_band"]:
            in_band.append(name)
        else:
            out_band.append(name)
    unmeasured = list(cert["unmeasured"])

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
            "statistics_unreadable": unreadable,
            "band_basis": cert["band_basis"],
            # The split: a green panel certifies the shape rows; the level
            # and crisis rows are reported with their own verdicts, and one
            # whose certified value is not yet measured is named here rather
            # than counted either way.
            "groups": cert["groups"],
            "statistics_unmeasured": unmeasured,
            # One line per served statistic, from the same rows as the
            # verdicts above. This walked `REAL_MARKETS` until 0.8.5, which
            # quoted the decade bands beside the default basis's verdicts
            # and left out `crisis_sector_dispersion`, a row that table
            # does not carry.
            "detail": [_statistic_line(n, cert) for n in cert["statistics"]],
        },
        "known_gaps": [
            {"id": g["id"], "forbids": g["forbids"]} for g in cert["gaps"]
        ],
        "structural_limitations": [
            "Single venue: no fragmentation, no NBBO, no routing.",
            "Zero latency; orders arrive instantly.",
            "No strategic counterparties by default. You trade against a "
            "market maker and aggregate flow, which do not adapt to you. A "
            "population (see `populations`) adds background traders that "
            "react to your trading by fixed rules.",
            "Generated rosters are sector-balanced, which no real index is. "
            "build_universe can concentrate one on chosen sectors or take "
            "authored instruments, and a result on either says the roster "
            "is outside the certification.",
            "Good results do not predict real returns. The price process "
            "comes from a known model.",
        ],
        "strategy_grammar": {
            "signal_kinds": list(tf.spec.SIGNAL_KINDS),
            "cadences": list(tf.spec.CADENCES),
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
        "scenarios": {
            "catalogue": "list_scenarios returns the shipped documents, the "
                         "constructors, and every intervention target with "
                         "what it was measured to be worth.",
            "shipped": "named documents with fingerprints. Pass the name to "
                       "run_stress_scenario with days past the document's "
                       "first_event_day, which list_scenarios gives.",
            "constructors": f"{', '.join(CONSTRUCTORS)}, timed with "
                            f"peak_day.",
            "custom": "build_scenario composes one, from hold/ramp/step "
                      "instructions (a macro path) or from shocks and "
                      "transmission (explicit interventions); "
                      "run_stress_scenario takes either.",
        },
        "universes": {
            "generated": "build_universe(size, seed)",
            "concentrated": "build_universe(size, seed, sectors=[...]) -- a "
                            "NAMED envelope gap, labelled in every result "
                            "that uses it",
            "authored": "build_universe(instruments=[...])",
        },
        "long_runs": {
            "direct_call_max_days": MAX_DAYS,
            "job_max_days": MAX_DAYS_ASYNC,
            "how": "start_job / check_job. A job is the ONLY way to reach "
                   "the certified horizon, and a result there does not "
                   "carry the SHORT WINDOW caveat.",
        },
        "factors": list(tf.Engine.FACTORS),
        "limits": {
            "max_days": MAX_DAYS, "max_universe": MAX_UNIVERSE,
            "max_strategies": MAX_STRATEGIES, "max_seeds": MAX_SEEDS,
            "note": "Limits of this MCP server, so a call answers inside a "
                    "conversation. The library imposes none of them.",
            "measured_cost": _measured_cost(),
        },
        "presets": {
            "default": tf.model_preset()["name"],
            "certified": envelope.PRESET,
            "how": (
                "Every run tool and open_session take `preset`, a shipped "
                "preset's name, and run the shipped default when it is "
                f"omitted. The realism envelope certifies {envelope.PRESET} "
                "only: its bands, gaps, level rows and crisis rows were "
                "measured on that preset. A result under any other preset "
                "says it is outside the certification, in a caveat that "
                "quotes that preset's own measured record, and its "
                "provenance names the preset beside `certified_preset`. "
                "check_envelope takes `preset` too."),
            "records": (
                "Each preset's record (tf.preset_record) as `record`: at "
                "252 and 504 days, the panel rows in their real-market "
                "bands (`in_band`) of the rows the record grades (`of`), "
                "and the rows it misses. A record measures a preset; it "
                "does not certify it."),
            "shipped": [_preset_summary(name) for name in tf.preset_names()],
        },
        "populations": {
            "default": None,
            "how": (
                "evaluate_strategies, run_stress_scenario, open_session and "
                "start_job take `population`, a shipped population's name. "
                "Omitted, a run is in isolated mode, the default: every "
                "strategy meets the same market to the bit. Named, the "
                "population's traders share each strategy's market and react "
                "to its trading (populated mode), so the result says whether "
                "an edge survives other traders. It is reproducible, but "
                "strategies in it no longer face identical markets; every "
                "populated result says so in a caveat, and its provenance "
                "names the population and its fingerprint."),
            "rank_strategies": _RANK_ISOLATED,
            "measured": dict(_population.MEASURED),
            "shipped": [_population_summary(tf.Population.named(name))
                        for name in _population.SHIPPED],
        },
        "sessions": {
            "what": (
                "open_session keeps one market in this server between calls. "
                "session_step advances it a few steps or days with your "
                "orders, session_state reads it, session_fork copies it to "
                "try two actions from one state, session_rewind returns to a "
                "checkpoint, and close_session frees it. An order is a "
                "signed share count, {\"quantity\": q, \"limit_price\": "
                "p} for a limit order, or \"cancel\"."),
            "what_you_see": (
                "What a trader sees: prices, the day's bars so far, the "
                "book's best bid and ask, the published macro figures, which "
                "names have news, and your own portfolios. Nothing of the "
                "simulator's own state."),
            "not_a_score": (
                "A session can be forked, rewound and reopened from the same "
                "seed, so its P&L can use knowledge of the market's future. "
                "Score a strategy with evaluate_strategies or "
                "rank_strategies."),
            "limits": {
                "max_sessions": MAX_SESSIONS,
                "max_checkpoints": MAX_CHECKPOINTS,
                "idle_seconds": SESSION_IDLE_SECONDS,
                "max_days": MAX_SESSION_DAYS,
                "max_steps_per_call": MAX_SESSION_STEPS,
                "max_agents": MAX_SESSION_AGENTS,
                "lifetime": "this server process only",
            },
        },
        "not_exposed_here": {
            "atlas": "Response-surface surveys run for hours. Library only.",
            "model_coefficients": "Runs take a shipped preset by name and "
                                  "nothing else: the settable "
                                  "coefficients behind a preset are not "
                                  "exposed, because improvised coefficients "
                                  "produce a market nobody calibrated. A "
                                  "custom vector is a library call.",
            "custom_populations": "Runs take a shipped population by name. "
                                  "A population of your own participants "
                                  "is a library call (tf.Population).",
        },
        "provenance": _provenance(),
    }


@server.tool(
    title='Check a question against the realism envelope',
    description=(
        "Check whether a question falls inside the range the simulator's "
        "realism was measured for, BEFORE running it. Use it whenever a "
        "conclusion leans on a horizon longer than a year, on particular "
        "statistics, on a sector-concentrated roster, on the size of a "
        "scenario's effect or on a preset other than the default. Returns "
        "ok or a refusal that names the measurement behind it, and for a "
        "named preset that preset's measured record. Runs no market, so it "
        "answers at once."),
    annotations=_READ_ONLY,
)
@_guarded
def check_envelope(
    horizon_days: Annotated[int, Field(description=(
        f"The run length you are asking about, in trading days. The "
        f"certified horizon is {envelope.CERTIFIED_HORIZON_DAYS}."))],
    statistics: Annotated[list[str] | None, Field(description=(
        f"Panel statistics your conclusion leans on. Known: "
        f"{', '.join(sorted(REAL_MARKETS))}. An unknown name is refused."))
    ] = None,
    sector_concentrated: Annotated[bool | str, Field(description=(
        f"true if the roster is sector-concentrated, or the name of a "
        f"measured mix: {', '.join(sorted(envelope.ROSTER_SHAPES))}. A mix "
        f"is certified only on the preset it was measured on."))] = False,
    scenario_magnitude: Annotated[bool, Field(description=(
        "true if the conclusion depends on the SIZE of a scenario's effect "
        "rather than its direction."))] = False,
    macro_regime: Annotated[bool, Field(description=(
        "true if the conclusion depends on the economy reaching a particular "
        "state, such as high inflation, stagflation or a policy crisis. "
        "run_stress_scenario sets this itself when a scenario drives "
        "inflation, growth or the cycle."))] = False,
    preset: Annotated[str | None, Field(description=(
        f"The preset the run uses, by name: {', '.join(tf.preset_names())}. "
        f"Omit for the shipped default, {envelope.PRESET}, the only preset "
        f"the envelope certifies. Any other is answered as outside, with "
        f"that preset's own measured record beside the answer; the measured "
        f"roster mixes are granted only on "
        f"{envelope.ROSTER_MEASUREMENT['preset']}."))] = None,
) -> dict[str, Any]:
    """Check a question against the realism envelope before an expensive run.

    `sector_concentrated` takes a mix name as well as a flag, and
    `macro_regime` is exposed, since 0.8.5. Both are `envelope.check`
    arguments this tool left out, which put a named mix and the macro-range
    gap out of reach of a client.

    `preset` is `envelope.check`'s own argument. The library decides the
    roster grant on it and only warns that its other tables describe the
    default. This tool answers `inside: false` for any preset but the
    certified one, with the reason first, because the question a caller
    asks here is whether the certification covers the run, and for another
    preset it does not.
    """
    preset, refusal = _preset_choice(preset)
    if refusal is not None:
        return _fail(refusal)
    try:
        v = envelope.check(
            horizon_days=horizon_days,
            statistics=statistics or (),
            sector_concentrated=sector_concentrated,
            scenario_magnitude=scenario_magnitude,
            macro_regime=macro_regime,
            preset=preset,
        )
    except tf.ValidationError as exc:
        # The statistic list only where a statistic was the problem. A
        # horizon error used to carry it too, and an unknown statistic got
        # the list twice, since the library's message already includes it.
        unknown = [s for s in (statistics or ()) if s not in REAL_MARKETS]
        return _fail(
            str(exc) + (
                ". An unknown name is refused rather than ignored, because "
                "silently dropping one would grant a certification nobody "
                "measured." if unknown and horizon_days >= 1 else ""))
    out: dict[str, Any] = {
        "ok": True,
        "inside": v.inside,
        "reasons": list(v.reasons),
        "gaps": [{"id": g.id, "forbids": g.forbids, "detail": g.detail}
                 for g in v.gaps],
    }
    if preset is not None:
        # The library's own reason for a verdict with no gap says the
        # question met none, which is true of the gaps and not of the run.
        reasons = [r for r in v.reasons if v.gaps]
        out["inside"] = False
        out["reasons"] = [_preset_caveat(preset), *reasons]
        out["preset"] = _preset_summary(preset)
    out["provenance"] = _provenance(preset)
    return out


@server.tool(
    title='Validate a strategy spec',
    description=(
        "Parse and fingerprint one strategy spec WITHOUT running it. Use it "
        "to iterate on a spec cheaply before evaluate_strategies or "
        "rank_strategies: a grammar error comes back naming the field that "
        "was wrong, and a valid spec comes back normalised with its "
        f"fingerprint. A spec looks like {_SPEC_EXAMPLE}. Runs no market."),
    annotations=_READ_ONLY,
)
@_guarded
def validate_strategy(spec: SpecArg) -> dict[str, Any]:
    """Parse and fingerprint one strategy spec without running it.

    Separate from `evaluate_strategies` because a model gets the grammar
    wrong several times before it gets it right, and each of those attempts
    should cost a parse rather than a simulation.
    """
    try:
        specs, assumed = _specs_from({"spec": spec})
        built = specs["spec"]
    except ValueError as exc:
        return _fail(
            f"{exc}\n\nSignal kinds: {list(tf.spec.SIGNAL_KINDS)}. "
            f"Cadences: {list(tf.spec.CADENCES)}. "
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
    title='Evaluate strategies on one market',
    description=(
        "Run strategies on one simulated market, beside the baseline agents "
        "on the same market, and score each one: return, P&L, the cost of "
        "its own trading in basis points, turnover and errors. Use it first, "
        "but it runs one seed, so use rank_strategies before believing an "
        "ordering. A "
        f"strategy is data, for example {_SPEC_EXAMPLE}, and "
        "validate_strategy checks one without running it. days 1 to "
        f"{MAX_DAYS} here (a few seconds), up to {MAX_DAYS_ASYNC} through "
        f"start_job; roster 2 to {MAX_UNIVERSE} names. `preset` runs another "
        "shipped model preset, outside the certification, and `population` "
        "adds background traders that react to each strategy. "
        "Deterministic: the same arguments give the same scores."),
    annotations=_READ_ONLY,
)
@_guarded
def evaluate_strategies(
    strategies: StrategiesArg,
    seed: SeedArg = 7,
    universe_size: UniverseSizeArg = 40,
    universe_seed: UniverseSeedArg = 111,
    universe_sectors: SectorsArg = None,
    universe: UniverseArg = None,
    days: DaysArg = 5,
    steps_per_day: StepsPerDayArg = 6,
    cash: CashArg = 1_000_000.0,
    max_leverage: LeverageArg = 2.0,
    include_baselines: BaselinesArg = True,
    preset: PresetArg = None,
    population: PopulationArg = None,
) -> dict[str, Any]:
    """Run strategies on one market beside the baselines, and score each one.

    `include_baselines` defaults to True because a return of +4% means
    nothing without knowing what buy-and-hold did on the same market, and a
    model handed a bare number will report the bare number.
    """
    if (refused := _seed_refusal(seed=seed)) is not None:
        return refused
    preset, refusal = _preset_choice(preset)
    if refusal is not None:
        return _fail(refusal)
    pop, refusal = _population_choice(population)
    if refusal is not None:
        return _fail(refusal)
    cap = _day_cap()
    if not 1 <= days <= cap:
        return _fail(
            f"days must be 1..{cap}, got {days}"
            + ("" if cap > MAX_DAYS else
               f". For a longer run use `start_job`, which allows up to "
               f"{MAX_DAYS_ASYNC} days -- the certified horizon."))
    if (refused := _steps_refusal(days, steps_per_day, cap)) is not None:
        return refused
    try:
        specs, assumed = _specs_from(
            strategies, _baseline_names() if include_baselines else ())
        roster, concentrated, uni_doc = _resolve_universe(
            universe or {"size": universe_size, "seed": universe_seed,
                         "sectors": universe_sectors})
    except ValueError as exc:
        return _fail(str(exc))

    entrants: dict[str, Any] = dict(specs)
    if include_baselines:
        for name, agent in baselines.reference_agents(seed=seed).items():
            entrants.setdefault(name, agent)

    try:
        scores = tf.evaluate(
            entrants, seed=seed, universe=roster, days=days,
            steps_per_day=steps_per_day, cash=cash, max_leverage=max_leverage,
            # Stated rather than defaulted: a strategy here is never handed
            # the live engine. See the module docstring.
            trusted_agents=False, model=preset,
            # Omitted in isolated mode, so that call is the call it was.
            **({} if pop is None else {"population": pop}),
        )
    except tf.ValidationError as exc:
        return _fail(str(exc))

    rows = [_scorecard_row(s) for s in scores.values()]
    rows.sort(key=lambda r: r["return_pct"], reverse=True)

    result: dict[str, Any] = {
        "ok": True,
        "scores": rows,
        "ranking_note": "Sorted by return on this ONE market draw.",
    }
    withheld = baselines.capture_withheld(scores)
    if include_baselines and withheld is not None:
        # The preset's Oracle is no ceiling, so no ratio is sent, not even
        # an empty one: the headline is buy-and-hold, and the reason is
        # sent in place of the ratio.
        result["versus_buy_and_hold"] = {
            k: round(v, 2)
            for k, v in baselines.versus_buy_and_hold(scores).items()
        }
        result["versus_buy_and_hold_note"] = (
            "Each entrant's P&L less buy-and-hold's on the same market, in "
            "currency: above zero, it earned more than owning the market."
        )
        result["capture_ratio_withheld"] = withheld
    elif include_baselines and "oracle" in scores:
        result["capture_ratio"] = {
            k: round(v, 4) for k, v in tf.capture_ratio(scores).items()
        }
        result["capture_note"] = (
            "Fraction of the oracle's P&L captured. The oracle reads the "
            "simulator's own fair value, so this is a ceiling: 1.0 is "
            "perfect foresight, not a target."
        )

    result["caveats"] = _caveats(
        days=days, n_seeds=1, signals=_signals_in(specs),
        max_leverage=max_leverage, universe_size=len(roster),
        sector_concentrated=concentrated, preset=preset, population=pop,
    )
    result["provenance"] = _provenance(
        preset, pop,
        seed=seed,
        universe=uni_doc,
        universe_fingerprint=next(iter(scores.values())).universe_fingerprint
        if scores else "",
        days=days, steps_per_day=steps_per_day,
    )
    return result


@server.tool(
    title='Rank strategies across many seeds',
    description=(
        "Score strategies across MANY seeds, beside the baseline agents, and "
        "rank them with a paired sign test on each pair. Use it after "
        "evaluate_strategies, because one seed's ordering is often luck. "
        f"Costs about one evaluate_strategies call per seed: 2 to {MAX_SEEDS} "
        f"seeds (default six), days 1 to {MAX_DAYS} here, up to "
        f"{MAX_DAYS_ASYNC} through start_job. Returns each entrant's record "
        "across the seeds (median P&L, seeds ahead of buy-and-hold) and each "
        "pair's sign test. Takes `preset` as evaluate_strategies does. Runs "
        "isolated only and refuses `population`, because the paired test "
        "needs every strategy on the same market. Deterministic."),
    annotations=_READ_ONLY,
)
@_guarded
def rank_strategies(
    strategies: StrategiesArg,
    seeds: SeedsArg = None,
    universe_size: UniverseSizeArg = 40,
    universe_seed: UniverseSeedArg = 111,
    universe_sectors: SectorsArg = None,
    universe: UniverseArg = None,
    days: DaysArg = 5,
    steps_per_day: StepsPerDayArg = 6,
    max_leverage: LeverageArg = 2.0,
    preset: PresetArg = None,
    population: RankPopulationArg = None,
) -> dict[str, Any]:
    """Score strategies across many seeds and rank them with paired tests.

    `rank` takes a factory and not instances because agents are stateful,
    and a reused instance carries one market's history into the next with no
    visible symptom. Specs are rebuilt per seed, so they carry nothing over,
    and this server only ever passes specs.

    Omitted seeds are `DEFAULT_SEEDS`. An empty list is refused like any
    other count outside 2 to `MAX_SEEDS`. Until 0.8.5 `seeds or [...]` ran
    the six defaults for it without a word.

    `population` is always refused (`_RANK_ISOLATED` says why). It is a
    parameter so that the refusal can say so, rather than the argument
    being dropped or refused as unknown.
    """
    if population is not None:
        return _fail(_RANK_ISOLATED)
    seeds = list(DEFAULT_SEEDS) if seeds is None else list(seeds)
    if not 2 <= len(seeds) <= MAX_SEEDS:
        return _fail(f"seeds must be 2..{MAX_SEEDS} values, got {len(seeds)}")
    if (refused := _seed_refusal(seed=seeds)) is not None:
        return refused
    preset, refusal = _preset_choice(preset)
    if refusal is not None:
        return _fail(refusal)
    cap = _day_cap()
    if not 1 <= days <= cap:
        return _fail(
            f"days must be 1..{cap}, got {days}"
            + ("" if cap > MAX_DAYS else
               f". For a longer run use `start_job`, which allows up to "
               f"{MAX_DAYS_ASYNC} days -- the certified horizon."))
    if (refused := _steps_refusal(days, steps_per_day, cap)) is not None:
        return refused
    try:
        specs, assumed = _specs_from(strategies, _baseline_names())
        roster, concentrated, uni_doc = _resolve_universe(
            universe or {"size": universe_size, "seed": universe_seed,
                         "sectors": universe_sectors})
    except ValueError as exc:
        return _fail(str(exc))

    def make_agents() -> dict[str, Any]:
        entrants: dict[str, Any] = dict(specs)
        for name, agent in baselines.reference_agents(seed=0).items():
            entrants.setdefault(name, agent)
        return entrants

    try:
        ranking = tf.rank(
            make_agents, seeds=seeds, universe=roster, days=days,
            steps_per_day=steps_per_day, max_leverage=max_leverage,
            trusted_agents=False, model=preset,
        )
    except tf.ValidationError as exc:
        return _fail(str(exc))

    # `table()` is the RANKED order; `records` is a dict, and iterating it
    # would present insertion order as if it were a ranking.
    if ranking.capture_withheld is not None:
        return _ranking_against_buy_and_hold(ranking, specs, days, seeds,
                                             steps_per_day, max_leverage,
                                             roster, concentrated, uni_doc,
                                             preset)
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
            max_leverage=max_leverage, universe_size=len(roster),
            sector_concentrated=concentrated, preset=preset,
        ),
        "provenance": _provenance(
            preset,
            seeds=list(seeds), days=days, steps_per_day=steps_per_day,
            universe=uni_doc,
            universe_fingerprint=ranking.universe_fingerprint,
        ),
    }


def _ranking_against_buy_and_hold(ranking: Any, specs: dict[str, Any],
                                  days: int, seeds: list[int],
                                  steps_per_day: int,
                                  max_leverage: float | None, roster: Any,
                                  concentrated: bool,
                                  uni_doc: Any,
                                  preset: str | None = None) -> dict[str, Any]:
    """`rank_strategies`' result on a preset where the Oracle is no ceiling.

    The same shape as the capture version, with every capture field left
    out rather than sent as None, the buy-and-hold comparison in their
    place, and the reason in `capture_withheld`.
    """
    records = [
        {
            "name": r.name,
            "mean_excess_over_buy_and_hold": (
                round(r.mean_excess_pnl, 2)
                if r.mean_excess_pnl is not None else None),
            "seeds_ahead_of_buy_and_hold": r.seeds_ahead,
            "median_pnl": round(r.median_pnl, 2),
            "seeds_first": r.wins,
        }
        for r in ranking.table()
    ]
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
        "capture_withheld": ranking.capture_withheld,
        "report": ranking.report(),
        "reading_note": (
            "QUOTE `mean_excess_over_buy_and_hold`: the entrant's P&L less "
            "buy-and-hold's on the same market, averaged over the seeds, "
            "with `seeds_ahead_of_buy_and_hold` beside it. No capture ratio "
            "is reported on this preset; `capture_withheld` says why. "
            "`seeds_first` counts seeds where this entrant ranked FIRST "
            "among ALL entrants, baselines included -- so it is not a "
            "head-to-head record, and two entrants that behave identically "
            "will split it arbitrarily on a tie. For 'is A better than B', "
            "read `paired_sign_tests`: both traded the SAME market on each "
            "seed, so the pairing removes the market from the question. "
            "`decisive` is true only when one won on every paired seed."
        ),
        "caveats": _caveats(
            days=days, n_seeds=len(seeds), signals=_signals_in(specs),
            max_leverage=max_leverage, universe_size=len(roster),
            sector_concentrated=concentrated, preset=preset,
        ),
        "provenance": _provenance(
            preset,
            seeds=list(seeds), days=days, steps_per_day=steps_per_day,
            universe=uni_doc,
            universe_fingerprint=ranking.universe_fingerprint,
        ),
    }


#: The macro fields whose values are names, which the library checks. Every
#: other field takes a number.
_NAMED_FIELDS = frozenset({"cycle", "epicentre"})


def _step_value(i: int, kind: str, field: Any, key: str, value: Any) -> Any:
    """A path step's value, refused by name when a number was needed.

    `Scenario` range-checks a rate and names a bad cycle phase, but it
    takes a string for `vix` and fails on it later, inside arithmetic or
    the engine, as a `TypeError` that reached a client as a bare "Error
    executing tool build_scenario" until 0.8.5.
    """
    if field in _NAMED_FIELDS:
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        what = f"{field!r}" if key == field else f"{key} for {field!r}"
        raise ValueError(f"step {i} ({kind}): {what} must be a number, got "
                         f"{value!r}")
    return value


def _step_days(i: int, kind: str, key: str, value: Any) -> int:
    """A path step's day count, as a whole number or refused by name."""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"step {i} ({kind}): {key} must be a whole number "
                         f"of days, got {value!r}")
    return value


def _scenario_from(doc: Any, days: int) -> Any:
    """Build a `Scenario` from an authored document.

    The grammar mirrors the fluent API one step at a time, because a model
    composing JSON cannot chain method calls:

        {"label": "slow burn", "steps": [
            {"kind": "ramp", "field": "vix", "start": 15, "end": 45,
             "over": 10},
            {"kind": "step", "field": "federal_funds_rate",
             "before": 0.025, "after": 0.05, "at": 5},
            {"kind": "hold", "fields": {"inflation_rate": 0.04}}]}

    Validation is the engine's, not this module's: an unknown field is
    refused by `Scenario` with the valid list attached, and a conflicting
    pin on the same field is refused too. Re-implementing those checks here
    would be a second opinion that could drift from the first.
    """
    if isinstance(doc, str):
        doc = json.loads(doc)
    if not isinstance(doc, dict):
        raise ValueError(f"a scenario must be an object, got "
                         f"{type(doc).__name__}")
    # A document that came back from `build_scenario` is `Scenario`'s OWN
    # serialisation -- a pinned path, not the steps that generated it. Round
    # -tripping it has to go through `Scenario.from_json`, or handing a tool
    # its own output back would fail, which is the first thing anyone tries.
    if "path" in doc and "steps" not in doc:
        try:
            return tf.Scenario.from_json(json.dumps(doc))
        except tf.ValidationError as exc:
            raise ValueError(f"not a readable scenario document: {exc}") from exc

    # An INTERVENTION document, in either of its two shapes: the authoring
    # form a person writes in YAML (`version` + `scenario`), and the resolved
    # form `Scenario.to_json` produces (`schema` + `shocks`). Both are the
    # same experiment, and both route through the library's own loaders
    # rather than being re-implemented here -- a second opinion about what a
    # scenario means is a second thing to keep right.
    if "version" in doc and "scenario" in doc:
        try:
            return tf.Scenario.from_document(doc)
        except tf.ValidationError as exc:
            raise ValueError(str(exc)) from exc
    if "shocks" in doc or "transmission" in doc:
        try:
            if "schema" in doc:
                return tf.Scenario.from_json(json.dumps(doc))
            return tf.Scenario.from_document(
                {"version": 1, "scenario": {
                    "name": doc.get("name") or doc.get("label") or "authored",
                    "description": doc.get("description", ""),
                    **{k: doc[k] for k in ("shocks", "transmission")
                       if k in doc},
                }})
        except tf.ValidationError as exc:
            raise ValueError(str(exc)) from exc

    steps = doc.get("steps")
    if not steps:
        raise ValueError(
            'a scenario document needs either "steps" (a macro PATH) or '
            '"shocks"/"transmission" (explicit INTERVENTIONS), and this has '
            'neither.\n\n'
            'A path pins fields for the whole run. Each step is one of '
            '{"kind":"hold","fields":{...}}, '
            '{"kind":"ramp","field":F,"start":X,"end":Y,"over":N}, or '
            '{"kind":"step","field":F,"before":X,"after":Y,"at":N}. '
            f'Fields: {_macro_fields()}\n\n'
            'An intervention is a change relative to whatever the market has '
            'reached: {"target":T,"operation":"set|add|multiply",'
            '"value":V,"at":N,"duration":D,"shape":"impulse|hold|ramp|'
            'permanent"}. Targets and what each one actually reaches: call '
            '`list_scenarios`.')
    if not isinstance(steps, list):
        raise ValueError(f"steps must be a list of step objects, got "
                         f"{type(steps).__name__}")
    sc = tf.Scenario(str(doc.get("label", "")))
    for i, st in enumerate(steps):
        if not isinstance(st, dict) or "kind" not in st:
            raise ValueError(f"step {i}: needs a 'kind'")
        kind = st["kind"]
        try:
            if kind == "hold":
                fields = st.get("fields", {})
                if not isinstance(fields, dict):
                    raise ValueError(
                        f"step {i} (hold): fields must be an object of "
                        f"field to value, for example {{\"vix\": 30.0}}, got "
                        f"{fields!r}")
                for field, value in fields.items():
                    _step_value(i, kind, field, field, value)
                sc = sc.hold(**fields)
            elif kind == "ramp":
                field = st["field"]
                sc = sc.ramp(
                    field,
                    start=_step_value(i, kind, field, "start", st["start"]),
                    end=_step_value(i, kind, field, "end", st["end"]),
                    over=_step_days(i, kind, "over", st["over"]),
                    begin=_step_days(i, kind, "begin", st.get("begin", 0)))
            elif kind == "step":
                field = st["field"]
                sc = sc.step(
                    field,
                    before=_step_value(i, kind, field, "before", st["before"]),
                    after=_step_value(i, kind, field, "after", st["after"]),
                    at=_step_days(i, kind, "at", st["at"]))
            else:
                raise ValueError(
                    f"unknown step kind {kind!r}; use hold, ramp or step")
        except KeyError as exc:
            raise ValueError(f"step {i} ({kind}): missing {exc}") from exc
        except tf.ValidationError as exc:
            raise ValueError(f"step {i} ({kind}): {exc}") from exc
        except TypeError as exc:
            # A string where a number belongs, or an argument the step does
            # not take. Until 0.8.5 this escaped the tool and reached the
            # client as a bare "Error executing tool build_scenario".
            raise ValueError(
                f"step {i} ({kind}): {exc}. Values are numbers (the cycle "
                f"is a phase name), and `over`, `at` and `begin` are whole "
                f"days.") from exc
    return sc


@server.tool(
    title='List scenarios and intervention targets',
    description=(
        "List the shipped stress scenarios, the scenario constructors and "
        "every intervention target, with what each target was measured to "
        "reach. Read it before build_scenario or run_stress_scenario: each "
        "shipped scenario's first_event_day sets the shortest useful run, "
        "and four targets have effects too small to see over a hundred "
        "days. Takes no arguments and runs no market."),
    annotations=_READ_ONLY,
)
@_guarded
def list_scenarios() -> dict[str, Any]:
    """The catalogue: shipped documents, constructors, and the registry.

    A model authoring a scenario is choosing between fifteen targets whose
    effect sizes differ by three orders of magnitude, and nothing on the wire
    told it which. `macro.corporate_yield` held 200bp higher moves the median
    instrument -4.02%. `macro.fear_greed` moves it exactly 0.00%, measured,
    because nothing in the market reads it. Both are valid to write, and
    only the first changes the market.

    So every target here carries the note the library carries: what reads it,
    how long it takes to arrive, and what it was measured to be worth. The
    refusals come too, because "there is no volatility level to set in this
    model" is a more useful answer than a schema error.
    """
    shipped = []
    for name in _packaged():
        sc = tf.Scenario.load(name)
        starts = sorted(item.at for item in sc.interventions)
        beyond = [day for day in starts if day >= MAX_DAYS_ASYNC]
        shipped.append({
            "name": name,
            "description": sc.description,
            "fingerprint": sc.fingerprint,
            # The day each document's first and last intervention starts.
            # A run must be longer than `first_event_day`, or nothing in the
            # document happens and `run_stress_scenario` refuses it.
            "first_event_day": starts[0] if starts else None,
            "last_event_day": starts[-1] if starts else None,
            "reach": (
                f"Run at least {starts[0] + 1} days to reach the first "
                f"event" + (
                    f" (past the {MAX_DAYS}-day direct cap, so through "
                    f"start_job)" if starts[0] + 1 > MAX_DAYS else "")
                + (f". {len(beyond)} of its {len(starts)} events start on "
                   f"day {beyond[0]} or later, past the {MAX_DAYS_ASYNC}-day "
                   f"job cap, and no run here reaches them." if beyond
                   else ".")) if starts else "",
            "shocks": [item.as_dict() for item in sc.shocks],
            "transmission": [item.as_dict() for item in sc.transmission],
        })
    return {
        "ok": True,
        "shipped": shipped,
        "constructors": list(CONSTRUCTORS),
        "targets": {
            name: {"units": target.units, "note": target.note}
            for name, target in sorted(tf.TARGETS.items())
        },
        "not_supported": dict(sorted(tf.UNSUPPORTED_TARGETS.items())),
        "operations": list(tf.interventions.OPERATIONS),
        "shapes": list(tf.interventions.SHAPES),
        "note": ("Pass a shipped name straight to `run_stress_scenario`, "
                 "with `days` past its `first_event_day`. To author one, "
                 "call `build_scenario` with `shocks` (what the scenario "
                 "asserts happened) and `transmission` (what it ASSUMES "
                 "happened next; this simulator derives neither). `at` "
                 "counts days from the start of the run, from day 0, so an "
                 "event at day 50 needs a run of at least 51 days. A "
                 "constructor is timed with `peak_day`: the day vix_shock's "
                 "spike arrives, or the day rate_ramp's rates reach their "
                 "end level."),
        "caveats": [
            "Scenario MAGNITUDE is outside the certified envelope: the "
            "DIRECTION of a shock's effect is certified, the SIZE is not.",
            "The measured figures in each target note come from one "
            "universe over three seeds at 120 days. They size the LEVER, "
            "not any particular experiment.",
        ],
        "provenance": _provenance(),
    }


@server.tool(
    title='Build and preview a custom scenario',
    description=(
        "Author a custom scenario and see what it resolves to before running "
        "it. Give a macro PATH as hold, ramp and step instructions in "
        "`steps`, or explicit INTERVENTIONS as `shocks` and assumed "
        "`transmission`. Returns the resolved document, its fingerprint and "
        "any warnings; pass that document to run_stress_scenario as "
        "`scenario`. Days count from 0, so an event at day 50 needs a run of "
        "at least 51 days. Runs no market."),
    annotations=_READ_ONLY,
)
@_guarded
def build_scenario(
    steps: Annotated[list[dict[str, Any]] | None, Field(description=(
        "A macro PATH, pinned for the whole run. Each step is "
        "{\"kind\": \"hold\", \"fields\": {\"vix\": 30.0}}, "
        "{\"kind\": \"ramp\", \"field\": F, \"start\": X, \"end\": Y, "
        "\"over\": N, \"begin\": D} or "
        "{\"kind\": \"step\", \"field\": F, \"before\": X, \"after\": Y, "
        "\"at\": D}. Fields: " + ", ".join(_macro_fields()) + "."))] = None,
    shocks: Annotated[list[dict[str, Any]] | None, Field(description=(
        "INTERVENTIONS the scenario asserts happened, each {\"target\": T, "
        "\"operation\": \"set|add|multiply\", \"value\": V, \"at\": D, "
        "\"duration\": N, \"shape\": \"impulse|hold|ramp|permanent\"}. "
        "list_scenarios gives every target and what it was measured to "
        "move."))] = None,
    transmission: Annotated[list[dict[str, Any]] | None, Field(description=(
        "Interventions the scenario ASSUMES followed, in the same form as "
        "shocks. The simulator treats them alike; the split records which "
        "effects are assumptions."))] = None,
    label: Annotated[str, Field(description=(
        "A name for the scenario, carried into results."))] = "",
    days: Annotated[int, Field(description=(
        f"The run length you intend, 1 to {MAX_DAYS} (up to "
        f"{MAX_DAYS_ASYNC} through start_job). A path's table covers these "
        f"days, and a scenario whose events all fall after them is "
        f"refused."))] = 20,
) -> dict[str, Any]:
    """Compose a scenario as data, and check it before spending anything.

    Separate from `run_stress_scenario` for the same reason
    `validate_strategy` is separate from `evaluate_strategies`. A model gets
    a grammar wrong several times before it gets it right, and each of those
    attempts should cost a parse and not a simulation.

    `days` is checked against the scenario's timing, the way
    `run_stress_scenario` checks it, so a shock on day 100 in a 20-day run
    is refused here instead of being accepted and then run to a difference
    of 0.0.
    """
    cap = _day_cap()
    if not 1 <= days <= cap:
        return _fail(
            f"days must be 1..{cap}, got {days}"
            + ("" if cap > MAX_DAYS else
               f". For a longer run use `start_job`, which allows up to "
               f"{MAX_DAYS_ASYNC} days -- the certified horizon."))
    doc: dict[str, Any] = {"label": label}
    if steps:
        doc["steps"] = steps
    if shocks:
        doc["shocks"] = shocks
    if transmission:
        doc["transmission"] = transmission
    if "steps" in doc and ("shocks" in doc or "transmission" in doc):
        return _fail(
            "a scenario can carry a macro path AND interventions, but this "
            "tool takes one at a time so that a mistake in either is "
            "reported on its own. Build the path first, then add the "
            "interventions to the document it returns.")
    try:
        sc = _scenario_from(doc, days)
        table = sc.table(days) if sc.fields else []
    except (ValueError, TypeError, tf.ValidationError) as exc:
        return _fail(str(exc))
    refusal, timing = _timing(sc, label or "this scenario", days)
    if refusal is not None:
        return _fail(refusal)
    return {
        "ok": True,
        "scenario": json.loads(sc.to_json(days if sc.fields else None)),
        "fingerprint": sc.fingerprint,
        "fields_pinned": list(sc.fields),
        "shocks": [item.as_dict() for item in sc.shocks],
        "transmission": [item.as_dict() for item in sc.transmission],
        "describe": sc.describe(),
        "table": table,
        "note": ("Pass this document straight to `run_stress_scenario` as "
                 "`scenario`. A day absent from the table is a day the "
                 "scenario does not pin, and the engine's own dynamics run. "
                 "`transmission` entries are ASSUMPTIONS the author is "
                 "making, not effects this simulator derives."),
        "caveats": [
            "Scenario MAGNITUDE is outside the certified envelope: the "
            "DIRECTION of a shock's effect is certified, the SIZE is not. "
            "An authored scenario can pin macro states no calibration ever "
            "saw -- which is a legitimate question, and an uncertified "
            "answer.",
            *timing,
        ],
        "provenance": _provenance(days=days),
    }


@server.tool(
    title='Run strategies through a stress scenario',
    description=(
        "Run strategies through a macro stress scenario, always beside the "
        "same market unshocked, and compare each strategy across the two. "
        "`scenario` is a shipped document by name (list_scenarios gives "
        "each one's first_event_day, and the run must be longer than that), "
        "a constructor by name (" + ", ".join(CONSTRUCTORS) + ", timed "
        "with peak_day), or a document from build_scenario. A scenario whose "
        "events all fall after the run is refused. Pass fork_day to run both "
        "markets together first and start the scenario on that day, as a "
        "fork of one shared history. Use the result to detect a response, "
        "not to forecast its size. days 1 to "
        f"{MAX_DAYS} here, up to {MAX_DAYS_ASYNC} through start_job. Takes "
        "`preset` and `population` as evaluate_strategies does, the same in "
        "both markets. Deterministic."),
    annotations=_READ_ONLY,
)
@_guarded
def run_stress_scenario(
    scenario: ScenarioArg,
    strategies: Annotated[dict[str, Any] | None, Field(description=(
        "Optional strategies to run beside the baselines, in the "
        "evaluate_strategies form. The baseline names are taken."))] = None,
    seed: SeedArg = 7,
    universe_size: UniverseSizeArg = 40,
    universe_seed: UniverseSeedArg = 111,
    universe_sectors: SectorsArg = None,
    universe: UniverseArg = None,
    days: DaysArg = 20,
    peak_day: PeakDayArg = None,
    fork_day: Annotated[int | None, Field(description=(
        "Run both markets together for this many days, then split them and "
        "start the scenario on the first day after the split: day "
        "`fork_day` of the run. The scenario's events keep their spacing, "
        "and every strategy trades the shared days identically in both "
        "arms. 0 to days - 1. Only for a scenario made of interventions (a "
        "shipped document, or build_scenario with `shocks`); a macro path "
        "or a constructor pins the macro from day 0, so it has no fork "
        "point."))] = None,
    preset: PresetArg = None,
    population: PopulationArg = None,
) -> dict[str, Any]:
    """Stress testing, always paired against the unshocked control.

    A scenario result on its own cannot be read, because a -3% return under
    a rate shock could be the shock or could be the market. Running the
    identical seed with and without the scenario separates the two, so this
    tool always returns both.

    The run length is checked against the scenario's timing before
    anything runs (`_timing`). Every shipped document starts on day 30 or
    later, and until 0.8.5 each one, run at the default 20 days, came back
    with a difference of 0.0 for every entrant and nothing saying why.
    """
    if (refused := _seed_refusal(seed=seed)) is not None:
        return refused
    preset, refusal = _preset_choice(preset)
    if refusal is not None:
        return _fail(refusal)
    pop, refusal = _population_choice(population)
    if refusal is not None:
        return _fail(refusal)
    cap = _day_cap()
    if not 1 <= days <= cap:
        return _fail(
            f"days must be 1..{cap}, got {days}"
            + ("" if cap > MAX_DAYS else
               f". For a longer run use `start_job`, which allows up to "
               f"{MAX_DAYS_ASYNC} days -- the certified horizon."))
    # A document that arrived as JSON text (a direct call, or a job's
    # arguments) is a document, not a name.
    if isinstance(scenario, str) and scenario.lstrip().startswith("{"):
        try:
            scenario = json.loads(scenario)
        except ValueError as exc:
            return _fail(f"scenario looks like a JSON document and does not "
                         f"parse: {exc}")
    name = (_CONSTRUCTOR_ALIASES.get(scenario, scenario)
            if isinstance(scenario, str) else None)
    if (name is not None and name not in CONSTRUCTORS
            and name not in _packaged()):
        return _fail(
            f"unknown scenario {scenario!r}. Constructors: "
            f"{list(CONSTRUCTORS)}. Shipped scenarios: {list(_packaged())}. "
            f"For anything else, author one with `build_scenario` and pass "
            f"the document here.")
    if peak_day is not None and name not in CONSTRUCTORS:
        what = (f"{scenario!r} is a shipped document" if name is not None
                else "an authored document carries its own timing")
        return _fail(
            f"peak_day times a constructor ({', '.join(CONSTRUCTORS)}), and "
            f"{what}, so peak_day means nothing to it. "
            + ("For a rate ramp you can time, use the constructor "
               "'rate_ramp'. " if name == "rate_shock" else "")
            + "Read a document's timing with `list_scenarios`, or author "
              "your own with `build_scenario`.")
    if peak_day is not None and peak_day < 1:
        return _fail(f"peak_day must be a day of the run, 1 or later, got "
                     f"{peak_day}")
    try:
        specs, assumed = (_specs_from(strategies, _baseline_names())
                          if strategies else ({}, []))
        roster, concentrated, uni_doc = _resolve_universe(
            universe or {"size": universe_size, "seed": universe_seed,
                         "sectors": universe_sectors})
    except ValueError as exc:
        return _fail(str(exc))

    authored = name is None
    try:
        if authored:
            built = _scenario_from(scenario, days)
            label = (scenario.get("label") or scenario.get("name")
                     or built.name or "authored")
        elif name in CONSTRUCTORS:
            method, keyword = CONSTRUCTORS[name]
            kwargs = {} if peak_day is None else {keyword: peak_day}
            built = getattr(tf.Scenario, method)(**kwargs)
            label = name
        else:
            built = tf.Scenario.load(name)
            label = name
    except (ValueError, tf.ValidationError) as exc:
        return _fail(f"building scenario: {exc}")

    if fork_day is not None:
        if isinstance(fork_day, bool) or not 0 <= fork_day < days:
            return _fail(f"fork_day must be a day of the run, 0 to "
                         f"{days - 1}, got {fork_day}")
        if name in CONSTRUCTORS:
            return _fail(
                f"{name!r} pins the macro path from day 0, so the two "
                f"markets differ from the first day and there is no shared "
                f"history to fork from. Time it with peak_day instead, or "
                f"use a shipped scenario (list_scenarios), which is made of "
                f"interventions and can start at fork_day.")
        try:
            built = built.starting_at(fork_day)
        except (ValueError, tf.ValidationError) as exc:
            return _fail(
                f"fork_day needs a scenario made of interventions: {exc}. "
                f"Author one with `shocks` in build_scenario.")

    refusal, timing = _timing(built, label, days)
    if refusal is not None:
        return _fail(refusal)

    # The same entrants twice: once shocked, once not. Baselines are rebuilt
    # per call rather than shared between the two runs, because a reference
    # agent is stateful and reusing one would carry the shocked market's
    # history into the control -- which would corrupt the very difference
    # this tool exists to report, with no visible symptom.
    def entrants() -> dict[str, Any]:
        out: dict[str, Any] = dict(specs)
        for key, agent in baselines.reference_agents(seed=seed).items():
            out.setdefault(key, agent)
        return out

    # The same population in both markets, and none in isolated mode, where
    # the call is the call it was.
    populated = {} if pop is None else {"population": pop}
    try:
        shocked = tf.evaluate(entrants(), seed=seed, universe=roster,
                              days=days, scenario=built,
                              trusted_agents=False, model=preset, **populated)
        # The control is the same world WITHOUT the thing being tested. For a
        # macro PATH that is no scenario at all. For a scenario carrying
        # INTERVENTIONS it is the same pins with the interventions removed --
        # otherwise a scenario that both holds a level and shocks it would
        # have its level counted as part of the shock.
        against = (built.without_interventions() if built.interventions
                   else None)
        control = tf.evaluate(entrants(), seed=seed, universe=roster,
                              days=days, scenario=against or None,
                              trusted_agents=False, model=preset, **populated)
    except tf.ValidationError as exc:
        return _fail(str(exc))

    rows = []
    for entrant, s in shocked.items():
        base = control.get(entrant)
        # Differenced on the ROUNDED figures, so a reader who subtracts the
        # two numbers shown gets the number shown. Rounding the exact
        # difference instead would leave the result disagreeing with its own
        # arithmetic by up to 1e-4 -- small, invisible, and the sort of thing
        # a model reports as a discrepancy.
        hi = round(s.return_pct, 4)
        lo = round(base.return_pct, 4) if base else None
        rows.append({
            "name": entrant,
            "return_pct_shocked": hi,
            "return_pct_control": lo,
            "difference": round(hi - lo, 4) if lo is not None else None,
        })
    rows.sort(key=lambda r: (r["difference"] is None, r["difference"] or 0.0))

    caveats = _caveats(
        days=days, n_seeds=1, signals=_signals_in(specs),
        max_leverage=2.0, universe_size=len(roster),
        scenario_magnitude=True, sector_concentrated=concentrated,
        macro_regime=_drives_regime(built), preset=preset, population=pop,
    )
    # After the model caveat and the preset's and population's, if any.
    lead = 1 + (preset is not None) + (pop is not None)
    caveats.insert(lead, (
        "Scenario MAGNITUDE is outside the envelope: the direction of a "
        "shock's effect is certified, the size of it is not. Read these "
        "differences as sign and ordering, not as a calibrated loss."
    ))
    at = lead + 1
    caveats[at:at] = timing
    return {
        "ok": True,
        "scenario": label,
        "scenario_authored": authored,
        "scenario_table": built.table(days),
        "comparison": rows,
        "reading_note": (
            "`difference` is shocked minus control on the IDENTICAL seed, so "
            "the market draw cancels and what is left is the scenario."
            + ("" if fork_day is None else
               f" The two markets were identical through day "
               f"{fork_day - 1} and split on day {fork_day}, when the "
               f"scenario started, so every return includes the shared "
               f"days and the difference comes from the days after the "
               f"split.")
        ),
        **({} if fork_day is None else {"fork_day": fork_day}),
        "caveats": caveats,
        "provenance": _provenance(
            preset, pop,
            seed=seed, days=days, scenario=label,
            **({} if fork_day is None else {"fork_day": fork_day}),
            scenario_document=json.loads(built.to_json(days)),
            universe=uni_doc,
        ),
    }


@server.tool(
    title='Explain a price move by factor',
    description=(
        "Break one day's move for each name into the "
        f"{len(tf.Engine.FACTORS)} factor contributions that sum to the "
        "day's change in the mispricing, the log gap between the model "
        "price and fair value. Use it to ask which factors moved prices; use "
        "explain to trace one name's move down to the random draws behind "
        "it. They are the simulator's own bookkeeping, and they are not the "
        "whole price move. On the default preset most of the day's news and "
        "noise moves fair value, `fair_value_shift` takes that part out of "
        "the mispricing, and the fair-value move itself is not split up. "
        "Without a ticker it returns the top_n largest moves. Builds and "
        f"runs its own market for up to {MAX_DAYS} days, under `preset` if "
        "one is named; read-only."),
    annotations=_READ_ONLY,
)
@_guarded
def explain_price_move(
    ticker: TickerArg = None,
    seed: SeedArg = 3,
    universe_size: UniverseSizeArg = 40,
    universe_seed: UniverseSeedArg = 111,
    universe_sectors: SectorsArg = None,
    universe: UniverseArg = None,
    day: DayArg = 1,
    top_n: Annotated[int, Field(description=(
        "How many instruments to return, largest moves first, when no "
        "ticker is given. 1 or more."))] = 10,
    preset: PresetArg = None,
) -> dict[str, Any]:
    """How much each driver moved each name's mispricing on one day.

    A price history shows that a stock fell. It cannot show how much of the
    fall was order-flow pressure and how much was noise. This tool can,
    because the simulator computed each part. It covers the mispricing
    only. On pt-v20 most of a price's move is fair value moving, and these
    factors do not split that move up.
    """
    if (refused := _seed_refusal(seed=seed)) is not None:
        return refused
    preset, refusal = _preset_choice(preset)
    if refusal is not None:
        return _fail(refusal)
    if not 1 <= day <= MAX_DAYS:
        return _fail(f"day must be 1..{MAX_DAYS}, got {day}")
    if top_n < 1:
        return _fail(f"top_n must be 1 or more, got {top_n}")
    try:
        roster, concentrated, uni_doc = _resolve_universe(
            universe or {"size": universe_size, "seed": universe_seed,
                         "sectors": universe_sectors})
    except ValueError as exc:
        return _fail(str(exc))

    engine = tf.Engine(universe=roster, seed=seed, model=preset)
    engine.run_days(day)
    tickers = list(engine.tickers)

    cols: dict[str, tuple[float, ...]] = {}
    for factor in tf.Engine.FACTORS:
        raw = engine.attribution(factor)
        cols[factor] = struct.unpack(f"<{len(raw) // 8}d", raw)

    rows = []
    for i, tk in enumerate(tickers):
        parts = {f: cols[f][i] for f in tf.Engine.FACTORS}
        total = ordered_sum(parts.values())
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
            "residual": abs(ordered_sum(shown.values()) - shown_total),
            "largest_factor": max(parts, key=lambda f: abs(parts[f])),
        })

    if ticker is not None:
        rows = [r for r in rows if r["ticker"] == ticker]
        if not rows:
            return _fail(f"unknown ticker {ticker!r}; roster starts "
                         f"{tickers[:8]}")
    else:
        rows.sort(key=lambda r: abs(r["total_log_move"]), reverse=True)
        rows = rows[:min(top_n, len(rows))]

    return {
        "ok": True,
        "day": day,
        "rows": rows,
        "factors": list(tf.Engine.FACTORS),
        "reading_note": (
            f"The {len(tf.Engine.FACTORS)} factors in `factors` sum to "
            "`total_log_move`, which is the day's change in the mispricing "
            "`s` (the log gap between the model price and fair value), not "
            "the log change in the price. On pt-v20 and pt-v21, the default, "
            "most of the day's news and noise moves fair value instead, so "
            "`random_noise` and `fair_value_shift` are both large and mostly "
            "cancel, and `total_log_move` is a small part of the price's "
            "move. Each row carries its own `residual`, the measured "
            "disagreement in the figures as returned, so the sum can be "
            "checked. Contributions accumulate per day and reset at market "
            "open, so this is the named day only."
        ),
        "caveats": [
            "This is the simulator's own bookkeeping, not an inference. It "
            "is exact for this model and says nothing about why a real "
            "stock moved.",
            "No agent traded in this run and no order flow was injected, so "
            "`order_flow_impact` is zero. Use `evaluate_strategies` to see a "
            "strategy's own footprint.",
            "`fair_value_shift` is the part of the day's news and noise that "
            "changed the stock's fair value for good, entered as a negative "
            "because it left the mispricing. The other factors report the "
            "whole shock; on presets through pt-v19 this one is zero.",
            "`dividend` is the change in the mispricing at an ex-date open, "
            "where the price drops by the cash dividend; it is zero on a "
            "model without dividends.",
            *([] if preset is None else [
                _preset_caveat(preset),
                f"The reading note describes {envelope.PRESET}. On "
                f"{preset}, `fair_value_shift` is "
                + ("zero on every name returned for this day, so the "
                   "factors split the whole move in the mispricing and "
                   "nothing moved fair value."
                   if all(r["factors"].get("fair_value_shift", 0.0) == 0.0
                          for r in rows) else
                   "not zero here, so part of the day's news and noise "
                   "moved fair value and is not split up."),
            ]),
        ],
        "provenance": _provenance(
            preset,
            seed=seed, day=day,
            universe=uni_doc,
            model_fingerprint=engine.model_fingerprint,
        ),
    }


@server.tool(
    title='Trace a move to its random draws',
    description=(
        "Trace one name's price move on one day down to the random draws "
        "that caused it, as a tree: the day's log move at the top, then each "
        "factor, then the draw addresses beneath them. Every node can be "
        "replayed, and every number is measured by running the day again. "
        "Use explain_price_move to see "
        "which factors moved prices across the roster; use this for one "
        "name when you need to know which draws moved those factors. "
        "`depth` sets how much of the tree the `render` text shows. Builds "
        f"and runs its own market for up to {MAX_DAYS} days, under `preset` "
        "if one is named; read-only and deterministic."),
    annotations=_READ_ONLY,
)
@_guarded
def explain(
    ticker: TickerArg = None,
    seed: SeedArg = 3,
    universe_size: UniverseSizeArg = 12,
    universe_seed: UniverseSeedArg = 111,
    universe_sectors: SectorsArg = None,
    universe: UniverseArg = None,
    day: DayArg = 1,
    depth: Annotated[int, Field(description=(
        "How many levels of the tree the `render` text shows, 0 to 4. The "
        "`tree` field is always whole."))] = 3,
    preset: PresetArg = None,
) -> dict[str, Any]:
    """One name's day, from its move down to the draws that seeded it.

    `explain_price_move` says which factors moved a price. This says which
    draws moved those factors, and hands back a tree whose every node can
    be run again from the state the day started in.

    It is read-only, like every tool here. It builds its own engine, runs
    the days, and returns what it measured. It exposes no replay, because a
    replay runs engines and a tool call answers inside a conversation.
    """
    if (refused := _seed_refusal(seed=seed)) is not None:
        return refused
    preset, refusal = _preset_choice(preset)
    if refusal is not None:
        return _fail(refusal)
    if not 1 <= day <= MAX_DAYS:
        return _fail(f"day must be 1..{MAX_DAYS}, got {day}")
    try:
        roster, concentrated, uni_doc = _resolve_universe(
            universe or {"size": universe_size, "seed": universe_seed,
                         "sectors": universe_sectors})
    except ValueError as exc:
        return _fail(str(exc))

    engine = tf.Engine(universe=roster, seed=seed, model=preset)
    # Asked for before the days run, because a day is kept at its open.
    # The run reaches one day past the target so the day before it is on
    # the tape, which is what separates the valuation's move from the
    # book's.
    engine.keep_explanations(day, day)
    engine.run_days(day + 1, record=True)
    name = ticker if ticker is not None else engine.tickers[0]
    if name not in engine.tickers:
        return _fail(f"unknown ticker {name!r}; roster starts "
                     f"{list(engine.tickers[:8])}")
    try:
        result = engine.explain(name, day)
    except tf.ValidationError as exc:
        return _fail(str(exc))
    except ImportError as exc:
        # pyarrow, which reads the truth table. The `mcp` extra installs
        # it since 0.8.5; before that, `pip install "tradefloor[mcp]"`
        # left it out, and the exception reached the client as a bare
        # "Error executing tool explain" with the install line lost. A
        # server whose mcp was installed some other way can still lack it.
        return _fail(f"{exc}. The extra that installs the MCP server "
                     "installs it too: pip install \"tradefloor[mcp]\"")

    misses = result.check()
    tree = json.loads(result.to_json())["root"]
    # The addresses are the bulk of the payload and they do not shrink
    # with the roster: one name's day carries the same few thousand
    # whatever the market size, which is 88 KB of a 97 KB result. Trimmed
    # to a sample per node, with the count kept, so a model reading this
    # gets the shape of the evidence rather than the whole of it.
    kept, dropped = _trim_addresses(tree, ADDRESS_SAMPLE)
    contributions = [child["name"] for child in tree["children"]]
    return {
        "ok": True,
        "ticker": result.ticker,
        "day": result.day,
        "move": result.move,
        "tree": tree,
        "render": result.render(depth=max(0, min(depth, 4))),
        "checked": {"nodes": _nodes(tree), "misses": misses,
                    "addresses": kept + dropped,
                    "addresses_shown": kept},
        "reading_note": (
            "`move` is the day's log move in the printed price. The "
            f"{len(contributions)} contributions under it sum to that "
            f"move: {', '.join(contributions)}. `misses` is empty when "
            "every node replayed to the contribution it sits under, which "
            "is the claim this tool makes rather than asserts. Each draw "
            f"node states its own count and carries at most "
            f"{ADDRESS_SAMPLE} of its addresses here; `addresses` is how "
            "many the tree holds and `addresses_shown` how many are in "
            "this result."
        ),
        "caveats": list(result.caveats) + _caveats(
            days=day + 1, n_seeds=1, signals=set(), max_leverage=None,
            universe_size=len(roster),
            sector_concentrated=concentrated, preset=preset),
        "provenance": _provenance(
            preset,
            seed=seed, day=day, universe=uni_doc,
            model_fingerprint=engine.model_fingerprint,
        ),
    }


@server.tool(
    title='Build and preview a roster',
    description=(
        "Build a roster of companies and preview it, either generated from "
        "a size and seed (optionally concentrated on chosen sectors) or from "
        "explicit instruments you supply. Use it when the default random "
        "roster will not do, for example to test one sector or your own "
        "companies. Returns a `universe` document that every run tool takes "
        "as `universe`, with the roster's fingerprint and any envelope "
        "warning. Runs no market."),
    annotations=_READ_ONLY,
)
@_guarded
def build_universe(
    size: Annotated[int, Field(description=(
        f"Names in a generated roster, 2 to {MAX_UNIVERSE}."))] = 40,
    seed: Annotated[int, Field(description=(
        "Seed that generates the roster, 0 to 2**64 - 1."))] = 111,
    sectors: SectorsArg = None,
    instruments: Annotated[list[dict[str, Any]] | None, Field(description=(
        f"Explicit rows, 2 to {MAX_UNIVERSE}, in roster order. Each needs "
        f"ticker, sector (a lowercase id), initial_price and "
        f"shares_outstanding. Give eps (earnings per share, valued at a "
        f"sector P/E) or, for a loss-making company, book_value_per_share: "
        f"a row with neither is refused, because the model would value it "
        f"at one cent. Optional: revenue_growth, avg_volume, beta, "
        f"short_interest (a share count, not a fraction). When given, "
        f"size, seed and sectors are ignored."))] = None,
    limit: Annotated[int, Field(description=(
        "How many instruments the preview lists."))] = 20,
) -> dict[str, Any]:
    """Construct a roster and inspect it in one call.

    Rosters were previously only reachable as `(size, seed)` arguments on
    every other tool, so a caller could not ask for a sector-concentrated
    roster (one of the six named envelope gaps) or a hand-authored one. Both
    can be expressed as data, so this tool builds them.

    Returns a `universe` document. Pass it to any run tool as `universe` and
    it supersedes that tool's inline size/seed/sectors, so a roster is
    composed once and reused instead of re-specified per call.
    """
    try:
        universe, concentrated, doc = _resolve_universe(
            {"instruments": instruments} if instruments
            else {"size": size, "seed": seed, "sectors": sectors})
    except ValueError as exc:
        return _fail(str(exc))

    shown = list(universe)[:max(1, min(limit, len(universe)))]
    counts: dict[str, int] = {}
    for inst in universe:
        counts[inst.sector] = counts.get(inst.sector, 0) + 1

    return {
        "ok": True,
        "universe": doc,
        "size": len(universe),
        "fingerprint": tf.universe_util.fingerprint_of(universe),
        "sector_counts": counts,
        "instruments": [
            {"ticker": i.ticker, "sector": i.sector,
             "initial_price": round(i.initial_price, 4),
             "beta": round(i.beta, 4),
             "avg_volume": round(i.avg_volume, 1)}
            for i in shown
        ],
        "truncated": len(universe) - len(shown),
        "known_sectors": list(tf.sectors()),
        "caveats": [
            "Roster ORDER is contractual: the engine draws in index order, "
            "so a reordered universe is a different market from the same "
            "seed.",
            ("This roster is sector-CONCENTRATED, which is a named envelope "
             "gap: certification was measured on a balanced roster, so "
             "realism is not established for this one. It is the honest way "
             "to ask, and the answer is uncertified."
             if concentrated else
             "The generated cross-section is sector-BALANCED, which no real "
             "index is. That is a named gap in the realism envelope -- pass "
             "`sectors` to ask the concentrated question."),
        ],
        "provenance": _provenance(universe=doc),
    }


# -- background jobs -------------------------------------------------------
#
# The simulator releases the GIL during a run -- measured: 412 main-thread
# ticks during a 2.81s evaluation -- so a thread pool is enough and a process
# pool would buy nothing but pickling.
#
# Jobs live in this process. If the client restarts the server, they are
# gone: there is no queue, no database and no promise of durability, and
# `check_job` says so rather than leaving a caller to infer it.

#: At most this many run at once. The cap is about the machine, not the
#: protocol: two 252-day evaluations already saturate a laptop, and a third
#: would slow both without finishing sooner.
MAX_RUNNING_JOBS = 2

#: Finished jobs are kept so a result can be collected late, but not
#: forever. Oldest finished jobs are dropped past this.
MAX_KEPT_JOBS = 32

#: The tools worth running in the background. The cheap ones are absent on
#: purpose -- a job for a 40ms call is two round trips to save nothing.
JobTool = Literal["evaluate_strategies", "rank_strategies",
                  "run_stress_scenario"]
JOBBABLE: tuple[str, ...] = typing.get_args(JobTool)

_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()
_pool = ThreadPoolExecutor(max_workers=MAX_RUNNING_JOBS,
                           thread_name_prefix="tradefloor-mcp-job")

#: What a run costs, in CPU seconds, measured with `tf.evaluate` on pt-v20
#: on the 0.8.5 release candidate on an Apple-silicon Mac (rosters of 8, 40
#: and 120 names, 1 and 11 days, the five reference agents, sim seed 7).
#: `tf.evaluate` builds one engine and forks it for the untraded market and
#: each entrant, and a pt-v20 engine now takes about 0.02s to build, so the
#: cost is almost all days times entrants: 0.05s for 8 names over 1 day,
#: 1.58s for 40 names over 11 days, 4.44s for 120 names over 11 days.
#: Measured on 2026-09-26, before the engine build went from 0.9s to 0.02s
#: and before evaluate forked one engine, the same model had 0.9s per
#: entrant of start-up, 0.02s per entrant-day and 0.0012s per name-day.
_COST_ENGINE = 0.02
_COST_ENTRANT_DAY = 0.002
_COST_NAME_DAY = 0.00065


def _roster_size(args: dict[str, Any]) -> int:
    """How many names a tool call's roster holds, for the estimate.

    Tolerant by design: the estimate must not be the thing that fails. A
    universe can arrive as a document, as JSON text, or with explicit
    instruments; anything unreadable counts as the default 40.
    """
    doc = args.get("universe")
    if isinstance(doc, str):
        try:
            doc = json.loads(doc)
        except ValueError:
            doc = None
    if isinstance(doc, dict):
        rows = doc.get("instruments")
        if isinstance(rows, list) and rows:
            return len(rows)
        if isinstance(doc.get("size"), (int, float)):
            return int(doc["size"])
    size = args.get("universe_size", 40)
    return int(size) if isinstance(size, (int, float)) else 40


def _estimate_seconds(tool: str, args: dict[str, Any]) -> float:
    """A rough run time, from measured cost (see `_COST_ENGINE`).

    Scaled by entrants, roster size, days and steps per day, by the seed
    count for a ranking (six when none are given, as `rank_strategies`
    runs), by two for a stress test, which runs its control as well, and by
    the measured run-time ratio for a populated run. It is an estimate and
    the field says so; a model deciding whether to wait or
    poll needs an order of magnitude, not a promise. The figures are CPU
    time, so a loaded machine takes longer.
    """
    days = args.get("days", 20 if tool == "run_stress_scenario" else 5)
    days = float(days) if isinstance(days, (int, float)) else 5.0
    strategies = args.get("strategies")
    entrants = len(strategies) if isinstance(strategies, dict) else 0
    if tool != "evaluate_strategies" or args.get("include_baselines", True):
        entrants += len(_baseline_names())
    entrants = max(1, entrants)
    # A step past the sixth runs more ticks a day, so the per-day term
    # scales with steps against the six the costs were measured at.
    steps = args.get("steps_per_day", DEFAULT_STEPS_PER_DAY)
    steps = (float(steps) if isinstance(steps, (int, float)) and steps > 0
             else float(DEFAULT_STEPS_PER_DAY))
    run = (_COST_ENGINE
           + days * entrants * (steps / DEFAULT_STEPS_PER_DAY)
           * (_COST_ENTRANT_DAY + _COST_NAME_DAY * _roster_size(args)))
    # A populated run takes the measured ratio longer.
    if args.get("population") is not None:
        run *= _population.MEASURED["runtime_ratio"]
    if tool == "rank_strategies":
        seeds = args.get("seeds")
        count = len(seeds) if isinstance(seeds, list) else len(DEFAULT_SEEDS)
        return run * max(1, count)
    if tool == "run_stress_scenario":
        return run * 2.0                   # shocked plus its control
    return run


def _schema_message(exc: _SchemaError) -> str:
    return "; ".join(
        (".".join(str(p) for p in err["loc"]) + ": " if err["loc"] else "")
        + err["msg"] for err in exc.errors()[:3])


def _job_arguments(tool: str, arguments: Any
                   ) -> tuple[dict[str, Any] | None, str | None]:
    """A job's arguments checked as the SDK checks a direct call's.

    Returns (the checked arguments, None) or (None, the reason). Checked
    BEFORE the job is registered: until 0.8.5 an unknown argument was
    accepted and the job then failed on it, a string for `days` raised out
    of `start_job` with no message, and a string `universe` made the
    estimate raise after the job had been submitted, so a job ran whose id
    the caller never saw.

    Each value goes through the tool's own annotation with pydantic, as a
    direct call's does. A string where a structure belongs is read as JSON
    first, as the SDK does for a direct call.
    """
    if not isinstance(arguments, dict):
        return None, (f"arguments must be an object of argument name to "
                      f"value, got {type(arguments).__name__}")
    fn = inspect.unwrap(globals()[tool])
    params = inspect.signature(fn).parameters
    unknown = sorted(set(arguments) - set(params))
    if unknown:
        return None, (f"{tool} takes no argument named {unknown}. Its "
                      f"arguments are {list(params)}.")
    missing = [name for name, p in params.items()
               if p.default is inspect.Parameter.empty
               and name not in arguments]
    if missing:
        return None, f"{tool} needs {missing}"
    hints = typing.get_type_hints(fn, include_extras=True)
    checked: dict[str, Any] = {}
    for key, value in arguments.items():
        adapter = TypeAdapter(hints[key])
        try:
            checked[key] = adapter.validate_python(value)
            continue
        except _SchemaError as exc:
            error = exc
        if isinstance(value, str):
            try:
                checked[key] = adapter.validate_json(value)
                continue
            except _SchemaError:
                pass
        return None, f"{tool}: {key}: {_schema_message(error)}"
    return checked, None


def _run_job(job_id: str, tool: str, args: dict[str, Any]) -> None:
    _local.async_job = True
    try:
        result = globals()[tool](**args)
    except Exception as exc:                # a crashed job is a result too
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}",
                  "traceback": traceback.format_exc()[-2000:]}
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is not None:
            job["status"] = "done" if result.get("ok") else "failed"
            job["result"] = result
            job["finished"] = time.time()


@server.tool(
    title='Start a background simulation',
    description=(
        "Start a long run of evaluate_strategies, rank_strategies or "
        "run_stress_scenario in the background and get a job id back "
        "immediately. This is the ONLY way to run to the certified "
        f"{MAX_DAYS_ASYNC}-day horizon; a direct call is capped at "
        f"{MAX_DAYS} days so it can answer inside a conversation. The "
        "arguments are checked before the job starts, and the response "
        f"estimates its run time. At most {MAX_RUNNING_JOBS} jobs run at "
        f"once and the last {MAX_KEPT_JOBS} are kept, in this server's "
        "memory only. A `preset` or `population` in the arguments is checked "
        "before the job starts. Poll with check_job."),
    annotations=_STARTS_JOB,
)
@_guarded
def start_job(
    tool: Annotated[JobTool, Field(description=(
        "The tool to run in the background."))],
    arguments: Annotated[dict[str, Any] | None, Field(description=(
        f"That tool's arguments, as a direct call takes them. days may go "
        f"to {MAX_DAYS_ASYNC}. An unknown argument or a wrong type is "
        f"refused before the job starts."))] = None,
) -> dict[str, Any]:
    """Submit work that is too slow to answer inline.

    Everything that can refuse runs before the job is registered: the tool
    name, the arguments, the day cap and the estimate. A job that has been
    submitted always comes back with its id.
    """
    if tool not in JOBBABLE:
        return _fail(f"{tool!r} cannot be run as a job. Jobbable: "
                     f"{list(JOBBABLE)}. Everything else answers inline.")
    args, reason = _job_arguments(tool, {} if arguments is None
                                  else arguments)
    if reason is not None:
        return _fail(reason)
    params = inspect.signature(inspect.unwrap(globals()[tool])).parameters
    days = args.get("days", params["days"].default)
    if not 1 <= days <= MAX_DAYS_ASYNC:
        return _fail(f"days must be 1..{MAX_DAYS_ASYNC} for a job, got {days}")
    # Days times steps, bounded as a direct call bounds it, before a worker
    # is spent on the job.
    if "steps_per_day" in params:
        steps = args.get("steps_per_day", params["steps_per_day"].default)
        if (refused := _steps_refusal(days, steps, MAX_DAYS_ASYNC)) is not None:
            return refused
    # The preset is checked here too, so an unknown name is refused before
    # a worker is spent, and the response names the preset the job runs.
    preset, refusal = _preset_choice(args.get("preset"))
    if refusal is not None:
        return _fail(refusal)
    # And the population, which a ranking refuses outright.
    if tool == "rank_strategies" and args.get("population") is not None:
        return _fail(_RANK_ISOLATED)
    pop, refusal = _population_choice(args.get("population"))
    if refusal is not None:
        return _fail(refusal)
    est = _estimate_seconds(tool, args)
    provenance = _provenance(preset, pop)

    with _jobs_lock:
        running = sum(1 for j in _jobs.values() if j["status"] == "running")
        if running >= MAX_RUNNING_JOBS:
            return _fail(
                f"{running} jobs already running (cap {MAX_RUNNING_JOBS}). "
                f"Wait for one to finish -- starting a third would slow both "
                f"without finishing sooner.")
        job_id = _unguessable_id("job", _jobs)
        _jobs[job_id] = {"id": job_id, "tool": tool, "arguments": args,
                         "status": "running", "started": time.time(),
                         "finished": None, "result": None}
        # Drop the oldest FINISHED jobs; a running one is never evicted.
        finished = sorted((j for j in _jobs.values() if j["status"] != "running"),
                          key=lambda j: j["finished"] or 0.0)
        for stale in finished[:max(0, len(_jobs) - MAX_KEPT_JOBS)]:
            _jobs.pop(stale["id"], None)

    _pool.submit(_run_job, job_id, tool, args)
    return {
        "ok": True,
        "job_id": job_id,
        "status": "running",
        "estimated_seconds": round(est, 1),
        "note": (f"Poll `check_job` with this id. Estimated ~{est:.1f}s of "
                 f"CPU time, from measured cost. It is an estimate, and a "
                 f"loaded machine takes longer. Jobs live in the server "
                 f"process and do not survive a restart."),
        "provenance": provenance,
    }


@server.tool(
    title='Check a background job',
    description=(
        "Check a background job started by start_job. Returns its status "
        "and, once it has finished, the full result in the same form the "
        "direct tool returns. Omit job_id to list every job this server "
        "still holds. Changes nothing, so it is safe to poll."),
    annotations=_READ_ONLY,
)
@_guarded
def check_job(
    job_id: Annotated[str | None, Field(description=(
        "An id exactly as start_job returned it. Omit to list every job "
        "this server process holds."))] = None,
) -> dict[str, Any]:
    """Poll a job, or list what this server is holding."""
    with _jobs_lock:
        if job_id is None:
            return {
                "ok": True,
                "jobs": [
                    {"job_id": j["id"], "tool": j["tool"],
                     "status": j["status"],
                     "elapsed_seconds": round(
                         (j["finished"] or time.time()) - j["started"], 1)}
                    for j in sorted(_jobs.values(), key=lambda j: j["started"])
                ],
                "note": ("Jobs live in this server process only -- a restart "
                         "loses them, finished results included."),
                "provenance": _provenance(),
            }
        job = _jobs.get(job_id)
        if job is None:
            return _fail(
                f"no job {job_id!r}. Either it never existed, it was dropped "
                f"once more than {MAX_KEPT_JOBS} jobs had finished, or the "
                f"server restarted -- jobs do not survive a restart.")
        elapsed = (job["finished"] or time.time()) - job["started"]
        out = {"ok": True, "job_id": job_id, "tool": job["tool"],
               "status": job["status"],
               "elapsed_seconds": round(elapsed, 1),
               "arguments": job["arguments"]}
        if job["status"] == "running":
            est = _estimate_seconds(job["tool"], job["arguments"])
            out["estimated_seconds"] = round(est, 1)
            out["note"] = ("Still running. The estimate is CPU time from "
                           "measured cost, and a long overrun means the "
                           "machine is loaded, not that the job has hung.")
        else:
            out["result"] = job["result"]
        out["provenance"] = _provenance()
        return out


# -- sessions --------------------------------------------------------------
#
# Every tool above builds its own market, runs it and throws it away, so the
# same arguments give the same bytes. A session keeps one market between
# calls: the caller opens it, steps it a few decision points at a time with
# orders of its own, and reads what happened before deciding the next step.
# That is the loop an agent that trades needs, and no tool above offers it,
# because a strategy spec decides every step in advance.
#
# The design choices, and why:
#
# - In this process only, like the jobs. A session is an engine and Python
#   portfolios in memory. There is no store, and a restart loses every
#   session, which each refusal for a missing id says.
# - The loop is `tf.evaluate`'s: the same clock, observe, execute, then the
#   session with the step's own flow, then the resting fills. A session
#   with one strategy-spec agent and no orders of the caller's ends on the
#   net worth `tf.evaluate` scores for that spec, and a test holds the two
#   together. Several agents share one market and execute in label order
#   against one book, as a cohort `tf.World` does.
# - Orders use the harness's `act()` grammar in JSON: a signed share count
#   is a market order, {"quantity": q, "limit_price": p} is a `tf.Limit`,
#   and "cancel" is a `tf.Cancel`. The shape is checked before anything
#   runs; a market refusal (the leverage cap, a book that cannot fill) is
#   reported per order and the rest trade, as the harness does.
# - Checkpoints are `Engine.state_snapshot` and `Engine.restore_state` with
#   copies of the portfolios, the strategy agents and the history: one at
#   the open and one at the end of every call. A fork and a rewind restore
#   one onto a fresh engine built from the same seed, roster, preset and
#   population,
#   and the restored market continues bit for bit, mid-day included; the
#   tests check both.
# - The caller sees the market through `tradefloor.sandbox.MarketView` and
#   its own portfolios through `PortfolioView`, the views a sandboxed agent
#   gets, and nothing else: no fundamentals, no mispricing, no economy
#   block, no true cycle phase. The live engine never leaves the session.
# - A session can be forked, rewound and reopened from the same seed, so
#   whoever drives it can have seen the market's future. Every result says
#   so in a caveat, and the P&L of a session is not a strategy's score.
#
# The caps are memory and wall-clock decisions. A checkpoint of a 40-name
# market pickles to about 23 KB and one of 120 names to about 54 KB, and a
# 120-name session holding eleven checkpoints traced 1.3 MB of Python
# memory, all on pt-v20. A session keeps at most `MAX_CHECKPOINTS`, so
# `MAX_SESSIONS` full sessions on the largest roster hold tens of megabytes.
# Ten days of 120 names ran in 0.47 s, so the per-call step cap, the budget
# of a direct evaluate_strategies call, answers in seconds.

#: Sessions open at once in this server process.
MAX_SESSIONS = 8

#: Checkpoints a session keeps. Past this the oldest after the first is
#: dropped, so the state a session was opened or forked at stays reachable.
MAX_CHECKPOINTS = 64

#: A session no call has touched for this long is closed on the next
#: session call, so a client that forgets to close one does not hold it for
#: the life of the server.
SESSION_IDLE_SECONDS = 30 * 60

#: The longest a session may run: two certified horizons, the 504 days the
#: envelope's longest bands grade. Past it no band reads a result.
MAX_SESSION_DAYS = 2 * envelope.CERTIFIED_HORIZON_DAYS

#: Steps one `session_step` call may run, the budget of a direct
#: evaluate_strategies call: `MAX_DAYS` days at the default six steps.
MAX_SESSION_STEPS = MAX_DAYS * DEFAULT_STEPS_PER_DAY

#: Fills one agent's entry in a step result lists; the rest are counted.
MAX_FILLS_SHOWN = 50

#: The largest moves over a call that a step result names.
MOVERS_SHOWN = 5

#: Agents in one session, hand-driven and strategy together.
MAX_SESSION_AGENTS = MAX_STRATEGIES

#: The clock and the step length `tf.evaluate` uses by default.
_TICKS_PER_STEP = 65
_SESSION_START = (9, 30, 3)

_sessions: dict[str, "_Session"] = {}
_sessions_lock = threading.Lock()


def _copy_live(live: dict[str, Any]) -> dict[str, Any]:
    """An independent copy of a session's state outside the engine.

    The history is copied by its own method: its days are immutable tuples
    and a deep copy would walk every one of them on every checkpoint.
    """
    history = live["history"]
    rest = {k: v for k, v in live.items() if k != "history"}
    out = copy.deepcopy(rest)
    out["history"] = history._copy()
    return out


class _Session:
    """One market kept between calls. See the comment above."""

    def __init__(self, sid: str, *, seed: int, roster: Any, uni_doc: Any,
                 concentrated: bool, preset: str | None, steps_per_day: int,
                 cash: float, max_leverage: float | None,
                 specs: dict[str, Any], hand: list[str],
                 population: Any = None) -> None:
        self.id = sid
        self.seed = seed
        self.roster = roster
        self.uni_doc = uni_doc
        self.concentrated = concentrated
        self.preset = preset
        self.population = population
        self.steps_per_day = steps_per_day
        self.cash = cash
        self.max_leverage = max_leverage
        self.specs = specs
        self.hand = list(hand)
        # Execution order: by label, so the same agents in any order in the
        # request are the same market.
        self.labels = sorted([*specs, *hand])
        self.engine = self._fresh_engine()
        self.tickers = list(self.engine.tickers)
        self.adv = tuple(inst.avg_volume for inst in roster)
        self.live: dict[str, Any] = {
            "step": 0,
            "portfolios": {
                label: tf.Portfolio(cash=cash, max_leverage=max_leverage,
                                    owner=label)
                for label in self.labels},
            "agents": {label: spec.build() for label, spec in specs.items()},
            "history": History(),
            "ledger": {label: {"trades": 0, "rejected": 0, "errors": [],
                               "tampered": False}
                       for label in self.labels},
            "orders": [],
        }
        self.checkpoints: dict[int, tuple[dict[str, Any], dict[str, Any]]] = {}
        self.lineage: list[dict[str, Any]] = []
        self.lock = threading.Lock()
        self.touched = time.monotonic()
        self.keep()

    def _fresh_engine(self, *, to_restore: bool = False) -> Any:
        # With the session's population, which a checkpoint's snapshot
        # carries the state of and restores only into an engine built with
        # it; none in isolated mode, where the engine is the one it was.
        populated = ({} if self.population is None
                     else {"population": self.population})
        # An engine a snapshot is about to be restored into keeps a named
        # opening, so it plays no burn-in and no market prehistory: on
        # pt-v21 that is about 1.5 s a build over 20 names, for a state the
        # restore then replaces whole. The restore refuses a snapshot that
        # lacks a block, so whatever it accepts it puts back entire, and
        # tests/test_mcp_sessions.py holds a fork and a rewind to the
        # session they came from, bit for bit.
        opening = {"macro_state": tf.Macro()} if to_restore else {}
        return tf.Engine(seed=self.seed, universe=self.roster,
                         model=self.preset, **populated, **opening)

    # -- the clock --------------------------------------------------------

    @property
    def step(self) -> int:
        return self.live["step"]

    def clock(self) -> dict[str, Any]:
        day, of_day = divmod(self.step, self.steps_per_day)
        return {"step": self.step, "day": day, "step_of_day": of_day,
                "market_open": of_day != 0,
                "days_closed": day}

    def days_touched(self) -> int:
        """Trading days the session has run into, the one in progress
        included, and at least one."""
        return max(1, -(-self.step // self.steps_per_day))

    # -- checkpoints ------------------------------------------------------

    def keep(self) -> None:
        """Keep the present state as the checkpoint at this step."""
        self.checkpoints[self.step] = (self.engine.state_snapshot(),
                                       _copy_live(self.live))
        if len(self.checkpoints) > MAX_CHECKPOINTS:
            ordered = sorted(self.checkpoints)
            self.checkpoints.pop(ordered[1])

    def restore(self, step: int) -> None:
        """Put the checkpoint at `step` back, on a fresh engine, and drop
        every checkpoint after it: they were a future of this line."""
        snapshot, live = self.checkpoints[step]
        engine = self._fresh_engine(to_restore=True)
        engine.restore_state(snapshot)
        self.engine = engine
        self.live = _copy_live(live)
        for later in [s for s in self.checkpoints if s > step]:
            del self.checkpoints[later]

    def forked(self, sid: str) -> "_Session":
        """A copy of this session under a new id, from its present state.

        The engine goes through a snapshot and a restore, as a rewind does.
        The checkpoints are shared: nothing writes to one once it is kept,
        and a restore copies what it reads.
        """
        child = copy.copy(self)
        child.id = sid
        child.engine = self._fresh_engine(to_restore=True)
        child.engine.restore_state(self.engine.state_snapshot())
        child.live = _copy_live(self.live)
        child.checkpoints = dict(self.checkpoints)
        child.lineage = [*self.lineage,
                         {"forked_at_step": self.step}]
        child.lock = threading.Lock()
        child.touched = time.monotonic()
        return child

    # -- running ----------------------------------------------------------

    def _merged_flow(self) -> dict[str, tuple[float, float]]:
        """Every portfolio's flow for the session, summed per ticker in
        label order. One portfolio passes its own mapping, as `World`
        does, so a one-agent session hands the engine what `evaluate`
        hands it."""
        books = self.live["portfolios"]
        if len(books) == 1:
            return next(iter(books.values())).pending_flow()
        merged: dict[str, tuple[float, float]] = {}
        for label in self.labels:
            for ticker, (buy, sell) in books[label].pending_flow().items():
                have = merged.get(ticker)
                merged[ticker] = ((buy, sell) if have is None
                                  else (have[0] + buy, have[1] + sell))
        return merged

    def _ask(self, label: str, obs: Any, guard: Any) -> list[tuple[Any, Any]]:
        """A strategy agent's orders for this step, as `evaluate` asks."""
        ledger = self.live["ledger"][label]
        orders = None
        try:
            with guard:
                orders = self.live["agents"][label].act(obs)
        except Exception as exc:                      # noqa: BLE001
            ledger["errors"].append(
                f"step {obs.step}: {type(exc).__name__}: {exc}")
        if guard.tampered:
            ledger["tampered"] = True
            ledger["errors"].append(f"step {obs.step}: tampered: "
                                    f"{guard.what}")
        try:
            return order_items(orders)
        except tf.ValidationError as exc:
            ledger["errors"].append(f"step {obs.step}: {exc}")
            return []

    def _execute(self, label: str, entries: list[tuple[Any, Any]],
                 step: int) -> list[str]:
        """Send one agent's orders, as `evaluate` sends them. Returns the
        refusals, each naming the ticker."""
        refused: list[str] = []
        portfolio = self.live["portfolios"][label]
        ledger = self.live["ledger"][label]
        for ticker, value in entries:
            try:
                order = check_order(ticker, value)
                if order is None:
                    continue
                if isinstance(order, tf.Cancel):
                    portfolio.cancel(self.engine, ticker=ticker)
                    continue
                if isinstance(order, tf.Limit):
                    # A new limit on a name replaces the one waiting there.
                    portfolio.cancel(self.engine, ticker=ticker)
                    report = portfolio.submit_limit(
                        self.engine, ticker, order.quantity, order.price)
                    if report["filled"] > 0:
                        ledger["trades"] += 1
                    continue
                portfolio.execute(self.engine, ticker, order)
                ledger["trades"] += 1
            except (OrderError, tf.ValidationError) as exc:
                ledger["rejected"] += 1
                refused.append(f"step {step} {ticker}: {exc}")
        return refused

    def advance(self, steps: int, hand_orders: dict[str, dict[str, Any]]
                ) -> dict[str, list[str]]:
        """Run `steps` decision points. `hand_orders` are sent at the first.

        Returns each agent's refusals over the call.
        """
        live = self.live
        books = live["portfolios"]
        spd = self.steps_per_day
        refused: dict[str, list[str]] = {label: [] for label in self.labels}
        for i in range(steps):
            engine = self.engine
            step = live["step"]
            day, of_day = divmod(step, spd)
            if of_day == 0:
                engine.open_market()
            prices = _f64(engine.prices())
            observed: dict[str, Any] = {}
            for label in self.labels:
                if label in self.specs:
                    agent = live["agents"][label]
                    observed[label] = Observation(
                        step, day, list(self.tickers), list(prices),
                        PortfolioView(books[label], engine),
                        MarketView(engine), self.adv, spd,
                        hidden=(HiddenState(engine)
                                if declares_hidden_state(agent) else None),
                        history=live["history"])
                books[label].stamp(day, step, of_day * _TICKS_PER_STEP)
            guard = TamperGuard(engine, books.values(), trusted=False)
            asked: dict[str, list[tuple[Any, Any]]] = {}
            for label in self.labels:
                if label in self.specs:
                    asked[label] = self._ask(label, observed[label], guard)
                else:
                    sent = hand_orders.get(label) if i == 0 else None
                    asked[label] = list((sent or {}).items())
            for label in self.labels:
                refused[label] += self._execute(label, asked[label], step)
            engine.run_session(
                *session_clock(_SESSION_START, of_day, _TICKS_PER_STEP),
                _TICKS_PER_STEP, fills=self._merged_flow())
            for book in books.values():
                book.clear_flow()
            for label in self.labels:
                taken = books[label].sync(engine)
                live["ledger"][label]["trades"] += len(taken)
            live["step"] = step + 1
            if of_day == spd - 1:
                for book in books.values():
                    book.accrue(engine)
                live["history"]._close(engine, day)
                engine.close_market()
        return refused


def _expire_sessions() -> None:
    """Close every session idle past `SESSION_IDLE_SECONDS`. A session a
    call is using right now is never closed under it."""
    now = time.monotonic()
    with _sessions_lock:
        for sid, sess in list(_sessions.items()):
            if now - sess.touched <= SESSION_IDLE_SECONDS:
                continue
            if sess.lock.acquire(blocking=False):
                try:
                    _sessions.pop(sid, None)
                finally:
                    sess.lock.release()


def _session(sid: Any) -> tuple["_Session | None", dict[str, Any] | None]:
    """The session under `sid`, or a refusal saying why there is none."""
    _expire_sessions()
    with _sessions_lock:
        sess = _sessions.get(sid) if isinstance(sid, str) else None
        held = sorted(_sessions)
    if sess is None:
        return None, _fail(
            f"no session {sid!r}. Open sessions: {held or 'none'}. A session "
            f"is closed by close_session, after {SESSION_IDLE_SECONDS // 60} "
            f"minutes with no call, or when the server restarts: sessions "
            f"live in this server process only.")
    return sess, None


def _closed_under(sess: "_Session") -> dict[str, Any] | None:
    """A refusal when `sess` was closed while this call waited for its
    lock, else None. Call holding the session's lock."""
    with _sessions_lock:
        if _sessions.get(sess.id) is sess:
            return None
    return _fail(f"session {sess.id!r} was closed while this call waited "
                 f"for it.")


@functools.lru_cache(maxsize=256)
def _house_label(label: str) -> str | None:
    """The engine's refusal when `label` is one of its book's own owners,
    else None.

    Read from the engine by placing one order on a two-name market, as
    `_macro_fields` reads the macro fields, so the list is the engine's and
    not a copy of it. A session agent under such a name would have every
    order refused.
    """
    probe = tf.Engine(seed=0, universe=tf.Universe.random(2, seed=0))
    probe.open_market()
    try:
        probe.submit(label, probe.tickers[0], 1.0)
    except OrderError as exc:
        if "cannot place orders" in str(exc):
            return str(exc)
    return None


def _unguessable_id(prefix: str, taken: Any) -> str:
    """A fresh id that no other id predicts.

    Random rather than counted, so a host that serves several clients from
    one process cannot have one client step, read or close another's
    session, or collect another's job, by guessing the next number. No
    result depends on an id: provenance records how a session was opened
    and what was sent to it, never which id it had.
    """
    while True:
        new = f"{prefix}-{secrets.token_urlsafe(12)}"
        if new not in taken:
            return new


def _new_session_id() -> str:
    """Call under the registry lock."""
    return _unguessable_id("session", _sessions)


def _session_capacity() -> dict[str, Any] | None:
    """A refusal when no other session fits, else None. Call under the
    registry lock."""
    if len(_sessions) >= MAX_SESSIONS:
        return _fail(
            f"{len(_sessions)} sessions are open (cap {MAX_SESSIONS}): "
            f"{sorted(_sessions)}. Close one with close_session first. Each "
            f"holds a market and up to {MAX_CHECKPOINTS} checkpoints in this "
            f"server's memory.")
    return None


#: The order grammar's word for a `tf.Cancel`.
_CANCEL = "cancel"


def _orders_from(orders: Any, tickers: list[str]
                 ) -> tuple[dict[str, Any], dict[str, Any]]:
    """The wire form of one agent's orders, as an `act()` mapping and as
    the canonical form a result echoes. Raises ValueError naming the entry.

    The grammar is the harness's: a number is a market order of that many
    shares, positive to buy; {"quantity": q, "limit_price": p} is a
    `tf.Limit`; "cancel" is a `tf.Cancel`. Checked whole before anything
    runs, so a step never advances on half an instruction.
    """
    if not isinstance(orders, dict):
        raise ValueError(
            f"orders must be an object of ticker to order, for example "
            f"{{\"{tickers[0]}\": 100}}, got {type(orders).__name__}")
    mapping: dict[str, Any] = {}
    echo: dict[str, Any] = {}
    for ticker, value in orders.items():
        if ticker not in tickers:
            raise ValueError(_unknown_ticker(ticker, tickers)
                             .replace("obs.tickers", "session_state"))
        if isinstance(value, str) and value.strip().lower() == _CANCEL:
            mapping[ticker], echo[ticker] = tf.Cancel(), _CANCEL
            continue
        if isinstance(value, dict):
            unknown = sorted(set(value) - {"quantity", "limit_price"})
            if unknown or not {"quantity", "limit_price"} <= set(value):
                raise ValueError(
                    f"order for {ticker!r}: a limit order is "
                    f"{{\"quantity\": shares, \"limit_price\": price}}, got "
                    f"{value!r}")
            try:
                mapping[ticker] = tf.Limit(value["quantity"],
                                           value["limit_price"])
            except (tf.ValidationError, TypeError) as exc:
                raise ValueError(f"order for {ticker!r}: {exc}") from exc
            echo[ticker] = {"quantity": mapping[ticker].quantity,
                            "limit_price": mapping[ticker].price}
            continue
        try:
            quantity = check_order(ticker, value)
        except tf.ValidationError as exc:
            raise ValueError(
                f"{exc}. An order is a signed share count, "
                f"{{\"quantity\": q, \"limit_price\": p}} or \"cancel\"."
            ) from exc
        if quantity is not None:
            mapping[ticker] = echo[ticker] = quantity
    return mapping, echo


def _rounded(value: float, places: int) -> float | None:
    """`value` rounded, or None for an infinity or NaN, which JSON lacks."""
    return round(value, places) if math.isfinite(value) else None


def _portfolio_view(view: Any) -> dict[str, Any]:
    """One agent's book as a result shows it, read through `PortfolioView`."""
    return {
        "cash": round(view.cash, 2),
        "net_worth": round(view.net_worth(), 2),
        "pnl": round(view.pnl(), 2),
        "leverage": _rounded(view.leverage(), 4),
        "positions": {
            ticker: {"quantity": held.quantity,
                     "avg_cost": round(held.avg_cost, 4)}
            for ticker, held in sorted(view.positions.items())
            if held.quantity != 0},
        "open_orders": [
            {k: order[k] for k in ("order_id", "ticker", "side",
                                   "limit_price", "quantity", "remaining")
             if k in order}
            for order in view.open_orders()],
    }


def _prices(view: Any) -> dict[str, float]:
    return {t: round(p, 4) for t, p in zip(view.tickers, _f64(view.prices()))}


def _session_caveats(sess: "_Session") -> list[str]:
    """The caveats a session's results carry, computed for its state now."""
    out = _caveats(
        days=sess.days_touched(), n_seeds=1,
        signals=_signals_in(sess.specs), max_leverage=sess.max_leverage,
        universe_size=len(sess.roster),
        sector_concentrated=sess.concentrated, preset=sess.preset,
        population=sess.population)
    history = []
    for event in sess.lineage:
        if "forked_at_step" in event:
            history.append(f"was forked from another session at step "
                           f"{event['forked_at_step']}")
        else:
            history.append(f"was rewound from step {event['rewound_from']} "
                           f"to step {event['to_step']}")
    out.insert(1 + (sess.preset is not None) + (sess.population is not None), (
        "SESSION: this market can be forked, rewound and reopened from the "
        "same seed, so whoever drives it can have seen its future. Its P&L "
        "measures decisions made with that chance open, and it is not a "
        "strategy's score: score a strategy with evaluate_strategies or "
        "rank_strategies, where a strategy is data and sees only the "
        "present."
        + (f" This session {'; it '.join(history)}." if history else "")))
    return out


def _session_provenance(sess: "_Session", *, orders: bool) -> dict[str, Any]:
    """What re-runs this session: how it was opened, and with `orders`
    every order the caller sent on this line, by step."""
    extra: dict[str, Any] = {
        "seed": sess.seed,
        "universe": sess.uni_doc,
        "universe_fingerprint": tf.universe_util.fingerprint_of(sess.roster),
        "steps_per_day": sess.steps_per_day,
        "cash": sess.cash,
        "max_leverage": sess.max_leverage,
        "agents": {label: (sess.specs[label].fingerprint
                           if label in sess.specs else "orders")
                   for label in sess.labels},
        "lineage": list(sess.lineage),
    }
    if orders:
        extra["orders"] = copy.deepcopy(sess.live["orders"])
    else:
        extra["orders_sent"] = len(sess.live["orders"])
    return _provenance(sess.preset, sess.population, **extra)


def _session_view(sess: "_Session", tickers: list[str] | None = None
                  ) -> dict[str, Any]:
    """What the caller may see of a session: the market view and its own
    portfolios, never the engine."""
    view = MarketView(sess.engine)
    market: dict[str, Any] = {
        "prices": _prices(view),
        "macro": view.macro_fields,
        "news_today": view.news(),
    }
    if tickers:
        detail = {}
        columns = {field: _f64(view.column(field))
                   for field in ("previous_close", "open", "high", "low",
                                 "volume")}
        for ticker in tickers:
            i = view.index_of(ticker)
            book = view.book(ticker)
            detail[ticker] = {
                **{field: round(col[i], 4) for field, col in columns.items()},
                "best_bid": book.best_bid, "best_ask": book.best_ask,
                "spread": book.spread,
            }
        market["detail"] = detail
    agents = {}
    for label in sess.labels:
        ledger = sess.live["ledger"][label]
        agents[label] = {
            "driven_by": "strategy" if label in sess.specs else "orders",
            **_portfolio_view(PortfolioView(sess.live["portfolios"][label],
                                            sess.engine)),
            "trades": ledger["trades"],
            "rejected": ledger["rejected"],
            **({"errors": list(ledger["errors"][-10:])}
               if ledger["errors"] else {}),
            **({"tampered": True} if ledger["tampered"] else {}),
            **({"uses_hidden_state": True}
               if label in sess.specs and declares_hidden_state(
                   sess.live["agents"][label]) else {}),
        }
    return {"clock": sess.clock(), "market": market, "agents": agents,
            "checkpoints": sorted(sess.checkpoints)}


SessionIdArg = Annotated[str, Field(description=(
    "A session id exactly as open_session or session_fork returned it."))]


@server.tool(
    title='Open a market session',
    description=(
        "Open a simulated market that this server keeps between calls, and "
        "get a session id back. Use a session when you want to decide each "
        "step yourself and see what happened before the next: send orders "
        "with session_step, read with session_state, try two actions from "
        "one state with session_fork, and go back with session_rewind. To "
        "score a strategy you can write as a spec, use evaluate_strategies "
        "or rank_strategies instead. `agents` names the traders: null for "
        "one you drive with orders, or a strategy spec for one that trades "
        "itself each step. `preset` and `population` choose the market as "
        "they do for evaluate_strategies. Opening runs no market. At most "
        f"{MAX_SESSIONS} sessions are open at once, in this server's memory "
        f"only, and one idle for {SESSION_IDLE_SECONDS // 60} minutes is "
        "closed."),
    annotations=_STARTS_JOB,
)
@_guarded
def open_session(
    agents: Annotated[dict[str, dict[str, Any] | None] | None, Field(
        description=(
            f"The traders in this market, keyed by a name you choose. null "
            f"is one you drive with orders in session_step; a strategy spec, "
            f"for example {_SPEC_EXAMPLE}, trades by itself each step. At "
            f"most {MAX_SESSION_AGENTS}. All of them trade one market and "
            f"move each other's prices. Omit for one agent called \"me\"."))
    ] = None,
    seed: SeedArg = 7,
    universe_size: UniverseSizeArg = 40,
    universe_seed: UniverseSeedArg = 111,
    universe_sectors: SectorsArg = None,
    universe: UniverseArg = None,
    preset: PresetArg = None,
    population: PopulationArg = None,
    steps_per_day: Annotated[int, Field(description=(
        f"Decision points per trading day, 1 to {MAX_STEPS_PER_DAY}. A step "
        f"is 65 minutes, so {DEFAULT_STEPS_PER_DAY} cover the trading "
        f"session."))] = DEFAULT_STEPS_PER_DAY,
    cash: CashArg = 1_000_000.0,
    max_leverage: LeverageArg = 2.0,
) -> dict[str, Any]:
    """Open a session. See the sessions comment above for the design."""
    if (refused := _seed_refusal(seed=seed)) is not None:
        return refused
    preset, refusal = _preset_choice(preset)
    if refusal is not None:
        return _fail(refusal)
    pop, refusal = _population_choice(population)
    if refusal is not None:
        return _fail(refusal)
    if not 1 <= steps_per_day <= MAX_STEPS_PER_DAY:
        return _fail(f"steps_per_day must be 1..{MAX_STEPS_PER_DAY}, got "
                     f"{steps_per_day}")
    agents = {"me": None} if agents is None else agents
    if not isinstance(agents, dict) or not agents:
        return _fail("agents must name at least one trader, for example "
                     "{\"me\": null}")
    if len(agents) > MAX_SESSION_AGENTS:
        return _fail(f"at most {MAX_SESSION_AGENTS} agents in a session, got "
                     f"{len(agents)}")
    bad = [name for name in agents
           if not isinstance(name, str) or not name.strip() or len(name) > 64]
    if bad:
        return _fail(f"agent names are non-empty strings of at most 64 "
                     f"characters, got {bad}")
    for name in agents:
        if (house := _house_label(name)) is not None:
            return _fail(f"agent name {name!r}: {house}. Choose another.")
    hand = [name for name, spec in agents.items() if spec is None]
    try:
        specs, _assumed = (_specs_from({k: v for k, v in agents.items()
                                        if v is not None})
                           if len(hand) < len(agents) else ({}, []))
        roster, concentrated, uni_doc = _resolve_universe(
            universe or {"size": universe_size, "seed": universe_seed,
                         "sectors": universe_sectors})
        # The portfolio's own checks on cash and the leverage cap, before a
        # session is registered.
        tf.Portfolio(cash=cash, max_leverage=max_leverage)
    except (ValueError, tf.ValidationError) as exc:
        return _fail(str(exc))

    _expire_sessions()
    with _sessions_lock:
        if (refused := _session_capacity()) is not None:
            return refused
        sid = _new_session_id()
        sess = _Session(sid, seed=seed, roster=roster, uni_doc=uni_doc,
                        concentrated=concentrated, preset=preset,
                        steps_per_day=steps_per_day, cash=float(cash),
                        max_leverage=max_leverage, specs=specs, hand=hand,
                        population=pop)
        _sessions[sid] = sess
    return {
        "ok": True,
        "session_id": sid,
        **_session_view(sess),
        "note": (
            "Advance with session_step, which sends orders for one agent at "
            "the first step it runs. An order is a signed share count "
            "(positive buys), {\"quantity\": q, \"limit_price\": p} for a "
            "limit order, or \"cancel\". A checkpoint is kept at the open "
            "and after every session_step, and session_rewind returns to "
            "one. Close the session with close_session when done."),
        "limits": {
            "max_sessions": MAX_SESSIONS,
            "max_checkpoints": MAX_CHECKPOINTS,
            "idle_seconds": SESSION_IDLE_SECONDS,
            "max_days": MAX_SESSION_DAYS,
            "max_steps_per_call": MAX_SESSION_STEPS,
        },
        "caveats": _session_caveats(sess),
        "provenance": _session_provenance(sess, orders=False),
    }


@server.tool(
    title='Advance a market session',
    description=(
        "Advance an open session by a number of steps or days, optionally "
        "sending orders for one of its agents at the first step, and get "
        "back the fills, each agent's portfolio, the prices and what "
        "changed. This is the one session tool that trades. An order is a "
        "signed share count (positive buys), {\"quantity\": q, "
        "\"limit_price\": p} for a limit order, or \"cancel\"; a malformed "
        "order is refused before anything runs, and one the market refuses "
        "(the leverage cap, a book that cannot fill) is listed and the rest "
        f"trade. At most {MAX_SESSION_STEPS} steps a call and "
        f"{MAX_SESSION_DAYS} days a session; a step costs about a sixth of "
        "a day of evaluate_strategies for one entrant. Keeps a checkpoint "
        "at the step it ends on. Deterministic: the same calls from the "
        "same open give the same result."),
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                                idempotentHint=False, openWorldHint=False),
)
@_guarded
def session_step(
    session_id: SessionIdArg,
    steps: Annotated[int | None, Field(description=(
        "Decision points to advance, 1 or more. Give steps or days, not "
        "both; with neither, one step."))] = None,
    days: Annotated[int | None, Field(description=(
        "Advance through this many market closes, the day in progress "
        "counting as the first. Give steps or days, not both."))] = None,
    agent: Annotated[str | None, Field(description=(
        "The agent the orders are for, one opened with null. May be "
        "omitted when the session has exactly one such agent."))] = None,
    orders: Annotated[dict[str, Any] | None, Field(description=(
        "Orders sent at the first step this call runs, keyed by ticker: a "
        "signed share count for a market order (positive buys), "
        "{\"quantity\": q, \"limit_price\": p} for a limit order that waits "
        "in the book, or \"cancel\" to withdraw the agent's waiting orders "
        "on that ticker."))] = None,
) -> dict[str, Any]:
    """Step a session, with the caller's orders at the first step."""
    sess, refused = _session(session_id)
    if refused is not None:
        return refused
    with sess.lock:
        if (refused := _closed_under(sess)) is not None:
            return refused
        sess.touched = time.monotonic()
        if steps is not None and days is not None:
            return _fail("give steps or days, not both")
        spd = sess.steps_per_day
        if days is not None:
            if days < 1:
                return _fail(f"days must be 1 or more, got {days}")
            n = (spd - sess.step % spd) + (days - 1) * spd
        else:
            n = 1 if steps is None else steps
            if n < 1:
                return _fail(f"steps must be 1 or more, got {n}")
        if n > MAX_SESSION_STEPS:
            return _fail(
                f"{n} steps is more than the {MAX_SESSION_STEPS} one call may "
                f"run ({MAX_DAYS} days at {DEFAULT_STEPS_PER_DAY} steps). "
                f"Step in parts.")
        if -(-(sess.step + n) // spd) > MAX_SESSION_DAYS:
            return _fail(
                f"a session runs at most {MAX_SESSION_DAYS} days, and "
                f"{n} more steps from step {sess.step} would pass it. Past "
                f"{MAX_SESSION_DAYS} days no band in the envelope grades a "
                f"result.")
        hand_orders: dict[str, dict[str, Any]] = {}
        echo: dict[str, Any] = {}
        if orders:
            if agent is None:
                if len(sess.hand) != 1:
                    return _fail(
                        f"name the agent the orders are for: this session's "
                        f"agents driven by orders are {sess.hand or 'none'}")
                agent = sess.hand[0]
            if agent not in sess.labels:
                return _fail(f"no agent {agent!r} in this session; it holds "
                             f"{sess.labels}")
            if agent in sess.specs:
                return _fail(
                    f"{agent!r} trades by its strategy spec, so it takes no "
                    f"orders. The agents driven by orders are "
                    f"{sess.hand or 'none'}; open a session with an agent "
                    f"set to null to trade by hand.")
            try:
                hand_orders[agent], echo = _orders_from(orders, sess.tickers)
            except ValueError as exc:
                return _fail(str(exc))
            if echo:
                sess.live["orders"].append(
                    {"step": sess.step, "agent": agent, "orders": echo})
        elif agent is not None and agent not in sess.labels:
            return _fail(f"no agent {agent!r} in this session; it holds "
                         f"{sess.labels}")

        view = MarketView(sess.engine)
        books = sess.live["portfolios"]
        before = {
            "clock": sess.clock(),
            "prices": _f64(view.prices()),
            "macro": view.macro_fields,
            "worth": {label: books[label].net_worth(sess.engine)
                      for label in sess.labels},
            "fills": {label: len(books[label].fills) for label in sess.labels},
        }
        refusals = sess.advance(n, hand_orders)
        sess.keep()

        view = MarketView(sess.engine)
        after = _f64(view.prices())
        moves = [(t, a, b) for t, a, b in zip(view.tickers, before["prices"],
                                              after) if a > 0]
        moves.sort(key=lambda m: (-abs(m[2] / m[1] - 1.0), m[0]))
        macro_now = view.macro_fields
        state = _session_view(sess)
        books = sess.live["portfolios"]
        for label in sess.labels:
            new = books[label].fills[before["fills"][label]:]
            entry = state["agents"][label]
            entry["net_worth_change"] = round(
                books[label].net_worth(sess.engine)
                - before["worth"][label], 2)
            entry["fills"] = [
                {k: f[k] for k in ("ticker", "quantity", "price", "notional",
                                   "partial", "day", "step", "order_id",
                                   "liquidity") if k in f}
                for f in new[:MAX_FILLS_SHOWN]]
            entry["fills_this_call"] = len(new)
            entry["refused"] = refusals[label]
            if label == agent and echo:
                entry["orders_sent"] = echo
        return {
            "ok": True,
            "session_id": sess.id,
            "from": before["clock"],
            **state,
            "changed": {
                "steps_run": n,
                "movers": [{"ticker": t, "from": round(a, 4),
                            "to": round(b, 4),
                            "change_pct": round((b / a - 1.0) * 100.0, 4)}
                           for t, a, b in moves[:MOVERS_SHOWN]],
                "macro": {k: {"from": before["macro"][k], "to": v}
                          for k, v in macro_now.items()
                          if before["macro"].get(k) != v},
            },
            "checkpoint": sess.step,
            "caveats": _session_caveats(sess),
            "provenance": _session_provenance(sess, orders=False),
        }


@server.tool(
    title='Read a market session',
    description=(
        "Read an open session without changing it: the clock, every price, "
        "the published macro figures, today's news by name, each agent's "
        "portfolio and open orders, and the checkpoints session_rewind can "
        "return to. Name tickers to add each one's open, high, low, volume "
        "and best bid and ask. It shows what a trader could see and nothing "
        "of the simulator's own state. Omit session_id to list the open "
        "sessions. Runs no market."),
    annotations=_READ_ONLY,
)
@_guarded
def session_state(
    session_id: Annotated[str | None, Field(description=(
        "A session id from open_session or session_fork. Omit to list the "
        "sessions this server holds."))] = None,
    tickers: Annotated[list[str] | None, Field(description=(
        "Tickers to show in detail: the day's open, high, low and volume, "
        "the previous close, and the book's best bid and ask."))] = None,
) -> dict[str, Any]:
    """The session as a trader may see it, or the list of sessions."""
    if session_id is None:
        _expire_sessions()
        with _sessions_lock:
            held = sorted(_sessions.values(), key=lambda s: s.id)
        now = time.monotonic()
        return {
            "ok": True,
            "sessions": [
                {"session_id": s.id, "step": s.step, "agents": s.labels,
                 "preset": s.preset or tf.model_preset()["name"],
                 **({} if s.population is None
                    else {"population": s.population.name}),
                 "idle_seconds": round(now - s.touched, 1)}
                for s in held],
            "note": (f"Sessions live in this server process only. At most "
                     f"{MAX_SESSIONS} are open at once, and one idle for "
                     f"{SESSION_IDLE_SECONDS // 60} minutes is closed."),
            "provenance": _provenance(),
        }
    sess, refused = _session(session_id)
    if refused is not None:
        return refused
    with sess.lock:
        if (refused := _closed_under(sess)) is not None:
            return refused
        sess.touched = time.monotonic()
        unknown = [t for t in (tickers or []) if t not in sess.tickers]
        if unknown:
            return _fail(_unknown_ticker(unknown[0], sess.tickers)
                         .replace("obs.tickers", "the prices"))
        return {
            "ok": True,
            "session_id": sess.id,
            **_session_view(sess, tickers),
            "caveats": _session_caveats(sess),
            "provenance": _session_provenance(sess, orders=True),
        }


@server.tool(
    title='Fork a market session',
    description=(
        "Copy an open session, at its present state, into a new session "
        "with its own id, so you can try two different actions from the "
        "same market and compare them. The copy continues exactly as the "
        "original would: the same steps and orders give the same prices "
        "in both. The original is unchanged. Counts against the "
        f"{MAX_SESSIONS}-session cap; runs no market."),
    annotations=_STARTS_JOB,
)
@_guarded
def session_fork(session_id: SessionIdArg) -> dict[str, Any]:
    """Two futures from one state, through a snapshot and a restore."""
    sess, refused = _session(session_id)
    if refused is not None:
        return refused
    with sess.lock:
        if (refused := _closed_under(sess)) is not None:
            return refused
        sess.touched = time.monotonic()
        with _sessions_lock:
            if (refused := _session_capacity()) is not None:
                return refused
            child = sess.forked(_new_session_id())
            _sessions[child.id] = child
    return {
        "ok": True,
        "session_id": child.id,
        "forked_from": sess.id,
        **_session_view(child),
        "caveats": _session_caveats(child),
        "provenance": _session_provenance(child, orders=False),
    }


@server.tool(
    title='Rewind a market session',
    description=(
        "Put an open session back to a checkpoint it kept, by step, and "
        "drop every checkpoint after it. Checkpoints are kept at the open "
        "and at the end of every session_step call; session_state lists "
        f"them, and a session keeps at most {MAX_CHECKPOINTS}. To keep the "
        "later state as well, session_fork first. Returns the session as "
        "session_state does. Runs no market."),
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True,
                                idempotentHint=True, openWorldHint=False),
)
@_guarded
def session_rewind(
    session_id: SessionIdArg,
    step: Annotated[int, Field(description=(
        "The step to return to, one of the session's checkpoints."))],
) -> dict[str, Any]:
    """Back to an earlier state of this line."""
    sess, refused = _session(session_id)
    if refused is not None:
        return refused
    with sess.lock:
        if (refused := _closed_under(sess)) is not None:
            return refused
        sess.touched = time.monotonic()
        if step not in sess.checkpoints:
            return _fail(
                f"no checkpoint at step {step}. This session keeps "
                f"{sorted(sess.checkpoints)}: the open, the end of each "
                f"session_step call, and at most {MAX_CHECKPOINTS} in all.")
        if step != sess.step:
            sess.lineage.append({"rewound_from": sess.step, "to_step": step})
            sess.restore(step)
        return {
            "ok": True,
            "session_id": sess.id,
            **_session_view(sess),
            "caveats": _session_caveats(sess),
            "provenance": _session_provenance(sess, orders=False),
        }


@server.tool(
    title='Close a market session',
    description=(
        "Close an open session and free its memory, returning each agent's "
        "final portfolio and every order sent on the session's line, which "
        "is what re-runs it from open_session. The session and its "
        "checkpoints are gone afterwards. Runs no market."),
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True,
                                idempotentHint=True, openWorldHint=False),
)
@_guarded
def close_session(session_id: SessionIdArg) -> dict[str, Any]:
    """Free a session, with its final state."""
    sess, refused = _session(session_id)
    if refused is not None:
        return refused
    with sess.lock:
        if (refused := _closed_under(sess)) is not None:
            return refused
        out = {
            "ok": True,
            "session_id": sess.id,
            "closed": True,
            **_session_view(sess),
            "caveats": _session_caveats(sess),
            "provenance": _session_provenance(sess, orders=True),
        }
        with _sessions_lock:
            _sessions.pop(sess.id, None)
    return out


def main() -> None:
    """Entry point for `tradefloor-mcp`. Speaks MCP over stdio."""
    server.run("stdio")


if __name__ == "__main__":  # pragma: no cover
    main()
