"""A PydanticAI agent trading a Tradefloor market, through the adapter.

The same job as `../callable/five_days.py`, done by a real `pydantic_ai.Agent`
instead of a plain function, so the two can be read line for line:

    1. build a small market
    2. give the agent a simple mean-reversion rule over what it can observe
    3. run the evaluation and read the scorecard

Only step 2 differs. Here the rule lives inside a `FunctionModel` -- the
framework's own offline model -- which is what makes this run in seconds
with no API key, no network and no provider account, while still driving the
real `Agent`, the real output schema, the real validation and the real retry
loop. Nothing is stubbed below the model boundary.

`FunctionModel` rather than `TestModel`, because `TestModel` generates its
answer from the output schema and would trade the same way whatever the
market did, which would make this a test of the plumbing rather than an
example of an agent. `FunctionModel` lets the example state a real decision
rule and show it responding to prices.

## This file is also the notebook's source of constants

`pydantic_ai/rate_shock.ipynb` runs the same market with a real model behind it,
and imports `SEED`, `ROSTER`, `universe()` and the fork settings from here
rather than restating them. Two copies of a roster are two rosters that will
eventually disagree, and the notebook and the script would then describe two
different experiments under one name.

The framework imports below are soft for the same reason: the notebook's
default path REPLAYS a recorded run and must work for a reader who has never
installed PydanticAI, and it cannot do that if importing this module
requires it.

## DO NOT RENAME THIS FILE TO pydantic_ai.py

A script's own directory is `sys.path[0]`, so a file named `pydantic_ai.py`
here would shadow the installed `pydantic_ai` package for this very process,
and the example could not import the framework it demonstrates. The `_agent`
suffix is load-bearing, not decoration. Verified by building such a file and
watching the local module win the import.

The library module `tradefloor/integrations/pydantic_ai.py` is safe from
this, because it is only ever imported as part of a package and never sits
on `sys.path[0]`.

Run it:

    python examples/integrations/pydantic_ai/rate_shock.py
"""

from __future__ import annotations

import importlib.util
import json
import os
from dataclasses import dataclass
from pathlib import Path

# Soft, and at module scope rather than inside the functions the house style
# would otherwise put them in. Two requirements pull in opposite directions:
# `RunContext[Desk]` on the tool below has to resolve out of THIS module's
# globals when PydanticAI inspects the signature -- `from __future__ import
# annotations` makes it a string, and a name local to a builder function is
# not there to be found -- while the notebook must still import this module
# with the framework absent. A guarded import satisfies both: the real names
# when it is installed, `None` when it is not, and nothing that needs them
# is reachable without `have_framework()` returning True first.
try:
    from pydantic_ai import Agent, RunContext
    from pydantic_ai.messages import ModelResponse, ToolCallPart
    from pydantic_ai.models.function import AgentInfo, FunctionModel
except ImportError:                                      # pragma: no cover
    Agent = RunContext = None
    ModelResponse = ToolCallPart = AgentInfo = FunctionModel = None

import tradefloor as tf
from tradefloor.integrations.common import Transcript, decision_model
from tradefloor.integrations.pydantic_ai import PydanticAIAdapter

SEED = 4242
DAYS = 5

#: The book the agent runs. Large enough that its orders are a real fraction
#: of daily volume, so execution costs something, and small enough that the
#: funding cap actually binds: at 2x leverage this is $20M of buying power
#: against roughly $40M of participation capacity across the four names. An
#: agent that sizes to the per-asset cap and ignores the funding one asks
#: for about twice what it can hold. See `mean_reversion`.
CASH = 10_000_000.0

#: The macro regime the run is pinned to, so the experiment is about the
#: agent rather than about a macro path that wandered.
POLICY_RATE = 0.04
DISCOUNT_RATE = 0.055
PINS = {"federal_funds_rate": POLICY_RATE,
        "corporate_bond_yield": DISCOUNT_RATE}

#: The counterfactual the notebook runs: shared history, then one arm gets a
#: rate shock and the other does not. Days rather than the template's twenty,
#: because a live decision costs money and ten decisions per arm is enough to
#: see whether the agent reads the change.
SHARED_DAYS = 5
BRANCH_DAYS = 5
SHOCK_BPS = 200
SHOCKED_POLICY_RATE = POLICY_RATE + SHOCK_BPS / 10_000
SHOCKED_DISCOUNT_RATE = DISCOUNT_RATE + SHOCK_BPS / 10_000

