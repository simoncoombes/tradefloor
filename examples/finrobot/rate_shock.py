"""How does a FinRobot agent respond to a sudden +200bps rate shock?

Run it:

    python examples/finrobot/rate_shock.py             # replays a real run
    python examples/finrobot/rate_shock.py --live      # calls FinRobot

The canonical rate-shock counterfactual from
`examples/rate-shock/counterfactual.py`, with one thing changed: the agent is
a real FinRobot agent instead of a native policy. Nothing else moves. Same
seed, same roster, same pins, same twenty days of shared history, same
checkpoint, same fork, same +200bps intervention, same comparison.

    MARKET -> FINROBOT -> CHECKPOINT -> FORK -> +200bps -> COMPARE

That is the claim the integration exists to make. FinRobot builds the
financial AI agent; Tradefloor is the controlled environment that measures
what the agent does when the world changes. The agent is a parameter of the
experiment, and swapping it changes no line of the market, the fork, the
intervention or the comparison.

This is an agent-EVALUATION experiment. One seed cannot say which arm was the
better investor, so the comparison reports behaviour before P&L. It can say
exactly what the same agent, holding the same book, did differently when one
number changed.

## Replay by default

The market is deterministic. FinRobot is an LLM behind an API, so running
this twice live gives two different agents. The default run therefore
replays a genuine recorded FinRobot run from
`tests/fixtures/finrobot/rate-shock.json` -- no API key, no network, and
FinRobot itself does not need to be installed. `--live` calls the real thing
and costs money; `--live --record` overwrites the fixture with the run it
just did.

Everything reported is ground truth about THIS simulated market. It is a
controlled synthetic experiment, not a prediction about how real securities
would react to a real rate rise.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import tradefloor as tf
from tradefloor.counterfactual import World, agree, compare
from tradefloor.integrations.finrobot import (MANDATE, MANDATE_VERSION,
                                              MAX_PARTICIPATION,
                                              FinRobotAdapter, Transcript)

# ---------------------------------------------------------------------------
# The experiment, as constants. Everything that decides what runs is here, and
# every value is the one `examples/rate-shock/counterfactual.py` uses, so the
# two runs are comparable to each other and not only to themselves.
# ---------------------------------------------------------------------------

#: The simulation seed. Fixed, and the whole market descends from it.
SEED = 4242

#: Days before the fork, and days each arm runs after it. Six decision steps a
#: day and 65 ticks a step, which is the library's own evaluation cadence.
WARMUP_DAYS = 20
BRANCH_DAYS = 20
STEPS_PER_DAY = 6
TICKS_PER_STEP = 65

#: How often FinRobot is asked, in steps. Six is once a simulated day. The
#: market still advances every step; a portfolio manager does not re-decide
#: six times a day, and an LLM call per tick would multiply the bill by six
#: without adding a decision worth measuring. Identical in both arms, which
#: `FinRobotAdapter.fork` guarantees rather than leaves to the caller.
DECISION_EVERY = STEPS_PER_DAY

#: The book the agent runs. Large enough that its orders are a real fraction
#: of daily volume, so execution costs something.
CASH = 50_000_000.0

#: The macro regime both worlds share until the intervention.
POLICY_RATE = 0.04
DISCOUNT_RATE = 0.055
BASE_PINS = {"federal_funds_rate": POLICY_RATE,
             "corporate_bond_yield": DISCOUNT_RATE}

#: The intervention. A parallel 200bp shift: the policy rate the agent reads
#: and the corporate bond yield equities are discounted off, moving together.
#: Both, and not the policy rate alone, because of how this model transmits --
#: `federal_funds_rate` reaches a valuation only by steering the corporate
#: bond yield, which is recomputed at central-bank meetings, the first
#: scheduled past the end of this run. `tests/test_macro_transmission.py`
#: pins that map.
SHOCK_BPS = 200
SHOCKED_POLICY_RATE = POLICY_RATE + SHOCK_BPS / 10_000
SHOCKED_DISCOUNT_RATE = DISCOUNT_RATE + SHOCK_BPS / 10_000

#: Four companies, written down rather than drawn. They differ in the one
#: property that decides rate sensitivity in this model: revenue growth is the
#: duration term in `1 - (discount - neutral) * 1.5 * (1 + growth * 2)`.
#:
#: ticker, sector, what it is, price, shares, eps, book value, revenue growth,
#: average daily volume, beta, short interest.
ROSTER = [
    ("NOVA", "technology", "long-duration growth", 112.0, 4.0e8, 3.60, 12.0,
     0.35, 6.0e6, 1.35, 9.0e6),
    ("HELX", "healthcare", "mid-duration growth", 88.0, 5.0e8, 3.80, 22.0,
     0.18, 4.5e6, 1.05, 5.0e6),
    ("BRDG", "industrials", "short-duration cyclical", 70.0, 7.0e8, 4.20, 31.0,
     0.06, 5.0e6, 0.95, 4.0e6),
    ("STAP", "consumer_staples", "minimal-duration defensive", 51.0, 9.0e8,
     2.60, 17.0, 0.01, 4.0e6, 0.60, 3.0e6),
]

#: Public company facts handed to FinRobot beside the market data. Supplied
#: here instead of read off the engine, so the adapter stays auditable about
#: what it exposes. An analyst reads all five off a filing. The valuation they
#: feed stays out: that is the simulator's answer key. See
#: `tradefloor/integrations/finrobot.py`.
FUNDAMENTALS = {
    row[0]: {
        "sector": row[1],
        "eps": row[5],
        "book_value_per_share": row[6],
        "revenue_growth": row[7],
        "beta": row[9],
    }
    for row in ROSTER
}

#: Longest duration first. The order every table below uses, because the claim
#: is that the response is monotone in it and an alphabetical table hides it.
BY_DURATION = sorted(ROSTER, key=lambda row: row[7], reverse=True)

#: The standing objective, appended to every observation in both arms. It says
#: what the agent is FOR and nothing about what is about to happen.
OBJECTIVE = ("Manage the portfolio for attractive risk-adjusted returns "
             "while controlling downside risk.")

#: The recorded run. In `tests/` rather than beside this file because the
#: automated tests replay the same interactions, and two copies of a recording
#: are two recordings that can drift apart.
FIXTURE = (Path(__file__).resolve().parents[2]
           / "tests" / "fixtures" / "finrobot" / "rate-shock.json")

#: Where a run writes its artifacts. Beside the run that wrote them, and
#: git-ignored: they are output, regenerable by running this file, and not the
#: recorded input that `FIXTURE` is.
DEFAULT_OUT = Path(__file__).resolve().parent / "artifacts"

#: The provider FinRobot talks to in `--live`. Any provider `autogen` supports
#: works -- this is passed straight through to FinRobot as its `llm_config`,
#: and the integration has no opinion about it beyond recording which one ran.
#:
#: A dated snapshot rather than a moving alias, because the recorded run is
#: evidence and an alias re-pointed next quarter would make the provenance
#: block describe a model that did not produce it.
#:
#: One constraint comes from FinRobot's dependency rather than from here:
#: `autogen` 0.2.35's Anthropic client sets `temperature` unconditionally
#: (`load_config` gives it a default of 1.0 and `create` always forwards it),
#: so a model that has deprecated the parameter answers 400 and cannot be
#: driven through FinRobot at all. That rules out the newest Anthropic
#: models until `pyautogen` moves.
LIVE_MODEL = os.environ.get("TRADEFLOOR_FINROBOT_MODEL",
                            "claude-sonnet-4-5-20250929")
LIVE_API_TYPE = os.environ.get("TRADEFLOOR_FINROBOT_API_TYPE", "anthropic")
LIVE_KEY_VAR = "ANTHROPIC_API_KEY" if LIVE_API_TYPE == "anthropic" \
    else "OPENAI_API_KEY"


def universe() -> tf.Universe:
    return tf.Universe([
        tf.Instrument(ticker, sector, initial_price=price,
                      shares_outstanding=shares, eps=eps,
                      book_value_per_share=book, revenue_growth=growth,
                      avg_volume=volume, beta=beta, short_interest=short)
        for (ticker, sector, _what, price, shares, eps, book, growth,
             volume, beta, short) in ROSTER
    ])


def rule(text: str) -> None:
    print(f"\n{text}\n" + "-" * 68)


def llm_config() -> dict:
    """The autogen configuration FinRobot runs under, in `--live`.

    Temperature zero: the experiment measures the agent's response to the
    OBSERVATION, and sampling noise would add a second source of difference
    between the arms that the design cannot separate from the intervention.
    Zero does not make the agent deterministic, and nothing here claims it
    does. See the provenance block in the printed report.
    """
    key = os.environ.get(LIVE_KEY_VAR)
    if not key:
        sys.exit(f"--live needs {LIVE_KEY_VAR} in the environment. "
                 "Without it, run without --live to replay the recorded run.")
    return {
        "config_list": [{"model": LIVE_MODEL, "api_key": key,
                         "api_type": LIVE_API_TYPE}],
        "temperature": 0.0,
        "cache_seed": None,
    }


# ---------------------------------------------------------------------------
# The experiment
# ---------------------------------------------------------------------------


def main(*, live: bool = False, record: bool = False,
         out: Path = DEFAULT_OUT, fixture: Path = FIXTURE) -> dict:
    started = time.time()
    roster = list(universe())

    # -- 1. the agent -----------------------------------------------------
    #
    # One adapter. The fork copies it, so both arms run the same agent under
    # the same mandate at the same cadence, and the only thing that differs
    # between them is the observation each is handed.
    recorder = Transcript() if (live and record) else None
    if live:
        agent = FinRobotAdapter(
            mode="live", llm_config=llm_config(), recorder=recorder,
            fundamentals=FUNDAMENTALS, objective=OBJECTIVE,
            every=DECISION_EVERY, arm="shared")
        source = f"live: {LIVE_API_TYPE} / {LIVE_MODEL}"
    else:
        if not fixture.exists():
            sys.exit(f"no recorded run at {fixture}. Record one with:\n"
                     f"    python {Path(__file__).name} --live --record")
        transcript = Transcript.load(fixture)
        agent = FinRobotAdapter(
            mode="replay", transcript=transcript, fundamentals=FUNDAMENTALS,
            objective=OBJECTIVE, every=DECISION_EVERY, arm="shared")
        source = f"replay: {fixture.name} ({len(transcript)} interactions)"

    provenance = _provenance(live, agent, recorder)

    print("\nTRADEFLOOR x FINROBOT")
    print("CONTROLLED AGENT EVALUATION")
    print("=" * 68)
    _show_setup(source, provenance)

    # -- 2. shared history ------------------------------------------------
    world = World(seed=SEED, universe=roster, agent=agent, pins=BASE_PINS,
                  cash=CASH, steps_per_day=STEPS_PER_DAY,
                  ticks_per_step=TICKS_PER_STEP, label="shared")
    rule("Shared history")
    print(f"  Running {WARMUP_DAYS} days, FinRobot deciding once a day.")
    world.run(days=WARMUP_DAYS)
    _show_book("  after " + str(WARMUP_DAYS) + " days", world)

    # -- 3. checkpoint ----------------------------------------------------
    mark = world.checkpoint("before the shock")
    rule("Checkpoint")
    print(f"  step                       {world.step}")
    print(f"  day                        {world.day}")
    print(f"  policy rate                {POLICY_RATE:.2%}")
    print(f"  portfolio value            ${world.net_worth():,.0f}")
    print(f"  market digest              {world.digest()[:32]}...")
    print(f"  universe fingerprint       {world.universe_fingerprint[:32]}...")
    print(f"  operations in the log      {len(mark.log):,}")

    # -- 4. fork ----------------------------------------------------------
    control, shock = world.fork("control", "+200bps")
    # Cosmetic, and only cosmetic: the arm name is recorded beside each
    # interaction so a reader of the transcript can tell them apart. Replay is
    # keyed by the observation, never by this, so a run that forgot these
    # lines would still replay correctly.
    control.agent.arm, shock.agent.arm = "control", "+200bps"
    agreement = agree(control, shock)
    rule("Fork")
    print(agreement.render())
    if not agreement.identical:
        sys.exit("the arms did not start identical, so nothing below is a "
                 f"controlled comparison: {agreement.differences}")

    # -- 5. the one intervention ------------------------------------------
    shock.intervene(federal_funds_rate=SHOCKED_POLICY_RATE,
                    corporate_bond_yield=SHOCKED_DISCOUNT_RATE)
    rule("Intervention")
    print(f"  {'':<26}{'CONTROL':>16}{'RATE SHOCK':>16}")
    print(f"  {'policy rate':<26}{POLICY_RATE:>15.2%}"
          f"{SHOCKED_POLICY_RATE:>16.2%}")
    print(f"  {'corporate bond yield':<26}{DISCOUNT_RATE:>15.2%}"
          f"{SHOCKED_DISCOUNT_RATE:>16.2%}")
    print(f"\n  One field pair, +{SHOCK_BPS}bps, in one arm. The agent is not "
          "told.")

    # -- 6. continue both worlds ------------------------------------------
    rule("Both worlds continue")
    print(f"  {BRANCH_DAYS} more days each, same agent, same cadence.")
    control.run(days=BRANCH_DAYS)
    shock.run(days=BRANCH_DAYS)

    # -- 7. compare -------------------------------------------------------
    report = compare(control, shock, agreement=agreement)
    rule("How FinRobot behaved")
    print(_behaviour(control, shock))
    print()
    print(report.render())

    rule("Where the two worlds came apart")
    print(report.divergence.render())
    print(f"  {'first different ACTION':<26} "
          f"{_where(_action_divergence(control, shock))}")
    print()
    print(_divergence_story(control, shock))

    rule("The simulator's own view, which FinRobot never saw")
    print(_ground_truth(control, shock))

    written = _save(out, world, mark, control, shock, report, provenance)
    if recorder is not None:
        recorder.meta = provenance
        recorder.save(fixture)
        written.append(fixture)

    rule("Artifacts")
    for path in written:
        print(f"  {path}")

    elapsed = time.time() - started
    print(f"\n  {elapsed:.1f}s\n")
    print("  Same starting market. Same FinRobot agent. One controlled")
    print("  intervention. Ground truth about THIS market, not a real one.\n")

    return {
        "divergence": report.divergence.as_dict(),
        "control": report.control,
        "treatment": report.treatment,
        "provenance": provenance,
        "artifacts": [str(p) for p in written],
        "seconds": elapsed,
    }


# ---------------------------------------------------------------------------
# Narration. Everything below reports; none of it decides.
# ---------------------------------------------------------------------------


def _provenance(live: bool, agent: FinRobotAdapter,
                recorder: Transcript | None) -> dict:
    """What has to be recorded for the run to mean anything later.

    Tradefloor's market is deterministic. FinRobot's agent is not, and the two
    facts must not blur together. Everything here describes the AGENT: which
    framework, which version, which provider, which model, which generation
    parameters, which mandate. The market's own provenance sits in the
    manifest and the checkpoint.
    """
    version = "not installed"
    entry_point = "finrobot.agents.workflow.SingleAssistant"
    try:                                    # pragma: no cover - optional
        from importlib.metadata import version as _version
        version = _version("finrobot")
    except Exception:                       # pragma: no cover - optional
        pass

    meta = {
        "framework": "FinRobot",
        "framework_version": version,
        "framework_url": "https://github.com/AI4Finance-Foundation/FinRobot",
        "entry_point": entry_point,
        "mandate_version": MANDATE_VERSION,
        "decision_every_steps": agent.every,
        "max_participation": agent.max_participation,
        "mode": agent.mode,
    }
    if live:
        meta.update({
            "provider": LIVE_API_TYPE,
            "model": LIVE_MODEL,
            "temperature": 0.0,
            "recorded": recorder is not None,
        })
    elif agent.transcript is not None:
        # A replay's provenance is the RECORDING's provenance. Reporting this
        # run's instead would say a replay was produced by whatever model is
        # configured today, which is the one thing a replay is not.
        meta.update({k: v for k, v in agent.transcript.meta.items()
                     if k in ("provider", "model", "temperature",
                              "framework_version", "mandate_version")})
    return meta


def _show_setup(source: str, provenance: dict) -> None:
    rule("Agent")
    print(f"  {'framework':<26} FinRobot {provenance.get('framework_version')}"
          "  (AI4Finance Foundation)")
    print(f"  {'entry point':<26} {provenance['entry_point']}")
    print(f"  {'provider / model':<26} {provenance.get('provider', '-')}"
          f" / {provenance.get('model', '-')}")
    print(f"  {'temperature':<26} {provenance.get('temperature', '-')}")
    print(f"  {'mandate version':<26} {provenance['mandate_version']}")
    print(f"  {'decision cadence':<26} every "
          f"{provenance['decision_every_steps']} steps (once a day)")
    print(f"  {'order size cap':<26} "
          f"{provenance['max_participation']:.1%} of average daily volume")
    print(f"  {'source':<26} {source}")

    rule("Experiment")
    print(f"  {'seed':<26} {SEED}")
    print(f"  {'universe':<26} "
          + ", ".join(f"{row[0]} ({row[2]})" for row in BY_DURATION))
    print(f"  {'shared history':<26} {WARMUP_DAYS} days "
          f"({WARMUP_DAYS * STEPS_PER_DAY} steps)")
    print(f"  {'each arm afterwards':<26} {BRANCH_DAYS} days")
    print(f"  {'starting cash':<26} ${CASH:,.0f}")
    print(f"""
                    SHARED HISTORY
                         |
                      FinRobot
                         |
                    CHECKPOINT
                         |
                +--------+--------+
                |                 |
             CONTROL          +{SHOCK_BPS}BPS
              {POLICY_RATE:.1%}              {SHOCKED_POLICY_RATE:.1%}
                |                 |
             FinRobot          FinRobot
                |                 |
                +--------+--------+
                         |
                      COMPARE
