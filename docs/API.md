# API overview

What the package holds and how its main calls fit together. The README has
the install line and a first run, and this page has the contents table,
price attribution, market forks and scenarios.

## Package contents

Give tradefloor a seed and a list of companies. It runs a market forward: prices, a
limit order book, fills, and an economy that moves each day. An agent or a
strategy trades in that market, and its orders match against the book's
depth, so its trades move the price. `tf.evaluate` scores an agent on one
seeded market, and `tf.rank` compares agents across many seeds with a paired
sign test.

| | |
|---|---|
| `engine.truth()` | why each price moved: twelve factors that sum to the mispricing's move, to 1e-16 |
| `engine.prints()` | how each trade price came about: the shock, and the order book depth that absorbed it |
| counterfactual TCA | your trading cost, from the same seed run with your orders and without them |
| `tf.rank` | many seeds, paired sign tests |
| `RunManifest` | what a reader needs to replay a run, checked by `reproduce()` |
| `World` / `compare` | fork a running experiment, change one variable, and measure where the two came apart |
| scenarios | eight packaged shocks, and a file format for your own |
| MCP server | twenty-two tools for a coding agent, sixteen of them read-only, with scenarios, any shipped preset, a shipped population of background traders, and market sessions you step, fork and rewind |
| more | a Gymnasium environment, Arrow output, checkpoints, SEC EDGAR data, simulated rate indices, a browser build |

The agent calls and the scoring rules are in
[docs/AGENTS.md](https://github.com/simoncoombes/tradefloor/blob/main/docs/AGENTS.md),
and the MCP server's install and client configuration are in
[docs/MCP.md](https://github.com/simoncoombes/tradefloor/blob/main/docs/MCP.md).

## Versions and names

The API may change before 1.0. Model changes ship as new presets, so a market
with no agent orders in it replays exactly on its named preset in later
releases. tradefloor was called pretium until 0.5.0. Versions up to 0.4.3
still install under that name, and results recorded with them still replay.

## Price attribution

Real market data can't tell you what would have happened if you had traded
differently, or what caused a move. tradefloor can, because it computed every
price. You can fork a running market, change one thing in one branch (a rate
rise, a liquidity crisis, a different agent), and measure where the two
branches came apart. `engine.truth()` splits each move in the gap between a
price and the model's fair value into twelve factors, and
`engine.explain(ticker, day)` breaks down the move in the traded price, two
records no historical dataset carries.

## Market forks

A `tf.World` holds a market, an agent and its portfolio. `world.run` advances
it, `world.fork` returns independent branches that share its history, and
`tf.compare` reads two branches side by side. This block runs the market in
the README's fork figure and applies the packaged `rate_shock` scenario to one
branch:

```python
import tradefloor as tf

universe = tf.Universe.random(20, seed=11)
world = tf.World(seed=7, universe=universe,
                 agent=tf.baselines.BuyAndHold())
world.run(days=40)                          # a shared history

control, shock = world.fork("control", "rate_shock")
same = tf.agree(control, shock)             # checked before any change
shock.apply(tf.Scenario.load("rate_shock"), at=0)

control.run(days=80)
shock.run(days=80)
print(same)
print(tf.compare(control, shock, agreement=same).render())
```

```
Agreement(identical=True, 9 checks)
                                    control       rate_shock
  ----------------------------------------------------------
  --- agent behaviour ---
  final gross exposure                1.00x            1.00x
  steps it traded on                      0                0
  orders sent                             0                0
  unusable responses                      0                0
  turnover                               $0               $0
  --- execution ---
  trades filled                           0                0
  partial fills                           0                0
  refused trades                          0                0
  cost against arrival                   $0               $0
  the same, in bps                +0.00 bps        +0.00 bps
  --- portfolio ---
  cash                                $-577            $-580
  final value                    $1,097,613       $1,025,667
  P&L since the fork               $+52,652         $-19,293
  return since the fork              +5.04%           -1.85%
  max drawdown since                  3.05%            8.76%
  P&L since inception              $+97,613         $+25,667
  return since inception             +9.76%           +2.57%
```

`tf.agree` runs nine checks that two branches hold the same state, and its
verdict travels with the comparison. `at=0` fires the scenario on the first
day after the fork. To set macro fields by hand, call
`shock.intervene(federal_funds_rate=0.06, corporate_bond_yield=0.075)` in
place of `shock.apply`.

### Rate-shock demo

The examples are in this repository and not in the package, so clone it
first:

```
git clone https://github.com/simoncoombes/tradefloor
cd tradefloor
python examples/rate-shock/counterfactual.py
```

It runs an agent in a controlled market, checkpoints the world and forks it,
raises rates by 200 bp in one branch, and compares what the same agent does
next. It prints nine checks that the two branches started identical, the
step at which the agent's behavior changed, and the two branches side by
side. The run takes under five seconds of CPU and needs no keys and no network.
The walkthrough is
[Your first counterfactual experiment](https://github.com/simoncoombes/tradefloor/blob/main/examples/rate-shock/README.md).

### Fork figure

[`examples/fork-futures/`](https://github.com/simoncoombes/tradefloor/tree/main/examples/fork-futures)
is the study behind the figure at the top of the README: the same fork with a
control and three scenario branches. The animation below is hosted on
tradefloor.dev.

<a href="https://tradefloor.dev"><img src="https://tradefloor.dev/multiverse.gif" width="900" alt="One simulated market, forked on six days into seven futures: a rate cut, a stimulus, an oil spike, a rate shock, a liquidity crisis and a recession, each run on from the same past."></a>

## Scenarios

A scenario is a file of changes to the market and the assumptions behind
them. Each change targets a field the engine reads, and the file keeps the
shock apart from the knock-on effects you assume follow it:

```
tradefloor scenario show oil_price_spike

Exogenous shocks
----------------------------------------------------------
  day 50+            commodity.oil            x1.4

Assumed transmission
----------------------------------------------------------
  day 55..74 ramp    macro.inflation          +1.50pp
  day 55+            macro.corporate_yield    +0.50pp
```

`at` in a scenario counts days from the first day it is applied, so on a
branch it counts from the branch. Most packaged files first fire on day 50.
To fire one on the first day after a fork, use `scenario.starting_at(0)`, or
`world.apply(scenario, at=0)` on a `World`. The gaps between its events stay
the same, and the run's record keeps the packaged file's fingerprint and the
days each event fired.

tradefloor does not predict what a war, an election, an oil shock or a
recession will do to markets. You state the assumptions and it measures how an
agent behaves under them. `tradefloor scenario list` names the eight packaged
scenarios, and `tradefloor scenario targets` lists every field a scenario can
change.
