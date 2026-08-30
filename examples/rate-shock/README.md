# Your first counterfactual experiment

Five minutes, one command, no keys and no network.

```
git clone https://github.com/simoncoombes/tradefloor
cd tradefloor
pip install tradefloor
python examples/rate-shock/counterfactual.py
```

The wheel carries the library, not the examples, so the clone is what puts
the script on disk. `matplotlib` is optional and only decides whether you get
the chart.

It runs in about two seconds and answers one question:

> **How does the exact same trading agent behave when interest rates
> unexpectedly rise by 200 basis points?**

The reason that question is hard anywhere else is that history ran once. You
can find a real hiking cycle and see what a strategy did through it, but you
cannot see what the *same* strategy would have done in the *same* market
without the hike, because that market does not exist. Here it does, because
the simulator computed it.

```
                         CHECKPOINT
                             |
                    +--------+--------+
                    |                 |
                 CONTROL          RATE SHOCK
                    |                +200bps
                    v                 v
                 SAME AGENT       SAME AGENT
                    |                 |
                    +--------+--------+
                             |
                          COMPARE
```

---

## 1 -- Create the market

Four companies, written down rather than drawn from a seed, so the roster is
pinned exactly and small enough to hold in your head. They differ in the one
property that decides rate sensitivity in this model:

| | sector | revenue growth | |
|---|---|---|---|
| `NOVA` | technology | 0.35 | long-duration growth |
| `HELX` | healthcare | 0.18 | mid-duration growth |
| `BRDG` | industrials | 0.06 | short-duration cyclical |
| `STAP` | consumer staples | 0.01 | minimal-duration defensive |

Fair value in Tradefloor is earnings times a target multiple, and the multiple
is discounted by

```
rate_adjustment = 1 - (discount - 0.04) * 1.5 * (1 + growth * 2)
```

Revenue growth **is** the duration term. A 200bp rise costs `NOVA` about 5.3%
of its multiple and `STAP` about 3.1%.

That correspondence is honest for this market and does not transfer. A real
utility is a long-duration bond proxy; here, on one percent revenue growth, it
is the least rate-sensitive name on the roster. This is a controlled synthetic
experiment, not a prediction about real securities.

```python
world = World(
    seed=4242, universe=roster, agent=agent,
    pins={"federal_funds_rate": 0.04, "corporate_bond_yield": 0.055},
    cash=50_000_000.0, steps_per_day=6, ticks_per_step=65,
)
```

The two pinned rates are the macro regime **both** branches will run under.
Pinning them is what makes the control a control: the arms differ in the level
of one thing, not in whether it was pinned at all.

---

## 2 -- Run an agent

`MacroAwareAgent` is deterministic, has no RNG and no LLM, and fits on a page.
Three rules:

```
tightening   = policy rate now - policy rate when it started
gross        = 0.95 - 0.30 per 100bp of tightening, floor 0.20
               then x (1 - 0.40 x vol excess)
weight_i     = gross x 1 / (1 + 0.35 x growth_i x 100bp), normalised
```

It reads the policy rate off `obs.engine.macro_state`, and its own recent
prices for the volatility term. It never reads `mispricing_s`, fair value or
the factor attribution -- those are what the simulator knows and a trader does
not.

It is not a moving-average crossover on purpose. A price rule would react to
the shock only *after* the shock moved prices, so its divergence would be the
market's, borrowed. This one reads the rate, so a change in its behaviour is a
change in its behaviour.

```python
world.run(days=20)
```

Twenty days, six decisions a day. Both branches will share this history
exactly.

---

## 3 -- Checkpoint and fork

```python
mark = world.checkpoint(label="before the rate shock")
control, shock = world.fork("control", "+200bps")

print(agree(control, shock).render())
```

```
  market columns         identical  18 columns x 4
  prices                 identical  133.71  97.93  76.37  56.59
  order book             identical  80 levels
  generator state        identical  21 words
  macro chain            identical  federal_funds_rate=0.04  corporate_bond_yield=0.055
  whole engine state     identical  14 fields, day 20
  portfolio              identical  $2,864,512 cash, 4 positions
  agent state            identical  3 fields
  shared history         identical  120 steps
```

