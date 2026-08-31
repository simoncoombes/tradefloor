"""A plain Python function trading a Tradefloor market, through the adapter.

The smallest complete integration: a decision rule written as one function,
run against a simulated market with the same validation, clipping and
record-keeping every framework adapter gets. No network, no API key, and it
finishes in seconds.

This is also the template the framework examples follow, so a reader can
compare them line for line. Each one does the same job:

    1. build a small market
    2. give the agent a simple mean-reversion rule over what it can observe
    3. run the evaluation and read the scorecard

The only part that changes between frameworks is step 2 -- who interprets
the observation and produces the decision. Everything else is Tradefloor,
and it is identical on purpose: if the harness changed shape per framework,
a comparison between two adapters would measure the harness.

Run it:

    python examples/integrations/callable_agent.py

The notebook beside this file runs the same market twice -- once with the
rule below, once with a function that calls a language model -- through the
identical adapter. The model half replays from a committed recording, so
the notebook needs no key; the constants and both decision functions live
HERE, because two definitions of one experiment drift into describing
different ones.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import tradefloor as tf
from tradefloor.integrations.callable import callable_agent
from tradefloor.integrations.common import require

SEED = 4242
DAYS = 5

#: The model the live half of the notebook calls, the key variable it
#: needs, and the output-token budget per decision. Replay -- the committed
#: default -- needs none of them.
LIVE_MODEL = "claude-opus-5"
LIVE_KEY_VAR = "ANTHROPIC_API_KEY"
LIVE_MAX_TOKENS = 1500

#: The explicit opt-in for live model calls, spelled like
#: TRADEFLOOR_SLOW_TESTS and required IN ADDITION to the key and the SDK.
#: See :func:`can_run_live` for why a key alone is not enough.
LIVE_OPT_IN_VAR = "TRADEFLOOR_LIVE_EXAMPLES"


def _repo_root() -> Path:
    """The repository root, found by marker rather than by counting parents.

    ``parents[2]`` was correct at this file's previous depth and would have
    broken silently when `examples/` gained a level -- the exact defect the
    FinRobot example carried through the same move. A marker survives any
    future rearrangement or fails loudly, and those are the only two
    acceptable outcomes.
    """
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").exists():
            return parent
    raise RuntimeError("no pyproject.toml above this file; is the "
                       "repository layout intact?")


#: The recorded model run. Committed under tests/fixtures/ like every other
#: recording, because the test suite and the notebook must read the SAME
#: file -- two copies of a recording are two recordings that can drift
#: apart.
FIXTURE = _repo_root() / "tests" / "fixtures" / "callable" / "five-days.json"

#: What the model is told, once, as the system prompt. It states the job,
#: the answer contract and the limits to respect; it says nothing about
#: what is coming, because the question is whether the model reads the
#: market, not whether it reads the instructions.
MANDATE = """\
You manage a portfolio inside a simulated financial market. The user message
is a JSON observation and is your only information: the tickers are
synthetic, so nothing you know about real securities applies.

Seek risk-adjusted returns while controlling downside. You are not required
to trade. Size every order within BOTH stated limits: max_order_shares per
order, and the portfolio's buying_power across the book.

Answer with a single JSON object and nothing else:
{"actions": [{"symbol": "<listed symbol>", "side": "BUY|SELL|HOLD",
"quantity": <shares>}], "rationale": "<one or two sentences>"}
An empty actions list means change nothing.
"""


def llm_decide(payload: dict) -> str:
    """One model decision. The same shape as :func:`mean_reversion`.

    That is the point of it: a function of the payload returning a
    decision, and nothing in the adapter, the validation or the market
    knows which of the two is deciding. The framework import lives inside
    the function, per the integrations rule -- replaying the recorded run
    never executes this line.

    Not deterministic, and not even askable: the current SDK's
    ``messages.create`` takes no temperature at all, so two live runs are
    two different agents by construction. That is why the notebook replays
    a recording instead of calling twice and hoping.
    """
    anthropic = require("anthropic", pip="anthropic>=1.0",
                        purpose="the live half of this example needs "
                                "'anthropic'")
    client = anthropic.Anthropic()
    response = client.messages.create(
        model=LIVE_MODEL, max_tokens=LIVE_MAX_TOKENS, system=MANDATE,
        messages=[{"role": "user",
                   "content": json.dumps(payload, indent=2)}])
    # The TEXT block, by type. The model may think before it answers, and
    # content[0] is then a thinking block; indexing it cost one recording.
    return "".join(block.text for block in response.content
                   if block.type == "text")


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

    The SDK probe is guarded, and the guard is part of the convention: a
    helper whose entire job is answering "can we?" must never be the thing
    that fails. Under an import blocker -- a restricted sandbox, a broken
    parent package -- ``find_spec`` RAISES instead of returning None, which
    would turn "the framework is absent" into a crash inside the very test
    written to prove it can be absent. A raised question is a no.
    """
    if not (os.environ.get(LIVE_OPT_IN_VAR) and have_live_key()):
        return False
    import importlib.util
    try:
        return importlib.util.find_spec("anthropic") is not None
    except Exception:
        return False


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

    ``payload`` is the serialized observation: the allowlisted view of the
    market the adapter shows every framework. The rule reads five-day
    returns and current positions, and it sizes against BOTH limits the
    payload states: ``max_order_shares``, the participation cap on what
    this market can absorb per order, and ``buying_power``, the funding
    headroom under the leverage cap. The first draft sized on
    participation alone -- at the time the payload named no other limit --
    and every order it sent asked for nine times the portfolio's equity
    and was refused. The payload now states the funding side too, so the
    rule takes the minimum instead of deriving the harness's rule by hand.

    ``return_5d`` is None until five days have been observed. That is the
    serializer refusing to claim the price did not move when the record is
    simply short, and the rule holds until it can actually see.
    """
    book = payload["portfolio"]
    budget = 0.15 * book["net_worth"]
    if book["buying_power"] is not None:
        budget = min(budget, book["buying_power"])
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


def main() -> dict:
    agent = callable_agent(mean_reversion)
    scores = tf.evaluate({"mean_reversion": agent},
                         seed=SEED, universe=universe(), days=DAYS)
    card = scores["mean_reversion"]

    print(f"seed {SEED}, {DAYS} days, {len(ROSTER)} instruments")
    print(f"decisions          {len(agent.record)}")
    print(f"trades             {card.trades}")
    print(f"turnover           {card.turnover:,.0f}")
    print(f"pnl                {card.pnl:+,.0f}")
    print(f"return             {card.return_pct:+.2f}%")
    print(f"impact             {card.impact_bps:+.2f} bps")
    for entry in agent.record:
        acted = ", ".join(f"{a['side']} {a['quantity']:,.0f} {a['symbol']}"
                          for a in entry["decision"]["actions"]) or "hold"
        print(f"  day {entry['day']}  {acted}")

    # The structural gates, asserted rather than eyeballed: the agent was
    # consulted once per day, nothing it sent was refused by the market, and
    # every error column is empty. A demo that can rot silently is a demo
    # that teaches whatever it has rotted into.
    assert len(agent.record) == DAYS, agent.record
    assert not card.errors, card.errors
    assert card.rejected == 0, card.rejected

    return {"scorecard": card.as_dict(), "record": agent.record}


if __name__ == "__main__":
    main()
