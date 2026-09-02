"""Experiment 002: will a financial AI agent reduce risk in a crisis?

Two arms leave one checkpoint under one agent. One of them runs under a
Tradefloor scenario read off disk; the other does not, and nothing else
differs.

    control   nothing
    crisis    `scenarios/liquidity_crisis_at_fork.yml` -- the scenario that
              ships with Tradefloor, rebased to fire at the fork. Quoted
              depth to 40%, volatility doubled, and one stated assumption
              that credit widens 50 basis points alongside them.

The scenario is the experiment. Anyone can read the file and see exactly
what changed, which is the thing a hand-written mutation inside experiment
code cannot offer.

The design came out of experiment 001, which ran the +200bps intervention
over 421 companies and found the agent running cross-sectional momentum and
barely moving. Three pilots then established why, and each of them is a
constant in this file rather than a paragraph somebody has to trust.

A rate shock is a LEVEL shift. The observation carries prices and five-day
returns, so a one-off repricing is visible in a return window for exactly
one window. After that the only trace of it is two decimal places. A
crisis, by contrast, changes a WORD.

The second finding is the reason `resample` is part of this experiment
rather than an appendix to it. A single decision pair cannot tell a
response from sampling noise: on the first post-fork decision of a pilot,
the agent bought a dip in control and de-risked under the shock, which read
as a clean result and turned out to sit inside the control arm's own
variance over eight identical calls.

## What this measures

Gross exposure per decision in each arm, and separately the part of it the
agent itself caused: exposure read immediately before its fills and
immediately after, at the same arrival prices, so the market is held still.
Reporting only the first would let a difference in prices read as a
difference in behaviour.

In the recorded run the control arm averaged 0.859 gross exposure and added
0.122 through its own trades; the crisis arm averaged 0.636 and removed
0.128. Across four live replications the crisis arm was lower in three.

Three separate bodies of evidence, and they answer different questions. One
canonical trajectory per arm carries the path. Eight identical fork-step
calls per arm measure how much the agent varies when asked one question
repeatedly. Four complete replications are the check on the direction. The
days inside one trajectory are not independent samples of anything.
"""

from __future__ import annotations

import hashlib
import json
import re
import statistics
from pathlib import Path
from typing import Any, Sequence

import tradefloor as tf
from tradefloor.edgar import Snapshot

HERE = Path(__file__).resolve().parent
DEFAULT_OUT = HERE / "artifacts"
DEFAULT_ASSETS = HERE / "assets"


#: The repository root, four levels up from this study.
ROOT = HERE.parent.parent.parent

#: The frozen SEC EDGAR fundamentals this market starts from. Committed, so
#: the initial conditions are the same for everyone and forever.
SNAPSHOT = HERE / "data" / "edgar-2026-08-31.json"

#: The recorded FinRobot run this replays. Under `tests/fixtures/` rather
#: than beside the study, because `CONTRIBUTING.md` is explicit about it:
#: the test suite and the example read the SAME recording, since two copies
#: of one drift apart.
FIXTURE = ROOT / "tests" / "fixtures" / "finrobot" / "liquidity-crisis.json"

#: The earlier three-arm rate ladder, for the supporting story about a
#: result that looked convincing once and did not reproduce.
LEGACY_FIXTURE = ROOT / "tests" / "fixtures" / "finrobot" / "rate-ladder.json"

PRESET = "pt-v16"
SEED = 4242
UNIVERSE_SEED = 4242
CASH = 50_000_000.0
WARMUP_DAYS = 20
BRANCH_DAYS = 20
STEPS_PER_DAY = 6
TICKS_PER_STEP = 65

#: One decision a day. Experiment 001 asks every fifth day, which is a
#: rebalancing interval and also exactly the width of the five-day return
#: window, so a one-off repricing landed in precisely one observation.
#: Decoupling the two is deliberate.
DECISION_EVERY_DAYS = 1
DECISION_EVERY = DECISION_EVERY_DAYS * STEPS_PER_DAY

POLICY_RATE = 0.04
DISCOUNT_RATE = 0.055
BASE_PINS = {"federal_funds_rate": POLICY_RATE,
             "corporate_bond_yield": DISCOUNT_RATE}

