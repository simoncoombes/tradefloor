"""An OpenAI Agents SDK agent trading a Tradefloor market, offline.

The same job as ``callable_agent.py`` beside it, so the two can be read line
for line. Only step 2 changes -- who turns the observation into a decision:

    1. build a small market
    2. an agents.Agent, run by the real SDK, decides
    3. run the evaluation and read the scorecard

## Why there is no API key here, and what is real anyway

An example that needed a key would not run in CI, would cost the reader
money, and would answer differently every time -- so this one substitutes
the SDK's own deterministic model double, ``agents.testing.ScriptedModel``,
through ``RunConfig(model=...)``. That override sits at the framework's model
boundary and nowhere else, so EVERYTHING between the adapter and the model is
the genuine SDK: the agent, its instructions, the turn loop, the bound output
type, and the strict-schema validation of what comes back.

The scripted model applies the same five-day mean-reversion rule the callable
example uses. That is the honest offline stand-in for a language model: a
deterministic decision function, sitting exactly where the model would sit,
reading exactly the payload the model would read.

To run this against a real model instead, delete ``model=...`` from the
adapter and give the agent one:

    pm = Agent(name="Portfolio Manager", instructions=..., model=LIVE_MODEL)
    agent = OpenAIAgentsAdapter(pm, mode="live", recorder=Transcript())

with ``OPENAI_API_KEY`` set and ``pip install "tradefloor[openai-agents]"``.
``openai_agents_agent.ipynb`` beside this file does exactly that, and ships
the recording it made.

## The second half: replay

The run is recorded into a ``Transcript`` and then replayed. Replay reads
recorded responses keyed by a digest of the exact input, and needs no SDK, no
key and no network -- which is how somebody reproduces a paid experiment for
free. The example asserts the two runs made identical decisions, because a
replay that quietly diverged would be worse than no replay at all.

## The filename

``openai_agents_agent.py``, not ``openai_agents.py``. The doubled word reads
oddly enough to invite tidying, so the two reasons not to, in order.

Every example here is ``<framework>_agent.py`` -- ``callable_agent.py``,
``pydantic_ai_agent.py``, ``langgraph_agent.py`` -- and renaming this one
alone would break the pattern a reader uses to find the others.

And the ``_agent`` suffix is what keeps an example from SHADOWING the package
it demonstrates. Running a script puts its directory first on ``sys.path``,
so ``examples/integrations/langgraph.py`` would shadow the real ``langgraph``
for its own import, and ``pydantic_ai.py`` would shadow ``pydantic_ai``. This
file has no such clash of its own -- the SDK imports as ``agents``, not as
``openai_agents`` -- but it follows the rule that makes its siblings safe,
because a convention with one exception is a convention nobody trusts.

Run it:

    python examples/integrations/openai_agents_agent.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib

# Before the SDK is imported: its trace provider reads this once, on first
# use, and caches it. Nothing in an example should phone home.
os.environ.setdefault("OPENAI_AGENTS_DISABLE_TRACING", "1")

import tradefloor as tf
from tradefloor.integrations.common import Transcript
from tradefloor.integrations.openai_agents import (OpenAIAgentsAdapter,
                                                   payload_of)

SEED = 4242
DAYS = 5

#: The model a live run calls, the key variable it needs, and the per-decision
#: turn budget. Replay -- the committed default -- needs none of them.
#:
#: The model is pinned to a dated snapshot rather than a moving alias. A
#: recording is evidence, and evidence has to say what produced it: an alias
#: that silently advances turns a committed transcript into a claim about a
#: model nobody can identify afterwards.
LIVE_MODEL = "gpt-5.2"
LIVE_KEY_VAR = "OPENAI_API_KEY"

#: One turn is one model call, so this is the ceiling on what a single
#: decision can cost. It is a constant rather than a literal in two places
#: because the notebook prints a spend estimate from it before the run cell
#: spends anything, and a preview that could disagree with the call is worse
#: than no preview.
LIVE_MAX_TURNS = 6

#: The explicit opt-in for live model calls, spelled like
#: TRADEFLOOR_SLOW_TESTS and required IN ADDITION to the key and the SDK.
#: See :func:`can_run_live` for why a key alone is not enough.
LIVE_OPT_IN_VAR = "TRADEFLOOR_LIVE_EXAMPLES"


def _repo_root() -> pathlib.Path:
    """The repository root, found by marker rather than by counting parents.

    ``parents[2]`` is correct at this file's current depth and would break
    silently the next time ``examples/`` gains a level -- which has already
    happened once on this branch, and which the FinRobot example carried
    through unnoticed. A marker survives any future rearrangement or fails
    loudly, and those are the only two acceptable outcomes.
    """
    for parent in pathlib.Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").exists():
            return parent
    raise RuntimeError("no pyproject.toml above this file; is the repository "
                       "layout intact?")


#: The recorded run the notebook replays. Committed, so a reader with no
#: key, no network and no SDK still re-executes the whole experiment: the
#: MARKET runs for real either way, and only the agent's answers come from
#: the recording, keyed by the exact input it was sent.
FIXTURE = (_repo_root() / "tests" / "fixtures" / "openai_agents"
           / "five-days.json")


def have_live_key() -> bool:
    return bool(os.environ.get(LIVE_KEY_VAR))


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
    if not os.environ.get(LIVE_OPT_IN_VAR) or not have_live_key():
        return False
    try:
        return importlib.util.find_spec("agents") is not None
    except (ImportError, ValueError):
        # `find_spec` returns None for a module that is merely absent, but
        # it RAISES when a finder on `sys.meta_path` objects -- an import
        # blocker, a restricted sandbox, a broken parent package. Every one
        # of those means the same thing here: no live run. Letting it
        # propagate would abort the notebook on its first cell instead of
        # taking the replay path, which is the one thing this function
        # exists to prevent.
        return False


def missing_for_live() -> list[str]:
    """Which of the three conditions are absent, in the order to fix them.

    The notebook prints this, so the mode cell reads as a recipe rather
    than as a refusal.
    """
    missing = []
    if not os.environ.get(LIVE_OPT_IN_VAR):
        missing.append(f"{LIVE_OPT_IN_VAR}=1")
    if not have_live_key():
        missing.append(LIVE_KEY_VAR)
    try:
        if importlib.util.find_spec("agents") is None:
            missing.append('pip install "tradefloor[openai-agents]"')
    except (ImportError, ValueError):
        missing.append('pip install "tradefloor[openai-agents]"')
    return missing


#: Four synthetic names. Nothing about them is real: the tickers, the
#: sectors and every fundamental are generated, so the rule below cannot be
#: smuggling in knowledge of a listed company.
ROSTER = [
    ("TECH_A", "technology", 140.0, 0.30),
    ("TECH_B", "technology", 95.0, 0.22),
    ("BANK_A", "financial_services", 60.0, 0.05),
    ("STAPLE_A", "consumer_staples", 48.0, 0.02),
]


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

    Identical to the rule in ``callable_agent.py``, deliberately: the point
    of the four integration examples is that the harness, the observation and
    the decision are the same, and only the thing making the decision
    changes.

    ``payload`` is the serialized observation -- the allowlisted view every
    framework is shown. The rule sizes against BOTH limits the payload
    states: ``max_order_shares``, the participation cap on what this market
    can absorb, and a fixed slice of net worth, because the harness runs a 2x
    leverage cap and an order the book cannot fund is refused. Sizing on
    participation alone asks for nine times the portfolio's equity.

    ``return_5d`` is None until five days have been observed. That is the
    serializer refusing to claim the price did not move when the record is
    simply short, and the rule holds until it can actually see.
    """
    budget = 0.15 * payload["portfolio"]["net_worth"]
    actions = []
    for asset in payload["assets"]:
        move = asset["return_5d"]
        if move is None:
            continue
        if move < -0.02:
            quantity = min(asset["max_order_shares"],
                           budget / asset["price"])
            actions.append({"symbol": asset["symbol"], "side": "BUY",
                            "quantity": quantity})
        elif move > 0.02 and asset["position"] > 0:
            actions.append({"symbol": asset["symbol"], "side": "SELL",
                            "quantity": asset["position"]})
    return {"actions": actions,
            "rationale": "five-day mean reversion, participation-sized"}


