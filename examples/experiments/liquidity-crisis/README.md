# A financial AI agent in a market crisis

This study asks whether a financial AI agent reduces risk when its market
goes into a crisis. A [FinRobot](https://github.com/AI4Finance-Foundation/FinRobot) agent
manages twenty-four real companies and fifty million dollars for twenty
simulated trading days. The run is checkpointed and forked in two. One
branch continues unchanged; the other runs under `liquidity_crisis`, the
scenario as Tradefloor shipped it through 0.8.1. Both run twenty more days
under the same agent, the same mandate and the same daily cadence, on the
`pt-v21` market.

```
MARKET -> FINROBOT -> CHECKPOINT -> FORK x2 -> SCENARIO -> COMPARE
```

The agent is never told there is a crisis. No word in its observation says
so; it reads a volatility number, a credit spread, prices that have fallen,
and volume and order limits at 40% of their usual size.

Every run here was recorded live on `pt-v21` with
`claude-sonnet-4-5-20250929` at temperature 0: 636 model calls in all.

## The result

In the recorded run the crisis arm held less gross exposure than control
on all twenty days.

| arm | mean gross exposure | the agent's own change | risk words |
|---|---:|---:|---:|
| control | 0.615 | +0.149 | 14/20 |
| crisis | 0.486 | -0.056 | 16/20 |

Gross exposure moves for two reasons, and only one of them is the agent.
The second column holds the market still: at each decision, exposure
immediately before the fills and immediately after, at the same arrival
prices. The crisis arm's trades took exposure down and control's added to
it, but most of control's +0.149 is one decision on day 35 that added
0.158. The rest of the gap is the market: by the agent's first decision
under the crisis every one of the twenty-four names is down, by 3.4% on
average over the day, and a long book's gross exposure falls with its
prices.

## Four replications

The whole experiment run again four times, with the same seed, universe,
scenario and cadence, so only the agent varies.

| run | control | crisis | gap | agent's own change, control | agent's own change, crisis |
|---|---:|---:|---:|---:|---:|
| 1 | 0.415 | 0.374 | +0.041 | +0.031 | +0.068 |
| 2 | 0.559 | 0.475 | +0.084 | -0.114 | -0.215 |
| 3 | 0.629 | 0.611 | +0.018 | -0.231 | -0.177 |
| 4 | 0.689 | 0.614 | +0.075 | -0.035 | -0.260 |

The crisis arm carried less exposure in all four. The gap averages 0.055,
which is 0.46 times the spread of either arm's level between runs (0.118
for control): each run's agent builds a different book in the shared days,
and the levels differ far more than the gap does. The agent's own trades
reduced exposure more in the crisis arm than in control in runs 2 and 4,
and less in runs 1 and 3.

Asked the first post-fork question eight more times, with every byte of
the input the same, the crisis arm's answers cannot be told from
control's: the gap is 0.00 times the within-arm spread on net direction
and -0.20 times on size.

What the runs support: under this scenario the crisis arm carried less
gross exposure than its paired control in five runs out of five. What they
do not settle is how much of that the agent did. Four replications on one
market seed are not enough for a confidence interval, and none is quoted.

## The rate shock in parts

The same four shared histories carry a second fork, of hand-written macro
moves: `+200bps` on the policy rate and corporate yield, `vix` to 45 alone,
`cycle` to `contraction` alone, and `rate+regime`, all four together.
`control` is the same arm as above.

| arm | mean of four | gap from control | gap / spread | lower than control |
|---|---:|---:|---:|---:|
| control | 0.573 | | | |
| +200bps | 0.549 | +0.024 | 0.18x | 3 of 4 |
| vix | 0.525 | +0.048 | 0.41x | 3 of 4 |
| cycle | 0.598 | -0.025 | -0.18x | 1 of 4 |
| rate+regime | 0.511 | +0.062 | 0.46x | 3 of 4 |

No arm separates from control: every gap is under half its spread.

## Liquidity's share of the print

Both arms also run with the depth counterfactual on. It settles every open
tick a second time against every resting level, under the same four
uniforms and from the same book state, so the two prints differ only where
the depth bound was reached. It takes no draw and its fills reach no
company field, so the arms above are the same arms and their exposure
numbers are the same numbers.

| arm | prints | depth reached | median share | negative | mean \|absorbed\| |
|---|---:|---:|---:|---:|---:|
| control | 9,360 | 25, or 0.27% | +0.167 | 12 of 25 | 3.0 bps |
| crisis | 9,360 | 51, or 0.54% | -0.333 | 33 of 51 | 3.8 bps |

