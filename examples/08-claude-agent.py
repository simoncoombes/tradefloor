"""A trading agent driven by Claude, scored against ground truth.

Run:

    pip install "tradefloor[claude]"
    export ANTHROPIC_API_KEY=...          # or: ant auth login
    python examples/08-claude-agent.py

Without a key, read `integrations/callable/five_days.ipynb` instead. It
replays a committed Claude recording through the callable adapter, so it
shows an LLM agent's run end to end with no call made. It also has the
record-and-replay pattern (`Transcript`) that this file leaves out, which
you need for tests that run in CI without a key.

What makes this worth doing here rather than on real data: the harness knows
why every price moved. Claude is asked for a portfolio AND for the factor it
believes moved prices most that day, and `evaluate` scores that answer
against the engine's own attribution. So the run reports two different things
that usually get conflated -- whether the model made money, and whether it was
right for the right reason. A model can score well on the first by accident.
Nobody can measure the second on real market data, because nobody knows the
answer there.

The second score has a floor well above zero. On pt-v20, the default, the
scored factor is `random_noise` or `fair_value_shift` on nearly every day, so
an agent that names `random_noise` every day scores about 55 to 70 per cent
on this market without reading anything. The run prints what that constant
answer scored on the same days, and Claude's figure means something only
where it is higher.

Claude decides once a day, on the day's last step. The harness scores the
driver against the attribution of the day it was named on, and by the last
step Claude has seen that day's overnight gap and five of its six steps. A
decision at the open would see only yesterday's moves and be scored on
today's.

Cost: one API call per day. The harness steps six times a day by default, so
the agent gates itself to one decision per day -- without that gate this is six
times the bill. The default run below is 20 days over 12 instruments, so 20
calls. At Claude Opus 5 rates ($5/MTok
in, $25/MTok out) with the system prompt cached, that is roughly $0.30-$0.60.
Raise `days` and it scales linearly. Nothing here needs a frontier model to
demonstrate the mechanism -- pass model="claude-haiku-4-5" for a cheap smoke
test, and expect worse answers.
"""

from __future__ import annotations

import os
import sys
from collections import Counter
from typing import Literal

import tradefloor as tf
from tradefloor.harness import FACTOR_NAMES

try:
    import anthropic
    from pydantic import BaseModel, Field
except ImportError:
    sys.exit('This example needs the extra: pip install "tradefloor[claude]"')


# The components the engine splits every price move into, read from the
# harness's own list. `evaluate` scores Claude's answer against exactly these
# names, so a list typed out here can fall behind, and it did three times.
# The last one missed `fair_value_shift`, which pt-v20 added in 0.8.5 and
# scores as the answer on about two days in five of this market, so Claude
# was marked wrong on days it was never offered the right answer.
Factor = Literal[FACTOR_NAMES]  # type: ignore[valid-type]


class Decision(BaseModel):
    """What Claude returns at each decision point.

    Weights rather than share counts, deliberately. Share counts would make
    the model do arithmetic against each instrument's price and average
    volume, which is the harness's job and not the interesting part of the
    task. Weights keep the decision about conviction.
    """

    weights: dict[str, float] = Field(
        description=(
            "Target portfolio weight per ticker, from -1.0 (max short) to 1.0 "
            "(max long). Weights are fractions of gross exposure and should "
            "sum in absolute value to at most 1.0. Omit a ticker to hold "
            "nothing in it."
        )
    )
    driver: Factor = Field(
        description=(
            "The factor you believe moved prices most today, adding up its "
            "push on every name whichever way it went."
        )
    )
    reasoning: str = Field(
        description="Two sentences at most, explaining the position, not the driver."
    )


