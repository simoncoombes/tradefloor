"""Build examples/experiments/liquidity-crisis/notebook.ipynb.

Cells are written here and executed with nbclient, so the committed copy
carries its output and reads on GitHub without a kernel. That is the
repository's own convention for a notebook and what
`tests/test_examples.py` checks.
"""
import sys
from pathlib import Path

import nbformat
from nbclient import NotebookClient

STUDY = Path(__file__).resolve().parent
OUT = STUDY / "notebook.ipynb"

# The figure descriptions live beside the code that draws them.
sys.path.insert(0, str(STUDY))
import charts  # noqa: E402

md, code = [], []
cells = []


def M(text):
    cells.append(nbformat.v4.new_markdown_cell(text.strip("\n")))


def C(text, alt=None):
    """A code cell, optionally declaring what its figure shows.

    `alt` is carried in the cell metadata, for anything that
    renders this notebook to a page. Without it nbconvert
    writes "No description has been provided for this image",
    which is a placeholder rather than a description and is
    what a screen reader reads out.
    """
    cell = nbformat.v4.new_code_cell(text.strip(chr(10)))
    if alt:
        cell.metadata["alt"] = alt
    cells.append(cell)


M("""
# A financial AI agent in a market crisis

This study asks whether a financial AI agent reduces risk when its market
goes into a crisis. It runs [FinRobot](https://github.com/AI4Finance-Foundation/FinRobot)
inside [Tradefloor](https://tradefloor.dev), copies the market it is
trading, and drops a liquidity crisis on one copy.

Twenty-four real companies loaded from SEC filings, fifty million dollars,
twenty shared trading days, then one checkpoint and two arms. One arm
continues unchanged. The other runs under `liquidity_crisis`, the scenario
as Tradefloor shipped it through 0.8.1: quoted depth to 40%, volatility
doubled, and a stated assumption that credit widens fifty basis points.
Both arms run twenty more days under the same agent and the same cadence.

The agent is never told there is a crisis. No word in its observation says
so. It reads a volatility number, a credit spread, prices that have
fallen, and volume and order limits at 40% of their usual size.

**What it did.** In the recorded run the crisis arm held less gross
exposure than control on all twenty days, 0.486 against 0.615 on average.
The agent's own contribution, exposure measured immediately before and
after its fills at the same prices, is -0.056 in the crisis arm and +0.149
in control. Most of control's figure is one decision on day 35.

**Check one, the resample.** Ask each arm the same fork-step question eight
more times, with every byte of the input the same. The crisis arm's answers
are not distinguishable from control's: the gaps read 0.00 and -0.20 times
the within-arm spread.

**Check two, replication.** Run the whole experiment four more times. The
crisis arm carried less exposure in all four. The gap averages 0.055, which
is 0.46 times the spread between runs, and the agent's own trades reduced
exposure more in the crisis arm than in control in two of the four.

Getting to that needs a deterministic market, a fork that leaves the arms
verifiably identical, a way to re-ask one question, and a way to re-run the
whole thing. This notebook does all four in a few seconds, because the
agent's real responses were recorded once and are replayed here.
""")

M("""
## Scope of the claim

Real company fundamentals are the initial conditions. EPS, book value per
share, revenue growth, share count and sector come from SEC EDGAR filings.
Every price, spread, fill and order-book state after step zero is generated
by Tradefloor under the `pt-v21` preset.

`experiment.py` pins `pt-v21`, the shipped default from 0.10.0. A replay is
keyed to the exact text the agent was sent, so it only replays in the
market it was recorded in, and the pin keeps it there when the default
moves on, and the numbers below describe `pt-v21`.

Nothing here predicts or describes the behaviour of any real security. The
tickers are real companies and the market is not.

Three separate bodies of evidence appear below, and they answer different
questions. One canonical recorded trajectory per arm carries the detailed
path analysis. Eight identical fork-step calls per arm measure how much the
agent varies when asked one question repeatedly. Four complete live
replications of the whole experiment provide separate model trajectories
for checking the direction. All of them were recorded live on pt-v21 with
the same model.

The days inside one trajectory are not independent samples of anything.
Where a number cannot carry a claim, the notebook says so rather than
rounding it into one.
""")

