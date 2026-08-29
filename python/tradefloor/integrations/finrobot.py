"""Run a FinRobot agent inside a Tradefloor market, and record what it did.

FinRobot is an open-source financial-AI agent platform from the AI4Finance
Foundation (https://github.com/AI4Finance-Foundation/FinRobot, Apache-2.0).
This module is a Tradefloor integration FOR FinRobot. It is not affiliated
with, endorsed by, or produced in partnership with AI4Finance, and nothing
here is an official FinRobot interface.

    Tradefloor observation
            |
      FinRobotAdapter        allowlist -> text
            |
        FinRobot             the real SingleAssistant, over autogen
            |
      FinRobotAdapter        parse -> validate -> share deltas
            |
    Tradefloor execution

The adapter is deliberately thin, and everything interesting about it is a
boundary rather than a behaviour.

## What FinRobot decides, and what it is never allowed to touch

FinRobot owns interpretation, the portfolio decision and a one-line
rationale. Tradefloor owns the market, the macro path, execution, the order
book, fills, accounting, checkpoints, forks, interventions and the
comparison. FinRobot never mutates engine state: it returns JSON, and
:func:`orders_from` turns validated JSON into the share deltas
:class:`~tradefloor.counterfactual.World` executes. There is no path from a
model response to the engine that does not pass through :func:`parse` and
:func:`orders_from`.

## The observation is an ALLOWLIST, not a filter

:class:`~tradefloor.harness.Observation` carries ``.engine``, and the engine
knows the answer key: :func:`tradefloor.fair_value`, the nine-way factor
:meth:`~tradefloor.Engine.attribution` of every price move, each company's
``mispricing_s``, and -- through a :class:`~tradefloor.Scenario` -- the macro
path the run has not reached yet. An agent that read any of those would be
inverting the simulator rather than trading in it, and the resulting
experiment would measure nothing.

So :func:`observe` names every field it emits, one at a time, and reads
nothing by reflection. Adding a field is a deliberate edit here, which is the
only version of this boundary that survives the engine gaining a new
attribute. ``OBSERVABLE_MACRO`` is the macro half of that allowlist, and it
is ``counterfactual.MACRO_FIELDS`` on purpose: the library already decided
which macro fields a run is ABOUT, and that set excludes ``qe_pe_boost``,
which is a model coefficient rather than a published number.
``tests/test_finrobot.py`` runs the mapping against an engine proxy that
raises on the forbidden attributes, so a future edit that reaches for one
fails rather than leaks.

Company fundamentals -- sector, EPS, book value, revenue growth, beta -- are
NOT read off the engine either. They are passed in by the caller as
``fundamentals``, because they are facts about the roster that a real analyst
reads off a filing, and because an adapter that pulled them out of the
simulator would have one more line to audit. The distinction that matters:
the INPUTS to a valuation are public, the valuation itself is the answer key.

## Which FinRobot abstraction, and why

:class:`finrobot.agents.workflow.SingleAssistant` -- the real class, driven
through the real ``autogen`` chat plumbing. It assembles a
``finrobot.agents.workflow.FinRobot`` assistant (an ``autogen.AssistantAgent``
subclass, including FinRobot's own role-prompt preprocessing) opposite an
``autogen.UserProxyAgent``, which is FinRobot's supported single-agent entry
point.

Two things are set deliberately when constructing it:

- ``toolkits=[]``. Every FinRobot library role that carries toolkits carries
  ones that fetch REAL market data -- FinnHub company news, Yahoo Finance
  prices, SEC filings. In a simulated market those describe a different world,
  and handing them to the agent would break the controlled comparison in the
  most confusing possible way: the agent would be reasoning about securities
  that are not the ones it is trading. The roster here is synthetic and its
  tickers are not real ones.
- ``code_execution_config=False``. The ``SingleAssistant`` default gives the
  user proxy a working directory and lets it run model-authored code. This
  integration wants one JSON object, not a shell.

``MultiAssistant`` and ``MultiAssistantWithLeader`` were the alternative. They
are group chats whose value is division of labour across a research report,
and a portfolio decision every simulated day is one question to one role. A
group chat would multiply the cost per decision by the number of participants
without changing what is being measured, which is how the agent responds when
the world changes.

FinRobot has no structured-output mechanism of its own -- no Pydantic response
model, no schema binding -- so the contract is a JSON object requested in the
mandate and validated here. :func:`parse` is strict and total: anything it
cannot turn into a well-formed :class:`Decision` raises
:class:`DecisionError`, and the caller decides whether that ends the run or
costs the agent a step.

## Replay, and why the default has no API key in it

A live decision costs money and is not reproducible: the market is
deterministic, the model is not. So the adapter has two modes over one code
path. ``mode="live"`` calls FinRobot and, with a recorder attached, writes
every interaction to a :class:`Transcript`. ``mode="replay"`` reads that
transcript back, keyed by the SHA-256 of the exact text FinRobot was sent.

Keying on the input rather than on (arm, step) is what makes a replay honest.
If the observation mapping changes, the digest changes, the key is missing,
and the replay RAISES naming the step -- rather than answering a question
nobody asked with a response recorded for a different one. Replay needs
neither FinRobot nor an API key nor a network, which is why the shipped
example and notebook default to it and why CI can run them.
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

#: The macro fields FinRobot is shown. ``counterfactual.MACRO_FIELDS``, and
#: the same object rather than a copy of the list, so the two cannot drift
#: into disagreeing about what a macro experiment is about.
OBSERVABLE_MACRO = MACRO_FIELDS

#: The sides a decision may name. HOLD carries no quantity and produces no
#: order, which is what lets the agent decline a decision period rather than
#: being forced to trade one.
SIDES = ("BUY", "SELL", "HOLD")

#: Price rows kept for the recent-return and volatility lines. Five days at
#: the library's six steps a day. Long enough for a realised-volatility number
#: to mean something, short enough that the agent is reading recent conditions
#: rather than the whole run.
HISTORY_STEPS = 30

#: Fraction of an instrument's average daily volume one order may take.
#: ``tradefloor.baselines.rebalance`` uses the same 2% for every shipped
#: baseline, and an integration that let an agent name an arbitrary share
#: count would be measuring market impact rather than the agent: one
#: mis-scaled number would move the price more than the intervention did.
#: Requests above the cap are clipped and RECORDED as clipped, never silently
#: obeyed and never refused -- being unable to size a position is information
#: about the agent, and the trace should carry it.
MAX_PARTICIPATION = 0.02

#: Mandate version, recorded beside every decision. A run replayed against a
#: different mandate is a different experiment, and this is what says so.
MANDATE_VERSION = "1"

#: The mandate, identical in both arms of the experiment. It says what the
#: agent is for, what it may do, and the shape of the answer. It does NOT say
#: what is about to happen: the whole question is whether the agent infers a
#: changed world from the observation, so an arm told it was the rate-shock
#: arm would be answering a different question from its control.
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
#: ``toolkits`` is empty and is a KEY here rather than an argument to
#: ``SingleAssistant``, which does not accept one: it forwards its ``**kwargs``
#: to the ``UserProxyAgent``, so ``toolkits=[]`` reaches
#: ``ConversableAgent.__init__`` and raises ``TypeError``. The supported route
#: is the config, where ``FinRobot.__init__`` reads it.
AGENT_CONFIG = {"name": "Portfolio_Manager", "profile": MANDATE,
                "toolkits": []}


class DecisionError(ValidationError):
    """FinRobot returned something that is not an executable decision.

    A subclass of :class:`~tradefloor.ValidationError` so a caller already
    catching the library's refusals catches this too. It is raised for a
    response that cannot be parsed, and for one that parses into an action the
    market cannot accept: an unknown symbol, an unsupported side, a negative
    or non-finite quantity, a symbol named twice.

    Raised rather than repaired, deliberately. A guess at what the model meant
    is a second, unrecorded agent sitting between FinRobot and the market, and
    every experiment run afterwards would be measuring it too.
    """


class Action:
    """One validated instruction: a symbol, a side, and a share count."""

    __slots__ = ("symbol", "side", "quantity")

    def __init__(self, symbol: str, side: str, quantity: float = 0.0) -> None:
        self.symbol = symbol
        self.side = side
        self.quantity = float(quantity)

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
    shown, which is what makes a recent return and a realised volatility
    computable without asking the simulator for either.
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
    return {
        "step": obs.step,
        "day": obs.day,
        "steps_per_day": obs.steps_per_day,
        "macro": macro,
        "assets": assets,
        "portfolio": {
            "cash": portfolio.cash,
            "net_worth": portfolio.net_worth(obs.engine),
            "gross_exposure": portfolio.leverage(obs.engine),
        },
    }


def _window_return(rows: Sequence[Sequence[float]], i: int,
                   steps: int) -> float | None:
    """Return over the last ``steps`` observations, or None if unseen.

    None rather than 0.0 for a window the agent has not lived through yet.
    Zero would be a claim that the price did not move, which on day one is a
    statement about the market rather than about the record, and the agent
    cannot tell the two apart from a number alone.
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

    Text rather than raw JSON because the assistant is a chat agent and reads
    prose better than it reads a nested object, and because a rendered block
    is what a reader of the recorded transcript has to be able to audit. It is
    generated from ``payload`` alone, so anything not in the allowlist cannot
    appear here by accident.
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
#: and add a closing sentence however firmly the mandate asks them not to, and
#: a strictness that failed on a fence would be measuring formatting
#: compliance rather than the portfolio decision. The BRACES are what has to
#: be found; everything inside them is then parsed by ``json``, not by this.
_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


