"""Run a FinRobot agent inside a Tradefloor market, and record what it did.

FinRobot is an open-source financial-AI agent platform from the AI4Finance
Foundation (https://github.com/AI4Finance-Foundation/FinRobot, Apache-2.0).
This module is a Tradefloor integration for FinRobot. There is no affiliation
with or endorsement by AI4Finance, and nothing here is an official FinRobot
interface.

    Tradefloor observation
            |
      FinRobotAdapter        allowlist -> text
            |
        FinRobot             the real SingleAssistant, over autogen
            |
      FinRobotAdapter        parse -> validate -> share deltas
            |
    Tradefloor execution

## Ownership

FinRobot owns interpretation, the portfolio decision and a one-line
rationale. Tradefloor owns the market, the macro path, execution, the order
book, fills, accounting, checkpoints, forks, interventions and the
comparison.

FinRobot never mutates engine state. It returns JSON, and
:func:`orders_from` turns validated JSON into the share deltas
:class:`~tradefloor.counterfactual.World` executes. Every path from a model
response to the engine runs through :func:`parse` and :func:`orders_from`.

## The observation allowlist

:class:`~tradefloor.harness.Observation` carries ``.engine``, and the engine
knows the answer key: :func:`tradefloor.fair_value`, the nine-way factor
:meth:`~tradefloor.Engine.attribution` of every price move, each company's
``mispricing_s``, and -- through a :class:`~tradefloor.Scenario` -- the macro
path the run has not reached yet. An agent reading any of those inverts the
simulator, and the experiment around it measures nothing.

So :func:`observe` names every field it emits, one at a time, and reads
nothing by reflection. Adding a field takes a deliberate edit here. A
denylist would go stale the first time the engine gained an attribute; an
allowlist survives that.

``OBSERVABLE_MACRO`` is the macro half of the list, bound to
``counterfactual.MACRO_FIELDS`` on purpose: the library has already settled
which macro fields a run is ABOUT, and that set leaves out ``qe_pe_boost``, a
model coefficient no exchange publishes. ``tests/test_finrobot.py`` runs the
mapping against an engine proxy that raises on the forbidden attributes, so a
future edit reaching for one fails on the access.

Company fundamentals -- sector, EPS, book value, revenue growth, beta -- do
not come off the engine either. The caller supplies them as ``fundamentals``.
An analyst reads all five off a filing, and keeping them out of the adapter
leaves one less line to audit.

What that does NOT do is withhold the valuation, and an earlier version of
this docstring claimed it did. :func:`tradefloor.fair_value` is public, and
its arguments are ``sector``, ``eps``, ``book_value_per_share`` and
``revenue_growth`` -- the four fields the caller is invited to supply --
plus ``federal_funds_rate`` and ``corporate_bond_yield``, both in
``OBSERVABLE_MACRO``. So a caller who supplies full fundamentals has supplied
the means to reconstruct the model's own anchor, and no engine attribute is
read to do it -- the ``Sealed`` proxy in ``tests/test_finrobot.py`` cannot
see it happen, and neither can any allowlist of engine reads.

Two different claims sit here and they are worth separating, because three
people measured this and produced three numbers by conflating them.

``fair_value`` reconstructs EXACTLY. It is a pure function of six inputs,
four supplied and two observable, so there is nothing approximate about it.

``mispricing_s`` is closely APPROXIMABLE and not recoverable. The engine
applies it as ``fair_value * exp(s)``, so the inversion is
``log(price / fair_value)`` and not ``price / fair_value - 1``; the ratio
form is simply the wrong arithmetic and every figure derived from it was an
artefact. Even the right inversion lands near rather than on, because a
traded price carries microstructure on top of the anchor. How near depends
on the roster and the moment, which is why no distance is quoted here. The
tolerance lives in
``test_the_valuation_is_reconstructible_from_what_the_caller_supplies``,
where it can be re-derived, rather than in prose where it would rot.

The boundary is unchanged by this: a native agent reads ``mispricing_s``
straight off the engine without reconstructing anything, and the allowlist
still stops that. What is true is narrower and worth stating plainly.
Supplying fundamentals is the caller's decision about their own experiment,
this adapter only declines to make it for them, and the shipped example --
which does supply all five -- is trading that ground-truth distance for a
roster an analyst could reason about.

## Which FinRobot abstraction

:class:`finrobot.agents.workflow.SingleAssistant`, FinRobot's supported
single-agent entry point, driven through the real ``autogen`` chat plumbing.
It assembles a ``finrobot.agents.workflow.FinRobot`` assistant -- an
``autogen.AssistantAgent`` subclass, including FinRobot's own role-prompt
preprocessing -- opposite an ``autogen.UserProxyAgent``.

Two constructor arguments matter:

- ``toolkits=[]``. Every FinRobot library role carrying toolkits carries ones
  that fetch REAL market data: FinnHub company news, Yahoo Finance prices,
  SEC filings. Those describe a different world from the simulated one. An
  agent given them reasons about securities it is not trading, and the roster
  here is synthetic down to its tickers.
- ``code_execution_config=False``. The ``SingleAssistant`` default gives the
  user proxy a working directory and lets it run model-authored code. This
  integration needs one JSON object.

``MultiAssistant`` and ``MultiAssistantWithLeader`` were the alternative.
Both are group chats, and their value is division of labour across a research
report. A portfolio decision every simulated day is one question to one role.
A group chat multiplies the cost per decision by the number of participants
and measures the same thing.

FinRobot ships no structured-output mechanism: no Pydantic response model, no
schema binding. So the contract is a JSON object requested in the mandate and
validated here. :func:`parse` is strict and total. Anything it cannot turn
into a well-formed :class:`Decision` raises :class:`DecisionError`, and the
caller decides whether that ends the run or costs the agent a step.

## Replay

A live decision costs money, and running one twice gives two answers.
Tradefloor's market is deterministic; an LLM behind an API is not. The
adapter has two modes over one code path. ``mode="live"`` calls FinRobot and,
with a recorder attached, writes every interaction to a :class:`Transcript`.
``mode="replay"`` reads that transcript back, keyed by the SHA-256 of the
exact text FinRobot was sent.

The key is a hash of the input. Change the observation mapping and the digest
changes, the key goes missing, and the replay RAISES naming the step. Keyed
by (arm, step) it would answer the new question with a response given to the
old one.

What a key over the input cannot see is a change to something that never
enters it. The mandate reaches FinRobot as the agent profile, not as part of
the prompt, so editing it leaves every recorded key intact -- the run
completes, all sixty digests match, and the decisions replayed were taken
under instructions nobody is running any more. That is a property of any
adapter whose instructions travel separately from the keyed input, not a
FinRobot quirk, and the other integrations in this package share it. Replay
therefore compares the mandate's own digest against the one the transcript
recorded, and refuses on a mismatch. See ``_refuse_a_changed_mandate``.

Replay needs no FinRobot, no API key and no network. The shipped example and
notebook default to it, and CI runs them.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import statistics
from typing import Any, Sequence

from .._core import ValidationError
from ..counterfactual import MACRO_FIELDS
from .common import (AdapterInfo, FrameworkError, IntegrationError,
                     MissingDependencyError)
from .common import DecisionError as _CommonDecisionError
#: The shared digest, which hashes a mapping as canonical JSON. This module's
#: own :func:`digest` takes the rendered prompt and is the replay key; it is
#: deliberately str-only and does not change. This one is for hashing a
#: config, which is a dict.
from .common import check_prior as _check_prior  # noqa: F401 -- parity
from .common import digest as _digest_any
from .common import stamp_resume_counts
from .common import jsonable as _as_jsonable

#: The macro fields FinRobot is shown. Bound to ``counterfactual.MACRO_FIELDS``
#: itself, so the two cannot drift apart over what a macro experiment covers.
OBSERVABLE_MACRO = MACRO_FIELDS

#: The sides a decision may name. HOLD carries no quantity and produces no
#: order, so the agent can decline a decision period.
SIDES = ("BUY", "SELL", "HOLD")

#: The only keys a decision object may carry, and the only keys an action may
#: carry. Anything else is refused rather than dropped.
#:
#: Silently ignoring an unknown field executes an instruction the agent did
#: not give. A model that writes ``stop_loss`` believes it has protection and
#: sizes accordingly; dropping the field buys at market with none, and the
#: trace records a decision the agent never made. The two fields a model
#: reaches for most -- ``order_type`` and ``limit_price`` -- get refusals
#: naming the missing capability, because Tradefloor executes market sweeps
#: only: ``Portfolio.execute`` takes a signed share count and sweeps the live
#: book, and there is no resting order at the agent boundary.
DECISION_FIELDS = ("actions", "rationale")
ACTION_FIELDS = ("symbol", "side", "quantity")

#: Price rows kept for the recent-return and volatility lines. Five days at
#: the library's six steps a day: long enough for a realised-volatility number
#: to mean something, short enough to describe recent conditions.
HISTORY_STEPS = 30

#: Fraction of an instrument's average daily volume one order may take.
#: ``tradefloor.baselines.rebalance`` uses the same 2% for every shipped
#: baseline. Without a cap, one mis-scaled share count moves the price further
#: than the intervention does, and the comparison then measures market impact.
#: Requests above the cap are clipped and RECORDED as clipped. Being unable to
#: size a position says something about the agent, so the trace carries it.
MAX_PARTICIPATION = 0.02

#: Mandate version, recorded beside every decision. Replaying a run under a
#: different mandate produces a different experiment; this is how a reader
#: notices.
MANDATE_VERSION = "1"

#: The FinRobot abstraction this integration drives, recorded in the adapter
#: metadata. Named here rather than only in the example, because a reader of a
#: transcript has to be able to tell a SingleAssistant run from a group chat:
#: they are different experiments and the responses do not mean the same
#: thing. See the module docstring for why this one.
ENTRY_POINT = "finrobot.agents.workflow.SingleAssistant"

#: The upstream project. Recorded so a citation of a run can name what it ran
#: against; this repository neither owns FinRobot nor is affiliated with it.
FRAMEWORK_URL = "https://github.com/AI4Finance-Foundation/FinRobot"

#: The mandate, identical in both arms of the experiment. It says what the
#: agent is for, what it may do, and the shape of the answer. It says nothing
#: about what is coming. The question the experiment asks is whether the agent
#: infers a changed world from the observation, and an arm told it was the
#: rate-shock arm would be answering something else.
MANDATE = """\
You manage a portfolio inside a simulated financial market.