M("""
The path lookup in the next cell exists so the notebook opens from
anywhere: a notebook has no `__file__`, and a reader who starts Jupyter at
the repository root is one level above `experiment.py`. Everything after
it is the experiment.
""")

C("""
import sys
from pathlib import Path

HERE = Path("examples/experiments/liquidity-crisis")
CANDIDATES = (Path.cwd(), *Path.cwd().parents)
STUDY = next(
    (d for base in CANDIDATES
     for d in (base, base / HERE)
     if (d / "experiment.py").is_file()), None)
if STUDY is None:
    raise RuntimeError(
        "start Jupyter inside the tradefloor repository, or inside "
        f"{HERE}")
sys.path.insert(0, str(STUDY))

import experiment as ex

import tradefloor as tf
from tradefloor.counterfactual import World, agree, compare
from tradefloor.integrations.finrobot import (
    DecisionError, FinRobotAdapter, Transcript, parse)

print(f"tradefloor {tf.__version__}, preset {ex.PRESET}, seed {ex.SEED}")
""")

M("""
## The universe

Twenty-four companies, two from each of the twelve sectors Tradefloor
models, drawn from a frozen EDGAR snapshot.

Drawn deterministically. Each ticker is ranked inside its sector by
`sha256(salt + "|" + ticker)` under a salt fixed before the run, and the
lowest two are taken. The rule depends on the ticker string and nothing
else, so it cannot be steered by an outcome, and a reader can recompute any
line by hand.
""")

C("""
snapshot = ex.load_snapshot()
small = ex.subset(snapshot)
instruments = ex.universe(small)
fundamentals = ex.fundamentals(small)

print(f"drawn from {len(snapshot.rows)} filers, snapshot {snapshot.hash[:16]}")
print(f"subset of {len(small.rows)}, hash {small.hash[:16]}")
print()
for row in small.rows:
    print(f"  {row['ticker']:<6} {row['sector']:<24} "
          f"eps {row['eps']:>7.2f}   key {ex.universe_key(row['ticker'])[:8]}")
""")

M("""
## The agent

A real FinRobot `SingleAssistant`, driven through autogen, the same class
FinRobot ships. It is handed an observation as text, and it answers with a
JSON portfolio decision that Tradefloor validates and executes.

The observation is an allowlist, written out field by field. Fair value,
the factor attribution of every price move, each company's mispricing and
the macro path the run has not reached are all on the simulator's side of
the line. The agent sees what a trader in this market could see.

Below is the agent's own record of what it was sent. The macro block is the
part this experiment moves.
""")

C("""
transcript = Transcript.load(ex.FIXTURE)
print(f"recorded run: {len(transcript)} interactions, "
      f"{transcript.meta.get('model')} at temperature "
      f"{transcript.meta.get('temperature')}")

# The saved transcript is not kept in decision order, so take the first
# decision the shared history asked for.
first = min((e for e in transcript.entries if e["arm"] == "shared"),
            key=lambda e: e["step"])
sample = first["prompt"]
print()
print(sample[:sample.index("Assets")].rstrip())
""")

M("""
### Refused decisions

At temperature 0 a model still sometimes returns output the market cannot
execute. An older recording of this agent, on a three-arm rate ladder,
contains one: a `rationale` attached to an individual action, which
Tradefloor refuses rather than dropping the unknown field and executing a
trade the agent believed was conditioned on something else.

An action like that is refused on its own, and the rest of the decision
trades. An answer with no decision in it at all, with no JSON object or no
`actions` list, costs the agent the step: `World(on_refusal="skip")`
counts it and moves on. No repair, no retry, no trade. The count is kept
apart from the market-side `refused`, because an agent that could not
format an answer and a market that rejected an order are different
failures.

The counts are published either way, so a reader can tell an agent that
never stumbled from a report that never counted.
""")