#: How often the agent is asked, in steps. Six is once a simulated day. The
#: market still advances every step; a portfolio manager does not re-decide
#: six times a day, and a model call per tick would multiply the bill by six
#: to measure the same thing.
DECISION_EVERY = 6

#: Four synthetic names. Nothing about them is real: the tickers, the
#: sectors and every fundamental are generated, so the rule below cannot be
#: smuggling in knowledge of a listed company. They differ in revenue
#: growth, which is the duration term in this model's valuation and so
#: decides how much a rate move is worth to each.
ROSTER = [
    ("TECH_A", "technology", 140.0, 0.30),
    ("TECH_B", "technology", 95.0, 0.22),
    ("BANK_A", "financial_services", 60.0, 0.05),
    ("STAPLE_A", "consumer_staples", 48.0, 0.02),
]

#: The recorded run the notebook replays. Committed, because it is INPUT --
#: the thing that makes the notebook reproducible without a key -- and not
#: output, which is what `artifacts/` is for.
def _repo_root() -> Path:
    """The repository root, found by marker rather than by counting parents.

    This file kept a fixed ``parents[2]`` climb after its three siblings had
    already been converted, so it was the one example still betting on its
    own depth -- and the bet lost the moment each integration gained a
    folder. A marker survives any future rearrangement or fails loudly, and
    those are the only two acceptable outcomes.
    """
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").exists():
            return parent
    raise RuntimeError("no pyproject.toml above this file; is the "
                       "repository layout intact?")


FIXTURE = (_repo_root()
           / "tests" / "fixtures" / "pydantic_ai" / "rate-shock.json")

#: The model a live run calls, in PydanticAI's `provider:name` spelling.
LIVE_MODEL = "anthropic:claude-opus-5"
LIVE_KEY_VAR = "ANTHROPIC_API_KEY"

#: The output-token ceiling one decision may cost. A constant rather than a
#: literal so the spend preview in the notebook and the call itself cannot
#: disagree about what is being bought.
LIVE_MAX_TOKENS = 1500

#: The explicit opt-in for live model calls, spelled like
#: TRADEFLOOR_SLOW_TESTS and required IN ADDITION to the key and the SDK.
#: See :func:`can_run_live` for why a key alone is not enough.
LIVE_OPT_IN_VAR = "TRADEFLOOR_LIVE_EXAMPLES"


@dataclass
class Desk:
    """The user's own dependency type, which the adapter never touches.

    It exists to prove a claim: your `deps_type`, your tools and your
    `RunContext` usage keep working inside the adapter, because the
    observation travels in the prompt and `deps` is passed through verbatim.
    """

    name: str
    #: The desk's ceiling on gross exposure, as a MULTIPLE of net worth --
    #: the same unit as `portfolio.max_leverage`, so the agent can compare
    #: the two without converting.
    gross_exposure_limit: float


#: 1.5x against the market's own 2.0x cap: a real constraint that still
#: leaves the agent somewhere to go.
#:
#: The first live recording used 0.25, which reads as 25% of net worth, and
#: the result is why this comment exists. The agent pinned the book at 0.25x,
#: and fourteen of its fifteen rationales were about having no room to add
#: risk; not one mentioned rates, in an experiment whose entire subject is a
#: rate shock. A demonstration agent constrained until it can only say "I am
#: at my limit" measures the constraint, not the agent.
DESK = Desk(name="systematic", gross_exposure_limit=1.5)


def universe() -> list:
    return list(tf.Universe([
        tf.Instrument(ticker, sector, initial_price=price,
                      shares_outstanding=4.0e8, eps=3.0,
                      book_value_per_share=15.0, revenue_growth=growth,
                      avg_volume=5.0e6, beta=1.0, short_interest=4.0e6)
        for ticker, sector, price, growth in ROSTER
    ]))