def parse(text: str) -> Decision:
    """Turn a FinRobot response into a validated :class:`Decision`.

    Structural validation only: this checks that the answer is a decision.
    Whether the symbols exist and the sizes are executable is
    :func:`orders_from`'s question, because it needs the observation to answer
    it.
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
        raw = json.loads(text.strip())
    except json.JSONDecodeError:
        match = _OBJECT.search(text)
        if match is None:
            raise DecisionError(
                "no JSON object in the FinRobot response. The mandate asks "
                f"for one object and nothing else; got {text.strip()[:200]!r}"
            ) from None
        try:
            raw = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise DecisionError(
                "the JSON object in the FinRobot response does not parse: "
                f"{exc}. Got {match.group(0)[:200]!r}") from exc

    if not isinstance(raw, dict):
        raise DecisionError(
            "expected a JSON object with an 'actions' key, got a "
            f"{type(raw).__name__}")

    actions_raw = raw.get("actions", [])
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
    ``World._execute``. An unknown symbol raises: it means the model is
    trading an instrument this market does not list, and executing the rest of
    the decision would be executing half of a plan the agent did not make.

    A size above the participation cap does not raise. It is clipped to the
    cap and the clip is returned as a note, because an oversized request is a
    real thing the agent did and the trace should say so. See
    ``MAX_PARTICIPATION``.
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
        cap = max_participation * obs.avg_volume(action.symbol)
        if cap > 0 and abs(delta) > cap:
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

    Sixteen hex characters, which is far more than enough to separate the few
    dozen decision points of one experiment and short enough to print in an
    error message a reader has to compare by eye.
    """
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]