C("""
def unusable(entries):
    \"\"\"Responses, or actions in them, this market has no execution path for.\"\"\"
    out = []
    for entry in entries:
        try:
            decision = parse(entry["response"])
        except DecisionError as exc:
            out.append((entry, str(exc)))
            continue
        out.extend((entry, item["reason"]) for item in decision.refused)
    return out


bad = unusable(transcript.entries)
print(f"this recording: {len(bad)} unusable answers or actions "
      f"in {len(transcript)} responses")

# The one that prompted the refusal policy, from the earlier recording of
# this experiment. Shown because a rate of one in eighty is worth seeing
# rather than being told.
older = Transcript.load(ex.LEGACY_FIXTURE)
was_bad = unusable(older.entries)
print(f"the earlier recording: {len(was_bad)} in {len(older)}")
print()
if was_bad:
    entry, reason = was_bad[0]
    print(reason[:200])
    print()
    print(entry["response"][:380].rstrip() + " ...")
""")

M("""
## Twenty shared days

One agent, one market, one decision a day, and no fork yet.
""")

C("""
agent = FinRobotAdapter(mode="replay", transcript=transcript,
                        fundamentals=fundamentals, objective=ex.OBJECTIVE,
                        every=ex.DECISION_EVERY, arm="shared")

world = World(seed=ex.SEED, universe=instruments, agent=agent,
              pins=ex.BASE_PINS, cash=ex.CASH,
              steps_per_day=ex.STEPS_PER_DAY,
              ticks_per_step=ex.TICKS_PER_STEP,
              model=ex.PRESET, label="shared",
              on_refusal="skip")
world.run(days=ex.WARMUP_DAYS)
mark = world.checkpoint("before the interventions")

print(f"after {world.day} days: ${world.net_worth():,.0f}, "
      f"{world.portfolio.leverage(world.engine):.2f}x gross")
print(f"market digest {world.digest()[:32]}...")
print(f"{len(mark.log):,} operations in the checkpoint")
""")

M("""
## One checkpoint and two arms

`fork` produces independent continuations, with separate engines,
separate portfolios and separate agents, so driving one cannot perturb
another.

`agree` then checks that they really are identical rather than merely
similar. Nine checks, every one read back off the two objects: the market
columns, the prices, the order book, the generator position, the macro
chain, the whole engine state, the portfolio, the agent's own state and the
shared history.

Every pair is checked, not one. Two arms make one pair; the six-arm fork
`record.py` makes for each replication makes fifteen, and it checks all of
them.
""")

C("""
names = list(ex.ARMS)
worlds = dict(zip(names, world.fork(*names)))
for label, arm in worlds.items():
    arm.agent.arm = label

for i, left in enumerate(names):
    for right in names[i + 1:]:
        found = agree(worlds[left], worlds[right])
        print(f"{left:>9} vs {right:<9} "
              f"{'identical' if found.identical else 'DIFFERENT'}"
              f"   {len(found.checks)} checks")
        assert found.identical, found.differences
""")

M("""
## The scenario

One branch gets a Tradefloor scenario, read off disk, and the other gets
nothing.

A scenario is a small, explicit description of what changes in the market,
read off disk rather than written into the experiment. It separates what it
asserts happened from what it merely assumes followed, and it carries a
fingerprint, so two people can check they ran the same one.

This one is `liquidity_crisis` as the Tradefloor wheel shipped it through
0.8.1. Quoted depth falls to 40% and volatility doubles for twenty-five
days; alongside them the file states one ASSUMPTION, that credit widens
fifty basis points. Nothing in the simulator derives that third number,
which is why it sits under a different heading.

The file here is that one with a single field changed. Every packaged
scenario fires `at: 50`, because they are written for a single market with
fifty days of warmup, and `World.apply` rebases `at` onto the day it is
applied on -- so handing the packaged file to an arm forked on day 20 would
fire it on day 70. Fifty post-fork days before the shock means fifty days
of the two arms drifting apart on nothing but the agent answering the same
question two ways, and the crisis would then land on two markets that are
no longer comparable. So `at: 0`, and nothing else.

The packaged file has since been recalibrated: the VIX goes three and a
half times rather than two, and earnings fall 15% and recover. So the check
below, against the package in this wheel, reads False. This study runs the
0.8.1 shocks, and the shocks it prints are this file's.
""")

C("""
scenario = ex.load_scenario()
packaged = tf.Scenario.load(ex.PACKAGED_SCENARIO)


def shape(s):
    return sorted((i.target, i.operation, i.value, i.role)
                  for i in s.interventions)


print(f"{scenario.name}  {scenario.fingerprint}")
print(f"rebased from {packaged.name}  {packaged.fingerprint}")
print(f"every shock identical to the packaged file: "
      f"{shape(scenario) == shape(packaged)}")
print()
for item in scenario.interventions:
    print(f"  {item.role:<12}{item.target:<24}"
          f"{item.operation} {item.value}   "
          f"days {item.at} to {item.last_day}")
""")