#: The scenario the crisis arm runs under, as a file on disk. Loaded rather
#: than written into this module, because a reader checking what the crisis
#: was should read the scenario and not the experiment.
SCENARIO_DIR = HERE / "scenarios"
SCENARIO_NAME = "liquidity_crisis_at_fork"

#: The scenario it was rebased from, which ships in the wheel. Every shock,
#: every value and the one assumption are identical; only `at` differs, and
#: the notebook checks that rather than asking a reader to trust it.
PACKAGED_SCENARIO = "liquidity_crisis"

#: The quiet intervention, kept for the supporting story. The canonical
#: +200bps pair, and the one experiment 001 ran. It changes two numbers and
#: no word. It is not part of the public comparison any more: a rate shock
#: is a LEVEL shift, visible in a return window for exactly one window,
#: which is what 001 measured and what four replications failed to
#: reproduce.
QUIET = {"federal_funds_rate": 0.06, "corporate_bond_yield": 0.075}

#: The loud rate-and-regime arm the earlier framing compared against. Kept
#: because the notebook tells the supporting story from its recording.
LOUD = {**QUIET, "vix": 45.0, "cycle": "contraction"}

#: The public experiment. Ordered: `dict` preserves insertion order, the
#: fork takes these labels in this order, and every table reads them in it.
#:
#: A value is one of three things, and `treat` dispatches on which:
#: `None` for the control arm, a `str` naming a scenario file, or a mapping
#: of macro fields for the legacy arms below.
ARMS: dict[str, str | dict | None] = {"control": None,
                                      "crisis": SCENARIO_NAME}
CONTROL_ARM = "control"
CRISIS_ARM = "crisis"
TREATMENTS = [name for name, treatment in ARMS.items() if treatment]

#: The three-arm rate ladder the earlier framing ran, kept so the
#: supporting story and the decomposition still have their arms. Nothing in
#: the public experiment reads this.
LEGACY_ARMS: dict[str, str | dict | None] = {"control": None,
                                             "+200bps": QUIET,
                                             "crisis": LOUD}

#: The crisis moves four fields at once, so the headline result cannot say
#: which of them carried it. These two arms split the regime half of it:
#: VIX alone, and the cycle phase alone. Both are in
#: `finrobot.OBSERVABLE_MACRO`, and `cycle` is the one that reaches the
#: agent as a word.
#:
#: Every arm of a fork is bit-identical at the fork whatever the arm count.
#: So a five-arm fork reproduces the three-arm arms exactly, and the
#: recorded replications cover three of the five without a further call.
VIX_ONLY = {"vix": LOUD["vix"]}
CYCLE_ONLY = {"cycle": LOUD["cycle"]}
#: Legacy, like `LEGACY_ARMS`: these split the rate-and-regime arm.
ARMS_DECOMPOSED: dict[str, dict | None] = {
    "control": None,
    "+200bps": QUIET,
    "vix": VIX_ONLY,
    "cycle": CYCLE_ONLY,
    "crisis": LOUD,
}

OBJECTIVE = ("Manage the portfolio for attractive risk-adjusted returns "
             "while controlling downside risk.")

# ---------------------------------------------------------------------------
# The universe
# ---------------------------------------------------------------------------

#: Two companies per sector, drawn from experiment 001's frozen snapshot by
#: the digest of a fixed salt and the ticker. Twenty-four names rather than
#: 421, because the compact rendering a large universe needs is what turned
#: the observation into a momentum screen, and this experiment is about the
#: intervention rather than about the rendering.
#:
#: A hash rather than a seeded sample: the digest is fixed by the algorithm,
#: so two Python versions and two processes agree, and a reader can
#: recompute one line by hand.
UNIVERSE_SALT = "showcase-pilot-a"
PER_SECTOR = 2
UNIVERSE_RULE = (
    "sha256(UNIVERSE_SALT + '|' + ticker), lowest PER_SECTOR digests per "
    "sector of the 001 snapshot, sectors and ties ordered by ticker")