def mean_reversion(payload: dict) -> dict:
    """Buy what fell, trim what ran. The whole strategy is this function.

    Identical to the rule in `callable/five_days.py`, deliberately: the examples
    differ in who runs the rule, not in what the rule is.

    ``payload`` is the serialized observation: the allowlisted view of the
    market every framework is shown. The rule sizes against BOTH limits the
    payload states, because respecting only one is the mistake this
    environment is most likely to elicit:

    * ``max_order_shares`` is the PARTICIPATION cap, per asset: what this
      market can absorb from one order without the order becoming the price.
    * ``portfolio.buying_power`` is the FUNDING cap, shared across every
      asset: what this book can hold before the leverage limit refuses the
      trade outright. Refused, not reduced -- an oversized order buys
      nothing and costs the decision.

    The funding cap is usually the binding one. Sizing each of four names to
    its own participation cap here asks for about twice the buying power
    available, so the budget is split across the names being bought and each
    leg is then capped by participation.

    ``return_5d`` is None until five days have been observed. That is the
    serializer refusing to claim the price did not move when the record is
    simply short, and the rule holds until it can actually see.
    """
    book = payload["portfolio"]
    # None means no funding limit was configured, not a limit of zero.
    budget = book["buying_power"]
    if budget is None:
        budget = book["net_worth"]
    # Four fifths, so a price move between deciding and filling does not turn
    # a legal order into a refused one.
    budget *= 0.8

    buys = [a for a in payload["assets"]
            if a["return_5d"] is not None and a["return_5d"] < -0.02]
    sells = [a for a in payload["assets"]
             if a["return_5d"] is not None and a["return_5d"] > 0.02
             and a["position"] > 0]

    actions = []
    if buys:
        per_name = budget / len(buys)
        for asset in buys:
            shares = min(asset["max_order_shares"], per_name / asset["price"])
            if shares >= 1:
                actions.append({"symbol": asset["symbol"], "side": "BUY",
                                "quantity": shares})
    for asset in sells:
        actions.append({"symbol": asset["symbol"], "side": "SELL",
                        "quantity": asset["position"]})

    return {"actions": actions,
            "rationale": "five-day mean reversion, sized against buying "
                         "power and split across the names bought"}


def offline_model():
    """The framework's own offline model, running the rule above.

    A `FunctionModel` is handed the messages and the run's `AgentInfo`, and
    returns a `ModelResponse` exactly as a provider would. Answering with a
    `ToolCallPart` addressed to `info.output_tools[0].name` is how a model
    fills the run's output schema -- the adapter binds Tradefloor's shared
    decision model there -- so everything downstream of the wire runs for
    real: the schema is enforced, the output is validated, and a malformed
    answer would be retried by the framework.

    The prompt is the serialized observation as JSON, which is what
    `PydanticAIAdapter.render` sends, so the rule reads it back with
    `json.loads` and decides from exactly what a hosted model would see.
    """
    def decide(messages, info: AgentInfo) -> ModelResponse:
        prompt = [part.content for message in messages
                  for part in getattr(message, "parts", [])
                  if getattr(part, "part_kind", "") == "user-prompt"][-1]
        decision = mean_reversion(json.loads(prompt))
        return ModelResponse(
            parts=[ToolCallPart(info.output_tools[0].name, decision)])

    return FunctionModel(decide, model_name="offline-mean-reversion")


def build_agent(model=None):
    """The user's own agent, built the way a user would build one.

    `deps_type`, a tool and instructions of its own, none of which the
    adapter touches. `defer_model_check=True` so constructing it needs no
    API key: the model is supplied per run by the adapter.

    ``model`` is the model NAME the agent carries. A live run passes
    `LIVE_MODEL`; the offline path leaves it and lets the adapter override
    with a `FunctionModel` per run.
    """
    # `max_tokens` from the constant the spend preview quotes, so the number
    # a reader is shown before the run and the number the run is configured
    # with cannot drift apart.
    agent = Agent(model or LIVE_MODEL, deps_type=Desk,
                  defer_model_check=True,
                  model_settings={"max_tokens": LIVE_MAX_TOKENS},
                  instructions="You are a systematic portfolio manager.")

    @agent.tool
    def house_gross_exposure_limit(ctx: RunContext[Desk]) -> float:
        """The desk's own ceiling on gross exposure, as a multiple of net
        worth. Stricter than the market's `portfolio.max_leverage`, and in
        the same unit so the two can be compared directly.

        The agent's own tool, reading the agent's own deps -- both still
        work unchanged inside the adapter, which is the point of it.
        """
        return ctx.deps.gross_exposure_limit

    return agent


# -- live and replay ---------------------------------------------------------


def have_live_key() -> bool:
    """Whether a key is present, without reading or printing it."""
    return bool(os.environ.get(LIVE_KEY_VAR))


def have_framework() -> bool:
    """Whether PydanticAI and an Anthropic client are both installed.

    `find_spec` rather than an import, so asking the question costs nothing
    and does not drag a provider SDK into a process that only wants to
    replay.

    The `except` is not defensive clutter. `find_spec` returns None for an
    absent top-level module but RAISES for several near-misses -- a missing
    parent package, a module whose `__spec__` is None, an import hook that
    objects -- and a helper whose entire job is answering "can we?" must
    never be the thing that fails. A raised question is a no.
    """
    for module in ("pydantic_ai", "anthropic"):
        try:
            if importlib.util.find_spec(module) is None:
                return False
        except (ImportError, ValueError):
            return False
    return True