M("""
`market.liquidity` is the one target here outside the macro fields, and it
is the only lever in Tradefloor that touches execution. It scales the volume
column the market maker quotes off, so every ladder level thins and the same
trade costs more to put on.

It also reaches the agent. The volume figure in the observation and the
order cap the agent is held to are read from the same column, so under the
shock the agent sees, and is allowed, 40% of the size.
""")

M("""
Both arms also run with the depth counterfactual on. It settles every open
tick a second time against every resting level, under the same four
uniforms and from the same book state, and writes what that would have
printed into a column beside the real print. It takes no draw and its
fills reach nothing, so the market is the market either way; the arms
below are the same arms, and their exposure numbers are the same numbers.
""")

C("""
for label, treatment in ex.ARMS.items():
    print(f"{label:>9}  {ex.treat(worlds[label], treatment)}")

for arm in worlds.values():
    arm.engine.settle_depth_counterfactual(True)
    arm.run(days=ex.BRANCH_DAYS)
    # The engine holds one day's tape at a time, so this keeps the last
    # one for the depth reading further down.
    arm.engine.record(arm.day - 1)
print(f"\\nboth arms ran {ex.BRANCH_DAYS} days")
""")

M("""
### The difference in the prompt

This is the whole difference between the two prompts at the first decision
after the fork, grouped by the line it falls on.

The two macro lines are the scenario itself. Everything else is the crisis
market having already traded: by the time the agent is asked, every price,
return, volatility and spread differs, the volume and order-size lines sit
at 40%, and the portfolio is worth less.

Compare it with experiment 001, which moved a policy rate by 200 basis
points over 421 companies: two lines of 376, and the agent barely moved. A
rate shock is a LEVEL shift, and an observation carrying prices and
five-day returns shows a one-off repricing for exactly one return window.
After that the only trace is two decimal places.
""")

C("""
import re
from collections import Counter

step = worlds[ex.CONTROL_ARM].fork_step
prompts = {n: next(e["prompt"] for e in worlds[n].agent.record
                   if e["step"] == step) for n in names}
base = prompts[ex.CONTROL_ARM].splitlines()
tickers = set(worlds[ex.CONTROL_ARM].engine.tickers)


def kind(line):
    label = re.split(r"\\s{2,}", line.strip())[0]
    return "a holding's value" if label in tickers else label


def day_returns(prompt):
    return [float(v) for v in
            re.findall(r"return, 1 day\\s+([-+0-9.]+)%", prompt)]


for name in ex.TREATMENTS:
    lines = prompts[name].splitlines()
    diff = [(a, b) for a, b in zip(base, lines) if a != b]
    print(f"{name}: {len(diff)} of {len(base)} lines differ")
    for a, b in diff:
        if kind(a) in ("vix", "corporate_bond_yield"):
            print(f"    - {a.strip()}")
            print(f"    + {b.strip()}")
    print()
    for label, count in Counter(kind(a) for a, _ in diff).most_common():
        print(f"  {count:>4}  {label}")
    print()
    for arm in (ex.CONTROL_ARM, name):
        r = day_returns(prompts[arm])
        print(f"{arm:>9}: one-day return, mean {sum(r) / len(r):+.2f}%, "
              f"{sum(1 for v in r if v < 0)} of {len(r)} names down")
""")

M("""
## The agent's response in the recorded run

Gross exposure, per decision, in each arm. Gross exposure rather than P&L:
after the fork the arms trade different markets, so comparing their returns
measures the market as much as the agent.

Read this as one run rather than as the result, because two checks follow
it and they qualify it.
""")

C("""
series = ex.exposure_series(worlds)
bands = ex.bands(series, names)
order = ex.ordering(series, names)

print(f"{'day':>4}" + "".join(f"{n:>12}" for n in names) + "   risk words")
for item in series:
    marks = "".join("Y" if item[f"risk_{n}"] else "." for n in names)
    print(f"{item['day']:>4}"
          + "".join(f"{item[f'exposure_{n}']:>12.2f}" for n in names)
          + f"   {marks}")

print()
for name in names:
    row = bands[name]
    print(f"{name:<10} mean gross exposure {row['mean_exposure']:.3f}   "
          f"risk language {row['risk_language']}/{row['decisions']}")
""")

