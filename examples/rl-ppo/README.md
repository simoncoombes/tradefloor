# PPO on TradingEnv

This study trains a Stable-Baselines3 PPO agent on `tradefloor.gym.TradingEnv`
for a fixed budget of environment steps, then scores it on markets it never
trained on. The question is what PPO learns in that budget, measured against
holding cash, holding every name at equal weight, and the same network before
training.

```
pip install "tradefloor[rl]" stable-baselines3
python examples/rl-ppo/ppo.py
```

The study needs `stable-baselines3`, which installs `torch`. No extra of
this package installs either, so the command above names it. The script
prints a table and writes `artifacts/results.json` beside itself, with the
versions and the preset it ran on.

## Setup

| | |
|---|---|
| market | `tf.Universe.random(8, seed=7)`, the roster notebook 05 uses |
| episode | 5 days of 6 steps, 30 decisions, $1,000,000 starting cash |
| action | a target weight per name in [-1, 1], scaled down to the env's leverage cap |
| reward | the step's P&L, divided by starting cash and times 1e4 (basis points) |
| training | PPO, `MlpPolicy`, SB3 defaults except `n_steps=512` and `batch_size=64`, policy seed 0, one torch thread |
| training markets | every episode seed drawn by the env's own generator from seed 0 |
| held-out markets | seeds 10000 to 10019, checked disjoint from every training seed |
| scored policies | cash (all weights 0), equal weight (1/8 each, rebalanced every step), PPO before training, PPO after training |

`n_steps` is 512 rather than SB3's 2,048 because one environment step costs
about 0.1 s here, so a 2,048-step rollout delays the first update by minutes.
PPO trains in whole rollouts, and the record's `timesteps_run` is the number
that ran. The return on a held-out seed comes from the account's net worth at
the end of the episode, not from the summed reward.

## Results

Measured on 11-27 with tradefloor 0.11.0 built from `dev` (preset `pt-v22`),
stable-baselines3 2.9.2, torch 2.15.0 and gymnasium 1.4.0 on macOS arm64.
8,192 training steps over 274 episodes took 607 s of wall time, and the whole
script took 1,122 s on a loaded machine. Each row is 20 held-out seeds of 5
days. The return is percent of starting cash at the end of the episode, and
gross is the mean of the absolute holdings over net worth after each step.

| policy | mean return % | stdev % | mean gross |
|---|---|---|---|
| cash | 0.000 | 0.000 | 0.00 |
| equal weight | 0.024 | 2.278 | 1.00 |
| PPO before training | -0.003 | 0.015 | 0.01 |
| PPO after training | -0.773 | 1.711 | 1.55 |

PPO after training did worse than every reference policy. It beat cash on 4
of the 20 held-out seeds, equal weight on 5 and its untrained self on 4. An
exact two-sided sign test gives p = 0.012 against cash and p = 0.041 against
equal weight, so on these seeds training made the policy worse. The network
before training outputs weights near zero, which leaves it close to cash. In
8,192 steps it learned to hold a levered book, 1.55 times net worth on
average, and that book lost money on 16 of the 20 seeds. This study does not
separate how much of the loss is market impact and how much is direction.

A longer budget is one flag away (`--timesteps`). Only the 8,192-step run
above has been measured.

## Limits

- One roster, one policy seed and one training budget. A different policy
  seed trains a different network, and this study does not measure how much
  that moves the table.
- Twenty held-out episodes of five days each. The standard deviations in the
  table are across those twenty seeds.
- The numbers reproduce on one machine with one torch build. Torch does not
  promise identical floating point across platforms or versions, so another
  machine can train a different network from the same seeds.
- Every policy pays its own market impact, because the env measures P&L after
  the market moves. Equal weight rebalanced every step trades every step.
- A result here says nothing about real markets. The env is a simulated
  market on the default preset named in `artifacts/results.json`.

## Checks

`tests/test_rl_ppo_study.py` runs the script with two 128-step rollouts on two
held-out seeds, checks the four policies and the seed bookkeeping, and runs it
twice to check the numbers repeat. It runs behind `TRADEFLOOR_SLOW_TESTS=1`
and skips without `stable-baselines3`. In CI it runs on the nightly only,
because the install brings `torch`. On a pull request the script gets the
syntax check in `tests/test_examples.py`.