def load_scenario(name: str = SCENARIO_NAME) -> tf.Scenario:
    """One scenario, read off disk.

    `Scenario.from_yaml` rather than `Scenario.load`, because this file
    lives with the experiment rather than in the wheel. The packaged one it
    was rebased from is reachable as `tf.Scenario.load(PACKAGED_SCENARIO)`,
    and the only field that differs is `at`.
    """
    path = SCENARIO_DIR / f"{name}.yml"
    if not path.is_file():
        raise MissingInput(f"no scenario at {path}")
    return tf.Scenario.from_yaml(path.read_text(encoding="utf-8"))


def treat(world, treatment: str | dict | None) -> str:
    """Apply one arm's treatment, and say in one line what it was.

    Three shapes, because the public experiment and the supporting story
    treat their arms differently and both have to run through one call
    site. `None` leaves the arm alone; a `str` names a scenario file and
    reaches `World.apply`, which rebases it onto this world's own day
    numbering; a mapping reaches `World.intervene`, one absolute macro
    value at a time.

    A scenario is preferred wherever one can express the experiment. It is
    a file a reader can open, it carries a fingerprint, and it reaches
    `market.liquidity`, which is not a macro field at all and is the only
    lever that touches execution.
    """
    if treatment is None:
        return "none"
    if isinstance(treatment, str):
        scenario = load_scenario(treatment)
        world.apply(scenario)
        return f"scenario {scenario.name} {scenario.fingerprint[:19]}"
    world.intervene(**treatment)
    return ", ".join(f"{k}={v}" for k, v in treatment.items())



class MissingInput(RuntimeError):
    """A frozen input this experiment cannot run without is not on disk."""


def universe_key(ticker: str) -> str:
    return hashlib.sha256(
        f"{UNIVERSE_SALT}|{ticker}".encode("utf-8")).hexdigest()


def load_snapshot(path: Path | None = None) -> Snapshot:
    target = Path(path) if path is not None else SNAPSHOT
    if not target.is_file():
        raise MissingInput(
            f"no frozen EDGAR snapshot at {target}.\n"
            "This experiment reads the snapshot experiment 001 froze. "
            "Build it with:\n"
            "    python 001-finrobot-rate-shock/prepare.py snapshot")
    return Snapshot.load(str(target))


def subset(snapshot: Snapshot) -> Snapshot:
    """The 24-name universe, as its own snapshot.

    A `Snapshot` rather than a list, so it hashes, so `to_instruments`
    takes it unchanged, and so the artifacts can cite what this experiment
    ran on as well as what it was drawn from.
    """
    buckets: dict[str, list[dict]] = {}
    for row in snapshot.rows:
        buckets.setdefault(row["sector"], []).append(row)
    rows: list[dict] = []
    for sector in sorted(buckets):
        ranked = sorted(buckets[sector],
                        key=lambda r: (universe_key(r["ticker"]),
                                       r["ticker"]))
        rows.extend(ranked[:PER_SECTOR])
    rows.sort(key=lambda r: r["ticker"])
    return Snapshot(as_of=snapshot.as_of, rows=rows,
                    source="002-subset-of-001-snapshot",
                    notes={"rule": UNIVERSE_RULE, "salt": UNIVERSE_SALT,
                           "per_sector": PER_SECTOR,
                           "drawn_from": snapshot.hash})


def universe(small: Snapshot) -> list:
    return tf.edgar.to_instruments(
        small, federal_funds_rate=POLICY_RATE,
        corporate_bond_yield=DISCOUNT_RATE,
        initial_s="stationary", s_seed=UNIVERSE_SEED)


def fundamentals(small: Snapshot) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in small.rows:
        facts: dict[str, Any] = {"sector": row["sector"], "eps": row["eps"]}
        if row.get("book_value_per_share") is not None:
            facts["book_value_per_share"] = row["book_value_per_share"]
        if row.get("revenue_growth") is not None:
            facts["revenue_growth"] = row["revenue_growth"]
        out[row["ticker"]] = facts
    return out


# ---------------------------------------------------------------------------
# Measurement
# ---------------------------------------------------------------------------