C("""
import matplotlib.pyplot as plt
import charts

shared = charts.shared_history(world, ex.WARMUP_DAYS,
                               ex.STEPS_PER_DAY)
charts.exposure_bands(series, bands, shared, ex.WARMUP_DAYS)
plt.show()
""",
  alt=charts.ALT["exposure-bands"])

M("""
### The agent's own trades

Gross exposure moves for two reasons: the agent trades, and prices move
under a portfolio nobody touched. After the fork the arms trade different
markets, so the gap above is not by itself evidence that the agent did
anything.

So measure the agent's part alone. At each decision, gross exposure
immediately before its fills and immediately after, priced at the SAME
arrival prices. The market is held still and what is left is behaviour.
""")

C("""
for name in names:
    row = bands[name]
    largest = max(series, key=lambda i: abs(i[f"agent_change_{name}"]))
    print(f"{name:>9}  agent moved exposure "
          f"{row['agent_change_total']:+.3f} over {row['decisions']} "
          f"decisions   ({row['agent_reductions']} down, "
          f"{row['agent_additions']} up); largest single decision "
          f"{largest[f'agent_change_{name}']:+.3f} on day {largest['day']}")
""")

C("""
charts.agent_actions(series, bands)
plt.show()
""",
  alt=charts.ALT["agent-actions"])

C("""
print(f"expected ordering: {order['expected']}")
print(f"holds strictly on {order['strict']} of {order['days']} days")
print(f"days where it fails: {order['failures'] or 'none'}")
print(f"settled from day {order['settled_from_day']} onward")
print()
print(order["note"])
""")

M("""
### The market's side of each print

The exposure numbers above are the agent's side of the crisis. This is the
market's side, on the same ticks.

Every print decomposes into two log distances. `shock` is the distance from
the last print to the price the model wanted; `absorbed` is the distance
from there to what actually printed, and they sum to the print's own move.
`absorbed` is the book, plus the circuit breaker where it fired.

Then the counterfactual. The same tick is settled a second time against
every resting level, under the same four uniforms and from the same book
state, so the two prints differ only where the flow reached the end of the
quoted depth. `liquidity_share` is `log(print / unbounded_print)` over the
print's own move.

Read the share through the identity the column satisfies: the unbounded
book's move is `1 - share` times the printed move. A NEGATIVE share is a
truncated walk. An order that exhausts a shallow book stops there, while
against every resting level it keeps filling and prints further from where
it started, so a share of -1 means the deeper book would have moved the
price twice as far. A positive share is the other case, where the deeper
book would have absorbed part of the move.

`market.liquidity` at 40% is a claim about depth, and this is the column
that reads it back off the tape rather than off the scenario file.
""")

C("""
depth = ex.depth_readings(worlds)

print(f"{'arm':<10}{'prints':>9}{'depth reached':>15}{'of prints':>11}"
      f"{'median share':>14}{'negative':>10}{'mean |absorbed|':>17}")
for name in names:
    row = depth[name]
    print(f"{name:<10}{row['rows']:>9,}{row['moved']:>15,}"
          f"{row['moved_fraction']:>10.2%}"
          f"{row['median_share']:>14.3f}"
          f"{row['shares_negative']:>10,}"
          f"{row['mean_abs_absorbed_bps']:>14.1f} bps")

print()
print(f"day {depth[names[0]]['day']}, the last of the post-fork window, "
      f"{depth[names[0]]['instruments']} names over 390 ticks")
print("median share is signed; mean |absorbed| is a distance, because the "
      "signed mean cancels across up and down ticks")
print()

import pyarrow as pa
for name in names:
    tape = pa.table(worlds[name].engine.prints()).to_pydict()
    signed = 1e4 * sum(tape["absorbed"]) / len(tape["absorbed"])
    halted = sum(1 for v in tape["clamp"] if v)
    print(f"{name:<10}signed mean absorbed {signed:+.1f} bps, "
          f"clamp nonzero on {halted} of {len(tape['clamp']):,} rows")
""")

