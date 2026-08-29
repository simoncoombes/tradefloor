---
title: Ground truth
nav_order: 5
rack: measure
---

# Ground truth

`truth` carries one row per instrument per tick:

- `fundamental_value` - what the company is worth on its fundamentals
- `anchor_price` - what the model wanted before the book touched it
- `mispricing_s` - log deviation of price from fair value
- nine factor columns - `reversion`, `momentum`, `crowd_lean`, `company_news`,
  `order_flow_impact`, `short_squeeze_effect`, `random_noise`,
  `circuit_breaker`, `jump`

Keep the three price levels apart. Conflating them ruins the join.

The nine factors sum to the change in `mispricing_s`. Difference
`mispricing_s` across ticks, add the columns, and you can verify the label
instead of trusting it. On the 20-name, 5-day run below the worst residual
over all 39,000 rows is 1.9e-16, which is floating-point addition and nothing
else.

```python
import polars as pl
import tradefloor as tf

engine = tf.Engine(seed=42, universe=tf.Universe.random(20, seed=3))
engine.run_days(5)
truth = pl.DataFrame(engine.truth())

d = truth.sort("instrument_id", "day", "tick").with_columns(
    pl.col("mispricing_s").diff().over("instrument_id").alias("move"),
    sum(pl.col(name) for name in tf.Engine.FACTORS).alias("sum_factors"),
)
print((d["move"] - d["sum_factors"]).drop_nulls().abs().max())
# 1.9147010366094008e-16
```

Two things that identity depends on.

**Difference within the instrument, and sort `day` before `tick`.** `tick`
restarts at zero each day, so `sort("tick")` on its own files day 4's tick 0
next to day 0's tick 0 and the moves stop lining up with the rows that explain
them: make only that substitution above and the same run's worst residual goes
from 1.9e-16 to 0.149. Drop the `.over("instrument_id")` as well and the diff
also runs across the seam between one name and the next, which takes it to
0.957.

**Add all nine.** `jump` is applied to `s` after the tick loop rather than
inside it, so the eight tick components alone cannot reconstruct a day that
jumped, and every preset from pt-v4 carries jumps: over 20 days on the same
roster, pt-v1 and pt-v3 produce no non-zero `jump` rows while pt-v4 and pt-v12
produce 45 and 25. The eighth of those tick components, `circuit_breaker`,
books the other rewrite -- when the model price leaves the session band the
tick re-derives `s` from the clamped price, and with no column for it the sum
misses that correction on any day the breaker binds. The run above carries one
jump row in 39,000 and no binding breaker, so dropping `jump` alone moves the
worst residual from 1.9e-16 to 0.090.

Historical data cannot carry these columns. You can observe that a stock fell;
you cannot observe that 60% of the fall was order flow, because nobody knows.