#: Words that mark a rationale as talking about risk rather than about
#: momentum. Counted rather than interpreted: the claim this supports is
#: "the crisis arm used risk language in every decision and the others did
#: not", which is a property of the text and needs no reading.
RISK_WORDS = re.compile(
    r"risk|defensive|de-?risk|volatil|vix|caution|hedge|downside|preserve"
    r"|contraction|recession|crisis|uncertain|protect", re.I)



def row_at(world, step: int) -> dict:
    index = step - world.trace[0]["step"]
    row = world.trace[index]
    if row["step"] != step:
        raise RuntimeError("the trace is not contiguous")
    return row


def scheduled_steps(world) -> list[int]:
    """Every step the agent was SCHEDULED to decide at, after the fork.

    Driven off the trace rather than off `agent.record`, and that is the
    whole point of it. A refused response produces no record entry -- the
    adapter raises before appending one -- so a series built by zipping the
    arms' records silently drops the refused decision from EVERY arm, and
    the published run reported nineteen observations for twenty scheduled
    decisions with nothing saying which one was missing.

    A refusal is behaviour. It stays in the series, with no orders and a
    flag.
    """
    start = world.fork_step or 0
    every = getattr(world.agent, "every", 1)
    return [row["step"] for row in world.trace
            if row["step"] >= start and row["step"] % every == 0]


def _gross_and_worth(positions: dict, prices: Sequence[float],
                     tickers: Sequence[str], cash: float) -> tuple[float, float]:
    """Gross notional and net worth, both priced at the same clock."""
    gross = 0.0
    net = cash
    for ticker, quantity in positions.items():
        price = prices[tickers.index(ticker)]
        gross += abs(quantity * price)
        net += quantity * price
    return gross, net


def agent_exposure_change(world, step: int) -> dict[str, Any]:
    """What the AGENT did to gross exposure at one decision, in isolation.

    Section 7 of the brief, and the check that decides whether a difference
    between the arms is the agent or the market. Ordinary gross exposure
    moves for two reasons -- the agent trades, and prices move underneath a
    portfolio nobody touched -- and after a fork the two arms trade
    different markets, so a gap in exposure is not by itself evidence that
    the agent did anything.

    So: read exposure immediately before the agent's fills, execute, read it
    again at the SAME prices. The difference is the agent's, with the
    market held still. The arrival prices are the previous row's closing
    prices, which are exactly the prices this step opened on.
    """
    index = step - world.trace[0]["step"]
    row = world.trace[index]
    tickers = list(world.engine.tickers)

    if index == 0:
        before_positions: dict = {}
        before_prices = list(row["prices"])
        before_cash = world.cash
    else:
        previous = world.trace[index - 1]
        before_positions = dict(previous["positions"])
        before_prices = list(previous["prices"])
        before_cash = previous["cash"]

    gross_before, worth_before = _gross_and_worth(
        before_positions, before_prices, tickers, before_cash)

    after_positions = dict(before_positions)
    after_cash = before_cash
    for fill in row["fills"]:
        after_positions[fill["ticker"]] = (
            after_positions.get(fill["ticker"], 0.0) + fill["quantity"])
        after_cash -= fill["notional"]
    gross_after, worth_after = _gross_and_worth(
        after_positions, before_prices, tickers, after_cash)

    before = gross_before / worth_before if worth_before else 0.0
    after = gross_after / worth_after if worth_after else 0.0
    return {
        "exposure_before": before,
        "exposure_after_fills": after,
        "agent_change": after - before,
        "fills": len(row["fills"]),
    }


