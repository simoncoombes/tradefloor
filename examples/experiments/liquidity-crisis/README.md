# Will a financial AI agent reduce risk in a market crisis?

A [FinRobot](https://github.com/AI4Finance-Foundation/FinRobot) agent
manages twenty-four real companies and fifty million dollars for twenty
simulated trading days. The run is checkpointed and forked in two. One
branch continues unchanged; the other runs under `liquidity_crisis`, the
scenario that ships in the wheel. Both run twenty more days under the same
agent, the same mandate and the same daily cadence.

```
MARKET -> FINROBOT -> CHECKPOINT -> FORK x2 -> SCENARIO -> COMPARE
```

The agent is never told there is a crisis. No word in its observation says
so; it reads a volatility number, a credit spread, and a book with 40% of
its usual depth.

## The result

| arm | mean gross exposure | the agent's own change | risk words |
|---|---:|---:|---:|
| control | 0.859 | +0.122 | 12/20 |
| crisis | 0.636 | -0.128 | 17/20 |

Gross exposure moves for two reasons, and only one of them is the agent.
The second column holds the market still: at each decision, exposure
immediately before the fills and immediately after, at the same arrival
prices. The sign flips.

Across four live replications the crisis arm carried less exposure in
three. Two observations about the fourth, without reading more into them:
the crisis values occupy a narrower range than the control values, and run
4's crisis figure sits among the other crisis figures while its control
figure sits below the other three controls. We do not know why the model
produced that trajectory.

## Liquidity's share of the print

Both arms also run with the depth counterfactual on. It settles every open
tick a second time against every resting level, under the same four
uniforms and from the same book state, so the two prints differ only where
the depth bound was reached. It takes no draw and its fills reach no
company field, so the arms above are the same arms and their exposure
numbers are the same numbers.

| arm | prints | depth reached | median share | negative | mean \|absorbed\| |
|---|---:|---:|---:|---:|---:|
| control | 9,360 | 71, or 0.76% | -1.002 | 62 of 71 | 23.3 bps |
| crisis | 9,360 | 191, or 2.04% | -1.002 | 172 of 191 | 34.7 bps |

`market.liquidity` at 40% is a claim about depth, and these columns read it
back off the tape. The crisis changes how OFTEN flow runs out of book
rather than how far it goes when it does: 2.7 times as many prints reach
the end of the quoted depth, at the same median share. The mean distance
from the model price to the print rises with that count.

The share is negative because the depth bound truncates a walk. An order
that exhausts a shallow book stops there, while against every resting level
it keeps filling and prints further from where it started, so the real
print sits between the last print and the unbounded one. A share of -1.0
says the deeper book would have moved the price twice as far, since the
unbounded move is `1 - share` times the printed one.

The absorption column is a distance and is reported as one, because
absorption is signed with the move and the signed mean cancels to -0.4
basis points across up and down ticks. It carries the circuit breaker
alongside the book, and the `clamp` column is the breaker's own part of it.
Neither arm halted a name on this day: `clamp` is zero on all 9,360 rows of
each, so both figures above are the book alone. The counterfactual prints
one tick from the real state, so it says nothing about what a deeper book
would have done to the tick after.

Measured on day 39, the last day of the post-fork window, over 390 ticks
and the twenty-four names drawn from `data/edgar-2026-08-31.json`. Seed
4242, universe seed 4242, preset `pt-v16`, at commit `7ad6235`. The
notebook prints this table from `ex.depth_readings(worlds)`.

## Reading it

`notebook.ipynb` carries its output, so it reads on GitHub without a
kernel. To re-execute it:

```bash
pip install tradefloor matplotlib nbformat nbclient
python build_notebook.py
```

No model call, no API key, no network. Every decision the agent took was
recorded once, live, and is replayed from
[`tests/fixtures/finrobot/liquidity-crisis.json`](../../../tests/fixtures/finrobot/liquidity-crisis.json).

## What is here

| | |
|---|---|
| `notebook.ipynb` | the experiment, executed, with its output |
| `build_notebook.py` | builds and runs the notebook |
| `experiment.py` | the design as constants and functions; the notebook imports it |
| `charts.py` | every figure, so the notebook and any published copy draw the same one |
| `scenarios/` | the scenario, as a file a reader can open and change |
| `data/` | the frozen EDGAR snapshot and the recorded summaries |

The recordings live in `tests/fixtures/finrobot/`, not here, because the
test suite and this example read the same ones and two copies of a
recording drift apart.

## The scenario

`scenarios/liquidity_crisis_at_fork.yml` is the packaged `liquidity_crisis`
with one field changed. Every packaged scenario fires `at: 50`, and
`World.apply` rebases that onto the day it is applied on, so handing the
packaged file to an arm forked on day 20 fires it on day 70. Fifty
post-fork days before the shock is fifty days of the two arms drifting
apart on nothing but the agent answering the same question two ways.

So `at: 0`, and nothing else. Both fingerprints are recorded, and the
notebook checks that every shock, value and window matches the packaged
file rather than asking you to believe it.

`market.liquidity` is the one target here that is not a macro field, and
the only lever that touches execution. It scales the volume column the
market maker quotes off, so every ladder level thins and the same trade
costs more to put on.

## What is not here

This directory is the notebook and the module it imports. The runner, the
validator, the replication driver and the publication figures live in the
study repository this came from.