M("""
Flow reaches the end of the quoted depth on a small minority of prints in
both arms, which is what the book is built for: it quotes the depth this
tick's flow can reach, and ordinary flow does not leave the top level or
two.

Flow runs out of book about twice as often in the crisis arm, 51 prints
against 25. In the crisis arm two in three of those prints carry a
negative share, where the depth bound cut a walk short and a deeper book
would have moved the price further. In control about half do, and the
other half are prints a deeper book would have moved less. With 25 and 51
events on one day, the two medians are a description of these prints and
not a measured property of either book.

The mean distance from the model price to the print is 3.0 basis points in
control and 3.8 in the crisis arm. The circuit breaker plays no part: the
`clamp` column is zero on every row of both arms.

One day of one recorded run, and one tick at a time. The counterfactual
prints from the real state and stops there: the inventory a deeper book
would have left is discarded, so this says nothing about what the next tick
would have done.
""")

M("""
## Check one: the same question asked again

Everything above is the canonical trajectory, one per arm. Before reading
the separation as a response, it needs something to be compared against.

Tradefloor removes every source of variation except one. The market is
deterministic, the arms share a history that `agree` verified, and the
prompts differ in the handful of lines printed above. So the only thing
left that can move is the agent itself, and the size of that is
measurable: ask each arm's fork-step question again, several times, and
look at the spread.

These are recorded, because a resample is N answers to one question and a
transcript holds one answer per question.
""")

C("""
import json

recorded = json.loads((ex.HERE / "data" / "resample-fork.json")
                      .read_text(encoding="utf-8"))
stats = recorded["stats"]
print(f"n = {recorded['n']} per arm, {recorded['model']} at "
      f"temperature {recorded['temperature']}\\n")
for name in names:
    row = stats[name]
    print(f"{name:<10} {row['distinct']}/{row['calls']} distinct answers   "
          f"net buys-sells {row['mean_net']:+.2f} "
          f"+/- {row['stdev_net']:.3f}")

print("\\ngap from control, in units of the larger within-arm stdev:")
for name in ex.TREATMENTS:
    for field in ("net", "gross"):
        sep = ex.separation(stats, ex.CONTROL_ARM, name, field)
        print(f"  {name:<10} {field:<6} {sep['ratio']:+.2f}x noise")
""")

M("""
Neither ratio is above one. Asked once, at the moment the intervention
lands, the crisis arm is not distinguishable from control: both arms'
answers buy two more names than they sell on average, and the crisis arm's
answers vary more, six distinct answers in eight against two.

A single decision pair could have read as a response in either direction.
This is the yardstick it has to be read against.
""")

M("""
## Check two: run the whole thing again

The resample bounds one decision. It says nothing about the path, and the
exposure gap is a property of the path.

So run the experiment again. The seed, the universe, the scenario and the
cadence are identical every time, which means the only thing that varies
between replications is the agent. Two replications differ by the agent
and by nothing else, from the first shared day on, so each one builds its
own book in the twenty shared days before the fork.

Four replications, each a full twenty shared days and twenty days per arm,
recorded live.
""")

C("""
replications = json.loads((ex.HERE / "data" / "replications.json")
                          .read_text(encoding="utf-8"))

print(f"{replications['replications']} replications, "
      f"ordering holds on the means in "
      f"{replications['ordering_holds_on_means']}")
print()
print(f"{'run':>4}" + "".join(f"{n:>10}" for n in names)
      + "       gap   ordering   agent's own change")
for row in replications["rows"]:
    gap = row["mean_exposure"][names[0]] - row["mean_exposure"][names[1]]
    print(f"{row['index']:>4}"
          + "".join(f"{row['mean_exposure'][n]:>10.3f}" for n in names)
          + f"{gap:>+10.3f}"
          + f"   {row['ordering_strict']:>2}/{row['ordering_days']} days"
          + "".join(f"{row['agent_change'][n]:>+10.3f}" for n in names))
print()
for name in names:
    row = replications["per_arm"][name]
    print(f"{name:<10} mean of means {row['mean_of_means']:.3f} "
          f"+/- {row['stdev_of_means']:.3f}   "
          f"range {row['min']:.3f} to {row['max']:.3f}")
""")

