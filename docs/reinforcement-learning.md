---
title: Reinforcement learning
nav_order: 13
rack: connect
short: Reinforcement learning
---

# Reinforcement learning

```python
from tradefloor.gym import TradingEnv

env = TradingEnv(universe=universe, seed=42, days=20)
obs, info = env.reset(seed=42)
obs, reward, terminated, truncated, info = env.step(action)
```

Passes gymnasium's `env_checker`. Actions are target weights in `[-1, 1]`
rather than share counts, so a policy does not have to learn each instrument's
price range first. Reward is the step's P&L measured after the market moves,
which includes the cost of the agent's own footprint.

```
pip install tradefloor[rl]
```
