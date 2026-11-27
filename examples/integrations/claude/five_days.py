"""A Claude agent trading a Tradefloor market, through ClaudeAdapter.

The same job as `callable/five_days.py`, line for line: build the same small
market, decide once a simulated day with the same five-day mean-reversion
rule, read the scorecard. The part that changes is who answers. Here the
decision travels through `tradefloor.integrations.claude.ClaudeAdapter` and
a real `anthropic.Anthropic` client.

Offline, which is the default, the client's HTTP transport answers locally:
it reads the request the SDK built, runs `mean_reversion` on the observation
inside it, and returns the answer as a Messages API response. So what runs
offline is the adapter and the SDK, with a deterministic function where the
model would sit, and no request leaves the machine. Its scorecard matches
`callable/five_days.py` because the rule and the market are the same.

Live, the same adapter calls a Claude model instead. It runs only when all
three are present: `TRADEFLOOR_LIVE_EXAMPLES=1`, `ANTHROPIC_API_KEY`, and
the SDK. A live run costs money and gives different answers from the rule.

    pip install "tradefloor[claude]"
    python examples/integrations/claude/five_days.py

Twenty simulated days, for the reason `callable/five_days.py` gives under
`OFFLINE_DAYS`: the rule needs five days of history before it can act.
"""

from __future__ import annotations

import json
import os

import tradefloor as tf
from tradefloor.integrations.claude import ClaudeAdapter, payload_of
from tradefloor.integrations.common import require

SEED = 4242
OFFLINE_DAYS = 20

#: The model a live run calls. The adapter names no default; an example
#: may, and this one names the current Opus.
LIVE_MODEL = "claude-opus-5-5"
LIVE_KEY_VAR = "ANTHROPIC_API_KEY"
LIVE_OPT_IN_VAR = "TRADEFLOOR_LIVE_EXAMPLES"

#: The name the offline run records as its model. Not a Claude model id.
OFFLINE_MODEL = "offline-mean-reversion"

#: The user's system prompt in a live run. The adapter adds its own brief
#: and the decision schema, so this says only who the agent is.
SYSTEM = "You manage a portfolio and decide once a simulated day."

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
    """The rule from `callable/five_days.py`, unchanged."""
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


def can_run_live() -> bool:
    """True only when a live run was asked for and is possible."""
    if not (os.environ.get(LIVE_OPT_IN_VAR) and os.environ.get(LIVE_KEY_VAR)):
        return False
    import importlib.util
    try:
        return importlib.util.find_spec("anthropic") is not None
    except Exception:                                    # noqa: BLE001
        return False


def offline_client():
    """A real SDK client whose transport answers with `mean_reversion`."""
    anthropic = require("anthropic", pip="anthropic",
                        purpose="this example drives the Anthropic SDK")
    httpx = require("httpx2", pip="anthropic",
                    purpose="the offline transport uses the SDK's httpx2")

    def answer(request):
        decision = mean_reversion(payload_of(json.loads(request.content)))
        return httpx.Response(200, json={
            "id": "msg_offline", "type": "message", "role": "assistant",
            "model": OFFLINE_MODEL, "stop_reason": "end_turn",
            "stop_sequence": None,
            "content": [{"type": "text", "text": json.dumps(decision)}],
            "usage": {"input_tokens": 0, "output_tokens": 0}})

    # The key never leaves the process: the transport above answers first.
    return anthropic.Anthropic(
        api_key="offline", max_retries=0,
        http_client=anthropic.DefaultHttpxClient(
            transport=httpx.MockTransport(answer)))


def main() -> dict:
    if can_run_live():
        anthropic = require("anthropic", pip="anthropic")
        agent = ClaudeAdapter(anthropic.Anthropic(), model=LIVE_MODEL,
                              system=SYSTEM, mode="live")
        print(f"Live: {OFFLINE_DAYS} calls to {LIVE_MODEL}.")
    else:
        agent = ClaudeAdapter(offline_client(), model=OFFLINE_MODEL,
                              mode="live")
    scores = tf.evaluate({"claude": agent}, seed=SEED, universe=universe(),
                         days=OFFLINE_DAYS)
    card = scores["claude"]

    print(f"seed {SEED}, {OFFLINE_DAYS} days, {len(ROSTER)} instruments")
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

    assert len(agent.record) == OFFLINE_DAYS, agent.record
    assert not card.errors, card.errors
    assert card.rejected == 0, card.rejected
    return {"scorecard": card.as_dict(), "record": agent.record}


if __name__ == "__main__":
    main()