def can_run_live() -> bool:
    """True only when a live run was ASKED FOR and is possible.

    Three conditions, and the opt-in comes first:
    ``TRADEFLOOR_LIVE_EXAMPLES=1`` in the environment, then the API key,
    then the SDK. A credential existing in the environment is not consent
    to spend it: without the opt-in, a developer with a key exported who
    ran the slow suite would re-execute every live notebook and spend real
    money, having asked for neither -- and the replay-identity cell would
    then honestly read False against the committed recording, so the
    surprise bill would arrive dressed as a test failure. Replay is the
    default even when live is possible, because a recorded run is the
    point of these examples, and spending money should be a thing somebody
    typed rather than a thing their shell had set.
    """
    return (bool(os.environ.get(LIVE_OPT_IN_VAR))
            and have_live_key()
            and have_framework())


def live_agent(*, recorder=None, arm=""):
    """A `PydanticAIAdapter` calling the real model, recording as it goes."""
    return PydanticAIAdapter(build_agent(LIVE_MODEL), deps=DESK,
                             mode="live", recorder=recorder,
                             every=DECISION_EVERY, arm=arm)


def replay_agent(*, transcript=None, arm=""):
    """A `PydanticAIAdapter` replaying a recording.

    Needs no framework, no key and no network, which is the whole point:
    this is the path a reader of the notebook takes.

    `is None` and not `or`: `Transcript` defines `__len__`, so an EMPTY one
    is falsy, and `transcript or Transcript.load(FIXTURE)` would silently
    swap the committed fixture in for a caller who deliberately passed an
    empty transcript -- replaying a different run than the one asked for,
    and looking like it worked.
    """
    if transcript is None:
        transcript = Transcript.load(FIXTURE)
    return PydanticAIAdapter(mode="replay", transcript=transcript,
                             every=DECISION_EVERY, arm=arm)


def agent_for(mode: str, *, transcript=None, recorder=None, arm=""):
    """Whichever of the two the caller asked for, in one call."""
    if mode == "live":
        return live_agent(recorder=recorder, arm=arm)
    return replay_agent(transcript=transcript, arm=arm)


# -- the shipped demo --------------------------------------------------------


def main() -> dict:
    """A flat five-day run, offline, in seconds.

    The counterfactual experiment lives in the notebook beside this file,
    because it wants a real model and a recorded transcript to replay. This
    is the part that runs anywhere with no key at all.
    """
    pm = PydanticAIAdapter(build_agent(), deps=DESK, model=offline_model(),
                           every=DECISION_EVERY)

    scores = tf.evaluate({"pydantic_ai": pm}, seed=SEED, universe=universe(),
                         days=DAYS, cash=CASH)
    card = scores["pydantic_ai"]

    print(f"seed {SEED}, {DAYS} days, {len(ROSTER)} instruments")
    print(f"framework          {pm.info.reference()}")
    print(f"decisions          {len(pm.record)}")
    print(f"trades             {card.trades}")
    print(f"turnover           {card.turnover:,.0f}")
    print(f"pnl                {card.pnl:+,.0f}")
    print(f"return             {card.return_pct:+.2f}%")
    print(f"impact             {card.impact_bps:+.2f} bps")
    print(f"rejected           {card.rejected}")
    for entry in pm.record:
        acted = ", ".join(f"{a['side']} {a['quantity']:,.0f} {a['symbol']}"
                          for a in entry["decision"]["actions"]) or "hold"
        print(f"  day {entry['day']}  {acted}")

    # The structural gates, asserted rather than eyeballed: the agent was
    # consulted once per day, nothing it sent was refused by the market,
    # every error column is empty, and the decision really did come through
    # the shared model rather than some shape that happened to parse.
    #
    # `rejected == 0` is the one worth keeping honest. It is easy to satisfy
    # by trading nothing, and easy to fail by sizing against the
    # participation cap alone, which is what four independent agents did.
    assert len(pm.record) == DAYS, pm.record
    assert not card.errors, card.errors
    assert card.rejected == 0, card.rejected
    assert card.trades > 0, "a run that never traded proves nothing"
    assert decision_model().model_validate(
        pm.record[-1]["decision"]), pm.record[-1]

    return {"scorecard": card.as_dict(), "record": pm.record}


if __name__ == "__main__":
    main()