def offline_model(turns: int = 32):
    """The SDK's own deterministic model double, applying the rule above.

    ``payload_of`` reads the observation back out of the call the adapter
    made, so this decides from exactly the dict a real model would be shown
    -- not from a copy this script kept on the side, which could drift.
    """
    from agents.testing import ModelStep, ScriptedModel, assistant_message

    def answer(call):
        decision = mean_reversion(payload_of(call))
        return [assistant_message(json.dumps(decision))]

    return ScriptedModel([ModelStep.respond(answer) for _ in range(turns)])


def portfolio_manager():
    """The user's agent: their name, their instructions, their model.

    Nothing Tradefloor-specific is written into it. The adapter binds the
    decision contract on a CLONE and sends its standing brief as a message,
    so this object is exactly what its author wrote and is what a reader
    would run anywhere else.
    """
    from agents import Agent

    return Agent(
        name="Portfolio Manager",
        instructions=("You manage a concentrated equity book. You prefer "
                      "buying weakness and trimming strength, and you would "
                      "rather do nothing than force a trade."),
    )


def main() -> dict:
    transcript = Transcript()

    # -- the live run, against the SDK's offline model --------------------
    live = OpenAIAgentsAdapter(agent=portfolio_manager(), mode="live",
                               model=offline_model(), recorder=transcript,
                               arm="live")
    transcript.meta.update(live.provenance())
    scores = tf.evaluate({"pm": live}, seed=SEED, universe=universe(),
                         days=DAYS)
    card = scores["pm"]

    print(f"seed {SEED}, {DAYS} days, {len(ROSTER)} instruments")
    print(f"framework          {live.info.framework} "
          f"{live.info.framework_version}")
    print(f"decisions          {len(live.record)}")
    print(f"trades             {card.trades}")
    print(f"turnover           {card.turnover:,.0f}")
    print(f"pnl                {card.pnl:+,.0f}")
    print(f"return             {card.return_pct:+.2f}%")
    print(f"impact             {card.impact_bps:+.2f} bps")
    for entry in live.record:
        acted = ", ".join(f"{a['side']} {a['quantity']:,.0f} {a['symbol']}"
                          for a in entry["decision"]["actions"]) or "hold"
        print(f"  day {entry['day']}  {acted}")

    # -- the same experiment, replayed for free ---------------------------
    replayed = OpenAIAgentsAdapter(mode="replay", transcript=transcript,
                                   arm="replay")
    again = tf.evaluate({"pm": replayed}, seed=SEED, universe=universe(),
                        days=DAYS)
    print(f"replayed           {len(transcript)} interactions, no SDK needed")

    # The structural gates, asserted rather than eyeballed: the agent was
    # consulted once per day, nothing it sent was refused by the market,
    # every error column is empty, and the replay reproduced the run rather
    # than quietly diverging from it. A demo that can rot silently is a demo
    # that teaches whatever it has rotted into.
    assert len(live.record) == DAYS, live.record
    assert not card.errors, card.errors
    assert card.rejected == 0, card.rejected
    assert [e["decision"] for e in replayed.record] == \
           [e["decision"] for e in live.record], "the replay diverged"
    assert again["pm"].pnl == card.pnl, "the replayed market differed"

    return {"scorecard": card.as_dict(), "record": live.record,
            "transcript": transcript.as_dict()}


if __name__ == "__main__":
    main()