C("""
print("gap between adjacent arms, against the spread across replications:")
print()
for pair, row in replications["gaps"].items():
    ratio = "n/a" if row["ratio"] is None else f"{row['ratio']:.2f}x"
    print(f"  {pair:<22} gap {row['mean_gap']:+.3f}   "
          f"spread {row['spread']:.3f}   {ratio:>6} "
          f"   same sign in {row['positive_in']}/"
          f"{replications['replications']}")
""")

C("""
charts.replications(replications)
plt.show()
""",
  alt=charts.ALT["replications"])

M("""
Crisis exposure was lower than its paired control in all four runs, by
0.018 to 0.084.

The levels move far more than the gap. Control ranges from 0.415 to 0.689
across runs, because each run's agent built a different book in the shared
days, and the gap averages 0.46 times that spread. Read against the
between-run spread, the direction is consistent and the size is small.

The agent's own trades do not carry the gap in every run. They reduced
exposure more in the crisis arm than in control in runs 2 and 4, and less
in runs 1 and 3. Where they did not, the crisis arm's lower exposure came
from prices: the crisis market falls at the open, which shrinks a long
book's gross exposure without a trade.
""")

M("""
## The rate shock in parts

The same four replications carry a second comparison, which asks how much
of the observation an intervention has to change before the agent acts on
it. It uses hand-written macro moves instead of a scenario:

    +200bps      the policy rate and the corporate yield up 200 bps
    vix          VIX to 45, and nothing else
    cycle        the cycle phase expansion -> contraction, and nothing else
    rate+regime  all four together

Every arm of a fork is bit-identical at the fork whatever the arm count,
so these four arms were forked from the same shared histories as the two
above, and `control` is the same arm in both comparisons.

The arms differ in a way the numbers cannot separate. `cycle` is a word
and changes nothing else in the observation. `vix` is a number that also
widens spreads immediately, so that arm trades a rougher market as well as
reading a higher figure.
""")

C("""
decomp = json.loads((ex.HERE / "data" / "decomposition.json")
                    .read_text(encoding="utf-8"))
arms5 = list(decomp["arms"])

print(f"{decomp['replications']} replications, five arms")
print()
for name in arms5:
    row = decomp["per_arm"][name]
    print(f"  {name:<12} {row['mean_of_means']:.3f} "
          f"+/- {row['stdev_of_means']:.3f}   "
          f"range {row['min']:.3f} to {row['max']:.3f}")

print()
print("against control, in units of the larger spread:")
print()
for name, row in decomp["against_control"].items():
    ratio = "n/a" if row["ratio"] is None else f"{row['ratio']:+.2f}x"
    print(f"  {name:<12} gap {row['gap']:+.3f}   "
          f"spread {row['spread']:.3f}   {ratio:>7}   "
          f"lower than control in {row['lower_in']}/"
          f"{decomp['replications']}")
""")

C("""
charts.decomposition(decomp)
plt.show()
""",
  alt=charts.ALT["decomposition"])

M("""
No arm separates from control. Every gap is under half its spread.
`rate+regime` reads 0.46 times and `vix` 0.41 times, each lower than
control in three replications of four. `+200bps` reads 0.18 times. `cycle`
sits above control on average, at -0.18 times, and is lower in one
replication of four.

So at four replications none of these interventions moves mean exposure by
more than the agent's own variation between runs, and the parts cannot be
compared with the whole. Telling an interaction from noise here would need
more runs than this.
""")

M("""
## The checks together

| | one decision | four replications |
|---|---|---|
| crisis, net direction | 0.00x the within-arm spread | |
| crisis, gross size | -0.20x | |
| mean exposure | | 0.46x the between-run spread, lower in 4 of 4 |
| agent's own change | | more negative than control in 2 of 4 |

The three checks answer different questions. Asked the same fork-step
question again, the agent gives answers the crisis arm cannot be told
apart from control by. The canonical trajectory separates on all 20 days,
by 0.129 of gross exposure on average, and its days are not independent
samples. The four live replications are the strongest check here: the
direction held in all four, and the size of the gap is small against how
far the runs differ from each other.

What the runs support: under this scenario the crisis arm carried less
gross exposure than its paired control in five runs out of five. What they
do not settle is how much of that is the agent. The agent-only measure
attributes the canonical gap to trades in the direction of the result, and
in the replications it does so in two runs of four; the rest is the
crisis market repricing a long book.

The paths suggest why the first decision did not tell the whole story. The
agent adjusts at each decision rather than reacting once and holding, so a
single decision is a small step and twenty of them, on a market that has
already fallen, are the separation.
""")

