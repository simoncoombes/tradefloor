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
