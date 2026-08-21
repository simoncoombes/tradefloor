---
title: Ground truth
nav_order: 5
---

# Ground truth

`truth` carries one row per instrument per tick:

- `fundamental_value` - what the company is worth on its fundamentals
- `anchor_price` - what the model wanted before the book touched it
- `mispricing_s` - log deviation of price from fair value
- seven factor columns - mean reversion, momentum, crowd, news, order flow,
  short squeeze, noise

Keep the three price levels apart. Conflating them ruins the join.

The seven factors sum to the change in `mispricing_s`, with a measured residual
around 1e-16. Difference `mispricing_s` across ticks, add the columns, and you
can verify the label instead of trusting it.

```python
d = truth.sort("tick").with_columns(
    (pl.col("mispricing_s").diff()).alias("move"),
    (pl.col("f_reversion") + pl.col("f_momentum") + pl.col("f_crowd")
     + pl.col("f_news") + pl.col("f_flow") + pl.col("f_squeeze")
     + pl.col("f_noise")).alias("sum_factors"),
)
```

Historical data cannot carry these columns. You can observe that a stock fell;
you cannot observe that 60% of the fall was order flow, because nobody knows.
