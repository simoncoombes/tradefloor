"""A trading agent driven by Claude, scored against ground truth.

Run:

    pip install "tradefloor[claude]"
    python examples/08-claude-agent.py

That replays a committed Claude run, `tests/fixtures/claude/example-08.json`,
with no key and no network. The market re-executes for real and only Claude's
answers come from the file, each looked up by a digest of the exact prompt
Claude was sent. If anything in the prompt moves, the lookup misses and the
run stops at that day with a message naming it. The recording is in the
repository, not the package, so the replay needs a clone.

To call Claude yourself, opt in and give it a key:

    export TRADEFLOOR_LIVE_EXAMPLES=1
    export ANTHROPIC_API_KEY=...          # or: ant auth login
    python examples/08-claude-agent.py            # live, writes nothing
    python examples/08-claude-agent.py --record   # live, rewrites the fixture

A key in the environment is not enough on its own, because having one is not
the same as asking to spend it. The opt-in is the one the integration
examples use (`integrations/callable/five_days.py`).

What makes this worth doing here rather than on real data: the harness knows
why every price moved. Claude is asked for a portfolio AND for the factor it
believes moved prices most that day, and `evaluate` scores that answer
against the engine's own attribution. So the run reports two different things
that usually get conflated -- whether the model made money, and whether it was
right for the right reason. A model can score well on the first by accident.
Nobody can measure the second on real market data, because nobody knows the
answer there.

The second score has a floor well above zero. On pt-v20, the default,
`random_noise` moves prices most on almost every day, so an agent that names
it every day scores 95 to 100 per cent on this market without reading
anything (seeds 2026 and 1 to 5; `jump` took one day in twenty on three of
them). The run prints what that constant answer scored on the same days, and
Claude's figure means something only where it is higher.

Claude decides once a day, on the day's last step. The harness scores the
driver against the attribution of the whole day it was named on, open to
close, and by the last step Claude has seen that day's overnight gap and five
of its six steps. A decision at the open would see only yesterday's moves and
be scored on today's.

A live run costs one API call per day, and a replay costs nothing. The
harness steps six times a day by default, so the agent gates itself to one
decision per day -- without that gate this is six times the bill. The run
below is 20 days over 12 instruments, so 20 calls. At Claude Opus 5 rates
($5/MTok in, $25/MTok out) with the system prompt cached, that is roughly
$0.30-$0.60. Raise `DAYS` and it scales linearly. Nothing here needs a
frontier model to demonstrate the mechanism -- pass model="claude-haiku-4-5"
for a cheap smoke test, and expect worse answers.
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Literal

import tradefloor as tf
from tradefloor.harness import DRIVER_NAMES
from tradefloor.integrations.common import (
    ReplayMiss, Transcript, digest, preset_of, replay_response)

try:
    from pydantic import BaseModel, Field
except ImportError:
    sys.exit('This example needs the extra: pip install "tradefloor[claude]"')

#: The market seed and the length of the run, one decision a day.
SEED, DAYS = 2026, 20

#: The committed recording, found by walking up from this file, and the
#: variable that must be set before anything here calls Claude.
FIXTURE_IN_REPO = Path("tests") / "fixtures" / "claude" / "example-08.json"
LIVE_OPT_IN_VAR = "TRADEFLOOR_LIVE_EXAMPLES"
MAX_TOKENS = 4000


# The factors that move a price, read from the harness's own list. `evaluate`
# scores Claude's answer against exactly these names, so a list typed out here
# can fall behind, and it did three times. `fair_value_shift` is not offered:
# it moves no price, and the harness never scores it as the answer.
Factor = Literal[DRIVER_NAMES]  # type: ignore[valid-type]


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
every name's price move into these factors: %s. For each factor it adds up \
the size of its push on every name over the whole day, open to close, up or \
down alike, and the factor with the largest total is the answer you are \
scored against. A shock counts at its full size whether it moved the \
company's fair value or only its mispricing.

Give a portfolio, not a trade list. Concentration is allowed and often \
correct; equal-weighting everything is a way of declining to have a view.\
""" % (tf.envelope.CERTIFIED["return_acf1"], tf.envelope.PRESET,
       ", ".join(DRIVER_NAMES))
# The autocorrelation is read from the envelope rather than typed: it read
# +0.0239 here, a figure no current preset record carries, until 0.8.0.