""")


def _show_book(label: str, world: World) -> None:
    print(f"{label}: ${world.net_worth():,.0f}, "
          f"{world.portfolio.leverage(world.engine):.2f}x gross")
    held = {t: p.quantity for t, p in world.portfolio.positions.items()
            if p.quantity}
    if not held:
        print("  no positions")
        return
    for ticker, quantity in sorted(held.items()):
        price = world.trace[-1]["prices"][world.engine.tickers.index(ticker)]
        print(f"  {ticker:<8} {quantity:>12,.0f} shares  "
              f"${quantity * price:>14,.0f}")


def _label(row) -> str:
    """`NOVA (long-duration)`. The full descriptor in ROSTER is prose for the
    setup block; a table column needs a label that fits one."""
    return f"{row[0]} ({row[2].split()[0]})"


def _weights(world: World) -> dict[str, float]:
    """Final portfolio weight per name, as a fraction of net worth."""
    prices = world.trace[-1]["prices"]
    worth = world.trace[-1]["net_worth"]
    tickers = list(world.engine.tickers)
    positions = world.trace[-1]["positions"]
    return {t: (positions.get(t, 0.0) * prices[tickers.index(t)]) / worth
            for t in tickers}


def _sides(world: World) -> dict[str, int]:
    """How many BUY, SELL and HOLD instructions the agent issued after the
    fork. The behavioural question in its most direct form."""
    counts = {"BUY": 0, "SELL": 0, "HOLD": 0}
    for entry in world.agent.record:
        if world.fork_step is not None and entry["step"] < world.fork_step:
            continue
        for action in entry["decision"]["actions"]:
            counts[action["side"]] += 1
    return counts


def _behaviour(control: World, shock: World) -> str:
    """Exposure and decisions per name, which `Comparison` does not carry.

    `Comparison.render` reports the portfolio in aggregate. That is the right
    default, and it hides what this experiment is about: WHICH names the agent
    moved. Duration order, because the claim under test is that the response
    is monotone in it.
    """
    left, right = _weights(control), _weights(shock)
    lines = [f"  {'final weight':<26}{'CONTROL':>16}{'RATE SHOCK':>16}"
             f"{'DELTA':>12}",
             "  " + "-" * 68]
    for row in BY_DURATION:
        ticker = row[0]
        a, b = left.get(ticker, 0.0), right.get(ticker, 0.0)
        lines.append(f"  {_label(row):<26}"
                     f"{a:>15.1%} {b:>15.1%} {b - a:>+11.1%}")

    a_sides, b_sides = _sides(control), _sides(shock)
    lines += ["", f"  {'instructions after the fork':<26}"
                  f"{'CONTROL':>16}{'RATE SHOCK':>16}",
              "  " + "-" * 68]
    for side in ("BUY", "SELL", "HOLD"):
        lines.append(f"  {side:<26}{a_sides[side]:>16,}{b_sides[side]:>16,}")
    return "\n".join(lines)


def _action_divergence(control: World, shock: World) -> int | None:
    """The first step at which the agent's ACTIONS differ, ignoring wording.

    Separate from `Divergence.decision`, which compares the whole recorded
    decision including the rationale. Both numbers earn their place. A changed
    rationale says the agent read the world differently; changed actions say
    it traded on that reading. The two often land on different steps, and
    reporting only the first lets a change of mind that never reached the
    market read as a change of behaviour.
    """
    by_step = {e["step"]: e for e in shock.agent.record}
    for entry in control.agent.record:
        other = by_step.get(entry["step"])
        if other is None:
            continue
        if entry["decision"]["actions"] != other["decision"]["actions"]:
            return entry["step"]
    return None


def _where(step: int | None) -> str:
    if step is None:
        return "never"
    return f"step {step:<5} (day {step // STEPS_PER_DAY})"


def _first_after_fork(world: World) -> dict | None:
    fork = world.fork_step or 0
    for entry in world.agent.record:
        if entry["step"] >= fork:
            return entry
    return None


def _divergence_story(control: World, shock: World) -> str:
    """The two decisions, side by side, at the first post-fork decision point.

    One agent, one book, two worlds that were identical a step earlier, and
    what it said in each. The clearest single output of the run.
    """
    a, b = _first_after_fork(control), _first_after_fork(shock)
    if a is None or b is None:
        return "  no decision was taken after the fork."

    def block(label: str, entry: dict, rate: float) -> list[str]:
        lines = [f"  {label}  (policy rate {rate:.2%})", ""]
        actions = entry["decision"]["actions"]
        if not actions:
            lines.append("    no change")
        for action in actions:
            if action["side"] == "HOLD":
                lines.append(f"    HOLD {action['symbol']}")
            else:
                lines.append(f"    {action['side']} "
                             f"{action['quantity']:,.0f} {action['symbol']}")
        rationale = entry["decision"]["rationale"]
        if rationale:
            lines += ["", f"    \"{rationale}\""]
        return lines

    return "\n".join(
        [f"  At step {a['step']} (day {a['day']}), the first decision after "
         "the fork:", ""]
        + block("CONTROL", a, POLICY_RATE)
        + [""]
        + block("RATE SHOCK", b, SHOCKED_POLICY_RATE))


def _ground_truth(control: World, shock: World) -> str:
    """What the simulator knows and the agent was never shown.

    Printed AFTER the experiment, never before. The boundary means the
    decisions above were taken without it. Fair value is the model's own
    valuation, computed from the same public fundamentals the agent had and
    the discount rate it could see, so in principle the agent could derive it.
    Handed it directly, the agent would be inverting the simulator.
    """
    lines = ["  FinRobot did not receive any of this.", "",
             f"  {'model fair value':<26}{'CONTROL':>16}{'RATE SHOCK':>16}"
             f"{'DELTA':>12}",
             "  " + "-" * 68]
    for row in BY_DURATION:

        def valued(policy: float, discount: float) -> float:
            return tf.fair_value(
                eps=row[5], sector=row[1], revenue_growth=row[7],
                book_value_per_share=row[6], federal_funds_rate=policy,
                corporate_bond_yield=discount).fair_value

        a = valued(POLICY_RATE, DISCOUNT_RATE)
        b = valued(SHOCKED_POLICY_RATE, SHOCKED_DISCOUNT_RATE)
        lines.append(f"  {_label(row):<26}"
                     f"{a:>15,.2f} {b:>15,.2f} {(b / a - 1):>+11.1%}")
    lines += ["",
              "  The valuation falls furthest for the longest-duration name.",
              "  Whether FinRobot inferred that from prices and the rate, "
              "without",
              "  being told it, is what the table above measures."]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------


def _save(out: Path, world: World, mark, control: World, shock: World,
          report, provenance: dict) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    def write(name: str, payload) -> None:
        path = out / name
        path.write_text(json.dumps(payload, indent=2) + "\n",
                        encoding="utf-8")
        written.append(path)

    write("checkpoint.json", json.loads(mark.to_json()))
    write("comparison.json", report.as_dict())
    write("manifest.json", json.loads(
        world.manifest(label="finrobot-rate-shock").to_json()))
    write("provenance.json", provenance)
    for arm in (control, shock):
        write(f"{arm.label.replace('+', 'plus')}.json", _arm(arm))
    return written


def _arm(world: World) -> dict:
    """One arm's evaluation trace, whole.

    The chain the experiment is evidence for, joined up per decision instead
    of left in two structures a reader has to line up by step:

        observation -> FinRobot input -> response -> validated action
                    -> Tradefloor order -> fill -> position -> exposure

    The prompt is dropped here and only here. The replay fixture holds the
    same text verbatim, and copying forty of them into an artifact that every
    run regenerates would give the recording a second copy to drift from.
    Everything else below is the run's own.
    """
    by_step = {row["step"]: row for row in world.trace}
    decisions = []
    for entry in world.agent.record:
        row = by_step.get(entry["step"], {})
        decisions.append({
            "step": entry["step"],
            "day": entry["day"],
            "digest": entry["digest"],
            "response": entry["response"],
            "decision": entry["decision"],
            "orders": entry["orders"],
            "clipped": entry["clipped"],
            "macro": row.get("macro"),
            "fills": row.get("fills", []),
            "refused": row.get("refused", []),
            "positions": row.get("positions"),
            "cash": row.get("cash"),
            "net_worth": row.get("net_worth"),
            "exposure": row.get("exposure"),
        })
    return {
        "label": world.label,
        "fork_step": world.fork_step,
        "interventions": world.interventions,
        "summary": world.summary(since=world.fork_step),
        "decisions": decisions,
    }


def _cli() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--live", action="store_true",
                        help="call the real FinRobot instead of replaying "
                             "the recorded run. Needs the finrobot extra, an "
                             "API key, and spends money per decision.")
    parser.add_argument("--record", action="store_true",
                        help="with --live, overwrite the replay fixture with "
                             "the run just performed")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT,
                        help="where to write the experiment artifacts")
    parser.add_argument("--fixture", type=Path, default=FIXTURE,
                        help="the recorded run to replay, or to overwrite")
    args = parser.parse_args()
    if args.record and not args.live:
        parser.error("--record only means something with --live: there is "
                     "nothing to record from a replay.")
    return args


if __name__ == "__main__":
    _args = _cli()
    main(live=_args.live, record=_args.record, out=_args.out,
         fixture=_args.fixture)