SYSTEM = """\
You are trading a simulated equity market. It is a model, not a real market, \
and you are being measured on two separate things: the return of the \
portfolio you choose, and whether you correctly identify what moved prices.

What you can see: current prices, your positions, recent returns, and the top \
of the order book. What you cannot see, and must infer: each company's fair \
value, and the decomposition of any price move into its causes.

How this market works, stated plainly so you are not guessing at mechanics:

- Price is fair value times exp(s), where s is a log mispricing that reverts \
toward zero on a 60-day half-life.
- Your orders consume real depth. A large order pays worse prices because it \
ate the book, so size relative to average daily volume matters more than \
notional size.
- Returns carry almost no lag-one autocorrelation, %+.4f on the shipped \
model (%s), which is inside the range real equities show. Momentum is not a \
free edge here, though it was in earlier versions of this simulator.

You also name the factor that moved prices most today. The engine splits \
every name's move into these factors: %s. For each factor it adds up the \
size of its push on every name, up or down alike, and the factor with the \
largest total is the answer you are scored against. `fair_value_shift` is \
the part of the day's shocks that changed a company's fair value for good \
rather than its mispricing.

Give a portfolio, not a trade list. Concentration is allowed and often \
correct; equal-weighting everything is a way of declining to have a view.\
""" % (tf.envelope.CERTIFIED["return_acf1"], tf.envelope.PRESET,
       ", ".join(FACTOR_NAMES))
# The autocorrelation is read from the envelope rather than typed: it read
# +0.0239 here, a figure no current preset record carries, until 0.8.0.


class ClaudeTrader:
    """Implements tradefloor's agent protocol: act(), and the optional explain()."""

    def __init__(
        self,
        *,
        model: str = "claude-opus-5",
        effort: str = "medium",
        max_names: int = 12,
        client: "anthropic.Anthropic | None" = None,
    ) -> None:
        # A bare constructor resolves ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN,
        # or an `ant auth login` profile, in that order. Do not pass a key.
        self.client = client or anthropic.Anthropic()
        self.model = model
        self.effort = effort
        self.max_names = max_names
        self._drivers: dict[int, str] = {}
        self._last_prices: dict[str, float] = {}
        self._log: list[tuple[int, str, str]] = []

    # -- the observation Claude actually sees ---------------------------

    def _market_table(self, obs) -> str:
        rows = ["ticker   price     since_last   position     adv"]
        for ticker in obs.tickers[: self.max_names]:
            price = obs.price(ticker)
            prev = self._last_prices.get(ticker)
            move = "     -   " if prev is None else "%+8.2f%%" % ((price / prev - 1) * 100)
            held = obs.portfolio.positions.get(ticker)
            qty = held.quantity if held else 0.0
            rows.append(
                "%-8s %8.2f  %s  %10.0f  %8.0f"
                % (ticker, price, move, qty, obs.avg_volume(ticker))
            )
        return "\n".join(rows)

    def _prompt(self, obs) -> str:
        book_lines = []
        for ticker in obs.tickers[:3]:
            book = obs.book(ticker)
            book_lines.append(
                "%-8s bid %8.2f  ask %8.2f" % (ticker, book.best_bid, book.best_ask)
            )
        return (
            "Day %d. Cash %.0f. Net worth %.0f.\n\n"
            "%s\n\nTop of book (first three):\n%s\n\n"
            "This is the last decision of the day; since_last is the move "
            "since the same point yesterday. Choose target weights for the "
            "next day, and name the factor that moved prices most today."
            % (
                obs.day,
                obs.portfolio.cash,
                obs.portfolio.net_worth(obs.engine),
                self._market_table(obs),
                "\n".join(book_lines),
            )
        )

    # -- the agent protocol ----------------------------------------------

    def act(self, obs) -> dict[str, float]:
        # The harness steps six times a day by default. Deciding on every step
        # would be six API calls a day and six times the bill, for a horizon
        # the model was told is daily. Decide once, on the day's last step,
        # and hold: the driver is scored against the attribution of the day
        # it is named on, and by the last step Claude has seen most of it.
        if not obs.is_last_step_of_day:
            return {}

        response = self.client.messages.parse(
            model=self.model,
            max_tokens=4000,
            # The rules of the market never change, so they cache. The volatile
            # market state goes in the user turn, after the breakpoint, or the
            # cache would be invalidated on every single call.
            system=[{"type": "text", "text": SYSTEM,
                     "cache_control": {"type": "ephemeral"}}],
            thinking={"type": "adaptive"},
            output_config={"effort": self.effort},
            messages=[{"role": "user", "content": self._prompt(obs)}],
            output_format=Decision,
        )

        if response.stop_reason == "refusal":
            # Hold rather than guess. A refused turn is not a flat view.
            return {}

        decision = response.parsed_output
        self._drivers[obs.day] = decision.driver
        self._log.append((obs.day, decision.driver, decision.reasoning))

        # Weights to share deltas. The harness wants quantities, and this
        # arithmetic is what the model was deliberately not asked to do.
        gross = obs.portfolio.net_worth(obs.engine)
        orders: dict[str, float] = {}
        for ticker, weight in decision.weights.items():
            if ticker not in obs.tickers:
                continue  # a hallucinated ticker buys nothing
            weight = max(-1.0, min(1.0, weight))
            price = obs.price(ticker)
            if price <= 0:
                continue
            target = (weight * gross) / price
            held = obs.portfolio.positions.get(ticker)
            delta = target - (held.quantity if held else 0.0)
            # Cap participation so the agent cannot pay unbounded impact in
            # one name. The engine would let it; the result would be noise.
            cap = 0.05 * obs.avg_volume(ticker)
            orders[ticker] = max(-cap, min(cap, delta))

        self._last_prices = {t: obs.price(t) for t in obs.tickers}
        return orders

    def explain(self, day: int) -> str | None:
        """The factor Claude named on this day's last step.

        The harness calls this after the day's last step and checks the
        answer against the engine's attribution for that day, which turns a
        plausible-sounding rationale into a score. None, for a day whose call
        failed or was refused, leaves the day unscored instead of scoring an
        older answer against it.
        """
        return self._drivers.get(day)