def refuse_a_different_question(transcript: Transcript) -> None:
    """Refuse a recording made under another system prompt or answer schema.

    Each answer is keyed by a digest of the day's prompt alone, so a change
    to `SYSTEM` or to `Decision` would replay every answer against a
    question Claude was never asked. This says which one moved.
    """
    for field, now in (("instructions_digest", digest(SYSTEM)),
                       ("decision_schema_digest",
                        digest(Decision.model_json_schema()))):
        if transcript.meta.get(field) != now:
            raise ReplayMiss(
                f"the recording's {field} is {transcript.meta.get(field)} "
                f"and this file's is {now}: the recorded answers are to a "
                "different question. Re-record with --record.")


def fixture_path() -> Path | None:
    """The committed recording, or None outside a repository checkout."""
    for parent in Path(__file__).resolve().parents:
        if (parent / FIXTURE_IN_REPO).is_file():
            return parent / FIXTURE_IN_REPO
    return None


def fixture_target() -> Path | None:
    """Where --record writes: under the checkout's `tests/fixtures/`."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "tests" / "fixtures").is_dir():
            return parent / FIXTURE_IN_REPO
    return None


class ClaudeTrader:
    """Implements tradefloor's agent protocol: act(), and the optional explain().

    Given a ``transcript``, it replays Claude's recorded answers and never
    builds a client. Otherwise it calls Claude, and given a ``recorder`` it
    writes down every exchange so that the run can be replayed.
    """

    def __init__(
        self,
        *,
        model: str = "claude-opus-5",
        effort: str = "medium",
        max_names: int = 12,
        client: "anthropic.Anthropic | None" = None,
        transcript: Transcript | None = None,
        recorder: Transcript | None = None,
    ) -> None:
        self.transcript = transcript
        self.recorder = recorder
        if transcript is not None:
            refuse_a_different_question(transcript)
            model = transcript.meta["model"]
            effort = transcript.meta["generation"]["effort"]
            client = None
        elif client is None:
            try:
                import anthropic
            except ImportError:
                sys.exit('A live run needs the extra: '
                         'pip install "tradefloor[claude]"')
            # A bare constructor resolves ANTHROPIC_API_KEY,
            # ANTHROPIC_AUTH_TOKEN, or an `ant auth login` profile, in that
            # order. Do not pass a key.
            client = anthropic.Anthropic()
        self.client = client
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

        prompt = self._prompt(obs)
        key = digest(prompt)
        if self.transcript is not None:
            # Raises ReplayMiss, which ends the run, if the recording holds
            # no answer to this exact prompt.
            answer = json.loads(replay_response(
                self.transcript, key, step=obs.step, day=obs.day,
                preset=preset_of(obs)))
        else:
            answer = self._ask(prompt)
            if self.recorder is not None:
                if not self.recorder.meta:
                    self.recorder.meta.update(self.provenance(obs))
                self.recorder.record({
                    "arm": "claude", "step": obs.step, "day": obs.day,
                    "digest": key, "prompt": prompt,
                    "response": json.dumps(answer)})

        if answer["stop_reason"] == "refusal":
            # Hold rather than guess. A refused turn is not a flat view.
            return {}

        decision = Decision.model_validate(answer["output"])
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

    def _ask(self, prompt: str) -> dict:
        """One live call, returned in the form the recording keeps."""
        response = self.client.messages.parse(
            model=self.model,
            max_tokens=MAX_TOKENS,
            # The rules of the market never change, so they cache. The volatile
            # market state goes in the user turn, after the breakpoint, or the
            # cache would be invalidated on every single call.
            system=[{"type": "text", "text": SYSTEM,
                     "cache_control": {"type": "ephemeral"}}],
            thinking={"type": "adaptive"},
            output_config={"effort": self.effort},
            messages=[{"role": "user", "content": prompt}],
            output_format=Decision,
        )
        output = (None if response.stop_reason == "refusal"
                  else response.parsed_output.model_dump())
        return {"stop_reason": response.stop_reason, "output": output}

    def provenance(self, obs) -> dict:
        """What the recording's meta says about how it was made.

        The prompt digest keys each answer. These fields cover what the
        digest does not: the model, the system prompt, the answer schema
        and the market preset, which `refuse_a_different_question` and
        `replay_response` check before a replay looks anything up.
        """
        return {
            "framework": "anthropic-sdk", "provider": "anthropic",
            "model": self.model, "agent_name": "ClaudeTrader",
            "generation": {"max_tokens": MAX_TOKENS, "effort": self.effort,
                           "thinking": "adaptive"},
            "instructions_digest": digest(SYSTEM),
            "decision_schema_digest": digest(Decision.model_json_schema()),
            "decision_every_steps": obs.steps_per_day,
            "seed": SEED, "days": DAYS,
            "tradefloor_version": tf.__version__,
            "model_preset": preset_of(obs),
        }

    def explain(self, day: int) -> str | None:
        """The factor Claude named on this day's last step.

        The harness calls this after the day's close and checks the
        answer against the engine's attribution for that day, which turns a
        plausible-sounding rationale into a score. None, for a day whose call
        failed or was refused, leaves the day unscored instead of scoring an
        older answer against it.
        """
        return self._drivers.get(day)


def main(argv: list[str] | None = None) -> None:
    record = "--record" in (sys.argv[1:] if argv is None else argv)
    live = bool(os.environ.get(LIVE_OPT_IN_VAR))
    if record and not live:
        sys.exit(f"--record calls Claude {DAYS} times and rewrites "
                 f"{FIXTURE_IN_REPO.as_posix()}. Set {LIVE_OPT_IN_VAR}=1 and "
                 "ANTHROPIC_API_KEY to ask for that.")

    if live:
        if not (os.environ.get("ANTHROPIC_API_KEY")
                or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
            print("No API key in the environment. `ant auth login` also works.\n"
                  "Continuing anyway -- the SDK resolves a stored profile if there is one.\n")
        target = fixture_target()
        if record and target is None:
            sys.exit("--record writes under the repository's tests/fixtures/, "
                     "and there is none above this file.")
        claude = ClaudeTrader(recorder=Transcript() if record else None)
        print(f"Running Claude against the reference agents. {DAYS} API calls.\n")
    else:
        path = fixture_path()
        if path is None:
            sys.exit(
                f"No recording at {FIXTURE_IN_REPO.as_posix()} above this file. "
                "It is in the tradefloor repository, not in the package, so "
                "the replay needs a clone. To call Claude instead, set "
                f"{LIVE_OPT_IN_VAR}=1 and ANTHROPIC_API_KEY.")
        transcript = Transcript.load(path)
        claude = ClaudeTrader(transcript=transcript)
        print(f"Replaying {len(transcript)} answers {claude.model} gave on "
              f"{transcript.meta.get('recorded_utc', 'an unrecorded date')}, "
              f"from {FIXTURE_IN_REPO.as_posix()}.\nNo call is made. "
              f"{LIVE_OPT_IN_VAR}=1 with a key runs it live.\n")

    universe = tf.Universe.random(12, seed=7)
    agents = tf.reference_agents(seed=3)
    agents["claude"] = claude

    try:
        scores = tf.evaluate(
            agents, seed=SEED, universe=universe, days=DAYS, max_leverage=2.0,
        )
    except ReplayMiss as miss:
        sys.exit(f"The recording no longer matches this run.\n{miss}")

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

    # The why-right figure only beside what naming one factor every day
    # scored and the difference. On pt-v20 a constant answer scores 95 to
    # 100 per cent, so only the edge says whether an agent read anything.
    print("%-16s %12s %9s %10s %9s %7s"
          % ("agent", "pnl", "impact", "why-right", "constant", "edge"))
    print("-" * 68)
    for s in tf.leaderboard(scores):
        if s.explanation_edge is None:
            why = "%10s %9s %7s" % ("-", "-", "-")
        else:
            why = "%9.0f%% %8.0f%% %+6.0fpt" % (
                s.explanation_accuracy * 100, s.explanation_baseline * 100,
                s.explanation_edge * 100)
        print("%-16s %12.0f %9.1f %s" % (s.name, s.pnl, s.impact_bps, why))

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
    # every scored day. On pt-v20 it is `random_noise` at 95 to 100 per cent
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

    if record:
        # Every day answered, or nothing written: a recording with a hole
        # in it would stop every replay at the missing day.
        if len(claude.recorder) != DAYS:
            sys.exit(f"Not written: Claude answered {len(claude.recorder)} "
                     f"of {DAYS} days. The errors are in the scorecard.")
        claude.recorder.save(target)
        print(f"\nWrote {len(claude.recorder)} answers to {target}.")

    print(
        "\nOne seed ranks the seed, not the agents. Before concluding anything,"
        "\nrun tf.rank(...) across a dozen seeds -- a single market picks the"
        "\ntop agent about half the time."
    )


if __name__ == "__main__":
    main()