def exposure_series(worlds: dict) -> list[dict[str, Any]]:
    """Per scheduled decision, per arm: exposure, the agent's own move,
    whether the answer was usable, and whether it mentioned risk.

    Gross exposure rather than P&L, because the arms trade different
    markets after the fork and a P&L comparison across them measures the
    market as much as the agent. `agent_change` is the same quantity with
    the market held still, and it is the one that says the agent acted.
    """
    names = list(worlds)
    steps = {n: scheduled_steps(worlds[n]) for n in names}
    lengths = {n: len(v) for n, v in steps.items()}
    if len(set(lengths.values())) != 1:
        raise RuntimeError(
            f"the arms were scheduled a different number of decisions: "
            f"{lengths}. Every arm runs the same cadence over the same "
            "window, so this is a bug and not a finding.")

    out: list[dict[str, Any]] = []
    for position in range(len(steps[names[0]])):
        step = steps[names[0]][position]
        item: dict[str, Any] = {"day": row_at(worlds[names[0]], step)["day"],
                                "step": step}
        for name in names:
            world = worlds[name]
            row = row_at(world, step)
            entry = next((e for e in world.agent.record
                          if e["step"] == step), None)
            moved = agent_exposure_change(world, step)
            item[f"exposure_{name}"] = row["exposure"]
            item[f"net_worth_{name}"] = row["net_worth"]
            item[f"agent_change_{name}"] = moved["agent_change"]
            item[f"exposure_before_{name}"] = moved["exposure_before"]
            item[f"exposure_after_fills_{name}"] = moved["exposure_after_fills"]
            # A refused answer executes nothing, keeps the portfolio, and
            # is measured here like any other decision point. It is not
            # retried, and it is not dropped.
            item[f"refused_{name}"] = bool(row.get("unusable"))
            item[f"risk_{name}"] = bool(
                entry is not None
                and RISK_WORDS.search(entry["decision"]["rationale"]))
        out.append(item)
    return out



def ordering(series: Sequence[dict], names: Sequence[str]) -> dict:
    """How often the arms hold their expected order.

    Reported as a count of days, and deliberately not as a p-value.
    Consecutive days of one trajectory are not independent observations,
    so a test that assumed they were would report a confidence nobody
    measured.
    """
    def holds(item, strict: bool) -> bool:
        values = [item[f"exposure_{n}"] for n in names]
        pairs = zip(values, values[1:])
        return all(b < a if strict else b <= a for a, b in pairs)

    failures = [i["day"] for i in series if not holds(i, strict=False)]
    return {
        "expected": " > ".join(names),
        "days": len(series),
        "strict": sum(1 for i in series if holds(i, strict=True)),
        "weak": sum(1 for i in series if holds(i, strict=False)),
        "failures": failures,
        "settled_from_day": (max(failures) + 1) if failures else
                            (series[0]["day"] if series else None),
        "note": ("consecutive days of one trajectory are not independent, "
                 "so this is a description and not a significance test"),
    }


def bands(series: Sequence[dict], names: Sequence[str]) -> dict:
    """One row per arm: where the portfolio sat, and what the agent did.

    `mean_exposure` is where the portfolio ended up, prices and all.
    `agent_change_total` is the part of that the agent caused, summed over
    the window with the market held still at each decision. Reporting only
    the first would let a difference in prices read as a difference in
    behaviour.
    """
    return {n: {
        "mean_exposure": statistics.mean(i[f"exposure_{n}"] for i in series),
        "final_exposure": series[-1][f"exposure_{n}"] if series else None,
        "agent_change_total": sum(i[f"agent_change_{n}"] for i in series),
        "agent_reductions": sum(1 for i in series
                                if i[f"agent_change_{n}"] < 0),
        "agent_additions": sum(1 for i in series
                               if i[f"agent_change_{n}"] > 0),
        "risk_language": sum(1 for i in series if i[f"risk_{n}"]),
        "refused": sum(1 for i in series if i[f"refused_{n}"]),
        "decisions": len(series),
    } for n in names}


def separation(stats: dict, control: str, arm: str, field: str) -> dict:
    """One arm's gap from control, in units of the larger within-arm stdev.

    A ratio and not a test. The denominator is the noise floor measured by
    resampling the same prompt, so a ratio under one says the difference is
    smaller than the variation the agent shows when asked twice.
    """
    gap = stats[arm][f"mean_{field}"] - stats[control][f"mean_{field}"]
    noise = max(stats[arm][f"stdev_{field}"], stats[control][f"stdev_{field}"])
    return {"gap": gap, "noise": noise,
            "ratio": None if noise == 0 else gap / noise}