M("""
## Limits

- **Four replications, one market.** They all run the same market seed, so
  they resample the AGENT and not the market. Nothing here shows the result
  holds on a different market. Four is also not enough for a confidence
  interval, and none is quoted.
- **The days inside a run are autocorrelated.** Consecutive days of one
  trajectory are not independent observations, so a per-run ordering count
  is a description and not a significance test. The replication count is
  the number to read instead.
- **One model, one temperature.** `claude-sonnet-4-5-20250929` at
  temperature 0. Claude Opus 5 and Sonnet 5 have deprecated `temperature`
  and answer `400 invalid_request_error` to any explicit value, and
  `pyautogen` 0.2.35 sends one on every request. Those models run through
  FinRobot at the default of 1.0; what they cannot do is run at
  temperature 0, which is what a reproducible study wants.
- **One agent.** Everything here describes what FinRobot did. Another
  framework, or another mandate, is another experiment.
- **This universe is uncertified.** Twenty-four names is outside the roster
  Tradefloor's published realism envelope is measured on. The market is a
  valid Tradefloor market and it has not been measured against that
  envelope.
- **Risk language is counted, not read.** A regular expression counts
  decisions whose rationale contains one of a fixed list of words. In the
  canonical run that is 16 of 20 decisions in the crisis branch and 14 of
  20 in control; across the replications the crisis arm used them more
  often in two runs of four. It is a count of words, and nothing about
  what the agent meant by them.
- **The scenario moves three targets at once.** Depth, volatility and the
  credit spread all change together, so which of them the agent responded
  to is unmeasured here. The decomposition above splits the rate-and-regime
  arm and finds no part that separates, which is the same problem measured
  on a different treatment.
- **The credit widening is an assumption.** The scenario file labels it as
  one. Nothing in Tradefloor derives fifty basis points from a depth
  collapse, and a reader who disagrees can change the number and re-run.
""")

M("""
## Reproduction

```bash
pip install "tradefloor[arrow]" matplotlib
git clone https://github.com/simoncoombes/tradefloor
cd tradefloor/examples/experiments/liquidity-crisis
jupyter lab notebook.ipynb
```

Or rebuild the notebook from the module, which is what produced the copy
committed here:

```bash
pip install matplotlib pyarrow nbformat nbclient
python build_notebook.py
```

`pyarrow` is for the depth reading, which reads the prints table, and
`matplotlib` draws the charts. Either way it makes no model call, needs no
API key and reaches no network. Every decision the agent took was recorded
once, live, and is replayed from
`tests/fixtures/finrobot/liquidity-crisis.json`.

## Recording it again

`record.py` makes the recordings, and every command in it except
`summarise` calls the model and costs money. `canonical` writes the run
this notebook replays (60 calls), `replication N` one replication with all
six arms (140 calls), and `resample` the sixteen re-asks; `summarise`
rebuilds the three files in `data/` from what they wrote. On this study
the full set came to 636 calls. It needs the FinRobot extra, which installs
on Python 3.11 only, and `ANTHROPIC_API_KEY`.

What is here is enough to read the experiment, re-execute it, record it
again, and check every number in it against the recording.
""")

nb = nbformat.v4.new_notebook(cells=cells)
nb.metadata["kernelspec"] = {"display_name": "Python 3",
                             "language": "python", "name": "python3"}
nb.metadata["language_info"] = {"name": "python"}

print(f"{len(cells)} cells, executing...")
NotebookClient(nb, timeout=900, kernel_name="python3",
               resources={"metadata": {"path": str(STUDY)}}).execute()
nbformat.write(nb, OUT)
print(f"wrote {OUT}")
empty = [i for i, c in enumerate(nb.cells)
         if c.cell_type == "code" and not c.get("outputs")]
print(f"code cells with no output: {empty or 'none'}")