`market.liquidity` at 40% is a claim about depth, and these columns read it
back off the tape. Flow runs out of quoted book about twice as often in the
crisis arm. The unbounded move is `1 - share` times the printed one, so a
negative share is a walk the depth bound cut short, where a deeper book
would have moved the price further, and a positive share is a move a deeper
book would have absorbed part of. Two in three of the crisis arm's
depth-reached prints are the first kind, against about half of control's.
With 25 and 51 events on one day, the medians describe these prints and
nothing wider.

The absorption column is a distance and is reported as one, because
absorption is signed with the move and up and down ticks cancel: the
signed mean is +1.3 basis points on the control arm and +1.6 on the crisis
arm, against 3.0 and 3.8 for the distance. It carries the circuit breaker
alongside the book, and the `clamp` column is the breaker's own part of it.
Neither arm halted a name on this day: `clamp` is zero on all 9,360 rows of
each, so both figures above are the book alone. The counterfactual prints
one tick from the real state, so it says nothing about what a deeper book
would have done to the tick after.

Measured on day 39, the last day of the post-fork window, over 390 ticks
and the twenty-four names drawn from `data/edgar-2026-08-31.json`. Seed
4242, universe seed 4242, preset `pt-v21`, on tradefloor 0.10.1. The
notebook prints this table from `ex.depth_readings(worlds)`.

## Reading it

`notebook.ipynb` carries its output, so it reads on GitHub without a
kernel. Every decision the agent took in the canonical run was recorded
once, live, and is replayed from
[`tests/fixtures/finrobot/liquidity-crisis.json`](../../../tests/fixtures/finrobot/liquidity-crisis.json).
This rebuilds and re-executes the notebook, with no model call, no API key
and no network:

```bash
pip install "tradefloor[arrow]" matplotlib nbformat nbclient
python build_notebook.py
```

`experiment.py` pins `pt-v21`. A replay is keyed to the exact text the
agent was sent, so the pin keeps the recording replaying when the shipped
default moves on.

## Contents

| | |
|---|---|
| `notebook.ipynb` | the experiment, executed, with its output |
| `build_notebook.py` | builds and runs the notebook |
| `experiment.py` | the design as constants and functions; the notebook imports it |
| `record.py` | records the runs live, and rebuilds `data/` from them |
| `charts.py` | every figure, so the notebook and any published copy draw the same one |
| `scenarios/` | the scenario, as a file a reader can open and change |
| `data/` | the frozen EDGAR snapshot and the recorded summaries |

The canonical recording lives in `tests/fixtures/finrobot/`, not here,
because the test suite and this example read the same one and two copies of
a recording drift apart. The replication transcripts are about 1.7 MB each
and are not committed; `data/` holds their summaries.

## Recording it again

Every command in `record.py` except `summarise` calls the model and costs
money. It needs the FinRobot extra, which installs on Python 3.11 only, and
`ANTHROPIC_API_KEY`:

```bash
pip install "tradefloor[finrobot,arrow]"
python record.py canonical          # 60 calls, the run the notebook replays
python record.py replication 1      # 140 calls each, runs 1 to 4
python record.py resample           # 16 calls
python record.py summarise          # no calls: rebuilds data/
```

A replication forks six arms from one shared history: the study's two and
the four treated arms of the decomposition. Every arm of a fork is
bit-identical at the fork whatever the arm count, so one shared history
serves both comparisons.

## The scenario

`scenarios/liquidity_crisis_at_fork.yml` is the packaged `liquidity_crisis`
as 0.8.1 shipped it, with one field changed. Every packaged scenario fires
`at: 50`, and `World.apply` rebases that onto the day it is applied on, so
handing the packaged file to an arm forked on day 20 fires it on day 70.
Fifty post-fork days before the shock is fifty days of the two arms
drifting apart on nothing but the agent answering the same question two
ways.

So `at: 0`, and nothing else. Both fingerprints are recorded. The packaged
file has since been recalibrated (the VIX goes three and a half times
rather than two, and earnings fall 15% and recover), so the notebook's
check against the package in this wheel reads False. The study runs the
0.8.1 shocks: depth, volatility and the credit assumption, with no earnings
path. A study that wants the current package fires it on the first day
after the fork with
`World.apply(tf.Scenario.load("liquidity_crisis"), at=0)`.

`market.liquidity` is the one target here outside the macro fields, and
the only lever that touches execution. It scales the volume column the
market maker quotes off, so every ladder level thins and the same trade
costs more to put on.