Use only the macro, market, portfolio and execution information supplied in \
the message. You have no other data source, no browsing and no tools. The \
tickers are synthetic and are not real listed companies, so anything you know \
about real securities does not apply here.

Seek attractive risk-adjusted returns while controlling downside risk. You \
may buy, sell, resize or maintain positions. You are not required to trade: \
if the right decision is to leave the book where it is, say so.

Answer with a single JSON object and nothing else. No preamble, no commentary \
after it, no code fences.

{
  "actions": [
    {"symbol": "<one of the listed symbols>", "side": "BUY", "quantity": 1234}
  ],
  "rationale": "<one or two sentences>"
}

Rules for the answer:
  - "side" is "BUY", "SELL" or "HOLD".
  - "quantity" is a NUMBER OF SHARES, positive, and is omitted or 0 for HOLD.
  - Sell quantities are positive: the side says the direction.
  - Name a symbol at most once.
  - An empty "actions" list means change nothing.
  - "rationale" states why, in one or two sentences. Do not explain your \
working, do not restate the data, and do not describe these instructions.
"""

#: The role FinRobot is constructed with. A dict rather than one of the ten
#: names in ``finrobot.agents.agent_library.library``, for two reasons. The
#: library's finance roles are a Market_Analyst and an Expert_Investor, whose
#: profiles are about producing research on real listed companies, and both
#: arrive with toolkits pointed at live market data (see the module
#: docstring). And the mandate has to be IDENTICAL across the two arms of a
#: controlled experiment, so it belongs in a constant the experiment can print
#: rather than in a library lookup that could change under a version bump.
#: This still goes through ``FinRobot._preprocess_config`` and
#: ``autogen.AssistantAgent``, so it is the real class doing the real thing.
#:
#: ``toolkits`` is empty, and belongs in the config because
#: ``SingleAssistant`` takes no such argument. It forwards ``**kwargs`` to the
#: ``UserProxyAgent``, so ``toolkits=[]`` reaches
#: ``ConversableAgent.__init__`` and raises ``TypeError``. The supported route
#: is the config, where ``FinRobot.__init__`` reads it.
AGENT_CONFIG = {"name": "Portfolio_Manager", "profile": MANDATE,
                "toolkits": []}


class DecisionError(_CommonDecisionError):
    """FinRobot returned something that is not an executable decision.

    A subclass of :class:`~tradefloor.ValidationError`, so a caller already
    catching the library's refusals catches this too. Raised for a response
    that cannot be parsed, and for one that parses into an action the market
    cannot accept: an unknown symbol, an unsupported side, a negative or
    non-finite quantity, a symbol named twice.

    It derives from the shared
    :class:`~tradefloor.integrations.common.DecisionError` rather than from
    ``ValidationError`` directly, which reaches ``ValidationError`` by the
    same path and adds one thing: code written against the shared adapter
    layer catches FinRobot's refusals without naming FinRobot. Nothing that
    already caught this stops catching it -- that is the only reason the base
    moved.

    It raises instead of repairing. A guess at what the model meant would be a
    second, unrecorded agent between FinRobot and the market, and every
    experiment run afterwards would measure that too.
    """


class Action:
    """One validated instruction: a symbol, a side, and a share count."""

    __slots__ = ("symbol", "side", "quantity")

    def __init__(self, symbol: str, side: str, quantity: float = 0.0) -> None:
        # Validated rather than trusted. `Action("A", "SHORT", -5)` used to
        # construct: `signed()` returns 0.0 for an unrecognised side, so an
        # unknown side became a silent hold, and a negative SELL became a
        # sign-flipped BUY. Both are the kind of quiet wrongness `parse`
        # exists to refuse, one layer below where it refuses it.
        #
        # Case is normalised in `parse`, not here, because leniency belongs
        # at the boundary where model output arrives and this constructor is
        # reached with values already checked.
        if side not in SIDES:
            raise DecisionError(
                f"side must be one of {', '.join(SIDES)}, got {side!r}. "
                "Action is built from validated input; parse normalises "
                "case and refuses the rest.")
        quantity = float(quantity)
        if not quantity >= 0 or quantity == float("inf"):
            raise DecisionError(
                f"quantity must be a finite, non-negative share count, got "
                f"{quantity}. The side carries the direction.")
        self.symbol = symbol
        self.side = side
        self.quantity = quantity

    def as_dict(self) -> dict[str, Any]:
        return {"symbol": self.symbol, "side": self.side,
                "quantity": self.quantity}

    def signed(self) -> float:
        """The share delta this action asks for. HOLD is zero."""
        if self.side == "BUY":
            return self.quantity
        if self.side == "SELL":
            return -self.quantity
        return 0.0

    def __eq__(self, other: Any) -> bool:
        return isinstance(other, Action) and self.as_dict() == other.as_dict()

    def __repr__(self) -> str:
        if self.side == "HOLD":
            return f"HOLD {self.symbol}"
        return f"{self.side} {self.quantity:,.0f} {self.symbol}"


class Decision:
    """What FinRobot decided at one decision point, after validation."""

    __slots__ = ("actions", "rationale")

    def __init__(self, actions: Sequence[Action], rationale: str = "") -> None:
        self.actions = list(actions)
        self.rationale = rationale

    def as_dict(self) -> dict[str, Any]:
        return {"actions": [a.as_dict() for a in self.actions],
                "rationale": self.rationale}

    def __eq__(self, other: Any) -> bool:
        return isinstance(other, Decision) and self.as_dict() == other.as_dict()

    def __repr__(self) -> str:
        if not self.actions:
            return "Decision(no change)"
        return "Decision(" + ", ".join(repr(a) for a in self.actions) + ")"


# -- observation -> FinRobot ------------------------------------------------


def observe(obs: Any, *, history: Sequence[Sequence[float]] = (),
            fundamentals: dict[str, dict[str, Any]] | None = None,
            max_participation: float = MAX_PARTICIPATION) -> dict[str, Any]:
    """The observable state, as a JSON-able payload. An allowlist.

    Every key below is written out by hand. Nothing is copied off the engine
    by reflection, and ``obs.engine`` is read for exactly two things: the
    macro fields in ``OBSERVABLE_MACRO``, and the marks that price the
    portfolio. See the module docstring for why that matters.

    ``history`` is the adapter's own record of the prices it has already been
    shown. A recent return and a realised volatility come from that, without
    asking the simulator for either.
    """
    macro_state = obs.engine.macro_state
    macro = {field: getattr(macro_state, field) for field in OBSERVABLE_MACRO}

    rows = [list(row) for row in history]
    facts = fundamentals or {}
    assets = []
    for i, ticker in enumerate(obs.tickers):
        book = obs.book(ticker)
        adv = obs.avg_volume(ticker)
        assets.append({
            "symbol": ticker,
            "price": obs.price(ticker),
            "return_1d": _window_return(rows, i, obs.steps_per_day),
            "return_5d": _window_return(rows, i, obs.steps_per_day * 5),
            "volatility": _volatility(rows, i),
            "best_bid": book.best_bid,
            "best_ask": book.best_ask,
            "avg_daily_volume": adv,
            "max_order_shares": max_participation * adv,
            "position": obs.position(ticker),
            # Public company facts, supplied by the caller rather than read
            # off the simulator. Absent is fine and renders as absent.
            "fundamentals": dict(facts.get(ticker, {})),
        })

    portfolio = obs.portfolio
    # The funding limit, and what is left under it. Without these the payload
    # states a participation cap worth several times equity and never states
    # the constraint that actually refuses the trade -- an agent that sizes to
    # what it was told then scores zero fills and reads as a bad agent when it
    # was misled. Measured on the native OpenAI path: twelve rejections, no
    # trades. `None` where no limit is configured, never a made-up number,
    # because a fabricated ceiling is the same defect pointing the other way.
    equity = portfolio.net_worth(obs.engine)
    limit = portfolio.max_leverage
    exposure = portfolio.gross_exposure(obs.engine)
    headroom = (None if limit is None
                else max(0.0, limit * equity - exposure))
    return {
        "step": obs.step,
        "day": obs.day,
        "steps_per_day": obs.steps_per_day,
        "macro": macro,
        "assets": assets,
        "portfolio": {
            "cash": portfolio.cash,
            "net_worth": equity,
            "gross_exposure": portfolio.leverage(obs.engine),
            "max_leverage": limit,
            "buying_power": headroom,
        },
    }


def _window_return(rows: Sequence[Sequence[float]], i: int,
                   steps: int) -> float | None:
    """Return over the last ``steps`` observations, or None if unseen.

    None, for a window the agent has not lived through yet. Zero would claim
    the price did not move, and on day one that describes the record and not
    the market. From the number alone the agent cannot tell the two apart.
    """
    if len(rows) < 2:
        return None
    window = rows[-(steps + 1):]
    if len(window) < 2:
        return None
    first, last = window[0][i], window[-1][i]
    return None if first <= 0 else (last / first) - 1.0


def _volatility(rows: Sequence[Sequence[float]], i: int) -> float | None:
    """Realised volatility of the step returns held in ``rows``."""
    if len(rows) < 3:
        return None
    steps = [rows[k][i] / rows[k - 1][i] - 1.0
             for k in range(1, len(rows)) if rows[k - 1][i] > 0]
    return statistics.pstdev(steps) if len(steps) >= 2 else None


def render(payload: dict[str, Any], *, objective: str = "") -> str:
    """The payload as the text FinRobot receives.

    Text and not raw JSON: the assistant is a chat agent and reads prose
    better than a nested object, and a rendered block is what somebody
    auditing the recorded transcript has to read. Generated from ``payload``
    alone, so nothing outside the allowlist can appear here by accident.
    """
    macro = payload["macro"]
    out = [
        "SIMULATED MARKET",
        "",
        f"Day {payload['day']}, decision step {payload['step']}.",
        "",
        "Macro",
        "-----",
    ]
    for field in OBSERVABLE_MACRO:
        out.append(f"{field:<22} {_num(macro.get(field))}")

    out += ["", "Assets", "------"]
    for asset in payload["assets"]:
        out.append("")
        out.append(asset["symbol"])
        out.append(f"  price                {_money(asset['price'])}")
        out.append(f"  return, 1 day        {_pct(asset['return_1d'])}")
        out.append(f"  return, 5 days       {_pct(asset['return_5d'])}")
        out.append(f"  step volatility      {_pct(asset['volatility'])}")
        out.append(f"  bid / ask            {_money(asset['best_bid'])}"
                   f" / {_money(asset['best_ask'])}")
        out.append(f"  avg daily volume     {_qty(asset['avg_daily_volume'])}")
        out.append(f"  your position        {_qty(asset['position'])} shares")
        out.append(f"  max order this step  {_qty(asset['max_order_shares'])}"
                   " shares")
        for key, value in sorted(asset["fundamentals"].items()):
            out.append(f"  {key:<20} {_num(value)}")

    book = payload["portfolio"]
    out += [
        "",
        "Portfolio",
        "---------",
        f"cash                   {_money(book['cash'])}",
        f"net worth              {_money(book['net_worth'])}",
        f"gross exposure         {_num(book['gross_exposure'])}x",
        # The funding cap, and what is left under it. `max_order_shares`
        # above says what the MARKET absorbs per order; these say what this
        # BOOK can hold before the leverage limit refuses the trade, and it
        # is usually the smaller of the two. Stating one without the other
        # is what produced twelve rejections and no trades on the native
        # OpenAI path: the agent sized to the limit it was shown.
        f"max leverage           {_num(book['max_leverage'])}"
        + ("" if book["max_leverage"] is None else "x"),
        f"buying power           {_money(book['buying_power'])}",
        "",
        "Positions:",
    ]
    held = [a for a in payload["assets"] if a["position"]]
    if not held:
        out.append("  none")
    for asset in held:
        out.append(f"  {asset['symbol']:<8} {_qty(asset['position'])} shares"
                   f"  ({_money(asset['position'] * asset['price'])})")

    if objective:
        out += ["", "Objective", "---------", objective]
    return "\n".join(out)


def _num(value: Any) -> str:
    if value is None:
        return "not available"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def _money(value: Any) -> str:
    return "not available" if value is None else f"{value:,.2f}"


def _pct(value: Any) -> str:
    return "not available" if value is None else f"{value * 100:+.2f}%"


def _qty(value: Any) -> str:
    return "not available" if value is None else f"{value:,.0f}"


# -- FinRobot -> Tradefloor -------------------------------------------------

#: A JSON object anywhere in the response. Models wrap answers in code fences
#: and add a closing sentence however firmly the mandate asks them not to.
#: Failing on a fence would score formatting compliance instead of the
#: portfolio decision. This finds the BRACES; ``json`` parses what is inside.
_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


def _no_duplicate_keys(pairs: list) -> dict[str, Any]:
    """``json.loads`` hook that refuses an object naming a key twice.

    Standard JSON parsing keeps the LAST occurrence, so a response carrying
    ``"actions": [], "actions": [...]`` resolved to whichever the model
    emitted second, silently, and nothing recorded that a first statement
    existed. :func:`parse` refuses duplicate SYMBOLS one layer up for exactly
    this reason -- two instructions with no defined order -- and the JSON
    layer owed the same principle to duplicate keys.
    """
    keys = [key for key, _ in pairs]
    duplicated = sorted({key for key in keys if keys.count(key) > 1})
    if duplicated:
        raise DecisionError(
            f"the FinRobot response names keys more than once: "
            f"{', '.join(duplicated)}. json parsing keeps the last "
            "occurrence, so which of the model's statements reached the "
            "market would depend on emission order, and nothing would "
            "record that another existed.")
    return dict(pairs)


def parse(text: str) -> Decision:
    """Turn a FinRobot response into a validated :class:`Decision`.

    Structural validation only: this checks that the answer is a decision.
    Whether the symbols exist and the sizes are executable belongs to
    :func:`orders_from`, which has the observation needed to answer it.
    """
    if not isinstance(text, str) or not text.strip():
        raise DecisionError(
            "FinRobot returned an empty response, so there is no decision to "
            "validate. An empty answer is not the same as HOLD, and treating "
            "it as one would score a failed call as a considered choice.")

    # The exact contract first, so a compliant answer is parsed as written
    # and never through the brace search, which cannot tell a top-level list
    # from an object and would report the wrong thing about it.
    try:
        raw = json.loads(text.strip(),
                         object_pairs_hook=_no_duplicate_keys)
    except json.JSONDecodeError:
        match = _OBJECT.search(text)
        if match is None:
            raise DecisionError(
                "no JSON object in the FinRobot response. The mandate asks "
                f"for one object and nothing else; got {text.strip()[:200]!r}"
            ) from None
        try:
            raw = json.loads(match.group(0),
                             object_pairs_hook=_no_duplicate_keys)
        except json.JSONDecodeError as exc:
            raise DecisionError(
                "the JSON object in the FinRobot response does not parse: "
                f"{exc}. Got {match.group(0)[:200]!r}") from exc

    if not isinstance(raw, dict):
        raise DecisionError(
            "expected a JSON object with an 'actions' key, got a "
            f"{type(raw).__name__}")

    # An ABSENT 'actions' key is refused; an EMPTY list is accepted. The
    # distinction is load-bearing, and defaulting the absent key to [] was a
    # defect in released code rather than a lenience.
    #
    # A model that half-follows the mandate writes what it would say to a
    # person -- `{"recommendation": "trim NOVA", "confidence": 0.8}` -- and
    # the brace search above finds it. Read as a hold, that run completes
    # with trades=0 and an empty errors list, which is EXACTLY the shape of
    # an agent that considered the market and declined. Measured against the
    # shared adapter layer on one market, one seed and that same response:
    # this adapter scored trades=0 with no errors while the shared validator
    # reported three refusals. Two adapters disagreeing about whether a
    # decision happened makes a cross-adapter comparison uncontrolled, and
    # nothing in either scorecard says so.
    #
    # `{"actions": null}` stays a no-op: the key is present, so whoever
    # wrote it addressed the question and declined.
    if "actions" not in raw:
        keys = ", ".join(sorted(str(k) for k in raw)) or "none"
        raise DecisionError(
            f"no 'actions' key in the FinRobot response (keys present: "
            f"{keys}). A decision that declines to trade says so with an "
            "EMPTY 'actions' list, which the mandate asks for by name; a "
            "JSON object without the key is a model answering a different "
            "question, and treating it as HOLD would score a failed "
            "instruction as a considered choice.")
    stray = sorted(set(raw) - set(DECISION_FIELDS))
    if stray:
        raise DecisionError(
            f"the FinRobot response carries {', '.join(stray)}, and a "
            f"decision is {', '.join(DECISION_FIELDS)} and nothing else. A "
            "key this adapter does not read is either a model answering a "
            "different question or an instruction the market cannot follow, "
            "and dropping it silently would execute a decision the agent did "
            "not make.")
    actions_raw = raw["actions"]
    if actions_raw is None:
        actions_raw = []
    if not isinstance(actions_raw, list):
        raise DecisionError(
            f"'actions' must be a list, got a {type(actions_raw).__name__}")

    actions: list[Action] = []
    for i, item in enumerate(actions_raw):
        if not isinstance(item, dict):
            raise DecisionError(
                f"action {i} is a {type(item).__name__}, not an object with "
                "'symbol' and 'side'")
        symbol = item.get("symbol")
        if not isinstance(symbol, str) or not symbol.strip():
            raise DecisionError(
                f"action {i} has no usable 'symbol': {item.get('symbol')!r}")
        side = item.get("side")
        if not isinstance(side, str) or side.upper() not in SIDES:
            raise DecisionError(
                f"action {i} names side {side!r}, which is not one of "
                f"{', '.join(SIDES)}")
        side = side.upper()

        # Named refusals for the two a model reaches for most, so the message
        # says what the market cannot do rather than that a key was unexpected.
        order_type = item.get("order_type")
        if order_type is not None and not (isinstance(order_type, str)
                                           and order_type.lower() == "market"):
            raise DecisionError(
                f"action {i} asks for order_type {order_type!r}. Tradefloor "
                "executes market sweeps only -- Portfolio.execute takes a "
                "signed share count and sweeps the live book, and there is no "
                "resting order at the agent boundary -- so only 'market' is "
                "accepted.")
        if item.get("limit_price") is not None:
            raise DecisionError(
                f"action {i} carries a limit_price, and this market has no "
                "limit orders at the agent boundary: Portfolio.execute sweeps "
                "the live book at whatever price it gives. Dropping the field "
                "silently would execute at market a trade the agent priced as "
                "protected, so it is refused instead.")
        unknown = sorted(set(item) - set(ACTION_FIELDS)
                         - {"order_type", "limit_price"})
        if unknown:
            raise DecisionError(
                f"action {i} carries {', '.join(unknown)}, which this market "
                "has no execution path for. An action is "
                f"{', '.join(ACTION_FIELDS)} and nothing else. Ignoring the "
                "field would execute a trade the agent conditioned on "
                "something it never got.")

        quantity = item.get("quantity", 0)
        if quantity is None:
            quantity = 0
        if isinstance(quantity, bool) or not isinstance(quantity, (int, float)):
            raise DecisionError(
                f"action {i} has quantity {quantity!r}, which is not a number "
                "of shares")
        quantity = float(quantity)
        if quantity != quantity or quantity in (float("inf"), float("-inf")):
            raise DecisionError(
                f"action {i} has a non-finite quantity ({quantity})")
        if quantity < 0:
            raise DecisionError(
                f"action {i} has quantity {quantity}, which is negative. The "
                "side carries the direction, so a sell is SELL with a "
                "positive quantity; a negative one is ambiguous about which "
                "of the two the model meant.")
        if side == "HOLD" and quantity:
            raise DecisionError(
                f"action {i} is a HOLD carrying quantity {quantity}. HOLD "
                "means no trade; a quantity beside it does not say whether "
                "the model wanted to buy it or to keep it.")
        actions.append(Action(symbol.strip(), side, quantity))

    seen = [a.symbol for a in actions]
    duplicated = sorted({s for s in seen if seen.count(s) > 1})
    if duplicated:
        raise DecisionError(
            f"symbols named more than once: {', '.join(duplicated)}. Two "
            "instructions for one symbol have no defined order, so which one "
            "reaches the market would depend on dict iteration.")

    rationale = raw.get("rationale", "")
    if rationale is None:
        rationale = ""
    if not isinstance(rationale, str):
        raise DecisionError(
            f"'rationale' must be a string, got a {type(rationale).__name__}")
    return Decision(actions, rationale.strip())


def orders_from(decision: Decision, obs: Any, *,
                max_participation: float = MAX_PARTICIPATION,
                ) -> tuple[dict[str, float], list[str]]:
    """Validated share deltas, plus a note for anything that was adjusted.

    The second half of validation, and the only thing that ever reaches
    ``World._execute``. An unknown symbol raises: the model is trading an
    instrument this market does not list, and executing the remaining actions
    would execute half a plan the agent never made.

    A size above the participation cap does not raise. It is clipped to the
    cap and the clip comes back as a note. An oversized request is something
    the agent did, so the trace says so. See ``MAX_PARTICIPATION``.
    """
    listed = list(obs.tickers)
    orders: dict[str, float] = {}
    notes: list[str] = []

    for action in decision.actions:
        if action.symbol not in listed:
            raise DecisionError(
                f"{action.symbol!r} is not listed in this market. The "
                f"universe is {', '.join(listed)}.")
        delta = action.signed()
        if not delta:
            continue
        # A cap of zero IS a cap. `if cap > 0` once read "no volume to
        # participate in" as "no cap at all": with avg_volume or
        # max_participation at zero, a 1e12-share order passed through whole
        # with no note, while the payload showed the agent max_order_shares
        # of exactly 0.0 -- the observation and the enforcement stating
        # opposite things. Clipped to zero, the order falls out as dust
        # below and the note says what happened.
        cap = max(0.0, max_participation * obs.avg_volume(action.symbol))
        if abs(delta) > cap:
            notes.append(
                f"{action.symbol}: asked for {abs(delta):,.0f} shares, "
                f"clipped to {cap:,.0f} ({max_participation:.1%} of average "
                "daily volume)")
            delta = cap if delta > 0 else -cap
        # Below one share is dust: it generates a trade every step and turns
        # turnover, which the comparison reports, into noise.
        if abs(delta) >= 1.0:
            orders[action.symbol] = delta
    return orders, notes


# -- recording and replay ---------------------------------------------------


def digest(prompt: str) -> str:
    """The replay key: SHA-256 of the exact text FinRobot was sent.

    Sixteen hex characters: ample for the few dozen decision points of one
    experiment, and short enough to compare by eye in an error message.
    """
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]


class Transcript:
    """Recorded FinRobot interactions, keyed by the input that produced them.

    A file of these lets ``python rate_shock.py`` reproduce a real agent run
    with no API key, no network and no FinRobot install. It holds what
    re-executing and auditing the experiment need -- the prompt, the raw
    response, the parsed decision -- and nothing else. No API keys, no account
    identifiers, no request IDs, no provider headers.

    ``meta`` records what the run cannot reconstruct: the FinRobot version,
    the provider and model, the generation parameters and the mandate version.
    Replaying a transcript under a different mandate produces a different
    experiment, and ``meta`` is how a reader notices.
    """

    __slots__ = ("meta", "entries", "_by_digest")

    def __init__(self, meta: dict[str, Any] | None = None,
                 entries: Sequence[dict[str, Any]] = ()) -> None:
        self.meta = dict(meta or {})
        self.entries = [dict(e) for e in entries]
        self._by_digest = {e["digest"]: e for e in self.entries}

    def record(self, entry: dict[str, Any]) -> None:
        self.entries.append(dict(entry))
        # Last write wins. An identical prompt must have one answer
        # available, and re-recording one re-runs the same question.
        self._by_digest[entry["digest"]] = self.entries[-1]

    def response_for(self, key: str) -> str | None:
        entry = self._by_digest.get(key)
        return None if entry is None else entry["response"]

    def entry_for(self, key: str) -> dict[str, Any] | None:
        """The whole recorded entry, or None only when none exists.

        Distinct from :meth:`response_for`, which returns None BOTH for a
        missing entry and for an entry whose recorded response is null --
        two situations with opposite remedies, which :meth:`_ask` has to
        tell apart.
        """
        return self._by_digest.get(key)

    def __len__(self) -> int:
        return len(self.entries)

    def as_dict(self) -> dict[str, Any]:
        return {"meta": self.meta, "entries": self.entries}

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), indent=2) + "\n"

    @classmethod
    def from_json(cls, text: str) -> "Transcript":
        raw = json.loads(text)
        return cls(meta=raw.get("meta", {}), entries=raw.get("entries", []))

    @classmethod
    def load(cls, path: Any) -> "Transcript":
        import pathlib
        return cls.from_json(pathlib.Path(path).read_text(encoding="utf-8"))

    def save(self, path: Any) -> None:
        import pathlib
        target = pathlib.Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self.to_json(), encoding="utf-8")


def _refuse_a_changed_mandate(transcript: "Transcript | None",
                              mandate: str) -> None:
    """Refuse a replay whose instructions are not the recorded ones.

    The replay key is the digest of the rendered prompt, and the mandate does
    NOT travel in that prompt -- it reaches FinRobot as the agent profile, a
    separate constructor argument. So editing the mandate leaves every
    recorded key intact: the run completes, all sixty digests match, and the
    decisions replayed were taken under instructions nobody is running any
    more. Nothing in the output says the question changed.

    This is not a FinRobot quirk. It is a property of any adapter whose
    instructions travel separately from the input the key is computed over,
    and the other integrations in this package have the same shape.

    Keyed on the digest rather than on ``mandate_version`` because a version
    is a thing somebody has to remember to bump, and the dangerous edit is
    the one made without remembering. The version is still checked, as the
    fallback for a recording made before the digest was stamped: an older
    transcript carries the version and not the hash, and a deliberate version
    bump is at least caught. A transcript carrying neither cannot be checked
    and is allowed through -- refusing those would break every recording made
    before this existed, and they are no worse off than they were.
    """
    if transcript is None:
        return
    meta = transcript.meta or {}
    recorded = meta.get("instructions_digest")
    if recorded:
        current = digest(str(mandate))
        if recorded != current:
            raise DecisionError(
                f"this transcript was recorded under a different mandate "
                f"(recorded {recorded}, current {current}). The mandate does "
                "not travel in the prompt the replay is keyed on, so every "
                "recorded key still matches and the run would complete -- "
                "answering the instructions you have now with decisions taken "
                "under the ones you had then. Restore the mandate, or "
                "re-record with --live --record.")
        return
    version = meta.get("mandate_version")
    if version is not None and str(version) != MANDATE_VERSION:
        raise DecisionError(
            f"this transcript was recorded under mandate version {version!r} "
            f"and the current mandate is version {MANDATE_VERSION!r}. The "
            "recording predates the instructions digest, so the version is "
            "all there is to compare -- and it says the instructions changed. "
            "Re-record with --live --record.")


# -- the adapter ------------------------------------------------------------


class FinRobotAdapter:
    """A FinRobot agent, in the shape :class:`World` runs.

    Implements the four methods the counterfactual harness looks for:
    :meth:`act`, and the optional :meth:`decision`, :meth:`state` and
    :meth:`fork`. Nothing else about the experiment changes when this replaces
    a native policy.

    ```python
    agent = FinRobotAdapter(mode="replay",
                            transcript=Transcript.load(FIXTURE),
                            fundamentals=FUNDAMENTALS)
    world = World(seed=4242, universe=roster, agent=agent, pins=PINS)
    ```

    ``every`` is the decision cadence in steps. At the library's six steps a
    day, the default of six gives one decision per simulated day. That matches
    how often a portfolio manager decides, and it keeps the bill proportional
    to the experiment instead of to the tick rate. The two arms of a
    comparison MUST run the same cadence, and :meth:`fork` copies it.

    ``mode`` is ``"replay"`` or ``"live"``. Replay imports nothing from
    FinRobot, so a reader without the extra installed can still run the
    experiment. ``"live"`` imports it inside :meth:`_finrobot`, and names the
    extra if that import fails.
    """

    def __init__(self, *, mode: str = "replay",
                 transcript: Transcript | None = None,
                 recorder: Transcript | None = None,
                 prior: Transcript | None = None,
                 llm_config: dict[str, Any] | None = None,
                 fundamentals: dict[str, dict[str, Any]] | None = None,
                 objective: str = "",
                 mandate: str = MANDATE,
                 agent_config: dict[str, Any] | None = None,
                 every: int = 6,
                 max_participation: float = MAX_PARTICIPATION,
                 arm: str = "", info: AdapterInfo | None = None) -> None:
        if mode not in ("replay", "live"):
            raise ValidationError(
                f"mode must be 'replay' or 'live', got {mode!r}")
        if mode == "replay" and transcript is None:
            raise ValidationError(
                "replay mode needs a transcript to replay. Load one with "
                "Transcript.load(path), or pass mode='live' to call FinRobot.")
        if mode == "live" and not llm_config:
            raise ValidationError(
                "live mode needs an llm_config -- the autogen config that "
                "says which provider and model FinRobot talks to. See "
                "examples/integrations/finrobot/rate_shock.py for one.")
        if every < 1:
            raise ValidationError(f"every must be >= 1 step, got {every}")
        if mode == "replay":
            _refuse_a_changed_mandate(transcript, mandate)
        if prior is not None:
            if mode != "live":
                raise ValidationError(
                    "prior is for resuming a live run: it is consulted "
                    "before FinRobot is called, and replay mode calls "
                    "nothing. To replay a recording, pass it as transcript=.")
            if recorder is None:
                raise ValidationError(
                    "prior needs a recorder to resume into. Without one the "
                    "resumed run keeps nothing, which is the loss prior "
                    "exists to stop.")
            _refuse_a_changed_mandate(prior, mandate)

        self.mode = mode
        self.transcript = transcript
        self.recorder = recorder
        #: A recording an earlier live run produced, consulted before
        #: FinRobot. See :meth:`call_or_resume`.
        self.prior = prior
        self.llm_config = llm_config
        self.fundamentals = dict(fundamentals or {})
        self.objective = objective
        self.mandate = mandate
        self.agent_config = dict(agent_config or AGENT_CONFIG)
        self.every = int(every)
        self.max_participation = float(max_participation)
        self.arm = arm

        #: Prices this adapter has been shown, oldest first. The agent's own
        #: memory, and the only reason it can quote a return or a volatility
        #: without asking the simulator for one.
        self.history: list[list[float]] = []
        #: Every decision point, for the example and the notebook. Not state:
        #: :meth:`state` publishes the parts a fork has to agree on.
        self.record: list[dict[str, Any]] = []
        self._decision: dict[str, Any] | None = None
        self._assistant: Any = None
        #: What ran, for ``Transcript.meta`` and for a manifest citation.
        #: Built here rather than at module scope because ``mode`` and
        #: whether a recorder is attached are per-instance, and a fork
        #: rebuilds it from the arguments it was handed.
        self.info = info or self._describe()

    def _describe(self) -> AdapterInfo:
        """The adapter's own identity, with nothing secret in it.

        Provider, model and the generation parameters are lifted OUT of
        ``llm_config`` by name. The config object itself never reaches this
        record and never should: it carries the API key, and this dictionary
        is written into a committed fixture. ``config_digest`` is the
        one-way record of it, which is enough to prove two arms ran the same
        configuration without storing what it was.
        """
        config = self.llm_config or {}
        entries = config.get("config_list") or []
        first = entries[0] if entries and isinstance(entries[0], dict) else {}
        # Rendered rather than passed through, for the same reason the digest
        # is: a caller may hold anything under these names, and `AdapterInfo`
        # refuses what it cannot serialise. Constructing an adapter must not
        # fail on a config the released version accepted.
        generation = {field: _as_jsonable(config[field])
                      for field in ("temperature", "top_p", "max_tokens",
                                    "seed")
                      if config.get(field) is not None}
        version = "not installed"
        try:                                    # pragma: no cover - optional
            from importlib.metadata import version as _version
            version = _version("finrobot")
        except Exception:                       # pragma: no cover - optional
            pass
        return AdapterInfo(
            framework="FinRobot", framework_version=version,
            provider=str(first.get("api_type") or ""),
            model=str(first.get("model") or ""),
            agent_name=str(self.agent_config.get("name") or ""),
            entry_point=ENTRY_POINT, mode=self.mode,
            framework_url=FRAMEWORK_URL,
            instructions_version=MANDATE_VERSION,
            # `str()` because the mandate is a caller-supplied argument and
            # `digest` is str-only; a non-string one used to reach FinRobot
            # and fail there, not fail construction here. For a string
            # mandate -- every real one -- this is the identical digest, so
            # the recorded fixture keeps matching.
            instructions_digest=digest(str(self.mandate)),
            config_digest=_digest_any(_as_jsonable(config)) if config else "",
            generation=generation,
            # `mandate_version` is kept under its own name as well as in
            # `instructions_version`. The shipped fixture's meta already
            # spells it this way and the suite asserts it by that name, so
            # dropping the spelling would either break that test or quietly
            # change what an existing recording claims about itself.
            extra={"recorded": self.recorder is not None,
                   "mandate_version": MANDATE_VERSION})

    def provenance(self) -> dict[str, Any]:
        """What ``Transcript.meta`` should carry, as the shared layer shapes
        it: the framework's identity plus the two Tradefloor-side settings a
        recording cannot reconstruct without them -- the decision cadence,
        without which an agent asked once a day and one asked every step read
        as the same agent, and the participation cap, which decides what
        "clipped" means in the record."""
        out = self.info.as_dict()
        out["decision_every_steps"] = self.every
        out["max_participation"] = self.max_participation
        return out

    # -- the agent protocol ----------------------------------------------

    def act(self, obs: Any) -> dict[str, float]:
        """Share deltas for this step. Empty on the steps between decisions.

        The market advances every step; FinRobot is asked every ``every``
        steps. On the steps in between, this records the prices it saw and
        returns nothing. A human manager watches the book continuously and
        revisits it on a schedule.
        """
        self.history.append(list(obs.prices))
        if len(self.history) > HISTORY_STEPS:
            self.history.pop(0)

        if obs.step % self.every:
            return {}

        payload = observe(obs, history=self.history,
                          fundamentals=self.fundamentals,
                          max_participation=self.max_participation)
        prompt = render(payload, objective=self.objective)
        key = digest(prompt)
        try:
            response = self._ask(prompt, key, obs)
        except IntegrationError:
            # Already one of the adapter family's own -- a DecisionError from
            # a replay miss, a MissingDependencyError naming the extra --
            # and already actionable. Wrapping one again would bury the pip
            # command or the missing digest a level down.
            raise
        except Exception as exc:
            # Everything else is FinRobot's own failure, and it is a
            # different thing from a bad decision: the agent never answered.
            # An experiment scoring "the agent decided badly" must not count
            # "the call did not complete" in the same column, which is why
            # this is a FrameworkError and not a DecisionError. The original
            # exception rides on __cause__, so the traceback a user debugs
            # still reaches the provider's own error.
            raise FrameworkError(
                f"FinRobot raised {type(exc).__name__} instead of returning "
                f"a decision: {exc}") from exc

        decision = parse(response)
        orders, notes = orders_from(
            decision, obs, max_participation=self.max_participation)

        self._decision = {"step": obs.step, **decision.as_dict()}
        self.record.append({
            "arm": self.arm,
            "step": obs.step,
            "day": obs.day,
            "digest": key,
            "prompt": prompt,
            "response": response,
            "decision": decision.as_dict(),
            "orders": dict(orders),
            "clipped": notes,
        })
        return orders

    def decision(self) -> dict[str, Any] | None:
        """The last validated decision, as ``World`` records it every step.

        The actions and the rationale. The prompt, the raw response and the
        arm stay out: :func:`~tradefloor.counterfactual.compare` finds the
        first step at which two arms' decisions differ by comparing these
        dictionaries, so a field varying for any other reason would report a
        divergence that never happened.
        """
        return self._decision

    def state(self) -> dict[str, Any]:
        """What a fork has to agree on, for :func:`tradefloor.agree`.

        The price memory and the last decision: everything surviving from one
        step to the next that could make two arms behave differently for some
        reason other than the intervention. The ``llm_config`` stays out. It
        carries an API key, and this dictionary gets printed.
        """
        return {
            "history": [list(row) for row in self.history],
            "decision": copy.deepcopy(self._decision),
            "every": self.every,
            "mandate_version": MANDATE_VERSION,
        }

    def fork(self) -> "FinRobotAdapter":
        """An independent copy, for :meth:`World.fork`.

        Written out instead of left to ``copy.deepcopy``: in live mode this
        object holds a FinRobot assistant holding an HTTP client, and copying
        one is wasteful at best and a shared socket at worst. The copy takes
        the decision state and SHARES the transcript and the recorder. Both
        directions want that -- a replay of one arm must read the same
        recorded run as the other, and a live recording of both arms belongs
        in one file.

        ``type(self)``, so a subclass forks into its own type. Hard-coding the
        class name broke this: a subclass overriding how a decision is
        obtained kept the override through the shared history and lost it in
        both arms. The run then completes, and the comparison it prints is
        between two agents neither of which was the one under test.
        """
        twin = type(self)(
            mode=self.mode, transcript=self.transcript,
            recorder=self.recorder, prior=self.prior,
            llm_config=self.llm_config,
            fundamentals=self.fundamentals, objective=self.objective,
            mandate=self.mandate, agent_config=self.agent_config,
            every=self.every, max_participation=self.max_participation,
            arm=self.arm,
            # Passed rather than left to rebuild. It rebuilds identically
            # from the arguments above, but a caller who supplied their own
            # `info` would silently lose it in both arms.
            info=self.info)
        twin.history = [list(row) for row in self.history]
        twin.record = copy.deepcopy(self.record)
        twin._decision = copy.deepcopy(self._decision)
        return twin

    # -- FinRobot ---------------------------------------------------------

    def _ask(self, prompt: str, key: str, obs: Any) -> str:
        if self.mode == "replay":
            # `entry_for`, not `response_for`. The latter returns None both
            # for a missing entry and for an entry whose recorded response is
            # null, and the two have opposite remedies: one means the inputs
            # moved and the recording no longer covers them, the other means
            # the recording covers this exact input and captured no answer.
            # Sending the second case the first case's message tells a reader
            # to spend sixty live calls re-recording something already
            # recorded.
            entry = self.transcript.entry_for(key)
            if entry is None:
                raise DecisionError(
                    f"no recorded FinRobot response for step {obs.step} "
                    f"(day {obs.day}, digest {key}). The transcript holds "
                    f"{len(self.transcript)} interactions, none for this "
                    "input. A replay is keyed by the exact text FinRobot was "
                    "sent, so this means the observation mapping, the mandate "
                    "or the market configuration has changed since the "
                    "recording -- replaying anyway would answer this question "
                    "with a response given to a different one. Re-record with "
                    "--live --record.")
            response = entry.get("response")
            if response is None:
                raise DecisionError(
                    f"the recorded interaction for step {obs.step} (day "
                    f"{obs.day}, digest {key}) has no response. The recording "
                    "covers this exact input -- nothing has drifted -- so the "
                    "live call that produced it returned nothing and that is "
                    "what was written down. An empty answer is not a HOLD, so "
                    "it cannot be replayed as one. Re-record this run, or "
                    "drop the entry if the failed call is what you meant to "
                    "keep.")
            return response

        response = self.call_or_resume(key, lambda: self._live(prompt))
        if self.recorder is not None:
            # Stamp the provenance on first write rather than leaving it to
            # the caller. `_refuse_a_changed_mandate` can only refuse a
            # mismatched replay if the recording says what it ran under, and
            # a fresh `Transcript()` has empty `meta` -- so before this, the
            # only transcript in the world that armed the guard was the
            # committed fixture, because it was stamped by hand. Every
            # recording a user made landed in the permissive branch, forever.
            #
            # `setdefault`, so an explicit `meta` wins key by key: the
            # shipped example sets a richer one, and a stamp that clobbered
            # it would trade a working guard for a lost record.
            if "instructions_digest" not in self.recorder.meta:
                for field, value in self.provenance().items():
                    self.recorder.meta.setdefault(field, value)
            self.recorder.record({
                "arm": self.arm, "step": obs.step, "day": obs.day,
                "digest": key, "prompt": prompt, "response": response,
            })
            stamp_resume_counts(self.recorder, self.prior)
        return response

    def call_or_resume(self, key: str, live: Any) -> Any:
        """The answer recorded for ``key`` in :attr:`prior`, or ``live()``.

        Any live run can die -- a rate limit, a dropped connection, a
        keyboard interrupt -- and without this the second attempt re-asks
        every question it already holds an answer to. Measured: a 60-call
        pilot died on call 36 and the 35 answers it had paid for were
        unreachable to the next attempt.

        The market is deterministic, so a resumed run reaches the same
        prompts and computes the same digests, and a recorded answer is
        still an answer to the question being asked. That is the property
        the replay path already rests on; this changes which source is
        consulted first, and a miss falls through to FinRobot.

        The same method as
        :meth:`~tradefloor.integrations.common.FrameworkAdapter.call_or_resume`,
        written out here for the reason everything else in this file is:
        the adapter predates the shared base and does not take it.
        """
        if self.prior is not None:
            entry = self.prior.entry_for(key)
            if entry is not None:
                return entry.get("response")
        return live()

    def _live(self, prompt: str) -> str:
        """One real FinRobot decision.

        ``SingleAssistant.chat`` prints its conversation and resets the agents
        without returning anything, so the response is unreachable through it.
        This drives the SAME two agents it builds through the same
        ``initiate_chat`` it calls, and keeps the result. Nothing is
        reimplemented: ``.assistant`` is ``finrobot.agents.workflow.FinRobot``
        and ``.user_proxy`` is an ``autogen.UserProxyAgent``, both assembled
        by FinRobot's own constructor.
        """
        assistant = self._finrobot()
        result = assistant.user_proxy.initiate_chat(
            assistant.assistant,
            message=prompt,
            max_turns=1,
            clear_history=True,
            silent=True,
        )
        assistant.reset()
        if not result.chat_history:
            raise DecisionError(
                "FinRobot returned an empty conversation, so there is no "
                "response to parse.")
        return result.chat_history[-1].get("content") or ""

    def _finrobot(self) -> Any:
        """The real FinRobot ``SingleAssistant``, built once and reused.

        Imported here instead of at module scope, so that replay mode -- and
        ``import tradefloor`` -- never require FinRobot to be installed.
        """
        if self._assistant is not None:
            return self._assistant
        try:
            from finrobot.agents.workflow import SingleAssistant
        except ImportError as exc:
            # MissingDependencyError rather than a plain ImportError, and it
            # loses nothing: it IS an ImportError, so every caller written
            # against the old behaviour still catches it. What it adds is
            # membership of the adapter error family, which is what lets
            # `act` tell a missing install apart from a framework that failed
            # and pass this through with its own message instead of wrapping
            # it in a FrameworkError.
            #
            # Raised by hand rather than through `common.require`, because
            # `require` has no way to carry the sentence after the pip
            # command, and on this integration that sentence is the
            # diagnosis: somebody on 3.12 who runs the command gets a
            # resolver failure they cannot read.
            raise MissingDependencyError(
                "live mode needs FinRobot, which is an optional extra:\n"
                '    pip install "tradefloor[finrobot]"\n'
                "FinRobot supports Python 3.10 and 3.11 only, and Tradefloor "
                "needs 3.11 or later, so the two overlap at 3.11 exactly. "
                f"Original error: {exc}"
            ) from exc

        config = dict(self.agent_config)
        config["profile"] = config.get("profile") or self.mandate
        # See the module docstring. The library roles' toolkits fetch REAL
        # market data, describing a different world from the simulated one.
        # `setdefault` and not an assignment: a caller who supplies toolkits
        # on purpose keeps them, and owns what that does to the ground-truth
        # boundary. A config that never mentions them gets none.
        config.setdefault("toolkits", [])
        self._assistant = SingleAssistant(
            agent_config=config,
            llm_config=self.llm_config,
            # The SingleAssistant default gives the user proxy a working
            # directory and lets it run model-authored code. This wants one
            # JSON object.
            code_execution_config=False,
            max_consecutive_auto_reply=0,
            human_input_mode="NEVER",
            is_termination_msg=lambda message: True,
        )
        return self._assistant

    def __repr__(self) -> str:
        return (f"FinRobotAdapter(mode={self.mode!r}, arm={self.arm!r}, "
                f"every={self.every}, decisions={len(self.record)})")