def main() -> None:
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        print("No API key in the environment. `ant auth login` also works.\n"
              "Continuing anyway -- the SDK resolves a stored profile if there is one.\n")

    universe = tf.Universe.random(12, seed=7)
    claude = ClaudeTrader()

    print("Running Claude against the reference agents. 20 API calls.\n")

    agents = tf.reference_agents(seed=3)
    agents["claude"] = claude

    scores = tf.evaluate(
        agents, seed=2026, universe=universe, days=20, max_leverage=2.0,
    )

    # A run where every decision failed is not a result. Without this the
    # table below reports claude at zero pnl and no explanation accuracy,
    # which reads as a weak agent -- when in fact it was never asked. The
    # commonest cause is no resolvable credential, which surfaces at request
    # time rather than at construction, so it cannot be caught up front.
    failures = scores["claude"].errors
    if failures and not claude._log:
        sys.exit(
            "Claude was never reached -- all %d decisions failed.\n"
            "First error: %s\n\n"
            "If that is a credentials problem, set ANTHROPIC_API_KEY or run\n"
            '`ant auth login`. The extra is: pip install "tradefloor[claude]"'
            % (len(failures), failures[0])
        )

    print("%-16s %12s %9s %12s" % ("agent", "pnl", "impact", "why-right"))
    print("-" * 54)
    for s in tf.leaderboard(scores):
        acc = "     -" if s.explanation_accuracy is None else "%5.0f%%" % (s.explanation_accuracy * 100)
        print("%-16s %12.0f %9.1f %12s" % (s.name, s.pnl, s.impact_bps, acc))

    print("\nP&L over buy-and-hold:")
    for name, excess in tf.versus_buy_and_hold(scores).items():
        print("  %-16s %+12.0f" % (name, excess))
    # Only where the Oracle is a ceiling: on pt-v20, the default, market
    # moves mostly stick and no capture ratio is reported.
    withheld = tf.capture_withheld(scores)
    if withheld is None:
        print("\nCapture against the Oracle:")
        for name, ratio in tf.capture_ratio(scores).items():
            print("  %-16s %+.3f" % (name, ratio))
    else:
        print("\n" + withheld)

    # The floor for the why-right column: the best single factor named on
    # every scored day. On pt-v20 it is `random_noise` at 55 to 70 per cent
    # on this market, so a figure near it says Claude read nothing.
    scored = scores["claude"].explanations
    if scored:
        best, hits = Counter(actual for _, actual in scored).most_common(1)[0]
        print("\nNaming %s every day would have scored %.0f%% on the same %d days."
              % (best, 100 * hits / len(scored), len(scored)))

    print("\nWhat Claude said, and whether the engine agreed:")
    for (claimed, actual), (day, _, why) in list(zip(scored, claude._log))[:5]:
        mark = "right" if claimed == actual else "engine: " + actual
        print("  day %-3d %-18s %-24s %s" % (day, claimed, mark, why[:48]))

    print(
        "\nOne seed ranks the seed, not the agents. Before concluding anything,"
        "\nrun tf.rank(...) across a dozen seeds -- a single market picks the"
        "\ntop agent about half the time."
    )


if __name__ == "__main__":
    main()