Nine checks, every one read back off the two worlds rather than asserted.
This is the moment the experiment either exists or does not: if the arms are
not identical here, everything downstream is comparing two different markets.

The two forks are for different things. `fork()` copies the engine's state
snapshot and takes under a millisecond; `checkpoint()` records the seed, the
roster and every input that reached the engine as about 21 kB of JSON, costs
what the run cost to restore, and survives the process. Use the first for an
experiment inside one script and the second for anything you save or cite.

---

## 4 -- Introduce the rate shock

```python
shock.intervene(federal_funds_rate=0.06, corporate_bond_yield=0.075)
```

One call, one arm, two fields -- a parallel 200bp shift of the policy rate the
agent watches and the corporate bond yield equities are discounted off.

Both, and not the policy rate alone, because of how this model transmits:
`federal_funds_rate` reaches a valuation *only* by steering the corporate bond
yield, recomputed at central-bank meetings, the first scheduled 45
days out. A policy-rate hike by itself would move the agent and not the
market. `tests/test_macro_transmission.py` pins that map.

`intervene` writes macro fields and nothing else. It cannot reach the
portfolio, the book or the agent -- an intervention that could would not be a
controlled variable.

It is recorded three times over: on the world, in the `Scenario` the world
derives, and -- once the next day opens and `pin_macro` runs -- in the engine's
own order log, which travels inside the checkpoint and the manifest.

---

## 5 -- Compare

```python
control.run(days=20)
shock.run(days=20)
print(compare(control, shock).render())
```

The agent's target moved on the very first decision after the hike, and it did
not cut evenly:

```
            growth   control    shock      cut
    NOVA      0.35    0.2375   0.0838   -64.7%
    HELX      0.18    0.2375   0.0926   -61.0%
    BRDG      0.06    0.2375   0.1001   -57.9%
    STAP      0.01    0.2375   0.1036   -56.4%
```

Twenty days later the two markets are apart, in the same order:

```
                               NOVA       HELX       BRDG       STAP
  control        40 days     122.75      97.70      76.83      50.98
  +200bps        40 days     117.21      93.66      74.50      49.44
  difference                 -4.51%     -4.14%     -3.03%     -3.02%
```

And the two books:

```
                                    control          +200bps
  final gross exposure                0.93x            0.37x
  turnover                       $4,112,409      $31,875,630
  cost against arrival               $1,059          $20,957
  cash                           $3,518,470      $34,266,971
  P&L since the fork            $-2,344,740      $-1,868,420
  max drawdown since                  7.08%            4.45%
```

The behaviour comes first here deliberately. The P&L difference is one draw of
one market, and a single seed measures the seed as much as the decision;
`tf.rank` is the tool for "is this policy better", across many seeds with a
paired test. What this experiment establishes is narrower and stronger: the
agent *did* react to the macro state, at a known step, in a known direction,
and nothing else could have caused it because nothing else differed.

---

## What you get afterwards

```
examples/rate-shock/artifacts/
    manifest.json       the experiment: design, intervention, agreement, divergence
    checkpoint.json     the state both arms forked from
    control.json        a RunManifest; .reproduce() rebuilds the market
    rate_shock.json     the same, for the treated arm
    comparison.json     the side-by-side and the divergence steps
    comparison.png      portfolio value and exposure, with the fork marked
```

Both `RunManifest` files reproduce: `tf.RunManifest.from_json(text).reproduce()`
replays the recorded log and refuses on a digest mismatch, so somebody with
only the JSON rebuilds the same market -- including the intervention, which
rides inside the log as a `pin_macro` entry.

## Swapping the agent

`World` calls `act(obs)` and, if they exist, `decision()` and `state()`.
Anything with those methods drops in without changing the market, the
checkpoint, the fork, the intervention, the execution or the comparison. That
separation is the point: Tradefloor owns the experiment, and the agent is the
subject being measured.

## What this does not claim

Ground truth about this market, not about any real one. The prices come from a
known model, so a strategy shaped like that model looks excellent and teaches
you nothing. There is one venue, no latency, and no counterparty that adapts
to you. See [the realism envelope](https://tradefloor.dev) for the five limits
that are measured and written down.
