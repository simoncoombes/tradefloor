"""The candidate agent for `.github/workflows/evaluate-selftest.yml`: one-day momentum, top four names."""

import tradefloor as tf


def make_agent():
    return tf.StrategySpec.momentum(lookback_days=1.0, top_k=4)