class Transcript:
    """Recorded FinRobot interactions, keyed by the input that produced them.

    A file of these is what makes ``python rate_shock.py`` reproduce a real
    agent run with no API key, no network and no FinRobot installed. It holds
    what is needed to re-execute the experiment and to audit it -- the prompt,
    the raw response, the parsed decision -- and deliberately nothing else. No
    API keys, no account identifiers, no request IDs, no provider headers.

    ``meta`` records what the run cannot reconstruct: the FinRobot version,
    the provider and model, the generation parameters and the mandate version.
    A transcript replayed under a different mandate is a different experiment,
    and this is what lets a reader notice.
    """

    __slots__ = ("meta", "entries", "_by_digest")

    def __init__(self, meta: dict[str, Any] | None = None,
                 entries: Sequence[dict[str, Any]] = ()) -> None:
        self.meta = dict(meta or {})
        self.entries = [dict(e) for e in entries]
        self._by_digest = {e["digest"]: e for e in self.entries}

    def record(self, entry: dict[str, Any]) -> None:
        self.entries.append(dict(entry))
        # Last write wins, which is right: an identical prompt has to have an
        # identical answer available, and re-recording one is a re-run of the
        # same question rather than a second question.
        self._by_digest[entry["digest"]] = self.entries[-1]

    def response_for(self, key: str) -> str | None:
        entry = self._by_digest.get(key)
        return None if entry is None else entry["response"]

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
    day the default of six is one decision per day, which is what a portfolio
    manager does and what keeps the bill proportional to the experiment rather
    than to the tick rate. It MUST be identical across the arms of a
    comparison, and it is: :meth:`fork` copies it.

    ``mode`` is ``"replay"`` or ``"live"``. Replay imports nothing from
    FinRobot, so a reader without the extra installed can still run the
    experiment; ``"live"`` imports it inside :meth:`_finrobot` and says which
    extra installs it if that fails.
    """

    def __init__(self, *, mode: str = "replay",
                 transcript: Transcript | None = None,
                 recorder: Transcript | None = None,
                 llm_config: dict[str, Any] | None = None,
                 fundamentals: dict[str, dict[str, Any]] | None = None,
                 objective: str = "",
                 mandate: str = MANDATE,
                 agent_config: dict[str, Any] | None = None,
                 every: int = 6,
                 max_participation: float = MAX_PARTICIPATION,
                 arm: str = "") -> None:
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
                "examples/finrobot/rate_shock.py for one.")
        if every < 1:
            raise ValidationError(f"every must be >= 1 step, got {every}")

        self.mode = mode
        self.transcript = transcript
        self.recorder = recorder
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

    # -- the agent protocol ----------------------------------------------

    def act(self, obs: Any) -> dict[str, float]:
        """Share deltas for this step. Empty on the steps between decisions.

        The market advances every step; FinRobot is asked every ``every``
        steps. On the steps in between this records the prices it saw and
        returns nothing, which is the same shape a human manager has: the book
        is watched continuously and revisited on a schedule.
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
        response = self._ask(prompt, key, obs)

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

        The actions and the rationale, and not the prompt, the raw response or
        the arm: :func:`~tradefloor.counterfactual.compare` finds the first
        step at which two arms' decisions differ by comparing these
        dictionaries, so anything in here that differed for a reason other
        than the agent deciding differently would report a divergence that did
        not happen.
        """
        return self._decision

    def state(self) -> dict[str, Any]:
        """What a fork has to agree on, for :func:`tradefloor.agree`.

        The price memory and the last decision -- everything that survives
        from one step to the next and could make two arms behave differently
        for a reason other than the intervention. NOT the ``llm_config``: it
        carries an API key, and this dictionary is printed.
        """
        return {
            "history": [list(row) for row in self.history],
            "decision": copy.deepcopy(self._decision),
            "every": self.every,
            "mandate_version": MANDATE_VERSION,
        }

    def fork(self) -> "FinRobotAdapter":
        """An independent copy, for :meth:`World.fork`.

        Explicit rather than left to ``copy.deepcopy`` because in live mode
        this object holds a FinRobot assistant holding an HTTP client, and
        deep-copying one is at best wasteful and at worst a shared socket. The
        copy carries the decision state and SHARES the transcript and the
        recorder, which is correct in both directions: a replay of one arm
        must read the same recorded run as the other, and a live recording of
        both arms belongs in one file.

        ``type(self)`` rather than the class name, so a subclass forks into
        its own type. Written the other way round first, a subclass that
        overrode how a decision is obtained kept that override in the shared
        history and lost it in both arms -- which is the worst possible
        version of the bug, because the run completes and the comparison it
        prints is between two agents that are not the one that was tested.
        """
        twin = type(self)(
            mode=self.mode, transcript=self.transcript,
            recorder=self.recorder, llm_config=self.llm_config,
            fundamentals=self.fundamentals, objective=self.objective,
            mandate=self.mandate, agent_config=self.agent_config,
            every=self.every, max_participation=self.max_participation,
            arm=self.arm)
        twin.history = [list(row) for row in self.history]
        twin.record = copy.deepcopy(self.record)
        twin._decision = copy.deepcopy(self._decision)
        return twin

    # -- FinRobot ---------------------------------------------------------

    def _ask(self, prompt: str, key: str, obs: Any) -> str:
        if self.mode == "replay":
            response = self.transcript.response_for(key)
            if response is None:
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
            return response

        response = self._live(prompt)
        if self.recorder is not None:
            self.recorder.record({
                "arm": self.arm, "step": obs.step, "day": obs.day,
                "digest": key, "prompt": prompt, "response": response,
            })
        return response

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

        Imported here rather than at module scope so that replay mode -- and
        ``import tradefloor`` -- never require FinRobot to be installed.
        """
        if self._assistant is not None:
            return self._assistant
        try:
            from finrobot.agents.workflow import SingleAssistant
        except ImportError as exc:
            raise ImportError(
                "live mode needs FinRobot, which is an optional extra:\n"
                '    pip install "tradefloor[finrobot]"\n'
                "FinRobot supports Python 3.10 and 3.11 only, and Tradefloor "
                "needs 3.11 or later, so the two overlap at 3.11 exactly. "
                f"Original error: {exc}"
            ) from exc

        config = dict(self.agent_config)
        config["profile"] = config.get("profile") or self.mandate
        # See the module docstring. The library roles' toolkits fetch REAL
        # market data, which describes a different world from the one being
        # simulated. `setdefault` rather than an assignment: a caller who
        # deliberately supplies toolkits keeps them and owns what that means
        # for the ground-truth boundary, but a config that never mentions
        # them gets none rather than inheriting a role's.
        config.setdefault("toolkits", [])
        self._assistant = SingleAssistant(
            agent_config=config,
            llm_config=self.llm_config,
            # Not a shell: the SingleAssistant default gives the user proxy a
            # working directory and lets it run model-authored code.
            code_execution_config=False,
            max_consecutive_auto_reply=0,
            human_input_mode="NEVER",
            is_termination_msg=lambda message: True,
        )
        return self._assistant

    def __repr__(self) -> str:
        return (f"FinRobotAdapter(mode={self.mode!r}, arm={self.arm!r}, "
                f"every={self.every}, decisions={len(self.record)})")
